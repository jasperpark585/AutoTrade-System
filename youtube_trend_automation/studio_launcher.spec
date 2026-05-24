# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path.cwd()
datas = [
    (str(project_root / "app"), "app"),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "configs"), "configs"),
    (str(project_root / "data"), "data"),
    (str(project_root / "deploy"), "deploy"),
    (str(project_root / "docs"), "docs"),
    (str(project_root / "vendor"), "vendor"),
    (str(project_root / "main.py"), "."),
    (str(project_root / "studio_app.py"), "."),
    (str(project_root / ".env.example"), "."),
    (str(project_root / "README.md"), "."),
]
binaries = []
hiddenimports = []

for package_name in (
    "streamlit",
    "altair",
    "pydeck",
    "watchdog",
    "tornado",
    "googleapiclient",
    "google_auth_oauthlib",
    "google.auth",
    "google.oauth2",
    "edge_tts",
    "apscheduler",
    "openai",
):
    collected_datas, collected_binaries, collected_hiddenimports = collect_all(package_name)
    datas += collected_datas
    binaries += collected_binaries
    hiddenimports += collected_hiddenimports


a = Analysis(
    ["studio_launcher.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YouTubeAutomationStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="YouTubeAutomationStudio",
)
