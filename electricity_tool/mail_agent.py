from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
import re
from threading import RLock
from typing import Iterable
from urllib.parse import quote

import requests


GRAPH_ROOT = 'https://graph.microsoft.com/v1.0'
EMAIL_PATTERN = re.compile(r'^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$')
MAX_ATTACHMENT_BYTES = 2_500_000


class MailAgentError(RuntimeError):
    """A user-facing directory, draft or email delivery failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DirectoryPerson:
    display_name: str
    email: str
    user_principal_name: str = ''
    user_id: str = ''


@dataclass(frozen=True)
class MailDraft:
    subject: str
    body: str
    to_recipients: tuple[str, ...] = ()
    cc_recipients: tuple[str, ...] = ()
    sender_mailbox: str = ''
    source: str = 'Verified local template'
    alert_id: str = ''
    attachment_paths: tuple[str, ...] = ()
    body_content_type: str = 'Text'

    def validated(self) -> MailDraft:
        subject = ' '.join(str(self.subject).split()).strip()
        body = str(self.body).strip()
        to_recipients = validate_recipients(self.to_recipients, required=True)
        cc_recipients = validate_recipients(self.cc_recipients)
        sender = str(self.sender_mailbox).strip().casefold()
        if sender and not EMAIL_PATTERN.fullmatch(sender):
            raise MailAgentError(f'The sender mailbox is not a valid email address: {sender}')
        if not subject:
            raise MailAgentError('The email subject cannot be empty.')
        if len(subject) > 255:
            raise MailAgentError('The email subject cannot exceed 255 characters.')
        if not body:
            raise MailAgentError('The email body cannot be empty.')
        if len(body) > 100_000:
            raise MailAgentError('The email body is too long to send safely.')
        requested_content_type = str(self.body_content_type).strip().casefold()
        content_type = 'HTML' if requested_content_type == 'html' else 'Text'
        if requested_content_type not in {'text', 'html'}:
            raise MailAgentError('The email body content type must be Text or HTML.')
        paths = tuple(str(Path(value).expanduser().resolve()) for value in self.attachment_paths)
        return MailDraft(
            subject=subject,
            body=body,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            sender_mailbox=sender,
            source=self.source,
            alert_id=self.alert_id,
            attachment_paths=paths,
            body_content_type=content_type,
        )


@dataclass(frozen=True)
class MailActionResult:
    action: str
    completed_at: str
    message_id: str = ''
    web_link: str = ''


@dataclass(frozen=True)
class RecipientPreferences:
    to_recipients: tuple[str, ...] = ()
    cc_recipients: tuple[str, ...] = ()
    sender_mailbox: str = ''


@dataclass(frozen=True)
class MailAuditEntry:
    completed_at: str
    action: str
    subject: str
    to_recipients: tuple[str, ...]
    cc_recipients: tuple[str, ...]
    sender_mailbox: str
    alert_id: str
    source: str
    attachment_names: tuple[str, ...] = field(default_factory=tuple)


class GraphMailClient:
    """Least-privilege Microsoft Graph mail and recipient operations."""

    def __init__(
        self,
        graph_root: str = GRAPH_ROOT,
        session: requests.Session | None = None,
        timeout_seconds: int = 45,
    ) -> None:
        self.graph_root = graph_root.rstrip('/')
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def search_directory(
        self,
        access_token: str,
        query: str,
        *,
        limit: int = 12,
    ) -> tuple[DirectoryPerson, ...]:
        cleaned = ' '.join(str(query).split()).strip()
        if len(cleaned) < 2:
            raise MailAgentError('Enter at least two characters to search company recipients.')
        escaped = cleaned.replace('"', '\\"')
        headers = self._headers(access_token)
        try:
            response = self.session.get(
                f'{self.graph_root}/me/people',
                headers=headers,
                params={
                    '$select': 'id,displayName,userPrincipalName,scoredEmailAddresses',
                    '$search': f'"{escaped}"',
                    '$top': str(max(1, min(int(limit), 25))),
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise MailAgentError(
                'Microsoft Graph could not be reached. Check the internet connection and try again.'
            ) from exc
        self._raise_for_graph_error(response, 'Company recipients could not be searched')
        try:
            rows = response.json().get('value', [])
        except (ValueError, AttributeError) as exc:
            raise MailAgentError('Microsoft Graph returned an invalid recipient-search response.') from exc
        people: list[DirectoryPerson] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            scored_addresses = row.get('scoredEmailAddresses')
            address_candidates: list[tuple[float, str]] = []
            if isinstance(scored_addresses, list):
                for item in scored_addresses:
                    if not isinstance(item, dict):
                        continue
                    address = str(item.get('address') or '').strip()
                    if not EMAIL_PATTERN.fullmatch(address):
                        continue
                    try:
                        relevance = float(item.get('relevanceScore') or 0.0)
                    except (TypeError, ValueError):
                        relevance = 0.0
                    address_candidates.append((relevance, address))
            email = (
                max(address_candidates, default=(0.0, ''), key=lambda item: item[0])[1]
                or str(row.get('userPrincipalName') or '').strip()
            )
            if not EMAIL_PATTERN.fullmatch(email):
                continue
            people.append(DirectoryPerson(
                display_name=str(row.get('displayName') or email),
                email=email.casefold(),
                user_principal_name=str(row.get('userPrincipalName') or ''),
                user_id=str(row.get('id') or ''),
            ))
        return tuple(people)

    def create_draft(self, access_token: str, draft: MailDraft) -> MailActionResult:
        prepared = draft.validated()
        try:
            response = self.session.post(
                self._mailbox_endpoint(prepared.sender_mailbox, 'messages'),
                headers=self._headers(access_token),
                json=self._message_payload(prepared),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise MailAgentError(
                'Microsoft Graph could not be reached. Check the internet connection and try again.'
            ) from exc
        self._raise_for_graph_error(response, 'The Outlook draft could not be created')
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return MailActionResult(
            action='Draft created',
            completed_at=datetime.now().astimezone().isoformat(timespec='seconds'),
            message_id=str(payload.get('id') or ''),
            web_link=str(payload.get('webLink') or ''),
        )

    def send_mail(self, access_token: str, draft: MailDraft) -> MailActionResult:
        prepared = draft.validated()
        try:
            response = self.session.post(
                self._mailbox_endpoint(prepared.sender_mailbox, 'sendMail'),
                headers=self._headers(access_token),
                json={
                    'message': self._message_payload(prepared),
                    'saveToSentItems': True,
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise MailAgentError(
                'Microsoft Graph could not be reached. Check the internet connection and try again.'
            ) from exc
        self._raise_for_graph_error(response, 'The email could not be sent')
        return MailActionResult(
            action='Email sent',
            completed_at=datetime.now().astimezone().isoformat(timespec='seconds'),
        )

    def _message_payload(self, draft: MailDraft) -> dict[str, object]:
        payload: dict[str, object] = {
            'subject': draft.subject,
            'body': {'contentType': draft.body_content_type, 'content': draft.body},
            'toRecipients': [self._recipient(value) for value in draft.to_recipients],
            'ccRecipients': [self._recipient(value) for value in draft.cc_recipients],
        }
        if draft.attachment_paths:
            payload['attachments'] = [
                self._attachment(value) for value in draft.attachment_paths
            ]
        return payload

    def _mailbox_endpoint(self, sender_mailbox: str, operation: str) -> str:
        if sender_mailbox:
            mailbox = quote(sender_mailbox, safe='@._-')
            return f'{self.graph_root}/users/{mailbox}/{operation}'
        return f'{self.graph_root}/me/{operation}'

    @staticmethod
    def _recipient(email: str) -> dict[str, object]:
        return {'emailAddress': {'address': email}}

    @staticmethod
    def _attachment(path_value: str) -> dict[str, object]:
        path = Path(path_value)
        if not path.is_file():
            raise MailAgentError(f'The attachment no longer exists: {path.name}')
        size = path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise MailAgentError(
                f'{path.name} is too large for the direct email attachment workflow '
                f'({size / 1_000_000:.1f} MB).'
            )
        return {
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': path.name,
            'contentType': _content_type(path),
            'contentBytes': base64.b64encode(path.read_bytes()).decode('ascii'),
        }

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        token = str(access_token).strip()
        if not token:
            raise MailAgentError('Microsoft sign-in is required before using the mail agent.')
        return {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }

    @staticmethod
    def _raise_for_graph_error(
        response: requests.Response,
        prefix: str,
    ) -> None:
        if 200 <= response.status_code < 300:
            return
        detail = ''
        try:
            payload = response.json()
            error = payload.get('error', {}) if isinstance(payload, dict) else {}
            if isinstance(error, dict):
                detail = str(error.get('message') or error.get('code') or '')
        except ValueError:
            detail = response.text
        detail = ' '.join(detail.split())[:500]
        if response.status_code in {401, 403}:
            raise MailAgentError(
                f'{prefix}: the renewed Microsoft token does not yet contain the required '
                'delegated permission, or the signed-in user cannot access the selected mailbox.',
                status_code=response.status_code,
            )
        raise MailAgentError(
            f'{prefix}{f": {detail}" if detail else "."}',
            status_code=response.status_code,
        )


class MailAgentStore:
    """Small local recipient configuration and append-only communication history."""

    def __init__(self, config_path: str | Path, audit_path: str | Path) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.audit_path = Path(audit_path).expanduser().resolve()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def preferences(self) -> RecipientPreferences:
        with self._lock:
            if not self.config_path.exists():
                return RecipientPreferences()
            try:
                payload = json.loads(self.config_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return RecipientPreferences()
        if not isinstance(payload, dict):
            return RecipientPreferences()
        try:
            return RecipientPreferences(
                to_recipients=validate_recipients(payload.get('to_recipients', ())),
                cc_recipients=validate_recipients(payload.get('cc_recipients', ())),
                sender_mailbox=_optional_email(payload.get('sender_mailbox', '')),
            )
        except MailAgentError:
            return RecipientPreferences()

    def save_preferences(self, preferences: RecipientPreferences) -> None:
        validated = RecipientPreferences(
            to_recipients=validate_recipients(preferences.to_recipients),
            cc_recipients=validate_recipients(preferences.cc_recipients),
            sender_mailbox=_optional_email(preferences.sender_mailbox),
        )
        payload = json.dumps(asdict(validated), indent=2, ensure_ascii=False)
        temporary = self.config_path.with_suffix(self.config_path.suffix + '.tmp')
        with self._lock:
            temporary.write_text(payload, encoding='utf-8')
            temporary.replace(self.config_path)

    def record(self, entry: MailAuditEntry) -> None:
        payload = json.dumps(asdict(entry), ensure_ascii=False)
        with self._lock:
            with self.audit_path.open('a', encoding='utf-8') as handle:
                handle.write(payload + '\n')

    def history(self, limit: int = 30) -> tuple[MailAuditEntry, ...]:
        if not self.audit_path.exists():
            return ()
        with self._lock:
            try:
                lines = self.audit_path.read_text(encoding='utf-8').splitlines()
            except OSError:
                return ()
        rows: list[MailAuditEntry] = []
        for line in reversed(lines[-max(1, int(limit)):]):
            try:
                payload = json.loads(line)
                rows.append(MailAuditEntry(
                    completed_at=str(payload.get('completed_at') or ''),
                    action=str(payload.get('action') or ''),
                    subject=str(payload.get('subject') or ''),
                    to_recipients=tuple(payload.get('to_recipients') or ()),
                    cc_recipients=tuple(payload.get('cc_recipients') or ()),
                    sender_mailbox=str(payload.get('sender_mailbox') or ''),
                    alert_id=str(payload.get('alert_id') or ''),
                    source=str(payload.get('source') or ''),
                    attachment_names=tuple(payload.get('attachment_names') or ()),
                ))
            except (TypeError, ValueError):
                continue
        return tuple(rows)


def parse_recipient_text(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = re.split(r'[;,\n]+', value)
    else:
        try:
            candidates = list(value)  # type: ignore[arg-type]
        except TypeError:
            candidates = [value]
    return tuple(str(candidate).strip() for candidate in candidates if str(candidate).strip())


def validate_recipients(
    values: Iterable[object],
    *,
    required: bool = False,
) -> tuple[str, ...]:
    recipients: list[str] = []
    invalid: list[str] = []
    for value in values:
        email = str(value).strip().casefold()
        if not email:
            continue
        if not EMAIL_PATTERN.fullmatch(email):
            invalid.append(str(value).strip())
            continue
        if email not in recipients:
            recipients.append(email)
    if invalid:
        raise MailAgentError(f"Invalid recipient address: {', '.join(invalid)}")
    if required and not recipients:
        raise MailAgentError('Add at least one recipient before continuing.')
    if len(recipients) > 50:
        raise MailAgentError('A maximum of 50 recipients is allowed per agent email.')
    return tuple(recipients)


def compose_alert_email(
    alert: dict[str, object],
    *,
    period_start: date,
    period_end: date,
) -> MailDraft:
    area = str(alert.get('area') or 'Selected meter area')
    alert_date = str(alert.get('date') or period_end.isoformat())[:10]
    severity = str(alert.get('alert_level') or alert.get('severity') or 'Alert')
    reason = str(alert.get('alert_reason') or alert.get('reason') or 'Unusual electricity usage was recorded.')
    peak_kw = _number(alert.get('peak_kw'))
    peak_kva = _number(alert.get('peak_kva'))
    import_kwh = _number(alert.get('import_kwh') or alert.get('kwh'))
    kw_time = str(alert.get('peak_kw_time') or '')
    kva_time = str(alert.get('peak_kva_time') or '')
    alert_id = str(alert.get('spike_id') or f'{area}-{alert_date}')
    subject = f'[{severity}] Electricity alert – {area} – {alert_date}'
    body = '\n'.join((
        'Hello,',
        '',
        f'The Sydney Road Electricity Monitor identified a {severity.lower()} electricity alert for {area} on {alert_date}.',
        '',
        'Verified meter evidence',
        f'- Reason: {reason}',
        f'- Electricity used: {import_kwh:,.1f} kWh',
        f'- Highest working load: {peak_kw:,.1f} kW{f" at {kw_time}" if kw_time else ""}',
        f'- Highest apparent demand: {peak_kva:,.1f} kVA{f" at {kva_time}" if kva_time else ""}',
        f'- Analytical period: {period_start.isoformat()} to {period_end.isoformat()}',
        '',
        'Recommended check',
        'Please review the relevant shift, equipment, charging and maintenance records at the recorded peak time. '
        'The meter data confirms when and by how much usage changed; operational records are required to confirm the physical cause.',
        '',
        'Kind regards,',
        'Sydney Road Electricity Monitor',
    ))
    return MailDraft(subject=subject, body=body, source='Verified local template', alert_id=alert_id)


def compose_period_summary_email(
    *,
    period_start: date,
    period_end: date,
    area: str,
    total_kwh: float,
    peak_kw: float,
    peak_kw_time: str,
    peak_kva: float,
    peak_kva_time: str,
    alert_count: int,
) -> MailDraft:
    subject = f'Electricity summary – {area} – {period_start.isoformat()} to {period_end.isoformat()}'
    body = '\n'.join((
        'Hello,',
        '',
        f'Please find the electricity summary for {area} from {period_start.isoformat()} to {period_end.isoformat()}.',
        '',
        'Verified meter evidence',
        f'- Electricity used: {total_kwh:,.1f} kWh',
        f'- Highest working load: {peak_kw:,.1f} kW{f" at {peak_kw_time}" if peak_kw_time else ""}',
        f'- Highest apparent demand: {peak_kva:,.1f} kVA{f" at {peak_kva_time}" if peak_kva_time else ""}',
        f'- Unusual-usage alerts in the selected period: {alert_count}',
        '',
        'The app report for these filters contains the detailed area and daily analysis for the selected period.',
        '',
        'Kind regards,',
        'Sydney Road Electricity Monitor',
    ))
    return MailDraft(subject=subject, body=body, source='Verified local template', alert_id='period-summary')


def compose_daily_peak_usage_email(
    *,
    alert_date: date,
    peak_analysis_date: date,
    ctou_analysis: list[dict[str, object]],
    monthly_surcharge_analysis: list[dict[str, object]],
    monthly_cost_outlook: dict[str, object],
    recipients: tuple[str, ...],
    tariff_status: str,
    tariff_checked_at: datetime,
    tariff_message: str,
    tariff_source_url: str,
    ctou_rates_inc_vat: dict[str, float],
    test_mode: bool = False,
) -> MailDraft:
    """Build an HTML report about energy consumed during municipal Peak bands."""
    if not ctou_analysis:
        raise MailAgentError('CTOU interval analysis is required for the daily Peak-band email.')
    if not monthly_surcharge_analysis:
        raise MailAgentError('Current-month demand analysis is required for the daily Peak-band email.')
    ordered = sorted(
        ctou_analysis,
        key=lambda row: _number(row.get('peak_kwh')),
        reverse=True,
    )
    highest = ordered[0]
    action_rows = [row for row in ordered if bool(row.get('action_required'))]
    total_peak_kwh = sum(_number(row.get('peak_kwh')) for row in ordered)
    total_peak_energy_charge = sum(
        _number(row.get('peak_energy_charge_inc_vat')) for row in ordered
    )
    total_saving = sum(_number(row.get('estimated_off_peak_saving')) for row in ordered)
    using_prior_peak_day = peak_analysis_date != alert_date
    monthly_peak_kwh = sum(
        _number(row.get('peak_kwh')) for row in monthly_surcharge_analysis
    )
    monthly_peak_cost = sum(
        _number(row.get('peak_energy_charge_inc_vat'))
        for row in monthly_surcharge_analysis
    )
    subject_prefix = '[TEST] ' if test_mode else ''
    subject = f'{subject_prefix}[Electricity Tool] CTOU cost control - {alert_date:%d %b %Y}'
    analysis_rows = ''.join(_ctou_analysis_html_row(row) for row in ordered)
    interval_rows = ''.join(
        _ctou_window_html_row(
            str(row.get('area') or 'Warehouse'),
            window,
            rank,
        )
        for row in ordered
        for rank, window in enumerate(
            (
                item for item in row.get('peak_windows', [])
                if isinstance(item, dict)
            ),
            start=1,
        )
    )
    monthly_rows = ''.join(
        _monthly_surcharge_html_row(row)
        for row in monthly_surcharge_analysis
    )
    warehouse_six = next(
        (
            row for row in monthly_surcharge_analysis
            if str(row.get('area') or '') == 'Warehouse 6'
        ),
        monthly_surcharge_analysis[0],
    )
    warehouse_six_active = bool(warehouse_six.get('surcharge_active'))
    warehouse_six_message = (
        f'Warehouse 6 is {_number(warehouse_six.get("threshold_variance_kva")):,.1f} kVA '
        'above the 110 kVA threshold in PNPSCADA. The estimated 25% surcharge remains active.'
        if warehouse_six_active else
        f'Warehouse 6 is {_number(warehouse_six.get("threshold_variance_kva")) * -1:,.1f} kVA '
        'below the threshold in PNPSCADA, but this does not confirm that the billed surcharge has ended. '
        'The electricity advisor reports recent UMFA demand readings remained above 110 kVA; verify the '
        'current UMFA demand register before treating the surcharge as avoided.'
    )
    if action_rows:
        action_items = ''.join(
            f'<li><strong>{escape(str(row.get("area")))}</strong>: investigate '
            f'{_number(row.get("increase_kwh")):,.1f} kWh above its matching-weekday Peak baseline. '
            f'Review flexible loads around {escape(str(row.get("highest_peak_time") or "the Peak window"))}.</li>'
            for row in action_rows
        )
        management_summary = (
            f'{len(action_rows)} warehouse{"s" if len(action_rows) != 1 else ""} exceeded the strict '
            'matching-weekday Peak-band threshold (at least 10% and 5 kWh above baseline).'
        )
    else:
        action_items = (
            '<li>No warehouse exceeded the strict matching-weekday increase threshold. '
            'Continue reviewing flexible loads because all Peak-band consumption attracts the highest energy rate.</li>'
        )
        management_summary = (
            'No CTOU warehouse exceeded the strict matching-weekday Peak-band increase threshold.'
        )
    tariff_badge_colour = '#027a48' if tariff_status in {'current', 'updated'} else '#b54708'
    source_link = (
        f'<a href="{escape(tariff_source_url, quote=True)}" style="color:#175cd3;">official tariff document</a>'
        if tariff_source_url else 'last-known-good tariff schedule'
    )
    tariff_rows = ''.join(
        f'<tr><td style="{_CELL}">{season}</td><td style="{_CELL}">{day_type}</td>'
        f'<td style="{_CELL}">{peak}</td><td style="{_CELL}">{standard}</td>'
        f'<td style="{_CELL}">{off_peak}</td></tr>'
        for season, day_type, peak, standard, off_peak in _CTOU_TIME_BANDS
    )
    rate_summary = (
        f'Applicable {("high" if peak_analysis_date.month in {6, 7, 8} else "low")}-season rates '
        f'(VAT inclusive): Peak R{ctou_rates_inc_vat.get("peak", 0):.4f}/kWh, '
        f'Standard R{ctou_rates_inc_vat.get("standard", 0):.4f}/kWh, '
        f'Off-peak R{ctou_rates_inc_vat.get("off_peak", 0):.4f}/kWh.'
    )
    test_banner = (
        '<div style="margin-bottom:18px;padding:14px 16px;background:#fff1f0;'
        'border:2px solid #d92d20;color:#912018;font-weight:700;">'
        'AGENT TESTING - This message is being sent only to test the automated daily '
        'electricity agent and email layout. No action is required for this test message.'
        '</div>'
        if test_mode else ''
    )
    no_peak_notice = (
        f'''<div style="margin-bottom:18px;padding:16px 18px;background:#eef6ff;border-left:4px solid #2878bd;">
          <strong>No CTOU Peak time band applied on {alert_date:%A, %d %B %Y}.</strong><br>
          <span style="color:#475467;">Zero-value Peak rows are intentionally not shown. The daily Peak analysis
          below uses the latest complete applicable Peak day:
          <strong>{peak_analysis_date:%A, %d %B %Y}</strong>.</span>
        </div>'''
        if using_prior_peak_day else ''
    )
    body = f'''<!doctype html>
<html><body style="margin:0;background:#f3f5f9;font-family:Segoe UI,Arial,sans-serif;color:#17233f;">
  <div style="max-width:920px;margin:0 auto;padding:24px 12px;">
    <div style="background:#17233f;padding:22px 26px;border-radius:10px 10px 0 0;">
      <div style="color:#9fc5ff;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">
        Sydney Road Electricity Monitor
      </div>
      <h1 style="margin:6px 0 0;color:#ffffff;font-size:24px;">CTOU cost-control report</h1>
      <div style="margin-top:6px;color:#d7e3f7;">CTOU Peak-period consumption | {alert_date:%A, %d %B %Y}</div>
    </div>
    <div style="background:#ffffff;padding:24px 26px;border:1px solid #d9dfeb;border-top:0;">
      {test_banner}
      {no_peak_notice}
      <h2 style="{_HEADING}">Latest applicable Peak-day summary — {peak_analysis_date:%d %b %Y}</h2>
      <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:22px;">
        <tr>
          <td style="padding:14px;background:#f5f8ff;border:1px solid #d9e2f3;">
            <div style="font-size:12px;color:#667085;">Electricity used during CTOU Peak hours</div>
            <div style="font-size:22px;font-weight:700;">{total_peak_kwh:,.1f} kWh</div>
            <div style="font-size:13px;color:#475467;">Total energy used during CTOU Peak periods</div>
          </td>
          <td style="width:14px;"></td>
          <td style="padding:14px;background:#f5f8ff;border:1px solid #d9e2f3;">
            <div style="font-size:12px;color:#667085;">Base Peak energy charge</div>
            <div style="font-size:22px;font-weight:700;">R{total_peak_energy_charge:,.2f}</div>
            <div style="font-size:13px;color:#475467;">VAT inclusive; before any network surcharge</div>
          </td>
          <td style="width:14px;"></td>
          <td style="padding:14px;background:#f5f8ff;border:1px solid #d9e2f3;">
            <div style="font-size:12px;color:#667085;">Highest Peak-period user</div>
            <div style="font-size:22px;font-weight:700;">{_number(highest.get('peak_kwh')):,.1f} kWh</div>
            <div style="font-size:13px;color:#475467;">{escape(str(highest.get('area') or 'Warehouse'))}</div>
          </td>
        </tr>
      </table>

      <h2 style="{_HEADING}">Current-month cost outlook — through {escape(str(monthly_cost_outlook.get('through_date') or ''))}</h2>
      <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:22px;">
        <tr>
          <td style="padding:14px;background:#f5f8ff;border:1px solid #d9e2f3;">
            <div style="font-size:12px;color:#667085;">Estimated bill to date</div>
            <div style="font-size:21px;font-weight:700;">R{_number(monthly_cost_outlook.get('estimated_bill_to_date')):,.2f}</div>
            <div style="font-size:12px;color:#475467;">Through {escape(str(monthly_cost_outlook.get('through_date') or ''))}</div>
          </td>
          <td style="width:10px;"></td>
          <td style="padding:14px;background:#f5f8ff;border:1px solid #d9e2f3;">
            <div style="font-size:12px;color:#667085;">Projected month-end bill</div>
            <div style="font-size:21px;font-weight:700;">R{_number(monthly_cost_outlook.get('projected_month_end_bill')):,.2f}</div>
            <div style="font-size:12px;color:#475467;">Based on {_number(monthly_cost_outlook.get('complete_days')):,.0f} complete days</div>
          </td>
          <td style="width:10px;"></td>
          <td style="padding:14px;background:#fff8e7;border:1px solid #ead7a2;">
            <div style="font-size:12px;color:#667085;">MTD CTOU Peak-hours energy</div>
            <div style="font-size:21px;font-weight:700;">{monthly_peak_kwh:,.1f} kWh</div>
            <div style="font-size:12px;color:#475467;">Base Peak energy cost R{monthly_peak_cost:,.2f}</div>
          </td>
        </tr>
      </table>

      <h2 style="{_HEADING}">Current-month demand and surcharge exposure</h2>
      <div style="margin-bottom:12px;padding:14px 16px;background:{'#fff1f0' if warehouse_six_active else '#ecfdf3'};border-left:4px solid {'#d92d20' if warehouse_six_active else '#039855'};">
        <strong>{escape(warehouse_six_message)}</strong>
      </div>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_HEADER}">Warehouse</th><th style="{_HEADER}">MTD maximum demand</th>
          <th style="{_HEADER}">110 kVA threshold</th><th style="{_HEADER}">Status</th>
          <th style="{_HEADER}">MTD Peak energy</th>
          <th style="{_HEADER}">Estimated MTD 25% surcharge</th>
        </tr></thead>
        <tbody>{monthly_rows}</tbody>
      </table>
      <p style="margin:9px 0 0;color:#667085;font-size:12px;">
        This is not the total electricity bill or the Peak-hour energy cost. The estimated MTD surcharge is
        25% of month-to-date CTOU energy charges plus the monthly demand charge, then VAT. When the measured
        demand is below 110 kVA, the amount is shown only as a conditional estimate if UMFA's billed demand
        register still triggers the surcharge, using 110 kVA as the minimum threshold assumption. Service
        charges are excluded.
        PNPSCADA profile kVA is an operational estimate and must be reconciled with UMFA's billed demand register.
        Solar-credit reassignment from Warehouse 8 to Warehouse 6 is not assumed; it remains an UMFA billing action
        requiring confirmation and backdating.
      </p>

      <div style="margin-bottom:18px;padding:14px 16px;background:#fff8e7;border-left:4px solid #f0a000;">
        <strong>{escape(management_summary)}</strong><br>
        <span style="color:#475467;">This report measures kWh consumed inside CTOU Peak time bands. It does not
        use the highest instantaneous load outside those bands.</span>
      </div>

      <h2 style="{_HEADING}">{'Latest applicable' if using_prior_peak_day else 'Previous-day'} CTOU Peak-hours performance — {peak_analysis_date:%d %b %Y}</h2>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_HEADER}">Warehouse</th><th style="{_HEADER}">Peak-band energy</th>
          <th style="{_HEADER}">Share of daily energy</th><th style="{_HEADER}">Rate applied</th>
          <th style="{_HEADER}">Base Peak energy charge</th>
          <th style="{_HEADER}">Matching-weekday baseline</th><th style="{_HEADER}">Variance</th>
          <th style="{_HEADER}">Status</th>
        </tr></thead>
        <tbody>{analysis_rows}</tbody>
      </table>
      <p style="margin:9px 0 0;color:#667085;font-size:12px;">
        Base charge formula: Peak-band kWh x the applicable VAT-inclusive municipal Peak energy rate.
        It excludes demand, service and the conditional 25% network surcharge.
        <br>
        Highest load inside a Peak interval: {escape(str(highest.get('area') or 'Warehouse'))}
        {_number(highest.get('highest_peak_kw')):,.1f} kW at
        {escape(str(highest.get('highest_peak_time') or 'time unavailable'))}.
        Warehouse 9 is excluded because it uses the Scale 1 tariff; solar is generation, not warehouse consumption.
      </p>

      <h2 style="{_HEADING}">Electricity used during CTOU Peak time bands — {peak_analysis_date:%d %b %Y}</h2>
      <p style="margin:0 0 12px;color:#475467;font-size:13px;">
        This table includes only electricity used during the municipality's CTOU Peak hours; Standard and
        Off-peak consumption is excluded. Consecutive half-hour readings are grouped into the applicable
        municipal Peak time band, such as <strong>07:00-09:00</strong> or <strong>18:00-21:00</strong>.
        Rows are ranked from highest to lowest Peak-band kWh within each warehouse. The stretch target is
        <strong>0 kWh for flexible loads</strong> in every listed time band. Essential refrigeration, safety,
        security and operational base loads must first be identified and treated as the documented minimum,
        not switched off indiscriminately.
      </p>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_HEADER}">Warehouse</th><th style="{_HEADER}">Rank</th>
          <th style="{_HEADER}">CTOU Peak time band</th>
          <th style="{_HEADER}">Electricity used during Peak hours</th>
          <th style="{_HEADER}">Average load</th><th style="{_HEADER}">Peak rate</th>
          <th style="{_HEADER}">Peak energy cost</th>
          <th style="{_HEADER}">Flexible-load target</th>
        </tr></thead>
        <tbody>{interval_rows}</tbody>
      </table>
      <p style="margin:9px 0 0;color:#667085;font-size:12px;">
        Peak energy cost = electricity used during the Peak time band x the applicable VAT-inclusive Peak rate.
        Amounts are base energy charges before any applicable 25% network surcharge.
      </p>

      <h2 style="{_HEADING}">Management interpretation and instruction</h2>
      <div style="padding:16px 18px;background:#fff8e7;border-left:4px solid #f0a000;color:#344054;">
        <ol style="margin:0;padding-left:20px;line-height:1.6;">
          {action_items}
          <li>Identify charging, refrigeration defrost, pumping and other flexible processes that can be moved
          into Standard or Off-peak periods without affecting safety or operations.</li>
          <li>Start with each warehouse's highest-ranked Peak time band, assign the load owner, and reduce
          flexible consumption toward 0 kWh before moving to the next band.</li>
          <li>Record the confirmed cause, responsible owner and agreed load-shifting action.</li>
        </ol>
        <p style="margin:12px 0 0;"><strong>Indicative opportunity:</strong> R{total_saving:,.2f} per day if only
        the flagged excess above baseline can be shifted from Peak to Off-peak. This is a tariff-energy estimate,
        not a guaranteed bill saving.</p>
      </div>

      <h2 style="{_HEADING}">CTOU time-band reference</h2>
      <p style="margin:0 0 12px;color:#475467;font-size:13px;">
        Energy is classified by the half-hour in which it was used. Public holidays follow the municipality's
        published Saturday or Sunday profile.
      </p>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_HEADER}">Season</th><th style="{_HEADER}">Day type</th>
          <th style="{_HEADER}">Peak</th><th style="{_HEADER}">Standard</th>
          <th style="{_HEADER}">Off-peak</th>
        </tr></thead>
        <tbody>{tariff_rows}</tbody>
      </table>
      <p style="margin:10px 0 0;color:#344054;font-size:13px;"><strong>{rate_summary}</strong></p>
      <p style="margin:8px 0 0;color:#667085;font-size:12px;">
        <span style="color:{tariff_badge_colour};font-weight:700;">Official tariff check: {escape(tariff_status.title())}</span>
        &nbsp;|&nbsp; Checked {tariff_checked_at.astimezone():%d %b %Y %H:%M}
        &nbsp;|&nbsp; {escape(tariff_message)} &nbsp;|&nbsp; Source: {source_link}
      </p>

      <p style="margin:0;color:#667085;font-size:12px;line-height:1.5;">
        The estimated bill uses the app's Cost Centre model for all warehouse meters: energy, demand, service,
        VAT, modeled network surcharge and matched solar savings. Projected month-end usage is
        {_number(monthly_cost_outlook.get('projected_month_end_kwh')):,.1f} kWh. It excludes landlord or UMFA
        adjustments, bill-only meters, penalties not present in the official tariff model, and any unconfirmed
        solar-credit reassignment between Warehouse 8 and Warehouse 6.
      </p>

      <p style="margin:22px 0 0;padding-top:16px;border-top:1px solid #eaecf0;color:#667085;font-size:12px;line-height:1.5;">
        Meter readings verify when and by how much load changed. They do not establish the physical cause;
        operational records are required for confirmation. The 25% network surcharge applies to monthly energy
        plus demand only when monthly peak demand reaches 110 kVA; it is not silently added to these daily
        base-energy figures.
      </p>
    </div>
  </div>
</body></html>'''
    return MailDraft(
        subject=subject,
        body=body,
        to_recipients=recipients,
        source='Autonomous rule - daily complete peak usage',
        alert_id=f'daily-peak-{alert_date.isoformat()}',
        body_content_type='HTML',
    ).validated()


_TABLE = 'width:100%;border-collapse:collapse;border:1px solid #d0d5dd;font-size:13px;'
_HEADER = 'padding:10px 9px;background:#1e2a4a;color:#ffffff;text-align:left;border:1px solid #344264;'
_CELL = 'padding:9px;border:1px solid #d0d5dd;color:#344054;vertical-align:top;'
_HEADING = 'margin:24px 0 12px;font-size:17px;color:#17233f;'
_CTOU_TIME_BANDS = (
    ('Low Sep-May', 'Weekday', '07:00-09:00; 18:00-21:00', '06:00-07:00; 09:00-18:00; 21:00-22:00', '22:00-06:00'),
    ('Low Sep-May', 'Saturday', 'None', '07:00-12:00; 18:00-20:00', 'All other hours'),
    ('Low Sep-May', 'Sunday', 'None', '18:00-20:00', 'All other hours'),
    ('High Jun-Aug', 'Weekday', '06:00-08:00; 17:00-20:00', '08:00-17:00; 20:00-22:00', '22:00-06:00'),
    ('High Jun-Aug', 'Saturday', 'None', '07:00-12:00; 17:00-19:00', 'All other hours'),
    ('High Jun-Aug', 'Sunday', 'None', 'None', 'All hours'),
)


def _ctou_analysis_html_row(row: dict[str, object]) -> str:
    baseline = row.get('baseline_peak_kwh')
    increase = row.get('increase_kwh')
    increase_percent = row.get('increase_percent')
    assessed = baseline is not None
    action_required = bool(row.get('action_required'))
    status = 'Action required' if action_required else 'Within baseline' if assessed else 'Not assessed'
    colour = '#b42318' if action_required else '#027a48' if assessed else '#475467'
    baseline_text = (
        f'{_number(baseline):,.1f} kWh<br><span style="color:#667085;">'
        f'{int(_number(row.get("baseline_days")))} comparable days</span>'
        if assessed else 'Insufficient history'
    )
    variance_text = (
        f'{_number(increase):+,.1f} kWh<br><span style="color:#667085;">'
        f'{_number(increase_percent):+,.1f}%</span>'
        if increase is not None and increase_percent is not None else 'Not assessed'
    )
    return (
        f'<tr><td style="{_CELL}"><strong>{escape(str(row.get("area") or "Meter area"))}</strong></td>'
        f'<td style="{_CELL}"><strong>{_number(row.get("peak_kwh")):,.1f} kWh</strong></td>'
        f'<td style="{_CELL}">{_number(row.get("peak_share_percent")):,.1f}%</td>'
        f'<td style="{_CELL}">R{_number(row.get("peak_rate_inc_vat")):.4f}/kWh<br>'
        f'<span style="color:#667085;">VAT inclusive</span></td>'
        f'<td style="{_CELL}">R{_number(row.get("peak_energy_charge_inc_vat")):,.2f}<br>'
        f'<span style="color:#667085;">before surcharge</span></td>'
        f'<td style="{_CELL}">{baseline_text}</td>'
        f'<td style="{_CELL}">{variance_text}</td>'
        f'<td style="{_CELL};color:{colour};font-weight:700;">{status}</td></tr>'
    )


def _monthly_surcharge_html_row(row: dict[str, object]) -> str:
    active = bool(row.get('surcharge_active'))
    variance = _number(row.get('threshold_variance_kva'))
    status = (
        f'Above by {variance:,.1f} kVA'
        if active else f'Below by {abs(variance):,.1f} kVA'
    )
    colour = '#b42318' if active else '#027a48'
    return (
        f'<tr><td style="{_CELL}"><strong>{escape(str(row.get("area") or "Warehouse"))}</strong></td>'
        f'<td style="{_CELL}"><strong>{_number(row.get("maximum_kva")):,.1f} kVA</strong><br>'
        f'<span style="color:#667085;">{escape(str(row.get("maximum_kva_time") or "No complete reading"))}</span></td>'
        f'<td style="{_CELL}">{_number(row.get("threshold_kva")):,.0f} kVA</td>'
        f'<td style="{_CELL};color:{colour};font-weight:700;">{escape(status)}</td>'
        f'<td style="{_CELL}">{_number(row.get("peak_kwh")):,.1f} kWh</td>'
        f'<td style="{_CELL}"><strong>R{_number(row.get("estimated_surcharge_inc_vat")):,.2f}</strong><br>'
        f'<span style="color:#667085;">{"Estimated active surcharge" if active else "If UMFA threshold applies"}'
        f'<br>Through {escape(str(row.get("through_date") or ""))}; '
        f'{int(_number(row.get("complete_days")))} complete days</span></td></tr>'
    )


def _ctou_window_html_row(
    area: str,
    window: dict[str, object],
    rank: int,
) -> str:
    kwh = _number(window.get('kwh'))
    charge = _number(window.get('energy_charge_inc_vat'))
    return (
        f'<tr><td style="{_CELL}"><strong>{escape(area)}</strong></td>'
        f'<td style="{_CELL}">#{rank}</td>'
        f'<td style="{_CELL}"><strong>{escape(str(window.get("start_time") or ""))}-'
        f'{escape(str(window.get("end_time") or ""))}</strong></td>'
        f'<td style="{_CELL}">{kwh:,.1f} kWh</td>'
        f'<td style="{_CELL}">{_number(window.get("average_kw")):,.1f} kW</td>'
        f'<td style="{_CELL}">R{_number(window.get("rate_inc_vat")):.4f}/kWh<br>'
        f'<span style="color:#667085;">VAT inclusive</span></td>'
        f'<td style="{_CELL}"><strong>R{charge:,.2f}</strong><br>'
        f'<span style="color:#667085;">before surcharge</span></td>'
        f'<td style="{_CELL};color:#b42318;font-weight:700;">0 kWh flexible load<br>'
        f'<span style="color:#667085;font-weight:400;">Gap: {kwh:,.1f} kWh before essential-base adjustment</span></td></tr>'
    )


def compose_refresh_complete_email(
    *,
    recipient: str,
    completed_at: datetime,
    inserted_readings: int,
    refreshed_readings: int,
    latest_closed_date: date,
    duration_seconds: float,
) -> MailDraft:
    """Build the deterministic message for the successful-refresh automation rule."""
    local_time = completed_at.astimezone()
    timestamp = local_time.strftime('%d %b %Y %H:%M')
    subject = f'[Electricity Tool] Meter data refresh completed - {timestamp}'
    body = '\n'.join((
        'Hello Christopher,',
        '',
        'The Sydney Road Electricity Monitor completed a PNPSCADA meter-data refresh successfully.',
        '',
        'Refresh summary',
        f'- Completed: {timestamp}',
        f'- New half-hour readings stored: {max(0, int(inserted_readings)):,}',
        f'- Existing half-hour readings refreshed: {max(0, int(refreshed_readings)):,}',
        f'- Analytics available through: {latest_closed_date:%d %b %Y}',
        f'- Processing time: {max(0.0, float(duration_seconds)):.0f} seconds',
        '',
        'No action is required. This confirmation was sent automatically because the meter-data '
        'refresh completed successfully. Failed refreshes do not trigger this message.',
        '',
        'Kind regards,',
        'Sydney Road Electricity Monitor',
        'Autonomous refresh notification',
    ))
    return MailDraft(
        subject=subject,
        body=body,
        to_recipients=(recipient,),
        source='Autonomous rule - successful meter refresh',
        alert_id=f'data-refresh-{local_time.isoformat(timespec="seconds")}',
    ).validated()


def parse_copilot_email(text: str, fallback: MailDraft) -> MailDraft:
    cleaned = str(text).replace('\r\n', '\n').strip()
    cleaned = re.sub(r'^```(?:text|markdown)?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()
    subject = fallback.subject
    body = cleaned
    subject_match = re.search(
        r'(?im)^\s*(?:#{1,4}\s*)?(?:\*\*)?subject(?:\*\*)?\s*:\s*(.+?)\s*$',
        cleaned,
    )
    if subject_match:
        subject = _strip_markdown(subject_match.group(1))[:255]
        body = (cleaned[:subject_match.start()] + cleaned[subject_match.end():]).strip()
    body = re.sub(
        r'(?im)^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:body|email body)(?:\*\*)?\s*:\s*',
        '',
        body,
        count=1,
    ).strip()
    body = _strip_markdown(body)
    if len(body) < 80:
        return fallback
    return MailDraft(
        subject=subject or fallback.subject,
        body=body,
        to_recipients=fallback.to_recipients,
        cc_recipients=fallback.cc_recipients,
        sender_mailbox=fallback.sender_mailbox,
        source='Microsoft Copilot · grounded in app data',
        alert_id=fallback.alert_id,
        attachment_paths=fallback.attachment_paths,
    )


def audit_entry(draft: MailDraft, result: MailActionResult) -> MailAuditEntry:
    prepared = draft.validated()
    return MailAuditEntry(
        completed_at=result.completed_at,
        action=result.action,
        subject=prepared.subject,
        to_recipients=prepared.to_recipients,
        cc_recipients=prepared.cc_recipients,
        sender_mailbox=prepared.sender_mailbox,
        alert_id=prepared.alert_id,
        source=prepared.source,
        attachment_names=tuple(Path(value).name for value in prepared.attachment_paths),
    )


def _optional_email(value: object) -> str:
    email = str(value or '').strip().casefold()
    if email and not EMAIL_PATTERN.fullmatch(email):
        raise MailAgentError(f'The sender mailbox is not a valid email address: {email}')
    return email


def _strip_markdown(value: str) -> str:
    text = str(value)
    text = re.sub(r'(?m)^#{1,6}\s*', '', text)
    text = text.replace('**', '').replace('__', '').replace('`', '')
    text = re.sub(r'(?m)^\s*[-*]\s+', '- ', text)
    return text.strip()


def _content_type(path: Path) -> str:
    return {
        '.pdf': 'application/pdf',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.csv': 'text/csv',
        '.txt': 'text/plain',
    }.get(path.suffix.casefold(), 'application/octet-stream')


def _number(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
