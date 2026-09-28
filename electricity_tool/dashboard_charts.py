from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from math import ceil, floor
from statistics import fmean

from .dashboard_data import (
    ALL_AREAS,
    SOLAR_AREA,
    DashboardView,
    PeriodComparison,
    SolarPerformance,
    SupplyDemandBalance,
)
from .tariffs import CostAnalysis
from .solar_investment import SolarProposal


AREA_COLORS = {
    'Connect Logistics Solar': '#F59E0B',
    'Warehouse 6': '#2563EB',
    'Warehouse 7': '#06B6D4',
    'Warehouse 8': '#8B5CF6',
    'Warehouse 9': '#10B981',
}
FALLBACK_COLORS = ['#2563EB', '#06B6D4', '#8B5CF6', '#10B981', '#F59E0B']


def _number(row: dict, field: str) -> float:
    try:
        return float(row.get(field, '') or 0)
    except (TypeError, ValueError):
        return 0.0


def _theme() -> dict[str, str]:
    return {
        'text': '#1C2545',
        'muted': '#64748B',
        'grid': '#D6DEEA',
    }


def _base(title: str) -> dict:
    palette = _theme()
    return {
        'animationDuration': 500,
        'backgroundColor': 'transparent',
        'textStyle': {'color': palette['text']},
        'color': FALLBACK_COLORS,
        'title': {
            'text': title,
            'left': 8,
            'top': 2,
            'textStyle': {'fontSize': 15, 'fontWeight': 700, 'color': palette['text']},
        },
        'tooltip': {'trigger': 'axis'},
        'toolbox': {
            'right': 8,
            'top': 0,
            'feature': {'saveAsImage': {'title': 'Save chart'}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 88, 'bottom': 62, 'containLabel': True},
    }


def daily_energy_options(view: DashboardView) -> dict:
    palette = _theme()
    option = _base('Electricity used each day')
    dates = sorted({row['date'] for row in view.daily})
    grouped: dict[str, dict[str, float]] = defaultdict(dict)
    for row in view.daily:
        grouped[row['area']][row['date']] = _number(row, 'import_kwh')
    series = []
    for index, (area, values) in enumerate(sorted(grouped.items())):
        color = AREA_COLORS.get(area, FALLBACK_COLORS[index % len(FALLBACK_COLORS)])
        series.append({
            'name': area,
            'type': 'line',
            'smooth': True,
            'showSymbol': len(dates) <= 31,
            'symbolSize': 5,
            'lineStyle': {'width': 2},
            'itemStyle': {'color': color},
            'areaStyle': {'opacity': 0.05} if len(grouped) == 1 else None,
            'data': [round(values.get(day, 0), 2) for day in dates],
        })
        if len(grouped) == 1:
            raw = [values.get(day, 0) for day in dates]
            rolling = [round(fmean(raw[max(0, i - 6):i + 1]), 2) for i in range(len(raw))]
            series.append({
                'name': '7-day average',
                'type': 'line',
                'smooth': True,
                'showSymbol': False,
                'lineStyle': {'type': 'dashed', 'width': 2, 'color': '#64748B'},
                'data': rolling,
            })
    option.update({
        'legend': {
            'top': 38,
            'left': 8,
            'right': 36,
            'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category',
            'data': dates,
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value',
            'name': 'kWh',
            'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 92, 'bottom': 82, 'containLabel': True},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 18, 'bottom': 8}],
        'series': series,
    })
    return option


def weekly_energy_options(view: DashboardView) -> dict:
    palette = _theme()
    option = _base('Electricity used each week')
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    weeks: set[str] = set()
    for row in view.daily:
        value = date.fromisoformat(row['date'])
        week = (value - timedelta(days=value.weekday())).isoformat()
        weeks.add(week)
        grouped[row['area']][week] += _number(row, 'import_kwh')
    ordered_weeks = sorted(weeks)
    series = [
        {
            'name': area,
            'type': 'bar',
            'stack': 'energy',
            'emphasis': {'focus': 'series'},
            'itemStyle': {'color': AREA_COLORS.get(area, FALLBACK_COLORS[index % len(FALLBACK_COLORS)])},
            'data': [round(values.get(week, 0), 2) for week in ordered_weeks],
        }
        for index, (area, values) in enumerate(sorted(grouped.items()))
    ]
    option.update({
        'legend': {
            'top': 38,
            'left': 8,
            'right': 36,
            'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category',
            'data': ordered_weeks,
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value',
            'name': 'kWh',
            'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 92, 'bottom': 82, 'containLabel': True},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 18, 'bottom': 8}],
        'series': series,
    })
    return option


def demand_by_area_options(view: DashboardView) -> dict:
    palette = _theme()
    option = _base('Highest demand by area')
    rows = sorted(view.area_summary, key=lambda row: float(row['peak_kva']), reverse=True)
    option.update({
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 116, 'right': 34, 'top': 68, 'bottom': 48, 'containLabel': True},
        'xAxis': {
            'type': 'value',
            'name': 'kVA',
            'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'yAxis': {
            'type': 'category',
            'data': [row['area'] for row in reversed(rows)],
            'axisLabel': {'color': palette['muted']},
        },
        'series': [{
            'name': 'Peak kVA',
            'type': 'bar',
            'barMaxWidth': 28,
            'data': [
                {
                    'value': round(float(row['peak_kva']), 2),
                    'itemStyle': {'color': AREA_COLORS.get(str(row['area']), '#2563EB')},
                }
                for row in reversed(rows)
            ],
        }],
    })
    return option


def consumption_share_options(view: DashboardView) -> dict:
    palette = _theme()
    return {
        'backgroundColor': 'transparent',
        'textStyle': {'color': palette['text']},
        'title': {
            'text': 'Share of recorded electricity use',
            'left': 8,
            'top': 2,
            'textStyle': {'fontSize': 15, 'fontWeight': 700, 'color': palette['text']},
        },
        'tooltip': {'trigger': 'item'},
        'legend': {
            'bottom': 2,
            'left': 8,
            'right': 8,
            'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'series': [{
            'name': 'Electricity used',
            'type': 'pie',
            'radius': ['36%', '61%'],
            'center': ['50%', '51%'],
            'top': 40,
            'bottom': 54,
            'avoidLabelOverlap': True,
            'label': {'formatter': '{b}\n{d}%', 'fontSize': 11, 'lineHeight': 15},
            'labelLine': {'length': 10, 'length2': 8},
            'labelLayout': {'hideOverlap': True},
            'data': [
                {
                    'name': row['area'],
                    'value': round(float(row['import_kwh']), 2),
                    'itemStyle': {'color': AREA_COLORS.get(str(row['area']), '#2563EB')},
                }
                for row in view.area_summary
            ],
        }],
    }


def load_profile_options(profile: list[dict[str, object]]) -> dict:
    palette = _theme()
    option = _base('Typical demand through the day')
    option.update({
        'legend': {
            'top': 38,
            'left': 8,
            'right': 36,
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category',
            'data': [row['time'] for row in profile],
            'axisLabel': {'interval': 3, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value',
            'name': 'kW',
            'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': 'Average kW',
                'type': 'line',
                'smooth': True,
                'showSymbol': False,
                'areaStyle': {'opacity': 0.12},
                'lineStyle': {'width': 3, 'color': '#2563EB'},
                'data': [round(float(row['average_kw']), 2) for row in profile],
            },
            {
                'name': 'Observed peak kW',
                'type': 'line',
                'smooth': True,
                'showSymbol': False,
                'lineStyle': {'width': 2, 'type': 'dashed', 'color': '#F59E0B'},
                'data': [round(float(row['peak_kw']), 2) for row in profile],
            },
        ],
    })
    return option


def solar_daily_generation_options(performance: SolarPerformance) -> dict:
    palette = _theme()
    option = _base('Solar energy generated each day')
    dates = [str(row['date']) for row in performance.daily]
    generated = [float(row['generation_kwh']) for row in performance.daily]
    rolling = [
        round(fmean(generated[max(0, index - 6):index + 1]), 2)
        for index in range(len(generated))
    ]
    option.update({
        'legend': {
            'top': 38, 'left': 8, 'right': 36, 'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category', 'data': dates,
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value', 'name': 'kWh', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 92, 'bottom': 82, 'containLabel': True},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 18, 'bottom': 8}],
        'series': [
            {
                'name': 'Solar generated', 'type': 'line', 'smooth': True,
                'showSymbol': len(dates) <= 31, 'symbolSize': 5,
                'lineStyle': {'width': 3, 'color': '#F59E0B'},
                'itemStyle': {'color': '#F59E0B'},
                'areaStyle': {'opacity': 0.08, 'color': '#F59E0B'},
                'data': [round(value, 2) for value in generated],
            },
            {
                'name': '7-day average', 'type': 'line', 'smooth': True,
                'showSymbol': False,
                'lineStyle': {'width': 2, 'type': 'dashed', 'color': '#007D6D'},
                'itemStyle': {'color': '#007D6D'},
                'data': rolling,
            },
        ],
    })
    return option


def solar_period_generation_options(performance: SolarPerformance) -> dict:
    palette = _theme()
    span = (performance.end_date - performance.start_date).days + 1
    use_months = span > 92
    totals: dict[str, float] = defaultdict(float)
    for row in performance.daily:
        value = date.fromisoformat(str(row['date']))
        label = (
            value.strftime('%Y-%m')
            if use_months else
            (value - timedelta(days=value.weekday())).isoformat()
        )
        totals[label] += float(row['generation_kwh'])
    labels = sorted(totals)
    option = _base(
        'Solar energy generated each month'
        if use_months else 'Solar energy generated each week'
    )
    option.update({
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'xAxis': {
            'type': 'category', 'data': labels,
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value', 'name': 'kWh', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 72, 'bottom': 82, 'containLabel': True},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 18, 'bottom': 8}],
        'series': [{
            'name': 'Solar generated', 'type': 'bar', 'barMaxWidth': 30,
            'itemStyle': {'color': '#F59E0B', 'borderRadius': [4, 4, 0, 0]},
            'data': [round(totals[label], 2) for label in labels],
        }],
    })
    return option


def solar_daily_peak_options(performance: SolarPerformance) -> dict:
    palette = _theme()
    option = _base('Highest recorded solar output each day')
    dates = [str(row['date']) for row in performance.daily]
    option.update({
        'xAxis': {
            'type': 'category', 'data': dates,
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value', 'name': 'kW', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 72, 'bottom': 82, 'containLabel': True},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 18, 'bottom': 8}],
        'series': [{
            'name': 'Peak output', 'type': 'line', 'smooth': True,
            'showSymbol': len(dates) <= 31, 'symbolSize': 5,
            'lineStyle': {'width': 3, 'color': '#E04403'},
            'itemStyle': {'color': '#E04403'},
            'data': [round(float(row['peak_kw']), 2) for row in performance.daily],
        }],
    })
    return option


def solar_output_profile_options(performance: SolarPerformance) -> dict:
    palette = _theme()
    option = _base('Typical solar output through the day')
    option.update({
        'legend': {
            'top': 38, 'left': 8, 'right': 36,
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category',
            'data': [str(row['time']) for row in performance.profile],
            'axisLabel': {'interval': 3, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value', 'name': 'kW', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': 'Average output', 'type': 'line', 'smooth': True,
                'showSymbol': False,
                'lineStyle': {'width': 3, 'color': '#F59E0B'},
                'itemStyle': {'color': '#F59E0B'},
                'areaStyle': {'opacity': 0.1, 'color': '#F59E0B'},
                'data': [round(float(row['average_kw']), 2) for row in performance.profile],
            },
            {
                'name': 'Highest observed output', 'type': 'line', 'smooth': True,
                'showSymbol': False,
                'lineStyle': {'width': 2, 'type': 'dashed', 'color': '#E04403'},
                'itemStyle': {'color': '#E04403'},
                'data': [round(float(row['peak_kw']), 2) for row in performance.profile],
            },
        ],
    })
    return option


def daily_supply_demand_options(balance: SupplyDemandBalance) -> dict:
    palette = _theme()
    option = _base('Daily warehouse use vs solar generation')
    dates = [str(row['date']) for row in balance.daily]
    option.update({
        'legend': {
            'top': 38,
            'left': 8,
            'right': 36,
            'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'xAxis': {
            'type': 'category',
            'data': dates,
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value',
            'name': 'kWh',
            'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 96, 'bottom': 82, 'containLabel': True},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 18, 'bottom': 8}],
        'series': [
            {
                'name': 'Warehouse use',
                'type': 'bar',
                'barMaxWidth': 24,
                'itemStyle': {'color': '#2563EB'},
                'data': [round(float(row['warehouse_kwh']), 2) for row in balance.daily],
            },
            {
                'name': 'Solar generated',
                'type': 'bar',
                'barMaxWidth': 24,
                'itemStyle': {'color': '#F59E0B'},
                'data': [round(float(row['solar_kwh']), 2) for row in balance.daily],
            },
            {
                'name': 'Estimated grid energy needed',
                'type': 'line',
                'smooth': True,
                'showSymbol': len(dates) <= 31,
                'symbolSize': 5,
                'lineStyle': {'width': 3, 'color': '#1C2545'},
                'itemStyle': {'color': '#1C2545'},
                'data': [
                    round(float(row['estimated_grid_kwh']), 2) for row in balance.daily
                ],
            },
        ],
    })
    return option


def supply_demand_profile_options(balance: SupplyDemandBalance) -> dict:
    palette = _theme()
    option = _base('Typical load and solar generation through the day')
    option.update({
        'legend': {
            'top': 38,
            'left': 8,
            'right': 36,
            'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category',
            'data': [str(row['time']) for row in balance.profile],
            'axisLabel': {'interval': 3, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value',
            'name': 'kW',
            'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': 'Average warehouse load',
                'type': 'line',
                'smooth': True,
                'showSymbol': False,
                'lineStyle': {'width': 3, 'color': '#2563EB'},
                'areaStyle': {'opacity': 0.06},
                'data': [
                    round(float(row['average_warehouse_kw']), 2)
                    for row in balance.profile
                ],
            },
            {
                'name': 'Average solar output',
                'type': 'line',
                'smooth': True,
                'showSymbol': False,
                'lineStyle': {'width': 3, 'color': '#F59E0B'},
                'areaStyle': {'opacity': 0.08},
                'data': [
                    round(float(row['average_solar_kw']), 2)
                    for row in balance.profile
                ],
            },
            {
                'name': 'Average estimated grid requirement',
                'type': 'line',
                'smooth': True,
                'showSymbol': False,
                'lineStyle': {'width': 2, 'type': 'dashed', 'color': '#1C2545'},
                'data': [
                    round(float(row['average_estimated_grid_kw']), 2)
                    for row in balance.profile
                ],
            },
        ],
    })
    return option


def _period_name(start: date, end: date) -> str:
    return f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"


def _comparison_subject(comparison: PeriodComparison) -> str:
    area = getattr(comparison, 'area', ALL_AREAS)
    if area == SOLAR_AREA:
        return 'Solar generation'
    if area == ALL_AREAS:
        return 'Warehouse electricity used'
    return f'{area} electricity used'


def period_daily_comparison_options(comparison: PeriodComparison) -> dict:
    palette = _theme()
    option = _base(f'{_comparison_subject(comparison)}: current vs previous')
    current_name = _period_name(comparison.current_start, comparison.current_end)
    previous_name = _period_name(comparison.previous_start, comparison.previous_end)
    option.update({
        'legend': {
            'top': 38, 'left': 8, 'right': 36, 'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category',
            'data': [str(row['label']) for row in comparison.daily],
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value', 'name': 'kWh', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'grid': {'left': 58, 'right': 28, 'top': 92, 'bottom': 82, 'containLabel': True},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 18, 'bottom': 8}],
        'series': [
            {
                'name': current_name,
                'type': 'line',
                'smooth': True,
                'showSymbol': len(comparison.daily) <= 31,
                'symbolSize': 6,
                'lineStyle': {'width': 3, 'color': '#E04403'},
                'itemStyle': {'color': '#E04403'},
                'data': [
                    None if row['current_kwh'] is None else round(float(row['current_kwh']), 2)
                    for row in comparison.daily
                ],
            },
            {
                'name': previous_name,
                'type': 'line',
                'smooth': True,
                'showSymbol': len(comparison.daily) <= 31,
                'symbolSize': 5,
                'lineStyle': {'width': 2, 'type': 'dashed', 'color': '#2563EB'},
                'itemStyle': {'color': '#2563EB'},
                'data': [
                    None if row['previous_kwh'] is None else round(float(row['previous_kwh']), 2)
                    for row in comparison.daily
                ],
            },
        ],
    })
    return option


def period_area_comparison_options(comparison: PeriodComparison) -> dict:
    palette = _theme()
    has_quality_limits = any(
        str(row.get('comparison_status', 'Complete')) != 'Complete'
        for row in comparison.area_comparison
    )
    option = _base(
        ('Recorded comparable use by area' if has_quality_limits else 'Electricity used by warehouse area')
        if getattr(comparison, 'area', ALL_AREAS) == ALL_AREAS
        else f'{_comparison_subject(comparison)}: period totals'
    )
    rows = sorted(
        comparison.area_comparison,
        key=lambda row: max(float(row['current_kwh']), float(row['previous_kwh'])),
        reverse=True,
    )
    option.update({
        'legend': {
            'top': 38, 'left': 8, 'right': 36,
            'textStyle': {'color': palette['text']},
        },
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 66, 'right': 28, 'top': 92, 'bottom': 70, 'containLabel': True},
        'xAxis': {
            'type': 'category',
            'data': [str(row['area']) for row in rows],
            'axisLabel': {'color': palette['muted'], 'interval': 0, 'rotate': 18},
        },
        'yAxis': {
            'type': 'value', 'name': 'kWh', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': 'Current period', 'type': 'bar', 'barMaxWidth': 26,
                'itemStyle': {'color': '#E04403'},
                'data': [round(float(row['current_kwh']), 2) for row in rows],
            },
            {
                'name': 'Previous period', 'type': 'bar', 'barMaxWidth': 26,
                'itemStyle': {'color': '#2563EB'},
                'data': [round(float(row['previous_kwh']), 2) for row in rows],
            },
        ],
    })
    return option


def period_area_change_options(comparison: PeriodComparison) -> dict:
    palette = _theme()
    has_quality_limits = any(
        str(row.get('comparison_status', 'Complete')) != 'Complete'
        for row in comparison.area_comparison
    )
    option = _base(
        ('Recorded change in electricity used by area' if has_quality_limits else 'Absolute change in electricity used by area')
        if getattr(comparison, 'area', ALL_AREAS) == ALL_AREAS
        else f'{_comparison_subject(comparison)}: absolute change'
    )
    rows = sorted(
        comparison.area_comparison,
        key=lambda row: float(row['difference_kwh']),
    )
    data = []
    quality_markers: dict[str, list[dict[str, object]]] = {
        'Incomplete days': [],
        'Estimated readings': [],
        'Low volume': [],
    }
    marker_colors = {
        'Incomplete days': '#94A3B8',
        'Estimated readings': '#D97706',
        'Low volume': '#64748B',
    }
    maximum_change = max(
        (abs(float(row['difference_kwh'])) for row in rows),
        default=0.0,
    )
    for row in rows:
        difference = float(row['difference_kwh'])
        status = str(row.get('comparison_status', 'Complete'))
        change = row.get('change_percent')
        color = '#E04403' if difference > 0 else '#007D6D' if difference < 0 else '#2563EB'
        if status == 'Incomplete data':
            label = f'{difference:+,.1f} kWh recorded'
            marker_name = 'Incomplete days'
            marker_detail = f"{int(row.get('excluded_day_pairs', 0))} day pair(s) unavailable"
        elif status == 'Estimated data':
            label = f'{difference:+,.1f} kWh recorded'
            marker_name = 'Estimated readings'
            marker_detail = f"{int(row.get('estimated_day_pairs', 0))} day pair(s) include estimates"
        elif status == 'Low volume':
            label = f'{difference:+,.1f} kWh recorded'
            marker_name = 'Low volume'
            marker_detail = 'Recorded total is too small for a reliable percentage'
        else:
            label = (
                f'{difference:+,.0f} kWh ({float(change):+.1f}%)'
                if isinstance(change, float) else f'{difference:+,.0f} kWh'
            )
            marker_name = ''
            marker_detail = ''
        label_inside_negative = (
            difference < 0
            and maximum_change > 0
            and abs(difference) >= maximum_change * 0.18
        )
        data.append({
            'value': round(difference, 1),
            'itemStyle': {'color': color},
            'label': {
                'show': True,
                'position': (
                    'insideLeft' if label_inside_negative
                    else 'right' if difference >= 0
                    else 'left'
                ),
                'distance': 12,
                'formatter': label,
                'color': '#FFFFFF' if label_inside_negative else palette['text'],
            },
        })
        if marker_name:
            quality_markers[marker_name].append({
                'value': [round(difference, 1), str(row['area'])],
                'itemStyle': {'color': marker_colors[marker_name]},
                'tooltip': {
                    'formatter': f"{row['area']}<br>{label}<br>{marker_detail}",
                },
            })
    marker_series = [
        {
            'name': name,
            'type': 'scatter',
            'symbol': 'rect',
            'symbolSize': [8, 26],
            'z': 5,
            'itemStyle': {'color': marker_colors[name]},
            'data': marker_data,
            'tooltip': {'trigger': 'item'},
        }
        for name, marker_data in quality_markers.items()
        if marker_data
    ]
    option.update({
        'legend': {
            'top': 36, 'left': 8, 'right': 36,
            'data': [series['name'] for series in marker_series],
            'textStyle': {'color': palette['text']},
        } if marker_series else None,
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 116, 'right': 174, 'top': 86 if marker_series else 68, 'bottom': 48, 'containLabel': True},
        'xAxis': {
            'type': 'value', 'name': 'kWh change', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'yAxis': {
            'type': 'category',
            'data': [str(row['area']) for row in rows],
            'axisLabel': {'color': palette['muted']},
        },
        'series': [{
            'name': 'Change', 'type': 'bar', 'barMaxWidth': 28,
            'data': data,
        }, *marker_series],
    })
    if option['legend'] is None:
        option.pop('legend')
    return option


def _compact_comparison_rows(
    rows: list[dict[str, object]],
    current_start: date,
    previous_start: date,
) -> tuple[list[dict[str, object]], str]:
    """Aggregate long comparisons without hiding their period totals."""
    if len(rows) <= 62:
        return rows, 'Daily values'

    if len(rows) <= 180:
        compact: list[dict[str, object]] = []
        for start_index in range(0, len(rows), 7):
            group = rows[start_index:start_index + 7]
            current_values = [float(row['current_kwh']) for row in group if row['current_kwh'] is not None]
            previous_values = [float(row['previous_kwh']) for row in group if row['previous_kwh'] is not None]
            compact.append({
                'label': f'Week {start_index // 7 + 1}',
                'current_kwh': sum(current_values) if current_values else None,
                'previous_kwh': sum(previous_values) if previous_values else None,
            })
        return compact, 'Weekly totals'

    current_months: dict[int, float] = defaultdict(float)
    previous_months: dict[int, float] = defaultdict(float)
    current_seen: set[int] = set()
    previous_seen: set[int] = set()
    for row in rows:
        if row.get('current_date') and row.get('current_kwh') is not None:
            value = date.fromisoformat(str(row['current_date']))
            index = (value.year - current_start.year) * 12 + value.month - current_start.month
            current_months[index] += float(row['current_kwh'])
            current_seen.add(index)
        if row.get('previous_date') and row.get('previous_kwh') is not None:
            value = date.fromisoformat(str(row['previous_date']))
            index = (value.year - previous_start.year) * 12 + value.month - previous_start.month
            previous_months[index] += float(row['previous_kwh'])
            previous_seen.add(index)
    maximum_index = max((*current_seen, *previous_seen), default=-1)
    calendar_aligned = (
        current_start.day == 1
        and previous_start.day == 1
        and current_start.month == previous_start.month
    )
    compact = []
    for index in range(maximum_index + 1):
        month_number = current_start.month - 1 + index
        label_date = date(
            current_start.year + month_number // 12,
            month_number % 12 + 1,
            1,
        )
        compact.append({
            'label': label_date.strftime('%b') if calendar_aligned else f'Month {index + 1}',
            'current_kwh': current_months[index] if index in current_seen else None,
            'previous_kwh': previous_months[index] if index in previous_seen else None,
        })
    return compact, 'Monthly totals'


def solar_period_comparison_options(comparison: PeriodComparison) -> dict:
    palette = _theme()
    option = _base('Solar generation: current vs previous')
    current_name = _period_name(comparison.current_start, comparison.current_end)
    previous_name = _period_name(comparison.previous_start, comparison.previous_end)
    rows, resolution = _compact_comparison_rows(
        comparison.solar_daily,
        comparison.current_start,
        comparison.previous_start,
    )
    option['title']['subtext'] = resolution
    option['title']['subtextStyle'] = {'color': palette['muted'], 'fontSize': 11}
    option.update({
        'legend': {
            'top': 38, 'left': 8, 'right': 36, 'type': 'scroll',
            'textStyle': {'color': palette['text']},
        },
        'xAxis': {
            'type': 'category',
            'data': [str(row['label']) for row in rows],
            'axisLabel': {'hideOverlap': True, 'color': palette['muted'], 'margin': 14},
        },
        'yAxis': {
            'type': 'value', 'name': 'kWh', 'nameGap': 16,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': current_name, 'type': 'line', 'smooth': True,
                'showSymbol': len(rows) <= 31, 'symbolSize': 6,
                'lineStyle': {'width': 3, 'color': '#F59E0B'},
                'itemStyle': {'color': '#F59E0B'},
                'areaStyle': {'opacity': 0.08},
                'data': [
                    None if row['current_kwh'] is None else round(float(row['current_kwh']), 2)
                    for row in rows
                ],
            },
            {
                'name': previous_name, 'type': 'line', 'smooth': True,
                'showSymbol': len(rows) <= 31, 'symbolSize': 5,
                'lineStyle': {'width': 2, 'type': 'dashed', 'color': '#64748B'},
                'itemStyle': {'color': '#64748B'},
                'data': [
                    None if row['previous_kwh'] is None else round(float(row['previous_kwh']), 2)
                    for row in rows
                ],
            },
        ],
    })
    return option


def unusual_timeline_options(view: DashboardView) -> dict:
    palette = _theme()
    option = _base('Unusual readings over time')
    span = (view.end_date - view.start_date).days + 1
    grouped: dict[date, Counter[str]] = defaultdict(Counter)
    if span <= 45:
        resolution = 'Daily incidents'
        bucket = lambda value: value
        formatter = lambda value: value.strftime('%d %b')
    elif span <= 180:
        resolution = 'Weekly incidents'
        bucket = lambda value: value - timedelta(days=value.weekday())
        formatter = lambda value: value.strftime('%d %b')
    else:
        resolution = 'Monthly incidents'
        bucket = lambda value: value.replace(day=1)
        formatter = lambda value: value.strftime('%b %Y')
    for row in _warehouse_incidents(view):
        grouped[bucket(date.fromisoformat(str(row['date'])[:10]))][str(row['alert_level'])] += 1
    dates = sorted(grouped)
    option['title']['subtext'] = resolution
    option['title']['subtextStyle'] = {'color': palette['muted'], 'fontSize': 11}
    option.update({
        'legend': {'top': 42, 'left': 8, 'textStyle': {'color': palette['text']}},
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 48, 'right': 24, 'top': 92, 'bottom': 66, 'containLabel': True},
        'xAxis': {
            'type': 'category', 'data': [formatter(value) for value in dates],
            'axisLabel': {'color': palette['muted'], 'hideOverlap': True, 'rotate': 18 if len(dates) > 12 else 0},
        },
        'yAxis': {
            'type': 'value', 'name': 'Incidents', 'minInterval': 1,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': label, 'type': 'bar', 'stack': 'incidents', 'barMaxWidth': 28,
                'itemStyle': {'color': color},
                'data': [grouped[value][level] for value in dates],
            }
            for level, label, color in [
                ('Watch', 'Monitor', '#F59E0B'),
                ('High', 'Review', '#EA580C'),
                ('Critical', 'Urgent review', '#DC2626'),
            ]
        ],
    })
    return option


def unusual_by_area_options(view: DashboardView) -> dict:
    palette = _theme()
    option = _base('Signals requiring review by area')
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for row in _warehouse_incidents(view):
        alert_type = str(row.get('alert_type', '')).lower()
        if 'consumption' in alert_type:
            grouped[str(row['area'])]['Electricity use'] += 1
        if 'demand' in alert_type:
            grouped[str(row['area'])]['Highest demand'] += 1
    areas = sorted(grouped, key=lambda area: sum(grouped[area].values()))
    option.update({
        'legend': {'top': 38, 'left': 8, 'textStyle': {'color': palette['text']}},
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 96, 'right': 28, 'top': 86, 'bottom': 46, 'containLabel': True},
        'xAxis': {
            'type': 'value', 'name': 'Signals', 'minInterval': 1,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'yAxis': {
            'type': 'category', 'data': areas,
            'axisLabel': {'color': palette['muted']},
        },
        'series': [
            {
                'name': label, 'type': 'bar', 'stack': 'signals', 'barMaxWidth': 24,
                'itemStyle': {'color': color},
                'label': {'show': True, 'position': 'inside', 'formatter': '{c}', 'color': '#FFFFFF'},
                'data': [grouped[area][label] for area in areas],
            }
            for label, color in [
                ('Electricity use', '#2563EB'),
                ('Highest demand', '#E04403'),
            ]
        ],
    })
    return option


def _incident_peak_datetime(row: dict[str, str]) -> datetime | None:
    alert_type = str(row.get('alert_type', '')).lower()
    fields: list[str] = []
    if 'demand' in alert_type:
        fields.append('peak_kva_time')
    if 'consumption' in alert_type:
        fields.append('peak_kw_time')
    fields.extend(['peak_kva_time', 'peak_kw_time'])
    for field in fields:
        value = str(row.get(field, '') or '').strip()
        if not value:
            continue
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            continue
    return None


def _warehouse_incidents(view: DashboardView) -> list[dict[str, str]]:
    return [
        row for row in view.spikes
        if str(row.get('area', '')) != SOLAR_AREA
    ]


def _incident_frequency_counts(
    view: DashboardView,
) -> tuple[list[str], list[Counter[str]], list[str], list[Counter[str]]]:
    time_labels = [
        f'{hour:02d}-{hour + 2:02d}'
        for hour in range(0, 24, 2)
    ]
    month_labels = ['1-7', '8-14', '15-21', '22-28', '29-31']
    time_counts = [Counter() for _ in time_labels]
    month_counts = [Counter() for _ in month_labels]
    for row in _warehouse_incidents(view):
        level = str(row.get('alert_level', 'Watch'))
        timestamp = _incident_peak_datetime(row)
        if timestamp is not None:
            time_counts[timestamp.hour // 2][level] += 1
        try:
            day_number = date.fromisoformat(str(row['date'])[:10]).day
        except (KeyError, ValueError):
            continue
        month_index = min((day_number - 1) // 7, 4)
        month_counts[month_index][level] += 1
    return time_labels, time_counts, month_labels, month_counts


def _incident_frequency_options(
    title: str,
    subtitle: str,
    labels: list[str],
    counts: list[Counter[str]],
    *,
    rotate_labels: bool,
) -> dict:
    palette = _theme()
    option = _base(title)
    totals = [sum(bucket.values()) for bucket in counts]
    option['title']['subtext'] = subtitle
    option['title']['subtextStyle'] = {'color': palette['muted'], 'fontSize': 11}
    severity_definitions = [
        ('Watch', 'Monitor', '#F59E0B'),
        ('High', 'Review', '#EA580C'),
        ('Critical', 'Urgent review', '#DC2626'),
    ]
    severity_series = []
    for level, label, color in severity_definitions:
        data = []
        for bucket, total in zip(counts, totals):
            top_level = next(
                (candidate for candidate, _label, _color in reversed(severity_definitions) if bucket[candidate]),
                '',
            )
            data.append({
                'value': bucket[level],
                'label': {
                    'show': total > 0 and level == top_level,
                    'position': 'top',
                    'formatter': str(total),
                    'color': palette['text'],
                    'fontWeight': 700,
                },
            })
        severity_series.append({
            'name': label,
            'type': 'bar',
            'stack': 'incidents',
            'barMaxWidth': 30,
            'itemStyle': {'color': color},
            'data': data,
        })
    option.update({
        'legend': {
            'top': 48,
            'left': 8,
            'data': ['Monitor', 'Review', 'Urgent review'],
            'textStyle': {'color': palette['text']},
        },
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 48, 'right': 24, 'top': 102, 'bottom': 76, 'containLabel': True},
        'xAxis': {
            'type': 'category',
            'data': labels,
            'axisLabel': {
                'color': palette['muted'],
                'interval': 0,
                'rotate': 32 if rotate_labels else 0,
            },
        },
        'yAxis': {
            'type': 'value',
            'name': 'Incidents',
            'minInterval': 1,
            'axisLabel': {'color': palette['muted']},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': severity_series,
    })
    return option


def unusual_time_of_day_options(view: DashboardView) -> dict:
    time_labels, time_counts, _month_labels, _month_counts = _incident_frequency_counts(view)
    return _incident_frequency_options(
        'When incidents occur during the day',
        'Each meter-day incident is placed at its recorded peak time',
        time_labels,
        time_counts,
        rotate_labels=True,
    )


def unusual_month_position_options(view: DashboardView) -> dict:
    _time_labels, _time_counts, month_labels, month_counts = _incident_frequency_counts(view)
    return _incident_frequency_options(
        'Where incidents occur in the month',
        'Frequency by calendar-day range across the active dates',
        month_labels,
        month_counts,
        rotate_labels=False,
    )


def unusual_frequency_summary(view: DashboardView) -> dict[str, object]:
    time_labels, time_counts, month_labels, month_counts = _incident_frequency_counts(view)
    time_totals = [sum(bucket.values()) for bucket in time_counts]
    month_totals = [sum(bucket.values()) for bucket in month_counts]
    peak_time_index = max(range(len(time_totals)), key=time_totals.__getitem__, default=0)
    peak_month_index = max(range(len(month_totals)), key=month_totals.__getitem__, default=0)
    return {
        'time_label': f'{peak_time_index * 2:02d}:00-{peak_time_index * 2 + 1:02d}:59',
        'time_count': time_totals[peak_time_index],
        'month_label': f'Days {month_labels[peak_month_index]}',
        'month_count': month_totals[peak_month_index],
        'timed_incidents': sum(time_totals),
        'total_incidents': len(_warehouse_incidents(view)),
    }


def solar_signal_options(view: DashboardView) -> dict:
    """Show low-output warnings separately from positive higher-output events."""
    palette = _theme()
    low_rows = [
        row for row in view.spikes
        if str(row.get('area', '')) == SOLAR_AREA
    ]
    positive_rows = list(getattr(view, 'solar_positive_events', []))
    span = (view.end_date - view.start_date).days + 1
    option = _base('Solar generation outside its recent range')
    if span > 62:
        grouped: dict[date, Counter[str]] = defaultdict(Counter)
        for row in low_rows:
            event_date = date.fromisoformat(str(row['date'])[:10]).replace(day=1)
            grouped[event_date]['Low generation warning'] += 1
        for row in positive_rows:
            event_date = date.fromisoformat(str(row['date'])[:10]).replace(day=1)
            grouped[event_date]['Higher generation'] += 1
        dates = sorted(grouped)
        option['title']['subtext'] = 'Monthly count - higher output is positive; low output requires review'
        option['title']['subtextStyle'] = {'color': palette['muted'], 'fontSize': 11}
        option.update({
            'legend': {'top': 44, 'left': 8, 'textStyle': {'color': palette['text']}},
            'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
            'grid': {'left': 48, 'right': 24, 'top': 94, 'bottom': 68, 'containLabel': True},
            'xAxis': {
                'type': 'category',
                'data': [value.strftime('%b %Y') for value in dates],
                'axisLabel': {'color': palette['muted'], 'rotate': 18 if len(dates) > 12 else 0},
            },
            'yAxis': {
                'type': 'value', 'name': 'Days', 'minInterval': 1,
                'axisLabel': {'color': palette['muted']},
                'nameTextStyle': {'color': palette['muted']},
                'splitLine': {'lineStyle': {'color': palette['grid']}},
            },
            'series': [
                {
                    'name': label,
                    'type': 'bar',
                    'barMaxWidth': 28,
                    'itemStyle': {'color': color},
                    'label': {'show': True, 'position': 'top', 'color': palette['text']},
                    'data': [grouped[value][label] for value in dates],
                }
                for label, color in [
                    ('Higher generation', '#008575'),
                    ('Low generation warning', '#DC2626'),
                ]
            ],
        })
        return option

    rows: list[dict[str, object]] = []
    for row in positive_rows:
        rows.append({
            'date': str(row['date'])[:10],
            'value': float(row.get('consumption_variance_percent', 0) or 0),
            'kind': 'Higher generation',
        })
    for row in low_rows:
        rows.append({
            'date': str(row['date'])[:10],
            'value': float(row.get('consumption_variance_percent', 0) or 0),
            'kind': 'Low generation warning',
        })
    rows.sort(key=lambda row: str(row['date']))
    values = [float(row['value']) for row in rows]
    axis_min = floor((min(values, default=0.0) - 10.0) / 10.0) * 10.0
    axis_max = ceil((max(values, default=0.0) + 10.0) / 10.0) * 10.0
    option['title']['subtext'] = 'Difference from the recent same-weekday average'
    option['title']['subtextStyle'] = {'color': palette['muted'], 'fontSize': 11}
    option.update({
        'tooltip': {
            'trigger': 'axis',
            'formatter': '{b}<br/>{c}% versus usual generation',
        },
        'grid': {'left': 54, 'right': 30, 'top': 78, 'bottom': 72, 'containLabel': True},
        'xAxis': {
            'type': 'category',
            'data': [date.fromisoformat(str(row['date'])).strftime('%d %b') for row in rows],
            'axisLabel': {'color': palette['muted'], 'rotate': 24 if len(rows) > 10 else 0},
        },
        'yAxis': {
            'type': 'value', 'name': '% vs usual',
            'min': axis_min,
            'max': axis_max,
            'axisLabel': {'color': palette['muted'], 'formatter': '{value}%'},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [{
            'name': 'Solar generation difference',
            'type': 'bar',
            'barMaxWidth': 30,
            'data': [
                {
                    'value': round(float(row['value']), 1),
                    'itemStyle': {
                        'color': '#008575' if row['kind'] == 'Higher generation' else '#DC2626'
                    },
                    'label': {
                        'show': True,
                        'position': 'top' if float(row['value']) >= 0 else 'bottom',
                        'formatter': f"{float(row['value']):+.1f}%",
                        'color': palette['text'],
                    },
                }
                for row in rows
            ],
        }],
    })
    return option


def alert_mix_options(view: DashboardView) -> dict:
    palette = _theme()
    counts = defaultdict(int)
    for row in view.daily:
        if row['alert_level'] in {'Watch', 'High', 'Critical'}:
            counts[row['alert_level']] += 1
    return {
        'backgroundColor': 'transparent',
        'textStyle': {'color': palette['text']},
        'title': {
            'text': 'Unusual meter-day readings',
            'left': 8,
            'top': 2,
            'textStyle': {'fontSize': 15, 'fontWeight': 700, 'color': palette['text']},
        },
        'tooltip': {'trigger': 'item'},
        'series': [{
            'type': 'pie',
            'radius': ['38%', '64%'],
            'center': ['50%', '55%'],
            'top': 42,
            'bottom': 18,
            'avoidLabelOverlap': True,
            'label': {'formatter': '{b}: {c}', 'fontSize': 11},
            'labelLine': {'length': 10, 'length2': 8},
            'labelLayout': {'hideOverlap': True},
            'data': [
                {'name': 'Urgent review', 'value': counts['Critical'], 'itemStyle': {'color': '#DC2626'}},
                {'name': 'Review', 'value': counts['High'], 'itemStyle': {'color': '#EA580C'}},
                {'name': 'Monitor', 'value': counts['Watch'], 'itemStyle': {'color': '#F59E0B'}},
            ],
        }],
    }


def cost_trend_options(analysis: CostAnalysis) -> dict:
    """Show cost before and after solar savings, with the saving kept visible."""
    palette = _theme()
    monthly = (analysis.end_date - analysis.start_date).days + 1 > 92
    if monthly:
        grouped: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in analysis.daily:
            key = date.fromisoformat(str(row['date'])[:10]).strftime('%b %Y')
            grouped[key]['total_cost'] += float(row['total_cost'])
            grouped[key]['estimated_total_cost'] += float(row['estimated_total_cost'])
            grouped[key]['solar_avoided_cost'] += float(row['solar_avoided_cost'])
        labels = list(grouped)
        before_savings = [round(grouped[label]['total_cost'], 2) for label in labels]
        after_savings = [round(grouped[label]['estimated_total_cost'], 2) for label in labels]
        solar = [round(grouped[label]['solar_avoided_cost'], 2) for label in labels]
        title = 'Cost before and after solar savings by month'
    else:
        labels = [str(row['date']) for row in analysis.daily]
        before_savings = [round(float(row['total_cost']), 2) for row in analysis.daily]
        after_savings = [round(float(row['estimated_total_cost']), 2) for row in analysis.daily]
        solar = [round(float(row['solar_avoided_cost']), 2) for row in analysis.daily]
        title = 'Cost before and after solar savings by day'

    option = _base(title)
    show_labels = len(labels) <= 10
    def labelled(values: list[float]) -> list[dict[str, object]]:
        return [
            {'value': value, 'label': {'formatter': f'R {value:,.0f}'}}
            for value in values
        ]
    option.update({
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'legend': {
            'top': 42, 'left': 8,
            'textStyle': {'color': palette['text']},
        },
        'grid': {'left': 70, 'right': 28, 'top': 98, 'bottom': 76, 'containLabel': True},
        'xAxis': {
            'type': 'category', 'data': labels,
            'axisLabel': {
                'color': palette['muted'], 'hideOverlap': True,
                'rotate': 20 if len(labels) > 12 else 0,
            },
        },
        'yAxis': {
            'type': 'value', 'name': 'Rand (VAT incl.)',
            'axisLabel': {'color': palette['muted'], 'formatter': 'R {value}'},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': 'Cost before solar savings', 'type': 'bar',
                'barMaxWidth': 28, 'itemStyle': {'color': '#E04403'},
                'label': {
                    'show': show_labels, 'position': 'top', 'color': palette['text'],
                    'fontSize': 10, 'hideOverlap': True,
                },
                'data': labelled(before_savings),
            },
            {
                'name': 'Cost after solar savings', 'type': 'bar',
                'barMaxWidth': 28, 'itemStyle': {'color': '#2563EB'},
                'label': {
                    'show': show_labels, 'position': 'top', 'color': palette['text'],
                    'fontSize': 10,
                    'hideOverlap': True,
                },
                'data': labelled(after_savings),
            },
            {
                'name': 'Solar savings', 'type': 'bar',
                'barMaxWidth': 22, 'itemStyle': {'color': '#008575'},
                'label': {
                    'show': show_labels, 'position': 'top', 'color': palette['text'],
                    'fontSize': 10,
                    'hideOverlap': True,
                },
                'data': labelled(solar),
            },
        ],
    })
    if len(labels) > 18:
        option['dataZoom'] = [
            {'type': 'inside'},
            {'type': 'slider', 'height': 18, 'bottom': 8},
        ]
    return option


def cost_area_comparison_options(analysis: CostAnalysis) -> dict:
    palette = _theme()
    labels = [str(row['area']) for row in analysis.area_rows]

    def compact_rand(value: float) -> str:
        if abs(value) >= 1_000_000:
            return f'R {value / 1_000_000:.1f}m'
        if abs(value) >= 1_000:
            return f'R {value / 1_000:.1f}k'
        return f'R {value:,.0f}'

    current = [
        {
            'value': round(float(row['current_total_cost']), 2),
            'label': {'formatter': compact_rand(float(row['current_total_cost']))},
        }
        for row in analysis.area_rows
    ]
    previous = [
        {
            'value': round(float(row['comparison_total_cost']), 2),
            'label': {'formatter': compact_rand(float(row['comparison_total_cost']))},
        }
        for row in analysis.area_rows
    ]
    option = _base('Estimated cost after solar savings by area')
    option.update({
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'legend': {
            'top': 42, 'left': 8,
            'textStyle': {'color': palette['text']},
        },
        'grid': {'left': 54, 'right': 92, 'top': 92, 'bottom': 42, 'containLabel': True},
        'xAxis': {
            'type': 'value', 'name': 'Rand (VAT incl.)',
            'axisLabel': {'color': palette['muted'], 'formatter': 'R {value}'},
            'nameTextStyle': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'yAxis': {
            'type': 'category', 'data': labels,
            'axisLabel': {'color': palette['muted']},
        },
        'series': [
            {
                'name': 'Selected period', 'type': 'bar', 'barMaxWidth': 24,
                'itemStyle': {'color': '#E04403'},
                'label': {
                    'show': True, 'position': 'right', 'color': palette['text'],
                },
                'data': current,
            },
            {
                'name': 'Comparison period', 'type': 'bar', 'barMaxWidth': 24,
                'itemStyle': {'color': '#2563EB'},
                'label': {
                    'show': True, 'position': 'right', 'color': palette['text'],
                },
                'data': previous,
            },
        ],
    })
    return option


def cost_component_options(analysis: CostAnalysis) -> dict:
    """VAT-inclusive cost components for the selected period."""
    palette = _theme()
    labels = ['Energy', 'Demand', 'Service', 'Network surcharge']
    values = [
        float(analysis.metrics.get('energy_cost') or 0.0),
        float(analysis.metrics.get('demand_cost') or 0.0),
        float(analysis.metrics.get('service_cost') or 0.0),
        float(analysis.metrics.get('network_surcharge') or 0.0),
    ]
    option = _base('What makes up the cost before solar savings')
    option.update({
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 62, 'right': 80, 'top': 64, 'bottom': 50, 'containLabel': True},
        'xAxis': {
            'type': 'value',
            'name': 'Rand (VAT incl.)',
            'axisLabel': {'color': palette['muted'], 'formatter': 'R {value}'},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'yAxis': {
            'type': 'category', 'data': labels,
            'axisLabel': {'color': palette['muted']},
        },
        'series': [{
            'name': 'Selected period', 'type': 'bar', 'barMaxWidth': 32,
            'itemStyle': {
                'color': '#E04403', 'borderRadius': [0, 5, 5, 0],
            },
            'label': {
                'show': True, 'position': 'right', 'color': palette['text'],
                'formatter': '{@[0]}',
            },
            'data': [
                {
                    'value': round(value, 2),
                    'label': {'formatter': f'R {value:,.0f}'},
                }
                for value in values
            ],
        }],
    })
    return option


def tou_energy_mix_options(analysis: CostAnalysis) -> dict:
    palette = _theme()
    labels = ['Peak', 'Standard', 'Off-peak', 'Scale 1 flat rate']
    values = [
        float(analysis.metrics.get('peak_kwh') or 0.0),
        float(analysis.metrics.get('standard_kwh') or 0.0),
        float(analysis.metrics.get('off_peak_kwh') or 0.0),
        float(analysis.metrics.get('flat_kwh') or 0.0),
    ]
    option = _base('Electricity used by tariff time band')
    option.update({
        'tooltip': {'trigger': 'item'},
        'legend': {
            'top': 42, 'left': 8,
            'textStyle': {'color': palette['text']},
        },
        'series': [{
            'type': 'pie',
            'radius': ['36%', '65%'],
            'center': ['50%', '58%'],
            'top': 44,
            'label': {
                'show': True,
                'formatter': '{b}\n{c} kWh ({d}%)',
                'color': palette['text'],
                'fontSize': 11,
            },
            'labelLayout': {'hideOverlap': True},
            'data': [
                {'name': label, 'value': round(value, 1), 'itemStyle': {'color': color}}
                for label, value, color in zip(
                    labels, values, ['#DC2626', '#F59E0B', '#008575', '#2563EB']
                )
                if value > 0
            ],
        }],
    })
    return option


def solar_proposal_energy_flow_options(proposal: SolarProposal) -> dict:
    palette = _theme()
    option = _base(f'{proposal.label}: forecast annual solar energy flow')
    values = [
        proposal.annual_grid_reduction_kwh,
        proposal.annual_export_kwh,
        proposal.annual_losses_kwh,
    ]
    labels = ['Reduces grid purchases', 'Forecast export', 'System/storage losses']
    option.update({
        'tooltip': {'trigger': 'item'},
        'legend': {
            'top': 42, 'left': 8,
            'textStyle': {'color': palette['text']},
        },
        'series': [{
            'type': 'pie', 'radius': ['36%', '65%'], 'center': ['50%', '58%'],
            'top': 44,
            'label': {
                'formatter': '{b}\n{c} kWh ({d}%)',
                'color': palette['text'], 'fontSize': 11,
            },
            'labelLayout': {'hideOverlap': True},
            'data': [
                {'name': label, 'value': value, 'itemStyle': {'color': color}}
                for label, value, color in zip(
                    labels, values, ['#008575', '#F59E0B', '#94A3B8']
                )
            ],
        }],
    })
    return option


def solar_proposal_cash_flow_options(proposal: SolarProposal) -> dict:
    palette = _theme()
    rows = proposal.cumulative_cash_flow()
    option = _base(f'{proposal.label}: cumulative cash flow after net investment')
    option.update({
        'tooltip': {'trigger': 'axis'},
        'grid': {'left': 74, 'right': 32, 'top': 68, 'bottom': 54, 'containLabel': True},
        'xAxis': {
            'type': 'category',
            'data': [f"Year {row['year']}" for row in rows],
            'axisLabel': {'color': palette['muted'], 'hideOverlap': True},
        },
        'yAxis': {
            'type': 'value', 'name': 'Rand',
            'axisLabel': {'color': palette['muted'], 'formatter': 'R {value}'},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [{
            'name': 'Cumulative cash flow', 'type': 'line', 'smooth': True,
            'symbolSize': 7,
            'lineStyle': {'width': 3, 'color': '#2563EB'},
            'itemStyle': {'color': '#2563EB'},
            'markLine': {
                'silent': True,
                'symbol': 'none',
                'lineStyle': {'color': '#64748B', 'type': 'dashed'},
                'data': [{'yAxis': 0, 'label': {'formatter': 'Break-even'}}],
            },
            'data': [round(float(row['cumulative_cash_flow']), 2) for row in rows],
        }],
    })
    return option


def solar_actual_vs_proposal_options(
    actual_annualized_generation_kwh: float,
    actual_annualized_grid_reduction_kwh: float,
    proposals: tuple[SolarProposal, ...],
) -> dict:
    palette = _theme()
    labels = ['Current annualised', *(proposal.label for proposal in proposals)]
    generation = [
        round(actual_annualized_generation_kwh, 1),
        *(proposal.annual_generation_kwh for proposal in proposals),
    ]
    grid_reduction = [
        round(actual_annualized_grid_reduction_kwh, 1),
        *(proposal.annual_grid_reduction_kwh for proposal in proposals),
    ]
    option = _base('Current run-rate compared with proposal forecasts')
    option.update({
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'legend': {'top': 42, 'left': 8, 'textStyle': {'color': palette['text']}},
        'grid': {'left': 72, 'right': 26, 'top': 92, 'bottom': 54, 'containLabel': True},
        'xAxis': {
            'type': 'category', 'data': labels,
            'axisLabel': {'color': palette['muted']},
        },
        'yAxis': {
            'type': 'value', 'name': 'kWh/year',
            'axisLabel': {'color': palette['muted']},
            'splitLine': {'lineStyle': {'color': palette['grid']}},
        },
        'series': [
            {
                'name': 'Solar generated', 'type': 'bar', 'barMaxWidth': 34,
                'itemStyle': {'color': '#F59E0B'}, 'data': generation,
            },
            {
                'name': 'Grid purchases reduced', 'type': 'bar', 'barMaxWidth': 34,
                'itemStyle': {'color': '#008575'}, 'data': grid_reduction,
            },
        ],
    })
    return option
