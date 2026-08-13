from __future__ import annotations
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any
from .paths import CONFIG_PATH

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "start_minimized": False,
        "start_with_windows": False,
        "auto_start_protection": False,
        "check_updates": True,
        "auto_update": True,
        "auto_update_zapret": True,
        "notify_updates": True,
        "theme": "light",
        "theme_user_selected": False,
        "accent_color": "#3B82F6",
        "compact_sidebar": False,
        "auto_recover": True,
        "watchdog_interval": 5,
        "ui_defaults_version": 1,
    },
    "zapret": {
        "enabled": True,
        "strategy": "general.bat",
        "game_filter": "off",  # off | all | tcp | udp
        "strategy_mode": "auto",  # auto | manual
        "last_good_strategy": "general.bat",
        "auto_strategy_max_candidates": 21,
    },
    "telegram": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 1443,
        "secret": "",
        "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
        "buffer_kb": 256,
        "pool_size": 4,
        "cfproxy": True,
        "cfproxy_user_domain_enabled": False,
        "cfproxy_user_domain": [],
        "cfproxy_worker_enabled": False,
        "cfproxy_worker_domain": [],
        "force_test_dc": False,
        "auto_configure_client": True,
        "setup_signature": "",
    },
    "sites": [],
}


def _merge(default: dict, current: dict) -> dict:
    result = deepcopy(default)
    for k, v in current.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    # migrate 0.1 bool game_filter
    gf = result.get("zapret", {}).get("game_filter")
    if isinstance(gf, bool):
        result["zapret"]["game_filter"] = "all" if gf else "off"
    return result


class ConfigManager:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data = deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> dict:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = _merge(DEFAULT_CONFIG, raw)
                    # One-time UI migration: older development builds may carry a dark
                    # startup preference. 0.6.1 establishes light as the official default.
                    raw_app = raw.get("app", {}) if isinstance(raw.get("app", {}), dict) else {}
                    if "ui_defaults_version" not in raw_app:
                        self.data["app"]["theme"] = "light"
                        self.data["app"]["theme_user_selected"] = False
                        self.data["app"]["ui_defaults_version"] = 1
                        self.save()
            except Exception:
                self.data = deepcopy(DEFAULT_CONFIG)
        if not self.data["telegram"].get("secret"):
            self.data["telegram"]["secret"] = os.urandom(16).hex()
            self.save()
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, *keys, default=None):
        cur = self.data
        for key in keys:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur
