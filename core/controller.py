from __future__ import annotations
import logging
import time
from urllib.parse import urlparse
from engines.zapret.manager import ZapretManager
from engines.telegram.manager import TelegramManager

log = logging.getLogger(__name__)


class AppController:
    def __init__(self, config):
        self.config = config
        self.zapret = ZapretManager(config)
        self.telegram = TelegramManager(config)
        self.active = False
        self.telegram_setup_requested = False
        self.last_start_result: dict = {}
        from services.updater import UpdateManager
        from services.strategy_tuner import StrategyTuner
        self.updater = UpdateManager(self, config)
        self.strategy_tuner = StrategyTuner(self, config)
        self.desired_active = False
        self.operation_note = ""
        self._recovering = set()
        self.recovery_counts = {"zapret": 0, "telegram": 0}
        self.last_recovery_note = ""

    def start_all(self) -> dict:
        """Start engines independently.

        A failure in Zapret must never roll back a healthy Telegram proxy (and vice versa).
        Returns per-engine status so UI can report partial success accurately.
        """
        self.telegram_setup_requested = False
        self.desired_active = True
        result = {
            "zapret": {"requested": self.config.get("zapret", "enabled", default=True), "ok": None, "error": "", "notice": ""},
            "telegram": {"requested": self.config.get("telegram", "enabled", default=True), "ok": None, "error": ""},
        }

        if result["zapret"]["requested"]:
            try:
                self.zapret.start()
                if self.config.get("zapret", "strategy_mode", default="auto") == "auto":
                    tuned = self.strategy_tuner.ensure_working()
                    if tuned is not None:
                        result["zapret"]["notice"] = f"Авто: {tuned.strategy}"
                result["zapret"]["ok"] = True
                if not result["zapret"]["notice"]:
                    result["zapret"]["notice"] = self.zapret.last_resolution
            except Exception as exc:
                result["zapret"]["ok"] = False
                result["zapret"]["error"] = str(exc)
                log.exception("Zapret engine failed to start")

        if result["telegram"]["requested"]:
            try:
                self.telegram.start()
                result["telegram"]["ok"] = True
                if self.telegram.needs_client_setup():
                    self.telegram_setup_requested = self.telegram.open_in_telegram(mark_configured=True)
            except Exception as exc:
                message = str(exc)
                # A just-stopped local listener can need a fraction of a second to
                # disappear on Windows. Retry once only for our known lifecycle cases.
                retryable = (
                    "ещё завершает работу" in message
                    or "всё ещё занят" in message
                    or "10048" in message
                )
                if retryable:
                    try:
                        time.sleep(0.35)
                        self.telegram.stop()
                        time.sleep(0.12)
                        self.telegram.start()
                        result["telegram"]["ok"] = True
                        result["telegram"]["error"] = ""
                        if self.telegram.needs_client_setup():
                            self.telegram_setup_requested = self.telegram.open_in_telegram(mark_configured=True)
                    except Exception as retry_exc:
                        result["telegram"]["ok"] = False
                        result["telegram"]["error"] = str(retry_exc)
                        log.exception("Telegram engine failed after lifecycle retry")
                else:
                    result["telegram"]["ok"] = False
                    result["telegram"]["error"] = message
                    log.exception("Telegram engine failed to start")

        self.active = self.zapret.is_running() or self.telegram.is_running()
        self.last_start_result = result
        return result

    def stop_all(self):
        self.desired_active = False
        self.operation_note = ""
        errors = []
        for engine in (self.telegram, self.zapret):
            try:
                engine.stop()
            except Exception as exc:
                errors.append(exc); log.exception("Failed to stop engine")
        self.active = False
        if errors:
            raise RuntimeError("; ".join(map(str, errors)))

    def status(self):
        tg_health = self.telegram.health() if self.config.get("telegram", "enabled", default=True) else None
        return {
            "zapret": self.zapret.is_running() if self.config.get("zapret", "enabled", default=True) else None,
            "zapret_error": self.zapret.last_error,
            "telegram": tg_health,
        }

    def diagnostics(self) -> str:
        pf = self.zapret.preflight()
        th = self.telegram.health() if self.config.get("telegram", "enabled", default=True) else None
        lines = ["Zapret+ diagnostics", "", "[Automation]"]
        lines.append(f"Desired active: {self.desired_active}")
        lines.append(f"Auto recover: {self.config.get('app','auto_recover',default=True)}")
        lines.append(f"Strategy mode: {self.config.get('zapret','strategy_mode',default='auto')}")
        lines.append(f"Last good strategy: {self.config.get('zapret','last_good_strategy',default='?')}")
        if self.last_recovery_note:
            lines.append(f"Last recovery: {self.last_recovery_note}")
        lines += ["", "[Zapret]"]
        lines.append(f"Strategy: {pf.get('strategy','?')}")
        lines.append(f"Running: {self.zapret.is_running()}")
        lines.append(f"Preflight: {'OK' if pf['ok'] else 'ERROR'}")
        for e in pf["errors"]:
            lines.append(f"ERROR: {e}")
        for w in pf["warnings"]:
            lines.append(f"WARNING: {w}")
        if self.zapret.last_resolution:
            lines.append(f"Auto-resolution: {self.zapret.last_resolution}")
        if self.zapret.last_error:
            lines.append(f"Last error: {self.zapret.last_error}")
        tail = self.zapret.log_tail(7000).strip()
        if tail:
            lines += ["", "[winws output]", tail]
        lines += ["", "[Telegram]"]
        if th is None:
            lines.append("Disabled in settings")
        else:
            for key in ("running", "listener", "client_seen", "active_connections", "bytes_up", "bytes_down", "ws_errors", "bad_connections", "error"):
                lines.append(f"{key}: {th.get(key)}")
        return "\n".join(lines)

    def recovery_tick(self) -> list[str]:
        """One watchdog iteration. Safe to call periodically from a background worker."""
        if not self.desired_active or not self.config.get("app", "auto_recover", default=True):
            return []
        notes = []
        wanted_z = self.config.get("zapret", "enabled", default=True)
        wanted_t = self.config.get("telegram", "enabled", default=True)

        if wanted_z and not self.zapret.is_running() and "zapret" not in self._recovering:
            self._recovering.add("zapret")
            try:
                self.recovery_counts["zapret"] += 1
                if self.recovery_counts["zapret"] <= 3:
                    self.operation_note = "Восстанавливаем Zapret…"
                    try:
                        self.zapret.start()
                    except Exception:
                        # On repeated winws failure, auto mode may choose a different strategy.
                        if self.config.get("zapret", "strategy_mode", default="auto") == "auto":
                            self.strategy_tuner.tune(force=True)
                        else:
                            raise
                    notes.append("Zapret восстановлен автоматически")
                    self.recovery_counts["zapret"] = 0
            finally:
                self._recovering.discard("zapret")
                self.operation_note = ""

        if wanted_t and not self.telegram.is_running() and "telegram" not in self._recovering:
            self._recovering.add("telegram")
            try:
                self.recovery_counts["telegram"] += 1
                if self.recovery_counts["telegram"] <= 3:
                    self.operation_note = "Восстанавливаем Telegram Proxy…"
                    self.telegram.stop()
                    self.telegram.start()
                    notes.append("Telegram Proxy восстановлен автоматически")
                    self.recovery_counts["telegram"] = 0
            finally:
                self._recovering.discard("telegram")
                self.operation_note = ""

        if notes:
            self.last_recovery_note = " · ".join(notes)
        return notes

    def tune_strategy(self, force: bool = True):
        return self.strategy_tuner.tune(force=force)

    @staticmethod
    def normalize_domain(value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Пустой адрес")
        raw = value if "://" in value else "https://" + value
        host = (urlparse(raw).hostname or "").lower().strip(".")
        if not host or "." not in host or " " in host:
            raise ValueError("Не удалось распознать домен")
        return host.encode("idna").decode("ascii")

    def add_site(self, value: str) -> str:
        domain = self.normalize_domain(value)
        sites = self.config.data.setdefault("sites", [])
        if not any(isinstance(x, dict) and x.get("domain") == domain for x in sites):
            sites.append({"domain": domain, "enabled": True})
            self.config.save(); self.zapret.sync_sites()
            if self.zapret.is_running(): self.zapret.restart()
        return domain

    def remove_site(self, domain: str):
        sites = self.config.data.setdefault("sites", [])
        self.config.data["sites"] = [x for x in sites if not (isinstance(x, dict) and x.get("domain") == domain)]
        self.config.save(); self.zapret.sync_sites()
        if self.zapret.is_running(): self.zapret.restart()
