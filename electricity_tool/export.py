from __future__ import annotations

import csv
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Iterable

from .aggregate import (
    daily_summaries,
    month_to_date_summaries,
    monthly_summaries,
    weekly_summaries,
    year_to_date_summaries,
)
from .models import MeterAccount, Reading
from .monitoring import (
    BASELINE_WEEKS,
    CONSUMPTION_MIN_INCREASE_KWH,
    CONSUMPTION_SPIKE_RATIO,
    DEMAND_MIN_INCREASE_KVA,
    DEMAND_SPIKE_RATIO,
    MIN_BASELINE_DAYS,
    daily_meter_records,
    solar_positive_register,
    spike_register,
    weekly_meter_records,
)


INTERVAL_FIELDS = [
    "date",
    "timestamp",
    "account_name",
    "account_code",
    "account_eid",
    "area",
    "period_seconds",
    "kw_import",
    "kw_export",
    "kw_net",
    "kvar_net",
    "kva",
    "import_kwh",
    "export_kwh",
    "net_kwh",
    "power_factor",
    "kva_method",
    "raw_p1",
    "raw_p2",
    "raw_q1",
    "raw_q2",
    "raw_q3",
    "raw_q4",
    "raw_s",
    "raw_scalar_s",
    "source_status",
    "source",
]

SUMMARY_FIELDS = [
    "period_type",
    "period_start",
    "period_end",
    "data_start",
    "data_end",
    "account_name",
    "account_code",
    "account_eid",
    "area",
    "samples",
    "observed_hours",
    "ok_samples",
    "calc_samples",
    "other_status_samples",
    "import_kwh",
    "export_kwh",
    "net_kwh",
    "average_kw",
    "minimum_kw",
    "peak_kw",
    "peak_kw_time",
    "kw_range",
    "average_kva",
    "minimum_kva",
    "peak_kva",
    "peak_kva_time",
    "kva_range",
    "power_factor_at_peak_kva",
]

DAILY_METER_FIELDS = [
    "date",
    "iso_week",
    "area",
    "account_name",
    "account_code",
    "account_eid",
    "data_quality_status",
    "samples",
    "observed_hours",
    "ok_samples",
    "calc_samples",
    "other_status_samples",
    "import_kwh",
    "export_kwh",
    "net_kwh",
    "previous_day_date",
    "previous_day_import_kwh",
    "day_on_day_change_kwh",
    "day_on_day_change_percent",
    "baseline_method",
    "baseline_comparable_days",
    "baseline_import_kwh",
    "consumption_variance_kwh",
    "consumption_variance_percent",
    "average_kw",
    "minimum_kw",
    "peak_kw",
    "peak_kw_time",
    "average_kva",
    "minimum_kva",
    "peak_kva",
    "peak_kva_time",
    "baseline_peak_kva",
    "demand_variance_kva",
    "demand_variance_percent",
    "power_factor_at_peak_kva",
    "consumption_spike",
    "demand_spike",
    "solar_signal",
    "alert_level",
    "alert_reason",
    "investigation_status",
    "cause_category",
    "confirmed_cause",
    "corrective_action",
    "responsible_person",
    "target_close_date",
    "investigation_notes",
]

WEEKLY_METER_FIELDS = [
    "iso_week",
    "week_start",
    "week_end",
    "data_start",
    "data_end",
    "week_status",
    "days_recorded",
    "area",
    "account_name",
    "account_code",
    "account_eid",
    "samples",
    "observed_hours",
    "ok_samples",
    "calc_samples",
    "other_status_samples",
    "import_kwh",
    "export_kwh",
    "net_kwh",
    "average_kw",
    "peak_kw",
    "peak_kw_time",
    "average_kva",
    "peak_kva",
    "peak_kva_time",
    "comparison_basis",
    "previous_week_comparable_import_kwh",
    "week_on_week_change_kwh",
    "week_on_week_change_percent",
    "watch_days",
    "high_alert_days",
    "critical_alert_days",
    "total_alert_days",
    "review_status",
    "weekly_comment",
    "reviewed_by",
    "reviewed_date",
    "action_required",
]

SPIKE_FIELDS = [
    "spike_id",
    "date",
    "iso_week",
    "area",
    "account_name",
    "account_code",
    "account_eid",
    "alert_level",
    "alert_type",
    "solar_signal",
    "alert_reason",
    "import_kwh",
    "baseline_import_kwh",
    "consumption_variance_kwh",
    "consumption_variance_percent",
    "peak_kw",
    "peak_kw_time",
    "peak_kva",
    "peak_kva_time",
    "baseline_peak_kva",
    "demand_variance_kva",
    "demand_variance_percent",
    "data_quality_status",
    "investigation_status",
    "cause_category",
    "confirmed_cause",
    "corrective_action",
    "responsible_person",
    "target_close_date",
    "closed_date",
    "investigation_notes",
]

SOLAR_POSITIVE_FIELDS = [
    "event_id",
    "date",
    "iso_week",
    "area",
    "account_name",
    "account_code",
    "account_eid",
    "signal_type",
    "alert_reason",
    "import_kwh",
    "baseline_import_kwh",
    "consumption_variance_kwh",
    "consumption_variance_percent",
    "peak_kw",
    "peak_kw_time",
    "data_quality_status",
    "verification_status",
]


def _write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> int:
    materialized = list(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)
    return len(materialized)


def _write_report_tables(path: Path, tables: list[list[list[str]]]) -> int:
    max_columns = max((len(row) for table in tables for row in table), default=0)
    fields = ["table_index", "row_index"] + [f"column_{index}" for index in range(1, max_columns + 1)]
    rows: list[dict] = []
    for table_index, table in enumerate(tables, start=1):
        for row_index, cells in enumerate(table, start=1):
            row = {"table_index": table_index, "row_index": row_index}
            row.update({f"column_{index}": value for index, value in enumerate(cells, start=1)})
            rows.append(row)
    return _write_csv(path, rows, fields)


def _sanitize_portal_text(text: str) -> str:
    sanitized = re.sub(
        r"([?&]memh=)[^&'\"<>\s]+", r"\1[REDACTED]", text, flags=re.IGNORECASE
    )
    sanitized = re.sub(
        r"(name\s*=\s*['\"]memh['\"][^>]*value\s*=\s*['\"])[^'\"]+",
        r"\1[REDACTED]",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


def export_diagnostic_run(
    output_root: str | Path,
    diagnostics: dict[str, tuple[str, str]],
    start_date: date,
    end_date: date,
    warnings: list[str],
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = Path(output_root).expanduser().resolve() / "diagnostics" / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    index: list[dict[str, str]] = []
    for name, (url, text) in diagnostics.items():
        path = run_dir / name
        path.write_text(_sanitize_portal_text(text), encoding="utf-8")
        index.append(
            {
                "file": name,
                "url": _sanitize_portal_text(url),
            }
        )
    (run_dir / "diagnostic_index.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "start_date": start_date.isoformat(),
                "end_date_inclusive": end_date.isoformat(),
                "pages": index,
                "warnings": warnings,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return run_dir


def export_run(
    output_root: str | Path,
    readings: list[Reading],
    accounts: list[MeterAccount],
    start_date: date,
    end_date: date,
    raw_downloads: dict[str, str],
    raw_report_html: str | None,
    report_tables: list[list[list[str]]],
    warnings: list[str],
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = Path(output_root).expanduser().resolve() / stamp
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)

    sorted_readings = sorted(readings, key=lambda row: (row.account_eid, row.timestamp))
    daily_monitor = daily_meter_records(sorted_readings)
    weekly_monitor = weekly_meter_records(sorted_readings, daily_monitor)
    spikes = spike_register(daily_monitor)
    solar_positive_events = solar_positive_register(daily_monitor)
    counts = {
        "interval_readings.csv": _write_csv(
            run_dir / "interval_readings.csv",
            (reading.to_csv_row() for reading in sorted_readings),
            INTERVAL_FIELDS,
        ),
        "daily_summary.csv": _write_csv(
            run_dir / "daily_summary.csv", daily_summaries(sorted_readings), SUMMARY_FIELDS
        ),
        "monthly_summary.csv": _write_csv(
            run_dir / "monthly_summary.csv", monthly_summaries(sorted_readings), SUMMARY_FIELDS
        ),
        "weekly_summary.csv": _write_csv(
            run_dir / "weekly_summary.csv", weekly_summaries(sorted_readings), SUMMARY_FIELDS
        ),
        "daily_meter_record.csv": _write_csv(
            run_dir / "daily_meter_record.csv", daily_monitor, DAILY_METER_FIELDS
        ),
        "weekly_meter_record.csv": _write_csv(
            run_dir / "weekly_meter_record.csv", weekly_monitor, WEEKLY_METER_FIELDS
        ),
        "spike_register.csv": _write_csv(
            run_dir / "spike_register.csv", spikes, SPIKE_FIELDS
        ),
        "solar_positive_register.csv": _write_csv(
            run_dir / "solar_positive_register.csv",
            solar_positive_events,
            SOLAR_POSITIVE_FIELDS,
        ),
        "month_to_date_summary.csv": _write_csv(
            run_dir / "month_to_date_summary.csv",
            month_to_date_summaries(sorted_readings, end_date),
            SUMMARY_FIELDS,
        ),
        "year_to_date_summary.csv": _write_csv(
            run_dir / "year_to_date_summary.csv",
            year_to_date_summaries(sorted_readings, end_date),
            SUMMARY_FIELDS,
        ),
    }
    if report_tables:
        counts["portal_daily_report.csv"] = _write_report_tables(
            run_dir / "portal_daily_report.csv", report_tables
        )

    for eid, csv_text in raw_downloads.items():
        (raw_dir / f"account_{eid}_profile_graph.csv").write_text(
            csv_text, encoding="utf-8-sig"
        )
    if raw_report_html:
        # PNPSCADA report links can contain a short-lived memh session value.
        # Preserve the report for audit while removing that credential-like token.
        sanitized_html = _sanitize_portal_text(raw_report_html)
        (raw_dir / "per_day_report.html").write_text(sanitized_html, encoding="utf-8")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": start_date.isoformat(),
        "end_date_inclusive": end_date.isoformat(),
        "accounts": [
            {
                "name": account.name,
                "area": account.area or account.name,
                "code": account.code,
                "eid": account.eid,
            }
            for account in accounts
        ],
        "row_counts": counts,
        "warnings": warnings,
        "calculation_notes": {
            "kw_net": "kw_import - kw_export",
            "kvar_net": "Q1 + Q2 - Q3 - Q4",
            "calculated_kva": "sqrt(kw_net^2 + kvar_net^2)",
            "reported_kva": "PNPSCADA vector S (per kVA); scalar S retained as a raw audit column",
            "kwh": "kW multiplied by the PNPSCADA profile interval in hours",
            "weekly_period": "ISO Monday through Sunday; partial weeks compare the same elapsed days with the prior week",
            "spike_baseline": (
                f"Average of the previous {MIN_BASELINE_DAYS}-{BASELINE_WEEKS} matching weekdays"
            ),
            "consumption_spike": (
                f"At least {(CONSUMPTION_SPIKE_RATIO - 1) * 100:.0f}% and "
                f"{CONSUMPTION_MIN_INCREASE_KWH:.0f} kWh above baseline"
            ),
            "demand_spike": (
                f"At least {(DEMAND_SPIKE_RATIO - 1) * 100:.0f}% and "
                f"{DEMAND_MIN_INCREASE_KVA:.0f} kVA above baseline"
            ),
        },
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return run_dir
