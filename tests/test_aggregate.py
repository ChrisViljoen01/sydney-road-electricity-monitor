from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import unittest

from electricity_tool.aggregate import daily_summaries, month_to_date_summaries, year_to_date_summaries
from electricity_tool.models import Reading


def reading(timestamp: str, kw: float, kva: float) -> Reading:
    return Reading(
        timestamp=datetime.fromisoformat(timestamp),
        account_name="WH 6",
        account_code="E9623",
        account_eid="10005",
        period_seconds=1800,
        kw_import=kw,
        kw_export=0,
        kw_net=kw,
        kvar_net=0,
        kva=kva,
        import_kwh=kw * 0.5,
        export_kwh=0,
        net_kwh=kw * 0.5,
        power_factor=kw / kva if kva else None,
        kva_method="pnpscada_source",
        raw_p1=kw,
        raw_p2=0,
        raw_q1=0,
        raw_q2=0,
        raw_q3=0,
        raw_q4=0,
    )


class AggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            reading("2026-07-01 00:30:00", 10, 12),
            reading("2026-07-01 01:00:00", 20, 25),
            reading("2026-07-02 00:30:00", 30, 35),
        ]

    def test_daily_summary_tracks_fluctuation(self) -> None:
        summaries = daily_summaries(self.rows)
        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[0]["import_kwh"], 15)
        self.assertEqual(summaries[0]["average_kw"], 15)
        self.assertEqual(summaries[0]["peak_kw"], 20)
        self.assertEqual(summaries[0]["kw_range"], 10)

    def test_mtd_and_ytd(self) -> None:
        end = datetime(2026, 7, 2).date()
        self.assertEqual(month_to_date_summaries(self.rows, end)[0]["import_kwh"], 30)
        self.assertEqual(year_to_date_summaries(self.rows, end)[0]["peak_kva"], 35)
if __name__ == "__main__":
    unittest.main()
