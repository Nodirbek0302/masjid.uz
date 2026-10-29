# Release v1.0.5

- **Commit:** the commit the annotated tag `v1.0.5` points to (`git rev-parse v1.0.5^{commit}`).
- **Change:** `import-osm` retries a busy Overpass (30/60/120/240 s, about 7.5 minutes at most) instead of failing at the first 502/504; web map pins like the mobile app's (CHANGELOG 1.0.5).
- **Data:** the database stays empty until `import-osm` has run once. Run it once right after deploying (`python server/cli.py import-osm`, one-off container), then nightly at 02:15. It prints `import-osm done: added=… …`; a first run on an empty database adds about 2 200 mosques in a few seconds (plus the Overpass query, ~50 s).
- **Migrations:** none. **New env vars:** none.
- **How it was tested:** 249 tests, ruff; `import-osm` against a fresh database with a real Overpass answer (2 207 mosques added in 1.6 s); the pins in a phone-size browser.

## Release v1.0.4

- **Commit:** the commit the annotated tag `v1.0.4` points to (`git rev-parse v1.0.4^{commit}`).
- **Change:** bot and web client only (CHANGELOG 1.0.4): a pressed button updates its own message instead of adding a new one; reactions and a temporary draft while a photo/voice is read; coloured buttons; copy-the-times and Telegram-map buttons on the mosque card; bot descriptions in three languages; the Mini App takes the position from Telegram's LocationManager and follows Telegram's day/night theme. Fixes: the bot's menu button opens `…/masjiduz/web/` (it opened the address without the slash, a 404); the map now changes colour with the day/night switch; the "no mosques found" line is visible.
- **External services:** unchanged. Every new call goes to the Telegram Bot API (`api.telegram.org`) from the `bot` container; the Mini App's location comes from the Telegram client itself.
- **Migrations:** none. **New env vars:** none. Restart `bot` and `serve` (same image).
- **How it was tested:** 247 tests (the bot with a fake Telegram API, including Telegram refusing edits, reactions or drafts), ruff; the Mini App location logic in a browser with a stubbed Telegram (granted, denied, no GPS, Telegram older than 8.0, plain browser). Not tried in a real Telegram client yet.

## Release v1.0.3

- **Commit:** the commit the annotated tag `v1.0.3` points to (`git rev-parse v1.0.3^{commit}`).
- **Change:** bot only. New env `TIMUS_APP_MASJIDUZ_ADMIN_URL` (https address of the moderator page; empty for now): the bot's `/admin` button no longer points to `WEBAPP_URL/admin.html`, which is closed (404) on the API domain. Without `WEBAPP_URL` the bot no longer mentions a missing HTTPS address.
- **Moderator actions** (verify, archive, restore, dismiss, reports, map keys) have one user interface: `admin.html`. The `/admin/...` API works with the Telegram signature from any domain, so the page can move to its own domain later: then set `ADMIN_URL` to it, add the domain to `CORS_ORIGINS`, and serve it with `API_BASE=https://api.timus.uz/masjiduz/v1`.
- **Env for this deployment:** `WEBAPP_URL=https://api.timus.uz/masjiduz/web` (staging `https://api-staging.timus.uz/masjiduz/web`), `MODERATOR_IDS=722170186`, `ADMIN_URL` empty. nginx: `/masjiduz/web/` to `serve`, `/masjiduz/web/admin.html` 404.
- **Migrations:** none. Restart `bot` (and `serve`, same image).
- **How it was tested:** 238 tests, ruff.

## Release v1.0.2

- **Commit:** the commit the annotated tag `v1.0.2` points to (`git rev-parse v1.0.2^{commit}`).
- **Fixes acceptance 8b (§13.8):** `/mosques/nearest` p95 < 300 ms with 60 parallel clients. The 660 ms on every endpoint (even `/health` on an empty database) came from waiting on database connections, not from CPU: see CHANGELOG 1.0.2.
- **Migrations:** none.
- **Env vars:** `TIMUS_APP_MASJIDUZ_WEB_CONCURRENCY` (since v1.0.1, default 4 = four cores; CONTRACT §10). `TIMUS_APP_MASJIDUZ_DB_POOL_MAX` default is now **40** for the serve container (10 per worker, all opened at start and kept open). With `bot` (at most 10) the service holds about 50 connections to the cluster: please check the cluster allows that, or set `DB_POOL_MAX` lower (at least 2 per worker; fewer connections means more waiting under load).
- **How it was tested:** 237 tests, ruff. Load (`ab -n 900 -c 60`, 16-core laptop, 2205 mosques, `WEB_CONCURRENCY=4`, `DB_POOL_MAX=40`), p95:

  | database | `/mosques/nearest` | `/health` | `/mosques?limit=20` |
  |---|---|---|---|
  | local | 34 ms | 5 ms | 21 ms |
  | simulated remote: 0.6 s per new connection, +5 ms per reply | 44 ms | 5 ms | 30 ms |
  | simulated remote, +20 ms per reply | 143 ms | 13 ms | 140 ms |
  | v1.0.1 against the +5 ms database (reproduces the 660 ms) | 638 ms | 659 ms | — |

## Release v1.0.1

- **Commit:** the commit the annotated tag `v1.0.1` points to (`git rev-parse v1.0.1^{commit}`).
- **Fixes the v1.0.0 acceptance findings:** (1) `POST /map/yandex-failed` had no rate limit: it and `POST /me/lang` now answer 429 + `Retry-After` past `WRITE_RATE_PER_HOUR` per address and hour (CONTRACT §6); (2) latency: `WEB_CONCURRENCY` worker processes, a lighter `/mosques/nearest`, a cached `/health` probe.
- **Migrations:** yes — `V0004__rate_hits.sql` (a new UNLOGGED table, expand only: v1.0.0 runs unchanged against it, so a failed deploy can stay on v1.0.0). Run `migrate` before `serve`/`bot`.
- **New env vars:** `TIMUS_APP_MASJIDUZ_WEB_CONCURRENCY` (default 4; about one per CPU of the serve container). `TIMUS_APP_MASJIDUZ_DB_POOL_MAX` is now the total of the serve container (default 10), split between its workers (at least 2 each). `TIMUS_APP_MASJIDUZ_WORKER_FD` is internal (set by serve for its own workers), never set it.
- **Containers:** unchanged: `serve` (now one leader process plus `WEB_CONCURRENCY - 1` workers on port 8080; SIGTERM stops them all, a dying worker stops the container so it is restarted), `bot` (single replica), `import-osm` nightly at 02:15 Tashkent.
- **How it was tested:** 233 unit/integration tests against PostgreSQL (including 4 workers sharing the socket and the limits: 150 posts from one address → 30 × 200, 120 × 429; SIGTERM stops all workers), ruff; load test with `ab -n 900 -c 60` against a local database of 2205 mosques on a 16-core laptop:

  | endpoint | v1.0.0 p95 | v1.0.1, WEB_CONCURRENCY=1 | v1.0.1, WEB_CONCURRENCY=4 |
  |---|---|---|---|
  | `/health` | 113 ms | 16 ms | 23 ms |
  | `/mosques/nearest` | 139 ms | 109 ms | 60 ms |
  | `/mosques?limit=20` | 69 ms | 61 ms | 27 ms |

  The p95 depends on the CPUs given to the serve container: with fewer than 4 CPUs set `WEB_CONCURRENCY` to the CPU count.

## Still valid from v1.0.0
- **Scope of v1 (Eldor, requirements v1.2 §17):** API + bot only. The web client, Mini App and `admin.html` are in the image but are **not deployed in v1**; `WEBAPP_URL` and `CORS_ORIGINS` stay empty and the bot works without Mini App buttons.
- **Env vars (names only):** all of `.env.example`. Required secrets in staging/prod: `TIMUS_APP_MASJIDUZ_DB_PASSWORD`, `_ACTOR_SALT`, `_TELEGRAM_BOT_TOKEN`. Optional: `_ADMIN_TOKEN`, `_GEMINI_API_KEY`.
- **Manual steps for Eldor:** set `_MODERATOR_IDS`; set `_GEMINI_API_KEY` (handed over through a one-time secret link, restricted to the Generative Language API) and `_AI_DAILY_LIMIT`; `_TRUSTED_PROXIES=127.0.0.1/32,::1/128,172.16.0.0/12` (nginx overwrites `X-Forwarded-For` with one value); `migrate` before every deploy in its own container.
- **`SHOW_TILE_MOSQUES=0`:** set it after the first complete `import-osm` run has filled the database (the day after the first nightly run succeeds), not before.
- **Key rotation:** the key owner creates a new key and hands it over the same way, Eldor replaces the env value and restarts `serve` and `bot`, then the old key is deleted.
- **Migrations must be expand → contract:** every migration has to work with the previous version of the code too (add first; drop or rename only in a later release).
