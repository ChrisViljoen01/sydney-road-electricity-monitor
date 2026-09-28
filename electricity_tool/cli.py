from __future__ import annotations

import argparse
from datetime import date
from getpass import getpass
import os
from pathlib import Path

from .config import DEFAULT_BASE_URL, DEFAULT_EXPORT_DIR
from .credentials import CredentialError, WindowsCredentialStore
from .service import run_extraction


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract PNPSCADA electricity readings to CSV")
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="Run an extraction")
    today = date.today()
    extract.add_argument("--start", type=_date, default=today.replace(month=1, day=1))
    extract.add_argument("--end", type=_date, default=today)
    extract.add_argument("--base-url", default=DEFAULT_BASE_URL)
    extract.add_argument("--output", type=Path, default=DEFAULT_EXPORT_DIR)
    extract.add_argument("--username", help="Username only; password is prompted or read securely")
    extract.add_argument("--save-credentials", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    username = args.username or os.environ.get("PNPSCADA_USERNAME")
    password = os.environ.get("PNPSCADA_PASSWORD")
    store = None
    try:
        store = WindowsCredentialStore()
        saved = store.read()
    except CredentialError:
        saved = None
    if saved:
        username = username or saved[0]
        password = password or saved[1]
    if not username:
        username = input("PNPSCADA username: ").strip()
    if not password:
        password = getpass("PNPSCADA password: ")

    result = run_extraction(
        args.base_url,
        username,
        password,
        args.start,
        args.end,
        args.output,
        progress=print,
        on_login_success=(
            (lambda: store.write(username, password))
            if args.save_credentials and store is not None
            else None
        ),
    )
    if args.save_credentials and store is not None:
        print("Login saved in Windows Credential Manager after authentication.")
    print(f"CSV export created at: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
