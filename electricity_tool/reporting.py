from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Iterable

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .config import DEFAULT_BRAND_DIR, DEFAULT_OUTPUT_DIR
from .dashboard_data import (
    ALL_AREAS,
    SOLAR_AREA,
    DashboardDataset,
    DashboardView,
    build_dashboard_view,
    without_area,
)
from .tariffs import (
    BUILT_IN_CTOU_RATES,
    BUILT_IN_RATES,
    CtouTariffRate,
    CostAnalysis,
    TariffRate,
    build_cost_analysis,
)


REPORT_TYPES = (
    'Current month overview',
    'Filtered overview',
    'Usage comparison',
    'Cost comparison',
)
NAVY = colors.HexColor('#1C2545')
ORANGE = colors.HexColor('#E04403')
TEAL = colors.HexColor('#007D6D')
MUTED = colors.HexColor('#64748B')
PALE = colors.HexColor('#F4F7FB')
BORDER = colors.HexColor('#D6DEEA')
WHITE = colors.white


def _date_label(start_date: date, end_date: date) -> str:
    if start_date == end_date:
        return start_date.strftime('%d %B %Y')
    return f'{start_date:%d %B %Y} to {end_date:%d %B %Y}'


@dataclass(frozen=True)
class ReportPeriod:
    report_type: str
    start_date: date
    end_date: date
    requested_date: date
    comparison_start: date | None = None
    comparison_end: date | None = None
    comparison_method: str = ''

    @property
    def comparison_enabled(self) -> bool:
        return self.comparison_start is not None and self.comparison_end is not None

    @property
    def label(self) -> str:
        return _date_label(self.start_date, self.end_date)

    @property
    def note(self) -> str:
        if self.report_type == 'Current month overview':
            return 'Current month through the latest closed meter day. No comparison is applied.'
        if self.report_type == 'Filtered overview':
            return 'Exact active app filters. No comparison is applied.'
        if self.comparison_enabled:
            method = self.comparison_method or 'Selected comparison method'
            return (
                f'{method}: {_date_label(self.start_date, self.end_date)} compared with '
                f'{_date_label(self.comparison_start, self.comparison_end)}.'
            )
        return 'Exact available meter data for the selected period.'


class ReportGenerationError(RuntimeError):
    pass


def resolve_report_period(
    report_type: str,
    anchor_date: date,
    dataset: DashboardDataset,
    *,
    today: date | None = None,
    selected_start: date | None = None,
    selected_end: date | None = None,
    comparison_start: date | None = None,
    comparison_end: date | None = None,
    comparison_method: str = '',
) -> ReportPeriod:
    current_day = today or date.today()
    latest_closed = min(dataset.last_date, current_day - timedelta(days=1))
    if latest_closed < dataset.first_date:
        raise ValueError('No closed meter day is available for this report.')

    if report_type == 'Current month overview':
        start = max(dataset.first_date, latest_closed.replace(day=1))
        end = latest_closed
        return ReportPeriod(report_type, start, end, latest_closed)

    if report_type in {'Filtered overview', 'Usage comparison', 'Cost comparison'}:
        if selected_start is None or selected_end is None:
            raise ValueError('This report requires the active app From and To dates.')
        if selected_end < selected_start:
            raise ValueError('The selected report end date cannot be before its start date.')
        start = max(selected_start, dataset.first_date)
        end = min(selected_end, latest_closed)
        if end < start:
            raise ValueError('The selected dates do not contain a closed meter day.')
        if report_type == 'Filtered overview':
            return ReportPeriod(report_type, start, end, selected_end)
        if comparison_start is None or comparison_end is None:
            raise ValueError('Comparison reports require both comparison dates.')
        if comparison_end < comparison_start:
            raise ValueError('The comparison end date cannot be before its start date.')
        compare_start = max(comparison_start, dataset.first_date)
        compare_end = min(comparison_end, latest_closed)
        if compare_end < compare_start:
            raise ValueError('The comparison dates do not contain available closed meter data.')
        return ReportPeriod(
            report_type,
            start,
            end,
            selected_end,
            compare_start,
            compare_end,
            comparison_method or report_type,
        )

    raise ValueError(f'Unknown report type: {report_type}')


def _build_report_view(
    dataset: DashboardDataset,
    period: ReportPeriod,
    area: str,
) -> DashboardView:
    comparison_start = period.comparison_start or period.start_date
    comparison_end = period.comparison_end or period.end_date
    return build_dashboard_view(
        dataset,
        period.start_date,
        period.end_date,
        area,
        comparison_start_date=comparison_start,
        comparison_end_date=comparison_end,
    )


def report_view(
    dataset: DashboardDataset,
    report_type: str,
    anchor_date: date,
    area: str,
    selected_start: date | None = None,
    selected_end: date | None = None,
    comparison_start: date | None = None,
    comparison_end: date | None = None,
    comparison_method: str = '',
) -> tuple[ReportPeriod, DashboardView]:
    period = resolve_report_period(
        report_type,
        anchor_date,
        dataset,
        selected_start=selected_start,
        selected_end=selected_end,
        comparison_start=comparison_start,
        comparison_end=comparison_end,
        comparison_method=comparison_method,
    )
    report_dataset = without_area(dataset, SOLAR_AREA) if area == ALL_AREAS else dataset
    return period, _build_report_view(report_dataset, period, area)


@dataclass(frozen=True)
class ReportBundle:
    period: ReportPeriod
    view: DashboardView
    solar_view: DashboardView | None
    unusual_rows: list[dict[str, str]]
    solar_low_rows: list[dict[str, str]]
    solar_positive_rows: list[dict[str, str]]
    quality_notes: tuple[str, ...]
    cost_analysis: CostAnalysis | None


def _number(row: dict, field: str) -> float:
    try:
        return float(row.get(field, '') or 0)
    except (TypeError, ValueError):
        return 0.0


def _kva_quality_note(
    dataset: DashboardDataset,
    period: ReportPeriod,
    area: str,
) -> str:
    material: list[dict] = []
    for row in dataset.intervals:
        timestamp = str(row.get('timestamp', ''))
        if not timestamp:
            continue
        row_date = date.fromisoformat(timestamp[:10])
        if not period.start_date <= row_date <= period.end_date:
            continue
        row_area = str(row.get('area', ''))
        if area == ALL_AREAS and row_area == SOLAR_AREA:
            continue
        if area != ALL_AREAS and row_area != area:
            continue
        if abs(_number(row, 'kw_net')) >= 1:
            material.append(row)
    if not material:
        return 'No material kW/kVA samples were available for a source-quality comparison.'
    mirrored = sum(
        abs(_number(row, 'kva') - abs(_number(row, 'kw_net')))
        <= max(0.05, abs(_number(row, 'kva')) * 0.005)
        for row in material
    )
    ratio = mirrored / len(material)
    if ratio >= 0.75:
        return (
            f"PNPSCADA's supplied kVA channel mirrors kW in {ratio:.0%} of material samples. "
            'kVA is reported exactly as supplied and may not represent an independent apparent-power measurement.'
        )
    return "kVA is reported directly from PNPSCADA's supplied apparent-power channel."


def _report_bundle(
    dataset: DashboardDataset,
    report_type: str,
    anchor_date: date,
    area: str,
    selected_start: date | None = None,
    selected_end: date | None = None,
    comparison_start: date | None = None,
    comparison_end: date | None = None,
    comparison_method: str = '',
    tariff_rates: Iterable[TariffRate] = BUILT_IN_RATES,
    ctou_tariff_rates: Iterable[CtouTariffRate] = BUILT_IN_CTOU_RATES,
) -> ReportBundle:
    period, view = report_view(
        dataset,
        report_type,
        anchor_date,
        area,
        selected_start,
        selected_end,
        comparison_start,
        comparison_end,
        comparison_method,
    )
    solar_view: DashboardView | None = None
    unusual_rows = [row for row in view.spikes if row.get('area') != SOLAR_AREA]
    solar_low_rows = [row for row in view.spikes if row.get('area') == SOLAR_AREA]
    solar_positive_rows = list(view.solar_positive_events)
    if area == ALL_AREAS and SOLAR_AREA in dataset.areas:
        solar_view = _build_report_view(dataset, period, SOLAR_AREA)
        solar_low_rows = list(solar_view.spikes)
        solar_positive_rows = list(solar_view.solar_positive_events)
    elif area == SOLAR_AREA:
        solar_view = view
    cost_analysis: CostAnalysis | None = None
    if area != SOLAR_AREA:
        cost_comparison_start = period.comparison_start or period.start_date
        cost_comparison_end = period.comparison_end or period.end_date
        cost_analysis = build_cost_analysis(
            dataset,
            period.start_date,
            period.end_date,
            area,
            tariff_rates,
            cost_comparison_start,
            cost_comparison_end,
            ctou_tariff_rates,
        )

    notes = [period.note]
    if period.end_date < period.requested_date:
        notes.append(
            f"The open day after {period.end_date.strftime('%d %B %Y')} was excluded because its meter readings are still being populated."
        )
    if solar_view is not None:
        notes.append(
            'Solar generation is shown separately and is not added to warehouse electricity consumption.'
        )
    if cost_analysis is not None:
        notes.append(
            'Cost uses CTOU for Warehouses 6-8 and Scale 1 for Warehouse 9. '
            'Solar savings is credited to Warehouse 8, matching the available recovery bills. '
            'PNPSCADA readings remain the consumption source.'
        )
        notes.append(
            'Admin common-area electricity and the supplemental WH8 Scale 1 meter are bill-only '
            'items without matching PNPSCADA meters and are excluded.'
        )
    notes.append(_kva_quality_note(dataset, period, area))
    return ReportBundle(
        period,
        view,
        solar_view,
        unusual_rows,
        solar_low_rows,
        solar_positive_rows,
        tuple(notes),
        cost_analysis,
    )


def _kwh(value: float) -> str:
    return f'{value / 1000:,.1f} MWh' if abs(value) >= 1000 else f'{value:,.0f} kWh'


def _clean(text: object) -> str:
    return (
        str(text)
        .replace('—', '-')
        .replace('–', '-')
        .replace('“', '"')
        .replace('”', '"')
        .replace('‘', "'")
        .replace('’', "'")
    )


def _format_timestamp(value: object) -> str:
    text = str(value or '').strip()
    if not text or text in {'-', 'â€”'}:
        return '-'
    try:
        return datetime.fromisoformat(text).strftime('%d %b %Y %H:%M')
    except ValueError:
        return _clean(text)


def _paragraph(text: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_clean(text)), style)


def _scope(area: str) -> str:
    if area == ALL_AREAS:
        return 'All warehouse areas | Solar generation shown separately'
    if area == SOLAR_AREA:
        return 'Solar generation'
    return area


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        'title': ParagraphStyle(
            'ReportTitle', parent=base['Title'], fontName='Helvetica-Bold',
            fontSize=20, leading=24, textColor=NAVY, alignment=TA_LEFT,
            spaceAfter=3,
        ),
        'subtitle': ParagraphStyle(
            'ReportSubtitle', parent=base['Normal'], fontName='Helvetica',
            fontSize=9, leading=13, textColor=MUTED,
        ),
        'section': ParagraphStyle(
            'Section', parent=base['Heading2'], fontName='Helvetica-Bold',
            fontSize=12, leading=15, textColor=NAVY, spaceBefore=7, spaceAfter=6,
        ),
        'body': ParagraphStyle(
            'Body', parent=base['BodyText'], fontName='Helvetica',
            fontSize=8.5, leading=12, textColor=NAVY,
        ),
        'small': ParagraphStyle(
            'Small', parent=base['BodyText'], fontName='Helvetica',
            fontSize=7, leading=9, textColor=NAVY,
        ),
        'small_white': ParagraphStyle(
            'SmallWhite', parent=base['BodyText'], fontName='Helvetica-Bold',
            fontSize=7, leading=9, textColor=WHITE,
        ),
        'small_muted': ParagraphStyle(
            'SmallMuted', parent=base['BodyText'], fontName='Helvetica',
            fontSize=7, leading=9, textColor=MUTED,
        ),
        'kpi_label': ParagraphStyle(
            'KpiLabel', parent=base['Normal'], fontName='Helvetica-Bold',
            fontSize=6.8, leading=8, textColor=MUTED, alignment=TA_CENTER,
        ),
        'kpi_value': ParagraphStyle(
            'KpiValue', parent=base['Normal'], fontName='Helvetica-Bold',
            fontSize=13, leading=16, textColor=NAVY, alignment=TA_CENTER,
        ),
        'card_label': ParagraphStyle(
            'CardLabel', parent=base['Normal'], fontName='Helvetica-Bold',
            fontSize=6.8, leading=8.5, textColor=MUTED, alignment=TA_LEFT,
            spaceAfter=2,
        ),
        'card_value': ParagraphStyle(
            'CardValue', parent=base['Normal'], fontName='Helvetica-Bold',
            fontSize=13, leading=16, textColor=NAVY, alignment=TA_LEFT,
            spaceAfter=2,
        ),
        'card_detail': ParagraphStyle(
            'CardDetail', parent=base['Normal'], fontName='Helvetica',
            fontSize=6.8, leading=9, textColor=MUTED, alignment=TA_LEFT,
        ),
        'callout': ParagraphStyle(
            'Callout', parent=base['BodyText'], fontName='Helvetica-Bold',
            fontSize=8, leading=11, textColor=NAVY,
        ),
    }


def _header(styles: dict[str, ParagraphStyle], period: ReportPeriod, area: str) -> Table:
    logo_path = DEFAULT_BRAND_DIR / 'Connect-Logistics-Main Logo.png'
    logo = Image(str(logo_path), width=62 * mm, height=20 * mm) if logo_path.exists() else Spacer(62 * mm, 20 * mm)
    report_titles = {
        'Current month overview': 'Sydney Road Current Month Report',
        'Filtered overview': 'Sydney Road Filtered Electricity Report',
        'Usage comparison': 'Sydney Road Usage Comparison Report',
        'Cost comparison': 'Sydney Road Cost Comparison Report',
    }
    title = [
        _paragraph(report_titles.get(period.report_type, f'Sydney Road {period.report_type} Report'), styles['title']),
        _paragraph(f'{period.label} | {_scope(area)}', styles['subtitle']),
        _paragraph(
            f"Generated {datetime.now().strftime('%d %B %Y at %H:%M')} | Internal management report",
            styles['subtitle'],
        ),
    ]
    table = Table([[logo, title]], colWidths=[70 * mm, 110 * mm])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -1), 1.2, ORANGE),
    ]))
    return table


def _recorded_change_percent(view: DashboardView) -> float | None:
    current = float(view.metrics.get('total_import_kwh', 0) or 0)
    previous = float(view.metrics.get('previous_import_kwh', 0) or 0)
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def _analytics_cards(
    cards: list[tuple[str, str, str, colors.Color]],
    styles: dict[str, ParagraphStyle],
) -> Table:
    card_width = 57 * mm
    cells: list[object] = []
    for label, value, detail, accent in cards:
        card = Table(
            [[_paragraph(label.upper(), styles['card_label'])],
             [_paragraph(value, styles['card_value'])],
             [_paragraph(detail, styles['card_detail'])]],
            colWidths=[card_width],
        )
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), WHITE),
            ('BOX', (0, 0), (-1, -1), .7, BORDER),
            ('LINEABOVE', (0, 0), (-1, 0), 2.2, accent),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 2),
            ('TOPPADDING', (0, 1), (-1, 1), 1),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 2),
            ('TOPPADDING', (0, 2), (-1, 2), 1),
            ('BOTTOMPADDING', (0, 2), (-1, 2), 7),
        ]))
        cells.append(card)
    while len(cells) % 3:
        cells.append(Spacer(card_width, 1))
    rows = [cells[index:index + 3] for index in range(0, len(cells), 3)]
    table = Table(rows, colWidths=[60 * mm] * 3, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 1.5 * mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 1.5 * mm),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5 * mm),
    ]))
    return table


def _kpi_table(
    bundle: ReportBundle,
    styles: dict[str, ParagraphStyle],
    area: str,
) -> Table:
    view = bundle.view
    metrics = view.metrics
    energy_label = (
        'Solar generated' if area == SOLAR_AREA
        else 'Warehouse electricity used' if area == ALL_AREAS
        else 'Electricity used'
    )
    quality_rows = list(view.daily)
    complete = sum(row.get('data_quality_status') == 'Complete' for row in quality_rows)
    coverage = complete / len(quality_rows) * 100.0 if quality_rows else 0.0
    cards: list[tuple[str, str, str, colors.Color]] = [
        (energy_label, _kwh(float(metrics['total_import_kwh'])), bundle.period.label, ORANGE),
    ]
    if bundle.period.comparison_enabled:
        recorded_change = _recorded_change_percent(view)
        comparison_label = _date_label(
            bundle.period.comparison_start,
            bundle.period.comparison_end,
        )
        cards.extend([
            ('Comparison electricity', _kwh(float(metrics['previous_import_kwh'])), comparison_label, TEAL),
            (
                'Recorded change',
                f'{recorded_change:+.1f}%' if recorded_change is not None else 'No prior usage',
                'Recorded totals; data quality is stated separately.',
                ORANGE if recorded_change is not None and recorded_change > 0 else TEAL,
            ),
        ])
    cards.extend([
        ('Average per day', _kwh(float(metrics['average_daily_kwh'])), f"{int(metrics['data_days'])} available day(s)", NAVY),
        ('Highest load', f"{float(metrics['peak_kw']):,.1f} kW", _format_timestamp(metrics['peak_kw_time']), ORANGE),
        ('Highest demand', f"{float(metrics['peak_kva']):,.1f} kVA", _format_timestamp(metrics['peak_time']), NAVY),
    ])
    if not bundle.period.comparison_enabled:
        cards.extend([
            ('Unusual readings', f"{len(bundle.unusual_rows) + len(bundle.solar_low_rows):,}", 'Items requiring operational review', ORANGE),
            ('Complete meter days', f'{coverage:.1f}%', f'{complete} of {len(quality_rows)} meter-day records', TEAL),
        ])
    return _analytics_cards(cards, styles)


def _cost_kpi_table(bundle: ReportBundle, styles: dict[str, ParagraphStyle]) -> Table | None:
    analysis = bundle.cost_analysis
    if analysis is None:
        return None
    metrics = analysis.metrics
    cards: list[tuple[str, str, str, colors.Color]] = [
        (
            'Cost before solar savings',
            f"R {float(metrics['total_cost']):,.2f}",
            'Energy, CTOU demand, service, network surcharge and VAT',
            ORANGE,
        ),
        (
            'Solar savings',
            f"R {float(metrics['solar_avoided_cost']):,.2f}",
            f"{_kwh(float(metrics['solar_used_kwh']))} credited to Warehouse 8",
            TEAL,
        ),
        ('Cost after solar savings', f"R {float(metrics['estimated_total_cost']):,.2f}", 'Cost before savings less estimated solar savings', NAVY),
    ]
    if bundle.period.comparison_enabled:
        change, change_basis = _cost_comparison_change(analysis)
        cards.extend([
            ('Comparison cost after savings', f"R {float(metrics['comparison_estimated_total_cost']):,.2f}", _date_label(analysis.comparison_start, analysis.comparison_end), TEAL),
            (
                f'{change_basis} movement',
                f'{float(change):+.1f}%' if isinstance(change, (int, float)) else 'No prior cost',
                'Selected period versus the comparison period; matched to Cost Centre logic',
                ORANGE if isinstance(change, (int, float)) and float(change) > 0 else TEAL,
            ),
        ])
    else:
        cards.append((
            'Complete-day cost used for projection',
            f"R {float(metrics['forecast_cost_to_date']):,.2f}",
            f"{int(metrics['forecast_elapsed_days'])} complete day(s) through {_format_timestamp(metrics['forecast_through_date'])[:11]}",
            NAVY,
        ))
    cards.append((
        f"Projected {metrics['forecast_month']} cost",
        f"R {float(metrics['forecast_month_end_cost']):,.2f}",
        (
            f"Based on {int(metrics['forecast_elapsed_days'])} complete day(s); "
            f"includes R {float(metrics['forecast_month_end_network_surcharge']):,.2f} "
            'projected network surcharge'
        ),
        ORANGE,
    ))
    cards.append((
        'Projection without network surcharge',
        f"R {float(metrics['forecast_month_end_cost_without_network_surcharge']):,.2f}",
        'Sensitivity view; all other projected cost components are unchanged',
        TEAL,
    ))
    return _analytics_cards(cards, styles)


def _cost_comparison_change(analysis: CostAnalysis) -> tuple[float | None, str]:
    metrics = analysis.metrics
    current_days = int(metrics.get('current_days', 0) or 0)
    comparison_days = int(metrics.get('comparison_days', 0) or 0)
    if current_days == comparison_days:
        value = metrics.get('cost_change_percent')
        return (float(value) if isinstance(value, (int, float)) else None, 'Total cost')
    value = metrics.get('daily_cost_change_percent')
    return (float(value) if isinstance(value, (int, float)) else None, 'Average daily cost')


def _daily_totals_from_rows(rows: Iterable[dict]) -> tuple[list[date], list[float]]:
    totals: dict[str, float] = {}
    for row in rows:
        totals[row['date']] = totals.get(row['date'], 0.0) + float(row.get('import_kwh', 0) or 0)
    dates = sorted(totals)
    return [date.fromisoformat(value) for value in dates], [totals[value] for value in dates]


def _daily_totals(view: DashboardView) -> tuple[list[date], list[float]]:
    return _daily_totals_from_rows(view.daily)


def _compact_number(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f'{value / 1_000_000:.1f}m'
    if absolute >= 10_000:
        return f'{value / 1000:.1f}k'
    if absolute >= 1000:
        return f'{value / 1000:.2f}k'
    return f'{value:,.0f}'


def _trend_points_from_rows(rows: Iterable[dict]) -> tuple[list[str], list[float], str]:
    dates, values = _daily_totals_from_rows(rows)
    if len(dates) <= 31:
        return [value.strftime('%d %b') for value in dates], values, 'by day'
    grouped: dict[date, float] = {}
    if len(dates) <= 120:
        for row_date, value in zip(dates, values):
            bucket = row_date - timedelta(days=row_date.weekday())
            grouped[bucket] = grouped.get(bucket, 0.0) + value
        return [value.strftime('%d %b') for value in sorted(grouped)], [grouped[value] for value in sorted(grouped)], 'by week'
    for row_date, value in zip(dates, values):
        bucket = row_date.replace(day=1)
        grouped[bucket] = grouped.get(bucket, 0.0) + value
    return [value.strftime('%b %Y') for value in sorted(grouped)], [grouped[value] for value in sorted(grouped)], 'by month'


def _trend_points(view: DashboardView) -> tuple[list[str], list[float], str]:
    return _trend_points_from_rows(view.daily)


def _daily_chart(view: DashboardView, area: str, include_comparison: bool = False) -> Drawing:
    labels, values, granularity = _trend_points(view)
    _previous_labels, previous_values, _previous_granularity = _trend_points_from_rows(view.previous_daily)
    drawing = Drawing(510, 205)
    title = (
        f'Solar generation {granularity} (kWh)' if area == SOLAR_AREA
        else f'Warehouse electricity used {granularity} (kWh)' if area == ALL_AREAS
        else f'Electricity used {granularity} (kWh)'
    )
    drawing.add(String(0, 192, title, fontName='Helvetica-Bold', fontSize=10, fillColor=NAVY))
    subtitle = (
        'Selected and comparison periods are aligned by reporting interval; labels sit above each line.'
        if include_comparison else 'Values are labelled directly on the chart.'
    )
    drawing.add(String(0, 180, subtitle, fontName='Helvetica', fontSize=7, fillColor=MUTED))
    if not values:
        drawing.add(String(180, 85, 'No readings in this period', fontName='Helvetica', fontSize=9, fillColor=MUTED))
        return drawing
    if len(values) == 1:
        chart = VerticalBarChart()
        chart.x, chart.y, chart.width, chart.height = 80, 38, 340, 120
        chart.data = [values, previous_values[:1]] if include_comparison and previous_values else [values]
        chart.categoryAxis.categoryNames = labels
        chart.valueAxis.valueMin = 0
        chart.valueAxis.valueMax = max(values[0] * 1.3, 1)
        chart.valueAxis.visibleGrid = 1
        chart.valueAxis.gridStrokeColor = colors.HexColor('#E5EAF1')
        chart.valueAxis.labels.fontSize = 7
        chart.bars[0].fillColor = ORANGE
        chart.bars[0].strokeColor = ORANGE
        if include_comparison and previous_values:
            chart.bars[1].fillColor = TEAL
            chart.bars[1].strokeColor = TEAL
        chart.barWidth = 45
        chart.barLabelFormat = _compact_number
        chart.barLabels.fontName = 'Helvetica-Bold'
        chart.barLabels.fontSize = 8
        chart.barLabels.fillColor = NAVY
        chart.barLabels.nudge = 7
        drawing.add(chart)
        return drawing
    chart = HorizontalLineChart()
    chart.x, chart.y, chart.width, chart.height = 52, 38, 438, 122
    series = [values]
    if include_comparison and previous_values:
        common_length = min(len(values), len(previous_values))
        series = [values[:common_length], previous_values[:common_length]]
        labels = labels[:common_length]
    chart.data = series
    step = max(1, len(labels) // 8)
    chart.categoryAxis.categoryNames = [
        value if index % step == 0 or index == len(labels) - 1 else ''
        for index, value in enumerate(labels)
    ]
    chart.categoryAxis.labels.fontName = 'Helvetica'
    chart.categoryAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.angle = 30
    chart.categoryAxis.labels.dy = -10
    chart.valueAxis.valueMin = 0
    plotted_values = [number for values_for_series in series for number in values_for_series]
    chart.valueAxis.valueMax = max(plotted_values) * (1.34 if include_comparison else 1.23) if max(plotted_values) else 1
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E5EAF1')
    chart.valueAxis.visibleGrid = 1
    chart.lines[0].strokeColor = ORANGE
    chart.lines[0].strokeWidth = 2
    if len(series) > 1:
        chart.lines[1].strokeColor = TEAL
        chart.lines[1].strokeWidth = 1.6
    chart.lineLabelFormat = 'values'
    label_arrays: list[list[str]] = []
    occupied_indices: set[int] = set()
    for series_index, values_for_series in enumerate(series):
        if include_comparison:
            candidates = {
                values_for_series.index(max(values_for_series)),
                values_for_series.index(min(values_for_series)),
            }
            candidates.update(range(series_index * 2, len(values_for_series), 4))
            if series_index == 0:
                candidates.update({0, len(values_for_series) - 1})
            candidates -= occupied_indices
            occupied_indices.update(candidates)
        else:
            label_step = max(1, len(values_for_series) // 12)
            candidates = {
                0,
                len(values_for_series) - 1,
                values_for_series.index(max(values_for_series)),
                values_for_series.index(min(values_for_series)),
            }
            candidates.update(range(0, len(values_for_series), label_step))
        label_arrays.append([
            _compact_number(value) if index in candidates else ''
            for index, value in enumerate(values_for_series)
        ])
    chart.lineLabelArray = label_arrays
    chart.lineLabels.fontName = 'Helvetica-Bold'
    chart.lineLabels.fontSize = 6
    chart.lineLabels.fillColor = NAVY
    for index in range(len(series[0])):
        chart.lineLabels[(0, index)].dy = 10
        if len(series) > 1:
            chart.lineLabels[(1, index)].dy = 12
    chart.joinedLines = 1
    drawing.add(chart)
    if len(series) > 1:
        drawing.add(Rect(350, 190, 8, 6, fillColor=ORANGE, strokeColor=ORANGE))
        drawing.add(String(362, 189, 'Selected', fontName='Helvetica', fontSize=7, fillColor=NAVY))
        drawing.add(Rect(417, 190, 8, 6, fillColor=TEAL, strokeColor=TEAL))
        drawing.add(String(429, 189, 'Comparison', fontName='Helvetica', fontSize=7, fillColor=NAVY))
    return drawing


def _area_chart(view: DashboardView, area: str, include_comparison: bool) -> Drawing:
    rows = list(view.area_summary)
    drawing = Drawing(510, 210)
    subject = (
        'Solar generation' if area == SOLAR_AREA
        else 'Warehouse electricity used by area' if area == ALL_AREAS
        else 'Electricity used'
    )
    title = f'{subject}: selected vs comparison period (kWh)' if include_comparison else f'{subject} (kWh)'
    drawing.add(String(0, 197, title, fontName='Helvetica-Bold', fontSize=10, fillColor=NAVY))
    if not rows:
        return drawing
    previous_by_area: dict[str, float] = {}
    for previous in view.previous_daily:
        key = str(previous['area'])
        previous_by_area[key] = previous_by_area.get(key, 0.0) + _number(previous, 'import_kwh')
    current_values = [float(row['import_kwh']) for row in rows]
    previous_values = [previous_by_area.get(str(row['area']), 0.0) for row in rows]
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 50, 42, 435, 125
    chart.data = [current_values, previous_values] if include_comparison else [current_values]
    chart.categoryAxis.categoryNames = [str(row['area']).replace('Connect Logistics ', '') for row in rows]
    chart.categoryAxis.labels.fontName = 'Helvetica'
    chart.categoryAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.angle = 15
    chart.categoryAxis.labels.dy = -7
    chart.valueAxis.valueMin = 0
    compared_values = current_values + previous_values if include_comparison else current_values
    chart.valueAxis.valueMax = max(compared_values + [1]) * 1.28
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.visibleGrid = 1
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E5EAF1')
    chart.bars[0].fillColor = NAVY
    chart.bars[0].strokeColor = NAVY
    if include_comparison:
        chart.bars[1].fillColor = TEAL
        chart.bars[1].strokeColor = TEAL
    chart.barWidth = 16
    chart.barSpacing = 2
    chart.groupSpacing = 12
    chart.barLabelFormat = _compact_number
    chart.barLabels.fontName = 'Helvetica-Bold'
    chart.barLabels.fontSize = 5.8
    chart.barLabels.fillColor = NAVY
    chart.barLabels.nudge = 6
    drawing.add(chart)
    drawing.add(Rect(350, 184, 8, 6, fillColor=NAVY, strokeColor=NAVY))
    drawing.add(String(362, 183, 'Selected period', fontName='Helvetica', fontSize=7, fillColor=NAVY))
    if include_comparison:
        drawing.add(Rect(430, 184, 8, 6, fillColor=TEAL, strokeColor=TEAL))
        drawing.add(String(442, 183, 'Comparison', fontName='Helvetica', fontSize=7, fillColor=NAVY))
    return drawing


def _area_table(
    view: DashboardView,
    styles: dict[str, ParagraphStyle],
    include_comparison: bool,
) -> Table:
    headers = (
        ['Area', 'Selected kWh', 'Comparison kWh', 'Change kWh', 'Recorded change', 'Share', 'Avg/day', 'Data quality']
        if include_comparison else
        ['Area', 'Electricity used', 'Share', 'Average/day', 'Peak kW', 'Peak kVA', 'Unusual', 'Data quality']
    )
    data: list[list[object]] = [[_paragraph(value, styles['small_white']) for value in headers]]
    previous_by_area: dict[str, float] = {}
    for previous in view.previous_daily:
        key = str(previous['area'])
        previous_by_area[key] = previous_by_area.get(key, 0.0) + _number(previous, 'import_kwh')
    data_days = max(1, int(view.metrics.get('data_days', 0) or 0))
    for row in view.area_summary:
        previous = previous_by_area.get(str(row['area']), 0.0)
        area_rows = [item for item in view.daily if item['area'] == row['area']]
        quality_values = {str(item.get('data_quality_status', 'Complete')) for item in area_rows}
        quality = 'Incomplete' if 'Incomplete' in quality_values else 'Estimated' if 'Estimated' in quality_values else 'Complete'
        current = float(row['import_kwh'])
        raw_change = (current - previous) / abs(previous) * 100.0 if previous else None
        if include_comparison:
            values = [
                row['area'], f'{current:,.0f}', f'{previous:,.0f}',
                f'{current - previous:+,.0f}',
                f'{raw_change:+.1f}%' if raw_change is not None else '-',
                f"{float(row['share_percent']):.1f}%",
                f'{current / data_days:,.0f}', quality,
            ]
        else:
            values = [
                row['area'], f'{current:,.0f}', f"{float(row['share_percent']):.1f}%",
                f'{current / data_days:,.0f}', f"{float(row['peak_kw']):,.1f}",
                f"{float(row['peak_kva']):,.1f}", str(int(row['alert_days'])), quality,
            ]
        data.append([_paragraph(value, styles['small']) for value in values])
    table = Table(data, colWidths=[30 * mm, 23 * mm, 23 * mm, 22 * mm, 18 * mm, 17 * mm, 19 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _cost_area_chart(analysis: CostAnalysis, include_comparison: bool) -> Drawing:
    rows = list(analysis.area_rows)
    drawing = Drawing(510, 210)
    title = (
        'Estimated cost after solar savings: selected vs comparison period (Rand)'
        if include_comparison else
        'Estimated cost after solar savings by warehouse area (Rand)'
    )
    drawing.add(String(0, 197, title, fontName='Helvetica-Bold', fontSize=10, fillColor=NAVY))
    if not rows:
        drawing.add(String(175, 95, 'No cost records in this period', fontName='Helvetica', fontSize=9, fillColor=MUTED))
        return drawing
    current_values = [float(row['current_total_cost']) for row in rows]
    comparison_values = [float(row['comparison_total_cost']) for row in rows]
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 55, 42, 430, 125
    chart.data = [current_values, comparison_values] if include_comparison else [current_values]
    chart.categoryAxis.categoryNames = [str(row['area']).replace('Connect Logistics ', '') for row in rows]
    chart.categoryAxis.labels.fontName = 'Helvetica'
    chart.categoryAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.angle = 15
    chart.categoryAxis.labels.dy = -7
    chart.valueAxis.valueMin = 0
    all_values = current_values + comparison_values if include_comparison else current_values
    chart.valueAxis.valueMax = max(all_values + [1]) * 1.28
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.visibleGrid = 1
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E5EAF1')
    chart.bars[0].fillColor = ORANGE
    chart.bars[0].strokeColor = ORANGE
    if include_comparison:
        chart.bars[1].fillColor = TEAL
        chart.bars[1].strokeColor = TEAL
    chart.barWidth = 16
    chart.barSpacing = 2
    chart.groupSpacing = 12
    chart.barLabelFormat = lambda value: f'R {_compact_number(value)}'
    chart.barLabels.fontName = 'Helvetica-Bold'
    chart.barLabels.fontSize = 5.8
    chart.barLabels.fillColor = NAVY
    chart.barLabels.nudge = 6
    drawing.add(chart)
    drawing.add(Rect(335, 184, 8, 6, fillColor=ORANGE, strokeColor=ORANGE))
    drawing.add(String(347, 183, 'Selected period', fontName='Helvetica', fontSize=7, fillColor=NAVY))
    if include_comparison:
        drawing.add(Rect(425, 184, 8, 6, fillColor=TEAL, strokeColor=TEAL))
        drawing.add(String(437, 183, 'Comparison', fontName='Helvetica', fontSize=7, fillColor=NAVY))
    return drawing


def _cost_area_table(
    analysis: CostAnalysis,
    styles: dict[str, ParagraphStyle],
    include_comparison: bool,
) -> Table:
    current_days = max(1, int(analysis.metrics.get('current_days', 0) or 0))
    comparison_days = max(1, int(analysis.metrics.get('comparison_days', 0) or 0))
    unequal_days = current_days != comparison_days
    headers = (
        [
            'Area', 'Selected cost', 'Comparison cost',
            'Average/day selected', 'Average/day comparison',
            'Daily cost change' if unequal_days else 'Total cost change',
        ]
        if include_comparison else
        ['Area', 'Electricity used', 'Cost before savings', 'Solar savings', 'Cost after savings']
    )
    data: list[list[object]] = [[_paragraph(value, styles['small_white']) for value in headers]]
    for row in analysis.area_rows:
        current_cost = float(row['current_total_cost'])
        comparison_cost = float(row['comparison_total_cost'])
        if unequal_days:
            prior_basis = comparison_cost / comparison_days
            current_basis = current_cost / current_days
        else:
            prior_basis = comparison_cost
            current_basis = current_cost
        change = (current_basis - prior_basis) / abs(prior_basis) * 100.0 if prior_basis else None
        if include_comparison:
            values = [
                row['area'], f'R {current_cost:,.2f}', f'R {comparison_cost:,.2f}',
                f'R {current_cost / current_days:,.2f}', f'R {comparison_cost / comparison_days:,.2f}',
                f'{change:+.1f}%' if change is not None else '-',
            ]
        else:
            values = [
                row['area'], f"{float(row['current_kwh']):,.0f} kWh",
                f"R {float(row['current_gross_total_cost']):,.2f}",
                f"R {float(row['current_solar_avoided_cost']):,.2f}",
                f'R {current_cost:,.2f}',
            ]
        data.append([_paragraph(value, styles['small']) for value in values])
    widths = [32 * mm, 29 * mm, 31 * mm, 32 * mm, 32 * mm, 24 * mm] if include_comparison else [36 * mm] * 5
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


def _demand_table(view: DashboardView, styles: dict[str, ParagraphStyle]) -> Table:
    headers = ['Area', 'Peak kW', 'Peak load time', 'Peak kVA', 'Peak demand time', 'Unusual', 'Review level']
    data: list[list[object]] = [[_paragraph(value, styles['small_white']) for value in headers]]
    for row in view.area_summary:
        data.append([
            _paragraph(row['area'], styles['small']),
            _paragraph(f"{float(row['peak_kw']):,.1f}", styles['small']),
            _paragraph(_format_timestamp(row.get('peak_kw_time')), styles['small']),
            _paragraph(f"{float(row['peak_kva']):,.1f}", styles['small']),
            _paragraph(_format_timestamp(row.get('peak_time')), styles['small']),
            _paragraph(str(int(row['alert_days'])), styles['small']),
            _paragraph(str(row['status']), styles['small']),
        ])
    table = Table(data, colWidths=[31 * mm, 20 * mm, 33 * mm, 20 * mm, 33 * mm, 17 * mm, 26 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _solar_summary(
    view: DashboardView,
    styles: dict[str, ParagraphStyle],
    include_comparison: bool,
) -> Table:
    metrics = view.metrics
    values = [
        ('SOLAR GENERATED', _kwh(float(metrics['total_import_kwh']))),
        ('PEAK kW OUTPUT', f"{float(metrics['peak_kw']):,.1f} kW"),
        ('LOW OUTPUT WARNINGS', f"{int(metrics['alert_days']):,}"),
        ('HIGHER OUTPUT DAYS', f"{len(view.solar_positive_events):,}"),
    ]
    if include_comparison:
        change = _recorded_change_percent(view)
        values.insert(
            1,
            ('RECORDED CHANGE', f'{change:+.1f}%' if change is not None else 'No prior output'),
        )
    else:
        values.append(('AVERAGE PER DAY', _kwh(float(metrics['average_daily_kwh']))))
    table = Table(
        [
            [_paragraph(label, styles['kpi_label']) for label, _value in values],
            [_paragraph(value, styles['kpi_value']) for _label, value in values],
        ],
        colWidths=[36 * mm] * 5,
        rowHeights=[9 * mm, 12 * mm],
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF8E6')),
        ('BOX', (0, 0), (-1, -1), .7, colors.HexColor('#F1C36A')),
        ('INNERGRID', (0, 0), (-1, -1), .45, colors.HexColor('#F1C36A')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


def _solar_analysis_table(
    view: DashboardView,
    styles: dict[str, ParagraphStyle],
    include_comparison: bool,
) -> Table:
    dates, values = _daily_totals(view)
    highest_text = 'No solar readings were available.'
    lowest_text = highest_text
    if values:
        high_index = values.index(max(values))
        low_index = values.index(min(values))
        highest_text = f"{_kwh(values[high_index])} on {dates[high_index].strftime('%d %b %Y')}"
        lowest_text = f"{_kwh(values[low_index])} on {dates[low_index].strftime('%d %b %Y')}"
    change = _recorded_change_percent(view)
    change_text = (
        f'{float(change):+.1f}% versus the comparison period'
        if change is not None else 'No comparison output was recorded.'
    )
    statuses: dict[str, int] = {}
    for row in view.daily:
        status = str(row.get('data_quality_status', 'Unknown'))
        statuses[status] = statuses.get(status, 0) + 1
    rows = [
        ('Daily average', _kwh(float(view.metrics['average_daily_kwh']))),
        ('Highest generation day', highest_text),
        ('Lowest generation day', lowest_text),
        ('Peak output', f"{float(view.metrics['peak_kw']):,.1f} kW on {_format_timestamp(view.metrics['peak_kw_time'])}"),
        ('Recorded comparison' if include_comparison else 'Available days',
         change_text if include_comparison else f"{int(view.metrics['data_days'])} day(s) in this report"),
        ('Review and quality', f"{int(view.metrics['alert_days'])} low-output warning(s), "
         f"{len(view.solar_positive_events)} positive higher-output day(s); "
         f"{statuses.get('Complete', 0)} complete, {statuses.get('Estimated', 0)} estimated and "
         f"{statuses.get('Incomplete', 0)} incomplete."),
    ]
    table = Table([
        [_paragraph(label, styles['kpi_label']) for label, _value in rows[:3]],
        [_paragraph(value, styles['body']) for _label, value in rows[:3]],
        [_paragraph(label, styles['kpi_label']) for label, _value in rows[3:]],
        [_paragraph(value, styles['body']) for _label, value in rows[3:]],
    ], colWidths=[60 * mm] * 3)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), PALE),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EAF0F8')),
        ('BACKGROUND', (0, 2), (-1, 2), colors.HexColor('#EAF0F8')),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), .35, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


def _review_checklist(styles: dict[str, ParagraphStyle]) -> Table:
    checks = [
        'Validate incomplete or estimated readings before making cost-allocation decisions.',
        'Start with the area driving the largest kWh movement and the exact recorded peak time.',
        'Compare meter movements with shifts, throughput, overtime, HVAC, maintenance and equipment runtime.',
        'Record the confirmed cause, responsible person, corrective action and target completion date.',
        'Use the following report to confirm whether consumption and demand returned to the expected range.',
    ]
    data: list[list[object]] = [[
        _paragraph('Priority', styles['small_white']),
        _paragraph('Management review checklist', styles['small_white']),
    ]]
    data.extend([
        [_paragraph(str(index), styles['callout']), _paragraph(check, styles['body'])]
        for index, check in enumerate(checks, start=1)
    ])
    table = Table(data, colWidths=[18 * mm, 162 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


def _quality_notes(notes: tuple[str, ...], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [_paragraph('REPORTING BASIS' if index == 0 else 'DATA NOTE', styles['kpi_label']),
         _paragraph(note, styles['body'])]
        for index, note in enumerate(notes)
    ]
    table = Table(rows, colWidths=[34 * mm, 146 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF4EE')),
        ('BOX', (0, 0), (-1, -1), .7, ORANGE),
        ('LINEBELOW', (0, 0), (-1, -2), .35, colors.HexColor('#F2B79D')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


def _comparison_description(bundle: ReportBundle) -> str:
    view = bundle.view
    current = float(view.metrics['total_import_kwh'])
    previous = float(view.metrics['previous_import_kwh'])
    difference = current - previous
    comparison_start = bundle.period.comparison_start or view.start_date
    comparison_end = date.fromisoformat(str(view.metrics['comparison_end']))
    comparison_label = (
        comparison_start.strftime('%d %b %Y')
        if comparison_start == comparison_end
        else f"{comparison_start.strftime('%d %b %Y')} to {comparison_end.strftime('%d %b %Y')}"
    )
    change = _recorded_change_percent(view)
    if change is not None:
        direction = 'higher' if difference > 0 else 'lower' if difference < 0 else 'unchanged'
        return (
            f"{_kwh(current)} was recorded, {abs(float(change)):.1f}% {direction} "
            f"({difference:+,.0f} kWh) than {comparison_label}."
        )
    return f'{_kwh(current)} was recorded; the comparison period had no recorded usage.'


def _management_findings(bundle: ReportBundle) -> list[tuple[str, str, str]]:
    view = bundle.view
    metrics = view.metrics
    findings: list[tuple[str, str, str]] = []
    if bundle.period.comparison_enabled:
        findings.append((
            'Electricity comparison',
            _comparison_description(bundle),
            'Reconcile the movement with throughput, shifts, operating hours and major equipment runtime.',
        ))
    else:
        findings.append((
            'Electricity used',
            f"{_kwh(float(metrics['total_import_kwh']))} was recorded across "
            f"{int(metrics['data_days'])} available day(s), averaging "
            f"{_kwh(float(metrics['average_daily_kwh']))} per day.",
            'Use this as the current operating baseline and review the highest-use days and areas below.',
        ))

    if bundle.cost_analysis is not None:
        cost_metrics = bundle.cost_analysis.metrics
        cost_text = (
            f"Cost before solar savings was R {float(cost_metrics['total_cost']):,.2f}; "
            f"this includes R {float(cost_metrics['energy_cost']):,.2f} energy, "
            f"R {float(cost_metrics['demand_cost']):,.2f} demand, "
            f"R {float(cost_metrics['service_cost']):,.2f} service and "
            f"R {float(cost_metrics['network_surcharge']):,.2f} network surcharge. "
            f"estimated solar savings were R {float(cost_metrics['solar_avoided_cost']):,.2f}; "
            f"cost after solar savings was R {float(cost_metrics['estimated_total_cost']):,.2f}. "
            f"The projected current-month cost is R {float(cost_metrics['forecast_month_end_cost']):,.2f}, "
            f"including R {float(cost_metrics['forecast_month_end_network_surcharge']):,.2f} projected network "
            f"surcharge. Without that surcharge, the sensitivity projection is "
            f"R {float(cost_metrics['forecast_month_end_cost_without_network_surcharge']):,.2f}."
        )
        matched_days = int(cost_metrics.get('forecast_comparable_days') or 0)
        matched_cost_change = cost_metrics.get('forecast_mtd_cost_change_percent')
        matched_usage_change = cost_metrics.get('forecast_mtd_usage_change_percent')
        if matched_days and isinstance(matched_cost_change, (int, float)):
            cost_text += (
                f' Across {matched_days} identical complete month-to-date day(s), estimated cost was '
                f'{float(matched_cost_change):+.1f}% and electricity use was '
                f'{float(matched_usage_change):+.1f}% versus the previous month.'
                if isinstance(matched_usage_change, (int, float)) else
                f' Across {matched_days} identical complete month-to-date day(s), estimated cost was '
                f'{float(matched_cost_change):+.1f}% versus the previous month.'
            )
        if bundle.period.comparison_enabled:
            movement, movement_basis = _cost_comparison_change(bundle.cost_analysis)
            if movement is not None:
                cost_text += (
                    f' {movement_basis} was {movement:+.1f}% versus the comparison period, '
                    'using the same day-count logic as Cost Centre.'
                )
        findings.append((
            'Electricity cost and solar savings',
            cost_text,
            'Use the projection for current-month cost control. Demand is a PNPSCADA exposure estimate, not an invoice reconciliation.',
        ))

    dates, totals = _daily_totals(view)
    if totals:
        high_index = totals.index(max(totals))
        low_index = totals.index(min(totals))
        spread = max(totals) - min(totals)
        findings.append((
            'Daily range',
            f"Highest recorded use was {_kwh(totals[high_index])} on {dates[high_index].strftime('%d %b %Y')}; "
            f"lowest was {_kwh(totals[low_index])} on {dates[low_index].strftime('%d %b %Y')}. "
            f"The recorded daily range was {_kwh(spread)}.",
            'Check the highest-use day against dispatch volume, overtime, HVAC and equipment schedules.',
        ))

    if view.area_summary:
        largest = view.area_summary[0]
        findings.append((
            'Largest consuming area',
            f"{largest['area']} used {_kwh(float(largest['import_kwh']))}, "
            f"{float(largest['share_percent']):.1f}% of the selected warehouse total.",
            f"Prioritise {largest['area']} when reviewing operating hours and avoidable base load.",
        ))
        previous_by_area: dict[str, float] = {}
        for row in view.previous_daily:
            key = str(row['area'])
            previous_by_area[key] = previous_by_area.get(key, 0.0) + _number(row, 'import_kwh')
        drivers = [
            (
                abs(float(row['import_kwh']) - previous_by_area.get(str(row['area']), 0.0)),
                float(row['import_kwh']) - previous_by_area.get(str(row['area']), 0.0),
                row,
            )
            for row in view.area_summary
        ]
        if drivers and bundle.period.comparison_enabled:
            _absolute, movement, driver = max(drivers, key=lambda item: item[0])
            direction = 'increase' if movement > 0 else 'decrease' if movement < 0 else 'no movement'
            findings.append((
                'Area driving the change',
                f"{driver['area']} had the largest recorded movement: {movement:+,.0f} kWh ({direction}) "
                'against its comparison period.',
                f"Review activity in {driver['area']} first; it had the largest effect on the site movement.",
            ))

    findings.append((
        'Peak load and demand',
        f"Working load reached {float(metrics['peak_kw']):,.1f} kW at {metrics['peak_kw_area']} on "
        f"{_format_timestamp(metrics['peak_kw_time'])}. Total demand reached "
        f"{float(metrics['peak_kva']):,.1f} kVA at {metrics['peak_area']} on "
        f"{_format_timestamp(metrics['peak_time'])}.",
        'Check which high-load equipment operated together at the recorded peak times.',
    ))

    if bundle.solar_view is not None:
        solar = bundle.solar_view.metrics
        solar_total = float(solar['total_import_kwh'])
        warehouse_total = float(metrics['total_import_kwh'])
        equivalent = solar_total / warehouse_total * 100 if warehouse_total else 0.0
        solar_change = _recorded_change_percent(bundle.solar_view)
        solar_comparison = (
            f" Generation was {float(solar_change):+.1f}% versus the comparison period;"
            if bundle.period.comparison_enabled and solar_change is not None else ''
        )
        findings.append((
            'Solar generation',
            f"Solar generated {_kwh(solar_total)}, equivalent to {equivalent:.1f}% of warehouse electricity used. "
            f"{solar_comparison} peak output was {float(solar['peak_kw']):,.1f} kW on "
            f"{_format_timestamp(solar['peak_kw_time'])}.",
            'Review inverter availability, outages and weather when generation is materially below the comparison period.',
        ))
        if bundle.solar_low_rows:
            latest_low = max(bundle.solar_low_rows, key=lambda row: str(row['date']))
            findings.append((
                'Low solar generation warnings',
                f"{len(bundle.solar_low_rows)} day(s) were at least 30% and 30 kWh below their "
                f"recent same-weekday average. The latest was {date.fromisoformat(str(latest_low['date'])[:10]).strftime('%d %b %Y')} "
                f"at {_number(latest_low, 'import_kwh'):,.0f} kWh versus "
                f"{_number(latest_low, 'baseline_import_kwh'):,.0f} kWh usual.",
                'Check weather first, then confirm inverter availability, outages, faults and curtailment.',
            ))
        if bundle.solar_positive_rows:
            strongest = max(
                bundle.solar_positive_rows,
                key=lambda row: _number(row, 'consumption_variance_percent'),
            )
            findings.append((
                'Higher solar generation - positive',
                f"{len(bundle.solar_positive_rows)} day(s) generated materially more than usual. "
                f"The strongest was {date.fromisoformat(str(strongest['date'])[:10]).strftime('%d %b %Y')} "
                f"at {_number(strongest, 'import_kwh'):,.0f} kWh "
                f"({_number(strongest, 'consumption_variance_percent'):+.1f}% versus usual).",
                'Verify the PNPSCADA reading against the inverter portal and weather, then retain it as a positive performance event.',
            ))

    severity_counts: dict[str, int] = {'Critical': 0, 'High': 0, 'Watch': 0}
    area_counts: dict[str, int] = {}
    for row in bundle.unusual_rows:
        level = str(row.get('alert_level', ''))
        if level in severity_counts:
            severity_counts[level] += 1
        row_area = str(row.get('area', 'Unknown'))
        area_counts[row_area] = area_counts.get(row_area, 0) + 1
    if bundle.unusual_rows:
        most_affected = max(area_counts, key=area_counts.get)
        findings.append((
            'Unusual warehouse readings',
            f"{len(bundle.unusual_rows)} readings require review: {severity_counts['Critical']} urgent, "
            f"{severity_counts['High']} review and {severity_counts['Watch']} monitor. "
            f"{most_affected} had the most flagged readings ({area_counts[most_affected]}).",
            'Investigate urgent and review items first, then record the confirmed cause and corrective action.',
        ))
    else:
        findings.append((
            'Unusual warehouse readings',
            'No unusual warehouse use or demand readings were detected for the selected period.',
            'Continue the same review cadence and compare the next report for new movements.',
        ))

    quality_rows = list(view.daily)
    if bundle.solar_view is not None:
        quality_rows.extend(bundle.solar_view.daily)
    quality_counts: dict[str, int] = {}
    for row in quality_rows:
        status = str(row.get('data_quality_status', 'Unknown'))
        quality_counts[status] = quality_counts.get(status, 0) + 1
    complete = quality_counts.get('Complete', 0)
    estimated = quality_counts.get('Estimated', 0)
    incomplete = quality_counts.get('Incomplete', 0)
    findings.append((
        'Data completeness',
        f"{complete} meter-day records were complete, {estimated} estimated and {incomplete} incomplete.",
        'Validate incomplete records before using affected movements for cost allocation or performance decisions.',
    ))
    return findings


def _management_summary(bundle: ReportBundle, styles: dict[str, ParagraphStyle]) -> Table:
    data: list[list[object]] = [[
        _paragraph('Management question', styles['small_white']),
        _paragraph('What the meter data shows', styles['small_white']),
        _paragraph('Recommended first check', styles['small_white']),
    ]]
    for title, evidence, action in _management_findings(bundle):
        data.append([
            _paragraph(title, styles['callout']),
            _paragraph(evidence, styles['small']),
            _paragraph(action, styles['small']),
        ])
    table = Table(data, colWidths=[34 * mm, 88 * mm, 58 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .7, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


def _period_bucket(row_date: date, span: int) -> date:
    if span <= 31:
        return row_date
    if span <= 120:
        return row_date - timedelta(days=row_date.weekday())
    return row_date.replace(day=1)


def _period_detail_table(
    bundle: ReportBundle,
    styles: dict[str, ParagraphStyle],
) -> tuple[str, Table]:
    view = bundle.view
    span = (view.end_date - view.start_date).days + 1
    comparison_start = date.fromisoformat(str(view.metrics['comparison_start']))
    current_by_date: dict[date, list[dict]] = {}
    previous_by_date: dict[date, list[dict]] = {}
    solar_by_date: dict[date, float] = {}
    for row in view.daily:
        current_by_date.setdefault(date.fromisoformat(str(row['date'])), []).append(row)
    for row in view.previous_daily:
        previous_by_date.setdefault(date.fromisoformat(str(row['date'])), []).append(row)
    if bundle.solar_view is not None:
        for row in bundle.solar_view.daily:
            row_date = date.fromisoformat(str(row['date']))
            solar_by_date[row_date] = solar_by_date.get(row_date, 0.0) + _number(row, 'import_kwh')

    buckets: dict[date, dict[str, object]] = {}
    for offset in range(span):
        current_date = view.start_date + timedelta(days=offset)
        previous_date = comparison_start + timedelta(days=offset)
        current_rows = current_by_date.get(current_date, [])
        previous_rows = previous_by_date.get(previous_date, [])
        if not current_rows:
            continue
        bucket = _period_bucket(current_date, span)
        item = buckets.setdefault(bucket, {
            'first_date': current_date,
            'last_date': current_date,
            'current_kwh': 0.0,
            'previous_kwh': 0.0,
            'peak_kw': 0.0,
            'peak_kva': 0.0,
            'solar_kwh': 0.0,
            'unusual': 0,
            'statuses': set(),
        })
        item['last_date'] = current_date
        item['current_kwh'] = float(item['current_kwh']) + sum(_number(row, 'import_kwh') for row in current_rows)
        item['previous_kwh'] = float(item['previous_kwh']) + sum(_number(row, 'import_kwh') for row in previous_rows)
        item['peak_kw'] = max(float(item['peak_kw']), max((_number(row, 'peak_kw') for row in current_rows), default=0.0))
        item['peak_kva'] = max(float(item['peak_kva']), max((_number(row, 'peak_kva') for row in current_rows), default=0.0))
        item['solar_kwh'] = float(item['solar_kwh']) + solar_by_date.get(current_date, 0.0)
        item['unusual'] = int(item['unusual']) + sum(
            row.get('alert_level') in {'Watch', 'High', 'Critical'} for row in current_rows
        )
        statuses = item['statuses']
        if isinstance(statuses, set):
            statuses.update(str(row.get('data_quality_status', 'Unknown')) for row in current_rows)

    headers = (
        ['Period', 'Selected kWh', 'Comparison kWh', 'Recorded change', 'Peak kW', 'Peak kVA', 'Solar kWh', 'Unusual', 'Quality']
        if bundle.period.comparison_enabled else
        ['Period', 'Electricity used', 'Average/day', 'Peak kW', 'Peak kVA', 'Solar kWh', 'Unusual', 'Quality']
    )
    data: list[list[object]] = [[_paragraph(value, styles['small_white']) for value in headers]]
    for bucket in sorted(buckets):
        item = buckets[bucket]
        first_date = item['first_date']
        last_date = item['last_date']
        if isinstance(first_date, date) and isinstance(last_date, date):
            label = first_date.strftime('%d %b') if first_date == last_date else f"{first_date.strftime('%d %b')}-{last_date.strftime('%d %b')}"
        else:
            label = str(bucket)
        current_value = float(item['current_kwh'])
        previous_value = float(item['previous_kwh'])
        change = (current_value - previous_value) / abs(previous_value) * 100 if previous_value else None
        statuses = item['statuses'] if isinstance(item['statuses'], set) else set()
        quality = 'Incomplete' if 'Incomplete' in statuses else 'Estimated' if 'Estimated' in statuses else 'Complete'
        if bundle.period.comparison_enabled:
            values = [
                label, f'{current_value:,.0f}', f'{previous_value:,.0f}',
                f'{change:+.1f}%' if change is not None else '-',
                f"{float(item['peak_kw']):,.1f}", f"{float(item['peak_kva']):,.1f}",
                f"{float(item['solar_kwh']):,.0f}" if bundle.solar_view is not None else '-',
                str(int(item['unusual'])), quality,
            ]
        else:
            days_in_bucket = max(1, (item['last_date'] - item['first_date']).days + 1)
            values = [
                label, f'{current_value:,.0f}', f'{current_value / days_in_bucket:,.0f}',
                f"{float(item['peak_kw']):,.1f}", f"{float(item['peak_kva']):,.1f}",
                f"{float(item['solar_kwh']):,.0f}" if bundle.solar_view is not None else '-',
                str(int(item['unusual'])), quality,
            ]
        data.append([_paragraph(value, styles['small']) for value in values])
    title = 'Daily performance detail' if span <= 31 else 'Weekly performance detail' if span <= 120 else 'Monthly performance detail'
    widths = (
        [24 * mm, 23 * mm, 22 * mm, 23 * mm, 17 * mm, 17 * mm, 20 * mm, 14 * mm, 20 * mm]
        if bundle.period.comparison_enabled else
        [27 * mm, 28 * mm, 24 * mm, 21 * mm, 21 * mm, 24 * mm, 20 * mm, 35 * mm]
    )
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return title, table


def _reason(row: dict[str, str]) -> str:
    parts: list[str] = []
    alert_type = str(row.get('alert_type', ''))
    solar = row.get('area') == SOLAR_AREA
    if 'Consumption' in alert_type:
        parts.append(
            f"{'Solar generation' if solar else 'Use'} was "
            f"{float(row.get('consumption_variance_percent', 0) or 0):.1f}% above the recent same-weekday average."
        )
    if 'demand' in alert_type.lower() or alert_type == 'Demand':
        parts.append(
            f"{'Solar output' if solar else 'Demand'} was "
            f"{float(row.get('demand_variance_percent', 0) or 0):.1f}% above the recent same-weekday average."
        )
    return ' '.join(parts) or 'Reading was outside its recent same-weekday range.'


def _check(row: dict[str, str]) -> str:
    if row.get('area') == SOLAR_AREA:
        return 'Check inverter output, weather, outages and curtailment.'
    if row.get('alert_type') == 'Demand':
        return 'Check equipment operating together at the recorded peak time.'
    if row.get('alert_type') == 'Consumption':
        return 'Check shifts, operating hours, HVAC, lighting and equipment runtime.'
    return 'Check operating hours and equipment running together around the peak time.'


def _unusual_table(rows: list[dict[str, str]], styles: dict[str, ParagraphStyle]) -> Table | Paragraph:
    if not rows:
        return _paragraph('No unusual readings were found in this reporting period.', styles['body'])
    headers = ['Date', 'Area', 'Review', 'Used', 'Peak load / demand', 'What the meter shows', 'First check']
    data: list[list[object]] = [[_paragraph(value, styles['small_white']) for value in headers]]
    labels = {'Critical': 'Urgent', 'High': 'Review', 'Watch': 'Monitor'}
    for row in rows[:10]:
        peak_time = _format_timestamp(row.get('peak_kva_time'))
        if peak_time != '-':
            peak_time = peak_time[-5:]
        data.append([
            _paragraph(date.fromisoformat(row['date']).strftime('%d %b'), styles['small']),
            _paragraph(row['area'], styles['small']),
            _paragraph(labels.get(row['alert_level'], row['alert_level']), styles['small']),
            _paragraph(f"{_number(row, 'import_kwh'):,.0f} kWh", styles['small']),
            _paragraph(
                f"{_number(row, 'peak_kw'):,.1f} kW / {_number(row, 'peak_kva'):,.1f} kVA | {peak_time}",
                styles['small'],
            ),
            _paragraph(_reason(row), styles['small']),
            _paragraph(_check(row), styles['small']),
        ])
    table = Table(data, colWidths=[15 * mm, 26 * mm, 17 * mm, 18 * mm, 28 * mm, 40 * mm, 36 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _solar_signal_table(
    rows: list[dict[str, str]],
    styles: dict[str, ParagraphStyle],
    *,
    positive: bool,
) -> Table | Paragraph:
    if not rows:
        message = (
            'No materially higher-than-usual solar-generation days were found.'
            if positive else
            'No materially low solar-generation days were found.'
        )
        return _paragraph(message, styles['body'])
    headers = [
        'Date', 'Classification', 'Generated', 'Recent average',
        'Difference', 'Peak output', 'Verification / first check',
    ]
    data: list[list[object]] = [[
        _paragraph(value, styles['small_white']) for value in headers
    ]]
    level_labels = {'Critical': 'Urgent', 'High': 'Review', 'Watch': 'Monitor'}
    for row in rows[:12]:
        variance = _number(row, 'consumption_variance_percent')
        data.append([
            _paragraph(
                date.fromisoformat(str(row['date'])[:10]).strftime('%d %b'),
                styles['small'],
            ),
            _paragraph(
                'Positive - verify' if positive else level_labels.get(
                    str(row.get('alert_level', '')), 'Review'
                ),
                styles['small'],
            ),
            _paragraph(f"{_number(row, 'import_kwh'):,.0f} kWh", styles['small']),
            _paragraph(f"{_number(row, 'baseline_import_kwh'):,.0f} kWh", styles['small']),
            _paragraph(f"{variance:+.1f}%", styles['small']),
            _paragraph(
                f"{_number(row, 'peak_kw'):,.1f} kW | "
                f"{_format_timestamp(row.get('peak_kw_time'))[-5:]}",
                styles['small'],
            ),
            _paragraph(
                'Confirm against inverter portal and weather.'
                if positive else
                'Check weather, inverter availability, outages and faults.',
                styles['small'],
            ),
        ])
    header_color = TEAL if positive else ORANGE
    table = Table(
        data,
        colWidths=[17 * mm, 25 * mm, 21 * mm, 24 * mm, 20 * mm, 29 * mm, 44 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), .6, BORDER),
        ('LINEBELOW', (0, 1), (-1, -1), .35, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.line(15 * mm, 13 * mm, 195 * mm, 13 * mm)
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm, 8 * mm, 'Connect Logistics | Internal electricity management')
    canvas.drawRightString(195 * mm, 8 * mm, f'Page {doc.page}')
    canvas.restoreState()


def generate_pdf_report(
    dataset: DashboardDataset,
    report_type: str,
    anchor_date: date,
    area: str,
    output_dir: str | Path | None = None,
    selected_start: date | None = None,
    selected_end: date | None = None,
    comparison_start: date | None = None,
    comparison_end: date | None = None,
    comparison_method: str = '',
    tariff_rates: Iterable[TariffRate] = BUILT_IN_RATES,
    ctou_tariff_rates: Iterable[CtouTariffRate] = BUILT_IN_CTOU_RATES,
) -> Path:
    bundle = _report_bundle(
        dataset,
        report_type,
        anchor_date,
        area,
        selected_start,
        selected_end,
        comparison_start,
        comparison_end,
        comparison_method,
        tariff_rates,
        ctou_tariff_rates,
    )
    period = bundle.period
    view = bundle.view
    target_dir = Path(output_dir or DEFAULT_OUTPUT_DIR / 'pdf').resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = report_type.lower().replace(' ', '_')
    area_slug = 'all_areas' if area == ALL_AREAS else area.lower().replace(' ', '_')
    path = target_dir / f'sydney_road_{slug}_{area_slug}_{period.end_date.isoformat()}.pdf'
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=13 * mm, bottomMargin=18 * mm,
        title=f'Sydney Road {report_type} Electricity Report',
        author='Connect Logistics',
    )
    story: list[object] = [
        _header(styles, period, area),
        Spacer(1, 5 * mm),
        _paragraph('Energy and demand overview', styles['section']),
        _kpi_table(bundle, styles, area),
        Spacer(1, 4 * mm),
    ]
    cost_cards = _cost_kpi_table(bundle, styles)
    if cost_cards is not None:
        story.extend([
            _paragraph('Cost and current-month projection', styles['section']),
            cost_cards,
            Spacer(1, 4 * mm),
        ])
    story.extend([
        _quality_notes(bundle.quality_notes, styles),
        PageBreak(),
        _paragraph('Management summary and recommended checks', styles['section']),
        _management_summary(bundle, styles),
        Spacer(1, 4 * mm),
        Table(
            [[_paragraph(
                'The meter data identifies what changed, where it changed and when the peak occurred. '
                'Operational records are still required to confirm why it changed.',
                styles['callout'],
            )]],
            colWidths=[180 * mm],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF4EE')),
                ('BOX', (0, 0), (-1, -1), .7, ORANGE),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ]),
        ),
        PageBreak(),
    ])
    if bundle.cost_analysis is not None:
        comparison_basis = ''
        if period.comparison_enabled:
            _movement, movement_basis = _cost_comparison_change(bundle.cost_analysis)
            comparison_basis = (
                f'{movement_basis} is used for the headline movement because the selected and comparison ranges '
                + (
                    'contain different numbers of available days.'
                    if int(bundle.cost_analysis.metrics['current_days']) != int(bundle.cost_analysis.metrics['comparison_days'])
                    else 'contain the same number of available days.'
                )
            )
        story.extend([
            _paragraph(
                'Cost comparison detail' if period.comparison_enabled else 'Current cost detail',
                styles['section'],
            ),
            _paragraph(
                (
                    comparison_basis
                    if comparison_basis else
                    'This overview shows recorded electricity cost before solar savings, the estimated solar savings and cost after those savings.'
                ),
                styles['small_muted'],
            ),
            Spacer(1, 2 * mm),
            _cost_area_chart(bundle.cost_analysis, period.comparison_enabled),
            Spacer(1, 3 * mm),
            _cost_area_table(bundle.cost_analysis, styles, period.comparison_enabled),
            PageBreak(),
        ])
    story.extend([
        _paragraph(
            'Electricity comparison visuals'
            if period.comparison_enabled else 'Electricity overview visuals',
            styles['section'],
        ),
        _daily_chart(view, area, period.comparison_enabled),
        Spacer(1, 2 * mm),
        _area_chart(view, area, period.comparison_enabled),
        Spacer(1, 3 * mm),
        _paragraph(
            'Energy performance by warehouse area' if area == ALL_AREAS else 'Meter energy performance',
            styles['section'],
        ),
        _area_table(view, styles, period.comparison_enabled),
        PageBreak(),
    ])
    detail_title, detail_table = _period_detail_table(bundle, styles)
    story.extend([
        _paragraph(
            'Peak load, demand and review level by warehouse area' if area == ALL_AREAS else 'Peak load, demand and review level',
            styles['section'],
        ),
        _demand_table(view, styles),
        Spacer(1, 4 * mm),
        _paragraph(detail_title, styles['section']),
        _paragraph(
            'The table keeps the selected period visible at a useful management level. '
            + (
                'Recorded comparison percentages remain visible; use the quality column to judge confidence.'
                if period.comparison_enabled else
                'No comparison is applied to this overview report.'
            ),
            styles['small_muted'],
        ),
        Spacer(1, 2 * mm),
        detail_table,
    ])
    if bundle.solar_view is not None:
        story.extend([
            KeepTogether([
                Spacer(1, 5 * mm),
                _paragraph('Solar generation - reported separately', styles['section']),
                _solar_summary(bundle.solar_view, styles, period.comparison_enabled),
                Spacer(1, 2 * mm),
                _paragraph(
                    'This is electricity generated by the solar installation. It is reported separately, is not added to warehouse electricity consumption and does not prove how much solar was self-consumed or exported.',
                    styles['small_muted'],
                ),
            ]),
            Spacer(1, 2 * mm),
            _daily_chart(bundle.solar_view, SOLAR_AREA, period.comparison_enabled),
            Spacer(1, 2 * mm),
            _solar_analysis_table(bundle.solar_view, styles, period.comparison_enabled),
            Spacer(1, 4 * mm),
            _paragraph('Low solar generation - warnings to investigate', styles['section']),
            _paragraph(
                'A warning is raised only when complete daily generation is at least 30% and 30 kWh below the recent same-weekday average.',
                styles['small_muted'],
            ),
            Spacer(1, 2 * mm),
            _solar_signal_table(bundle.solar_low_rows, styles, positive=False),
            KeepTogether([
                Spacer(1, 4 * mm),
                _paragraph('Higher solar generation - positive if verified', styles['section']),
                _paragraph(
                    'Higher output is not an unusual-usage incident. Confirm it against the inverter portal and weather, then retain it as a positive performance event.',
                    styles['small_muted'],
                ),
                Spacer(1, 2 * mm),
                _solar_signal_table(bundle.solar_positive_rows, styles, positive=True),
            ]),
        ])
    story.extend([
        Spacer(1, 6 * mm) if bundle.solar_view is not None else PageBreak(),
        _paragraph('Unusual warehouse readings requiring review', styles['section']),
        _paragraph(
            'These are warehouse use or demand readings above their recent same-weekday pattern. Solar performance signals are reported separately in the solar section.',
            styles['small_muted'],
        ),
        Spacer(1, 2 * mm),
        _unusual_table(bundle.unusual_rows, styles),
    ])
    if len(bundle.unusual_rows) > 10:
        story.extend([
            Spacer(1, 2 * mm),
            _paragraph(
                f"Showing the 10 most recent of {len(bundle.unusual_rows)} unusual readings. Export XLSX or CSV for the complete filtered register.",
                styles['small_muted'],
            ),
        ])
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return path


CSV_FIELDS = [
    'date', 'area', 'account_name', 'account_code', 'data_quality_status',
    'import_kwh', 'export_kwh', 'net_kwh', 'average_kw', 'peak_kw', 'peak_kw_time',
    'average_kva', 'peak_kva', 'peak_kva_time', 'alert_level', 'alert_reason',
    'investigation_status', 'cause_category', 'confirmed_cause', 'corrective_action',
    'responsible_person', 'target_close_date', 'investigation_notes',
]


def generate_csv_export(
    dataset: DashboardDataset,
    start_date: date,
    end_date: date,
    area: str,
    output_dir: str | Path | None = None,
) -> Path:
    view = build_dashboard_view(dataset, start_date, end_date, area)
    target_dir = Path(output_dir or DEFAULT_OUTPUT_DIR / 'csv').resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    area_slug = 'all_areas' if area == ALL_AREAS else area.lower().replace(' ', '_')
    path = target_dir / f'sydney_road_filtered_{area_slug}_{view.start_date}_{view.end_date}.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(view.daily)
    return path


def _xlsx_payload(
    view: DashboardView,
    summary_view: DashboardView | None = None,
    solar_view: DashboardView | None = None,
) -> dict:
    management_view = summary_view or view
    solar_separated = solar_view is not None
    insights = [
        {'title': item['title'], 'text': _clean(item['text'])}
        for item in management_view.insights
    ]
    if solar_separated:
        for insight in insights:
            insight['text'] = (
                insight['text']
                .replace('Electricity use', 'Warehouse electricity use')
                .replace("selected meters' total", "selected warehouses' total")
            )
            if insight['title'] == 'Unusual usage days':
                warehouse_alerts = int(management_view.metrics['alert_days'])
                solar_low = sum(
                    row.get('area') == SOLAR_AREA for row in view.spikes
                )
                alert_days = warehouse_alerts + solar_low
                critical_days = sum(
                    row.get('alert_level') == 'Critical' for row in view.spikes
                )
                insight['text'] = (
                    f'{alert_days} warehouse use/demand or low-solar readings require review, '
                    f'including {critical_days} marked urgent.'
                    if alert_days else 'No unusual warehouse use, demand or low-solar readings were detected.'
                )
        positive_count = len(view.solar_positive_events)
        insights.append({
            'title': 'Higher solar generation',
            'text': (
                f'{positive_count} day(s) generated materially more solar energy than usual. '
                'These are positive events to verify, not unusual-usage incidents.'
                if positive_count else
                'No materially higher-than-usual solar generation days were detected.'
            ),
        })
    return {
        'title': 'Sydney Road Electricity Data Export',
        'scope': f"{_scope(view.area)} | {view.start_date.strftime('%d %B %Y')} to {view.end_date.strftime('%d %B %Y')}",
        'generatedAt': datetime.now().strftime('%d %B %Y at %H:%M'),
        'solarSeparated': solar_separated,
        'energyCardLabel': 'WAREHOUSE ELECTRICITY' if solar_separated else (
            'SOLAR GENERATED' if view.area == SOLAR_AREA else 'ELECTRICITY USED'
        ),
        'daily': [
            {
                'date': row['date'],
                'area': row['area'],
                'electricityUsedKwh': float(row.get('import_kwh', 0) or 0),
                'peakKw': float(row.get('peak_kw', 0) or 0),
                'peakKva': float(row.get('peak_kva', 0) or 0),
                'unusual': 'Yes' if row.get('alert_level') in {'Watch', 'High', 'Critical'} else 'No',
                'reviewLevel': (
                    'Higher output - verify'
                    if row.get('alert_level') == 'Positive'
                    else row.get('alert_level', '')
                ),
                'peakTime': row.get('peak_kva_time', ''),
                'dataQuality': row.get('data_quality_status', ''),
                'meterClassification': (
                    'Solar generation' if row.get('area') == SOLAR_AREA
                    else 'Warehouse consumption'
                ),
            }
            for row in view.daily
        ],
        'unusual': [
            {
                'date': row['date'],
                'area': row['area'],
                'reviewLevel': row['alert_level'],
                'type': row['alert_type'],
                'whatMeterShows': _reason(row),
                'suggestedCheck': _check(row),
                'peakTime': row.get('peak_kva_time', ''),
                'status': row.get('investigation_status', ''),
            }
            for row in view.spikes
            if row.get('area') != SOLAR_AREA
        ],
        'solarWarnings': [
            {
                'date': row['date'],
                'classification': row['alert_level'],
                'generatedKwh': _number(row, 'import_kwh'),
                'usualKwh': _number(row, 'baseline_import_kwh'),
                'differencePercent': _number(row, 'consumption_variance_percent'),
                'peakKw': _number(row, 'peak_kw'),
                'peakTime': row.get('peak_kw_time', ''),
                'action': 'Check weather, inverter availability, outages and faults.',
            }
            for row in view.spikes
            if row.get('area') == SOLAR_AREA
        ],
        'solarPositive': [
            {
                'date': row['date'],
                'classification': 'Positive - verify',
                'generatedKwh': _number(row, 'import_kwh'),
                'usualKwh': _number(row, 'baseline_import_kwh'),
                'differencePercent': _number(row, 'consumption_variance_percent'),
                'peakKw': _number(row, 'peak_kw'),
                'peakTime': row.get('peak_kw_time', ''),
                'action': 'Confirm against the inverter portal and weather.',
            }
            for row in view.solar_positive_events
        ],
        'insights': insights,
        'areas': [str(row['area']) for row in management_view.area_summary],
    }


def generate_xlsx_export(
    dataset: DashboardDataset,
    start_date: date,
    end_date: date,
    area: str,
    output_dir: str | Path | None = None,
    preview_path: str | Path | None = None,
) -> Path:
    import xlsxwriter

    _ = preview_path  # Kept for compatibility with earlier callers.
    view = build_dashboard_view(dataset, start_date, end_date, area)
    summary_view = view
    solar_view: DashboardView | None = None
    if area == ALL_AREAS and SOLAR_AREA in dataset.areas:
        summary_view = build_dashboard_view(
            without_area(dataset, SOLAR_AREA), start_date, end_date, ALL_AREAS
        )
        solar_view = build_dashboard_view(dataset, start_date, end_date, SOLAR_AREA)
    target_dir = Path(output_dir or DEFAULT_OUTPUT_DIR / 'xlsx').resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    area_slug = 'all_areas' if area == ALL_AREAS else area.lower().replace(' ', '_')
    output_path = target_dir / f'sydney_road_filtered_{area_slug}_{view.start_date}_{view.end_date}.xlsx'
    payload = _xlsx_payload(view, summary_view, solar_view)
    output_path.unlink(missing_ok=True)
    workbook = xlsxwriter.Workbook(output_path)
    workbook.set_properties({
        'title': str(payload['title']),
        'subject': str(payload['scope']),
        'author': 'Connect Logistics',
        'company': 'Connect Logistics',
    })
    navy = '#1C2545'
    orange = '#E04403'
    teal = '#008674'
    pale = '#F4F7FB'
    border = '#D6DEEA'
    muted = '#64748B'
    header = workbook.add_format({
        'bold': True, 'font_color': '#FFFFFF', 'bg_color': navy,
        'border': 1, 'border_color': border, 'text_wrap': True, 'valign': 'vcenter',
    })
    text_cell = workbook.add_format({'border': 1, 'border_color': border, 'valign': 'top'})
    wrap_cell = workbook.add_format({
        'border': 1, 'border_color': border, 'valign': 'top', 'text_wrap': True,
    })
    number_cell = workbook.add_format({
        'border': 1, 'border_color': border, 'num_format': '#,##0.0', 'valign': 'top',
    })
    date_cell = workbook.add_format({
        'border': 1, 'border_color': border, 'num_format': 'yyyy-mm-dd', 'valign': 'top',
    })
    percent_cell = workbook.add_format({
        'border': 1, 'border_color': border, 'num_format': '+0.0%;-0.0%;0.0%', 'valign': 'top',
    })

    def write_table_sheet(
        name: str,
        headers: list[str],
        rows: list[list[object]],
        widths: list[float],
        date_columns: set[int] = set(),
        number_columns: set[int] = set(),
        percent_columns: set[int] = set(),
    ):
        sheet = workbook.add_worksheet(name)
        sheet.hide_gridlines(2)
        sheet.freeze_panes(1, 0)
        sheet.set_row(0, 32)
        for column, value in enumerate(headers):
            sheet.write(0, column, value, header)
            sheet.set_column(column, column, widths[column])
        for row_index, values in enumerate(rows, start=1):
            sheet.set_row(row_index, 30 if any(len(str(value)) > 55 for value in values) else 20)
            for column, value in enumerate(values):
                if column in date_columns and value:
                    parsed = datetime.fromisoformat(str(value)[:10])
                    sheet.write_datetime(row_index, column, parsed, date_cell)
                elif column in percent_columns:
                    sheet.write_number(row_index, column, float(value or 0) / 100.0, percent_cell)
                elif column in number_columns:
                    sheet.write_number(row_index, column, float(value or 0), number_cell)
                else:
                    sheet.write(row_index, column, value, wrap_cell if len(str(value)) > 30 else text_cell)
        if rows:
            sheet.autofilter(0, 0, len(rows), len(headers) - 1)
        return sheet

    daily_rows = [[
        row['date'], row['area'], row['electricityUsedKwh'], row['peakKw'], row['peakKva'],
        row['unusual'], row['reviewLevel'], row['peakTime'], row['dataQuality'], row['meterClassification'],
    ] for row in payload['daily']]
    daily_sheet = write_table_sheet(
        'Daily Usage',
        ['Date', 'Area', 'Electricity used (kWh)', 'Highest load (kW)', 'Highest demand (kVA)',
         'Unusual', 'Review level', 'Peak time', 'Data quality', 'Meter classification'],
        daily_rows,
        [13, 24, 21, 20, 22, 12, 18, 22, 16, 24],
        {0}, {2, 3, 4},
    )
    if daily_rows:
        daily_sheet.conditional_format(1, 5, len(daily_rows), 5, {
            'type': 'text', 'criteria': 'containing', 'value': 'Yes',
            'format': workbook.add_format({'bg_color': '#FDECEC', 'font_color': '#B91C1C', 'bold': True}),
        })

    unusual_rows = [[
        row['date'], row['area'], row['reviewLevel'], row['type'], row['whatMeterShows'],
        row['suggestedCheck'], row['peakTime'], row['status'],
    ] for row in payload['unusual']]
    write_table_sheet(
        'Unusual Usage',
        ['Date', 'Area', 'Review level', 'What changed', 'What the meter shows',
         'Suggested operational check', 'Peak time', 'Status'],
        unusual_rows, [13, 22, 16, 18, 48, 48, 22, 16], {0},
    )

    solar_rows = [[
        row['date'], f"Low generation - {row['classification']}", row['generatedKwh'],
        row['usualKwh'], row['differencePercent'], row['peakKw'], str(row['peakTime'])[-5:], row['action'],
    ] for row in payload['solarWarnings']]
    solar_rows.extend([[
        row['date'], row['classification'], row['generatedKwh'], row['usualKwh'],
        row['differencePercent'], row['peakKw'], str(row['peakTime'])[-5:], row['action'],
    ] for row in payload['solarPositive']])
    solar_sheet = write_table_sheet(
        'Solar Signals',
        ['Date', 'Classification', 'Generated (kWh)', 'Recent average (kWh)', 'Difference vs usual',
         'Peak output (kW)', 'Peak time', 'Verification / first check'],
        solar_rows, [13, 25, 20, 22, 20, 20, 15, 48], {0}, {2, 3, 5}, {4},
    )
    if solar_rows:
        solar_sheet.conditional_format(1, 1, len(solar_rows), 1, {
            'type': 'text', 'criteria': 'containing', 'value': 'Positive',
            'format': workbook.add_format({'bg_color': '#E8F6F3', 'font_color': '#00695C', 'bold': True}),
        })
        solar_sheet.conditional_format(1, 1, len(solar_rows), 1, {
            'type': 'text', 'criteria': 'containing', 'value': 'Low generation',
            'format': workbook.add_format({'bg_color': '#FDECEC', 'font_color': '#B91C1C', 'bold': True}),
        })

    summary = workbook.add_worksheet('Summary')
    summary.hide_gridlines(2)
    summary.freeze_panes(4, 0)
    summary.set_column('A:B', 19)
    summary.set_column('C:H', 17)
    title_format = workbook.add_format({
        'bold': True, 'font_color': '#FFFFFF', 'bg_color': navy, 'font_size': 20,
        'valign': 'vcenter', 'align': 'left',
    })
    scope_format = workbook.add_format({'bold': True, 'font_color': navy, 'bg_color': '#EEF2F8'})
    note_format = workbook.add_format({'font_color': muted, 'italic': True, 'font_size': 9})
    summary.merge_range('A1:H2', str(payload['title']), title_format)
    summary.merge_range('A3:H3', str(payload['scope']), scope_format)
    summary.merge_range('A4:H4', f"Generated {payload['generatedAt']} | Filtered management export", note_format)

    warehouse_daily = [row for row in payload['daily'] if row['meterClassification'] == 'Warehouse consumption']
    energy_rows = warehouse_daily if payload['solarSeparated'] else list(payload['daily'])
    cards = [
        (str(payload['energyCardLabel']), sum(float(row['electricityUsedKwh']) for row in energy_rows), 'kWh', orange),
        ('HIGHEST LOAD', max((float(row['peakKw']) for row in energy_rows), default=0.0), 'kW', navy),
        ('HIGHEST DEMAND', max((float(row['peakKva']) for row in energy_rows), default=0.0), 'kVA', navy),
        ('UNUSUAL READINGS', sum(row['unusual'] == 'Yes' for row in energy_rows), '', orange),
    ]
    card_ranges = [('A6:B6', 'A7:B8'), ('C6:D6', 'C7:D8'), ('E6:F6', 'E7:F8'), ('G6:H6', 'G7:H8')]
    for (label, value, unit, accent), (label_range, value_range) in zip(cards, card_ranges):
        label_style = workbook.add_format({
            'bold': True, 'font_color': muted, 'bg_color': pale, 'border': 1,
            'border_color': border, 'top': 3, 'top_color': accent, 'align': 'center', 'valign': 'vcenter',
        })
        value_style = workbook.add_format({
            'bold': True, 'font_color': navy, 'bg_color': pale, 'border': 1,
            'border_color': border, 'font_size': 15, 'align': 'center', 'valign': 'vcenter',
            'num_format': f'#,##0.0 "{unit}"' if unit else '#,##0',
        })
        summary.merge_range(label_range, label, label_style)
        summary.merge_range(value_range, value, value_style)

    row_cursor = 10
    if payload['solarSeparated']:
        solar_total = sum(
            float(row['electricityUsedKwh']) for row in payload['daily']
            if row['meterClassification'] == 'Solar generation'
        )
        solar_format = workbook.add_format({
            'bg_color': '#FFF8E6', 'font_color': navy, 'border': 1,
            'border_color': '#F1C36A', 'bold': True, 'valign': 'vcenter',
        })
        solar_note_format = workbook.add_format({
            'bg_color': '#FFF8E6', 'font_color': navy, 'border': 1,
            'border_color': '#F1C36A', 'bold': True, 'valign': 'vcenter',
            'text_wrap': True,
        })
        summary.merge_range(row_cursor - 1, 0, row_cursor - 1, 1, 'SOLAR GENERATED', solar_format)
        summary.merge_range(row_cursor - 1, 2, row_cursor - 1, 3, solar_total, solar_format)
        summary.merge_range(
            row_cursor - 1, 4, row_cursor - 1, 7,
            'Generated electricity supplied by the solar installation; shown separately from warehouse consumption.',
            solar_note_format,
        )
        row_cursor += 2

    summary.merge_range(row_cursor - 1, 0, row_cursor - 1, 7, 'WHAT THE FILTERED DATA SHOWS',
                        workbook.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': orange}))
    row_cursor += 1
    insight_title = workbook.add_format({'bold': True, 'font_color': navy, 'bg_color': pale, 'border': 1, 'border_color': border, 'valign': 'top'})
    insight_text = workbook.add_format({'font_color': navy, 'bg_color': pale, 'border': 1, 'border_color': border, 'text_wrap': True, 'valign': 'top'})
    for insight in payload['insights']:
        summary.set_row(row_cursor - 1, 38)
        summary.merge_range(row_cursor - 1, 0, row_cursor - 1, 1, insight['title'], insight_title)
        summary.merge_range(row_cursor - 1, 2, row_cursor - 1, 7, insight['text'], insight_text)
        row_cursor += 1

    row_cursor += 1
    for column, label in enumerate(['Area', 'Electricity used (kWh)', 'Share of total', 'Unusual readings']):
        summary.write(row_cursor - 1, column, label, header)
    row_cursor += 1
    total_energy = sum(float(row['electricityUsedKwh']) for row in energy_rows)
    for area_name in payload['areas']:
        matching = [row for row in energy_rows if row['area'] == area_name]
        used = sum(float(row['electricityUsedKwh']) for row in matching)
        summary.write(row_cursor - 1, 0, area_name, text_cell)
        summary.write_number(row_cursor - 1, 1, used, number_cell)
        summary.write_number(row_cursor - 1, 2, used / total_energy if total_energy else 0,
                             workbook.add_format({'border': 1, 'border_color': border, 'num_format': '0.0%'}))
        summary.write_number(row_cursor - 1, 3, sum(row['unusual'] == 'Yes' for row in matching), text_cell)
        row_cursor += 1

    workbook.close()
    if not output_path.exists() or output_path.stat().st_size <= 1024 or output_path.read_bytes()[:2] != b'PK':
        raise ReportGenerationError('Spreadsheet export did not produce a valid XLSX workbook.')
    return output_path
