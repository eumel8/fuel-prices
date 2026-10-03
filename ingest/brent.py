from __future__ import annotations

import csv
import io
import logging

import httpx

from app import db
from app.config import FRED_BRENT_URL, HTTP_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

SOURCE = "fred_eia_dcoilbrenteu"

BENCHMARKS = {
    "DCOILBRENTEU": "brent",
    "DCOILWTICO": "wti",
}


def fetch(series_id: str) -> list[tuple]:
    """Return (benchmark, day, value, unit, currency) rows from the FRED CSV endpoint."""
    response = httpx.get(
        FRED_BRENT_URL.replace("DCOILBRENTEU", series_id),
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()

    rows: list[tuple] = []
    for record in csv.DictReader(io.StringIO(response.text)):
        raw_day = (record.get("observation_date") or "").strip()
        raw_value = (record.get(series_id) or "").strip()
        if not raw_day or not raw_value or raw_value == ".":
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if value <= 0:
            continue
        rows.append((BENCHMARKS[series_id], raw_day, value, "USD/barrel", "USD"))
    return rows


def ingest() -> int:
    started = db.utcnow()
    total = 0
    with db.connect() as conn:
        for series_id in BENCHMARKS:
            rows = fetch(series_id)
            if not rows:
                raise RuntimeError(f"keine Daten von FRED fuer {series_id}")
            written = db.upsert_oil_prices(
                conn, rows, source=f"fred_eia_{series_id.lower()}", ingested_at=db.utcnow()
            )
            total += written
            log.info("%s: %d Zeilen geschrieben", series_id, written)
        db.record_run(conn, SOURCE, started, rows=total, status="ok")
    return total
