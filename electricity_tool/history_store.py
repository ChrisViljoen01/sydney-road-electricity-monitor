from __future__ import annotations

import csv
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Iterable
from collections.abc import Iterator

from .config import (
    ACCOUNT_COMPLETE_HISTORY_START_DATES,
    DEFAULT_EXPORT_DIR,
    DEFAULT_HISTORY_DB,
    HISTORY_START_DATE,
)
from .export import INTERVAL_FIELDS


OPTIONAL_NUMERIC_FIELDS = {
    'power_factor', 'raw_p1', 'raw_p2', 'raw_q1', 'raw_q2', 'raw_q3', 'raw_q4',
    'raw_s', 'raw_scalar_s',
}
NUMERIC_FIELDS = {
    'period_seconds', 'kw_import', 'kw_export', 'kw_net', 'kvar_net', 'kva',
    'import_kwh', 'export_kwh', 'net_kwh', *OPTIONAL_NUMERIC_FIELDS,
}


@dataclass(frozen=True)
class HistoryWriteResult:
    received: int
    inserted: int
    replaced: int


class HistoryStore:
    """Persistent interval-reading store keyed by meter account and timestamp."""

    def __init__(self, path: str | Path = DEFAULT_HISTORY_DB) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA synchronous=NORMAL')
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS interval_readings (
                    date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    account_code TEXT NOT NULL,
                    account_eid TEXT NOT NULL,
                    area TEXT NOT NULL,
                    period_seconds INTEGER NOT NULL,
                    kw_import REAL NOT NULL,
                    kw_export REAL NOT NULL,
                    kw_net REAL NOT NULL,
                    kvar_net REAL NOT NULL,
                    kva REAL NOT NULL,
                    import_kwh REAL NOT NULL,
                    export_kwh REAL NOT NULL,
                    net_kwh REAL NOT NULL,
                    power_factor REAL,
                    kva_method TEXT NOT NULL,
                    raw_p1 REAL,
                    raw_p2 REAL,
                    raw_q1 REAL,
                    raw_q2 REAL,
                    raw_q3 REAL,
                    raw_q4 REAL,
                    raw_s REAL,
                    raw_scalar_s REAL,
                    source_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    PRIMARY KEY (account_eid, timestamp)
                );
                CREATE INDEX IF NOT EXISTS idx_interval_date ON interval_readings(date);
                CREATE INDEX IF NOT EXISTS idx_interval_area_date ON interval_readings(area, date);
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    completed_at TEXT NOT NULL,
                    requested_start_date TEXT NOT NULL,
                    requested_end_date TEXT NOT NULL,
                    source_folder TEXT NOT NULL,
                    rows_received INTEGER NOT NULL,
                    rows_inserted INTEGER NOT NULL,
                    rows_replaced INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _db_value(field: str, value: object) -> object:
        if field == 'period_seconds':
            return int(float(value or 0))
        if field in NUMERIC_FIELDS:
            if value in {'', None}:
                return None if field in OPTIONAL_NUMERIC_FIELDS else 0.0
            return float(value)
        return '' if value is None else str(value)

    def upsert_rows(self, rows: Iterable[dict]) -> HistoryWriteResult:
        unique: dict[tuple[str, str], dict] = {}
        history_start = date.fromisoformat(HISTORY_START_DATE)
        for row in rows:
            row_date = date.fromisoformat(str(row['date'])[:10])
            if row_date < history_start:
                continue
            unique[(str(row['account_eid']), str(row['timestamp']))] = row
        materialized = list(unique.values())
        if not materialized:
            return HistoryWriteResult(0, 0, 0)

        columns = list(INTERVAL_FIELDS)
        placeholders = ', '.join('?' for _ in columns)
        updates = ', '.join(
            f'{field}=excluded.{field}' for field in columns
            if field not in {'account_eid', 'timestamp'}
        )
        sql = (
            f"INSERT INTO interval_readings ({', '.join(columns)}, stored_at) "
            f"VALUES ({placeholders}, ?) "
            f"ON CONFLICT(account_eid, timestamp) DO UPDATE SET {updates}, stored_at=excluded.stored_at"
        )
        keys = list(unique)
        with self._lock, self._connection() as connection:
            existing = 0
            for offset in range(0, len(keys), 400):
                batch = keys[offset:offset + 400]
                predicates = ' OR '.join('(account_eid=? AND timestamp=?)' for _ in batch)
                parameters = [part for key in batch for part in key]
                existing += int(connection.execute(
                    f'SELECT COUNT(*) FROM interval_readings WHERE {predicates}', parameters
                ).fetchone()[0])
            stored_at = datetime.now().isoformat(timespec='seconds')
            connection.executemany(
                sql,
                [
                    tuple(self._db_value(field, row.get(field, '')) for field in columns)
                    + (stored_at,)
                    for row in materialized
                ],
            )
        return HistoryWriteResult(
            received=len(materialized),
            inserted=len(materialized) - existing,
            replaced=existing,
        )

    def ingest_run(self, run_dir: str | Path) -> HistoryWriteResult:
        folder = Path(run_dir).expanduser().resolve()
        path = folder / 'interval_readings.csv'
        with path.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        return self.upsert_rows(rows)

    def record_sync(
        self,
        start_date: date,
        end_date: date,
        source_folder: str,
        result: HistoryWriteResult,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs (
                    completed_at, requested_start_date, requested_end_date, source_folder,
                    rows_received, rows_inserted, rows_replaced
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec='seconds'),
                    start_date.isoformat(), end_date.isoformat(), source_folder,
                    result.received, result.inserted, result.replaced,
                ),
            )

    def _metadata(self, key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return None if row is None else str(row['value'])

    def _set_metadata(self, key: str, value: object) -> None:
        encoded = value if isinstance(value, str) else json.dumps(value)
        with self._lock, self._connection() as connection:
            connection.execute(
                'INSERT INTO metadata(key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, encoded),
            )

    @staticmethod
    def _daily_peak_alert_key(alert_date: date) -> str:
        return f'daily_peak_alert_sent:{alert_date.isoformat()}'

    def daily_peak_alert_sent(self, alert_date: date) -> bool:
        """Return whether an automated daily peak email was recorded as sent."""
        return self._metadata(self._daily_peak_alert_key(alert_date)) is not None

    def record_daily_peak_alert_sent(
        self,
        alert_date: date,
        sent_at: datetime,
    ) -> None:
        """Persist the delivery marker only after Microsoft Graph accepts the email."""
        self._set_metadata(
            self._daily_peak_alert_key(alert_date),
            sent_at.astimezone().isoformat(timespec='seconds'),
        )

    def bootstrap_from_exports(self, export_root: str | Path = DEFAULT_EXPORT_DIR) -> HistoryWriteResult:
        if self._metadata('exports_bootstrapped_v1') == 'complete':
            return HistoryWriteResult(0, 0, 0)
        root = Path(export_root).expanduser().resolve()
        folders = sorted(
            folder for folder in root.iterdir()
            if folder.is_dir()
            and (folder / 'run_manifest.json').exists()
            and (folder / 'interval_readings.csv').exists()
        ) if root.exists() else []
        merged: dict[tuple[str, str], dict] = {}
        for folder in folders:
            with (folder / 'interval_readings.csv').open(encoding='utf-8-sig', newline='') as handle:
                for row in csv.DictReader(handle):
                    if str(row.get('date', '')) >= HISTORY_START_DATE:
                        merged[(str(row['account_eid']), str(row['timestamp']))] = row
        result = self.upsert_rows(merged.values())
        self._set_metadata('exports_bootstrapped_v1', 'complete')
        self._set_metadata('bootstrap_source_folders', len(folders))
        self._set_metadata('bootstrap_completed_at', datetime.now().isoformat(timespec='seconds'))
        return result

    def interval_rows(self) -> list[dict[str, str]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT {', '.join(INTERVAL_FIELDS)} FROM interval_readings "
                'WHERE date >= ? ORDER BY account_eid, timestamp',
                (HISTORY_START_DATE,),
            ).fetchall()
        return [
            {
                field: '' if row[field] is None else str(row[field])
                for field in INTERVAL_FIELDS
            }
            for row in rows
        ]

    def latest_timestamp(self) -> datetime | None:
        with self._connection() as connection:
            value = connection.execute('SELECT MAX(timestamp) FROM interval_readings').fetchone()[0]
        return None if value is None else datetime.fromisoformat(str(value))

    def missing_date_ranges(
        self,
        start_date: date,
        end_date: date,
        expected_account_eids: Iterable[str],
    ) -> list[tuple[date, date]]:
        """Return contiguous dates without 48 readings for every configured meter."""
        if end_date < start_date:
            return []
        expected = tuple(dict.fromkeys(str(eid) for eid in expected_account_eids))
        if not expected:
            return [(start_date, end_date)]
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT date, account_eid, COUNT(*) AS samples
                FROM interval_readings
                WHERE date BETWEEN ? AND ?
                GROUP BY date, account_eid
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        coverage = {
            (str(row['date']), str(row['account_eid'])): int(row['samples'])
            for row in rows
        }
        missing: list[date] = []
        account_starts = {
            eid: date.fromisoformat(
                ACCOUNT_COMPLETE_HISTORY_START_DATES.get(eid, HISTORY_START_DATE)
            )
            for eid in expected
        }
        current = start_date
        while current <= end_date:
            day = current.isoformat()
            active_accounts = [eid for eid in expected if current >= account_starts[eid]]
            if active_accounts and any(
                coverage.get((day, eid), 0) < 48 for eid in active_accounts
            ):
                missing.append(current)
            current += timedelta(days=1)
        ranges: list[tuple[date, date]] = []
        for missing_date in missing:
            if ranges and missing_date == ranges[-1][1] + timedelta(days=1):
                ranges[-1] = (ranges[-1][0], missing_date)
            else:
                ranges.append((missing_date, missing_date))
        return ranges

    def stats(self) -> dict[str, object]:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT COUNT(*) AS row_count, COUNT(DISTINCT account_eid) AS meter_count, '
                'MIN(timestamp) AS first_timestamp, MAX(timestamp) AS latest_timestamp '
                'FROM interval_readings'
            ).fetchone()
            sync_count = int(connection.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0])
        return {
            'row_count': int(row['row_count']),
            'meter_count': int(row['meter_count']),
            'first_timestamp': row['first_timestamp'],
            'latest_timestamp': row['latest_timestamp'],
            'sync_count': sync_count,
            'path': str(self.path),
        }
