from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import UTC, date

from app.config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_points (
    country     TEXT NOT NULL,
    fuel        TEXT NOT NULL,
    basis       TEXT NOT NULL,
    granularity TEXT NOT NULL,
    day         TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT NOT NULL,
    currency    TEXT NOT NULL,
    source      TEXT NOT NULL,
    sample_size INTEGER,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (country, fuel, basis, granularity, day)
);

CREATE INDEX IF NOT EXISTS idx_price_points_lookup
    ON price_points (country, fuel, basis, granularity, day);

CREATE TABLE IF NOT EXISTS oil_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark   TEXT NOT NULL,
    day         TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT NOT NULL,
    currency    TEXT NOT NULL,
    source      TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    UNIQUE (benchmark, day)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    rows       INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL,
    message    TEXT
);

CREATE TABLE IF NOT EXISTS station_prices (
    country   TEXT NOT NULL,
    station_id TEXT NOT NULL,
    day       TEXT NOT NULL,
    e5        REAL,
    e10       REAL,
    diesel    REAL,
    lat       REAL,
    lng       REAL,
    place     TEXT,
    brand     TEXT,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (country, station_id, day)
);
"""


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def upsert_price_points(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence],
    *,
    source: str,
    ingested_at: str,
) -> int:
    """rows: (country, fuel, basis, granularity, day, value, unit, currency, sample_size)"""
    payload = [
        (
            country,
            fuel,
            basis,
            granularity,
            day,
            value,
            unit,
            currency,
            source,
            sample_size,
            ingested_at,
        )
        for country, fuel, basis, granularity, day, value, unit, currency, sample_size in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO price_points
            (country, fuel, basis, granularity, day, value, unit, currency, source,
             sample_size, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (country, fuel, basis, granularity, day) DO UPDATE SET
            value       = excluded.value,
            unit        = excluded.unit,
            currency    = excluded.currency,
            source      = excluded.source,
            sample_size = excluded.sample_size,
            ingested_at = excluded.ingested_at
        """,
        payload,
    )
    return len(payload)


def upsert_oil_prices(
    conn: sqlite3.Connection,
    rows: Iterable[tuple],
    *,
    source: str,
    ingested_at: str,
) -> int:
    payload = [
        (benchmark, day, value, unit, currency, source, ingested_at)
        for benchmark, day, value, unit, currency in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO oil_prices (benchmark, day, value, unit, currency, source, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (benchmark, day) DO UPDATE SET
            value       = excluded.value,
            unit        = excluded.unit,
            currency    = excluded.currency,
            source      = excluded.source,
            ingested_at = excluded.ingested_at
        """,
        payload,
    )
    return len(payload)


def record_run(
    conn: sqlite3.Connection,
    source: str,
    started_at: str,
    *,
    rows: int,
    status: str,
    message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_runs (source, started_at, ended_at, rows, status, message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source, started_at, utcnow(), rows, status, message),
    )


def utcnow() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def iso(day: date) -> str:
    return day.isoformat()
