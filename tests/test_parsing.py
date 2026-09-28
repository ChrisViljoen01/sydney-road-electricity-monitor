from __future__ import annotations

import math
from datetime import date
import unittest

from electricity_tool.models import MeterAccount
from electricity_tool.parsing import (
    parse_account_profile,
    parse_profile_graph_csv,
    parse_report_tables,
)


PROFILE_XML = """<?xml version="1.0"?>
<xml>
  <result>SUCCESS</result>
  <meter_account>
    <id>35775386</id>
    <meter><serial>SOLAR1</serial><period>1800</period></meter>
    <profile>
      <sample>
        <date>2026-07-01 00:30:00</date>
        <P1>80</P1><P2>5</P2><Q1>20</Q1><Q2>0</Q2><Q3>0</Q3><Q4>0</Q4>
      </sample>
      <sample>
        <date>2026-07-01 01:00:00</date>
        <P1>100</P1><P2>0</P2><Q1>0</Q1><Q2>0</Q2><Q3>0</Q3><Q4>0</Q4><S>110</S>
      </sample>
    </profile>
  </meter_account>
</xml>"""


class ProfileParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = MeterAccount("Solar", "35775386", "9987")

    def test_parses_power_and_energy(self) -> None:
        readings = parse_account_profile(PROFILE_XML, self.account)
        self.assertEqual(len(readings), 2)
        first = readings[0]
        self.assertEqual(first.period_seconds, 1800)
        self.assertEqual(first.kw_net, 75)
        self.assertEqual(first.import_kwh, 40)
        self.assertAlmostEqual(first.kva, math.sqrt(75**2 + 20**2))
        self.assertEqual(first.kva_method, "calculated_from_p_q")
        self.assertEqual(readings[1].kva, 110)
        self.assertEqual(readings[1].kva_method, "pnpscada_source")

    def test_parses_html_report_tables(self) -> None:
        html = "<html><table><tr><th>Meter</th><th>kWh</th></tr><tr><td>WH 6</td><td>123.4</td></tr></table></html>"
        tables = parse_report_tables(html)
        self.assertEqual(tables, [[["Meter", "kWh"], ["WH 6", "123.4"]]])

    def test_parses_profile_graph_download_csv(self) -> None:
        text = '''"pnpscada.com", "28113962", "Africa/Johannesburg"
"P1 (per kW)", "Q1 (per kvar)", "P2 (per kW)", "S (per kVA)", "scalar sum S (per kVA)", "DATE", "TIME", "STATUS"
80, 20, 5, 77.62, 85.0, 2026-07-01, 00:30:00, Ok
100, 0, 0, 110, 110, 2026-07-01, 01:00:00, Calc
'''
        readings = parse_profile_graph_csv(text, self.account)
        self.assertEqual(len(readings), 2)
        self.assertEqual(readings[0].period_seconds, 1800)
        self.assertEqual(readings[0].kw_net, 75)
        self.assertEqual(readings[0].import_kwh, 40)
        self.assertEqual(readings[0].kva, 77.62)
        self.assertEqual(readings[0].raw_scalar_s, 85)
        self.assertEqual(readings[1].source_status, "Calc")
        self.assertEqual(readings[1].source, "Profile Graph Download CSV")

    def test_filters_profile_graph_boundary_prefetch(self) -> None:
        text = '''"P1 (per kW)", "S (per kVA)", "DATE", "TIME", "STATUS"
1, 1, 2025-12-31, 23:30:00, Ok
2, 2, 2026-01-01, 00:00:00, Ok
3, 3, 2026-01-01, 00:30:00, Ok
'''
        readings = parse_profile_graph_csv(
            text, self.account, start_date=date(2026, 1, 1),
            end_date_inclusive=date(2026, 1, 1)
        )
        self.assertEqual([row.timestamp.strftime("%H:%M") for row in readings], ["00:00", "00:30"])


if __name__ == "__main__":
    unittest.main()
