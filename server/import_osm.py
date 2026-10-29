"""Import of mosques from OpenStreetMap (ODbL) into the community database: container command `import-osm`, run once a night by
TIMUS as a one-off container (never inside `serve`). Idempotent; prints one summary line at the end.

Licence: data (c) OpenStreetMap contributors, ODbL 1.0. Imported rows are marked origin='imported', source='openstreetmap',
carry osm_ref, and are re-published via /masjiduz/v1/export/osm-derived (ODbL share-alike).
The only outbound call is the Overpass query (User-Agent names info@timus.uz); photos are worked out from the tags, no request is made.

  python server/cli.py import-osm              # query Overpass for Uzbekistan
  python server/cli.py import-osm --file F     # or use a saved Overpass JSON answer
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import app
import osm_photo
import psycopg
import translit

QUERY = ('[out:json][timeout:180];area["ISO3166-1"="UZ"][admin_level=2]->.a;'
         'nwr["amenity"="place_of_worship"]["religion"="muslim"](area.a);out center tags;')
UA = "masjid-uz-import/1.0 (info@timus.uz)"
log = logging.getLogger("import-osm")


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


RETRY_WAITS_S = (30, 60, 120, 240)          # Overpass often answers 429/502/503/504 when it is busy; it usually works a little later


def fetch(endpoint, sleep=time.sleep):
    """The Overpass query (the only outbound call of this command); a busy or unreachable server is asked again a few times
    (about 7.5 minutes in all) before the command gives up, so one bad moment does not leave the night without a sync."""
    for wait in RETRY_WAITS_S + (None,):
        req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                     headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.load(r)
            if "elements" not in data or "error" in str(data.get("remark", "")).lower():
                raise ValueError("not a complete Overpass answer")     # e.g. "runtime error: Query timed out": a partial list
            return data
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or wait is None:
                raise
            why = "HTTP %d" % e.code
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            if wait is None:
                raise
            why = type(e).__name__
        log.warning("overpass: %s, trying again in %d s", why, wait)
        sleep(wait)


def sync_photos(data, store):
    """Photos come from the tags and are never edited by people, so they follow OSM even for rows a human has edited.
    A mosque is looked up once; a failed lookup is retried at the next sync."""
    wanted = {"%s/%s" % (el["type"], el["id"]): el["tags"] for el in data.get("elements", []) if osm_photo.commons_title(el.get("tags") or {})}
    done = 0
    for ref, tags in wanted.items():
        with store.pool.connection() as c:
            row = c.execute("SELECT id, photo_url FROM mosques WHERE osm_ref=%s AND status<>'archived'", (ref,)).fetchone()
            if not row or row["photo_url"]:
                continue
            found = osm_photo.photo_for(tags)
            if found:
                c.execute("UPDATE mosques SET photo_url=%s, photo_credit=%s WHERE id=%s", (found[0], found[1], row["id"]))
                done += 1
    return done


LOCK_ID = 727006                          # one import at a time: the nightly container and the moderator's bot button never overlap


def download(cfg):
    """Overpass for Uzbekistan, checked: an empty or partial answer must never look like a real sync."""
    data = fetch(cfg.overpass_url)
    if len(data.get("elements", [])) < 100:
        raise RuntimeError("suspiciously small Overpass answer (%d elements)" % len(data.get("elements", [])))
    return data


def run(data, cfg, store=None):
    """The sync under a database lock; while another import holds it nothing is done -> {"skipped": True}."""
    store = store or app.Store(cfg, name="masjiduz-import", statement_timeout_ms=60000)
    lock = psycopg.connect(cfg.conninfo(), autocommit=True)
    try:
        if not lock.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            line = "import-osm skipped: another import is running"
            log.info(line)
            print(line, flush=True)
            return {"skipped": True}
        return _run(data, cfg, store)
    finally:
        lock.close()                                  # closing the session releases the lock


def _run(data, cfg, store):
    """Idempotent sync. New OSM objects are inserted. Existing imported rows are refreshed from OSM only while no
    human has touched them (every history row is by 'import:osm'); community edits always win. Objects that vanished
    from OSM are only counted, never archived automatically."""
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
        try:
            lat, lon = app.clean_coord(*pt)
            name = app.clean_text((tags.get("name") or tags.get("name:uz") or tags.get("name:ru") or "")[:200], "name", 200)
        except app.ApiError:                              # one odd OSM object never aborts the whole sync
            continue
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
                cur.execute("UPDATE mosques SET lat=%s, lon=%s, name=%s, address=COALESCE(%s, address), name_search=%s, version=%s, updated_at=now() WHERE id=%s",
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
    photos = sync_photos(data, store)
    summary = {"added": added, "updated": updated, "unchanged": unchanged, "protected_edited": protected,
               "near_existing": near, "missing_in_osm": gone, "photos": photos}
    line = "import-osm done: " + " ".join("%s=%d" % kv for kv in summary.items())
    log.info(line)
    print(line, flush=True)
    return summary
