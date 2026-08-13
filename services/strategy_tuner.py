from __future__ import annotations

import concurrent.futures
import logging
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

# Connectivity, not content correctness. HTTP 2xx/3xx/4xx still proves that TLS/HTTP
# reached the destination. 5xx is accepted as reachable too; transport errors are not.
DEFAULT_PROBES = (
    "https://www.youtube.com/generate_204",
    "https://discord.com/api/v9/gateway",
    "https://www.google.com/generate_204",
)
NEUTRAL_PROBE = "https://www.microsoft.com/favicon.ico"


@dataclass
class ProbeResult:
    url: str
    ok: bool
    elapsed_ms: int
    detail: str = ""


@dataclass
class StrategyResult:
    strategy: str
    ok: bool
    score: int
    median_ms: int
    probes: list[ProbeResult]


class StrategyTuner:
    """Find a working Zapret profile without making every startup expensive."""

    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.last_results: list[StrategyResult] = []
        self.running = False

    @staticmethod
    def _probe(url: str, timeout: float = 2.2) -> ProbeResult:
        started = time.monotonic()
        req = Request(
            url,
            method="GET",
            headers={
                "User-Agent": "Mozilla/5.0 ZapretPlus/0.6",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                # Read only one byte. We test reachability, not download speed.
                resp.read(1)
                code = getattr(resp, "status", 200) or 200
            ok = int(code) < 600
            detail = f"HTTP {code}"
        except HTTPError as exc:
            # 401/403/404 still mean the connection crossed DPI successfully.
            ok = int(exc.code) < 600
            detail = f"HTTP {exc.code}"
        except (URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as exc:
            ok = False
            detail = exc.__class__.__name__
        elapsed = int((time.monotonic() - started) * 1000)
        return ProbeResult(url, ok, elapsed, detail)

    def probe_current(self) -> StrategyResult:
        urls = tuple(DEFAULT_PROBES)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as pool:
            probes = list(pool.map(self._probe, urls))
        good = [x for x in probes if x.ok]
        times = sorted(x.elapsed_ms for x in good)
        median = times[len(times)//2] if times else 99999
        # Require at least two independent destinations. This avoids accepting one
        # coincidentally reachable endpoint as a "working strategy".
        ok = len(good) >= 2
        score = len(good) * 100000 - median
        strategy = self.config.get("zapret", "strategy", default="general.bat")
        return StrategyResult(strategy, ok, score, median, probes)

    def _ordered_candidates(self) -> list[str]:
        all_names = self.controller.zapret.strategies()
        preferred = [
            self.config.get("zapret", "last_good_strategy", default=""),
            self.config.get("zapret", "strategy", default=""),
            "general.bat",
            "general (ALT).bat",
            "general (ALT2).bat",
            "general (ALT3).bat",
            "general (ALT4).bat",
            "general (ALT5).bat",
            "general (ALT6).bat",
            "general (ALT7).bat",
            "general (ALT8).bat",
            "general (ALT9).bat",
            "general (ALT10).bat",
            "general (ALT11).bat",
            "general (ALT12).bat",
        ]
        result = []
        for name in preferred + all_names:
            if name and name in all_names and name not in result:
                result.append(name)
        limit = int(self.config.get("zapret", "auto_strategy_max_candidates", default=10) or 10)
        return result[:max(3, min(limit, len(result)))]

    def tune(self, force: bool = False) -> StrategyResult:
        if self.running:
            raise RuntimeError("Автоподбор стратегии уже выполняется")
        self.running = True
        original = self.config.get("zapret", "strategy", default="general.bat")
        was_running = self.controller.zapret.is_running()
        results: list[StrategyResult] = []
        self.last_results = results
        try:
            if was_running and not force:
                self.controller.operation_note = "Проверяем текущую стратегию…"
                current = self.probe_current()
                results.append(current)
                if current.ok:
                    self._remember(current.strategy)
                    return current
                # No useful network at all -> do not churn through 21 profiles and
                # replace a known configuration while the machine may simply be offline.
                neutral = self._probe(NEUTRAL_PROBE, timeout=2.0)
                if not neutral.ok and not any(p.ok for p in current.probes):
                    log.info("Internet appears unavailable; strategy tuning deferred")
                    current.ok = True
                    current.probes.append(neutral)
                    return current

            candidates = self._ordered_candidates()
            best = None
            for index, name in enumerate(candidates, 1):
                self.controller.operation_note = f"Подбираем стратегию {index}/{len(candidates)}: {name}"
                if self.controller.zapret.is_running():
                    self.controller.zapret.stop()
                self.config.data["zapret"]["strategy"] = name
                self.config.save()
                try:
                    self.controller.zapret.start()
                except Exception as exc:
                    r = StrategyResult(name, False, -10**9, 99999, [
                        ProbeResult("winws", False, 0, str(exc).splitlines()[0][:180])
                    ])
                    results.append(r)
                    continue

                # Small stabilization window after the WinDivert filter is installed.
                time.sleep(0.18)
                r = self.probe_current()
                r.strategy = name
                results.append(r)
                if r.ok and (best is None or r.score > best.score):
                    best = r
                    # A clean 3/3 result is enough; do not waste time testing more.
                    if sum(p.ok for p in r.probes) == len(r.probes):
                        break

            if best is None:
                self.config.data["zapret"]["strategy"] = original
                self.config.save()
                if self.controller.zapret.is_running():
                    self.controller.zapret.stop()
                if was_running:
                    self.controller.zapret.start()
                raise RuntimeError("Автоподбор не нашёл стратегию, прошедшую минимум 2 из 3 проверок")

            if self.controller.zapret.is_running():
                self.controller.zapret.stop()
            self.config.data["zapret"]["strategy"] = best.strategy
            self._remember(best.strategy)
            self.config.save()
            self.controller.zapret.start()
            return best
        finally:
            self.controller.operation_note = ""
            self.running = False

    def ensure_working(self) -> StrategyResult | None:
        if self.config.get("zapret", "strategy_mode", default="auto") != "auto":
            return None
        return self.tune(force=False)

    def _remember(self, name: str):
        self.config.data["zapret"]["strategy"] = name
        self.config.data["zapret"]["last_good_strategy"] = name
        self.config.save()
