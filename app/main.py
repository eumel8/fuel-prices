from __future__ import annotations

import base64
import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import WEB_DIR
from app.query import build_chart_payload, latest_snapshot

log = logging.getLogger("app.main")

AUTH_USER = os.environ.get("FUEL_AUTH_USER")
AUTH_PASSWORD = os.environ.get("FUEL_AUTH_PASSWORD")
AUTH_REALM = os.environ.get("FUEL_AUTH_REALM", "fuel-prices")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    if AUTH_USER and AUTH_PASSWORD:
        log.info("HTTP Basic Auth aktiv (User %s)", AUTH_USER)
    yield


app = FastAPI(
    title="Fuel Prices DE/PL",
    description="Rohöl-, Benzin- und Dieselpreise für Deutschland und Polen.",
    version="1.0.0",
    lifespan=lifespan,
)


def _unauthorized() -> Response:
    # Realm und Charset mitgeben, sonst ignoriert der Browser die Challenge.
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": f'Basic realm="{AUTH_REALM}", charset="UTF-8"'},
    )


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # /healthz bleibt offen: Probes brauchen keinen Zugangsdialog.
    if not AUTH_USER or not AUTH_PASSWORD or request.url.path == "/healthz":
        return await call_next(request)

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic":
        return _unauthorized()
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return _unauthorized()
    user, sep, password = decoded.partition(":")
    if not sep:
        return _unauthorized()
    # hmc.compare_digest auf beide Felder, damit die Laufzeit nichts preisgibt.
    ok_user = hmac.compare_digest(user, AUTH_USER)
    ok_pass = hmac.compare_digest(password, AUTH_PASSWORD)
    if not (ok_user and ok_pass):
        return _unauthorized()
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict:
    with db.connect() as conn:
        runs = conn.execute(
            "SELECT source, status, ended_at, rows, message"
            " FROM ingest_runs ORDER BY id DESC LIMIT 8"
        ).fetchall()
        points = conn.execute("SELECT COUNT(*) AS n FROM price_points").fetchone()["n"]
        oil = conn.execute("SELECT COUNT(*) AS n FROM oil_prices").fetchone()["n"]
    return {
        "status": "ok",
        "price_points": points,
        "oil_points": oil,
        "recent_ingest_runs": [dict(r) for r in runs],
    }


@app.get("/api/series")
def api_series(
    from_: str | None = Query(None, alias="from", description="YYYY-MM-DD"),
    to: str | None = Query(None, description="YYYY-MM-DD"),
) -> dict:
    try:
        return build_chart_payload(from_=from_, to=to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/latest")
def api_latest() -> dict:
    return latest_snapshot()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    log.exception("unbehandelter Fehler bei %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})
