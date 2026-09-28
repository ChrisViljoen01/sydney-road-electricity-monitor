from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from electricity_tool.mail_agent import (
    GraphMailClient,
    MailActionResult,
    MailAgentError,
    MailAgentStore,
    MailDraft,
    RecipientPreferences,
    audit_entry,
    compose_alert_email,
    compose_daily_peak_usage_email,
    compose_refresh_complete_email,
    parse_copilot_email,
    parse_recipient_text,
)


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        self.text = ''

    def json(self) -> dict[str, object]:
        return self.payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(('GET', url, kwargs))
        return self.responses.pop(0)

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(('POST', url, kwargs))
        return self.responses.pop(0)


def test_recipient_parser_and_validation_normalize_addresses() -> None:
    draft = MailDraft(
        subject=' Alert subject ',
        body='Useful alert body',
        to_recipients=parse_recipient_text('Adam@Example.com; kevin@example.com\nadam@example.com'),
    ).validated()
    assert draft.to_recipients == ('adam@example.com', 'kevin@example.com')
    assert draft.subject == 'Alert subject'


def test_invalid_recipient_is_rejected_before_graph_call() -> None:
    with pytest.raises(MailAgentError, match='Invalid recipient'):
        MailDraft(
            subject='Alert', body='Body', to_recipients=('not-an-address',)
        ).validated()


def test_send_mail_uses_signed_in_mailbox_and_attachment(tmp_path: Path) -> None:
    attachment = tmp_path / 'alert.pdf'
    attachment.write_bytes(b'%PDF-test')
    session = _Session([_Response(202)])
    client = GraphMailClient('https://graph.test/v1.0', session=session)
    draft = MailDraft(
        subject='Alert',
        body='Verified evidence',
        to_recipients=('owner@example.com',),
        cc_recipients=('ops@example.com',),
        attachment_paths=(str(attachment),),
    )
    result = client.send_mail('token', draft)
    method, url, kwargs = session.calls[0]
    assert result.action == 'Email sent'
    assert method == 'POST'
    assert url == 'https://graph.test/v1.0/me/sendMail'
    assert kwargs['json']['saveToSentItems'] is True
    assert kwargs['json']['message']['attachments'][0]['name'] == 'alert.pdf'
    assert kwargs['headers']['Authorization'] == 'Bearer token'


def test_shared_mailbox_draft_uses_shared_endpoint() -> None:
    session = _Session([_Response(201, {'id': 'draft-1', 'webLink': 'https://outlook/draft'})])
    client = GraphMailClient('https://graph.test/v1.0', session=session)
    result = client.create_draft('token', MailDraft(
        subject='Alert',
        body='Verified evidence',
        to_recipients=('owner@example.com',),
        sender_mailbox='energy@example.com',
    ))
    assert session.calls[0][1] == 'https://graph.test/v1.0/users/energy@example.com/messages'
    assert result.message_id == 'draft-1'


def test_directory_search_returns_mail_enabled_people() -> None:
    session = _Session([_Response(200, {'value': [
        {
            'id': '1',
            'displayName': 'Adam Example',
            'userPrincipalName': 'adam@example.com',
            'scoredEmailAddresses': [
                {'address': 'adam@example.com', 'relevanceScore': 20.0},
            ],
        },
        {'id': '2', 'displayName': 'No Mail'},
    ]})])
    client = GraphMailClient('https://graph.test/v1.0', session=session)
    people = client.search_directory('token', 'Ad')
    assert len(people) == 1
    assert people[0].email == 'adam@example.com'
    assert session.calls[0][1] == 'https://graph.test/v1.0/me/people'
    assert session.calls[0][2]['params']['$search'] == '"Ad"'
    assert 'scoredEmailAddresses' in session.calls[0][2]['params']['$select']


def test_copilot_email_parser_keeps_recipients_and_removes_markdown() -> None:
    fallback = MailDraft(
        subject='Fallback',
        body='Fallback body long enough to be used when needed.',
        to_recipients=('owner@example.com',),
        alert_id='alert-1',
    )
    parsed = parse_copilot_email(
        '**Subject:** Warehouse 6 demand alert\n\n**Body:**\nHello,\n\n'
        'Warehouse 6 recorded a verified demand increase. Please review the shift and '
        'equipment log at the recorded peak time before confirming the cause.\n\nKind regards,',
        fallback,
    )
    assert parsed.subject == 'Warehouse 6 demand alert'
    assert '**' not in parsed.body
    assert parsed.to_recipients == ('owner@example.com',)
    assert parsed.source.startswith('Microsoft Copilot')


def test_local_alert_composer_only_states_verified_cause_limit() -> None:
    draft = compose_alert_email(
        {
            'spike_id': 'w6-alert',
            'date': '2026-07-20',
            'area': 'Warehouse 6',
            'through_date': '2026-07-21',
            'alert_level': 'High',
            'alert_reason': 'Peak kVA is above baseline',
            'import_kwh': '1000',
            'peak_kw': '110',
            'peak_kw_time': '2026-07-20 17:30:00',
            'peak_kva': '114',
            'peak_kva_time': '2026-07-20 17:30:00',
        },
        period_start=date(2026, 7, 20),
        period_end=date(2026, 7, 20),
    )
    assert draft.subject.startswith('[High]')
    assert 'operational records are required to confirm the physical cause' in draft.body
    assert draft.alert_id == 'w6-alert'


def test_refresh_complete_email_is_bounded_to_successful_refresh_evidence() -> None:
    from datetime import datetime

    draft = compose_refresh_complete_email(
        recipient='operations@example.com',
        completed_at=datetime.fromisoformat('2026-07-22T12:34:00+02:00'),
        inserted_readings=48,
        refreshed_readings=240,
        latest_closed_date=date(2026, 7, 21),
        duration_seconds=67.4,
    )
    assert draft.to_recipients == ('operations@example.com',)
    assert draft.subject == '[Electricity Tool] Meter data refresh completed - 22 Jul 2026 12:34'
    assert 'New half-hour readings stored: 48' in draft.body
    assert 'Existing half-hour readings refreshed: 240' in draft.body
    assert 'Analytics available through: 21 Jul 2026' in draft.body
    assert 'Failed refreshes do not trigger this message.' in draft.body
    assert draft.source == 'Autonomous rule - successful meter refresh'


def test_daily_peak_email_contains_all_area_evidence_and_actions() -> None:
    draft = compose_daily_peak_usage_email(
        alert_date=date(2026, 7, 21),
        peak_analysis_date=date(2026, 7, 21),
        recipients=('owner@example.com',),
        monthly_surcharge_analysis=[
            {
                'area': 'Warehouse 6',
                'complete_days': 20,
                'maximum_kva': 115,
                'maximum_kva_time': '18 Jul 2026 18:30',
                'threshold_kva': 110,
                'threshold_variance_kva': 5,
                'surcharge_active': True,
                'peak_kwh': 900,
                'estimated_surcharge_inc_vat': 2500,
            },
            {
                'area': 'Warehouse 7',
                'through_date': '2026-07-21',
                'complete_days': 20,
                'maximum_kva': 90,
                'maximum_kva_time': '17 Jul 2026 08:00',
                'threshold_kva': 110,
                'threshold_variance_kva': -20,
                'surcharge_active': False,
                'peak_kwh': 400,
                'estimated_surcharge_inc_vat': 0,
            },
        ],
        monthly_cost_outlook={
            'month': 'July 2026',
            'through_date': '2026-07-21',
            'complete_days': 21,
            'estimated_bill_to_date': 125000,
            'projected_month_end_bill': 184523.81,
            'projected_month_end_kwh': 45000,
            'projected_network_surcharge': 12000,
            'solar_savings_to_date': 5000,
        },
        tariff_status='current',
        tariff_checked_at=datetime.fromisoformat('2026-07-22T06:00:00+02:00'),
        tariff_message='The stored tariffs match the official schedule.',
        tariff_source_url='https://example.test/tariff.pdf',
        ctou_rates_inc_vat={
            'peak': 8.0493,
            'standard': 4.0275,
            'off_peak': 1.9620,
        },
        test_mode=True,
        ctou_analysis=[
            {
                'area': 'Warehouse 6',
                'peak_kwh': 42,
                'peak_share_percent': 18,
                'peak_rate_inc_vat': 8.0493,
                'peak_energy_charge_inc_vat': 338.07,
                'highest_peak_kw': 110,
                'highest_peak_time': '17:30',
                'baseline_peak_kwh': 30,
                'increase_kwh': 12,
                'increase_percent': 40,
                'baseline_days': 4,
                'action_required': True,
                'estimated_off_peak_saving': 73.05,
                'peak_intervals': [
                    {
                        'start_time': '17:00',
                        'end_time': '17:30',
                        'kwh': 18,
                        'average_kw': 36,
                        'energy_charge_inc_vat': 144.89,
                    },
                ],
                'peak_windows': [
                    {
                        'start_time': '17:00',
                        'end_time': '20:00',
                        'kwh': 42,
                        'average_kw': 14,
                        'rate_inc_vat': 8.0493,
                        'energy_charge_inc_vat': 338.07,
                    },
                ],
            },
            {
                'area': 'Warehouse 7',
                'peak_kwh': 20,
                'peak_share_percent': 10,
                'peak_rate_inc_vat': 8.0493,
                'peak_energy_charge_inc_vat': 160.99,
                'highest_peak_kw': 90,
                'highest_peak_time': '07:30',
                'baseline_peak_kwh': 19,
                'increase_kwh': 1,
                'increase_percent': 5.26,
                'baseline_days': 4,
                'action_required': False,
                'estimated_off_peak_saving': 0,
                'peak_intervals': [],
                'peak_windows': [],
            },
        ],
    )
    assert draft.subject == '[TEST] [Electricity Tool] CTOU cost control - 21 Jul 2026'
    assert 'AGENT TESTING' in draft.body
    assert 'No action is required for this test message.' in draft.body
    assert 'Warehouse 6' in draft.body
    assert '110.0 kW' in draft.body
    assert 'Warehouse 7' in draft.body
    assert '42.0 kWh' in draft.body
    assert 'R8.0493/kWh' in draft.body
    assert 'before surcharge' in draft.body
    assert 'Management interpretation and instruction' in draft.body
    assert 'Current-month demand and surcharge exposure' in draft.body
    assert 'Above by 5.0 kVA' in draft.body
    assert 'R2,500.00' in draft.body
    assert 'Estimated active surcharge' in draft.body
    assert 'This is not the total electricity bill or the Peak-hour energy cost.' in draft.body
    assert 'CTOU time-band reference' in draft.body
    assert 'Current-month cost outlook' in draft.body
    assert 'R125,000.00' in draft.body
    assert 'R184,523.81' in draft.body
    assert 'MTD CTOU Peak-hours energy' in draft.body
    assert draft.body.index('Latest applicable Peak-day summary') < draft.body.index(
        'Current-month demand and surcharge exposure'
    )
    assert draft.body.index('Current-month cost outlook') < draft.body.index(
        'Current-month demand and surcharge exposure'
    )
    assert draft.body.count('Current-month cost outlook') == 1
    assert 'Peak R8.0493/kWh' in draft.body
    assert 'Action required' in draft.body
    assert '10% and 5 kWh above baseline' in draft.body
    assert 'Electricity used during CTOU Peak time bands' in draft.body
    assert 'Standard and' in draft.body
    assert 'Off-peak consumption is excluded' in draft.body
    assert '17:00-20:00' in draft.body
    assert '42.0 kWh' in draft.body
    assert 'Peak energy cost' in draft.body
    assert 'R338.07' in draft.body
    assert 'before surcharge' in draft.body
    assert '0 kWh flexible load' in draft.body
    assert draft.body_content_type == 'HTML'
    assert draft.alert_id == 'daily-peak-2026-07-21'


def test_mail_agent_store_persists_preferences_and_audit(tmp_path: Path) -> None:
    store = MailAgentStore(tmp_path / 'recipients.json', tmp_path / 'history.jsonl')
    preferences = RecipientPreferences(
        to_recipients=('owner@example.com',),
        cc_recipients=('ops@example.com',),
        sender_mailbox='energy@example.com',
    )
    store.save_preferences(preferences)
    assert store.preferences() == preferences

    draft = MailDraft(
        subject='Alert',
        body='Verified evidence',
        to_recipients=preferences.to_recipients,
        cc_recipients=preferences.cc_recipients,
        sender_mailbox=preferences.sender_mailbox,
        alert_id='alert-1',
    )
    result = MailActionResult('Email sent', '2026-07-21T12:00:00+02:00')
    store.record(audit_entry(draft, result))
    history = store.history()
    assert history[0].subject == 'Alert'
    assert history[0].action == 'Email sent'
