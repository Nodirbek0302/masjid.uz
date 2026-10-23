"""One-off / periodic import of mosques from OpenStreetMap (ODbL) into the community database.

Licence: data (c) OpenStreetMap contributors, ODbL 1.0. Imported rows are marked origin='imported',
source='openstreetmap', carry osm_ref, and are re-published via /api/export/osm-derived.geojson
(ODbL share-alike). Run manually, respectfully (Overpass fair-use); do not schedule frequent runs.

Usage:
  python server/import_osm.py --fetch [--endpoint URL]   # query Overpass for Uzbekistan once
  python server/import_osm.py --file overpass.json       # or import a saved Overpass JSON
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

QUERY = ('[out:json][timeout:180];area["ISO3166-1"="UZ"][admin_level=2]->.a;'
         'nwr["amenity"="place_of_worship"]["religion"="muslim"](area.a);out center tags;')
UA = "masjid-uz-import/0.1"


def fetch(endpoint):
    req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def run(data, cfg):
    store = app.Store(cfg)
    added = skipped = near = 0
    for el in data.get("elements", []):
        pt = (el.get("lat"), el.get("lon")) if el["type"] == "node" else (
            (el.get("center") or {}).get("lat"), (el.get("center") or {}).get("lon"))
        if pt[0] is None:
            continue
        ref = "%s/%s" % (el["type"], el["id"])
        tags = el.get("tags", {})
        lat, lon = app.clean_coord(*pt)
        name = (tags.get("name") or tags.get("name:uz") or tags.get("name:ru") or "")[:200] or None
        addr = ", ".join(tags[k] for k in ("addr:street", "addr:housenumber", "addr:city") if tags.get(k))[:300] or None
        with store.pool.connection() as c:
            cur = c.cursor()
            cur.execute("SELECT 1 FROM mosques WHERE osm_ref=%s", (ref,))
            if cur.fetchone():
                skipped += 1
                continue
            if store._nearby(cur, lat, lon, 25):      # a community record already covers this spot
                near += 1
                continue
            cur.execute("INSERT INTO mosques(lat,lon,name,address,source,origin,status,creator_id,osm_ref) "
                        "VALUES (%s,%s,%s,%s,'openstreetmap','imported','unverified','import:osm',%s) RETURNING id",
                        (lat, lon, name, addr, ref))
            m_id = str(cur.fetchone()["id"])
            store._history(cur, m_id, 1, "create", "import:osm",
                           {"lat": [None, lat], "lon": [None, lon], "name": [None, name], "address": [None, addr]},
                           "Imported from OpenStreetMap (ODbL) " + ref)
            added += 1
    print("added=%d already_imported=%d near_community_record=%d" % (added, skipped, near))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--endpoint", default="https://overpass-api.de/api/interpreter")
    a = ap.parse_args()
    if not (a.fetch or a.file):
        ap.error("--fetch or --file required")
    run(fetch(a.endpoint) if a.fetch else json.load(open(a.file)), app.Config())
