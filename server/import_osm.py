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
import osm_photo  # noqa: E402
import translit  # noqa: E402

QUERY = ('[out:json][timeout:180];area["ISO3166-1"="UZ"][admin_level=2]->.a;'
         'nwr["amenity"="place_of_worship"]["religion"="muslim"](area.a);out center tags;')
UA = "masjid-uz-import/0.1 (%s)" % os.environ.get("OSM_CONTACT", "no contact set")


def osm_address(tags):
    """Best human-readable address from OSM tags: addr:full when present, else street/house/place/city/district/region."""
    full = (tags.get("addr:full") or "").strip()
    if full:
        try:
            return app.clean_text(full[:300], "address", 300)     # never let one odd tag abort a whole sync
        except app.ApiError:
            return None
    street = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x)
    parts = [street, tags.get("addr:place") or tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
             tags.get("addr:city"), tags.get("addr:district"), tags.get("addr:region")]
    parts = [p.strip() for p in parts if p and p.strip()]
    if not parts and tags.get("addr:postcode"):
        return None                                   # a bare postcode is not an address
    if tags.get("addr:postcode") and parts:
        parts.append(tags["addr:postcode"])
    try:
        return app.clean_text(", ".join(parts)[:300], "address", 300)
    except app.ApiError:
        return None


def fetch(endpoint):
    req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def sync_photos(data, store, lookup=osm_photo.commons_lookup):
    """Photos come from the tags and are never edited by people, so they follow OSM even for rows a human has edited.
    A mosque is looked up once; a failed lookup is retried at the next sync."""
    wanted = {"%s/%s" % (el["type"], el["id"]): el["tags"] for el in data.get("elements", []) if osm_photo.commons_title(el.get("tags") or {})}
    done = 0
    for ref, tags in wanted.items():
        with store.pool.connection() as c:
            row = c.execute("SELECT id, photo_url FROM mosques WHERE osm_ref=%s AND status<>'archived'", (ref,)).fetchone()
            if not row or row["photo_url"]:
                continue
            try:
                found = osm_photo.photo_for(tags, lookup)
            except Exception as e:                      # Commons unreachable / odd answer: the sync itself must not fail
                sys.stderr.write("photo lookup failed for %s: %s\n" % (ref, e))
                continue
            if found:
                c.execute("UPDATE mosques SET photo_url=%s, photo_credit=%s WHERE id=%s", (found[0], found[1], row["id"]))
                done += 1
    return done


def run(data, cfg, store=None, photo_lookup=osm_photo.commons_lookup):
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
        addr = osm_address(tags)
        with store.pool.connection() as c:
            cur = c.cursor()
            cur.execute("SELECT * FROM mosques WHERE osm_ref=%s FOR UPDATE", (ref,))
            row = cur.fetchone()
            if row:
                cur.execute("SELECT 1 FROM mosque_history WHERE mosque_id=%s AND actor IS DISTINCT FROM 'import:osm' LIMIT 1", (row["id"],))
                if cur.fetchone() or row["status"] == "archived":      # a human edited / moderated it: leave it alone
                    protected += 1
                    continue
                # an address (also one filled in by enrich_address.py) is never erased just because OSM has no address tags
                changes = {k: [row[k], v] for k, v in (("lat", lat), ("lon", lon), ("name", name), ("address", addr))
                           if row[k] != v and not (k == "address" and v is None)}
                if not changes:
                    unchanged += 1
                    continue
                version = row["version"] + 1
                cur.execute("UPDATE mosques SET lat=%s, lon=%s, name=%s, address=%s, name_search=%s, version=%s, updated_at=now() WHERE id=%s",
                            (lat, lon, name, addr, translit.search_key(name), version, row["id"]))
                store._history(cur, str(row["id"]), version, "osm_sync", "import:osm", changes, "Refreshed from OpenStreetMap " + ref)
                updated += 1
                continue
            if store._nearby(cur, lat, lon, 25):      # another record already covers this spot
                near += 1
                continue
            cur.execute("INSERT INTO mosques(lat,lon,name,address,source,origin,status,creator_id,osm_ref,name_search) "
                        "VALUES (%s,%s,%s,%s,'openstreetmap','imported','unverified','import:osm',%s,%s) RETURNING id",
                        (lat, lon, name, addr, ref, translit.search_key(name)))
            m_id = str(cur.fetchone()["id"])
            store._history(cur, m_id, 1, "create", "import:osm",
                           {"lat": [None, lat], "lon": [None, lon], "name": [None, name], "address": [None, addr]},
                           "Imported from OpenStreetMap (ODbL) " + ref)
            added += 1
    gone = 0
    if seen:                                          # only meaningful for a complete download
        with store.pool.connection() as c:
            gone = sum(1 for r in c.execute("SELECT osm_ref FROM mosques WHERE osm_ref IS NOT NULL AND status<>'archived'") if r["osm_ref"] not in seen)
    photos = sync_photos(data, store, photo_lookup)
    summary = {"added": added, "updated": updated, "unchanged": unchanged, "protected_edited": protected,
               "near_existing": near, "missing_in_osm": gone, "photos": photos}
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
