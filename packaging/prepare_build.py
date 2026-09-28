from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3


ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "packaging" / "staging"
RESOURCES = STAGING / "resources"
SEED = RESOURCES / "seed"
BRANDING = RESOURCES / "branding"


def _reset_staging() -> None:
    resolved = STAGING.resolve()
    expected_parent = (ROOT / "packaging").resolve()
    if resolved.parent != expected_parent:
        raise RuntimeError(f"Refusing to reset unexpected staging path: {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)
    SEED.mkdir(parents=True, exist_ok=True)
    BRANDING.mkdir(parents=True, exist_ok=True)


def _copy_database_snapshot() -> None:
    source_path = ROOT / "electricity_history.db"
    destination_path = SEED / "electricity_history.db"
    if not source_path.exists():
        raise FileNotFoundError(f"Historical database not found: {source_path}")
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Historical database snapshot failed integrity check: {result}")
    finally:
        destination.close()
        source.close()


def _copy_branding() -> None:
    from PIL import Image

    configured = os.getenv("CONNECT_BRAND_DIR", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.home() / "OneDrive - Connect Logistics" / "Desktop" / "Logos",
        Path.home() / "Connect Logistics YMS App" / ".logo",
        ROOT / "assets" / "branding",
    ]
    source = next((path for path in candidates if path and path.exists()), None)
    if source is None:
        raise FileNotFoundError("Connect Logistics branding folder could not be found.")
    icon_source = source / "Connect-Logistics-Icon.png"
    logo_source = source / "Connect-Logistics-Logo.png"
    if not logo_source.exists():
        logo_source = source / "Connect-Logistics-Main Logo.png"
    for source_file in (icon_source, logo_source):
        if not source_file.exists():
            raise FileNotFoundError(f"Required branding file not found: {source_file}")
    shutil.copy2(icon_source, BRANDING / "Connect-Logistics-Icon.png")
    shutil.copy2(logo_source, BRANDING / "Connect-Logistics-Main Logo.png")
    # Normalize the Windows icon so both PyInstaller and Inno Setup receive a
    # multi-resolution ICO, even when the source application's ICO is minimal.
    with Image.open(BRANDING / "Connect-Logistics-Icon.png") as image:
        image.convert("RGBA").save(
            BRANDING / "Connect-Logistics-Icon.ico",
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )


def main() -> None:
    _reset_staging()
    shutil.copy2(ROOT / "accounts.json", RESOURCES / "accounts.json")
    tariff_cache = ROOT / "tariff_cache.json"
    if tariff_cache.exists():
        shutil.copy2(tariff_cache, SEED / "tariff_cache.json")
    _copy_branding()
    _copy_database_snapshot()
    size_mb = (SEED / "electricity_history.db").stat().st_size / (1024 * 1024)
    print(f"Prepared installer resources with a {size_mb:.1f} MB verified database snapshot.")


if __name__ == "__main__":
    main()
