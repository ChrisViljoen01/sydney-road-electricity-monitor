from __future__ import annotations

from dataclasses import dataclass
from .tariffs import CostAnalysis


PROPOSAL_OPTION_1 = 'AP20260212 Connect Logistics Expanded System Option 1.pdf'
PROPOSAL_OPTION_2 = 'AP20260212 Connect Logistics Expanded System Option 2.pdf'


@dataclass(frozen=True)
class SolarProposal:
    key: str
    label: str
    pv_dc_kwp: float
    inverter_ac_kw: float
    annual_generation_kwh: float
    battery_power_kw: float
    battery_capacity_kwh: float
    annual_battery_discharge_kwh: float
    round_trip_efficiency_percent: float
    annual_grid_reduction_kwh: float
    annual_export_kwh: float
    annual_losses_kwh: float
    usage_offset_percent: float
    gross_investment: float
    tax_deduction: float
    net_investment: float
    year_one_cost_before_solar: float
    year_one_savings: float
    year_one_cost_after_solar: float
    vendor_payback_years: float
    annual_savings: tuple[float, ...]
    source_name: str

    @property
    def simple_payback_years(self) -> float:
        return self.net_investment / self.year_one_savings

    @property
    def self_use_percent(self) -> float:
        return self.annual_grid_reduction_kwh / self.annual_generation_kwh * 100.0

    def cumulative_cash_flow(self) -> list[dict[str, float | int]]:
        cumulative = -self.net_investment
        rows: list[dict[str, float | int]] = [{
            'year': 0,
            'annual_savings': 0.0,
            'cumulative_cash_flow': cumulative,
        }]
        for year, saving in enumerate(self.annual_savings, start=1):
            cumulative += saving
            rows.append({
                'year': year,
                'annual_savings': saving,
                'cumulative_cash_flow': cumulative,
            })
        return rows


SOLAR_PROPOSALS: tuple[SolarProposal, ...] = (
    SolarProposal(
        key='option_1',
        label='Option 1',
        pv_dc_kwp=196.470,
        inverter_ac_kw=172.902,
        annual_generation_kwh=250_953,
        battery_power_kw=92,
        battery_capacity_kwh=184,
        annual_battery_discharge_kwh=62_528,
        round_trip_efficiency_percent=72.3,
        annual_grid_reduction_kwh=218_973,
        annual_export_kwh=8_141,
        annual_losses_kwh=23_839,
        usage_offset_percent=24.6,
        gross_investment=1_369_580,
        tax_deduction=369_787,
        net_investment=999_793,
        year_one_cost_before_solar=2_721_737,
        year_one_savings=772_652,
        year_one_cost_after_solar=1_949_085,
        vendor_payback_years=1.3,
        annual_savings=(
            772_652, 825_195, 881_200, 940_885, 1_004_479,
            1_072_226, 1_144_385, 1_221_227, 1_303_039, 1_390_125,
            1_482_806, 1_581_419, 1_686_320, 1_797_884, 1_916_506,
        ),
        source_name=PROPOSAL_OPTION_1,
    ),
    SolarProposal(
        key='option_2',
        label='Option 2',
        pv_dc_kwp=263.070,
        inverter_ac_kw=231.512,
        annual_generation_kwh=336_021,
        battery_power_kw=122,
        battery_capacity_kwh=245.8,
        annual_battery_discharge_kwh=89_475,
        round_trip_efficiency_percent=72.3,
        annual_grid_reduction_kwh=273_288,
        annual_export_kwh=28_603,
        annual_losses_kwh=34_130,
        usage_offset_percent=32.7,
        gross_investment=2_958_008,
        tax_deduction=798_662,
        net_investment=2_159_346,
        year_one_cost_before_solar=2_721_737,
        year_one_savings=965_284,
        year_one_cost_after_solar=1_756_453,
        vendor_payback_years=2.2,
        annual_savings=(
            965_284, 1_029_839, 1_098_545, 1_171_653, 1_249_427,
            1_332_146, 1_420_105, 1_513_611, 1_612_989, 1_718_581,
            1_830_744, 1_949_854, 2_076_306, 2_210_510, 2_352_899,
        ),
        source_name=PROPOSAL_OPTION_2,
    ),
)


def current_solar_investment_metrics(analysis: CostAnalysis) -> dict[str, object]:
    """Build source-of-truth operational metrics from PNPSCADA-backed costs."""
    metrics = analysis.metrics
    days = max(int(metrics.get('current_days') or 0), 1)
    scale = 365.0 / days
    generated = float(metrics.get('solar_generated_kwh') or 0.0)
    solar_used = float(metrics.get('solar_used_kwh') or 0.0)
    possible_excess = float(metrics.get('possible_excess_solar_kwh') or 0.0)
    return {
        'days': days,
        'cost_before_solar': float(metrics.get('total_cost') or 0.0),
        'solar_savings': float(metrics.get('solar_avoided_cost') or 0.0),
        'cost_after_solar': float(metrics.get('estimated_total_cost') or 0.0),
        'solar_generated_kwh': generated,
        'solar_used_kwh': solar_used,
        'possible_excess_solar_kwh': possible_excess,
        'solar_utilization_percent': (
            solar_used / generated * 100.0 if generated else 0.0
        ),
        'annualized_generation_kwh': generated * scale,
        'annualized_grid_reduction_kwh': solar_used * scale,
        'peak_demand_kva': float(metrics.get('peak_demand_kva') or 0.0),
        'peak_demand_area': str(metrics.get('peak_demand_area') or ''),
        'peak_demand_time': str(metrics.get('peak_demand_time') or ''),
    }
