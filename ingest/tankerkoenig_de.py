"""Tageswerte fuer Deutschland aus der Tankerkönig-Open-Data-API.

Die MTS-K-Daten des Bundeskartellamts sind nur zugelassenen
Verbraucher-Informationsdiensten zugänglich. Tankerkönig stellt dieselben
Rohdaten unter CC BY 4.0 als REST-API bereit (nicht-kommerzielle Nutzung).

Wir fragen kein bundesweites Raster aller ~15.000 Tankstellen ab, sondern ein
geplantes Raster ueber die Bundesrepublik. Das haelt die Last niedrig und liefert
eine repräsentative Stichprobe fuer den Tagesdurchschnitt.

Ohne eigenen API-Key liefert der offizielle Demo-Key ausschliesslich Tankstellen
im Raum Berlin. Das Skript erkennt das und warnt.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import statistics
import time

import httpx

from app import db
from app.config import (
    HTTP_TIMEOUT,
    TANKERKOENIG_API_KEY,
    TANKERKOENIG_DEMO_KEY,
    TANKERKOENIG_URL,
    USER_AGENT,
)

log = logging.getLogger(__name__)

SOURCE = "tankerkoenig_open_data"
COUNTRY = "DE"

RADIUS_KM = 25
LATITUDES = [47.6, 48.6, 49.6, 50.6, 51.6, 52.6, 53.6, 54.5]
LONGITUDES = [6.2, 7.7, 9.2, 10.7, 12.2, 13.7, 15.0]

FUELS = ("e5", "e10", "diesel")
# Bei type=all liefert die API die Felder e5/e10/diesel. Mit type=<fuel>
# heisst das E5-Feld dagegen "price", deshalb der Fallback.
PRICE_KEYS = {"e5": ("e5", "price"), "e10": ("e10", "price_e10"), "diesel": ("diesel",)}

# Plausibilitaetsgrenzen in EUR/l, darunter liegen Fehlmeldungen ("-1") oder
# Nachtabschlaege, darueber Tippfehler im Datenfeed.
MIN_PLAUSIBLE = 0.5
MAX_PLAUSIBLE = 5.0

# Ohne Pause zwischen den Abfragen drosselt die API: gemessen auf dem Cluster
# lieferten 30 von 56 Rasterpunkten HTTP 503, und der Tagesdurchschnitt waere
# aus einer raeumlich verzerrten Teilmenge entstanden.
REQUEST_DELAY_SECONDS = float(os.environ.get("TANKERKOENIG_DELAY", "1.5"))
# Das Kontingent der API erholt sich erst nach 15-30 s (gemessen); ein
# Backoff von 2+4 s liess die letzten 13 Rasterpunkte dauerhaft scheitern.
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = float(os.environ.get("TANKERKOENIG_BACKOFF", "15"))
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# Verloren mehr als ein Viertel der Rasterpunkte, ist der Durchschnitt nicht
# mehr bundesweit repraesentativ. Dann lieber gar nichts schreiben.
MAX_FAILED_RATIO = 0.25


def grid() -> list[tuple[float, float]]:
    return [(lat, lng) for lat in LATITUDES for lng in LONGITUDES]


def fetch_area(client: httpx.Client, lat: float, lng: float) -> list[dict]:
    params = {
        "lat": f"{lat:.4f}",
        "lng": f"{lng:.4f}",
        "rad": str(RADIUS_KM),
        "sort": "dist",
        "type": "all",
        "apikey": TANKERKOENIG_API_KEY,
    }
    last = "unbekannter Fehler"
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = client.get(TANKERKOENIG_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # Nur den Status nennen: in der Exception-URL steht apikey=...
            status = exc.response.status_code
            last = f"HTTP {status}"
            if status not in RETRY_STATUS or attempt == RETRY_ATTEMPTS:
                break
        except httpx.HTTPError as exc:
            last = type(exc).__name__
            if attempt == RETRY_ATTEMPTS:
                break
        except ValueError:
            last = "keine JSON-Antwort"
            break
        else:
            if not payload.get("ok"):
                raise RuntimeError(f"Tankerkönig API Fehler: {payload.get('message') or payload}")
            return payload.get("stations") or []
        time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(last)


def _collect_all(
    client: httpx.Client | None, points: list[tuple[float, float]]
) -> tuple[dict[str, dict], int]:
    owns_client = client is None
    client = client or httpx.Client(
        timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    stations: dict[str, dict] = {}
    failed = 0
    try:
        for index, (lat, lng) in enumerate(points, 1):
            if index > 1 and REQUEST_DELAY_SECONDS > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                found = fetch_area(client, lat, lng)
            except (httpx.HTTPError, RuntimeError) as exc:
                failed += 1
                log.warning("Rasterpunkt %.2f/%.2f fehlgeschlagen: %s", lat, lng, exc)
                continue
            for station in found:
                stations[str(station["id"])] = station
            log.info(
                "Raster %d/%d -> %d Tankstellen (gesamt %d)",
                index, len(points), len(found), len(stations),
            )
    finally:
        if owns_client:
            client.close()
    return stations, failed


def collect(client: httpx.Client | None = None, *, max_requests: int | None = None) -> list[dict]:
    points = grid()
    if max_requests is not None:
        points = points[:max_requests]
    stations, _failed = _collect_all(client, points)
    return list(stations.values())


def _price(station: dict, fuel: str) -> float | None:
    for key in PRICE_KEYS[fuel]:
        raw = station.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if MIN_PLAUSIBLE <= value <= MAX_PLAUSIBLE:
            return value
    return None


def summarise(stations: list[dict], day: str) -> list[tuple]:
    """Build upsert rows for the daily German mean, plus sanity logging."""
    rows: list[tuple] = []
    for fuel in FUELS:
        values = [v for v in (_price(s, fuel) for s in stations) if v is not None]
        if not values:
            log.warning("keine verwertbaren %s-Preise am %s", fuel, day)
            continue
        mean = round(statistics.fmean(values), 6)
        rows.append(
            (
                COUNTRY,
                fuel,
                "incl_taxes",
                "daily",
                day,
                mean,
                "EUR/l",
                "EUR",
                len(values),
            )
        )
        log.info(
            "DE %s %s: Mittel %.3f EUR/l aus %d Tankstellen (Median %.3f, min %.3f, max %.3f)",
            fuel, day, mean, len(values), statistics.median(values), min(values), max(values),
        )
    return rows


def _station_rows(stations: list[dict], day: str) -> list[tuple]:
    now = db.utcnow()
    out = []
    for station in stations:
        coords = station.get("coords") or {}
        out.append(
            (
                COUNTRY,
                str(station.get("id")),
                day,
                _price(station, "e5"),
                _price(station, "e10"),
                _price(station, "diesel"),
                coords.get("lat"),
                coords.get("lng"),
                (station.get("place") or "")[:120] or None,
                (station.get("brand") or None),
                now,
            )
        )
    return out


def ingest(*, day: dt.date | None = None, max_requests: int | None = None) -> int:
    started = db.utcnow()
    day = day or dt.date.today()
    points = grid()
    if max_requests is not None:
        points = points[:max_requests]
    stations_map, failed = _collect_all(None, points)
    stations = list(stations_map.values())
    if not stations:
        raise RuntimeError("keine Tankstellen von Tankerkönig erhalten")
    if points and failed / len(points) > MAX_FAILED_RATIO:
        raise RuntimeError(
            f"{failed} von {len(points)} Rasterpunkten fehlgeschlagen "
            f"(mehr als {MAX_FAILED_RATIO:.0%}) - der Tagesdurchschnitt waere "
            f"rueumlich verzerrt, deshalb wird nichts geschrieben"
        )

    places = {s.get("place") for s in stations if s.get("place")}
    if len(places) < 10 or TANKERKOENIG_API_KEY == TANKERKOENIG_DEMO_KEY:
        log.warning(
            "nur %d Orte (%s) - laeuft very wahrscheinlich mit dem Demo-Key, "
            "der nur Berlin abdeckt. Eigenen Key in TANKERKOENIG_API_KEY setzen.",
            len(places), ", ".join(sorted(places)[:5]),
        )

    rows = summarise(stations, day.isoformat())
    with db.connect() as conn:
        written = db.upsert_price_points(conn, rows, source=SOURCE, ingested_at=db.utcnow())
        conn.executemany(
            """
            INSERT INTO station_prices
                (country, station_id, day, e5, e10, diesel, lat, lng, place, brand, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (country, station_id, day) DO UPDATE SET
                e5 = excluded.e5, e10 = excluded.e10, diesel = excluded.diesel,
                lat = excluded.lat, lng = excluded.lng, place = excluded.place,
                brand = excluded.brand, ingested_at = excluded.ingested_at
            """,
            _station_rows(stations, day.isoformat()),
        )
        db.record_run(
            conn, SOURCE, started, rows=written, status="ok",
            message=(
                f"{len(stations)} Tankstellen, {len(places)} Orte, "
                f"{failed}/{len(points)} Rasterpunkte ausgefallen, "
                f"key={'demo' if TANKERKOENIG_API_KEY == TANKERKOENIG_DEMO_KEY else 'eigen'}"
            ),
        )
    return written
