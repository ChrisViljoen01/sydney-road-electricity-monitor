from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from electricity_tool.export import INTERVAL_FIELDS
from electricity_tool.history_store import HistoryStore


def interval_row(timestamp: str, kw_import: float = 10.0) -> dict[str, object]:
    return {
        'date': timestamp[:10],
        'timestamp': timestamp,
        'account_name': '265 Sydney Rd WH 6',
        'account_code': 'E9623',
        'account_eid': '10005',
        'area': 'Warehouse 6',
        'period_seconds': 1800,
        'kw_import': kw_import,
        'kw_export': 0,
        'kw_net': kw_import,
        'kvar_net': 0,
        'kva': kw_import,
        'import_kwh': kw_import / 2,
        'export_kwh': 0,
        'net_kwh': kw_import / 2,
        'power_factor': 1,
        'kva_method': 'reported_vector_s',
        'raw_p1': kw_import,
        'raw_p2': 0,
        'raw_q1': 0,
        'raw_q2': 0,
        'raw_q3': 0,
        'raw_q4': 0,
        'raw_s': kw_import,
        'raw_scalar_s': kw_import,
        'source_status': 'Ok',
        'source': 'profile_graph_download_csv',
    }


class HistoryStoreTests(unittest.TestCase):
    def test_upsert_replaces_duplicate_meter_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / 'history.db')
            first = store.upsert_rows([interval_row('2026-01-01 00:30:00', 10)])
            second = store.upsert_rows([interval_row('2026-01-01 00:30:00', 12)])

            self.assertEqual((first.inserted, first.replaced), (1, 0))
            self.assertEqual((second.inserted, second.replaced), (0, 1))
            self.assertEqual(len(store.interval_rows()), 1)
            self.assertEqual(float(store.interval_rows()[0]['kw_import']), 12)
            self.assertEqual(store.latest_timestamp().isoformat(sep=' '), '2026-01-01 00:30:00')

    def test_bootstrap_imports_existing_export_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / 'exports' / '20260102_120000_000000'
            run.mkdir(parents=True)
            (run / 'run_manifest.json').write_text('{}', encoding='utf-8')
            with (run / 'interval_readings.csv').open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=INTERVAL_FIELDS)
                writer.writeheader()
                writer.writerow(interval_row('2026-01-01 00:30:00'))

            store = HistoryStore(root / 'history.db')
            first = store.bootstrap_from_exports(root / 'exports')
            second = store.bootstrap_from_exports(root / 'exports')

            self.assertEqual(first.inserted, 1)
            self.assertEqual(second.received, 0)
            self.assertEqual(store.stats()['row_count'], 1)

    def test_missing_date_ranges_detects_historic_backfill_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / 'history.db')
            rows = []
            for value in (date(2025, 1, 1), date(2025, 1, 3)):
                start = datetime.combine(value, datetime.min.time())
                rows.extend(
                    interval_row((start + timedelta(minutes=30 * index)).isoformat(sep=' '))
                    for index in range(48)
                )
            store.upsert_rows(rows)

            missing = store.missing_date_ranges(
                date(2025, 1, 1), date(2025, 1, 3), ('10005',)
            )

            self.assertEqual(missing, [(date(2025, 1, 2), date(2025, 1, 2))])

    def test_missing_ranges_ignore_dates_before_meter_had_a_complete_day(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / 'history.db')

            missing = store.missing_date_ranges(
                date(2024, 3, 18), date(2024, 3, 19), ('9987',)
            )

            self.assertEqual(missing, [(date(2024, 3, 19), date(2024, 3, 19))])


if __name__ == '__main__':
    unittest.main()
