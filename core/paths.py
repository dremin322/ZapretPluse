from __future__ import annotations
import os
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
        path = base / "ZapretPlus"
    else:
        path = Path.home() / ".config" / "zapretplus"
    path.mkdir(parents=True, exist_ok=True)
    return path


ROOT = app_root()
DATA_DIR = user_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "zapretplus.log"
WINWS_LOG_PATH = DATA_DIR / "winws.log"
ZAPRET_DIR = ROOT / "runtime" / "zapret"
TG_PROXY_DIR = ROOT / "runtime" / "telegram_proxy"
