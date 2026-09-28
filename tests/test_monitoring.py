from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
import unittest

from electricity_tool.models import Reading
from electricity_tool.monitoring import (
    daily_meter_records,
    solar_positive_register,
    spike_register,
    weekly_meter_records,
)


def daily_readings(day: str, import_kwh: float, peak_kva: float) -> list[Reading]:
    average_kw = import_kwh / 24.0
    start = datetime.fromisoformat(day + " 00:00:00")
    return [
        Reading(
            timestamp=start + timedelta(minutes=30 * offset),
            account_name="265 Sydney Rd WH 6",
            account_code="E9623",
            account_eid="10005",
            period_seconds=1800,
            kw_import=average_kw,
            kw_export=0,
            kw_net=average_kw,
            kvar_net=0,
            kva=peak_kva,
            import_kwh=import_kwh / 48.0,
            export_kwh=0,
            net_kwh=import_kwh / 48.0,
            power_factor=average_kw / peak_kva,
            kva_method="pnpscada_source",
            raw_p1=average_kw,
            raw_p2=0,
            raw_q1=0,
            raw_q2=0,
            raw_q3=0,
            raw_q4=0,
            source_status="Ok",
            area="Warehouse 6",
        )
        for offset in range(48)
    ]


def solar_readings(day: str, generation_kwh: float, peak_kw: float) -> list[Reading]:
    return [
        replace(
            row,
            account_name='265 Sydney Rd Connect Logistics (Solar)',
            account_code='35775386',
            account_eid='9987',
            area='Connect Logistics Solar',
            kva=peak_kw,
        )
        for row in daily_readings(day, generation_kwh, peak_kw)
    ]


class MonitoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            reading
            for day, used, demand in [
                ("2026-01-01", 100, 50),
                ("2026-01-08", 100, 50),
                ("2026-01-15", 100, 50),
                ("2026-01-22", 160, 60),
            ]
            for reading in daily_readings(day, used, demand)
        ]

    def test_matching_weekday_baseline_flags_consumption_and_demand(self) -> None:
        records = daily_meter_records(self.rows)
        alert = records[-1]
        self.assertEqual(alert["baseline_import_kwh"], 100)
        self.assertEqual(alert["consumption_spike"], "Yes")
        self.assertEqual(alert["demand_spike"], "Yes")
        self.assertEqual(alert["alert_level"], "Critical")
        self.assertEqual(alert["area"], "Warehouse 6")

    def test_spike_register_is_investigation_ready(self) -> None:
        records = daily_meter_records(self.rows)
        spikes = spike_register(records)
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["alert_type"], "Consumption and demand")
        self.assertEqual(spikes[0]["investigation_status"], "Open")
        self.assertEqual(spikes[0]["confirmed_cause"], "")

    def test_weekly_record_compares_same_elapsed_days(self) -> None:
        daily = daily_meter_records(self.rows)
        weekly = weekly_meter_records(self.rows, daily)
        latest = weekly[-1]
        self.assertEqual(latest["previous_week_comparable_import_kwh"], 100)
        self.assertEqual(latest["week_on_week_change_percent"], 60)
        self.assertEqual(latest["total_alert_days"], 1)

    def test_higher_solar_generation_is_positive_not_an_incident(self) -> None:
        rows = [
            reading
            for day, generation in [
                ('2026-01-01', 200),
                ('2026-01-08', 200),
                ('2026-01-15', 200),
                ('2026-01-22', 320),
            ]
            for reading in solar_readings(day, generation, 50)
        ]
        records = daily_meter_records(rows)

        self.assertEqual(records[-1]['alert_level'], 'Positive')
        self.assertEqual(records[-1]['solar_signal'], 'Higher generation')
        self.assertEqual(spike_register(records), [])
        self.assertEqual(len(solar_positive_register(records)), 1)

    def test_drastically_lower_solar_generation_is_a_warning(self) -> None:
        rows = [
            reading
            for day, generation in [
                ('2026-01-01', 200),
                ('2026-01-08', 200),
                ('2026-01-15', 200),
                ('2026-01-22', 100),
            ]
            for reading in solar_readings(day, generation, 50)
        ]
        records = daily_meter_records(rows)
        warnings = spike_register(records)

        self.assertEqual(records[-1]['alert_level'], 'High')
        self.assertEqual(records[-1]['solar_signal'], 'Low generation')
        self.assertEqual(warnings[0]['alert_type'], 'Low solar generation')

    def test_current_day_is_marked_in_progress(self) -> None:
        current = daily_meter_records([
            *daily_readings(date.today().isoformat(), 100, 50)
        ])[0]
        self.assertEqual(current['data_quality_status'], 'In progress')

    def test_calc_placeholders_make_day_incomplete(self) -> None:
        rows = daily_readings('2026-07-12', 1, 1)
        corrupted = [
            row if index < 2 else replace(
                row,
                source_status='Calc',
                kw_import=0,
                kw_net=0,
                kva=0,
                import_kwh=0,
                net_kwh=0,
            )
            for index, row in enumerate(rows)
        ]
        record = daily_meter_records(corrupted)[0]
        self.assertEqual(record['data_quality_status'], 'Incomplete')
        self.assertEqual(record['ok_samples'], 2)
        self.assertEqual(record['calc_samples'], 46)
        self.assertEqual(record['alert_level'], 'Not assessed')


if __name__ == "__main__":
    unittest.main()
