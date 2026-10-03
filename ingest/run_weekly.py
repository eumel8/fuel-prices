"""Wöchentlicher Lauf: EU Weekly Oil Bulletin (DE + PL Wochenmittel)."""

from __future__ import annotations

import argparse
import logging

from app import db
from ingest import wob

log = logging.getLogger("ingest.run_weekly")


def build_parser() -> argparse.ArgumentParser:
    """Getrennt von main(), damit Tests die Flags pruefen koennen."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-download", action="store_true",
        help="History-XLSX neu laden statt den Cache zu verwenden",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init_db()

    try:
        written = wob.ingest(force_download=args.force_download)
    except Exception:
        log.exception("Weekly-Oil-Bulletin-Ingest fehlgeschlagen")
        return 1
    log.info("Weekly Oil Bulletin: %d Datenpunkte geschrieben", written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
