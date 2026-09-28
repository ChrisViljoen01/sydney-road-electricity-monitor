from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

import pytest

import electricity_tool.ai_hub as ai_hub
from electricity_tool.ai_hub import (
    AiEvidence,
    COPILOT_SCOPES,
    CopilotChatClient,
    CopilotConnectionError,
    MicrosoftCopilotAuth,
    ai_database_scope,
    build_ai_evidence,
    is_contextual_ai_follow_up,
)
from electricity_tool.dashboard_data import ALL_AREAS, DashboardDataset


def _daily(
    day: str,
    area: str,
    kwh: float,
    peak_kw: float,
    peak_kva: float,
    quality: str = 'Complete',
) -> dict[str, str]:
    return {
        'date': day,
        'area': area,
        'data_quality_status': quality,
        'import_kwh': str(kwh),
        'peak_kw': str(peak_kw),
        'peak_kw_time': f'{day} 17:30:00',
        'peak_kva': str(peak_kva),
        'peak_kva_time': f'{day} 17:30:00',
    }


def _dataset() -> DashboardDataset:
    daily = [
        _daily('2026-07-13', 'Warehouse 6', 800, 80, 82),
        _daily('2026-07-13', 'Warehouse 7', 250, 30, 32),
        _daily('2026-07-20', 'Warehouse 6', 1000, 112, 114),
        _daily('2026-07-20', 'Warehouse 7', 300, 35, 36),
        _daily('2026-07-20', 'Connect Logistics Solar', 210, 44, 45),
        _daily('2026-07-19', 'Warehouse 6', 9999, 900, 910, 'Incomplete'),
    ]
    intervals = [
        {
            'date': '2026-07-20', 'timestamp': '2026-07-20 06:00:00',
            'area': 'Warehouse 6', 'import_kwh': '20', 'kw_import': '40',
        },
        {
            'date': '2026-07-20', 'timestamp': '2026-07-20 22:00:00',
            'area': 'Warehouse 6', 'import_kwh': '10', 'kw_import': '20',
        },
        {
            'date': '2026-07-20', 'timestamp': '2026-07-20 17:30:00',
            'area': 'Warehouse 7', 'import_kwh': '5', 'kw_import': '10',
        },
    ]
    spikes = [{
        'spike_id': 'w6-2026-07-20',
        'date': '2026-07-20',
        'area': 'Warehouse 6',
        'data_quality_status': 'Complete',
        'alert_level': 'Critical',
        'alert_type': 'Demand',
        'alert_reason': 'Peak kVA is 40.0% above the matching-weekday baseline',
        'import_kwh': '1000',
        'peak_kw': '112',
        'peak_kw_time': '2026-07-20 17:30:00',
        'peak_kva': '114',
        'peak_kva_time': '2026-07-20 17:30:00',
    }]
    return DashboardDataset(
        run_dir=Path('.'),
        manifest={},
        daily=daily,
        weekly=[],
        spikes=spikes,
        intervals=intervals,
        areas=('Connect Logistics Solar', 'Warehouse 6', 'Warehouse 7'),
        first_date=date(2026, 7, 13),
        last_date=date(2026, 7, 20),
    )


def _two_month_dataset() -> DashboardDataset:
    base = _dataset()
    daily = [
        *base.daily,
        _daily('2026-08-16', 'Warehouse 6', 1100, 120, 125),
        _daily('2026-08-16', 'Warehouse 7', 275, 34, 36),
    ]
    spikes = [
        *base.spikes,
        {
            'spike_id': 'w6-2026-08-16',
            'date': '2026-08-16',
            'area': 'Warehouse 6',
            'data_quality_status': 'Complete',
            'alert_level': 'High',
            'alert_type': 'Demand',
            'alert_reason': 'Peak kVA is 31.0% above the matching-weekday baseline',
            'import_kwh': '1100',
            'peak_kw': '120',
            'peak_kw_time': '2026-08-16 18:00:00',
            'peak_kva': '125',
            'peak_kva_time': '2026-08-16 18:00:00',
        },
    ]
    return DashboardDataset(
        run_dir=base.run_dir,
        manifest=base.manifest,
        daily=daily,
        weekly=base.weekly,
        spikes=spikes,
        intervals=base.intervals,
        areas=base.areas,
        first_date=base.first_date,
        last_date=date(2026, 8, 17),
    )


@dataclass
class _CostAnalysis:
    metrics: dict[str, object]
    warnings: tuple[str, ...] = ()


@pytest.fixture(autouse=True)
def _stub_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ai_hub,
        'build_cost_analysis',
        lambda *args, **kwargs: _CostAnalysis({
            'estimated_total_cost': 5000.0,
            'total_cost': 5400.0,
            'solar_avoided_cost': 400.0,
            'energy_cost': 4200.0,
            'demand_cost': 800.0,
            'network_surcharge': 0.0,
            'coverage_percent': 100.0,
        }),
    )


def test_ai_scope_rejects_general_questions() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'Write an email about the weather',
        date(2026, 7, 13),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    assert evidence.allowed is False
    assert evidence.intent == 'out_of_scope'
    assert 'only help' in evidence.local_answer


def test_microsoft_sign_in_requests_agent_mail_and_directory_scopes() -> None:
    assert {
        'Mail.ReadWrite', 'Mail.ReadWrite.Shared', 'Mail.Send', 'Mail.Send.Shared',
        'People.Read.All',
    }.issubset(COPILOT_SCOPES)


def test_greeting_starts_a_natural_app_conversation() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'Hi',
        date(2026, 7, 13),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    assert evidence.allowed is True
    assert evidence.intent == 'conversation'
    assert evidence.local_answer.startswith('Hello.')


def test_current_week_with_one_day_asks_for_clarification() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'When was our highest usage this week and why?',
        date(2026, 7, 13),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    assert evidence.allowed is True
    assert evidence.intent == 'highest_usage'
    assert evidence.start_date == date(2026, 7, 20)
    assert evidence.end_date == date(2026, 7, 20)
    assert 'partial current week' in evidence.clarification
    assert 'last complete Monday-to-Sunday week' in evidence.clarification


def test_highest_usage_uses_complete_rows_for_partial_week_when_requested() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'When was our highest usage this week so far and why?',
        date(2026, 7, 13),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    assert evidence.allowed is True
    assert evidence.clarification == ''
    assert evidence.facts['total_electricity_used_kwh'] == 1300.0
    assert evidence.facts['highest_daily_use'] == {
        'area': 'Warehouse 6', 'date': '2026-07-20', 'kwh': 1000.0,
    }
    assert '9999' not in evidence.local_answer
    assert 'operational records are required to confirm' in evidence.local_answer


def test_short_follow_up_inherits_previous_electricity_intent_and_active_filter() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'Try again with this data filter applied',
        date(2026, 7, 13),
        date(2026, 7, 20),
        ALL_AREAS,
        prior_question='Which warehouse used the most electricity?',
    )
    assert evidence.allowed is True
    assert evidence.intent == 'highest_usage'
    assert evidence.start_date == date(2026, 7, 13)
    assert evidence.end_date == date(2026, 7, 20)
    assert evidence.facts['conversation_context']['previous_user_question'].startswith(
        'Which warehouse'
    )


def test_open_management_question_is_allowed_to_reason_over_active_data() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'What concerns you most and what should we do first?',
        date(2026, 7, 13),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    assert evidence.allowed is True
    assert evidence.intent == 'highest_usage'
    assert evidence.facts['area_totals'][0]['area'] == 'Warehouse 6'


def test_solar_weather_question_is_treated_as_a_hypothesis_not_rejected() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'Could weather explain the lower solar output?',
        date(2026, 7, 20),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    assert evidence.allowed is True
    assert evidence.intent == 'solar'


def test_explicit_date_in_question_overrides_active_filter() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'What happened to Warehouse 6 on 20/07/2026?',
        date(2026, 7, 13),
        date(2026, 7, 13),
        ALL_AREAS,
    )
    assert evidence.start_date == date(2026, 7, 20)
    assert evidence.end_date == date(2026, 7, 20)
    assert evidence.area == 'Warehouse 6'


def test_ai_database_scope_ignores_dashboard_dates_and_uses_all_areas() -> None:
    assert ai_database_scope(_two_month_dataset()) == (
        date(2026, 7, 13),
        date(2026, 8, 16),
        ALL_AREAS,
    )
    assert is_contextual_ai_follow_up('Why was that?') is True
    assert is_contextual_ai_follow_up(
        'Show the July and August 2026 demand exceedances'
    ) is False


def test_named_months_include_each_months_alert_and_threshold_evidence() -> None:
    dataset = _two_month_dataset()
    evidence = build_ai_evidence(
        dataset,
        'Show the July and August 2026 demand exceedances',
        *ai_database_scope(dataset),
    )
    assert evidence.intent == 'unusual'
    assert evidence.start_date == date(2026, 7, 13)
    assert evidence.end_date == date(2026, 8, 16)
    assert {
        row['month'] for row in evidence.facts['unusual_readings_by_month']
    } == {'2026-07', '2026-08'}
    exceedance_months = {
        row['month']
        for row in evidence.facts['demand_threshold_exceedances_by_month']
    }
    assert exceedance_months == {'2026-07', '2026-08'}
    assert {
        row['date'] for row in evidence.facts['demand_threshold_exceedances']
    } == {'2026-07-20', '2026-08-16'}


def test_tariff_question_calculates_time_of_use_mix() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'How should we restructure usage based on peak and off-peak hours?',
        date(2026, 7, 20),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    mix = evidence.facts['ctou_energy_mix']
    assert evidence.intent == 'tariff'
    # PNPSCADA timestamps are interval ends: 06:00 is assigned to 05:30-06:00.
    assert mix['peak']['kwh'] == 5.0
    assert mix['standard']['kwh'] == 10.0
    assert mix['off_peak']['kwh'] == 20.0
    tariff = evidence.facts['tariff_reference']
    assert tariff['area_assignments']['Warehouse 6'] == 'CTOU'
    assert tariff['area_assignments']['Warehouse 9'] == 'Scale 1'
    high_weekday = tariff['ctou_clock_schedule']['high_season']['weekday']
    assert {'from': '06:00', 'to': '08:00', 'band': 'peak'} in high_weekday
    assert {'from': '22:00', 'to': '24:00', 'band': 'off_peak'} in high_weekday
    assert evidence.facts['half_hour_load_profile']
    assert evidence.facts['half_hour_load_profile'][0]['interval_start'] == '05:30'
    assert evidence.facts[
        'theoretical_saving_if_10_percent_peak_kwh_moves_to_off_peak_rand'
    ] > 0
    assert 'Operational feasibility has not been assumed' in evidence.local_answer


def test_ai_catalog_covers_every_sidebar_page_and_underlying_data() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'What data can you use from every sidebar tab?',
        date(2026, 7, 13),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    catalog = evidence.facts['app_page_catalog']
    assert set(catalog) == {
        'Overview', 'Usage Trends', 'Supply & Demand', 'Solar Performance',
        'Solar Investment', 'Comparisons', 'Cost Centre', 'Unusual Usage',
        'AI Hub', 'Agent Centre', 'Reports Center', 'Data Update',
    }
    assert 'half_hour_readings' in evidence.facts['data_dictionary']
    assert evidence.facts['dataset_inventory']['available_from'] == '2026-07-13'


def test_whole_app_request_includes_cost_and_cross_page_evidence() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'Analyse everything in the app and tell me what matters most.',
        date(2026, 7, 20),
        date(2026, 7, 20),
        ALL_AREAS,
    )
    assert evidence.intent == 'overview'
    assert evidence.facts['cost']['estimated_cost_after_solar_savings_rand'] == 5000.0
    assert evidence.facts['supply_demand']['metrics']
    assert len(evidence.facts['solar_investment']['proposal_options']) == 2


def test_supply_and_demand_question_receives_page_specific_evidence() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'Use the Supply & Demand tab to explain our grid need.',
        date(2026, 7, 20),
        date(2026, 7, 20),
        'Warehouse 6',
    )
    assert evidence.intent == 'supply'
    assert evidence.area == ALL_AREAS
    assert 'metrics' in evidence.facts['supply_demand']
    assert 'typical_time_profile' in evidence.facts['supply_demand']
    assert evidence.facts['supply_demand']['strongest_recorded_solar_output'] == {
        'area': 'Connect Logistics Solar',
        'timestamp': '2026-07-20 17:30:00',
        'value': 44.0,
        'unit': 'kW',
    }
    assert 'Estimated remaining grid need' in evidence.local_answer
    assert 'Strongest recorded solar output' in evidence.local_answer


def test_solar_investment_uses_site_data_and_both_proposals() -> None:
    evidence = build_ai_evidence(
        _dataset(),
        'Compare the Solar Investment proposal options and payback.',
        date(2026, 7, 20),
        date(2026, 7, 20),
        'Warehouse 6',
    )
    investment = evidence.facts['solar_investment']
    assert evidence.intent == 'solar_investment'
    assert evidence.area == ALL_AREAS
    assert [row['option'] for row in investment['proposal_options']] == ['Option 1', 'Option 2']
    assert investment['actual_selected_period']
    assert 'proposal option(s)' in evidence.local_answer


@pytest.mark.parametrize(
    ('question', 'intent', 'answer_fragment'),
    (
        ('What PDF and XLSX reports can the Reports Center export?', 'reports', 'Available report types'),
        ('Is the Data Update database current and what is stored?', 'data_update', 'Stored history'),
    ),
)
def test_reports_and_data_update_are_valid_app_topics(
    question: str,
    intent: str,
    answer_fragment: str,
) -> None:
    evidence = build_ai_evidence(
        _dataset(), question, date(2026, 7, 13), date(2026, 7, 20), ALL_AREAS
    )
    assert evidence.allowed is True
    assert evidence.intent == intent
    assert answer_fragment in evidence.local_answer


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs) -> _Response:
        self.calls.append({'url': url, **kwargs})
        return self.responses.pop(0)


def _evidence() -> AiEvidence:
    return AiEvidence(
        True,
        'highest_usage',
        'When was usage highest?',
        date(2026, 7, 20),
        date(2026, 7, 20),
        ALL_AREAS,
        {'highest': 1000},
        'Warehouse 6 was highest.',
        ('Cause is not confirmed.',),
        ('Check the shift log.',),
    )


def test_copilot_client_disables_web_and_sends_only_evidence_context() -> None:
    session = _Session([
        _Response(201, {'id': 'conversation-1'}),
        _Response(200, {'messages': [
            {'text': 'When was usage highest?'},
            {'text': 'Warehouse 6 was highest based on the supplied readings.'},
        ]}),
    ])
    client = CopilotChatClient(api_root='https://example.test/copilot', session=session)
    reply = client.ask('token', _evidence())
    assert reply.conversation_id == 'conversation-1'
    assert reply.text.startswith('Warehouse 6')
    payload = session.calls[1]['json']
    assert payload['contextualResources']['webContext']['isWebEnabled'] is False
    assert 'verified_facts' in payload['additionalContext'][0]['text']
    assert 'analysis_permissions' in payload['additionalContext'][0]['text']
    assert 'app_calculated_reference' in payload['additionalContext'][0]['text']
    assert 'reason freely' in payload['message']['text']
    assert 'sound domain knowledge' in payload['message']['text']
    assert 'never claim that the tariff timetable or exact clock windows are unavailable' in payload['message']['text']
    assert 'Treat every listed page as analytically accessible' in payload['message']['text']
    assert 'read-only analytical access' in payload['message']['text']
    assert payload['additionalContext'][0]['@odata.type'] == '#microsoft.graph.copilotContextMessage'
    assert 'not a restriction on deeper analysis' in payload['additionalContext'][0]['description']
    assert session.calls[1]['headers']['Authorization'] == 'Bearer token'


def test_copilot_client_explains_pending_admin_consent() -> None:
    session = _Session([_Response(403, {'error': {'message': 'Forbidden'}})])
    client = CopilotChatClient(api_root='https://example.test/copilot', session=session)
    with pytest.raises(CopilotConnectionError, match='administrator'):
        client.ask('token', _evidence())


def test_auth_status_does_not_contact_microsoft_before_sign_in(tmp_path: Path) -> None:
    auth = MicrosoftCopilotAuth(cache_path=tmp_path / 'token.bin')
    assert auth.status().signed_in is False


def _scope_token(scopes: tuple[str, ...]) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({'scp': ' '.join(scopes)}).encode('utf-8')
    ).decode('ascii').rstrip('=')
    return f'header.{payload}.signature'


class _FakeAuthApp:
    def __init__(self, token: str) -> None:
        self.token = token
        self.interactive_kwargs: dict[str, object] = {}
        self.device_scopes: list[str] = []

    def get_accounts(self) -> list[dict[str, str]]:
        return [{'username': 'employee@example.com', 'name': 'Employee Example'}]

    def acquire_token_silent_with_error(
        self, scopes: list[str], *, account: dict[str, str]
    ) -> dict[str, str]:
        return {'access_token': self.token}

    def acquire_token_interactive(self, **kwargs: object) -> dict[str, str]:
        self.interactive_kwargs = kwargs
        return {'access_token': self.token}

    def initiate_device_flow(self, *, scopes: list[str]) -> dict[str, object]:
        self.device_scopes = scopes
        return {
            'user_code': 'ABCD-EFGH',
            'verification_uri': 'https://microsoft.com/devicelogin',
            'message': 'Enter the code.',
            'expires_in': 900,
            'device_code': 'private-device-code',
        }

    def acquire_token_by_device_flow(self, flow: dict[str, object]) -> dict[str, str]:
        assert flow['device_code'] == 'private-device-code'
        return {'access_token': self.token}


def test_auth_status_reports_missing_scopes_from_cached_token(tmp_path: Path) -> None:
    auth = MicrosoftCopilotAuth(cache_path=tmp_path / 'token.bin')
    auth._app = _FakeAuthApp(_scope_token(('User.Read',)))
    status = auth.status()
    assert status.signed_in is True
    assert status.token_available is True
    assert 'Mail.Send' in status.missing_scopes


def test_sign_in_forces_account_selection_and_verifies_scopes(tmp_path: Path) -> None:
    auth = MicrosoftCopilotAuth(cache_path=tmp_path / 'token.bin')
    fake_app = _FakeAuthApp(_scope_token(COPILOT_SCOPES))
    auth._app = fake_app
    status = auth.sign_in()
    assert status.token_available is True
    assert status.missing_scopes == ()
    assert fake_app.interactive_kwargs['prompt'] == 'select_account'


def test_device_sign_in_avoids_browser_callback_and_verifies_scopes(tmp_path: Path) -> None:
    auth = MicrosoftCopilotAuth(cache_path=tmp_path / 'token.bin')
    fake_app = _FakeAuthApp(_scope_token(COPILOT_SCOPES))
    auth._app = fake_app
    device_sign_in = auth.begin_device_sign_in()
    assert device_sign_in.user_code == 'ABCD-EFGH'
    assert device_sign_in.verification_uri == 'https://microsoft.com/devicelogin'
    assert set(fake_app.device_scopes) == set(COPILOT_SCOPES)
    status = auth.complete_device_sign_in(device_sign_in)
    assert status.token_available is True
    assert status.missing_scopes == ()


def test_auth_status_does_not_block_an_active_device_sign_in(tmp_path: Path) -> None:
    auth = MicrosoftCopilotAuth(cache_path=tmp_path / 'token.bin')
    auth._app = _FakeAuthApp(_scope_token(COPILOT_SCOPES))
    auth._device_sign_in_active.set()
    status = auth.status()
    assert status.signed_in is False
    assert status.token_available is False
    assert 'being completed' in status.warning
    assert auth.access_token() is None
