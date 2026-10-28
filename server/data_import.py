"""`import --file F.json`: one-off, idempotent import of existing mosques (format: docs/IMPORT.md).

Upserts by a stable key: the record's `id` (uuid) when given, else its `osmRef`. Existing rows are updated only in the fields the file
carries; every change is written to the history (action `import`, actor `import:file`). Prints one summary line.
"""
import json
import logging
import uuid

import app
import translit
import psycopg
from psycopg.types.json import Jsonb

log = logging.getLogger("import")
FIELDS = ("lat", "lon", "name", "address", "prayer_times", "source", "origin", "status", "verification_note", "photo_url", "photo_credit")


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("mosques") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError('the file must be {"mosques": [...]}')
    return items


def clean(raw):
    """-> (id | None, osmRef | None, the fields the record carries (snake_case), nothing else). Defaults are filled only when a row is inserted."""
    allowed = {"id", "osmRef", "lat", "lon", "name", "address", "prayerTimes", "source", "origin", "status", "verificationNote", "photoUrl", "photoCredit"}
    extra = set(raw) - allowed
    if extra:
        raise ValueError("unknown field: " + sorted(extra)[0])
    rec = {}
    if "lat" in raw or "lon" in raw:
        rec["lat"], rec["lon"] = app.clean_coord(app.json_number(raw.get("lat"), "lat"), app.json_number(raw.get("lon"), "lon"))
    if "name" in raw:
        rec["name"] = app.clean_text(raw["name"], "name", 200)
    if "address" in raw:
        rec["address"] = app.clean_text(raw["address"], "address", 300)
    if "prayerTimes" in raw:
        rec["prayer_times"] = app.clean_times(raw["prayerTimes"])
    for key, col in (("source", "source"), ("origin", "origin"), ("status", "status"), ("photoUrl", "photo_url"), ("photoCredit", "photo_credit")):
        if key in raw:
            rec[col] = raw[key]
    if "verificationNote" in raw:
        rec["verification_note"] = app.clean_text(raw["verificationNote"], "verificationNote", 500)
    if rec.get("origin", "imported") not in ("community", "imported") or rec.get("status", "unverified") not in ("unverified", "reported", "verified", "archived"):
        raise ValueError("bad origin or status")
    for k in ("photo_url", "photo_credit"):
        if rec.get(k) is not None and not str(rec[k]).startswith("https://"):
            raise ValueError(k + " must be an https address")
    key_id = str(uuid.UUID(raw["id"])) if raw.get("id") else None
    return key_id, raw.get("osmRef"), rec


def run(items, cfg, store=None):
    store = store or app.Store(cfg, name="masjiduz-import", statement_timeout_ms=60000)
    added = updated = unchanged = bad = 0
    for raw in items:
        try:
            key_id, osm_ref, rec = clean(raw)
            if not key_id and not osm_ref:
                raise ValueError("each record needs an id or an osmRef")
            outcome = upsert(store, key_id, osm_ref, rec)
        except (ValueError, app.ApiError, AttributeError, TypeError, psycopg.Error) as e:
            bad += 1
            log.warning("skipped a record: %s", type(e).__name__ if isinstance(e, psycopg.Error) else e)
            continue
        added, updated, unchanged = added + (outcome == "added"), updated + (outcome == "updated"), unchanged + (outcome == "unchanged")
    line = "import done: added=%d updated=%d unchanged=%d skipped=%d" % (added, updated, unchanged, bad)
    log.info(line)
    print(line, flush=True)
    return {"added": added, "updated": updated, "unchanged": unchanged, "skipped": bad}


def upsert(store, key_id, osm_ref, rec):
    with store.pool.connection() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM mosques WHERE " + ("id=%s" if key_id else "osm_ref=%s") + " FOR UPDATE", (key_id or osm_ref,))
        row = cur.fetchone()
        if row:
            if row["status"] == "archived" and rec.get("status", "archived") != "archived":
                return "unchanged"                                  # a moderator archived it: the import does not bring it back
            changes = {k: [row[k], v] for k, v in rec.items() if row[k] != v}
            if not changes:
                return "unchanged"
            version = row["version"] + 1
            sets = ", ".join(k + "=%s" for k in changes)
            vals = [Jsonb(v[1]) if k == "prayer_times" else v[1] for k, v in changes.items()]
            if "name" in changes:
                sets += ", name_search=%s"
                vals.append(translit.search_key(changes["name"][1]))
            if "prayer_times" in changes:
                sets += ", times_updated_at=" + ("now()" if changes["prayer_times"][1] else "NULL")
            cur.execute("UPDATE mosques SET " + sets + ", version=%s, updated_at=now() WHERE id=%s", vals + [version, row["id"]])
            store._history(cur, str(row["id"]), version, "import", "import:file", changes, "data import")
            return "updated"
        if "lat" not in rec or "lon" not in rec:
            raise ValueError("a new record needs lat and lon")
        full = dict({"source": "import", "origin": "imported", "status": "unverified", "prayer_times": {}, "name": None, "address": None,
                     "verification_note": None, "photo_url": None, "photo_credit": None}, **rec)
        if full["status"] == "verified" and not full["verification_note"]:
            raise ValueError("verified needs a verificationNote")
        cur.execute("INSERT INTO mosques(id,lat,lon,name,address,prayer_times,source,origin,status,verification_note,photo_url,photo_credit,"
                    "osm_ref,name_search,creator_id,times_updated_at) VALUES (COALESCE(%s::uuid, gen_random_uuid()),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "'import:file',CASE WHEN %s THEN now() END) RETURNING id",
                    (key_id, full["lat"], full["lon"], full["name"], full["address"], Jsonb(full["prayer_times"]), full["source"], full["origin"],
                     full["status"], full["verification_note"], full["photo_url"], full["photo_credit"], osm_ref, translit.search_key(full["name"]),
                     bool(full["prayer_times"])))
        store._history(cur, str(cur.fetchone()["id"]), 1, "import", "import:file", {k: [None, v] for k, v in full.items()}, "data import")
        return "added"
