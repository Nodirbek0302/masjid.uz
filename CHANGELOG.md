# Changelog

## 1.0.1 — acceptance fixes
### Fixed
- `POST /map/yandex-failed` and `POST /me/lang` are rate limited (per address and hour, `WRITE_RATE_PER_HOUR`, each its own budget): 429 + `Retry-After` over the limit (CONTRACT §6).
- Latency under load: `serve` runs `WEB_CONCURRENCY` worker processes (default 4) on one port; `/mosques/nearest` reads only id/lat/lon inside the search circle and the full rows for the returned page only; `/health` reuses its database probe for one second. Locally, 60 parallel clients and 900 requests: `/mosques/nearest` p95 139 ms → 60 ms, `/health` p95 113 ms → 23 ms.
### Changed
- Rate-limit counters moved from process memory to the database (`rate_hits`), shared by all workers and replicas; the key is a salted hash, not the address.
### Migrations
- `V0004__rate_hits.sql`: new UNLOGGED table `rate_hits` (expand only; v1.0.0 keeps working with it).

## 1.0.0 — first TIMUS release
### Added
- API under `/masjiduz/v1` with the standard envelope, camelCase fields, cursor lists, `GET /health`, `X-Request-Id`, `X-Timus-Env`.
- Map list `GET /mosques?bbox=…` (compact fields, up to 3000, cached 60 s with ETag).
- Web client under `/masjiduz/web/` (content-hashed assets, generated `config.js`, strict CSP without `unsafe-eval`).
- Container commands `serve`, `bot`, `migrate`, `import-osm`, `import --file`; graceful SIGTERM.
- Photo/voice timetable reading through Google Gemini (off without a key; approved by Eldor with conditions): the user is told before sending that Google reads the file, nothing is stored, `AI_DAILY_LIMIT` caps readings per day, photo ≤ 5 MB, voice ≤ 60 s.
- Yandex map provider for the **mobile app only** (MapKit, admin-controlled key pool, off by default); the web is always OpenStreetMap with the strict CSP.
### Changed
- Environment variables renamed to `TIMUS_ENV` / `TIMUS_APP_MASJIDUZ_*` (see `.env.example`); secrets have no default in staging/prod.
- Admin: Telegram signature stays the main login (moderator ids from env, `auth_date` ≤ 24 h); the bearer token is optional.
- Client networks are hashed per /24 (IPv4) or /48 (IPv6) and cleared after 30 days.
- Photo links are derived from OSM tags (no request to Wikimedia).
### Removed
- Old `/api/...` paths and snake_case, on-server Tesseract/Whisper, Caddy/systemd/compose/registry-push files (moved to `legacy/`).
### Migrations
- Fresh baseline `V0001__init.sql` in schema `masjiduz`; existing data is carried over with `import --file` (`docs/IMPORT.md`).
### Breaking
- Everything: new paths, envelope and field names; the mobile app moves to `/masjiduz/v1` at the same time.
