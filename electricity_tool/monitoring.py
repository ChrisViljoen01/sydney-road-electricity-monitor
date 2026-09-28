from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import fmean
from typing import Iterable

from .aggregate import daily_summaries, weekly_summaries
from .models import Reading


BASELINE_WEEKS = 4
MIN_BASELINE_DAYS = 3
CONSUMPTION_SPIKE_RATIO = 1.20
CONSUMPTION_MIN_INCREASE_KWH = 50.0
DEMAND_SPIKE_RATIO = 1.15
DEMAND_MIN_INCREASE_KVA = 5.0
SOLAR_AREA = "Connect Logistics Solar"
SOLAR_LOW_GENERATION_RATIO = 0.70
SOLAR_LOW_GENERATION_MIN_DROP_KWH = 30.0


def _average(rows: list[dict], field: str) -> float:
    return fmean(float(row[field]) for row in rows)


def _percent_change(current: float, baseline: float | None) -> float | str:
    if baseline is None or baseline == 0:
        return ""
    return ((current - baseline) / abs(baseline)) * 100.0


def _difference(current: float, baseline: float | None) -> float | str:
    return "" if baseline is None else current - baseline


def _iso_week(value: date) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _alert_level(
    consumption_spike: bool,
    demand_spike: bool,
    consumption_ratio: float | None,
    demand_ratio: float | None,
) -> str:
    if not (consumption_spike or demand_spike):
        return "None"
    if (consumption_spike and consumption_ratio is not None and consumption_ratio >= 1.50) or (
        demand_spike and demand_ratio is not None and demand_ratio >= 1.35
    ):
        return "Critical"
    if (consumption_spike and consumption_ratio is not None and consumption_ratio >= 1.30) or (
        demand_spike and demand_ratio is not None and demand_ratio >= 1.25
    ):
        return "High"
    return "Watch"


def _solar_low_alert_level(generation_ratio: float) -> str:
    """Classify a material solar-generation shortfall against like weekdays."""
    if generation_ratio <= 0.40:
        return "Critical"
    if generation_ratio <= 0.55:
        return "High"
    return "Watch"


def daily_meter_records(readings: Iterable[Reading]) -> list[dict]:
    summaries = daily_summaries(readings)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in summaries:
        grouped[str(row["account_eid"])].append(row)

    output: list[dict] = []
    for eid, account_rows in sorted(grouped.items()):
        history: list[dict] = []
        for row in sorted(account_rows, key=lambda item: item["period_start"]):
            current_date = date.fromisoformat(str(row["period_start"]))
            coverage_complete = (
                int(row["samples"]) >= 48 and float(row["observed_hours"]) >= 24.0
            )
            calc_samples = int(row.get("calc_samples", 0) or 0)
            other_status_samples = int(row.get("other_status_samples", 0) or 0)
            quality_status = (
                'In progress' if current_date >= date.today()
                else 'Complete'
                if coverage_complete and calc_samples == 0 and other_status_samples == 0
                else 'Estimated'
                if coverage_complete and calc_samples <= 2 and other_status_samples == 0
                else 'Incomplete'
            )
            previous = next(
                (
                    prior for prior in reversed(history)
                    if prior.get('_quality_status') == 'Complete'
                ),
                None,
            )
            candidates = [
                prior
                for prior in history
                if prior.get('_quality_status') == 'Complete'
                if date.fromisoformat(str(prior["period_start"])).weekday()
                == current_date.weekday()
                and 0
                < (current_date - date.fromisoformat(str(prior["period_start"]))).days
                <= 35
            ][-BASELINE_WEEKS:]
            assessed = quality_status == 'Complete' and len(candidates) >= MIN_BASELINE_DAYS
            baseline_import = _average(candidates, "import_kwh") if assessed else None
            baseline_peak_kva = _average(candidates, "peak_kva") if assessed else None
            import_kwh = float(row["import_kwh"])
            peak_kva = float(row["peak_kva"])
            consumption_ratio = (
                import_kwh / baseline_import if baseline_import is not None and baseline_import > 0 else None
            )
            demand_ratio = (
                peak_kva / baseline_peak_kva
                if baseline_peak_kva is not None and baseline_peak_kva > 0
                else None
            )
            consumption_spike = bool(
                assessed
                and consumption_ratio is not None
                and consumption_ratio >= CONSUMPTION_SPIKE_RATIO
                and import_kwh - float(baseline_import) >= CONSUMPTION_MIN_INCREASE_KWH
            )
            demand_spike = bool(
                assessed
                and demand_ratio is not None
                and demand_ratio >= DEMAND_SPIKE_RATIO
                and peak_kva - float(baseline_peak_kva) >= DEMAND_MIN_INCREASE_KVA
            )
            area = row.get("area") or row["account_name"]
            is_solar = area == SOLAR_AREA
            solar_low_generation = bool(
                is_solar
                and assessed
                and consumption_ratio is not None
                and consumption_ratio <= SOLAR_LOW_GENERATION_RATIO
                and float(baseline_import) - import_kwh
                >= SOLAR_LOW_GENERATION_MIN_DROP_KWH
            )
            solar_higher_generation = bool(is_solar and consumption_spike)
            if solar_low_generation:
                level = _solar_low_alert_level(float(consumption_ratio))
                solar_signal = "Low generation"
            elif solar_higher_generation:
                level = "Positive"
                solar_signal = "Higher generation"
            else:
                level = _alert_level(
                    consumption_spike, demand_spike, consumption_ratio, demand_ratio
                )
                solar_signal = ""
            reasons: list[str] = []
            if solar_low_generation:
                reasons.append(
                    f"Solar generation is {abs(_percent_change(import_kwh, baseline_import)):.1f}% "
                    "below the matching-weekday baseline"
                )
            elif solar_higher_generation:
                if consumption_spike:
                    reasons.append(
                        f"Solar generation is {_percent_change(import_kwh, baseline_import):.1f}% "
                        "above the matching-weekday baseline"
                    )
            elif consumption_spike:
                reasons.append(
                    f"Daily import is {_percent_change(import_kwh, baseline_import):.1f}% above the matching-weekday baseline"
                )
            if demand_spike and not is_solar:
                reasons.append(
                    f"Peak kVA is {_percent_change(peak_kva, baseline_peak_kva):.1f}% above the matching-weekday baseline"
                )
            previous_import = float(previous["import_kwh"]) if previous is not None else None
            comparable_dates = ";".join(str(item["period_start"]) for item in candidates)
            output.append(
                {
                    "date": current_date.isoformat(),
                    "iso_week": _iso_week(current_date),
                    "area": area,
                    "account_name": row["account_name"],
                    "account_code": row["account_code"],
                    "account_eid": eid,
                    "data_quality_status": quality_status,
                    "samples": row["samples"],
                    "observed_hours": row["observed_hours"],
                    "ok_samples": row.get("ok_samples", 0),
                    "calc_samples": calc_samples,
                    "other_status_samples": other_status_samples,
                    "import_kwh": row["import_kwh"],
                    "export_kwh": row["export_kwh"],
                    "net_kwh": row["net_kwh"],
                    "previous_day_date": previous["period_start"] if previous else "",
                    "previous_day_import_kwh": previous_import if previous_import is not None else "",
                    "day_on_day_change_kwh": _difference(import_kwh, previous_import),
                    "day_on_day_change_percent": _percent_change(import_kwh, previous_import),
                    "baseline_method": "Previous 3-4 matching weekdays",
                    "baseline_comparable_days": comparable_dates,
                    "baseline_import_kwh": baseline_import if baseline_import is not None else "",
                    "consumption_variance_kwh": _difference(import_kwh, baseline_import),
                    "consumption_variance_percent": _percent_change(import_kwh, baseline_import),
                    "average_kw": row["average_kw"],
                    "minimum_kw": row["minimum_kw"],
                    "peak_kw": row["peak_kw"],
                    "peak_kw_time": row["peak_kw_time"],
                    "average_kva": row["average_kva"],
                    "minimum_kva": row["minimum_kva"],
                    "peak_kva": row["peak_kva"],
                    "peak_kva_time": row["peak_kva_time"],
                    "baseline_peak_kva": baseline_peak_kva if baseline_peak_kva is not None else "",
                    "demand_variance_kva": _difference(peak_kva, baseline_peak_kva),
                    "demand_variance_percent": _percent_change(peak_kva, baseline_peak_kva),
                    "power_factor_at_peak_kva": row["power_factor_at_peak_kva"],
                    "consumption_spike": "Not assessed" if not assessed else ("Yes" if consumption_spike else "No"),
                    "demand_spike": "Not assessed" if not assessed else ("Yes" if demand_spike else "No"),
                    "solar_signal": solar_signal,
                    "alert_level": "Not assessed" if not assessed else level,
                    "alert_reason": "; ".join(reasons),
                    "investigation_status": (
                        "Verify" if level == "Positive"
                        else "Open" if level != "None"
                        else ""
                    ),
                    "cause_category": "",
                    "confirmed_cause": "",
                    "corrective_action": "",
                    "responsible_person": "",
                    "target_close_date": "",
                    "investigation_notes": "",
                }
            )
            history.append({**row, '_quality_status': quality_status})
    return sorted(output, key=lambda row: (row["date"], row["area"]))


def weekly_meter_records(
    readings: Iterable[Reading], daily_records: list[dict] | None = None
) -> list[dict]:
    materialized = list(readings)
    daily = daily_records if daily_records is not None else daily_meter_records(materialized)
    daily_index = {
        (str(row["account_eid"]), date.fromisoformat(str(row["date"]))): row for row in daily
    }
    output: list[dict] = []
    for row in weekly_summaries(materialized):
        eid = str(row["account_eid"])
        data_start = date.fromisoformat(str(row["data_start"]))
        data_end = date.fromisoformat(str(row["data_end"]))
        current_dates = [
            data_start + timedelta(days=offset)
            for offset in range((data_end - data_start).days + 1)
            if (eid, data_start + timedelta(days=offset)) in daily_index
        ]
        current_daily = [daily_index[(eid, value)] for value in current_dates]
        previous_daily = [
            daily_index[(eid, value - timedelta(days=7))]
            for value in current_dates
            if (eid, value - timedelta(days=7)) in daily_index
        ]
        prior_import = (
            sum(float(item["import_kwh"]) for item in previous_daily)
            if len(previous_daily) == len(current_daily) and current_daily
            else None
        )
        current_import = float(row["import_kwh"])
        alert_counts = defaultdict(int)
        for item in current_daily:
            alert_counts[str(item["alert_level"])] += 1
        week_complete = len(current_daily) == 7 and all(
            item["data_quality_status"] == "Complete" for item in current_daily
        )
        alerts = sum(alert_counts[level] for level in ("Watch", "High", "Critical"))
        output.append(
            {
                "iso_week": _iso_week(date.fromisoformat(str(row["period_start"]))),
                "week_start": row["period_start"],
                "week_end": row["period_end"],
                "data_start": row["data_start"],
                "data_end": row["data_end"],
                "week_status": "Complete" if week_complete else "Partial",
                "days_recorded": len(current_daily),
                "area": row.get("area") or row["account_name"],
                "account_name": row["account_name"],
                "account_code": row["account_code"],
                "account_eid": eid,
                "samples": row["samples"],
                "observed_hours": row["observed_hours"],
                "ok_samples": row.get("ok_samples", 0),
                "calc_samples": row.get("calc_samples", 0),
                "other_status_samples": row.get("other_status_samples", 0),
                "import_kwh": row["import_kwh"],
                "export_kwh": row["export_kwh"],
                "net_kwh": row["net_kwh"],
                "average_kw": row["average_kw"],
                "peak_kw": row["peak_kw"],
                "peak_kw_time": row["peak_kw_time"],
                "average_kva": row["average_kva"],
                "peak_kva": row["peak_kva"],
                "peak_kva_time": row["peak_kva_time"],
                "comparison_basis": (
                    f"Same {len(current_daily)} day(s) in previous week" if prior_import is not None else "Insufficient prior-week data"
                ),
                "previous_week_comparable_import_kwh": prior_import if prior_import is not None else "",
                "week_on_week_change_kwh": _difference(current_import, prior_import),
                "week_on_week_change_percent": _percent_change(current_import, prior_import),
                "watch_days": alert_counts["Watch"],
                "high_alert_days": alert_counts["High"],
                "critical_alert_days": alert_counts["Critical"],
                "total_alert_days": alerts,
                "review_status": "Review required" if alerts else "Routine review",
                "weekly_comment": "",
                "reviewed_by": "",
                "reviewed_date": "",
                "action_required": "",
            }
        )
    return sorted(output, key=lambda row: (row["week_start"], row["area"]))


def spike_register(daily_records: list[dict]) -> list[dict]:
    output: list[dict] = []
    for row in daily_records:
        if row["alert_level"] not in {"Watch", "High", "Critical"}:
            continue
        consumption = row["consumption_spike"] == "Yes"
        demand = row["demand_spike"] == "Yes"
        if row.get("area") == SOLAR_AREA:
            alert_type = "Low solar generation"
        else:
            alert_type = "Consumption and demand" if consumption and demand else (
                "Consumption" if consumption else "Demand"
            )
        output.append(
            {
                "spike_id": f"{row['account_eid']}-{row['date']}",
                "date": row["date"],
                "iso_week": row["iso_week"],
                "area": row["area"],
                "account_name": row["account_name"],
                "account_code": row["account_code"],
                "account_eid": row["account_eid"],
                "alert_level": row["alert_level"],
                "alert_type": alert_type,
                "solar_signal": row.get("solar_signal", ""),
                "alert_reason": row["alert_reason"],
                "import_kwh": row["import_kwh"],
                "baseline_import_kwh": row["baseline_import_kwh"],
                "consumption_variance_kwh": row["consumption_variance_kwh"],
                "consumption_variance_percent": row["consumption_variance_percent"],
                "peak_kw": row["peak_kw"],
                "peak_kw_time": row["peak_kw_time"],
                "peak_kva": row["peak_kva"],
                "peak_kva_time": row["peak_kva_time"],
                "baseline_peak_kva": row["baseline_peak_kva"],
                "demand_variance_kva": row["demand_variance_kva"],
                "demand_variance_percent": row["demand_variance_percent"],
                "data_quality_status": row["data_quality_status"],
                "investigation_status": "Open",
                "cause_category": "",
                "confirmed_cause": "",
                "corrective_action": "",
                "responsible_person": "",
                "target_close_date": "",
                "closed_date": "",
                "investigation_notes": "",
            }
        )
    severity = {"Critical": 0, "High": 1, "Watch": 2}
    return sorted(output, key=lambda row: (row["date"], severity[row["alert_level"]], row["area"]))


def solar_positive_register(daily_records: list[dict]) -> list[dict]:
    """Return higher-than-usual solar-output events for positive verification."""
    output: list[dict] = []
    for row in daily_records:
        if row.get("area") != SOLAR_AREA or row.get("alert_level") != "Positive":
            continue
        output.append(
            {
                "event_id": f"{row['account_eid']}-{row['date']}-solar-high",
                "date": row["date"],
                "iso_week": row["iso_week"],
                "area": row["area"],
                "account_name": row["account_name"],
                "account_code": row["account_code"],
                "account_eid": row["account_eid"],
                "signal_type": "Higher solar generation",
                "alert_reason": row["alert_reason"],
                "import_kwh": row["import_kwh"],
                "baseline_import_kwh": row["baseline_import_kwh"],
                "consumption_variance_kwh": row["consumption_variance_kwh"],
                "consumption_variance_percent": row["consumption_variance_percent"],
                "peak_kw": row["peak_kw"],
                "peak_kw_time": row["peak_kw_time"],
                "data_quality_status": row["data_quality_status"],
                "verification_status": "To verify",
            }
        )
    return sorted(output, key=lambda row: (row["date"], row["area"]), reverse=True)
