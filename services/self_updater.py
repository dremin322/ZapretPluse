from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from core.paths import DATA_DIR, ROOT


@dataclass
class SelfUpdatePlan:
    old_version: str
    new_version: str
    stage_dir: Path
    helper_script: Path
    marker: Path


def _ps(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def find_package_root(extracted: Path) -> Path:
    """Locate the inner application directory in a verified release archive."""
    candidates: list[Path] = []
    for exe in extracted.rglob("ZapretPlus.exe"):
        root = exe.parent
        # The real onedir app contains runtime. The outer release launcher does not.
        if (root / "runtime").is_dir():
            candidates.append(root)
    if candidates:
        return min(candidates, key=lambda p: len(p.parts))

    # Source/development package fallback.
    for p in extracted.rglob("version.py"):
        root = p.parent
        if (root / "main.py").is_file() and (root / "runtime").is_dir():
            candidates.append(root)
    if candidates:
        return min(candidates, key=lambda p: len(p.parts))

    raise RuntimeError(
        "В пакете обновления не найдена папка приложения "
        "(ZapretPlus.exe/main.py + runtime)"
    )


def prepare_plan(old_version: str, new_version: str, package_root: Path) -> SelfUpdatePlan:
    """Copy an already verified release package to a persistent staging folder.

    The caller is responsible for download + SHA-256 verification. This function only
    prepares the Windows helper which applies the update after the current process exits.
    """
    updates = DATA_DIR / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    stage = updates / f"app-stage-{new_version.lstrip('v')}"
    shutil.rmtree(stage, ignore_errors=True)
    payload = stage / "payload"
    shutil.copytree(package_root, payload)

    frozen_payload = (payload / "ZapretPlus.exe").is_file()
    source_payload = (payload / "main.py").is_file()
    if not (frozen_payload or source_payload):
        raise RuntimeError("Пакет Zapret+ не содержит точки запуска")
    if not (payload / "runtime").is_dir():
        raise RuntimeError("Пакет Zapret+ не содержит runtime")
    if frozen_payload and not (payload / "assets").is_dir():
        raise RuntimeError("Пакет Zapret+ не содержит assets")

    marker = updates / f"app-update-ok-{int(time.time())}.marker"
    marker.unlink(missing_ok=True)
    backup = DATA_DIR / "backups" / "app" / f"{old_version}-{time.strftime('%Y%m%d-%H%M%S')}"
    helper = updates / "apply-zapretplus-update.ps1"

    if getattr(sys, "frozen", False):
        launch = ROOT / Path(sys.executable).name
        launch_args = f'--post-update-marker \"{marker}\"'
        rollback_args = ""
    else:
        launch = Path(sys.executable)
        main_script = ROOT / "main.py"
        launch_args = f'"{main_script}" --post-update-marker "{marker}"'
        rollback_args = f'"{main_script}"'

    lines = [
        '$ErrorActionPreference = "Stop"',
        f'$pidToWait = {os.getpid()}',
        f'$src = {_ps(payload)}',
        f'$dst = {_ps(ROOT)}',
        f'$backup = {_ps(backup)}',
        f'$marker = {_ps(marker)}',
        f'$launch = {_ps(launch)}',
        f'$launchArgs = {_ps(launch_args)}',
        'try { Wait-Process -Id $pidToWait -Timeout 30 -ErrorAction SilentlyContinue } catch {}',
        'Start-Sleep -Milliseconds 500',
        'New-Item -ItemType Directory -Force -Path $backup | Out-Null',
        '& robocopy $dst $backup /MIR /R:1 /W:1 /XD .venv __pycache__ | Out-Null',
        'if ($LASTEXITCODE -ge 8) { throw "Backup failed: robocopy $LASTEXITCODE" }',
        '& robocopy $src $dst /E /R:2 /W:1 /XD __pycache__ | Out-Null',
        'if ($LASTEXITCODE -ge 8) { throw "Install failed: robocopy $LASTEXITCODE" }',
        '$p = Start-Process -FilePath $launch -ArgumentList $launchArgs -PassThru',
        '$ok = $false',
        'for ($i=0; $i -lt 60; $i++) {',
        '  if (Test-Path $marker) { $ok = $true; break }',
        '  if ($p.HasExited) { break }',
        '  Start-Sleep -Milliseconds 500',
        '}',
        'if (-not $ok) {',
        '  try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch {}',
        '  & robocopy $backup $dst /MIR /R:2 /W:1 /XD .venv __pycache__ | Out-Null',
        '  if ($LASTEXITCODE -ge 8) { throw "Rollback failed: robocopy $LASTEXITCODE" }',
    ]
    if rollback_args:
        lines.append(f'  Start-Process -FilePath $launch -ArgumentList {_ps(rollback_args)}')
    else:
        lines.append('  Start-Process -FilePath $launch')
    lines += [
        '  exit 2',
        '}',
        'Remove-Item -Force $marker -ErrorAction SilentlyContinue',
        'Remove-Item -Recurse -Force $backup -ErrorAction SilentlyContinue',
        f'Remove-Item -Recurse -Force {_ps(stage)} -ErrorAction SilentlyContinue',
        'exit 0',
    ]
    helper.write_text("\r\n".join(lines), encoding="utf-8-sig")
    return SelfUpdatePlan(old_version, new_version, stage, helper, marker)


def launch(plan: SelfUpdatePlan):
    if os.name != "nt":
        raise RuntimeError("Self-update Zapret+ поддерживается только на Windows")
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(plan.helper_script)],
        creationflags=0x08000000,
        close_fds=True,
    )
