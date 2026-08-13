from __future__ import annotations
import re
import shlex
from pathlib import Path


def available_strategies(strategy_dir: Path) -> list[str]:
    preferred = ["general.bat"]
    rest = sorted((p.name for p in strategy_dir.glob("general*.bat") if p.name != "general.bat"), key=str.casefold)
    return preferred + rest if (strategy_dir / "general.bat").exists() else rest


def _game_ports(mode: str) -> tuple[str, str]:
    mode = (mode or "off").lower()
    if mode == "all":
        return "1024-65535", "1024-65535"
    if mode == "tcp":
        return "1024-65535", "12"
    if mode == "udp":
        return "12", "1024-65535"
    return "12", "12"


def parse_strategy(strategy_file: Path, bin_dir: Path, lists_dir: Path, game_filter: str = "off") -> list[str]:
    """Extract exactly the winws command-line portion from Flowseal strategy BAT.

    We intentionally do not execute service.bat. Zapret+ owns process/service lifecycle,
    but preserves the upstream BIN/LISTS/GameFilter substitutions.
    """
    text = strategy_file.read_text(encoding="utf-8-sig", errors="replace")
    lines: list[str] = []
    collecting = False
    for raw in text.splitlines():
        line = raw.strip()
        if not collecting and "winws.exe" in line.lower():
            collecting = True
            pos = line.lower().find("winws.exe")
            tail = line[pos + len("winws.exe"):]
            if tail.startswith('"'):
                tail = tail[1:]
            lines.append(tail.rstrip("^ "))
            continue
        if collecting:
            if not line or line.startswith("::"):
                break
            lines.append(line.rstrip("^ "))

    joined = " ".join(lines)
    tcp_game, udp_game = _game_ports(game_filter)
    replacements = {
        "%BIN%": str(bin_dir) + "\\",
        "%LISTS%": str(lists_dir) + "\\",
        "%GameFilterTCP%": tcp_game,
        "%GameFilterUDP%": udp_game,
    }
    for old, new in replacements.items():
        joined = joined.replace(old, new)

    tokens = shlex.split(joined, posix=False)
    clean: list[str] = []
    for token in tokens:
        if not token:
            continue
        if len(token) >= 2 and token[0] == token[-1] == '"':
            token = token[1:-1]
        if '=\"' in token and token.endswith('\"'):
            key, value = token.split('=', 1)
            token = key + '=' + value[1:-1]
        clean.append(token)
    return clean
