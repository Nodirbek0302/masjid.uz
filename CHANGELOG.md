# Changelog

## 1.0.0 — first TIMUS release
### Added
- API under `/masjiduz/v1` with the standard envelope, camelCase fields, cursor lists, `GET /health`, `X-Request-Id`, `X-Timus-Env`.
- Map list `GET /mosques?bbox=…` (compact fields, up to 3000, cached 60 s with ETag).
- Web client under `/masjiduz/web/` (content-hashed assets, generated `config.js`, strict CSP without `unsafe-eval`).
- Container commands `serve`, `bot`, `migrate`, `import-osm`, `import --file`; graceful SIGTERM.
- Photo/voice timetable reading through Google Gemini (off without a key).
### Changed
- Environment variables renamed to `TIMUS_ENV` / `TIMUS_APP_MASJIDUZ_*` (see `.env.example`); secrets have no default in staging/prod.
- Admin: Telegram signature stays the main login (moderator ids from env, `auth_date` ≤ 24 h); the bearer token is optional.
- Client networks are hashed per /24 (IPv4) or /48 (IPv6) and cleared after 30 days.
- Photo links are derived from OSM tags (no request to Wikimedia).
### Removed
- Old `/api/...` paths and snake_case, Yandex map provider and its key pool, on-server Tesseract/Whisper, Caddy/systemd/compose/registry-push files (moved to `legacy/`).
### Migrations
- Fresh baseline `V0001__init.sql` in schema `masjiduz`; existing data is carried over with `import --file` (`docs/IMPORT.md`).
### Breaking
- Everything: new paths, envelope and field names; the mobile app moves to `/masjiduz/v1` at the same time.
