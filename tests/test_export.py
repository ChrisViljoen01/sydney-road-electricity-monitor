from __future__ import annotations

import csv
from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import unittest

from electricity_tool.export import export_run
from electricity_tool.models import MeterAccount, Reading


class ExportTests(unittest.TestCase):
    def test_writes_all_reporting_levels_and_redacts_session_token(self) -> None:
        account = MeterAccount("WH 6", "E9623", "10005")
        row = Reading(
            timestamp=datetime(2026, 7, 1, 0, 30),
            account_name=account.name,
            account_code=account.code,
            account_eid=account.eid,
            period_seconds=1800,
            kw_import=20,
            kw_export=0,
            kw_net=20,
            kvar_net=5,
            kva=20.6155,
            import_kwh=10,
            export_kwh=0,
            net_kwh=10,
            power_factor=0.97,
            kva_method="calculated_from_p_q",
            raw_p1=20,
            raw_p2=0,
            raw_q1=5,
            raw_q2=0,
            raw_q3=0,
            raw_q4=0,
        )
        with tempfile.TemporaryDirectory() as folder:
            output = export_run(
                folder,
                [row],
                [account],
                date(2026, 1, 1),
                date(2026, 7, 1),
                {account.eid: "<xml><result>SUCCESS</result></xml>"},
                '<a href="x?memh=-123">report</a>',
                [[["Meter", "kWh"], ["WH 6", "10"]]],
                [],
            )
            expected = {
                "interval_readings.csv",
                "daily_summary.csv",
                "daily_meter_record.csv",
                "weekly_summary.csv",
                "weekly_meter_record.csv",
                "spike_register.csv",
                "monthly_summary.csv",
                "month_to_date_summary.csv",
                "year_to_date_summary.csv",
                "portal_daily_report.csv",
                "run_manifest.json",
            }
            self.assertTrue(expected.issubset({path.name for path in output.iterdir()}))
            with (output / "daily_summary.csv").open(encoding="utf-8-sig") as handle:
                daily = list(csv.DictReader(handle))
            self.assertEqual(daily[0]["peak_kw"], "20")
            self.assertEqual(daily[0]["data_start"], "2026-07-01")
            html = (output / "raw" / "per_day_report.html").read_text(encoding="utf-8")
            self.assertNotIn("-123", html)
            self.assertIn("[REDACTED]", html)
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["row_counts"]["interval_readings.csv"], 1)
            self.assertEqual(manifest["row_counts"]["daily_meter_record.csv"], 1)
            self.assertEqual(manifest["row_counts"]["spike_register.csv"], 0)


if __name__ == "__main__":
    unittest.main()
