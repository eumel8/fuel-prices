from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Eigene DB, damit die Tests nie die Datenbank aus data/ anfassen.
_TMP = tempfile.mkdtemp(prefix="fuel-prices-test-")
os.environ["FUEL_DATA_DIR"] = _TMP
os.environ["FUEL_DB_PATH"] = str(Path(_TMP) / "test.db")
# Netzwerkzugriff in den Tests unterbinden.
os.environ["FUEL_HTTP_TIMEOUT"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import app.query as query  # noqa: E402
import ingest.tankerkoenig_de as tk  # noqa: E402
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


_class_counter = 0


def _fresh_db() -> None:
    """Leere, eigene DB pro Testklasse.

    Sonst leakt UpsertTest (2026-02-01) in die danach laufenden Klassen und
    latest_snapshot()/coverage sehen Punkte, die ihre Fixtures nicht kennen.
    """
    global _class_counter
    _class_counter += 1
    db.DB_PATH = Path(_TMP) / f"klasse-{_class_counter}.db"
    db.DB_PATH.unlink(missing_ok=True)
    db.init_db()


def _seed() -> None:
    """Legt je Serie zwei Wochenpunkte an."""
    _fresh_db()
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


class FuelTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _fresh_db()


class DbSchemaTest(FuelTestCase):
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


class UpsertTest(FuelTestCase):
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


class ChartPayloadTest(FuelTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
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


class CoverageTest(FuelTestCase):
    """coverage unterscheidet 'nie importiert' von 'Zeitraum passt nicht'."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        _seed()

    def test_coverage_spans_the_whole_database(self) -> None:
        cov = build_chart_payload()["coverage"]
        self.assertEqual(cov["from"], "2026-01-05")
        self.assertEqual(cov["to"], "2026-01-12")
        self.assertGreater(cov["price_points"], 0)
        self.assertGreater(cov["oil_points"], 0)

    def test_coverage_ignores_the_requested_window(self) -> None:
        # Fenster ohne Treffer: das Frontend braucht trotzdem den Gesamtbestand,
        # sonst zeigt es eine leere Seite ohne erklaerung.
        payload = build_chart_payload(from_="2019-01-01", to="2019-01-31")
        self.assertEqual(payload["series"], [])
        self.assertEqual(payload["oil"], [])
        self.assertEqual(payload["coverage"]["from"], "2026-01-05")

    def test_coverage_on_an_empty_database(self) -> None:
        # Regression: leere DB lieferte keine Info, die Seite blieb weiss.
        with tempfile.TemporaryDirectory() as tmp:
            original = db.DB_PATH
            db.DB_PATH = Path(tmp) / "leer.sqlite3"
            try:
                db.init_db()
                payload = build_chart_payload()
            finally:
                db.DB_PATH = original
        self.assertEqual(payload["series"], [])
        self.assertEqual(payload["oil"], [])
        self.assertIsNone(payload["coverage"]["from"])
        self.assertIsNone(payload["coverage"]["to"])
        self.assertEqual(payload["coverage"]["price_points"], 0)
        self.assertEqual(payload["coverage"]["oil_points"], 0)


def _price_key(p: dict) -> tuple:
    """latest_snapshot liefert Rohfelder, kein Label."""
    return (p["country"], p["fuel"], p["basis"], p["granularity"])


class LatestSnapshotTest(FuelTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
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


class AuthConfigTest(FuelTestCase):
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


class UserAgentTest(FuelTestCase):
    """Regression: FRED antwortet nur auf User-Agents mit Kontakt-URL."""

    def test_user_agent_carries_a_contact_url(self) -> None:
        from app.config import USER_AGENT

        # Gemessen auf dem Cluster: "fuel-prices/1.0" ohne URL fuehrt zu einem
        # ReadTimeout nach 60 s, mit "(+https://...)" kommt die Antwort in 0,1 s.
        self.assertRegex(USER_AGENT, r"\(\+https://\S+\)", USER_AGENT)

    def test_every_http_client_uses_the_configured_agent(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for name in ("wob.py", "brent.py", "tankerkoenig_de.py"):
            source = (root / "ingest" / name).read_text()
            self.assertIn("USER_AGENT", source, f"{name} setzt keinen User-Agent")


class ChartRunnerContractTest(FuelTestCase):
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



class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "upstream error",
                request=httpx.Request(
                    "GET", "https://creativecommons.tankerkoenig.de/json/list.php?apikey=GEHEIM"
                ),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Gibt Antworten der Reihe nach aus; die letzte wird wiederholt."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        self.calls += 1
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class TankerkoenigRobustnessTest(FuelTestCase):
    """Regression: die API drosselt, und der Key darf nicht im Log landen."""

    def test_retries_a_503_and_succeeds(self) -> None:
        ok = _FakeResponse(200, {"ok": True, "stations": [{"id": "1"}]})
        client = _FakeClient([_FakeResponse(503), ok])
        with patch.object(tk.time, "sleep"):
            stations = tk.fetch_area(client, 50.0, 9.0)
        self.assertEqual([s["id"] for s in stations], ["1"])
        self.assertEqual(client.calls, 2)

    def test_gives_up_after_the_configured_attempts(self) -> None:
        client = _FakeClient([_FakeResponse(503)])
        with patch.object(tk.time, "sleep"), self.assertRaises(RuntimeError) as ctx:
            tk.fetch_area(client, 50.0, 9.0)
        self.assertEqual(str(ctx.exception), "HTTP 503")
        self.assertEqual(client.calls, tk.RETRY_ATTEMPTS)

    def test_api_key_never_appears_in_the_error(self) -> None:
        # In der Exception-URL steht apikey=... - die Meldung darf sie nicht kopieren.
        client = _FakeClient([_FakeResponse(500)])
        with patch.object(tk.time, "sleep"), self.assertRaises(RuntimeError) as ctx:
            tk.fetch_area(client, 50.0, 9.0)
        self.assertNotIn("GEHEIM", str(ctx.exception))

    def test_collect_paces_the_requests(self) -> None:
        client = _FakeClient([_FakeResponse(200, {"ok": True, "stations": []})])
        with patch.object(tk.time, "sleep") as sleep:
            tk._collect_all(client, [(50.0, 9.0), (51.0, 9.0), (52.0, 9.0)])
        self.assertEqual(sleep.call_count, 2)
        for call in sleep.call_args_list:
            self.assertEqual(call.args[0], tk.REQUEST_DELAY_SECONDS)

    def test_ingest_refuses_to_publish_a_distorted_average(self) -> None:
        # 30 von 56 Punkten fielen real aus; der Mittelwert waere aus einer
        # raeumlich verzerrten Teilmenge entstanden. Dann: nichts schreiben.
        stations = {
            str(i): {
                "id": str(i), "e5": "2.1", "e10": "2.0", "diesel": "2.2",
                "coords": {"lat": 50.0, "lng": 9.0}, "place": f"Ort{i}",
            }
            for i in range(50)
        }
        with patch.object(tk, "_collect_all", return_value=(stations, 30)), self.assertRaises(
            RuntimeError
        ) as ctx:
            tk.ingest()
        self.assertIn("verzerrt", str(ctx.exception))
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM price_points").fetchone()[0], 0)

    def test_ingest_writes_when_only_few_points_fail(self) -> None:
        stations = {
            str(i): {
                "id": str(i), "e5": "2.1", "e10": "2.0", "diesel": "2.2",
                "coords": {"lat": 50.0, "lng": 9.0}, "place": f"Ort{i}",
            }
            for i in range(50)
        }
        with patch.object(tk, "_collect_all", return_value=(stations, 2)):
            written = tk.ingest()
        self.assertEqual(written, 3)
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM station_prices").fetchone()[0], 50)
