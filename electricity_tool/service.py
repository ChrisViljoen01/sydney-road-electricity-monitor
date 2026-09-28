from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from .config import load_accounts
from .export import export_diagnostic_run, export_run
from .models import Reading
from .parsing import parse_profile_graph_csv
from .portal import PortalClient, PortalError


class ExtractionError(RuntimeError):
    pass


def run_extraction(
    base_url: str,
    username: str,
    password: str,
    start_date: date,
    end_date: date,
    output_dir: str | Path,
    progress: Callable[[str], None] | None = None,
    on_login_success: Callable[[], None] | None = None,
) -> Path:
    notify = progress or (lambda _message: None)
    if end_date < start_date:
        raise ValueError("End date cannot be before start date")
    accounts = load_accounts()
    client = PortalClient(base_url, progress=notify)
    client.login(username, password)
    if on_login_success is not None:
        on_login_success()

    readings: list[Reading] = []
    raw_downloads: dict[str, str] = {}
    warnings: list[str] = []
    notify("Discovering the five meter accounts in Profile Graph...")
    try:
        graph_account_ids = client.discover_graph_account_ids(accounts)
    except PortalError as exc:
        client.capture_navigation_pages(accounts)
        diagnostic_dir = export_diagnostic_run(
            output_dir, client.diagnostics, start_date, end_date, [str(exc)]
        )
        raise ExtractionError(
            "Login succeeded, but the Profile Graph meter selector could not be read. "
            f"A sanitized diagnostic was saved to:\n{diagnostic_dir}"
        ) from exc

    for index, account in enumerate(accounts, start=1):
        notify(f"Reading account {index}/{len(accounts)}: {account.name}")
        try:
            csv_text, _endpoint = client.fetch_profile_graph_csv(
                account, graph_account_ids[account.eid], start_date, end_date
            )
            raw_downloads[account.eid] = csv_text
            account_readings = parse_profile_graph_csv(
                csv_text, account, start_date, end_date
            )
            readings.extend(account_readings)
            non_ok = sum(
                1 for row in account_readings if row.source_status.casefold() not in {"", "ok"}
            )
            suffix = f" ({non_ok:,} portal-calculated/flagged)" if non_ok else ""
            notify(f"  {len(account_readings):,} half-hour samples received{suffix}.")
        except (PortalError, ValueError) as exc:
            warning = f"{account.name}: {exc}"
            warnings.append(warning)
            notify(f"  Warning: {warning}")

    if not readings:
        notify("Capturing a sanitized website diagnostic...")
        client.capture_navigation_pages(accounts)
        diagnostic_dir = export_diagnostic_run(
            output_dir, client.diagnostics, start_date, end_date, warnings
        )
        raise ExtractionError(
            "Login succeeded, but the website did not return any Profile Graph CSV samples.\n\n"
            f"Diagnostic folder:\n{diagnostic_dir}"
        )

    notify("Creating interval, daily, weekly, spike, MTD, and YTD CSV files...")
    result = export_run(
        output_dir,
        readings,
        accounts,
        start_date,
        end_date,
        raw_downloads,
        None,
        [],
        warnings,
    )
    notify(f"Finished: {result}")
    return result
