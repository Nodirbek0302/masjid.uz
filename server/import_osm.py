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
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

QUERY = ('[out:json][timeout:180];area["ISO3166-1"="UZ"][admin_level=2]->.a;'
         'nwr["amenity"="place_of_worship"]["religion"="muslim"](area.a);out center tags;')
UA = "masjid-uz-import/0.1 (%s)" % os.environ.get("OSM_CONTACT", "no contact set")


def fetch(endpoint):
    req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def run(data, cfg, store=None):
    """Idempotent sync. New OSM objects are inserted. Existing imported rows are refreshed from OSM only while no
    human has touched them (every history row is by 'import:osm'); community edits always win. Objects that vanished
    from OSM are only counted, never archived automatically."""
    store = store or app.Store(cfg)
    added = updated = protected = near = unchanged = 0
    seen = set()
    for el in data.get("elements", []):
        pt = (el.get("lat"), el.get("lon")) if el["type"] == "node" else (
            (el.get("center") or {}).get("lat"), (el.get("center") or {}).get("lon"))
        if pt[0] is None:
            continue
        ref = "%s/%s" % (el["type"], el["id"])
        seen.add(ref)
        tags = el.get("tags", {})
        lat, lon = app.clean_coord(*pt)
        name = (tags.get("name") or tags.get("name:uz") or tags.get("name:ru") or "")[:200] or None
        addr = ", ".join(tags[k] for k in ("addr:street", "addr:housenumber", "addr:city") if tags.get(k))[:300] or None
        with store.pool.connection() as c:
            cur = c.cursor()
            cur.execute("SELECT * FROM mosques WHERE osm_ref=%s FOR UPDATE", (ref,))
            row = cur.fetchone()
            if row:
                cur.execute("SELECT 1 FROM mosque_history WHERE mosque_id=%s AND actor IS DISTINCT FROM 'import:osm' LIMIT 1", (row["id"],))
                if cur.fetchone() or row["status"] == "archived":      # a human edited / moderated it: leave it alone
                    protected += 1
                    continue
                changes = {k: [row[k], v] for k, v in (("lat", lat), ("lon", lon), ("name", name), ("address", addr)) if row[k] != v}
                if not changes:
                    unchanged += 1
                    continue
                version = row["version"] + 1
                cur.execute("UPDATE mosques SET lat=%s, lon=%s, name=%s, address=%s, version=%s, updated_at=now() WHERE id=%s",
                            (lat, lon, name, addr, version, row["id"]))
                store._history(cur, str(row["id"]), version, "osm_sync", "import:osm", changes, "Refreshed from OpenStreetMap " + ref)
                updated += 1
                continue
            if store._nearby(cur, lat, lon, 25):      # another record already covers this spot
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
    gone = 0
    if seen:                                          # only meaningful for a complete download
        with store.pool.connection() as c:
            gone = sum(1 for r in c.execute("SELECT osm_ref FROM mosques WHERE osm_ref IS NOT NULL AND status<>'archived'") if r["osm_ref"] not in seen)
    summary = {"added": added, "updated": updated, "unchanged": unchanged, "protected_edited": protected,
               "near_existing": near, "missing_in_osm": gone}
    print(" ".join("%s=%d" % kv for kv in summary.items()))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--endpoint", default=os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter"))
    a = ap.parse_args()
    if not (a.fetch or a.file):
        ap.error("--fetch or --file required")
    run(fetch(a.endpoint) if a.fetch else json.load(open(a.file)), app.Config())
