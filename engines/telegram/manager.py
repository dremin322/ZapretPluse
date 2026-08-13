from __future__ import annotations
import asyncio
import hashlib
import importlib
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from core.paths import TG_PROXY_DIR

log = logging.getLogger(__name__)


class TelegramManager:
    def __init__(self, config):
        self.config = config
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop_event: asyncio.Event | None = None
        self.last_error = ""
        self.proxy_pkg = None
        self.proxy_config_module = None
        self.proxy_config = None
        self.proxy_runner = None
        self.proxy_stats = None
        self.main_task = None
        self._stopping = False
        # User intent is separate from thread state. A normal shutdown must never
        # be reported as a crash just because upstream cleanup finishes late.
        self.desired_running = False

    def _ensure_loaded(self):
        """Import the relatively heavy proxy core only when it is actually needed."""
        if self.proxy_runner is not None:
            return
        parent = str(TG_PROXY_DIR.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        self.proxy_pkg = importlib.import_module("telegram_proxy")
        self.proxy_config_module = importlib.import_module("telegram_proxy.config")
        self.proxy_config = self.proxy_config_module.proxy_config
        self.proxy_runner = importlib.import_module("telegram_proxy.tg_ws_proxy")
        self.proxy_stats = importlib.import_module("telegram_proxy.stats").stats

    def _reset_stats(self):
        """Start every proxy session with clean counters."""
        if self.proxy_stats is None:
            return
        for name in (
            "connections_total", "connections_active", "connections_ws",
            "connections_tcp_fallback", "connections_cfproxy",
            "connections_fronting", "connections_bad", "connections_masked",
            "ws_errors", "bytes_up", "bytes_down", "pool_hits", "pool_misses",
            "cf_pool_hits", "cf_pool_misses",
        ):
            if hasattr(self.proxy_stats, name):
                setattr(self.proxy_stats, name, 0)

    @staticmethod
    def _port_is_listening(host: str, port: int, timeout: float = 0.12) -> bool:
        """Return True only when something actively accepts TCP on host:port."""
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((host, int(port))) == 0
        except OSError:
            return False

    def _wait_port_free(self, timeout: float = 3.0) -> bool:
        cfg = self.config.data["telegram"]
        host = cfg.get("host", "127.0.0.1")
        port = int(cfg.get("port", 1443))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._port_is_listening(host, port):
                return True
            time.sleep(0.04)
        return not self._port_is_listening(host, port)

    def _close_listener_now(self):
        """Close tg-ws-proxy's asyncio.Server even if upstream is still warming pools.

        Upstream creates the listening socket before pool warm-up. Cancelling _run()
        during that warm-up can bypass its later server-cleanup block. Closing the
        server explicitly here prevents a stale 127.0.0.1 listener and WinError 10048
        on the next start.
        """
        if self.proxy_runner is None or self.loop is None:
            return
        server = getattr(self.proxy_runner, "_server_instance", None)
        if server is None:
            return

        done = threading.Event()

        def close_server():
            try:
                server.close()
            finally:
                done.set()

        try:
            self.loop.call_soon_threadsafe(close_server)
            done.wait(timeout=0.7)
        except (RuntimeError, AttributeError):
            pass

    def _clear_upstream_server_ref(self):
        if self.proxy_runner is not None:
            try:
                self.proxy_runner._server_instance = None
            except Exception:
                pass

    def _apply_config(self):
        self._ensure_loaded()
        cfg = self.config.data["telegram"]
        pc = self.proxy_config
        pc.host = cfg.get("host", "127.0.0.1")
        pc.port = int(cfg.get("port", 1443))
        pc.secret = cfg["secret"]
        pc.dc_redirects = self.proxy_config_module.parse_dc_ip_list(
            cfg.get("dc_ip", ["2:149.154.167.220", "4:149.154.167.220"])
        )
        pc.buffer_size = max(4, int(cfg.get("buffer_kb", 256))) * 1024
        pc.pool_size = max(0, int(cfg.get("pool_size", 4)))
        pc.fallback_cfproxy = bool(cfg.get("cfproxy", True))
        coerce = self.proxy_config_module.coerce_domain_list
        user_domains = coerce(cfg.get("cfproxy_user_domain", []))
        worker_domains = coerce(cfg.get("cfproxy_worker_domain", []))
        pc.cfproxy_user_domains = user_domains if cfg.get("cfproxy_user_domain_enabled", False) else []
        pc.cfproxy_worker_domains = worker_domains if cfg.get("cfproxy_worker_enabled", False) else []
        pc.fake_tls_domain = ""
        pc.proxy_protocol = False
        pc.force_test_dc = bool(cfg.get("force_test_dc", False))

    def _listener_ready(self) -> bool:
        if self.proxy_runner is None:
            return False
        server = getattr(self.proxy_runner, "_server_instance", None)
        if server is None:
            return False
        sockets = getattr(server, "sockets", None) or []
        return any(getattr(sock, "fileno", lambda: -1)() >= 0 for sock in sockets)

    def is_running(self) -> bool:
        return bool(self.desired_running and self.thread and self.thread.is_alive() and self._listener_ready() and not self.last_error)

    def start(self, timeout: float = 8.0):
        if self.is_running():
            return

        self._ensure_loaded()

        # Never start a second event-loop/thread on top of a previous session.
        if self.thread and self.thread.is_alive():
            self.stop()
            if self.thread and self.thread.is_alive():
                raise RuntimeError("Предыдущий Telegram Proxy ещё завершает работу. Повторите через секунду.")

        # Clean a stale server object left by an interrupted upstream warm-up.
        self._close_listener_now()
        self._clear_upstream_server_ref()

        cfg = self.config.data["telegram"]
        host = cfg.get("host", "127.0.0.1")
        port = int(cfg.get("port", 1443))
        if not self._wait_port_free(timeout=1.5):
            raise RuntimeError(
                f"Локальный порт {host}:{port} всё ещё занят. "
                "Zapret+ не будет запускать второй экземпляр Telegram Proxy."
            )

        self.desired_running = True
        self._stopping = False
        self._apply_config()
        self._reset_stats()
        self.last_error = ""

        def runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            self.stop_event = asyncio.Event()
            try:
                self.main_task = loop.create_task(
                    self.proxy_runner._run(stop_event=self.stop_event)
                )
                loop.run_until_complete(self.main_task)
            except asyncio.CancelledError:
                if self.desired_running and not self._stopping:
                    self.last_error = "Telegram Proxy был неожиданно отменён"
            except Exception as exc:
                if self.desired_running and not self._stopping:
                    self.last_error = str(exc) or exc.__class__.__name__
                    log.exception("Telegram proxy crashed")
                else:
                    log.debug("Telegram proxy finished during shutdown: %s", exc)
            finally:
                self.main_task = None
                # The manager owns the lifecycle, so make sure the listening socket is
                # closed even when upstream cancellation happened before its own finally.
                server = getattr(self.proxy_runner, "_server_instance", None)
                if server is not None:
                    try:
                        server.close()
                        loop.run_until_complete(server.wait_closed())
                    except BaseException:
                        pass
                self._clear_upstream_server_ref()

                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except BaseException:
                    pass
                loop.close()
                self.loop = None
                self.stop_event = None

        self.thread = threading.Thread(
            target=runner, daemon=True, name="ZapretPlus-TelegramProxy"
        )
        self.thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.last_error:
                self.desired_running = False
                raise RuntimeError(self.last_error)
            if self._listener_ready():
                return
            if not self.thread.is_alive():
                self.desired_running = False
                raise RuntimeError(
                    self.last_error or "Telegram Proxy завершился при запуске"
                )
            time.sleep(0.05)

        self.desired_running = False
        self.stop()
        raise RuntimeError(
            f"Telegram Proxy не открыл локальный порт "
            f"{self.proxy_config.host}:{self.proxy_config.port}"
        )

    def stop(self):
        """Fully stop the proxy and do not return until its local listener is gone."""
        self.desired_running = False
        self._stopping = True
        self.last_error = ""
        thread = self.thread

        try:
            # Ask upstream to finish normally.
            if self.loop and self.stop_event:
                try:
                    self.loop.call_soon_threadsafe(self.stop_event.set)
                except RuntimeError:
                    pass

            # Close the socket immediately. This is essential when upstream is still
            # inside pool warm-up and has not reached its normal stop_event wait loop.
            self._close_listener_now()

            if thread:
                thread.join(timeout=1.0)

            # If warm-up is still running, cancel the main task after the socket is
            # already closed. runner.finally performs the remaining loop cleanup.
            if thread and thread.is_alive() and self.loop and self.main_task:
                try:
                    self.loop.call_soon_threadsafe(self.main_task.cancel)
                except (RuntimeError, AttributeError):
                    pass
                thread.join(timeout=3.5)

            # Do a second listener close in case upstream recreated the server while
            # shutdown was racing with its watchdog.
            self._close_listener_now()
            self._clear_upstream_server_ref()
            self._wait_port_free(timeout=2.0)

            # A late daemon cleanup is not a red user-facing error after explicit stop.
            self.last_error = ""
        finally:
            if not (thread and thread.is_alive()):
                self.thread = None
            self._stopping = False

    def restart(self):
        self.stop(); self.start()

    def proxy_url(self) -> str:
        self._ensure_loaded()
        cfg = self.config.data["telegram"]
        host = self.proxy_pkg.get_link_host(cfg.get("host", "127.0.0.1"))
        return f"tg://proxy?server={host}&port={int(cfg.get('port',1443))}&secret=dd{cfg['secret']}"

    def setup_signature(self) -> str:
        return hashlib.sha256(self.proxy_url().encode("utf-8")).hexdigest()[:24]

    def needs_client_setup(self) -> bool:
        cfg = self.config.data["telegram"]
        return bool(cfg.get("auto_configure_client", True) and cfg.get("setup_signature", "") != self.setup_signature())

    def open_in_telegram(self, mark_configured: bool = False) -> bool:
        url = self.proxy_url()
        try:
            # os.startfile uses the Windows URL protocol association directly and is
            # more reliable for tg:// than webbrowser.open().
            if os.name == "nt":
                os.startfile(url)
                ok = True
            else:
                ok = bool(webbrowser.open(url))
        except Exception as exc:
            self.last_error = f"Не удалось открыть Telegram: {exc}"
            log.exception("Could not open tg:// proxy URL")
            return False
        if ok and mark_configured:
            self.config.data["telegram"]["setup_signature"] = self.setup_signature()
            self.config.save()
        return ok

    def health(self) -> dict:
        running = self.is_running()
        visible_error = self.last_error if self.desired_running else ""
        if self.proxy_stats is None:
            return {
                "running": False,
                "listener": False,
                "client_seen": False,
                "active_connections": 0,
                "bytes_up": 0,
                "bytes_down": 0,
                "ws_errors": 0,
                "bad_connections": 0,
                "error": visible_error,
            }
        stats = self.proxy_stats
        return {
            "running": running,
            "listener": self._listener_ready(),
            "client_seen": int(getattr(stats, "connections_total", 0)) > 0,
            "active_connections": int(getattr(stats, "connections_active", 0)),
            "bytes_up": int(getattr(stats, "bytes_up", 0)),
            "bytes_down": int(getattr(stats, "bytes_down", 0)),
            "ws_errors": int(getattr(stats, "ws_errors", 0)),
            "bad_connections": int(getattr(stats, "connections_bad", 0)),
            "error": visible_error,
        }
