from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import unittest

from electricity_tool.dashboard_data import (
    ALL_AREAS,
    DashboardDataset,
    build_dashboard_view,
    build_period_comparison,
    build_solar_performance,
    build_supply_demand_balance,
    comparison_dates,
    comparison_period_ranges,
    first_valid_area_date,
    interval_profile,
    _merge_incremental_dataset,
    _reading,
)
from electricity_tool.models import Reading
from electricity_tool.monitoring import (
    daily_meter_records,
    solar_positive_register,
    spike_register,
    weekly_meter_records,
)


def day(value: str, area: str, imported: float, peak: float, alert: str = 'None') -> dict[str, str]:
    return {
        'date': value,
        'area': area,
        'account_name': area,
        'account_code': area.replace(' ', ''),
        'account_eid': area[-1],
        'import_kwh': str(imported),
        'export_kwh': '0',
        'net_kwh': str(imported),
        'peak_kw': str(peak * 0.9),
        'peak_kw_time': value + ' 11:30:00',
        'peak_kva': str(peak),
        'peak_kva_time': value + ' 12:00:00',
        'alert_level': alert,
        'data_quality_status': 'Complete',
    }


class DashboardDataTests(unittest.TestCase):
    def setUp(self) -> None:
        daily = [
            day('2026-07-01', 'Warehouse 6', 100, 40),
            day('2026-07-01', 'Warehouse 7', 200, 60),
            day('2026-07-02', 'Warehouse 6', 120, 45, 'High'),
            day('2026-07-02', 'Warehouse 7', 240, 70),
            day('2026-07-03', 'Warehouse 6', 180, 80, 'Critical'),
            day('2026-07-03', 'Warehouse 7', 300, 75),
            day('2026-07-04', 'Warehouse 6', 240, 90),
            day('2026-07-04', 'Warehouse 7', 360, 85),
        ]
        intervals = [
            {'date': '2026-07-03', 'timestamp': '2026-07-03 08:00:00', 'area': 'Warehouse 6', 'kw_net': '30'},
            {'date': '2026-07-03', 'timestamp': '2026-07-03 08:00:00', 'area': 'Warehouse 7', 'kw_net': '50'},
            {'date': '2026-07-04', 'timestamp': '2026-07-04 08:00:00', 'area': 'Warehouse 6', 'kw_net': '40'},
            {'date': '2026-07-04', 'timestamp': '2026-07-04 08:00:00', 'area': 'Warehouse 7', 'kw_net': '60'},
        ]
        self.dataset = DashboardDataset(
            run_dir=Path('exports/test'),
            manifest={'row_counts': {}},
            daily=daily,
            weekly=[],
            spikes=[],
            intervals=intervals,
            areas=('Warehouse 6', 'Warehouse 7'),
            first_date=date(2026, 7, 1),
            last_date=date(2026, 7, 4),
        )

    def test_comparable_period_metrics_and_area_share(self) -> None:
        view = build_dashboard_view(
            self.dataset, date(2026, 7, 3), date(2026, 7, 4), ALL_AREAS
        )
        self.assertEqual(view.metrics['total_import_kwh'], 1080)
        self.assertEqual(view.metrics['previous_import_kwh'], 660)
        self.assertAlmostEqual(view.metrics['change_percent'], 63.6363636)
        self.assertEqual(view.metrics['peak_area'], 'Warehouse 6')
        self.assertEqual(view.metrics['peak_kw_area'], 'Warehouse 6')
        self.assertEqual(view.area_summary[0]['area'], 'Warehouse 7')
        self.assertEqual(view.area_summary[0]['share_percent'], 660 / 1080 * 100)

    def test_incremental_refresh_matches_full_analytics_rebuild(self) -> None:
        start = date(2025, 1, 1)

        def readings_for_day(value: date, kw: float) -> list[Reading]:
            return [
                Reading(
                    timestamp=datetime.combine(value, datetime.min.time())
                    + timedelta(minutes=30 * interval),
                    account_name='265 Sydney Rd WH 6',
                    account_code='E9623',
                    account_eid='10005',
                    period_seconds=1800,
                    kw_import=kw,
                    kw_export=0,
                    kw_net=kw,
                    kvar_net=2,
                    kva=kw + 1,
                    import_kwh=kw / 2,
                    export_kwh=0,
                    net_kwh=kw / 2,
                    power_factor=0.95,
                    kva_method='PNPSCADA vector S',
                    raw_p1=kw,
                    raw_p2=0,
                    raw_q1=2,
                    raw_q2=0,
                    raw_q3=0,
                    raw_q4=0,
                    source_status='Ok',
                    area='Warehouse 6',
                )
                for interval in range(48)
            ]

        existing_readings = [
            reading
            for offset in range(70)
            for reading in readings_for_day(start + timedelta(days=offset), 10)
        ]
        existing_daily = daily_meter_records(existing_readings)
        existing_weekly = weekly_meter_records(existing_readings, existing_daily)
        cached = DashboardDataset(
            run_dir=Path('exports/test'),
            manifest={'row_counts': {}},
            daily=existing_daily,
            weekly=existing_weekly,
            spikes=spike_register(existing_daily),
            intervals=[reading.to_csv_row() for reading in existing_readings],
            areas=('Warehouse 6',),
            first_date=start,
            last_date=start + timedelta(days=69),
            solar_positive_events=solar_positive_register(existing_daily),
        )
        incoming = [
            reading.to_csv_row()
            for reading in readings_for_day(start + timedelta(days=69), 20)
        ]

        incremental = _merge_incremental_dataset(
            cached,
            incoming,
            {'row_count': len(existing_readings), 'meter_count': 1},
        )
        expected_interval_index = {
            (str(row['account_eid']), str(row['timestamp'])): row
            for row in [*cached.intervals, *incoming]
        }
        expected_intervals = sorted(
            expected_interval_index.values(),
            key=lambda row: (str(row['account_eid']), str(row['timestamp'])),
        )
        expected_readings = [_reading(row) for row in expected_intervals]
        expected_daily = daily_meter_records(expected_readings)
        expected_weekly = weekly_meter_records(expected_readings, expected_daily)

        self.assertEqual(incremental.intervals, expected_intervals)
        self.assertEqual(incremental.daily, expected_daily)
        self.assertEqual(incremental.weekly, expected_weekly)
        self.assertEqual(incremental.spikes, spike_register(expected_daily))

    def test_area_filter(self) -> None:
        view = build_dashboard_view(
            self.dataset, date(2026, 7, 3), date(2026, 7, 4), 'Warehouse 6'
        )
        self.assertEqual(view.metrics['total_import_kwh'], 420)
        self.assertEqual(len(view.area_summary), 1)

    def test_comparison_is_hidden_when_earlier_period_is_incomplete(self) -> None:
        incomplete = DashboardDataset(
            run_dir=self.dataset.run_dir,
            manifest=self.dataset.manifest,
            daily=[
                row for row in self.dataset.daily
                if not (row['date'] == '2026-07-02' and row['area'] == 'Warehouse 7')
            ],
            weekly=[],
            spikes=[],
            intervals=self.dataset.intervals,
            areas=self.dataset.areas,
            first_date=self.dataset.first_date,
            last_date=self.dataset.last_date,
        )
        view = build_dashboard_view(
            incomplete, date(2026, 7, 3), date(2026, 7, 4), ALL_AREAS
        )
        self.assertFalse(view.metrics['comparison_complete'])
        self.assertIsNone(view.metrics['change_percent'])

    def test_combined_interval_profile(self) -> None:
        profile = interval_profile(
            self.dataset, date(2026, 7, 3), date(2026, 7, 4), ALL_AREAS
        )
        self.assertEqual(profile[0]['time'], '08:00')
        self.assertEqual(profile[0]['average_kw'], 90)
        self.assertEqual(profile[0]['peak_kw'], 100)

    def test_supply_demand_balance_uses_matching_complete_intervals(self) -> None:
        intervals = [
            {
                'date': '2026-07-03', 'timestamp': '2026-07-03 08:00:00',
                'area': 'Warehouse 6', 'period_seconds': '1800',
                'kw_import': '40', 'import_kwh': '20',
            },
            {
                'date': '2026-07-03', 'timestamp': '2026-07-03 08:00:00',
                'area': 'Warehouse 7', 'period_seconds': '1800',
                'kw_import': '60', 'import_kwh': '30',
            },
            {
                'date': '2026-07-03', 'timestamp': '2026-07-03 08:00:00',
                'area': 'Connect Logistics Solar', 'period_seconds': '1800',
                'kw_import': '40', 'import_kwh': '20',
            },
            {
                'date': '2026-07-03', 'timestamp': '2026-07-03 08:30:00',
                'area': 'Warehouse 6', 'period_seconds': '1800',
                'kw_import': '10', 'import_kwh': '5',
            },
            {
                'date': '2026-07-03', 'timestamp': '2026-07-03 08:30:00',
                'area': 'Warehouse 7', 'period_seconds': '1800',
                'kw_import': '20', 'import_kwh': '10',
            },
            {
                'date': '2026-07-03', 'timestamp': '2026-07-03 08:30:00',
                'area': 'Connect Logistics Solar', 'period_seconds': '1800',
                'kw_import': '50', 'import_kwh': '25',
            },
        ]
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=self.dataset.daily, weekly=[], spikes=[], intervals=intervals,
            areas=('Connect Logistics Solar', 'Warehouse 6', 'Warehouse 7'),
            first_date=date(2026, 7, 1), last_date=date(2026, 7, 4),
        )
        balance = build_supply_demand_balance(
            dataset, date(2026, 7, 3), date(2026, 7, 3)
        )
        self.assertEqual(balance.metrics['matched_intervals'], 2)
        self.assertEqual(balance.metrics['warehouse_kwh'], 65)
        self.assertEqual(balance.metrics['solar_kwh'], 45)
        self.assertEqual(balance.metrics['estimated_grid_kwh'], 30)
        self.assertAlmostEqual(balance.metrics['remaining_demand_percent'], 30 / 65 * 100)
        self.assertEqual(balance.metrics['solar_used_kwh'], 35)
        self.assertAlmostEqual(balance.metrics['solar_contribution_percent'], 35 / 65 * 100)
        self.assertEqual(balance.metrics['possible_excess_solar_kwh'], 10)
        self.assertEqual(balance.metrics['demand_above_solar_percent'], 50)
        self.assertEqual(balance.metrics['demand_above_solar_hours'], 0.5)
        self.assertEqual(balance.metrics['matched_hours'], 1.0)
        self.assertEqual(balance.metrics['peak_estimated_grid_kw'], 60)

    def test_solar_performance_measures_generation_and_output_hours(self) -> None:
        solar_area = 'Connect Logistics Solar'
        daily = [
            day('2026-07-01', solar_area, 100, 30),
            day('2026-07-02', solar_area, 120, 35),
            day('2026-07-03', solar_area, 150, 40),
            day('2026-07-04', solar_area, 180, 45),
        ]
        intervals = []
        for value in (date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 4)):
            for time_value, output in [('06:00:00', 0), ('08:00:00', 10), ('08:30:00', 20)]:
                intervals.append({
                    'date': value.isoformat(),
                    'timestamp': f'{value.isoformat()} {time_value}',
                    'area': solar_area,
                    'period_seconds': '1800',
                    'kw_import': str(output),
                    'import_kwh': str(output / 2),
                    'source_status': 'Ok',
                })
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=intervals,
            areas=(solar_area,),
            first_date=date(2026, 7, 1), last_date=date(2026, 7, 4),
        )

        performance = build_solar_performance(
            dataset, date(2026, 7, 3), date(2026, 7, 4)
        )

        self.assertEqual(first_valid_area_date(dataset, solar_area), date(2026, 7, 1))
        self.assertEqual(performance.metrics['total_generation_kwh'], 330)
        self.assertEqual(performance.metrics['previous_period_kwh'], 220)
        self.assertAlmostEqual(performance.metrics['change_percent'], 50.0)
        self.assertEqual(performance.metrics['active_generation_hours'], 2.0)
        self.assertEqual(performance.metrics['average_generation_hours_per_day'], 1.0)
        self.assertEqual(performance.metrics['best_day_date'], '2026-07-04')
        self.assertEqual(performance.daily[0]['output_start'], '2026-07-03 08:00:00')
        self.assertEqual(performance.daily[0]['output_end'], '2026-07-03 08:30:00')

    def test_weekly_comparison_uses_completed_monday_to_sunday_periods(self) -> None:
        daily = []
        for offset in range(14):
            value = date(2026, 6, 29) + timedelta(days=offset)
            current = value >= date(2026, 7, 6)
            daily.extend([
                day(value.isoformat(), 'Warehouse 6', 120 if current else 100, 50),
                day(value.isoformat(), 'Warehouse 7', 50, 25),
                day(value.isoformat(), 'Connect Logistics Solar', 30 if current else 20, 15),
            ])
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=[],
            areas=('Connect Logistics Solar', 'Warehouse 6', 'Warehouse 7'),
            first_date=date(2026, 6, 29), last_date=date(2026, 7, 12),
        )
        comparison = build_period_comparison(dataset, 'Weekly', date(2026, 7, 12))
        self.assertEqual(comparison.current_start, date(2026, 7, 6))
        self.assertEqual(comparison.current_end, date(2026, 7, 12))
        self.assertEqual(comparison.previous_start, date(2026, 6, 29))
        self.assertEqual(comparison.previous_end, date(2026, 7, 5))
        self.assertEqual(comparison.metrics['current_warehouse_kwh'], 1190)
        self.assertEqual(comparison.metrics['previous_warehouse_kwh'], 1050)
        self.assertAlmostEqual(comparison.metrics['warehouse_change_percent'], 140 / 1050 * 100)
        self.assertEqual(comparison.metrics['current_solar_kwh'], 210)
        self.assertEqual(comparison.metrics['previous_solar_kwh'], 140)
        self.assertTrue(comparison.metrics['comparison_complete'])

    def test_weekly_comparison_excludes_incomplete_area_day_pair(self) -> None:
        daily = []
        for offset in range(14):
            value = date(2026, 6, 29) + timedelta(days=offset)
            current = value >= date(2026, 7, 6)
            warehouse_6 = day(value.isoformat(), 'Warehouse 6', 120 if current else 100, 50)
            warehouse_9 = day(value.isoformat(), 'Warehouse 9', 1 if current else 2, 1)
            if value == date(2026, 7, 12):
                warehouse_9['data_quality_status'] = 'Incomplete'
            daily.extend([warehouse_6, warehouse_9])
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=[],
            areas=('Warehouse 6', 'Warehouse 9'),
            first_date=date(2026, 6, 29), last_date=date(2026, 7, 12),
        )

        comparison = build_period_comparison(dataset, 'Weekly', date(2026, 7, 12))
        warehouse_9 = next(
            row for row in comparison.area_comparison if row['area'] == 'Warehouse 9'
        )

        self.assertEqual(warehouse_9['current_kwh'], 6)
        self.assertEqual(warehouse_9['previous_kwh'], 12)
        self.assertEqual(warehouse_9['difference_kwh'], -6)
        self.assertEqual(warehouse_9['comparison_status'], 'Incomplete data')
        self.assertEqual(warehouse_9['excluded_day_pairs'], 1)
        self.assertIsNone(warehouse_9['change_percent'])
        self.assertFalse(comparison.metrics['comparison_complete'])
        self.assertEqual(comparison.metrics['incomplete_areas'], ('Warehouse 9',))
        self.assertEqual(comparison.metrics['excluded_area_day_pairs'], 1)
        self.assertEqual(comparison.metrics['biggest_driver_area'], 'Warehouse 6')

    def test_estimated_day_pair_remains_visible_without_percentage(self) -> None:
        daily = []
        for offset in range(14):
            value = date(2026, 6, 29) + timedelta(days=offset)
            current = value >= date(2026, 7, 6)
            row = day(value.isoformat(), 'Warehouse 6', 120 if current else 100, 50)
            if value == date(2026, 7, 9):
                row['data_quality_status'] = 'Estimated'
            daily.append(row)
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=[],
            areas=('Warehouse 6',),
            first_date=date(2026, 6, 29), last_date=date(2026, 7, 12),
        )

        comparison = build_period_comparison(dataset, 'Weekly', date(2026, 7, 12))
        warehouse_6 = comparison.area_comparison[0]

        self.assertEqual(warehouse_6['current_kwh'], 840)
        self.assertEqual(warehouse_6['previous_kwh'], 700)
        self.assertEqual(warehouse_6['comparison_status'], 'Estimated data')
        self.assertEqual(warehouse_6['estimated_day_pairs'], 1)
        self.assertEqual(warehouse_6['excluded_day_pairs'], 0)
        self.assertIsNone(warehouse_6['change_percent'])
        self.assertIsNotNone(comparison.daily[3]['current_kwh'])
        self.assertIsNotNone(comparison.daily[3]['previous_kwh'])
        self.assertEqual(comparison.metrics['estimated_areas'], ('Warehouse 6',))
        self.assertEqual(comparison.metrics['estimated_area_day_pairs'], 1)
        self.assertFalse(comparison.metrics['comparison_complete'])

    def test_monthly_comparison_aligns_elapsed_calendar_days(self) -> None:
        daily = []
        for month in (6, 7):
            for day_number in range(1, 14):
                value = date(2026, month, day_number)
                daily.extend([
                    day(value.isoformat(), 'Warehouse 6', 100, 50),
                    day(value.isoformat(), 'Warehouse 7', 50, 25),
                    day(value.isoformat(), 'Connect Logistics Solar', 20, 15),
                ])
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=[],
            areas=('Connect Logistics Solar', 'Warehouse 6', 'Warehouse 7'),
            first_date=date(2026, 6, 1), last_date=date(2026, 7, 13),
        )
        comparison = build_period_comparison(dataset, 'Monthly', date(2026, 7, 13))
        self.assertEqual(comparison.current_start, date(2026, 7, 1))
        self.assertEqual(comparison.current_end, date(2026, 7, 13))
        self.assertEqual(comparison.previous_start, date(2026, 6, 1))
        self.assertEqual(comparison.previous_end, date(2026, 6, 13))
        self.assertEqual(len(comparison.daily), 13)
        self.assertTrue(comparison.metrics['comparison_complete'])

    def test_selected_date_and_area_filters_control_comparison_scope(self) -> None:
        daily = []
        for month in (6, 7):
            for day_number in range(1, 14):
                value = date(2026, month, day_number)
                daily.extend([
                    day(value.isoformat(), 'Warehouse 6', 120 if month == 7 else 100, 50),
                    day(value.isoformat(), 'Warehouse 7', 500, 80),
                    day(value.isoformat(), 'Connect Logistics Solar', 30, 15),
                ])
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=[],
            areas=('Connect Logistics Solar', 'Warehouse 6', 'Warehouse 7'),
            first_date=date(2026, 6, 1), last_date=date(2026, 7, 13),
        )

        comparison = build_period_comparison(
            dataset,
            'Selected dates',
            date(2026, 7, 13),
            'Warehouse 6',
            date(2026, 7, 1),
            date(2026, 7, 13),
        )

        self.assertEqual(comparison.area, 'Warehouse 6')
        self.assertEqual(comparison.current_start, date(2026, 7, 1))
        self.assertEqual(comparison.current_end, date(2026, 7, 13))
        self.assertEqual(comparison.previous_start, date(2026, 6, 1))
        self.assertEqual(comparison.previous_end, date(2026, 6, 13))
        self.assertEqual(comparison.metrics['current_warehouse_kwh'], 13 * 120)
        self.assertEqual(comparison.metrics['previous_warehouse_kwh'], 13 * 100)
        self.assertEqual(
            [row['area'] for row in comparison.area_comparison], ['Warehouse 6']
        )
        self.assertIsNone(comparison.current_solar_view)
        self.assertFalse(comparison.metrics['has_separate_solar'])

    def test_custom_selected_dates_use_immediately_preceding_equal_range(self) -> None:
        daily = []
        for offset in range(14):
            value = date(2026, 6, 30) + timedelta(days=offset)
            daily.append(day(value.isoformat(), 'Warehouse 6', 100, 50))
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=[],
            areas=('Warehouse 6',),
            first_date=date(2026, 6, 30), last_date=date(2026, 7, 13),
        )

        comparison = build_period_comparison(
            dataset,
            'Selected dates',
            date(2026, 7, 13),
            'Warehouse 6',
            date(2026, 7, 7),
            date(2026, 7, 13),
        )

        self.assertEqual(comparison.previous_start, date(2026, 6, 30))
        self.assertEqual(comparison.previous_end, date(2026, 7, 6))
        self.assertEqual(comparison.metrics['current_warehouse_kwh'], 700)
        self.assertEqual(comparison.metrics['previous_warehouse_kwh'], 700)

    def test_year_to_year_uses_matching_calendar_dates(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_dates(
            'Year to year', date(2026, 7, 14)
        )

        self.assertEqual(current_start, date(2026, 1, 1))
        self.assertEqual(current_end, date(2026, 7, 14))
        self.assertEqual(previous_start, date(2025, 1, 1))
        self.assertEqual(previous_end, date(2025, 7, 14))

    def test_multiyear_selected_range_never_overlaps_previous_range(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_dates(
            'Selected dates',
            date(2026, 7, 14),
            date(2025, 1, 1),
            date(2026, 7, 14),
        )

        self.assertEqual(current_start, date(2025, 1, 1))
        self.assertEqual(current_end, date(2026, 7, 14))
        self.assertEqual(previous_end, date(2024, 12, 31))
        self.assertLess(previous_start, previous_end)
        self.assertEqual(current_end - current_start, previous_end - previous_start)

    def test_named_years_align_to_same_available_calendar_dates(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
            'Year vs year',
            '2026',
            '2025',
            date(2023, 11, 9),
            date(2026, 7, 14),
        )

        self.assertEqual((current_start, current_end), (date(2026, 1, 1), date(2026, 7, 14)))
        self.assertEqual((previous_start, previous_end), (date(2025, 1, 1), date(2025, 7, 14)))

    def test_named_months_align_partial_month_to_same_elapsed_days(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
            'Month vs month',
            '2026-07',
            '2026-02',
            date(2023, 11, 9),
            date(2026, 7, 14),
        )

        self.assertEqual((current_start, current_end), (date(2026, 7, 1), date(2026, 7, 14)))
        self.assertEqual((previous_start, previous_end), (date(2026, 2, 1), date(2026, 2, 14)))

    def test_named_full_months_use_the_shorter_calendar_month(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
            'Month vs month',
            '2025-03',
            '2025-02',
            date(2023, 11, 9),
            date(2026, 7, 14),
        )

        self.assertEqual((current_start, current_end), (date(2025, 3, 1), date(2025, 3, 28)))
        self.assertEqual((previous_start, previous_end), (date(2025, 2, 1), date(2025, 2, 28)))

    def test_named_months_keep_matching_day_numbers_at_history_boundary(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
            'Month vs month',
            '2023-11',
            '2023-12',
            date(2023, 11, 9),
            date(2026, 7, 14),
        )

        self.assertEqual((current_start, current_end), (date(2023, 11, 9), date(2023, 11, 30)))
        self.assertEqual((previous_start, previous_end), (date(2023, 12, 9), date(2023, 12, 30)))

    def test_named_weeks_align_partial_week_to_same_elapsed_days(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
            'Week vs week',
            '2026-W29',
            '2026-W28',
            date(2023, 11, 9),
            date(2026, 7, 14),
        )

        self.assertEqual((current_start, current_end), (date(2026, 7, 13), date(2026, 7, 14)))
        self.assertEqual((previous_start, previous_end), (date(2026, 7, 6), date(2026, 7, 7)))

    def test_named_weeks_keep_matching_weekdays_at_history_boundary(self) -> None:
        current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
            'Week vs week',
            '2023-W45',
            '2023-W46',
            date(2023, 11, 9),
            date(2026, 7, 14),
        )

        self.assertEqual((current_start, current_end), (date(2023, 11, 9), date(2023, 11, 12)))
        self.assertEqual((previous_start, previous_end), (date(2023, 11, 16), date(2023, 11, 19)))

    def test_named_periods_must_be_different(self) -> None:
        with self.assertRaisesRegex(ValueError, 'different periods'):
            comparison_period_ranges(
                'Year vs year',
                '2026',
                '2026',
                date(2023, 11, 9),
                date(2026, 7, 14),
            )

    def test_custom_ranges_are_compared_exactly(self) -> None:
        daily = []
        for value, imported in [
            (date(2026, 5, 1), 80),
            (date(2026, 5, 2), 90),
            (date(2026, 5, 3), 100),
            (date(2026, 7, 10), 110),
            (date(2026, 7, 11), 120),
        ]:
            daily.append(day(value.isoformat(), 'Warehouse 6', imported, 50))
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={'row_counts': {}},
            daily=daily, weekly=[], spikes=[], intervals=[],
            areas=('Warehouse 6',),
            first_date=date(2026, 5, 1), last_date=date(2026, 7, 11),
        )

        comparison = build_period_comparison(
            dataset,
            'Custom ranges',
            date(2026, 7, 11),
            'Warehouse 6',
            date(2026, 7, 10),
            date(2026, 7, 11),
            date(2026, 5, 1),
            date(2026, 5, 3),
        )

        self.assertEqual(comparison.current_start, date(2026, 7, 10))
        self.assertEqual(comparison.current_end, date(2026, 7, 11))
        self.assertEqual(comparison.previous_start, date(2026, 5, 1))
        self.assertEqual(comparison.previous_end, date(2026, 5, 3))
        self.assertEqual(comparison.metrics['current_warehouse_kwh'], 230)
        self.assertEqual(comparison.metrics['previous_warehouse_kwh'], 270)
        self.assertEqual(len(comparison.daily), 3)


if __name__ == '__main__':
    unittest.main()
