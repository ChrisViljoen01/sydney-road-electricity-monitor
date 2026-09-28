from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
import tempfile
import unittest
import zipfile

from electricity_tool.dashboard_data import (
    ALL_AREAS,
    SOLAR_AREA,
    DashboardDataset,
    build_dashboard_view,
)
from electricity_tool.reporting import (
    REPORT_TYPES,
    _daily_chart,
    _recorded_change_percent,
    _report_bundle,
    generate_csv_export,
    generate_pdf_report,
    generate_xlsx_export,
    resolve_report_period,
)


def daily_row(value: str, area: str, used: float) -> dict[str, str]:
    return {
        'date': value,
        'area': area,
        'account_name': area,
        'account_code': area.replace(' ', ''),
        'account_eid': area[-1],
        'import_kwh': str(used),
        'export_kwh': '0',
        'net_kwh': str(used),
        'average_kw': '10',
        'peak_kw': '20',
        'peak_kw_time': f'{value} 10:30:00',
        'average_kva': '11',
        'peak_kva': '22',
        'peak_kva_time': f'{value} 10:30:00',
        'alert_level': 'None',
        'data_quality_status': 'Complete',
    }


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = DashboardDataset(
            run_dir=Path('exports/test'),
            manifest={'row_counts': {}},
            daily=[
                daily_row('2026-01-01', 'Warehouse 6', 100),
                daily_row('2026-07-13', 'Warehouse 6', 120),
                daily_row('2026-07-14', 'Warehouse 6', 130),
                daily_row('2026-07-14', 'Warehouse 7', 80),
            ],
            weekly=[],
            spikes=[],
            intervals=[],
            areas=('Warehouse 6', 'Warehouse 7'),
            first_date=date(2026, 1, 1),
            last_date=date(2026, 7, 14),
        )

    def test_report_periods_use_expected_management_boundaries(self) -> None:
        current = resolve_report_period(
            'Current month overview', date(2025, 1, 1), self.dataset,
            today=date(2026, 7, 15),
        )
        filtered = resolve_report_period(
            'Filtered overview', date(2026, 7, 14), self.dataset,
            today=date(2026, 7, 15),
            selected_start=date(2026, 7, 13),
            selected_end=date(2026, 7, 14),
        )

        self.assertEqual(current.start_date, date(2026, 7, 1))
        self.assertEqual(current.end_date, date(2026, 7, 14))
        self.assertFalse(current.comparison_enabled)
        self.assertEqual(filtered.start_date, date(2026, 7, 13))
        self.assertEqual(filtered.end_date, date(2026, 7, 14))
        self.assertFalse(filtered.comparison_enabled)

    def test_current_month_report_uses_latest_closed_data_not_anchor(self) -> None:
        period = resolve_report_period(
            'Current month overview', date(2025, 1, 1), self.dataset,
            today=date(2026, 7, 15),
        )
        self.assertEqual(period.start_date, date(2026, 7, 1))
        self.assertEqual(period.end_date, date(2026, 7, 14))

    def test_filtered_report_uses_active_filter_range(self) -> None:
        self.assertIn('Filtered overview', REPORT_TYPES)
        period = resolve_report_period(
            'Filtered overview',
            date(2026, 7, 14),
            self.dataset,
            today=date(2026, 7, 15),
            selected_start=date(2026, 7, 13),
            selected_end=date(2026, 7, 14),
        )

        self.assertEqual(period.start_date, date(2026, 7, 13))
        self.assertEqual(period.end_date, date(2026, 7, 14))
        self.assertIn('No comparison', period.note)

    def test_pdf_report_is_generated_for_filtered_dates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = generate_pdf_report(
                self.dataset,
                'Filtered overview',
                date(2026, 7, 14),
                ALL_AREAS,
                folder,
                date(2026, 7, 13),
                date(2026, 7, 14),
            )
            content = path.read_bytes()

        self.assertGreater(len(content), 5_000)
        self.assertTrue(content.startswith(b'%PDF'))

    def test_pdf_line_chart_labels_are_positioned_above_the_line(self) -> None:
        view = build_dashboard_view(
            self.dataset,
            date(2026, 7, 13),
            date(2026, 7, 14),
            ALL_AREAS,
        )
        drawing = _daily_chart(view, ALL_AREAS)
        chart = next(
            item for item in drawing.contents
            if item.__class__.__name__ == 'HorizontalLineChart'
        )

        for index in range(2):
            self.assertGreater(chart.lineLabels[(0, index)].dy, 0)

    def test_comparison_chart_labels_do_not_share_the_same_x_position(self) -> None:
        view = build_dashboard_view(
            self.dataset,
            date(2026, 7, 13),
            date(2026, 7, 14),
            ALL_AREAS,
            comparison_start_date=date(2026, 1, 1),
            comparison_end_date=date(2026, 1, 2),
        )
        drawing = _daily_chart(view, ALL_AREAS, include_comparison=True)
        chart = next(
            item for item in drawing.contents
            if item.__class__.__name__ == 'HorizontalLineChart'
        )

        for current_label, comparison_label in zip(*chart.lineLabelArray):
            self.assertFalse(current_label and comparison_label)

    def test_comparison_reports_keep_recorded_change_visible_with_quality_notes(self) -> None:
        bundle = _report_bundle(
            self.dataset,
            'Usage comparison',
            date(2026, 7, 13),
            ALL_AREAS,
            date(2026, 7, 13),
            date(2026, 7, 13),
            date(2026, 1, 1),
            date(2026, 1, 1),
            'Custom ranges',
        )

        self.assertTrue(bundle.period.comparison_enabled)
        self.assertEqual(_recorded_change_percent(bundle.view), 20.0)

    def test_current_month_pdf_has_cost_projection_without_comparison_language(self) -> None:
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as folder:
            path = generate_pdf_report(
                self.dataset,
                'Current month overview',
                date(2026, 7, 14),
                ALL_AREAS,
                folder,
            )
            text = '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)

        report_text = text.casefold()
        self.assertIn('projected july 2026 cost', report_text)
        self.assertIn('cost before solar savings', report_text)
        self.assertNotIn('not comparable', report_text)
        self.assertNotIn('vs comparable', report_text)

    def test_usage_comparison_pdf_uses_exact_ranges_and_recorded_change(self) -> None:
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as folder:
            path = generate_pdf_report(
                self.dataset,
                'Usage comparison',
                date(2026, 7, 14),
                ALL_AREAS,
                folder,
                date(2026, 7, 13),
                date(2026, 7, 14),
                date(2026, 1, 1),
                date(2026, 1, 1),
                'Custom ranges',
            )
            text = ' '.join(
                '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
                .casefold()
                .split()
            )

        self.assertIn('usage comparison report', text)
        self.assertIn('recorded change', text)
        self.assertIn('custom ranges', text)
        self.assertIn('average daily cost movement', text)
        self.assertIn('different numbers of available days', text)
        self.assertNotIn('not comparable', text)

    def test_report_splits_solar_warnings_and_positive_events(self) -> None:
        solar_day = daily_row('2026-07-14', SOLAR_AREA, 160)
        dataset = DashboardDataset(
            run_dir=self.dataset.run_dir,
            manifest=self.dataset.manifest,
            daily=[*self.dataset.daily, solar_day],
            weekly=[],
            spikes=[
                {
                    'date': '2026-07-14', 'area': 'Warehouse 7',
                    'alert_level': 'High', 'alert_type': 'Consumption',
                },
                {
                    'date': '2026-07-14', 'area': SOLAR_AREA,
                    'alert_level': 'High', 'alert_type': 'Low solar generation',
                },
            ],
            intervals=[],
            areas=('Warehouse 6', 'Warehouse 7', SOLAR_AREA),
            first_date=self.dataset.first_date,
            last_date=self.dataset.last_date,
            solar_positive_events=[{
                'event_id': '9987-2026-07-13-solar-high',
                'date': '2026-07-13',
                'area': SOLAR_AREA,
            }],
        )

        bundle = _report_bundle(
            dataset,
            'Filtered overview',
            date(2026, 7, 14),
            ALL_AREAS,
            date(2026, 7, 13),
            date(2026, 7, 14),
        )

        self.assertEqual(len(bundle.unusual_rows), 1)
        self.assertEqual(bundle.unusual_rows[0]['area'], 'Warehouse 7')
        self.assertEqual(len(bundle.solar_low_rows), 1)
        self.assertEqual(len(bundle.solar_positive_rows), 1)

    def test_csv_export_obeys_date_and_area_filters(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = generate_csv_export(
                self.dataset,
                date(2026, 7, 14),
                date(2026, 7, 14),
                'Warehouse 7',
                output_dir=folder,
            )
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['date'], '2026-07-14')
        self.assertEqual(rows[0]['area'], 'Warehouse 7')
        self.assertEqual(rows[0]['import_kwh'], '80')

    def test_xlsx_export_is_self_contained_and_contains_management_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = generate_xlsx_export(
                self.dataset,
                date(2026, 7, 13),
                date(2026, 7, 14),
                ALL_AREAS,
                output_dir=folder,
            )
            self.assertTrue(zipfile.is_zipfile(path))
            with zipfile.ZipFile(path) as archive:
                workbook_xml = archive.read('xl/workbook.xml').decode('utf-8')

        self.assertIn('Summary', workbook_xml)
        self.assertIn('Daily Usage', workbook_xml)
        self.assertIn('Unusual Usage', workbook_xml)
        self.assertIn('Solar Signals', workbook_xml)


if __name__ == '__main__':
    unittest.main()
