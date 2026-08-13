from __future__ import annotations

import ctypes
import os
import sys
import traceback
from pathlib import Path


def _data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
        path = base / "ZapretPlus"
    else:
        path = Path.home() / ".config" / "zapretplus"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


STARTUP_LOG = _data_dir() / "startup.log"


def _sanitize_startup_text(message: object) -> str:
    text = str(message)
    private_paths = {
        os.environ.get("USERPROFILE", ""),
        os.environ.get("HOME", ""),
        os.environ.get("APPDATA", ""),
        os.environ.get("LOCALAPPDATA", ""),
        str(Path.home()),
    }
    for value in sorted((p for p in private_paths if p), key=len, reverse=True):
        text = text.replace(value, "<USER>")
        text = text.replace(value.replace("\\\\", "/"), "<USER>")
    return text


def _startup_note(message: str) -> None:
    try:
        STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with STARTUP_LOG.open("a", encoding="utf-8") as fh:
            fh.write(_sanitize_startup_text(message).rstrip() + "\n")
    except Exception:
        pass


def _native_message(title: str, message: str, error: bool = False) -> None:
    if sys.platform == "win32":
        try:
            flags = 0x10 if error else 0x40
            ctypes.windll.user32.MessageBoxW(None, message, title, flags)
            return
        except Exception:
            pass


def _activate_existing_window() -> bool:
    """Bring an existing Zapret+ UI to the foreground.

    We intentionally use a real window as the single-instance signal instead of a
    global mutex. Old/crashed background builds can keep a process alive without a
    window; they must not permanently block a newer portable build from starting.
    """
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        matches = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @WNDENUMPROC
        def enum_proc(hwnd, lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if title == "Zapret+" or title.startswith("Zapret+ "):
                # Only treat an actually visible top-level UI as the running instance.
                # Hidden/tray HWNDs from older builds must not block a clean launch.
                if user32.IsWindowVisible(hwnd):
                    matches.append(hwnd)
                    return False
            return True

        user32.EnumWindows(enum_proc, 0)
        if not matches:
            return False

        hwnd = matches[0]
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _show_fatal(message: str) -> None:
    _startup_note("FATAL: " + message)
    _native_message("Zapret+ — ошибка запуска", message, error=True)


def _run_application() -> int:
    """Import the actual application only after bootstrap logging is active."""
    _startup_note("loading application modules")

    import logging
    from PySide6.QtGui import QIcon
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from core.admin import is_admin, relaunch_as_admin
    from core.config import ConfigManager
    from core.controller import AppController
    from core.logging_setup import setup_logging
    from core.paths import ROOT
    from ui.main_window import MainWindow
    from ui.style import LIGHT_STYLE

    _startup_note("application modules loaded")
    _startup_note("single-instance policy: visible-window activation (no global mutex)")

    # Dev/source launches may still need elevation. Production EXE has uac_admin=True,
    # so it normally arrives here already elevated.
    if sys.platform == "win32" and not is_admin():
        _startup_note("requesting elevation")
        if relaunch_as_admin():
            _startup_note("elevated child requested; parent exits")
            return 0
        _startup_note("UAC elevation cancelled or failed")

    if _activate_existing_window():
        _startup_note("existing Zapret+ window activated")
        return 0

    setup_logging()
    logging.getLogger(__name__).info("Zapret+ bootstrap complete")
    _startup_note("logging ready")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(LIGHT_STYLE)

    icon_path = ROOT / "assets" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    _startup_note(f"QApplication ready; ROOT={ROOT}; icon={icon_path.exists()}")

    config = ConfigManager()
    _startup_note("config ready")
    controller = AppController(config)
    _startup_note("controller ready")
    win = MainWindow(controller, config)
    _startup_note("main window constructed")

    ok, ui_problem = win.validate_ui()
    if not ok:
        raise RuntimeError("Интерфейс не был построен: " + ui_problem)
    _startup_note("UI structure validated")

    if "--post-update-marker" in sys.argv:
        try:
            i = sys.argv.index("--post-update-marker")
            marker = sys.argv[i + 1]
            Path(marker).parent.mkdir(parents=True, exist_ok=True)
            Path(marker).write_text("ok", encoding="utf-8")
        except Exception:
            logging.getLogger(__name__).exception("Could not create post-update marker")

    minimized = "--minimized" in sys.argv
    if not minimized:
        win.show()
        win.raise_()
        win.activateWindow()
        # Force one real paint before status checks, tray setup, networking or engine work.
        app.processEvents()
        _startup_note("first UI frame painted")
    else:
        _startup_note("explicit minimized launch")

    def after_first_paint():
        try:
            win.finish_initialization()
            _startup_note("deferred runtime initialization complete")
        except Exception:
            logging.getLogger(__name__).exception("Deferred initialization failed")
            _startup_note("deferred runtime initialization failed; UI remains available")

        try:
            win.init_optional_services()
            _startup_note("optional services initialized")
        except Exception:
            logging.getLogger(__name__).exception("Optional services failed")
            _startup_note("optional services failed; UI remains available")

        # Never call controller.start_all() on the GUI thread. toggle_all() uses EngineWorker.
        if "--auto-start" in sys.argv or config.get("app", "auto_start_protection", default=False):
            if not controller.active:
                win.toggle_all()
                _startup_note("auto-start queued asynchronously")

    # Give Windows a moment to finish showing the first frame before any runtime probing.
    QTimer.singleShot(120, after_first_paint)

    rc = app.exec()
    try:
        controller.stop_all()
    except Exception:
        logging.getLogger(__name__).exception("Could not stop engines on exit")
    return rc


def guarded_main() -> int:
    _startup_note("\n=== Zapret+ startup ===")
    _startup_note(f"python={sys.version}")
    _startup_note(f"frozen={getattr(sys, 'frozen', False)}")
    _startup_note("startup context captured with private paths redacted")

    try:
        return _run_application()
    except BaseException as exc:
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _startup_note(details)
        _show_fatal(
            "Zapret+ не смог запуститься.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Подробный отчёт сохранён в:\n"
            f"{STARTUP_LOG}"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(guarded_main())
