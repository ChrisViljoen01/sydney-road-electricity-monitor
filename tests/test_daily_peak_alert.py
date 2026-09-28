from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from electricity_tool.daily_peak_alert import (
    DailyPeakAlertError,
    build_daily_ctou_analysis,
    build_monthly_surcharge_analysis,
    complete_daily_records,
    require_ctou_baselines,
    has_ctou_peak_band,
    latest_ctou_peak_date,
)
from electricity_tool.dashboard_data import DashboardDataset
from electricity_tool.tariffs import BUILT_IN_CTOU_RATES


def _dataset(rows: list[dict[str, str]]) -> DashboardDataset:
    return DashboardDataset(
        run_dir=Path('exports/test'),
        manifest={},
        daily=rows,
        weekly=[],
        spikes=[],
        intervals=[],
        areas=('Warehouse 6', 'Warehouse 7'),
        first_date=date(2026, 7, 21),
        last_date=date(2026, 7, 21),
    )


def test_complete_daily_records_requires_every_configured_area() -> None:
    dataset = _dataset([
        {
            'date': '2026-07-21',
            'area': 'Warehouse 6',
            'data_quality_status': 'Complete',
        },
    ])

    with pytest.raises(DailyPeakAlertError, match='missing areas: Warehouse 7'):
        complete_daily_records(
            dataset,
            date(2026, 7, 21),
            {'Warehouse 6', 'Warehouse 7'},
        )


def test_latest_peak_date_skips_low_season_weekend() -> None:
    assert has_ctou_peak_band(date(2026, 9, 27)) is False
    assert has_ctou_peak_band(date(2026, 9, 26)) is False
    assert latest_ctou_peak_date(date(2026, 9, 27)) == date(2026, 9, 25)


def test_complete_daily_records_rejects_partial_portal_data() -> None:
    dataset = _dataset([
        {
            'date': '2026-07-21',
            'area': 'Warehouse 6',
            'data_quality_status': 'Complete',
        },
        {
            'date': '2026-07-21',
            'area': 'Warehouse 7',
            'data_quality_status': 'Incomplete',
        },
    ])

    with pytest.raises(DailyPeakAlertError, match='incomplete areas: Warehouse 7'):
        complete_daily_records(
            dataset,
            date(2026, 7, 21),
            {'Warehouse 6', 'Warehouse 7'},
        )


def test_complete_daily_records_returns_only_complete_configured_areas() -> None:
    rows = [
        {
            'date': '2026-07-21',
            'area': 'Warehouse 6',
            'data_quality_status': 'Complete',
        },
        {
            'date': '2026-07-21',
            'area': 'Warehouse 7',
            'data_quality_status': 'Complete',
        },
        {
            'date': '2026-07-21',
            'area': 'Unconfigured meter',
            'data_quality_status': 'Incomplete',
        },
    ]
    records = complete_daily_records(
        _dataset(rows),
        date(2026, 7, 21),
        {'Warehouse 6', 'Warehouse 7'},
    )

    assert [record['area'] for record in records] == ['Warehouse 6', 'Warehouse 7']


def test_ctou_analysis_flags_strict_peak_band_increase() -> None:
    comparison_dates = ('2026-06-30', '2026-07-07', '2026-07-14')
    daily = []
    intervals = []
    for area in ('Warehouse 6', 'Warehouse 7', 'Warehouse 8'):
        for value in (*comparison_dates, '2026-07-21'):
            daily.append({
                'date': value,
                'area': area,
                'data_quality_status': 'Complete',
            })
            peak_kwh = (
                16.0 if area == 'Warehouse 6' and value == '2026-07-21'
                else 14.0 if area == 'Warehouse 7' and value == '2026-07-21'
                else 10.0
            )
            intervals.extend([
                {
                    'date': value,
                    'timestamp': f'{value} 07:30:00',
                    'area': area,
                    'import_kwh': str(peak_kwh),
                    'kw_import': str(peak_kwh * 2),
                },
                {
                    'date': value,
                    'timestamp': f'{value} 12:00:00',
                    'area': area,
                    'import_kwh': '20',
                    'kw_import': '40',
                },
            ])
    dataset = DashboardDataset(
        run_dir=Path('exports/test'),
        manifest={},
        daily=daily,
        weekly=[],
        spikes=[],
        intervals=intervals,
        areas=('Warehouse 6', 'Warehouse 7', 'Warehouse 8'),
        first_date=date(2026, 6, 30),
        last_date=date(2026, 7, 21),
    )

    rows = build_daily_ctou_analysis(
        dataset,
        date(2026, 7, 21),
        BUILT_IN_CTOU_RATES[-1],
    )
    by_area = {str(row['area']): row for row in rows}

    assert by_area['Warehouse 6']['peak_kwh'] == 16
    assert by_area['Warehouse 6']['peak_rate_inc_vat'] == pytest.approx(8.049305)
    assert by_area['Warehouse 6']['peak_energy_charge_inc_vat'] == pytest.approx(
        16 * 8.049305
    )
    assert by_area['Warehouse 6']['baseline_peak_kwh'] == 10
    assert by_area['Warehouse 6']['increase_kwh'] == 6
    assert by_area['Warehouse 6']['action_required'] is True
    assert by_area['Warehouse 6']['peak_intervals'] == [{
        'start_time': '07:00',
        'end_time': '07:30',
        'kwh': 16.0,
        'average_kw': 32.0,
        'energy_charge_inc_vat': pytest.approx(16 * 8.049305),
    }]
    assert by_area['Warehouse 6']['peak_windows'] == [{
        'start_time': '07:00',
        'end_time': '07:30',
        'kwh': 16.0,
        'energy_charge_inc_vat': pytest.approx(16 * 8.049305),
        'half_hours': 1,
        'average_kw': 32.0,
        'rate_inc_vat': pytest.approx(8.049305),
    }]
    assert by_area['Warehouse 7']['increase_kwh'] == 4
    assert by_area['Warehouse 7']['action_required'] is False
    assert by_area['Warehouse 8']['action_required'] is False


def test_daily_email_requires_three_matching_weekday_baselines() -> None:
    with pytest.raises(DailyPeakAlertError, match='Warehouse 7'):
        require_ctou_baselines([
            {
                'area': 'Warehouse 6',
                'baseline_peak_kwh': 10.0,
                'baseline_days': 3,
            },
            {
                'area': 'Warehouse 7',
                'baseline_peak_kwh': None,
                'baseline_days': 2,
            },
        ])


def test_monthly_surcharge_analysis_tracks_kva_threshold_and_exposure() -> None:
    rows = []
    daily = []
    for area, kva in (
        ('Warehouse 6', 115.0),
        ('Warehouse 7', 90.0),
        ('Warehouse 8', 80.0),
    ):
        daily.append({
            'date': '2026-07-21',
            'area': area,
            'data_quality_status': 'Complete',
        })
        rows.extend([
            {
                'date': '2026-07-21',
                'timestamp': '2026-07-21 07:30:00',
                'area': area,
                'import_kwh': '10',
                'kva': str(kva),
            },
            {
                'date': '2026-07-21',
                'timestamp': '2026-07-21 12:00:00',
                'area': area,
                'import_kwh': '20',
                'kva': '50',
            },
        ])
    dataset = DashboardDataset(
        run_dir=Path('exports/test'),
        manifest={},
        daily=daily,
        weekly=[],
        spikes=[],
        intervals=rows,
        areas=('Warehouse 6', 'Warehouse 7', 'Warehouse 8'),
        first_date=date(2026, 7, 21),
        last_date=date(2026, 7, 21),
    )

    results = build_monthly_surcharge_analysis(
        dataset,
        date(2026, 7, 21),
        BUILT_IN_CTOU_RATES[-1],
    )
    by_area = {str(row['area']): row for row in results}

    warehouse_six = by_area['Warehouse 6']
    assert warehouse_six['maximum_kva'] == 115
    assert warehouse_six['threshold_variance_kva'] == 5
    assert warehouse_six['surcharge_active'] is True
    expected_energy_ex_vat = 10 * 6.9994 + 20 * 3.5022
    expected_demand_ex_vat = 115 * 149.37
    assert warehouse_six['estimated_surcharge_inc_vat'] == pytest.approx(
        (expected_energy_ex_vat + expected_demand_ex_vat) * 0.25 * 1.15
    )
    assert by_area['Warehouse 7']['surcharge_active'] is False
    assert by_area['Warehouse 7']['estimated_surcharge_inc_vat'] > 0
