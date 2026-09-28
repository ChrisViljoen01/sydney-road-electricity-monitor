from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import fmean
from threading import RLock
from typing import Iterable

from .config import DEFAULT_EXPORT_DIR, DEFAULT_HISTORY_DB, HISTORY_START_DATE
from .history_store import HistoryStore, HistoryWriteResult
from .models import Reading
from .monitoring import (
    daily_meter_records,
    solar_positive_register,
    spike_register,
    weekly_meter_records,
)


ALL_AREAS = 'All areas'
SOLAR_AREA = 'Connect Logistics Solar'
SEVERITY_ORDER = {'Critical': 0, 'High': 1, 'Watch': 2, 'None': 3, 'Not assessed': 4}


@dataclass(frozen=True)
class DashboardDataset:
    run_dir: Path
    manifest: dict
    daily: list[dict[str, str]]
    weekly: list[dict[str, str]]
    spikes: list[dict[str, str]]
    intervals: list[dict[str, str]]
    areas: tuple[str, ...]
    first_date: date
    last_date: date
    solar_positive_events: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardView:
    start_date: date
    end_date: date
    area: str
    daily: list[dict[str, str]]
    previous_daily: list[dict[str, str]]
    spikes: list[dict[str, str]]
    metrics: dict[str, object]
    area_summary: list[dict[str, object]]
    insights: list[dict[str, str]]
    solar_positive_events: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SupplyDemandBalance:
    """Site-level comparison of warehouse demand and solar generation."""

    start_date: date
    end_date: date
    intervals: list[dict[str, object]]
    daily: list[dict[str, object]]
    profile: list[dict[str, object]]
    metrics: dict[str, object]


@dataclass(frozen=True)
class PeriodComparison:
    comparison_type: str
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date
    current_view: DashboardView
    previous_view: DashboardView
    current_solar_view: DashboardView | None
    previous_solar_view: DashboardView | None
    daily: list[dict[str, object]]
    solar_daily: list[dict[str, object]]
    area_comparison: list[dict[str, object]]
    metrics: dict[str, object]
    note: str
    area: str = ALL_AREAS


@dataclass(frozen=True)
class SolarPerformance:
    """Recorded solar-meter output for an operational performance view."""

    start_date: date
    end_date: date
    view: DashboardView
    comparison: PeriodComparison
    daily: list[dict[str, object]]
    profile: list[dict[str, object]]
    metrics: dict[str, object]


def without_area(dataset: DashboardDataset, excluded_area: str) -> DashboardDataset:
    """Return an analysis view with one meter area removed, preserving source history."""
    keep = lambda row: row.get('area') != excluded_area
    return DashboardDataset(
        run_dir=dataset.run_dir,
        manifest=dataset.manifest,
        daily=[row for row in dataset.daily if keep(row)],
        weekly=[row for row in dataset.weekly if keep(row)],
        spikes=[row for row in dataset.spikes if keep(row)],
        intervals=[row for row in dataset.intervals if keep(row)],
        areas=tuple(area for area in dataset.areas if area != excluded_area),
        first_date=dataset.first_date,
        last_date=dataset.last_date,
        solar_positive_events=[
            row for row in dataset.solar_positive_events if keep(row)
        ],
    )


def only_area(dataset: DashboardDataset, selected_area: str) -> DashboardDataset:
    """Return an analysis view containing only the selected meter area."""
    keep = lambda row: row.get('area') == selected_area
    return DashboardDataset(
        run_dir=dataset.run_dir,
        manifest=dataset.manifest,
        daily=[row for row in dataset.daily if keep(row)],
        weekly=[row for row in dataset.weekly if keep(row)],
        spikes=[row for row in dataset.spikes if keep(row)],
        intervals=[row for row in dataset.intervals if keep(row)],
        areas=(selected_area,) if selected_area in dataset.areas else (),
        first_date=dataset.first_date,
        last_date=dataset.last_date,
        solar_positive_events=[
            row for row in dataset.solar_positive_events if keep(row)
        ],
    )


def first_valid_area_date(dataset: DashboardDataset, area: str) -> date:
    """Return the first portal-confirmed interval date for a meter area."""

    valid_dates = [
        _row_date(row)
        for row in dataset.intervals
        if row.get('area') == area
        and str(row.get('source_status', '')).casefold() in {'', 'ok'}
    ]
    if valid_dates:
        return min(valid_dates)
    daily_dates = [
        _row_date(row) for row in dataset.daily if row.get('area') == area
    ]
    return min(daily_dates) if daily_dates else dataset.first_date


class DashboardRepository:
    def __init__(
        self,
        export_root: str | Path = DEFAULT_EXPORT_DIR,
        history_db: str | Path = DEFAULT_HISTORY_DB,
    ) -> None:
        self.export_root = Path(export_root).expanduser().resolve()
        self.history_store = HistoryStore(history_db)
        self._lock = RLock()
        self._dataset: DashboardDataset | None = None

    def refresh(self) -> DashboardDataset:
        dataset = load_latest_dataset(self.export_root, self.history_store)
        with self._lock:
            self._dataset = dataset
        return dataset

    def get(self) -> DashboardDataset:
        with self._lock:
            if self._dataset is not None:
                return self._dataset
        return self.refresh()

    def latest_timestamp(self) -> datetime | None:
        self.history_store.bootstrap_from_exports(self.export_root)
        return self.history_store.latest_timestamp()

    def missing_date_ranges(
        self,
        start_date: date,
        end_date: date,
        expected_account_eids: Iterable[str],
    ) -> list[tuple[date, date]]:
        self.history_store.bootstrap_from_exports(self.export_root)
        return self.history_store.missing_date_ranges(
            start_date, end_date, expected_account_eids
        )

    def ingest_run(
        self,
        run_dir: str | Path,
        requested_start: date,
        requested_end: date,
    ) -> tuple[DashboardDataset, HistoryWriteResult]:
        folder = Path(run_dir).expanduser().resolve()
        incoming_rows = _read_csv(folder / 'interval_readings.csv')
        result = self.history_store.upsert_rows(incoming_rows)
        self.history_store.record_sync(
            requested_start, requested_end, Path(run_dir).name, result
        )
        with self._lock:
            cached = self._dataset
        if cached is None or not incoming_rows:
            return self.refresh(), result
        try:
            dataset = _merge_incremental_dataset(
                cached,
                incoming_rows,
                self.history_store.stats(),
            )
        except (KeyError, TypeError, ValueError):
            # A full database reload is the correctness fallback for an
            # unexpected or older export schema.
            return self.refresh(), result
        with self._lock:
            self._dataset = dataset
        _write_dataset_snapshot(dataset)
        return dataset, result


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write the exact merged dashboard rows used by the on-screen view."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8-sig')
        return
    fieldnames = list(rows[0])
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def _dataset_manifest(
    intervals: list[dict[str, str]],
    daily: list[dict[str, str]],
    weekly: list[dict[str, str]],
    spikes: list[dict[str, str]],
    solar_positive_events: list[dict[str, str]],
    database_stats: dict[str, object],
) -> dict[str, object]:
    dates = [_row_date(row) for row in daily]
    return {
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'start_date': min(dates).isoformat(),
        'end_date_inclusive': max(dates).isoformat(),
        'row_counts': {
            'interval_readings.csv': len(intervals),
            'daily_meter_record.csv': len(daily),
            'weekly_meter_record.csv': len(weekly),
            'spike_register.csv': len(spikes),
            'solar_positive_register.csv': len(solar_positive_events),
        },
        'database': database_stats,
    }


def _write_dataset_snapshot(dataset: DashboardDataset) -> None:
    """Keep management downloads aligned with the in-memory dashboard dataset."""
    _write_csv(dataset.run_dir / 'interval_readings.csv', dataset.intervals)
    _write_csv(dataset.run_dir / 'daily_meter_record.csv', dataset.daily)
    _write_csv(dataset.run_dir / 'weekly_meter_record.csv', dataset.weekly)
    _write_csv(dataset.run_dir / 'spike_register.csv', dataset.spikes)
    _write_csv(
        dataset.run_dir / 'solar_positive_register.csv',
        dataset.solar_positive_events,
    )


def _merge_incremental_dataset(
    cached: DashboardDataset,
    incoming_rows: list[dict[str, str]],
    database_stats: dict[str, object],
) -> DashboardDataset:
    """Merge a small portal refresh and recalculate only analytics it can affect.

    Daily alerts use up to five weeks of matching-weekday history. A changed day
    can therefore alter itself and the following 35 days; six weeks of lookback
    gives the recalculation enough context without reloading the complete DB.
    """
    valid_incoming = [
        row for row in incoming_rows
        if str(row.get('date', '')) >= HISTORY_START_DATE
    ]
    if not valid_incoming:
        return cached
    changed_dates = [date.fromisoformat(str(row['date'])[:10]) for row in valid_incoming]
    changed_start = min(changed_dates)
    changed_end = max(changed_dates)
    if len(valid_incoming) > 50_000 or (changed_end - changed_start).days > 184:
        raise ValueError('Incremental refresh is too large for the fast merge path.')

    interval_index = {
        (str(row['account_eid']), str(row['timestamp'])): row
        for row in cached.intervals
    }
    for row in valid_incoming:
        interval_index[(str(row['account_eid']), str(row['timestamp']))] = row
    intervals = sorted(
        interval_index.values(),
        key=lambda row: (str(row['account_eid']), str(row['timestamp'])),
    )
    first_interval_date = min(cached.first_date, changed_start)
    last_interval_date = max(
        date.fromisoformat(str(row['date'])[:10]) for row in valid_incoming
    )
    last_interval_date = max(last_interval_date, cached.last_date)

    impact_end = min(last_interval_date, changed_end + timedelta(days=35))
    analysis_start = max(first_interval_date, changed_start - timedelta(days=42))
    analysis_end = min(
        last_interval_date,
        impact_end + timedelta(days=6 - impact_end.weekday()),
    )
    analysis_start_text = analysis_start.isoformat()
    analysis_end_text = analysis_end.isoformat()
    affected_readings = [
        _reading(row)
        for row in intervals
        if analysis_start_text <= str(row['date'])[:10] <= analysis_end_text
    ]
    recalculated_daily = daily_meter_records(affected_readings)
    changed_start_text = changed_start.isoformat()
    impact_end_text = impact_end.isoformat()
    daily_index = {
        (str(row['account_eid']), str(row['date'])): row for row in cached.daily
    }
    for row in recalculated_daily:
        if changed_start_text <= str(row['date']) <= impact_end_text:
            daily_index[(str(row['account_eid']), str(row['date']))] = row
    daily = sorted(daily_index.values(), key=lambda row: (row['date'], row['area']))

    recalculated_weekly = weekly_meter_records(affected_readings, recalculated_daily)
    affected_week_start = changed_start - timedelta(days=changed_start.weekday())
    affected_week_end = impact_end - timedelta(days=impact_end.weekday())
    weekly_index = {
        (str(row['account_eid']), str(row['week_start'])): row for row in cached.weekly
    }
    for row in recalculated_weekly:
        week_start = date.fromisoformat(str(row['week_start'])[:10])
        if affected_week_start <= week_start <= affected_week_end:
            weekly_index[(str(row['account_eid']), str(row['week_start']))] = row
    weekly = sorted(weekly_index.values(), key=lambda row: (row['week_start'], row['area']))

    spikes = spike_register(daily)
    solar_positive_events = solar_positive_register(daily)
    areas = tuple(sorted({str(row['area']) for row in daily}))
    dates = [_row_date(row) for row in daily]
    manifest = _dataset_manifest(
        intervals,
        daily,
        weekly,
        spikes,
        solar_positive_events,
        database_stats,
    )
    return DashboardDataset(
        run_dir=cached.run_dir,
        manifest=manifest,
        daily=daily,
        weekly=weekly,
        spikes=spikes,
        intervals=intervals,
        areas=areas,
        first_date=min(dates),
        last_date=max(dates),
        solar_positive_events=solar_positive_events,
    )


def _number(row: dict[str, str], field: str) -> float:
    try:
        return float(row.get(field, '') or 0)
    except ValueError:
        return 0.0


def _row_date(row: dict[str, str], field: str = 'date') -> date:
    return date.fromisoformat(row[field][:10])


def _optional_number(row: dict[str, str], field: str) -> float | None:
    value = row.get(field, '')
    return None if value in {'', None} else float(value)


def _reading(row: dict[str, str]) -> Reading:
    return Reading(
        timestamp=datetime.fromisoformat(row['timestamp']),
        account_name=row['account_name'],
        account_code=row['account_code'],
        account_eid=row['account_eid'],
        period_seconds=int(float(row['period_seconds'])),
        kw_import=_number(row, 'kw_import'),
        kw_export=_number(row, 'kw_export'),
        kw_net=_number(row, 'kw_net'),
        kvar_net=_number(row, 'kvar_net'),
        kva=_number(row, 'kva'),
        import_kwh=_number(row, 'import_kwh'),
        export_kwh=_number(row, 'export_kwh'),
        net_kwh=_number(row, 'net_kwh'),
        power_factor=_optional_number(row, 'power_factor'),
        kva_method=row.get('kva_method', ''),
        raw_p1=_optional_number(row, 'raw_p1'),
        raw_p2=_optional_number(row, 'raw_p2'),
        raw_q1=_optional_number(row, 'raw_q1'),
        raw_q2=_optional_number(row, 'raw_q2'),
        raw_q3=_optional_number(row, 'raw_q3'),
        raw_q4=_optional_number(row, 'raw_q4'),
        source=row.get('source', ''),
        source_status=row.get('source_status', ''),
        raw_s=_optional_number(row, 'raw_s'),
        raw_scalar_s=_optional_number(row, 'raw_scalar_s'),
        area=row.get('area', '') or row['account_name'],
    )


def load_latest_dataset(
    export_root: str | Path = DEFAULT_EXPORT_DIR,
    history_store: HistoryStore | None = None,
) -> DashboardDataset:
    resolved_root = Path(export_root).expanduser().resolve()
    store = history_store or HistoryStore(DEFAULT_HISTORY_DB)
    store.bootstrap_from_exports(resolved_root)
    intervals = store.interval_rows()
    if not intervals:
        raise FileNotFoundError(
            'The history database is empty. Run a meter-data update first.'
        )
    readings = [_reading(row) for row in intervals]
    daily = daily_meter_records(readings)
    weekly = weekly_meter_records(readings, daily)
    spikes = spike_register(daily)
    solar_positive_events = solar_positive_register(daily)
    if not daily:
        raise ValueError('The history database has no daily meter records.')
    areas = tuple(sorted({row['area'] for row in daily}))
    database_stats = store.stats()
    dates = [_row_date(row) for row in daily]
    manifest = _dataset_manifest(
        intervals, daily, weekly, spikes, solar_positive_events, database_stats
    )
    combined_dir = resolved_root / '_dashboard_current'
    dataset = DashboardDataset(
        run_dir=combined_dir,
        manifest=manifest,
        daily=daily,
        weekly=weekly,
        spikes=spikes,
        intervals=intervals,
        areas=areas,
        first_date=min(dates),
        last_date=max(dates),
        solar_positive_events=solar_positive_events,
    )
    _write_dataset_snapshot(dataset)
    return dataset


def _select_daily(
    rows: Iterable[dict[str, str]], start_date: date, end_date: date, area: str
) -> list[dict[str, str]]:
    return [
        row for row in rows
        if start_date <= _row_date(row) <= end_date
        and (area == ALL_AREAS or row['area'] == area)
    ]


def _percent_change(current: float, previous: float) -> float | None:
    return None if previous == 0 else ((current - previous) / abs(previous)) * 100.0


def _area_rows(rows: Iterable[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row['area']].append(row)
    return groups


def build_supply_demand_balance(
    dataset: DashboardDataset,
    start_date: date,
    end_date: date,
) -> SupplyDemandBalance:
    """Compare all warehouse meters with the solar meter at matching intervals.

    The solar account is confirmed as generation output. The remaining warehouse
    requirement is an estimate, not a measured grid-import value.
    """
    start_date = max(start_date, dataset.first_date)
    end_date = min(end_date, dataset.last_date)
    if end_date < start_date:
        start_date = end_date

    warehouse_areas = {area for area in dataset.areas if area != SOLAR_AREA}
    grouped: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            'date': '',
            'warehouse_areas': set(),
            'solar_seen': False,
            'warehouse_kw': 0.0,
            'warehouse_kwh': 0.0,
            'solar_kw': 0.0,
            'solar_kwh': 0.0,
            'period_seconds': 0,
        }
    )
    for row in dataset.intervals:
        row_date = _row_date(row)
        if not start_date <= row_date <= end_date:
            continue
        timestamp = str(row['timestamp'])
        values = grouped[timestamp]
        values['date'] = row_date.isoformat()
        values['period_seconds'] = max(
            int(values['period_seconds']), int(_number(row, 'period_seconds'))
        )
        area = str(row.get('area', ''))
        if area == SOLAR_AREA:
            values['solar_seen'] = True
            values['solar_kw'] = float(values['solar_kw']) + _number(row, 'kw_import')
            values['solar_kwh'] = float(values['solar_kwh']) + _number(row, 'import_kwh')
        elif area in warehouse_areas:
            cast_areas = values['warehouse_areas']
            if isinstance(cast_areas, set):
                cast_areas.add(area)
            values['warehouse_kw'] = float(values['warehouse_kw']) + _number(row, 'kw_import')
            values['warehouse_kwh'] = float(values['warehouse_kwh']) + _number(row, 'import_kwh')

    intervals: list[dict[str, object]] = []
    for timestamp, values in sorted(grouped.items()):
        seen_areas = values['warehouse_areas']
        if (
            not warehouse_areas
            or not isinstance(seen_areas, set)
            or not warehouse_areas.issubset(seen_areas)
            or not bool(values['solar_seen'])
        ):
            continue
        warehouse_kw = float(values['warehouse_kw'])
        warehouse_kwh = float(values['warehouse_kwh'])
        solar_kw = float(values['solar_kw'])
        solar_kwh = float(values['solar_kwh'])
        intervals.append({
            'date': str(values['date']),
            'timestamp': timestamp,
            'time': timestamp[11:16],
            'period_seconds': int(values['period_seconds']) or 1800,
            'warehouse_kw': warehouse_kw,
            'warehouse_kwh': warehouse_kwh,
            'solar_kw': solar_kw,
            'solar_kwh': solar_kwh,
            'estimated_grid_kw': max(warehouse_kw - solar_kw, 0.0),
            'estimated_grid_kwh': max(warehouse_kwh - solar_kwh, 0.0),
            'solar_used_kwh': min(warehouse_kwh, solar_kwh),
            'possible_excess_solar_kwh': max(solar_kwh - warehouse_kwh, 0.0),
        })

    daily_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    profile_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in intervals:
        daily_groups[str(row['date'])].append(row)
        profile_groups[str(row['time'])].append(row)

    daily: list[dict[str, object]] = []
    for day_value, rows in sorted(daily_groups.items()):
        warehouse_kwh = sum(float(row['warehouse_kwh']) for row in rows)
        solar_kwh = sum(float(row['solar_kwh']) for row in rows)
        solar_used_kwh = sum(float(row['solar_used_kwh']) for row in rows)
        estimated_grid_kwh = sum(float(row['estimated_grid_kwh']) for row in rows)
        possible_excess = sum(float(row['possible_excess_solar_kwh']) for row in rows)
        peak_grid = max(rows, key=lambda row: float(row['estimated_grid_kw']))
        daily.append({
            'date': day_value,
            'warehouse_kwh': warehouse_kwh,
            'solar_kwh': solar_kwh,
            'solar_used_kwh': solar_used_kwh,
            'estimated_grid_kwh': estimated_grid_kwh,
            'possible_excess_solar_kwh': possible_excess,
            'solar_contribution_percent': (
                solar_used_kwh / warehouse_kwh * 100.0 if warehouse_kwh else 0.0
            ),
            'peak_estimated_grid_kw': float(peak_grid['estimated_grid_kw']),
            'peak_estimated_grid_time': str(peak_grid['timestamp']),
        })

    profile: list[dict[str, object]] = []
    for time_value, rows in sorted(profile_groups.items()):
        profile.append({
            'time': time_value,
            'average_warehouse_kw': fmean(float(row['warehouse_kw']) for row in rows),
            'average_solar_kw': fmean(float(row['solar_kw']) for row in rows),
            'average_estimated_grid_kw': fmean(
                float(row['estimated_grid_kw']) for row in rows
            ),
        })

    warehouse_kwh = sum(float(row['warehouse_kwh']) for row in intervals)
    solar_kwh = sum(float(row['solar_kwh']) for row in intervals)
    solar_used_kwh = sum(float(row['solar_used_kwh']) for row in intervals)
    estimated_grid_kwh = sum(float(row['estimated_grid_kwh']) for row in intervals)
    possible_excess = sum(
        float(row['possible_excess_solar_kwh']) for row in intervals
    )
    above_solar = [
        row for row in intervals
        if float(row['warehouse_kw']) > float(row['solar_kw'])
    ]
    above_solar_hours = sum(
        int(row['period_seconds']) / 3600.0 for row in above_solar
    )
    matched_hours = sum(
        int(row['period_seconds']) / 3600.0 for row in intervals
    )
    peak_grid = max(
        intervals, key=lambda row: float(row['estimated_grid_kw']), default=None
    )
    best_solar_day = max(
        daily, key=lambda row: float(row['solar_contribution_percent']), default=None
    )
    metrics: dict[str, object] = {
        'warehouse_kwh': warehouse_kwh,
        'solar_kwh': solar_kwh,
        'solar_used_kwh': solar_used_kwh,
        'estimated_grid_kwh': estimated_grid_kwh,
        'remaining_demand_percent': (
            estimated_grid_kwh / warehouse_kwh * 100.0 if warehouse_kwh else 0.0
        ),
        'possible_excess_solar_kwh': possible_excess,
        'solar_contribution_percent': (
            solar_used_kwh / warehouse_kwh * 100.0 if warehouse_kwh else 0.0
        ),
        'solar_flow_ratio_percent': (
            solar_kwh / warehouse_kwh * 100.0 if warehouse_kwh else 0.0
        ),
        'demand_above_solar_intervals': len(above_solar),
        'demand_above_solar_percent': (
            len(above_solar) / len(intervals) * 100.0 if intervals else 0.0
        ),
        'demand_above_solar_hours': above_solar_hours,
        'matched_hours': matched_hours,
        'matched_intervals': len(intervals),
        'data_days': len(daily),
        'peak_estimated_grid_kw': (
            float(peak_grid['estimated_grid_kw']) if peak_grid else 0.0
        ),
        'peak_estimated_grid_time': (
            str(peak_grid['timestamp']) if peak_grid else '—'
        ),
        'best_solar_day': str(best_solar_day['date']) if best_solar_day else '—',
        'best_solar_day_percent': (
            float(best_solar_day['solar_contribution_percent'])
            if best_solar_day else 0.0
        ),
        'has_solar_meter': SOLAR_AREA in dataset.areas,
    }
    return SupplyDemandBalance(
        start_date=start_date,
        end_date=end_date,
        intervals=intervals,
        daily=daily,
        profile=profile,
        metrics=metrics,
    )


def build_dashboard_view(
    dataset: DashboardDataset,
    start_date: date,
    end_date: date,
    area: str = ALL_AREAS,
    comparison_shift_days: int | None = None,
    comparison_start_date: date | None = None,
    comparison_end_date: date | None = None,
) -> DashboardView:
    start_date = max(start_date, dataset.first_date)
    end_date = min(end_date, dataset.last_date)
    if end_date < start_date:
        start_date = end_date
    selected = _select_daily(dataset.daily, start_date, end_date, area)
    span = (end_date - start_date).days + 1
    explicit_comparison = comparison_start_date is not None or comparison_end_date is not None
    if explicit_comparison:
        if comparison_start_date is None or comparison_end_date is None:
            raise ValueError('Both comparison dates are required.')
        if comparison_end_date < comparison_start_date:
            raise ValueError('The comparison end date cannot be before its start date.')
        previous_start = comparison_start_date
        previous_end = comparison_end_date
        comparison_shift = None
    else:
        comparison_shift = comparison_shift_days or span
        if comparison_shift <= 0:
            raise ValueError('The comparison shift must be at least one day.')
        previous_start = start_date - timedelta(days=comparison_shift)
        previous_end = end_date - timedelta(days=comparison_shift)
    previous = _select_daily(dataset.daily, previous_start, previous_end, area)
    spikes = [
        row for row in dataset.spikes
        if start_date <= _row_date(row) <= end_date
        and (area == ALL_AREAS or row['area'] == area)
    ]
    spikes.sort(
        key=lambda row: (-_row_date(row).toordinal(), SEVERITY_ORDER.get(row['alert_level'], 9))
    )
    solar_positive_events = [
        row for row in dataset.solar_positive_events
        if start_date <= _row_date(row) <= end_date
        and (area == ALL_AREAS or row['area'] == area)
    ]
    solar_positive_events.sort(key=lambda row: _row_date(row), reverse=True)

    total_import = sum(_number(row, 'import_kwh') for row in selected)
    previous_import = sum(_number(row, 'import_kwh') for row in previous)
    selected_keys = {(row['area'], _row_date(row)) for row in selected}
    selected_areas = {area_name for area_name, _row_date_value in selected_keys}
    selected_dates = {
        start_date + timedelta(days=offset) for offset in range(span)
    }
    current_complete = selected_keys == {
        (area_name, row_date) for area_name in selected_areas for row_date in selected_dates
    } and all(row.get('data_quality_status') == 'Complete' for row in selected)
    if explicit_comparison:
        comparison_dates = {
            previous_start + timedelta(days=offset)
            for offset in range((previous_end - previous_start).days + 1)
        }
        expected_previous_keys = {
            (area_name, row_date)
            for area_name in selected_areas
            for row_date in comparison_dates
        }
    else:
        expected_previous_keys = {
            (area_name, row_date - timedelta(days=comparison_shift))
            for area_name, row_date in selected_keys
        }
    previous_keys = {(row['area'], _row_date(row)) for row in previous}
    previous_quality_complete = all(
        row.get('data_quality_status') == 'Complete' for row in previous
    )
    comparison_complete = (
        bool(selected_keys)
        and current_complete
        and previous_quality_complete
        and previous_keys == expected_previous_keys
    )
    dates = {_row_date(row) for row in selected}
    peak_row = max(selected, key=lambda row: _number(row, 'peak_kva'), default=None)
    peak_kw_row = max(selected, key=lambda row: _number(row, 'peak_kw'), default=None)
    alert_counts = Counter(row['alert_level'] for row in selected)
    metrics: dict[str, object] = {
        'total_import_kwh': total_import,
        'previous_import_kwh': previous_import,
        'change_percent': (
            _percent_change(total_import, previous_import) if comparison_complete else None
        ),
        'comparison_complete': comparison_complete,
        'comparison_start': previous_start.isoformat(),
        'comparison_end': previous_end.isoformat(),
        'average_daily_kwh': total_import / len(dates) if dates else 0.0,
        'peak_kw': _number(peak_kw_row, 'peak_kw') if peak_kw_row else 0.0,
        'peak_kw_area': peak_kw_row['area'] if peak_kw_row else '—',
        'peak_kw_time': peak_kw_row['peak_kw_time'] if peak_kw_row else '—',
        'peak_kva': _number(peak_row, 'peak_kva') if peak_row else 0.0,
        'peak_area': peak_row['area'] if peak_row else '—',
        'peak_time': peak_row['peak_kva_time'] if peak_row else '—',
        'peak_date': peak_row['date'] if peak_row else '—',
        'alert_days': sum(alert_counts[level] for level in ('Watch', 'High', 'Critical')),
        'critical_days': alert_counts['Critical'],
        'data_days': len(dates),
        'complete_rows': sum(row['data_quality_status'] == 'Complete' for row in selected),
        'row_count': len(selected),
    }

    previous_by_area = _area_rows(previous)
    area_summary: list[dict[str, object]] = []
    for area_name, rows in sorted(_area_rows(selected).items()):
        area_import = sum(_number(row, 'import_kwh') for row in rows)
        prior_rows = previous_by_area.get(area_name, [])
        prior_import = sum(_number(row, 'import_kwh') for row in prior_rows)
        current_area_dates = {_row_date(row) for row in rows}
        current_area_quality = all(
            row.get('data_quality_status') == 'Complete' for row in rows
        )
        prior_area_quality = all(
            row.get('data_quality_status') == 'Complete' for row in prior_rows
        )
        if explicit_comparison:
            area_comparison_complete = (
                current_area_quality
                and prior_area_quality
                and current_area_dates == selected_dates
                and {_row_date(row) for row in prior_rows} == comparison_dates
            )
        else:
            area_comparison_complete = (
                current_area_quality
                and prior_area_quality
                and {
                    _row_date(row) - timedelta(days=comparison_shift) for row in rows
                } == {_row_date(row) for row in prior_rows}
            )
        area_alerts = Counter(row['alert_level'] for row in rows)
        area_peak = max(rows, key=lambda row: _number(row, 'peak_kva'))
        area_peak_kw = max(rows, key=lambda row: _number(row, 'peak_kw'))
        status = (
            'Critical' if area_alerts['Critical']
            else 'High' if area_alerts['High']
            else 'Watch' if area_alerts['Watch']
            else 'Stable'
        )
        area_summary.append({
            'area': area_name,
            'import_kwh': area_import,
            'share_percent': (area_import / total_import * 100.0) if total_import else 0.0,
            'change_percent': (
                _percent_change(area_import, prior_import)
                if area_comparison_complete else None
            ),
            'peak_kw': _number(area_peak_kw, 'peak_kw'),
            'peak_kw_time': area_peak_kw['peak_kw_time'],
            'peak_kva': _number(area_peak, 'peak_kva'),
            'peak_time': area_peak['peak_kva_time'],
            'alert_days': sum(area_alerts[level] for level in ('Watch', 'High', 'Critical')),
            'status': status,
        })
    area_summary.sort(key=lambda row: float(row['import_kwh']), reverse=True)
    insights = _build_insights(metrics, area_summary, selected)
    return DashboardView(
        start_date=start_date,
        end_date=end_date,
        area=area,
        daily=selected,
        previous_daily=previous,
        spikes=spikes,
        metrics=metrics,
        area_summary=area_summary,
        insights=insights,
        solar_positive_events=solar_positive_events,
    )


def _month_end(value: date) -> date:
    next_month = (value.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


def comparison_period_ranges(
    comparison_type: str,
    first_period: str,
    second_period: str,
    available_start: date,
    available_end: date,
) -> tuple[date, date, date, date, str]:
    """Resolve user-friendly year, month or ISO-week selections to fair ranges."""

    if available_end < available_start:
        raise ValueError('The available comparison history is invalid.')
    if first_period == second_period:
        raise ValueError('Choose two different periods to compare.')

    def clip(start: date, end: date) -> tuple[date, date]:
        clipped_start = max(start, available_start)
        clipped_end = min(end, available_end)
        if clipped_end < clipped_start:
            raise ValueError('One selected period falls outside the available meter history.')
        return clipped_start, clipped_end

    if comparison_type == 'Year vs year':
        try:
            first_year = int(first_period)
            second_year = int(second_period)
        except ValueError as exc:
            raise ValueError('Choose valid calendar years.') from exc
        first_start, first_end = clip(date(first_year, 1, 1), date(first_year, 12, 31))
        second_start, second_end = clip(date(second_year, 1, 1), date(second_year, 12, 31))
        shared_start = max(
            (first_start.month, first_start.day),
            (second_start.month, second_start.day),
        )
        shared_end = min(
            (first_end.month, first_end.day),
            (second_end.month, second_end.day),
        )
        if shared_end < shared_start:
            raise ValueError('The selected years do not contain overlapping available calendar dates.')

        def calendar_date(year: int, month_day: tuple[int, int]) -> date:
            month, day_number = month_day
            return date(year, month, min(day_number, _month_end(date(year, month, 1)).day))

        first_start = calendar_date(first_year, shared_start)
        first_end = calendar_date(first_year, shared_end)
        second_start = calendar_date(second_year, shared_start)
        second_end = calendar_date(second_year, shared_end)
        note = (
            'The selected calendar years are compared across the same available month-and-day range. '
            'A current partial year is matched only to the same elapsed dates in the other year.'
        )
        return first_start, first_end, second_start, second_end, note

    if comparison_type == 'Month vs month':
        try:
            first_month = date.fromisoformat(first_period + '-01')
            second_month = date.fromisoformat(second_period + '-01')
        except ValueError as exc:
            raise ValueError('Choose valid calendar months.') from exc
        first_start, first_end = clip(first_month, _month_end(first_month))
        second_start, second_end = clip(second_month, _month_end(second_month))
        shared_start_offset = max(
            (first_start - first_month).days,
            (second_start - second_month).days,
        )
        shared_end_offset = min(
            (first_end - first_month).days,
            (second_end - second_month).days,
        )
        if shared_end_offset < shared_start_offset:
            raise ValueError('The selected months do not contain overlapping available days.')
        first_start = first_month + timedelta(days=shared_start_offset)
        first_end = first_month + timedelta(days=shared_end_offset)
        second_start = second_month + timedelta(days=shared_start_offset)
        second_end = second_month + timedelta(days=shared_end_offset)
        note = (
            'The selected calendar months are compared across the same number of available days. '
            'A current partial month is matched only to the same elapsed days in the other month.'
        )
        return first_start, first_end, second_start, second_end, note

    if comparison_type == 'Week vs week':
        def week_start(value: str) -> date:
            try:
                year_text, week_text = value.split('-W', 1)
                return date.fromisocalendar(int(year_text), int(week_text), 1)
            except (ValueError, TypeError) as exc:
                raise ValueError('Choose valid Monday-to-Sunday calendar weeks.') from exc

        first_week = week_start(first_period)
        second_week = week_start(second_period)
        first_start, first_end = clip(first_week, first_week + timedelta(days=6))
        second_start, second_end = clip(second_week, second_week + timedelta(days=6))
        shared_start_offset = max(
            (first_start - first_week).days,
            (second_start - second_week).days,
        )
        shared_end_offset = min(
            (first_end - first_week).days,
            (second_end - second_week).days,
        )
        if shared_end_offset < shared_start_offset:
            raise ValueError('The selected weeks do not contain overlapping available weekdays.')
        first_start = first_week + timedelta(days=shared_start_offset)
        first_end = first_week + timedelta(days=shared_end_offset)
        second_start = second_week + timedelta(days=shared_start_offset)
        second_end = second_week + timedelta(days=shared_end_offset)
        note = (
            'The selected Monday-to-Sunday weeks are compared across the same number of available days.'
        )
        return first_start, first_end, second_start, second_end, note

    raise ValueError('Comparison basis must be Year vs year, Month vs month or Week vs week.')


def comparison_dates(
    comparison_type: str,
    anchor_date: date,
    selected_start: date | None = None,
    selected_end: date | None = None,
    selected_previous_start: date | None = None,
    selected_previous_end: date | None = None,
) -> tuple[date, date, date, date, str]:
    if comparison_type in {
        'Custom ranges', 'Year vs year', 'Month vs month', 'Week vs week'
    }:
        if (
            selected_start is None
            or selected_end is None
            or selected_previous_start is None
            or selected_previous_end is None
        ):
            raise ValueError('Custom ranges require a start and end date for both periods.')
        if selected_end < selected_start:
            raise ValueError('The current comparison end date cannot precede its start date.')
        if selected_previous_end < selected_previous_start:
            raise ValueError('The comparison range end date cannot precede its start date.')
        notes = {
            'Custom ranges': (
                'The two independently selected date ranges are compared exactly as shown. '
                'Average-per-day figures help when the ranges contain different numbers of days.'
            ),
            'Year vs year': (
                'The selected calendar years are compared across the same available calendar dates.'
            ),
            'Month vs month': (
                'The selected calendar months are compared across the same number of elapsed days.'
            ),
            'Week vs week': (
                'The selected Monday-to-Sunday weeks are compared across the same number of elapsed days.'
            ),
        }
        return (
            selected_start,
            selected_end,
            selected_previous_start,
            selected_previous_end,
            notes[comparison_type],
        )
    if comparison_type == 'Year to year':
        current_start = anchor_date.replace(month=1, day=1)
        current_end = anchor_date
        previous_start = current_start.replace(year=current_start.year - 1)
        previous_month_end = _month_end(
            current_end.replace(year=current_end.year - 1, day=1)
        )
        previous_end = current_end.replace(
            year=current_end.year - 1,
            day=min(current_end.day, previous_month_end.day),
        )
        return (
            current_start,
            current_end,
            previous_start,
            previous_end,
            'The current year to the selected To date is compared with the same calendar '
            'dates in the preceding year.',
        )
    if comparison_type == 'Selected dates':
        if selected_start is None or selected_end is None:
            raise ValueError('Selected dates require both a start and end date.')
        if selected_end < selected_start:
            raise ValueError('The selected comparison end date cannot precede its start date.')
        current_start = selected_start
        current_end = selected_end
        if (
            current_start.year == current_end.year
            and current_start.month == 1
            and current_start.day == 1
        ):
            previous_start = current_start.replace(year=current_start.year - 1)
            previous_month_end = _month_end(
                current_end.replace(year=current_end.year - 1, day=1)
            )
            previous_end = current_end.replace(
                year=current_end.year - 1,
                day=min(current_end.day, previous_month_end.day),
            )
            note = (
                'The selected year-to-date range is compared with the same calendar dates '
                'in the preceding year.'
            )
        elif (
            current_start.day == 1
            and current_start.year == current_end.year
            and current_start.month == current_end.month
        ):
            previous_month_end = current_start - timedelta(days=1)
            previous_start = previous_month_end.replace(day=1)
            elapsed_days = (current_end - current_start).days
            previous_end = min(
                previous_month_end,
                previous_start + timedelta(days=elapsed_days),
            )
            note = (
                'The selected calendar-month range is compared with the same elapsed '
                'calendar days in the preceding month.'
            )
        else:
            duration = current_end - current_start
            previous_end = current_start - timedelta(days=1)
            previous_start = previous_end - duration
            note = (
                'The exact selected date range is compared with the immediately preceding '
                'range of the same length.'
            )
        return current_start, current_end, previous_start, previous_end, note
    if comparison_type == 'Weekly':
        current_end = anchor_date - timedelta(days=(anchor_date.weekday() + 1) % 7)
        current_start = current_end - timedelta(days=6)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=6)
        note = (
            'The latest completed Monday-to-Sunday week on or before the selected To date '
            'is compared with the immediately preceding completed week.'
        )
        return current_start, current_end, previous_start, previous_end, note
    if comparison_type != 'Monthly':
        raise ValueError(
            'Comparison type must be Year vs year, Month vs month, Week vs week, '
            'Year to year, Selected dates, Custom ranges, Weekly or Monthly.'
        )

    current_start = anchor_date.replace(day=1)
    current_end = anchor_date
    previous_month_end = current_start - timedelta(days=1)
    previous_start = previous_month_end.replace(day=1)
    if current_end == _month_end(current_end):
        previous_end = previous_month_end
        note = (
            'The selected full calendar month is compared with the full preceding calendar month. '
            'Average-per-day figures are included because month lengths can differ.'
        )
    else:
        elapsed_days = (current_end - current_start).days
        previous_end = min(previous_month_end, previous_start + timedelta(days=elapsed_days))
        note = (
            'The selected month to date is compared with the same elapsed calendar days in the '
            'preceding month, so the totals cover equivalent portions of each month.'
        )
    return current_start, current_end, previous_start, previous_end, note


def _daily_totals(rows: Iterable[dict[str, str]]) -> dict[date, float]:
    totals: dict[date, float] = defaultdict(float)
    for row in rows:
        totals[_row_date(row)] += _number(row, 'import_kwh')
    return totals


def build_period_comparison(
    dataset: DashboardDataset,
    comparison_type: str,
    anchor_date: date,
    area: str = ALL_AREAS,
    selected_start: date | None = None,
    selected_end: date | None = None,
    selected_previous_start: date | None = None,
    selected_previous_end: date | None = None,
) -> PeriodComparison:
    """Build a site-wide weekly or monthly comparison using closed meter data."""
    anchor_date = min(anchor_date, dataset.last_date)
    current_start, current_end, previous_start, previous_end, note = comparison_dates(
        comparison_type,
        anchor_date,
        selected_start,
        selected_end,
        selected_previous_start,
        selected_previous_end,
    )
    if area != ALL_AREAS and area not in dataset.areas:
        raise ValueError(f'Unknown meter area: {area}')
    if area == ALL_AREAS:
        primary_dataset = (
            without_area(dataset, SOLAR_AREA)
            if SOLAR_AREA in dataset.areas else dataset
        )
        primary_area = ALL_AREAS
    else:
        primary_dataset = only_area(dataset, area)
        primary_area = area
    current_view = build_dashboard_view(
        primary_dataset,
        current_start,
        current_end,
        primary_area,
        comparison_start_date=previous_start,
        comparison_end_date=previous_end,
    )
    previous_view = build_dashboard_view(
        primary_dataset, previous_start, previous_end, primary_area
    )

    current_solar_view: DashboardView | None = None
    previous_solar_view: DashboardView | None = None
    if area == ALL_AREAS and SOLAR_AREA in dataset.areas:
        current_solar_view = build_dashboard_view(
            dataset,
            current_start,
            current_end,
            SOLAR_AREA,
            comparison_start_date=previous_start,
            comparison_end_date=previous_end,
        )
        previous_solar_view = build_dashboard_view(
            dataset, previous_start, previous_end, SOLAR_AREA
        )

    current_days = (current_end - current_start).days + 1
    previous_days = (previous_end - previous_start).days + 1
    current_dates = [current_start + timedelta(days=offset) for offset in range(current_days)]
    previous_dates = [previous_start + timedelta(days=offset) for offset in range(previous_days)]
    common_days = min(current_days, previous_days)

    current_index = {
        (str(row['area']), _row_date(row)): row for row in current_view.daily
    }
    previous_index = {
        (str(row['area']), _row_date(row)): row for row in previous_view.daily
    }
    current_totals: dict[date, float] = defaultdict(float)
    previous_totals: dict[date, float] = defaultdict(float)
    included_current_rows: list[dict[str, str]] = []
    included_previous_rows: list[dict[str, str]] = []
    area_comparison: list[dict[str, object]] = []
    incomplete_areas: list[str] = []
    estimated_areas: list[str] = []
    excluded_area_day_pairs = 0
    estimated_area_day_pairs = 0

    for area_name in sorted(primary_dataset.areas):
        area_current: list[dict[str, str]] = []
        area_previous: list[dict[str, str]] = []
        excluded = 0
        estimated = 0
        for offset in range(common_days):
            current_date = current_dates[offset]
            previous_date = previous_dates[offset]
            current_row = current_index.get((area_name, current_date))
            previous_row = previous_index.get((area_name, previous_date))
            if (
                current_row is not None
                and previous_row is not None
                and current_row.get('data_quality_status') in {'Complete', 'Estimated'}
                and previous_row.get('data_quality_status') in {'Complete', 'Estimated'}
            ):
                area_current.append(current_row)
                area_previous.append(previous_row)
                current_totals[current_date] += _number(current_row, 'import_kwh')
                previous_totals[previous_date] += _number(previous_row, 'import_kwh')
                if (
                    current_row.get('data_quality_status') == 'Estimated'
                    or previous_row.get('data_quality_status') == 'Estimated'
                ):
                    estimated += 1
            else:
                excluded += 1
        for current_date in current_dates[common_days:]:
            current_row = current_index.get((area_name, current_date))
            if (
                current_row is not None
                and current_row.get('data_quality_status') in {'Complete', 'Estimated'}
            ):
                area_current.append(current_row)
                current_totals[current_date] += _number(current_row, 'import_kwh')
                estimated += current_row.get('data_quality_status') == 'Estimated'
            else:
                excluded += 1
        for previous_date in previous_dates[common_days:]:
            previous_row = previous_index.get((area_name, previous_date))
            if (
                previous_row is not None
                and previous_row.get('data_quality_status') in {'Complete', 'Estimated'}
            ):
                area_previous.append(previous_row)
                previous_totals[previous_date] += _number(previous_row, 'import_kwh')
                estimated += previous_row.get('data_quality_status') == 'Estimated'
            else:
                excluded += 1

        included_current_rows.extend(area_current)
        included_previous_rows.extend(area_previous)
        current_kwh = sum(_number(row, 'import_kwh') for row in area_current)
        previous_kwh = sum(_number(row, 'import_kwh') for row in area_previous)
        difference_kwh = current_kwh - previous_kwh
        raw_change = _percent_change(current_kwh, previous_kwh)
        if excluded:
            comparison_status = 'Incomplete data'
            incomplete_areas.append(area_name)
            excluded_area_day_pairs += excluded
        elif estimated:
            comparison_status = 'Estimated data'
            estimated_areas.append(area_name)
            estimated_area_day_pairs += estimated
        elif max(current_kwh, previous_kwh) < 50.0 or abs(difference_kwh) < 5.0:
            comparison_status = 'Low volume'
        else:
            comparison_status = 'Complete'
        current_peak_kw = max(
            (_number(row, 'peak_kw') for row in area_current), default=0.0
        )
        previous_peak_kw = max(
            (_number(row, 'peak_kw') for row in area_previous), default=0.0
        )
        current_peak_kva = max(
            (_number(row, 'peak_kva') for row in area_current), default=0.0
        )
        previous_peak_kva = max(
            (_number(row, 'peak_kva') for row in area_previous), default=0.0
        )
        area_comparison.append({
            'area': area_name,
            'current_kwh': current_kwh,
            'previous_kwh': previous_kwh,
            'difference_kwh': difference_kwh,
            'change_percent': raw_change if comparison_status == 'Complete' else None,
            'raw_change_percent': raw_change,
            'comparison_status': comparison_status,
            'excluded_day_pairs': excluded,
            'estimated_day_pairs': estimated,
            'current_peak_kw': current_peak_kw,
            'previous_peak_kw': previous_peak_kw,
            'current_peak_kva': current_peak_kva,
            'previous_peak_kva': previous_peak_kva,
            'current_unusual': sum(
                row.get('alert_level') in {'Watch', 'High', 'Critical'}
                for row in area_current
            ),
            'previous_unusual': sum(
                row.get('alert_level') in {'Watch', 'High', 'Critical'}
                for row in area_previous
            ),
        })
    area_comparison.sort(key=lambda row: abs(float(row['difference_kwh'])), reverse=True)

    def paired_solar_totals(
    ) -> tuple[dict[date, float], dict[date, float], str, int, int]:
        if current_solar_view is None or previous_solar_view is None:
            return {}, {}, 'Not applicable', 0, 0
        current_solar_index = {_row_date(row): row for row in current_solar_view.daily}
        previous_solar_index = {_row_date(row): row for row in previous_solar_view.daily}
        current_values: dict[date, float] = {}
        previous_values: dict[date, float] = {}
        estimated = 0
        excluded = 0
        for offset in range(common_days):
            current_date = current_dates[offset]
            previous_date = previous_dates[offset]
            current_row = current_solar_index.get(current_date)
            previous_row = previous_solar_index.get(previous_date)
            if (
                current_row is not None
                and previous_row is not None
                and current_row.get('data_quality_status') in {'Complete', 'Estimated'}
                and previous_row.get('data_quality_status') in {'Complete', 'Estimated'}
            ):
                current_values[current_date] = _number(current_row, 'import_kwh')
                previous_values[previous_date] = _number(previous_row, 'import_kwh')
                if (
                    current_row.get('data_quality_status') == 'Estimated'
                    or previous_row.get('data_quality_status') == 'Estimated'
                ):
                    estimated += 1
            else:
                excluded += 1
        for current_date in current_dates[common_days:]:
            row = current_solar_index.get(current_date)
            if row is not None and row.get('data_quality_status') in {'Complete', 'Estimated'}:
                current_values[current_date] = _number(row, 'import_kwh')
                estimated += row.get('data_quality_status') == 'Estimated'
            else:
                excluded += 1
        for previous_date in previous_dates[common_days:]:
            row = previous_solar_index.get(previous_date)
            if row is not None and row.get('data_quality_status') in {'Complete', 'Estimated'}:
                previous_values[previous_date] = _number(row, 'import_kwh')
                estimated += row.get('data_quality_status') == 'Estimated'
            else:
                excluded += 1
        quality = (
            'Incomplete data' if excluded
            else 'Estimated data' if estimated
            else 'Complete'
        )
        return current_values, previous_values, quality, estimated, excluded

    (
        current_solar_totals,
        previous_solar_totals,
        solar_quality,
        estimated_solar_day_pairs,
        excluded_solar_day_pairs,
    ) = paired_solar_totals()
    solar_complete = solar_quality in {'Complete', 'Not applicable'}
    chart_days = max(current_days, previous_days)
    daily: list[dict[str, object]] = []
    solar_daily: list[dict[str, object]] = []
    for offset in range(chart_days):
        current_date = current_start + timedelta(days=offset) if offset < current_days else None
        previous_date = previous_start + timedelta(days=offset) if offset < previous_days else None
        label = (
            current_date.strftime('%a')
            if comparison_type in {'Weekly', 'Week vs week'} and current_date is not None
            else current_date.strftime('%d %b')
            if comparison_type in {'Year to year', 'Year vs year'} and current_date is not None
            else f'Day {offset + 1}'
        )
        daily.append({
            'label': label,
            'current_date': current_date.isoformat() if current_date else '',
            'previous_date': previous_date.isoformat() if previous_date else '',
            'current_kwh': current_totals.get(current_date) if current_date else None,
            'previous_kwh': previous_totals.get(previous_date) if previous_date else None,
        })
        solar_daily.append({
            'label': label,
            'current_date': current_date.isoformat() if current_date else '',
            'previous_date': previous_date.isoformat() if previous_date else '',
            'current_kwh': current_solar_totals.get(current_date) if current_date else None,
            'previous_kwh': previous_solar_totals.get(previous_date) if previous_date else None,
        })

    current_total = sum(float(row['current_kwh']) for row in area_comparison)
    previous_total = sum(float(row['previous_kwh']) for row in area_comparison)
    current_solar = sum(current_solar_totals.values())
    previous_solar = sum(previous_solar_totals.values())
    driver_candidates = [
        row
        for row in area_comparison
        if row['comparison_status'] != 'Low volume'
        and max(float(row['current_kwh']), float(row['previous_kwh'])) >= 50.0
    ]
    biggest_driver = max(
        driver_candidates,
        key=lambda row: abs(float(row['difference_kwh'])),
        default=None,
    )
    current_peak_kw_row = max(
        included_current_rows, key=lambda row: _number(row, 'peak_kw'), default=None
    )
    previous_peak_kw_row = max(
        included_previous_rows, key=lambda row: _number(row, 'peak_kw'), default=None
    )
    current_peak_kva_row = max(
        included_current_rows, key=lambda row: _number(row, 'peak_kva'), default=None
    )
    previous_peak_kva_row = max(
        included_previous_rows, key=lambda row: _number(row, 'peak_kva'), default=None
    )
    metrics: dict[str, object] = {
        'current_warehouse_kwh': current_total,
        'previous_warehouse_kwh': previous_total,
        'warehouse_difference_kwh': current_total - previous_total,
        'warehouse_change_percent': _percent_change(current_total, previous_total),
        'current_average_daily_kwh': current_total / current_days if current_days else 0.0,
        'previous_average_daily_kwh': previous_total / previous_days if previous_days else 0.0,
        'current_peak_kw': _number(current_peak_kw_row, 'peak_kw') if current_peak_kw_row else 0.0,
        'previous_peak_kw': _number(previous_peak_kw_row, 'peak_kw') if previous_peak_kw_row else 0.0,
        'current_peak_kw_area': current_peak_kw_row['area'] if current_peak_kw_row else '—',
        'previous_peak_kw_area': previous_peak_kw_row['area'] if previous_peak_kw_row else '—',
        'current_peak_kva': _number(current_peak_kva_row, 'peak_kva') if current_peak_kva_row else 0.0,
        'previous_peak_kva': _number(previous_peak_kva_row, 'peak_kva') if previous_peak_kva_row else 0.0,
        'current_peak_kva_area': current_peak_kva_row['area'] if current_peak_kva_row else '—',
        'previous_peak_kva_area': previous_peak_kva_row['area'] if previous_peak_kva_row else '—',
        'current_unusual': sum(
            row.get('alert_level') in {'Watch', 'High', 'Critical'}
            for row in included_current_rows
        ),
        'previous_unusual': sum(
            row.get('alert_level') in {'Watch', 'High', 'Critical'}
            for row in included_previous_rows
        ),
        'current_solar_kwh': current_solar,
        'previous_solar_kwh': previous_solar,
        'solar_change_percent': (
            _percent_change(current_solar, previous_solar) if solar_complete else None
        ),
        'biggest_driver_area': str(biggest_driver['area']) if biggest_driver else '—',
        'biggest_driver_kwh': float(biggest_driver['difference_kwh']) if biggest_driver else 0.0,
        'biggest_driver_status': (
            str(biggest_driver['comparison_status']) if biggest_driver else 'Not available'
        ),
        'comparison_complete': (
            not incomplete_areas
            and not estimated_areas
            and solar_complete
        ),
        'incomplete_areas': tuple(incomplete_areas),
        'estimated_areas': tuple(estimated_areas),
        'excluded_area_day_pairs': excluded_area_day_pairs,
        'estimated_area_day_pairs': estimated_area_day_pairs,
        'solar_quality': solar_quality,
        'estimated_solar_day_pairs': estimated_solar_day_pairs,
        'excluded_solar_day_pairs': excluded_solar_day_pairs,
        'scope_area': area,
        'scope_is_solar': area == SOLAR_AREA,
        'has_separate_solar': current_solar_view is not None,
    }
    return PeriodComparison(
        comparison_type=comparison_type,
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
        current_view=current_view,
        previous_view=previous_view,
        current_solar_view=current_solar_view,
        previous_solar_view=previous_solar_view,
        daily=daily,
        solar_daily=solar_daily,
        area_comparison=area_comparison,
        metrics=metrics,
        note=note,
        area=area,
    )


def build_solar_performance(
    dataset: DashboardDataset,
    start_date: date,
    end_date: date,
) -> SolarPerformance:
    """Build solar-only output measures from the recorded solar meter channel."""

    if SOLAR_AREA not in dataset.areas:
        raise ValueError('The configured solar meter is not present in the dataset.')
    start_date = max(start_date, first_valid_area_date(dataset, SOLAR_AREA))
    end_date = min(end_date, dataset.last_date)
    if end_date < start_date:
        start_date = end_date
    view = build_dashboard_view(dataset, start_date, end_date, SOLAR_AREA)
    comparison = build_period_comparison(
        dataset,
        'Selected dates',
        end_date,
        SOLAR_AREA,
        start_date,
        end_date,
    )

    valid_intervals = [
        row
        for row in dataset.intervals
        if row.get('area') == SOLAR_AREA
        and start_date <= _row_date(row) <= end_date
        and str(row.get('source_status', '')).casefold() in {'', 'ok'}
    ]
    intervals_by_date: dict[date, list[dict[str, str]]] = defaultdict(list)
    profile_values: dict[str, list[float]] = defaultdict(list)
    for row in valid_intervals:
        intervals_by_date[_row_date(row)].append(row)
        try:
            time_label = datetime.fromisoformat(str(row['timestamp'])).strftime('%H:%M')
        except ValueError:
            time_label = str(row.get('time', ''))[:5]
        profile_values[time_label].append(max(_number(row, 'kw_import'), 0.0))

    profile = [
        {
            'time': time_label,
            'average_kw': fmean(values),
            'peak_kw': max(values),
        }
        for time_label, values in sorted(profile_values.items())
        if time_label
    ]

    daily: list[dict[str, object]] = []
    previous_usable: dict[str, str] | None = None
    total_active_hours = 0.0
    for source_row in sorted(view.daily, key=_row_date):
        row_date = _row_date(source_row)
        productive = [
            row for row in intervals_by_date.get(row_date, [])
            if _number(row, 'kw_import') > 0.1
        ]
        active_hours = sum(
            _number(row, 'period_seconds') or 1800.0 for row in productive
        ) / 3600.0
        total_active_hours += active_hours
        output_times = sorted(str(row['timestamp']) for row in productive)
        quality = str(source_row.get('data_quality_status', 'Incomplete'))
        usable = quality in {'Complete', 'Estimated'}
        change_percent: float | None = None
        if (
            usable
            and previous_usable is not None
            and _row_date(previous_usable) == row_date - timedelta(days=1)
        ):
            change_percent = _percent_change(
                _number(source_row, 'import_kwh'),
                _number(previous_usable, 'import_kwh'),
            )
        daily.append({
            'date': row_date.isoformat(),
            'generation_kwh': _number(source_row, 'import_kwh'),
            'peak_kw': _number(source_row, 'peak_kw'),
            'peak_time': str(source_row.get('peak_kw_time', '')),
            'active_hours': active_hours,
            'output_start': output_times[0] if output_times else '',
            'output_end': output_times[-1] if output_times else '',
            'change_percent': change_percent,
            'data_quality_status': quality,
            'alert_level': str(source_row.get('alert_level', 'None')),
        })
        previous_usable = source_row if usable else None

    usable_daily = [
        row for row in daily
        if row['data_quality_status'] in {'Complete', 'Estimated'}
    ]
    best_day = max(
        usable_daily,
        key=lambda row: float(row['generation_kwh']),
        default=None,
    )
    quality_counts = Counter(str(row['data_quality_status']) for row in daily)
    comparison_row = comparison.area_comparison[0] if comparison.area_comparison else None
    productive_profile = [row for row in profile if float(row['average_kw']) > 0.1]
    metrics: dict[str, object] = {
        'total_generation_kwh': float(view.metrics['total_import_kwh']),
        'average_daily_generation_kwh': float(view.metrics['average_daily_kwh']),
        'peak_output_kw': float(view.metrics['peak_kw']),
        'peak_output_time': str(view.metrics['peak_kw_time']),
        'best_day_kwh': float(best_day['generation_kwh']) if best_day else 0.0,
        'best_day_date': str(best_day['date']) if best_day else '—',
        'active_generation_hours': total_active_hours,
        'average_generation_hours_per_day': (
            total_active_hours / len(usable_daily) if usable_daily else 0.0
        ),
        'average_output_while_generating_kw': (
            float(view.metrics['total_import_kwh']) / total_active_hours
            if total_active_hours else 0.0
        ),
        'typical_output_start': (
            str(productive_profile[0]['time']) if productive_profile else '—'
        ),
        'typical_output_end': (
            str(productive_profile[-1]['time']) if productive_profile else '—'
        ),
        'complete_days': quality_counts['Complete'],
        'estimated_days': quality_counts['Estimated'],
        'incomplete_days': len(daily) - quality_counts['Complete'] - quality_counts['Estimated'],
        'unusual_days': sum(
            row['alert_level'] in {'Watch', 'High', 'Critical'} for row in daily
        ),
        'higher_generation_days': sum(
            row['alert_level'] == 'Positive' for row in daily
        ),
        'previous_period_kwh': (
            float(comparison_row['previous_kwh']) if comparison_row else 0.0
        ),
        'difference_kwh': (
            float(comparison_row['difference_kwh']) if comparison_row else 0.0
        ),
        'change_percent': comparison_row.get('change_percent') if comparison_row else None,
        'comparison_status': (
            str(comparison_row['comparison_status'])
            if comparison_row else 'Not available'
        ),
        'comparison_start': comparison.previous_start.isoformat(),
        'comparison_end': comparison.previous_end.isoformat(),
    }
    return SolarPerformance(
        start_date=start_date,
        end_date=end_date,
        view=view,
        comparison=comparison,
        daily=daily,
        profile=profile,
        metrics=metrics,
    )


def _build_insights(
    metrics: dict[str, object],
    area_summary: list[dict[str, object]],
    daily: list[dict[str, str]],
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    try:
        peak_time = datetime.fromisoformat(str(metrics['peak_time'])).strftime('%d %b %Y, %H:%M')
    except ValueError:
        peak_time = str(metrics['peak_time'])
    try:
        peak_kw_time = datetime.fromisoformat(str(metrics['peak_kw_time'])).strftime('%d %b %Y, %H:%M')
    except ValueError:
        peak_kw_time = str(metrics['peak_kw_time'])
    try:
        comparison_start = date.fromisoformat(str(metrics['comparison_start'])).strftime('%d %b %Y')
        comparison_end = date.fromisoformat(str(metrics['comparison_end'])).strftime('%d %b %Y')
    except ValueError:
        comparison_start = str(metrics['comparison_start'])
        comparison_end = str(metrics['comparison_end'])
    change = metrics['change_percent']
    if isinstance(change, float):
        direction = 'higher' if change > 0 else 'lower'
        difference = float(metrics['total_import_kwh']) - float(metrics['previous_import_kwh'])
        insights.append({
            'icon': 'trending_up' if change > 0 else 'trending_down',
            'tone': 'negative' if change > 10 else 'positive' if change < 0 else 'warning',
            'title': 'Use compared with the previous period',
            'text': (
                f"Electricity use was {abs(change):.1f}% {direction} "
                f"({abs(difference):,.0f} kWh) than "
                f"{comparison_start} to {comparison_end}."
            ),
        })
    else:
        insights.append({
            'icon': 'date_range',
            'tone': 'primary',
            'title': 'Previous-period comparison',
            'text': 'A full earlier period is not available for a reliable comparison.',
        })
    if area_summary:
        top = area_summary[0]
        insights.append({
            'icon': 'warehouse',
            'tone': 'primary',
            'title': 'Area with the highest recorded use',
            'text': (
                f"{top['area']} recorded {float(top['import_kwh']):,.0f} kWh, "
                f"or {float(top['share_percent']):.1f}% of the selected meters' total."
            ),
        })
    insights.append({
        'icon': 'speed',
        'tone': 'warning',
        'title': 'Highest load and demand recorded',
        'text': (
            f"Working load reached {float(metrics['peak_kw']):,.1f} kW at "
            f"{metrics['peak_kw_area']} on {peak_kw_time}. Total demand reached "
            f"{float(metrics['peak_kva']):,.1f} kVA at {metrics['peak_area']} on {peak_time}."
        ),
    })
    alert_days = int(metrics['alert_days'])
    critical = int(metrics['critical_days'])
    insights.append({
        'icon': 'priority_high' if alert_days else 'verified',
        'tone': 'negative' if critical else 'warning' if alert_days else 'positive',
        'title': 'Unusual usage days',
        'text': (
            f'{alert_days} daily area readings were unusual, including {critical} marked for urgent review. '
            'The meter shows where and when; site records are needed to confirm why.'
            if alert_days else 'No unusual use or demand was detected in the selected period.'
        ),
    })
    incomplete = sum(row['data_quality_status'] != 'Complete' for row in daily)
    if incomplete:
        insights.append({
            'icon': 'data_alert',
            'tone': 'warning',
            'title': 'Incomplete meter data',
            'text': f'{incomplete} meter-day records are incomplete and should be checked before use.',
        })
    return insights


def interval_profile(
    dataset: DashboardDataset, start_date: date, end_date: date, area: str
) -> list[dict[str, object]]:
    by_timestamp: dict[str, float] = defaultdict(float)
    for row in dataset.intervals:
        row_date = _row_date(row)
        if not start_date <= row_date <= end_date:
            continue
        if area != ALL_AREAS and row.get('area') != area:
            continue
        by_timestamp[row['timestamp']] += _number(row, 'kw_net')
    by_time: dict[str, list[float]] = defaultdict(list)
    for timestamp, value in by_timestamp.items():
        by_time[datetime.fromisoformat(timestamp).strftime('%H:%M')].append(value)
    return [
        {'time': time, 'average_kw': fmean(values), 'peak_kw': max(values)}
        for time, values in sorted(by_time.items())
    ]
