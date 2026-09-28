from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import unittest

from electricity_tool.dashboard_data import ALL_AREAS, DashboardDataset
from electricity_tool.tariffs import (
    BUILT_IN_CTOU_RATES,
    BUILT_IN_RATES,
    build_cost_analysis,
    parse_ctou_tariff,
    parse_scale_one_tariff,
    resolve_cost_month_comparison,
    tou_band,
)


def daily_row(value: str, area: str, kwh: float) -> dict[str, str]:
    return {
        'date': value,
        'area': area,
        'import_kwh': str(kwh),
        'data_quality_status': 'Complete',
    }


class TariffTests(unittest.TestCase):
    def test_parses_final_scale_one_vat_inclusive_rates(self) -> None:
        text = '''
        EFFECTIVE : 01 July 2026 - 30 June 2027
        High Season (Jun-Aug) Low Season (Sep - May)
        c/kWh VAT incl c/kWh VAT incl
        403.65 464.20 403.65 464.20
        R VAT incl
        528.16 607.38
        BUSINESS & GENERAL SCALE 1
        '''

        rate = parse_scale_one_tariff(text, 'https://example.test/tariff.pdf')

        self.assertEqual(rate.effective_start, date(2026, 7, 1))
        self.assertEqual(rate.effective_end, date(2027, 6, 30))
        self.assertEqual(rate.high_season_r_per_kwh, 4.642)
        self.assertEqual(rate.low_season_r_per_kwh, 4.642)
        self.assertEqual(rate.service_charge_r_per_month, 607.38)

    def test_cost_analysis_uses_rate_valid_on_each_date_and_excludes_solar(self) -> None:
        dataset = DashboardDataset(
            run_dir=Path('exports/test'),
            manifest={},
            daily=[
                daily_row('2026-06-28', 'Warehouse 6', 80),
                daily_row('2026-06-29', 'Warehouse 6', 90),
                daily_row('2026-06-30', 'Warehouse 6', 100),
                daily_row('2026-07-01', 'Warehouse 6', 100),
                daily_row('2026-06-30', 'Connect Logistics Solar', 500),
                daily_row('2026-07-01', 'Connect Logistics Solar', 500),
            ],
            weekly=[], spikes=[], intervals=[],
            areas=('Connect Logistics Solar', 'Warehouse 6'),
            first_date=date(2026, 6, 28),
            last_date=date(2026, 7, 1),
        )

        analysis = build_cost_analysis(
            dataset,
            date(2026, 6, 30),
            date(2026, 7, 1),
            ALL_AREAS,
            BUILT_IN_RATES,
            date(2026, 6, 28),
            date(2026, 6, 29),
        )

        self.assertEqual(analysis.metrics['kwh'], 200)
        self.assertAlmostEqual(
            float(analysis.metrics['energy_cost']),
            100 * 3.2130 * 1.15 + 100 * 3.5022 * 1.15,
        )
        self.assertEqual(analysis.metrics['solar_generated_kwh'], 0)
        self.assertEqual(len(analysis.area_rows), 1)
        self.assertEqual(analysis.area_rows[0]['area'], 'Warehouse 6')
        self.assertEqual(len(analysis.tariff_rows), 4)
        self.assertEqual(
            {row['tariff'] for row in analysis.tariff_rows},
            {'Business & General Scale 1', 'Commercial Time of Use (CTOU)'},
        )

    def test_cost_analysis_values_matched_solar_and_projects_month_end(self) -> None:
        dates = ('2026-06-01', '2026-06-02', '2026-07-01', '2026-07-02')
        daily = []
        intervals = []
        for value in dates:
            daily.extend([
                daily_row(value, 'Warehouse 8', 100),
                daily_row(value, 'Connect Logistics Solar', 20),
            ])
            intervals.extend([
                {
                    'date': value, 'timestamp': f'{value} 12:00:00',
                    'area': 'Warehouse 8', 'period_seconds': '1800',
                    'kw_import': '200', 'import_kwh': '100',
                },
                {
                    'date': value, 'timestamp': f'{value} 12:00:00',
                    'area': 'Connect Logistics Solar', 'period_seconds': '1800',
                    'kw_import': '40', 'import_kwh': '20',
                },
            ])
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={}, daily=daily,
            weekly=[], spikes=[], intervals=intervals,
            areas=('Connect Logistics Solar', 'Warehouse 8'),
            first_date=date(2026, 6, 1), last_date=date(2026, 7, 2),
        )

        analysis = build_cost_analysis(
            dataset,
            date(2026, 7, 1), date(2026, 7, 2), ALL_AREAS,
            BUILT_IN_RATES,
            date(2026, 6, 1), date(2026, 6, 2),
        )

        self.assertEqual(analysis.metrics['solar_generated_kwh'], 40)
        self.assertEqual(analysis.metrics['solar_used_kwh'], 40)
        self.assertAlmostEqual(
            analysis.metrics['solar_avoided_cost'], 40 * 3.5022 * 1.15
        )
        self.assertAlmostEqual(
            analysis.metrics['estimated_total_cost'],
            analysis.metrics['total_cost'] - analysis.metrics['solar_avoided_cost'],
        )
        self.assertAlmostEqual(
            analysis.metrics['forecast_month_end_cost'],
            analysis.metrics['forecast_cost_to_date'] / 2 * 31,
        )
        self.assertLessEqual(
            analysis.metrics['forecast_month_end_cost_without_network_surcharge'],
            analysis.metrics['forecast_month_end_cost'],
        )
        historical_analysis = build_cost_analysis(
            dataset,
            date(2026, 6, 1), date(2026, 6, 2), ALL_AREAS,
            BUILT_IN_RATES,
            date(2026, 7, 1), date(2026, 7, 2),
        )
        self.assertEqual(historical_analysis.metrics['forecast_month'], 'July 2026')
        self.assertAlmostEqual(
            historical_analysis.metrics['forecast_month_end_cost'],
            analysis.metrics['forecast_month_end_cost'],
        )

    def test_current_month_comparison_uses_identical_complete_days(self) -> None:
        june_second = daily_row('2026-06-02', 'Warehouse 6', 50)
        june_second['data_quality_status'] = 'Incomplete'
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={},
            daily=[
                daily_row('2026-06-01', 'Warehouse 6', 50),
                june_second,
                daily_row('2026-07-01', 'Warehouse 6', 100),
                daily_row('2026-07-02', 'Warehouse 6', 1000),
            ],
            weekly=[], spikes=[], intervals=[], areas=('Warehouse 6',),
            first_date=date(2026, 6, 1), last_date=date(2026, 7, 2),
        )

        analysis = build_cost_analysis(
            dataset,
            date(2026, 7, 1), date(2026, 7, 2), ALL_AREAS,
            BUILT_IN_RATES,
            date(2026, 6, 1), date(2026, 6, 2),
        )

        self.assertEqual(analysis.metrics['forecast_elapsed_days'], 2)
        self.assertEqual(analysis.metrics['forecast_comparable_days'], 1)
        self.assertEqual(analysis.metrics['forecast_current_comparable_kwh'], 100)
        self.assertEqual(analysis.metrics['forecast_previous_comparable_kwh'], 50)
        self.assertEqual(analysis.metrics['forecast_mtd_usage_change_percent'], 100)
        self.assertLess(
            analysis.metrics['forecast_current_comparable_cost'],
            analysis.metrics['forecast_cost_to_date'],
        )

    def test_cost_month_comparison_resolves_full_and_matching_months(self) -> None:
        full = resolve_cost_month_comparison(
            '2026-06', '2026-05', date(2025, 1, 1), date(2026, 7, 17)
        )
        matched = resolve_cost_month_comparison(
            '2026-07', '2026-06', date(2025, 1, 1), date(2026, 7, 17),
            through_day=17,
        )

        self.assertEqual(
            full,
            (date(2026, 6, 1), date(2026, 6, 30), date(2026, 5, 1), date(2026, 5, 31)),
        )
        self.assertEqual(
            matched,
            (date(2026, 7, 1), date(2026, 7, 17), date(2026, 6, 1), date(2026, 6, 17)),
        )

    def test_cost_comparison_accepts_different_date_ranges(self) -> None:
        dataset = DashboardDataset(
            run_dir=Path('exports/test'), manifest={},
            daily=[
                daily_row('2026-07-01', 'Warehouse 6', 100),
                daily_row('2026-07-02', 'Warehouse 6', 100),
                daily_row('2026-07-03', 'Warehouse 6', 150),
                daily_row('2026-07-04', 'Warehouse 6', 150),
            ],
            weekly=[], spikes=[], intervals=[], areas=('Warehouse 6',),
            first_date=date(2026, 7, 1), last_date=date(2026, 7, 4),
        )

        analysis = build_cost_analysis(
            dataset,
            date(2026, 7, 3), date(2026, 7, 4), ALL_AREAS,
            BUILT_IN_RATES,
            date(2026, 7, 1), date(2026, 7, 2),
        )

        self.assertGreater(float(analysis.metrics['cost_change_percent']), 0)
        self.assertGreater(float(analysis.metrics['usage_change_percent']), 0)
        self.assertEqual(analysis.comparison_start, date(2026, 7, 1))
        self.assertEqual(analysis.comparison_end, date(2026, 7, 2))

    def test_parses_final_ctou_rates_when_pdf_text_joins_values(self) -> None:
        text = '''
        EFFECTIVE : 01 July 2026 - 30 June 2027
        699.94 804.93 350.22 402.75 170.61 196.20
        345.33 397.13 277.82319.49161.60 185.84
        149.37 171.78 25% Levied on the sum of the energy and demand charges
        741.22 852.40
        COMMERCIAL TIME OF USE (CTOU) < 100kVA
        '''

        rate = parse_ctou_tariff(text, 'https://example.test/final.pdf')

        self.assertEqual(rate.high_peak_r_per_kwh, 6.9994)
        self.assertEqual(rate.low_standard_r_per_kwh, 2.7782)
        self.assertEqual(rate.low_off_peak_r_per_kwh, 1.6160)
        self.assertEqual(rate.demand_charge_r_per_kva, 149.37)
        self.assertEqual(rate.service_charge_r_per_month, 741.22)
        self.assertEqual(rate.network_surcharge_percent, 25.0)

    def test_ctou_time_bands_follow_published_high_and_low_seasons(self) -> None:
        self.assertEqual(tou_band(datetime(2026, 7, 6, 6, 30)), 'peak')
        self.assertEqual(tou_band(datetime(2026, 7, 6, 12, 0)), 'standard')
        self.assertEqual(tou_band(datetime(2026, 7, 5, 12, 0)), 'off_peak')
        self.assertEqual(tou_band(datetime(2026, 5, 4, 7, 30)), 'peak')
        self.assertEqual(tou_band(datetime(2026, 5, 2, 18, 30)), 'standard')
        self.assertEqual(tou_band(datetime(2026, 5, 3, 12, 0)), 'off_peak')

    def test_verified_2025_26_ctou_rates_match_recovery_bills(self) -> None:
        rate = BUILT_IN_CTOU_RATES[2]
        self.assertEqual(rate.high_peak_r_per_kwh, 6.4215)
        self.assertEqual(rate.low_standard_r_per_kwh, 2.5488)
        self.assertEqual(rate.demand_charge_r_per_kva, 137.04)
        self.assertEqual(rate.service_charge_r_per_month, 680.02)


if __name__ == '__main__':
    unittest.main()
