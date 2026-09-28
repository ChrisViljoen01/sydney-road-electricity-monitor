from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from electricity_tool.dashboard_charts import (
    _base,
    cost_area_comparison_options,
    cost_trend_options,
    period_area_change_options,
    solar_period_comparison_options,
    solar_signal_options,
    unusual_by_area_options,
    unusual_frequency_summary,
    unusual_month_position_options,
    unusual_time_of_day_options,
    unusual_timeline_options,
)
from electricity_tool.tariffs import CostAnalysis


class DashboardChartLayoutTests(unittest.TestCase):
    def test_cost_charts_show_total_labels_and_period_comparison(self) -> None:
        analysis = CostAnalysis(
            start_date=date(2026, 7, 1), end_date=date(2026, 7, 2),
            comparison_start=date(2026, 6, 29), comparison_end=date(2026, 6, 30),
            area='All areas',
            daily=[
                {
                    'date': '2026-07-01', 'energy_cost_after_solar': 90.0,
                    'service_cost': 5.0, 'solar_avoided_cost': 10.0,
                    'total_cost': 105.0, 'estimated_total_cost': 95.0,
                },
                {
                    'date': '2026-07-02', 'energy_cost_after_solar': 108.0,
                    'service_cost': 5.0, 'solar_avoided_cost': 12.0,
                    'total_cost': 125.0, 'estimated_total_cost': 113.0,
                },
            ],
            comparison_daily=[],
            area_rows=[{
                'area': 'Warehouse 6',
                'current_total_cost': 230.0,
                'comparison_total_cost': 200.0,
            }],
            tariff_rows=[], metrics={}, warnings=(),
        )

        trend = cost_trend_options(analysis)
        comparison = cost_area_comparison_options(analysis)

        self.assertEqual(trend['series'][0]['name'], 'Cost before solar savings')
        self.assertEqual(trend['series'][1]['name'], 'Cost after solar savings')
        self.assertEqual(trend['series'][2]['name'], 'Solar savings')
        self.assertEqual(trend['series'][0]['data'][0]['value'], 105.0)
        self.assertEqual(trend['series'][1]['data'][0]['value'], 95.0)
        self.assertTrue(trend['series'][1]['label']['show'])
        self.assertEqual(comparison['series'][0]['data'][0]['value'], 230.0)
        self.assertEqual(comparison['series'][0]['data'][0]['label']['formatter'], 'R 230')
        self.assertTrue(comparison['series'][0]['label']['show'])
        self.assertNotIn('function (params)', str(trend))
        self.assertNotIn('function (params)', str(comparison))

    def test_chart_plot_starts_below_title_and_legend(self) -> None:
        options = _base('Test chart')
        self.assertLess(options['title']['top'], options['grid']['top'])
        self.assertGreaterEqual(options['grid']['top'], 80)
        self.assertGreaterEqual(options['grid']['bottom'], 60)

    def test_area_change_uses_absolute_kwh_and_marks_incomplete_data(self) -> None:
        comparison = SimpleNamespace(area_comparison=[
            {
                'area': 'Warehouse 6', 'difference_kwh': 378.0,
                'change_percent': 4.9, 'comparison_status': 'Complete',
            },
            {
                'area': 'Warehouse 9', 'difference_kwh': -1.3,
                'change_percent': None, 'comparison_status': 'Incomplete data',
                'excluded_day_pairs': 1,
            },
            {
                'area': 'Warehouse 7', 'difference_kwh': 12.5,
                'change_percent': None, 'comparison_status': 'Estimated data',
                'estimated_day_pairs': 2,
            },
        ])

        options = period_area_change_options(comparison)
        wh9_index = options['yAxis']['data'].index('Warehouse 9')
        wh9_bar = options['series'][0]['data'][wh9_index]

        self.assertEqual(options['xAxis']['name'], 'kWh change')
        self.assertEqual(wh9_bar['value'], -1.3)
        self.assertEqual(wh9_bar['itemStyle']['color'], '#007D6D')
        self.assertEqual(wh9_bar['label']['formatter'], '-1.3 kWh recorded')
        incomplete_series = next(
            series for series in options['series'] if series['name'] == 'Incomplete days'
        )
        self.assertEqual(incomplete_series['data'][0]['value'], [-1.3, 'Warehouse 9'])
        self.assertEqual(incomplete_series['data'][0]['itemStyle']['color'], '#94A3B8')
        wh7_index = options['yAxis']['data'].index('Warehouse 7')
        wh7_bar = options['series'][0]['data'][wh7_index]
        self.assertEqual(wh7_bar['itemStyle']['color'], '#E04403')
        self.assertEqual(wh7_bar['label']['formatter'], '+12.5 kWh recorded')

    def test_long_solar_comparison_uses_monthly_totals(self) -> None:
        start = date(2026, 1, 1)
        previous_start = date(2025, 1, 1)
        rows = []
        for offset in range(365):
            rows.append({
                'label': f'Day {offset + 1}',
                'current_date': (start + timedelta(days=offset)).isoformat(),
                'previous_date': (previous_start + timedelta(days=offset)).isoformat(),
                'current_kwh': 10.0,
                'previous_kwh': 8.0,
            })
        comparison = SimpleNamespace(
            current_start=start,
            current_end=date(2026, 12, 31),
            previous_start=previous_start,
            previous_end=date(2025, 12, 31),
            solar_daily=rows,
        )

        options = solar_period_comparison_options(comparison)

        self.assertEqual(options['title']['subtext'], 'Monthly totals')
        self.assertEqual(len(options['xAxis']['data']), 12)
        self.assertEqual(options['xAxis']['data'][0], 'Jan')
        self.assertEqual(options['series'][0]['data'][0], 310.0)

    def test_unusual_usage_charts_show_time_and_area_signals(self) -> None:
        view = SimpleNamespace(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            spikes=[
                {'date': '2026-07-14', 'area': 'Warehouse 7', 'alert_level': 'Critical', 'alert_type': 'Consumption and demand'},
                {'date': '2026-07-15', 'area': 'Warehouse 6', 'alert_level': 'Watch', 'alert_type': 'Consumption'},
                {'date': '2026-07-16', 'area': 'Connect Logistics Solar', 'alert_level': 'High', 'alert_type': 'Low solar generation'},
            ],
        )

        timeline = unusual_timeline_options(view)
        by_area = unusual_by_area_options(view)

        self.assertEqual(timeline['title']['subtext'], 'Daily incidents')
        self.assertEqual(sum(sum(series['data']) for series in timeline['series']), 2)
        self.assertEqual(by_area['yAxis']['data'], ['Warehouse 6', 'Warehouse 7'])
        self.assertEqual(sum(sum(series['data']) for series in by_area['series']), 3)

    def test_solar_signals_are_split_from_unusual_usage(self) -> None:
        view = SimpleNamespace(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            spikes=[{
                'date': '2026-07-10',
                'area': 'Connect Logistics Solar',
                'alert_level': 'High',
                'alert_type': 'Low solar generation',
                'consumption_variance_percent': -50.0,
            }],
            solar_positive_events=[{
                'date': '2026-07-14',
                'area': 'Connect Logistics Solar',
                'consumption_variance_percent': 60.0,
            }],
        )

        options = solar_signal_options(view)
        values = [item['value'] for item in options['series'][0]['data']]

        self.assertEqual(values, [-50.0, 60.0])
        self.assertEqual(options['series'][0]['data'][0]['itemStyle']['color'], '#DC2626')
        self.assertEqual(options['series'][0]['data'][1]['itemStyle']['color'], '#008575')

    def test_unusual_frequency_charts_group_peak_time_and_month_position(self) -> None:
        view = SimpleNamespace(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            spikes=[
                {
                    'date': '2026-07-14', 'area': 'Warehouse 7',
                    'alert_level': 'Critical', 'alert_type': 'Consumption and demand',
                    'peak_kw_time': '2026-07-14 15:00:00',
                    'peak_kva_time': '2026-07-14 15:00:00',
                },
                {
                    'date': '2026-07-15', 'area': 'Warehouse 6',
                    'alert_level': 'Watch', 'alert_type': 'Consumption',
                    'peak_kw_time': '2026-07-15 17:30:00',
                    'peak_kva_time': '2026-07-15 17:30:00',
                },
                {
                    'date': '2026-07-31', 'area': 'Warehouse 8',
                    'alert_level': 'High', 'alert_type': 'Demand',
                    'peak_kw_time': '2026-07-31 15:30:00',
                    'peak_kva_time': '2026-07-31 15:30:00',
                },
            ],
        )

        by_time = unusual_time_of_day_options(view)
        by_month = unusual_month_position_options(view)
        summary = unusual_frequency_summary(view)

        self.assertEqual(summary['time_label'], '14:00-15:59')
        self.assertEqual(summary['time_count'], 2)
        self.assertEqual(summary['month_label'], 'Days 8-14')
        self.assertEqual(summary['timed_incidents'], 3)
        self.assertEqual(by_time['xAxis']['data'][7], '14-16')
        self.assertEqual(
            sum(series['data'][7]['value'] for series in by_time['series']),
            2,
        )
        self.assertIn(
            '2',
            [
                series['data'][7]['label']['formatter']
                for series in by_time['series']
                if series['data'][7]['label']['show']
            ],
        )
        self.assertEqual(by_month['xAxis']['data'], ['1-7', '8-14', '15-21', '22-28', '29-31'])
        self.assertEqual(
            sum(series['data'][4]['value'] for series in by_month['series']),
            1,
        )


if __name__ == '__main__':
    unittest.main()
