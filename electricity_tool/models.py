from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MeterAccount:
    name: str
    code: str
    eid: str
    area: str = ""


@dataclass(frozen=True)
class Reading:
    timestamp: datetime
    account_name: str
    account_code: str
    account_eid: str
    period_seconds: int
    kw_import: float
    kw_export: float
    kw_net: float
    kvar_net: float
    kva: float
    import_kwh: float
    export_kwh: float
    net_kwh: float
    power_factor: float | None
    kva_method: str
    raw_p1: float | None
    raw_p2: float | None
    raw_q1: float | None
    raw_q2: float | None
    raw_q3: float | None
    raw_q4: float | None
    source: str = "getMeterAccountProfile.jsp"
    source_status: str = ""
    raw_s: float | None = None
    raw_scalar_s: float | None = None
    area: str = ""

    def to_csv_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["timestamp"] = self.timestamp.isoformat(sep=" ")
        row["date"] = self.timestamp.date().isoformat()
        return {"date": row.pop("date"), **row}
