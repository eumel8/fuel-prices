from __future__ import annotations

import datetime as dt

from app import db

DEFAULT_WINDOW_DAYS = 365 * 3

FUEL_LABELS = {
    "e5": "Super E5 / Pb95",
    "e10": "Super E10",
    "diesel": "Diesel",
}

COUNTRY_LABELS = {"DE": "Deutschland", "PL": "Polen", "EU": "EU-Durchschnitt"}

BASIS_LABELS = {"incl_taxes": "inkl. Steuern", "excl_taxes": "netto"}

SERIES_SPEC = (
    # (country, fuel, basis, granularity, id)
    ("DE", "e5", "incl_taxes", "daily", "de-e5-daily"),
    ("DE", "e10", "incl_taxes", "daily", "de-e10-daily"),
    ("DE", "diesel", "incl_taxes", "daily", "de-diesel-daily"),
    ("DE", "e5", "incl_taxes", "weekly", "de-e5-weekly"),
    ("DE", "diesel", "incl_taxes", "weekly", "de-diesel-weekly"),
    ("PL", "e5", "incl_taxes", "weekly", "pl-e5-weekly"),
    ("PL", "diesel", "incl_taxes", "weekly", "pl-diesel-weekly"),
    ("PL", "e5", "excl_taxes", "weekly", "pl-e5-weekly-net"),
    ("PL", "diesel", "excl_taxes", "weekly", "pl-diesel-weekly-net"),
    ("EU", "e5", "incl_taxes", "weekly", "eu-e5-weekly"),
    ("EU", "diesel", "incl_taxes", "weekly", "eu-diesel-weekly"),
    ("EU", "e5", "excl_taxes", "weekly", "eu-e5-weekly-net"),
    ("EU", "diesel", "excl_taxes", "weekly", "eu-diesel-weekly-net"),
)

SOURCE_LABELS = {
    "ec_weekly_oil_bulletin": "EU Weekly Oil Bulletin (Europäische Kommission, wöchentlich)",
    "tankerkoenig_open_data": "Tankerkönig Open Data (MTS-K des Bundeskartellamts, täglich)",
    "fred_eia_dcoilbrenteu": "U.S. EIA via FRED (Brent/WTI, täglich)",
}

OIL_LABELS = {
    "brent": "Brent Crude (USD/Barrel)",
    "wti": "WTI Crude (USD/Barrel)",
}


def _parse_day(value: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} muss das Format YYYY-MM-DD haben, nicht {value!r}") from exc


def _window(from_: str | None, to: str | None) -> tuple[dt.date, dt.date]:
    end = _parse_day(to, "to") if to else dt.date.today()
    start = _parse_day(from_, "from") if from_ else end - dt.timedelta(days=DEFAULT_WINDOW_DAYS)
    if start > end:
        raise ValueError("from darf nicht nach to liegen")
    return start, end


def build_chart_payload(
    *, from_: str | None = None, to: str | None = None, with_oil: bool = True
) -> dict:
    start, end = _window(from_, to)
    series: list[dict] = []
    sources: set[str] = set()

    with db.connect() as conn:
        for country, fuel, basis, granularity, series_id in SERIES_SPEC:
            rows = conn.execute(
                """
                SELECT day, value, sample_size, source
                FROM price_points
                WHERE country = ? AND fuel = ? AND basis = ? AND granularity = ?
                  AND day >= ? AND day <= ?
                ORDER BY day
                """,
                (country, fuel, basis, granularity, start.isoformat(), end.isoformat()),
            ).fetchall()
            if not rows:
                continue
            sources.update(r["source"] for r in rows)
            label = f"{COUNTRY_LABELS[country]} · {FUEL_LABELS[fuel]}"
            if basis == "excl_taxes":
                label += " (netto)"
            if granularity == "daily":
                label += " · Tagesmittel"
            else:
                label += " · Wochenmittel"
            series.append(
                {
                    "id": series_id,
                    "label": label,
                    "country": country,
                    "fuel": fuel,
                    "basis": basis,
                    "granularity": granularity,
                    "unit": "EUR/l",
                    "points": [[r["day"], r["value"]] for r in rows],
                    "sample_size": rows[-1]["sample_size"],
                }
            )

        oil: list[dict] = []
        if with_oil:
            rows = conn.execute(
                "SELECT benchmark, day, value FROM oil_prices"
                " WHERE day >= ? AND day <= ? ORDER BY day",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            by_bench: dict[str, list] = {}
            for row in rows:
                by_bench.setdefault(row["benchmark"], []).append([row["day"], row["value"]])
            for benchmark, points in by_bench.items():
                source = "fred_eia_dcoilbrenteu" if benchmark == "brent" else "fred_eia_dcoilwtico"
                sources.add(source)
                oil.append(
                    {
                        "id": benchmark,
                        "label": OIL_LABELS.get(benchmark, benchmark),
                        "unit": "USD/barrel",
                        "points": points,
                    }
                )

        # Abdeckung des Gesamtbestands, unabhaengig vom Fenster. Ohne diese
        # Angabe kann die Seite nicht unterscheiden, ob nie importiert wurde
        # oder der gewaehlte Zeitraum schlicht keine Punkte enthaelt.
        price_cov = conn.execute(
            "SELECT MIN(day) AS lo, MAX(day) AS hi, COUNT(*) AS n FROM price_points"
        ).fetchone()
        oil_cov = conn.execute(
            "SELECT MIN(day) AS lo, MAX(day) AS hi, COUNT(*) AS n FROM oil_prices"
        ).fetchone()

    # ISO-Daten sortieren lexikografisch, also reicht min/max auf den Strings.
    lows = [d for d in (price_cov["lo"], oil_cov["lo"]) if d]
    highs = [d for d in (price_cov["hi"], oil_cov["hi"]) if d]

    return {
        "generated_at": db.utcnow(),
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "coverage": {
            "from": min(lows) if lows else None,
            "to": max(highs) if highs else None,
            "price_points": price_cov["n"],
            "oil_points": oil_cov["n"],
        },
        "currency": "EUR",
        "series": series,
        "oil": oil,
        "sources": [{"id": sid, "label": SOURCE_LABELS.get(sid, sid)} for sid in sorted(sources)],
        "notes": [
            "Tankstellenpreise sind Durchschnitte und inkl. Steuern;"
            " für PL und EU nur wöchentlich verfügbar.",
            "DE-Tageswerte stammen aus Tankerkönig (MTS-K-Rohdaten);"
            " die Historie beginnt erst mit der Aufnahme durch diesen Dienst.",
            "Die jüngste Woche im EU-Weekly-Oil-Bulletin kann vorläufig sein.",
        ],
    }


def latest_snapshot() -> dict:
    with db.connect() as conn:
        prices = conn.execute(
            """
            SELECT p.country, p.fuel, p.basis, p.granularity, p.day, p.value,
                   p.unit, p.currency, p.sample_size, p.source
            FROM price_points AS p
            JOIN (
                SELECT country, fuel, basis, granularity, MAX(day) AS max_day
                FROM price_points
                GROUP BY country, fuel, basis, granularity
            ) AS newest
              ON newest.country = p.country AND newest.fuel = p.fuel
             AND newest.basis = p.basis AND newest.granularity = p.granularity
             AND newest.max_day = p.day
            ORDER BY p.country, p.fuel, p.basis, p.granularity
            """
        ).fetchall()
        oil = conn.execute(
            """
            SELECT o.benchmark, o.day, o.value, o.unit
            FROM oil_prices AS o
            JOIN (
                SELECT benchmark, MAX(day) AS max_day
                FROM oil_prices
                GROUP BY benchmark
            ) AS newest
              ON newest.benchmark = o.benchmark AND newest.max_day = o.day
            ORDER BY o.benchmark
            """
        ).fetchall()
        runs = conn.execute(
            "SELECT source, status, ended_at, rows, message"
            " FROM ingest_runs ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return {
        "generated_at": db.utcnow(),
        "prices": [dict(r) for r in prices],
        "oil": [dict(r) for r in oil],
        "recent_ingest_runs": [dict(r) for r in runs],
    }
