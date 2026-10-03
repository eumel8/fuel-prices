from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FUEL_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("FUEL_DB_PATH", DATA_DIR / "fuel.db"))
WEB_DIR = BASE_DIR / "web"

WOB_HISTORY_URL = (
    "https://energy.ec.europa.eu/document/download/906e60ca-8b6a-44e7-8589-652854d2fd3f_en"
    "?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx"
)

FRED_BRENT_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"

TANKERKOENIG_URL = "https://creativecommons.tankerkoenig.de/json/list.php"

# Offizieller Demo-Key der Tankerkönig-Doku: liefert Preise nur fuer Tankstellen
# im Raum Berlin. Fuer ein bundesweites Raster wird ein eigener Key benoetigt
# (kostenlos registrierbar auf https://tankerkoenig.de).
TANKERKOENIG_DEMO_KEY = "00000000-0000-0000-0000-000000000002"

TANKERKOENIG_API_KEY = os.environ.get("TANKERKOENIG_API_KEY", "").strip() or TANKERKOENIG_DEMO_KEY

HTTP_TIMEOUT = float(os.environ.get("FUEL_HTTP_TIMEOUT", "60"))
# Wichtig: FRED beantwortet nur User-Agents mit Kontakt-URL. Ein reines
# "fuel-prices/1.0" wird ignoriert und laeuft nach 60 s in einen ReadTimeout,
# wodurch der Brent/WTI-Import still fehlschlaegt. Die URL darum herum muss
# bleiben. Gegen die EU-Kommission ist der User-Agent dagegen egal.
USER_AGENT = "fuel-prices/1.0 (+https://github.com/eumel8/fuel-prices)"
