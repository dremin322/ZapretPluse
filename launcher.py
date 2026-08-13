from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def _message(title: str, text: str, error: bool = True) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
            return
        except Exception:
            pass


def _is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _find_target(root: Path) -> Path | None:
    candidates = [
        root / "ZapretPlus" / "ZapretPlus.exe",
        root / "ZapretPlus.exe",
    ]
    current = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    for p in candidates:
        try:
            if p.exists() and (current is None or p.resolve() != current):
                return p
        except Exception:
            continue
    return None


def _run_target(target: Path) -> int:
    # The launcher itself is built with requireAdministrator. Therefore the normal
    # subprocess path inherits the elevated token and does not trigger WinError 740.
    try:
        subprocess.Popen(
            [str(target)],
            cwd=str(target.parent),
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            close_fds=True,
        )
        return 0
    except OSError as exc:
        # Defensive fallback for old/mismatched builds: if Windows still reports
        # elevation-required, ask ShellExecute to launch the target with runas.
        if os.name == "nt" and getattr(exc, "winerror", None) == 740:
            try:
                rc = ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "runas",
                    str(target),
                    None,
                    str(target.parent),
                    1,
                )
                if int(rc) > 32:
                    return 0
            except Exception:
                pass
        raise


def main() -> int:
    root = _root()
    target = _find_target(root)

    if target is None:
        _message(
            "Zapret+",
            "Не найден основной файл приложения:\n\n"
            f"{root}\\ZapretPlus\\ZapretPlus.exe\n\n"
            "Не перемещайте корневой ZapretPlus.exe отдельно от папки ZapretPlus.",
        )
        return 2

    try:
        return _run_target(target)
    except BaseException as exc:
        _message(
            "Zapret+ — ошибка запуска",
            "Не удалось запустить Zapret+.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            f"Основной файл:\n{target}",
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
