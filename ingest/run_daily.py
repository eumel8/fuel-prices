"""Täglicher Lauf: Brent (Workday) + Deutschland Tagesmittel aus Tankerkönig."""

from __future__ import annotations

import argparse
import logging

from app import db
from ingest import brent, tankerkoenig_de

log = logging.getLogger("ingest.run_daily")


def build_parser() -> argparse.ArgumentParser:
    """Getrennt von main(), damit Tests die Flags pruefen koennen."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-requests", type=int, default=None,
        help="Rasterpunkte begrenzen (Smoke-Test / niedrige Last)",
    )
    parser.add_argument(
        "--skip-brent", action="store_true", help="Brent nicht aktualisieren",
    )
    parser.add_argument(
        "--skip-pump", action="store_true", help="Tankerkönig nicht abfragen",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init_db()

    failures: list[str] = []
    if not args.skip_brent:
        try:
            total = brent.ingest()
            log.info("Brent/WTI: %d Datenpunkte", total)
        except Exception as exc:  # noqa: BLE001 - Lauf soll weitergehen
            failures.append(f"brent: {exc}")
            log.exception("Brent-Ingest fehlgeschlagen")

    if not args.skip_pump:
        try:
            total = tankerkoenig_de.ingest(max_requests=args.max_requests)
            log.info("DE Tagesmittel: %d Serien aktualisiert", total)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"tankerkoenig_de: {exc}")
            log.exception("Tankerkönig-Ingest fehlgeschlagen")

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
