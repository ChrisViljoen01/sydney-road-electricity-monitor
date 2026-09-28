from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime
from html.parser import HTMLParser
import io
import math
import statistics
from typing import Iterable
import xml.etree.ElementTree as ET

from .models import MeterAccount, Reading
from .portal import ProfileAccessError


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)


def parse_datetime(value: str) -> datetime:
    cleaned = value.strip()
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    raise ValueError(f"Unsupported PNPSCADA date: {value!r}")


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().replace(" ", "")
    if not cleaned or cleaned.lower() in {"null", "none", "nan", "-"}:
        return None
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _infer_period(timestamps: list[datetime], declared_periods: list[int]) -> int:
    valid = [value for value in declared_periods if value > 0]
    if valid:
        return Counter(valid).most_common(1)[0][0]
    differences = [
        int((right - left).total_seconds())
        for left, right in zip(timestamps, timestamps[1:])
        if right > left
    ]
    if differences:
        return int(statistics.median(differences))
    return 1800


def _build_readings(
    samples: list[tuple[datetime, dict[str, str]]],
    account: MeterAccount,
    declared_periods: list[int],
    source: str,
) -> list[Reading]:
    samples.sort(key=lambda item: item[0])
    if not samples:
        raise ProfileAccessError(f"PNPSCADA returned no samples for {account.name}")
    period_seconds = _infer_period([item[0] for item in samples], declared_periods)
    hours = period_seconds / 3600.0
    readings: list[Reading] = []
    for timestamp, fields in samples:
        p1 = _number(fields.get("p1"))
        p2 = _number(fields.get("p2"))
        q1 = _number(fields.get("q1"))
        q2 = _number(fields.get("q2"))
        q3 = _number(fields.get("q3"))
        q4 = _number(fields.get("q4"))
        kw_import = p1 or 0.0
        kw_export = p2 or 0.0
        kw_net = kw_import - kw_export
        kvar_net = (q1 or 0.0) + (q2 or 0.0) - (q3 or 0.0) - (q4 or 0.0)
        raw_s = _number(fields.get("s"))
        raw_scalar_s = _number(fields.get("scalar_s"))
        source_kva = raw_s if raw_s is not None else raw_scalar_s
        if source_kva is None:
            kva = math.sqrt((kw_net * kw_net) + (kvar_net * kvar_net))
            kva_method = "calculated_from_p_q"
        else:
            kva = abs(source_kva)
            kva_method = "pnpscada_source"
        power_factor = abs(kw_net) / kva if kva > 0 else None
        readings.append(
            Reading(
                timestamp=timestamp,
                account_name=account.name,
                account_code=account.code,
                account_eid=account.eid,
                period_seconds=period_seconds,
                kw_import=kw_import,
                kw_export=kw_export,
                kw_net=kw_net,
                kvar_net=kvar_net,
                kva=kva,
                import_kwh=kw_import * hours,
                export_kwh=kw_export * hours,
                net_kwh=kw_net * hours,
                power_factor=power_factor,
                kva_method=kva_method,
                raw_p1=p1,
                raw_p2=p2,
                raw_q1=q1,
                raw_q2=q2,
                raw_q3=q3,
                raw_q4=q4,
                source=source,
                source_status=fields.get("status", "").strip(),
                raw_s=raw_s,
                raw_scalar_s=raw_scalar_s,
                area=account.area or account.name,
            )
        )
    return readings


def parse_account_profile(xml_text: str, account: MeterAccount) -> list[Reading]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProfileAccessError(f"PNPSCADA returned invalid XML for {account.name}: {exc}") from exc

    results = [
        (element.text or "").strip()
        for element in root.iter()
        if _local_name(element.tag) == "result"
    ]
    errors = [value for value in results if value.upper().startswith("ERROR")]
    if errors:
        raise ProfileAccessError(f"PNPSCADA profile error for {account.name}: {errors[0]}")

    declared_periods: list[int] = []
    for element in root.iter():
        if _local_name(element.tag) == "period":
            value = _number(element.text)
            if value:
                declared_periods.append(int(value))

    samples: list[tuple[datetime, dict[str, str]]] = []
    for sample in root.iter():
        if _local_name(sample.tag) != "sample":
            continue
        fields = {
            _local_name(child.tag): (child.text or "").strip()
            for child in list(sample)
        }
        if not fields.get("date"):
            continue
        samples.append((parse_datetime(fields["date"]), fields))
    return _build_readings(samples, account, declared_periods, "getMeterAccountProfile.jsp")


def _profile_csv_field(header: str) -> str | None:
    cleaned = " ".join(header.strip().strip('"').lower().split())
    if cleaned == "date":
        return "date"
    if cleaned == "time":
        return "time"
    if cleaned == "status":
        return "status"
    if cleaned.startswith("scalar sum s"):
        return "scalar_s"
    for field in ("p1", "p2", "q1", "q2", "q3", "q4"):
        if cleaned == field or cleaned.startswith(field + " "):
            return field
    if cleaned == "s" or cleaned.startswith("s "):
        return "s"
    return None


def parse_profile_graph_csv(
    csv_text: str,
    account: MeterAccount,
    start_date: date | None = None,
    end_date_inclusive: date | None = None,
) -> list[Reading]:
    """Parse the CSV produced by Profile Graph -> View -> Download CSV."""

    rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff")), skipinitialspace=True))
    header_index: int | None = None
    field_indexes: dict[str, int] = {}
    for index, row in enumerate(rows):
        mapped = {
            field: position
            for position, heading in enumerate(row)
            if (field := _profile_csv_field(heading)) is not None
        }
        if {"date", "time"}.issubset(mapped) and ({"p1", "p2", "s"} & set(mapped)):
            header_index = index
            field_indexes = mapped
            break
    if header_index is None:
        raise ProfileAccessError(f"PNPSCADA Profile Graph CSV headers were not found for {account.name}")

    samples: list[tuple[datetime, dict[str, str]]] = []
    for row in rows[header_index + 1 :]:
        if not row or max(field_indexes.values(), default=-1) >= len(row):
            continue
        fields = {field: row[position].strip() for field, position in field_indexes.items()}
        if not fields.get("date") or not fields.get("time"):
            continue
        timestamp = parse_datetime(f"{fields['date']} {fields['time']}")
        if start_date is not None and timestamp.date() < start_date:
            continue
        if end_date_inclusive is not None and timestamp.date() > end_date_inclusive:
            continue
        samples.append((timestamp, fields))
    return _build_readings(samples, account, [], "Profile Graph Download CSV")


class ReportTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if lowered == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
        elif lowered == "tr" and self._table_depth == 1:
            self._row = []
        elif lowered in {"td", "th"} and self._table_depth == 1 and self._row is not None:
            self._cell_parts = []
        elif lowered == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style"}:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if (
            lowered in {"td", "th"}
            and self._table_depth == 1
            and self._cell_parts is not None
            and self._row is not None
        ):
            value = " ".join("".join(self._cell_parts).split())
            self._row.append(value)
            self._cell_parts = None
        elif (
            lowered == "tr"
            and self._table_depth == 1
            and self._row is not None
            and self._table is not None
        ):
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif lowered == "table" and self._table_depth:
            if self._table_depth == 1 and self._table:
                self.tables.append(self._table)
                self._table = None
            self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and self._cell_parts is not None:
            self._cell_parts.append(data)


def parse_report_tables(html: str) -> list[list[list[str]]]:
    parser = ReportTableParser()
    parser.feed(html)
    return [table for table in parser.tables if any(len(row) >= 2 for row in table)]
