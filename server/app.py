"""masjid-uz backend: community mosque records with audit history.

Standard library only (Python 3.9+) + SQLite. Run behind a TLS reverse proxy (see deploy/Caddyfile).
"""
import gzip
import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

import translit
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
REPORT_KINDS = ("incorrect", "duplicate", "relocated", "closed", "other")
EDITABLE = ("lat", "lon", "name", "address", "prayer_times")


def env(name, default=None):
    return os.environ.get(name, default)


def load_dotenv(path=ROOT / ".env"):
    """Minimal .env loader (KEY=VALUE). Real environment variables win. File must stay private (chmod 600)."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


class Config:
    def __init__(self):
        load_dotenv()
        self.database_url = env("DATABASE_URL", "postgresql://masjid@127.0.0.1:5432/masjid")
        self.admin_token = env("ADMIN_TOKEN", "")
        self.bot_token = env("TELEGRAM_BOT_TOKEN", "")
        self.dup_radius_m = float(env("DUPLICATE_RADIUS_M", "50"))
        self.report_threshold = int(env("REPORT_THRESHOLD", "3"))
        self.show_tile_mosques = env("SHOW_TILE_MOSQUES", "1") == "1"
        self.prayer = {"fajr_angle": float(env("PRAYER_FAJR_ANGLE", "15")), "isha_angle": float(env("PRAYER_ISHA_ANGLE", "15")),
                       "asr_factor": int(env("PRAYER_ASR_FACTOR", "2")),
                       "dhuhr_offset_min": int(env("PRAYER_DHUHR_OFFSET_MIN", "5"))}     # Asr: 1 = Shafi'i, 2 = Hanafi
        self.actor_salt = env("ACTOR_SALT", "") or env("ADMIN_TOKEN", "") or "masjid-uz"
        self.admin_local_only = env("ADMIN_LOCAL_ONLY", "0") == "1"
        self.trust_proxy = env("TRUST_PROXY", "0") == "1"
        self.rate_limit = int(env("WRITE_RATE_PER_MIN", "30"))
        self.tile_style_url = env("TILE_STYLE_URL", "https://tiles.openfreemap.org/styles/liberty")
        self.host = env("HOST", "127.0.0.1")
        self.port = int(env("PORT", "8080"))


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        self.status, self.code, self.message, self.extra = status, code, message, extra


# ---------------------------------------------------------------- validation
# Control characters and invisible/bidi-override characters are never legitimate in names, addresses or reasons.
_BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")


def clean_text(value, field, limit, multiline=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "invalid_" + field, field + " must be text")
    try:
        value.encode("utf-8")                    # lone surrogates (broken emoji, "\ud83d") would crash the DB driver
    except UnicodeEncodeError:
        raise ApiError(400, "invalid_text", field + " contains invalid characters")
    value = unicodedata.normalize("NFC", value)  # one canonical form for Cyrillic/Latin letters with diacritics
    if not multiline:
        value = re.sub(r"[\r\n\t]+", " ", value)
    value = _BAD_CHARS.sub("", value).strip()
    if len(value) > limit:
        raise ApiError(400, "invalid_" + field, field + " too long")
    return value or None


def clean_coord(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_coordinates", "lat/lon must be numbers")
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        raise ApiError(400, "invalid_coordinates", "lat/lon out of range")
    return round(lat, 6), round(lon, 6)


def clean_times(times):
    if times is None:
        return {}
    if not isinstance(times, dict):
        raise ApiError(400, "invalid_prayer_times", "prayer_times must be an object")
    out = {}
    for key, val in times.items():
        if key not in PRAYERS:
            raise ApiError(400, "invalid_prayer_times", "unknown prayer: " + str(key))
        if val in (None, ""):
            continue  # missing = unknown
        if not isinstance(val, str) or not TIME_RE.match(val):
            raise ApiError(400, "invalid_prayer_times", key + " must be HH:MM (24h)")
        out[key] = val
    return out


# ---------------------------------------------------------------- store
MIGRATIONS = Path(__file__).with_name("migrations")


def iso(v):
    return v.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(v, datetime) else str(v)


def migrate(conninfo):
    with psycopg.connect(conninfo, autocommit=True) as c:
        c.execute("SELECT pg_advisory_lock(727001)")
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            with c.transaction():
                c.execute(f.read_text())
                c.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (f.name,))
            sys.stderr.write("applied migration %s\n" % f.name)
        c.execute("SELECT pg_advisory_unlock(727001)")


class Store:
    def __init__(self, cfg):
        self.cfg = cfg
        migrate(cfg.database_url)
        self.pool = ConnectionPool(cfg.database_url, min_size=1, max_size=10, open=True,
                                   kwargs={"row_factory": dict_row})
        self._backfill_search()

    def _backfill_search(self):
        with self.pool.connection() as c:
            for r in c.execute("SELECT id, name FROM mosques WHERE name IS NOT NULL AND name_search IS NULL").fetchall():
                c.execute("UPDATE mosques SET name_search=%s WHERE id=%s", (translit.search_key(r["name"]), r["id"]))

    @staticmethod
    def row(r, private=False):
        d = dict(r)
        d["id"] = str(d["id"])
        if not private:
            d.pop("creator_id", None)        # never expose Telegram/device ids in public responses
        for k in ("created_at", "updated_at"):
            d[k] = iso(d[k])
        return d

    @staticmethod
    def _history(cur, m_id, version, action, actor, changes, note=None, client=None):
        cur.execute("INSERT INTO mosque_history(mosque_id,version,action,actor,changes,note,client) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (m_id, version, action, actor, Jsonb(changes), note, Jsonb(snapshot(client)) if client else None))

    @staticmethod
    def touch_actor(cur, actor, info, edits=0, reports=0):
        """Create/refresh the actor record. Newer non-empty values replace older ones."""
        if not actor or not info:
            return
        tg = info.get("telegram") or {}
        cur.execute(
            "INSERT INTO actors(actor_id,kind,telegram_id,tg_username,tg_first_name,tg_last_name,tg_language,device_id,platform,"
            "os_version,app_version,device_model,user_agent,ip_hash,edits_count,reports_count) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (actor_id) DO UPDATE SET last_seen=now(), "
            "telegram_id=COALESCE(EXCLUDED.telegram_id, actors.telegram_id), tg_username=COALESCE(EXCLUDED.tg_username, actors.tg_username), "
            "tg_first_name=COALESCE(EXCLUDED.tg_first_name, actors.tg_first_name), tg_last_name=COALESCE(EXCLUDED.tg_last_name, actors.tg_last_name), "
            "tg_language=COALESCE(EXCLUDED.tg_language, actors.tg_language), device_id=COALESCE(EXCLUDED.device_id, actors.device_id), "
            "platform=COALESCE(EXCLUDED.platform, actors.platform), os_version=COALESCE(EXCLUDED.os_version, actors.os_version), "
            "app_version=COALESCE(EXCLUDED.app_version, actors.app_version), device_model=COALESCE(EXCLUDED.device_model, actors.device_model), "
            "user_agent=COALESCE(EXCLUDED.user_agent, actors.user_agent), ip_hash=COALESCE(EXCLUDED.ip_hash, actors.ip_hash), "
            "edits_count=actors.edits_count + EXCLUDED.edits_count, reports_count=actors.reports_count + EXCLUDED.reports_count",
            (actor, info["kind"], tg.get("id"), tg.get("username"), tg.get("first_name"), tg.get("last_name"), tg.get("language_code"),
             info.get("device_id"), info.get("platform"), info.get("os_version"), info.get("app_version"), info.get("device_model"),
             info.get("user_agent"), info.get("ip_hash"), edits, reports))

    def _fetch(self, cur, m_id, lock=False):
        try:
            uuid.UUID(m_id)
        except ValueError:
            raise ApiError(404, "not_found", "mosque not found")
        cur.execute("SELECT * FROM mosques WHERE id=%s" + (" FOR UPDATE" if lock else ""), (m_id,))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "mosque not found")
        return self.row(r)

    def get(self, m_id):
        with self.pool.connection() as c:
            return self._fetch(c.cursor(), m_id)

    def list(self, bbox):
        sql, args = "SELECT * FROM mosques WHERE status<>'archived'", []
        if bbox:
            w, s, e, n = bbox
            sql += " AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s"
            args += [s, n, w, e]
        with self.pool.connection() as c:
            return [self.row(r) for r in c.execute(sql + " LIMIT 5000", args)]

    def _nearby(self, cur, lat, lon, radius):
        dlat = radius / 111320.0
        dlon = radius / (111320.0 * max(math.cos(math.radians(lat)), 0.01))
        cur.execute("SELECT * FROM mosques WHERE status<>'archived' AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
                    (lat - dlat, lat + dlat, lon - dlon, lon + dlon))
        out = []
        for r in cur.fetchall():
            d = haversine_m(lat, lon, r["lat"], r["lon"])
            if d <= radius:
                item = self.row(r)
                item["distance_m"] = round(d, 1)
                out.append(item)
        return sorted(out, key=lambda x: x["distance_m"])

    def nearby(self, lat, lon, radius):
        with self.pool.connection() as c:
            return self._nearby(c.cursor(), lat, lon, radius)

    def create(self, body, actor, client=None):
        lat, lon = clean_coord(body.get("lat"), body.get("lon"))
        name = clean_text(body.get("name"), "name", 200)
        address = clean_text(body.get("address"), "address", 300)
        times = clean_times(body.get("prayer_times"))
        with self.pool.connection() as c:
            cur = c.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(727002)")  # serialise duplicate check + insert
            dups = self._nearby(cur, lat, lon, self.cfg.dup_radius_m)
            if dups and not body.get("force"):
                raise ApiError(409, "possible_duplicate", "A mosque already exists nearby", duplicates=dups)
            cur.execute("INSERT INTO mosques(lat,lon,name,address,prayer_times,source,origin,status,creator_id,name_search) "
                        "VALUES (%s,%s,%s,%s,%s,'community-app','community','unverified',%s,%s) RETURNING *",
                        (lat, lon, name, address, Jsonb(times), actor, translit.search_key(name)))
            m = self.row(cur.fetchone())
            self._history(cur, m["id"], 1, "create", actor, {
                "lat": [None, lat], "lon": [None, lon], "name": [None, name],
                "address": [None, address], "prayer_times": [None, times]}, client=client)
            self.touch_actor(cur, actor, client, edits=1)
            return m

    def edit(self, m_id, body, actor, client=None):
        if not isinstance(body.get("expected_version"), int):
            raise ApiError(400, "version_required", "expected_version (integer) is required")
        with self.pool.connection() as c:
            cur = c.cursor()
            old = self._fetch(cur, m_id, lock=True)
            if old["status"] == "archived":
                raise ApiError(409, "archived", "record is archived")
            if body["expected_version"] != old["version"]:
                raise ApiError(409, "version_conflict", "record changed; reload and retry", current=old)
            new = {}
            if "lat" in body or "lon" in body:
                new["lat"], new["lon"] = clean_coord(body.get("lat", old["lat"]), body.get("lon", old["lon"]))
            if "name" in body:
                new["name"] = clean_text(body["name"], "name", 200)
            if "address" in body:
                new["address"] = clean_text(body["address"], "address", 300)
            if "prayer_times" in body:
                if not isinstance(body["prayer_times"], dict):
                    raise ApiError(400, "invalid_prayer_times", "prayer_times must be an object")
                merged = dict(old["prayer_times"])
                for k, v in body["prayer_times"].items():
                    if v in (None, ""):
                        merged.pop(k, None)   # explicit clear
                    else:
                        merged[k] = v
                new["prayer_times"] = clean_times(merged)
            changes = {k: [old[k], v] for k, v in new.items() if old[k] != v}
            if not changes:
                return old
            version = old["version"] + 1
            sets = ", ".join(k + "=%s" for k in changes)
            vals = [Jsonb(v[1]) if k == "prayer_times" else v[1] for k, v in changes.items()]
            if "name" in changes:
                sets += ", name_search=%s"
                vals.append(translit.search_key(changes["name"][1]))
            cur.execute("UPDATE mosques SET " + sets + ", version=%s, updated_at=now() WHERE id=%s RETURNING *",
                        vals + [version, m_id])
            out = self.row(cur.fetchone())
            self._history(cur, m_id, version, "edit", actor, changes, clean_text(body.get("note"), "note", 300), client=client)
            self.touch_actor(cur, actor, client, edits=1)
            return out

    def export_osm(self):
        """ODbL share-alike: publish the OpenStreetMap-derived part of the database."""
        with self.pool.connection() as c:
            rows = c.execute("SELECT osm_ref,lat,lon,name,address FROM mosques WHERE osm_ref IS NOT NULL").fetchall()
        return {"type": "FeatureCollection",
                "license": "ODbL 1.0 - (c) OpenStreetMap contributors - https://www.openstreetmap.org/copyright",
                "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                              "properties": {"osm_ref": r["osm_ref"], "name": r["name"], "address": r["address"]}}
                             for r in rows]}

    def search(self, q, limit=20):
        """Finds names typed in any script/apostrophe form: masjid = масжид, o'zbek = oʻzbek = ўзбек, mechet = мечеть."""
        q = (q or "").strip()[:60]
        if len(q.strip("'ʻʼ‘’`´")) < 2:
            return []
        pats = ["%" + v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for v in translit.search_variants(q)]
        with self.pool.connection() as c:
            return [dict(r, id=str(r["id"])) for r in c.execute(
                "SELECT id,name,lat,lon,status FROM mosques WHERE status<>'archived' AND name_search LIKE ANY(%s) "
                "ORDER BY char_length(name), name LIMIT %s", (pats, limit))]

    def admin_list(self, status, limit=100, offset=0):
        if status not in ("unverified", "reported", "verified", "archived"):
            raise ApiError(400, "invalid_status", "status must be unverified|reported|verified|archived")
        with self.pool.connection() as c:
            rows = c.execute("SELECT * FROM mosques WHERE status=%s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                             (status, min(limit, 200), max(offset, 0))).fetchall()
            total = c.execute("SELECT COUNT(*) AS n FROM mosques WHERE status=%s", (status,)).fetchone()["n"]
        return {"total": total, "items": [self.row(r, private=True) for r in rows]}

    def overview(self):
        with self.pool.connection() as c:
            counts = {"%s/%s" % (r["origin"], r["status"]): r["n"] for r in c.execute(
                "SELECT origin, status, COUNT(*) AS n FROM mosques GROUP BY 1,2")}
            open_reports = c.execute("SELECT COUNT(*) AS n FROM reports WHERE resolution IS NULL").fetchone()["n"]
            recent = [dict(r, mosque_id=str(r["mosque_id"]), at=iso(r["at"])) for r in c.execute(
                "SELECT h.seq,h.mosque_id,h.version,h.action,h.actor,h.at,h.note,h.client,m.name,"
                "a.kind AS actor_kind,a.tg_username,a.tg_first_name,a.platform,a.app_version,a.device_model "
                "FROM mosque_history h JOIN mosques m ON m.id=h.mosque_id LEFT JOIN actors a ON a.actor_id=h.actor "
                "WHERE h.actor IS DISTINCT FROM 'import:osm' ORDER BY h.seq DESC LIMIT 50")]
        return {"counts": counts, "open_reports": open_reports, "recent": recent}

    def actors(self, q=None, limit=50):
        sql = "SELECT * FROM actors"
        args = []
        if q:
            sql += " WHERE actor_id ILIKE %s OR tg_username ILIKE %s OR tg_first_name ILIKE %s OR device_model ILIKE %s"
            args = ["%" + q.replace("%", "").replace("_", "") + "%"] * 4
        with self.pool.connection() as c:
            rows = c.execute(sql + " ORDER BY last_seen DESC LIMIT %s", args + [min(limit, 200)]).fetchall()
        return [dict(r, first_seen=iso(r["first_seen"]), last_seen=iso(r["last_seen"])) for r in rows]

    def history(self, m_id):
        """Public audit trail: no personal identifiers. `actor` is only a role: user | moderator | osm | system."""
        with self.pool.connection() as c:
            cur = c.cursor()
            self._fetch(cur, m_id)
            cur.execute("SELECT * FROM mosque_history WHERE mosque_id=%s ORDER BY seq DESC", (m_id,))
            out = []
            for r in cur.fetchall():
                a = r["actor"]
                role = None if a is None else "moderator" if a == "admin" else "osm" if a == "import:osm" else "user"
                out.append({"seq": r["seq"], "mosque_id": str(r["mosque_id"]), "version": r["version"], "action": r["action"],
                            "actor": role, "at": iso(r["at"]), "changes": r["changes"], "note": r["note"]})
            return out

    def report(self, m_id, body, actor, client=None):
        kind = body.get("kind", "incorrect")
        if kind not in REPORT_KINDS:
            raise ApiError(400, "invalid_kind", "kind must be one of " + ", ".join(REPORT_KINDS))
        reason = clean_text(body.get("reason"), "reason", 1000, multiline=True)
        if not reason:
            raise ApiError(400, "reason_required", "a reason is required")
        with self.pool.connection() as c:
            cur = c.cursor()
            m = self._fetch(cur, m_id, lock=True)
            try:
                with c.transaction():
                    cur.execute("INSERT INTO reports(mosque_id,kind,reason,reporter_id,client) VALUES (%s,%s,%s,%s,%s)",
                                (m_id, kind, reason, actor, Jsonb(snapshot(client)) if client else None))
            except psycopg.errors.UniqueViolation:
                raise ApiError(409, "already_reported", "you already reported this")
            self.touch_actor(cur, actor, client, reports=1)
            cur.execute("SELECT COUNT(DISTINCT COALESCE(reporter_id, id::text)) AS n FROM reports "
                        "WHERE mosque_id=%s AND resolution IS NULL", (m_id,))
            n = cur.fetchone()["n"]
            if n >= self.cfg.report_threshold and m["status"] == "unverified":
                v = m["version"] + 1
                cur.execute("UPDATE mosques SET status='reported', version=%s, updated_at=now() WHERE id=%s", (v, m_id))
                self._history(cur, m_id, v, "report_threshold", None, {"status": ["unverified", "reported"]},
                              "%d open reports" % n)
        return {"ok": True}

    def reports(self, only_open=True):
        sql = ("SELECT r.*, m.name, m.status, m.lat, m.lon FROM reports r JOIN mosques m ON m.id=r.mosque_id"
               + (" WHERE r.resolution IS NULL" if only_open else "") + " ORDER BY r.mosque_id, r.kind, r.id")
        groups = {}
        with self.pool.connection() as c:
            for r in c.execute(sql):
                g = groups.setdefault((r["mosque_id"], r["kind"]), {
                    "mosque_id": str(r["mosque_id"]), "kind": r["kind"], "mosque_name": r["name"],
                    "mosque_status": r["status"], "lat": r["lat"], "lon": r["lon"], "count": 0, "reports": []})
                g["count"] += 1
                g["reports"].append({"id": r["id"], "reason": r["reason"], "reporter_id": r["reporter_id"],
                                     "created_at": iso(r["created_at"]), "resolution": r["resolution"]})
        return sorted(groups.values(), key=lambda g: -g["count"])

    def moderate(self, m_id, action, actor, note):
        if action not in ("archive", "restore", "verify", "dismiss"):
            raise ApiError(400, "invalid_action", "unknown action")
        note = clean_text(note, "note", 500)
        with self.pool.connection() as c:
            cur = c.cursor()
            m = self._fetch(cur, m_id, lock=True)
            st, vnote, resolution = m["status"], m["verification_note"], None
            if action == "archive":
                if st == "archived":
                    raise ApiError(409, "already_archived", "already archived")
                new, resolution = "archived", "accepted"
            elif action == "restore":
                if st != "archived":
                    raise ApiError(409, "not_archived", "record is not archived")
                new, vnote = "unverified", None
            elif action == "verify":
                if not note:
                    raise ApiError(400, "evidence_required", "verification requires an evidence note")
                new, vnote = "verified", note
            else:  # dismiss reports
                new, resolution = ("unverified" if st == "reported" else st), "rejected"
            v = m["version"] + 1
            cur.execute("UPDATE mosques SET status=%s, verification_note=%s, version=%s, updated_at=now() "
                        "WHERE id=%s RETURNING *", (new, vnote, v, m_id))
            out = self.row(cur.fetchone())
            if resolution:
                cur.execute("UPDATE reports SET resolution=%s, resolved_by=%s, resolved_at=now() "
                            "WHERE mosque_id=%s AND resolution IS NULL", (resolution, actor, m_id))
            self._history(cur, m_id, v, action, actor, {"status": [st, new]}, note)
            return out


# ---------------------------------------------------------------- identity
def snapshot(info):
    """Non-personal client snapshot kept with each audit/report row (no Telegram profile, no IP)."""
    return {k: info.get(k) for k in ("kind", "platform", "os_version", "app_version", "device_model", "user_agent") if info.get(k)}


def verify_telegram(init_data, bot_token):
    """Validate Telegram WebApp initData (HMAC per Telegram docs). Returns the signed user dict or None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join("%s=%s" % (k, pairs[k]) for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > 86400:
            return None
        user = json.loads(pairs["user"])
        return user if isinstance(user.get("id"), int) else None
    except Exception:
        return None


class RateLimiter:
    def __init__(self, per_min):
        self.per_min, self.hits, self.lock = per_min, {}, threading.Lock()

    def allow(self, key, count=True):
        t = time.time()
        with self.lock:
            q = [x for x in self.hits.get(key, []) if t - x < 60]
            ok = len(q) < self.per_min
            if ok and count:
                q.append(t)
            self.hits[key] = q
            return ok


# ---------------------------------------------------------------- http
ID = r"([0-9a-f-]{36})"
STATIC_CACHE = {}
COMPRESSIBLE = (".js", ".css", ".html", ".json", ".svg")


def make_handler(cfg, store):
    limiter = RateLimiter(cfg.rate_limit)
    admin_limiter = RateLimiter(10)      # failed admin logins per minute per client

    class H(BaseHTTPRequestHandler):
        server_version = "masjid-uz"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

        # -- helpers
        def wants_gzip(self):
            return "gzip" in self.headers.get("Accept-Encoding", "")

        def send_json(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if len(data) > 1024 and self.wants_gzip():
                data = gzip.compress(data, 5)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def security_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob: https://tiles.openfreemap.org; "
                             "connect-src 'self' https://tiles.openfreemap.org; worker-src 'self' blob:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self' https://telegram.org; "
                             "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org")

        def body_json(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n > 20000:
                raise ApiError(413, "too_large", "body too large")
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise ApiError(400, "bad_json", "invalid JSON")
            if not isinstance(data, dict):
                raise ApiError(400, "bad_json", "JSON object expected")
            return data

        def identity(self):
            """-> (actor_id | None, info). Order of trust: signature-verified Telegram user, mobile app device id,
            browser uuid. Telegram profile fields are recorded ONLY when the initData signature checks out."""
            h = self.headers

            def clip(name, n):
                return re.sub(r"[\x00-\x1f\x7f]", "", h.get(name, "") or "")[:n] or None
            info = {"platform": clip("X-Client-Platform", 20), "os_version": clip("X-OS-Version", 40),
                    "app_version": clip("X-App-Version", 40), "device_model": clip("X-Device-Model", 80),
                    "user_agent": clip("User-Agent", 200),
                    "ip_hash": hmac.new(cfg.actor_salt.encode(), self.client_ip().encode(), hashlib.sha256).hexdigest()[:24]}
            tg = h.get("X-Telegram-Init-Data")
            user = verify_telegram(tg, cfg.bot_token) if tg and cfg.bot_token else None
            dev = h.get("X-Device-Id", "")
            cid = h.get("X-Client-Id", "")
            if user:
                info.update(kind="telegram", telegram={k: user.get(k) for k in ("id", "username", "first_name", "last_name", "language_code")})
                return "tg:%d" % user["id"], info
            if re.fullmatch(r"[0-9a-zA-Z_-]{8,64}", dev):
                info.update(kind="mobile", device_id=dev, platform=info["platform"] or "mobile")
                return "device:" + dev, info
            if re.fullmatch(r"[0-9a-fA-F-]{8,64}", cid):
                info.update(kind="web", platform=info["platform"] or "web")
                return "client:" + cid, info
            return None, info

        def client_ip(self):
            """Real client address. X-Forwarded-For is honoured only when TRUST_PROXY=1 (behind our Caddy);
            otherwise a client could dodge limits by inventing the header."""
            if cfg.trust_proxy:
                xff = self.headers.get("X-Forwarded-For", "")
                if xff:
                    return xff.split(",")[-1].strip()   # the hop our proxy appended
            return self.client_address[0]

        def local_request(self):
            """True only for a direct request that never passed a proxy/tunnel: a loopback or private-network peer
            (private covers Docker's bridge when the port is published on 127.0.0.1 and reached over an SSH
            tunnel), a local Host, and no proxy headers. A public tunnel (ngrok, Cloudflare) forwards a public Host
            and X-Forwarded-For / CF-Connecting-IP, so it never qualifies."""
            host = self.headers.get("Host", "").split(":")[0].lower()
            try:
                peer = ipaddress.ip_address(self.client_address[0])
                peer_ok = peer.is_loopback or peer.is_private
            except ValueError:
                peer_ok = False
            proxied = any(h in self.headers for h in ("X-Forwarded-For", "Forwarded", "CF-Connecting-IP", "X-Real-IP"))
            return peer_ok and host in ("localhost", "127.0.0.1", "[::1]") and not proxied

        def admin_blocked(self, path):
            return cfg.admin_local_only and (path.startswith("/api/admin/") or path.startswith("/admin")) and not self.local_request()

        def is_admin(self):
            tok = self.headers.get("Authorization", "")
            return bool(cfg.admin_token) and hmac.compare_digest(
                tok.encode(), ("Bearer " + cfg.admin_token).encode())

        # -- routing
        def handle_any(self, method):
            try:
                url = urlparse(self.path)
                path = url.path
                if self.admin_blocked(path):
                    raise ApiError(404, "not_found", "not found")
                if path.startswith("/api/"):
                    return self.api(method, path, dict(parse_qsl(url.query)))
                if method != "GET":
                    raise ApiError(405, "method_not_allowed", "method not allowed")
                return self.static(path)
            except ApiError as e:
                self.send_json(e.status, {"error": e.code, "message": e.message, **e.extra})
            except Exception as e:  # noqa
                sys.stderr.write("ERROR %r\n" % (e,))
                self.send_json(500, {"error": "internal", "message": "internal error"})

        def api(self, method, path, q):
            if method in ("POST", "PATCH") and not limiter.allow(self.client_ip()):
                raise ApiError(429, "rate_limited", "too many writes; slow down")
            if path == "/api/config" and method == "GET":
                return self.send_json(200, {"style_url": cfg.tile_style_url,
                                            "duplicate_radius_m": cfg.dup_radius_m,
                                            "show_tile_mosques": cfg.show_tile_mosques,
                                            "prayer": cfg.prayer})
            if path == "/api/health":
                return self.send_json(200, {"ok": True})
            if path == "/api/mosques" and method == "GET":
                bbox = None
                if q.get("bbox"):
                    try:
                        bbox = tuple(float(x) for x in q["bbox"].split(","))
                        assert len(bbox) == 4
                    except Exception:
                        raise ApiError(400, "invalid_bbox", "bbox=west,south,east,north")
                return self.send_json(200, {"items": store.list(bbox)})
            if path == "/api/export/osm-derived.geojson" and method == "GET":
                return self.send_json(200, store.export_osm())
            if path == "/api/search" and method == "GET":
                return self.send_json(200, {"items": store.search(q.get("q"))})
            if path == "/api/mosques/nearby" and method == "GET":
                lat, lon = clean_coord(q.get("lat"), q.get("lon"))
                r = min(float(q.get("radius", cfg.dup_radius_m)), 500)
                return self.send_json(200, {"items": store.nearby(lat, lon, r)})
            if path == "/api/mosques" and method == "POST":
                return self.send_json(201, store.create(self.body_json(), *self.identity()))
            m = re.fullmatch(r"/api/mosques/" + ID, path)
            if m:
                if method == "GET":
                    return self.send_json(200, store.get(m.group(1)))
                if method == "PATCH":
                    return self.send_json(200, store.edit(m.group(1), self.body_json(), *self.identity()))
            m = re.fullmatch(r"/api/mosques/" + ID + "/history", path)
            if m and method == "GET":
                return self.send_json(200, {"items": store.history(m.group(1))})
            m = re.fullmatch(r"/api/mosques/" + ID + "/reports", path)
            if m and method == "POST":
                return self.send_json(201, store.report(m.group(1), self.body_json(), *self.identity()))
            if path.startswith("/api/admin/"):
                ip = self.client_ip()
                if not admin_limiter.allow(ip, count=False):
                    raise ApiError(429, "rate_limited", "too many failed admin attempts")
                if not self.is_admin():
                    admin_limiter.allow(ip)             # count failures only
                    raise ApiError(401, "unauthorized", "admin token required")
                if path == "/api/admin/actors" and method == "GET":
                    return self.send_json(200, {"items": store.actors(q.get("q"), int(q.get("limit", 50)))})
                if path == "/api/admin/overview" and method == "GET":
                    return self.send_json(200, store.overview())
                if path == "/api/admin/mosques" and method == "GET":
                    return self.send_json(200, store.admin_list(q.get("status", "reported"), int(q.get("limit", 100)), int(q.get("offset", 0))))
                if path == "/api/admin/reports" and method == "GET":
                    return self.send_json(200, {"groups": store.reports(q.get("all") != "1")})
                m = re.fullmatch(r"/api/admin/mosques/" + ID + "/(archive|restore|verify|dismiss)", path)
                if m and method == "POST":
                    body = self.body_json()
                    return self.send_json(200, store.moderate(m.group(1), m.group(2), "admin", body.get("note")))
            raise ApiError(404, "not_found", "no such endpoint")

        def static(self, path):
            rel = unquote(path).lstrip("/") or "index.html"
            f = (WEB_DIR / rel).resolve()
            if WEB_DIR.resolve() not in f.parents or not f.is_file():
                raise ApiError(404, "not_found", "not found")
            st = f.stat()
            key = (str(f), st.st_mtime_ns, st.st_size)
            ent = STATIC_CACHE.get(str(f))
            if not ent or ent[0] != key:        # (re)build entry: raw, gzip, etag
                raw = f.read_bytes()
                gz = gzip.compress(raw, 9) if f.suffix in COMPRESSIBLE and len(raw) > 512 else None
                ent = (key, raw, gz, '"%s"' % hashlib.sha1(raw).hexdigest()[:20])
                STATIC_CACHE[str(f)] = ent
            _, raw, gz, etag = ent
            self.send_response(304 if self.headers.get("If-None-Match") == etag else 200)
            if rel.startswith("vendor/"):       # pinned third-party files never change in place
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            else:                               # our own files: always revalidate (cheap 304)
                self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            self.security_headers()
            if rel.startswith("admin"):
                self.send_header("X-Robots-Tag", "noindex, nofollow")
            if self.headers.get("If-None-Match") == etag:
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = raw
            if gz is not None and self.wants_gzip():
                body = gz
                self.send_header("Content-Encoding", "gzip")
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"             # never leave the encoding to the browser's guess
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self): self.handle_any("GET")
        def do_HEAD(self): self.handle_any("GET")        # monitors/proxies/curl -I: same headers, no body
        def do_POST(self): self.handle_any("POST")
        def do_PATCH(self): self.handle_any("PATCH")
        def do_DELETE(self): self.handle_any("DELETE")  # always 405/404: nothing is deletable via API
        def do_PUT(self): self.handle_any("PUT")

    return H


def build_server(cfg):
    store = Store(cfg)
    srv = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg, store))
    srv.store = store
    return srv


if __name__ == "__main__":
    cfg = Config()
    if not cfg.admin_token:
        sys.stderr.write("WARNING: ADMIN_TOKEN not set; moderation endpoints disabled\n")
    srv = build_server(cfg)
    sys.stderr.write("listening on http://%s:%d\n" % (cfg.host, cfg.port))
    srv.serve_forever()
