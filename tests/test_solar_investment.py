from __future__ import annotations

import unittest

from electricity_tool.solar_investment import SOLAR_PROPOSALS


class SolarInvestmentTests(unittest.TestCase):
    def test_proposal_energy_flows_reconcile_to_generation(self) -> None:
        for proposal in SOLAR_PROPOSALS:
            with self.subTest(option=proposal.label):
                self.assertAlmostEqual(
                    proposal.annual_grid_reduction_kwh
                    + proposal.annual_export_kwh
                    + proposal.annual_losses_kwh,
                    proposal.annual_generation_kwh,
                )

    def test_year_one_cost_and_savings_reconcile(self) -> None:
        for proposal in SOLAR_PROPOSALS:
            with self.subTest(option=proposal.label):
                self.assertAlmostEqual(
                    proposal.year_one_cost_before_solar - proposal.year_one_savings,
                    proposal.year_one_cost_after_solar,
                )

    def test_cumulative_cash_flow_starts_with_net_investment(self) -> None:
        for proposal in SOLAR_PROPOSALS:
            with self.subTest(option=proposal.label):
                rows = proposal.cumulative_cash_flow()
                self.assertEqual(rows[0]['year'], 0)
                self.assertEqual(
                    rows[0]['cumulative_cash_flow'], -proposal.net_investment
                )
                self.assertGreater(rows[-1]['cumulative_cash_flow'], 0)


if __name__ == '__main__':
    unittest.main()
