from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import socket
import sys
import threading
import webbrowser

from .config import APP_NAME, DATA_ROOT, DEFAULT_BRAND_DIR, DEFAULT_LOG_DIR, IS_FROZEN


MUTEX_NAME = r"Local\ConnectLogistics.SydneyRoadElectricityMonitor"
PORT_FILE = DATA_ROOT / "desktop_port.txt"
ERROR_ALREADY_EXISTS = 183


def _message(text: str, title: str = APP_NAME) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)


def _open_data_folder() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    os.startfile(DATA_ROOT)  # type: ignore[attr-defined]


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_existing_url() -> str | None:
    try:
        port = int(PORT_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return f"http://127.0.0.1:{port}/"


def _configure_frozen_log() -> None:
    if not IS_FROZEN:
        return
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stream = (DEFAULT_LOG_DIR / "desktop.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def _acquire_mutex() -> tuple[int, bool]:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    return int(handle), kernel32.GetLastError() == ERROR_ALREADY_EXISTS


def _release_mutex(handle: int) -> None:
    ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))


def _start_tray(url: str):
    from PIL import Image
    import pystray
    from nicegui import app

    icon_path = DEFAULT_BRAND_DIR / "Connect-Logistics-Icon.png"
    image = Image.open(icon_path).convert("RGBA")
    tray: pystray.Icon

    def open_dashboard(_icon=None, _item=None) -> None:
        webbrowser.open(url, new=2)

    def open_data(_icon=None, _item=None) -> None:
        _open_data_folder()

    def exit_application(_icon=None, _item=None) -> None:
        tray.stop()
        app.shutdown()

    tray = pystray.Icon(
        "SydneyRoadElectricityMonitor",
        image,
        APP_NAME,
        pystray.Menu(
            pystray.MenuItem("Open dashboard", open_dashboard, default=True),
            pystray.MenuItem("Open data folder", open_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", exit_application),
        ),
    )
    threading.Thread(target=tray.run, name="electricity-monitor-tray", daemon=True).start()
    return tray


def run_desktop() -> None:
    if sys.platform != "win32":
        raise RuntimeError("The packaged desktop application supports Windows only.")
    _configure_frozen_log()
    mutex_handle, already_running = _acquire_mutex()
    if already_running:
        existing_url = _read_existing_url()
        if existing_url:
            webbrowser.open(existing_url, new=2)
        else:
            _message("The application is already running. Use its system-tray icon to open it.")
        _release_mutex(mutex_handle)
        return

    tray = None
    try:
        port = _reserve_port()
        PORT_FILE.write_text(str(port), encoding="utf-8")
        url = f"http://127.0.0.1:{port}/"
        tray = _start_tray(url)
        from .dashboard_app import main

        main(show=True, port=port)
    except Exception as exc:
        _message(f"The application could not start.\n\n{exc}\n\nLog folder:\n{DEFAULT_LOG_DIR}")
        raise
    finally:
        if tray is not None:
            tray.stop()
        PORT_FILE.unlink(missing_ok=True)
        _release_mutex(mutex_handle)
