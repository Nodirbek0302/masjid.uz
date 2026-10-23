# Importing mosques from OpenStreetMap

**Automatic (Docker):** the `osm` compose profile runs `server/osm_sync.py`: one download at start (this also fills an empty
database) and then every `OSM_SYNC_HOURS` (default 24). New OSM mosques are added; imported rows nobody has edited follow
OSM (name/coordinates/address, logged in history as `osm_sync`); anything a person edited or a moderator archived is never
touched; objects that vanished from OSM are only counted in the log. Failed downloads retry after 1 h, never in a loop.
Why not query Overpass live from the app: it is a shared free service with fair-use limits and no guarantee (it has
returned 502/504 and takes seconds to minutes), and our records need their own ids for prayer times, edits and history.

**Manual (below):** the same importer by hand.


Data: `amenity=place_of_worship` + `religion=muslim` inside Uzbekistan. Licence: ODbL 1.0, © OpenStreetMap contributors.
Run it once (and again only occasionally): Overpass is a shared free service.

```bash
cd /Users/nodirbek/ALLPROJECTS/masjid-uz
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # skip if .venv exists

# 1. Your database (use your own credentials)
createdb -h 127.0.0.1 -U <user> masjid
export DATABASE_URL='postgresql://<user>:<password>@127.0.0.1:5432/masjid'

# 2. Download once to a file (so a retry does not hit Overpass again)
curl -sS -m 240 -A 'masjid-uz-import/0.1 (<your email>)' \
  --data-urlencode 'data=[out:json][timeout:180];area["ISO3166-1"="UZ"][admin_level=2]->.a;nwr["amenity"="place_of_worship"]["religion"="muslim"](area.a);out center tags;' \
  https://overpass-api.de/api/interpreter -o osm-mosques.json
python3 -c "import json;print(len(json.load(open('osm-mosques.json'))['elements']), 'elements')"

# 3. Import (idempotent; applies DB migrations first; safe to re-run)
.venv/bin/python server/import_osm.py --file osm-mosques.json

# 4. Check
psql "$DATABASE_URL" -c "select origin, source, status, count(*) from mosques group by 1,2,3"

# 5. Run the app without the interim tile layer
SHOW_TILE_MOSQUES=0 ADMIN_TOKEN=<token> .venv/bin/python server/app.py
```

If `overpass-api.de` is busy, retry later or use another public Overpass mirror in step 2.
Records within 25 m of an existing community record are skipped. Re-running adds only new OSM objects.
Imported rows show "OpenStreetMap ma'lumoti · tasdiqlanmagan"; the OSM-derived subset is published at
`/api/export/osm-derived.geojson` (ODbL share-alike requirement for public use).
