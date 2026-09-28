"""Scheduled previous-day peak-usage email for a Windows GitHub Actions runner."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os
from statistics import fmean

from .ai_hub import MicrosoftCopilotAuth
from .config import (
    DEFAULT_BASE_URL,
    DEFAULT_EXPORT_DIR,
    DEFAULT_MAIL_AGENT_AUDIT,
    DEFAULT_MAIL_AGENT_CONFIG,
    DEFAULT_TARIFF_CACHE,
    HISTORY_START_DATE,
    load_accounts,
)
from .credentials import CredentialError, WindowsCredentialStore
from .dashboard_data import ALL_AREAS, DashboardDataset, DashboardRepository
from .mail_agent import (
    GraphMailClient,
    MailAgentError,
    MailAgentStore,
    audit_entry,
    compose_daily_peak_usage_email,
    parse_recipient_text,
)
from .service import run_extraction
from .sync_planning import plan_sync_ranges
from .tariffs import (
    AREA_TARIFF_ASSIGNMENTS,
    CtouTariffRate,
    TariffRepository,
    build_cost_analysis,
    ctou_tariff_for_day,
    tou_band,
)


class DailyPeakAlertError(RuntimeError):
    """Raised when a daily peak email cannot be sent safely."""


@dataclass(frozen=True)
class DailyPeakAlertResult:
    alert_date: date
    status: str
    message: str


PEAK_INCREASE_RATIO = 1.10
PEAK_MIN_INCREASE_KWH = 5.0
PEAK_BASELINE_WEEKS = 4
PEAK_MIN_BASELINE_DAYS = 3


def has_ctou_peak_band(value: date) -> bool:
    """Return whether the municipal schedule contains any Peak interval that day."""
    midnight = datetime.combine(value, datetime.min.time())
    return any(
        tou_band(midnight + timedelta(minutes=30 * offset)) == 'peak'
        for offset in range(48)
    )


def latest_ctou_peak_date(value: date) -> date:
    """Find the latest date on or before value with an applicable Peak band."""
    candidate = value
    for _offset in range(8):
        if has_ctou_peak_band(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise DailyPeakAlertError(
        f'No applicable CTOU Peak day was found on or before {value.isoformat()}.'
    )


def _group_peak_intervals(
    intervals: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Combine consecutive half-hours into the municipality's Peak windows."""
    chronological = sorted(intervals, key=lambda row: str(row.get('start_time') or ''))
    windows: list[dict[str, object]] = []
    for interval in chronological:
        start_time = str(interval.get('start_time') or '')
        end_time = str(interval.get('end_time') or '')
        if windows and str(windows[-1]['end_time']) == start_time:
            window = windows[-1]
            window['end_time'] = end_time
            window['kwh'] = float(window['kwh']) + float(interval.get('kwh') or 0.0)
            window['energy_charge_inc_vat'] = (
                float(window['energy_charge_inc_vat'])
                + float(interval.get('energy_charge_inc_vat') or 0.0)
            )
            window['half_hours'] = int(window['half_hours']) + 1
        else:
            windows.append({
                'start_time': start_time,
                'end_time': end_time,
                'kwh': float(interval.get('kwh') or 0.0),
                'energy_charge_inc_vat': float(
                    interval.get('energy_charge_inc_vat') or 0.0
                ),
                'half_hours': 1,
            })
    for window in windows:
        hours = int(window['half_hours']) * 0.5
        window['average_kw'] = float(window['kwh']) / hours if hours else 0.0
        window['rate_inc_vat'] = (
            float(window['energy_charge_inc_vat']) / float(window['kwh'])
            if float(window['kwh']) else 0.0
        )
    return sorted(windows, key=lambda row: float(row['kwh']), reverse=True)


def build_daily_ctou_analysis(
    dataset: DashboardDataset,
    alert_date: date,
    tariff: CtouTariffRate,
) -> list[dict[str, object]]:
    """Measure CTOU Peak-band energy against prior matching complete weekdays."""
    ctou_areas = {
        area for area, assignment in AREA_TARIFF_ASSIGNMENTS.items()
        if assignment == 'CTOU'
    }
    complete_dates: dict[str, set[date]] = {area: set() for area in ctou_areas}
    for row in dataset.daily:
        area = str(row.get('area') or '')
        if area in ctou_areas and str(row.get('data_quality_status') or '') == 'Complete':
            complete_dates[area].add(date.fromisoformat(str(row['date'])[:10]))

    required_dates: set[date] = {alert_date}
    baseline_dates: dict[str, list[date]] = {}
    for area in ctou_areas:
        candidates = sorted(
            value for value in complete_dates[area]
            if value < alert_date
            and value.weekday() == alert_date.weekday()
            and (alert_date - value).days <= 35
        )[-PEAK_BASELINE_WEEKS:]
        baseline_dates[area] = candidates
        required_dates.update(candidates)

    usage: dict[tuple[str, date], dict[str, object]] = {}
    for row in dataset.intervals:
        area = str(row.get('area') or '')
        if area not in ctou_areas:
            continue
        row_date = date.fromisoformat(str(row.get('date') or '')[:10])
        if row_date not in required_dates:
            continue
        try:
            interval_end = datetime.fromisoformat(str(row.get('timestamp') or ''))
            kwh = float(row.get('import_kwh') or 0.0)
            kw = float(row.get('kw_import') or 0.0)
        except (TypeError, ValueError):
            continue
        band = tou_band(interval_end - timedelta(minutes=30))
        totals = usage.setdefault(
            (area, row_date),
            {
                'peak_kwh': 0.0,
                'standard_kwh': 0.0,
                'off_peak_kwh': 0.0,
                'highest_peak_kw': 0.0,
                'highest_peak_time': '',
                'peak_intervals': [],
            },
        )
        totals[f'{band}_kwh'] = float(totals[f'{band}_kwh']) + kwh
        if band == 'peak':
            interval_start = interval_end - timedelta(minutes=30)
            peak_intervals = totals['peak_intervals']
            if isinstance(peak_intervals, list):
                peak_intervals.append({
                    'start_time': interval_start.strftime('%H:%M'),
                    'end_time': interval_end.strftime('%H:%M'),
                    'kwh': kwh,
                    'average_kw': kw,
                    'energy_charge_inc_vat': kwh * tariff.energy_rate_inc_vat(
                        interval_start.date(),
                        'peak',
                    ),
                })
        if band == 'peak' and kw > float(totals['highest_peak_kw']):
            totals['highest_peak_kw'] = kw
            interval_start = interval_end - timedelta(minutes=30)
            totals['highest_peak_time'] = (
                f'{interval_start:%H:%M}-{interval_end:%H:%M}'
            )

    peak_rate = tariff.energy_rate_inc_vat(alert_date, 'peak')
    off_peak_rate = tariff.energy_rate_inc_vat(alert_date, 'off_peak')
    output: list[dict[str, object]] = []
    for area in sorted(ctou_areas):
        current = usage.get((area, alert_date), {})
        peak_kwh = float(current.get('peak_kwh') or 0.0)
        standard_kwh = float(current.get('standard_kwh') or 0.0)
        off_peak_kwh = float(current.get('off_peak_kwh') or 0.0)
        total_kwh = peak_kwh + standard_kwh + off_peak_kwh
        comparable = [
            float(usage[(area, value)]['peak_kwh'])
            for value in baseline_dates[area]
            if (area, value) in usage
        ]
        assessed = len(comparable) >= PEAK_MIN_BASELINE_DAYS
        baseline = fmean(comparable) if assessed else None
        increase_kwh = peak_kwh - baseline if baseline is not None else None
        increase_percent = (
            increase_kwh / baseline * 100.0
            if baseline is not None and baseline > 0 and increase_kwh is not None
            else None
        )
        action_required = bool(
            baseline is not None
            and baseline > 0
            and peak_kwh >= baseline * PEAK_INCREASE_RATIO
            and increase_kwh is not None
            and increase_kwh >= PEAK_MIN_INCREASE_KWH
        )
        shiftable_excess = max(0.0, increase_kwh or 0.0) if action_required else 0.0
        peak_intervals = sorted(
            (
                dict(item) for item in current.get('peak_intervals', [])
                if isinstance(item, dict)
            ),
            key=lambda item: float(item.get('kwh') or 0.0),
            reverse=True,
        )
        output.append({
            'area': area,
            'peak_kwh': peak_kwh,
            'peak_share_percent': peak_kwh / total_kwh * 100.0 if total_kwh else 0.0,
            'peak_rate_inc_vat': peak_rate,
            'peak_energy_charge_inc_vat': peak_kwh * peak_rate,
            'highest_peak_kw': float(current.get('highest_peak_kw') or 0.0),
            'highest_peak_time': str(current.get('highest_peak_time') or 'No Peak interval'),
            'peak_intervals': peak_intervals,
            'peak_windows': _group_peak_intervals(peak_intervals),
            'baseline_peak_kwh': baseline,
            'increase_kwh': increase_kwh,
            'increase_percent': increase_percent,
            'baseline_days': len(comparable),
            'action_required': action_required,
            'shift_opportunity_kwh': shiftable_excess,
            'estimated_off_peak_saving': shiftable_excess * max(0.0, peak_rate - off_peak_rate),
        })
    return sorted(output, key=lambda row: float(row['peak_kwh']), reverse=True)


def build_monthly_surcharge_analysis(
    dataset: DashboardDataset,
    through_date: date,
    tariff: CtouTariffRate,
) -> list[dict[str, object]]:
    """Estimate current-month demand threshold and surcharge exposure from PNPSCADA."""
    month_start = through_date.replace(day=1)
    ctou_areas = {
        area for area, assignment in AREA_TARIFF_ASSIGNMENTS.items()
        if assignment == 'CTOU'
    }
    complete_dates: dict[str, set[date]] = {area: set() for area in ctou_areas}
    for row in dataset.daily:
        area = str(row.get('area') or '')
        row_date = date.fromisoformat(str(row.get('date') or '')[:10])
        if (
            area in ctou_areas
            and month_start <= row_date <= through_date
            and str(row.get('data_quality_status') or '') == 'Complete'
        ):
            complete_dates[area].add(row_date)

    totals = {
        area: {
            'energy_charge_ex_vat': 0.0,
            'peak_kwh': 0.0,
            'standard_kwh': 0.0,
            'off_peak_kwh': 0.0,
            'maximum_kva': 0.0,
            'maximum_kva_time': '',
        }
        for area in ctou_areas
    }
    for row in dataset.intervals:
        area = str(row.get('area') or '')
        if area not in ctou_areas:
            continue
        row_date = date.fromisoformat(str(row.get('date') or '')[:10])
        if row_date not in complete_dates[area]:
            continue
        try:
            interval_end = datetime.fromisoformat(str(row.get('timestamp') or ''))
            kwh = float(row.get('import_kwh') or 0.0)
            kva = float(row.get('kva') or 0.0)
        except (TypeError, ValueError):
            continue
        effective_time = interval_end - timedelta(minutes=30)
        band = tou_band(effective_time)
        current = totals[area]
        current[f'{band}_kwh'] = float(current[f'{band}_kwh']) + kwh
        current['energy_charge_ex_vat'] = (
            float(current['energy_charge_ex_vat'])
            + kwh * tariff.energy_rate_ex_vat(effective_time.date(), band)
        )
        if band != 'off_peak' and kva > float(current['maximum_kva']):
            current['maximum_kva'] = kva
            current['maximum_kva_time'] = interval_end.strftime('%d %b %Y %H:%M')

    output: list[dict[str, object]] = []
    for area in sorted(ctou_areas):
        current = totals[area]
        maximum_kva = float(current['maximum_kva'])
        threshold = tariff.surcharge_threshold_kva
        demand_charge_ex_vat = (
            max(maximum_kva, tariff.minimum_demand_kva)
            * tariff.demand_charge_r_per_kva
            if complete_dates[area] else 0.0
        )
        surcharge_active = maximum_kva >= threshold
        potential_demand_charge_ex_vat = (
            max(maximum_kva, threshold)
            * tariff.demand_charge_r_per_kva
            if complete_dates[area] else 0.0
        )
        surcharge_ex_vat = (
            (
                float(current['energy_charge_ex_vat'])
                + (
                    demand_charge_ex_vat
                    if surcharge_active else potential_demand_charge_ex_vat
                )
            )
            * tariff.network_surcharge_percent
            / 100.0
            if complete_dates[area] else 0.0
        )
        output.append({
            'area': area,
            'through_date': through_date.isoformat(),
            'complete_days': len(complete_dates[area]),
            'maximum_kva': maximum_kva,
            'maximum_kva_time': str(current['maximum_kva_time']),
            'threshold_kva': threshold,
            'threshold_variance_kva': maximum_kva - threshold,
            'surcharge_active': surcharge_active,
            'peak_kwh': float(current['peak_kwh']),
            'peak_energy_charge_inc_vat': (
                float(current['peak_kwh'])
                * tariff.energy_rate_inc_vat(through_date, 'peak')
            ),
            'energy_charge_ex_vat': float(current['energy_charge_ex_vat']),
            'demand_charge_ex_vat': demand_charge_ex_vat,
            'estimated_surcharge_inc_vat': surcharge_ex_vat * (1.0 + tariff.vat_rate),
            'surcharge_percent': tariff.network_surcharge_percent,
        })
    return sorted(output, key=lambda row: float(row['maximum_kva']), reverse=True)


def build_monthly_cost_outlook(
    dataset: DashboardDataset,
    through_date: date,
    tariff_repository: TariffRepository,
) -> dict[str, object]:
    """Return the existing Cost Centre's live month-to-date and month-end outlook."""
    month_start = through_date.replace(day=1)
    span_days = (through_date - month_start).days + 1
    comparison_end = month_start - timedelta(days=1)
    comparison_start = comparison_end - timedelta(days=span_days - 1)
    analysis = build_cost_analysis(
        dataset,
        month_start,
        through_date,
        ALL_AREAS,
        tariff_repository.rates(),
        comparison_start,
        comparison_end,
        tariff_repository.ctou_rates(),
    )
    metrics = analysis.metrics
    return {
        'month': str(metrics.get('forecast_month') or through_date.strftime('%B %Y')),
        'through_date': str(metrics.get('forecast_through_date') or through_date.isoformat()),
        'complete_days': int(metrics.get('forecast_elapsed_days') or 0),
        'estimated_bill_to_date': float(metrics.get('forecast_cost_to_date') or 0.0),
        'projected_month_end_bill': float(metrics.get('forecast_month_end_cost') or 0.0),
        'projected_month_end_kwh': float(metrics.get('forecast_month_end_kwh') or 0.0),
        'projected_network_surcharge': float(
            metrics.get('forecast_month_end_network_surcharge') or 0.0
        ),
        'solar_savings_to_date': float(metrics.get('solar_avoided_cost') or 0.0),
    }


def require_ctou_baselines(rows: list[dict[str, object]]) -> None:
    """Prevent emails that imply a comparison when the baseline is unavailable."""
    unavailable = sorted(
        str(row.get('area') or 'Unknown warehouse')
        for row in rows
        if row.get('baseline_peak_kwh') is None
        or int(row.get('baseline_days') or 0) < PEAK_MIN_BASELINE_DAYS
    )
    if unavailable:
        raise DailyPeakAlertError(
            'The CTOU matching-weekday baseline is not ready for '
            f'{", ".join(unavailable)}. At least {PEAK_MIN_BASELINE_DAYS} complete comparable '
            'days are required. No email was sent.'
        )


def complete_daily_records(
    dataset: DashboardDataset,
    alert_date: date,
    expected_areas: set[str],
) -> list[dict[str, object]]:
    """Require a complete prior-day record for every configured meter area."""
    records = [
        dict(row)
        for row in dataset.daily
        if str(row.get('date') or '') == alert_date.isoformat()
    ]
    by_area = {str(row.get('area') or ''): row for row in records}
    missing = sorted(expected_areas - set(by_area))
    incomplete = sorted(
        area for area, row in by_area.items()
        if area in expected_areas and str(row.get('data_quality_status') or '') != 'Complete'
    )
    if missing or incomplete:
        details: list[str] = []
        if missing:
            details.append(f'missing areas: {", ".join(missing)}')
        if incomplete:
            details.append(f'incomplete areas: {", ".join(incomplete)}')
        raise DailyPeakAlertError(
            f'Previous-day data for {alert_date.isoformat()} is not ready ({("; ".join(details))}). '
            'No email was sent.'
        )
    return [by_area[area] for area in sorted(expected_areas)]


def _portal_credentials() -> tuple[str, str]:
    try:
        saved = WindowsCredentialStore().read()
    except CredentialError as exc:
        raise DailyPeakAlertError(f'PNPSCADA credentials could not be read: {exc}') from exc
    if saved is not None:
        return saved
    username = os.getenv('PNPSCADA_USERNAME', '').strip()
    password = os.getenv('PNPSCADA_PASSWORD', '')
    if username and password:
        return username, password
    raise DailyPeakAlertError(
        'No PNPSCADA login is available. Save it in Windows Credential Manager for the runner '
        'account or provide PNPSCADA_USERNAME and PNPSCADA_PASSWORD as GitHub Actions secrets.'
    )


def run_daily_peak_alert(
    *,
    recipients: tuple[str, ...],
    today: date | None = None,
    repository: DashboardRepository | None = None,
    test_mode: bool = False,
) -> DailyPeakAlertResult:
    """Refresh recent readings and send one email for the latest closed calendar day."""
    if not recipients:
        raise DailyPeakAlertError('At least one daily peak-email recipient is required.')
    run_date = today or date.today()
    alert_date = run_date - timedelta(days=1)
    peak_analysis_date = latest_ctou_peak_date(alert_date)
    if alert_date < date.fromisoformat(HISTORY_START_DATE):
        raise DailyPeakAlertError('The requested previous day predates the supported meter history.')
    data = repository or DashboardRepository()
    if not test_mode and data.history_store.daily_peak_alert_sent(alert_date):
        return DailyPeakAlertResult(
            alert_date,
            'already-sent',
            f'Daily peak email for {alert_date.isoformat()} was already sent.',
        )

    username, password = _portal_credentials()
    history_start = date.fromisoformat(HISTORY_START_DATE)
    baseline_start = max(history_start, peak_analysis_date - timedelta(days=35))
    accounts = load_accounts()
    missing_ranges = data.missing_date_ranges(
        baseline_start,
        alert_date,
        (account.eid for account in accounts),
    )
    extraction_windows = plan_sync_ranges(
        baseline_start,
        run_date,
        missing_ranges,
    )
    dataset = data.get()
    for refresh_start, refresh_end in extraction_windows:
        run_dir = run_extraction(
            DEFAULT_BASE_URL,
            username,
            password,
            refresh_start,
            refresh_end,
            DEFAULT_EXPORT_DIR,
            progress=print,
        )
        dataset, _write = data.ingest_run(run_dir, refresh_start, refresh_end)
    expected_areas = {account.area for account in accounts}
    records = complete_daily_records(dataset, alert_date, expected_areas)
    if peak_analysis_date != alert_date:
        complete_daily_records(dataset, peak_analysis_date, expected_areas)

    tariff_repository = TariffRepository(DEFAULT_TARIFF_CACHE)
    tariff_check = tariff_repository.check_for_updates(force=True, today=run_date)
    ctou_rate = ctou_tariff_for_day(tariff_repository.ctou_rates(), alert_date)
    if ctou_rate is None:
        raise DailyPeakAlertError(
            f'No verified CTOU tariff is available for {alert_date.isoformat()}. No email was sent.'
        )
    ctou_analysis = build_daily_ctou_analysis(dataset, peak_analysis_date, ctou_rate)
    require_ctou_baselines(ctou_analysis)
    monthly_surcharge_analysis = build_monthly_surcharge_analysis(
        dataset,
        alert_date,
        ctou_rate,
    )
    monthly_cost_outlook = build_monthly_cost_outlook(
        dataset,
        alert_date,
        tariff_repository,
    )

    access_token = MicrosoftCopilotAuth().access_token()
    if not access_token:
        raise DailyPeakAlertError(
            'The Microsoft mail session is unavailable for the runner account. Sign in to the '
            'desktop app as that account and confirm that the Mail.Send permission is granted.'
        )
    draft = compose_daily_peak_usage_email(
        alert_date=alert_date,
        peak_analysis_date=peak_analysis_date,
        ctou_analysis=ctou_analysis,
        monthly_surcharge_analysis=monthly_surcharge_analysis,
        monthly_cost_outlook=monthly_cost_outlook,
        recipients=recipients,
        tariff_status=tariff_check.status,
        tariff_checked_at=tariff_check.checked_at,
        tariff_message=tariff_check.message,
        tariff_source_url=tariff_check.source_url or ctou_rate.source_url,
        ctou_rates_inc_vat={
            band: ctou_rate.energy_rate_inc_vat(peak_analysis_date, band)
            for band in ('peak', 'standard', 'off_peak')
        },
        test_mode=test_mode,
    )
    mail_result = GraphMailClient().send_mail(access_token, draft)
    store = MailAgentStore(DEFAULT_MAIL_AGENT_CONFIG, DEFAULT_MAIL_AGENT_AUDIT)
    store.record(audit_entry(draft, mail_result))
    if not test_mode:
        data.history_store.record_daily_peak_alert_sent(alert_date, datetime.now().astimezone())
    return DailyPeakAlertResult(
        alert_date,
        'test-sent' if test_mode else 'sent',
        f'{"Test " if test_mode else ""}daily peak email for {alert_date.isoformat()} '
        f'sent to {", ".join(recipients)}.',
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Refresh PNPSCADA data and email verified previous-day peak usage.'
    )
    parser.add_argument(
        '--recipients',
        default=os.getenv('CONNECT_DAILY_PEAK_RECIPIENTS', ''),
        help='Semicolon- or comma-separated email recipients; defaults to CONNECT_DAILY_PEAK_RECIPIENTS.',
    )
    parser.add_argument(
        '--date',
        type=date.fromisoformat,
        help='Runner date in YYYY-MM-DD; intended for controlled recovery runs.',
    )
    parser.add_argument(
        '--test-mode',
        action='store_true',
        help='Send a clearly labelled test email without changing the production sent marker.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_daily_peak_alert(
            recipients=parse_recipient_text(args.recipients),
            today=args.date,
            test_mode=args.test_mode,
        )
    except (DailyPeakAlertError, MailAgentError, ValueError) as exc:
        print(f'Daily peak email failed: {exc}')
        return 1
    print(result.message)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
