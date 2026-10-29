# masjid.uz — API contract (v1)

This document is what TIMUS checks the service against (INTEGRATION-REQUIREMENTS.md v1.1, §16 wins over earlier sections).
Every endpoint answers in the envelope below. Anything not listed here answers `404 NOT_FOUND` in the envelope.

## 1. Conventions

* Base path: `/masjiduz/v1`. Web client: `/masjiduz/web/`. Container port `8080`.
* JSON only (`application/json`, UTF-8), **camelCase** field names, kebab-case plural resource names.
* Time: ISO-8601 UTC with `Z`. Prayer times are local wall-clock `HH:MM` in the time zone `Asia/Tashkent`.
* Request body: max **64 KiB**; over it → `413 PAYLOAD_TOO_LARGE` before anything else is read.
* Unknown JSON fields → `400 VALIDATION_ERROR` (`message.reason` = the field). Wrong type/range → the same.
* Every response carries `X-Request-Id` (the request's UUID if valid, else generated), `X-Timus-Env`, `Date`.
* CORS: only origins in `TIMUS_APP_MASJIDUZ_CORS_ORIGINS` (default none). Allowed request headers: `Content-Type, Authorization, X-Request-Id, X-Client-Id, X-Device-Id, X-Client-Platform, X-OS-Version, X-App-Version, X-Device-Model, X-Telegram-Init-Data`.
* Client address: `X-Forwarded-For` is used only when the TCP peer is inside `TIMUS_APP_MASJIDUZ_TRUSTED_PROXIES` (default `127.0.0.0/8, ::1/128, 172.16.0.0/12, 10.0.0.0/8, 192.168.0.0/16`).

### Envelope

```json
{"success": true, "httpStatus": 200, "code": "OK", "message": {"code": "common.ok", "text": "OK"}, "data": {}}
```

Errors: `success=false`, `data=null` unless a documented payload (e.g. duplicates). `message.code` is `error.<camelCase>`;
`message.text` is English; `message.reason` is present only when there is one.

| code | HTTP | meaning |
|---|---|---|
| `OK` | 200/201 | success |
| `BAD_REQUEST` | 400 | malformed JSON / request |
| `VALIDATION_ERROR` | 400 | a field is missing, unknown, wrong type or out of range |
| `UNAUTHORIZED` | 401 | moderator access needed |
| `FORBIDDEN` | 403 | not allowed |
| `NOT_FOUND` | 404 | no such endpoint or record |
| `METHOD_NOT_ALLOWED` | 405 | wrong method for a known path |
| `CONFLICT` | 409 | state conflict (already reported, already archived, …) |
| `PAYLOAD_TOO_LARGE` | 413 | body over 64 KiB |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | body is not JSON |
| `RATE_LIMITED` | 429 | too many requests, `Retry-After` seconds |
| `INTERNAL_ERROR` | 500 | never leaks details |
| `SERVICE_UNAVAILABLE` | 503 | database busy/unreachable |
| `MASJIDUZ_POSSIBLE_DUPLICATE` | 409 | a mosque already exists within `duplicateRadiusM`; `data.duplicates` lists them; resend with `"force": true` to create anyway |
| `MASJIDUZ_MOSQUE_ARCHIVED` | 409 | the record is archived and cannot be edited |
| `MASJIDUZ_FIELD_LOCKED` | 403 | for records taken from OpenStreetMap only the prayer times can be changed (`message.reason` = the field) |

Specific `message.code` values clients may show: `error.validation`, `error.invalidCoordinates`, `error.invalidBbox`, `error.invalidPrayerTimes`,
`error.invalidKind`, `error.reasonRequired`, `error.evidenceRequired`, `error.invalidLang`, `error.alreadyReported`, `error.alreadyArchived`,
`error.notArchived`, `error.possibleDuplicate`, `error.archived`, `error.fieldLocked`, `error.rateLimited`, `error.notFound`,
`error.unauthorized`, `error.serviceUnavailable`, `error.internal`, `error.tooLarge`, `error.badJson`, `error.badRequest`, `error.methodNotAllowed`, `error.unsupportedMediaType`, `error.invalidStatus`, `error.invalidAction`, `error.readerUnavailable`, `error.badImage`, `error.badAudio`. A 405 carries an `Allow` header; `/masjiduz/web` needs the trailing slash.

### Lists

`data = {"items": [...], "nextCursor": "<opaque>" | null}`; query `limit` (default 20, max 100) and `cursor`.
**One documented exception:** `GET /mosques?bbox=…` (section 3).

### Identity (no accounts)

Clients may send `X-Telegram-Init-Data` (verified by HMAC with the bot token, `auth_date` ≤ 24 h), else `X-Device-Id` (mobile app installation id),
else `X-Client-Id` (browser id). Only a verified Telegram profile is recorded.

## 2. Health

`GET /masjiduz/v1/health` → `data: {"status": "UP"|"DEGRADED"|"DOWN", "version": "1.0.4", "commit": "...", "env": "staging", "db": "UP"|"DOWN"}`.
`503` when DOWN (database unreachable). Public, no rate limit. The database is probed in the background (`SELECT 1` every 2 s on its own long-lived connection, 2 s timeout); a request only reads the last result, so it never waits on the database (a probe older than 10 s counts as DOWN).

## 3. Public endpoints

### `GET /masjiduz/v1/config`
`data`: `{"styleUrl": str, "duplicateRadiusM": number, "showTileMosques": bool, "prayer": {"fajrAngle": n, "ishaAngle": n, "asrFactor": 1|2, "dhuhrOffsetMin": n}, "botUsername": str, "adminTokenLogin": bool, "photoReading": bool, "voiceReading": bool}` (the last two are true only when the Gemini key is configured)

`config.map`: only the **mobile app** (a request with `X-Device-Id`) can be handed a Yandex MapKit key; the browser, Telegram and every other client always get OSM. It is `{"provider": "osm"}` (the default) or `{"provider": "yandex", "yandexKey": str, "keyId": int}`.
### `POST /masjiduz/v1/map/yandex-failed` body `{"keyId": int}` → `{"exhausted": bool, "map": <config.map>}`: a client reports that Yandex refused its key; three different clients exhaust the key for the (Moscow) day; the reply already carries the next key or `osm`, so the client switches without a gap. Only the mobile app uses this; the server never contacts Yandex.
### `GET /masjiduz/v1/mosques?bbox=minLon,minLat,maxLon,maxLat` (map list — exception to cursor lists)
`data = {"items": [{"id": uuid, "lat": n, "lon": n, "name": str|null, "status": "unverified"|"reported"|"verified"}], "truncated": bool}`.
Up to 3000 items, no cursor, archived records omitted. `Cache-Control: public, max-age=60`, `ETag` (`If-None-Match` → `304`).
Without `bbox`: a normal cursor list of full mosque objects (below). Errors: `error.invalidBbox`.

### Mosque object
`{"id", "lat", "lon", "name", "address", "prayerTimes": {"fajr"|"dhuhr"|"asr"|"maghrib"|"isha"|"jumuah": "HH:MM"}, "source", "origin": "community"|"imported", "status", "verificationNote", "version": int, "osmRef", "photoUrl", "photoCredit", "createdAt", "updatedAt", "timesUpdatedAt"}`; `distanceM` is added by `nearest`/`nearby`.

### `GET /masjiduz/v1/mosques/{id}` → mosque object. `404` for an unknown id.
### `GET /masjiduz/v1/mosques/search?q=text` → list (max 20, `nextCursor` always null). Items: `{id, name, address, photoUrl, lat, lon, status}`. Finds names in any script or apostrophe form.
### `GET /masjiduz/v1/mosques/nearest?lat&lon&limit&cursor` → list of mosque objects with `distanceM`, nearest first (the cursor goes at most 500 deep).
### `GET /masjiduz/v1/mosques/nearby?lat&lon&radius` → list within `radius` m (max 500), `nextCursor` null.
### `POST /masjiduz/v1/mosques` → 201 mosque object
Body: `lat`, `lon` (required numbers), `name` (≤200), `address` (≤300), `prayerTimes` (object), `force` (bool). Errors: `MASJIDUZ_POSSIBLE_DUPLICATE`.
### `PATCH /masjiduz/v1/mosques/{id}` → 200 mosque object
Body (any of): `lat`, `lon`, `name`, `address`, `prayerTimes` (merged key by key, `null`/`""` clears a key), `note` (≤300). Concurrent edits never fail: applied on the latest row, field by field. Errors: `MASJIDUZ_MOSQUE_ARCHIVED`, `MASJIDUZ_FIELD_LOCKED`.
### `GET /masjiduz/v1/mosques/{id}/history?limit&cursor` → list (newest first) of `{seq, mosqueId, version, action, actor: "user"|"moderator"|"osm"|"system"|null, at, changes: {field: [old, new]}, note}`. No personal identifiers.
### `POST /masjiduz/v1/mosques/{id}/reports` → 201 `{"ok": true}`
Body: `kind` (`incorrect|duplicate|relocated|closed|other`), `reason` (1–1000, required). A client identity header is required (400 otherwise). Errors: `error.alreadyReported` (CONFLICT). Three open reports from different people move an unverified mosque to `reported`.
### `GET /masjiduz/v1/me` → `{"lang": "uz"|"uz-cyrl"|"ru"|"en"|null}` (verified Telegram user only; else null).
### `POST /masjiduz/v1/me/lang` body `{"lang": …}` → `{"lang": …}`.
### `POST /masjiduz/v1/timetable/photo` and `POST /masjiduz/v1/timetable/voice` (reading by Gemini)
Raw body (not JSON): a photo of the timetable (`Content-Type: image/jpeg|image/png|image/webp`, max 5 MiB) or a spoken recording (`audio/mp4|audio/m4a|audio/aac|audio/ogg|audio/webm|audio/wav|audio/mpeg`, max 2 MiB; a recording is at most 60 s, the clients stop at 60 s) — these two routes document a larger body than 64 KiB. The file is forwarded to Google Gemini in memory and never stored (not on disk, not in the database, not in logs; only a one-hour in-memory answer cache keyed by the file hash). Before anything is sent the bot and the clients tell the user that the photo/voice goes to Google; the privacy text names Google. All users together may use at most `TIMUS_APP_MASJIDUZ_AI_DAILY_LIMIT` readings per day (Tashkent day, default 300; cached answers are free), after that `503 error.readerUnavailable` with `Retry-After`; nothing is saved: the client shows the result and saves it with a normal `PATCH`.
`data = {"times": {…}, "alt": [] | {…}, "complete": bool, "warnings": [str], "heard": str}` (`heard` only for voice). `alt` is `[]` normally; when two models read all five times differently it holds the second reading (same keys as `times`) and `complete` is false, so the user chooses. Only plausible times for each prayer, in the order of the day, are returned; anything unsure is left out. Errors: `415`, `413`, `429` (30 per device/hour, 120 per address/hour), `503 error.readerUnavailable` (no key configured, every model at its limit, or providers down; `Retry-After` seconds), `400 error.badImage` / `error.badAudio`.
### `GET /masjiduz/v1/export/osm-derived` → `data` = GeoJSON FeatureCollection of the OpenStreetMap-derived part (ODbL share-alike).

## 4. Moderation (`/masjiduz/v1/admin/...`)

Auth: a signature-verified Telegram user (`X-Telegram-Init-Data`, `auth_date` ≤ 24 h) whose id is in `TIMUS_APP_MASJIDUZ_MODERATOR_IDS`;
or `Authorization: Bearer <TIMUS_APP_MASJIDUZ_ADMIN_TOKEN>` (constant-time compare) when that token is configured (≥ 32 chars), otherwise token login is off.
Failure → `401 UNAUTHORIZED`; 10 failures / 10 min / IP → `429`. Every action is recorded in the history with `admin:<telegram id>` (`admin:token` for the token).

* `GET /admin/overview` → `{counts: {"origin/status": n}, openReports, recent: [...]}`
* `GET /admin/actors?q&limit` → list of actor records (personal data: moderators only).
* `GET /admin/mosques?status=unverified|reported|verified|archived&limit&cursor` → list (+ `total`), objects include `creatorId`.
* `GET /admin/reports?all=1` → `{groups: [...]}`
* `GET /admin/map` → `{"provider": "osm"|"yandex", "keys": [{"id","label","kind": "app","key": "ABCD…WXYZ" (masked),"dailyLimit","enabled","exhausted","usersToday"}], "effective": {"app": "osm"|"yandex"}}`
* `POST /admin/map/provider` body `{"provider": "osm"|"yandex"}` → the same as `GET /admin/map`
* `POST /admin/map/keys` body `{"key", "label", "kind": "app" (the only kind), "dailyLimit": 25000}` → 201 (`error.invalidKey`, `error.keyExists`, `error.invalidKind`, `error.invalidLimit`); `PATCH /admin/map/keys/{id}` body any of `enabled`, `dailyLimit`, `label`, `reset: true`; `POST /admin/map/keys/{id}/delete`
* `POST /admin/mosques/{id}/archive|restore|verify|dismiss` body `{"note", "kind"}` (`verify` requires a note; `kind` only for `dismiss`: only the open reports of that kind are rejected, others keep the mosque `reported`) → mosque object.

## 5. Web client

**Deployment (Eldor, after v1.0.2): the web client and the Telegram Mini App are served at `/masjiduz/web/` on the API domain and `WEBAPP_URL` points there. `admin.html` is not served on the API domain (requirements §8: nginx answers 404); it moves to its own domain later.** `/masjiduz/web/` serves the map and the Telegram Mini App. `/masjiduz/web/config.js` is generated at request time:
`window.MASJIDUZ_API_BASE = "<TIMUS_APP_MASJIDUZ_API_BASE>"` (default `/masjiduz/v1`). Asset names are content-hashed (`app.1a2b3c4d.js`, 1 year immutable); `index.html` is `no-cache`. MapLibre is bundled (`vendor/`).
Headers (the web never uses Yandex: the CSP is always this strict one): `Content-Security-Policy: default-src 'self'; img-src 'self' data: blob: https://tiles.openfreemap.org https://upload.wikimedia.org; connect-src 'self' https://tiles.openfreemap.org; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' https://telegram.org; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: geolocation=(self)`. No `unsafe-eval`.
`/masjiduz/web/admin.html` is the moderator page (Telegram signature or bearer token). It is the **only** user interface for moderator actions (verify, archive, restore, dismiss, reports, map keys): the main Mini App and the mobile app have none. The `/admin/...` API accepts the Telegram signature from any page, so the moderator page can live on its own domain without server changes; the bot's `/admin` button opens `ADMIN_URL` (no button while it is empty). For a separate domain: add it to `CORS_ORIGINS`, and serve the page with `API_BASE` set to the absolute API address (`https://api.timus.uz/masjiduz/v1`).

## 6. Rate limits

Every public `POST`/`PATCH` is rate limited per client IP (the address nginx puts in `X-Forwarded-For`) and per hour; over the limit → `429 RATE_LIMITED` + `Retry-After` (seconds). The counters are kept in the database (table `rate_hits`, the key is a salted hash, never the address), so all `serve` workers and replicas share them.
* Community writes (create/edit mosques, reports): `TIMUS_APP_MASJIDUZ_WRITE_RATE_PER_HOUR` (default 30).
* `/admin/...`: a signed-in moderator is not counted (actions are logged with the Telegram id); failed admin logins: 10 per 10 minutes per IP → `429`.
* `POST /me/lang` and `POST /map/yandex-failed`: the same number (`WRITE_RATE_PER_HOUR`), each route its own budget, so they never use up the community budget.
* `POST /timetable/photo|voice`: 30 per device and 120 per address per hour, plus `AI_DAILY_LIMIT` per day for everybody (see §4).

## 7. Container commands

One image, `python server/cli.py <command>` (entrypoint, default `serve`):

| command | what |
|---|---|
| `serve` | HTTP API + web (does **not** migrate; exits if migrations are pending) |
| `bot` | Telegram bot, a single replica, handles SIGTERM |
| `migrate` | applies `server/migrations/V*.sql` into schema `masjiduz`, history in `masjiduz.schema_history`, checksum-checked, idempotent |
| `import-osm` | one-off nightly OpenStreetMap sync, idempotent, prints one summary line |
| `import --file F.json` | one-off data import (format in `docs/IMPORT.md`), idempotent |

## 8. Data stored (schema `masjiduz`)

* `mosques`: coordinates, name, address, prayer times, status, OSM reference, photo link (Wikimedia address, never the picture), creator id.
* `mosque_history`: append-only audit rows: action, actor id, changes, note, non-personal client snapshot (kind, platform, OS/app version, device model, user agent).
* `reports`: kind, reason (free text from the reporter), reporter id, client snapshot.
* `actors`: id (`tg:<id>` | `device:<id>` | `client:<id>`), Telegram profile fields **only when signature-verified**, device/app info, `ip_hash` (salted HMAC of the /24 (IPv4) or /48 (IPv6) network, never the address; cleared after 30 days), counters, first/last seen.
* `map_settings`, `map_keys`, `map_key_users`, `map_key_reports`: the map provider choice, the Yandex MapKit keys of the app (secrets held in the database, masked in the admin listing), and per-day counters (client id + key id) that keep a key within its daily limit; removed after 2 days.
* `ai_calls`, `ai_cooldown`: only counters (which model slot was called when) that keep the readers inside their provider limits; no file or user data.
* `rate_hits` (UNLOGGED): rate-limit counters (bucket, salted hash of the client address or device, time); rows older than an hour are deleted.
* `bot_users`, `bot_subs`, `bot_sent`: Telegram id, chosen language/region/reminder settings, subscriptions, a send ledger. `/stop` removes every row of the user.
* No full IP addresses are stored anywhere. Logs never contain tokens, `Authorization`, `initData`, IPs or free text.

## 9. External services (the only outbound calls)

| service | host | who | why |
|---|---|---|---|
| Telegram Bot API | `api.telegram.org` | `bot`, `serve` | the bot; (serve: none at request time) |
| OpenStreetMap Overpass | `overpass-api.de` (or `TIMUS_APP_MASJIDUZ_OVERPASS_URL`) | `import-osm` only | nightly sync, `User-Agent` contains `info@timus.uz` |
| Wikimedia Commons | `upload.wikimedia.org` | the **browser** only (CSP `img-src`) | mosque photos; the server never calls it (thumbnail address is derived from the file name) |
| Google Gemini API | `generativelanguage.googleapis.com` | `serve`, `bot` (only when `GEMINI_API_KEY` is set; off otherwise) | reading a timetable photo / spoken times; the file is sent, never stored. **Approved by Eldor (INTEGRATION-REQUIREMENTS v1.2 §17)** with conditions: the user is told before sending, media is not kept, `AI_DAILY_LIMIT`, photo ≤ 5 MB, voice ≤ 60 s, Google named in the privacy text. The key is created by the project owner (restricted to the Generative Language API), billing is in the owner's Google project. |
| Yandex MapKit | none from the server | the **mobile app** only (MapKit SDK on the device) | optional map provider, off by default (OSM); the admin turns it on, the key is restricted to the app's bundle/package id. **Approved by Eldor for the app only; not for the web.** The server and the web never contact Yandex. |
| Map tile server | `tiles.openfreemap.org` | the **browser** only (CSP `img-src`, `connect-src`) | vector map tiles, style, glyphs |

Nothing else: no telemetry or analytics. (`telegram.org/js/telegram-web-app.js` is loaded by the browser inside Telegram only; CSP `script-src https://telegram.org`.)

## 10. Environment variables

`TIMUS_ENV` (`dev|staging|prod`), `TIMUS_APP_MASJIDUZ_` + `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` (secret), `DB_POOL_MAX` (40, connections of the whole `serve` container, split between its workers and kept open; `bot`/`import-osm` use at most 10), `WEB_CONCURRENCY` (4, `serve` worker processes sharing the port; 1–16),
`ADMIN_TOKEN` (secret, optional, ≥32 chars), `TELEGRAM_BOT_TOKEN` (secret), `MODERATOR_IDS` (comma-separated Telegram ids), `ACTOR_SALT` (secret), `CORS_ORIGINS`, `API_BASE`, `TRUSTED_PROXIES`, `COMMIT`,
`BOT_USERNAME`, `WEBAPP_URL` (https address of the web client, e.g. `https://api.timus.uz/masjiduz/web`; empty = the bot shows no map button), `ADMIN_URL` (https address of the moderator page; empty = the bot's `/admin` only says it is not connected), `WRITE_RATE_PER_HOUR` (30), `DUPLICATE_RADIUS_M` (50), `REPORT_THRESHOLD` (3), `SHOW_TILE_MOSQUES` (1), `TILE_STYLE_URL`, `AI_DAILY_LIMIT` (300), `GEMINI_API_KEY` (secret, optional, comma-separated keys of separate projects add their limits; reading is off without any key), `GEMINI_MODELS` (`name:rpm:rpd,...` in the order tried), `OVERPASS_URL`, `PRAYER_FAJR_ANGLE`, `PRAYER_ISHA_ANGLE`, `PRAYER_ASR_FACTOR`, `PRAYER_DHUHR_OFFSET_MIN`.
In `staging`/`prod` missing secrets (`DB_PASSWORD`, `ACTOR_SALT`, `TELEGRAM_BOT_TOKEN`) stop the process with one log line naming the variable; `CORS_ORIGINS=*` stops it in `prod` (warning in `staging`).
Names only here; values are never in the repository. See `.env.example`.
