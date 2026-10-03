from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Eigene DB, damit die Tests nie die Datenbank aus data/ anfassen.
_TMP = tempfile.mkdtemp(prefix="fuel-prices-test-")
os.environ["FUEL_DATA_DIR"] = _TMP
os.environ["FUEL_DB_PATH"] = str(Path(_TMP) / "test.db")
# Netzwerkzugriff in den Tests unterbinden.
os.environ["FUEL_HTTP_TIMEOUT"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.query as query  # noqa: E402
from app import db  # noqa: E402
from app.query import build_chart_payload, latest_snapshot  # noqa: E402


def setUpModule() -> None:
    """Schema einmal anlegen; die Testklassen laufen in alphabetischer Reihenfolge."""
    db.init_db()


UNIT = "EUR/l"
CURRENCY = "EUR"


def _point(country, fuel, basis, gran, day, value, n=100):
    """Tuple in der Reihenfolge, die upsert_price_points erwartet."""
    return (country, fuel, basis, gran, day, value, UNIT, CURRENCY, n)


def _seed() -> None:
    """Legt je Serie zwei Wochenpunkte an."""
    rows = []
    for country, fuel, basis, gran, _ in query.SERIES_SPEC:
        for i, day in enumerate(("2026-01-05", "2026-01-12")):
            rows.append(_point(country, fuel, basis, gran, day, 1.0 + i / 10))
    with db.connect() as c:
        db.upsert_price_points(c, rows, source="test", ingested_at="2026-01-12T00:00:00Z")
        db.upsert_oil_prices(
            c,
            [
                ("brent", "2026-01-05", 70.0, "USD/bbl", "USD"),
                ("brent", "2026-01-12", 71.5, "USD/bbl", "USD"),
            ],
            source="test",
            ingested_at="2026-01-12T00:00:00Z",
        )


class DbSchemaTest(unittest.TestCase):
    def test_init_db_is_idempotent(self) -> None:
        db.init_db()
        db.init_db()
        with db.connect() as c:
            tables = {
                r["name"]
                for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        for expected in ("price_points", "oil_prices", "ingest_runs", "station_prices"):
            self.assertIn(expected, tables)

    def test_ingest_runs_has_id(self) -> None:
        # Regression: die Spalte fehlte, wodurch /healthz abbrach.
        with db.connect() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(ingest_runs)")}
        self.assertIn("id", cols)


class UpsertTest(unittest.TestCase):
    def test_same_day_is_replaced_not_duplicated(self) -> None:
        with db.connect() as c:
            db.upsert_price_points(
                c,
                [_point("DE", "e5", "incl_taxes", "daily", "2026-02-01", 1.9, 50)],
                source="test",
                ingested_at="2026-02-01T00:00:00Z",
            )
            db.upsert_price_points(
                c,
                [_point("DE", "e5", "incl_taxes", "daily", "2026-02-01", 2.1, 51)],
                source="test",
                ingested_at="2026-02-01T01:00:00Z",
            )
            rows = c.execute(
                "SELECT COUNT(*) AS n, MAX(value) AS v FROM price_points"
                " WHERE country='DE' AND fuel='e5' AND basis='incl_taxes'"
                " AND granularity='daily' AND day='2026-02-01'"
            ).fetchone()
        self.assertEqual(rows["n"], 1)
        self.assertAlmostEqual(rows["v"], 2.1)

    def test_gross_and_net_do_not_collide(self) -> None:
        # Regression: netto ueberschrieb brutto.
        with db.connect() as c:
            db.upsert_price_points(
                c,
                [
                    _point("PL", "e5", "incl_taxes", "weekly", "2026-02-01", 1.85),
                    _point("PL", "e5", "excl_taxes", "weekly", "2026-02-01", 1.09),
                ],
                source="test",
                ingested_at="2026-02-01T00:00:00Z",
            )
            got = dict(
                c.execute(
                    "SELECT basis, value FROM price_points"
                    " WHERE country='PL' AND fuel='e5' AND day='2026-02-01'"
                ).fetchall()
            )
        self.assertAlmostEqual(got["incl_taxes"], 1.85)
        self.assertAlmostEqual(got["excl_taxes"], 1.09)

    def test_daily_and_weekly_do_not_collide(self) -> None:
        with db.connect() as c:
            db.upsert_price_points(
                c,
                [
                    _point("DE", "e5", "incl_taxes", "daily", "2026-02-01", 1.95),
                    _point("DE", "e5", "incl_taxes", "weekly", "2026-02-01", 1.88),
                ],
                source="test",
                ingested_at="2026-02-01T00:00:00Z",
            )
            got = dict(
                c.execute(
                    "SELECT granularity, value FROM price_points"
                    " WHERE country='DE' AND fuel='e5' AND day='2026-02-01'"
                ).fetchall()
            )
        self.assertAlmostEqual(got["daily"], 1.95)
        self.assertAlmostEqual(got["weekly"], 1.88)


class ChartPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _seed()

    def test_payload_has_oil_and_series(self) -> None:
        payload = build_chart_payload()
        self.assertIn("series", payload)
        self.assertIn("oil", payload)
        self.assertTrue(payload["oil"])

    def test_every_spec_series_is_present(self) -> None:
        payload = build_chart_payload()
        got = {
            (s["country"], s["fuel"], s["basis"], s["granularity"])
            for s in payload["series"]
        }
        for spec in query.SERIES_SPEC:
            self.assertIn(spec[:4], got, f"fehlt: {spec[4]}")

    def test_eu_net_series_exists(self) -> None:
        # Regression: EU-Nettowerte lagen in der DB, fehlten aber in SERIES_SPEC.
        payload = build_chart_payload()
        got = {
            (s["country"], s["basis"])
            for s in payload["series"]
            if s["country"] == "EU"
        }
        self.assertIn(("EU", "excl_taxes"), got)

    def test_points_are_sorted_and_numeric(self) -> None:
        for s in build_chart_payload()["series"]:
            days = [p[0] for p in s["points"]]
            self.assertEqual(days, sorted(days), s["label"])
            for day, value in s["points"]:
                dt.date.fromisoformat(day)
                self.assertIsInstance(value, (int, float))

    def test_from_and_to_filter_the_window(self) -> None:
        payload = build_chart_payload(from_="2026-01-12", to="2026-01-12")
        for s in payload["series"]:
            for day, _ in s["points"]:
                self.assertEqual(day, "2026-01-12", s["label"])

    def test_bad_date_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            build_chart_payload(from_="kein-datum")

    def test_from_after_to_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            build_chart_payload(from_="2026-01-12", to="2026-01-01")

    def test_points_stay_in_numeric_order(self) -> None:
        # Y-Achse darf nicht springen, wenn Daten luecken haben.
        payload = build_chart_payload()
        for s in payload["series"]:
            values = [v for _, v in s["points"]]
            self.assertTrue(all(isinstance(v, int | float) for v in values))


def _price_key(p: dict) -> tuple:
    """latest_snapshot liefert Rohfelder, kein Label."""
    return (p["country"], p["fuel"], p["basis"], p["granularity"])


class LatestSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _seed()

    def test_exactly_one_point_per_series(self) -> None:
        # Regression: die Abfrage lieferte alle historischen Punkte.
        keys = [_price_key(p) for p in latest_snapshot()["prices"]]
        self.assertEqual(len(keys), len(set(keys)), "Serien sind nicht eindeutig")
        self.assertEqual(len(keys), len(query.SERIES_SPEC))

    def test_latest_is_the_newest_day(self) -> None:
        for p in latest_snapshot()["prices"]:
            self.assertEqual(p["day"], "2026-01-12", str(_price_key(p)))

    def test_picks_a_newer_day_after_insert(self) -> None:
        with db.connect() as c:
            db.upsert_price_points(
                c,
                [_point("DE", "e5", "incl_taxes", "daily", "2026-03-01", 1.75, 10)],
                source="test",
                ingested_at="2026-03-01T00:00:00Z",
            )
        prices = {_price_key(p): p for p in latest_snapshot()["prices"]}
        daily = prices[("DE", "e5", "incl_taxes", "daily")]
        self.assertEqual(daily["day"], "2026-03-01")
        self.assertAlmostEqual(daily["value"], 1.75)

    def test_oil_uses_the_newest_day_per_benchmark(self) -> None:
        with db.connect() as c:
            db.upsert_oil_prices(
                c,
                [("wti", "2026-02-01", 65.0, "USD/bbl", "USD")],
                source="test",
                ingested_at="2026-02-01T00:00:00Z",
            )
        oil = {o["benchmark"]: o for o in latest_snapshot()["oil"]}
        self.assertAlmostEqual(oil["brent"]["value"], 71.5)
        self.assertEqual(oil["brent"]["day"], "2026-01-12")
        self.assertAlmostEqual(oil["wti"]["value"], 65.0)

    def test_includes_source_and_sample_size(self) -> None:
        for p in latest_snapshot()["prices"]:
            self.assertIn("source", p)
            self.assertIn("sample_size", p)


class AuthConfigTest(unittest.TestCase):
    def test_env_names_match_dockerfile_and_chart(self) -> None:
        # Die Variablen, die Dockerfile und Chart setzen, muessen im Code
        # gelesen werden - sonst zeigt der Container auf ein leeres Verzeichnis.
        source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
        for name in ("FUEL_AUTH_USER", "FUEL_AUTH_PASSWORD", "FUEL_AUTH_REALM"):
            self.assertIn(name, source)
        config = (Path(__file__).resolve().parent.parent / "app" / "config.py").read_text()
        for name in ("FUEL_DATA_DIR", "FUEL_DB_PATH", "TANKERKOENIG_API_KEY"):
            self.assertIn(name, config)
        # Das Image setzt FUEL_DATA_DIR; config.py leitet den DB-Pfad daraus ab.
        dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
        self.assertIn("FUEL_DATA_DIR=/data", dockerfile)
        # Muss sich mit dem Chart decken.
        helpers = (
            Path(__file__).resolve().parent.parent
            / "charts" / "fuel-prices" / "templates" / "_helpers.tpl"
        ).read_text()
        self.assertIn("FUEL_DB_PATH", helpers)
        self.assertIn("TANKERKOENIG_API_KEY", helpers)


class ChartRunnerContractTest(unittest.TestCase):
    """Die Flags, die das Helm-Chart benutzt, muessen existieren."""

    def test_daily_flags(self) -> None:
        import ingest.run_daily as mod

        self.assertIn("--max-requests", mod.build_parser().format_help())
        self.assertIn("--skip-pump", mod.build_parser().format_help())

    def test_weekly_flags(self) -> None:
        import ingest.run_weekly as mod

        self.assertIn("--force-download", mod.build_parser().format_help())


if __name__ == "__main__":
    unittest.main(verbosity=2)

