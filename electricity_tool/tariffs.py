from __future__ import annotations

import hashlib
import json
import re
import ssl
from calendar import monthrange
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .dashboard_data import (
    ALL_AREAS,
    SOLAR_AREA,
    DashboardDataset,
    build_supply_demand_balance,
)


ETHEKWINI_DOCUMENT_PAGE = (
    'https://www.durban.gov.za/pages/government/documents?d={start_year}_{end_year}'
)
DEFAULT_TARIFF_NAME = 'Business & General Scale 1'
CTOU_TARIFF_NAME = 'Commercial Time of Use (CTOU)'
DEFAULT_TARIFF_NOTE = (
    'PNPSCADA readings are costed against the tariff confirmed on the available '
    'municipal recovery bills: CTOU for Warehouses 6, 7 and 8, and Scale 1 for '
    'Warehouse 9. Bill-only meters and landlord adjustments are not inferred.'
)
AREA_TARIFF_ASSIGNMENTS: dict[str, str] = {
    'Warehouse 6': 'CTOU',
    'Warehouse 7': 'CTOU',
    'Warehouse 8': 'CTOU',
    'Warehouse 9': 'Scale 1',
}
SOLAR_CREDIT_AREA = 'Warehouse 8'


@dataclass(frozen=True)
class TariffRate:
    effective_start: date
    effective_end: date
    high_season_r_per_kwh: float
    low_season_r_per_kwh: float
    service_charge_r_per_month: float
    source_url: str
    source_label: str
    tariff_name: str = DEFAULT_TARIFF_NAME
    vat_included: bool = True

    def energy_rate(self, value: date) -> float:
        return (
            self.high_season_r_per_kwh
            if value.month in {6, 7, 8}
            else self.low_season_r_per_kwh
        )

    def to_json(self) -> dict[str, object]:
        row = asdict(self)
        row['effective_start'] = self.effective_start.isoformat()
        row['effective_end'] = self.effective_end.isoformat()
        return row

    @classmethod
    def from_json(cls, row: dict[str, object]) -> 'TariffRate':
        return cls(
            effective_start=date.fromisoformat(str(row['effective_start'])),
            effective_end=date.fromisoformat(str(row['effective_end'])),
            high_season_r_per_kwh=float(row['high_season_r_per_kwh']),
            low_season_r_per_kwh=float(row['low_season_r_per_kwh']),
            service_charge_r_per_month=float(row['service_charge_r_per_month']),
            source_url=str(row['source_url']),
            source_label=str(row['source_label']),
            tariff_name=str(row.get('tariff_name') or DEFAULT_TARIFF_NAME),
            vat_included=bool(row.get('vat_included', True)),
        )


@dataclass(frozen=True)
class CtouTariffRate:
    """VAT-exclusive commercial time-of-use components from eThekwini."""

    effective_start: date
    effective_end: date
    high_peak_r_per_kwh: float
    high_standard_r_per_kwh: float
    high_off_peak_r_per_kwh: float
    low_peak_r_per_kwh: float
    low_standard_r_per_kwh: float
    low_off_peak_r_per_kwh: float
    demand_charge_r_per_kva: float
    service_charge_r_per_month: float
    network_surcharge_percent: float
    source_url: str
    source_label: str
    tariff_name: str = CTOU_TARIFF_NAME
    vat_rate: float = 0.15
    minimum_demand_kva: float = 50.0
    surcharge_threshold_kva: float = 110.0

    def energy_rate_ex_vat(self, value: date, band: str) -> float:
        season = 'high' if value.month in {6, 7, 8} else 'low'
        normalized = band.replace('-', '_').casefold()
        field_name = f'{season}_{normalized}_r_per_kwh'
        return float(getattr(self, field_name))

    def energy_rate_inc_vat(self, value: date, band: str) -> float:
        return self.energy_rate_ex_vat(value, band) * (1.0 + self.vat_rate)

    def to_json(self) -> dict[str, object]:
        row = asdict(self)
        row['effective_start'] = self.effective_start.isoformat()
        row['effective_end'] = self.effective_end.isoformat()
        return row

    @classmethod
    def from_json(cls, row: dict[str, object]) -> 'CtouTariffRate':
        return cls(
            effective_start=date.fromisoformat(str(row['effective_start'])),
            effective_end=date.fromisoformat(str(row['effective_end'])),
            high_peak_r_per_kwh=float(row['high_peak_r_per_kwh']),
            high_standard_r_per_kwh=float(row['high_standard_r_per_kwh']),
            high_off_peak_r_per_kwh=float(row['high_off_peak_r_per_kwh']),
            low_peak_r_per_kwh=float(row['low_peak_r_per_kwh']),
            low_standard_r_per_kwh=float(row['low_standard_r_per_kwh']),
            low_off_peak_r_per_kwh=float(row['low_off_peak_r_per_kwh']),
            demand_charge_r_per_kva=float(row['demand_charge_r_per_kva']),
            service_charge_r_per_month=float(row['service_charge_r_per_month']),
            network_surcharge_percent=float(row['network_surcharge_percent']),
            source_url=str(row['source_url']),
            source_label=str(row['source_label']),
            tariff_name=str(row.get('tariff_name') or CTOU_TARIFF_NAME),
            vat_rate=float(row.get('vat_rate', 0.15)),
            minimum_demand_kva=float(row.get('minimum_demand_kva', 50.0)),
            surcharge_threshold_kva=float(row.get('surcharge_threshold_kva', 110.0)),
        )


@dataclass(frozen=True)
class TariffCheckResult:
    checked_at: datetime
    status: str
    message: str
    source_url: str
    changed: bool = False


@dataclass(frozen=True)
class CostAnalysis:
    start_date: date
    end_date: date
    comparison_start: date
    comparison_end: date
    area: str
    daily: list[dict[str, object]]
    comparison_daily: list[dict[str, object]]
    area_rows: list[dict[str, object]]
    tariff_rows: list[dict[str, object]]
    metrics: dict[str, object]
    warnings: tuple[str, ...]


# These are VAT-inclusive final eThekwini Scale 1 rates. They provide a safe,
# date-aware last-known-good schedule when the municipal website is unavailable.
BUILT_IN_RATES: tuple[TariffRate, ...] = (
    TariffRate(
        date(2023, 7, 1), date(2024, 6, 30), 3.3518, 3.3518, 438.56,
        'https://valuation.durban.gov.za/storage/Documents/Service%20Tariffs/'
        'Electricity%20Tariffs/Tariffs%202023%20-%202024/Tariff%20Rates.pdf',
        'eThekwini final electricity tariff rates 2023/24',
    ),
    TariffRate(
        date(2024, 7, 1), date(2025, 6, 30), 3.7781, 3.7781, 494.35,
        'https://valuation.durban.gov.za/storage/Documents/Service%20Tariffs/'
        'Electricity%20Tariffs/Tariffs%202024%20-%202025/Tariff%20Rates%202024_2025.pdf',
        'eThekwini final electricity tariff rates 2024/25',
    ),
    TariffRate(
        date(2025, 7, 1), date(2026, 6, 30), 4.2587, 4.2587, 557.23,
        'https://durban.gov.za/storage/Documents/Service%20Tariffs/'
        'Electricity%20Tariffs/Tariffs%202025%20-%202026/'
        'Final%20Tariff%20Rates%202025%20-%202026.pdf',
        'eThekwini final electricity tariff rates 2025/26',
    ),
    TariffRate(
        date(2026, 7, 1), date(2027, 6, 30), 4.6420, 4.6420, 607.38,
        'https://www.durban.gov.za/uploads/0000/13/2026/07/03/'
        'tariff-rates-2026-2027.pdf',
        'eThekwini final electricity tariff rates 2026/27',
    ),
)


# Final eThekwini CTOU rates are published excluding VAT. The 2025/26 values
# are independently confirmed by the three available UMFA recovery bills.
BUILT_IN_CTOU_RATES: tuple[CtouTariffRate, ...] = (
    CtouTariffRate(
        date(2023, 7, 1), date(2024, 6, 30),
        5.0540, 2.5287, 1.2319, 2.4935, 2.0060, 1.1669,
        107.86, 535.20, 25.0,
        'https://www.durban.gov.za/storage/Documents/Service%20Tariffs/'
        'Electricity%20Tariffs/Tariffs%202023%20-%202024/Tariff%20Rates.pdf',
        'eThekwini final CTOU rates 2023/24',
    ),
    CtouTariffRate(
        date(2024, 7, 1), date(2025, 6, 30),
        5.6969, 2.8504, 1.3886, 2.8107, 2.2612, 1.3153,
        121.58, 603.28, 25.0,
        'https://durban.gov.za/storage/Documents/Service%20Tariffs/'
        'Electricity%20Tariffs/Tariffs%202024%20-%202025/'
        'Tariff%20Rates%202024_2025.pdf',
        'eThekwini final CTOU rates 2024/25',
    ),
    CtouTariffRate(
        date(2025, 7, 1), date(2026, 6, 30),
        6.4215, 3.2130, 1.5652, 3.1682, 2.5488, 1.4826,
        137.04, 680.02, 25.0,
        'https://durban.gov.za/storage/Documents/Service%20Tariffs/'
        'Electricity%20Tariffs/Tariffs%202025%20-%202026/'
        'Final%20Tariff%20Rates%202025%20-%202026.pdf',
        'eThekwini final CTOU rates 2025/26',
    ),
    CtouTariffRate(
        date(2026, 7, 1), date(2027, 6, 30),
        6.9994, 3.5022, 1.7061, 3.4533, 2.7782, 1.6160,
        149.37, 741.22, 25.0,
        'https://www.durban.gov.za/uploads/0000/13/2026/07/03/'
        'tariff-rates-2026-2027.pdf',
        'eThekwini final CTOU rates 2026/27',
    ),
)


def tariff_for_day(rates: Iterable[TariffRate], value: date) -> TariffRate | None:
    matches = [
        rate for rate in rates
        if rate.effective_start <= value <= rate.effective_end
    ]
    return max(matches, key=lambda rate: rate.effective_start, default=None)


def ctou_tariff_for_day(
    rates: Iterable[CtouTariffRate], value: date
) -> CtouTariffRate | None:
    matches = [
        rate for rate in rates
        if rate.effective_start <= value <= rate.effective_end
    ]
    return max(matches, key=lambda rate: rate.effective_start, default=None)


def _dates_from_text(text: str) -> tuple[date, date]:
    match = re.search(
        r'EFFECTIVE\s*:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s*-\s*'
        r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})',
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError('The Scale 1 effective dates could not be read from the tariff PDF.')
    return tuple(
        datetime.strptime(value, '%d %B %Y').date() for value in match.groups()
    )  # type: ignore[return-value]


def _vat_pairs(text: str) -> list[tuple[float, float]]:
    """Read adjacent ex/incl VAT figures, including tightly joined PDF text."""
    values = [float(value) for value in re.findall(r'\d{2,4}\.\d{2}', text)]
    pairs: list[tuple[float, float]] = []
    index = 0
    while index + 1 < len(values):
        excluding_vat, including_vat = values[index:index + 2]
        if excluding_vat and 1.14 <= including_vat / excluding_vat <= 1.16:
            pairs.append((excluding_vat, including_vat))
            index += 2
        else:
            index += 1
    return pairs


def parse_scale_one_tariff(text: str, source_url: str) -> TariffRate:
    """Parse the final eThekwini Business & General Scale 1 PDF page text."""
    marker = re.search(r'BUSINESS\s*&\s*GENERAL\s+SCALE\s+1(?!\d)', text, re.I)
    if not marker:
        raise ValueError('Business & General Scale 1 was not found in the tariff PDF.')
    effective_start, effective_end = _dates_from_text(text)
    pairs = _vat_pairs(text)
    if len(pairs) < 2:
        raise ValueError('The Scale 1 VAT-inclusive energy and service rates could not be read.')

    seasonal = bool(re.search(r'High\s+Season.+Low\s+Season', text, re.I | re.S))
    if seasonal and len(pairs) >= 3:
        high_pair, low_pair, service_pair = pairs[:3]
    else:
        high_pair = low_pair = pairs[0]
        service_pair = pairs[1]
    return TariffRate(
        effective_start=effective_start,
        effective_end=effective_end,
        high_season_r_per_kwh=round(high_pair[1] / 100.0, 4),
        low_season_r_per_kwh=round(low_pair[1] / 100.0, 4),
        service_charge_r_per_month=round(service_pair[1], 2),
        source_url=source_url,
        source_label=(
            f'eThekwini final electricity tariff rates '
            f'{effective_start.year}/{str(effective_end.year)[-2:]}'
        ),
    )


def parse_ctou_tariff(text: str, source_url: str) -> CtouTariffRate:
    """Parse the final eThekwini Commercial Time of Use tariff page."""
    if not re.search(r'COMMERCIAL\s+TIME\s+OF\s+USE\s*\(CTOU\)', text, re.I):
        raise ValueError('Commercial Time of Use was not found in the tariff PDF.')
    effective_start, effective_end = _dates_from_text(text)
    pairs = _vat_pairs(text)
    if len(pairs) < 8:
        raise ValueError('The CTOU energy, demand and service rates could not be read.')
    energy_pairs = pairs[:6]
    demand_pair, service_pair = pairs[6:8]
    surcharge_match = re.search(
        r'(\d+(?:\.\d+)?)\s*%\s*(?:Levied|Applicable)', text, re.I
    )
    surcharge = float(surcharge_match.group(1)) if surcharge_match else 25.0
    values = [round(pair[0] / 100.0, 4) for pair in energy_pairs]
    return CtouTariffRate(
        effective_start=effective_start,
        effective_end=effective_end,
        high_peak_r_per_kwh=values[0],
        high_standard_r_per_kwh=values[1],
        high_off_peak_r_per_kwh=values[2],
        low_peak_r_per_kwh=values[3],
        low_standard_r_per_kwh=values[4],
        low_off_peak_r_per_kwh=values[5],
        demand_charge_r_per_kva=round(demand_pair[0], 2),
        service_charge_r_per_month=round(service_pair[0], 2),
        network_surcharge_percent=surcharge,
        source_url=source_url,
        source_label=(
            f'eThekwini final CTOU rates '
            f'{effective_start.year}/{str(effective_end.year)[-2:]}'
        ),
    )


def _extract_tariff_pages(document: bytes) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment setup failure
        raise RuntimeError('Install pypdf to check the official tariff document.') from exc
    from io import BytesIO

    scale_one_text = ''
    ctou_text = ''
    pages = PdfReader(BytesIO(document)).pages
    for page in pages:
        text = page.extract_text() or ''
        if not scale_one_text and re.search(
            r'BUSINESS\s*&\s*GENERAL\s+SCALE\s+1(?!\d)', text, re.I
        ):
            scale_one_text = text
        if not ctou_text and re.search(
            r'COMMERCIAL\s+TIME\s+OF\s+USE\s*\(CTOU\)', text, re.I
        ):
            ctou_text = text
        if scale_one_text and ctou_text:
            return scale_one_text, ctou_text
    missing = []
    if not scale_one_text:
        missing.append('Business & General Scale 1')
    if not ctou_text:
        missing.append('Commercial Time of Use')
    raise ValueError(f"The official PDF did not contain {' or '.join(missing)}.")


def _extract_pdf_text(document: bytes) -> str:
    """Backward-compatible Scale 1 page extractor used by existing callers."""
    return _extract_tariff_pages(document)[0]


class TariffRepository:
    def __init__(self, cache_path: str | Path) -> None:
        self.cache_path = Path(cache_path).expanduser().resolve()
        self._lock = RLock()

    def _read_cache(self) -> dict[str, object]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return {}

    def rates(self) -> tuple[TariffRate, ...]:
        by_start = {rate.effective_start: rate for rate in BUILT_IN_RATES}
        cache = self._read_cache()
        for row in cache.get('rates', []) if isinstance(cache.get('rates'), list) else []:
            try:
                rate = TariffRate.from_json(row)
            except (KeyError, TypeError, ValueError):
                continue
            by_start[rate.effective_start] = rate
        return tuple(sorted(by_start.values(), key=lambda rate: rate.effective_start))

    def ctou_rates(self) -> tuple[CtouTariffRate, ...]:
        by_start = {rate.effective_start: rate for rate in BUILT_IN_CTOU_RATES}
        cache = self._read_cache()
        rows = cache.get('ctou_rates', [])
        for row in rows if isinstance(rows, list) else []:
            try:
                rate = CtouTariffRate.from_json(row)
            except (KeyError, TypeError, ValueError):
                continue
            by_start[rate.effective_start] = rate
        return tuple(sorted(by_start.values(), key=lambda rate: rate.effective_start))

    def status(self) -> TariffCheckResult | None:
        cache = self._read_cache()
        value = cache.get('last_checked_at')
        if not value:
            return None
        try:
            checked_at = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        message = str(cache.get('message') or 'Official tariff source checked.')
        if 'ctou' not in message.casefold():
            message = (
                message.rstrip('.')
                + '. The verified last-known-good CTOU schedule is also active.'
            )
        return TariffCheckResult(
            checked_at=checked_at,
            status=str(cache.get('status') or 'checked'),
            message=message,
            source_url=str(cache.get('source_url') or ''),
            changed=bool(cache.get('changed', False)),
        )

    @staticmethod
    def _financial_year(today: date) -> tuple[int, int]:
        start_year = today.year if today.month >= 7 else today.year - 1
        return start_year, start_year + 1

    @staticmethod
    def _download(url: str) -> bytes:
        request = Request(url, headers={'User-Agent': 'SydneyRoadEnergyMonitor/1.0'})
        tls_context = ssl.create_default_context()
        # The municipality's current certificate chain is valid in Windows but
        # fails Python 3.13's optional X509 strictness because an intermediate
        # CA omits a non-security-critical marker. Keep certificate verification
        # enabled while matching the Windows/browser validation behaviour.
        if hasattr(ssl, 'VERIFY_X509_STRICT'):
            tls_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        with urlopen(request, timeout=45, context=tls_context) as response:
            return response.read()

    def _discover_pdf_url(self, today: date) -> str:
        start_year, end_year = self._financial_year(today)
        page_url = ETHEKWINI_DOCUMENT_PAGE.format(
            start_year=start_year,
            end_year=end_year,
        )
        html = self._download(page_url).decode('utf-8', errors='replace')
        hrefs = re.findall(r'href=["\']([^"\']+\.pdf)["\']', html, flags=re.I)
        preferred = [
            href for href in hrefs
            if 'tariff' in href.casefold()
            and 'connection' not in href.casefold()
            and str(start_year) in href
            and str(end_year) in href
        ]
        if not preferred:
            raise ValueError('No final electricity tariff PDF was listed on the municipal page.')
        return urljoin(page_url, preferred[-1])

    def check_for_updates(
        self,
        *,
        force: bool = False,
        today: date | None = None,
        minimum_interval: timedelta = timedelta(hours=24),
    ) -> TariffCheckResult:
        now = datetime.now(timezone.utc)
        existing = self.status()
        if (
            not force
            and existing is not None
            and now - existing.checked_at.astimezone(timezone.utc) < minimum_interval
        ):
            return existing

        check_date = today or date.today()
        try:
            source_url = self._discover_pdf_url(check_date)
            document = self._download(source_url)
            scale_text, ctou_text = _extract_tariff_pages(document)
            parsed = parse_scale_one_tariff(scale_text, source_url)
            parsed_ctou = parse_ctou_tariff(ctou_text, source_url)
            rates = list(self.rates())
            ctou_rates = list(self.ctou_rates())
            prior = tariff_for_day(rates, parsed.effective_start)
            prior_ctou = ctou_tariff_for_day(ctou_rates, parsed_ctou.effective_start)
            scale_changed = prior is None or (
                prior.high_season_r_per_kwh != parsed.high_season_r_per_kwh
                or prior.low_season_r_per_kwh != parsed.low_season_r_per_kwh
                or prior.service_charge_r_per_month != parsed.service_charge_r_per_month
                or prior.source_url != parsed.source_url
            )
            ctou_changed = prior_ctou is None or (
                prior_ctou.high_peak_r_per_kwh != parsed_ctou.high_peak_r_per_kwh
                or prior_ctou.high_standard_r_per_kwh != parsed_ctou.high_standard_r_per_kwh
                or prior_ctou.high_off_peak_r_per_kwh != parsed_ctou.high_off_peak_r_per_kwh
                or prior_ctou.low_peak_r_per_kwh != parsed_ctou.low_peak_r_per_kwh
                or prior_ctou.low_standard_r_per_kwh != parsed_ctou.low_standard_r_per_kwh
                or prior_ctou.low_off_peak_r_per_kwh != parsed_ctou.low_off_peak_r_per_kwh
                or prior_ctou.demand_charge_r_per_kva != parsed_ctou.demand_charge_r_per_kva
                or prior_ctou.service_charge_r_per_month != parsed_ctou.service_charge_r_per_month
                or prior_ctou.network_surcharge_percent != parsed_ctou.network_surcharge_percent
                or prior_ctou.source_url != parsed_ctou.source_url
            )
            changed = scale_changed or ctou_changed
            rates = [
                rate for rate in rates
                if rate.effective_start != parsed.effective_start
            ] + [parsed]
            rates.sort(key=lambda rate: rate.effective_start)
            ctou_rates = [
                rate for rate in ctou_rates
                if rate.effective_start != parsed_ctou.effective_start
            ] + [parsed_ctou]
            ctou_rates.sort(key=lambda rate: rate.effective_start)
            status = 'updated' if changed else 'current'
            message = (
                'New official Scale 1 and CTOU tariff components were found and applied.'
                if changed else 'The stored Scale 1 and CTOU tariffs match the official schedule.'
            )
            payload: dict[str, object] = {
                'last_checked_at': now.isoformat(),
                'status': status,
                'message': message,
                'source_url': source_url,
                'document_sha256': hashlib.sha256(document).hexdigest(),
                'changed': changed,
                'rates': [rate.to_json() for rate in rates],
                'ctou_rates': [rate.to_json() for rate in ctou_rates],
            }
        except Exception as exc:
            source_url = existing.source_url if existing is not None else ''
            changed = False
            status = 'unavailable'
            message = (
                f'Official tariff check could not complete ({exc}). '
                'The last-known-good rates remain active.'
            )
            payload = {
                **self._read_cache(),
                'last_checked_at': now.isoformat(),
                'status': status,
                'message': message,
                'source_url': source_url,
                'changed': False,
                'rates': [rate.to_json() for rate in self.rates()],
                'ctou_rates': [rate.to_json() for rate in self.ctou_rates()],
            }

        with self._lock:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + '.tmp')
            temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
            temporary.replace(self.cache_path)
        return TariffCheckResult(now, status, message, source_url, changed)


def _number(row: dict[str, object], field: str) -> float:
    try:
        return float(row.get(field, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100.0


def _easter_sunday(year: int) -> date:
    """Gregorian Easter date, used for South African movable public holidays."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _tou_public_holidays(year: int) -> dict[date, int]:
    """Return official-style TOU weekday substitutions (5=Saturday, 6=Sunday)."""
    easter = _easter_sunday(year)
    holidays: dict[date, int] = {
        date(year, 1, 1): 6,
        date(year, 3, 21): 5,
        easter - timedelta(days=2): 6,
        easter + timedelta(days=1): 6,
        date(year, 4, 27): 6,
        date(year, 5, 1): 5,
        date(year, 6, 16): 5,
        date(year, 8, 9): 5,
        date(year, 9, 24): 5,
        date(year, 12, 16): 5,
        date(year, 12, 25): 6,
        date(year, 12, 26): 6,
    }
    # Under the Public Holidays Act a fixed holiday falling on Sunday also
    # makes Monday a public holiday. eThekwini's 2025/26 CTOU table treats the
    # substituted Monday as a Saturday profile.
    for holiday in tuple(holidays):
        if holiday.weekday() == 6:
            holidays[holiday + timedelta(days=1)] = 5
    return holidays


def tou_day_type(value: date) -> int:
    """Weekday index after applying eThekwini's published TOU holiday treatment."""
    holidays: dict[date, int] = {}
    for year in {value.year - 1, value.year, value.year + 1}:
        holidays.update(_tou_public_holidays(year))
    return holidays.get(value, value.weekday())


def tou_band(value: datetime) -> str:
    """Classify a local half-hour as peak, standard or off-peak."""
    day_type = tou_day_type(value.date())
    hour = value.hour + value.minute / 60.0
    high_season = value.month in {6, 7, 8}
    if high_season:
        if day_type <= 4:
            if 6 <= hour < 8 or 17 <= hour < 20:
                return 'peak'
            if 8 <= hour < 17 or 20 <= hour < 22:
                return 'standard'
            return 'off_peak'
        if day_type == 5:
            return 'standard' if 7 <= hour < 12 or 17 <= hour < 19 else 'off_peak'
        return 'off_peak'
    if day_type <= 4:
        if 7 <= hour < 9 or 18 <= hour < 21:
            return 'peak'
        if 6 <= hour < 7 or 9 <= hour < 18 or 21 <= hour < 22:
            return 'standard'
        return 'off_peak'
    if day_type == 5:
        return 'standard' if 7 <= hour < 12 or 18 <= hour < 20 else 'off_peak'
    return 'standard' if 18 <= hour < 20 else 'off_peak'


def resolve_cost_month_comparison(
    selected_month: str,
    comparison_month: str,
    available_start: date,
    available_end: date,
    *,
    through_day: int | None = None,
) -> tuple[date, date, date, date]:
    """Resolve two calendar months as full months or matching month-to-date periods."""
    try:
        selected_start = datetime.strptime(selected_month, '%Y-%m').date().replace(day=1)
        comparison_start = datetime.strptime(comparison_month, '%Y-%m').date().replace(day=1)
    except ValueError as exc:
        raise ValueError('Choose a valid selected month and comparison month.') from exc

    selected_last = date(
        selected_start.year,
        selected_start.month,
        monthrange(selected_start.year, selected_start.month)[1],
    )
    comparison_last = date(
        comparison_start.year,
        comparison_start.month,
        monthrange(comparison_start.year, comparison_start.month)[1],
    )
    if through_day is None:
        selected_end = selected_last
        comparison_end = comparison_last
    else:
        if through_day < 1 or through_day > 31:
            raise ValueError('The matching day must be between 1 and 31.')
        selected_end = selected_start.replace(day=min(through_day, selected_last.day))
        comparison_end = comparison_start.replace(day=min(through_day, comparison_last.day))

    if selected_start < available_start or comparison_start < available_start:
        raise ValueError(
            f'Choose months from {available_start:%B %Y} onward; earlier meter data is unavailable.'
        )
    if selected_end > available_end or comparison_end > available_end:
        raise ValueError(
            f'The chosen period extends beyond the latest closed meter date, {available_end:%d %b %Y}.'
        )
    return selected_start, selected_end, comparison_start, comparison_end


def _period_cost(
    dataset: DashboardDataset,
    start_date: date,
    end_date: date,
    area: str,
    rates: tuple[TariffRate, ...],
    ctou_rates: tuple[CtouTariffRate, ...],
) -> tuple[
    list[dict[str, object]],
    dict[str, dict[str, float]],
    dict[str, object],
    list[str],
]:
    warehouse_areas = tuple(value for value in dataset.areas if value != SOLAR_AREA)
    selected_areas = (
        warehouse_areas if area == ALL_AREAS
        else (area,) if area in warehouse_areas
        else ()
    )
    area_totals: dict[str, dict[str, float]] = {
        value: defaultdict(float) for value in selected_areas
    }
    daily_by_date: dict[date, dict[str, object]] = {}
    cursor = start_date
    while cursor <= end_date:
        daily_by_date[cursor] = {
            'date': cursor.isoformat(),
            'kwh': 0.0,
            'energy_cost_ex_vat': 0.0,
            'energy_cost': 0.0,
            'demand_cost_ex_vat': 0.0,
            'demand_cost': 0.0,
            'service_cost_ex_vat': 0.0,
            'service_cost': 0.0,
            'network_surcharge_ex_vat': 0.0,
            'network_surcharge': 0.0,
            'vat': 0.0,
            'total_cost': 0.0,
            'solar_generated_kwh': 0.0,
            'solar_used_kwh': 0.0,
            'possible_excess_solar_kwh': 0.0,
            'solar_avoided_cost': 0.0,
            'energy_cost_after_solar': 0.0,
            'estimated_total_cost': 0.0,
            'peak_kwh': 0.0,
            'standard_kwh': 0.0,
            'off_peak_kwh': 0.0,
            'flat_kwh': 0.0,
            'quality': 'Incomplete',
        }
        cursor += timedelta(days=1)

    quality_by_day: dict[date, dict[str, str]] = defaultdict(dict)
    for row in dataset.daily:
        meter_area = str(row.get('area'))
        if meter_area not in selected_areas:
            continue
        day_value = date.fromisoformat(str(row['date'])[:10])
        if start_date <= day_value <= end_date:
            quality_by_day[day_value][meter_area] = str(
                row.get('data_quality_status') or 'Incomplete'
            )

    warnings: list[str] = []
    interval_counts: dict[str, int] = defaultdict(int)
    monthly_energy_ex: dict[tuple[str, int, int], float] = defaultdict(float)
    monthly_demand: dict[tuple[str, int, int], tuple[float, str]] = {}
    wh8_kwh_by_day_band: dict[tuple[date, str], float] = defaultdict(float)

    def interval_time(row: dict[str, object]) -> datetime:
        # PNPSCADA labels each half-hour with its end time. The municipal TOU
        # registers match when the interval is assigned to the preceding 30 min.
        return datetime.fromisoformat(str(row['timestamp'])) - timedelta(minutes=30)

    for row in dataset.intervals:
        meter_area = str(row.get('area'))
        if meter_area not in selected_areas:
            continue
        day_value = date.fromisoformat(str(row['date'])[:10])
        if not start_date <= day_value <= end_date:
            continue
        interval_counts[meter_area] += 1
        kwh = _number(row, 'import_kwh')
        totals = area_totals[meter_area]
        daily_row = daily_by_date[day_value]
        totals['kwh'] += kwh
        daily_row['kwh'] = float(daily_row['kwh']) + kwh
        tariff_type = AREA_TARIFF_ASSIGNMENTS.get(meter_area, 'Scale 1')
        if tariff_type == 'CTOU':
            effective_time = interval_time(row)
            effective_day = effective_time.date()
            tariff = ctou_tariff_for_day(ctou_rates, effective_day)
            if tariff is None:
                warnings.append(f'No CTOU tariff is stored for {effective_day:%d %b %Y}.')
                continue
            band = tou_band(effective_time)
            energy_ex = kwh * tariff.energy_rate_ex_vat(effective_day, band)
            band_key = 'off_peak' if band == 'off_peak' else band
            totals[f'{band_key}_kwh'] += kwh
            totals[f'{band_key}_energy_cost_ex_vat'] += energy_ex
            daily_row[f'{band_key}_kwh'] = float(daily_row[f'{band_key}_kwh']) + kwh
            if meter_area == SOLAR_CREDIT_AREA:
                wh8_kwh_by_day_band[(day_value, band)] += kwh
            if band != 'off_peak':
                demand_key = (meter_area, day_value.year, day_value.month)
                kva = _number(row, 'kva')
                previous = monthly_demand.get(demand_key, (0.0, ''))
                if kva > previous[0]:
                    monthly_demand[demand_key] = (kva, str(row.get('timestamp') or ''))
        else:
            tariff = tariff_for_day(rates, day_value)
            if tariff is None:
                warnings.append(f'No Scale 1 tariff is stored for {day_value:%d %b %Y}.')
                continue
            rate_inc = tariff.energy_rate(day_value)
            energy_ex = kwh * rate_inc / 1.15 if tariff.vat_included else kwh * rate_inc
            totals['flat_kwh'] += kwh
            totals['flat_energy_cost_ex_vat'] += energy_ex
            daily_row['flat_kwh'] = float(daily_row['flat_kwh']) + kwh
        totals['energy_cost_ex_vat'] += energy_ex
        daily_row['energy_cost_ex_vat'] = float(daily_row['energy_cost_ex_vat']) + energy_ex
        monthly_energy_ex[(meter_area, day_value.year, day_value.month)] += energy_ex

    # Older tests or imported summaries may contain daily values without the
    # half-hour detail. Keep those visible, but make the approximation explicit.
    for meter_area in selected_areas:
        if interval_counts[meter_area]:
            continue
        warnings.append(
            f'{meter_area}: half-hour readings were unavailable, so the energy '
            'component uses the applicable standard/flat rate and demand is not estimated.'
        )
        for row in dataset.daily:
            if str(row.get('area')) != meter_area:
                continue
            day_value = date.fromisoformat(str(row['date'])[:10])
            if not start_date <= day_value <= end_date:
                continue
            kwh = _number(row, 'import_kwh')
            totals = area_totals[meter_area]
            daily_row = daily_by_date[day_value]
            if AREA_TARIFF_ASSIGNMENTS.get(meter_area) == 'CTOU':
                tariff = ctou_tariff_for_day(ctou_rates, day_value)
                if tariff is None:
                    continue
                energy_ex = kwh * tariff.energy_rate_ex_vat(day_value, 'standard')
                totals['standard_kwh'] += kwh
                totals['standard_energy_cost_ex_vat'] += energy_ex
                daily_row['standard_kwh'] = float(daily_row['standard_kwh']) + kwh
            else:
                tariff = tariff_for_day(rates, day_value)
                if tariff is None:
                    continue
                energy_ex = kwh * tariff.energy_rate(day_value) / 1.15
                totals['flat_kwh'] += kwh
                totals['flat_energy_cost_ex_vat'] += energy_ex
                daily_row['flat_kwh'] = float(daily_row['flat_kwh']) + kwh
            totals['kwh'] += kwh
            totals['energy_cost_ex_vat'] += energy_ex
            daily_row['kwh'] = float(daily_row['kwh']) + kwh
            daily_row['energy_cost_ex_vat'] = float(daily_row['energy_cost_ex_vat']) + energy_ex
            monthly_energy_ex[(meter_area, day_value.year, day_value.month)] += energy_ex

    def selected_month_days(year: int, month: int) -> list[date]:
        first = max(start_date, date(year, month, 1))
        last = min(end_date, date(year, month, monthrange(year, month)[1]))
        if last < first:
            return []
        return [first + timedelta(days=index) for index in range((last - first).days + 1)]

    month_keys = {
        (meter_area, day.year, day.month)
        for meter_area in selected_areas
        for day in daily_by_date
    }
    for meter_area, year, month in sorted(month_keys):
        days = selected_month_days(year, month)
        if not days:
            continue
        fraction = len(days) / monthrange(year, month)[1]
        reference_day = days[0]
        totals = area_totals[meter_area]
        tariff_type = AREA_TARIFF_ASSIGNMENTS.get(meter_area, 'Scale 1')
        demand_ex = 0.0
        network_ex = 0.0
        peak_kva = 0.0
        peak_time = ''
        if tariff_type == 'CTOU':
            tariff = ctou_tariff_for_day(ctou_rates, reference_day)
            if tariff is None:
                continue
            peak_kva, peak_time = monthly_demand.get(
                (meter_area, year, month), (0.0, '')
            )
            if interval_counts[meter_area]:
                chargeable_kva = max(peak_kva, tariff.minimum_demand_kva)
                demand_ex = chargeable_kva * tariff.demand_charge_r_per_kva * fraction
            service_ex = tariff.service_charge_r_per_month * fraction
            energy_ex = monthly_energy_ex[(meter_area, year, month)]
            if peak_kva >= tariff.surcharge_threshold_kva:
                network_ex = (
                    energy_ex + demand_ex
                ) * tariff.network_surcharge_percent / 100.0
        else:
            tariff = tariff_for_day(rates, reference_day)
            if tariff is None:
                continue
            service_ex = tariff.service_charge_r_per_month / 1.15 * fraction

        totals['demand_cost_ex_vat'] += demand_ex
        totals['service_cost_ex_vat'] += service_ex
        totals['network_surcharge_ex_vat'] += network_ex
        if peak_kva > totals['peak_demand_kva']:
            totals['peak_demand_kva'] = peak_kva
            totals['peak_demand_time'] = peak_time  # type: ignore[assignment]
        for day_value in days:
            row = daily_by_date[day_value]
            row['demand_cost_ex_vat'] = float(row['demand_cost_ex_vat']) + demand_ex / len(days)
            row['service_cost_ex_vat'] = float(row['service_cost_ex_vat']) + service_ex / len(days)
            row['network_surcharge_ex_vat'] = (
                float(row['network_surcharge_ex_vat']) + network_ex / len(days)
            )

    apply_solar = (
        SOLAR_CREDIT_AREA in selected_areas and SOLAR_AREA in dataset.areas
    )
    solar_by_day_band: dict[tuple[date, str], float] = defaultdict(float)
    if apply_solar:
        for row in dataset.intervals:
            if str(row.get('area')) != SOLAR_AREA:
                continue
            day_value = date.fromisoformat(str(row['date'])[:10])
            if not start_date <= day_value <= end_date:
                continue
            effective_time = interval_time(row)
            solar_by_day_band[(day_value, tou_band(effective_time))] += _number(
                row, 'import_kwh'
            )

    for (day_value, band), generated_kwh in solar_by_day_band.items():
        tariff = ctou_tariff_for_day(ctou_rates, interval_time({
            'timestamp': f'{day_value.isoformat()} 12:00:00'
        }).date())
        if tariff is None:
            continue
        used_kwh = min(generated_kwh, wh8_kwh_by_day_band[(day_value, band)])
        excess_kwh = max(generated_kwh - used_kwh, 0.0)
        savings_ex = used_kwh * tariff.energy_rate_ex_vat(day_value, band)
        row = daily_by_date[day_value]
        row['solar_generated_kwh'] = float(row['solar_generated_kwh']) + generated_kwh
        row['solar_used_kwh'] = float(row['solar_used_kwh']) + used_kwh
        row['possible_excess_solar_kwh'] = (
            float(row['possible_excess_solar_kwh']) + excess_kwh
        )
        row['solar_avoided_cost'] = (
            float(row['solar_avoided_cost']) + savings_ex * 1.15
        )
        area_totals[SOLAR_CREDIT_AREA]['solar_generated_kwh'] += generated_kwh
        area_totals[SOLAR_CREDIT_AREA]['solar_used_kwh'] += used_kwh
        area_totals[SOLAR_CREDIT_AREA]['possible_excess_solar_kwh'] += excess_kwh
        area_totals[SOLAR_CREDIT_AREA]['solar_avoided_cost'] += savings_ex * 1.15

    daily: list[dict[str, object]] = []
    for day_value, row in daily_by_date.items():
        ex_total = sum(float(row[field]) for field in (
            'energy_cost_ex_vat', 'demand_cost_ex_vat',
            'service_cost_ex_vat', 'network_surcharge_ex_vat',
        ))
        row['energy_cost'] = float(row['energy_cost_ex_vat']) * 1.15
        row['demand_cost'] = float(row['demand_cost_ex_vat']) * 1.15
        row['service_cost'] = float(row['service_cost_ex_vat']) * 1.15
        row['network_surcharge'] = float(row['network_surcharge_ex_vat']) * 1.15
        row['vat'] = ex_total * 0.15
        row['total_cost'] = ex_total * 1.15
        row['energy_cost_after_solar'] = max(
            float(row['energy_cost']) - float(row['solar_avoided_cost']), 0.0
        )
        row['estimated_total_cost'] = max(
            float(row['total_cost']) - float(row['solar_avoided_cost']), 0.0
        )
        qualities = quality_by_day.get(day_value, {})
        row['quality'] = (
            'Complete'
            if len(qualities) == len(selected_areas)
            and all(value == 'Complete' for value in qualities.values())
            else 'Incomplete'
        )
        daily.append(row)

    for meter_area, values in area_totals.items():
        ex_total = sum(values[field] for field in (
            'energy_cost_ex_vat', 'demand_cost_ex_vat',
            'service_cost_ex_vat', 'network_surcharge_ex_vat',
        ))
        values['energy_cost'] = values['energy_cost_ex_vat'] * 1.15
        values['demand_cost'] = values['demand_cost_ex_vat'] * 1.15
        values['service_cost'] = values['service_cost_ex_vat'] * 1.15
        values['network_surcharge'] = values['network_surcharge_ex_vat'] * 1.15
        values['vat'] = ex_total * 0.15
        values['gross_total_cost'] = ex_total * 1.15
        values['total_cost'] = max(
            values['gross_total_cost'] - values['solar_avoided_cost'], 0.0
        )

    period_info: dict[str, object] = {
        'tariff_assignments': {
            meter_area: AREA_TARIFF_ASSIGNMENTS.get(meter_area, 'Scale 1')
            for meter_area in selected_areas
        },
        'peak_kwh': sum(values['peak_kwh'] for values in area_totals.values()),
        'standard_kwh': sum(values['standard_kwh'] for values in area_totals.values()),
        'off_peak_kwh': sum(values['off_peak_kwh'] for values in area_totals.values()),
        'flat_kwh': sum(values['flat_kwh'] for values in area_totals.values()),
    }
    highest_area = max(
        selected_areas,
        key=lambda value: area_totals[value]['peak_demand_kva'],
        default='',
    )
    period_info.update({
        'peak_demand_kva': (
            area_totals[highest_area]['peak_demand_kva'] if highest_area else 0.0
        ),
        'peak_demand_area': highest_area,
        'peak_demand_time': (
            area_totals[highest_area].get('peak_demand_time', '') if highest_area else ''
        ),
    })
    return daily, area_totals, period_info, list(dict.fromkeys(warnings))


def build_cost_analysis(
    dataset: DashboardDataset,
    start_date: date,
    end_date: date,
    area: str,
    rates: Iterable[TariffRate] = BUILT_IN_RATES,
    comparison_start: date | None = None,
    comparison_end: date | None = None,
    ctou_rates: Iterable[CtouTariffRate] = BUILT_IN_CTOU_RATES,
) -> CostAnalysis:
    start_date = max(dataset.first_date, start_date)
    end_date = min(dataset.last_date, end_date)
    if end_date < start_date:
        raise ValueError('The cost period falls outside the available meter history.')
    span = (end_date - start_date).days + 1
    comparison_end = comparison_end or start_date - timedelta(days=1)
    comparison_start = comparison_start or comparison_end - timedelta(days=span - 1)
    if comparison_end < comparison_start:
        raise ValueError('The comparison end date cannot be before its start date.')
    if comparison_start < dataset.first_date or comparison_end > dataset.last_date:
        raise ValueError('The cost comparison dates fall outside the available meter history.')

    schedule = tuple(sorted(rates, key=lambda value: value.effective_start))
    ctou_schedule = tuple(sorted(ctou_rates, key=lambda value: value.effective_start))
    daily, current_areas, current_info, warnings = _period_cost(
        dataset, start_date, end_date, area, schedule, ctou_schedule
    )
    comparison_daily, prior_areas, prior_info, prior_warnings = _period_cost(
        dataset, comparison_start, comparison_end, area, schedule, ctou_schedule
    )
    warnings.extend(prior_warnings)

    current_solar_avoided = sum(float(row['solar_avoided_cost']) for row in daily)
    comparison_solar_avoided = sum(
        float(row['solar_avoided_cost']) for row in comparison_daily
    )
    area_rows: list[dict[str, object]] = []
    all_area_names = sorted(set(current_areas) | set(prior_areas))
    for area_name in all_area_names:
        current = current_areas.get(area_name, defaultdict(float))
        previous = prior_areas.get(area_name, defaultdict(float))
        current_net_total = float(current['total_cost'])
        comparison_net_total = float(previous['total_cost'])
        area_rows.append({
            'area': area_name,
            'tariff': AREA_TARIFF_ASSIGNMENTS.get(area_name, 'Scale 1'),
            'current_kwh': current['kwh'],
            'comparison_kwh': previous['kwh'],
            'current_energy_cost': current['energy_cost'],
            'comparison_energy_cost': previous['energy_cost'],
            'current_demand_cost': current['demand_cost'],
            'comparison_demand_cost': previous['demand_cost'],
            'current_service_cost': current['service_cost'],
            'comparison_service_cost': previous['service_cost'],
            'current_network_surcharge': current['network_surcharge'],
            'comparison_network_surcharge': previous['network_surcharge'],
            'current_vat': current['vat'],
            'comparison_vat': previous['vat'],
            'current_gross_total_cost': current['gross_total_cost'],
            'comparison_gross_total_cost': previous['gross_total_cost'],
            'current_solar_avoided_cost': current['solar_avoided_cost'],
            'comparison_solar_avoided_cost': previous['solar_avoided_cost'],
            'current_total_cost': current_net_total,
            'comparison_total_cost': comparison_net_total,
            'peak_demand_kva': current['peak_demand_kva'],
            'peak_demand_time': current.get('peak_demand_time', ''),
            'cost_change_percent': _percent_change(
                current_net_total, comparison_net_total
            ),
        })
    area_rows.sort(key=lambda row: float(row['current_total_cost']), reverse=True)

    def totals(rows: list[dict[str, object]]) -> dict[str, float]:
        result = {
            key: sum(float(row[key]) for row in rows)
            for key in (
                'kwh',
                'energy_cost',
                'demand_cost',
                'service_cost',
                'network_surcharge',
                'vat',
                'total_cost',
                'solar_generated_kwh',
                'solar_used_kwh',
                'possible_excess_solar_kwh',
                'solar_avoided_cost',
                'energy_cost_after_solar',
                'estimated_total_cost',
            )
        }
        result['average_daily_cost'] = (
            result['estimated_total_cost'] / len(rows) if rows else 0.0
        )
        result['average_daily_kwh'] = result['kwh'] / len(rows) if rows else 0.0
        result['weighted_rate'] = (
            result['energy_cost'] / result['kwh'] if result['kwh'] else 0.0
        )
        result['coverage_percent'] = (
            sum(row['quality'] == 'Complete' for row in rows) / len(rows) * 100.0
            if rows else 0.0
        )
        return result

    current_totals = totals(daily)
    prior_totals = totals(comparison_daily)
    today = date.today()
    latest_closed_date = min(dataset.last_date, today - timedelta(days=1))
    calendar_month_start = today.replace(day=1)
    # Live data projects the real current month. If a historical/test database
    # has no readings in that month, project its latest available month instead
    # of displaying a misleading zero-value current-month forecast.
    current_month_start = (
        calendar_month_start
        if latest_closed_date >= calendar_month_start else
        latest_closed_date.replace(day=1)
    )
    forecast_start = max(dataset.first_date, current_month_start)
    if latest_closed_date >= forecast_start:
        forecast_daily, _, forecast_info, forecast_warnings = _period_cost(
            dataset,
            forecast_start,
            latest_closed_date,
            area,
            schedule,
            ctou_schedule,
        )
        warnings.extend(forecast_warnings)
    else:
        forecast_daily = []
        forecast_info = {
            'peak_demand_kva': 0.0,
            'peak_demand_area': '',
            'peak_demand_time': '',
        }

    # Month-end projections use complete meter days only. A partly populated
    # closed day can otherwise drag the daily average sharply downward and
    # create a misleadingly low forecast.
    forecast_basis_daily = [
        row for row in forecast_daily if row.get('quality') == 'Complete'
    ]
    forecast_through_date = max(
        (date.fromisoformat(str(row['date'])[:10]) for row in forecast_basis_daily),
        default=None,
    )

    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    previous_matching_end = previous_month_start.replace(
        day=min(latest_closed_date.day, previous_month_end.day)
    )
    if (
        latest_closed_date >= current_month_start
        and previous_month_start >= dataset.first_date
    ):
        previous_forecast_daily, _, _, previous_forecast_warnings = _period_cost(
            dataset,
            previous_month_start,
            previous_matching_end,
            area,
            schedule,
            ctou_schedule,
        )
        warnings.extend(previous_forecast_warnings)
    else:
        previous_forecast_daily = []

    current_complete_by_day = {
        date.fromisoformat(str(row['date'])[:10]).day: row
        for row in forecast_basis_daily
    }
    previous_complete_by_day = {
        date.fromisoformat(str(row['date'])[:10]).day: row
        for row in previous_forecast_daily
        if row.get('quality') == 'Complete'
    }
    comparable_day_numbers = sorted(
        set(current_complete_by_day) & set(previous_complete_by_day)
    )
    current_comparable_daily = [
        current_complete_by_day[day_number]
        for day_number in comparable_day_numbers
    ]
    previous_forecast_basis_daily = [
        previous_complete_by_day[day_number]
        for day_number in comparable_day_numbers
    ]

    if previous_month_start >= dataset.first_date:
        previous_full_daily, _, _, previous_full_warnings = _period_cost(
            dataset,
            previous_month_start,
            previous_month_end,
            area,
            schedule,
            ctou_schedule,
        )
        warnings.extend(previous_full_warnings)
    else:
        previous_full_daily = []

    month_days = monthrange(current_month_start.year, current_month_start.month)[1]
    elapsed_days = len(forecast_basis_daily)
    cost_to_date = sum(
        float(row['estimated_total_cost']) for row in forecast_basis_daily
    )
    current_comparable_cost = sum(
        float(row['estimated_total_cost']) for row in current_comparable_daily
    )
    previous_cost_to_date = sum(
        float(row['estimated_total_cost']) for row in previous_forecast_basis_daily
    )
    current_comparable_kwh = sum(
        float(row['kwh']) for row in current_comparable_daily
    )
    previous_comparable_kwh = sum(
        float(row['kwh']) for row in previous_forecast_basis_daily
    )
    forecast_month_end_cost = (
        cost_to_date / elapsed_days * month_days if elapsed_days else 0.0
    )
    cost_without_network_to_date = sum(
        float(row['estimated_total_cost']) - float(row['network_surcharge'])
        for row in forecast_basis_daily
    )
    forecast_month_end_cost_without_network = (
        cost_without_network_to_date / elapsed_days * month_days
        if elapsed_days else 0.0
    )
    forecast_month_end_network_surcharge = max(
        forecast_month_end_cost - forecast_month_end_cost_without_network,
        0.0,
    )
    forecast_month_end_kwh = (
        sum(float(row['kwh']) for row in forecast_basis_daily)
        / elapsed_days * month_days
        if elapsed_days else 0.0
    )
    previous_full_cost = sum(
        float(row['estimated_total_cost']) for row in previous_full_daily
    )
    previous_full_kwh = sum(float(row['kwh']) for row in previous_full_daily)
    previous_full_complete_days = sum(
        row.get('quality') == 'Complete' for row in previous_full_daily
    )
    metrics: dict[str, object] = {
        **current_totals,
        **{f'comparison_{key}': value for key, value in prior_totals.items()},
        **current_info,
        **{f'comparison_{key}': value for key, value in prior_info.items()},
        'cost_change_percent': _percent_change(
            current_totals['estimated_total_cost'],
            prior_totals['estimated_total_cost'],
        ),
        'daily_cost_change_percent': _percent_change(
            current_totals['average_daily_cost'], prior_totals['average_daily_cost']
        ),
        'usage_change_percent': _percent_change(
            current_totals['kwh'], prior_totals['kwh']
        ),
        'daily_usage_change_percent': _percent_change(
            current_totals['average_daily_kwh'], prior_totals['average_daily_kwh']
        ),
        'rate_change_percent': _percent_change(
            current_totals['weighted_rate'], prior_totals['weighted_rate']
        ),
        'solar_utilization_percent': (
            current_totals['solar_used_kwh']
            / current_totals['solar_generated_kwh'] * 100.0
            if current_totals['solar_generated_kwh'] else 0.0
        ),
        'solar_cost_avoided_percent': (
            current_totals['solar_avoided_cost']
            / current_totals['energy_cost'] * 100.0
            if current_totals['energy_cost'] else 0.0
        ),
        'solar_avoided_change_percent': _percent_change(
            current_totals['solar_avoided_cost'],
            prior_totals['solar_avoided_cost'],
        ),
        'forecast_month': current_month_start.strftime('%B %Y'),
        'forecast_through_date': (
            forecast_through_date.isoformat() if forecast_through_date else ''
        ),
        'forecast_elapsed_days': elapsed_days,
        'forecast_excluded_days': max(len(forecast_daily) - elapsed_days, 0),
        'forecast_previous_days': len(previous_forecast_basis_daily),
        'forecast_comparable_days': len(comparable_day_numbers),
        'forecast_days_in_month': month_days,
        'forecast_cost_to_date': cost_to_date,
        'forecast_current_comparable_cost': current_comparable_cost,
        'forecast_current_comparable_kwh': current_comparable_kwh,
        'forecast_previous_comparable_kwh': previous_comparable_kwh,
        'forecast_previous_start': previous_month_start.isoformat(),
        'forecast_previous_end': previous_matching_end.isoformat(),
        'forecast_previous_cost_to_date': previous_cost_to_date,
        'forecast_mtd_cost_change_percent': _percent_change(
            current_comparable_cost,
            previous_cost_to_date,
        ),
        'forecast_mtd_usage_change_percent': _percent_change(
            current_comparable_kwh,
            previous_comparable_kwh,
        ),
        'forecast_month_end_cost': forecast_month_end_cost,
        'forecast_month_end_cost_without_network_surcharge': (
            forecast_month_end_cost_without_network
        ),
        'forecast_month_end_network_surcharge': forecast_month_end_network_surcharge,
        'forecast_network_surcharge_active': forecast_month_end_network_surcharge > 0,
        'forecast_peak_demand_kva': float(forecast_info.get('peak_demand_kva') or 0.0),
        'forecast_peak_demand_area': str(forecast_info.get('peak_demand_area') or ''),
        'forecast_peak_demand_time': str(forecast_info.get('peak_demand_time') or ''),
        'forecast_month_end_kwh': forecast_month_end_kwh,
        'forecast_previous_full_month': previous_month_start.strftime('%B %Y'),
        'forecast_previous_full_cost': previous_full_cost,
        'forecast_previous_full_kwh': previous_full_kwh,
        'forecast_previous_full_complete_days': previous_full_complete_days,
        'forecast_previous_full_days': len(previous_full_daily),
        'forecast_vs_previous_full_cost_percent': _percent_change(
            forecast_month_end_cost,
            previous_full_cost,
        ),
        'forecast_vs_previous_full_usage_percent': _percent_change(
            forecast_month_end_kwh,
            previous_full_kwh,
        ),
        'forecast_remaining_cost': max(forecast_month_end_cost - cost_to_date, 0.0),
        'current_days': len(daily),
        'comparison_days': len(comparison_daily),
        'highest_cost_area': area_rows[0]['area'] if area_rows else 'No area',
        'highest_cost_area_value': area_rows[0]['current_total_cost'] if area_rows else 0.0,
    }
    used_rates = [
        rate for rate in schedule
        if (
            rate.effective_start <= end_date and rate.effective_end >= start_date
        ) or (
            rate.effective_start <= comparison_end
            and rate.effective_end >= comparison_start
        )
    ]
    tariff_rows = [{
        'tariff': rate.tariff_name,
        'period': f'{rate.effective_start:%d %b %Y} to {rate.effective_end:%d %b %Y}',
        'high_rate': rate.high_season_r_per_kwh,
        'low_rate': rate.low_season_r_per_kwh,
        'service_charge': rate.service_charge_r_per_month,
        'peak_rate': None,
        'standard_rate': None,
        'off_peak_rate': None,
        'demand_charge': None,
        'network_surcharge_percent': None,
        'vat_included': True,
        'source_url': rate.source_url,
        'source_label': rate.source_label,
    } for rate in used_rates]
    used_ctou_rates = [
        rate for rate in ctou_schedule
        if (
            rate.effective_start <= end_date and rate.effective_end >= start_date
        ) or (
            rate.effective_start <= comparison_end
            and rate.effective_end >= comparison_start
        )
    ]
    for rate in used_ctou_rates:
        reference = end_date if rate.effective_start <= end_date <= rate.effective_end else rate.effective_start
        season = 'high' if reference.month in {6, 7, 8} else 'low'
        tariff_rows.append({
            'tariff': rate.tariff_name,
            'period': f'{rate.effective_start:%d %b %Y} to {rate.effective_end:%d %b %Y}',
            'high_rate': None,
            'low_rate': None,
            'peak_rate': rate.energy_rate_inc_vat(reference, 'peak'),
            'standard_rate': rate.energy_rate_inc_vat(reference, 'standard'),
            'off_peak_rate': rate.energy_rate_inc_vat(reference, 'off_peak'),
            'season': season.title(),
            'service_charge': rate.service_charge_r_per_month * 1.15,
            'demand_charge': rate.demand_charge_r_per_kva * 1.15,
            'network_surcharge_percent': rate.network_surcharge_percent,
            'vat_included': True,
            'source_url': rate.source_url,
            'source_label': rate.source_label,
        })
    tariff_rows.sort(key=lambda row: (str(row['period']), str(row['tariff'])))
    return CostAnalysis(
        start_date=start_date,
        end_date=end_date,
        comparison_start=comparison_start,
        comparison_end=comparison_end,
        area=area,
        daily=daily,
        comparison_daily=comparison_daily,
        area_rows=area_rows,
        tariff_rows=tariff_rows,
        metrics=metrics,
        warnings=tuple(dict.fromkeys(warnings)),
    )
