# Zapret+ portable Windows build
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)

telegram_hidden = sorted(set(
    collect_submodules("telegram_proxy") + [
        "ctypes.util",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "certifi",
        "cryptography",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.primitives.ciphers",
    ]
))

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT), str(ROOT / "runtime")],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "runtime" / "zapret"), "runtime/zapret"),
        (str(ROOT / "runtime" / "telegram_proxy"), "runtime/telegram_proxy"),
        (str(ROOT / "third_party"), "third_party"),
    ],
    hiddenimports=telegram_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ZapretPlus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=True,
    icon=str(ROOT / "assets" / "icon.ico"),
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ZapretPlus",
)
