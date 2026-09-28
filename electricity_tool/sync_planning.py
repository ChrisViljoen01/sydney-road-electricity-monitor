from __future__ import annotations

from datetime import date, timedelta


RECENT_REPAIR_DAYS = 3
MAXIMUM_SYNC_DAYS = 92


def _merge_date_ranges(
    ranges: list[tuple[date, date]],
) -> list[tuple[date, date]]:
    """Combine overlapping or adjacent inclusive date ranges."""
    valid = sorted((start, end) for start, end in ranges if start <= end)
    merged: list[tuple[date, date]] = []
    for range_start, range_end in valid:
        if merged and range_start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], range_end))
        else:
            merged.append((range_start, range_end))
    return merged


def _chunk_date_ranges(
    ranges: list[tuple[date, date]],
    maximum_days: int = MAXIMUM_SYNC_DAYS,
) -> list[tuple[date, date]]:
    """Split portal backfills into manageable inclusive date windows."""
    if maximum_days < 1:
        raise ValueError('maximum_days must be at least one')
    chunks: list[tuple[date, date]] = []
    for range_start, range_end in ranges:
        chunk_start = range_start
        while chunk_start <= range_end:
            chunk_end = min(
                range_end,
                chunk_start + timedelta(days=maximum_days - 1),
            )
            chunks.append((chunk_start, chunk_end))
            chunk_start = chunk_end + timedelta(days=1)
    return chunks


def plan_sync_ranges(
    history_start: date,
    today: date,
    missing_ranges: list[tuple[date, date]],
    *,
    recent_repair_days: int = RECENT_REPAIR_DAYS,
    maximum_days: int = MAXIMUM_SYNC_DAYS,
) -> list[tuple[date, date]]:
    """Plan refresh windows that also repair the prior partial portal day.

    PNPSCADA supplies placeholder ``Calc`` rows for future half-hours when the
    current day is requested. A later gap therefore starts on the following
    day even though the preceding day still needs replacing. Each missing
    range is expanded backwards by one day, and a short rolling window is
    always refreshed to pick up late portal corrections.
    """
    if today < history_start:
        return []
    if recent_repair_days < 1:
        raise ValueError('recent_repair_days must be at least one')

    candidates = [
        (max(history_start, range_start - timedelta(days=1)), range_end)
        for range_start, range_end in missing_ranges
    ]
    closed_day = today - timedelta(days=1)
    rolling_start = max(
        history_start,
        closed_day - timedelta(days=recent_repair_days - 1),
    )
    candidates.append((rolling_start, today))
    return _chunk_date_ranges(
        _merge_date_ranges(candidates),
        maximum_days=maximum_days,
    )
