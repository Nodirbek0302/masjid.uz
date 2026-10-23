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
DATABASE_URL=postgresql://localhost/masjid ADMIN_TOKEN=dev .venv/bin/python server/app.py   # http://127.0.0.1:8080
TEST_DATABASE_URL=postgresql://localhost/masjid_test .venv/bin/python -m unittest discover -s tests   # WIPES that database
```

## Licence
No open-source licence is granted: all rights reserved by the owner until they decide otherwise.
Third-party components keep their own licences (MapLibre GL JS: BSD-3-Clause; map data: © OpenStreetMap contributors, ODbL).

## Local dev state (this machine)
- `.env` (private, gitignored) holds `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `ADMIN_TOKEN`, `SHOW_TILE_MOSQUES=0`; `server/app.py` loads it.
- Project-local PostgreSQL cluster in `.pgdata/` (port 54330): `pg_ctl -D .pgdata -o "-p 54330 -k /tmp/pgsock-masjid -c listen_addresses=127.0.0.1" -l .pgdata/log start`
- Run: `.venv/bin/python server/app.py` → http://127.0.0.1:8080

## Docker / CI-CD
`Dockerfile`, `docker-compose.yml` (+ `docker-compose.prod.yml`), `.github/workflows/{ci,docker}.yml` — ai-smm-agent bilan bir xil sxema.
Qadamlar: [docs/DEPLOY-DOCKER.md](docs/DEPLOY-DOCKER.md).

Serverga o'rnatish bo'yicha to'liq qo'llanma: [docs/SERVER-SETUP.md](docs/SERVER-SETUP.md) (`deploy/push.sh`, `deploy/verify.sh`).
