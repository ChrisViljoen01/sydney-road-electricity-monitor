from datetime import date
import unittest

from electricity_tool.sync_planning import plan_sync_ranges


class SyncPlanningTests(unittest.TestCase):
    def test_refresh_always_rechecks_three_closed_days_and_today(self) -> None:
        ranges = plan_sync_ranges(
            date(2023, 11, 9),
            date(2026, 7, 20),
            [],
        )

        self.assertEqual(ranges, [(date(2026, 7, 17), date(2026, 7, 20))])

    def test_missing_gap_expands_back_to_repair_previous_partial_day(self) -> None:
        ranges = plan_sync_ranges(
            date(2023, 11, 9),
            date(2026, 7, 20),
            [(date(2026, 7, 18), date(2026, 7, 19))],
        )

        self.assertEqual(ranges, [(date(2026, 7, 17), date(2026, 7, 20))])

    def test_large_backfill_is_merged_then_chunked(self) -> None:
        ranges = plan_sync_ranges(
            date(2026, 1, 1),
            date(2026, 7, 20),
            [(date(2026, 1, 2), date(2026, 7, 19))],
            maximum_days=92,
        )

        self.assertEqual(ranges[0], (date(2026, 1, 1), date(2026, 4, 2)))
        self.assertEqual(ranges[-1][1], date(2026, 7, 20))
        self.assertTrue(all((end - start).days < 92 for start, end in ranges))


if __name__ == '__main__':
    unittest.main()
