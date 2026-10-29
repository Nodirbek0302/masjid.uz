# Changelog

## 1.0.4 — the bot keeps the chat tidy; newer Telegram features
### Changed
- A button press updates the message it belongs to (`editMessageText` / `editMessageCaption`) instead of sending a new one; on a mosque card the card stays and only its subscribe button flips, with a short notice (`answerCallbackQuery`); the `/subs` list is redrawn without the removed mosque. If Telegram cannot edit, a new message is sent as before.
- Reading a photo or voice message: 👀 / 👌 (or 🤔) reactions on the user's own message and a temporary "Reading…" draft (`sendMessageDraft`) with the typing status, instead of a "Reading…" message that stayed in the chat.
### Added
- Button colours (`style`, Bot API 9.4): save green, cancel and "switch everything off" red, the main actions blue. Older apps show them as before.
- Mosque card: "📋 Copy the times" (`copy_text`) and "📍 On the Telegram map" (`sendVenue`, the map inside Telegram with directions in any maps app).
- The bot's description and short description in Uzbek, Russian and English (`setMyDescription`, `setMyShortDescription`), set at start.
- Mini App: inside Telegram (Bot API 8.0+) the position comes from Telegram's `LocationManager` (the browser's location prompt can be blocked in Telegram's webview); elsewhere from the browser, as before.
### Fixed
- Bot: the menu button (bottom left) opened `WEBAPP_URL` without the trailing slash, a 404; it now opens `…/masjiduz/web/`. The server also answers `GET /masjiduz/web` with a 301 to `/masjiduz/web/` (query kept), so an address without the slash works anywhere.
- Web: switching between night and day mode left the map in its old colours (black after leaving night mode): since 1.0.0 the map was only restyled when the page had no `theme-color` tag.
- Web: the "No mosques found nearby" line under an empty list was squeezed into a 1-pixel box and could not be seen.
- Mini App: until the user picks day or night, it follows Telegram's own theme (`colorScheme`, `themeChanged`) instead of the phone's.
### Migrations
- none

## 1.0.3 — web on, moderator page apart
### Added
- `TIMUS_APP_MASJIDUZ_ADMIN_URL`: the bot's `/admin` button opens this address (the moderator page on its own domain). Empty: a moderator is told the panel is not connected; nobody else gets an answer.
### Fixed
- The bot no longer builds the moderator link from `WEBAPP_URL` (`/admin.html` is closed on the API domain, the button would open a 404).
- With `WEBAPP_URL` empty `/start` no longer tells every user that an HTTPS address is missing; it greets normally without a map button.
### Migrations
- none

## 1.0.2 — latency under load
### Fixed
- p95 of every endpoint stayed ~660 ms under 60 parallel clients because requests waited on the database connection, not on the code:
  `/health` probed the database while every other health request waited behind it, the pool opened connections while requests waited (slow on a
  remote cluster), and with 10 connections for the container (3 per worker) requests queued for a connection.
### Changed
- `/health` reads the result of a background probe (`SELECT 1` every 2 s on its own connection); it never waits on the database.
- The `serve` pool is opened in full at start and kept open; `DB_POOL_MAX` default 10 → 40 for the container (10 per worker with `WEB_CONCURRENCY=4`). `bot` and `import-osm` use at most 10.
- `/mosques/nearest` is one SQL statement (the database ranks the mosques within 100 km; the whole radius only when fewer are that close); reads run in autocommit (no COMMIT round trip).
- Measured locally, 60 parallel clients, 900 requests, with a simulated remote database (0.6 s per new connection, +5 ms per reply): `/mosques/nearest` p95 638 ms → 44 ms, `/health` 659 ms → 5 ms; with +20 ms per reply: 143 ms and 13 ms.
### Migrations
- none

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
