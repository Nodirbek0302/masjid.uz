# masjid-uz

Free mosque finder for Uzbekistan: map + community-maintained congregation prayer times.
No ads, no payments, no user accounts. Runs in the TIMUS ecosystem (built and deployed by TIMUS from a release tag).

- `server/` – Python 3.12 standard-library HTTP server + psycopg; one entry point `python server/cli.py <command>` (`serve`, `bot`, `migrate`, `import-osm`, `import --file`)
- `server/migrations/` – `V0001__init.sql`, ... applied into schema `masjiduz` by `migrate`
- `web/` – MapLibre GL client (bundled, no CDN); the same page works in browsers and as a Telegram Mini App; served at `/masjiduz/web/`
- `docs/CONTRACT.md` – the API contract (`/masjiduz/v1`, standard envelope, camelCase); `docs/RELEASE.md`, `CHANGELOG.md`
- `legacy/` – the previous self-hosted deployment, kept for reference only (not used by TIMUS)
- the Flutter app lives in a separate repository (`masjid-uz-app`) and uses the same API

## Run locally (dev profile)
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # TIMUS_ENV=dev; fill in the DB values (PostgreSQL 17 recommended)
.venv/bin/python server/cli.py migrate
.venv/bin/python server/cli.py serve     # http://127.0.0.1:8080/masjiduz/web/  and  /masjiduz/v1/health
```
Local PostgreSQL used on the author's machine: project cluster in `.pgdata/` (port 54330):
`pg_ctl -D .pgdata -o "-p 54330 -k /tmp/pgsock-masjid -c listen_addresses=127.0.0.1" -l .pgdata/log start`.

## Tests
```
.venv/bin/ruff check server tests
.venv/bin/python -m unittest discover -s tests -v    # uses TEST_DATABASE_URL (default postgresql://masjid@127.0.0.1:54330/masjid_test); WIPES schema masjiduz there
```
CI (GitHub Actions) runs lint, the tests against an ephemeral PostgreSQL 17, `pip-audit` and `docker build` (build only: no push, no deploy).

## Behaviour worth knowing
- **Nearest mosque.** `GET /masjiduz/v1/mosques/nearest?lat&lon&limit&cursor` returns the nearest non-archived mosques with `distanceM`
  (the search circle grows 500 m → 2 km → 8 km → 30 km → 100 km → whole country, so the result is exact). Checked against a brute-force search.
- **Concurrency.** Edits never fail because somebody else saved first: the row is locked, the fields a user changed are applied on top of the latest
  state (prayer times key by key; the same field: last write wins) and the history records what really changed. The HTTP server accepts bursts
  (backlog 256) and answers `503 SERVICE_UNAVAILABLE` instead of crashing if the database pool is exhausted.
- **Photo/voice timetable reading** uses Google Gemini and is off unless `TIMUS_APP_MASJIDUZ_GEMINI_API_KEY` is set (needs Eldor's written approval; see `docs/CONTRACT.md` §9).

## Licence
No open-source licence is granted: all rights reserved by the owner until they decide otherwise.
Third-party components keep their own licences (MapLibre GL JS: BSD-3-Clause; map data: © OpenStreetMap contributors, ODbL).
