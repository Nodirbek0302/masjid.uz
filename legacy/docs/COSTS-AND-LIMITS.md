# Costs and known limits (owner pays infrastructure; users pay nothing)

| Item | Cost | Notes |
|---|---|---|
| Source code / tools | 0 | Python, PostgreSQL, Caddy, MapLibre are free software |
| Server (VPS) | owner-paid, monthly | 1 vCPU / 1–2 GB RAM is enough to start; PostgreSQL + API on same host |
| Domain | owner-paid, yearly | |
| TLS certificate | 0 | Let's Encrypt via Caddy |
| Map tiles | 0 today | OpenFreeMap public instance: donation-funded, no SLA/guarantee; plan a self-hosted fallback |
| Navigation links | 0 | plain links to Yandex Maps; no API used |
| Telegram bot / Mini App | 0 | Telegram does not charge for bots |
| Backups off-site | owner-paid, small | storage you control |
| Mobile app stores (if native app shipped) | Apple Developer ~$99/yr, Google Play one-off ~$25 | owner-paid; not needed for the web/Mini App |

## Known limitations
- No uptime guarantee for third-party tiles (OpenFreeMap). If it fails the map goes blank; our API still works.
- OSM POIs in Uzbekistan may be incomplete; tile data cannot distinguish mosques from other places of worship.
- Anonymous edits are possible (identity = device id or verified Telegram id). Abuse controls: rate limit, optimistic
  versioning, immutable history, report grouping, admin archive/restore. Device ids are not strong identities.
- Rate limiter is in-memory per process; behind Caddy it uses the proxy address unless forwarded headers are trusted. Add Caddy rate limiting if abused.
- Not implemented yet: native mobile app, admin web UI (admin API only), bulk import of open data, offline mode.

## Start-up performance (what was slow and what was changed)
Cause: a chain of sequential requests (page → JS → /api/config → style → tilejson → sprite/fonts/tiles → mosque list),
each ~0.25–0.35 s to the tile host, plus an uncompressed 803 KB map library that was revalidated on every visit.
Now: style, tilejson, config, JS and the first mosque query start in parallel; scripts are gzip-compressed (~211 KB) and
cached for a year (pinned `vendor/`), own files use ETag/304; the map opens at the cached or device location (zoom 14)
so the first tiles are the right ones; only a window around the view (+50%) is queried and it grows as the user pans;
panning is bounded to Uzbekistan + margin (`REGION` in `web/app.js`), so no world-scale tiles are requested.
Not measured: cold-cache load on a real phone/mobile network, and the device-location prompt inside Telegram.

## Daily prayer times (top strip)
Computed on the device from the sun's position (`web/prayer.js`): no API, key, quota or request. Defaults: Fajr 15°, Isha 15°,
Asr Hanafi (shadow factor 2), Dhuhr +5 min. Checked on 2026-10-23 against a public Uzbek timetable site for Tashkent,
Samarqand and Qarshi: all six times within about 5 minutes (Asr matches to ±3, Fajr/Isha to ±2, sunrise up to 5 earlier,
Dhuhr deliberately +5). Not the official calendar: tune with `PRAYER_*` env vars or import an official table if the
Muslim Board gives permission. islom.uz has no documented public API (and its TLS certificate did not match the host
when checked); islomapi.uz was returning 502; scraping third-party timetable sites is not done.
