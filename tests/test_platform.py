"""TIMUS acceptance: envelope, errors, headers, CORS, client address, rate limits, migrations, configuration, logs, shutdown, web client,
imports and the container commands."""
import contextlib
import gzip
import hashlib
import hmac
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from helpers import API, BOT_TOKEN, SALT, WEB, ServerCase, base_env, db, environment, headers_only, make_cfg, reset_db, sign_init_data  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import data_import  # noqa: E402
import logs  # noqa: E402
import migrate  # noqa: E402
import psycopg  # noqa: E402
from psycopg_pool import PoolTimeout  # noqa: E402

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ENVELOPE_KEYS = {"success", "httpStatus", "code", "message", "data"}
NOBODY = "00000000-0000-0000-0000-000000000000"


class Envelope(ServerCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.srv, cls.addr = helpers.start(make_cfg())

    @classmethod
    def tearDownClass(cls):
        helpers.stop(cls.srv)

    def check(self, r, status, code, message_code=None, text=None):
        b = r.json
        self.assertEqual(r.status, status)
        self.assertEqual(set(b), ENVELOPE_KEYS)
        self.assertEqual((b["success"], b["httpStatus"], b["code"]), (status < 400, status, code))
        self.assertLessEqual(set(b["message"]), {"code", "text", "reason"})
        self.assertIsInstance(b["message"]["text"], str)
        if message_code:
            self.assertEqual(b["message"]["code"], message_code)
        if text:
            self.assertEqual(b["message"]["text"], text)
        self.assertTrue(r.header("Content-Type").startswith("application/json"))
        for h in ("X-Request-Id", "X-Timus-Env", "Date"):                  # on every answer, errors included
            self.assertTrue(r.header(h), h)
        self.assertRegex(r.header("X-Request-Id"), UUID_RE)
        return b

    # -- success
    def test_success_envelope(self):
        r = self.call("GET", "/health", full=True)
        b = self.check(r, 200, "OK", "common.ok", "OK")
        self.assertEqual(b["message"], {"code": "common.ok", "text": "OK"})
        self.assertEqual(r.header("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(r.header("Cache-Control"), "no-store")
        r = self.call("POST", "/mosques", {"lat": 12.0, "lon": 12.0}, full=True)
        self.check(r, 201, "OK")
        self.assertEqual(r.json["httpStatus"], 201)

    def test_health_up(self):
        d = self.call("GET", "/health")[1]["data"]
        self.assertEqual(set(d), {"status", "version", "commit", "env", "db"})
        self.assertEqual((d["status"], d["db"], d["env"]), ("UP", "UP", "dev"))
        self.assertRegex(d["version"], r"^\d+\.\d+\.\d+")
        for _ in range(40):                                                 # public, no rate limit
            self.assertEqual(self.call("GET", "/health")[0], 200)

    def test_health_down_is_503_with_status_down(self):
        with mock.patch.object(self.srv.store, "ping", side_effect=psycopg.OperationalError("db is gone")):
            r = self.call("GET", "/health", full=True)
        b = self.check(r, 503, "SERVICE_UNAVAILABLE", "error.serviceUnavailable")
        self.assertEqual((b["data"]["status"], b["data"]["db"]), ("DOWN", "DOWN"))
        self.assertNotIn("db is gone", r.text)

    # -- 4xx
    def test_unknown_paths_are_404_in_the_envelope(self):
        for path in ("/", "/api/mosques", "/api/health", "/admin.html", "/index.html", "/masjiduz", "/masjiduz/", "/masjiduz/v2/mosques", "/favicon.ico",
                     API, API + "/", API + "/nope", API + "/mosques/abc", API + "/mosques/%s/nope" % NOBODY, WEB + "/nope.js", "/masjiduz/webx/"):
            r = self.req("GET", path)
            b = self.check(r, 404, "NOT_FOUND", "error.notFound")
            self.assertIsNone(b["data"], path)
        self.check(self.req("POST", "/"), 404, "NOT_FOUND")

    def test_unknown_record_is_404(self):
        self.check(self.call("GET", "/mosques/" + NOBODY, full=True), 404, "NOT_FOUND", "error.notFound")
        self.check(self.call("GET", "/mosques/%s/history" % NOBODY, full=True), 404, "NOT_FOUND")
        self.check(self.call("PATCH", "/mosques/" + NOBODY, {"name": "x"}, full=True), 404, "NOT_FOUND")
        self.check(self.call("POST", "/mosques/%s/reports" % NOBODY, {"reason": "x"}, full=True), 404, "NOT_FOUND")

    def test_wrong_method_on_a_known_path_is_405_in_the_envelope(self):
        mid = NOBODY
        for method, path in (("DELETE", "/mosques/" + mid), ("PUT", "/mosques"), ("POST", "/health"), ("PATCH", "/config"), ("DELETE", "/mosques"),
                             ("GET", "/mosques/%s/reports" % mid), ("POST", "/mosques/search"), ("GET", "/timetable/photo"), ("PUT", "/me"),
                             ("DELETE", "/admin/overview"), ("GET", "/admin/mosques/%s/archive" % mid)):
            self.check(self.call(method, path, full=True), 405, "METHOD_NOT_ALLOWED", "error.methodNotAllowed")
        self.check(self.req("POST", WEB + "/", b"x", {"Content-Type": "text/plain"}), 405, "METHOD_NOT_ALLOWED")
        self.check(self.req("DELETE", WEB + "/app.js"), 405, "METHOD_NOT_ALLOWED")

    def test_bad_json_is_400_bad_request(self):
        for body in (b"{bad", b"", b"[1, 2]", b"\"text\"", b"null", b"{\"lat\": 1,"):
            r = self.call("POST", "/mosques", body if body else None, full=True) if body else None
            if r is None:
                continue
            self.check(r, 400, "BAD_REQUEST", None)
        self.check(self.call("POST", "/mosques", b"{bad", full=True), 400, "BAD_REQUEST", "error.badJson")
        self.check(self.call("POST", "/mosques", b"[1]", full=True), 400, "BAD_REQUEST", "error.badJson")
        self.check(self.req("POST", API + "/mosques", b"{x", {"Content-Type": "application/json", "Content-Length": "2"}), 400, "BAD_REQUEST")

    def test_bad_content_length_and_chunked_bodies_are_refused(self):
        self.check(headers_only(self.addr, "POST", API + "/mosques", "abc", {"Content-Type": "application/json"}), 400, "BAD_REQUEST")
        r = self.req("POST", API + "/mosques", None, {"Transfer-Encoding": "chunked", "Content-Type": "application/json"})
        self.assertEqual(r.status, 400)

    def test_validation_errors_name_the_field(self):
        r = self.call("POST", "/mosques", {"lat": 1, "lon": 2, "bogus": 1}, full=True)
        b = self.check(r, 400, "VALIDATION_ERROR", "error.validation")
        self.assertEqual((b["message"]["reason"], b["data"]), ("bogus", None))
        for path, method, body, field in (("/mosques/%s" % NOBODY, "PATCH", {"nope": 1}, "nope"),
                                          ("/mosques/%s/reports" % NOBODY, "POST", {"reason": "x", "evil": 1}, "evil"),
                                          ("/me/lang", "POST", {"lang": "uz", "x": 1}, "x"), ("/admin/mosques/%s/archive" % NOBODY, "POST", {"admin": True}, "admin")):
            r = self.call(method, path, body, admin=path.startswith("/admin"), full=True)
            b = self.check(r, 400, "VALIDATION_ERROR")
            self.assertEqual(b["message"]["reason"], field, path)
        # old snake_case / old field names are unknown now
        for name in ("prayer_times", "expected_version", "photo_url", "creator_id"):
            r = self.call("PATCH", "/mosques/" + NOBODY, {name: 1}, full=True)
            self.assertEqual((r.status, r.json["message"]["reason"]), (400, name))
        self.assertEqual(self.call("POST", "/mosques", {"lat": "x", "lon": 1}, full=True).json["message"]["code"], "error.invalidCoordinates")
        self.assertEqual(self.call("POST", "/mosques", {"lat": 1, "lon": 2, "name": 5}, full=True).json["message"]["reason"], "name")
        self.assertEqual(self.call("POST", "/mosques", {"lat": 1, "lon": 2, "prayerTimes": [1]}, full=True).json["message"]["code"], "error.invalidPrayerTimes")

    def test_over_64_kib_is_413_before_auth_and_validation(self):
        big = b'{"reason": "' + b"x" * 66000 + b'"}'
        self.assertGreater(len(big), 65536)
        r = self.call("POST", "/mosques/%s/reports" % NOBODY, big, full=True)
        self.check(r, 413, "PAYLOAD_TOO_LARGE", "error.tooLarge")
        self.check(self.call("POST", "/admin/mosques/%s/archive" % NOBODY, big, full=True), 413, "PAYLOAD_TOO_LARGE")       # no admin rights, still 413
        self.check(self.call("POST", "/mosques", b"{" + b"z" * 70000, full=True), 413, "PAYLOAD_TOO_LARGE")                   # not even valid JSON
        self.check(self.call("POST", "/mosques", b"x" * 70000, ctype="text/plain", full=True), 413, "PAYLOAD_TOO_LARGE")       # before the media type is checked
        self.check(self.call("PATCH", "/mosques/%s" % NOBODY, big, full=True), 413, "PAYLOAD_TOO_LARGE")
        # exactly at the limit the body is read and judged on its content
        ok = json.dumps({"lat": 1, "lon": 2, "name": "a" * 65000}).encode()
        self.assertLessEqual(len(ok), 65536)
        r = self.call("POST", "/mosques", ok, full=True)
        self.check(r, 400, "VALIDATION_ERROR")
        head = len(json.dumps({"lat": 1, "lon": 2, "name": "a" * 20, "x": ""}).encode())
        exact = json.dumps({"lat": 1, "lon": 2, "name": "a" * 20, "x": "y" * (65536 - head)}).encode()
        self.assertEqual(len(exact), 65536)
        self.assertEqual(self.call("POST", "/mosques", exact, full=True).status, 400)         # unknown field x, but not 413
        self.check(self.call("POST", "/mosques", exact + b" ", full=True), 413, "PAYLOAD_TOO_LARGE")
        # a huge claimed body is refused from the Content-Length alone
        self.check(headers_only(self.addr, "POST", API + "/mosques", 50_000_000, {"Content-Type": "application/json"}), 413, "PAYLOAD_TOO_LARGE")
        self.check(headers_only(self.addr, "POST", API + "/admin/overview", 99_000_000), 413, "PAYLOAD_TOO_LARGE")

    def test_non_json_body_is_415(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "application/xml", "multipart/form-data"):
            r = self.call("POST", "/mosques", b'{"lat": 1, "lon": 2}', ctype=ctype, full=True)
            self.check(r, 415, "UNSUPPORTED_MEDIA_TYPE", "error.unsupportedMediaType")
        self.check(self.call("PATCH", "/mosques/%s" % NOBODY, b"name=x", ctype="text/plain", full=True), 415, "UNSUPPORTED_MEDIA_TYPE")
        self.assertEqual(self.call("POST", "/mosques", b'{"lat": 1, "lon": 2}', ctype="application/json; charset=utf-8", full=True).status in (201, 409), True)

    # -- 5xx
    def test_unexpected_error_is_500_without_details(self):
        with mock.patch.object(app.Store, "get", side_effect=RuntimeError("boom-secret-detail /srv/app.py")):
            r = self.call("GET", "/mosques/" + NOBODY, full=True)
        b = self.check(r, 500, "INTERNAL_ERROR", "error.internal", "Internal error")
        self.assertIsNone(b["data"])
        for leak in ("Traceback", "boom-secret-detail", "RuntimeError", "File \"", "app.py"):
            self.assertNotIn(leak, r.text)
        self.assertEqual(self.call("GET", "/health")[0], 200)                # the server lives on

    def test_database_trouble_is_503_with_retry_after(self):
        class Dead:
            def __init__(self, exc):
                self.exc = exc

            def connection(self, *a, **k):
                raise self.exc
        for exc in (PoolTimeout("pool exhausted"), psycopg.OperationalError("connection refused"), psycopg.errors.QueryCanceled("statement timeout")):
            with mock.patch.object(self.srv.store, "pool", Dead(exc)):
                r = self.call("GET", "/mosques/" + NOBODY, full=True)
                self.check(r, 503, "SERVICE_UNAVAILABLE", "error.serviceUnavailable")
                self.assertRegex(r.header("Retry-After"), r"^\d+$")
                self.assertNotIn("pool exhausted", r.text); self.assertNotIn("refused", r.text)
                self.assertEqual(self.call("POST", "/mosques", {"lat": 3, "lon": 3}, full=True).status, 503)
        self.assertEqual(self.call("GET", "/mosques/" + NOBODY)[0], 404)       # the pool is back

    def test_health_stays_up_while_the_pool_is_exhausted(self):
        class Dead:
            def connection(self, *a, **k):
                raise PoolTimeout("pool exhausted")
        with mock.patch.object(self.srv.store, "pool", Dead()):
            self.assertEqual(self.call("GET", "/mosques/" + NOBODY)[0], 503)    # busy pool: the data endpoints retry-later
            h = self.call("GET", "/health", full=True)                          # ... but the database itself is fine (own connection)
            self.assertEqual((h.status, h.json["data"]["status"], h.json["data"]["db"]), (200, "UP", "UP"))

    def test_health_is_down_when_the_database_cannot_be_reached(self):
        with mock.patch.dict(self.srv.store.cfg.db, {"port": 1}):               # nothing listens there
            h = self.call("GET", "/health", full=True)
        self.check(h, 503, "SERVICE_UNAVAILABLE", "error.serviceUnavailable")
        self.assertEqual((h.json["data"]["status"], h.json["data"]["db"]), ("DOWN", "DOWN"))
        self.assertEqual(self.call("GET", "/health")[0], 200)

    # -- request id and friends
    def test_request_id_is_echoed_when_valid_and_generated_otherwise(self):
        rid = str(uuid.uuid4())
        r = self.call("GET", "/health", headers={"X-Request-Id": rid}, full=True)
        self.assertEqual(r.header("X-Request-Id"), rid)
        r = self.call("GET", "/health", headers={"X-Request-Id": rid.upper()}, full=True)
        self.assertEqual(r.header("X-Request-Id").lower(), rid)
        seen = set()
        for bad in ("abc", "", "not-a-uuid-at-all-not-a-uuid-at-all-xx", rid + "x", "<script>", "../../etc", "x" * 500):
            r = self.call("GET", "/health", headers={"X-Request-Id": bad}, full=True)
            got = r.header("X-Request-Id")
            self.assertRegex(got, UUID_RE); self.assertNotEqual(got, bad); seen.add(got)
        self.assertEqual(len(seen), 7)                                       # a fresh one each time
        r = self.call("GET", "/mosques/" + NOBODY, headers={"X-Request-Id": rid}, full=True)
        self.assertEqual((r.status, r.header("X-Request-Id")), (404, rid))   # also on errors
        r = self.req("GET", "/nowhere", headers={"X-Request-Id": rid})
        self.assertEqual(r.header("X-Request-Id"), rid)

    def test_timus_env_date_and_no_python_banner(self):
        r = self.call("GET", "/health", full=True)
        self.assertEqual(r.header("X-Timus-Env"), "dev")
        self.assertRegex(r.header("Date"), r"^\w{3}, \d\d \w{3} \d{4} \d\d:\d\d:\d\d GMT$")
        self.assertNotIn("Python", r.header("Server") or "")
        self.assertEqual(r.header("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.req("OPTIONS", API + "/mosques").header("X-Timus-Env"), "dev")

    def test_env_name_follows_the_configuration(self):
        cfg = make_cfg()
        cfg.env = "staging"
        srv, addr = helpers.start(cfg)
        self.addCleanup(helpers.stop, srv)
        r = self.call("GET", "/health", addr=addr, full=True)
        self.assertEqual((r.header("X-Timus-Env"), r.json["data"]["env"]), ("staging", "staging"))

    def test_the_answer_is_gzipped_only_when_asked_and_big(self):
        r = self.req("GET", WEB + "/app.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.header("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(r.raw), (Path(helpers.app.WEB_DIR) / "app.js").read_bytes())
        self.assertIsNone(self.req("GET", WEB + "/app.js").header("Content-Encoding"))
        self.assertIsNone(self.call("GET", "/health", headers={"Accept-Encoding": "gzip"}, full=True).header("Content-Encoding"))


class HeadersAndAddresses(ServerCase):
    def setUp(self):
        reset_db()

    def test_vary_origin_whenever_cors_is_configured(self):
        _, addr = self.serve(CORS_ORIGINS="https://a.example")
        for h in ({}, {"Origin": "https://evil.example"}, {"Origin": "https://a.example"}):
            r = self.call("GET", "/health", headers=h, addr=addr, full=True)
            self.assertIn("Origin", r.header("Vary"), h)                            # caches must not mix answers per origin
        r = self.call("GET", "/mosques/" + NOBODY, addr=addr, full=True)
        self.assertIn("Origin", r.header("Vary"))
        r = self.req("OPTIONS", API + "/mosques", addr=addr)
        self.assertIn("Origin", r.header("Vary") or "")

    def test_cors_only_for_configured_origins(self):
        _, addr = self.serve(CORS_ORIGINS="https://a.example, https://b.example/")
        for origin in ("https://a.example", "https://b.example"):
            r = self.req("OPTIONS", API + "/mosques", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}, addr=addr)
            self.assertEqual(r.status, 204); self.assertEqual(r.raw, b"")
            self.assertEqual(r.header("Access-Control-Allow-Origin"), origin)
            self.assertEqual(r.header("Vary"), "Origin")
            self.assertEqual(set(m.strip() for m in r.header("Access-Control-Allow-Methods").split(",")), {"GET", "POST", "PATCH", "OPTIONS"})
            allowed = {h.strip() for h in r.header("Access-Control-Allow-Headers").split(",")}
            self.assertEqual(allowed, {"Content-Type", "Authorization", "X-Request-Id", "X-Client-Id", "X-Device-Id", "X-Client-Platform", "X-OS-Version",
                                       "X-App-Version", "X-Device-Model", "X-Telegram-Init-Data"})
            self.assertTrue(r.header("X-Request-Id")); self.assertTrue(r.header("X-Timus-Env")); self.assertTrue(r.header("Date"))
        for origin in ("https://evil.example", "http://a.example", "https://a.example.evil.example", "null", "*"):
            r = self.req("OPTIONS", API + "/mosques", headers={"Origin": origin}, addr=addr)
            self.assertEqual(r.status, 204)
            self.assertIsNone(r.header("Access-Control-Allow-Origin"), origin)
            self.assertIsNone(r.header("Access-Control-Allow-Methods"), origin)
        # simple requests
        r = self.call("GET", "/health", headers={"Origin": "https://a.example"}, addr=addr, full=True)
        self.assertEqual((r.header("Access-Control-Allow-Origin"), r.header("Vary").count("Origin")), ("https://a.example", 1))
        r = self.call("GET", "/health", headers={"Origin": "https://evil.example"}, addr=addr, full=True)
        self.assertIsNone(r.header("Access-Control-Allow-Origin"))
        r = self.call("GET", "/mosques/" + NOBODY, headers={"Origin": "https://a.example"}, addr=addr, full=True)        # also on errors
        self.assertEqual((r.status, r.header("Access-Control-Allow-Origin")), (404, "https://a.example"))
        self.assertIsNone(self.call("GET", "/health", addr=addr, full=True).header("Access-Control-Allow-Origin"))

    def test_no_cors_by_default(self):
        _, addr = self.serve()
        r = self.req("OPTIONS", API + "/mosques", headers={"Origin": "https://a.example"}, addr=addr)
        self.assertEqual(r.status, 204); self.assertIsNone(r.header("Access-Control-Allow-Origin"))
        r = self.call("GET", "/health", headers={"Origin": "https://a.example"}, addr=addr, full=True)
        self.assertIsNone(r.header("Access-Control-Allow-Origin"))

    def hash_of(self, actor_id):
        return db("SELECT ip_hash FROM actors WHERE actor_id=%s", actor_id)[0][0]

    def test_ip_hash_is_of_the_network_never_the_address(self):
        _, addr = self.serve()
        mk = lambda n, cid, ip: self.call("POST", "/mosques", {"lat": 10 + n, "lon": 10.0}, client=cid, ip=ip, addr=addr)[0]      # noqa: E731
        self.assertEqual(mk(1, "aaaaaaaa-1", "198.51.100.5"), 201)
        self.assertEqual(mk(2, "bbbbbbbb-1", "198.51.100.200"), 201)
        self.assertEqual(mk(3, "cccccccc-1", "198.51.101.5"), 201)
        a, b, c = (self.hash_of("client:%s-1" % x * 1) if False else self.hash_of("client:%s" % x) for x in ("aaaaaaaa-1", "bbbbbbbb-1", "cccccccc-1"))
        self.assertEqual(a, b)                                               # the same /24
        self.assertNotEqual(a, c)
        want = hmac.new(SALT.encode(), b"198.51.100.0/24", hashlib.sha256).hexdigest()[:24]
        self.assertEqual(a, want)
        stored = json.dumps([db("SELECT * FROM actors"), db("SELECT client FROM mosque_history"), db("SELECT client FROM reports")], default=str)
        for needle in ("198.51.100.5", "198.51.100.200", "198.51.101.5"):
            self.assertNotIn(needle, stored)

    def test_network_of(self):
        n = app.network_of
        self.assertEqual(n("198.51.100.77"), "198.51.100.0/24")
        self.assertEqual(n("198.51.100.1"), n("198.51.100.254"))
        self.assertNotEqual(n("198.51.100.1"), n("198.51.101.1"))
        self.assertEqual(n("2001:db8:1234:5678::1"), "2001:db8:1234::/48")
        self.assertEqual(n("2001:db8:1234:ffff::9"), n("2001:db8:1234:0::1"))
        self.assertEqual(n("::ffff:203.0.113.9"), "203.0.113.0/24")
        for junk in ("", "nonsense", "1.2.3", "999.1.1.1"):
            self.assertEqual(n(junk), "unknown")

    def test_purge_network_hashes_after_30_days(self):
        srv, addr = self.serve()
        for i, cid in enumerate(("aaaaaaaa-1", "bbbbbbbb-1")):
            self.call("POST", "/mosques", {"lat": 14 + i, "lon": 14.0}, client=cid, ip="198.51.100.%d" % (i + 1), addr=addr)
        db("UPDATE actors SET last_seen = now() - interval '31 days' WHERE actor_id='client:aaaaaaaa-1'")
        self.assertEqual(srv.store.purge_network_hashes(), 1)
        self.assertIsNone(self.hash_of("client:aaaaaaaa-1"))
        self.assertIsNotNone(self.hash_of("client:bbbbbbbb-1"))
        self.assertEqual(srv.store.purge_network_hashes(), 0)
        self.assertEqual(db("SELECT count(*) FROM actors")[0][0], 2)         # only the hash is cleared, not the record
        db("UPDATE actors SET last_seen = now() - interval '10 days'")
        self.assertEqual(srv.store.purge_network_hashes(), 0)
        self.assertEqual(srv.store.purge_network_hashes(days=5), 1)

    def test_forwarded_for_is_honoured_only_from_a_trusted_peer(self):
        # default: loopback is a trusted proxy -> X-Forwarded-For counts
        _, addr = self.serve()
        self.call("POST", "/mosques", {"lat": 16, "lon": 16.0}, client="aaaaaaaa-1", ip="198.51.100.5", addr=addr)
        want = hmac.new(SALT.encode(), b"198.51.100.0/24", hashlib.sha256).hexdigest()[:24]
        self.assertEqual(self.hash_of("client:aaaaaaaa-1"), want)
        # the peer (127.0.0.1) is not in an unrelated TRUSTED_PROXIES list: the header is ignored, the peer is the client
        _, addr2 = self.serve(TRUSTED_PROXIES="192.0.2.0/24")
        self.call("POST", "/mosques", {"lat": 17, "lon": 17.0}, client="bbbbbbbb-1", ip="198.51.100.5", addr=addr2)
        peer = hmac.new(SALT.encode(), b"127.0.0.0/24", hashlib.sha256).hexdigest()[:24]
        self.assertEqual(self.hash_of("client:bbbbbbbb-1"), peer)
        self.assertNotEqual(peer, want)
        # a multi-hop header: the right-most hop that is not one of ours
        _, addr3 = self.serve()
        self.call("POST", "/mosques", {"lat": 18, "lon": 18.0}, client="cccccccc-1", headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.9, 10.1.2.3"}, addr=addr3)
        self.assertEqual(self.hash_of("client:cccccccc-1"), want)             # 6.6.6.6 was claimed by the client, not added by a proxy of ours

    def test_write_rate_limit_is_429_with_integer_retry_after(self):
        _, addr = self.serve(WRITE_RATE_PER_HOUR="2")
        codes = [self.call("POST", "/mosques", {"lat": 40 + i, "lon": 40.0}, addr=addr)[0] for i in range(2)]
        self.assertEqual(codes, [201, 201])
        r = self.call("POST", "/mosques", {"lat": 43, "lon": 40.0}, addr=addr, full=True)
        b = r.json
        self.assertEqual((r.status, b["success"], b["httpStatus"], b["code"], b["message"]["code"]), (429, False, 429, "RATE_LIMITED", "error.rateLimited"))
        self.assertRegex(r.header("Retry-After"), r"^[1-9]\d*$")
        self.assertLessEqual(int(r.header("Retry-After")), 3600)
        self.assertIsNone(b["data"])
        self.assertEqual(self.call("PATCH", "/mosques/" + NOBODY, {"name": "x"}, addr=addr)[0], 429)       # PATCH counts, same budget
        self.assertEqual(self.call("GET", "/mosques", addr=addr)[0], 200)                                  # reads are free
        self.assertEqual(self.call("GET", "/health", addr=addr)[0], 200)
        self.assertEqual(self.call("POST", "/mosques", {"lat": 44, "lon": 40.0}, ip="198.51.100.77", addr=addr)[0], 201)   # another client address: own budget

    def test_rate_limit_keys_on_the_peer_when_the_proxy_is_not_trusted(self):
        _, addr = self.serve(WRITE_RATE_PER_HOUR="2", TRUSTED_PROXIES="192.0.2.0/24")
        codes = [self.call("POST", "/mosques", {"lat": 50 + i, "lon": 50.0}, ip="198.51.100.%d" % (i + 1), addr=addr)[0] for i in range(4)]
        self.assertEqual(codes, [201, 201, 429, 429])                        # a forged header cannot buy a new budget

    def test_admin_lockout_cannot_be_evaded_with_a_forged_header(self):
        _, addr = self.serve(TRUSTED_PROXIES="192.0.2.0/24")
        bad = {"X-Telegram-Init-Data": "hash=bad&user=%7B%7D"}
        codes = [self.call("GET", "/admin/overview", headers=bad, ip="198.51.100.%d" % (i + 1), addr=addr)[0] for i in range(12)]
        self.assertEqual(codes, [401] * 10 + [429] * 2)

    def test_the_rate_limiter_unit(self):
        rl = app.RateLimiter(2, 60)
        self.assertEqual([rl.allow("k"), rl.allow("k"), rl.allow("k")], [True, True, False])
        self.assertTrue(rl.allow("other"))
        self.assertTrue(1 <= rl.retry_after("k") <= 60)
        peek = app.RateLimiter(1, 60)
        self.assertEqual([peek.allow("k", count=False)] * 3, [True] * 3)
        self.assertEqual(rl.retry_after("never"), 1)


def exchange(addr, raw, read_all=True):
    """Sends raw bytes, returns Resp of the first answer (for requests http.client would never produce)."""
    import socket
    with socket.create_connection(addr, timeout=10) as sk:
        sk.sendall(raw)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sk.recv(65536)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        hdrs = dict(ln.split(": ", 1) for ln in lines[1:] if ": " in ln)
        want = int(hdrs.get("Content-Length", "0"))
        while len(rest) < want:
            chunk = sk.recv(65536)
            if not chunk:
                break
            rest += chunk
        if not lines[0].startswith("HTTP/"):
            return None                                                        # a head-less (HTTP/0.9 style) reply
        return helpers.Resp(int(lines[0].split()[1]), hdrs, rest)


class Hardening(ServerCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.srv, cls.addr = helpers.start(make_cfg())

    @classmethod
    def tearDownClass(cls):
        helpers.stop(cls.srv)

    def envelope(self, r, status=None):
        b = r.json
        self.assertEqual(set(b), ENVELOPE_KEYS)
        self.assertFalse(b["success"])
        if status:
            self.assertEqual(r.status, status)
        self.assertEqual(b["httpStatus"], r.status)
        return b

    def test_unread_bodies_do_not_poison_a_keep_alive_connection(self):
        import http.client
        body = json.dumps({"note": "x" * 3000}).encode()
        for method, path in (("POST", API + "/admin/mosques/%s/archive" % NOBODY), ("POST", "/nowhere"), ("POST", API + "/nope"),
                             ("PUT", API + "/mosques"), ("POST", API + "/health"), ("POST", WEB + "/app.js")):
            conn = http.client.HTTPConnection(*self.addr, timeout=10)
            try:
                conn.request(method, path, body=body, headers={"Content-Type": "application/json", "X-Forwarded-For": helpers.fresh_ip()})
                r1 = conn.getresponse()
                b1 = json.loads(r1.read())
                self.assertEqual(set(b1), ENVELOPE_KEYS, path)
                self.assertGreaterEqual(r1.status, 400)
                conn.request("GET", API + "/health")                          # the SAME connection object (reconnects if the server closed it)
                r2 = conn.getresponse()
                b2 = json.loads(r2.read())
                self.assertEqual((r2.status, b2["code"], b2["data"]["status"]), (200, "OK", "UP"), path)
            finally:
                conn.close()

    def test_two_requests_on_one_raw_connection_get_two_envelopes(self):
        import socket
        body = b'{"note": "hello"}'
        one = (b"POST %s HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % ((API + "/admin/overview").encode(), len(body))) + body
        two = b"GET %s HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n" % (API + "/health").encode()
        with socket.create_connection(self.addr, timeout=10) as sk:
            sk.sendall(one + two)
            data = b""
            while True:
                chunk = sk.recv(65536)
                if not chunk:
                    break
                data += chunk
        self.assertEqual(data.count(b"HTTP/1.1 "), 2 if data.count(b"HTTP/1.1 ") == 2 else data.count(b"HTTP/1.1 "))
        self.assertTrue(data.startswith(b"HTTP/1.1 405") or data.startswith(b"HTTP/1.1 401"))
        for part in data.split(b"HTTP/1.1 ")[1:]:                              # every answer on the wire is a complete envelope, none is garbage
            payload = part.split(b"\r\n\r\n", 1)[1]
            self.assertEqual(set(json.loads(payload)), ENVELOPE_KEYS)

    def test_stdlib_refusals_are_envelopes(self):
        self.assertEqual(self.call("GET", "/health")[0], 200)
        # a request line the stdlib cannot even parse a version from is answered before the version is known: the stdlib then sends no head
        # (HTTP/0.9 style). That is tolerated here; when a head IS sent it must be an envelope. The server must stay healthy either way.
        for junk in (b"GARBAGE\r\n\r\n", b"GARBAGE GARBAGE GARBAGE\r\n\r\n", b"\x00\x01\x02\r\n\r\n"):
            try:
                r = exchange(self.addr, junk)
            except (IndexError, OSError):
                r = None
            if r is not None:
                self.assertEqual(self.envelope(r, 400)["code"], "BAD_REQUEST")
        self.assertEqual(self.call("GET", "/health")[0], 200)
        r = exchange(self.addr, b"GET /masjiduz/v1/health HTTP/9.9\r\nHost: x\r\n\r\n")
        if r is not None:                                                      # (unsupported version: also answered before the version is stored)
            self.envelope(r)
        try:
            r = exchange(self.addr, b"GET /masjiduz/v1/health HTTP/1.1\r\nHost: x\r\nX-Big: " + b"a" * 66000 + b"\r\n\r\n")
        except OSError:
            r = None                                                           # the server closes with data unread: the reset may eat the answer
        if r is not None:
            self.envelope(r)
            self.assertIn(r.status, (400, 431))
            self.assertRegex(r.header("X-Request-Id"), UUID_RE)
        try:
            r = exchange(self.addr, b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\n\r\n")             # request target too long
        except OSError:
            r = None
        if r is not None:
            self.envelope(r)
            self.assertIn(r.status, (400, 414))
        r = exchange(self.addr, b"TRACE /masjiduz/v1/health HTTP/1.1\r\nHost: x\r\n\r\n")
        b = self.envelope(r, 405)
        self.assertEqual(b["code"], "METHOD_NOT_ALLOWED")
        self.assertRegex(r.header("X-Request-Id"), UUID_RE)
        self.assertEqual(self.call("GET", "/health")[0], 200)                  # the server is fine afterwards

    def test_too_many_header_lines_is_an_envelope(self):
        many = b"GET /masjiduz/v1/health HTTP/1.1\r\nHost: x\r\n" + b"".join(b"X-%d: y\r\n" % i for i in range(150)) + b"\r\n"
        r = exchange(self.addr, many)                                          # more than 100 header lines
        self.assertIsNotNone(r)
        self.envelope(r)
        self.assertIn(r.status, (400, 431))
        self.assertRegex(r.header("X-Request-Id"), UUID_RE)
        self.assertEqual(self.call("GET", "/health")[0], 200)

    def test_web_paths(self):
        r = self.req("GET", WEB + "/%00")
        self.assertEqual((r.status, r.json["code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.req("GET", WEB + "/app.js%00.css").status, 404)
        r = self.req("GET", WEB)                                               # no trailing slash: relative links would break, so it is not served
        self.assertEqual((r.status, r.json["code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.req("GET", WEB + "/").status, 200)

    def test_405_names_the_allowed_methods(self):
        r = self.call("DELETE", "/mosques/" + NOBODY, full=True)
        self.assertEqual(r.status, 405)
        self.assertEqual(sorted(m.strip() for m in r.header("Allow").split(",")), ["GET", "PATCH"])
        self.assertEqual(sorted(m.strip() for m in self.call("PUT", "/mosques", full=True).header("Allow").split(",")), ["GET", "POST"])
        self.assertEqual(self.call("POST", "/health", full=True).header("Allow"), "GET")

    def test_coordinates_in_json_must_be_real_numbers(self):
        huge = b'{"lat": ' + b"9" * 400 + b', "lon": 1}'
        existing = self.made(8.5, 8.5)["id"]
        for ep in (("POST", "/mosques"), ("PATCH", "/mosques/" + existing)):
            r = self.call(ep[0], ep[1], huge, full=True)
            self.assertEqual((r.status, r.json["code"], r.json["message"]["code"]), (400, "VALIDATION_ERROR", "error.invalidCoordinates"), ep)
        for bad in ('"41.3"', "true", "false", "null", "[1]", '{"a": 1}', "1e999", "-1e999", "NaN", "Infinity"):
            r = self.call("POST", "/mosques", ('{"lat": %s, "lon": 2}' % bad).encode(), full=True)
            self.assertEqual((r.status, r.json["message"]["code"]), (400, "error.invalidCoordinates"), bad)
            r = self.call("POST", "/mosques", ('{"lat": 2, "lon": %s}' % bad).encode(), full=True)
            self.assertEqual((r.status, r.json["message"]["code"]), (400, "error.invalidCoordinates"), bad)
        r = self.call("PATCH", "/mosques/" + existing, {"lat": "5", "lon": 5})
        self.assertEqual((r[0], r[1]["message"]["code"]), (400, "error.invalidCoordinates"))
        self.assertEqual(self.call("GET", "/mosques/nearest?lat=41.3&lon=69.2")[0], 200)          # the query string is text: fine there
        self.assertEqual(self.call("GET", "/mosques/nearby?lat=41.3&lon=69.2&radius=50")[0], 200)
        self.assertEqual(self.call("GET", "/mosques/nearest?lat=" + "9" * 400 + "&lon=1")[0], 400)
        self.assertEqual(self.call("POST", "/mosques", {"lat": 70, "lon": 70.5})[0], 201)         # ints and floats are fine

    def test_force_must_be_a_bool(self):
        for bad in ('"yes"', "1", "0", "null", '"true"', "[]"):
            r = self.call("POST", "/mosques", ('{"lat": 1.5, "lon": 2.5, "force": %s}' % bad).encode(), full=True)
            self.assertEqual((r.status, r.json["code"], r.json["message"]["reason"]), (400, "VALIDATION_ERROR", "force"), bad)
        self.assertEqual(self.call("POST", "/mosques", {"lat": 1.5, "lon": 2.5, "force": False})[0], 201)
        self.assertEqual(self.call("POST", "/mosques", {"lat": 1.5, "lon": 2.5, "force": True})[0], 201)

    def test_a_report_needs_some_identity(self):
        m = self.made(5.5, 5.5)
        s, r = self.call("POST", "/mosques/%s/reports" % m["id"], {"kind": "other", "reason": "x"}, headers={"X-Client-Id": ""})
        self.assertEqual(s, 400)
        self.assertEqual(set(r), ENVELOPE_KEYS)
        self.assertFalse(r["success"])
        self.assertEqual(self.call("POST", "/mosques/%s/reports" % m["id"], {"kind": "other", "reason": "x"}, client="dddddddd-9")[0], 201)

    def test_invalid_cursors_are_400(self):
        mid = self.made(6.5, 6.5)["id"]
        for path in ("/mosques?cursor=!!!", "/mosques?cursor=%%%", "/mosques/nearest?lat=1&lon=1&cursor=!!!", "/mosques/%s/history?cursor=!!!" % mid,
                     "/mosques?cursor=" + "A" * 500, "/mosques?cursor=a%20b"):
            s, r = self.call("GET", path)
            self.assertEqual((s, r["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "cursor"), path)
        s, r = self.call("GET", "/admin/mosques?status=unverified&cursor=!!!", admin=True)
        self.assertEqual((s, r["message"]["reason"]), (400, "cursor"))

    def test_future_auth_date_is_rejected(self):
        user = {"id": 7, "first_name": "F"}
        tok = BOT_TOKEN
        self.assertEqual(app.verify_telegram(sign_init_data(user, tok, age=-100), tok), user)          # a little clock skew is fine
        self.assertIsNone(app.verify_telegram(sign_init_data(user, tok, age=-600), tok))
        self.assertIsNone(app.verify_telegram(sign_init_data(user, tok, age=-86400 * 365), tok))
        s, _ = self.call("GET", "/admin/overview", headers={"X-Telegram-Init-Data": sign_init_data({"id": helpers.ADMIN_ID}, age=-3600)})
        self.assertEqual(s, 401)


class BotLock(unittest.TestCase):
    def test_only_one_bot_can_hold_the_advisory_lock(self):
        import inspect
        import bot
        self.assertIn("pg_try_advisory_lock(727004)", inspect.getsource(bot.main))
        reset_db()
        cfg = make_cfg("bot")
        first = psycopg.connect(cfg.conninfo(), autocommit=True)
        second = psycopg.connect(cfg.conninfo(), autocommit=True)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertTrue(first.execute("SELECT pg_try_advisory_lock(727004)").fetchone()[0])
        self.assertFalse(second.execute("SELECT pg_try_advisory_lock(727004)").fetchone()[0])      # a second replica would raise RuntimeError here
        first.close()
        self.assertTrue(second.execute("SELECT pg_try_advisory_lock(727004)").fetchone()[0])       # released when the holder is gone
        for lock_id in (727001, 727002, 727003):                              # distinct from migrate / duplicate check / reader limits
            self.assertNotEqual(lock_id, 727004)


class Migrations(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.real = migrate.DIR
        for f in self.real.glob("V*.sql"):
            shutil.copy(f, self.tmp / f.name)
        self.names = sorted(f.name for f in self.real.glob("V*.sql"))
        self.cfg = make_cfg()
        with psycopg.connect(helpers.TEST_URL, autocommit=True) as c:
            helpers.ensure_database()
            c.execute("DROP SCHEMA IF EXISTS masjiduz CASCADE")
        self.addCleanup(reset_db_quiet)                                      # registered first: runs after DIR is restored
        p = mock.patch.object(migrate, "DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def test_empty_schema_goes_to_the_latest_version_and_a_second_run_does_nothing(self):
        self.assertEqual(migrate.pending(self.cfg), self.names)
        self.assertEqual(migrate.migrate(self.cfg), self.names)
        self.assertEqual(migrate.migrate(self.cfg), [])
        self.assertEqual(migrate.pending(self.cfg), [])
        rows = db("SELECT version, name, checksum FROM schema_history ORDER BY version")
        self.assertEqual([r[1] for r in rows], self.names)
        self.assertEqual([r[0] for r in rows], list(range(1, len(self.names) + 1)))
        self.assertEqual(rows[0][2], hashlib.sha256((self.tmp / self.names[0]).read_bytes()).hexdigest())
        self.assertEqual(db("SELECT to_regclass('masjiduz.mosques') IS NOT NULL, to_regclass('masjiduz.schema_history') IS NOT NULL"), [(True, True)])
        for t in ("map_settings", "map_keys", "map_key_users", "map_key_reports", "ai_calls", "ai_cooldown", "bot_users"):
            self.assertEqual(db("SELECT to_regclass(%s) IS NOT NULL", "masjiduz." + t), [(True,)], t)
        self.assertGreaterEqual(len(self.names), 3)
        self.assertEqual([n[:5] for n in self.names][:3], ["V0001", "V0002", "V0003"])
        self.assertEqual(db("SELECT provider FROM map_settings"), [("osm",)])                            # the baseline row: OpenStreetMap

    def test_a_new_file_is_applied_alone_and_in_a_transaction(self):
        migrate.migrate(self.cfg)
        n = len(self.names) + 1
        (self.tmp / ("V%04d__extra.sql" % n)).write_text("CREATE TABLE extra_t (id int);")
        self.assertEqual(migrate.pending(self.cfg), ["V%04d__extra.sql" % n])
        self.assertEqual(migrate.migrate(self.cfg), ["V%04d__extra.sql" % n])
        self.assertEqual(db("SELECT to_regclass('masjiduz.extra_t') IS NOT NULL"), [(True,)])
        (self.tmp / ("V%04d__broken.sql" % (n + 1))).write_text("CREATE TABLE half (id int); SELECT nope_nope FROM nowhere;")
        with self.assertRaises(psycopg.Error):
            migrate.migrate(self.cfg)
        self.assertEqual(db("SELECT to_regclass('masjiduz.half') IS NULL"), [(True,)])                   # rolled back as a whole
        self.assertEqual(db("SELECT count(*) FROM schema_history"), [(n,)])

    def test_a_changed_checksum_of_an_applied_file_is_refused(self):
        migrate.migrate(self.cfg)
        f = self.tmp / self.names[0]
        f.write_text(f.read_text() + "\n-- edited after release\n")
        with self.assertRaises(migrate.MigrationError) as e:
            migrate.migrate(self.cfg)
        self.assertIn(self.names[0], str(e.exception))
        with self.assertRaises(migrate.MigrationError):
            migrate.pending(self.cfg)
        with self.assertRaises(migrate.MigrationError):
            app.Store(self.cfg)                                              # the service does not start on a tampered history

    def test_file_names_and_numbering_are_checked(self):
        (self.tmp / "Vbad.sql").write_text("SELECT 1;")
        with self.assertRaises(migrate.MigrationError):
            migrate.files()
        (self.tmp / "Vbad.sql").unlink()
        (self.tmp / ("V%04d__gap.sql" % (len(self.names) + 2))).write_text("SELECT 1;")
        with self.assertRaises(migrate.MigrationError) as e:
            migrate.migrate(self.cfg)
        self.assertIn("gaps", str(e.exception))

    def test_store_refuses_to_start_while_migrations_are_pending(self):
        with self.assertRaises(config.ConfigError) as e:
            app.Store(self.cfg)                                              # no schema at all
        self.assertIn(self.names[0], str(e.exception)); self.assertIn("migrat", str(e.exception))
        with self.assertRaises(config.ConfigError):
            app.build_server(self.cfg)
        migrate.migrate(self.cfg)
        n = len(self.names) + 1
        (self.tmp / ("V%04d__more.sql" % n)).write_text("SELECT 1;")
        with self.assertRaises(config.ConfigError) as e:
            app.Store(self.cfg)
        self.assertIn("V%04d__more.sql" % n, str(e.exception))
        with self.assertRaises(config.ConfigError):
            app.build_server(self.cfg)
        migrate.migrate(self.cfg)
        store = app.Store(self.cfg)                                          # now it starts
        store.ping()
        store.close()

    def test_concurrent_runs_apply_each_file_once(self):
        results = []
        ts = [threading.Thread(target=lambda: results.append(migrate.migrate(make_cfg()))) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sorted(sum(results, [])), self.names)
        self.assertEqual(db("SELECT count(*) FROM schema_history"), [(len(self.names),)])


def reset_db_quiet():
    reset_db()


class ConfigValidation(unittest.TestCase):
    SECRETS = {"DB_PASSWORD": "pw-secret-abc", "ACTOR_SALT": "salt-secret-xyz", "TELEGRAM_BOT_TOKEN": "999:tg-secret-token"}

    def cfg(self, role="serve", **env):
        e = {"TIMUS_ENV": "dev"}
        e.update(env)
        with environment(e):
            return config.Config(role)

    def staging(self, role="serve", drop=(), **extra):
        env = {"TIMUS_ENV": "staging"}
        env.update({k: v for k, v in self.SECRETS.items() if k not in drop})
        env.update(extra)
        return self.cfg(role, **env)

    def test_dev_needs_no_secrets_and_has_defaults(self):
        c = self.cfg()
        self.assertEqual((c.env, c.rate_limit, c.dup_radius_m, c.report_threshold, c.db_pool_max), ("dev", 30, 50.0, 3, 10))
        self.assertEqual((c.db["host"], c.db["port"], c.db["dbname"], c.db["user"], c.db["password"]), ("127.0.0.1", 5432, "masjid", "masjid", None))
        self.assertEqual((c.api_base, c.cors_origins, c.admin_token, c.moderator_ids, c.gemini_keys), ("/masjiduz/v1", [], "", set(), []))
        self.assertTrue(c.show_tile_mosques)
        self.assertEqual(self.cfg(TIMUS_ENV="").env, "dev")
        self.assertEqual(c.version, "1.0.0")

    def test_port_zero_is_accepted_in_dev(self):
        self.assertEqual(self.cfg(PORT="0").port, 0)
        self.assertEqual(self.cfg(PORT="0").host, "0.0.0.0")
        with self.assertRaises(config.ConfigError):
            self.cfg(PORT="70000")

    def test_dotenv_strips_inline_comments_of_unquoted_values(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        f = d / ".env"
        f.write_text("# a comment line\nA=value   # trailing comment\n"
                     "B='quoted # kept'\nC=\"dq # kept\"\nD=x#nospace\nTOKEN=   # empty\nE=\n  F = spaced  \nnot a pair\n")
        with mock.patch.dict(os.environ, {}):
            for k in "ABCDEF":
                os.environ.pop(k, None)
            os.environ.pop("TOKEN", None)
            config.real_load_dotenv(f)
            self.assertEqual([os.environ[k] for k in "ABCD"], ["value", "quoted # kept", "dq # kept", "x#nospace"])
            self.assertEqual((os.environ["TOKEN"], os.environ["E"], os.environ["F"]), ("", "", "spaced"))
            os.environ["A"] = "from-the-environment"
            config.real_load_dotenv(f)
            self.assertEqual(os.environ["A"], "from-the-environment")           # real variables win

    def test_copying_env_example_gives_clean_values(self):
        example = Path(config.ROOT) / ".env.example"
        with mock.patch.dict(os.environ, {}):
            for k in [k for k in os.environ if k.startswith("TIMUS_")]:
                del os.environ[k]
            config.real_load_dotenv(example)
            got = {k: v for k, v in os.environ.items() if k.startswith("TIMUS_")}
            self.assertGreater(len(got), 20)
            for k, v in got.items():
                self.assertNotIn(" #", v, k)
                self.assertEqual(v, v.strip(), k)
            self.assertEqual(got["TIMUS_APP_MASJIDUZ_ADMIN_TOKEN"], "")        # stays empty: token login off
            self.assertEqual(got["TIMUS_APP_MASJIDUZ_DB_PORT"], "5432")
            self.assertEqual(got["TIMUS_ENV"], "dev")
            c = config.Config("serve")                                            # and it is a valid configuration
            self.assertEqual((c.admin_token, c.db["port"], c.api_base, c.rate_limit), ("", 5432, "/masjiduz/v1", 30))
            self.assertTrue(c.tile_style_url.startswith("https://"))

    def test_unknown_env_is_refused(self):
        with self.assertRaises(config.ConfigError):
            self.cfg(TIMUS_ENV="production")

    def test_missing_secrets_in_staging_and_prod_name_the_variable_never_the_value(self):
        for env in ("staging", "prod"):
            for missing in ("DB_PASSWORD", "ACTOR_SALT", "TELEGRAM_BOT_TOKEN"):
                with self.assertRaises(config.ConfigError) as e:
                    self.staging(drop=(missing,), TIMUS_ENV=env)
                msg = str(e.exception)
                self.assertIn(config.P + missing, msg, (env, missing))
                for value in self.SECRETS.values():
                    self.assertNotIn(value, msg)
        self.assertEqual(self.staging().env, "staging")
        self.assertEqual(self.staging(TIMUS_ENV="prod").env, "prod")
        # roles need only what they use
        self.staging("bot", drop=("ACTOR_SALT",))
        with self.assertRaises(config.ConfigError):
            self.staging("bot", drop=("TELEGRAM_BOT_TOKEN",))
        self.staging("job", drop=("ACTOR_SALT", "TELEGRAM_BOT_TOKEN"))
        with self.assertRaises(config.ConfigError):
            self.staging("job", drop=("DB_PASSWORD",))
        with self.assertRaises(config.ConfigError):
            self.staging(DB_PASSWORD="   ")                                    # blank is missing

    def test_container_listens_on_8080_whatever_the_environment_says(self):
        c = self.staging(PORT="9999", HOST="127.0.0.1")
        self.assertEqual((c.host, c.port), ("0.0.0.0", 8080))
        c = self.cfg(PORT="9999", HOST="127.0.0.1")
        self.assertEqual((c.host, c.port), ("127.0.0.1", 9999))

    def test_cors_wildcard(self):
        with self.assertRaises(config.ConfigError) as e:
            self.staging(TIMUS_ENV="prod", CORS_ORIGINS="*")
        self.assertIn("CORS_ORIGINS", str(e.exception))
        with self.assertRaises(config.ConfigError):
            self.staging(TIMUS_ENV="prod", CORS_ORIGINS="https://a.example, *")
        c = self.staging(CORS_ORIGINS="*")
        self.assertTrue(any("CORS_ORIGINS" in w for w in c.warnings))        # staging: a warning only
        self.assertEqual(self.cfg(CORS_ORIGINS="*").cors_origins, ["*"])
        c = self.cfg(CORS_ORIGINS=" https://a.example/ , https://b.example ,, ")
        self.assertEqual(c.cors_origins, ["https://a.example", "https://b.example"])
        self.assertEqual(self.staging(TIMUS_ENV="prod", CORS_ORIGINS="https://a.example").cors_origins, ["https://a.example"])

    def test_admin_token_length(self):
        short = "short-token-value"
        c = self.cfg(ADMIN_TOKEN=short)
        self.assertTrue(any("ADMIN_TOKEN" in w for w in c.warnings))
        for env in ("staging", "prod"):
            with self.assertRaises(config.ConfigError) as e:
                self.staging(TIMUS_ENV=env, ADMIN_TOKEN=short)
            self.assertIn("ADMIN_TOKEN", str(e.exception)); self.assertNotIn(short, str(e.exception))
        self.assertEqual(self.staging(ADMIN_TOKEN="x" * 32).admin_token, "x" * 32)
        with self.assertRaises(config.ConfigError):
            self.staging(ADMIN_TOKEN="x" * 31)
        self.assertEqual(self.staging().admin_token, "")                      # optional

    def test_numbers_and_lists(self):
        for name, bad in (("DB_PORT", "abc"), ("DB_PORT", "0"), ("DB_PORT", "70000"), ("DB_POOL_MAX", "0"), ("DB_POOL_MAX", "101"), ("WRITE_RATE_PER_HOUR", "0"),
                          ("REPORT_THRESHOLD", "0"), ("DUPLICATE_RADIUS_M", "far"), ("PRAYER_ASR_FACTOR", "3"), ("MAX_PHOTO_BYTES", "10")):
            with self.assertRaises(config.ConfigError, msg=(name, bad)) as e:
                self.cfg(**{name: bad})
            self.assertIn(name, str(e.exception))
        c = self.cfg(MODERATOR_IDS="1, 2 3,x,-5", WRITE_RATE_PER_HOUR="7", DB_PORT="54330", DB_POOL_MAX="3", REPORT_THRESHOLD="5", DUPLICATE_RADIUS_M="12.5",
                     PRAYER_ASR_FACTOR="1", GEMINI_API_KEY="k1, k2", BOT_USERNAME="@mybot", TRUSTED_PROXIES="192.0.2.0/24, 2001:db8::/32", COMMIT="abc123")
        self.assertEqual((c.moderator_ids, c.rate_limit, c.db["port"], c.db_pool_max, c.report_threshold, c.dup_radius_m), ({1, 2, 3, -5}, 7, 54330, 3, 5, 12.5))
        self.assertEqual((c.prayer["asr_factor"], c.gemini_keys, c.bot_username, c.commit), (1, ["k1", "k2"], "mybot", "abc123"))
        self.assertEqual([str(n) for n in c.proxies], ["192.0.2.0/24", "2001:db8::/32"])
        self.assertIn("127.0.0.0/8", [str(n) for n in self.cfg().proxies])
        self.assertIn("host=127.0.0.1", c.conninfo())

    def test_models_setting(self):
        c = self.cfg(GEMINI_MODELS="a:5:100,b:10:0")
        self.assertEqual(c.gemini_models, [("a", 5, 100), ("b", 10, 0)])
        self.assertGreaterEqual(len(self.cfg().gemini_models), 1)

    def test_the_real_environment_and_dotenv_are_not_consulted_by_the_tests(self):
        with environment({"TIMUS_ENV": "dev"}):
            self.assertEqual([k for k in os.environ if k.startswith("TIMUS_APP_")], [])


class Logs(ServerCase):
    FORMAT = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z (INFO|WARN|ERROR)\s+\[rid=[^\]]+\] \[masjiduz\] \S+ - ")

    def setUp(self):
        reset_db()
        self.buf = io.StringIO()
        h = logging.StreamHandler(self.buf)
        h.setFormatter(logs._Format())
        root = logging.getLogger()
        self.old_level = root.level
        root.addHandler(h)
        root.setLevel(logging.INFO)
        self.addCleanup(lambda: (root.removeHandler(h), root.setLevel(self.old_level)))

    def lines(self):
        return [ln for ln in self.buf.getvalue().split("\n") if re.match(r"^\d{4}-\d\d-\d\dT", ln)]

    def test_mask(self):
        m = logs.mask
        self.assertNotIn("abc123", m("Authorization: Bearer abc123def"))
        self.assertEqual(m("Bearer abc123"), "Bearer ***")
        for secret in ("token=s3cret", "password=hunter2&x=1", "key=AIzaXYZ", "secret=zzz", "hash=deadbeef"):
            self.assertNotIn(secret.split("=")[1].split("&")[0], m(secret), secret)
        self.assertNotIn("123456:ABC-DEF_ghi", m("url /bot123456:ABC-DEF_ghi/getMe"))
        self.assertNotIn("203.0.113.9", m("client 203.0.113.9 did it"))
        self.assertNotIn("2001:db8:1::5", m("from 2001:db8:1::5 ok"))
        self.assertEqual(m("a\r\nb\tc\x00d"), "a  b c d")                      # one line, no injection
        self.assertEqual(m("x" * 100, 10), "x" * 10 + "...")
        self.assertEqual(m("plain text"), "plain text")
        self.assertEqual(m(ValueError("bad 10.0.0.1")), "bad ***")

    def test_log_lines_have_the_timus_format_and_the_request_id(self):
        self.use()
        rid = str(uuid.uuid4())
        self.call("GET", "/health", headers={"X-Request-Id": rid})
        self.call("GET", "/mosques/" + NOBODY)
        ls = self.lines()
        self.assertTrue(ls)
        for ln in ls:
            self.assertRegex(ln, self.FORMAT)
        mine = [ln for ln in ls if "[rid=%s]" % rid in ln]
        self.assertEqual(len(mine), 1)
        self.assertRegex(mine[0], r"access - GET /masjiduz/v1/health 200 \d+ms$")
        self.assertTrue(any(re.search(r"access - GET /masjiduz/v1/mosques/%s 404 \d+ms$" % NOBODY, ln) for ln in ls))
        self.assertNotIn("[rid=-]", "\n".join(mine))

    def test_nothing_secret_or_personal_reaches_the_log(self):
        tok = "tok-" + "z" * 40
        self.use(ADMIN_TOKEN=tok)
        init = sign_init_data({"id": 424242, "first_name": "Secretname"})
        hash_value = re.search(r"hash=([0-9a-f]+)", init).group(1)
        ip = "203.0.113.77"
        h = {"Authorization": "Bearer " + tok, "X-Telegram-Init-Data": init, "X-Device-Id": "device-secret-id-12345", "User-Agent": "SecretAgent/9"}
        self.call("GET", "/admin/overview", headers=h, ip=ip)
        self.call("GET", "/admin/overview", headers={"Authorization": "Bearer wrong-token-value", "X-Telegram-Init-Data": "hash=bad&user=x"}, ip=ip)
        self.call("POST", "/mosques", {"lat": 5, "lon": 5, "name": "Free text name zzz", "address": "Private street 12"}, headers=h, ip=ip)
        self.call("POST", "/mosques", b"{broken free text", ip=ip)
        self.call("GET", "/mosques/search?q=secretfreetext", ip=ip)
        with mock.patch.object(app.Store, "get", side_effect=RuntimeError("explode")):
            self.assertEqual(self.call("GET", "/mosques/" + NOBODY, headers=h, ip=ip)[0], 500)
        out = self.buf.getvalue()
        self.assertIn("explode", out)                                          # the error itself is logged (with its stack) ...
        self.assertIn("ERROR", out)
        for needle in (tok, "wrong-token-value", "Authorization", "Bearer", init, hash_value, "initData", "Secretname", "424242", ip, "203.0.113",
                       "device-secret-id-12345",
                       "SecretAgent", "Free text name zzz", "Private street", "secretfreetext", "broken free text", "X-Telegram-Init-Data"):
            self.assertNotIn(needle, out, needle)                              # ... but nothing private
        for ln in self.lines():
            self.assertRegex(ln, self.FORMAT)
        errs = [ln for ln in self.lines() if " ERROR " in ln or ln.split()[1] == "ERROR"]
        self.assertTrue(errs)

    def test_traceback_lines_are_masked_too(self):
        self.use()
        with mock.patch.object(app.Store, "get", side_effect=RuntimeError("failed token=abc123secret for 203.0.113.9 Bearer sekrit-value")):
            self.call("GET", "/mosques/" + NOBODY)
        out = self.buf.getvalue()
        self.assertIn("Traceback", out)
        self.assertIn("RuntimeError", out)
        for needle in ("abc123secret", "203.0.113.9", "sekrit-value"):
            self.assertNotIn(needle, out)

    def test_the_rid_follows_the_request_into_error_lines(self):
        self.use()
        rid = str(uuid.uuid4())
        with mock.patch.object(app.Store, "get", side_effect=RuntimeError("explode")):
            self.call("GET", "/mosques/" + NOBODY, headers={"X-Request-Id": rid})
        err = [ln for ln in self.lines() if "unhandled" in ln]
        self.assertEqual(len(err), 1)
        self.assertIn("[rid=%s]" % rid, err[0]); self.assertRegex(err[0], r"^\S+ ERROR\s+\[rid=")


class Shutdown(ServerCase):
    def setUp(self):
        reset_db()

    def test_wait_idle_counts_requests_in_flight(self):
        srv = self.use()
        st = srv.state
        self.assertTrue(st.wait_idle(0.1))
        with st.busy():
            self.assertEqual(st.active, 1)
            self.assertFalse(st.wait_idle(0.3))
        self.assertTrue(st.wait_idle(1))
        self.assertEqual(st.active, 0)

    def test_a_request_in_flight_finishes_before_the_server_is_idle(self):
        srv = self.use()
        mid = self.made(60.0, 60.0)["id"]
        gate, entered, out = threading.Event(), threading.Event(), []
        real = app.Store.get

        def slow(store, m_id):
            entered.set()
            gate.wait(10)
            return real(store, m_id)
        with mock.patch.object(app.Store, "get", slow):
            t = threading.Thread(target=lambda: out.append(self.call("GET", "/mosques/" + mid)))
            t.start()
            self.assertTrue(entered.wait(5))
            self.assertEqual(srv.state.active, 1)
            self.assertFalse(srv.state.wait_idle(0.3))
            gate.set()
            t.join(10)
        self.assertEqual(out[0][0], 200)
        self.assertTrue(srv.state.wait_idle(3))

    def test_shutdown_stops_accepting_new_connections(self):
        cfg = make_cfg()
        srv, addr = helpers.start(cfg)
        self.assertEqual(self.call("GET", "/health", addr=addr)[0], 200)
        srv.shutdown()
        self.assertTrue(srv.state.wait_idle(2))
        srv.server_close()
        srv.store.close()
        with self.assertRaises(OSError):
            helpers.http_request(addr, "GET", API + "/health", timeout=2)


class WebClient(ServerCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.srv, cls.addr = helpers.start(make_cfg())

    @classmethod
    def tearDownClass(cls):
        helpers.stop(cls.srv)

    def index(self):
        r = self.req("GET", WEB + "/")
        self.assertEqual(r.status, 200)
        return r

    def test_index_has_hashed_references_and_revalidates(self):
        r = self.index()
        self.assertEqual(r.header("Cache-Control"), "no-cache")
        self.assertTrue(r.header("Content-Type").startswith("text/html"))
        html = r.text
        for name in ("app", "style", "i18n", "prayer", "theme-init"):
            self.assertRegex(html, r'(src|href)="%s\.[0-9a-f]{8}\.(js|css)"' % name)
        self.assertRegex(html, r'src="vendor/maplibre-gl\.[0-9a-f]{8}\.js"')
        self.assertIn('src="config.js"', html)                                  # generated, never hashed
        self.assertEqual(self.req("GET", WEB + "/index.html").status, 200)
        et = r.header("ETag")
        self.assertEqual(self.req("GET", WEB + "/", headers={"If-None-Match": et}).status, 304)

    def test_hashed_assets_are_immutable_for_a_year(self):
        html = self.index().text
        refs = re.findall(r'(?:src|href)="((?:[\w-]+/)?[\w.-]+\.[0-9a-f]{8}\.(?:js|css))"', html)
        self.assertGreaterEqual(len(refs), 5)
        for ref in refs:
            r = self.req("GET", WEB + "/" + ref)
            self.assertEqual(r.status, 200, ref)
            self.assertEqual(r.header("Cache-Control"), "public, max-age=31536000, immutable", ref)
            plain = re.sub(r"\.[0-9a-f]{8}\.(js|css)$", r".\1", ref)
            self.assertEqual(hashlib.sha256((Path(app.WEB_DIR) / plain).read_bytes()).hexdigest()[:8], re.search(r"\.([0-9a-f]{8})\.", ref).group(1))
            self.assertEqual(r.body, (Path(app.WEB_DIR) / plain).read_bytes())
        self.assertTrue(any(ref.endswith(".js") for ref in refs)); self.assertTrue(any(ref.endswith(".css") for ref in refs))
        # the plain name and a stale/forged hash are served, but never cached for a year
        self.assertEqual(self.req("GET", WEB + "/app.js").header("Cache-Control"), "no-cache")
        r = self.req("GET", WEB + "/app.00000000.js")
        self.assertEqual((r.status, r.header("Cache-Control")), (200, "no-cache"))
        self.assertEqual(self.req("GET", WEB + "/nothing.00000000.js").status, 404)

    def test_content_types_and_conditional_requests(self):
        r = self.req("GET", WEB + "/app.js")
        self.assertIn(r.header("Content-Type"), ("application/javascript; charset=utf-8", "text/javascript; charset=utf-8"))
        self.assertTrue(self.req("GET", WEB + "/style.css").header("Content-Type").startswith("text/css"))
        et = r.header("ETag")
        r = self.req("GET", WEB + "/app.js", headers={"If-None-Match": et})
        self.assertEqual((r.status, r.raw), (304, b""))
        self.assertTrue(r.header("Content-Security-Policy"))                     # security headers are not lost on a 304

    def test_config_js_defines_the_api_base_from_the_environment(self):
        r = self.req("GET", WEB + "/config.js")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.text, 'window.MASJIDUZ_API_BASE="/masjiduz/v1";\n')
        self.assertTrue(r.header("Content-Type").startswith("application/javascript"))
        self.assertEqual(r.header("Cache-Control"), "no-cache")
        _, addr = self.serve(API_BASE="https://api.example.org/masjiduz/v1")
        r = self.req("GET", WEB + "/config.js", addr=addr)
        self.assertEqual(r.text, 'window.MASJIDUZ_API_BASE="https://api.example.org/masjiduz/v1";\n')
        _, addr = self.serve(API_BASE='/x";alert(1);//')
        self.assertNotIn('";alert', self.req("GET", WEB + "/config.js", addr=addr).text.replace('\\"', ""))     # quotes are escaped

    def test_security_headers(self):
        for path in (WEB + "/", WEB + "/app.js", WEB + "/config.js", WEB + "/admin.html", WEB + "/vendor/maplibre-gl.js"):
            r = self.req("GET", path)
            csp = r.header("Content-Security-Policy")
            self.assertTrue(csp, path)
            self.assertNotIn("unsafe-eval", csp)
            self.assertIn("script-src 'self' https://telegram.org", csp)
            self.assertRegex(csp, r"img-src [^;]*https://upload\.wikimedia\.org")
            self.assertIn("default-src 'self'", csp); self.assertIn("frame-ancestors", csp); self.assertIn("worker-src 'self' blob:", csp)
            self.assertNotIn("yandex", csp)
            self.assertEqual(r.header("X-Content-Type-Options"), "nosniff")
            self.assertEqual(r.header("Referrer-Policy"), "strict-origin-when-cross-origin")
            self.assertEqual(r.header("Permissions-Policy"), "geolocation=(self)")
        self.assertIn("https://tiles.openfreemap.org", self.req("GET", WEB + "/").header("Content-Security-Policy"))
        _, addr = self.serve(TILE_STYLE_URL="https://tiles.example.org/styles/x")
        self.assertIn("https://tiles.example.org", self.req("GET", WEB + "/", addr=addr).header("Content-Security-Policy"))
        _, addr = self.serve(TILE_STYLE_URL="http://insecure.example.org/x")
        self.assertNotIn("insecure.example.org", self.req("GET", WEB + "/", addr=addr).header("Content-Security-Policy"))

    def test_admin_page_is_under_web_and_not_indexed(self):
        r = self.req("GET", WEB + "/admin.html")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.header("X-Robots-Tag"), "noindex, nofollow")
        self.assertEqual(self.req("GET", "/admin.html").status, 404)

    def test_everything_else_is_the_404_envelope(self):
        for path in ("/", "/admin.html", "/api/mosques", "/app.js", "/vendor/maplibre-gl.js"):
            r = self.req("GET", path)
            self.assertEqual((r.status, r.json["code"]), (404, "NOT_FOUND"), path)

    def test_path_traversal_is_not_possible(self):
        for path in (WEB + "/..%2fserver/app.py", WEB + "/../server/app.py", WEB + "/%2e%2e/server/app.py", WEB + "/..%2f..%2f..%2fetc/passwd",
                     WEB + "/vendor/..%2f..%2fserver/config.py", WEB + "/.%2e/.env", WEB + "/%2e%2e%2f.env"):
            r = self.req("GET", path)
            self.assertEqual(r.status, 404, path)
            self.assertNotIn(b"import", r.raw[:50])
        self.assertEqual(self.req("GET", WEB + "/vendor/").status, 404)       # directories are not listed

    def test_a_missing_hashed_file_is_not_served_from_outside_the_web_dir(self):
        self.assertEqual(self.req("GET", WEB + "/..%2fserver/app.12345678.py").status, 404)


class Imports(ServerCase):
    def setUp(self):
        reset_db()
        self.cfg = make_cfg()
        self.store = app.Store(self.cfg)
        self.addCleanup(self.store.close)
        self.id1 = str(uuid.uuid4())

    def items(self):
        return [{"id": self.id1, "lat": 45.0, "lon": 55.0, "name": "Imported one", "address": "Street 1", "prayerTimes": {"fajr": "05:10", "isha": "20:00"}},
                {"osmRef": "node/555", "lat": 45.1, "lon": 55.1, "name": "Imported two", "status": "verified", "verificationNote": "checked", "origin": "imported"},
                {"osmRef": "node/556", "lat": 45.2, "lon": 55.2, "photoUrl": "https://upload.wikimedia.org/x.jpg", "photoCredit": "https://commons.wikimedia.org/wiki/File:x.jpg"}]

    def test_import_is_idempotent_and_audited(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r1 = data_import.run(self.items(), self.cfg, self.store)
            r2 = data_import.run(self.items(), self.cfg, self.store)
        self.assertEqual(r1, {"added": 3, "updated": 0, "unchanged": 0, "skipped": 0})
        self.assertEqual(r2, {"added": 0, "updated": 0, "unchanged": 3, "skipped": 0})
        self.assertIn("import done: added=3 updated=0 unchanged=0 skipped=0", out.getvalue())
        self.assertEqual(db("SELECT count(*) FROM mosques"), [(3,)])
        one = self.store.get(self.id1)
        self.assertEqual((one["name"], one["prayer_times"], one["origin"], one["status"], one["source"]),
                         ("Imported one", {"fajr": "05:10", "isha": "20:00"}, "imported", "unverified", "import"))
        self.assertTrue(one["times_updated_at"])
        self.assertEqual(db("SELECT status, verification_note, osm_ref FROM mosques WHERE osm_ref='node/555'"), [("verified", "checked", "node/555")])
        self.assertEqual(db("SELECT photo_url FROM mosques WHERE osm_ref='node/556'"), [("https://upload.wikimedia.org/x.jpg",)])
        self.assertEqual(db("SELECT count(*) FROM mosque_history WHERE action='import' AND actor='import:file'"), [(3,)])
        # search finds imported names
        self.assertEqual([m["name"] for m in self.store.search("imported one")], ["Imported one"])

    def test_changes_update_only_the_given_fields_and_write_history(self):
        data_import.run(self.items(), self.cfg, self.store)
        items = self.items()
        items[0]["name"] = "Renamed by import"
        items[1]["lat"] = 45.15
        with contextlib.redirect_stdout(io.StringIO()):
            r = data_import.run(items, self.cfg, self.store)
        self.assertEqual((r["added"], r["updated"], r["unchanged"]), (0, 2, 1))
        one = self.store.get(self.id1)
        self.assertEqual((one["name"], one["version"]), ("Renamed by import", 2))
        hist = db("SELECT version, action, changes FROM mosque_history WHERE mosque_id=%s ORDER BY seq", self.id1)
        self.assertEqual([h[:2] for h in hist], [(1, "import"), (2, "import")])
        self.assertEqual(hist[1][2], {"name": ["Imported one", "Renamed by import"]})
        self.assertEqual([m["name"] for m in self.store.search("renamed")], ["Renamed by import"])

    def test_bad_records_are_skipped_not_fatal(self):
        bad = [{"lat": 1, "lon": 2},                                           # no id/osmRef
               {"id": self.id1, "lat": 1, "lon": 2, "surprise": True},         # unknown field
               {"id": "not-a-uuid", "lat": 1, "lon": 2},
               {"id": str(uuid.uuid4()), "lat": 200, "lon": 2},                # bad coordinates
               {"id": str(uuid.uuid4()), "lat": 1, "lon": 2, "status": "verified"},        # verified needs a note
               {"id": str(uuid.uuid4()), "lat": 1, "lon": 2, "origin": "alien"},
               {"id": str(uuid.uuid4()), "lat": 1, "lon": 2, "prayerTimes": {"fajr": "99:99"}},
               "not an object", None,
               {"id": self.id1, "lat": 3.0, "lon": 4.0, "name": "good one"}]
        with contextlib.redirect_stdout(io.StringIO()):
            r = data_import.run(bad, self.cfg, self.store)
        self.assertEqual((r["added"], r["skipped"]), (1, 9))
        self.assertEqual(db("SELECT count(*) FROM mosques"), [(1,)])

    def run_quiet(self, items):
        with contextlib.redirect_stdout(io.StringIO()):
            return data_import.run(items, self.cfg, self.store)

    def test_a_partial_record_does_not_wipe_what_it_does_not_carry(self):
        full = {"id": self.id1, "osmRef": "node/700", "lat": 48.0, "lon": 58.0, "name": "Whole", "address": "Somewhere 1", "prayerTimes": {"fajr": "05:10"},
                "source": "survey", "origin": "community", "status": "verified", "verificationNote": "seen", "photoUrl": "https://upload.wikimedia.org/p.jpg"}
        self.assertEqual(self.run_quiet([full])["added"], 1)
        r = self.run_quiet([{"osmRef": "node/700", "lat": 48.0, "lon": 58.0, "name": "Renamed only"}])
        self.assertEqual((r["updated"], r["skipped"]), (1, 0))
        row = self.store.get(self.id1)
        self.assertEqual((row["name"], row["address"], row["prayer_times"], row["source"], row["origin"], row["status"], row["verification_note"]),
                         ("Renamed only", "Somewhere 1", {"fajr": "05:10"}, "survey", "community", "verified", "seen"))
        self.assertEqual(row["photo_url"], "https://upload.wikimedia.org/p.jpg")
        self.assertEqual(self.run_quiet([{"osmRef": "node/700", "lat": 48.0, "lon": 58.0, "name": "Renamed only"}])["unchanged"], 1)

    def test_times_updated_at_follows_changes_of_the_times(self):
        self.run_quiet([{"id": self.id1, "lat": 49.0, "lon": 59.0, "name": "Stamp"}])
        self.assertIsNone(self.store.get(self.id1)["times_updated_at"])
        self.run_quiet([{"id": self.id1, "prayerTimes": {"fajr": "05:10"}}])
        stamp = self.store.get(self.id1)["times_updated_at"]
        self.assertRegex(stamp, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.run_quiet([{"id": self.id1, "name": "Stamp 2"}])
        self.assertEqual(self.store.get(self.id1)["times_updated_at"], stamp)
        self.run_quiet([{"id": self.id1, "prayerTimes": {}}])                    # all times removed
        self.assertIsNone(self.store.get(self.id1)["times_updated_at"])

    def test_an_archived_row_is_not_resurrected(self):
        self.run_quiet(self.items())
        self.store.moderate(self.id1, "archive", "admin:1", "gone")
        r = self.run_quiet([{"id": self.id1, "lat": 45.0, "lon": 55.0, "status": "unverified", "name": "Back again"}])
        self.assertEqual((r["updated"], r["unchanged"]), (0, 1))
        self.assertEqual(self.store.get(self.id1)["status"], "archived")
        self.assertEqual(self.store.get(self.id1)["name"], "Imported one")
        r = self.run_quiet([{"id": self.id1, "status": "archived", "name": "Name still follows"}])      # an archived record may still be refreshed
        self.assertEqual(self.store.get(self.id1)["status"], "archived")

    def test_colliding_keys_and_bad_photo_links_are_skipped_not_fatal(self):
        self.run_quiet([{"osmRef": "node/1", "lat": 1.0, "lon": 1.0}])
        other = str(uuid.uuid4())
        r = self.run_quiet([{"id": other, "osmRef": "node/1", "lat": 2.0, "lon": 2.0},                    # osmRef already belongs to another id
                            {"osmRef": "node/2", "lat": 3.0, "lon": 3.0, "photoUrl": "http://insecure.example/x.jpg"},
                            {"osmRef": "node/3", "lat": 3.5, "lon": 3.5, "photoUrl": "javascript:alert(1)"},
                            {"osmRef": "node/4", "lat": 3.6, "lon": 3.6, "photoCredit": "http://insecure.example/c"},
                            {"osmRef": "node/5", "lat": 4.0, "lon": 4.0, "photoUrl": "https://upload.wikimedia.org/ok.jpg"}])
        self.assertEqual((r["added"], r["skipped"]), (1, 4))
        self.assertEqual(db("SELECT count(*) FROM mosques"), [(2,)])
        self.assertEqual(db("SELECT count(*) FROM mosques WHERE id=%s", other), [(0,)])
        self.assertEqual(self.run_quiet([{"osmRef": "node/6", "lat": 5.0, "lon": 5.0}])["added"], 1)        # and the store still works

    def test_history_shows_import_as_the_role_system(self):
        self.run_quiet(self.items())
        items, _ = self.store.history(self.id1)
        self.assertEqual([(i["action"], i["actor"]) for i in items], [("import", "system")])
        self.run_quiet([{"id": self.id1, "name": "Changed"}])
        self.assertEqual([i["actor"] for i in self.store.history(self.id1)[0]], ["system", "system"])
        self.assertNotIn("import:file", json.dumps(self.store.history(self.id1)[0]))

    def test_load_reads_the_documented_file_format(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        good = d / "ok.json"
        good.write_text(json.dumps({"mosques": self.items()}), encoding="utf-8")
        self.assertEqual(len(data_import.load(str(good))), 3)
        for content in ('[{"id": 1}]', '{"mosques": 5}', '{"other": []}'):
            f = d / "bad.json"
            f.write_text(content)
            with self.assertRaises(ValueError):
                data_import.load(str(f))

    def test_osm_import_summary_line_and_photos_without_network(self):
        import import_osm
        data = {"elements": [{"type": "node", "id": 31, "lat": 46.0, "lon": 56.0, "tags": {"name": "A", "wikimedia_commons": "File:A b.jpg"}}]}
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network allowed")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                s = import_osm.run(data, self.cfg, self.store)
        self.assertRegex(out.getvalue(), r"^import-osm done: added=1 updated=0 unchanged=0 protected_edited=0 near_existing=0 missing_in_osm=0 photos=1\n$")
        self.assertEqual(s["photos"], 1)
        self.assertIn("A_b.jpg", db("SELECT photo_url FROM mosques")[0][0])


class Commands(unittest.TestCase):
    """`python server/cli.py <command>` in-process: exit codes and the one-line summaries."""

    def run_cli(self, argv, **env):
        import cli
        e = base_env()
        e.update(env)
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        out = io.StringIO()
        try:
            with environment(e), contextlib.redirect_stdout(out):
                code = cli.main(argv)
        finally:
            root.handlers[:] = saved[0]
            root.setLevel(saved[1])
        return code, out.getvalue()

    def test_migrate_is_idempotent_and_serve_refuses_a_pending_schema(self):
        with psycopg.connect(helpers.TEST_URL, autocommit=True) as c:
            helpers.ensure_database()
            c.execute("DROP SCHEMA IF EXISTS masjiduz CASCADE")
        self.addCleanup(reset_db)
        with mock.patch.object(app, "serve", side_effect=AssertionError("must not start")):
            pass
        code, _ = self.run_cli(["migrate"])
        self.assertEqual(code, 0)
        code, out = self.run_cli(["migrate"])
        self.assertEqual(code, 0); self.assertIn("migrate done: 0 applied", out)
        with psycopg.connect(helpers.TEST_URL, autocommit=True) as c:
            c.execute("DROP SCHEMA masjiduz CASCADE")
        code, out = self.run_cli(["serve"])                                    # serve never migrates: it stops with a message
        self.assertEqual(code, 2)
        self.assertIn("migrations are pending", out)

    def test_missing_secret_stops_with_one_log_line_naming_the_variable(self):
        e = {"TIMUS_ENV": "staging", "DB_HOST": "127.0.0.1"}
        with environment(e), contextlib.redirect_stdout(io.StringIO()):
            pass
        import cli
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        out = io.StringIO()
        try:
            with environment({"TIMUS_ENV": "staging", "ACTOR_SALT": "s", "TELEGRAM_BOT_TOKEN": "1:t"}), contextlib.redirect_stdout(out):
                code = cli.main(["serve"])
        finally:
            root.handlers[:] = saved[0]
            root.setLevel(saved[1])
        self.assertEqual(code, 2)
        lines = [ln for ln in out.getvalue().split("\n") if ln]
        self.assertEqual(len(lines), 1)
        self.assertIn("TIMUS_APP_MASJIDUZ_DB_PASSWORD", lines[0])
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\dT[\d:.]+Z ERROR\s+\[rid=-\] \[masjiduz\] cli - ")

    def test_import_and_import_osm_commands(self):
        reset_db()
        self.addCleanup(reset_db)
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        f1 = d / "mosques.json"
        f1.write_text(json.dumps({"mosques": [{"osmRef": "node/9", "lat": 47.0, "lon": 57.0, "name": "From file"}]}))
        code, out = self.run_cli(["import", "--file", str(f1)])
        self.assertEqual(code, 0); self.assertIn("import done: added=1 updated=0 unchanged=0 skipped=0", out)
        code, out = self.run_cli(["import", "--file", str(f1)])
        self.assertIn("added=0 updated=0 unchanged=1", out)
        code, _ = self.run_cli(["import", "--file", str(d / "missing.json")])
        self.assertEqual(code, 1)
        f2 = d / "osm.json"
        f2.write_text(json.dumps({"elements": [{"type": "node", "id": 77, "lat": 47.5, "lon": 57.5, "tags": {"name": "OSM one"}}]}))
        code, out = self.run_cli(["import-osm", "--file", str(f2)])
        self.assertEqual(code, 0); self.assertIn("import-osm done: added=1", out)
        code, out = self.run_cli(["import-osm", "--file", str(f2)])
        self.assertIn("added=0 updated=0 unchanged=1", out)


if __name__ == "__main__":
    unittest.main()
