from __future__ import annotations
import ctypes
import logging
import os
import subprocess
import time
import json
import csv
import io
from pathlib import Path
from .strategy import parse_strategy, available_strategies
from core.paths import ZAPRET_DIR, WINWS_LOG_PATH

log = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000


class ZapretManager:
    def __init__(self, config):
        self.config = config
        self.root = ZAPRET_DIR
        self.bin_dir = self.root / "bin"
        self.lists_dir = self.root / "lists"
        self.strategy_dir = self.root / "strategies"
        self.process: subprocess.Popen | None = None
        self._output_file = None
        self.last_error = ""
        self.last_command: list[str] = []
        self.last_resolution = ""
        self._stopped_services: list[str] = []
        self.ensure_user_lists()

    def ensure_user_lists(self):
        """Mirror upstream's non-empty user-list invariant."""
        self.lists_dir.mkdir(parents=True, exist_ok=True)
        defaults = {
            "list-general-user.txt": "# Never leave this file empty\ndomain.example.abc\n",
            "list-exclude-user.txt": "domain.example.abc\n",
            "ipset-exclude-user.txt": "203.0.113.113/32\n",
        }
        for name, placeholder in defaults.items():
            p = self.lists_dir / name
            if not p.exists() or p.stat().st_size == 0:
                p.write_text(placeholder, encoding="utf-8")

    def sync_sites(self):
        domains = []
        for item in self.config.data.get("sites", []):
            if isinstance(item, dict) and item.get("enabled", True) and item.get("domain"):
                domains.append(item["domain"].strip().lower())
        domains = sorted(set(d for d in domains if d))
        content = "# Zapret+ user sites\n" + ("\n".join(domains) + "\n" if domains else "domain.example.abc\n")
        tmp = self.lists_dir / "list-general-user.txt.tmp"
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(self.lists_dir / "list-general-user.txt")

    def strategies(self) -> list[str]:
        return available_strategies(self.strategy_dir)

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @staticmethod
    def _is_admin() -> bool:
        if os.name != "nt":
            return True
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _enable_tcp_timestamps_if_needed(self, args: list[str]) -> None:
        if os.name != "nt" or not any("--dpi-desync-fooling=ts" in a for a in args):
            return
        try:
            cp = subprocess.run(
                ["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
            if cp.returncode != 0:
                log.warning("TCP timestamps command failed rc=%s: %s", cp.returncode, cp.stdout.strip())
        except Exception as exc:
            log.warning("Could not enable TCP timestamps: %s", exc)

    @staticmethod
    def _arg_path(token: str) -> Path | None:
        prefixes = (
            "--hostlist=", "--hostlist-exclude=", "--ipset=", "--ipset-exclude=",
            "--dpi-desync-fake-quic=", "--dpi-desync-fake-discord=", "--dpi-desync-fake-stun=",
            "--dpi-desync-fake-tls=", "--dpi-desync-fake-unknown-udp=",
            "--dpi-desync-split-seqovl-pattern=",
        )
        for prefix in prefixes:
            if token.startswith(prefix):
                return Path(token[len(prefix):].strip('"'))
        return None

    def preflight(self, args: list[str] | None = None) -> dict:
        cfg = self.config.data.get("zapret", {})
        strategy = self.strategy_dir / cfg.get("strategy", "general.bat")
        if not strategy.exists():
            strategy = self.strategy_dir / "general.bat"
        exe = self.bin_dir / "winws.exe"
        driver = self.bin_dir / "WinDivert64.sys"
        dll = self.bin_dir / "WinDivert.dll"
        errors: list[str] = []
        warnings: list[str] = []
        if os.name == "nt" and not self._is_admin():
            errors.append("Zapret+ запущен без прав администратора. WinDivert требует повышение прав.")
        for p, label in ((exe, "winws.exe"), (driver, "WinDivert64.sys"), (dll, "WinDivert.dll"), (strategy, "файл стратегии")):
            if not p.exists():
                errors.append(f"Не найден {label}: {p}")
        if args is None and strategy.exists():
            try:
                args = parse_strategy(strategy, self.bin_dir, self.lists_dir, str(cfg.get("game_filter", "off")))
            except Exception as exc:
                errors.append(f"Не удалось разобрать стратегию: {exc}")
                args = []
        for token in args or []:
            p = self._arg_path(token)
            if p is not None and not p.exists():
                errors.append(f"Стратегия ссылается на отсутствующий файл: {p}")
        if os.name == "nt":
            try:
                cp = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq winws.exe", "/FO", "CSV", "/NH"],
                    creationflags=CREATE_NO_WINDOW, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
                )
                rows = [x for x in cp.stdout.splitlines() if "winws.exe" in x.lower()]
                if rows and not self.is_running():
                    warnings.append("В системе уже запущен другой winws.exe. Он может конфликтовать с WinDivert-фильтрами Zapret+.")
            except Exception:
                pass
        return {"ok": not errors, "errors": errors, "warnings": warnings, "strategy": strategy.name}


    @staticmethod
    def _powershell_json(script: str):
        """Run a small PowerShell query and decode JSON. Windows-only."""
        if os.name != "nt":
            return []
        try:
            cp = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
            raw = (cp.stdout or "").strip()
            if cp.returncode != 0 or not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else [data]
        except Exception as exc:
            log.warning("PowerShell process query failed: %s", exc)
            return []

    def winws_processes(self) -> list[dict]:
        """Return running winws processes with PID/path/command line/service name when available."""
        if os.name != "nt":
            return []
        script = r"""$ps = Get-CimInstance Win32_Process -Filter "Name='winws.exe'" -ErrorAction SilentlyContinue; $sv = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -gt 0 }; @($ps | ForEach-Object { $p=$_; $svc=@($sv | Where-Object { $_.ProcessId -eq $p.ProcessId } | Select-Object -ExpandProperty Name); [PSCustomObject]@{ pid=[int]$p.ProcessId; path=$p.ExecutablePath; command=$p.CommandLine; services=$svc } }) | ConvertTo-Json -Compress"""
        rows = self._powershell_json(script)
        # Fallback for locked-down systems where CIM/PowerShell queries are unavailable.
        if not rows:
            try:
                cp = subprocess.run(
                    ["tasklist.exe", "/FI", "IMAGENAME eq winws.exe", "/FO", "CSV", "/NH"],
                    creationflags=CREATE_NO_WINDOW, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
                )
                fallback = []
                for record in csv.reader(io.StringIO(cp.stdout or "")):
                    if len(record) >= 2 and record[0].lower() == "winws.exe":
                        try:
                            fallback.append({"pid": int(record[1]), "path": "", "command": "", "services": []})
                        except ValueError:
                            pass
                rows = fallback
            except Exception as exc:
                log.warning("tasklist fallback failed: %s", exc)
        result = []
        own_pid = self.process.pid if self.is_running() else None
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                pid = int(row.get("pid") or 0)
            except Exception:
                continue
            if not pid or pid == own_pid:
                continue
            services = row.get("services") or []
            if isinstance(services, str):
                services = [services]
            result.append({
                "pid": pid,
                "path": str(row.get("path") or ""),
                "command": str(row.get("command") or ""),
                "services": [str(x) for x in services if x],
            })
        return result

    def _resolve_same_filter_conflict(self) -> str:
        """Stop only winws instances after winws itself proved the filter conflicts.

        Services are stopped first so SCM-owned winws instances are not immediately recreated.
        Nothing is deleted or reconfigured.
        """
        conflicts = self.winws_processes()
        if not conflicts:
            return "winws сообщил о конфликте фильтра, но конфликтующий процесс уже завершился"

        stopped_services: list[str] = []
        stopped_pids: list[int] = []
        for proc in conflicts:
            for service in proc.get("services", []):
                if service in stopped_services:
                    continue
                cp = subprocess.run(
                    ["sc.exe", "stop", service],
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                )
                if cp.returncode == 0 or "1062" in (cp.stdout or ""):
                    stopped_services.append(service)
                    if service not in self._stopped_services:
                        self._stopped_services.append(service)
                    log.info("Stopped conflicting winws service %s", service)

        time.sleep(0.7)
        for proc in self.winws_processes():
            pid = int(proc["pid"])
            try:
                cp = subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                    check=False,
                )
                if cp.returncode == 0:
                    stopped_pids.append(pid)
                    log.info("Stopped conflicting winws pid=%s", pid)
            except Exception as exc:
                log.warning("Could not stop conflicting winws pid=%s: %s", pid, exc)

        time.sleep(0.8)
        remaining = self.winws_processes()
        if remaining:
            pids = ", ".join(str(x["pid"]) for x in remaining)
            raise RuntimeError(
                "Обнаружен конфликтующий winws, но Zapret+ не смог его остановить "
                f"(PID: {pids}). Закройте старый Zapret и повторите попытку."
            )

        parts = []
        if stopped_services:
            parts.append("службы: " + ", ".join(stopped_services))
        if stopped_pids:
            parts.append("PID: " + ", ".join(map(str, stopped_pids)))
        detail = "; ".join(parts) if parts else "старый экземпляр winws"
        return f"Автоматически устранён конфликт ({detail})"

    @staticmethod
    def _same_filter_conflict(text: str) -> bool:
        return "a copy of winws is already running with the same filter" in (text or "").lower()

    def _close_output(self):
        if self._output_file:
            try:
                self._output_file.flush(); self._output_file.close()
            except Exception:
                pass
            self._output_file = None

    def log_tail(self, max_chars: int = 12000) -> str:
        try:
            if not WINWS_LOG_PATH.exists():
                return ""
            text = WINWS_LOG_PATH.read_text(encoding="utf-8", errors="replace")
            return text[-max_chars:]
        except Exception as exc:
            return f"Не удалось прочитать лог winws: {exc}"

    def _startup_error(self, rc: int | None) -> str:
        tail = self.log_tail(5000).strip()
        headline = f"winws завершился сразу после запуска (код {rc})"
        if tail:
            # Keep modal readable while preserving full output in diagnostics.
            compact = tail[-2500:]
            return f"{headline}\n\nВывод winws:\n{compact}"
        return headline + f"\n\nПодробный лог: {WINWS_LOG_PATH}"

    def _spawn_once(self, exe: Path, args: list[str], strategy_name: str) -> tuple[bool, int | None, str]:
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        self.last_command = [str(exe), *args]
        WINWS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._output_file = open(WINWS_LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._output_file.write(f"\n\n===== Zapret+ winws start {stamp} | {strategy_name} =====\n")
        self._output_file.flush()
        try:
            self.process = subprocess.Popen(
                self.last_command, cwd=str(self.bin_dir), creationflags=flags,
                stdin=subprocess.DEVNULL, stdout=self._output_file, stderr=subprocess.STDOUT,
            )
        except Exception:
            self._close_output(); self.process = None
            raise
        time.sleep(0.8)
        if self.process.poll() is None:
            return True, None, ""
        rc = self.process.returncode
        self.process = None
        self._close_output()
        return False, rc, self.log_tail(5000)

    def start(self) -> None:
        if self.is_running():
            return
        self.sync_sites()
        cfg = self.config.data["zapret"]
        strategy_name = cfg.get("strategy", "general.bat")
        strategy = self.strategy_dir / strategy_name
        if not strategy.exists():
            strategy = self.strategy_dir / "general.bat"
        exe = self.bin_dir / "winws.exe"
        args = parse_strategy(strategy, self.bin_dir, self.lists_dir, str(cfg.get("game_filter", "off")))
        pf = self.preflight(args)
        if not pf["ok"]:
            self.last_error = "\n".join(pf["errors"])
            raise RuntimeError(self.last_error)
        for warning in pf["warnings"]:
            log.warning(warning)
        self._enable_tcp_timestamps_if_needed(args)
        self.last_error = ""
        self.last_resolution = ""
        log.info("Starting winws using %s", strategy.name)

        ok, rc, output = self._spawn_once(exe, args, strategy.name)
        if ok:
            return

        if self._same_filter_conflict(output):
            try:
                resolution = self._resolve_same_filter_conflict()
                self.last_resolution = resolution
                WINWS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(WINWS_LOG_PATH, "a", encoding="utf-8", errors="replace") as fp:
                    fp.write(f"\n[Zapret+] {resolution}. Повторяем запуск один раз.\n")
                log.info("%s; retrying winws once", resolution)
                ok, rc, output = self._spawn_once(exe, args, strategy.name)
                if ok:
                    self.last_error = ""
                    return
            except Exception as exc:
                self.last_error = str(exc)
                log.error("Automatic winws conflict resolution failed: %s", exc)
                raise RuntimeError(self.last_error)

        self.last_error = self._startup_error(rc)
        log.error(self.last_error)
        raise RuntimeError(self.last_error)

    def stop(self) -> None:
        proc = self.process
        self.process = None
        if not proc or proc.poll() is not None:
            self._close_output()
            return
        log.info("Stopping winws pid=%s", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=2)
        finally:
            self._close_output()

    def restart(self) -> None:
        self.stop(); self.start()

    def built_in_sites(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name in ("list-google.txt", "list-general.txt", "list-exclude.txt"):
            p = self.lists_dir / name
            rows: list[str] = []
            if p.exists():
                for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        rows.append(line)
            result[name] = rows
        return result
