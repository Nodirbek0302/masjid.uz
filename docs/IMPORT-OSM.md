# Importing mosques from OpenStreetMap (one-off, run on your own machine/server)

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
