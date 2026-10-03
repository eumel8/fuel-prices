# fuel-prices

![ci](https://github.com/eumel8/fuel-prices/actions/workflows/ci.yml/badge.svg)
![image](https://github.com/eumel8/fuel-prices/actions/workflows/image.yml/badge.svg)

Webseite mit Diagrammen für Rohöl-, Benzin- und Dieselpreise in Deutschland und
Polen. FastAPI + SQLite + Chart.js, Datenimport über CLI oder Kubernetes-CronJobs.

Repository: <https://github.com/eumel8/fuel-prices>
Image: `ghcr.io/eumel8/fuel-prices`

| Land | Inhalt | Granularität | Quelle |
| --- | --- | --- | --- |
| DE | Super E5, Super E10, Diesel | täglich | Tankerkönig (MTS-K) |
| DE/PL/EU | E5 (Pb95), Diesel, brutto und netto | wöchentlich | EU Weekly Oil Bulletin, ab 2005 |
| Welt | Brent, WTI | täglich | U.S. EIA via FRED, ab 1987/1986 |

Alle Kraftstoffpreise werden vergleichbar in **EUR/l** geführt, Rohöl in **USD/Barrel**.
Polnische Tageswerte gibt es von keiner verifizierten kostenlosen Quelle, daher
Wochenmittel für PL.

## Lokal starten

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt

# Historie einmalig laden
./venv/bin/python -m ingest.run_weekly --force-download
./venv/bin/python -m ingest.run_daily

./venv/bin/uvicorn app.main:app --reload --port 8000
```

| Endpunkt | Zweck |
| --- | --- |
| `/` | Diagrammseite |
| `/api/series?from=YYYY-MM-DD&to=YYYY-MM-DD` | Chart-Daten, `from`/`to` optional |
| `/api/latest` | je Serie genau der neueste Punkt |
| `/healthz` | DB-Zähler und letzte Importläufe |

### Tägliche/wöchentliche Aktualisierung

```bash
./venv/bin/python -m ingest.run_daily                       # Brent/WTI + DE-Tagesmittel
./venv/bin/python -m ingest.run_daily --max-requests 4      # nur 4 Rasterpunkte
./venv/bin/python -m ingest.run_daily --skip-pump           # nur Rohöl
./venv/bin/python -m ingest.run_weekly                      # EU-Bulletin
./venv/bin/python -m ingest.run_weekly --force-download     # XLSX neu laden
```

Jeder Lauf wird in `ingest_runs` protokolliert und ist wiederholbar: vorhandene
Tage werden per Upsert überschrieben, keine Duplikate.

## Tankerkönig-Key

Ohne `TANKERKOENIG_API_KEY` greift der offizielle Demo-Key. Der liefert
ausschließlich Tankstellen im Raum Berlin mit festen Demo-Werten — gemessen
auf dem Cluster: Mittel 1,009 EUR/l bei 2337 Tankstellen, also Median = min =
max. Solche Werte landen als `de-*-daily` in der Datenbank und **wären eine
falsche Preisangabe** auf der Seite. Wer keinen Key hat, schaltet nur diese
Quelle ab:

```bash
helm upgrade fuel-prices ./charts/fuel-prices --reuse-values \
  --set tankerkoenig.enabled=false
```

Der Daily-CronJob bleibt dann bestehen und ruft `run_daily --skip-pump` auf —
Brent/WTI werden also weiter aktualisiert, nur die deutschen Tageswerte
fehlen. Das Diagramm zeigt dann die Wochenwerte aus dem EU-Bulletin. Für
echte Tagesdaten den kostenlosen Key unter <https://tankerkoenig.de> holen:

```bash
export TANKERKOENIG_API_KEY=<key>
```

Das Diagramm rastert Deutschland und fragt je Punkt ab; `--max-requests`
begrenzt das für Tests.

## Docker

```bash
docker build -t ghcr.io/eumel8/fuel-prices:1.0.0 .
docker run --rm -p 8000:8000 -v fuel-data:/data \
  -e TANKERKOENIG_API_KEY=<key> \
  ghcr.io/eumel8/fuel-prices:1.0.0
```

Image läuft als UID 10001, Root-Dateisystem ist im Cluster read-only, `/data`
ist der einzige beschreibbare Pfad. Erstbefüllung:

```bash
docker run --rm -v fuel-data:/data --entrypoint sh \
  ghcr.io/eumel8/fuel-prices:1.0.0 -c 'python -m ingest.run_weekly --force-download'
```

## Kubernetes (Helm)

```bash
# 1. Secret fuer die Web-API (optional, aber empfohlen)
htpasswd -c auth kloeker && kubectl create secret generic fuel-prices-auth --from-file=auth

# 2. Tankerkönig-Key extern halten
kubectl create secret generic fuel-prices-tankerkoenig \
  --from-literal=tankerkoenigApiKey=<key>

# 3. Installieren
helm install fuel-prices ./charts/fuel-prices \
  --set image.repository=ghcr.io/eumel8/fuel-prices \
  --set image.tag=1.0.0 \
  --set auth.enabled=true --set auth.existingSecret=fuel-prices-auth \
  --set tankerkoenig.existingSecret=fuel-prices-tankerkoenig \
  --set ingress.enabled=true --set ingress.hosts[0].host=<deine-domain>

# 4. Historie einmalig nachladen
helm upgrade fuel-prices ./charts/fuel-prices --reuse-values \
  --set ingest.bootstrap.enabled=true
kubectl wait --for=condition=complete job/fuel-prices-bootstrap --timeout=30m
kubectl delete job fuel-prices-bootstrap
helm upgrade fuel-prices ./charts/fuel-prices --reuse-values \
  --set ingest.bootstrap.enabled=false
```

Erzeugte Ressourcen: Deployment (1 Replica, `Recreate`), Service, optionaler
Ingress, PVC (1 Gi), ServiceAccount, CronJob `ingest-daily` (06:17 MEZ),
CronJob `ingest-weekly` (Mi 10:23 MEZ), optionaler Secret und Bootstrap-Job.

### Image-Tag und Chart-Version

`image.tag` ist leer und fällt auf `appVersion` aus `charts/fuel-prices/Chart.yaml`
zurück (aktuell `1.0.0`). Damit dieser Tag in der Registry existiert, pusht der
Workflow `image.yml` die `appVersion` bei **jedem** Build – auch ohne Git-Tag.
Nach einem Release wird beides erhöht:

```bash
# 1. Version im Chart
sed -i 's/^appVersion:.*/appVersion: "1.1.0"/' charts/fuel-prices/Chart.yaml
# 2. Tag setzen -> erzeugt zusätzlich 1.1.0 und 1.1
git tag v1.1.0 && git push --tags
```

So zeigt ein frisches `helm install` nie ins Leere. Wer den SHA statt des
Versionstags fahren will, überschreibt `image.tag`.

### Welche Image-Tags entstehen

| Auslöser | Tags im GHCR |
| --- | --- |
| Push auf `main` | `1.0.0` (appVersion), `sha-<kurzhash>`, `latest` |
| Push auf `v1.1.0` | `1.0.0`, `1.1.0`, `1.1`, `v1.1.0`, `sha-<kurzhash>`, `latest` |
| Push auf `nightly` | `1.0.0`, `nightly`, `sha-<kurzhash>`, `latest` |

Jeder Git-Tag baut ein Image, auch einer ohne Semver – der Tag landet 1:1 als
Image-Tag in der Registry. `latest` zeigt immer auf den letzten Build aus
`main` oder einem Tag.

**Wichtig beim Rollout:** `image.pullPolicy` ist `IfNotPresent`. Ein neues Image
unter demselben Tag zieht keinen Rollout. Nach einem Build mit gleichem Tag:

```bash
kubectl rollout restart deploy/fuel-prices -n <namespace>
```

Wer das umgehen will, fährt `image.tag` auf den SHA und `pullPolicy: Always`.

### Wichtige Eckpunkte

- **SQLite heißt eine Replica.** Der PVC ist `ReadWriteOnce`, Deployment und
  CronJobs müssen auf demselben Node landen. Bei mehreren Replikas oder
  ReadWriteMany besser PostgreSQL statt SQLite verwenden.
- **Ein PVC für Web und Jobs.** Nur Pods auf dem gleichen Node dürfen zugreifen;
  bei `nodeSelector`/Spread das berücksichtigen.
- **`--max-requests`** im Daily-CronJob begrenzt die Rasterabfragen
  (`tankerkoenig.maxRequests`, Default 56 = flächendeckend).
- **CronJobs laufen nur während ihres Pod-Lebenszyklus.** Ein Job, der länger
  läuft als `activeDeadlineSeconds`, wird abgebrochen; nach etwa 60 Tagen muss
  ein Node vorhanden sein. Für dauerhafte Ausführung einen Deployment-Pod mit
  Sidecar-Scheduler oder External Cron (CronJob der einen CronJob anlegt) nutzen.
- **Auth ist aus.** Ohne `auth.enabled` sind alle Daten frei abrufbar.

### Helm-Werte

Alle Optionen in `charts/fuel-prices/values.yaml`, wichtigste:

| Wert | Default | Bedeutung |
| --- | --- | --- |
| `image.repository` / `image.tag` | – / `appVersion` | Image |
| `replicaCount` | `1` | SQLite: nicht erhöhen |
| `persistence.size` | `1Gi` | PVC-Größe |
| `tankerkoenig.existingSecret` | `""` | bestehendes Secret mit dem Key |
| `tankerkoenig.apiKey` | `""` | Klartext-Key, legt ein Secret im Release an |
| `tankerkoenig.maxRequests` | `56` | Rasterpunkte pro Lauf |
| `ingest.daily.schedule` | `17 6 * * *` | Cron, Zeitzone `Europe/Berlin` |
| `ingest.weekly.schedule` | `23 10 * * 3` | Cron (Mittwoch) |
| `ingest.bootstrap.enabled` | `false` | einmaliger Historie-Job |
| `auth.enabled` + `auth.existingSecret` | `false` / `""` | HTTP Basic Auth |
| `ingress.enabled` | `false` | Ingress |
| `loki.enabled` | `false` | Log-Sidecar |

Chart prüfen:

```bash
helm lint charts/fuel-prices
helm template fuel-prices charts/fuel-prices > /tmp/render.yaml
```

## Tests

```bash
# Python: Schema, Upserts, Chart-Payload, Runner-Flags
./venv/bin/python -m unittest discover -s tests -p "test_*.py" -v

# Helm: 44 Assertions ueber Deployment, Service, Ingress, CronJobs
helm unittest charts/fuel-prices

# Lint
./venv/bin/ruff check app ingest tests

# Frontend-Logik headless (startet keinen Browser)
npm install
./venv/bin/uvicorn app.main:app --port 8099 &
PORT=8099 npm test
```

Der Frontend-Test prüft gegen die laufende API, ob Serien eindeutig sind, die
Tages-/Wochentrennung stimmt, Rohöl auf der zweiten Achse liegt und der
Netto-Schalter Polen *und* EU liefert. Die Python-Tests sichern die
Regressionen ab, die während der Entwicklung entstanden sind (Brutto/Netto-Collision,
mehrfache Punkte in `/api/latest`, fehlende EU-Nettoserien).

## GitHub Actions

| Workflow | Wann | Was |
| --- | --- | --- |
| `.github/workflows/ci.yml` | jeder Push auf `main`, jeder PR | 4 Jobs: `python`, `frontend`, `helm`, `docker` |
| `.github/workflows/image.yml` | Push auf `main`, Tags `v*` | Image bauen, nach GHCR pushen, SBOM |

Details:

- **python** — `compileall`, `ruff`, 21 Unittests, Coverage-Report.
- **frontend** — lädt die Historie, startet den Server, prüft `app.js`-Syntax,
  den headless Frontend-Test, `/healthz`, `/api/latest` (keine doppelten Serien),
  HTTP 400 bei ungültigem Datum und greift `server.log` auf Tracebacks ab.
- **helm** — `helm lint --strict`, `helm unittest`, Render mit allen Optionen,
  `kubeconform -strict` gegen die Kubernetes-1.31-Schemas, plus Guard-Checks,
  dass `replicas: 1`, `strategy: Recreate` und `runAsNonRoot` erhalten bleiben
  (SQLite verträgt keinen Mehrfach-Writer).
- **docker** — baut das Image und smoke-testet es: UID 10001, `/healthz` und
  die Seite erreichbar, Auth-Pfade 401/200/401.

Kein Push aus dem CI-Job. Der Image-Workflow nutzt `GITHUB_TOKEN` mit
`packages: write`; das genügt für `ghcr.io/eumel8/fuel-prices` ohne zusätzliches
Secret. Welche Tags ein Build erzeugt, steht in
[Image-Tag und Chart-Version](#image-tag-und-chart-version).

Lokal lässt sich das mit [actionlint](https://github.com/rhysd/actionlint)
prüfen:

```bash
actionlint .github/workflows/*.yml
```

## Lizenz

Der **Code** steht unter [MIT](LICENSE). Das betrifft nur die Anwendung, nicht
die Daten: Tankerkönig/MTS-K, EU Weekly Oil Bulletin und FRED/EIA unterliegen
eigenen Bedingungen, siehe unten.

## Datenqualität und Rechtliches

- **Tankerkönig / MTS-K** liefert die Rohtankstellen-Daten des Bundeskartellamts.
  Diese dürfen ausschließlich zur Verbraucherinformation genutzt werden, nicht
  für die Mineralölwirtschaft.
- **EU Weekly Oil Bulletin** und **FRED/EIA** sind frei verfügbar; die
  Einordnung der Brent-Serie als Benchmark ist zu kennzeichnen.
- Die Preise sind Durchschnitte ohne Händler- oder Produktspanne und ersetzen
  keine Information über lokale Tankstellenpreise.
- Historie bis 2005 für DE/PL/EU, Brent bis 1987, WTI bis 1986.
- Vor kommerzieller Veröffentlichung Lizenzbedingungen beider Datenanbieter
  prüfen; dieses Projekt nimmt keine Bewertung der Lizenzlage vor.
