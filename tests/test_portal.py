from __future__ import annotations

import unittest

from electricity_tool.portal import parse_login_form, parse_profile_graph


class PortalTests(unittest.TestCase):
    def test_extracts_login_action_and_hidden_fields(self) -> None:
        html = """
        <form id='loginform' action='/_Login' method='POST'>
          <input type='hidden' name='context' value='gts'>
          <input type='hidden' name='memh' value='-12345'>
          <input name='lusr'>
          <input name='lpwd' type='password'>
        </form>
        """
        form = parse_login_form(html)
        self.assertEqual(form.action, "/_Login")
        self.assertEqual(form.fields["context"], "gts")
        self.assertEqual(form.fields["memh"], "-12345")

    def test_extracts_profile_graph_account_ids_and_selected_utility(self) -> None:
        html = '''
        <input id="selGNAME_UTILITY" value="10005$Electricity">
        <select id="PNPENTID">
          <option value="38232">265 Sydney Rd Connect Logistics (Solar)</option>
          <option value="38359" selected>265 Sydney Rd WH 6</option>
        </select>
        '''
        graph = parse_profile_graph(html)
        self.assertEqual(graph.account_options["265 Sydney Rd WH 6"], "38359")
        self.assertEqual(graph.selected_utility, "10005$Electricity")


if __name__ == "__main__":
    unittest.main()
