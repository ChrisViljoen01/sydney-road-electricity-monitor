from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Callable

from nicegui import app, context, ui

from .ai_hub import (
    CopilotChatClient,
    CopilotConnectionError,
    MAIL_AGENT_SCOPES,
    MicrosoftAccountStatus,
    MicrosoftCopilotAuth,
    MicrosoftDeviceSignIn,
    ai_database_scope,
    build_ai_evidence,
    is_contextual_ai_follow_up,
)

from .config import (
    AUTONOMOUS_REFRESH_RECIPIENT,
    DEFAULT_BASE_URL,
    DEFAULT_BRAND_DIR,
    DEFAULT_EXPORT_DIR,
    DEFAULT_HISTORY_DB,
    DEFAULT_MAIL_AGENT_AUDIT,
    DEFAULT_MAIL_AGENT_CONFIG,
    DEFAULT_TARIFF_CACHE,
    HISTORY_START_DATE,
    load_accounts,
)
from .credentials import CredentialError, WindowsCredentialStore
from .dashboard_charts import (
    consumption_share_options,
    cost_area_comparison_options,
    cost_component_options,
    cost_trend_options,
    daily_supply_demand_options,
    daily_energy_options,
    demand_by_area_options,
    load_profile_options,
    period_area_change_options,
    period_area_comparison_options,
    period_daily_comparison_options,
    solar_daily_generation_options,
    solar_daily_peak_options,
    solar_actual_vs_proposal_options,
    solar_output_profile_options,
    solar_period_generation_options,
    solar_period_comparison_options,
    solar_proposal_cash_flow_options,
    solar_proposal_energy_flow_options,
    solar_signal_options,
    supply_demand_profile_options,
    tou_energy_mix_options,
    unusual_by_area_options,
    unusual_frequency_summary,
    unusual_month_position_options,
    unusual_time_of_day_options,
    unusual_timeline_options,
    weekly_energy_options,
)
from .dashboard_data import (
    ALL_AREAS,
    SOLAR_AREA,
    DashboardDataset,
    DashboardRepository,
    DashboardView,
    PeriodComparison,
    SolarPerformance,
    SupplyDemandBalance,
    build_dashboard_view,
    build_period_comparison,
    build_solar_performance,
    build_supply_demand_balance,
    comparison_dates,
    comparison_period_ranges,
    first_valid_area_date,
    interval_profile,
    without_area,
)
from .dashboard_styles import apply_dashboard_styles
from .mail_agent import (
    DirectoryPerson,
    GraphMailClient,
    MailAgentError,
    MailAgentStore,
    MailDraft,
    RecipientPreferences,
    audit_entry,
    compose_alert_email,
    compose_period_summary_email,
    compose_refresh_complete_email,
    parse_copilot_email,
    parse_recipient_text,
)
from .portal import PortalClient
from .reporting import (
    REPORT_TYPES,
    generate_csv_export,
    generate_pdf_report,
    generate_xlsx_export,
    resolve_report_period,
)
from .service import run_extraction
from .sync_planning import plan_sync_ranges
from .solar_investment import (
    SOLAR_PROPOSALS,
    SolarProposal,
    current_solar_investment_metrics,
)
from .tariffs import (
    AREA_TARIFF_ASSIGNMENTS,
    CTOU_TARIFF_NAME,
    DEFAULT_TARIFF_NOTE,
    DEFAULT_TARIFF_NAME,
    CostAnalysis,
    TariffCheckResult,
    TariffRepository,
    build_cost_analysis,
    ctou_tariff_for_day,
    resolve_cost_month_comparison,
    tariff_for_day,
)


repository = DashboardRepository(DEFAULT_EXPORT_DIR, DEFAULT_HISTORY_DB)
tariff_repository = TariffRepository(DEFAULT_TARIFF_CACHE)
copilot_auth = MicrosoftCopilotAuth()
copilot_client = CopilotChatClient()
graph_mail_client = GraphMailClient()
mail_agent_store = MailAgentStore(DEFAULT_MAIL_AGENT_CONFIG, DEFAULT_MAIL_AGENT_AUDIT)
PERIOD_OPTIONS = {
    'Last 7 days': '7d',
    'Last 30 days': '30d',
    'Month to date': 'mtd',
    'Year to date': 'ytd',
    'All available data': 'all',
    'Custom dates': 'custom',
}
COST_COMPARISON_OPTIONS = (
    'Current month vs previous month',
    'Past month vs past month',
    'Previous matching period',
    'Custom date ranges',
)
NAV_ITEMS = (
    ('overview', 'Overview', 'dashboard'),
    ('trends', 'Usage Trends', 'query_stats'),
    ('supply', 'Supply & Demand', 'balance'),
    ('solar', 'Solar Performance', 'solar_power'),
    ('solar_investment', 'Solar Investment', 'savings'),
    ('comparison', 'Comparisons', 'compare_arrows'),
    ('costs', 'Cost Centre', 'payments'),
    ('alerts', 'Unusual Usage', 'notification_important'),
    ('ai', 'AI Hub', 'smart_toy'),
    ('agent', 'Agent Centre', 'mark_email_unread'),
    ('reports', 'Reports Center', 'picture_as_pdf'),
    ('data', 'Data Update', 'database'),
)
LEVEL_LABELS = {
    'Critical': 'Urgent review',
    'High': 'Review',
    'Watch': 'Monitor',
    'None': 'Normal',
    'Not assessed': 'Not enough history',
    'Stable': 'Normal',
}

YMS_LOGO_DIR = DEFAULT_BRAND_DIR
if YMS_LOGO_DIR.exists():
    app.add_static_files('/connect-brand', str(YMS_LOGO_DIR))


@dataclass
class PageState:
    start_date: date
    end_date: date
    area: str = ALL_AREAS
    current_view: str = 'overview'
    sidebar_collapsed: bool = False
    syncing: bool = False
    report_type: str = 'Current month overview'
    comparison_type: str = 'Year vs year'
    comparison_current_start: date | None = None
    comparison_current_end: date | None = None
    comparison_previous_start: date | None = None
    comparison_previous_end: date | None = None
    cost_selected_start: date | None = None
    cost_selected_end: date | None = None
    cost_comparison_start: date | None = None
    cost_comparison_end: date | None = None
    cost_comparison_type: str = 'Current month vs previous month'
    cost_selected_month: str | None = None
    cost_compare_month: str | None = None
    cost_initialized: bool = False
    ai_messages: list[dict[str, str]] = field(default_factory=list)
    ai_busy: bool = False
    ai_conversation_id: str | None = None
    ai_connection_note: str = ''
    ai_context_start: date | None = None
    ai_context_end: date | None = None
    ai_context_area: str | None = None
    agent_alert_id: str = 'period-summary'
    agent_to_text: str = ''
    agent_cc_text: str = ''
    agent_sender_mailbox: str = ''
    agent_subject: str = ''
    agent_body: str = ''
    agent_draft_source: str = ''
    agent_attach_pdf: bool = True
    agent_reviewed: bool = False
    agent_busy: bool = False
    agent_note: str = ''
    agent_directory_results: list[DirectoryPerson] = field(default_factory=list)
    microsoft_device_sign_in: MicrosoftDeviceSignIn | None = None


def _kwh(value: float) -> str:
    return f'{value / 1000:,.1f} MWh' if abs(value) >= 1000 else f'{value:,.0f} kWh'


def _rand(value: float) -> str:
    return f'R {value:,.2f}'


def _display_date(value: object) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).strftime('%d %b %Y')
    except ValueError:
        return str(value)


def _display_timestamp(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime('%d %b %Y, %H:%M')
    except ValueError:
        return str(value)


def _display_time(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime('%H:%M')
    except ValueError:
        return '—' if not value else str(value)


def _latest_closed_date(dataset: DashboardDataset) -> date:
    return min(dataset.last_date, date.today() - timedelta(days=1))


def _month_end_date(value: date) -> date:
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _previous_month_start(value: date) -> date:
    return (value.replace(day=1) - timedelta(days=1)).replace(day=1)


def _available_month_options(first_date: date, last_date: date) -> dict[str, str]:
    options: dict[str, str] = {}
    cursor = first_date.replace(day=1)
    final = last_date.replace(day=1)
    while cursor <= final:
        options[cursor.strftime('%Y-%m')] = cursor.strftime('%B %Y')
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return options


def _comparison_detail(metrics: dict[str, object]) -> str:
    value = metrics['change_percent']
    if not isinstance(value, float):
        return 'No complete earlier period is available'
    direction = 'higher' if value > 0 else 'lower' if value < 0 else 'unchanged'
    return (
        f"{abs(value):.1f}% {direction} than "
        f"{_display_date(metrics['comparison_start'])}–{_display_date(metrics['comparison_end'])}"
    )


def _metric_card(label: str, value: str, detail: str, icon: str, tone: str) -> None:
    with ui.card().classes('metric-card w-full shadow-none'):
        with ui.row().classes('w-full items-start justify-between no-wrap'):
            with ui.column().classes('gap-1 min-w-0'):
                ui.label(label).classes('metric-label')
                ui.label(value).classes('metric-value')
                ui.label(detail).classes('metric-detail')
            ui.icon(icon, size='26px', color=tone).classes('metric-icon')


def _chart_card(options: dict, height: str = '360px') -> None:
    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.echart(options).classes('w-full').style(f'height: {height}')


def _overview(
    view: DashboardView,
    solar_view: DashboardView | None = None,
    balance: SupplyDemandBalance | None = None,
    on_supply_details: Callable[[], None] | None = None,
) -> None:
    metrics = view.metrics
    selected_scope = view.area if view.area != ALL_AREAS else 'all warehouse meters'
    unusual_days = int(metrics['alert_days']) + (
        int(solar_view.metrics['alert_days']) if solar_view is not None else 0
    )
    critical_days = int(metrics['critical_days']) + (
        int(solar_view.metrics['critical_days']) if solar_view is not None else 0
    )
    higher_solar_days = (
        len(solar_view.solar_positive_events) if solar_view is not None else 0
    )
    with ui.element('div').classes('metric-grid w-full'):
        _metric_card(
            'Warehouse electricity used' if solar_view is not None else 'Electricity used',
            _kwh(float(metrics['total_import_kwh'])),
            f"{_comparison_detail(metrics)} · Average {_kwh(float(metrics['average_daily_kwh']))}/day",
            'bolt',
            'orange',
        )
        if solar_view is not None:
            solar_detail = (
                f"Contributed {float(balance.metrics['solar_contribution_percent']):.1f}% of warehouse use"
                if balance is not None else
                'Generated electricity supplied by the solar installation'
            )
            _metric_card(
                'Solar generated',
                _kwh(float(solar_view.metrics['total_import_kwh'])),
                solar_detail,
                'solar_power',
                'amber',
            )
        else:
            _metric_card(
                'Average daily use',
                _kwh(float(metrics['average_daily_kwh'])),
                f"{selected_scope} across {metrics['data_days']} day(s)",
                'calendar_today',
                'teal',
            )
        _metric_card(
            'Highest working load',
            f"{float(metrics['peak_kw']):,.1f} kW",
            f"{metrics['peak_kw_area']} · {_display_timestamp(metrics['peak_kw_time'])}",
            'electric_meter',
            'light-blue',
        )
        _metric_card(
            'Highest total demand',
            f"{float(metrics['peak_kva']):,.1f} kVA",
            f"{metrics['peak_area']} · {_display_timestamp(metrics['peak_time'])}",
            'speed',
            'deep-orange',
        )
        _metric_card(
            'Unusual days',
            f"{unusual_days:,}",
            f"{critical_days} urgent warehouse or low-solar warning(s)",
            'notification_important',
            'red' if critical_days else 'amber',
        )

    if balance is not None and bool(balance.metrics['matched_intervals']):
        with ui.card().classes('dashboard-card w-full shadow-none'):
            with ui.row().classes('w-full items-start justify-between gap-3 flex-wrap'):
                with ui.column().classes('gap-0'):
                    ui.label('Supply & demand snapshot').classes('section-title')
                    ui.label(
                        'Solar generation compared with combined warehouse use for the active dates.'
                    ).classes('section-subtitle')
                if on_supply_details is not None:
                    ui.button(
                        'View detailed balance', icon='arrow_forward', on_click=on_supply_details
                    ).props('flat no-caps').classes('toolbar-action')
            with ui.element('div').classes('balance-summary-grid w-full'):
                with ui.column().classes('balance-summary-item gap-1'):
                    ui.label('Solar energy contribution').classes('metric-label')
                    ui.label(
                        f"{float(balance.metrics['solar_contribution_percent']):.1f}%"
                    ).classes('balance-value')
                    ui.label('Share of matched warehouse energy supplied by solar').classes('metric-detail')
                with ui.column().classes('balance-summary-item gap-1'):
                    ui.label('Warehouse energy remaining').classes('metric-label')
                    ui.label(
                        f"{float(balance.metrics['remaining_demand_percent']):.1f}%"
                    ).classes('balance-value')
                    ui.label(
                        f"Estimated {_kwh(float(balance.metrics['estimated_grid_kwh']))} after solar"
                    ).classes('metric-detail')
                with ui.column().classes('balance-summary-item gap-1'):
                    ui.label('Time solar did not fully cover load').classes('metric-label')
                    ui.label(
                        f"{float(balance.metrics['demand_above_solar_percent']):.1f}%"
                    ).classes('balance-value')
                    ui.label(
                        f"{float(balance.metrics['demand_above_solar_hours']):,.1f} of "
                        f"{float(balance.metrics['matched_hours']):,.1f} matched hours"
                    ).classes('metric-detail')
            with ui.row().classes('metric-explainer w-full items-start no-wrap gap-2 mt-2'):
                ui.icon('info', size='18px')
                ui.label(
                    'The solar contribution and energy remaining add to 100% because both measure energy. '
                    'The time figure uses matched hours, so it is a separate measure and is not reduced by 8.6%.'
                ).classes('text-xs')

    with ui.element('div').classes('chart-grid w-full'):
        _chart_card(daily_energy_options(view), '420px')
        _chart_card(consumption_share_options(view), '420px')

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Meter summary by area').classes('section-title')
            comparison = (
                f"Change is measured against {_display_date(metrics['comparison_start'])}–"
                f"{_display_date(metrics['comparison_end'])}."
                if metrics['comparison_complete'] else
                'Change is blank when a complete earlier period is not available.'
            )
            ui.label(comparison).classes('section-subtitle')
            columns = [
                {'name': 'area', 'label': 'Area', 'field': 'area', 'align': 'left', 'sortable': True},
                {'name': 'use_kwh', 'label': 'Electricity used (kWh)', 'field': 'use_kwh', 'align': 'right', 'sortable': True},
                {'name': 'share', 'label': 'Share of selected total', 'field': 'share', 'align': 'right', 'sortable': True},
                {'name': 'change', 'label': 'Change vs previous', 'field': 'change', 'align': 'right'},
                {'name': 'load', 'label': 'Highest load (kW)', 'field': 'load', 'align': 'right', 'sortable': True},
                {'name': 'demand', 'label': 'Highest demand (kVA)', 'field': 'demand', 'align': 'right', 'sortable': True},
                {'name': 'unusual', 'label': 'Unusual days', 'field': 'unusual', 'align': 'right', 'sortable': True},
                {'name': 'review', 'label': 'Review level', 'field': 'review', 'align': 'left', 'sortable': True},
            ]
            rows = []
            for row in view.area_summary:
                change = row['change_percent']
                rows.append({
                    'area': row['area'],
                    'use_kwh': round(float(row['import_kwh']), 1),
                    'share': f"{float(row['share_percent']):.1f}%",
                    'change': f'{change:+.1f}%' if isinstance(change, float) else '—',
                    'load': round(float(row['peak_kw']), 1),
                    'demand': round(float(row['peak_kva']), 1),
                    'unusual': int(row['alert_days']),
                    'review': LEVEL_LABELS.get(str(row['status']), str(row['status'])),
                })
            ui.table(columns=columns, rows=rows, row_key='area').classes('w-full').props(
                'flat dense rows-per-page-options="[0]"'
            )

        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('What the data shows').classes('section-title')
            ui.label(
                f"These statements use {_scope_label(view.area)} from "
                f"{_display_date(view.start_date)} to {_display_date(view.end_date)} only."
            ).classes('section-subtitle')
            with ui.column().classes('w-full gap-2'):
                insights = [
                    insight for insight in view.insights
                    if solar_view is None or insight['title'] != 'Unusual usage days'
                ]
                if solar_view is not None:
                    insights.append({
                        'icon': 'priority_high' if unusual_days else 'verified',
                        'tone': 'negative' if critical_days else 'warning' if unusual_days else 'positive',
                        'title': 'Readings requiring review',
                        'text': (
                            f'{unusual_days} warehouse usage/demand or low-solar readings require review.'
                            if unusual_days else 'No unusual warehouse use, demand or low-solar readings were detected.'
                        ),
                    })
                    insights.append({
                        'icon': 'wb_sunny',
                        'tone': 'positive',
                        'title': 'Higher solar generation',
                        'text': (
                            f'{higher_solar_days} day(s) generated materially more solar energy than their '
                            'recent same-weekday average. This is positive when confirmed against the inverter and weather.'
                            if higher_solar_days else
                            'No materially higher-than-usual solar generation days were detected.'
                        ),
                    })
                for insight in insights:
                    with ui.row().classes('insight-row items-start no-wrap gap-3'):
                        ui.icon(insight['icon'], color=insight['tone'], size='22px')
                        with ui.column().classes('gap-0 min-w-0'):
                            ui.label(insight['title']).classes('body-text font-bold text-sm')
                            ui.label(insight['text']).classes('muted-text text-xs leading-relaxed')


def _trends(dataset: DashboardDataset, view: DashboardView) -> None:
    profile = interval_profile(dataset, view.start_date, view.end_date, view.area)
    with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
        ui.icon('info', color='orange', size='22px')
        ui.label(
            'kWh is the amount used over time. kW is the working load at a point in time. '
            'kVA is the total electrical demand placed on the supply.'
        ).classes('text-sm')
    with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
        ui.icon('data_alert', color='orange', size='22px')
        ui.label(
            "kVA is shown exactly as supplied by PNPSCADA. On several warehouse meters the supplied kVA channel "
            'closely mirrors kW, so confirm the meter configuration before treating it as an independent demand measurement.'
        ).classes('text-sm')
    with ui.element('div').classes('chart-grid w-full'):
        _chart_card(daily_energy_options(view), '420px')
        _chart_card(weekly_energy_options(view), '420px')
        _chart_card(load_profile_options(profile), '420px')
        _chart_card(demand_by_area_options(view), '420px')


def _solar_performance(performance: SolarPerformance) -> None:
    metrics = performance.metrics
    change = metrics['change_percent']
    if isinstance(change, float):
        comparison_text = (
            f"{abs(change):.1f}% {'higher' if change > 0 else 'lower' if change < 0 else 'unchanged'} "
            f"than {_display_date(metrics['comparison_start'])} to "
            f"{_display_date(metrics['comparison_end'])}"
        )
    else:
        comparison_text = 'A complete matched earlier period is not available'

    with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
        ui.icon('info', color='amber', size='22px')
        ui.label(
            'This tab measures electricity recorded as generated by the solar meter. '
            'Panel efficiency, capacity factor, avoided cost and electricity exported to the grid '
            'cannot be confirmed without rated system capacity, irradiance, tariff and a main grid meter. '
            'Weather and seasonal daylight can materially affect day-to-day generation.'
        ).classes('text-sm')

    with ui.element('div').classes('metric-grid w-full'):
        _metric_card(
            'Solar energy generated',
            _kwh(float(metrics['total_generation_kwh'])),
            comparison_text,
            'solar_power',
            'amber',
        )
        _metric_card(
            'Average generated per day',
            _kwh(float(metrics['average_daily_generation_kwh'])),
            f"Across {len(performance.daily):,} selected day(s)",
            'calendar_today',
            'teal',
        )
        _metric_card(
            'Best generation day',
            _kwh(float(metrics['best_day_kwh'])),
            _display_date(metrics['best_day_date']),
            'emoji_events',
            'orange',
        )
        _metric_card(
            'Highest recorded output',
            f"{float(metrics['peak_output_kw']):,.1f} kW",
            _display_timestamp(metrics['peak_output_time']),
            'bolt',
            'deep-orange',
        )
        _metric_card(
            'Recorded generation time',
            f"{float(metrics['active_generation_hours']):,.1f} hours",
            f"Average {float(metrics['average_generation_hours_per_day']):.1f} hours/day above 0.1 kW",
            'schedule',
            'light-blue',
        )

    with ui.element('div').classes('chart-grid w-full'):
        _chart_card(solar_daily_generation_options(performance), '430px')
        _chart_card(solar_period_generation_options(performance), '430px')
        _chart_card(solar_output_profile_options(performance), '410px')
        _chart_card(solar_daily_peak_options(performance), '410px')

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Daily solar performance').classes('section-title')
            ui.label(
                'Generation time counts portal-confirmed half-hours where recorded solar output exceeded 0.1 kW.'
            ).classes('section-subtitle')
            columns = [
                {'name': 'date', 'label': 'Date', 'field': 'date', 'align': 'left', 'sortable': True},
                {'name': 'generated', 'label': 'Generated (kWh)', 'field': 'generated', 'align': 'right', 'sortable': True},
                {'name': 'change', 'label': 'Change vs prior day', 'field': 'change', 'align': 'right'},
                {'name': 'peak', 'label': 'Peak output (kW)', 'field': 'peak', 'align': 'right', 'sortable': True},
                {'name': 'peak_time', 'label': 'Peak time', 'field': 'peak_time', 'align': 'left'},
                {'name': 'hours', 'label': 'Generation hours', 'field': 'hours', 'align': 'right', 'sortable': True},
                {'name': 'window', 'label': 'Recorded output window', 'field': 'window', 'align': 'left'},
                {'name': 'quality', 'label': 'Data quality', 'field': 'quality', 'align': 'left', 'sortable': True},
                {'name': 'review', 'label': 'Meter flag', 'field': 'review', 'align': 'left', 'sortable': True},
            ]
            rows = []
            for row in reversed(performance.daily):
                day_change = row['change_percent']
                output_window = (
                    f"{_display_time(row['output_start'])}–{_display_time(row['output_end'])}"
                    if row['output_start'] and row['output_end'] else 'No output recorded'
                )
                rows.append({
                    'date': _display_date(row['date']),
                    'generated': round(float(row['generation_kwh']), 1),
                    'change': f'{day_change:+.1f}%' if isinstance(day_change, float) else '—',
                    'peak': round(float(row['peak_kw']), 1),
                    'peak_time': _display_time(row['peak_time']),
                    'hours': round(float(row['active_hours']), 1),
                    'window': output_window,
                    'quality': row['data_quality_status'],
                    'review': (
                        'Low output warning'
                        if row['alert_level'] in {'Watch', 'High', 'Critical'} else
                        'Higher output - verify'
                        if row['alert_level'] == 'Positive' else
                        'Not enough history'
                        if row['alert_level'] == 'Not assessed' else 'Normal'
                    ),
                })
            ui.table(columns=columns, rows=rows, row_key='date').classes('w-full').props(
                'flat dense rows-per-page-options="[7,14,31,0]"'
            )

        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('What the solar meter shows').classes('section-title')
            ui.label(
                f"These statements use solar readings from {_display_date(performance.start_date)} "
                f"to {_display_date(performance.end_date)} only."
            ).classes('section-subtitle')
            quality_text = (
                f"{int(metrics['complete_days'])} complete, "
                f"{int(metrics['estimated_days'])} estimated and "
                f"{int(metrics['incomplete_days'])} incomplete day(s)."
            )
            insights = [
                (
                    'compare_arrows',
                    'Generation compared with the preceding period',
                    comparison_text + '.',
                ),
                (
                    'emoji_events',
                    'Strongest generation day',
                    f"The solar meter recorded {_kwh(float(metrics['best_day_kwh']))} on "
                    f"{_display_date(metrics['best_day_date'])}.",
                ),
                (
                    'wb_sunny',
                    'Typical recorded output window',
                    f"Average output exceeded 0.1 kW from approximately "
                    f"{metrics['typical_output_start']} to {metrics['typical_output_end']}. "
                    f"Average output while generating was "
                    f"{float(metrics['average_output_while_generating_kw']):,.1f} kW.",
                ),
                (
                    'fact_check',
                    'Reading completeness',
                    quality_text,
                ),
                (
                    'notification_important' if int(metrics['unusual_days']) else 'verified',
                    'Low solar generation warnings',
                    f"{int(metrics['unusual_days'])} day(s) were at least 30% and 30 kWh below the recent same-weekday average. "
                    'Check weather first, then inverter availability, outages or curtailment.'
                    if int(metrics['unusual_days']) else
                    'No materially low solar-generation days were detected in the selected dates.',
                ),
                (
                    'wb_sunny',
                    'Higher solar generation',
                    f"{int(metrics['higher_generation_days'])} day(s) were materially above the recent same-weekday average. "
                    'This is positive if the meter reading is confirmed against the inverter and weather.'
                    if int(metrics['higher_generation_days']) else
                    'No materially higher-than-usual solar-generation days were detected.',
                ),
            ]
            with ui.column().classes('w-full gap-2'):
                for icon, title, text in insights:
                    with ui.row().classes('insight-row items-start no-wrap gap-3'):
                        ui.icon(icon, color='primary', size='22px')
                        with ui.column().classes('gap-0 min-w-0'):
                            ui.label(title).classes('body-text font-bold text-sm')
                            ui.label(text).classes('muted-text text-xs leading-relaxed')


def _solar_investment(analysis: CostAnalysis) -> None:
    actual = current_solar_investment_metrics(analysis)

    with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
        ui.icon('verified', color='teal', size='22px')
        ui.label(
            'Current performance uses PNPSCADA readings and the verified municipal tariff mapping. '
            'Option 1 and Option 2 are vendor forecasts from the February 2026 proposals; their proposal '
            'kWh and prices never replace the live meter history or Cost Centre calculations.'
        ).classes('text-sm')

    with ui.tabs().classes('w-full investment-tabs') as tabs:
        current_tab = ui.tab('Current performance', icon='monitoring')
        option_one_tab = ui.tab('Option 1', icon='solar_power')
        option_two_tab = ui.tab('Option 2', icon='battery_charging_full')

    with ui.tab_panels(tabs, value=current_tab).classes('w-full bg-transparent p-0'):
        with ui.tab_panel(current_tab).classes('p-0 pt-3'):
            with ui.element('div').classes('metric-grid w-full'):
                _metric_card(
                    'Cost before solar savings',
                    _rand(float(actual['cost_before_solar'])),
                    f"Selected {_display_date(analysis.start_date)} to {_display_date(analysis.end_date)}",
                    'payments', 'orange',
                )
                _metric_card(
                    'Solar savings',
                    _rand(float(actual['solar_savings'])),
                    f"{_kwh(float(actual['solar_used_kwh']))} matched to Warehouse 8 use",
                    'savings', 'teal',
                )
                _metric_card(
                    'Cost after solar savings',
                    _rand(float(actual['cost_after_solar'])),
                    'Operational estimate using PNPSCADA readings',
                    'account_balance_wallet', 'light-blue',
                )
                _metric_card(
                    'Solar generated',
                    _kwh(float(actual['solar_generated_kwh'])),
                    f"{float(actual['solar_utilization_percent']):.1f}% matched to WH8 consumption",
                    'solar_power', 'amber',
                )
                _metric_card(
                    'Possible surplus',
                    _kwh(float(actual['possible_excess_solar_kwh'])),
                    'Not confirmed as grid export without a main grid meter',
                    'swap_horiz', 'deep-purple',
                )

            with ui.element('div').classes('chart-grid w-full'):
                _chart_card(
                    solar_actual_vs_proposal_options(
                        float(actual['annualized_generation_kwh']),
                        float(actual['annualized_grid_reduction_kwh']),
                        SOLAR_PROPOSALS,
                    ),
                    '430px',
                )
                with ui.card().classes('dashboard-card w-full shadow-none'):
                    ui.label('What the current meter data can confirm').classes('section-title')
                    ui.label(
                        'Annualised values extend the selected-period daily average to 365 days. '
                        'Use a full recent year for the most meaningful proposal comparison.'
                    ).classes('section-subtitle')
                    confirmations = [
                        ('Solar generation', _kwh(float(actual['solar_generated_kwh'])), 'Measured by PNPSCADA'),
                        ('Grid purchases reduced', _kwh(float(actual['solar_used_kwh'])), 'Estimated where solar overlaps Warehouse 8 use'),
                        ('Battery contribution', 'Not available', 'No battery telemetry is present in PNPSCADA'),
                        ('Peak demand effect', 'Not confirmed', 'No pre-project baseline or main grid demand meter is available'),
                        ('Investment and payback', 'Not available for current system', 'Requires confirmed installed cost and commissioning scope'),
                    ]
                    for title, value, note in confirmations:
                        with ui.row().classes('insight-row w-full items-start justify-between gap-3'):
                            with ui.column().classes('gap-0'):
                                ui.label(title).classes('body-text text-sm font-bold')
                                ui.label(note).classes('muted-text text-xs')
                            ui.label(value).classes('body-text text-sm font-bold text-right')

        for tab, proposal in zip((option_one_tab, option_two_tab), SOLAR_PROPOSALS):
            with ui.tab_panel(tab).classes('p-0 pt-3'):
                _solar_proposal_panel(proposal, actual)


def _solar_proposal_panel(
    proposal: SolarProposal,
    actual: dict[str, object],
) -> None:
    with ui.row().classes('warning-banner w-full items-start gap-2'):
        ui.icon('info', size='20px', color='orange')
        ui.label(
            f'{proposal.label} values are forecasts from {proposal.source_name}. '
            'They are shown as an investment benchmark and are not substituted into live Cost Centre results.'
        ).classes('text-sm')

    with ui.element('div').classes('metric-grid w-full'):
        _metric_card(
            'Year 1 cost before solar', _rand(proposal.year_one_cost_before_solar),
            'Vendor baseline; PNPSCADA remains the live source', 'payments', 'orange',
        )
        _metric_card(
            'Year 1 solar savings', _rand(proposal.year_one_savings),
            f'{proposal.usage_offset_percent:.1f}% forecast usage offset', 'savings', 'teal',
        )
        _metric_card(
            'Year 1 cost after solar', _rand(proposal.year_one_cost_after_solar),
            'Vendor forecast after modeled savings', 'account_balance_wallet', 'light-blue',
        )
        _metric_card(
            'Net investment', _rand(proposal.net_investment),
            f'Gross {_rand(proposal.gross_investment)} less forecast tax deduction {_rand(proposal.tax_deduction)}',
            'paid', 'deep-purple',
        )
        _metric_card(
            'Simple payback', f'{proposal.simple_payback_years:.1f} years',
            f'Proposal states {proposal.vendor_payback_years:.1f} years', 'schedule', 'amber',
        )

    with ui.element('div').classes('chart-grid w-full'):
        _chart_card(solar_proposal_energy_flow_options(proposal), '430px')
        _chart_card(solar_proposal_cash_flow_options(proposal), '430px')

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('System and energy forecast').classes('section-title')
            rows = [
                ('PV array', f'{proposal.pv_dc_kwp:,.3f} kWp DC'),
                ('Inverter capacity', f'{proposal.inverter_ac_kw:,.3f} kW AC'),
                ('Annual solar generation', _kwh(proposal.annual_generation_kwh)),
                ('Annual grid purchases reduced', _kwh(proposal.annual_grid_reduction_kwh)),
                ('Annual forecast export', _kwh(proposal.annual_export_kwh)),
                ('Annual system/storage losses', _kwh(proposal.annual_losses_kwh)),
                ('Battery', f'{proposal.battery_power_kw:,.0f} kW / {proposal.battery_capacity_kwh:,.1f} kWh'),
                ('Annual battery discharge', _kwh(proposal.annual_battery_discharge_kwh)),
                ('Battery round-trip efficiency', f'{proposal.round_trip_efficiency_percent:.1f}%'),
                ('Peak demand effect', 'No reduction modeled in the proposal'),
            ]
            columns = [
                {'name': 'measure', 'label': 'Measure', 'field': 'measure', 'align': 'left'},
                {'name': 'value', 'label': 'Proposal forecast', 'field': 'value', 'align': 'right'},
            ]
            ui.table(
                columns=columns,
                rows=[{'measure': measure, 'value': value} for measure, value in rows],
                row_key='measure',
            ).classes('w-full').props(
                'flat bordered separator=horizontal hide-pagination rows-per-page-options="[0]"'
            )

        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Actual run-rate versus this option').classes('section-title')
            actual_generation = float(actual['annualized_generation_kwh'])
            actual_grid_reduction = float(actual['annualized_grid_reduction_kwh'])
            generation_percent = (
                actual_generation / proposal.annual_generation_kwh * 100.0
                if proposal.annual_generation_kwh else 0.0
            )
            grid_percent = (
                actual_grid_reduction / proposal.annual_grid_reduction_kwh * 100.0
                if proposal.annual_grid_reduction_kwh else 0.0
            )
            comparisons = [
                (
                    'Annualised solar generation',
                    f'{_kwh(actual_generation)} vs {_kwh(proposal.annual_generation_kwh)} ({generation_percent:.1f}%)',
                    'Comparable only if the PNPSCADA solar meter covers the same system scope.',
                ),
                (
                    'Annualised grid reduction',
                    f'{_kwh(actual_grid_reduction)} vs {_kwh(proposal.annual_grid_reduction_kwh)} ({grid_percent:.1f}%)',
                    'Current value is estimated from solar matched to Warehouse 8 consumption.',
                ),
                (
                    'Battery and export',
                    'Not directly comparable',
                    'PNPSCADA does not expose battery telemetry or a confirmed main-grid export meter.',
                ),
                (
                    'Demand savings',
                    'None assumed',
                    'Both proposals retain the same modeled annual demand charge.',
                ),
            ]
            for title, value, note in comparisons:
                with ui.row().classes('insight-row w-full items-start justify-between gap-3'):
                    with ui.column().classes('gap-0'):
                        ui.label(title).classes('body-text text-sm font-bold')
                        ui.label(note).classes('muted-text text-xs')
                    ui.label(value).classes('body-text text-sm font-bold text-right')


def _supply_demand(
    balance: SupplyDemandBalance,
    warehouse_view: DashboardView,
) -> None:
    metrics = balance.metrics
    with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
        ui.icon('info', color='orange', size='22px')
        ui.label(
            'This is a site energy balance: combined warehouse use is compared with solar generation. '
            'It does not test transformer or contracted supply capacity. Estimated grid energy is the remaining '
            'warehouse requirement after solar generation; it is not a grid-meter reading.'
        ).classes('text-sm')

    if not bool(metrics['matched_intervals']):
        with ui.card().classes('dashboard-card w-full shadow-none'):
            ui.label('No matching supply and demand readings').classes('section-title')
            ui.label(
                'The selected dates do not contain complete half-hour readings for all warehouse meters and the solar meter.'
            ).classes('section-subtitle')
        return

    with ui.element('div').classes('metric-grid w-full'):
        _metric_card(
            'Warehouse electricity use',
            _kwh(float(metrics['warehouse_kwh'])),
            f"Combined demand across {metrics['data_days']} day(s)",
            'warehouse',
            'light-blue',
        )
        _metric_card(
            'Solar generated',
            _kwh(float(metrics['solar_kwh'])),
            'Electricity provided by the solar installation',
            'solar_power',
            'amber',
        )
        _metric_card(
            'Estimated grid energy needed',
            _kwh(float(metrics['estimated_grid_kwh'])),
            f"{float(metrics['remaining_demand_percent']):.1f}% of matched warehouse energy remained after solar",
            'electrical_services',
            'indigo',
        )
        _metric_card(
            'Solar energy contribution',
            f"{float(metrics['solar_contribution_percent']):.1f}%",
            'Share of matched warehouse energy supplied by solar',
            'energy_savings_leaf',
            'teal',
        )
        _metric_card(
            'Highest estimated grid need',
            f"{float(metrics['peak_estimated_grid_kw']):,.1f} kW",
            _display_timestamp(metrics['peak_estimated_grid_time']),
            'bolt',
            'deep-orange',
        )

    with ui.element('div').classes('chart-grid w-full'):
        _chart_card(daily_supply_demand_options(balance), '440px')
        _chart_card(supply_demand_profile_options(balance), '440px')

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Daily energy balance').classes('section-title')
            ui.label(
                'Grid energy is estimated for each matching half-hour; it is not read from a grid meter.'
            ).classes('section-subtitle')
            columns = [
                {'name': 'date', 'label': 'Date', 'field': 'date', 'align': 'left', 'sortable': True},
                {'name': 'warehouse', 'label': 'Warehouse use (kWh)', 'field': 'warehouse', 'align': 'right', 'sortable': True},
                {'name': 'solar', 'label': 'Solar generated (kWh)', 'field': 'solar', 'align': 'right', 'sortable': True},
                {'name': 'grid', 'label': 'Est. grid energy (kWh)', 'field': 'grid', 'align': 'right', 'sortable': True},
                {'name': 'share', 'label': 'Solar contribution', 'field': 'share', 'align': 'right', 'sortable': True},
                {'name': 'peak_grid', 'label': 'Peak est. grid need (kW)', 'field': 'peak_grid', 'align': 'right', 'sortable': True},
                {'name': 'peak_time', 'label': 'Peak time', 'field': 'peak_time', 'align': 'left'},
            ]
            rows = [
                {
                    'date': _display_date(row['date']),
                    'warehouse': round(float(row['warehouse_kwh']), 1),
                    'solar': round(float(row['solar_kwh']), 1),
                    'grid': round(float(row['estimated_grid_kwh']), 1),
                    'share': f"{float(row['solar_contribution_percent']):.1f}%",
                    'peak_grid': round(float(row['peak_estimated_grid_kw']), 1),
                    'peak_time': _display_timestamp(row['peak_estimated_grid_time']),
                }
                for row in reversed(balance.daily)
            ]
            ui.table(columns=columns, rows=rows, row_key='date').classes('w-full').props(
                'flat dense rows-per-page-options="[7,14,31,0]"'
            )

        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('What the comparison means').classes('section-title')
            ui.label(
                f"Site-level result for {_display_date(balance.start_date)} to "
                f"{_display_date(balance.end_date)}."
            ).classes('section-subtitle')
            with ui.column().classes('w-full gap-2'):
                explanations = [
                    (
                        'compare_arrows',
                        'Time solar did not fully cover warehouse load',
                        f"Solar generation was below combined warehouse load for "
                        f"{float(metrics['demand_above_solar_hours']):,.1f} hours "
                        f"out of {float(metrics['matched_hours']):,.1f} matched hours "
                        f"({float(metrics['demand_above_solar_percent']):.1f}%). "
                        'The difference had to come from the grid or another unmetered source.',
                    ),
                    (
                        'pie_chart',
                        'Energy contribution and time are different measures',
                        f"Solar supplied {float(metrics['solar_contribution_percent']):.1f}% of matched energy, "
                        f"leaving {float(metrics['remaining_demand_percent']):.1f}% after solar. "
                        'The percentage of hours below load is measured separately and should not be subtracted from the solar contribution.',
                    ),
                    (
                        'bolt',
                        'Largest estimated grid requirement',
                        f"The largest remaining requirement was "
                        f"{float(metrics['peak_estimated_grid_kw']):,.1f} kW on "
                        f"{_display_timestamp(metrics['peak_estimated_grid_time'])}.",
                    ),
                    (
                        'solar_power',
                        'Best solar contribution day',
                        f"The strongest estimated contribution was "
                        f"{float(metrics['best_solar_day_percent']):.1f}% on "
                        f"{_display_date(metrics['best_solar_day'])}.",
                    ),
                ]
                if float(metrics['possible_excess_solar_kwh']) > 0:
                    explanations.append((
                        'swap_horiz',
                        'Possible surplus solar generation',
                        f"Solar generation was above warehouse demand by "
                        f"{_kwh(float(metrics['possible_excess_solar_kwh']))} across matching intervals. "
                        'Confirm export using a main grid meter before treating this as electricity sent to the grid.',
                    ))
                for icon, title, text in explanations:
                    with ui.row().classes('insight-row items-start no-wrap gap-3'):
                        ui.icon(icon, color='primary', size='22px')
                        with ui.column().classes('gap-0 min-w-0'):
                            ui.label(title).classes('body-text font-bold text-sm')
                            ui.label(text).classes('muted-text text-xs leading-relaxed')

    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('Warehouse demand by area').classes('section-title')
        ui.label(
            'Solar is a site-level meter, so it is not allocated to an individual warehouse without a confirmed allocation method.'
        ).classes('section-subtitle')
        columns = [
            {'name': 'area', 'label': 'Warehouse area', 'field': 'area', 'align': 'left', 'sortable': True},
            {'name': 'use', 'label': 'Electricity used (kWh)', 'field': 'use', 'align': 'right', 'sortable': True},
            {'name': 'share', 'label': 'Share of warehouse total', 'field': 'share', 'align': 'right', 'sortable': True},
            {'name': 'peak_kw', 'label': 'Highest load (kW)', 'field': 'peak_kw', 'align': 'right', 'sortable': True},
            {'name': 'peak_time', 'label': 'Peak load time', 'field': 'peak_time', 'align': 'left'},
        ]
        rows = [
            {
                'area': row['area'],
                'use': round(float(row['import_kwh']), 1),
                'share': f"{float(row['share_percent']):.1f}%",
                'peak_kw': round(float(row['peak_kw']), 1),
                'peak_time': _display_timestamp(row['peak_kw_time']),
            }
            for row in warehouse_view.area_summary
        ]
        ui.table(columns=columns, rows=rows, row_key='area').classes('w-full').props(
            'flat dense rows-per-page-options="[0]"'
        )


def _comparison_change(current: float, previous: float) -> str:
    if previous == 0:
        return 'No previous value is available' if current else 'No change'
    value = (current - previous) / abs(previous) * 100.0
    direction = 'higher' if value > 0 else 'lower' if value < 0 else 'unchanged'
    return f'{abs(value):.1f}% {direction} than the previous period'


def _quality_aware_change(current: float, previous: float, complete: bool) -> str:
    if complete:
        return _comparison_change(current, previous)
    return 'Recorded comparable days only; percentage withheld'


def _comparisons(
    comparison: PeriodComparison,
    on_comparison_type_change: Callable[[str], None],
    on_named_periods_apply: Callable[[str, str, str], None],
    on_custom_ranges_apply: Callable[[str, str, str, str], None],
    available_start: date,
    available_end: date,
) -> None:
    metrics = comparison.metrics
    is_all_areas = comparison.area == ALL_AREAS
    is_solar = comparison.area == SOLAR_AREA
    has_separate_solar = bool(metrics.get('has_separate_solar'))
    scope = (
        'all warehouse areas; solar generation shown separately'
        if is_all_areas else comparison.area
    )
    subject = (
        'Solar generation'
        if is_solar else 'Warehouse electricity use'
        if is_all_areas else f'{comparison.area} electricity use'
    )
    with ui.card().classes('dashboard-card w-full shadow-none'):
        with ui.row().classes('w-full items-end justify-between gap-4 flex-wrap'):
            with ui.column().classes('gap-0 min-w-0'):
                ui.label('Period comparison').classes('section-title')
                ui.label(
                    f'Compare {scope} across two clear date ranges.'
                ).classes('section-subtitle')
            ui.select(
                ['Year vs year', 'Month vs month', 'Week vs week', 'Custom ranges'],
                value=comparison.comparison_type,
                label='Comparison basis',
                on_change=lambda event: on_comparison_type_change(str(event.value)),
            ).props('outlined dense options-dense').classes('w-56')
        if comparison.comparison_type == 'Year vs year':
            year_options = [
                str(year)
                for year in range(available_end.year, available_start.year - 1, -1)
            ]
            with ui.element('div').classes('comparison-preset-grid w-full mt-3'):
                first_period = ui.select(
                    year_options,
                    value=str(comparison.current_start.year),
                    label='Period A year',
                ).props('outlined dense options-dense')
                second_period = ui.select(
                    year_options,
                    value=str(comparison.previous_start.year),
                    label='Compare with year',
                ).props('outlined dense options-dense')
                ui.button(
                    'Compare',
                    icon='compare_arrows',
                    on_click=lambda: on_named_periods_apply(
                        comparison.comparison_type,
                        str(first_period.value),
                        str(second_period.value),
                    ),
                ).props('flat no-caps').classes('primary-action')
        elif comparison.comparison_type in {'Month vs month', 'Week vs week'}:
            input_type = 'month' if comparison.comparison_type == 'Month vs month' else 'week'
            first_value = (
                comparison.current_start.strftime('%Y-%m')
                if input_type == 'month'
                else f"{comparison.current_start.isocalendar().year}-W{comparison.current_start.isocalendar().week:02d}"
            )
            second_value = (
                comparison.previous_start.strftime('%Y-%m')
                if input_type == 'month'
                else f"{comparison.previous_start.isocalendar().year}-W{comparison.previous_start.isocalendar().week:02d}"
            )
            period_label = 'month' if input_type == 'month' else 'week'
            with ui.element('div').classes('comparison-preset-grid w-full mt-3'):
                first_period = ui.input(
                    f'Period A {period_label}', value=first_value
                ).props(f'type={input_type} outlined dense')
                second_period = ui.input(
                    f'Compare with {period_label}', value=second_value
                ).props(f'type={input_type} outlined dense')
                ui.button(
                    'Compare',
                    icon='compare_arrows',
                    on_click=lambda: on_named_periods_apply(
                        comparison.comparison_type,
                        str(first_period.value),
                        str(second_period.value),
                    ),
                ).props('flat no-caps').classes('primary-action')
        else:
            with ui.element('div').classes('comparison-range-grid w-full mt-3'):
                current_from = ui.input(
                    'Current period from', value=comparison.current_start.isoformat()
                ).props('type=date outlined dense')
                current_to = ui.input(
                    'Current period to', value=comparison.current_end.isoformat()
                ).props('type=date outlined dense')
                previous_from = ui.input(
                    'Compare with from', value=comparison.previous_start.isoformat()
                ).props('type=date outlined dense')
                previous_to = ui.input(
                    'Compare with to', value=comparison.previous_end.isoformat()
                ).props('type=date outlined dense')
                ui.button(
                    'Compare',
                    icon='compare_arrows',
                    on_click=lambda: on_custom_ranges_apply(
                        str(current_from.value),
                        str(current_to.value),
                        str(previous_from.value),
                        str(previous_to.value),
                    ),
                ).props('flat no-caps').classes('primary-action')
        with ui.column().classes('report-scope w-full gap-1 mt-2'):
            ui.label(f'ACTIVE METER FILTER: {scope}').classes('text-xs font-bold uppercase')
            ui.label('CURRENT PERIOD').classes('text-xs font-bold uppercase')
            ui.label(
                f"{_display_date(comparison.current_start)} to {_display_date(comparison.current_end)}"
            ).classes('text-sm font-bold')
            ui.label(
                f"Previous: {_display_date(comparison.previous_start)} to "
                f"{_display_date(comparison.previous_end)}"
            ).classes('muted-text text-xs')
            ui.label(comparison.note).classes('muted-text text-xs')

    incomplete_scopes = list(metrics.get('incomplete_areas', ()))
    if int(metrics.get('excluded_solar_day_pairs', 0)):
        incomplete_scopes.append('Solar generation')
    if incomplete_scopes:
        with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
            ui.icon('info', color='orange', size='22px')
            ui.label(
                f"Incomplete readings were excluded from like-for-like totals for "
                f"{', '.join(incomplete_scopes)}. "
                f"{int(metrics['excluded_area_day_pairs']) + int(metrics.get('excluded_solar_day_pairs', 0))} "
                "affected meter-day pair(s) were removed; "
                'no percentage is shown for those areas.'
            ).classes('text-sm')

    estimated_scopes = list(metrics.get('estimated_areas', ()))
    if int(metrics.get('estimated_solar_day_pairs', 0)):
        estimated_scopes.append('Solar generation')
    if estimated_scopes:
        with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
            ui.icon('info', color='amber', size='22px')
            ui.label(
                f"Lightly estimated readings were included for {', '.join(estimated_scopes)} "
                'so valid daily totals remain visible. Percentages are withheld for those meters.'
            ).classes('text-sm')

    warehouse_comparison_complete = (
        not list(metrics.get('incomplete_areas', ()))
        and not list(metrics.get('estimated_areas', ()))
    )
    solar_comparison_complete = str(metrics.get('solar_quality', 'Complete')) == 'Complete'

    with ui.element('div').classes('metric-grid w-full'):
        _metric_card(
            'Solar generated' if is_solar else 'Warehouse electricity used' if is_all_areas else 'Electricity used',
            _kwh(float(metrics['current_warehouse_kwh'])),
            f"{_quality_aware_change(float(metrics['current_warehouse_kwh']), float(metrics['previous_warehouse_kwh']), warehouse_comparison_complete)} "
            f"· Previous {_kwh(float(metrics['previous_warehouse_kwh']))}",
            'bolt',
            'orange',
        )
        _metric_card(
            'Average generated per day' if is_solar else 'Average used per day',
            _kwh(float(metrics['current_average_daily_kwh'])),
            f"Previous {_kwh(float(metrics['previous_average_daily_kwh']))} per day",
            'calendar_today',
            'teal',
        )
        _metric_card(
            'Highest working load',
            f"{float(metrics['current_peak_kw']):,.1f} kW",
            f"{metrics['current_peak_kw_area']} · Previous {float(metrics['previous_peak_kw']):,.1f} kW",
            'electric_meter',
            'light-blue',
        )
        _metric_card(
            'Highest total demand',
            f"{float(metrics['current_peak_kva']):,.1f} kVA",
            f"{metrics['current_peak_kva_area']} · Previous {float(metrics['previous_peak_kva']):,.1f} kVA",
            'speed',
            'deep-orange',
        )
        if has_separate_solar:
            _metric_card(
                'Solar generated',
                _kwh(float(metrics['current_solar_kwh'])),
                f"{_quality_aware_change(float(metrics['current_solar_kwh']), float(metrics['previous_solar_kwh']), solar_comparison_complete)} "
                f"· Previous {_kwh(float(metrics['previous_solar_kwh']))}",
                'solar_power',
                'amber',
            )

    with ui.row().classes('filter-summary w-full items-center gap-2'):
        ui.icon('bar_chart', size='18px')
        ui.label(
            'Area-change bars always show the recorded kWh change in colour. A narrow grey or amber end marker flags '
            'unavailable or estimated days without inventing an unknown kWh value.'
        ).classes('text-sm font-bold')

    with ui.element('div').classes('chart-grid w-full'):
        _chart_card(period_daily_comparison_options(comparison), '420px')
        _chart_card(period_area_comparison_options(comparison), '420px')
    with ui.element('div').classes('w-full'):
        _chart_card(period_area_change_options(comparison), '400px')
    if has_separate_solar:
        with ui.element('div').classes('w-full'):
            _chart_card(solar_period_comparison_options(comparison), '440px')

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label(
                'Comparison by warehouse area' if is_all_areas else f'Comparison for {comparison.area}'
            ).classes('section-title')
            ui.label(
                'Recorded kWh stays visible. Percentage is withheld for incomplete, estimated or very low-volume comparisons.'
            ).classes('section-subtitle')
            energy_action = 'generated' if is_solar else 'used'
            columns = [
                {'name': 'area', 'label': 'Area', 'field': 'area', 'align': 'left', 'sortable': True},
                {'name': 'current', 'label': f'Current recorded {energy_action} (kWh)', 'field': 'current', 'align': 'right', 'sortable': True},
                {'name': 'previous', 'label': f'Previous recorded {energy_action} (kWh)', 'field': 'previous', 'align': 'right', 'sortable': True},
                {'name': 'difference', 'label': 'Difference (kWh)', 'field': 'difference', 'align': 'right', 'sortable': True},
                {'name': 'change', 'label': 'Change', 'field': 'change', 'align': 'right', 'sortable': True},
                {'name': 'peak_kw', 'label': 'Current / previous peak kW', 'field': 'peak_kw', 'align': 'right'},
                {'name': 'peak_kva', 'label': 'Current / previous peak kVA', 'field': 'peak_kva', 'align': 'right'},
                {'name': 'unusual', 'label': 'Current / previous unusual', 'field': 'unusual', 'align': 'right'},
                {'name': 'quality', 'label': 'Comparison quality', 'field': 'quality', 'align': 'left', 'sortable': True},
            ]
            rows = []
            for row in comparison.area_comparison:
                change = row['change_percent']
                status = str(row.get('comparison_status', 'Complete'))
                rows.append({
                    'area': row['area'],
                    'current': round(float(row['current_kwh']), 1),
                    'previous': round(float(row['previous_kwh']), 1),
                    'difference': round(float(row['difference_kwh']), 1),
                    'change': f'{change:+.1f}%' if isinstance(change, float) else '—',
                    'peak_kw': f"{float(row['current_peak_kw']):,.1f} / {float(row['previous_peak_kw']):,.1f}",
                    'peak_kva': f"{float(row['current_peak_kva']):,.1f} / {float(row['previous_peak_kva']):,.1f}",
                    'unusual': f"{int(row['current_unusual'])} / {int(row['previous_unusual'])}",
                    'quality': status,
                })
            ui.table(columns=columns, rows=rows, row_key='area').classes('w-full').props(
                'flat dense rows-per-page-options="[0]"'
            )

        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('What changed').classes('section-title')
            ui.label(
                f'These statements use the two periods shown above and {scope}.'
            ).classes('section-subtitle')
            warehouse_difference = float(metrics['warehouse_difference_kwh'])
            warehouse_direction = 'increased' if warehouse_difference > 0 else 'decreased' if warehouse_difference < 0 else 'did not change'
            driver_difference = float(metrics['biggest_driver_kwh'])
            driver_status = str(metrics.get('biggest_driver_status', 'Complete'))
            driver_context = (
                'Across the matched usable days, '
                if driver_status in {'Incomplete data', 'Estimated data'} else ''
            )
            solar_change = metrics['solar_change_percent']
            insights = [
                (
                    'trending_up' if warehouse_difference > 0 else 'trending_down',
                    subject,
                    f"{subject} {warehouse_direction} by "
                    f"{_kwh(abs(warehouse_difference))} across the recorded comparable days. "
                    f"{_quality_aware_change(float(metrics['current_warehouse_kwh']), float(metrics['previous_warehouse_kwh']), warehouse_comparison_complete)}.",
                ),
                (
                    'warehouse',
                    'Area with the largest change' if is_all_areas else 'Selected meter change',
                    (
                        f"{driver_context}{metrics['biggest_driver_area']} changed by "
                        f"{driver_difference:+,.0f} kWh and had the largest effect on the total."
                        if is_all_areas else
                        f"{comparison.area} changed by {warehouse_difference:+,.1f} kWh across the matched readings."
                    ),
                ),
                (
                    'speed',
                    'Peak load and demand',
                    f"Highest working load changed from {float(metrics['previous_peak_kw']):,.1f} kW to "
                    f"{float(metrics['current_peak_kw']):,.1f} kW. Highest total demand changed from "
                    f"{float(metrics['previous_peak_kva']):,.1f} kVA to {float(metrics['current_peak_kva']):,.1f} kVA.",
                ),
                (
                    'notification_important',
                    'Unusual readings',
                    f"The selected meter scope recorded {int(metrics['current_unusual'])} unusual meter-day readings, "
                    f"compared with {int(metrics['previous_unusual'])} previously.",
                ),
            ]
            if has_separate_solar:
                insights.insert(3, (
                    'solar_power',
                    'Solar generation',
                    f"Solar generation was {abs(float(solar_change)):.1f}% "
                    f"{'higher' if float(solar_change) > 0 else 'lower' if float(solar_change) < 0 else 'unchanged'} than the previous period."
                    if isinstance(solar_change, float) and solar_comparison_complete
                    else 'A complete previous solar period is not available for a percentage comparison.',
                ))
            with ui.column().classes('w-full gap-2'):
                for icon, title, text in insights:
                    with ui.row().classes('insight-row items-start no-wrap gap-3'):
                        ui.icon(icon, color='primary', size='22px')
                        with ui.column().classes('gap-0 min-w-0'):
                            ui.label(title).classes('body-text font-bold text-sm')
                            ui.label(text).classes('muted-text text-xs leading-relaxed')


def _plain_reason(row: dict[str, str]) -> str:
    parts: list[str] = []
    alert_type = str(row.get('alert_type', ''))
    solar = row.get('area') == SOLAR_AREA
    if alert_type == 'Low solar generation':
        actual = float(row.get('import_kwh', 0) or 0)
        typical = float(row.get('baseline_import_kwh', 0) or 0)
        change = abs(float(row.get('consumption_variance_percent', 0) or 0))
        return (
            f'Solar generated {actual:,.0f} kWh, {change:.1f}% below the recent '
            f'same-weekday average ({typical:,.0f} kWh).'
        )
    if 'Consumption' in alert_type:
        actual = float(row.get('import_kwh', 0) or 0)
        typical = float(row.get('baseline_import_kwh', 0) or 0)
        change = float(row.get('consumption_variance_percent', 0) or 0)
        parts.append(
            f"{'Solar generation was' if solar else 'Used'} {actual:,.0f} kWh, {change:.1f}% above "
            f'the recent average for the same weekday ({typical:,.0f} kWh).'
        )
    if 'demand' in alert_type.lower() or alert_type == 'Demand':
        actual = float(row.get('peak_kva', 0) or 0)
        typical = float(row.get('baseline_peak_kva', 0) or 0)
        change = float(row.get('demand_variance_percent', 0) or 0)
        parts.append(
            f"{'Solar output' if solar else 'Demand'} reached {actual:,.1f} kVA, {change:.1f}% above "
            f'the recent average for the same weekday ({typical:,.1f} kVA).'
        )
    return ' '.join(parts) or 'The reading was outside its recent same-weekday range.'


def _suggested_check(row: dict[str, str]) -> str:
    alert_type = str(row.get('alert_type', ''))
    if row.get('area') == SOLAR_AREA:
        return 'Check weather first, then inverter output, outages, faults and curtailment.'
    if alert_type == 'Demand':
        return f"Check which equipment was running together at {_display_timestamp(row.get('peak_kva_time'))}."
    if alert_type == 'Consumption':
        return 'Check operating hours, shift activity, HVAC, lighting and equipment runtime for this date.'
    return (
        f"Check operating hours and equipment running together around "
        f"{_display_timestamp(row.get('peak_kva_time'))}."
    )


def _incident_time(row: dict[str, str]) -> str:
    alert_type = str(row.get('alert_type', ''))
    alert_type_lower = alert_type.lower()
    parts: list[str] = []
    if alert_type == 'Low solar generation':
        return f"Highest solar output: {_display_time(row.get('peak_kw_time'))}"
    if 'consumption' in alert_type_lower:
        parts.append(f"Highest load: {_display_time(row.get('peak_kw_time'))}")
    if 'demand' in alert_type_lower:
        parts.append(f"Highest demand: {_display_time(row.get('peak_kva_time'))}")
    return ' · '.join(parts) or 'Daily total only'


def _variance_summary(row: dict[str, str]) -> str:
    alert_type = str(row.get('alert_type', ''))
    alert_type_lower = alert_type.lower()
    parts: list[str] = []
    if alert_type == 'Low solar generation':
        return (
            f"Generation {float(row.get('consumption_variance_percent', 0) or 0):.1f}% "
            f"({float(row.get('consumption_variance_kwh', 0) or 0):,.0f} kWh)"
        )
    if 'consumption' in alert_type_lower:
        parts.append(
            f"Use +{float(row.get('consumption_variance_percent', 0) or 0):.1f}% "
            f"(+{float(row.get('consumption_variance_kwh', 0) or 0):,.0f} kWh)"
        )
    if 'demand' in alert_type_lower:
        parts.append(
            f"Demand +{float(row.get('demand_variance_percent', 0) or 0):.1f}% "
            f"(+{float(row.get('demand_variance_kva', 0) or 0):,.1f} kVA)"
        )
    return ' · '.join(parts) or 'Outside recent range'


def _alerts(view: DashboardView) -> None:
    warehouse_incidents = [
        row for row in view.spikes if row.get('area') != SOLAR_AREA
    ]
    solar_warnings = [
        row for row in view.spikes if row.get('area') == SOLAR_AREA
    ]
    solar_positive = list(view.solar_positive_events)
    negative_signals = [*warehouse_incidents, *solar_warnings]
    counts = Counter(row['alert_level'] for row in negative_signals)
    with ui.row().classes('plain-note w-full items-start no-wrap gap-3'):
        ui.icon('rule', color='orange', size='22px')
        ui.label(
            f"This page covers {_scope_label(view.area)} from {_display_date(view.start_date)} to "
            f"{_display_date(view.end_date)}. High warehouse use or demand requires review. "
            'Low solar generation is a warning; higher solar generation is shown separately as a positive '
            'event that must be verified against the inverter and weather.'
        ).classes('text-sm')
    with ui.element('div').classes('incident-summary-grid w-full'):
        for label, value, detail in [
            ('Warehouse incidents', len(warehouse_incidents), 'Use or demand readings to review'),
            ('Urgent warnings', counts['Critical'], 'Warehouse or low-solar signals'),
            ('Low solar days', len(solar_warnings), 'Generation materially below usual'),
            ('Higher solar days', len(solar_positive), 'Positive events to verify'),
        ]:
            with ui.column().classes('balance-summary-item gap-1'):
                ui.label(label).classes('metric-label')
                ui.label(f'{value:,}').classes('balance-value')
                ui.label(detail).classes('metric-detail')

    if negative_signals:
        severity = {'Critical': 0, 'High': 1, 'Watch': 2}
        priority = sorted(
            negative_signals,
            key=lambda row: (
                severity.get(str(row.get('alert_level')), 9),
                -date.fromisoformat(str(row['date'])[:10]).toordinal(),
            ),
        )[0]
        with ui.card().classes('dashboard-card priority-investigation w-full shadow-none'):
            with ui.row().classes('w-full items-start no-wrap gap-3'):
                ui.icon(
                    'manage_search',
                    color='red' if priority['alert_level'] == 'Critical' else 'orange',
                    size='28px',
                )
                with ui.column().classes('gap-1 min-w-0'):
                    ui.label(
                        f"Start here: {LEVEL_LABELS.get(priority['alert_level'], priority['alert_level'])} · "
                        f"{priority['area']} · {_display_date(priority['date'])}"
                    ).classes('section-title')
                    ui.label(_incident_time(priority)).classes('muted-text text-xs font-bold')
                    ui.label(_plain_reason(priority)).classes('body-text text-sm leading-relaxed')
                    ui.label(f"First check: {_suggested_check(priority)}").classes(
                        'priority-action text-sm font-bold'
                    )

    if warehouse_incidents:
        with ui.element('div').classes('chart-grid w-full'):
            _chart_card(unusual_timeline_options(view), '400px')
            _chart_card(unusual_by_area_options(view), '400px')
        frequency = unusual_frequency_summary(view)
        with ui.row().classes('filter-summary w-full items-start no-wrap gap-2'):
            ui.icon('schedule', size='20px')
            ui.label(
                f"Most warehouse incidents occurred from {frequency['time_label']} "
                f"({int(frequency['time_count']):,}). Within a calendar month, "
                f"{frequency['month_label']} had the highest frequency "
                f"({int(frequency['month_count']):,}). "
                f"Peak-time analysis covers {int(frequency['timed_incidents']):,} of "
                f"{int(frequency['total_incidents']):,} warehouse incidents."
            ).classes('text-sm font-bold')
        with ui.element('div').classes('chart-grid w-full'):
            _chart_card(unusual_time_of_day_options(view), '410px')
            _chart_card(unusual_month_position_options(view), '410px')

    if view.area in {ALL_AREAS, SOLAR_AREA}:
        with ui.card().classes('dashboard-card w-full shadow-none'):
            with ui.row().classes('w-full items-start no-wrap gap-3'):
                ui.icon('solar_power', color='amber', size='28px')
                with ui.column().classes('gap-1 min-w-0'):
                    ui.label('Solar performance signals - kept separate').classes('section-title')
                    ui.label(
                        'Low generation is a warning because the system may be underperforming. '
                        'Higher generation is positive if the PNPSCADA reading agrees with the inverter '
                        'portal and expected weather conditions.'
                    ).classes('section-subtitle')
        if solar_warnings or solar_positive:
            _chart_card(solar_signal_options(view), '410px')
        with ui.column().classes('w-full gap-4'):
            with ui.card().classes('dashboard-card shadow-none'):
                ui.label('Low solar generation - investigate').classes('section-title')
                ui.label(
                    'Triggered only when complete daily generation is at least 30% and 30 kWh below '
                    'the recent same-weekday average.'
                ).classes('section-subtitle')
                warning_columns = [
                    {'name': 'date', 'label': 'Date', 'field': 'date', 'sortable': True},
                    {'name': 'generated', 'label': 'Generated', 'field': 'generated', 'align': 'right'},
                    {'name': 'usual', 'label': 'Recent average', 'field': 'usual', 'align': 'right'},
                    {'name': 'difference', 'label': 'Difference', 'field': 'difference', 'align': 'right'},
                    {'name': 'peak', 'label': 'Peak output', 'field': 'peak'},
                    {'name': 'review', 'label': 'Review', 'field': 'review'},
                    {'name': 'check', 'label': 'First check', 'field': 'check'},
                ]
                warning_rows = [
                    {
                        'id': row['spike_id'],
                        'date': _display_date(row['date']),
                        'generated': f"{float(row.get('import_kwh', 0) or 0):,.0f} kWh",
                        'usual': f"{float(row.get('baseline_import_kwh', 0) or 0):,.0f} kWh",
                        'difference': f"{float(row.get('consumption_variance_percent', 0) or 0):.1f}%",
                        'peak': f"{float(row.get('peak_kw', 0) or 0):,.1f} kW at {_display_time(row.get('peak_kw_time'))}",
                        'review': LEVEL_LABELS.get(row['alert_level'], row['alert_level']),
                        'check': _suggested_check(row),
                    }
                    for row in solar_warnings
                ]
                if warning_rows:
                    ui.table(
                        columns=warning_columns, rows=warning_rows, row_key='id', pagination=7
                    ).classes('w-full investigation-table solar-signal-table').props(
                        'flat wrap-cells separator=horizontal'
                    )
                else:
                    ui.label('No materially low solar-generation days were found.').classes(
                        'muted-text text-sm py-4'
                    )
            with ui.card().classes('dashboard-card shadow-none'):
                ui.label('Higher solar generation - positive if verified').classes('section-title')
                ui.label(
                    'These days produced materially more than the recent same-weekday average. '
                    'They are not usage alerts and are excluded from incident totals.'
                ).classes('section-subtitle')
                positive_columns = [
                    {'name': 'date', 'label': 'Date', 'field': 'date', 'sortable': True},
                    {'name': 'generated', 'label': 'Generated', 'field': 'generated', 'align': 'right'},
                    {'name': 'usual', 'label': 'Recent average', 'field': 'usual', 'align': 'right'},
                    {'name': 'difference', 'label': 'Above usual', 'field': 'difference', 'align': 'right'},
                    {'name': 'peak', 'label': 'Peak output', 'field': 'peak'},
                    {'name': 'status', 'label': 'Verification', 'field': 'status'},
                ]
                positive_rows = [
                    {
                        'id': row['event_id'],
                        'date': _display_date(row['date']),
                        'generated': f"{float(row.get('import_kwh', 0) or 0):,.0f} kWh",
                        'usual': f"{float(row.get('baseline_import_kwh', 0) or 0):,.0f} kWh",
                        'difference': f"+{float(row.get('consumption_variance_percent', 0) or 0):.1f}%",
                        'peak': f"{float(row.get('peak_kw', 0) or 0):,.1f} kW at {_display_time(row.get('peak_kw_time'))}",
                        'status': 'Verify against inverter and weather',
                    }
                    for row in solar_positive
                ]
                if positive_rows:
                    ui.table(
                        columns=positive_columns, rows=positive_rows, row_key='id', pagination=7
                    ).classes('w-full investigation-table solar-signal-table solar-positive-table').props(
                        'flat wrap-cells separator=horizontal'
                    )
                else:
                    ui.label('No materially higher-than-usual solar days were found.').classes(
                        'muted-text text-sm py-4'
                    )
    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('Warehouse incident investigation register').classes('section-title')
        ui.label(
            'Use the exact date and peak time to match the signal to shifts, equipment operation, maintenance, faults or weather.'
        ).classes('section-subtitle')
        columns = [
            {'name': 'date', 'label': 'Date', 'field': 'date', 'sortable': True},
            {'name': 'time', 'label': 'Peak time(s)', 'field': 'time'},
            {'name': 'area', 'label': 'Area', 'field': 'area', 'sortable': True},
            {'name': 'review', 'label': 'Review level', 'field': 'review', 'sortable': True},
            {'name': 'type', 'label': 'What changed', 'field': 'type', 'sortable': True},
            {'name': 'difference', 'label': 'Difference vs normal', 'field': 'difference'},
            {'name': 'reason', 'label': 'What happened in the data', 'field': 'reason'},
            {'name': 'check', 'label': 'What to do next', 'field': 'check'},
            {'name': 'status', 'label': 'Status', 'field': 'status', 'sortable': True},
        ]
        type_labels = {
            'Consumption and demand': 'Use and demand',
            'Consumption': 'Electricity use',
            'Demand': 'Highest demand',
        }
        rows = [
            {
                'spike_id': row['spike_id'],
                'date': _display_date(row['date']),
                'time': _incident_time(row),
                'area': row['area'],
                'review': LEVEL_LABELS.get(row['alert_level'], row['alert_level']),
                'type': type_labels.get(row['alert_type'], row['alert_type']),
                'difference': _variance_summary(row),
                'reason': _plain_reason(row),
                'check': _suggested_check(row),
                'status': 'Needs review' if row.get('investigation_status') == 'Open' else row.get('investigation_status', ''),
            }
            for row in warehouse_incidents
        ]
        if rows:
            ui.table(columns=columns, rows=rows, row_key='spike_id', pagination=10).classes(
                'w-full investigation-table'
            ).props(
                'flat wrap-cells separator=horizontal'
            )
        else:
            with ui.row().classes('w-full items-center justify-center py-12 gap-3'):
                ui.icon('verified', color='teal', size='30px')
                ui.label('No unusual warehouse use or demand was found in this selection.').classes('body-text text-base')


def _ai_conversation_panel(
    messages: list[dict[str, str]],
    busy: bool,
) -> None:
    with ui.column().classes('ai-conversation w-full gap-3').props(
        'id=ai-conversation-scroll'
    ):
        if not messages:
            with ui.row().classes('ai-empty-state w-full items-center justify-center gap-3'):
                ui.icon('forum', color='primary', size='30px')
                with ui.column().classes('gap-0'):
                    ui.label('Start with a suggested question or type your own.').classes(
                        'body-text text-sm font-bold'
                    )
                    ui.label(
                        'Ask naturally—the assistant can calculate, compare, infer patterns and recommend actions from the app data.'
                    ).classes('muted-text text-xs')
        for message in messages:
            role = message.get('role', 'assistant')
            with ui.element('div').classes(
                f"ai-message {'user' if role == 'user' else 'assistant'}"
            ):
                with ui.row().classes('items-start no-wrap gap-3'):
                    ui.icon(
                        'person' if role == 'user' else 'smart_toy',
                        size='20px',
                    ).classes('ai-message-icon')
                    with ui.column().classes('gap-1 min-w-0'):
                        ui.label(
                            'You' if role == 'user' else message.get('source', 'Electricity AI')
                        ).classes('ai-message-name')
                        if role == 'user':
                            ui.label(message.get('text', '')).classes(
                                'ai-message-text ai-user-text'
                            )
                        else:
                            ui.markdown(message.get('text', '')).classes(
                                'ai-message-text ai-message-markdown'
                            )
        if busy:
            with ui.row().classes('ai-thinking items-center gap-2'):
                ui.spinner('dots', size='24px', color='orange')
                ui.label('Checking the local readings and preparing the answer…').classes(
                    'muted-text text-xs'
                )


def _ai_chat_panel(
    view: DashboardView,
    messages: list[dict[str, str]],
    busy: bool,
    on_ask: Callable[..., object],
    on_clear: Callable[[], object],
    messages_panel: Callable[[], None] | None = None,
) -> None:
    with ui.card().classes('dashboard-card ai-chat-card shadow-none'):
        with ui.row().classes('w-full items-center justify-between gap-2'):
            with ui.column().classes('gap-0'):
                ui.label('Electricity analysis conversation').classes('section-title')
            ui.label(
                f'Active scope: {view.area} | {_display_date(view.start_date)} to {_display_date(view.end_date)}'
            ).classes('section-subtitle')
        if messages:
            ui.button('Clear', icon='delete_sweep', on_click=on_clear).props(
                'flat dense no-caps'
            ).classes('toolbar-action')

        if messages_panel is None:
            _ai_conversation_panel(messages, busy)
        else:
            messages_panel()

        question_input = ui.textarea(
            label='Ask about any part of the app',
            placeholder='Name a sidebar page, ask a follow-up, or specify a warehouse and date range…',
        ).props('outlined rows=3 maxlength=1000').classes('w-full mt-3 ai-question-input')

        async def submit_question() -> None:
            event_client = context.client
            question = str(question_input.value or '')
            page_scroll_y: float | None = None
            try:
                with event_client:
                    value = await ui.run_javascript('return window.scrollY;', timeout=2.0)
                page_scroll_y = float(value)
            except Exception:
                pass
            question_input.value = ''
            question_input.update()
            result = on_ask(question, page_scroll_y)
            if hasattr(result, '__await__'):
                await result

        with ui.row().classes('w-full items-center justify-between gap-3 flex-wrap'):
            ui.label(
                'The answer will state its date range and distinguish evidence from possible causes.'
            ).classes('muted-text text-xs')
        ui.button(
            'Ask AI Hub',
            icon='send',
            on_click=submit_question,
        ).props(f"flat no-caps {'loading disable' if busy else ''}").classes(
            'primary-action'
        )


def _ai_hub(
    view: DashboardView,
    account: MicrosoftAccountStatus,
    messages: list[dict[str, str]],
    busy: bool,
    connection_note: str,
    on_sign_in: Callable[[], object],
    on_sign_out: Callable[[], object],
    on_ask: Callable[..., object],
    on_clear: Callable[[], object],
    chat_panel: Callable[[], None] | None = None,
) -> None:
    copilot_connected = bool(
        account.signed_in and account.token_available and not account.missing_scopes
    )
    with ui.card().classes('dashboard-card ai-hero w-full shadow-none'):
        with ui.row().classes('w-full items-start justify-between gap-4 flex-wrap'):
            with ui.column().classes('gap-1'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('smart_toy', size='28px', color='primary')
                    ui.label('Connect Logistics AI Hub').classes('section-title text-lg')
                    ui.badge('Microsoft Copilot preview', color='orange').props('outline')
                ui.label(
                    'Ask across every sidebar page and the complete stored database. Dashboard filters do '
                    'not restrict the AI Hub; dates or areas named in your question narrow that answer. The app supplies '
                    'the matching readings, tariffs, costs, solar, comparisons and operational evidence; '
                    'Microsoft 365 Copilot then analyses them together conversationally.'
                ).classes('section-subtitle max-w-3xl')
            with ui.row().classes('items-center gap-2'):
                if copilot_connected:
                    with ui.row().classes('ai-account items-center gap-2'):
                        ui.icon('verified_user', color='teal', size='20px')
                        ui.label(account.display_name or account.username).classes(
                            'body-text text-xs font-bold'
                        )
                    ui.button('Sign out', icon='logout', on_click=on_sign_out).props(
                        'flat no-caps'
                    ).classes('toolbar-action')
                elif account.signed_in:
                    ui.button(
                        'Reconnect Microsoft', icon='sync', on_click=on_sign_in
                    ).props(f"flat no-caps {'loading disable' if busy else ''}").classes(
                        'primary-action'
                    )
                    ui.button('Sign out', icon='logout', on_click=on_sign_out).props(
                        'flat no-caps'
                    ).classes('toolbar-action')
                else:
                    ui.button(
                        'Sign in with Microsoft', icon='login', on_click=on_sign_in
                    ).props(f"flat no-caps {'loading disable' if busy else ''}").classes(
                        'primary-action'
                    )

        status_title = (
            'Microsoft Copilot connected'
            if copilot_connected else
            'Microsoft reconnect required'
            if account.signed_in else
            'Microsoft sign-in required'
        )
        status_text = (
            f'Signed in as {account.username}. Read-only AI access covers all meter areas from '
            f'{_display_date(view.start_date)} to {_display_date(view.end_date)}, independent of dashboard filters.'
            if copilot_connected else
            f"The cached token is missing: {', '.join(account.missing_scopes)}. Reconnect to issue a new approved token."
            if account.signed_in and account.missing_scopes else
            'Verified local answers work now. Sign in once to issue the Microsoft token approved by the administrator.'
        )
        with ui.row().classes(
            f"ai-status w-full items-start gap-3 {'connected' if copilot_connected else 'pending'}"
        ):
            ui.icon('check_circle' if copilot_connected else 'login', size='22px')
            with ui.column().classes('gap-0 min-w-0'):
                ui.label(status_title).classes('body-text text-sm font-bold')
                ui.label(status_text).classes('muted-text text-xs')
                if connection_note:
                    ui.label(connection_note).classes('ai-connection-note text-xs')
                if account.warning:
                    ui.label(account.warning).classes('ai-connection-note text-xs')

    suggestions = (
        'Which area presented the highest electricity risk in the last complete week, and why?',
        'Which area used the most electricity in the selected dates?',
        'What unusual usage needs investigation and at what time?',
        'How could we move suitable usage away from peak tariff hours?',
        'What did solar generate and when was its highest output?',
        'Explain the estimated electricity cost and main cost drivers for this period.',
        'Use Supply & Demand and Solar Performance together to explain how much demand solar covered.',
        'Compare both Solar Investment proposals with actual PNPSCADA performance and identify the trade-offs.',
    )
    with ui.element('div').classes('ai-layout w-full'):
        if chat_panel is None:
            _ai_chat_panel(view, messages, busy, on_ask, on_clear)
        else:
            chat_panel()

        with ui.column().classes('gap-4 min-w-0'):
            with ui.card().classes('dashboard-card shadow-none'):
                ui.label('Suggested questions').classes('section-title')
                ui.label('Each independent question starts with the full database; named dates and areas focus the answer.').classes(
                    'section-subtitle'
                )
                with ui.column().classes('w-full gap-2'):
                    for question in suggestions:
                        with ui.button(
                            on_click=lambda value=question: on_ask(value)
                        ).props('flat no-caps').classes('ai-suggestion w-full'):
                            ui.icon('arrow_forward', size='18px')
                            ui.label(question).classes('text-left')
            with ui.card().classes('dashboard-card shadow-none'):
                ui.label('What this assistant can use').classes('section-title')
                for icon, title, detail in (
                    ('dashboard', 'Every sidebar page', 'Overview, trends, supply and demand, solar, investment, comparisons, cost, alerts, agent communications, reports and data status.'),
                    ('database', 'PNPSCADA readings', 'Complete half-hour and daily meter records.'),
                    ('speed', 'Load and demand', 'Exact kW and kVA peaks with area and time.'),
                    ('schedule', 'Tariff periods', 'Peak, standard and off-peak classification.'),
                    ('warning', 'Unusual usage', 'Matching-weekday baselines and investigation signals.'),
                    ('payments', 'Cost calculations', 'Current verified tariff and solar-savings logic.'),
                    ('psychology', 'Conversational analysis', 'True follow-up questions retain the AI discussion scope; dashboard filters never replace it.'),
                ):
                    with ui.row().classes('ai-capability items-start no-wrap gap-3'):
                        ui.icon(icon, color='primary', size='20px')
                        with ui.column().classes('gap-0'):
                            ui.label(title).classes('body-text text-xs font-bold')
                            ui.label(detail).classes('muted-text text-xs')
                ui.separator().classes('my-2')
                ui.label('AI model routing').classes('body-text text-xs font-bold')
                ui.label(
                    'The Microsoft 365 Copilot API selects its model automatically. '
                    'The Claude model picker available inside Microsoft Copilot is not exposed to this API.'
                ).classes('muted-text text-xs')
                ui.separator().classes('my-2')
                ui.label(
                    'It can rank likely causes and recommend checks; shift, equipment and maintenance records are used to confirm what physically happened.'
                ).classes('plain-note text-xs')


def _agent_center(
    view: DashboardView,
    account: MicrosoftAccountStatus,
    state: PageState,
    history: tuple[object, ...],
    on_select_alert: Callable[[str], object],
    on_prepare: Callable[[], object],
    on_search_directory: Callable[[str], object],
    on_add_recipient: Callable[[str], object],
    on_save_preferences: Callable[[], object],
    on_create_outlook_draft: Callable[[], object],
    on_send: Callable[[], object],
    on_sign_in: Callable[[], object],
    on_device_sign_in: Callable[[], object],
) -> None:
    missing_agent_scopes = tuple(
        scope for scope in account.missing_scopes if scope in MAIL_AGENT_SCOPES
    )
    mail_connected = bool(
        account.signed_in and account.token_available and not missing_agent_scopes
    )
    alert_rows = [
        row for row in view.spikes
        if str(row.get('alert_level') or '') in {'Critical', 'High', 'Watch'}
    ]
    alert_options: dict[str, str] = {
        'period-summary': (
            f'Selected-period summary · {_display_date(view.start_date)} to '
            f'{_display_date(view.end_date)}'
        )
    }
    for index, row in enumerate(alert_rows, start=1):
        key = str(row.get('spike_id') or f'alert-{index}')
        alert_options[key] = (
            f"{row.get('alert_level')} · {row.get('area')} · "
            f"{_display_date(row.get('date'))} · {row.get('alert_type') or 'Usage'}"
        )
    if state.agent_alert_id not in alert_options:
        state.agent_alert_id = 'period-summary'

    with ui.card().classes('dashboard-card agent-hero w-full shadow-none'):
        with ui.row().classes('w-full items-start justify-between gap-4 flex-wrap'):
            with ui.column().classes('gap-1'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('support_agent', color='primary', size='28px')
                    ui.label('Operational Alert Agent').classes('section-title text-lg')
                    ui.badge('Review before send', color='orange').props('outline')
                ui.label(
                    'Prepare grounded internal emails from selected-period summaries or unusual-usage alerts. '
                    'Copilot can improve the wording; Microsoft Graph creates the Outlook draft or sends the approved message.'
                ).classes('section-subtitle max-w-4xl')
            if mail_connected:
                with ui.row().classes('ai-account items-center gap-2'):
                    ui.icon('verified_user', color='teal', size='20px')
                    ui.label(account.display_name or account.username).classes(
                        'body-text text-xs font-bold'
                    )
            else:
                ui.button(
                    'Reconnect Microsoft' if account.signed_in else 'Sign in with Microsoft',
                    icon='login', on_click=on_sign_in
                ).props('flat no-caps').classes('primary-action')
                ui.button(
                    'Use sign-in code', icon='vpn_key', on_click=on_device_sign_in
                ).props('flat no-caps').classes('toolbar-action')
        with ui.row().classes(
            f"ai-status w-full items-start gap-3 {'connected' if mail_connected else 'pending'}"
        ):
            ui.icon('check_circle' if mail_connected else 'login', size='22px')
            with ui.column().classes('gap-0'):
                ui.label(
                    'Microsoft mail connection active'
                    if mail_connected else
                    'Microsoft reconnect required'
                    if account.signed_in else
                    'Microsoft sign-in required'
                ).classes('body-text text-sm font-bold')
                ui.label(
                    'Recipient search, Outlook drafts and reviewed email sending are available.'
                    if mail_connected else
                    f"The cached session is missing: {', '.join(missing_agent_scopes)}. Reconnect to issue a new token."
                    if account.signed_in and missing_agent_scopes else
                    'Admin approval does not update an existing PC session automatically. Sign in once to issue the approved token.'
                ).classes('muted-text text-xs')
                if state.agent_note:
                    ui.label(state.agent_note).classes('ai-connection-note text-xs')
        if state.microsoft_device_sign_in is not None:
            device_sign_in = state.microsoft_device_sign_in
            with ui.row().classes('device-sign-in w-full items-center gap-4 flex-wrap'):
                with ui.column().classes('gap-0'):
                    ui.label('MICROSOFT SIGN-IN CODE').classes('metric-label')
                    ui.label(device_sign_in.user_code).classes('device-code')
                with ui.column().classes('gap-1'):
                    ui.label(
                        'Open the Microsoft sign-in page, enter this code and use your Connect Logistics account.'
                    ).classes('body-text text-sm font-bold')
                    ui.link(
                        'Open microsoft.com/devicelogin',
                        device_sign_in.verification_uri,
                        new_tab=True,
                    ).classes('data-link text-sm')

    with ui.card().classes('dashboard-card w-full shadow-none'):
        with ui.row().classes('w-full items-start justify-between gap-3 flex-wrap'):
            with ui.column().classes('gap-1'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('automation', color='teal', size='24px')
                    ui.label('Autonomous monitoring').classes('section-title')
                    ui.badge(
                        'Active' if mail_connected else 'Microsoft sign-in required',
                        color='teal' if mail_connected else 'orange',
                    ).props('outline')
                ui.label('Successful meter refresh confirmation').classes(
                    'body-text text-sm font-bold'
                )
                ui.label(
                    f'After Update meter data finishes successfully, the agent automatically sends '
                    f'one confirmation to {AUTONOMOUS_REFRESH_RECIPIENT}. Failed refreshes, page '
                    'loads and normal dashboard navigation do not trigger an email.'
                ).classes('section-subtitle max-w-4xl')
            with ui.column().classes('gap-0 items-end'):
                ui.label('TRIGGER').classes('metric-label')
                ui.label('Successful data refresh').classes('body-text text-xs font-bold')
                ui.label('Recorded in communication history').classes('muted-text text-xs')

    action_controls: dict[str, object] = {}

    def update_action_controls() -> None:
        draft_ready = bool(
            not state.agent_busy
            and state.agent_to_text.strip()
            and state.agent_subject.strip()
            and state.agent_body.strip()
        )
        send_ready = draft_ready and state.agent_reviewed
        create_control = action_controls.get('create')
        send_control = action_controls.get('send')
        if create_control is not None:
            create_control.enable() if draft_ready else create_control.disable()
        if send_control is not None:
            send_control.enable() if send_ready else send_control.disable()

    def set_value(name: str, value: object) -> None:
        setattr(state, name, value)
        if name in {
            'agent_to_text', 'agent_cc_text', 'agent_sender_mailbox',
            'agent_subject', 'agent_body', 'agent_attach_pdf',
        }:
            state.agent_reviewed = False
            review_control = action_controls.get('review')
            if review_control is not None and bool(review_control.value):
                review_control.value = False
                review_control.update()
        update_action_controls()

    with ui.element('div').classes('agent-layout w-full'):
        with ui.column().classes('gap-4 min-w-0'):
            with ui.card().classes('dashboard-card shadow-none'):
                ui.label('1. Choose what to communicate').classes('section-title')
                ui.label(
                    f'{len(alert_rows)} alert candidate(s) are available for the active filters. '
                    'The selected-period summary is always available.'
                ).classes('section-subtitle')
                ui.select(
                    alert_options,
                    value=state.agent_alert_id,
                    label='Alert or report scope',
                    on_change=lambda event: on_select_alert(str(event.value)),
                ).props('outlined options-dense').classes('w-full')
                with ui.row().classes('w-full items-center gap-2 mt-2'):
                    ui.button(
                        'Prepare grounded draft',
                        icon='auto_awesome',
                        on_click=on_prepare,
                    ).props(f"flat no-caps {'loading disable' if state.agent_busy else ''}").classes(
                        'primary-action'
                    )
                    ui.label(
                        'Copilot is used when the renewed token is available; otherwise the verified local template is used.'
                    ).classes('muted-text text-xs')

            with ui.card().classes('dashboard-card shadow-none'):
                ui.label('2. Select approved recipients').classes('section-title')
                ui.label(
                    'Use semicolons between addresses. Saved recipients are stored only on this PC.'
                ).classes('section-subtitle')
                ui.input(
                    'To',
                    value=state.agent_to_text,
                    on_change=lambda event: set_value('agent_to_text', str(event.value or '')),
                ).props('outlined').classes('w-full')
                ui.input(
                    'Cc (optional)',
                    value=state.agent_cc_text,
                    on_change=lambda event: set_value('agent_cc_text', str(event.value or '')),
                ).props('outlined').classes('w-full')
                ui.input(
                    'Shared sender mailbox (optional)',
                    value=state.agent_sender_mailbox,
                    on_change=lambda event: set_value('agent_sender_mailbox', str(event.value or '')),
                ).props('outlined').classes('w-full')
                with ui.row().classes('w-full items-end gap-2 flex-wrap'):
                    directory_query = ui.input(
                        'Search company recipients',
                        placeholder='Enter a name or email address',
                    ).props('outlined').classes('agent-directory-search')
                    ui.button(
                        'Search',
                        icon='person_search',
                        on_click=lambda: on_search_directory(str(directory_query.value or '')),
                    ).props(f"flat no-caps {'disable' if state.agent_busy else ''}").classes('toolbar-action')
                    ui.button(
                        'Save recipients',
                        icon='save',
                        on_click=on_save_preferences,
                    ).props('flat no-caps').classes('toolbar-action')
                if state.agent_directory_results:
                    with ui.column().classes('w-full gap-1 mt-2'):
                        for person in state.agent_directory_results:
                            with ui.button(
                                on_click=lambda email=person.email: on_add_recipient(email)
                            ).props('flat no-caps').classes('agent-person-result w-full'):
                                ui.icon('person_add', size='18px')
                                with ui.column().classes('gap-0 items-start'):
                                    ui.label(person.display_name).classes('body-text text-xs font-bold')
                                    ui.label(person.email).classes('muted-text text-xs')

        with ui.card().classes('dashboard-card agent-draft-card shadow-none'):
            ui.label('3. Review the email').classes('section-title')
            ui.label(
                f"Draft source: {state.agent_draft_source or 'No draft prepared'}"
            ).classes('section-subtitle')
            ui.input(
                'Subject',
                value=state.agent_subject,
                on_change=lambda event: set_value('agent_subject', str(event.value or '')),
            ).props('outlined').classes('w-full')
            ui.textarea(
                'Email body',
                value=state.agent_body,
                on_change=lambda event: set_value('agent_body', str(event.value or '')),
            ).props('outlined rows=18').classes('w-full agent-email-body')
            ui.checkbox(
                'Attach a PDF report for the active filters',
                value=state.agent_attach_pdf,
                on_change=lambda event: set_value('agent_attach_pdf', bool(event.value)),
            ).classes('body-text text-sm')
            review_checkbox = ui.checkbox(
                'I have reviewed the recipients, evidence, subject and email body',
                value=state.agent_reviewed,
                on_change=lambda event: set_value('agent_reviewed', bool(event.value)),
            ).classes('agent-review-check body-text text-sm')

            async def confirm_send() -> None:
                send_dialog.close()
                result = on_send()
                if hasattr(result, '__await__'):
                    await result

            with ui.dialog() as send_dialog, ui.card().classes('app-card w-full max-w-lg p-5'):
                ui.label('Send this approved email?').classes('section-title')
                ui.label(
                    'Microsoft Graph will send the message immediately and save it in Sent Items. '
                    'The action will be recorded in the local communication history.'
                ).classes('section-subtitle')
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Cancel', on_click=send_dialog.close).props('flat no-caps').classes('toolbar-action')
                    ui.button('Send now', icon='send', on_click=confirm_send).props('flat no-caps').classes('primary-action')

            with ui.row().classes('w-full items-center gap-2 flex-wrap mt-2'):
                create_button = ui.button(
                    'Save to Outlook drafts',
                    icon='drafts',
                    on_click=on_create_outlook_draft,
                ).props(f"flat no-caps {'loading disable' if state.agent_busy else ''}").classes('toolbar-action')
                send_button = ui.button(
                    'Send approved email',
                    icon='send',
                    on_click=send_dialog.open,
                ).props(f"flat no-caps {'loading disable' if state.agent_busy else ''}").classes('primary-action')
                action_controls.update({
                    'review': review_checkbox,
                    'create': create_button,
                    'send': send_button,
                })
                update_action_controls()

    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('Communication history on this PC').classes('section-title')
        ui.label(
            'A metadata-only audit is kept locally; email bodies and Microsoft tokens are not written to this history.'
        ).classes('section-subtitle')
        history_rows = [
            {
                'completed_at': _display_timestamp(getattr(row, 'completed_at', '')),
                'action': getattr(row, 'action', ''),
                'subject': getattr(row, 'subject', ''),
                'to': '; '.join(getattr(row, 'to_recipients', ())),
                'sender': getattr(row, 'sender_mailbox', '') or 'Signed-in employee',
                'attachments': '; '.join(getattr(row, 'attachment_names', ())) or '—',
            }
            for row in history
        ]
        columns = [
            {'name': 'completed_at', 'label': 'Date and time', 'field': 'completed_at', 'align': 'left'},
            {'name': 'action', 'label': 'Action', 'field': 'action', 'align': 'left'},
            {'name': 'subject', 'label': 'Subject', 'field': 'subject', 'align': 'left'},
            {'name': 'to', 'label': 'Recipients', 'field': 'to', 'align': 'left'},
            {'name': 'sender', 'label': 'Sender', 'field': 'sender', 'align': 'left'},
            {'name': 'attachments', 'label': 'Attachments', 'field': 'attachments', 'align': 'left'},
        ]
        if history_rows:
            ui.table(columns=columns, rows=history_rows, pagination=8).props(
                'flat wrap-cells separator=horizontal'
            ).classes('w-full investigation-table')
        else:
            ui.label('No Outlook drafts or agent emails have been recorded yet.').classes(
                'muted-text text-sm py-4'
            )


def _data_panel(
    dataset: DashboardDataset,
    credential_available: bool,
    on_configure_credentials: Callable[[], None],
) -> None:
    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            database = dataset.manifest['database']
            ui.label('Local history database').classes('section-title')
            ui.label(f"Database: {Path(str(database['path'])).name}").classes('body-text text-sm')
            ui.label(f'Stored through {_display_date(dataset.last_date)}').classes('body-text text-sm')
            ui.label(
                f'Closed-day analytics through {_display_date(_latest_closed_date(dataset))}'
            ).classes('body-text text-sm')
            ui.label(
                f'Coverage: {_display_date(dataset.first_date)} to {_display_date(dataset.last_date)}'
            ).classes('muted-text text-sm')
            ui.label(
                f"Half-hour readings loaded: {int(dataset.manifest['row_counts']['interval_readings.csv']):,}"
            ).classes('muted-text text-sm')
            ui.label(f"Latest stored reading: {_display_timestamp(database['latest_timestamp'])}").classes('muted-text text-sm')
            ui.label(f"Completed incremental updates: {int(database['sync_count']):,}").classes('muted-text text-sm')
            with ui.row().classes('items-center gap-2 mt-2'):
                ui.icon('key', color='teal' if credential_available else 'amber')
                ui.label(
                    'The shared PNPSCADA login is ready for meter updates on this PC.'
                    if credential_available else 'The shared PNPSCADA login still needs to be saved on this PC.'
                ).classes('muted-text text-sm')
            ui.button('Set up shared portal login', icon='manage_accounts', on_click=on_configure_credentials).props(
                'flat no-caps'
            ).classes('toolbar-action mt-3')
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Download records').classes('section-title')
            ui.label('CSV files can be used for weekly reviews, investigation notes and owner reports.').classes('section-subtitle')
            for filename, label, icon in [
                ('daily_meter_record.csv', 'Daily meter readings', 'today'),
                ('weekly_meter_record.csv', 'Weekly meter readings', 'date_range'),
                ('spike_register.csv', 'Unusual usage register', 'warning'),
                ('solar_positive_register.csv', 'Higher solar generation register', 'wb_sunny'),
                ('interval_readings.csv', 'Half-hour readings', 'query_stats'),
            ]:
                path = dataset.run_dir / filename
                if path.exists():
                    ui.button(label, icon=icon, on_click=lambda p=path: ui.download(p)).props(
                        'flat no-caps'
                    ).classes('w-full justify-start body-text')

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('What the electricity terms mean').classes('section-title')
            ui.markdown(
                '- **Electricity used (kWh):** energy recorded on the meter’s incoming P1 channel. '
                '“Incoming” means electricity flowing into that metered area; it does not mean imported from another country.\n'
                '- **kW:** the working electrical load at a point in time.\n'
                '- **kVA:** the total demand placed on the electrical supply at a point in time.\n'
                '- **Electricity sent back (kWh):** energy recorded separately on the outgoing P2 channel.'
            ).classes('muted-text text-sm')
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('How unusual readings are found').classes('section-title')
            ui.markdown(
                '- Each day is compared with the previous 3–4 readings for the same weekday.\n'
                '- Use is flagged when it is at least **20% and 50 kWh** higher than that recent average.\n'
                '- Demand is flagged when it is at least **15% and 5 kVA** higher than that recent average.\n'
                '- A flag identifies **where and when** to investigate. Staff records are still needed to confirm **why**.'
            ).classes('muted-text text-sm')
            ui.separator().classes('my-2')
            ui.label(
                'When Update meter data is selected, the app starts from the latest stored day and '
                'updates through today. The latest day is safely re-read because PNPSCADA accepts '
                'date windows; duplicate half-hour readings are replaced in the database.'
            ).classes('muted-text text-sm')


def _scope_label(area: str) -> str:
    return 'all meter areas' if area == ALL_AREAS else area


def _report_scope_label(area: str) -> str:
    return 'all warehouse areas; solar generation shown separately' if area == ALL_AREAS else area


def _filter_summary(view: DashboardView, scope_override: str | None = None) -> None:
    scope = scope_override or (
        'all warehouse areas; solar generation shown separately'
        if view.area == ALL_AREAS else _scope_label(view.area)
    )
    if scope_override is None and view.area == SOLAR_AREA:
        scope = 'solar generation only'
    with ui.row().classes('filter-summary w-full items-center gap-2'):
        ui.icon('filter_alt', size='18px')
        ui.label(
            f"Active filters: {scope} | "
            f"{_display_date(view.start_date)} to {_display_date(view.end_date)}"
        ).classes('text-sm font-bold')


def _cost_center(
    analysis: CostAnalysis,
    tariff_status: TariffCheckResult | None,
    comparison_type: str,
    month_options: dict[str, str],
    selected_month: str,
    compare_month: str,
    on_comparison_type_change,
    on_apply_month_comparison,
    on_apply_current_month_comparison,
    on_apply_custom_comparison,
    on_apply_previous_comparison,
    on_check_tariff,
) -> None:
    metrics = analysis.metrics
    current_days = int(metrics['current_days'])
    comparison_days = int(metrics['comparison_days'])
    comparable_metric = (
        float(metrics['cost_change_percent'])
        if current_days == comparison_days and metrics['cost_change_percent'] is not None
        else float(metrics['daily_cost_change_percent'])
        if metrics['daily_cost_change_percent'] is not None
        else None
    )
    def comparison_change_text(value: object) -> str:
        if value is None:
            return 'No valid percentage'
        number = float(value)
        direction = 'higher' if number > 0 else 'lower' if number < 0 else 'unchanged'
        return f'{abs(number):.1f}% {direction}'

    current_month_change = metrics.get('forecast_mtd_cost_change_percent')
    current_month_usage_change = metrics.get('forecast_mtd_usage_change_percent')
    projected_previous_month_change = metrics.get(
        'forecast_vs_previous_full_cost_percent'
    )
    comparable_days = int(metrics.get('forecast_comparable_days') or 0)
    network_surcharge_active = bool(metrics.get('forecast_network_surcharge_active'))
    today = date.today()
    current_scale = tariff_for_day(tariff_repository.rates(), today)
    current_ctou = ctou_tariff_for_day(tariff_repository.ctou_rates(), today)
    current_season = 'High season' if today.month in {6, 7, 8} else 'Low season'

    with ui.card().classes('dashboard-card w-full shadow-none'):
        with ui.row().classes('w-full items-start justify-between flex-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Cost Centre').classes('section-title')
                ui.label(
                    'VAT-inclusive eThekwini electricity estimate for Gate 4, 265 Sydney Road, KwaKhangela, Durban.'
                ).classes('section-subtitle')
            ui.button(
                'Check official tariff now', icon='published_with_changes',
                on_click=on_check_tariff,
            ).props('flat no-caps').classes('toolbar-action')
        ui.select(
            list(COST_COMPARISON_OPTIONS),
            value=comparison_type,
            label='Comparison method',
            on_change=lambda event: on_comparison_type_change(str(event.value)),
        ).props('outlined dense options-dense').classes('w-64 mt-3')
        if comparison_type == 'Current month vs previous month':
            with ui.row().classes('w-full items-center justify-between flex-wrap gap-3 mt-3'):
                ui.label(
                    'Uses the current month through the latest closed meter day and compares it with the same day range in the previous month.'
                ).classes('section-subtitle mb-0')
                ui.button(
                    'Use current month', icon='update',
                    on_click=on_apply_current_month_comparison,
                ).props('flat no-caps').classes('primary-action')
        elif comparison_type == 'Past month vs past month':
            with ui.element('div').classes('comparison-range-grid cost-comparison-grid w-full mt-3'):
                selected_month_input = ui.select(
                    month_options,
                    value=selected_month,
                    label='Selected month',
                ).props('outlined dense options-dense')
                compare_month_input = ui.select(
                    month_options,
                    value=compare_month,
                    label='Compare with month',
                ).props('outlined dense options-dense')
                ui.button(
                    'Compare full months', icon='compare_arrows',
                    on_click=lambda: on_apply_month_comparison(
                        str(selected_month_input.value),
                        str(compare_month_input.value),
                    ),
                ).props('flat no-caps').classes('primary-action')
        elif comparison_type == 'Custom date ranges':
            with ui.element('div').classes('comparison-range-grid w-full mt-3'):
                selected_start_input = ui.input(
                    'Selected from', value=analysis.start_date.isoformat()
                ).props('type=date outlined dense')
                selected_end_input = ui.input(
                    'Selected to', value=analysis.end_date.isoformat()
                ).props('type=date outlined dense')
                comparison_start_input = ui.input(
                    'Compare from', value=analysis.comparison_start.isoformat()
                ).props('type=date outlined dense')
                comparison_end_input = ui.input(
                    'Compare to', value=analysis.comparison_end.isoformat()
                ).props('type=date outlined dense')
                ui.button(
                    'Apply comparison', icon='compare_arrows',
                    on_click=lambda: on_apply_custom_comparison(
                        str(selected_start_input.value),
                        str(selected_end_input.value),
                        str(comparison_start_input.value),
                        str(comparison_end_input.value),
                    ),
                ).props('flat no-caps').classes('primary-action')
        else:
            with ui.row().classes('w-full items-center justify-between flex-wrap gap-3 mt-3'):
                ui.label(
                    'Compares the selected dates with the immediately preceding period of the same length.'
                ).classes('section-subtitle mb-0')
                ui.button(
                    'Use previous matching period', icon='compare_arrows',
                    on_click=on_apply_previous_comparison,
                ).props('flat no-caps').classes('primary-action')
        ui.label(
            f'Selected: {_display_date(analysis.start_date)} to {_display_date(analysis.end_date)} | '
            f'Comparison: {_display_date(analysis.comparison_start)} to {_display_date(analysis.comparison_end)}'
        ).classes('muted-text text-xs mt-2')

    with ui.element('div').classes('metric-grid w-full'):
        _metric_card(
            'Cost before solar savings',
            _rand(float(metrics['total_cost'])),
            (
                f"Energy {_rand(float(metrics['energy_cost']))} + demand {_rand(float(metrics['demand_cost']))} + "
                f"service {_rand(float(metrics['service_cost']))} + network {_rand(float(metrics['network_surcharge']))}"
            ),
            'payments',
            'orange',
        )
        _metric_card(
            'Solar savings',
            _rand(float(metrics['solar_avoided_cost'])),
            (
                f"Previous {_rand(float(metrics['comparison_solar_avoided_cost']))}; "
                f"{_kwh(float(metrics['solar_used_kwh']))} used on site"
                if analysis.area in {ALL_AREAS, 'Warehouse 8'} else
                'Solar savings is credited to Warehouse 8 only, as confirmed by the recovery bills'
            ),
            'solar_power',
            'teal',
        )
        _metric_card(
            'Cost after solar savings',
            _rand(float(metrics['estimated_total_cost'])),
            f"Before savings {_rand(float(metrics['total_cost']))} less {_rand(float(metrics['solar_avoided_cost']))}",
            'savings',
            'light-blue',
        )
        _metric_card(
            f"Projected {metrics['forecast_month']} cost",
            _rand(float(metrics['forecast_month_end_cost'])),
            (
                f"{int(metrics['forecast_elapsed_days'])} complete day(s) through "
                f"{_display_date(metrics['forecast_through_date'])}; "
                f"{comparison_change_text(projected_previous_month_change)} than "
                f"recorded {metrics['forecast_previous_full_month']}"
            ),
            'calendar_month',
            'deep-purple',
        )
        _metric_card(
            'Projection without network surcharge',
            _rand(float(metrics['forecast_month_end_cost_without_network_surcharge'])),
            (
                f"Sensitivity only: {_rand(float(metrics['forecast_month_end_network_surcharge']))} "
                f"below the {'triggered' if network_surcharge_active else 'currently inactive'} surcharge scenario"
            ),
            'price_check',
            'teal',
        )
        _metric_card(
            'Matched MTD electricity use',
            comparison_change_text(current_month_usage_change),
            (
                f"{comparable_days} identical complete day(s): "
                f"{_kwh(float(metrics['forecast_current_comparable_kwh']))} vs "
                f"{_kwh(float(metrics['forecast_previous_comparable_kwh']))}"
            ),
            'bolt',
            'orange' if current_month_usage_change is not None and float(current_month_usage_change) > 0 else 'teal',
        )
        _metric_card(
            'Matched MTD estimated cost',
            comparison_change_text(current_month_change),
            (
                f"{comparable_days} identical complete day(s): "
                f"{_rand(float(metrics['forecast_current_comparable_cost']))} vs "
                f"{_rand(float(metrics['forecast_previous_cost_to_date']))}"
            ),
            'compare_arrows',
            'orange' if current_month_change is not None and float(current_month_change) > 0 else 'teal',
        )
        _metric_card(
            'Network surcharge trigger',
            f"{float(metrics['forecast_peak_demand_kva']):,.1f} kVA",
            (
                f"{metrics['forecast_peak_demand_area']} | "
                f"{_display_timestamp(metrics['forecast_peak_demand_time'])} | "
                f"projected effect {_rand(float(metrics['forecast_month_end_network_surcharge']))}"
                if network_surcharge_active else
                (
                    f"Below the {current_ctou.surcharge_threshold_kva:,.0f} kVA threshold"
                    if current_ctou is not None else 'No CTOU surcharge threshold applies'
                )
            ),
            'speed',
            'deep-orange' if network_surcharge_active else 'teal',
        )

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Tariffs used in this estimate').classes('section-title')
            ui.label('eThekwini CTOU and Business & General Scale 1').classes(
                'body-text text-sm font-bold'
            )
            ui.label(DEFAULT_TARIFF_NOTE).classes('section-subtitle')
            ui.separator().classes('my-2')
            if tariff_status is None:
                ui.label('Official source check is scheduled.').classes('muted-text text-xs')
            else:
                checked = tariff_status.checked_at.astimezone().strftime('%d %b %Y, %H:%M')
                status_tone = 'text-positive' if tariff_status.status in {'current', 'updated'} else 'text-orange-9'
                ui.label(f'Last checked: {checked} | {tariff_status.status.title()}').classes(
                    f'text-xs font-bold {status_tone}'
                )
                ui.label(tariff_status.message).classes('muted-text text-xs')
                if tariff_status.source_url:
                    ui.link('Open the official tariff document', tariff_status.source_url, new_tab=True).classes(
                        'text-xs font-bold'
                    )
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('What this estimate includes').classes('section-title')
            ui.label(
                'PNPSCADA half-hour kWh is assigned to the municipal Peak, Standard and Off-peak bands for Warehouses 6, 7 and 8. Warehouse 9 uses Scale 1. Demand, service, network surcharge and VAT are included where the confirmed tariff requires them.'
            ).classes('section-subtitle')
            ui.label(
                'Solar savings uses the exact recovery-bill method: matched solar is credited to Warehouse 8 at the applicable CTOU band. It is an operational estimate, not a confirmed grid export credit. Deposits, landlord adjustments, water, sewerage and other non-electricity bill lines are excluded.'
            ).classes('muted-text text-xs mt-2')
            if analysis.area not in {ALL_AREAS, 'Warehouse 8'}:
                ui.label(
                    'Solar is not deducted from this area because the municipal recovery bills apply the solar credit to Warehouse 8.'
                ).classes('muted-text text-xs mt-2')
            ui.label(
                'Demand charges use PNPSCADA kVA as an exposure estimate. The billed demand register has not matched the portal profile consistently, so this is not an invoice reconciliation.'
            ).classes('muted-text text-xs mt-2')
            coverage = float(metrics['coverage_percent'])
            ui.label(f'Meter-day coverage in selected period: {coverage:.1f}% complete.').classes(
                'body-text text-xs font-bold mt-2'
            )
            ui.label(
                f"Current-month projection: the average of {int(metrics['forecast_elapsed_days'])} complete meter day(s) "
                f"through {_display_date(metrics['forecast_through_date'])} is extended across all "
                f"{int(metrics['forecast_days_in_month'])} calendar days. This projection always uses the current month, "
                f"even when the comparison filters show older dates. {int(metrics['forecast_excluded_days'])} incomplete "
                'current-month day(s) are excluded so partial data cannot depress the forecast.'
            ).classes('muted-text text-xs mt-2')
            ui.label(
                f"Matched month-to-date changes use the same {comparable_days} complete calendar day(s) in both months. "
                'The main projection includes the network surcharge when the observed chargeable demand crosses the '
                'published threshold; the second projection removes only that surcharge as a sensitivity view.'
            ).classes('muted-text text-xs mt-2')

    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('Current tariff snapshot for this location').classes('section-title')
        ui.label(
            f'Rates effective on {_display_date(today)}. Values include VAT; the published CTOU season is {current_season.lower()}.'
        ).classes('section-subtitle')
        tariff_snapshot_rows: list[dict[str, str]] = []
        if current_ctou is not None:
            tariff_snapshot_rows.append({
                'tariff': 'CTOU - Warehouses 6, 7 and 8',
                'peak': f"R {current_ctou.energy_rate_inc_vat(today, 'peak'):.4f}/kWh",
                'standard': f"R {current_ctou.energy_rate_inc_vat(today, 'standard'):.4f}/kWh",
                'off_peak': f"R {current_ctou.energy_rate_inc_vat(today, 'off_peak'):.4f}/kWh",
                'demand': f"R {current_ctou.demand_charge_r_per_kva * 1.15:,.2f}/kVA/month",
                'service': f"R {current_ctou.service_charge_r_per_month * 1.15:,.2f}/month",
                'network': f"{current_ctou.network_surcharge_percent:.0f}% when demand is at least {current_ctou.surcharge_threshold_kva:.0f} kVA",
            })
        if current_scale is not None:
            tariff_snapshot_rows.append({
                'tariff': 'Scale 1 - Warehouse 9 area',
                'peak': f'R {current_scale.energy_rate(today):.4f}/kWh',
                'standard': 'Same flat rate',
                'off_peak': 'Same flat rate',
                'demand': 'Not charged',
                'service': f'R {current_scale.service_charge_r_per_month:,.2f}/month',
                'network': 'Not charged separately',
            })
        tariff_snapshot_columns = [
            {'name': 'tariff', 'label': 'Tariff / mapped area', 'field': 'tariff', 'align': 'left'},
            {'name': 'peak', 'label': 'Peak / flat rate', 'field': 'peak', 'align': 'right'},
            {'name': 'standard', 'label': 'Standard', 'field': 'standard', 'align': 'right'},
            {'name': 'off_peak', 'label': 'Off-peak', 'field': 'off_peak', 'align': 'right'},
            {'name': 'demand', 'label': 'Demand', 'field': 'demand', 'align': 'right'},
            {'name': 'service', 'label': 'Service', 'field': 'service', 'align': 'right'},
            {'name': 'network', 'label': 'Network surcharge', 'field': 'network', 'align': 'left'},
        ]
        ui.table(
            columns=tariff_snapshot_columns,
            rows=tariff_snapshot_rows,
            row_key='tariff',
        ).classes('w-full').props(
            'flat bordered separator=horizontal hide-pagination rows-per-page-options="[0]"'
        )

    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('CTOU time bands').classes('section-title')
            ui.label(
                'Energy is costed by the half-hour in which it was used. Public holidays follow the municipality\'s published Saturday or Sunday profile.'
            ).classes('section-subtitle')
            season_rows = [
                {'key': 'low-weekday', 'season': 'Low Sep-May', 'day': 'Weekday', 'peak': '07:00-09:00; 18:00-21:00', 'standard': '06:00-07:00; 09:00-18:00; 21:00-22:00', 'off_peak': '22:00-06:00'},
                {'key': 'low-saturday', 'season': 'Low Sep-May', 'day': 'Saturday', 'peak': 'None', 'standard': '07:00-12:00; 18:00-20:00', 'off_peak': 'All other hours'},
                {'key': 'low-sunday', 'season': 'Low Sep-May', 'day': 'Sunday', 'peak': 'None', 'standard': '18:00-20:00', 'off_peak': 'All other hours'},
                {'key': 'high-weekday', 'season': 'High Jun-Aug', 'day': 'Weekday', 'peak': '06:00-08:00; 17:00-20:00', 'standard': '08:00-17:00; 20:00-22:00', 'off_peak': '22:00-06:00'},
                {'key': 'high-saturday', 'season': 'High Jun-Aug', 'day': 'Saturday', 'peak': 'None', 'standard': '07:00-12:00; 17:00-19:00', 'off_peak': 'All other hours'},
                {'key': 'high-sunday', 'season': 'High Jun-Aug', 'day': 'Sunday', 'peak': 'None', 'standard': 'None', 'off_peak': 'All hours'},
            ]
            season_columns = [
                {'name': 'season', 'label': 'Season', 'field': 'season', 'align': 'left'},
                {'name': 'day', 'label': 'Day type', 'field': 'day', 'align': 'left'},
                {'name': 'peak', 'label': 'Peak', 'field': 'peak', 'align': 'left'},
                {'name': 'standard', 'label': 'Standard', 'field': 'standard', 'align': 'left'},
                {'name': 'off_peak', 'label': 'Off-peak', 'field': 'off_peak', 'align': 'left'},
            ]
            ui.table(
                columns=season_columns, rows=season_rows, row_key='key',
            ).classes('w-full').props(
                'flat bordered separator=horizontal hide-pagination rows-per-page-options="[0]"'
            )

        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Meter and bill mapping used').classes('section-title')
            ui.label(
                'The bills validate tariff type and selected meter relationships; PNPSCADA remains the only kWh source used in calculations.'
            ).classes('section-subtitle')
            mapping_rows = [
                {'area': 'Warehouse 6', 'portal': 'E9623 (10005)', 'bill': 'Account 840 / E9623', 'tariff': 'CTOU', 'status': 'Meter match confirmed'},
                {'area': 'Warehouse 7', 'portal': 'E9607 (10006)', 'bill': 'Account 839 / E9607', 'tariff': 'CTOU', 'status': 'Meter match confirmed'},
                {'area': 'Warehouse 8', 'portal': 'E9615 (10004)', 'bill': 'Account 840 / E9615', 'tariff': 'CTOU', 'status': 'Meter and solar-credit match confirmed'},
                {'area': 'Warehouse 9', 'portal': 'E988 (10105)', 'bill': 'Account 841 / E89880 T', 'tariff': 'Scale 1', 'status': 'Area tariff confirmed; meter ID differs'},
                {'area': 'Admin office', 'portal': 'No matching meter', 'bill': 'Account 838 / common-area allocation', 'tariff': 'Bill only', 'status': 'Excluded from PNPSCADA cost'},
                {'area': 'WH8 supplemental', 'portal': 'No matching meter', 'bill': 'E1723', 'tariff': 'Scale 1', 'status': 'Bill only; excluded from PNPSCADA cost'},
            ]
            mapping_columns = [
                {'name': 'area', 'label': 'Area', 'field': 'area', 'align': 'left'},
                {'name': 'portal', 'label': 'PNPSCADA meter', 'field': 'portal', 'align': 'left'},
                {'name': 'bill', 'label': 'Bill reference', 'field': 'bill', 'align': 'left'},
                {'name': 'tariff', 'label': 'Tariff', 'field': 'tariff', 'align': 'left'},
                {'name': 'status', 'label': 'Verification', 'field': 'status', 'align': 'left'},
            ]
            ui.table(
                columns=mapping_columns, rows=mapping_rows, row_key='area',
            ).classes('w-full').props(
                'flat bordered separator=horizontal hide-pagination rows-per-page-options="[0]"'
            )

    usage_change = (
        metrics['usage_change_percent']
        if current_days == comparison_days else metrics['daily_usage_change_percent']
    )
    rate_change = metrics['rate_change_percent']
    def change_words(value: object, subject: str) -> str:
        if value is None:
            return f'{subject}: a reliable percentage is not available.'
        number = float(value)
        direction = 'higher' if number > 0 else 'lower' if number < 0 else 'unchanged'
        return f'{subject}: {abs(number):.1f}% {direction}.'

    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('What changed between the two periods').classes('section-title')
        ui.label(
            'Daily averages are used automatically when the selected ranges contain different numbers of days.'
        ).classes('section-subtitle')
        with ui.element('div').classes('report-feature-grid w-full mt-2'):
            for icon, title, text in [
                ('bolt', 'Electricity used', change_words(
                    usage_change,
                    'Average daily use' if current_days != comparison_days else 'Total use',
                )),
                ('price_change', 'Energy rate effect', change_words(
                    rate_change, 'The weighted VAT-inclusive kWh rate'
                )),
                ('payments', 'Estimated cost', change_words(
                    comparable_metric,
                    'Average daily cost' if current_days != comparison_days else 'Total estimated cost',
                )),
                ('solar_power', 'Solar savings', change_words(
                    metrics['solar_avoided_change_percent'],
                    'Estimated solar savings',
                ) if analysis.area in {ALL_AREAS, 'Warehouse 8'} else
                    'Solar savings is credited to Warehouse 8 and is not applied to this area.'),
                ('warehouse', 'Highest-cost area', (
                    f"{metrics['highest_cost_area']}: {_rand(float(metrics['highest_cost_area_value']))} "
                    'after any confirmed Warehouse 8 solar saving.'
                )),
                ('schedule', 'Peak-period exposure', (
                    f"{_kwh(float(metrics['peak_kwh']))} was used in CTOU peak periods; "
                    f"the highest working-period demand observed was {float(metrics['peak_demand_kva']):,.1f} kVA."
                )),
            ]:
                with ui.row().classes('insight-row items-start no-wrap gap-3'):
                    ui.icon(icon, color='primary', size='22px')
                    with ui.column().classes('gap-0'):
                        ui.label(title).classes('body-text text-sm font-bold')
                        ui.label(text).classes('muted-text text-xs')

    if analysis.warnings:
        with ui.row().classes('warning-banner w-full items-start gap-2'):
            ui.icon('warning_amber', size='20px', color='orange')
            ui.label(' '.join(analysis.warnings)).classes('text-sm')

    with ui.element('div').classes('chart-grid w-full'):
        _chart_card(cost_trend_options(analysis), '410px')
        _chart_card(cost_area_comparison_options(analysis), '410px')
        _chart_card(cost_component_options(analysis), '410px')
        _chart_card(tou_energy_mix_options(analysis), '410px')

    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('Cost by warehouse area').classes('section-title')
        ui.label(
            'Solar savings is credited only to Warehouse 8 because that is how the available municipal recovery bills apply the solar meter. Other areas retain their full estimated cost.'
        ).classes('section-subtitle')
        rows = [{
            'area': row['area'],
            'tariff': row['tariff'],
            'kwh': f"{float(row['current_kwh']):,.1f}",
            'energy': _rand(float(row['current_energy_cost'])),
            'demand': _rand(float(row['current_demand_cost'])),
            'service': _rand(float(row['current_service_cost'])),
            'network': _rand(float(row['current_network_surcharge'])),
            'solar': _rand(float(row['current_solar_avoided_cost'])),
            'total': _rand(float(row['current_total_cost'])),
            'comparison': _rand(float(row['comparison_total_cost'])),
            'change': (
                f"{float(row['cost_change_percent']):+.1f}%"
                if row['cost_change_percent'] is not None else 'Not available'
            ),
        } for row in analysis.area_rows]
        columns = [
            {'name': 'area', 'label': 'Warehouse area', 'field': 'area', 'align': 'left', 'sortable': True},
            {'name': 'tariff', 'label': 'Tariff', 'field': 'tariff', 'align': 'left', 'sortable': True},
            {'name': 'kwh', 'label': 'Selected kWh', 'field': 'kwh', 'align': 'right', 'sortable': True},
            {'name': 'energy', 'label': 'Energy before solar', 'field': 'energy', 'align': 'right'},
            {'name': 'demand', 'label': 'Demand estimate', 'field': 'demand', 'align': 'right'},
            {'name': 'service', 'label': 'Service estimate', 'field': 'service', 'align': 'right'},
            {'name': 'network', 'label': 'Network surcharge', 'field': 'network', 'align': 'right'},
            {'name': 'solar', 'label': 'Solar savings', 'field': 'solar', 'align': 'right'},
            {'name': 'total', 'label': 'Selected after savings', 'field': 'total', 'align': 'right'},
            {'name': 'comparison', 'label': 'Comparison after savings', 'field': 'comparison', 'align': 'right'},
            {'name': 'change', 'label': 'Total change', 'field': 'change', 'align': 'right'},
        ]
        ui.table(columns=columns, rows=rows, row_key='area').classes('w-full').props(
            'flat bordered separator=horizontal hide-pagination rows-per-page-options="[0]"'
        )

    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('Rates applied across the selected dates').classes('section-title')
        ui.label('All values below include VAT. June to August is the published high season.').classes(
            'section-subtitle'
        )
        def rate_text(value: object) -> str:
            return f'R {float(value):.4f}/kWh' if value is not None else 'Not applicable'

        tariff_rows = [{
            'key': f"{row['tariff']}-{row['period']}",
            'tariff': row['tariff'],
            'period': row['period'],
            'season': row.get('season') or 'Flat',
            'peak': rate_text(row.get('peak_rate') if row.get('peak_rate') is not None else row.get('high_rate')),
            'standard': rate_text(row.get('standard_rate')),
            'off_peak': rate_text(row.get('off_peak_rate')),
            'low': rate_text(row.get('low_rate')),
            'demand': (
                f"R {float(row['demand_charge']):,.2f}/kVA/month"
                if row.get('demand_charge') is not None else 'Not applicable'
            ),
            'service': f"R {float(row['service_charge']):,.2f}/month/meter",
            'network': (
                f"{float(row['network_surcharge_percent']):.0f}%"
                if row.get('network_surcharge_percent') is not None else 'Not applicable'
            ),
            'source': row['source_label'],
        } for row in analysis.tariff_rows]
        tariff_columns = [
            {'name': 'tariff', 'label': 'Tariff', 'field': 'tariff', 'align': 'left'},
            {'name': 'period', 'label': 'Effective dates', 'field': 'period', 'align': 'left'},
            {'name': 'season', 'label': 'Shown season', 'field': 'season', 'align': 'left'},
            {'name': 'peak', 'label': 'Peak / high-flat', 'field': 'peak', 'align': 'right'},
            {'name': 'standard', 'label': 'Standard', 'field': 'standard', 'align': 'right'},
            {'name': 'off_peak', 'label': 'Off-peak', 'field': 'off_peak', 'align': 'right'},
            {'name': 'low', 'label': 'Low-season flat', 'field': 'low', 'align': 'right'},
            {'name': 'demand', 'label': 'Demand', 'field': 'demand', 'align': 'right'},
            {'name': 'service', 'label': 'Service charge', 'field': 'service', 'align': 'right'},
            {'name': 'network', 'label': 'Network surcharge', 'field': 'network', 'align': 'right'},
            {'name': 'source', 'label': 'Official source', 'field': 'source', 'align': 'left'},
        ]
        ui.table(columns=tariff_columns, rows=tariff_rows, row_key='key').classes('w-full').props(
            'flat bordered separator=horizontal hide-pagination rows-per-page-options="[0]"'
        )


def _reports_center(
    dataset: DashboardDataset,
    report_type: str,
    anchor_date: date,
    area: str,
    filtered_start: date,
    filtered_end: date,
    comparison_start: date | None,
    comparison_end: date | None,
    comparison_method: str,
    on_report_type_change,
    on_generate_pdf,
    on_export_xlsx,
    on_export_csv,
) -> None:
    period = resolve_report_period(
        report_type,
        anchor_date,
        dataset,
        selected_start=filtered_start,
        selected_end=filtered_end,
        comparison_start=comparison_start,
        comparison_end=comparison_end,
        comparison_method=comparison_method,
    )
    with ui.element('div').classes('content-grid w-full'):
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Professional PDF reports').classes('section-title')
            ui.label(
                'Generate a management report using the exact app scope below, including solar savings and the current-month cost projection.'
            ).classes('section-subtitle')
            report_select = ui.select(
                list(REPORT_TYPES),
                value=report_type,
                label='Report type',
                on_change=lambda event: on_report_type_change(str(event.value)),
            ).props('outlined dense options-dense').classes('w-full max-w-xs')
            report_select.tooltip(
                'Overview reports do not compare periods. Comparison reports reuse the saved Comparison or Cost Centre ranges.'
            )
            with ui.column().classes('report-scope w-full gap-1 mt-3'):
                ui.label('PDF report scope').classes('text-xs font-bold uppercase')
                ui.label(f'{period.label} | {_report_scope_label(area)}').classes('text-sm font-bold')
                ui.label(period.note).classes('muted-text text-xs')
                if period.comparison_enabled:
                    ui.label(
                        f'Comparison: {_display_date(period.comparison_start)} to '
                        f'{_display_date(period.comparison_end)}'
                    ).classes('text-sm font-bold')
                if period.end_date < period.requested_date and report_type != 'Current month overview':
                    ui.label(
                        'The current day is still being populated and is excluded from management reports.'
                    ).classes('text-xs font-bold text-orange-9')
            ui.button('Generate and download PDF', icon='picture_as_pdf', on_click=on_generate_pdf).props(
                'flat no-caps'
            ).classes('primary-action mt-4')
        with ui.card().classes('dashboard-card shadow-none'):
            ui.label('Filtered data exports').classes('section-title')
            ui.label(
                'Exports use the complete active date and area filters shown at the top of the page.'
            ).classes('section-subtitle')
            with ui.column().classes('report-scope w-full gap-1'):
                ui.label('EXPORT SCOPE').classes('text-xs font-bold uppercase')
                ui.label(
                    f'{_display_date(filtered_start)} to {_display_date(filtered_end)} | {_scope_label(area)}'
                ).classes('text-sm font-bold')
            with ui.row().classes('w-full gap-2 mt-4 flex-wrap'):
                ui.button('Export XLSX', icon='table_view', on_click=on_export_xlsx).props(
                    'flat no-caps'
                ).classes('primary-action')
                ui.button('Export CSV', icon='description', on_click=on_export_csv).props(
                    'flat no-caps'
                ).classes('toolbar-action')

    with ui.card().classes('dashboard-card w-full shadow-none'):
        ui.label('What the PDF answers').classes('section-title')
        ui.label('Designed around proactive current-month control and exact app comparison ranges.').classes('section-subtitle')
        features = [
            ('date_range', 'What period is covered?', 'The exact current-month or active app date range shown above.'),
            ('warehouse', 'Where is use highest?', 'Area totals, shares and changes side by side.'),
            ('speed', 'When was demand highest?', 'Exact kW and kVA peaks with the meter area and time.'),
            ('payments', 'What is the expected cost?', 'Cost before savings, solar savings, cost after savings and current-month projection.'),
            ('warning', 'What needs investigation?', 'Unusual readings and a practical first check for each.'),
            ('solar_power', 'How is solar handled?', 'Solar generation is shown separately from warehouse consumption.'),
            ('label', 'What are the exact values?', 'Charts include direct data labels and detailed supporting tables.'),
        ]
        if period.comparison_enabled:
            features.insert(1, (
                'compare_arrows',
                'What changed?',
                f"Recorded values compared using {period.comparison_method or 'the selected comparison method'}.",
            ))
        with ui.element('div').classes('report-feature-grid w-full'):
            for icon, title, text in features:
                with ui.row().classes('insight-row items-start no-wrap gap-3'):
                    ui.icon(icon, color='primary', size='22px')
                    with ui.column().classes('gap-0'):
                        ui.label(title).classes('body-text text-sm font-bold')
                        ui.label(text).classes('muted-text text-xs')


@ui.page('/')
def dashboard() -> None:
    ui.colors(
        primary='#E04403', secondary='#28D2B3', accent='#E04403', positive='#28D2B3',
        negative='#EF4444', warning='#F59E0B', info='#38BDF8'
    )
    apply_dashboard_styles()

    try:
        dataset = repository.get()
    except (FileNotFoundError, ValueError) as exc:
        with ui.column().classes('w-full min-h-screen items-center justify-center gap-4'):
            ui.icon('electric_bolt', size='64px', color='orange')
            ui.label('Sydney Road Electricity Monitor').classes('toolbar-title')
            ui.label(str(exc)).classes('muted-text')
            ui.label('Run the extractor, then refresh this page.').classes('muted-text')
        return

    closed_date = _latest_closed_date(dataset)
    state = PageState(
        start_date=max(dataset.first_date, closed_date - timedelta(days=29)),
        end_date=closed_date,
    )
    comparison_year = closed_date.year - 1
    if comparison_year >= dataset.first_date.year:
        (
            state.comparison_current_start,
            state.comparison_current_end,
            state.comparison_previous_start,
            state.comparison_previous_end,
            _comparison_note,
        ) = comparison_period_ranges(
            'Year vs year',
            str(closed_date.year),
            str(comparison_year),
            dataset.first_date,
            closed_date,
        )
    else:
        state.comparison_type = 'Custom ranges'
        state.comparison_current_start = state.start_date
        state.comparison_current_end = state.end_date
        (
            _selected_start,
            _selected_end,
            state.comparison_previous_start,
            state.comparison_previous_end,
            _comparison_note,
        ) = comparison_dates('Selected dates', state.end_date, state.start_date, state.end_date)
    state.cost_selected_start = max(dataset.first_date, closed_date.replace(day=1))
    state.cost_selected_end = closed_date
    initial_comparison_start = _previous_month_start(state.cost_selected_start)
    if initial_comparison_start >= dataset.first_date:
        state.cost_comparison_start = initial_comparison_start
        state.cost_comparison_end = initial_comparison_start.replace(
            day=min(closed_date.day, _month_end_date(initial_comparison_start).day)
        )
    else:
        state.cost_comparison_start = state.cost_selected_start
        state.cost_comparison_end = state.cost_selected_end
    state.cost_selected_month = state.cost_selected_end.strftime('%Y-%m')
    state.cost_compare_month = initial_comparison_start.strftime('%Y-%m')
    agent_preferences = mail_agent_store.preferences()
    state.agent_to_text = '; '.join(agent_preferences.to_recipients)
    state.agent_cc_text = '; '.join(agent_preferences.cc_recipients)
    state.agent_sender_mailbox = agent_preferences.sender_mailbox
    store = WindowsCredentialStore()
    try:
        credential_available = store.read() is not None
    except CredentialError:
        credential_available = False

    def reset_cost_comparison() -> None:
        current = repository.get()
        closed_date = _latest_closed_date(current)
        state.cost_selected_month = state.end_date.strftime('%Y-%m')
        prior_month_start = _previous_month_start(state.start_date)
        state.cost_compare_month = prior_month_start.strftime('%Y-%m')
        same_month = (
            state.start_date.year == state.end_date.year
            and state.start_date.month == state.end_date.month
        )
        if state.start_date.day == 1 and same_month:
            state.cost_comparison_start = prior_month_start
            if state.end_date == _month_end_date(state.start_date):
                state.cost_comparison_end = _month_end_date(prior_month_start)
                state.cost_comparison_type = 'Past month vs past month'
            elif (
                state.end_date.year == closed_date.year
                and state.end_date.month == closed_date.month
            ):
                state.cost_comparison_end = prior_month_start.replace(
                    day=min(state.end_date.day, _month_end_date(prior_month_start).day)
                )
                state.cost_comparison_type = 'Current month vs previous month'
            else:
                state.cost_comparison_end = prior_month_start.replace(
                    day=min(state.end_date.day, _month_end_date(prior_month_start).day)
                )
                state.cost_comparison_type = 'Previous matching period'
            return
        span = (state.end_date - state.start_date).days + 1
        comparison_end = state.start_date - timedelta(days=1)
        if comparison_end >= current.first_date:
            state.cost_comparison_end = comparison_end
            state.cost_comparison_start = max(
                current.first_date,
                comparison_end - timedelta(days=span - 1),
            )
        else:
            state.cost_comparison_start = state.start_date
            state.cost_comparison_end = state.end_date
        state.cost_comparison_type = 'Previous matching period'

    async def save_credentials() -> None:
        nonlocal credential_available
        username = str(credential_username.value or '').strip()
        password = str(credential_password.value or '')
        if not username or not password:
            ui.notify('Enter both the PNPSCADA username and password.', type='warning')
            return
        credential_save_button.disable()
        notice = ui.notification('Checking the PNPSCADA login…', spinner=True, timeout=None)
        try:
            client = PortalClient(DEFAULT_BASE_URL)
            await asyncio.to_thread(client.login, username, password)
            await asyncio.to_thread(store.write, username, password)
            credential_available = True
            credential_password.value = ''
            notice.dismiss()
            ui.notify(
                'Login confirmed and saved securely in Windows Credential Manager.',
                type='positive',
                timeout=6.0,
            )
            credential_dialog.close()
            content.refresh()
            sidebar_area.refresh()
        except Exception as exc:
            notice.dismiss()
            ui.notify(
                f'Login could not be saved: {exc}',
                type='negative',
                timeout=10.0,
                close_button='Dismiss',
            )
        finally:
            credential_save_button.enable()

    with ui.dialog() as credential_dialog, ui.card().classes('app-card w-full max-w-lg p-5'):
        ui.label('Shared PNPSCADA portal login').classes('section-title')
        ui.label(
            'Enter the Connect Logistics portal account once on this PC. '
            'The same account is then used for every meter-data refresh.'
        ).classes('section-subtitle')
        credential_username = ui.input('Username').props('outlined autocomplete=username').classes('w-full')
        credential_password = ui.input(
            'Password', password=True, password_toggle_button=True
        ).props('outlined autocomplete=current-password').classes('w-full')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancel', on_click=credential_dialog.close).props('flat no-caps').classes('toolbar-action')
            credential_save_button = ui.button(
                'Check and save', icon='verified_user', on_click=save_credentials
            ).props('flat no-caps').classes('primary-action')

    def set_view(view_name: str) -> None:
        previous_view = state.current_view
        state.current_view = view_name
        if previous_view == 'solar' and view_name != 'solar' and state.area == SOLAR_AREA:
            state.area = ALL_AREAS
            area_select.value = ALL_AREAS
        if view_name == 'supply':
            state.area = ALL_AREAS
            area_select.value = ALL_AREAS
        elif view_name in {'costs', 'solar_investment'}:
            if state.area == SOLAR_AREA:
                state.area = ALL_AREAS
                area_select.value = ALL_AREAS
            if view_name == 'solar_investment':
                state.area = ALL_AREAS
                area_select.value = ALL_AREAS
            elif not state.cost_initialized:
                state.cost_initialized = True
                apply_cost_current_month_comparison()
            elif state.cost_comparison_start is None or state.cost_comparison_end is None:
                reset_cost_comparison()
            elif state.cost_selected_start is not None and state.cost_selected_end is not None:
                state.start_date = state.cost_selected_start
                state.end_date = state.cost_selected_end
                start_input.value = state.start_date.isoformat()
                end_input.value = state.end_date.isoformat()
        elif view_name == 'solar':
            current = repository.get()
            state.area = SOLAR_AREA
            area_select.value = SOLAR_AREA
            state.start_date = max(
                state.start_date,
                first_valid_area_date(current, SOLAR_AREA),
            )
            if state.end_date < state.start_date:
                state.end_date = _latest_closed_date(current)
            start_input.value = state.start_date.isoformat()
            end_input.value = state.end_date.isoformat()
        elif view_name == 'comparison' and state.comparison_type == 'Year vs year':
            current = repository.get()
            closed_date = _latest_closed_date(current)
            comparison_year = closed_date.year - 1
            if comparison_year < current.first_date.year:
                comparison_year = current.first_date.year
            current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
                'Year vs year',
                str(closed_date.year),
                str(comparison_year),
                current.first_date,
                closed_date,
            )
            state.start_date = current_start
            state.end_date = current_end
            state.comparison_current_start = current_start
            state.comparison_current_end = current_end
            state.comparison_previous_start = previous_start
            state.comparison_previous_end = previous_end
            start_input.value = state.start_date.isoformat()
            end_input.value = state.end_date.isoformat()
            period_select.value = 'Custom dates'
        elif view_name == 'comparison' and state.comparison_current_start is not None and state.comparison_current_end is not None:
            state.start_date = state.comparison_current_start
            state.end_date = state.comparison_current_end
            start_input.value = state.start_date.isoformat()
            end_input.value = state.end_date.isoformat()
            period_select.value = 'Custom dates'
        elif view_name == 'reports':
            set_report_type(state.report_type)
        filter_card.set_visibility(view_name != 'ai')
        toolbar_area.refresh()
        sidebar_area.refresh()
        content.refresh()

    def toggle_sidebar() -> None:
        state.sidebar_collapsed = not state.sidebar_collapsed
        sidebar_panel.classes(
            add='collapsed' if state.sidebar_collapsed else '',
            remove='' if state.sidebar_collapsed else 'collapsed',
        )
        sidebar_area.refresh()

    def apply_filters() -> None:
        try:
            requested_start = date.fromisoformat(str(start_input.value))
            requested_end = date.fromisoformat(str(end_input.value))
        except ValueError:
            ui.notify('Enter valid start and end dates.', type='negative')
            return
        if requested_end < requested_start:
            ui.notify('The end date cannot be before the start date.', type='negative')
            return
        current = repository.get()
        minimum_date = (
            first_valid_area_date(current, SOLAR_AREA)
            if state.current_view == 'solar' else current.first_date
        )
        state.start_date = max(minimum_date, requested_start)
        closed_date = _latest_closed_date(current)
        state.end_date = min(closed_date, requested_end)
        if state.end_date < state.start_date:
            ui.notify('The selected dates fall outside the available meter history.', type='warning')
            return
        if requested_end > closed_date:
            ui.notify(
                f'Analytics stop at {_display_date(closed_date)} because the current day is incomplete.',
                type='warning',
            )
        requested_area = str(area_select.value or ALL_AREAS)
        if state.current_view == 'solar':
            state.area = SOLAR_AREA
            area_select.value = SOLAR_AREA
            if requested_area != SOLAR_AREA:
                ui.notify(
                    'This page uses the solar meter only.',
                    type='info',
                    timeout=5.0,
                )
        elif state.current_view == 'supply' and requested_area != ALL_AREAS:
            state.area = ALL_AREAS
            area_select.value = ALL_AREAS
            ui.notify(
                'This page is site-level, so it uses all warehouse meters.',
                type='info',
                timeout=5.0,
            )
        elif state.current_view in {'costs', 'solar_investment'} and requested_area == SOLAR_AREA:
            state.area = ALL_AREAS
            area_select.value = ALL_AREAS
            ui.notify(
                'This page uses warehouse consumption and solar together at site level.',
                type='info',
                timeout=5.0,
            )
        elif state.current_view == 'solar_investment':
            state.area = ALL_AREAS
            area_select.value = ALL_AREAS
            if requested_area != ALL_AREAS:
                ui.notify(
                    'Solar Investment is a site-level view, so it uses all warehouse meters.',
                    type='info',
                    timeout=5.0,
                )
        else:
            state.area = requested_area
        if state.current_view == 'comparison':
            state.comparison_type = 'Custom ranges'
            _, _, previous_start, previous_end, _ = comparison_dates(
                'Selected dates', state.end_date, state.start_date, state.end_date
            )
            state.comparison_previous_start = previous_start
            state.comparison_previous_end = previous_end
            state.comparison_current_start = state.start_date
            state.comparison_current_end = state.end_date
        if state.current_view == 'costs':
            reset_cost_comparison()
        start_input.value = state.start_date.isoformat()
        end_input.value = state.end_date.isoformat()
        period_select.value = 'Custom dates'
        content.refresh()

    def set_period(period: str) -> None:
        if period == 'custom':
            return
        current = repository.get()
        state.end_date = _latest_closed_date(current)
        minimum_date = (
            first_valid_area_date(current, SOLAR_AREA)
            if state.current_view == 'solar' else current.first_date
        )
        if period == '7d':
            state.start_date = max(minimum_date, state.end_date - timedelta(days=6))
        elif period == '30d':
            state.start_date = max(minimum_date, state.end_date - timedelta(days=29))
        elif period == 'mtd':
            state.start_date = max(minimum_date, state.end_date.replace(day=1))
        elif period == 'ytd':
            state.start_date = max(minimum_date, state.end_date.replace(month=1, day=1))
        else:
            state.start_date = minimum_date
        if state.current_view == 'comparison':
            state.comparison_type = 'Custom ranges'
            _, _, previous_start, previous_end, _ = comparison_dates(
                'Selected dates', state.end_date, state.start_date, state.end_date
            )
            state.comparison_previous_start = previous_start
            state.comparison_previous_end = previous_end
            state.comparison_current_start = state.start_date
            state.comparison_current_end = state.end_date
        if state.current_view == 'costs':
            reset_cost_comparison()
        start_input.value = state.start_date.isoformat()
        end_input.value = state.end_date.isoformat()
        content.refresh()

    def set_report_type(value: str) -> None:
        if value not in REPORT_TYPES:
            return
        state.report_type = value
        current = repository.get()
        closed_date = _latest_closed_date(current)
        if value == 'Current month overview':
            state.start_date = max(current.first_date, closed_date.replace(day=1))
            state.end_date = closed_date
        elif value == 'Usage comparison':
            state.start_date = state.comparison_current_start or state.start_date
            state.end_date = state.comparison_current_end or state.end_date
        elif value == 'Cost comparison':
            state.start_date = state.cost_selected_start or state.start_date
            state.end_date = state.cost_selected_end or state.end_date
        start_input.value = state.start_date.isoformat()
        end_input.value = state.end_date.isoformat()
        period_select.value = 'Custom dates'
        content.refresh()

    def set_comparison_type(value: str) -> None:
        if value not in {'Year vs year', 'Month vs month', 'Week vs week', 'Custom ranges'}:
            return
        state.comparison_type = value
        current = repository.get()
        closed_date = _latest_closed_date(current)
        if value == 'Year vs year':
            first_token = str(closed_date.year)
            second_token = str(max(current.first_date.year, closed_date.year - 1))
            try:
                current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
                    value, first_token, second_token, current.first_date, closed_date
                )
            except ValueError as exc:
                ui.notify(str(exc), type='warning')
                return
        elif value == 'Month vs month':
            first_month = closed_date.replace(day=1)
            second_month = first_month - timedelta(days=1)
            try:
                current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
                    value,
                    first_month.strftime('%Y-%m'),
                    second_month.strftime('%Y-%m'),
                    current.first_date,
                    closed_date,
                )
            except ValueError as exc:
                ui.notify(str(exc), type='warning')
                return
        elif value == 'Week vs week':
            current_start, current_end, previous_start, previous_end, _ = comparison_dates(
                'Weekly', closed_date
            )
        else:
            _, _, previous_start, previous_end, _ = comparison_dates(
                'Selected dates', state.end_date, state.start_date, state.end_date
            )
            state.comparison_previous_start = previous_start
            state.comparison_previous_end = previous_end
            state.comparison_current_start = state.start_date
            state.comparison_current_end = state.end_date
            content.refresh()
            return
        state.start_date = max(current.first_date, current_start)
        state.end_date = min(closed_date, current_end)
        state.comparison_current_start = state.start_date
        state.comparison_current_end = state.end_date
        state.comparison_previous_start = previous_start
        state.comparison_previous_end = previous_end
        start_input.value = state.start_date.isoformat()
        end_input.value = state.end_date.isoformat()
        period_select.value = 'Custom dates'
        content.refresh()

    def apply_named_comparison_periods(
        comparison_type: str,
        first_period: str,
        second_period: str,
    ) -> None:
        current = repository.get()
        closed_date = _latest_closed_date(current)
        try:
            current_start, current_end, previous_start, previous_end, _ = comparison_period_ranges(
                comparison_type,
                first_period,
                second_period,
                current.first_date,
                closed_date,
            )
        except ValueError as exc:
            ui.notify(str(exc), type='warning', timeout=7.0)
            return
        state.comparison_type = comparison_type
        state.start_date = current_start
        state.end_date = current_end
        state.comparison_current_start = current_start
        state.comparison_current_end = current_end
        state.comparison_previous_start = previous_start
        state.comparison_previous_end = previous_end
        start_input.value = state.start_date.isoformat()
        end_input.value = state.end_date.isoformat()
        period_select.value = 'Custom dates'
        content.refresh()

    def apply_comparison_ranges(
        current_start_text: str,
        current_end_text: str,
        previous_start_text: str,
        previous_end_text: str,
    ) -> None:
        try:
            current_start = date.fromisoformat(current_start_text)
            current_end = date.fromisoformat(current_end_text)
            previous_start = date.fromisoformat(previous_start_text)
            previous_end = date.fromisoformat(previous_end_text)
        except ValueError:
            ui.notify('Enter valid dates for both comparison periods.', type='negative')
            return
        if current_end < current_start or previous_end < previous_start:
            ui.notify('Each period must end on or after its start date.', type='negative')
            return
        current = repository.get()
        closed_date = _latest_closed_date(current)
        if (
            current_start < current.first_date
            or previous_start < current.first_date
            or current_end > closed_date
            or previous_end > closed_date
        ):
            ui.notify(
                f'Choose dates from {_display_date(current.first_date)} through '
                f'{_display_date(closed_date)}.',
                type='warning',
                timeout=7.0,
            )
            return
        state.start_date = current_start
        state.end_date = current_end
        state.comparison_current_start = current_start
        state.comparison_current_end = current_end
        state.comparison_previous_start = previous_start
        state.comparison_previous_end = previous_end
        state.comparison_type = 'Custom ranges'
        start_input.value = current_start.isoformat()
        end_input.value = current_end.isoformat()
        period_select.value = 'Custom dates'
        content.refresh()

    def _store_cost_ranges(
        selected_start: date,
        selected_end: date,
        comparison_start: date,
        comparison_end: date,
        comparison_type: str,
    ) -> None:
        state.start_date = selected_start
        state.end_date = selected_end
        state.cost_selected_start = selected_start
        state.cost_selected_end = selected_end
        state.cost_comparison_start = comparison_start
        state.cost_comparison_end = comparison_end
        state.cost_comparison_type = comparison_type
        state.cost_selected_month = selected_start.strftime('%Y-%m')
        state.cost_compare_month = comparison_start.strftime('%Y-%m')
        start_input.value = selected_start.isoformat()
        end_input.value = selected_end.isoformat()
        period_select.value = 'Custom dates'
        content.refresh()

    def set_cost_comparison_type(value: str) -> None:
        if value not in COST_COMPARISON_OPTIONS:
            return
        current = repository.get()
        closed_date = _latest_closed_date(current)
        state.cost_comparison_type = value
        if value == 'Current month vs previous month':
            apply_cost_current_month_comparison()
            return
        if value == 'Past month vs past month':
            latest_full_month = closed_date.replace(day=1) - timedelta(days=1)
            state.cost_selected_month = latest_full_month.strftime('%Y-%m')
            state.cost_compare_month = _previous_month_start(latest_full_month).strftime('%Y-%m')
        content.refresh()

    def apply_cost_month_comparison(selected_month: str, compare_month: str) -> None:
        current = repository.get()
        try:
            selected_start, selected_end, comparison_start, comparison_end = (
                resolve_cost_month_comparison(
                    selected_month,
                    compare_month,
                    current.first_date,
                    _latest_closed_date(current),
                )
            )
        except ValueError as exc:
            ui.notify(str(exc), type='warning', timeout=7.0)
            return
        _store_cost_ranges(
            selected_start,
            selected_end,
            comparison_start,
            comparison_end,
            'Past month vs past month',
        )

    def apply_cost_current_month_comparison() -> None:
        current = repository.get()
        closed_date = _latest_closed_date(current)
        selected_month = closed_date.strftime('%Y-%m')
        compare_month = _previous_month_start(closed_date).strftime('%Y-%m')
        try:
            selected_start, selected_end, comparison_start, comparison_end = (
                resolve_cost_month_comparison(
                    selected_month,
                    compare_month,
                    current.first_date,
                    closed_date,
                    through_day=closed_date.day,
                )
            )
        except ValueError as exc:
            ui.notify(str(exc), type='warning', timeout=7.0)
            return
        _store_cost_ranges(
            selected_start,
            selected_end,
            comparison_start,
            comparison_end,
            'Current month vs previous month',
        )

    def apply_cost_custom_comparison(
        selected_start_text: str,
        selected_end_text: str,
        comparison_start_text: str,
        comparison_end_text: str,
    ) -> None:
        try:
            selected_start = date.fromisoformat(selected_start_text)
            selected_end = date.fromisoformat(selected_end_text)
            comparison_start = date.fromisoformat(comparison_start_text)
            comparison_end = date.fromisoformat(comparison_end_text)
        except ValueError:
            ui.notify('Enter valid dates for both cost periods.', type='negative')
            return
        if selected_end < selected_start or comparison_end < comparison_start:
            ui.notify('Each cost period must end on or after its start date.', type='negative')
            return
        current = repository.get()
        closed_date = _latest_closed_date(current)
        if (
            selected_start < current.first_date
            or comparison_start < current.first_date
            or selected_end > closed_date
            or comparison_end > closed_date
        ):
            ui.notify(
                f'Choose dates from {_display_date(current.first_date)} through '
                f'{_display_date(closed_date)}.',
                type='warning',
                timeout=7.0,
            )
            return
        _store_cost_ranges(
            selected_start,
            selected_end,
            comparison_start,
            comparison_end,
            'Custom date ranges',
        )

    def apply_cost_previous_comparison() -> None:
        current = repository.get()
        span = (state.end_date - state.start_date).days + 1
        comparison_end = state.start_date - timedelta(days=1)
        comparison_start = comparison_end - timedelta(days=span - 1)
        if comparison_start < current.first_date:
            ui.notify(
                'There is not enough earlier meter history for an equal-length preceding period.',
                type='warning',
                timeout=7.0,
            )
            return
        _store_cost_ranges(
            state.start_date,
            state.end_date,
            comparison_start,
            comparison_end,
            'Previous matching period',
        )

    async def check_tariff(*, force: bool = True, notify_user: bool = True) -> None:
        notice = None
        if notify_user:
            notice = ui.notification(
                'Checking the official eThekwini tariff source…',
                spinner=True,
                timeout=None,
            )
        result = await asyncio.to_thread(
            tariff_repository.check_for_updates,
            force=force,
        )
        if notice is not None:
            notice.dismiss()
        content.refresh()
        if notify_user:
            ui.notify(
                result.message,
                type='positive' if result.status in {'current', 'updated'} else 'warning',
                timeout=8.0,
                close_button='Dismiss' if result.status == 'unavailable' else None,
            )

    async def automatic_tariff_check() -> None:
        await check_tariff(force=False, notify_user=False)

    def report_parameters() -> tuple[date, date, date | None, date | None, str]:
        if state.report_type == 'Usage comparison':
            selected_start = state.comparison_current_start or state.start_date
            selected_end = state.comparison_current_end or state.end_date
            comparison_start = state.comparison_previous_start
            comparison_end = state.comparison_previous_end
            if comparison_start is None or comparison_end is None:
                _, _, comparison_start, comparison_end, _ = comparison_dates(
                    'Selected dates', selected_end, selected_start, selected_end
                )
            return (
                selected_start,
                selected_end,
                comparison_start,
                comparison_end,
                state.comparison_type,
            )
        if state.report_type == 'Cost comparison':
            return (
                state.cost_selected_start or state.start_date,
                state.cost_selected_end or state.end_date,
                state.cost_comparison_start,
                state.cost_comparison_end,
                state.cost_comparison_type,
            )
        return state.start_date, state.end_date, None, None, ''

    async def generate_pdf() -> None:
        notice = ui.notification('Creating the PDF report…', spinner=True, timeout=None)
        try:
            report_start, report_end, compare_start, compare_end, method = report_parameters()
            path = await asyncio.to_thread(
                generate_pdf_report,
                repository.get(),
                state.report_type,
                report_end,
                state.area,
                None,
                report_start,
                report_end,
                compare_start,
                compare_end,
                method,
                tariff_repository.rates(),
                tariff_repository.ctou_rates(),
            )
            notice.dismiss()
            ui.download(path)
            ui.notify('PDF report ready.', type='positive')
        except Exception as exc:
            notice.dismiss()
            ui.notify(f'PDF report could not be created: {exc}', type='negative')

    async def export_xlsx() -> None:
        notice = ui.notification('Creating the filtered Excel workbook…', spinner=True, timeout=None)
        try:
            path = await asyncio.to_thread(
                generate_xlsx_export,
                repository.get(), state.start_date, state.end_date, state.area,
            )
            notice.dismiss()
            ui.download(path)
            ui.notify('Excel export ready.', type='positive')
        except Exception as exc:
            notice.dismiss()
            ui.notify(f'Excel export could not be created: {exc}', type='negative')

    async def export_csv() -> None:
        notice = ui.notification('Creating the filtered CSV…', spinner=True, timeout=None)
        try:
            path = await asyncio.to_thread(
                generate_csv_export,
                repository.get(), state.start_date, state.end_date, state.area,
            )
            notice.dismiss()
            ui.download(path)
            ui.notify('CSV export ready.', type='positive')
        except Exception as exc:
            notice.dismiss()
            ui.notify(f'CSV export could not be created: {exc}', type='negative')

    async def sync_portal() -> None:
        if state.syncing:
            return
        event_client = context.client
        started_at = perf_counter()
        try:
            saved = store.read()
        except CredentialError as exc:
            ui.notify(str(exc), type='negative')
            return
        if saved is None:
            ui.notify('Set up the shared PNPSCADA portal login on this PC first.', type='warning')
            credential_dialog.open()
            return
        state.syncing = True
        toolbar_area.refresh()
        sidebar_area.refresh()
        notice = ui.notification('Updating meter data from PNPSCADA…', spinner=True, timeout=None)
        sync_succeeded = False
        success_message = ''
        agent_delivery_message = ''
        agent_delivery_type = 'positive'
        before_sync = repository.get()
        previous_closed_date = _latest_closed_date(before_sync)
        selected_latest_data = state.end_date >= previous_closed_date
        selected_span = max(0, (state.end_date - state.start_date).days)
        try:
            username, password = saved
            event_loop = asyncio.get_running_loop()

            def report_progress(message: str) -> None:
                event_loop.call_soon_threadsafe(
                    setattr, notice, 'message', message.replace('...', '…')
                )

            today = date.today()
            history_start = date.fromisoformat(HISTORY_START_DATE)
            accounts = load_accounts()
            closed_day = today - timedelta(days=1)
            missing_ranges = repository.missing_date_ranges(
                history_start,
                closed_day,
                (account.eid for account in accounts),
            )
            extraction_windows = plan_sync_ranges(
                history_start,
                today,
                missing_ranges,
            )

            total_inserted = 0
            total_replaced = 0
            current = repository.get()
            for requested_start, requested_end in extraction_windows:
                notice.message = (
                    f'Updating {requested_start:%d %b %Y} to '
                    f'{requested_end:%d %b %Y} from PNPSCADA…'
                )
                result = await asyncio.to_thread(
                    run_extraction,
                    DEFAULT_BASE_URL,
                    username,
                    password,
                    requested_start,
                    requested_end,
                    DEFAULT_EXPORT_DIR,
                    progress=report_progress,
                )
                notice.message = 'Saving readings and updating affected analytics…'
                current, database_write = await asyncio.to_thread(
                    repository.ingest_run, result, requested_start, requested_end
                )
                total_inserted += database_write.inserted
                total_replaced += database_write.replaced
            new_closed_date = _latest_closed_date(current)
            if selected_latest_data and state.current_view != 'comparison':
                state.end_date = new_closed_date
                minimum_date = (
                    first_valid_area_date(current, SOLAR_AREA)
                    if state.current_view == 'solar' else current.first_date
                )
                state.start_date = max(
                    minimum_date,
                    state.end_date - timedelta(days=selected_span),
                )
            else:
                state.start_date = max(current.first_date, state.start_date)
                state.end_date = min(new_closed_date, state.end_date)
            start_input.value = state.start_date.isoformat()
            end_input.value = state.end_date.isoformat()
            area_select.options = [ALL_AREAS, *current.areas]
            area_select.update()
            await asyncio.to_thread(
                tariff_repository.check_for_updates,
                force=False,
            )
            success_message = (
                f'Data update complete: {total_inserted:,} new and '
                f'{total_replaced:,} refreshed half-hour readings stored. '
                f'Completed in {perf_counter() - started_at:.0f} seconds.'
            )
            notice.dismiss()
            sync_succeeded = True
            try:
                access_token = await asyncio.to_thread(copilot_auth.access_token)
                if not access_token:
                    raise MailAgentError(
                        'the Microsoft mail session is not connected on this PC'
                    )
                notification_draft = compose_refresh_complete_email(
                    recipient=AUTONOMOUS_REFRESH_RECIPIENT,
                    completed_at=datetime.now().astimezone(),
                    inserted_readings=total_inserted,
                    refreshed_readings=total_replaced,
                    latest_closed_date=new_closed_date,
                    duration_seconds=perf_counter() - started_at,
                )
                mail_result = await asyncio.to_thread(
                    graph_mail_client.send_mail,
                    access_token,
                    notification_draft,
                )
                await asyncio.to_thread(
                    mail_agent_store.record,
                    audit_entry(notification_draft, mail_result),
                )
                agent_delivery_message = (
                    f'Autonomous refresh confirmation sent to '
                    f'{AUTONOMOUS_REFRESH_RECIPIENT}.'
                )
            except Exception as exc:
                agent_delivery_type = 'warning'
                agent_delivery_message = (
                    'Data update completed, but the autonomous confirmation email was not sent: '
                    f'{exc}'
                )
        except Exception as exc:
            notice.dismiss()
            with event_client:
                ui.notify(
                    f'Data update failed: {exc}',
                    type='negative',
                    timeout=10.0,
                    close_button='Dismiss',
                )
        finally:
            state.syncing = False
            toolbar_area.refresh()
            sidebar_area.refresh()
            content.refresh()
            await asyncio.sleep(0)
            content.refresh()
            if sync_succeeded:
                with event_client:
                    ui.notify(success_message, type='positive', timeout=6.0)
                    ui.notify(
                        agent_delivery_message,
                        type=agent_delivery_type,
                        timeout=7.0 if agent_delivery_type == 'positive' else 10.0,
                        close_button=None if agent_delivery_type == 'positive' else 'Dismiss',
                    )

    async def refresh_ai_content_stably(event_client: object) -> None:
        """Rebuild the active Microsoft surface without moving the surrounding page."""
        page_scroll_y: float | None = None
        try:
            with event_client:
                value = await ui.run_javascript('return window.scrollY;', timeout=2.0)
            page_scroll_y = float(value)
        except Exception:
            pass
        if state.current_view == 'agent':
            content.refresh()
        else:
            ai_content_area.refresh()
        await asyncio.sleep(0.05)
        if page_scroll_y is not None:
            try:
                with event_client:
                    await ui.run_javascript(
                        f"window.scrollTo({{top: {page_scroll_y}, left: 0, behavior: 'instant'}});",
                        timeout=2.0,
                    )
            except Exception:
                pass

    async def ai_sign_in() -> None:
        if state.ai_busy:
            return
        event_client = context.client
        state.ai_busy = True
        state.ai_connection_note = 'Opening Microsoft organisational sign-in…'
        if state.current_view == 'agent':
            state.agent_note = 'Opening Microsoft organisational sign-in in the default browser...'
        await refresh_ai_content_stably(event_client)
        with event_client:
            notice = ui.notification(
                'Opening Microsoft sign-in in your default browser…',
                spinner=True,
                timeout=None,
            )
        result_message = ''
        result_type = 'info'
        try:
            account = await asyncio.to_thread(copilot_auth.sign_in)
            state.ai_connection_note = (
                f'Microsoft Copilot connected for {account.username}.'
            )
            state.agent_note = (
                f'Microsoft mail and recipient search active for {account.username}.'
            )
            state.ai_conversation_id = None
            result_message = 'Microsoft Copilot, mail and recipient search connected.'
            result_type = 'positive'
        except CopilotConnectionError as exc:
            state.ai_connection_note = str(exc)
            state.agent_note = str(exc)
            result_message = str(exc)
            result_type = 'warning'
        except Exception as exc:
            state.ai_connection_note = f'Microsoft sign-in could not be completed: {exc}'
            state.agent_note = state.ai_connection_note
            result_message = state.ai_connection_note
            result_type = 'negative'
        finally:
            notice.dismiss()
            state.ai_busy = False
            await refresh_ai_content_stably(event_client)
            with event_client:
                ui.notify(
                    result_message,
                    type=result_type,
                    timeout=6.0 if result_type == 'positive' else 10.0,
                    close_button=None if result_type == 'positive' else 'Dismiss',
                )

    async def ai_device_sign_in() -> None:
        if state.ai_busy:
            return
        event_client = context.client
        state.ai_busy = True
        state.agent_note = 'Requesting a Microsoft sign-in code...'
        state.ai_connection_note = state.agent_note
        await refresh_ai_content_stably(event_client)
        with event_client:
            notice = ui.notification(
                'Requesting a Microsoft sign-in code...',
                spinner=True,
                timeout=None,
            )
        result_message = ''
        result_type = 'info'
        try:
            device_sign_in = await asyncio.to_thread(copilot_auth.begin_device_sign_in)
            state.microsoft_device_sign_in = device_sign_in
            state.agent_note = (
                f'Enter code {device_sign_in.user_code} on the Microsoft sign-in page.'
            )
            state.ai_connection_note = state.agent_note
            notice.message = 'Waiting for the Microsoft sign-in code to be completed...'
            await refresh_ai_content_stably(event_client)
            account = await asyncio.to_thread(
                copilot_auth.complete_device_sign_in,
                device_sign_in,
            )
            state.ai_connection_note = f'Microsoft Copilot connected for {account.username}.'
            state.agent_note = (
                f'Microsoft mail and recipient search active for {account.username}.'
            )
            state.ai_conversation_id = None
            result_message = 'Microsoft Copilot, mail and recipient search connected.'
            result_type = 'positive'
        except CopilotConnectionError as exc:
            state.ai_connection_note = str(exc)
            state.agent_note = str(exc)
            result_message = str(exc)
            result_type = 'warning'
        except Exception as exc:
            state.ai_connection_note = f'Microsoft sign-in could not be completed: {exc}'
            state.agent_note = state.ai_connection_note
            result_message = state.ai_connection_note
            result_type = 'negative'
        finally:
            state.microsoft_device_sign_in = None
            state.ai_busy = False
            notice.dismiss()
            await refresh_ai_content_stably(event_client)
            with event_client:
                ui.notify(
                    result_message,
                    type=result_type,
                    timeout=6.0 if result_type == 'positive' else 10.0,
                    close_button=None if result_type == 'positive' else 'Dismiss',
                )

    async def ai_sign_out() -> None:
        event_client = context.client
        await asyncio.to_thread(copilot_auth.sign_out)
        state.ai_conversation_id = None
        state.ai_connection_note = 'Signed out of Microsoft Copilot on this PC.'
        state.agent_note = 'Signed out of Microsoft on this PC.'
        await refresh_ai_content_stably(event_client)
        with event_client:
            ui.notify('Microsoft Copilot signed out.', type='info', timeout=5.0)

    async def scroll_ai_conversation(
        event_client: object,
        page_scroll_y: float | None = None,
    ) -> None:
        """Keep the newest turn visible without moving the surrounding page."""
        await asyncio.sleep(0.05)
        try:
            restore_page = ''
            if page_scroll_y is not None:
                restore_page = f"""
                    window.scrollTo(0, {page_scroll_y});
                    requestAnimationFrame(() => window.scrollTo(0, {page_scroll_y}));
                """
            with event_client:
                await ui.run_javascript(
                    f"""
                    const chat = document.getElementById('ai-conversation-scroll');
                    if (chat) {{
                        chat.scrollTo({{top: chat.scrollHeight, behavior: 'smooth'}});
                    }}
                    {restore_page}
                    """,
                    timeout=2.0,
                )
        except Exception:
            # A disconnected browser must not turn a completed analysis into an error.
            pass

    async def refresh_ai_messages_stably(
        event_client: object,
        page_scroll_y: float | None = None,
    ) -> None:
        """Refresh chat messages while preserving the user's page position."""
        if page_scroll_y is None:
            try:
                with event_client:
                    value = await ui.run_javascript('return window.scrollY;', timeout=2.0)
                page_scroll_y = float(value)
            except Exception:
                pass
        ai_messages_area.refresh()
        await scroll_ai_conversation(event_client, page_scroll_y)

    async def ask_ai_hub(
        question: str,
        initial_page_scroll_y: float | None = None,
    ) -> None:
        cleaned = ' '.join(str(question).split()).strip()
        if not cleaned:
            ui.notify('Enter an electricity-data question first.', type='warning')
            return
        if state.ai_busy:
            ui.notify('Please wait for the current answer.', type='info')
            return
        event_client = context.client
        if initial_page_scroll_y is None:
            try:
                with event_client:
                    value = await ui.run_javascript('return window.scrollY;', timeout=2.0)
                initial_page_scroll_y = float(value)
            except Exception:
                pass
        prior_user_question = next(
            (
                message.get('text', '')
                for message in reversed(state.ai_messages)
                if message.get('role') == 'user'
            ),
            '',
        )
        state.ai_messages.append({'role': 'user', 'text': cleaned, 'source': 'You'})
        state.ai_busy = True
        state.ai_connection_note = ''
        await refresh_ai_messages_stably(event_client, initial_page_scroll_y)
        try:
            current = repository.get()
            database_start, database_end, database_area = ai_database_scope(current)
            retain_ai_scope = bool(
                prior_user_question and is_contextual_ai_follow_up(cleaned)
            )
            if not retain_ai_scope:
                # Do not let an earlier Microsoft conversation carry a narrow
                # date/area assumption into a new independent question.
                state.ai_conversation_id = None
            evidence_start = (
                state.ai_context_start
                if retain_ai_scope and state.ai_context_start is not None
                else database_start
            )
            evidence_end = (
                state.ai_context_end
                if retain_ai_scope and state.ai_context_end is not None
                else database_end
            )
            evidence_area = (
                state.ai_context_area
                if retain_ai_scope and state.ai_context_area is not None
                else database_area
            )
            evidence = await asyncio.to_thread(
                build_ai_evidence,
                current,
                cleaned,
                evidence_start,
                evidence_end,
                evidence_area,
                tariff_repository.rates(),
                tariff_repository.ctou_rates(),
                prior_question=prior_user_question if retain_ai_scope else '',
            )
            if evidence.allowed:
                state.ai_context_start = evidence.start_date
                state.ai_context_end = evidence.end_date
                state.ai_context_area = evidence.area
            if not evidence.allowed:
                state.ai_messages.append({
                    'role': 'assistant',
                    'source': 'Electricity scope control',
                    'text': evidence.local_answer,
                })
                return
            if evidence.clarification:
                state.ai_messages.append({
                    'role': 'assistant',
                    'source': 'AI Hub · clarification',
                    'text': evidence.clarification,
                })
                return

            access_token = await asyncio.to_thread(copilot_auth.access_token)
            if not access_token:
                state.ai_messages.append({
                    'role': 'assistant',
                    'source': 'Verified local analysis',
                    'text': (
                        f'{evidence.local_answer}\n\n'
                        'Microsoft explanation is not connected yet. This answer was calculated '
                        'locally from the same app data and excludes incomplete readings.'
                    ),
                })
                if copilot_auth.status().signed_in:
                    state.ai_connection_note = (
                        'The Microsoft session needs renewed permission. Sign out and sign in again after administrator approval.'
                    )
                return

            try:
                reply = await asyncio.to_thread(
                    copilot_client.ask,
                    access_token,
                    evidence,
                    state.ai_conversation_id,
                )
                state.ai_conversation_id = reply.conversation_id
                state.ai_messages.append({
                    'role': 'assistant',
                    'source': 'Microsoft Copilot · grounded in app data',
                    'text': reply.text,
                })
                state.ai_connection_note = 'The latest answer used Microsoft Copilot with web search disabled.'
            except CopilotConnectionError as exc:
                state.ai_connection_note = str(exc)
                if exc.status_code in {401, 403}:
                    state.ai_conversation_id = None
                state.ai_messages.append({
                    'role': 'assistant',
                    'source': 'Verified local analysis',
                    'text': (
                        f'{evidence.local_answer}\n\n'
                        f'Microsoft explanation was unavailable: {exc}'
                    ),
                })
        except Exception as exc:
            state.ai_connection_note = f'The question could not be analysed: {exc}'
            state.ai_messages.append({
                'role': 'assistant',
                'source': 'AI Hub',
                'text': state.ai_connection_note,
            })
        finally:
            state.ai_busy = False
            # Keep the current session readable without allowing it to grow
            # indefinitely on an employee workstation.
            state.ai_messages[:] = state.ai_messages[-30:]
            await refresh_ai_messages_stably(event_client)

    async def clear_ai_hub() -> None:
        state.ai_messages.clear()
        state.ai_conversation_id = None
        state.ai_connection_note = ''
        state.ai_context_start = None
        state.ai_context_end = None
        state.ai_context_area = None
        ai_chat_area.refresh()

    @ui.refreshable
    def sidebar_area() -> None:
        current = repository.get()
        with ui.column().classes('sidebar-stack w-full'):
            with ui.row().classes('w-full items-center justify-between gap-2'):
                if not state.sidebar_collapsed:
                    ui.label('Energy Console').classes('sidebar-brand-text muted-text text-xs font-bold uppercase')
                ui.button(
                    icon='keyboard_double_arrow_right' if state.sidebar_collapsed else 'keyboard_double_arrow_left',
                    on_click=toggle_sidebar,
                ).props('flat round dense').classes('toolbar-action sidebar-collapse')
            with ui.element('div').classes('logo-plate w-full'):
                if YMS_LOGO_DIR.exists():
                    filename = (
                        'Connect-Logistics-Icon.png' if state.sidebar_collapsed
                        else 'Connect-Logistics-Main Logo.png'
                    )
                    ui.image(f"/connect-brand/{filename.replace(' ', '%20')}").classes('brand-logo')
                else:
                    ui.icon('electric_bolt', size='58px', color='orange')
            with ui.column().classes('w-full gap-1'):
                for key, label, icon in NAV_ITEMS:
                    with ui.button(on_click=lambda name=key: set_view(name)).props(
                        'flat no-caps'
                    ).classes('nav-btn active' if state.current_view == key else 'nav-btn') as button:
                        ui.icon(icon)
                        ui.label(label).classes('sidebar-label')
                    if state.sidebar_collapsed:
                        button.tooltip(label)
            with ui.column().classes('sidebar-meta glass-panel w-full p-3 gap-1'):
                ui.label('METER DATA').classes('muted-text text-xs font-bold')
                ui.label(f'Stored through {_display_date(current.last_date)}').classes('body-text text-xs mono')
                ui.label(
                    f'Analytics through {_display_date(_latest_closed_date(current))}'
                ).classes('muted-text text-xs')
                ui.label(f'{len(current.areas)} areas · {len(current.intervals):,} readings').classes('muted-text text-xs')
                ui.label('Updating now…' if state.syncing else 'Ready').classes('sidebar-status text-xs font-bold')
            with ui.row().classes('sidebar-bottom glass-panel w-full items-center gap-2 p-3'):
                ui.icon('shield', size='19px', color='teal')
                with ui.column().classes('sidebar-label gap-0'):
                    ui.label('Internal management').classes('body-text text-xs font-bold')
                    ui.label('Connect Logistics').classes('muted-text text-xs')

    @ui.refreshable
    def toolbar_area() -> None:
        current = repository.get()
        database_start, database_end, _ = ai_database_scope(current)
        with ui.row().classes('toolbar w-full items-center justify-between flex-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Sydney Road Electricity Monitor').classes('toolbar-title')
                ui.label('Daily electricity use, demand, cost and unusual-reading review').classes('toolbar-subtitle')
            with ui.row().classes('items-center gap-2'):
                toolbar_scope = (
                    f'AI access: {_display_date(database_start)} to '
                    f'{_display_date(database_end)} · all meter areas'
                    if state.current_view == 'ai' else
                    f'Analytics through {_display_date(state.end_date)}'
                )
                ui.label(toolbar_scope).classes('muted-text text-xs')
                ui.button(
                    'Updating…' if state.syncing else 'Update meter data',
                    icon='sync',
                    on_click=sync_portal,
                ).props(f"flat no-caps {'loading disable' if state.syncing else ''}").classes('primary-action')

    def current_filtered_view() -> DashboardView:
        current = repository.get()
        analysis_dataset = (
            without_area(current, SOLAR_AREA)
            if state.area == ALL_AREAS and SOLAR_AREA in current.areas
            else current
        )
        return build_dashboard_view(
            analysis_dataset,
            state.start_date,
            state.end_date,
            state.area,
        )

    def full_ai_view() -> DashboardView:
        current = repository.get()
        database_start, database_end, database_area = ai_database_scope(current)
        warehouse_dataset = (
            without_area(current, SOLAR_AREA)
            if SOLAR_AREA in current.areas else current
        )
        return build_dashboard_view(
            warehouse_dataset,
            database_start,
            database_end,
            database_area,
        )

    def select_agent_alert(value: str) -> None:
        state.agent_alert_id = value or 'period-summary'
        state.agent_subject = ''
        state.agent_body = ''
        state.agent_draft_source = ''
        state.agent_reviewed = False
        state.agent_note = ''
        content.refresh()

    def save_agent_preferences() -> None:
        try:
            preferences = RecipientPreferences(
                to_recipients=parse_recipient_text(state.agent_to_text),
                cc_recipients=parse_recipient_text(state.agent_cc_text),
                sender_mailbox=state.agent_sender_mailbox,
            )
            mail_agent_store.save_preferences(preferences)
            ui.notify('Agent recipients saved on this PC.', type='positive', timeout=5.0)
        except MailAgentError as exc:
            ui.notify(str(exc), type='negative', timeout=8.0, close_button='Dismiss')

    def add_agent_recipient(email: str) -> None:
        existing = list(parse_recipient_text(state.agent_to_text))
        if email and email.casefold() not in {value.casefold() for value in existing}:
            existing.append(email)
        state.agent_to_text = '; '.join(existing)
        state.agent_reviewed = False
        state.agent_directory_results = []
        content.refresh()

    async def search_agent_directory(query: str) -> None:
        if state.agent_busy:
            return
        state.agent_busy = True
        state.agent_note = 'Searching Microsoft People for company recipients...'
        content.refresh()
        try:
            token = await asyncio.to_thread(copilot_auth.access_token)
            if not token:
                raise MailAgentError(
                    'The Microsoft People session is not connected. '
                    'Select Sign in with Microsoft to issue a new approved token.'
                )
            state.agent_directory_results = list(
                await asyncio.to_thread(graph_mail_client.search_directory, token, query)
            )
            state.agent_note = (
                f'{len(state.agent_directory_results)} matching employee(s) found.'
                if state.agent_directory_results
                else 'No matching employees were found.'
            )
        except (MailAgentError, CopilotConnectionError) as exc:
            state.agent_directory_results = []
            state.agent_note = str(exc)
            ui.notify(str(exc), type='warning', timeout=9.0, close_button='Dismiss')
        except Exception as exc:
            state.agent_directory_results = []
            state.agent_note = f'Recipient search could not be completed: {exc}'
            ui.notify(state.agent_note, type='negative', timeout=9.0, close_button='Dismiss')
        finally:
            state.agent_busy = False
            content.refresh()

    def selected_agent_alert(view: DashboardView) -> dict[str, object] | None:
        alert_rows = [
            row for row in view.spikes
            if str(row.get('alert_level') or '') in {'Critical', 'High', 'Watch'}
        ]
        for index, row in enumerate(alert_rows, start=1):
            key = str(row.get('spike_id') or f'alert-{index}')
            if key == state.agent_alert_id:
                return row
        return None

    def local_agent_draft(view: DashboardView) -> MailDraft:
        if state.agent_alert_id == 'period-summary':
            metrics = view.metrics
            draft = compose_period_summary_email(
                period_start=view.start_date,
                period_end=view.end_date,
                area=view.area,
                total_kwh=float(metrics.get('total_import_kwh') or 0.0),
                peak_kw=float(metrics.get('peak_kw') or 0.0),
                peak_kw_time=str(metrics.get('peak_kw_time') or ''),
                peak_kva=float(metrics.get('peak_kva') or 0.0),
                peak_kva_time=str(metrics.get('peak_time') or ''),
                alert_count=int(metrics.get('alert_days') or 0),
            )
        else:
            alert = selected_agent_alert(view)
            if alert is None:
                raise MailAgentError(
                    'The selected alert is no longer available for the active filters. '
                    'Choose another alert and prepare the draft again.'
                )
            draft = compose_alert_email(
                alert,
                period_start=view.start_date,
                period_end=view.end_date,
            )
        return MailDraft(
            subject=draft.subject,
            body=draft.body,
            to_recipients=parse_recipient_text(state.agent_to_text),
            cc_recipients=parse_recipient_text(state.agent_cc_text),
            sender_mailbox=state.agent_sender_mailbox,
            source=draft.source,
            alert_id=draft.alert_id,
        )

    async def prepare_agent_draft() -> None:
        if state.agent_busy:
            return
        state.agent_busy = True
        state.agent_reviewed = False
        state.agent_note = 'Preparing a draft from verified app evidence...'
        content.refresh()
        try:
            view = current_filtered_view()
            fallback = local_agent_draft(view)
            prepared = fallback
            token = await asyncio.to_thread(copilot_auth.access_token)
            if token:
                if state.agent_alert_id == 'period-summary':
                    question = (
                        f'Draft a concise professional internal electricity summary email for '
                        f'{view.area} from {view.start_date.isoformat()} to {view.end_date.isoformat()}. '
                        'Use only verified app evidence. Include a clear subject line, the important '
                        'kWh, kW, kVA and unusual-usage facts, practical follow-up actions, and do not '
                        'claim a physical cause that the meter data cannot prove.'
                    )
                else:
                    alert = selected_agent_alert(view) or {}
                    question = (
                        f'Draft a concise professional internal electricity alert email about '
                        f"{alert.get('area') or view.area} on {str(alert.get('date') or view.end_date)}. "
                        'Use only verified app evidence. Include a clear subject line, severity, exact '
                        'kWh, kW and kVA evidence where available, the recorded time, likely checks and '
                        'actions. Clearly distinguish confirmed meter evidence from possible causes.'
                    )
                evidence = await asyncio.to_thread(
                    build_ai_evidence,
                    repository.get(),
                    question,
                    view.start_date,
                    view.end_date,
                    view.area,
                    tariff_repository.rates(),
                    tariff_repository.ctou_rates(),
                )
                reply = await asyncio.to_thread(copilot_client.ask, token, evidence, None)
                prepared = parse_copilot_email(reply.text, fallback)
                state.agent_note = (
                    'Copilot prepared the wording from the app evidence. Review every field before sending.'
                )
            else:
                state.agent_note = (
                    'A verified local draft was prepared. Copilot rewriting becomes available after '
                    'Microsoft sign-in issues the approved token.'
                )
            state.agent_subject = prepared.subject
            state.agent_body = prepared.body
            state.agent_draft_source = prepared.source
        except (MailAgentError, CopilotConnectionError) as exc:
            try:
                fallback = local_agent_draft(current_filtered_view())
                state.agent_subject = fallback.subject
                state.agent_body = fallback.body
                state.agent_draft_source = fallback.source
                state.agent_note = f'{exc} A verified local draft was prepared instead.'
                ui.notify(state.agent_note, type='warning', timeout=10.0, close_button='Dismiss')
            except MailAgentError:
                state.agent_note = str(exc)
                ui.notify(str(exc), type='negative', timeout=10.0, close_button='Dismiss')
        except Exception as exc:
            state.agent_note = f'The draft could not be prepared: {exc}'
            ui.notify(state.agent_note, type='negative', timeout=10.0, close_button='Dismiss')
        finally:
            state.agent_busy = False
            content.refresh()

    async def agent_mail_draft(*, include_attachment: bool) -> MailDraft:
        base_draft = MailDraft(
            subject=state.agent_subject,
            body=state.agent_body,
            to_recipients=parse_recipient_text(state.agent_to_text),
            cc_recipients=parse_recipient_text(state.agent_cc_text),
            sender_mailbox=state.agent_sender_mailbox,
            source=state.agent_draft_source or 'Reviewed in-app draft',
            alert_id=state.agent_alert_id,
        ).validated()
        attachment_paths: tuple[str, ...] = ()
        if include_attachment:
            path = await asyncio.to_thread(
                generate_pdf_report,
                repository.get(),
                'Filtered overview',
                state.end_date,
                state.area,
                None,
                state.start_date,
                state.end_date,
                None,
                None,
                '',
                tariff_repository.rates(),
                tariff_repository.ctou_rates(),
            )
            attachment_paths = (str(path),)
        return MailDraft(
            subject=base_draft.subject,
            body=base_draft.body,
            to_recipients=base_draft.to_recipients,
            cc_recipients=base_draft.cc_recipients,
            sender_mailbox=base_draft.sender_mailbox,
            source=base_draft.source,
            alert_id=base_draft.alert_id,
            attachment_paths=attachment_paths,
        ).validated()

    async def create_agent_outlook_draft() -> None:
        if state.agent_busy:
            return
        state.agent_busy = True
        state.agent_note = 'Creating the reviewed draft in Outlook...'
        content.refresh()
        try:
            token = await asyncio.to_thread(copilot_auth.access_token)
            if not token:
                raise MailAgentError(
                    'The Outlook session is not connected. '
                    'Select Sign in with Microsoft to issue a new approved token.'
                )
            draft = await agent_mail_draft(include_attachment=state.agent_attach_pdf)
            result = await asyncio.to_thread(graph_mail_client.create_draft, token, draft)
            await asyncio.to_thread(mail_agent_store.record, audit_entry(draft, result))
            state.agent_note = 'The email was saved in Outlook Drafts and recorded locally.'
            ui.notify(state.agent_note, type='positive', timeout=6.0)
        except (MailAgentError, CopilotConnectionError) as exc:
            state.agent_note = str(exc)
            ui.notify(str(exc), type='warning', timeout=10.0, close_button='Dismiss')
        except Exception as exc:
            state.agent_note = f'The Outlook draft could not be created: {exc}'
            ui.notify(state.agent_note, type='negative', timeout=10.0, close_button='Dismiss')
        finally:
            state.agent_busy = False
            content.refresh()

    async def send_agent_email() -> None:
        if state.agent_busy:
            return
        if not state.agent_reviewed:
            ui.notify('Review and approve the email before sending.', type='warning')
            return
        state.agent_busy = True
        state.agent_note = 'Sending the approved email through Microsoft Graph...'
        content.refresh()
        try:
            token = await asyncio.to_thread(copilot_auth.access_token)
            if not token:
                raise MailAgentError(
                    'The Microsoft mail session is not connected. '
                    'Select Sign in with Microsoft to issue a new approved token.'
                )
            draft = await agent_mail_draft(include_attachment=state.agent_attach_pdf)
            result = await asyncio.to_thread(graph_mail_client.send_mail, token, draft)
            await asyncio.to_thread(mail_agent_store.record, audit_entry(draft, result))
            state.agent_reviewed = False
            state.agent_note = 'The approved email was sent and recorded locally.'
            ui.notify(state.agent_note, type='positive', timeout=6.0)
        except (MailAgentError, CopilotConnectionError) as exc:
            state.agent_note = str(exc)
            ui.notify(str(exc), type='warning', timeout=10.0, close_button='Dismiss')
        except Exception as exc:
            state.agent_note = f'The email could not be sent: {exc}'
            ui.notify(state.agent_note, type='negative', timeout=10.0, close_button='Dismiss')
        finally:
            state.agent_busy = False
            content.refresh()

    @ui.refreshable
    def ai_messages_area() -> None:
        _ai_conversation_panel(state.ai_messages, state.ai_busy)

    @ui.refreshable
    def ai_chat_area() -> None:
        _ai_chat_panel(
            full_ai_view(),
            state.ai_messages,
            state.ai_busy,
            ask_ai_hub,
            clear_ai_hub,
            ai_messages_area,
        )

    @ui.refreshable
    def ai_content_area() -> None:
        """Refresh the complete Hub only for sign-in or page-level changes."""
        view = full_ai_view()
        _ai_hub(
            view,
            copilot_auth.status(),
            state.ai_messages,
            state.ai_busy,
            state.ai_connection_note,
            ai_sign_in,
            ai_sign_out,
            ask_ai_hub,
            clear_ai_hub,
            ai_chat_area,
        )

    @ui.refreshable
    def content() -> None:
        current = repository.get()
        full_view = build_dashboard_view(current, state.start_date, state.end_date, state.area)
        solar_view: DashboardView | None = None
        balance: SupplyDemandBalance | None = None
        analysis_dataset = current
        view = full_view
        if state.area == ALL_AREAS and SOLAR_AREA in current.areas:
            analysis_dataset = without_area(current, SOLAR_AREA)
            view = build_dashboard_view(
                analysis_dataset, state.start_date, state.end_date, ALL_AREAS
            )
            solar_view = build_dashboard_view(
                current, state.start_date, state.end_date, SOLAR_AREA
            )
            if state.current_view in {'overview', 'supply'}:
                balance = build_supply_demand_balance(
                    current, state.start_date, state.end_date
                )
        with ui.column().classes('w-full gap-4 fade-in'):
            if state.current_view not in {'comparison', 'ai'}:
                cost_scope = (
                    'all warehouse areas; matched solar savings included'
                    if state.current_view == 'costs' and view.area == ALL_AREAS
                    else f'{view.area}; site solar not allocated'
                    if state.current_view == 'costs'
                    else 'site-wide PNPSCADA performance; proposal values shown separately'
                    if state.current_view == 'solar_investment'
                    else None
                )
                _filter_summary(view, cost_scope)
            if state.current_view == 'overview':
                _overview(
                    view, solar_view, balance,
                    on_supply_details=lambda: set_view('supply'),
                )
            elif state.current_view == 'trends':
                _trends(analysis_dataset, view)
            elif state.current_view == 'supply':
                if balance is None:
                    balance = build_supply_demand_balance(
                        current, state.start_date, state.end_date
                    )
                _supply_demand(balance, view)
            elif state.current_view == 'solar':
                _solar_performance(
                    build_solar_performance(current, state.start_date, state.end_date)
                )
            elif state.current_view == 'solar_investment':
                span = (state.end_date - state.start_date).days + 1
                investment_comparison_end = state.start_date - timedelta(days=1)
                if investment_comparison_end < current.first_date:
                    investment_comparison_start = state.start_date
                    investment_comparison_end = state.end_date
                else:
                    investment_comparison_start = max(
                        current.first_date,
                        investment_comparison_end - timedelta(days=span - 1),
                    )
                investment_analysis = build_cost_analysis(
                    current,
                    state.start_date,
                    state.end_date,
                    ALL_AREAS,
                    tariff_repository.rates(),
                    investment_comparison_start,
                    investment_comparison_end,
                    tariff_repository.ctou_rates(),
                )
                _solar_investment(investment_analysis)
            elif state.current_view == 'comparison':
                exact_comparison_types = {
                    'Year vs year', 'Month vs month', 'Week vs week', 'Custom ranges'
                }
                comparison = build_period_comparison(
                    current,
                    state.comparison_type,
                    state.end_date,
                    state.area,
                    state.start_date
                    if state.comparison_type in exact_comparison_types else None,
                    state.end_date
                    if state.comparison_type in exact_comparison_types else None,
                    state.comparison_previous_start
                    if state.comparison_type in exact_comparison_types else None,
                    state.comparison_previous_end
                    if state.comparison_type in exact_comparison_types else None,
                )
                _comparisons(
                    comparison,
                    set_comparison_type,
                    apply_named_comparison_periods,
                    apply_comparison_ranges,
                    current.first_date,
                    _latest_closed_date(current),
                )
            elif state.current_view == 'costs':
                comparison_start = state.cost_comparison_start or state.start_date
                comparison_end = state.cost_comparison_end or state.end_date
                analysis = build_cost_analysis(
                    current,
                    state.start_date,
                    state.end_date,
                    state.area,
                    tariff_repository.rates(),
                    comparison_start,
                    comparison_end,
                    tariff_repository.ctou_rates(),
                )
                _cost_center(
                    analysis,
                    tariff_repository.status(),
                    state.cost_comparison_type,
                    _available_month_options(
                        current.first_date,
                        _latest_closed_date(current).replace(day=1) - timedelta(days=1),
                    ),
                    state.cost_selected_month or analysis.start_date.strftime('%Y-%m'),
                    state.cost_compare_month or analysis.comparison_start.strftime('%Y-%m'),
                    set_cost_comparison_type,
                    apply_cost_month_comparison,
                    apply_cost_current_month_comparison,
                    apply_cost_custom_comparison,
                    apply_cost_previous_comparison,
                    check_tariff,
                )
            elif state.current_view == 'alerts':
                _alerts(full_view)
            elif state.current_view == 'ai':
                ai_content_area()
            elif state.current_view == 'agent':
                _agent_center(
                    view,
                    copilot_auth.status(),
                    state,
                    mail_agent_store.history(),
                    select_agent_alert,
                    prepare_agent_draft,
                    search_agent_directory,
                    add_agent_recipient,
                    save_agent_preferences,
                    create_agent_outlook_draft,
                    send_agent_email,
                    ai_sign_in,
                    ai_device_sign_in,
                )
            elif state.current_view == 'reports':
                report_start, report_end, compare_start, compare_end, method = report_parameters()
                _reports_center(
                    current, state.report_type, report_end, state.area,
                    report_start, report_end, compare_start, compare_end, method,
                    set_report_type,
                    generate_pdf, export_xlsx, export_csv,
                )
            else:
                _data_panel(current, credential_available, credential_dialog.open)

    with ui.row().classes('app-shell w-full'):
        sidebar_panel = ui.column().classes('sidebar')
        with sidebar_panel:
            sidebar_area()
        with ui.column().classes('app-main gap-0'):
            toolbar_area()
            with ui.column().classes('content-wrap'):
                with ui.card().classes('app-card w-full p-4 shadow-none') as filter_card:
                    with ui.element('div').classes('filter-grid w-full'):
                        period_select = ui.select(
                            list(PERIOD_OPTIONS),
                            value='Last 30 days',
                            label='Time period',
                            on_change=lambda event: set_period(PERIOD_OPTIONS[str(event.value)]),
                        ).props('outlined dense options-dense')
                        area_select = ui.select(
                            [ALL_AREAS, *dataset.areas], value=ALL_AREAS, label='Meter area'
                        ).props('outlined dense options-dense')
                        start_input = ui.input('From', value=state.start_date.isoformat()).props(
                            'type=date outlined dense'
                        )
                        end_input = ui.input('To', value=state.end_date.isoformat()).props(
                            'type=date outlined dense'
                        )
                        ui.button('Apply', icon='filter_alt', on_click=apply_filters).props(
                            'flat no-caps'
                        ).classes('primary-action')
                filter_card.set_visibility(state.current_view != 'ai')
                content()
                ui.label(
                    'Internal management view · Reconcile meter figures with billing and operational records before external use.'
                ).classes('footer-note w-full mt-2')
    ui.timer(2.0, automatic_tariff_check, once=True)
    ui.timer(3600.0, automatic_tariff_check)


def main(*, show: bool = True, port: int = 8080) -> None:
    ui.run(
        host='127.0.0.1',
        port=port,
        title='Sydney Road Electricity Monitor',
        favicon=str(DEFAULT_BRAND_DIR / 'Connect-Logistics-Icon.png'),
        show=show,
        reload=False,
        dark=False,
        language='en-US',
        prod_js=True,
    )
