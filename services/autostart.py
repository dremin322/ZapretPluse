from __future__ import annotations
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "ZapretPlus"


def set_autostart(enabled: bool):
    if sys.platform != "win32":
        return
    import winreg
    exe = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()
    value = f'"{exe}" --minimized --auto-start'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass
