"""masjid-uz backend: community mosque records with audit history (PostgreSQL, schema `masjiduz`).

Python standard library HTTP server + psycopg. TIMUS runs nginx and TLS in front; this process serves
`/masjiduz/v1/...` (API, standard envelope, camelCase) and `/masjiduz/web/...` (map + Telegram Mini App).
The contract is docs/CONTRACT.md. Entry point: `python server/cli.py serve`.
"""
import base64
import gzip
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import mimetypes
import re
import signal
import socket
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

import botdb
import gemini
import logs
import migrate
import translit
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout
from config import Config, ConfigError  # noqa: F401  (Config is re-exported for callers: app.Config)

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
API = "/masjiduz/v1"
WEB = "/masjiduz/web"
MAX_BODY = 64 * 1024                    # bytes of one JSON request
MAP_LIMIT = 3000                        # most mosques one bbox request returns
log = logging.getLogger("app")
access = logging.getLogger("access")

PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
REPORT_KINDS = ("incorrect", "duplicate", "relocated", "closed", "other")
LANGS = ("uz", "uz-cyrl", "ru", "en")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- errors and the response envelope
class ApiError(Exception):
    """`code` is an internal snake_case name (becomes message.code `error.<camelCase>`); `reason` is the field at fault;
    extra keywords become the response `data` (e.g. duplicates)."""
    def __init__(self, status, code, message, reason=None, **extra):
        self.status, self.code, self.message, self.reason, self.extra = status, code, message, reason, extra


STD_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT",
             413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 503: "SERVICE_UNAVAILABLE"}
MODULE_CODES = {"possible_duplicate": "MASJIDUZ_POSSIBLE_DUPLICATE", "archived": "MASJIDUZ_MOSQUE_ARCHIVED", "field_locked": "MASJIDUZ_FIELD_LOCKED"}
VALIDATION = {"validation", "reason_required", "evidence_required"}


def camel(s):
    head, *rest = s.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def camelize(o):
    """Every dict key snake_case -> camelCase, recursively (the database speaks snake_case, the API camelCase)."""
    if isinstance(o, dict):
        return {camel(k) if isinstance(k, str) else k: camelize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [camelize(v) for v in o]
    return o


def ok_body(status, data):
    return {"success": True, "httpStatus": status, "code": "OK", "message": {"code": "common.ok", "text": "OK"}, "data": data}


def error_body(e):
    if e.code in MODULE_CODES:
        code = MODULE_CODES[e.code]
    elif e.status == 400 and (e.code in VALIDATION or e.code.startswith("invalid")):
        code = "VALIDATION_ERROR"
    else:
        code = STD_CODES.get(e.status, "BAD_REQUEST")
    msg = {"code": "error." + camel(e.code), "text": e.message}
    if e.reason:
        msg["reason"] = e.reason
    return {"success": False, "httpStatus": e.status, "code": code, "message": msg, "data": camelize(e.extra) if e.extra else None}


# ---------------------------------------------------------------- validation
# Control characters and invisible/bidi-override characters are never legitimate in names, addresses or reasons.
_BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤⁦-⁩﻿]")


def clean_text(value, field, limit, multiline=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "validation", field + " must be text", reason=field)
    try:
        value.encode("utf-8")                    # lone surrogates (broken emoji, "\ud83d") would crash the DB driver
    except UnicodeEncodeError:
        raise ApiError(400, "validation", field + " contains invalid characters", reason=field)
    value = unicodedata.normalize("NFC", value)  # one canonical form for Cyrillic/Latin letters with diacritics
    if not multiline:
        value = re.sub(r"[\r\n\t]+", " ", value)
    value = _BAD_CHARS.sub("", value).strip()
    if len(value) > limit:
        raise ApiError(400, "validation", field + " is too long (max %d)" % limit, reason=field)
    return value or None


def json_number(v, field):
    """A JSON body must carry a real number (not a string, not true/false)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ApiError(400, "invalid_coordinates", field + " must be a number", reason=field)
    return v


def clean_coord(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(400, "invalid_coordinates", "lat and lon must be numbers", reason="lat")
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        raise ApiError(400, "invalid_coordinates", "lat/lon out of range", reason="lat")
    return round(lat, 6), round(lon, 6)


def clean_times(times):
    if times is None:
        return {}
    if not isinstance(times, dict):
        raise ApiError(400, "invalid_prayer_times", "prayerTimes must be an object", reason="prayerTimes")
    out = {}
    for key, val in times.items():
        if key not in PRAYERS:
            raise ApiError(400, "invalid_prayer_times", "unknown prayer: " + str(key)[:20], reason="prayerTimes")
        if val in (None, ""):
            continue  # missing = unknown
        if not isinstance(val, str) or not TIME_RE.match(val):
            raise ApiError(400, "invalid_prayer_times", key + " must be HH:MM (24h)", reason="prayerTimes")
        out[key] = val
    return out


def pick(body, allowed):
    """Request body (camelCase) -> internal dict (snake_case). A field that is not allowed is a 400 naming that field."""
    out = {}
    for k, v in body.items():
        if k not in allowed:
            raise ApiError(400, "validation", "unknown field: " + str(k)[:40], reason=str(k)[:40])
        out[allowed[k]] = v
    return out


def enc_cursor(value):
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def dec_cursor(s):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", s):
        raise ApiError(400, "validation", "invalid cursor", reason="cursor")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()
    except Exception:
        raise ApiError(400, "validation", "invalid cursor", reason="cursor") from None


def page_args(q, default=20, maximum=100):
    try:
        limit = int(q.get("limit", default))
    except ValueError:
        raise ApiError(400, "validation", "limit must be a whole number", reason="limit") from None
    if not 1 <= limit <= maximum:
        raise ApiError(400, "validation", "limit must be between 1 and %d" % maximum, reason="limit")
    return limit, (dec_cursor(q["cursor"]) if q.get("cursor") else None)


def int_cursor(c):
    if c is None:
        return 0
    if not c.isdigit() or len(c) > 9:
        raise ApiError(400, "validation", "invalid cursor", reason="cursor")
    return int(c)


# ---------------------------------------------------------------- store
def iso(v):
    return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(v, datetime) else str(v)    # always UTC, whatever the session time zone


class Store:
    def __init__(self, cfg, name="masjiduz", statement_timeout_ms=5000, check=True):
        self.cfg = cfg
        if check:
            todo = migrate.pending(cfg)
            if todo:
                raise ConfigError("database migrations are pending (%s): run the migrate command first" % ", ".join(todo))
        opts = "-c search_path=%s -c statement_timeout=%d -c application_name=%s" % (migrate.SCHEMA, statement_timeout_ms, name)
        self.pool = ConnectionPool(cfg.conninfo(), min_size=1, max_size=cfg.db_pool_max, timeout=5, open=True,
                                   kwargs={"row_factory": dict_row, "options": opts})
        self._backfill_search()

    def close(self):
        self.pool.close()

    def ping(self):
        """A fresh short-lived connection (2 s): a busy pool is not a database outage."""
        opts = "-c search_path=%s -c statement_timeout=2000 -c application_name=masjiduz-health" % migrate.SCHEMA
        with psycopg.connect(self.cfg.conninfo(2), autocommit=True, options=opts) as c:
            c.execute("SELECT 1")

    def _backfill_search(self):
        with self.pool.connection() as c:
            for r in c.execute("SELECT id, name FROM mosques WHERE name IS NOT NULL AND name_search IS NULL").fetchall():
                c.execute("UPDATE mosques SET name_search=%s WHERE id=%s", (translit.search_key(r["name"]), r["id"]))

    def purge_network_hashes(self, days=30):
        """The hashed network of a client is kept at most 30 days."""
        with self.pool.connection() as c:
            return c.execute("UPDATE actors SET ip_hash=NULL WHERE ip_hash IS NOT NULL AND last_seen < now() - make_interval(days => %s)", (days,)).rowcount

    @staticmethod
    def row(r, private=False):
        d = dict(r)
        d["id"] = str(d["id"])
        d.pop("name_search", None)
        if not private:
            d.pop("creator_id", None)        # never expose Telegram/device ids in public responses
        for k in ("created_at", "updated_at"):
            d[k] = iso(d[k])
        d["times_updated_at"] = iso(d["times_updated_at"]) if d.get("times_updated_at") else None
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

    def map_items(self, bbox):
        """Compact points for the map inside a bounding box: (items, truncated). Archived records are left out."""
        w, s, e, n = bbox
        with self.pool.connection() as c:
            rows = c.execute("SELECT id, lat, lon, name, status FROM mosques WHERE status<>'archived' AND lat BETWEEN %s AND %s "
                             "AND lon BETWEEN %s AND %s ORDER BY id LIMIT %s", (s, n, w, e, MAP_LIMIT + 1)).fetchall()
        return [dict(r, id=str(r["id"])) for r in rows[:MAP_LIMIT]], len(rows) > MAP_LIMIT

    def list(self, limit, after=None):
        """Full records, a page at a time, in id order -> (items, next id | None)."""
        if after:
            try:
                uuid.UUID(after)
            except ValueError:
                raise ApiError(400, "validation", "invalid cursor", reason="cursor") from None
        with self.pool.connection() as c:
            rows = c.execute("SELECT * FROM mosques WHERE status<>'archived'" + (" AND id > %s" if after else "") + " ORDER BY id LIMIT %s",
                             ([after] if after else []) + [limit + 1]).fetchall()
        items = [self.row(r) for r in rows[:limit]]
        return items, (items[-1]["id"] if len(rows) > limit else None)

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
        return sorted(out, key=lambda x: (x["distance_m"], x["id"]))      # a fixed order for equal distances: pages never repeat or skip

    MAX_DEPTH = 500

    def nearest(self, lat, lon, limit=5, max_m=3000000.0, offset=0):
        """The `limit` nearest non-archived mosques after the first `offset` (a list is read page by page). The search circle grows
        until it holds enough results; every mosque inside a circle is considered, so what is returned really are the nearest
        ones (not an approximation)."""
        limit = max(1, min(int(limit), 101))
        offset = max(0, min(int(offset), self.MAX_DEPTH))
        found = []
        with self.pool.connection() as c:
            cur = c.cursor()
            for r in (500.0, 2000.0, 8000.0, 30000.0, 100000.0, max_m):
                found = self._nearby(cur, lat, lon, min(r, max_m))
                if len(found) >= limit + offset:
                    break
        return found[offset:offset + limit]

    def nearby(self, lat, lon, radius):
        with self.pool.connection() as c:
            return self._nearby(c.cursor(), lat, lon, radius)[:100]

    def create(self, body, actor, client=None):
        lat, lon = clean_coord(json_number(body.get("lat"), "lat"), json_number(body.get("lon"), "lon"))
        if not isinstance(body.get("force", False), bool):
            raise ApiError(400, "validation", "force must be true or false", reason="force")
        name = clean_text(body.get("name"), "name", 200)
        address = clean_text(body.get("address"), "address", 300)
        times = clean_times(body.get("prayer_times"))
        with self.pool.connection() as c:
            cur = c.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(727002)")  # serialise duplicate check + insert
            dups = self._nearby(cur, lat, lon, self.cfg.dup_radius_m)
            if dups and not body.get("force"):
                raise ApiError(409, "possible_duplicate", "A mosque already exists nearby", duplicates=dups)
            cur.execute("INSERT INTO mosques(lat,lon,name,address,prayer_times,source,origin,status,creator_id,name_search,times_updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,'community-app','community','unverified',%s,%s,CASE WHEN %s THEN now() END) RETURNING *",
                        (lat, lon, name, address, Jsonb(times), actor, translit.search_key(name), bool(times)))
            m = self.row(cur.fetchone())
            self._history(cur, m["id"], 1, "create", actor, {
                "lat": [None, lat], "lon": [None, lon], "name": [None, name],
                "address": [None, address], "prayer_times": [None, times]}, client=client)
            self.touch_actor(cur, actor, client, edits=1)
            return m

    def edit(self, m_id, body, actor, client=None):
        # Concurrent edits never fail: the row is locked, the sent fields are applied on top of the LATEST state (field by field,
        # prayer times key by key; for the very same field the last write wins) and the history records what really changed.
        # Clients send only the fields the user changed, so an old form cannot undo somebody else's edit.
        with self.pool.connection() as c:
            cur = c.cursor()
            old = self._fetch(cur, m_id, lock=True)
            if old["status"] == "archived":
                raise ApiError(409, "archived", "record is archived")
            new = {}
            if "lat" in body or "lon" in body:
                new["lat"], new["lon"] = clean_coord(json_number(body.get("lat", old["lat"]), "lat"), json_number(body.get("lon", old["lon"]), "lon"))
            if "name" in body:
                new["name"] = clean_text(body["name"], "name", 200)
            if "address" in body:
                new["address"] = clean_text(body["address"], "address", 300)
            if "prayer_times" in body:
                if not isinstance(body["prayer_times"], dict):
                    raise ApiError(400, "invalid_prayer_times", "prayerTimes must be an object", reason="prayerTimes")
                merged = dict(old["prayer_times"])
                for k, v in body["prayer_times"].items():
                    if v in (None, ""):
                        merged.pop(k, None)   # explicit clear
                    else:
                        merged[k] = v
                new["prayer_times"] = clean_times(merged)
            changes = {k: [old[k], v] for k, v in new.items() if old[k] != v}
            locked = [k for k in ("name", "address", "lat", "lon") if k in changes]
            if old["origin"] == "imported" and locked:    # records taken over from the map data: only the congregation times are editable
                raise ApiError(403, "field_locked", "only the prayer times of this mosque can be changed", reason=locked[0])
            if not changes:
                return old
            version = old["version"] + 1
            sets = ", ".join(k + "=%s" for k in changes)
            vals = [Jsonb(v[1]) if k == "prayer_times" else v[1] for k, v in changes.items()]
            if "name" in changes:
                sets += ", name_search=%s"
                vals.append(translit.search_key(changes["name"][1]))
            if "prayer_times" in changes:                  # the congregation times themselves changed: stamp that moment
                sets += ", times_updated_at=" + ("now()" if changes["prayer_times"][1] else "NULL")
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
                "SELECT id,name,address,photo_url,lat,lon,status FROM mosques WHERE status<>'archived' AND name_search LIKE ANY(%s) "
                "ORDER BY char_length(name), name LIMIT %s", (pats, limit))]

    def admin_list(self, status, limit=100, offset=0):
        if status not in ("unverified", "reported", "verified", "archived"):
            raise ApiError(400, "invalid_status", "status must be unverified|reported|verified|archived", reason="status")
        with self.pool.connection() as c:
            rows = c.execute("SELECT * FROM mosques WHERE status=%s ORDER BY updated_at DESC, id LIMIT %s OFFSET %s",
                             (status, limit + 1, max(offset, 0))).fetchall()
            total = c.execute("SELECT COUNT(*) AS n FROM mosques WHERE status=%s", (status,)).fetchone()["n"]
        return {"total": total, "items": [self.row(r, private=True) for r in rows[:limit]], "more": len(rows) > limit}

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
        sql = "SELECT actor_id,kind,telegram_id,tg_username,tg_first_name,tg_last_name,tg_language,device_id,platform,os_version,app_version,device_model," \
              "user_agent,edits_count,reports_count,first_seen,last_seen FROM actors"
        args = []
        if q:
            sql += " WHERE actor_id ILIKE %s OR tg_username ILIKE %s OR tg_first_name ILIKE %s OR device_model ILIKE %s"
            args = ["%" + q.replace("%", "").replace("_", "") + "%"] * 4
        with self.pool.connection() as c:
            rows = c.execute(sql + " ORDER BY last_seen DESC LIMIT %s", args + [limit]).fetchall()
        return [dict(r, first_seen=iso(r["first_seen"]), last_seen=iso(r["last_seen"])) for r in rows]

    # -- map provider: OpenStreetMap or Yandex with a pool of keys (see migrations/009_map_keys.sql)
    MAP_DAY = "(now() AT TIME ZONE 'Europe/Moscow')::date"          # Yandex counts its day in Moscow time
    MAP_REPORTS_TO_EXHAUST = 3                                         # distinct devices that must say "Yandex refused this key"

    def map_choice(self, client, kind="app"):
        """What a client should draw today -> {"provider": "osm"} or {"provider": "yandex", "yandex_key": ..., "key_id": ...}.
        Yandex is for the mobile app only (MapKit): the web client and the server never contact Yandex, a non-app client always gets OSM.
        The first enabled key with room (distinct users today < its limit, not refused by Yandex today) is handed out and remembered
        for the day, so one user counts once. Only MapKit keys (kind app) are considered.
        No key left -> OpenStreetMap, whatever the setting says."""
        osm = {"provider": "osm"}
        if kind != "app":
            return osm
        with self.pool.connection() as c:
            if c.execute("SELECT provider FROM map_settings").fetchone()["provider"] != "yandex":
                return osm
            c.execute("SELECT pg_advisory_xact_lock(727005)")      # its own lock: page loads never queue behind mosque creation
            day = c.execute("SELECT " + self.MAP_DAY + " AS d").fetchone()["d"]
            # a key this very client already reported as refused today is never handed to it again (the others may still work for it)
            not_refused = " AND NOT EXISTS (SELECT 1 FROM map_key_reports r WHERE r.day=%s AND r.key_id=k.id AND r.client_id=%s)"
            usable = ("SELECT k.id, k.api_key FROM map_keys k WHERE k.kind=%s AND k.enabled AND k.exhausted_on IS DISTINCT FROM %s"
                      " AND (SELECT COUNT(*) FROM map_key_users u WHERE u.day=%s AND u.key_id=k.id) < k.daily_limit" + not_refused + " ORDER BY k.id LIMIT 1")
            row = None
            if client:
                row = c.execute("SELECT k.id, k.api_key FROM map_key_users u JOIN map_keys k ON k.id=u.key_id WHERE u.day=%s AND u.client_id=%s"
                                " AND k.kind=%s AND k.enabled AND k.exhausted_on IS DISTINCT FROM %s" + not_refused, (day, client, kind, day, day, client)).fetchone()
            if not row:
                row = c.execute(usable, (kind, day, day, day, client or "")).fetchone()
                if row and client:
                    c.execute("INSERT INTO map_key_users (day, client_id, key_id) VALUES (%s,%s,%s)"
                              " ON CONFLICT (day, client_id) DO UPDATE SET key_id=EXCLUDED.key_id", (day, client, row["id"]))
                    c.execute("DELETE FROM map_key_users WHERE day < %s - 2", (day,))
                    c.execute("DELETE FROM map_key_reports WHERE day < %s - 2", (day,))
        return {"provider": "yandex", "yandex_key": row["api_key"], "key_id": row["id"]} if row else osm

    def map_report_failure(self, key_id, client):
        """A client says Yandex refused this key. Enough different devices saying so switch the key off for the rest of the day."""
        if not client:
            return False
        with self.pool.connection() as c:
            day = c.execute("SELECT " + self.MAP_DAY + " AS d").fetchone()["d"]
            if not c.execute("SELECT 1 FROM map_key_users WHERE day=%s AND client_id=%s AND key_id=%s", (day, client, key_id)).fetchone():
                return False                      # only a client that was really handed this key today can report it (made-up ids count for nothing)
            c.execute("INSERT INTO map_key_reports (day, key_id, client_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (day, key_id, client))
            n = c.execute("SELECT COUNT(*) AS n FROM map_key_reports WHERE day=%s AND key_id=%s", (day, key_id)).fetchone()["n"]
            if n >= self.MAP_REPORTS_TO_EXHAUST:
                c.execute("UPDATE map_keys SET exhausted_on=%s WHERE id=%s", (day, key_id))
                return True
        return False

    @staticmethod
    def _mask(k):
        return k[:4] + "…" + k[-4:] if len(k) > 10 else "…"

    def map_admin(self):
        with self.pool.connection() as c:
            provider = c.execute("SELECT provider FROM map_settings").fetchone()["provider"]
            day = c.execute("SELECT " + self.MAP_DAY + " AS d").fetchone()["d"]
            keys = [{"id": r["id"], "label": r["label"], "kind": r["kind"], "key": self._mask(r["api_key"]), "daily_limit": r["daily_limit"], "enabled": r["enabled"],
                     "exhausted": r["exhausted_on"] == day, "users_today": r["users"]} for r in c.execute(
                "SELECT k.*, (SELECT COUNT(*) FROM map_key_users u WHERE u.day=%s AND u.key_id=k.id) AS users FROM map_keys k ORDER BY k.id", (day,))]
        usable = [k for k in keys if k["enabled"] and not k["exhausted"] and k["users_today"] < k["daily_limit"]]
        return {"provider": provider, "keys": keys, "effective": {"app": "yandex" if provider == "yandex" and usable else "osm"}}

    def map_set_provider(self, provider):
        if provider not in ("osm", "yandex"):
            raise ApiError(400, "invalid_provider", "provider must be osm or yandex", reason="provider")
        with self.pool.connection() as c:
            c.execute("UPDATE map_settings SET provider=%s, updated_at=now()", (provider,))

    def map_add_key(self, body):
        key = str(body.get("key") or "").strip()
        if not re.fullmatch(r"[0-9A-Za-z._-]{10,200}", key):
            raise ApiError(400, "invalid_key", "the key looks wrong (10-200 letters, digits, dots, dashes)", reason="key")
        label = str(body.get("label") or "").strip()[:60]
        kind = body.get("kind", "app")
        if kind != "app":
            raise ApiError(400, "invalid_kind", "kind must be app (Yandex is used by the mobile app only)", reason="kind")
        limit = self._limit(body.get("daily_limit", 25000))
        with self.pool.connection() as c:
            try:
                r = c.execute("INSERT INTO map_keys (label, api_key, daily_limit, kind) VALUES (%s,%s,%s,%s) RETURNING id", (label, key, limit, kind)).fetchone()
            except psycopg.errors.UniqueViolation:
                raise ApiError(409, "key_exists", "this key is already added") from None
        return {"id": r["id"]}

    @staticmethod
    def _limit(v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = 0
        if not 1 <= n <= 10_000_000:
            raise ApiError(400, "invalid_limit", "the daily limit must be a whole number from 1 up", reason="dailyLimit")
        return n

    def map_update_key(self, key_id, body):
        sets, args = [], []
        if "enabled" in body:
            sets.append("enabled=%s"); args.append(bool(body["enabled"]))
        if "daily_limit" in body:
            sets.append("daily_limit=%s"); args.append(self._limit(body["daily_limit"]))
        if "label" in body:
            sets.append("label=%s"); args.append(str(body["label"]).strip()[:60])
        if body.get("reset"):
            sets.append("exhausted_on=NULL")
        if not sets:
            raise ApiError(400, "validation", "nothing to change")
        with self.pool.connection() as c:
            if not c.execute("UPDATE map_keys SET " + ",".join(sets) + " WHERE id=%s RETURNING id", args + [key_id]).fetchone():
                raise ApiError(404, "not_found", "no such key")
            if body.get("reset"):
                c.execute("DELETE FROM map_key_reports WHERE key_id=%s", (key_id,))

    def map_delete_key(self, key_id):
        with self.pool.connection() as c:
            if not c.execute("DELETE FROM map_keys WHERE id=%s RETURNING id", (key_id,)).fetchone():
                raise ApiError(404, "not_found", "no such key")

    def history(self, m_id, limit=20, before=None):
        """Public audit trail: no personal identifiers. `actor` is only a role: user | moderator | osm | system.
        Newest first; `before` is the seq of the last row already seen. -> (items, more)"""
        with self.pool.connection() as c:
            cur = c.cursor()
            self._fetch(cur, m_id)
            cur.execute("SELECT * FROM mosque_history WHERE mosque_id=%s" + (" AND seq < %s" if before else "") + " ORDER BY seq DESC LIMIT %s",
                        [m_id] + ([before] if before else []) + [limit + 1])
            rows = cur.fetchall()
        out = []
        for r in rows[:limit]:
            a = r["actor"]
            role = (None if a is None else "moderator" if a == "admin" or a.startswith("admin:") else "osm" if a == "import:osm"
                    else "system" if a.startswith("import:") else "user")
            out.append({"seq": r["seq"], "mosque_id": str(r["mosque_id"]), "version": r["version"], "action": r["action"],
                        "actor": role, "at": iso(r["at"]), "changes": r["changes"], "note": r["note"]})
        return out, len(rows) > limit

    def report(self, m_id, body, actor, client=None):
        if not actor:
            raise ApiError(400, "validation", "send X-Client-Id, X-Device-Id or Telegram data so the report counts once per person", reason="X-Client-Id")
        kind = body.get("kind", "incorrect")
        if kind not in REPORT_KINDS:
            raise ApiError(400, "invalid_kind", "kind must be one of " + ", ".join(REPORT_KINDS), reason="kind")
        reason = clean_text(body.get("reason"), "reason", 1000, multiline=True)
        if not reason:
            raise ApiError(400, "reason_required", "a reason is required", reason="reason")
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

    def moderate(self, m_id, action, actor, note, kind=None):
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
                    raise ApiError(400, "evidence_required", "verification requires an evidence note", reason="note")
                new, vnote = "verified", note
            else:  # dismiss reports (all of them, or only those of one kind)
                if kind is not None and kind not in REPORT_KINDS:
                    raise ApiError(400, "invalid_kind", "kind must be one of " + ", ".join(REPORT_KINDS), reason="kind")
                resolution = "rejected"
                left = cur.execute("SELECT 1 FROM reports WHERE mosque_id=%s AND resolution IS NULL AND kind<>%s LIMIT 1", (m_id, kind)).fetchone() if kind else None
                new = st if left else ("unverified" if st == "reported" else st)       # other open reports keep the mosque 'reported'
            v = m["version"] + 1
            cur.execute("UPDATE mosques SET status=%s, verification_note=%s, version=%s, updated_at=now() "
                        "WHERE id=%s RETURNING *", (new, vnote, v, m_id))
            out = self.row(cur.fetchone())
            if resolution:
                cur.execute("UPDATE reports SET resolution=%s, resolved_by=%s, resolved_at=now() "
                            "WHERE mosque_id=%s AND resolution IS NULL AND (%s::text IS NULL OR kind=%s)", (resolution, actor, m_id, kind, kind))
            self._history(cur, m_id, v, action, actor, {"status": [st, new]}, note)
            return out


# ---------------------------------------------------------------- identity
def looks_like_audio(data):
    """A real recording by its first bytes: Ogg, WAV, MP3 (ID3 or frame), MP4/M4A, WebM/Matroska, ADTS AAC (the declared type is not trusted)."""
    return (data[:4] in (b"OggS", b"\x1a\x45\xdf\xa3", b"ID3\x03", b"ID3\x04", b"ID3\x02") or (data[:4] == b"RIFF" and data[8:12] == b"WAVE")
            or data[4:8] == b"ftyp" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xf1", b"\xff\xf9"))


def looks_like_image(data):
    """JPEG, PNG or WebP by the first bytes (the declared content type is not trusted)."""
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")



def snapshot(info):
    """Non-personal client snapshot kept with each audit/report row (no Telegram profile, no IP)."""
    return {k: info.get(k) for k in ("kind", "platform", "os_version", "app_version", "device_model", "user_agent") if info.get(k)}


def verify_telegram(init_data, bot_token, max_age=86400):
    """Validate Telegram WebApp initData (HMAC per Telegram docs). Returns the signed user dict or None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join("%s=%s" % (k, pairs[k]) for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        age = time.time() - int(pairs.get("auth_date", "0"))
        if age > max_age or age < -300:                  # too old, or from the future
            return None
        user = json.loads(pairs["user"])
        return user if isinstance(user.get("id"), int) else None
    except Exception:
        return None


def network_of(ip):
    """The /24 (IPv4) or /48 (IPv6) network of an address, as text: the most that is ever hashed or kept."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return str(ipaddress.ip_network("%s/%d" % (a, 24 if a.version == 4 else 48), strict=False))


class RateLimiter:
    def __init__(self, per_window, window=60):
        self.per_window, self.window, self.hits, self.lock = per_window, window, {}, threading.Lock()

    def allow(self, key, count=True):
        t = time.time()
        with self.lock:
            q = [x for x in self.hits.get(key, []) if t - x < self.window]
            ok = len(q) < self.per_window
            if ok and count:
                q.append(t)
            self.hits[key] = q
            if len(self.hits) > 20000:                        # forget idle keys: memory stays bounded
                for k in [k for k, v in self.hits.items() if not v or t - v[-1] >= self.window]:
                    del self.hits[k]
            return ok

    def retry_after(self, key):
        with self.lock:
            q = self.hits.get(key) or []
            return max(1, int(math.ceil(self.window - (time.time() - q[0])))) if q else 1


# ---------------------------------------------------------------- http
ID = r"([0-9a-f-]{36})"
LIGHT_WRITES = ("/me/lang", "/map/yandex-failed", "/timetable/photo", "/timetable/voice")     # cheap or limited on their own: not counted as community writes
STATIC_CACHE = {}
COMPRESSIBLE = (".js", ".css", ".html", ".json", ".svg")
HASHED = re.compile(r"^(.+)\.([0-9a-f]{8})\.(js|css)$")
LOCAL_REF = re.compile(r'(\b(?:src|href)=")([^":?#]+)\.(js|css)(")')
CORS_HEADERS = "Content-Type, Authorization, X-Request-Id, X-Client-Id, X-Device-Id, X-Client-Platform, X-OS-Version, X-App-Version, X-Device-Model, X-Telegram-Init-Data"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def csp(cfg):
    """The strict policy: no 'unsafe-eval', no scripts but our own (and Telegram's on the Mini App page), no Yandex of any kind."""
    tiles = {"https://tiles.openfreemap.org"}
    u = urlparse(cfg.tile_style_url)
    if u.scheme == "https" and u.netloc:
        tiles.add("https://" + u.netloc)
    t = " ".join(sorted(tiles))
    return ("default-src 'self'; img-src 'self' data: blob: %s https://upload.wikimedia.org; connect-src 'self' %s; worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' https://telegram.org; "
            "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org" % (t, t))


def etag_matches(header, etag):
    """If-None-Match with one or several tags, weak (W/"...") ones too (a proxy that recompresses weakens them)."""
    return bool(header) and (header.strip() == "*" or etag in [t.strip()[2:] if t.strip().startswith("W/") else t.strip() for t in header.split(",")])


def file_hash(raw):
    return hashlib.sha256(raw).hexdigest()[:8]


class RateLimited(ApiError):
    def __init__(self, retry_after, message="too many requests; slow down"):
        super().__init__(429, "rate_limited", message)
        self.retry_after = retry_after


class Request(BaseHTTPRequestHandler):
    """One request. Built by make_handler() with `cfg`, `store`, limiters and `state` bound as class attributes."""
    cfg = store = bdb = limiter = admin_limiter = state = None
    command = None
    rid = "-"
    status = 0
    _consumed = False
    server_version = "masjid-uz"
    sys_version = ""                              # do not announce the Python version
    protocol_version = "HTTP/1.1"
    timeout = 30                                  # a client that goes quiet cannot hold a thread for ever

    def log_message(self, fmt, *args):            # the access line is written by handle_any, nothing from the stdlib
        pass

    # -- low level
    def wants_gzip(self):
        return "gzip" in self.headers.get("Accept-Encoding", "")

    def common_headers(self):
        self.send_header("X-Request-Id", self.rid)
        self.send_header("X-Timus-Env", self.cfg.env)
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = (getattr(self, "headers", None) or {}).get("Origin")
        if self.cfg.cors_origins:
            self.send_header("Vary", "Origin")
        if origin and self.cors_ok(origin):
            self.send_header("Access-Control-Allow-Origin", origin)

    def cors_ok(self, origin):
        return "*" in self.cfg.cors_origins or origin.rstrip("/") in self.cfg.cors_origins

    def send_envelope(self, status, body, headers=None):
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self.status = status
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if len(data) > 1024 and self.wants_gzip():
            data = gzip.compress(data, 5)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        if not (headers and "Cache-Control" in headers):
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.common_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_ok(self, data, status=200, headers=None):
        self.send_envelope(status, ok_body(status, camelize(data)), headers)

    def send_error_envelope(self, e, headers=None):
        self.send_envelope(e.status, error_body(e), headers)

    # -- request body
    def body_json(self):
        n = self.content_length()
        raw = self.rfile.read(n) if n else b""
        self._consumed = True
        if not raw.strip():
            return {}
        if "application/json" not in self.headers.get("Content-Type", "").lower():
            raise ApiError(415, "unsupported_media_type", "send the body as application/json")
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiError(400, "bad_json", "invalid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "JSON object expected")
        return data

    def content_length(self):
        try:
            return max(0, int(self.headers.get("Content-Length", "0")))
        except ValueError:
            raise ApiError(400, "bad_request", "invalid Content-Length") from None

    # -- who is calling
    def client_ip(self):
        """Real client address. X-Forwarded-For is honoured only when the TCP peer is a trusted proxy (loopback / private
        network / TIMUS_APP_MASJIDUZ_TRUSTED_PROXIES); otherwise the peer address is used."""
        peer = self.client_address[0]
        try:
            trusted = any(ipaddress.ip_address(peer) in net for net in self.cfg.proxies)
        except ValueError:
            trusted = False
        if trusted:
            xff = [x.strip() for x in self.headers.get("X-Forwarded-For", "").split(",") if x.strip()]
            for hop in reversed(xff):                       # the right-most hop that is not one of our own proxies
                try:
                    if not any(ipaddress.ip_address(hop) in net for net in self.cfg.proxies):
                        return hop
                except ValueError:
                    break
            if xff:
                return xff[0]
        return peer

    def identity(self):
        """-> (actor_id | None, info). Order of trust: signature-verified Telegram user, mobile app device id,
        browser uuid. Telegram profile fields are recorded ONLY when the initData signature checks out."""
        if getattr(self, "_identity", None):
            return self._identity
        h = self.headers

        def clip(name, n):
            return re.sub(r"[\x00-\x1f\x7f]", "", h.get(name, "") or "")[:n] or None
        info = {"platform": clip("X-Client-Platform", 20), "os_version": clip("X-OS-Version", 40),
                "app_version": clip("X-App-Version", 40), "device_model": clip("X-Device-Model", 80),
                "user_agent": clip("User-Agent", 200),
                "ip_hash": hmac.new(self.cfg.actor_salt.encode(), network_of(self.client_ip()).encode(), hashlib.sha256).hexdigest()[:24]}
        tg = h.get("X-Telegram-Init-Data")
        user = verify_telegram(tg, self.cfg.bot_token) if tg and self.cfg.bot_token else None
        dev = h.get("X-Device-Id", "")
        cid = h.get("X-Client-Id", "")
        if user:
            info.update(kind="telegram", telegram={k: user.get(k) for k in ("id", "username", "first_name", "last_name", "language_code")})
            self._identity = ("tg:%d" % user["id"], info)
        elif re.fullmatch(r"[0-9a-zA-Z_-]{8,64}", dev):
            info.update(kind="mobile", device_id=dev, platform=info["platform"] or "mobile")
            self._identity = ("device:" + dev, info)
        elif re.fullmatch(r"[0-9a-fA-F-]{8,64}", cid):
            info.update(kind="web", platform=info["platform"] or "web")
            self._identity = ("client:" + cid, info)
        else:
            self._identity = (None, info)
        return self._identity

    def admin_actor(self):
        """-> 'admin:<telegram id>' for a signature-verified moderator, 'admin:token' for the optional bearer token, else None."""
        cfg = self.cfg
        tg = self.headers.get("X-Telegram-Init-Data")
        if tg and cfg.bot_token and cfg.moderator_ids:
            user = verify_telegram(tg, cfg.bot_token, cfg.admin_session_s)
            if user and user["id"] in cfg.moderator_ids:
                return "admin:%d" % user["id"]
        if cfg.admin_token:
            tok = self.headers.get("Authorization", "")
            if hmac.compare_digest(tok.encode(), ("Bearer " + cfg.admin_token).encode()):
                return "admin:token"
        return None

    # -- entry points
    def do_GET(self): self.handle_any("GET")
    def do_HEAD(self): self.handle_any("GET")        # monitors/proxies/curl -I: same headers, no body
    def do_POST(self): self.handle_any("POST")
    def do_PATCH(self): self.handle_any("PATCH")
    def do_DELETE(self): self.handle_any("DELETE")
    def do_PUT(self): self.handle_any("PUT")
    def do_OPTIONS(self): self.handle_any("OPTIONS")

    def handle_any(self, method):
        t0 = time.monotonic()
        raw = self.headers.get("X-Request-Id", "")
        self.rid = raw.lower() if UUID_RE.fullmatch(raw.lower()) else str(uuid.uuid4())
        logs.set_rid(self.rid)
        self.status = 0
        self._identity = None
        self._consumed = False
        url = urlparse(self.path)
        path = url.path
        with self.state.busy():
            try:
                self.dispatch(method, path, dict(parse_qsl(url.query, keep_blank_values=True)))
            except ApiError as e:
                self.fail(e)
            except (PoolTimeout, psycopg.OperationalError, psycopg.errors.QueryCanceled) as e:   # busy or unreachable database: ask the client to retry
                if not isinstance(e, PoolTimeout):
                    log.error("database unavailable: %s", type(e).__name__)
                self.fail(ApiError(503, "service_unavailable", "service busy, retry in a moment"), {"Retry-After": "2"})
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                self.close_connection = True
            except Exception as e:  # noqa
                log.error("unhandled %s: %s", type(e).__name__, logs.mask(e, 200), exc_info=True)
                self.fail(ApiError(500, "internal", "Internal error"))
        self.finish_body()
        access.info("%s %s %d %dms", method if self.command != "HEAD" else "HEAD", logs.mask(path, 200), self.status, (time.monotonic() - t0) * 1000)
        logs.set_rid(None)

    def finish_body(self):
        """A body nobody read (an early 401/404/429 ...) would be taken for the next request on a kept-alive connection: read it or close."""
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except (ValueError, TypeError):
            n = 0
        if n > 0 and not self._consumed:
            if n <= MAX_BODY:
                try:
                    self.rfile.read(n)
                except Exception:  # noqa
                    self.close_connection = True
            else:
                self.close_connection = True

    def send_error(self, code, message=None, explain=None):
        """The stdlib's own refusals (bad request line, URI/header too large, unsupported method or version) as envelopes too."""
        self.rid = str(uuid.uuid4())
        self.close_connection = True
        if not getattr(self, "request_version", None) or not self.request_version.startswith("HTTP/1"):
            self.request_version = "HTTP/1.1"                  # a request the stdlib could not parse still gets a proper status line
        status, name, text = {414: (400, "uri_too_long", "request target too long"), 431: (400, "headers_too_large", "request headers too large"),
                              501: (405, "method_not_allowed", "method not allowed"), 505: (400, "bad_request", "unsupported HTTP version")}.get(
                                  code, (code if code in STD_CODES else 400, "bad_request", "bad request"))
        try:
            self.send_error_envelope(ApiError(status, name, text))
        except Exception:  # noqa
            pass

    def fail(self, e, headers=None):
        if self.status:                                  # the response already started: nothing more can be sent
            self.close_connection = True
            return
        if e.status in (400, 413, 415, 429) and self.content_length_safe():
            self.close_connection = True                 # the body may be left unread: do not reuse the connection
        h = dict(headers or {})
        if getattr(e, "allow", None):
            h["Allow"] = e.allow
        if e.status == 429:
            h["Retry-After"] = str(getattr(e, "retry_after", 1))
        elif getattr(e, "retry_after", None):
            h["Retry-After"] = str(e.retry_after)
        try:
            self.send_error_envelope(e, h)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def content_length_safe(self):
        try:
            return int(self.headers.get("Content-Length", "0")) > 0
        except ValueError:
            return True

    def dispatch(self, method, path, q):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            raise ApiError(400, "bad_request", "chunked bodies are not supported")
        limit = self.body_limit(method, path)
        if self.content_length() > limit:                    # before anything else
            if self.content_length() <= 1 << 20:
                self.rfile.read(self.content_length())       # read what was sent, so the client still receives the answer (not a reset)
            raise ApiError(413, "too_large", "request body is larger than %d KiB" % (limit // 1024))
        if method == "OPTIONS":
            return self.preflight()
        if path == API or path.startswith(API + "/"):
            return self.api(method, path[len(API):] or "/", q)
        if path.startswith(WEB + "/"):                   # (without the trailing slash relative URLs would break: not served)
            if method != "GET":
                raise ApiError(405, "method_not_allowed", "method not allowed")
            return self.static(path[len(WEB) + 1:] or "index.html")
        raise ApiError(404, "not_found", "no such endpoint")

    def body_limit(self, method, path):
        """64 KiB for JSON; the two reading routes document a larger raw body (a photo / a recording)."""
        if method == "POST" and path == API + "/timetable/photo":
            return self.cfg.max_photo_bytes
        if method == "POST" and path == API + "/timetable/voice":
            return self.cfg.max_voice_bytes
        return MAX_BODY

    def preflight(self):
        self.status = 204
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin and self.cors_ok(origin):
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", CORS_HEADERS)
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.common_headers()
        self.end_headers()

    # -- routing
    def routes(self):
        return [
            ("GET", r"/health", self.r_health),
            ("GET", r"/config", self.r_config),
            ("GET", r"/mosques", self.r_mosques),
            ("POST", r"/mosques", self.r_create),
            ("GET", r"/mosques/search", self.r_search),
            ("GET", r"/mosques/nearest", self.r_nearest),
            ("GET", r"/mosques/nearby", self.r_nearby),
            ("GET", r"/mosques/" + ID, self.r_get),
            ("PATCH", r"/mosques/" + ID, self.r_edit),
            ("GET", r"/mosques/" + ID + r"/history", self.r_history),
            ("POST", r"/mosques/" + ID + r"/reports", self.r_report),
            ("POST", r"/timetable/photo", self.r_read_photo),
            ("POST", r"/timetable/voice", self.r_read_voice),
            ("GET", r"/me", self.r_me),
            ("POST", r"/me/lang", self.r_me_lang),
            ("GET", r"/export/osm-derived", self.r_export),
            ("POST", r"/map/yandex-failed", self.r_map_failed),
            ("GET", r"/admin/map", self.a_map),
            ("POST", r"/admin/map/provider", self.a_map_provider),
            ("POST", r"/admin/map/keys", self.a_map_add),
            ("PATCH", r"/admin/map/keys/(\d{1,9})", self.a_map_update),
            ("POST", r"/admin/map/keys/(\d{1,9})/delete", self.a_map_delete),
            ("GET", r"/admin/overview", self.a_overview),
            ("GET", r"/admin/actors", self.a_actors),
            ("GET", r"/admin/mosques", self.a_mosques),
            ("GET", r"/admin/reports", self.a_reports),
            ("POST", r"/admin/mosques/" + ID + r"/(archive|restore|verify|dismiss)", self.a_moderate),
        ]

    def api(self, method, path, q):
        allowed = []
        for m, pattern, fn in self.routes():
            hit = re.fullmatch(pattern, path)
            if not hit:
                continue
            if m != method:
                allowed.append(m)
                continue
            if path.startswith("/admin/"):
                self.admin = self.require_admin()
            elif method in ("POST", "PATCH") and path not in LIGHT_WRITES and not self.state.write_limiter.allow(self.client_ip()):
                raise RateLimited(self.state.write_limiter.retry_after(self.client_ip()), "too many writes; slow down")
            return fn(q, *hit.groups())
        if allowed:
            err = ApiError(405, "method_not_allowed", "method not allowed")
            err.allow = ", ".join(sorted(set(allowed)))
            raise err
        raise ApiError(404, "not_found", "no such endpoint")

    def require_admin(self):
        ip = self.client_ip()
        if not self.state.admin_limiter.allow(ip, count=False):
            raise RateLimited(self.state.admin_limiter.retry_after(ip), "too many failed admin attempts")
        admin = self.admin_actor()
        if not admin:
            self.state.admin_limiter.allow(ip)             # count failures only
            raise ApiError(401, "unauthorized", "moderator access requires an allow-listed Telegram account or the admin token")
        return admin

    # -- public endpoints
    def r_health(self, q):
        db = "UP"
        try:
            self.store.ping()
        except Exception:
            db = "DOWN"
        status = "UP" if db == "UP" else "DOWN"
        data = {"status": status, "version": self.cfg.version, "commit": self.cfg.commit, "env": self.cfg.env, "db": db}
        if status == "DOWN":
            self.send_envelope(503, {"success": False, "httpStatus": 503, "code": "SERVICE_UNAVAILABLE",
                                     "message": {"code": "error.serviceUnavailable", "text": "Database unreachable", "reason": "db"}, "data": data})
        else:
            self.send_ok(data)

    def r_config(self, q):
        c = self.cfg
        self.send_ok({"style_url": c.tile_style_url, "duplicate_radius_m": c.dup_radius_m, "show_tile_mosques": c.show_tile_mosques,
                      "prayer": c.prayer, "admin_token_login": bool(c.admin_token), "bot_username": c.bot_username,
                      "photo_reading": gemini.available(c), "voice_reading": gemini.available(c),
                      "map": self.store.map_choice(self.identity()[0], self.map_kind())})

    def map_kind(self):
        """Only the mobile app may be handed a Yandex (MapKit) key; the browser and Telegram always draw OpenStreetMap."""
        return "app" if self.identity()[1].get("kind") == "mobile" else "web"

    def r_map_failed(self, q):
        kid = pick(self.body_json(), {"keyId": "key_id"}).get("key_id")
        if isinstance(kid, bool) or not isinstance(kid, int):
            raise ApiError(400, "validation", "keyId must be a whole number", reason="keyId")
        client = self.identity()[0]
        exhausted = self.store.map_report_failure(kid, client)
        self.send_ok({"exhausted": exhausted, "map": self.store.map_choice(client, self.map_kind())})       # the next key (or OSM) right away: no gap

    def r_mosques(self, q):
        if q.get("bbox"):
            try:
                bbox = tuple(float(x) for x in q["bbox"].split(","))
                w, s, e, n = bbox
                assert all(math.isfinite(x) for x in bbox) and -180 <= w <= e <= 180 and -90 <= s <= n <= 90
            except Exception:
                raise ApiError(400, "invalid_bbox", "bbox=minLon,minLat,maxLon,maxLat", reason="bbox") from None
            items, truncated = self.store.map_items(bbox)
            body = json.dumps(ok_body(200, {"items": items, "truncated": truncated}), ensure_ascii=False, separators=(",", ":"))
            etag = '"%s"' % hashlib.sha1(body.encode()).hexdigest()[:20]
            headers = {"Cache-Control": "public, max-age=60", "ETag": etag}
            if etag_matches(self.headers.get("If-None-Match"), etag):
                self.status = 304
                self.send_response(304)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.common_headers()
                self.end_headers()
                return
            return self.send_envelope(200, json.loads(body), headers)
        limit, cursor = page_args(q)
        items, nxt = self.store.list(limit, cursor)
        self.send_ok({"items": items, "next_cursor": enc_cursor(nxt) if nxt else None})

    def r_search(self, q):
        self.send_ok({"items": self.store.search(q.get("q")), "next_cursor": None})

    def r_nearest(self, q):
        lat, lon = clean_coord(q.get("lat"), q.get("lon"))
        limit, cursor = page_args(q)
        offset = int_cursor(cursor)
        items = self.store.nearest(lat, lon, limit + 1, offset=offset)
        more = len(items) > limit and offset + limit < Store.MAX_DEPTH
        self.send_ok({"items": items[:limit], "next_cursor": enc_cursor(offset + limit) if more else None})

    def r_nearby(self, q):
        lat, lon = clean_coord(q.get("lat"), q.get("lon"))
        try:
            radius = float(q.get("radius", self.cfg.dup_radius_m))
        except ValueError:
            raise ApiError(400, "validation", "radius must be a number", reason="radius") from None
        if not 0 < radius <= 500:
            raise ApiError(400, "validation", "radius must be between 0 and 500", reason="radius")
        self.send_ok({"items": self.store.nearby(lat, lon, radius), "next_cursor": None})

    def r_get(self, q, m_id):
        self.send_ok(self.store.get(m_id))

    def r_create(self, q):
        body = pick(self.body_json(), {"lat": "lat", "lon": "lon", "name": "name", "address": "address", "prayerTimes": "prayer_times", "force": "force"})
        self.send_ok(self.store.create(body, *self.identity()), 201)

    def r_edit(self, q, m_id):
        body = pick(self.body_json(), {"lat": "lat", "lon": "lon", "name": "name", "address": "address", "prayerTimes": "prayer_times", "note": "note"})
        self.send_ok(self.store.edit(m_id, body, *self.identity()))

    def r_history(self, q, m_id):
        limit, cursor = page_args(q)
        before = int_cursor(cursor) or None
        items, more = self.store.history(m_id, limit, before)
        self.send_ok({"items": items, "next_cursor": enc_cursor(items[-1]["seq"]) if more else None})

    def r_report(self, q, m_id):
        body = pick(self.body_json(), {"kind": "kind", "reason": "reason"})
        self.send_ok(self.store.report(m_id, body, *self.identity()), 201)

    def read_raw(self, kinds, limit, what):
        n = self.content_length()
        if n <= 0:
            raise ApiError(400, "bad_" + what, "a %s is required" % what)
        mime = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime not in kinds:
            raise ApiError(415, "unsupported_media_type", "Content-Type must be one of " + ", ".join(sorted(kinds)))
        actor, _ = self.identity()
        ip = self.client_ip()
        st = self.state
        if not st.read_ip_limiter.allow(ip) or not st.read_limiter.allow(actor or ip):
            raise RateLimited(st.read_limiter.retry_after(actor or ip), "too many readings; try again later")
        if not gemini.available(self.cfg):
            raise ApiError(503, "reader_unavailable", "reading is not available on this server")
        self._consumed = True
        return self.rfile.read(n), mime

    def reading(self, fn, data, mime):
        if not self.state.read_slots.acquire(blocking=False):
            raise ApiError(503, "reader_unavailable", "the reader is busy, retry in a moment")
        try:
            return fn(self.cfg, data, mime)
        except gemini.Unavailable as e:
            err = ApiError(503, "reader_unavailable", "the reader is busy or not reachable right now; retry later")
            err.retry_after = e.retry_after or 30
            raise err from None
        except gemini.Unreadable as e:
            raise ApiError(400, e.code, str(e)) from None
        finally:
            self.state.read_slots.release()

    def r_read_photo(self, q):
        data, mime = self.read_raw(gemini.IMAGE_TYPES, self.cfg.max_photo_bytes, "image")
        if not looks_like_image(data):
            raise ApiError(400, "bad_image", "not a supported image (JPEG, PNG or WebP)")
        self.send_ok(self.reading(gemini.read_photo, data, mime))

    def r_read_voice(self, q):
        data, mime = self.read_raw(gemini.AUDIO_TYPES, self.cfg.max_voice_bytes, "audio")
        if not looks_like_audio(data):
            raise ApiError(400, "bad_audio", "not a supported recording")
        self.send_ok(self.reading(gemini.read_voice, data, mime))

    def me_lang(self):
        """The language this (signature-verified) Telegram user chose in the bot, else None. Used by the Mini App."""
        actor, _ = self.identity()
        if not (actor or "").startswith("tg:"):
            return None
        return self.bdb.lang_of(int(actor[3:]))

    def r_me(self, q):
        self.send_ok({"lang": self.me_lang()})

    def r_me_lang(self, q):
        actor, _ = self.identity()
        code = pick(self.body_json(), {"lang": "lang"}).get("lang")
        if not (actor or "").startswith("tg:") or code not in LANGS:
            raise ApiError(400, "invalid_lang", "a verified Telegram user and a known language are required", reason="lang")
        self.bdb.set_lang(int(actor[3:]), code)
        self.send_ok({"lang": code})

    def r_export(self, q):
        data = self.store.export_osm()
        self.send_envelope(200, ok_body(200, data))        # GeoJSON keys are fixed by its standard: not camelized

    # -- moderation
    def a_map(self, q):
        self.send_ok(self.store.map_admin())

    def a_map_provider(self, q):
        self.store.map_set_provider(pick(self.body_json(), {"provider": "provider"}).get("provider"))
        self.send_ok(self.store.map_admin())

    def a_map_add(self, q):
        self.store.map_add_key(pick(self.body_json(), {"key": "key", "label": "label", "kind": "kind", "dailyLimit": "daily_limit"}))
        self.send_ok(self.store.map_admin(), 201)

    def a_map_update(self, q, key_id):
        self.store.map_update_key(int(key_id), pick(self.body_json(), {"enabled": "enabled", "dailyLimit": "daily_limit", "label": "label", "reset": "reset"}))
        self.send_ok(self.store.map_admin())

    def a_map_delete(self, q, key_id):
        self.store.map_delete_key(int(key_id))
        self.send_ok(self.store.map_admin())

    def a_overview(self, q):
        self.send_ok(self.store.overview())

    def a_actors(self, q):
        limit, _ = page_args(q, 50, 100)
        self.send_ok({"items": self.store.actors(q.get("q"), limit), "next_cursor": None})

    def a_mosques(self, q):
        limit, cursor = page_args(q, 50, 100)
        offset = int_cursor(cursor)
        r = self.store.admin_list(q.get("status", "reported"), limit, offset)
        self.send_ok({"items": r["items"], "next_cursor": enc_cursor(offset + limit) if r["more"] else None, "total": r["total"]})

    def a_reports(self, q):
        self.send_ok({"groups": self.store.reports(q.get("all") != "1")})

    def a_moderate(self, q, m_id, action):
        body = pick(self.body_json(), {"note": "note", "kind": "kind"})
        self.send_ok(self.store.moderate(m_id, action, self.admin, body.get("note"), body.get("kind") if action == "dismiss" else None))

    # -- the web client (`/masjiduz/web/...`)
    def web_headers(self):
        self.send_header("Content-Security-Policy", csp(self.cfg))
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "geolocation=(self)")

    def asset(self, f):
        """-> (raw bytes, gzip bytes | None, etag) of a web file; html files get content-hashed names for their scripts and styles."""
        st = f.stat()
        key = (str(f), st.st_mtime_ns, st.st_size)
        ent = STATIC_CACHE.get(str(f))
        if not ent or ent[0] != key:
            raw = f.read_bytes()
            if f.suffix == ".html":
                raw = self.hash_refs(raw.decode()).encode()
            gz = gzip.compress(raw, 9) if f.suffix in COMPRESSIBLE and len(raw) > 512 else None
            ent = (key, raw, gz, '"%s"' % hashlib.sha1(raw).hexdigest()[:20])
            STATIC_CACHE[str(f)] = ent
        return ent[1:]

    @staticmethod
    def hash_refs(html):
        def sub(m):
            rel = m.group(2) + "." + m.group(3)
            f = (WEB_DIR / rel).resolve()
            if WEB_DIR.resolve() in f.parents and f.is_file():
                return "%s%s.%s.%s%s" % (m.group(1), m.group(2), file_hash(f.read_bytes()), m.group(3), m.group(4))
            return m.group(0)
        return LOCAL_REF.sub(sub, html)

    def static(self, rel):
        rel = unquote(rel)
        if "\x00" in rel:
            raise ApiError(404, "not_found", "not found")
        if rel == "config.js":                                 # the API base, from the environment (no inline script is needed)
            body = ("window.MASJIDUZ_API_BASE=%s;\n" % json.dumps(self.cfg.api_base)).encode()
            return self.send_file(body, None, '"%s"' % hashlib.sha1(body).hexdigest()[:20], "application/javascript; charset=utf-8", "no-cache", rel)
        immutable = False
        m = HASHED.match(rel)
        if m and (WEB_DIR / (m.group(1) + "." + m.group(3))).is_file():
            plain = m.group(1) + "." + m.group(3)
            immutable = file_hash((WEB_DIR / plain).read_bytes()) == m.group(2)
            rel = plain
        try:
            f = (WEB_DIR / rel).resolve()
            ok = WEB_DIR.resolve() in f.parents and f.is_file()
        except (ValueError, OSError):
            ok = False
        if not ok:
            raise ApiError(404, "not_found", "not found")
        raw, gz, etag = self.asset(f)
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"                         # never leave the encoding to the browser's guess
        cache = "public, max-age=31536000, immutable" if immutable else "no-cache"      # hashed name: never changes; index.html and the rest: revalidate
        self.send_file(raw, gz, etag, ctype, cache, rel)

    def send_file(self, raw, gz, etag, ctype, cache, rel):
        same = etag_matches(self.headers.get("If-None-Match"), etag)
        self.status = 304 if same else 200
        self.send_response(self.status)
        self.send_header("Cache-Control", cache)
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        self.web_headers()
        self.common_headers()
        if rel.startswith("admin"):
            self.send_header("X-Robots-Tag", "noindex, nofollow")
        if same:
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = raw
        if gz is not None and self.wants_gzip():
            body = gz
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class State:
    """Per-server shared state: limiters, the CSP, and the count of requests in flight (for a graceful stop)."""
    def __init__(self, cfg):
        self.write_limiter = RateLimiter(cfg.rate_limit, 3600)         # writes per client address per hour
        self.read_limiter = RateLimiter(30, 3600)                      # timetable readings per device per hour
        self.read_ip_limiter = RateLimiter(120, 3600)                  # ... and per address (several people may share one)
        self.read_slots = threading.BoundedSemaphore(4)                # readings in flight at the same time
        self.admin_limiter = RateLimiter(10, 600)                      # failed admin logins per address per 10 minutes
        self.active, self.lock, self.idle = 0, threading.Lock(), threading.Condition()

    def busy(self):
        state = self

        class _Ctx:
            def __enter__(self):
                with state.idle:
                    state.active += 1

            def __exit__(self, *a):
                with state.idle:
                    state.active -= 1
                    state.idle.notify_all()
        return _Ctx()

    def wait_idle(self, seconds):
        end = time.monotonic() + seconds
        with self.idle:
            while self.active and time.monotonic() < end:
                self.idle.wait(0.2)
            return self.active == 0


def make_handler(cfg, store):
    state = State(cfg)

    class H(Request):
        pass
    H.cfg, H.store, H.bdb, H.state = cfg, store, botdb.BotDB(store), state
    return H


class Server(ThreadingHTTPServer):
    request_queue_size = 256         # the stdlib default is 5: a burst of simultaneous visitors would get connection resets
    daemon_threads = True

    def handle_error(self, request, client_address):
        """The stdlib would print the peer address and a traceback to stderr: log the kind of error only (no addresses)."""
        import sys as _sys
        log.error("connection error: %s", type(_sys.exc_info()[1]).__name__)


def build_server(cfg, store=None):
    store = store or Store(cfg)
    handler = make_handler(cfg, store)
    srv = Server((cfg.host, cfg.port), handler)
    srv.store, srv.state = store, handler.state
    return srv


def serve(cfg):
    store = Store(cfg)
    for w in cfg.warnings:
        log.warning("insecure setting: %s", w)
    if not cfg.moderator_ids and not cfg.admin_token:
        log.warning("no moderators configured: moderation is disabled for everyone")
    srv = build_server(cfg, store)

    def purge_loop():
        while True:
            try:
                n = store.purge_network_hashes()
                if n:
                    log.info("cleared %d network hashes older than 30 days", n)
            except Exception as e:  # noqa
                log.error("network hash purge failed: %s", type(e).__name__)
            time.sleep(6 * 3600)
    threading.Thread(target=purge_loop, daemon=True).start()

    def stop(signum, frame):
        log.info("SIGTERM: no new connections, finishing requests in flight")
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("serving %s v%s on %s:%d (env=%s)", API, cfg.version, cfg.host, cfg.port, cfg.env)
    srv.serve_forever()
    clean = srv.state.wait_idle(10)
    srv.server_close()
    store.close()
    log.info("stopped%s", "" if clean else " (some requests were still running after 10 s)")
    return 0
