from __future__ import annotations

import base64
import binascii
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from threading import Event, RLock
from typing import Iterable

import truststore

# Requests normally ships its own CA bundle. Company-managed Windows devices
# can also use a corporate TLS root, so Microsoft sign-in should follow the
# same Windows trust store as the employee's browser.
try:  # pragma: no cover - depends on the host certificate provider
    truststore.inject_into_ssl()
except Exception:
    pass

import msal
from msal_extensions import PersistedTokenCache, build_encrypted_persistence
import requests

from .config import (
    DEFAULT_MICROSOFT_TOKEN_CACHE,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_TENANT_ID,
)
from .dashboard_data import (
    ALL_AREAS,
    SOLAR_AREA,
    DashboardDataset,
    build_supply_demand_balance,
)
from .solar_investment import SOLAR_PROPOSALS, current_solar_investment_metrics
from .tariffs import (
    AREA_TARIFF_ASSIGNMENTS,
    BUILT_IN_CTOU_RATES,
    BUILT_IN_RATES,
    SOLAR_CREDIT_AREA,
    CtouTariffRate,
    TariffRate,
    build_cost_analysis,
    ctou_tariff_for_day,
    tou_band,
)


COPILOT_SCOPES: tuple[str, ...] = (
    'User.Read',
    'Sites.Read.All',
    'Mail.Read',
    'Mail.ReadWrite',
    'Mail.ReadWrite.Shared',
    'Mail.Send',
    'Mail.Send.Shared',
    'People.Read.All',
    'OnlineMeetingTranscript.Read.All',
    'Chat.Read',
    'ChannelMessage.Read.All',
    'ExternalItem.Read.All',
)
MAIL_AGENT_SCOPES: tuple[str, ...] = (
    'Mail.ReadWrite',
    'Mail.ReadWrite.Shared',
    'Mail.Send',
    'Mail.Send.Shared',
    'People.Read.All',
)
COPILOT_API_ROOT = 'https://graph.microsoft.com/beta/copilot'
WAREHOUSE_AREAS = ('Warehouse 6', 'Warehouse 7', 'Warehouse 8', 'Warehouse 9')
CTOU_AREAS = {'Warehouse 6', 'Warehouse 7', 'Warehouse 8'}
SEVERITY_ORDER = {'Critical': 0, 'High': 1, 'Watch': 2}


class CopilotConnectionError(RuntimeError):
    """A user-facing Microsoft authentication or Copilot API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class MicrosoftAccountStatus:
    signed_in: bool
    username: str = ''
    display_name: str = ''
    cache_encrypted: bool = True
    warning: str = ''
    token_available: bool = False
    granted_scopes: tuple[str, ...] = ()
    missing_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MicrosoftDeviceSignIn:
    user_code: str
    verification_uri: str
    message: str
    expires_in: int
    flow: dict[str, object] = field(repr=False)


@dataclass(frozen=True)
class AiEvidence:
    allowed: bool
    intent: str
    question: str
    start_date: date
    end_date: date
    area: str
    facts: dict[str, object]
    local_answer: str
    limitations: tuple[str, ...]
    actions: tuple[str, ...]
    clarification: str = ''

    def context_text(self) -> str:
        """Return verified facts plus explicit analytical freedom for Copilot."""
        payload = {
            'source': 'Connect Logistics local PNPSCADA analytical database',
            'question_scope': {
                'from': self.start_date.isoformat(),
                'to': self.end_date.isoformat(),
                'area': self.area,
                'intent': self.intent,
            },
            'verified_facts': self.facts,
            'app_calculated_reference': self.local_answer,
            'limitations': list(self.limitations),
            'recommended_actions': list(self.actions),
            'analysis_permissions': [
                'Calculate and derive additional metrics from the verified facts.',
                'Connect patterns across dates, areas, kWh, kW, kVA, tariffs, costs and solar.',
                'Rank plausible operational explanations while clearly labelling them as hypotheses.',
                'Apply established energy-management principles to recommendations and scenarios.',
                'Challenge a simplistic conclusion when the detailed evidence supports a better one.',
            ],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class CopilotReply:
    text: str
    conversation_id: str


class MicrosoftCopilotAuth:
    """Microsoft public-client authentication with an encrypted per-user cache."""

    def __init__(
        self,
        client_id: str = MICROSOFT_CLIENT_ID,
        tenant_id: str = MICROSOFT_TENANT_ID,
        cache_path: str | Path = DEFAULT_MICROSOFT_TOKEN_CACHE,
    ) -> None:
        self.client_id = client_id
        self.tenant_id = tenant_id
        self.cache_path = Path(cache_path).expanduser().resolve()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._device_sign_in_active = Event()
        self._cache_encrypted = True
        self._cache_warning = ''
        try:
            persistence = build_encrypted_persistence(str(self.cache_path))
            token_cache: msal.TokenCache = PersistedTokenCache(persistence)
        except Exception as exc:  # pragma: no cover - platform fallback
            # The installed Windows build uses DPAPI. An in-memory fallback keeps
            # development environments functional without writing plain tokens.
            token_cache = msal.SerializableTokenCache()
            self._cache_encrypted = False
            self._cache_warning = f'The Microsoft session will not persist: {exc}'
        self._token_cache = token_cache
        self._app: msal.PublicClientApplication | None = None
        self._initialization_error = ''

    def _ensure_app(self) -> msal.PublicClientApplication:
        if self._app is not None:
            return self._app
        try:
            self._app = msal.PublicClientApplication(
                self.client_id,
                authority=f'https://login.microsoftonline.com/{self.tenant_id}',
                token_cache=self._token_cache,
            )
            self._initialization_error = ''
            return self._app
        except Exception as exc:
            self._initialization_error = (
                'Microsoft organisational sign-in could not be initialized. '
                'Check the internet connection and company network certificate settings.'
            )
            raise CopilotConnectionError(self._initialization_error) from exc

    def status(self) -> MicrosoftAccountStatus:
        if self._device_sign_in_active.is_set():
            return MicrosoftAccountStatus(
                False,
                cache_encrypted=self._cache_encrypted,
                warning='Microsoft sign-in is being completed in the browser.',
            )
        if self._app is None and not self.cache_path.exists():
            return MicrosoftAccountStatus(
                False,
                cache_encrypted=self._cache_encrypted,
                warning=self._cache_warning or self._initialization_error,
            )
        try:
            app = self._ensure_app()
        except CopilotConnectionError:
            return MicrosoftAccountStatus(
                False,
                cache_encrypted=self._cache_encrypted,
                warning=self._cache_warning or self._initialization_error,
            )
        with self._lock:
            accounts = app.get_accounts()
        if not accounts:
            return MicrosoftAccountStatus(
                False,
                cache_encrypted=self._cache_encrypted,
                warning=self._cache_warning,
            )
        account = accounts[0]
        with self._lock:
            token_result = app.acquire_token_silent_with_error(
                list(COPILOT_SCOPES), account=account
            )
        access_token = (
            str(token_result.get('access_token') or '')
            if isinstance(token_result, dict) else ''
        )
        granted_scopes = _access_token_scopes(access_token)
        granted_lookup = {scope.casefold() for scope in granted_scopes}
        missing_scopes = tuple(
            scope for scope in COPILOT_SCOPES if scope.casefold() not in granted_lookup
        )
        return MicrosoftAccountStatus(
            True,
            username=str(account.get('username') or ''),
            display_name=str(account.get('name') or account.get('username') or ''),
            cache_encrypted=self._cache_encrypted,
            warning=self._cache_warning,
            token_available=bool(access_token),
            granted_scopes=granted_scopes,
            missing_scopes=missing_scopes,
        )

    def sign_in(self) -> MicrosoftAccountStatus:
        app = self._ensure_app()
        with self._lock:
            result = app.acquire_token_interactive(
                scopes=list(COPILOT_SCOPES),
                timeout=300,
                prompt='select_account',
            )
        return self._complete_token_result(result)

    def begin_device_sign_in(self) -> MicrosoftDeviceSignIn:
        app = self._ensure_app()
        with self._lock:
            flow = app.initiate_device_flow(scopes=list(COPILOT_SCOPES))
        if not isinstance(flow, dict) or 'user_code' not in flow:
            detail = (
                str(flow.get('error_description') or flow.get('error') or '')
                if isinstance(flow, dict) else ''
            )
            raise CopilotConnectionError(
                _clean_microsoft_error(
                    detail or 'Microsoft could not create a device sign-in code.'
                )
            )
        return MicrosoftDeviceSignIn(
            user_code=str(flow.get('user_code') or ''),
            verification_uri=str(
                flow.get('verification_uri')
                or flow.get('verification_url')
                or 'https://microsoft.com/devicelogin'
            ),
            message=str(flow.get('message') or ''),
            expires_in=int(flow.get('expires_in') or 900),
            flow=dict(flow),
        )

    def complete_device_sign_in(
        self,
        device_sign_in: MicrosoftDeviceSignIn,
    ) -> MicrosoftAccountStatus:
        app = self._ensure_app()
        self._device_sign_in_active.set()
        try:
            result = app.acquire_token_by_device_flow(device_sign_in.flow)
        finally:
            self._device_sign_in_active.clear()
        return self._complete_token_result(result)

    def _complete_token_result(self, result: object) -> MicrosoftAccountStatus:
        if not isinstance(result, dict):
            raise CopilotConnectionError('Microsoft sign-in returned an invalid response.')
        if 'access_token' not in result:
            detail = str(
                result.get('error_description')
                or result.get('error')
                or 'Microsoft sign-in did not return an access token.'
            )
            if 'AADSTS65001' in detail or 'consent' in detail.casefold():
                raise CopilotConnectionError(
                    'Microsoft sign-in is waiting for Connect Logistics administrator consent.'
                )
            raise CopilotConnectionError(_clean_microsoft_error(detail))
        granted_scopes = _access_token_scopes(str(result.get('access_token') or ''))
        granted_lookup = {scope.casefold() for scope in granted_scopes}
        missing_scopes = tuple(
            scope for scope in COPILOT_SCOPES if scope.casefold() not in granted_lookup
        )
        if missing_scopes:
            raise CopilotConnectionError(
                'Microsoft sign-in completed, but the renewed token is missing: '
                f"{', '.join(missing_scopes)}. Sign out, then reconnect after the "
                'Entra permission status shows Granted.'
            )
        return self.status()

    def access_token(self) -> str | None:
        if self._device_sign_in_active.is_set():
            return None
        if self._app is None and not self.cache_path.exists():
            return None
        try:
            app = self._ensure_app()
        except CopilotConnectionError:
            return None
        with self._lock:
            accounts = app.get_accounts()
            if not accounts:
                return None
            result = app.acquire_token_silent_with_error(
                list(COPILOT_SCOPES), account=accounts[0]
            )
        if result and 'access_token' in result:
            return str(result['access_token'])
        return None

    def sign_out(self) -> None:
        if self._app is None and not self.cache_path.exists():
            return
        app = self._ensure_app()
        with self._lock:
            for account in app.get_accounts():
                app.remove_account(account)


class CopilotChatClient:
    """Small REST client for the Microsoft 365 Copilot Chat API preview."""

    def __init__(
        self,
        api_root: str = COPILOT_API_ROOT,
        session: requests.Session | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        self.api_root = api_root.rstrip('/')
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def ask(
        self,
        access_token: str,
        evidence: AiEvidence,
        conversation_id: str | None = None,
    ) -> CopilotReply:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }
        if not conversation_id:
            created = self._post(f'{self.api_root}/conversations', headers, {})
            conversation_id = str(created.get('id') or '')
            if not conversation_id:
                raise CopilotConnectionError(
                    'Microsoft Copilot did not create a conversation.'
                )

        payload = {
            'message': {
                '@odata.type': '#microsoft.graph.copilotConversationRequestMessageParameter',
                'text': _copilot_instruction(evidence),
            },
            'additionalContext': [{
                '@odata.type': '#microsoft.graph.copilotContextMessage',
                'description': (
                    'Verified local PNPSCADA electricity evidence for this question. The '
                    'numeric facts are authoritative; the app-calculated reference is a '
                    'starting point, not a restriction on deeper analysis or reasoning.'
                ),
                'text': evidence.context_text(),
            }],
            'locationHint': {
                '@odata.type': '#microsoft.graph.copilotConversationLocation',
                'timeZone': 'Africa/Johannesburg',
            },
            'contextualResources': {
                '@odata.type': '#microsoft.graph.copilotContextualResources',
                'webContext': {
                    '@odata.type': '#microsoft.graph.copilotWebContext',
                    'isWebEnabled': False,
                },
            },
        }
        response = self._post(
            f'{self.api_root}/conversations/{conversation_id}/chat',
            headers,
            payload,
        )
        messages = response.get('messages')
        if not isinstance(messages, list):
            messages = []
        reply_text = ''
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            candidate = str(message.get('text') or '').strip()
            if candidate and candidate != evidence.question.strip():
                reply_text = candidate
                break
        if not reply_text:
            raise CopilotConnectionError('Microsoft Copilot returned an empty response.')
        return CopilotReply(reply_text, conversation_id)

    def _post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            response = self.session.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise CopilotConnectionError(
                'Microsoft Copilot could not be reached. Check the internet connection and try again.'
            ) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get('error') if isinstance(body, dict) else None
            detail = ''
            if isinstance(error, dict):
                detail = str(error.get('message') or error.get('code') or '')
            if response.status_code in {401, 403}:
                message = (
                    'Microsoft access is not approved yet, or the signed-in account does not have '
                    'the required Microsoft 365 Copilot licence. Ask the administrator to complete '
                    'consent, then sign in again.'
                )
            elif response.status_code == 429:
                message = 'Microsoft Copilot is temporarily busy. Please wait and try again.'
            else:
                message = detail or f'Microsoft Copilot returned error {response.status_code}.'
            raise CopilotConnectionError(
                _clean_microsoft_error(message), status_code=response.status_code
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise CopilotConnectionError(
                'Microsoft Copilot returned an unreadable response.'
            ) from exc
        return result if isinstance(result, dict) else {}


def _access_token_scopes(access_token: str) -> tuple[str, ...]:
    """Read delegated scope names from a Graph JWT without logging the token."""
    token = str(access_token or '').strip()
    parts = token.split('.')
    if len(parts) < 2:
        return ()
    encoded = parts[1] + ('=' * (-len(parts[1]) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode('ascii')).decode('utf-8'))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        return ()
    if not isinstance(payload, dict):
        return ()
    scopes = str(payload.get('scp') or '').split()
    return tuple(dict.fromkeys(scope for scope in scopes if scope))


def build_ai_evidence(
    dataset: DashboardDataset,
    question: str,
    active_start: date,
    active_end: date,
    active_area: str,
    rates: Iterable[TariffRate] = BUILT_IN_RATES,
    ctou_rates: Iterable[CtouTariffRate] = BUILT_IN_CTOU_RATES,
    prior_question: str = '',
) -> AiEvidence:
    """Calculate a bounded evidence pack from the local analytical dataset."""
    cleaned_question = ' '.join(str(question).split()).strip()
    intent = _question_intent(cleaned_question, prior_question)
    area = _question_area(cleaned_question, active_area, dataset, intent)
    start_date, end_date = _question_dates(
        dataset, cleaned_question, active_start, active_end, area
    )
    if intent == 'out_of_scope':
        answer = (
            'I can only help with this application\'s electricity readings, warehouse usage, '
            'kW and kVA demand, solar generation, tariffs, costs, comparisons and unusual usage.'
        )
        return AiEvidence(
            False,
            intent,
            cleaned_question,
            start_date,
            end_date,
            area,
            {},
            answer,
            ('The question falls outside the Connect Logistics electricity-data scope.',),
            (),
        )

    selected_areas = _selected_areas(dataset, area)
    daily_rows = [
        row for row in dataset.daily
        if start_date <= _as_date(row.get('date')) <= end_date
        and str(row.get('area')) in selected_areas
    ]
    complete_daily = [
        row for row in daily_rows if row.get('data_quality_status') == 'Complete'
    ]
    excluded_rows = len(daily_rows) - len(complete_daily)
    complete_dates = {_as_date(row.get('date')) for row in complete_daily}
    if _needs_current_week_clarification(
        cleaned_question,
        start_date,
        end_date,
        complete_dates,
    ):
        day_word = 'day' if len(complete_dates) == 1 else 'days'
        clarification = (
            f'The current week only contains {len(complete_dates)} complete {day_word} '
            f'({start_date:%d %b %Y} to {end_date:%d %b %Y}). Should I analyse '
            '**the partial current week** or **the last complete Monday-to-Sunday week**?'
        )
        return AiEvidence(
            True,
            intent,
            cleaned_question,
            start_date,
            end_date,
            area,
            {
                'data_period': {
                    'from': start_date.isoformat(),
                    'to': end_date.isoformat(),
                },
                'meter_area': area,
                'complete_calendar_days': len(complete_dates),
            },
            clarification,
            ('The current calendar week is still materially incomplete.',),
            (),
            clarification,
        )
    complete_keys = {
        (str(row.get('area')), _as_date(row.get('date'))) for row in complete_daily
    }
    intervals = [
        row for row in dataset.intervals
        if (str(row.get('area')), _as_date(row.get('date'))) in complete_keys
    ]
    spikes = [
        row for row in dataset.spikes
        if start_date <= _as_date(row.get('date')) <= end_date
        and str(row.get('area')) in selected_areas
        and row.get('data_quality_status') == 'Complete'
    ]
    spikes.sort(
        key=lambda row: (
            SEVERITY_ORDER.get(str(row.get('alert_level')), 9),
            -_as_date(row.get('date')).toordinal(),
        )
    )

    facts = _verified_facts(
        dataset,
        complete_daily,
        intervals,
        spikes,
        start_date,
        end_date,
        area,
        tuple(rates),
        tuple(ctou_rates),
        excluded_rows,
        intent,
    )
    limitations: list[str] = []
    if excluded_rows:
        limitations.append(
            f'{excluded_rows} incomplete meter-day record(s) were excluded from factual totals.'
        )
    if not complete_daily:
        limitations.append('No complete meter-day readings are available for this scope.')
    limitations.append(
        'Meter readings show when, where and by how much usage changed; operational records are required to confirm the physical cause.'
    )
    actions = _recommended_actions(facts, spikes, intent)
    answer = _local_answer(intent, facts, spikes, limitations, actions)
    ai_question = cleaned_question
    if prior_question and _is_conversational_follow_up(cleaned_question):
        ai_question = (
            f'Previous electricity question: {prior_question}\n'
            f'Current follow-up: {cleaned_question}'
        )
        facts['conversation_context'] = {
            'previous_user_question': prior_question,
            'current_follow_up': cleaned_question,
        }
    return AiEvidence(
        True,
        intent,
        ai_question,
        start_date,
        end_date,
        area,
        facts,
        answer,
        tuple(limitations),
        actions,
    )


def ai_database_scope(dataset: DashboardDataset) -> tuple[date, date, str]:
    """Return the AI Hub's filter-independent default analytical scope."""
    latest = (
        _latest_complete_date(dataset, ALL_AREAS)
        or min(dataset.last_date, date.today() - timedelta(days=1))
    )
    return dataset.first_date, max(dataset.first_date, latest), ALL_AREAS


def is_contextual_ai_follow_up(question: str) -> bool:
    """Return whether a chat turn should retain the preceding AI-only scope."""
    return _is_conversational_follow_up(question)


def _verified_facts(
    dataset: DashboardDataset,
    daily: list[dict[str, str]],
    intervals: list[dict[str, str]],
    spikes: list[dict[str, str]],
    start_date: date,
    end_date: date,
    area: str,
    rates: tuple[TariffRate, ...],
    ctou_rates: tuple[CtouTariffRate, ...],
    excluded_rows: int,
    intent: str,
) -> dict[str, object]:
    total_kwh = sum(_number(row.get('import_kwh')) for row in daily)
    dates = {_as_date(row.get('date')) for row in daily}
    top_daily = max(daily, key=lambda row: _number(row.get('import_kwh')), default=None)
    top_kw = max(daily, key=lambda row: _number(row.get('peak_kw')), default=None)
    top_kva = max(daily, key=lambda row: _number(row.get('peak_kva')), default=None)
    by_area: dict[str, float] = defaultdict(float)
    for row in daily:
        by_area[str(row.get('area'))] += _number(row.get('import_kwh'))
    area_totals = [
        {
            'area': area_name,
            'kwh': round(value, 3),
            'share_percent': round(value / total_kwh * 100.0, 2) if total_kwh else 0.0,
        }
        for area_name, value in sorted(by_area.items(), key=lambda item: item[1], reverse=True)
    ]

    bands = {'peak': 0.0, 'standard': 0.0, 'off_peak': 0.0}
    peak_shift_saving_10_percent = 0.0
    top_interval = None
    half_hour_profile: dict[tuple[str, str, str], dict[str, object]] = defaultdict(
        lambda: {
            'samples': 0,
            'kw_total': 0.0,
            'maximum_kw': 0.0,
            'maximum_kva': 0.0,
            'kwh': 0.0,
            'band_kwh': defaultdict(float),
        }
    )
    for row in intervals:
        row_area = str(row.get('area'))
        timestamp = _as_datetime(row.get('timestamp'))
        if top_interval is None or _number(row.get('kw_import')) > _number(top_interval.get('kw_import')):
            top_interval = row
        # PNPSCADA labels each half-hour with its end time. Tariff assignment
        # therefore uses the preceding 30-minute interval, matching the cost engine.
        effective_time = timestamp - timedelta(minutes=30)
        interval_end = effective_time + timedelta(minutes=30)
        profile_area = (
            row_area
            if area != ALL_AREAS
            else 'CTOU warehouses combined'
            if row_area in CTOU_AREAS
            else row_area
        )
        profile = half_hour_profile[(
            profile_area,
            effective_time.strftime('%H:%M'),
            interval_end.strftime('%H:%M'),
        )]
        profile['samples'] = int(profile['samples']) + 1
        profile['kw_total'] = float(profile['kw_total']) + _number(row.get('kw_import'))
        profile['maximum_kw'] = max(float(profile['maximum_kw']), _number(row.get('kw_import')))
        profile['maximum_kva'] = max(float(profile['maximum_kva']), _number(row.get('kva')))
        profile['kwh'] = float(profile['kwh']) + max(_number(row.get('import_kwh')), 0.0)
        if row_area not in CTOU_AREAS:
            band_kwh = profile['band_kwh']
            if isinstance(band_kwh, defaultdict):
                band_kwh['scale_1_flat'] += max(_number(row.get('import_kwh')), 0.0)
            continue
        kwh = max(_number(row.get('import_kwh')), 0.0)
        band = tou_band(effective_time)
        band_kwh = profile['band_kwh']
        if isinstance(band_kwh, defaultdict):
            band_kwh[band] += kwh
        bands[band] += kwh
        if band == 'peak':
            tariff = ctou_tariff_for_day(ctou_rates, effective_time.date())
            if tariff is not None:
                difference = max(
                    tariff.energy_rate_inc_vat(timestamp.date(), 'peak')
                    - tariff.energy_rate_inc_vat(timestamp.date(), 'off_peak'),
                    0.0,
                )
                peak_shift_saving_10_percent += kwh * 0.10 * difference
    band_total = sum(bands.values())
    band_facts = {
        band: {
            'kwh': round(value, 3),
            'share_percent': round(value / band_total * 100.0, 2) if band_total else 0.0,
        }
        for band, value in bands.items()
    }
    half_hour_profile_rows = []
    if intent == 'tariff':
        for (area_name, interval_start, interval_end), values in sorted(half_hour_profile.items()):
            samples = int(values['samples'])
            band_values = values['band_kwh']
            half_hour_profile_rows.append({
                'area': area_name,
                'interval_start': interval_start,
                'interval_end': interval_end,
                'average_kw': round(float(values['kw_total']) / samples, 3) if samples else 0.0,
                'maximum_kw': round(float(values['maximum_kw']), 3),
                'maximum_kva': round(float(values['maximum_kva']), 3),
                'total_kwh': round(float(values['kwh']), 3),
                'tariff_band_kwh': {
                    key: round(float(value), 3)
                    for key, value in sorted(dict(band_values).items())
                },
            })

    solar_daily = [
        row for row in dataset.daily
        if start_date <= _as_date(row.get('date')) <= end_date
        and row.get('area') == SOLAR_AREA
        and row.get('data_quality_status') == 'Complete'
    ]
    solar_total = sum(_number(row.get('import_kwh')) for row in solar_daily)
    solar_top = max(solar_daily, key=lambda row: _number(row.get('import_kwh')), default=None)
    solar_peak = max(solar_daily, key=lambda row: _number(row.get('peak_kw')), default=None)

    span = (end_date - start_date).days + 1
    comparison_end = start_date - timedelta(days=1)
    comparison_start = max(dataset.first_date, comparison_end - timedelta(days=span - 1))
    selected_area_names = set(_selected_areas(dataset, area))
    previous_daily = [
        row for row in dataset.daily
        if comparison_start <= _as_date(row.get('date')) <= comparison_end
        and str(row.get('area')) in selected_area_names
        and row.get('data_quality_status') == 'Complete'
    ]
    previous_kwh = sum(_number(row.get('import_kwh')) for row in previous_daily)
    expected_meter_days = len(selected_area_names) * span
    comparison_is_complete = (
        comparison_start == comparison_end - timedelta(days=span - 1)
        and len(daily) == expected_meter_days
        and len(previous_daily) == expected_meter_days
    )
    comparison_change = (
        (total_kwh - previous_kwh) / previous_kwh * 100.0
        if comparison_is_complete and previous_kwh
        else None
    )
    cost_facts: dict[str, object] = {}
    solar_investment_actual: dict[str, object] = {}
    # The tariff engine performs the application's full invoice-style analysis.
    # Only invoke it for a cost question so routine usage, demand and solar
    # questions remain responsive on the full historical database.
    if intent in {'cost', 'solar_investment', 'overview'} and area != SOLAR_AREA and comparison_end >= dataset.first_date:
        analysis = build_cost_analysis(
            dataset,
            start_date,
            end_date,
            area,
            rates,
            comparison_start,
            comparison_end,
            ctou_rates,
        )
        metrics = analysis.metrics
        cost_facts = {
            'estimated_cost_after_solar_savings_rand': round(float(metrics.get('estimated_total_cost') or 0.0), 2),
            'cost_before_solar_savings_rand': round(float(metrics.get('total_cost') or 0.0), 2),
            'solar_savings_rand': round(float(metrics.get('solar_avoided_cost') or 0.0), 2),
            'energy_cost_rand': round(float(metrics.get('energy_cost') or 0.0), 2),
            'demand_cost_rand': round(float(metrics.get('demand_cost') or 0.0), 2),
            'network_surcharge_rand': round(float(metrics.get('network_surcharge') or 0.0), 2),
            'coverage_percent': round(float(metrics.get('coverage_percent') or 0.0), 2),
            'warnings': list(analysis.warnings[:5]),
        }
        solar_investment_actual = current_solar_investment_metrics(analysis)

    unusual_limit = 40 if intent == 'unusual' else 12
    unusual = [
        {
            'date': str(row.get('date')),
            'area': str(row.get('area')),
            'severity': str(row.get('alert_level')),
            'type': str(row.get('alert_type')),
            'reason': str(row.get('alert_reason')),
            'kwh': round(_number(row.get('import_kwh')), 3),
            'peak_kw': round(_number(row.get('peak_kw')), 3),
            'peak_kw_time': str(row.get('peak_kw_time') or ''),
            'peak_kva': round(_number(row.get('peak_kva')), 3),
            'peak_kva_time': str(row.get('peak_kva_time') or ''),
        }
        for row in spikes[:unusual_limit]
    ]

    unusual_month_groups: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {
            'event_count': 0,
            'Critical': 0,
            'High': 0,
            'Watch': 0,
            'highest_peak_kva': 0.0,
            'highest_peak_kva_date': '',
            'highest_peak_kva_time': '',
            'highest_kwh': 0.0,
            'highest_kwh_date': '',
            'most_significant_event': None,
            'most_significant_rank': 99,
        }
    )
    for row in spikes:
        month = str(row.get('date') or '')[:7]
        area_name = str(row.get('area') or '')
        summary = unusual_month_groups[(month, area_name)]
        summary['event_count'] = int(summary['event_count']) + 1
        severity = str(row.get('alert_level') or '')
        if severity in {'Critical', 'High', 'Watch'}:
            summary[severity] = int(summary[severity]) + 1
        peak_kva = _number(row.get('peak_kva'))
        if peak_kva > float(summary['highest_peak_kva']):
            summary['highest_peak_kva'] = peak_kva
            summary['highest_peak_kva_date'] = str(row.get('date') or '')
            summary['highest_peak_kva_time'] = str(row.get('peak_kva_time') or '')
        kwh = _number(row.get('import_kwh'))
        if kwh > float(summary['highest_kwh']):
            summary['highest_kwh'] = kwh
            summary['highest_kwh_date'] = str(row.get('date') or '')
        rank = SEVERITY_ORDER.get(severity, 9)
        if rank < int(summary['most_significant_rank']):
            summary['most_significant_rank'] = rank
            summary['most_significant_event'] = {
                'date': str(row.get('date') or ''),
                'severity': severity,
                'type': str(row.get('alert_type') or ''),
                'reason': str(row.get('alert_reason') or ''),
            }
    unusual_by_month = [
        {
            'month': month,
            'area': area_name,
            'event_count': int(values['event_count']),
            'critical_count': int(values['Critical']),
            'high_count': int(values['High']),
            'watch_count': int(values['Watch']),
            'highest_peak_kva': round(float(values['highest_peak_kva']), 3),
            'highest_peak_kva_date': str(values['highest_peak_kva_date']),
            'highest_peak_kva_time': str(values['highest_peak_kva_time']),
            'highest_kwh': round(float(values['highest_kwh']), 3),
            'highest_kwh_date': str(values['highest_kwh_date']),
            'most_significant_event': values['most_significant_event'],
        }
        for (month, area_name), values in sorted(unusual_month_groups.items())
    ]

    demand_exceedances: list[dict[str, object]] = []
    demand_month_groups: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {
            'days_above_threshold': 0,
            'highest_peak_kva': 0.0,
            'threshold_kva': 0.0,
            'largest_exceedance_kva': 0.0,
            'highest_date': '',
            'highest_time': '',
        }
    )
    for row in daily:
        area_name = str(row.get('area') or '')
        if area_name not in CTOU_AREAS:
            continue
        day_value = _as_date(row.get('date'))
        tariff = ctou_tariff_for_day(ctou_rates, day_value)
        if tariff is None:
            continue
        threshold = float(tariff.surcharge_threshold_kva)
        peak_kva = _number(row.get('peak_kva'))
        if peak_kva <= threshold:
            continue
        exceedance = peak_kva - threshold
        detail = {
            'date': day_value.isoformat(),
            'area': area_name,
            'peak_kva': round(peak_kva, 3),
            'threshold_kva': round(threshold, 3),
            'exceedance_kva': round(exceedance, 3),
            'peak_time': str(row.get('peak_kva_time') or ''),
        }
        demand_exceedances.append(detail)
        summary = demand_month_groups[(day_value.strftime('%Y-%m'), area_name)]
        summary['days_above_threshold'] = int(summary['days_above_threshold']) + 1
        if peak_kva > float(summary['highest_peak_kva']):
            summary['highest_peak_kva'] = peak_kva
            summary['threshold_kva'] = threshold
            summary['largest_exceedance_kva'] = exceedance
            summary['highest_date'] = day_value.isoformat()
            summary['highest_time'] = str(row.get('peak_kva_time') or '')
    demand_exceedances.sort(
        key=lambda row: (str(row['date']), float(row['exceedance_kva'])),
        reverse=True,
    )
    demand_exceedances_by_month = [
        {
            'month': month,
            'area': area_name,
            'days_above_threshold': int(values['days_above_threshold']),
            'highest_peak_kva': round(float(values['highest_peak_kva']), 3),
            'threshold_kva': round(float(values['threshold_kva']), 3),
            'largest_exceedance_kva': round(float(values['largest_exceedance_kva']), 3),
            'highest_date': str(values['highest_date']),
            'highest_time': str(values['highest_time']),
        }
        for (month, area_name), values in sorted(demand_month_groups.items())
    ]

    ordered_daily = sorted(
        daily,
        key=lambda row: (_as_date(row.get('date')), str(row.get('area'))),
    )
    daily_detail: list[dict[str, object]] = []
    monthly_detail: list[dict[str, object]] = []
    if intent == 'tariff':
        # Tariff questions receive the more useful half-hour load profile below.
        # Omitting repetitive daily rows keeps the Copilot grounding concise.
        pass
    elif len(ordered_daily) <= 240:
        daily_detail = [
            {
                'date': str(row.get('date') or ''),
                'area': str(row.get('area') or ''),
                'electricity_used_kwh': round(_number(row.get('import_kwh')), 3),
                'peak_kw': round(_number(row.get('peak_kw')), 3),
                'peak_kw_time': str(row.get('peak_kw_time') or ''),
                'peak_kva': round(_number(row.get('peak_kva')), 3),
                'peak_kva_time': str(row.get('peak_kva_time') or ''),
            }
            for row in ordered_daily
        ]
    else:
        monthly_totals: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {'kwh': 0.0, 'days': 0.0, 'peak_kw': 0.0, 'peak_kva': 0.0}
        )
        for row in ordered_daily:
            key = (str(row.get('date') or '')[:7], str(row.get('area') or ''))
            summary = monthly_totals[key]
            summary['kwh'] += _number(row.get('import_kwh'))
            summary['days'] += 1
            summary['peak_kw'] = max(summary['peak_kw'], _number(row.get('peak_kw')))
            summary['peak_kva'] = max(summary['peak_kva'], _number(row.get('peak_kva')))
        monthly_detail = [
            {
                'month': month,
                'area': area_name,
                'electricity_used_kwh': round(values['kwh'], 3),
                'complete_meter_days': int(values['days']),
                'highest_kw': round(values['peak_kw'], 3),
                'highest_kva': round(values['peak_kva'], 3),
            }
            for (month, area_name), values in sorted(monthly_totals.items())
        ]

    top_intervals = sorted(
        intervals,
        key=lambda row: _number(row.get('kw_import')),
        reverse=True,
    )[:10]
    risk_by_area: dict[str, dict[str, int]] = defaultdict(
        lambda: {'Critical': 0, 'High': 0, 'Watch': 0}
    )
    for row in spikes:
        severity = str(row.get('alert_level') or '')
        if severity in risk_by_area[str(row.get('area') or '')]:
            risk_by_area[str(row.get('area') or '')][severity] += 1

    supply_balance = build_supply_demand_balance(dataset, start_date, end_date)
    supply_demand_facts: dict[str, object] = {
        'metrics': {
            key: round(value, 3) if isinstance(value, float) else value
            for key, value in supply_balance.metrics.items()
        },
        'daily': supply_balance.daily if intent == 'supply' else [],
        'typical_time_profile': supply_balance.profile if intent == 'supply' else [],
        'strongest_recorded_solar_output': _peak_fact(
            solar_peak, 'peak_kw', 'peak_kw_time', 'kW'
        ),
        'highest_solar_generation_day': _daily_fact(solar_top),
        'interpretation': (
            'Solar is measured generation output. Estimated grid need is warehouse demand minus solar at matched '
            'half-hours; it is not a separate measured utility incomer.'
        ),
    }

    solar_profile_rows: list[dict[str, object]] = []
    if intent in {'solar', 'solar_investment'}:
        solar_profile_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in dataset.intervals:
            if (
                row.get('area') == SOLAR_AREA
                and start_date <= _as_date(row.get('date')) <= end_date
            ):
                solar_profile_groups[str(row.get('timestamp') or '')[11:16]].append(row)
        for time_value, rows in sorted(solar_profile_groups.items()):
            solar_profile_rows.append({
                'time': time_value,
                'average_output_kw': round(
                    sum(_number(row.get('kw_import')) for row in rows) / len(rows), 3
                ),
                'maximum_output_kw': round(
                    max((_number(row.get('kw_import')) for row in rows), default=0.0), 3
                ),
                'generated_kwh': round(
                    sum(_number(row.get('import_kwh')) for row in rows), 3
                ),
            })

    proposal_facts = [
        {
            'option': proposal.label,
            'pv_dc_kwp': proposal.pv_dc_kwp,
            'inverter_ac_kw': proposal.inverter_ac_kw,
            'battery_power_kw': proposal.battery_power_kw,
            'battery_capacity_kwh': proposal.battery_capacity_kwh,
            'annual_generation_kwh': proposal.annual_generation_kwh,
            'annual_battery_discharge_kwh': proposal.annual_battery_discharge_kwh,
            'round_trip_efficiency_percent': proposal.round_trip_efficiency_percent,
            'annual_grid_reduction_kwh': proposal.annual_grid_reduction_kwh,
            'annual_export_kwh': proposal.annual_export_kwh,
            'annual_losses_kwh': proposal.annual_losses_kwh,
            'usage_offset_percent': proposal.usage_offset_percent,
            'self_use_percent': round(proposal.self_use_percent, 2),
            'gross_investment_rand': proposal.gross_investment,
            'tax_deduction_rand': proposal.tax_deduction,
            'net_investment_rand': proposal.net_investment,
            'year_one_cost_before_solar_rand': proposal.year_one_cost_before_solar,
            'year_one_savings_rand': proposal.year_one_savings,
            'year_one_cost_after_solar_rand': proposal.year_one_cost_after_solar,
            'simple_payback_years': round(proposal.simple_payback_years, 2),
            'vendor_payback_years': proposal.vendor_payback_years,
            'annual_savings_rand': list(proposal.annual_savings),
            'source_document': proposal.source_name,
        }
        for proposal in SOLAR_PROPOSALS
    ]
    manifest_counts = dataset.manifest.get('row_counts', {})
    database_manifest = dataset.manifest.get('database', {})
    dataset_inventory = {
        'available_from': dataset.first_date.isoformat(),
        'available_through': dataset.last_date.isoformat(),
        'meter_areas': list(dataset.areas),
        'interval_readings': len(dataset.intervals),
        'daily_meter_records': len(dataset.daily),
        'weekly_meter_records': len(dataset.weekly),
        'unusual_usage_records': len(dataset.spikes),
        'positive_solar_records': len(dataset.solar_positive_events),
        'stored_row_counts': dict(manifest_counts) if isinstance(manifest_counts, dict) else {},
        'database_sync_count': (
            int(database_manifest.get('sync_count') or 0)
            if isinstance(database_manifest, dict) else 0
        ),
        'latest_stored_timestamp': (
            str(database_manifest.get('latest_timestamp') or '')
            if isinstance(database_manifest, dict) else ''
        ),
    }
    app_page_catalog = {
        'Overview': {
            'purpose': 'Executive view of electricity used, daily average, kW, kVA, solar, costs and key insights.',
            'evidence_keys': ['total_electricity_used_kwh', 'area_totals', 'highest_daily_use', 'highest_working_load', 'highest_apparent_demand', 'solar', 'cost'],
        },
        'Usage Trends': {
            'purpose': 'Daily, weekly, monthly and time-of-day usage patterns by meter area.',
            'evidence_keys': ['daily_readings', 'monthly_readings', 'highest_half_hour_intervals', 'half_hour_load_profile'],
        },
        'Supply & Demand': {
            'purpose': 'Matched solar generation versus combined warehouse demand and estimated remaining grid need.',
            'evidence_keys': ['supply_demand'],
        },
        'Solar Performance': {
            'purpose': 'Recorded solar generation, output peaks, productive hours, daily performance and profile.',
            'evidence_keys': ['solar', 'solar_output_profile', 'positive_solar_events'],
        },
        'Solar Investment': {
            'purpose': 'Actual PNPSCADA performance versus the two proposal options, investment, savings, batteries and payback.',
            'evidence_keys': ['solar_investment'],
        },
        'Comparisons': {
            'purpose': 'Period, area and solar comparisons using matched complete coverage.',
            'evidence_keys': ['preceding_period_comparison', 'area_totals', 'daily_readings', 'monthly_readings'],
        },
        'Cost Centre': {
            'purpose': 'Tariff-based estimated cost, solar savings, projections, demand and comparison drivers.',
            'evidence_keys': ['cost', 'tariff_reference', 'ctou_energy_mix'],
        },
        'Unusual Usage': {
            'purpose': 'Incident evidence, severity, timestamps, baselines, recurring time patterns and investigation actions.',
            'evidence_keys': ['unusual_readings', 'risk_counts_by_area', 'positive_solar_events'],
        },
        'Reports Center': {
            'purpose': 'Professional PDF reports and CSV/XLSX exports using the active app filters.',
            'report_types': ['Current month overview', 'Filtered overview', 'Usage comparison', 'Cost comparison'],
            'export_formats': ['PDF', 'XLSX', 'CSV'],
        },
        'Data Update': {
            'purpose': 'Local historical database coverage, latest stored timestamp and PNPSCADA refresh status.',
            'evidence_keys': ['dataset_inventory'],
        },
        'AI Hub': {
            'purpose': (
                'Filter-independent conversational analysis across every app page and the complete '
                'stored PNPSCADA history. Dates or areas named in a question focus that answer.'
            ),
            'evidence_keys': ['app_page_catalog', 'data_dictionary'],
        },
        'Agent Centre': {
            'purpose': (
                'Prepare reviewed internal alert or summary emails from verified app evidence, '
                'save them to Outlook Drafts, or send them after explicit employee approval.'
            ),
            'evidence_keys': ['unusual_readings', 'risk_counts_by_area', 'cost', 'tariff_reference'],
            'controls': ['recipient validation', 'review confirmation', 'local metadata audit'],
        },
    }

    return {
        'data_period': {'from': start_date.isoformat(), 'to': end_date.isoformat()},
        'meter_area': area,
        'complete_meter_days': len(daily),
        'complete_calendar_days': len(dates),
        'excluded_incomplete_meter_days': excluded_rows,
        'total_electricity_used_kwh': round(total_kwh, 3),
        'average_used_per_calendar_day_kwh': round(total_kwh / len(dates), 3) if dates else 0.0,
        'highest_daily_use': _daily_fact(top_daily),
        'highest_working_load': _peak_fact(top_kw, 'peak_kw', 'peak_kw_time', 'kW'),
        'highest_apparent_demand': _peak_fact(top_kva, 'peak_kva', 'peak_kva_time', 'kVA'),
        'highest_half_hour_load': {
            'area': str(top_interval.get('area')) if top_interval else '',
            'timestamp': str(top_interval.get('timestamp')) if top_interval else '',
            'kw': round(_number(top_interval.get('kw_import')), 3) if top_interval else 0.0,
        },
        'area_totals': area_totals,
        'preceding_period_comparison': {
            'from': comparison_start.isoformat(),
            'to': comparison_end.isoformat(),
            'current_kwh': round(total_kwh, 3),
            'previous_kwh': round(previous_kwh, 3),
            'change_percent': round(comparison_change, 3) if comparison_change is not None else None,
            'comparable': comparison_is_complete,
            'note': (
                'Both periods contain the same complete meter-day coverage.'
                if comparison_is_complete
                else 'No percentage is reported because both periods do not contain identical complete meter-day coverage.'
            ),
        },
        'ctou_energy_mix': band_facts,
        'ctou_note': 'Warehouse 9 is on a flat Scale 1 tariff and is excluded from the CTOU band split.',
        'tariff_reference': _tariff_reference(start_date, end_date, rates, ctou_rates),
        'half_hour_load_profile': half_hour_profile_rows,
        'half_hour_profile_note': (
            'For tariff questions, average and maximum demand are grouped by the actual interval start and end time. '
            'Tariff-band kWh reflects season, weekday/weekend and public-holiday rules.'
            if half_hour_profile_rows else ''
        ),
        'theoretical_saving_if_10_percent_peak_kwh_moves_to_off_peak_rand': round(peak_shift_saving_10_percent, 2),
        'unusual_readings': unusual,
        'unusual_readings_by_month': unusual_by_month,
        'demand_threshold_exceedances': demand_exceedances[:80],
        'demand_threshold_exceedances_by_month': demand_exceedances_by_month,
        'demand_threshold_note': (
            'A demand threshold exceedance means the recorded daily peak kVA was above the '
            'CTOU tariff surcharge threshold active on that date. It is separate from a '
            'matching-weekday unusual-usage alert.'
        ),
        'risk_counts_by_area': [
            {'area': area_name, **counts}
            for area_name, counts in sorted(risk_by_area.items())
        ],
        'highest_half_hour_intervals': [
            {
                'timestamp': str(row.get('timestamp') or ''),
                'area': str(row.get('area') or ''),
                'kw': round(_number(row.get('kw_import')), 3),
                'kwh': round(_number(row.get('import_kwh')), 3),
            }
            for row in top_intervals
        ],
        'daily_readings': daily_detail,
        'monthly_readings': monthly_detail,
        'detail_note': (
            'Half-hour load profiles replace repetitive daily detail for this tariff analysis.'
            if intent == 'tariff'
            else 'Complete daily readings are included for direct analysis.'
            if daily_detail
            else 'The selected period is large, so monthly area summaries are supplied instead of every daily row.'
        ),
        'solar': {
            'generated_kwh': round(solar_total, 3),
            'complete_days': len(solar_daily),
            'highest_generation_day': _daily_fact(solar_top),
            'highest_output': _peak_fact(solar_peak, 'peak_kw', 'peak_kw_time', 'kW'),
        },
        'solar_output_profile': solar_profile_rows,
        'positive_solar_events': [
            {
                key: value for key, value in row.items()
                if key in {'date', 'area', 'data_quality_status', 'alert_level', 'alert_reason', 'import_kwh', 'peak_kw', 'peak_kw_time'}
            }
            for row in dataset.solar_positive_events
            if start_date <= _as_date(row.get('date')) <= end_date
        ][:20],
        'supply_demand': supply_demand_facts,
        'solar_investment': {
            'actual_selected_period': solar_investment_actual,
            'proposal_options': proposal_facts,
            'source_of_truth_note': (
                'PNPSCADA is authoritative for actual generation and usage. Proposal values are scenario inputs only.'
            ),
        },
        'cost': cost_facts,
        'dataset_inventory': dataset_inventory,
        'app_page_catalog': app_page_catalog,
        'data_dictionary': {
            'half_hour_readings': [
                'timestamp', 'meter area', 'interval length', 'kW imported', 'kW exported',
                'net kW', 'kVAR', 'kVA', 'kWh imported', 'kWh exported', 'net kWh',
                'power factor', 'calculation method', 'source quality status',
            ],
            'daily_meter_records': [
                'date', 'meter area', 'data quality', 'sample coverage', 'kWh',
                'previous-day change', 'matching-weekday baseline', 'kW peak and time',
                'kVA peak and time', 'power factor at peak', 'alert and investigation fields',
            ],
            'derived_app_data': [
                'daily/weekly/monthly trends', 'period comparisons', 'time-of-use energy mix',
                'tariff-based cost estimates', 'solar savings', 'supply-demand matching',
                'unusual-usage events and monthly summaries',
                'tariff demand-threshold exceedances by date and month',
                'positive solar events', 'PDF/XLSX/CSV outputs',
            ],
            'authority_note': (
                'PNPSCADA readings are the source of truth for actual electricity use and generation. '
                'Tariffs, proposal scenarios and calculated estimates are identified separately.'
            ),
        },
    }


def _tariff_reference(
    start_date: date,
    end_date: date,
    rates: tuple[TariffRate, ...],
    ctou_rates: tuple[CtouTariffRate, ...],
) -> dict[str, object]:
    """Expose the exact in-app tariff rates and clock windows to Copilot."""
    scale_rows = [
        {
            'tariff_name': rate.tariff_name,
            'effective_from': rate.effective_start.isoformat(),
            'effective_to': rate.effective_end.isoformat(),
            'high_season_energy_rate_inc_vat_rand_per_kwh': round(rate.high_season_r_per_kwh, 4),
            'low_season_energy_rate_inc_vat_rand_per_kwh': round(rate.low_season_r_per_kwh, 4),
            'service_charge_inc_vat_rand_per_month': round(rate.service_charge_r_per_month, 2),
            'source': rate.source_label,
        }
        for rate in rates
        if rate.effective_start <= end_date and rate.effective_end >= start_date
    ]
    ctou_rows = []
    for rate in ctou_rates:
        if rate.effective_start > end_date or rate.effective_end < start_date:
            continue
        multiplier = 1.0 + rate.vat_rate
        ctou_rows.append({
            'tariff_name': rate.tariff_name,
            'effective_from': rate.effective_start.isoformat(),
            'effective_to': rate.effective_end.isoformat(),
            'high_season_june_to_august': {
                band: {
                    'rand_per_kwh_ex_vat': round(rate.energy_rate_ex_vat(date(2026, 7, 1), band), 4),
                    'rand_per_kwh_inc_vat': round(rate.energy_rate_ex_vat(date(2026, 7, 1), band) * multiplier, 4),
                }
                for band in ('peak', 'standard', 'off_peak')
            },
            'low_season_september_to_may': {
                band: {
                    'rand_per_kwh_ex_vat': round(rate.energy_rate_ex_vat(date(2026, 5, 1), band), 4),
                    'rand_per_kwh_inc_vat': round(rate.energy_rate_ex_vat(date(2026, 5, 1), band) * multiplier, 4),
                }
                for band in ('peak', 'standard', 'off_peak')
            },
            'demand_charge_rand_per_kva_month_ex_vat': round(rate.demand_charge_r_per_kva, 2),
            'demand_charge_rand_per_kva_month_inc_vat': round(rate.demand_charge_r_per_kva * multiplier, 2),
            'minimum_chargeable_demand_kva': round(rate.minimum_demand_kva, 2),
            'service_charge_rand_per_month_ex_vat': round(rate.service_charge_r_per_month, 2),
            'service_charge_rand_per_month_inc_vat': round(rate.service_charge_r_per_month * multiplier, 2),
            'network_surcharge_percent': round(rate.network_surcharge_percent, 2),
            'network_surcharge_threshold_kva': round(rate.surcharge_threshold_kva, 2),
            'source': rate.source_label,
        })
    return {
        'location': 'Gate 4, 265 Sydney Road, KwaKhangela, Durban',
        'area_assignments': dict(AREA_TARIFF_ASSIGNMENTS),
        'solar_credit_area': SOLAR_CREDIT_AREA,
        'scale_1_rates_active_in_selected_period': scale_rows,
        'ctou_rates_active_in_selected_period': ctou_rows,
        'ctou_clock_schedule': {
            'time_zone': 'Africa/Johannesburg',
            'high_season': {
                'months': ['June', 'July', 'August'],
                'weekday': [
                    {'from': '00:00', 'to': '06:00', 'band': 'off_peak'},
                    {'from': '06:00', 'to': '08:00', 'band': 'peak'},
                    {'from': '08:00', 'to': '17:00', 'band': 'standard'},
                    {'from': '17:00', 'to': '20:00', 'band': 'peak'},
                    {'from': '20:00', 'to': '22:00', 'band': 'standard'},
                    {'from': '22:00', 'to': '24:00', 'band': 'off_peak'},
                ],
                'saturday': [
                    {'from': '00:00', 'to': '07:00', 'band': 'off_peak'},
                    {'from': '07:00', 'to': '12:00', 'band': 'standard'},
                    {'from': '12:00', 'to': '17:00', 'band': 'off_peak'},
                    {'from': '17:00', 'to': '19:00', 'band': 'standard'},
                    {'from': '19:00', 'to': '24:00', 'band': 'off_peak'},
                ],
                'sunday': [{'from': '00:00', 'to': '24:00', 'band': 'off_peak'}],
            },
            'low_season': {
                'months': ['September', 'October', 'November', 'December', 'January', 'February', 'March', 'April', 'May'],
                'weekday': [
                    {'from': '00:00', 'to': '06:00', 'band': 'off_peak'},
                    {'from': '06:00', 'to': '07:00', 'band': 'standard'},
                    {'from': '07:00', 'to': '09:00', 'band': 'peak'},
                    {'from': '09:00', 'to': '18:00', 'band': 'standard'},
                    {'from': '18:00', 'to': '21:00', 'band': 'peak'},
                    {'from': '21:00', 'to': '22:00', 'band': 'standard'},
                    {'from': '22:00', 'to': '24:00', 'band': 'off_peak'},
                ],
                'saturday': [
                    {'from': '00:00', 'to': '07:00', 'band': 'off_peak'},
                    {'from': '07:00', 'to': '12:00', 'band': 'standard'},
                    {'from': '12:00', 'to': '18:00', 'band': 'off_peak'},
                    {'from': '18:00', 'to': '20:00', 'band': 'standard'},
                    {'from': '20:00', 'to': '24:00', 'band': 'off_peak'},
                ],
                'sunday': [
                    {'from': '00:00', 'to': '18:00', 'band': 'off_peak'},
                    {'from': '18:00', 'to': '20:00', 'band': 'standard'},
                    {'from': '20:00', 'to': '24:00', 'band': 'off_peak'},
                ],
            },
            'holiday_note': (
                'The app applies the published eThekwini Saturday or Sunday profile to South African public holidays, '
                'including the substituted Monday rule when a fixed holiday falls on Sunday.'
            ),
            'meter_timestamp_note': (
                'PNPSCADA timestamps mark the end of each half-hour. A 22:30 timestamp represents 22:00-22:30.'
            ),
        },
    }


def _recommended_actions(
    facts: dict[str, object], spikes: list[dict[str, str]], intent: str
) -> tuple[str, ...]:
    actions: list[str] = []
    if intent == 'solar':
        if spikes:
            first = spikes[0]
            actions.append(
                f"Compare the solar inverter and weather records with {first.get('date')} at the recorded output time."
            )
        actions.append(
            'Review sustained low solar output against inverter alarms, maintenance records and comparable weather before classifying a fault.'
        )
        return tuple(dict.fromkeys(actions))
    mix = facts.get('ctou_energy_mix')
    if isinstance(mix, dict):
        peak = mix.get('peak')
        if isinstance(peak, dict) and float(peak.get('kwh') or 0.0) > 0:
            actions.append(
                'Review flexible loads in the recorded peak windows and move only operationally suitable work to off-peak periods.'
            )
    demand = facts.get('highest_apparent_demand')
    if isinstance(demand, dict) and float(demand.get('value') or 0.0) >= 110.0:
        actions.append(
            'Stagger high-demand equipment starts and investigate the exact demand-peak timestamp because the recorded kVA crossed the 110 kVA surcharge threshold.'
        )
    if spikes:
        first = spikes[0]
        actions.append(
            f"Check the shift, equipment and maintenance log for {first.get('area')} on {first.get('date')} at the recorded peak time."
        )
    area_totals = facts.get('area_totals')
    if isinstance(area_totals, list) and area_totals:
        top = area_totals[0]
        if isinstance(top, dict):
            actions.append(
                f"Prioritise the first operational review in {top.get('area')}, the highest-use area for this period."
            )
    return tuple(dict.fromkeys(actions))


def _local_answer(
    intent: str,
    facts: dict[str, object],
    spikes: list[dict[str, str]],
    limitations: list[str],
    actions: tuple[str, ...],
) -> str:
    period = facts.get('data_period')
    if not isinstance(period, dict):
        period = {'from': '', 'to': ''}
    prefix = f"Based on complete PNPSCADA readings from {period.get('from')} to {period.get('to')}."
    if int(facts.get('complete_meter_days') or 0) == 0:
        return f'{prefix}\n\nNo complete readings are available for this question.'

    highest_day = facts.get('highest_daily_use')
    highest_kw = facts.get('highest_working_load')
    highest_kva = facts.get('highest_apparent_demand')
    solar = facts.get('solar')
    cost = facts.get('cost')
    mix = facts.get('ctou_energy_mix')
    area_totals = facts.get('area_totals')
    lines = [prefix]

    if intent == 'conversation':
        return (
            'Hello. I can analyse the complete stored electricity data, tariff schedules, costs, demand, '
            'solar performance and unusual usage. Ask naturally or tell me what decision you are trying to make.'
        )
    if intent == 'tariff':
        if isinstance(mix, dict):
            peak = mix.get('peak', {})
            standard = mix.get('standard', {})
            off_peak = mix.get('off_peak', {})
            lines.extend([
                '',
                f"CTOU electricity: {_fmt_kwh(_dict_number(peak, 'kwh'))} peak ({_dict_number(peak, 'share_percent'):.1f}%), "
                f"{_fmt_kwh(_dict_number(standard, 'kwh'))} standard and {_fmt_kwh(_dict_number(off_peak, 'kwh'))} off-peak.",
                'High-season weekday clock bands: off-peak 00:00-06:00 and 22:00-24:00; '
                'peak 06:00-08:00 and 17:00-20:00; standard 08:00-17:00 and 20:00-22:00.',
                'Low-season weekday clock bands: off-peak 00:00-06:00 and 22:00-24:00; '
                'peak 07:00-09:00 and 18:00-21:00; standard 06:00-07:00, 09:00-18:00 and 21:00-22:00.',
                'Saturday, Sunday and public-holiday windows are included in the detailed tariff reference supplied to Copilot.',
                f"A theoretical 10% shift of peak-period kWh to off-peak would reduce the energy component by approximately R {float(facts.get('theoretical_saving_if_10_percent_peak_kwh_moves_to_off_peak_rand') or 0.0):,.2f}. Operational feasibility has not been assumed.",
            ])
    elif intent == 'supply':
        balance = facts.get('supply_demand')
        metrics = balance.get('metrics') if isinstance(balance, dict) else None
        if isinstance(metrics, dict):
            lines.extend([
                '',
                f"Matched warehouse demand: {_fmt_kwh(_number(metrics.get('warehouse_kwh')))}.",
                f"Recorded solar generation: {_fmt_kwh(_number(metrics.get('solar_kwh')))}.",
                f"Solar contribution to matched demand: {_number(metrics.get('solar_contribution_percent')):.1f}%.",
                f"Estimated remaining grid need: {_fmt_kwh(_number(metrics.get('estimated_grid_kwh')))} "
                f"({_number(metrics.get('remaining_demand_percent')):.1f}% of matched demand).",
            ])
            strongest = balance.get('strongest_recorded_solar_output')
            best_day = balance.get('highest_solar_generation_day')
            lines.extend([
                f"Strongest recorded solar output: {_fact_peak_text(strongest)}.",
                f"Highest solar generation day: {_fact_daily_text(best_day)}.",
            ])
    elif intent == 'solar_investment':
        investment = facts.get('solar_investment')
        options = investment.get('proposal_options') if isinstance(investment, dict) else None
        actual = investment.get('actual_selected_period') if isinstance(investment, dict) else None
        lines.append('')
        if isinstance(actual, dict) and actual:
            lines.append(
                f"PNPSCADA recorded {_fmt_kwh(_number(actual.get('solar_generated_kwh')))} of solar "
                f"generation and {_fmt_kwh(_number(actual.get('solar_used_kwh')))} used against site demand."
            )
        if isinstance(options, list) and options:
            lines.append(f'{len(options)} proposal option(s) are available for scenario analysis:')
            for option in options:
                if isinstance(option, dict):
                    lines.append(
                        f"- {option.get('option')}: {float(option.get('pv_dc_kwp') or 0.0):,.1f} kWp PV, "
                        f"{float(option.get('battery_capacity_kwh') or 0.0):,.1f} kWh battery, "
                        f"R {float(option.get('net_investment_rand') or 0.0):,.0f} net investment and "
                        f"{float(option.get('simple_payback_years') or 0.0):.2f}-year simple payback."
                    )
    elif intent == 'reports':
        catalog = facts.get('app_page_catalog')
        reports = catalog.get('Reports Center') if isinstance(catalog, dict) else None
        if isinstance(reports, dict):
            report_types = ', '.join(str(value) for value in reports.get('report_types', []))
            formats = ', '.join(str(value) for value in reports.get('export_formats', []))
            lines.extend(['', f'Available report types: {report_types}.', f'Available outputs: {formats}.'])
    elif intent == 'data_update':
        inventory = facts.get('dataset_inventory')
        if isinstance(inventory, dict):
            lines.extend([
                '',
                f"Stored history: {inventory.get('available_from')} through {inventory.get('available_through')}.",
                f"Latest stored timestamp: {inventory.get('latest_stored_timestamp') or 'not recorded in the manifest'}.",
                f"The local database contains {int(inventory.get('interval_readings') or 0):,} half-hour readings "
                f"across {len(inventory.get('meter_areas') or [])} meter areas.",
            ])
    elif intent == 'unusual':
        lines.append('')
        if spikes:
            lines.append(f'{len(spikes)} unusual complete meter-day reading(s) were found. Highest-priority examples:')
            for row in spikes[:3]:
                lines.append(
                    f"- {row.get('area')} on {row.get('date')}: {row.get('alert_reason')}"
                )
        else:
            lines.append('No unusual complete meter-day readings were flagged in this scope.')
    elif intent == 'solar' and isinstance(solar, dict):
        top = solar.get('highest_generation_day')
        peak = solar.get('highest_output')
        lines.extend([
            '',
            f"Solar generated {_fmt_kwh(float(solar.get('generated_kwh') or 0.0))} across {int(solar.get('complete_days') or 0)} complete day(s).",
            f"Highest generation day: {_fact_daily_text(top)}.",
            f"Highest recorded output: {_fact_peak_text(peak)}.",
        ])
    elif intent == 'cost' and isinstance(cost, dict):
        lines.extend([
            '',
            f"Estimated cost before solar savings: R {float(cost.get('cost_before_solar_savings_rand') or 0.0):,.2f}.",
            f"Solar savings: R {float(cost.get('solar_savings_rand') or 0.0):,.2f}.",
            f"Estimated cost after solar savings: R {float(cost.get('estimated_cost_after_solar_savings_rand') or 0.0):,.2f}.",
        ])
    elif intent == 'comparison':
        comparison = facts.get('preceding_period_comparison')
        lines.append('')
        if isinstance(comparison, dict) and comparison.get('comparable'):
            change = float(comparison.get('change_percent') or 0.0)
            direction = 'higher' if change > 0 else 'lower' if change < 0 else 'unchanged'
            lines.extend([
                f"Electricity use was {abs(change):.1f}% {direction} than {comparison.get('from')} to {comparison.get('to')}.",
                f"Selected period: {_fmt_kwh(float(comparison.get('current_kwh') or 0.0))}; preceding period: {_fmt_kwh(float(comparison.get('previous_kwh') or 0.0))}.",
            ])
        elif isinstance(comparison, dict):
            lines.append(str(comparison.get('note') or 'A reliable comparison is unavailable.'))
    else:
        lines.extend([
            '',
            f"Highest daily use: {_fact_daily_text(highest_day)}.",
            f"Highest working load: {_fact_peak_text(highest_kw)}.",
            f"Highest apparent demand: {_fact_peak_text(highest_kva)}.",
        ])
        if isinstance(area_totals, list) and area_totals and isinstance(area_totals[0], dict):
            top = area_totals[0]
            lines.append(
                f"Highest-use area: {top.get('area')} with {_fmt_kwh(float(top.get('kwh') or 0.0))} ({float(top.get('share_percent') or 0.0):.1f}% of the selected total)."
            )
        if spikes:
            lines.append(
                f"The most important matching alert was {spikes[0].get('area')} on {spikes[0].get('date')}: {spikes[0].get('alert_reason')}"
            )

    if limitations:
        lines.extend(['', f'Important: {limitations[-1]}'])
    if actions:
        lines.extend(['', 'Recommended next steps:'])
        lines.extend(f'- {action}' for action in actions[:4])
    return '\n'.join(lines)


def _question_intent(question: str, prior_question: str = '') -> str:
    text = question.casefold()
    if not text:
        return 'out_of_scope'
    if text.strip(' .!?') in {'hi', 'hello', 'hey', 'good morning', 'good afternoon'}:
        return 'conversation'
    domain_terms = (
        'electric', 'usage', 'used', 'consumption', 'demand', 'kwh', 'kw', 'kva',
        'meter', 'warehouse', 'wh ', 'solar', 'tariff', 'peak', 'off-peak',
        'offpeak', 'standard period', 'cost', 'saving', 'bill', 'spike', 'unusual',
        'anomaly', 'risk', 'exceed', 'threshold', 'surcharge', 'reading', 'load',
        'energy', 'area', 'filter', 'database',
        'this week', 'last week', 'this month', 'last month', 'what happened',
        'trend', 'compare', 'forklift', 'charger', 'vna', 'lighting', 'equipment',
        'shift', 'schedule', 'operation', 'efficien', 'performance', 'forecast',
        'project', 'pattern', 'overnight', 'weekend', 'hour', 'power factor',
        'app', 'overview', 'dashboard', 'sidebar', 'tab', 'page', 'report', 'pdf',
        'xlsx', 'csv', 'export', 'data update', 'data sync', 'refresh', 'sync',
        'supply', 'grid', 'investment', 'proposal', 'payback', 'battery',
    )
    has_domain_context = any(value in text for value in domain_terms)
    clearly_unrelated = (
        'recipe', 'sports score', 'politics', 'news headline', 'write a poem',
        'coding help', 'stock price', 'medical advice', 'capital of',
    )
    if any(value in text for value in clearly_unrelated) and not has_domain_context:
        return 'out_of_scope'
    if not has_domain_context and prior_question:
        previous_intent = _question_intent(prior_question)
        if previous_intent != 'out_of_scope':
            return previous_intent
    if not has_domain_context:
        analytical_language = (
            'why', 'explain', 'analyse', 'analyze', 'insight', 'recommend',
            'what should', 'what would', 'what concerns', 'what stands out',
            'tell me what', 'summarise', 'summarize', 'go deeper', 'likely cause',
            'priority', 'management view', 'how serious', 'what can we do',
        )
        if not any(value in text for value in analytical_language):
            return 'out_of_scope'
    if any(value in text for value in (
        'everything in the app', 'everything in this app', 'all sidebar pages',
        'all sidebar tabs', 'across the app', 'whole app', 'full app',
    )):
        return 'overview'
    if any(value in text for value in (
        'solar investment', 'investment option', 'proposal option', 'payback',
        'battery option', 'pv option',
    )):
        return 'solar_investment'
    if any(value in text for value in (
        'supply & demand', 'supply and demand', 'grid need', 'grid energy',
        'remaining demand', 'solar contribution',
    )):
        return 'supply'
    if any(value in text for value in (
        'report', 'pdf', 'xlsx', 'csv', 'export',
    )):
        return 'reports'
    if any(value in text for value in (
        'data update', 'data sync', 'sync status', 'refresh data', 'refresh status',
        'database coverage', 'latest stored', 'stored through',
    )):
        return 'data_update'
    if any(value in text for value in ('tariff', 'off-peak', 'offpeak', 'restructure', 'shift usage', 'peak hours', 'standard hours')):
        return 'tariff'
    if any(value in text for value in ('cost', 'saving', 'bill', 'rand')):
        return 'cost'
    if 'solar' in text:
        return 'solar'
    if any(value in text for value in (
        'unusual', 'spike', 'anomaly', 'risk', 'exceed', 'threshold',
        'what happened', 'issue',
    )):
        return 'unusual'
    if any(value in text for value in ('compare', 'comparison', 'trend', 'increase', 'decrease', 'change')):
        return 'comparison'
    return 'highest_usage'


def _question_area(
    question: str,
    active_area: str,
    dataset: DashboardDataset,
    intent: str = '',
) -> str:
    text = question.casefold()
    if intent in {'supply', 'solar_investment'}:
        return ALL_AREAS
    if 'solar' in text:
        return SOLAR_AREA
    match = re.search(r'\b(?:warehouse|wh)\s*([6789])\b', text)
    if match:
        candidate = f'Warehouse {match.group(1)}'
        if candidate in dataset.areas:
            return candidate
    if any(value in text for value in ('all areas', 'all warehouses', 'site-wide', 'site wide')):
        return ALL_AREAS
    return active_area if active_area in {ALL_AREAS, *dataset.areas} else ALL_AREAS


def _question_dates(
    dataset: DashboardDataset,
    question: str,
    active_start: date,
    active_end: date,
    area: str,
) -> tuple[date, date]:
    latest = _latest_complete_date(dataset, area) or min(dataset.last_date, date.today() - timedelta(days=1))
    text = question.casefold()
    explicit_dates = _explicit_dates(question)
    explicit_month_range = _explicit_month_range(question, latest)
    if explicit_dates:
        start = min(explicit_dates)
        end = max(explicit_dates)
    elif explicit_month_range is not None:
        start, end = explicit_month_range
    elif (
        'last week' in text
        or 'previous week' in text
        or 'last complete week' in text
        or 'last full week' in text
    ):
        this_monday = latest - timedelta(days=latest.weekday())
        start = this_monday - timedelta(days=7)
        end = this_monday - timedelta(days=1)
    elif (
        'this week' in text
        or 'current week' in text
        or 'partial week' in text
        or 'week so far' in text
    ):
        start = latest - timedelta(days=latest.weekday())
        end = latest
    elif 'last month' in text or 'previous month' in text:
        end = latest.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif 'this month' in text or 'current month' in text or 'month to date' in text:
        start = latest.replace(day=1)
        end = latest
    elif 'yesterday' in text:
        start = end = max(dataset.first_date, latest - timedelta(days=1))
    elif 'today' in text or 'latest day' in text:
        start = end = latest
    else:
        start = active_start
        end = active_end
    start = max(dataset.first_date, start)
    end = min(latest, end)
    if end < start:
        start = end
    return start, end


def _is_conversational_follow_up(question: str) -> bool:
    """Recognise short contextual turns without opening the assistant to general chat."""
    text = f" {' '.join(question.casefold().split())} "
    follow_up_phrases = (
        ' it ', ' that ', ' this ', ' those ', ' them ', ' then ', ' again ',
        ' why ', ' how about ', ' what about ', ' explain more ', ' more detail ',
        ' same ', ' filter ', ' database ', ' current partial ', ' last complete ',
        ' last full ', ' yes ', ' no ', ' go deeper ', ' break it down ',
        ' recommend ', ' should we ', ' what else ', ' which one ', ' how serious ',
        ' likely ', ' confidence ', ' prioritise ', ' prioritize ', ' action first ',
    )
    return len(text.split()) <= 30 and any(value in text for value in follow_up_phrases)


def _needs_current_week_clarification(
    question: str,
    start_date: date,
    end_date: date,
    complete_dates: set[date],
) -> bool:
    text = question.casefold()
    requested_current_week = 'this week' in text or 'current week' in text
    accepted_partial = any(
        value in text
        for value in ('so far', 'partial', 'available days', 'available data', 'to date')
    )
    return (
        requested_current_week
        and not accepted_partial
        and start_date.weekday() == 0
        and end_date.weekday() < 2
        and len(complete_dates) <= 2
    )


def _explicit_dates(question: str) -> tuple[date, ...]:
    values: list[date] = []
    for year, month, day in re.findall(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', question):
        try:
            values.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    for day, month, year in re.findall(r'\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b', question):
        try:
            values.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return tuple(dict.fromkeys(values))


def _explicit_month_range(question: str, latest: date) -> tuple[date, date] | None:
    month_names = {
        name.casefold(): index
        for index, name in enumerate(
            (
                '', 'January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December',
            )
        )
        if name
    }
    matches = list(re.finditer(
        r'\b(' + '|'.join(month_names) + r')\b',
        question,
        flags=re.IGNORECASE,
    ))
    if not matches:
        return None
    years_in_question = [int(value) for value in re.findall(r'\b(20\d{2})\b', question)]
    single_year = years_in_question[0] if len(set(years_in_question)) == 1 else None
    starts: list[date] = []
    for match in matches:
        month = month_names[match.group(1).casefold()]
        trailing = question[match.end():match.end() + 8]
        paired_year = re.match(r'\s+(20\d{2})\b', trailing)
        year = (
            int(paired_year.group(1))
            if paired_year else
            single_year
            if single_year is not None else
            latest.year
            if month <= latest.month else
            latest.year - 1
        )
        starts.append(date(year, month, 1))
    start = min(starts)
    final_month = max(starts)
    next_month = (
        final_month.replace(year=final_month.year + 1, month=1)
        if final_month.month == 12 else
        final_month.replace(month=final_month.month + 1)
    )
    return start, next_month - timedelta(days=1)


def _latest_complete_date(dataset: DashboardDataset, area: str) -> date | None:
    areas = set(_selected_areas(dataset, area))
    complete_by_date: dict[date, set[str]] = defaultdict(set)
    for row in dataset.daily:
        if row.get('data_quality_status') != 'Complete':
            continue
        row_area = str(row.get('area'))
        if row_area in areas:
            complete_by_date[_as_date(row.get('date'))].add(row_area)
    matches = [day for day, complete_areas in complete_by_date.items() if areas <= complete_areas]
    return max(matches, default=None)


def _selected_areas(dataset: DashboardDataset, area: str) -> tuple[str, ...]:
    if area == ALL_AREAS:
        return tuple(value for value in WAREHOUSE_AREAS if value in dataset.areas)
    return (area,) if area in dataset.areas else ()


def _copilot_instruction(evidence: AiEvidence) -> str:
    return (
        'Act as a senior energy analyst and a natural conversational partner for Connect Logistics. '
        'Stay focused on the application, its electricity data and decisions that can reasonably be '
        'supported by that data. Treat the verified numeric facts as the source of truth, but reason '
        'freely across the detailed evidence instead of merely repeating the app-calculated reference. '
        'You may calculate derived values, connect patterns, identify correlations, compare competing '
        'explanations, rank risks and propose scenarios. Apply established electricity and energy-management '
        'principles where useful. Clearly distinguish: observed facts, analytical inferences, plausible '
        'operational hypotheses and actions that require confirmation. Never invent a reading, event or '
        'confirmed physical cause. Maintain the multi-turn conversation and interpret natural short replies '
        'as follow-ups. If a reasonable assumption lets you answer, state it briefly and proceed; ask one '
        'focused clarification only when different interpretations would materially change the result. '
        'Do not use web search or retrieve email, Teams, SharePoint or meeting content. Do not let these '
        'data-source boundaries reduce the depth of reasoning available from the supplied facts and sound '
        'domain knowledge. Lead with the answer, explain why it matters, and give specific practical next '
        'steps. Use as much detail as the question needs, without repetitive warnings or a rigid template. '
        'Use natural Markdown with headings or bullets only when they improve readability; do not expose JSON or code. '
        'The tariff_reference in the evidence contains the application\'s complete eThekwini CTOU clock schedule, '
        'active season-specific rates, demand rules, Scale 1 rates and warehouse assignments. When it is present, '
        'use it directly and never claim that the tariff timetable or exact clock windows are unavailable. For '
        'charging or load-shifting questions, give exact local clock times, distinguish weekday/Saturday/Sunday '
        'and high/low season where material, and quantify impact using the supplied load profile or explicit assumptions. '
        'The app_page_catalog maps every sidebar page to its grounded evidence keys, and the data_dictionary explains '
        'the available underlying and derived fields. Treat every listed page as analytically accessible: never say that '
        'you cannot access a sidebar tab when its corresponding evidence is supplied. Move across pages when useful—for '
        'example, connect Usage Trends to Unusual Usage, Supply & Demand to Solar Performance, or Cost Centre to tariff '
        'timing and Solar Investment. A page name in the question is an instruction to prioritise that page\'s evidence, '
        'not a restriction against using related app evidence. You have read-only analytical access: do not claim to have '
        'changed readings, refreshed PNPSCADA, altered filters or generated a file unless the app actually performed that action. '
        'Before stating that a requested value is unavailable, check every relevant supplied evidence key; cross-page peak '
        'details may be repeated near the related page metrics specifically so they are not overlooked. The AI Hub is '
        'independent of dashboard filters: dataset_inventory describes its full accessible database, while data_period is '
        'the scope selected from the current question. For exceedance questions, check both unusual_readings_by_month and '
        'demand_threshold_exceedances_by_month, then use the corresponding detailed records. Do not say a covered month is '
        'absent merely because it is not among the first few highest-priority events. '
        f'User question: {evidence.question}\n\n'
        f'Verified scope: {evidence.start_date.isoformat()} to {evidence.end_date.isoformat()}, '
        f'{evidence.area}.\n'
        'App-calculated reference (useful context, but not a limit on deeper analysis):\n'
        f'{evidence.local_answer}'
    )


def _clean_microsoft_error(value: str) -> str:
    text = ' '.join(value.replace('\r', ' ').replace('\n', ' ').split())
    return text[:600]


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _number(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _daily_fact(row: dict[str, str] | None) -> dict[str, object]:
    if row is None:
        return {'area': '', 'date': '', 'kwh': 0.0}
    return {
        'area': str(row.get('area') or ''),
        'date': str(row.get('date') or ''),
        'kwh': round(_number(row.get('import_kwh')), 3),
    }


def _peak_fact(
    row: dict[str, str] | None,
    value_key: str,
    time_key: str,
    unit: str,
) -> dict[str, object]:
    if row is None:
        return {'area': '', 'timestamp': '', 'value': 0.0, 'unit': unit}
    return {
        'area': str(row.get('area') or ''),
        'timestamp': str(row.get(time_key) or ''),
        'value': round(_number(row.get(value_key)), 3),
        'unit': unit,
    }


def _fact_daily_text(value: object) -> str:
    if not isinstance(value, dict):
        return 'not available'
    return f"{_fmt_kwh(float(value.get('kwh') or 0.0))} at {value.get('area')} on {value.get('date')}"


def _fact_peak_text(value: object) -> str:
    if not isinstance(value, dict):
        return 'not available'
    return (
        f"{float(value.get('value') or 0.0):,.1f} {value.get('unit')} at "
        f"{value.get('area')} on {value.get('timestamp')}"
    )


def _dict_number(value: object, key: str) -> float:
    return _number(value.get(key)) if isinstance(value, dict) else 0.0


def _fmt_kwh(value: float) -> str:
    return f'{value / 1000:,.1f} MWh' if abs(value) >= 1000 else f'{value:,.0f} kWh'
