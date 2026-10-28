# Release v1.0.0

- **Commit:** the commit the annotated tag `v1.0.0` points to (fill in at tagging).
- **Migrations:** yes — `V0001__init.sql` (fresh baseline in schema `masjiduz`). Run `migrate` before `serve`/`bot`.
- **Data:** optional first import: `import --file <path>` (format `docs/IMPORT.md`), then `import-osm` nightly.
- **Env vars (names only):** all of `.env.example`. Required secrets in staging/prod: `TIMUS_APP_MASJIDUZ_DB_PASSWORD`, `_ACTOR_SALT`, `_TELEGRAM_BOT_TOKEN`. Optional: `_ADMIN_TOKEN`, `_GEMINI_API_KEY`.
- **Containers:** same image, `serve` and `bot` (single replica) as long-running containers; `import-osm` as a nightly one-off container.
- **Scope of v1 (Eldor, requirements v1.2 §17):** API + bot only. The web client, Mini App and `admin.html` are in the image but are **not deployed in v1** (the web domain comes later); `WEBAPP_URL` and `CORS_ORIGINS` stay empty and the bot works without Mini App buttons.
- **Manual steps for Eldor:** set `_MODERATOR_IDS`; set `_GEMINI_API_KEY` (handed over through a one-time secret link, restricted to the Generative Language API) and `_AI_DAILY_LIMIT`; `_TRUSTED_PROXIES=127.0.0.1/32,::1/128,172.16.0.0/12` (nginx overwrites `X-Forwarded-For` with one value); `migrate` before every deploy in its own container; `import-osm` nightly at 02:15 Tashkent.
- **`SHOW_TILE_MOSQUES=0`:** set it after the first complete `import-osm` run has filled the database (the day after the first nightly run succeeds), not before; announce the date in the release notes of that version.
- **Key rotation:** the key owner creates a new key and hands it over the same way, Eldor replaces the env value and restarts `serve` and `bot`, then the old key is deleted.
- **Migrations must be expand → contract:** `migrate` runs before the new version starts and the old version keeps running if it fails, so every migration has to work with the previous version of the code too (add first; drop or rename only in a later release).
- **How it was tested:** unit/integration tests against PostgreSQL, ruff, manual check of the web client in a browser; see the CI run of the tagged commit.
