# masjid-uz

Free mosque finder for Uzbekistan: map + community-maintained congregation prayer times.
Private repository, owner-operated. No ads, no payments, no user accounts required.

- `server/` – Python 3.9+ API (+ static hosting), PostgreSQL via psycopg, migrations in `server/migrations/`
- `web/` – MapLibre GL client; the same page works in browsers, mobile webviews and as a Telegram Mini App
- `deploy/` – Caddy, systemd, DB roles, backup script
- `docs/` – deployment, map-provider decision, costs and limits

## Run locally
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
createdb masjid
DATABASE_URL=postgresql://localhost/masjid ADMIN_ALLOW_TOKEN=1 ADMIN_TOKEN=dev .venv/bin/python server/app.py   # http://127.0.0.1:8080
TEST_DATABASE_URL=postgresql://localhost/masjid_test .venv/bin/python -m unittest discover -s tests   # WIPES that database
```

## Licence
No open-source licence is granted: all rights reserved by the owner until they decide otherwise.
Third-party components keep their own licences (MapLibre GL JS: BSD-3-Clause; map data: © OpenStreetMap contributors, ODbL).

## Local dev state (this machine)
- `.env` (private, gitignored) holds `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `ADMIN_ALLOW_TOKEN` (dev only), `SHOW_TILE_MOSQUES=0`; `server/app.py` loads it.
- Project-local PostgreSQL cluster in `.pgdata/` (port 54330): `pg_ctl -D .pgdata -o "-p 54330 -k /tmp/pgsock-masjid -c listen_addresses=127.0.0.1" -l .pgdata/log start`
- Run: `.venv/bin/python server/app.py` → http://127.0.0.1:8080

## Docker / CI-CD
`Dockerfile`, `docker-compose.yml` (+ `docker-compose.prod.yml`), `.github/workflows/{ci,docker}.yml` — ai-smm-agent bilan bir xil sxema.
Qadamlar: [docs/DEPLOY-DOCKER.md](docs/DEPLOY-DOCKER.md).

Serverga o'rnatish bo'yicha to'liq qo'llanma: [docs/SERVER-SETUP.md](docs/SERVER-SETUP.md) (`deploy/push.sh`, `deploy/verify.sh`).

## Nearest mosque
`GET /api/mosques/nearest?lat=..&lon=..&limit=5` returns the nearest non-archived mosques with `distance_m` (search circle grows
500 m → 2 km → 8 km → 30 km → 100 km → whole country, so the result is exact, not approximate). The web button "Eng yaqin masjid"
uses the device location (HTTPS needed by browsers) and falls back to the map centre. Checked against a brute-force search
over the whole database on 49 points: identical top-3 and distances.

## Concurrency
Edits never fail because somebody else saved first: the row is locked, the fields a user changed are applied on top of the latest
state (prayer times key by key; the same field: last write wins) and the history records what really changed. Clients send only the
fields they changed. Checked with 900 mixed requests from 60 parallel workers (all 200, versions gapless) and 12 simultaneous editors
of one mosque. The HTTP server accepts bursts (backlog 256) and answers 503 "busy" instead of crashing if the database pool is exhausted.
