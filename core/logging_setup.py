from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import LOG_PATH


def _private_path_values() -> list[str]:
    values: set[str] = set()
    for key in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(key)
        if value:
            values.add(str(Path(value)))
    try:
        values.add(str(Path.home()))
    except Exception:
        pass
    return sorted((v for v in values if v), key=len, reverse=True)


_PRIVATE_PATHS = _private_path_values()


def sanitize_log_text(value: object) -> str:
    """Remove local user paths and proxy credentials from persisted logs."""
    text = str(value)
    for path in _PRIVATE_PATHS:
        text = text.replace(path, "<USER>")
        text = text.replace(path.replace("\\", "/"), "<USER>")

    text = re.sub(
        r'(?i)(secret\s*[:=]\s*)(?:dd|ee)?[0-9a-f]{32,}',
        r'\1<REDACTED>',
        text,
    )
    text = re.sub(
        r'(?i)([?&]secret=)(?:dd|ee)?[0-9a-f]+',
        r'\1<REDACTED>',
        text,
    )
    text = re.sub(r'(?i)tg://proxy\?[^\s]+', 'tg://proxy?<REDACTED>', text)
    return text


class PrivacyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return sanitize_log_text(super().format(record))


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(
        PrivacyFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
