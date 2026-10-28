# Data import (`import --file`)

`python server/cli.py import --file mosques.json` — one-off, idempotent: records are upserted by their `id` (uuid) when given, otherwise by `osmRef`.
Every change is written to the history (action `import`, actor `import:file`). The command prints one summary line.

```json
{"mosques": [
  {"id": "6f1c0f0e-5b3a-4c52-9a52-0d1f2f1b9a11", "lat": 41.3111, "lon": 69.2797, "name": "Example masjid", "address": "Tashkent",
   "prayerTimes": {"fajr": "05:10", "dhuhr": "12:30", "asr": "16:00", "maghrib": "18:40", "isha": "20:00", "jumuah": "13:00"},
   "source": "community-app", "origin": "community", "status": "unverified"},
  {"osmRef": "node/123456", "lat": 39.65, "lon": 66.97, "name": "OSM masjid", "source": "openstreetmap", "origin": "imported"}
]}
```
Allowed fields: `id`, `osmRef`, `lat`, `lon`, `name`, `address`, `prayerTimes`, `source`, `origin` (`community|imported`), `status`, `verificationNote`
(required for `verified`), `photoUrl`, `photoCredit`. Unknown fields skip the record (counted as `skipped`). Never put personal data into the file.

# OpenStreetMap import (`import-osm`)

Run nightly by TIMUS as a one-off container: queries Overpass for Uzbekistan (`User-Agent` names info@timus.uz), inserts new mosques, refreshes the
ones nobody edited, never touches edited or archived ones, prints one summary line. `--file F` uses a saved Overpass JSON answer instead of the network.
