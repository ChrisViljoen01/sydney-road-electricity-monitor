from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

from .models import MeterAccount


DEFAULT_BASE_URL = "https://umfakzn-gideon.pnpscada.com"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Sydney Road Electricity Monitor"
APP_PUBLISHER = "Connect Logistics"
APP_VERSION = "1.0.1"
MICROSOFT_TENANT_ID = os.getenv(
    "CONNECT_AI_TENANT_ID", "328838f1-3214-4e14-9263-47b9595e3f64"
).strip()
MICROSOFT_CLIENT_ID = os.getenv(
    "CONNECT_AI_CLIENT_ID", "207d9293-d311-4b5d-ba2b-5bbd3d3ade48"
).strip()
AUTONOMOUS_REFRESH_RECIPIENT = os.getenv(
    "CONNECT_REFRESH_AGENT_RECIPIENT",
    "operations@example.com",
).strip()
IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve()
PACKAGED_RESOURCES = RESOURCE_ROOT / "resources"


def _installed_data_root() -> Path:
    override = os.getenv("SYDNEY_ROAD_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if not IS_FROZEN:
        return PROJECT_ROOT
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / APP_PUBLISHER / APP_NAME


DATA_ROOT = _installed_data_root()
_accounts_override = os.getenv("SYDNEY_ROAD_ACCOUNTS_FILE", "").strip()
DEFAULT_ACCOUNTS_FILE = (
    Path(_accounts_override).expanduser().resolve()
    if _accounts_override
    else PACKAGED_RESOURCES / "accounts.json"
    if IS_FROZEN
    else PROJECT_ROOT / "accounts.json"
)
DEFAULT_EXPORT_DIR = DATA_ROOT / "exports"
DEFAULT_HISTORY_DB = DATA_ROOT / "electricity_history.db"
DEFAULT_TARIFF_CACHE = DATA_ROOT / "tariff_cache.json"
DEFAULT_OUTPUT_DIR = DATA_ROOT / "output"
DEFAULT_TEMP_DIR = DATA_ROOT / "tmp"
DEFAULT_LOG_DIR = DATA_ROOT / "logs"
DEFAULT_MICROSOFT_TOKEN_CACHE = DATA_ROOT / "microsoft_ai_session.bin"
DEFAULT_MAIL_AGENT_CONFIG = DATA_ROOT / "mail_agent_recipients.json"
DEFAULT_MAIL_AGENT_AUDIT = DATA_ROOT / "mail_agent_history.jsonl"
DEFAULT_BRAND_DIR = PACKAGED_RESOURCES / "branding"
if not DEFAULT_BRAND_DIR.exists():
    external_brand_dir = Path.home() / "Connect Logistics YMS App" / ".logo"
    DEFAULT_BRAND_DIR = external_brand_dir if external_brand_dir.exists() else PROJECT_ROOT / "assets" / "branding"
HISTORY_START_DATE = "2023-11-09"
# The first portal day containing all 48 half-hour readings for each meter.
# Earlier partial installation days are still stored and shown, but are not
# repeatedly treated as failed syncs.
ACCOUNT_COMPLETE_HISTORY_START_DATES = {
    "9987": "2024-03-19",
    "10005": "2023-11-10",
    "10006": "2023-11-10",
    "10004": "2023-11-13",
    "10105": "2023-11-10",
}


def bootstrap_user_data() -> None:
    """Create writable per-user storage and install the bundled history snapshot once."""
    for folder in (DATA_ROOT, DEFAULT_EXPORT_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_TEMP_DIR, DEFAULT_LOG_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    if not IS_FROZEN:
        return
    seed_database = PACKAGED_RESOURCES / "seed" / "electricity_history.db"
    if not DEFAULT_HISTORY_DB.exists() and seed_database.exists():
        shutil.copy2(seed_database, DEFAULT_HISTORY_DB)
    seed_tariff = PACKAGED_RESOURCES / "seed" / "tariff_cache.json"
    if not DEFAULT_TARIFF_CACHE.exists() and seed_tariff.exists():
        shutil.copy2(seed_tariff, DEFAULT_TARIFF_CACHE)


bootstrap_user_data()


def load_accounts(path: str | Path = DEFAULT_ACCOUNTS_FILE) -> list[MeterAccount]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        rows = json.load(handle)
    accounts = [
        MeterAccount(
            name=str(row["name"]),
            code=str(row["code"]),
            eid=str(row["eid"]),
            area=str(row.get("area") or row["name"]),
        )
        for row in rows
    ]
    if not accounts:
        raise ValueError(f"No meter accounts are configured in {source}")
    return accounts
