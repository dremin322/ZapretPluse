from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import certifi

from core.paths import DATA_DIR, ROOT, ZAPRET_DIR
from version import __version__, APP_UPDATE_REPO

log = logging.getLogger(__name__)
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
UA = f"ZapretPlus/{__version__}"


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0
    digest: str = ""


@dataclass
class ReleaseInfo:
    repo: str
    tag: str
    page: str
    notes: str
    assets: list[Asset]

    def zip_asset(self, contains: str = "") -> Asset | None:
        contains = contains.lower()
        candidates = [a for a in self.assets if a.name.lower().endswith(".zip")]
        if contains:
            preferred = [a for a in candidates if contains in a.name.lower()]
            if preferred:
                candidates = preferred
        return candidates[0] if candidates else None


@dataclass
class UpdateResult:
    component: str
    old_version: str
    new_version: str
    changed: bool
    message: str
    restart_required: bool = False


def _ssl_context():
    return ssl.create_default_context(cafile=certifi.where())


def _request_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.load(resp)


def latest_release(repo: str, timeout: float = 10.0) -> ReleaseInfo | None:
    try:
        data = _request_json(GITHUB_API.format(repo=repo), timeout)
        assets = []
        for item in data.get("assets", []):
            assets.append(Asset(
                name=str(item.get("name", "")),
                url=str(item.get("browser_download_url", "")),
                size=int(item.get("size") or 0),
                digest=str(item.get("digest") or ""),
            ))
        return ReleaseInfo(
            repo=repo,
            tag=str(data.get("tag_name", "")),
            page=str(data.get("html_url", "")),
            notes=str(data.get("body", "")),
            assets=assets,
        )
    except Exception as exc:
        log.warning("Update check failed for %s: %s", repo, exc)
        return None


def _version_key(value: str):
    value = value.strip().lower().lstrip("v")
    # Good enough for upstream tags like 1.10.1, 1.9.9a, 0.3.0.
    parts = re.findall(r"\d+|[a-z]+", value)
    out = []
    for p in parts:
        out.append((0, int(p)) if p.isdigit() else (1, p))
    return tuple(out)


def is_newer(remote: str, local: str) -> bool:
    try:
        return _version_key(remote) > _version_key(local)
    except Exception:
        return remote.strip().lstrip("v") != local.strip().lstrip("v")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _expected_sha256(asset: Asset) -> str:
    digest = (asset.digest or "").strip().lower()
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1]
    return ""


def download_asset(asset: Asset, dest: Path, timeout: float = 60.0, require_digest: bool = True) -> str:
    expected = _expected_sha256(asset)
    if require_digest and not expected:
        raise RuntimeError(
            "GitHub не опубликовал SHA-256 digest для этого release asset. "
            "Автоматическая установка отменена: файл не будет установлен без проверки целостности."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(asset.url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as src, tmp.open("wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)
    actual = sha256_file(tmp)
    if expected and actual.lower() != expected.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 не совпал: ожидался {expected}, получен {actual}")
    tmp.replace(dest)
    return actual


def safe_extract_zip(archive: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"Небезопасный путь в ZIP: {info.filename}")
        zf.extractall(destination)


def _zapret_package_root(extracted: Path) -> Path:
    candidates = []
    for p in extracted.rglob("winws.exe"):
        if p.parent.name.lower() == "bin":
            root = p.parent.parent
            if (root / "lists").is_dir():
                candidates.append(root)
    if not candidates:
        raise RuntimeError("В архиве релиза не найдена ожидаемая структура Zapret (bin/winws.exe + lists)")
    return min(candidates, key=lambda p: len(p.parts))




def _read_local_telegram_version() -> str:
    """Read the embedded tg-ws-proxy version without importing the proxy package."""
    init_py = ROOT / "runtime" / "telegram_proxy" / "__init__.py"
    try:
        text = init_py.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return "unknown"

def _read_local_zapret_version() -> str:
    p = ZAPRET_DIR / "version.txt"
    return p.read_text(encoding="utf-8", errors="replace").strip() if p.exists() else "0"


def _copytree_replace(src: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _backup_dir(component: str, version: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = DATA_DIR / "backups" / component
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{version}-{stamp}"


def _prune_backups(component: str, keep: int = 2):
    base = DATA_DIR / "backups" / component
    if not base.exists():
        return
    rows = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    for p in rows[keep:]:
        shutil.rmtree(p, ignore_errors=True)


class UpdateManager:
    ZAPRET_REPO = "Flowseal/zapret-discord-youtube"
    TELEGRAM_REPO = "Flowseal/tg-ws-proxy"

    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.last_status = ""

    def check(self) -> dict:
        local_z = _read_local_zapret_version()
        zr = latest_release(self.ZAPRET_REPO)
        tr = latest_release(self.TELEGRAM_REPO)
        ar = latest_release(APP_UPDATE_REPO) if APP_UPDATE_REPO else None
        return {
            "zapret": {
                "local": local_z,
                "remote": zr.tag if zr else "",
                "available": bool(zr and is_newer(zr.tag, local_z)),
                "release": zr,
            },
            "telegram": {
                "local": _read_local_telegram_version(),
                "remote": tr.tag if tr else "",
                # The embedded Python core is API-coupled to Zapret+. We detect upstream
                # releases independently, but install them only through a compatible
                # Zapret+ release instead of hot-swapping live proxy code.
                "available": bool(
                    tr
                    and _read_local_telegram_version() != "unknown"
                    and is_newer(tr.tag, _read_local_telegram_version())
                ),
                "release": tr,
                "managed_by_app": True,
                "install_mode": "compatible_app_release",
            },
            "app": {
                "local": __version__,
                "remote": ar.tag if ar else "",
                "available": bool(ar and is_newer(ar.tag, __version__)),
                "release": ar,
                "configured": bool(APP_UPDATE_REPO),
            },
        }

    def install_zapret(self, release: ReleaseInfo | None = None) -> UpdateResult:
        release = release or latest_release(self.ZAPRET_REPO)
        if not release:
            raise RuntimeError("Не удалось получить информацию о последнем релизе Zapret")
        old = _read_local_zapret_version()
        if not is_newer(release.tag, old):
            return UpdateResult("zapret", old, release.tag, False, "Zapret уже актуален")
        asset = release.zip_asset("zapret-discord-youtube")
        if not asset:
            raise RuntimeError("В GitHub release не найден ZIP-архив Zapret")

        was_running = self.controller.zapret.is_running()

        updates = DATA_DIR / "updates"
        updates.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="zapretplus-update-", dir=updates) as td:
            td = Path(td)
            archive = td / asset.name
            download_asset(asset, archive, require_digest=True)
            extracted = td / "extract"
            safe_extract_zip(archive, extracted)
            package = _zapret_package_root(extracted)

            # Validate package before touching the live runtime.
            for required in (package / "bin" / "winws.exe", package / "bin" / "WinDivert.dll", package / "bin" / "WinDivert64.sys", package / "lists"):
                if not required.exists():
                    raise RuntimeError(f"Неполный релиз Zapret: отсутствует {required.name}")
            general_files = list(package.glob("general*.bat"))
            if not general_files:
                raise RuntimeError("Неполный релиз Zapret: отсутствуют general*.bat")

            # Only stop the live engine after download, hash check and package validation succeed.
            # Network or integrity failures therefore cannot interrupt working protection.
            if was_running:
                self.controller.zapret.stop()

            backup = _backup_dir("zapret", old)
            backup_ready = False
            try:
                shutil.copytree(ZAPRET_DIR, backup)
                backup_ready = True
                # Replace executable/data engine files.
                _copytree_replace(package / "bin", ZAPRET_DIR / "bin")

                strategies = ZAPRET_DIR / "strategies"
                if strategies.exists():
                    shutil.rmtree(strategies)
                strategies.mkdir(parents=True, exist_ok=True)
                for p in general_files:
                    shutil.copy2(p, strategies / p.name)

                # Replace upstream lists, but NEVER overwrite Zapret+ user lists.
                lists_dst = ZAPRET_DIR / "lists"
                lists_dst.mkdir(parents=True, exist_ok=True)
                user_names = {"list-general-user.txt", "list-exclude-user.txt", "ipset-exclude-user.txt"}
                for old_file in lists_dst.iterdir():
                    if old_file.is_file() and old_file.name not in user_names:
                        old_file.unlink()
                for p in (package / "lists").iterdir():
                    if p.is_file() and p.name not in user_names:
                        shutil.copy2(p, lists_dst / p.name)

                (ZAPRET_DIR / "version.txt").write_text(release.tag.lstrip("v"), encoding="utf-8")
                self.controller.zapret.sync_sites()
                pf = self.controller.zapret.preflight()
                if not pf.get("ok"):
                    raise RuntimeError("Новый Zapret не прошёл preflight: " + "; ".join(pf.get("errors", [])))
            except Exception:
                # Atomic-enough rollback: restore whole previous runtime when backup exists.
                if backup_ready:
                    if ZAPRET_DIR.exists():
                        shutil.rmtree(ZAPRET_DIR)
                    shutil.copytree(backup, ZAPRET_DIR)
                    self.controller.zapret.sync_sites()
                if was_running:
                    try:
                        self.controller.zapret.start()
                    except Exception as restart_exc:
                        log.exception("Rollback restored files but old Zapret did not restart: %s", restart_exc)
                raise
            finally:
                _prune_backups("zapret", 2)

        if was_running:
            self.controller.zapret.start()
        return UpdateResult("zapret", old, release.tag, True, f"Zapret обновлён {old} → {release.tag}")

    def stage_app_update(self, release: ReleaseInfo | None = None):
        if not APP_UPDATE_REPO:
            raise RuntimeError("Для этой сборки ещё не настроен официальный репозиторий обновлений Zapret+")
        release = release or latest_release(APP_UPDATE_REPO)
        if not release:
            raise RuntimeError("Не удалось получить информацию о релизе Zapret+")
        if not is_newer(release.tag, __version__):
            raise RuntimeError("Zapret+ уже актуален")
        asset = release.zip_asset("zapretplus") or release.zip_asset()
        if not asset:
            raise RuntimeError("В релизе Zapret+ нет ZIP-пакета обновления")

        updates = DATA_DIR / "updates"
        updates.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="zapretplus-app-download-", dir=updates) as td:
            td = Path(td)
            archive = td / asset.name
            download_asset(asset, archive, require_digest=True)
            extracted = td / "extract"
            safe_extract_zip(archive, extracted)
            from services.self_updater import find_package_root, prepare_plan
            package = find_package_root(extracted)
            return prepare_plan(__version__, release.tag, package)

    @staticmethod
    def launch_app_update(plan):
        from services.self_updater import launch
        launch(plan)

    def auto_update_supported_components(self) -> list[UpdateResult]:
        """Called at startup when auto-update is enabled.

        Only independently replaceable components are hot-updated. The embedded Telegram
        Python core is intentionally updated together with Zapret+ releases because its API
        is coupled to our integration layer.
        """
        results: list[UpdateResult] = []
        info = self.check()
        if info["zapret"]["available"]:
            results.append(self.install_zapret(info["zapret"]["release"]))
        return results
