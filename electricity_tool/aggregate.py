from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Callable, Hashable, Iterable

from .models import Reading


def _weighted_average(rows: list[Reading], field: str) -> float:
    total_seconds = sum(row.period_seconds for row in rows)
    if total_seconds <= 0:
        return 0.0
    return sum(getattr(row, field) * row.period_seconds for row in rows) / total_seconds


def _summary(rows: list[Reading], period_type: str, period_start: date, period_end: date) -> dict:
    ordered = sorted(rows, key=lambda row: row.timestamp)
    statuses = Counter((row.source_status or 'Ok').strip().casefold() for row in ordered)
    ok_samples = statuses['ok']
    calc_samples = statuses['calc']
    other_status_samples = len(ordered) - ok_samples - calc_samples
    peak_kw = max(ordered, key=lambda row: row.kw_net)
    peak_kva = max(ordered, key=lambda row: row.kva)
    min_kw = min(row.kw_net for row in ordered)
    max_kw = peak_kw.kw_net
    min_kva = min(row.kva for row in ordered)
    max_kva = peak_kva.kva
    return {
        "period_type": period_type,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "data_start": ordered[0].timestamp.date().isoformat(),
        "data_end": ordered[-1].timestamp.date().isoformat(),
        "account_name": ordered[0].account_name,
        "account_code": ordered[0].account_code,
        "account_eid": ordered[0].account_eid,
        "area": ordered[0].area or ordered[0].account_name,
        "samples": len(ordered),
        "observed_hours": sum(row.period_seconds for row in ordered) / 3600.0,
        "ok_samples": ok_samples,
        "calc_samples": calc_samples,
        "other_status_samples": other_status_samples,
        "import_kwh": sum(row.import_kwh for row in ordered),
        "export_kwh": sum(row.export_kwh for row in ordered),
        "net_kwh": sum(row.net_kwh for row in ordered),
        "average_kw": _weighted_average(ordered, "kw_net"),
        "minimum_kw": min_kw,
        "peak_kw": max_kw,
        "peak_kw_time": peak_kw.timestamp.isoformat(sep=" "),
        "kw_range": max_kw - min_kw,
        "average_kva": _weighted_average(ordered, "kva"),
        "minimum_kva": min_kva,
        "peak_kva": max_kva,
        "peak_kva_time": peak_kva.timestamp.isoformat(sep=" "),
        "kva_range": max_kva - min_kva,
        "power_factor_at_peak_kva": peak_kva.power_factor,
    }


def _group(
    readings: Iterable[Reading], key: Callable[[Reading], Hashable]
) -> dict[Hashable, list[Reading]]:
    groups: dict[Hashable, list[Reading]] = defaultdict(list)
    for reading in readings:
        groups[key(reading)].append(reading)
    return groups


def daily_summaries(readings: Iterable[Reading]) -> list[dict]:
    groups = _group(readings, lambda row: (row.account_eid, row.timestamp.date()))
    output = [
        _summary(rows, "day", day, day)
        for (_eid, day), rows in sorted(groups.items(), key=lambda item: item[0])
    ]
    return output


def monthly_summaries(readings: Iterable[Reading]) -> list[dict]:
    groups = _group(readings, lambda row: (row.account_eid, row.timestamp.year, row.timestamp.month))
    output: list[dict] = []
    for (_eid, year, month), rows in sorted(groups.items(), key=lambda item: item[0]):
        output.append(
            _summary(
                rows,
                "month",
                min(row.timestamp.date() for row in rows),
                max(row.timestamp.date() for row in rows),
            )
        )
    return output


def weekly_summaries(readings: Iterable[Reading]) -> list[dict]:
    groups = _group(
        readings,
        lambda row: (
            row.account_eid,
            row.timestamp.date() - timedelta(days=row.timestamp.weekday()),
        ),
    )
    return [
        _summary(rows, "week", week_start, week_start + timedelta(days=6))
        for (_eid, week_start), rows in sorted(groups.items(), key=lambda item: item[0])
    ]


def month_to_date_summaries(readings: Iterable[Reading], end_date: date) -> list[dict]:
    selected = [
        row
        for row in readings
        if row.timestamp.year == end_date.year
        and row.timestamp.month == end_date.month
        and row.timestamp.date() <= end_date
    ]
    groups = _group(selected, lambda row: row.account_eid)
    start = end_date.replace(day=1)
    return [_summary(rows, "month_to_date", start, end_date) for _eid, rows in sorted(groups.items())]


def year_to_date_summaries(readings: Iterable[Reading], end_date: date) -> list[dict]:
    selected = [
        row
        for row in readings
        if row.timestamp.year == end_date.year and row.timestamp.date() <= end_date
    ]
    groups = _group(selected, lambda row: row.account_eid)
    start = end_date.replace(month=1, day=1)
    return [_summary(rows, "year_to_date", start, end_date) for _eid, rows in sorted(groups.items())]
