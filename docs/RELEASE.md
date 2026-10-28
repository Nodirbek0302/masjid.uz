# Release v1.0.0

- **Commit:** the commit the annotated tag `v1.0.0` points to (fill in at tagging).
- **Migrations:** yes — `V0001__init.sql` (fresh baseline in schema `masjiduz`). Run `migrate` before `serve`/`bot`.
- **Data:** optional first import: `import --file <path>` (format `docs/IMPORT.md`), then `import-osm` nightly.
- **Env vars (names only):** all of `.env.example`. Required secrets in staging/prod: `TIMUS_APP_MASJIDUZ_DB_PASSWORD`, `_ACTOR_SALT`, `_TELEGRAM_BOT_TOKEN`. Optional: `_ADMIN_TOKEN`, `_GEMINI_API_KEY`.
- **Containers:** same image, `serve` and `bot` (single replica) as long-running containers; `import-osm` as a nightly one-off container.
- **Manual steps for Eldor:** set `_MODERATOR_IDS`; decide whether to approve Gemini (`docs/CONTRACT.md` §9) before setting the key; nginx must also route the web host to `/masjiduz/web/` (the moderator page is `/masjiduz/web/admin.html`).
- **How it was tested:** unit/integration tests against PostgreSQL, ruff, manual check of the web client in a browser; see the CI run of the tagged commit.
