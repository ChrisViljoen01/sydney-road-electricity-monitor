from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPEC).resolve().parent.parent
STAGING = ROOT / "packaging" / "staging"

nicegui_datas, nicegui_binaries, nicegui_hiddenimports = collect_all("nicegui")
msal_datas, msal_binaries, msal_hiddenimports = collect_all("msal")
msal_ext_datas, msal_ext_binaries, msal_ext_hiddenimports = collect_all("msal_extensions")

a = Analysis(
    [str(ROOT / "desktop_main.py")],
    pathex=[str(ROOT)],
    binaries=nicegui_binaries + msal_binaries + msal_ext_binaries,
    datas=nicegui_datas + msal_datas + msal_ext_datas + [(str(STAGING / "resources"), "resources")],
    hiddenimports=nicegui_hiddenimports + msal_hiddenimports + msal_ext_hiddenimports + [
        "pystray._win32",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sydney Road Electricity Monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=str(STAGING / "resources" / "branding" / "Connect-Logistics-Icon.ico"),
    version=str(ROOT / "packaging" / "windows_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Sydney Road Electricity Monitor",
)
