# Release v1.0.1

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
