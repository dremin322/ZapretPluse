from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Relaunch exactly once with a stable absolute command line."""
    if sys.platform != "win32" or is_admin():
        return False

    executable = sys.executable
    if getattr(sys, "frozen", False):
        # For a packaged exe ShellExecute already receives the executable path;
        # repeating argv[0] in parameters may break startup on some systems.
        args = list(sys.argv[1:])
        workdir = str(Path(executable).resolve().parent)
    else:
        script = str(Path(sys.argv[0]).resolve())
        args = [script, *sys.argv[1:]]
        workdir = str(Path(script).parent)

    params = subprocess.list2cmdline(args)
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, params, workdir or os.getcwd(), 1
    )
    return rc > 32
