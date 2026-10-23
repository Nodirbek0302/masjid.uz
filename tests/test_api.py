import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import psycopg
import app

TEST_URL = os.environ.get('TEST_DATABASE_URL', 'postgresql://masjid@127.0.0.1:54329/masjid_test')

class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        with psycopg.connect(TEST_URL, autocommit=True) as c:   # fresh schema for every run
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        os.environ.update(DATABASE_URL=TEST_URL, ADMIN_TOKEN="secret", PORT="0",
                          WRITE_RATE_PER_MIN="1000", REPORT_THRESHOLD="2")
        cfg = app.Config()
        cls.srv = app.build_server(cfg)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, method, path, body=None, admin=False, client="aaaaaaaa-1", headers=None):
        h = {"Content-Type": "application/json", "X-Client-Id": client}
        h.update(headers or {})
        if admin: h["Authorization"] = "Bearer secret"
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def add(self, lat, lon, **kw):
        return self.call("POST", "/api/mosques", dict(lat=lat, lon=lon, **kw))

    def test_full_flow(self):
        # add without name/times -> missing info allowed, community origin, unverified
        s, m = self.add(41.3111, 69.2797)
        self.assertEqual(s, 201)
        self.assertEqual((m["origin"], m["status"], m["version"], m["name"]), ("community", "unverified", 1, None))
        self.assertNotIn("creator_id", m)                       # personal ids never appear in public responses
        # duplicate detection
        s, d = self.add(41.31112, 69.27972, name="Dup")
        self.assertEqual(s, 409); self.assertEqual(d["duplicates"][0]["id"], m["id"])
        # far away is fine
        self.assertEqual(self.add(41.35, 69.35)[0], 201)
        # edit times
        s, m2 = self.call("PATCH", "/api/mosques/" + m["id"],
                          {"expected_version": 1, "name": "Test", "prayer_times": {"fajr": "05:10", "jumuah": "13:00"}})
        self.assertEqual((s, m2["version"], m2["prayer_times"]["fajr"]), (200, 2, "05:10"))
        # stale version rejected
        self.assertEqual(self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": 1, "name": "X"})[0], 409)
        # validation
        self.assertEqual(self.call("PATCH", "/api/mosques/" + m["id"],
                                   {"expected_version": 2, "prayer_times": {"fajr": "25:99"}})[0], 400)
        self.assertEqual(self.add(100, 0)[0], 400)
        # history
        s, h = self.call("GET", "/api/mosques/%s/history" % m["id"])
        self.assertEqual([x["action"] for x in h["items"]], ["edit", "create"])
        # reports: grouped, threshold -> reported
        self.assertEqual(self.call("POST", "/api/mosques/%s/reports" % m["id"], {"kind": "closed", "reason": "yopilgan"}, client="bbbbbbbb-1")[0], 201)
        self.assertEqual(self.call("POST", "/api/mosques/%s/reports" % m["id"], {"kind": "closed", "reason": "yopilgan"}, client="bbbbbbbb-1")[0], 409)
        self.assertEqual(self.call("POST", "/api/mosques/%s/reports" % m["id"], {"kind": "closed"}, client="cccccccc-1")[0], 400)
        self.call("POST", "/api/mosques/%s/reports" % m["id"], {"kind": "closed", "reason": "yo'q"}, client="cccccccc-1")
        self.assertEqual(self.call("GET", "/api/mosques/" + m["id"])[1]["status"], "reported")
        # admin only
        self.assertEqual(self.call("GET", "/api/admin/reports")[0], 401)
        s, g = self.call("GET", "/api/admin/reports", admin=True)
        self.assertEqual(g["groups"][0]["count"], 2)
        # archive keeps history, hides from map, restorable
        s, a = self.call("POST", "/api/admin/mosques/%s/archive" % m["id"], {"note": "closed"}, admin=True)
        self.assertEqual(a["status"], "archived")
        self.assertNotIn(m["id"], [x["id"] for x in self.call("GET", "/api/mosques")[1]["items"]])
        self.assertEqual(len(self.call("GET", "/api/mosques/%s/history" % m["id"])[1]["items"]), 4)
        self.assertEqual(self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": a["version"], "name": "z"})[0], 409)
        self.assertEqual(self.call("POST", "/api/admin/mosques/%s/restore" % m["id"], {}, admin=True)[1]["status"], "unverified")
        # verify needs evidence
        self.assertEqual(self.call("POST", "/api/admin/mosques/%s/verify" % m["id"], {}, admin=True)[0], 400)

    def test_immutability(self):
        s, m = self.add(40.0, 65.0)
        with psycopg.connect(TEST_URL, autocommit=True) as db:
            for sql in ("DELETE FROM mosque_history", "UPDATE mosque_history SET actor='x'",
                        "TRUNCATE mosque_history", "DELETE FROM mosques"):
                with self.assertRaises(psycopg.errors.RaiseException): db.execute(sql)
        self.assertEqual(self.call("DELETE", "/api/mosques/" + m["id"])[0], 404)

    def test_osm_import(self):
        import import_osm
        self.add(39.0, 66.0, name="Ours")
        data = {"elements": [
            {"type": "node", "id": 1, "lat": 39.5, "lon": 66.5, "tags": {"name": "OSM masjid", "amenity": "place_of_worship"}},
            {"type": "way", "id": 2, "center": {"lat": 39.6, "lon": 66.6}, "tags": {}},
            {"type": "node", "id": 3, "lat": 39.00005, "lon": 66.00005, "tags": {"name": "Too close"}}]}
        import_osm.run(data, app.Config()); import_osm.run(data, app.Config())   # idempotent
        s, items = self.call("GET", "/api/mosques?bbox=66.4,39.4,66.7,39.7")
        self.assertEqual(sorted((m["origin"], m["source"]) for m in items["items"]), [("imported", "openstreetmap")] * 2)
        self.assertEqual(self.call("GET", "/api/mosques?bbox=65.9,38.9,66.1,39.1")[1]["items"][0]["name"], "Ours")
        s, g = self.call("GET", "/api/export/osm-derived.geojson")
        self.assertEqual(len(g["features"]), 2); self.assertIn("ODbL", g["license"])

    def test_osm_resync_updates_untouched_and_protects_edited(self):
        import import_osm
        def el(i, lat, name): return {"type": "node", "id": i, "lat": lat, "lon": 70.0, "tags": {"name": name}}
        first = {"elements": [el(901, 38.1, "Old A"), el(902, 38.2, "Old B"), el(903, 38.3, "Old C")]}
        r = import_osm.run(first, app.Config()); self.assertEqual(r["added"], 3)
        s, lst = self.call("GET", "/api/mosques?bbox=69.9,38.0,70.1,38.4")
        by_name = {m["name"]: m for m in lst["items"]}
        # a human edits B; C gets archived by a moderator; A is untouched
        s, _ = self.call("PATCH", "/api/mosques/" + by_name["Old B"]["id"], {"expected_version": 1, "name": "Community B"})
        self.assertEqual(s, 200)
        self.call("POST", "/api/admin/mosques/%s/archive" % by_name["Old C"]["id"], {}, admin=True)
        second = {"elements": [el(901, 38.1, "New A"), el(902, 38.2, "OSM B changed"), el(903, 38.3, "New C")]}
        r = import_osm.run(second, app.Config())
        self.assertEqual((r["updated"], r["protected_edited"]), (1, 2))
        self.assertEqual(self.call("GET", "/api/mosques/" + by_name["Old A"]["id"])[1]["name"], "New A")        # followed OSM
        self.assertEqual(self.call("GET", "/api/mosques/" + by_name["Old B"]["id"])[1]["name"], "Community B")  # human edit wins
        self.assertEqual(self.call("GET", "/api/mosques/" + by_name["Old C"]["id"])[1]["status"], "archived")
        actions = [h["action"] for h in self.call("GET", "/api/mosques/%s/history" % by_name["Old A"]["id"])[1]["items"]]
        self.assertEqual(actions, ["osm_sync", "create"])                                                       # audit trail kept
        r = import_osm.run(second, app.Config()); self.assertEqual(r["updated"], 0)                             # idempotent
        third = {"elements": [el(901, 38.1, "New A")] + [el(1000 + i, 38.5 + i / 100.0, "X%d" % i) for i in range(3)]}
        self.assertEqual(import_osm.run(third, app.Config())["missing_in_osm"] >= 2, True)                      # vanished: counted only

    def db(self, sql, *args):
        with psycopg.connect(TEST_URL, autocommit=True) as c:
            return c.execute(sql, args).fetchall()

    def test_actor_tracking_web_and_mobile(self):
        # web client: actor row + non-personal snapshot in the audit log
        s, m = self.add(36.1, 61.1, name="Actor test")
        row = self.db("SELECT kind, platform, edits_count, ip_hash FROM actors WHERE actor_id='client:aaaaaaaa-1'")[0]
        self.assertEqual((row[0], row[1]), ("web", "web")); self.assertGreaterEqual(row[2], 1); self.assertEqual(len(row[3]), 24)
        # mobile app: device id + platform/app/model/os headers
        mob = {"X-Device-Id": "dev-ABCDEF123456", "X-Client-Platform": "ios", "X-App-Version": "1.4.2",
               "X-OS-Version": "iOS 19.1", "X-Device-Model": "iPhone16,2"}
        s, m2 = self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": 1, "name": "Changed on phone"}, headers=mob)
        self.assertEqual(s, 200)
        a = self.db("SELECT kind, platform, app_version, os_version, device_model, device_id, edits_count "
                    "FROM actors WHERE actor_id='device:dev-ABCDEF123456'")[0]
        self.assertEqual(a, ("mobile", "ios", "1.4.2", "iOS 19.1", "iPhone16,2", "dev-ABCDEF123456", 1))
        h = self.db("SELECT actor, client FROM mosque_history WHERE mosque_id=%s AND action='edit'", m["id"])[0]
        self.assertEqual(h[0], "device:dev-ABCDEF123456")
        self.assertEqual({k: h[1][k] for k in ("kind", "platform", "app_version", "device_model")},
                         {"kind": "mobile", "platform": "ios", "app_version": "1.4.2", "device_model": "iPhone16,2"})
        self.assertNotIn("ip_hash", h[1])
        # reports keep the snapshot too and bump the counter
        self.call("POST", "/api/mosques/%s/reports" % m["id"], {"kind": "other", "reason": "test"}, headers=dict(mob, **{"X-Device-Id": "dev-REPORTER9999"}))
        self.assertEqual(self.db("SELECT reports_count FROM actors WHERE actor_id='device:dev-REPORTER9999'")[0][0], 1)
        # PUBLIC history exposes roles only: no ids, no device info
        items = self.call("GET", "/api/mosques/%s/history" % m["id"])[1]["items"]
        self.assertEqual([i["actor"] for i in items], ["user", "user"])
        for i in items:
            self.assertNotIn("client", i)
        raw = json.dumps(self.call("GET", "/api/mosques?bbox=61,36,61.2,36.2")[1])
        for needle in ("dev-ABCDEF123456", "aaaaaaaa-1", "creator"):
            self.assertNotIn(needle, raw)
        # admin sees the details
        s, ov = self.call("GET", "/api/admin/overview", admin=True)
        mine = [r for r in ov["recent"] if r["mosque_id"] == m["id"] and r["action"] == "edit"][0]
        self.assertEqual((mine["actor_kind"], mine["platform"], mine["app_version"]), ("mobile", "ios", "1.4.2"))
        s, ac = self.call("GET", "/api/admin/actors?q=ABCDEF123456", admin=True)
        self.assertEqual([x["actor_id"] for x in ac["items"]], ["device:dev-ABCDEF123456"])
        self.assertEqual(self.call("GET", "/api/admin/actors")[0], 401)

    def test_search_and_admin_views(self):
        self.add(37.5, 64.5, name="Alisher Navoiy jome masjidi")
        self.add(37.6, 64.6, name="100%_special")
        s, r = self.call("GET", "/api/search?q=navoiy")
        self.assertEqual([m["name"] for m in r["items"]], ["Alisher Navoiy jome masjidi"])
        self.assertEqual(self.call("GET", "/api/search?q=%25")[1]["items"], [])      # '%' is literal, not a wildcard
        self.assertEqual(self.call("GET", "/api/search?q=a")[1]["items"], [])        # too short
        self.assertEqual(self.call("GET", "/api/admin/overview")[0], 401)
        s, ov = self.call("GET", "/api/admin/overview", admin=True)
        self.assertEqual(s, 200); self.assertIn("recent", ov); self.assertGreaterEqual(ov["counts"]["community/unverified"], 2)
        self.assertNotIn("import:osm", [h["actor"] for h in ov["recent"]])
        s, lst = self.call("GET", "/api/admin/mosques?status=archived", admin=True)
        self.assertEqual(s, 200); self.assertIn("total", lst)
        self.assertEqual(self.call("GET", "/api/admin/mosques?status=bogus", admin=True)[0], 400)

    def test_head_requests(self):
        for path in ("/", "/api/health", "/vendor/maplibre-gl.js"):
            req = urllib.request.Request(self.base + path, method="HEAD", headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200); self.assertEqual(r.read(), b"")
                self.assertGreater(int(r.headers["Content-Length"]), 0)

    def test_static_and_traversal(self):
        with urllib.request.urlopen(self.base + "/") as r: self.assertIn(b"maplibre", r.read().lower())
        try: urllib.request.urlopen(self.base + "/..%2fserver/app.py"); self.fail()
        except urllib.error.HTTPError as e: self.assertEqual(e.code, 404)

def make_server(**extra):
    """Fresh server on a free port against the test DB, independent of test order and of the real .env."""
    env = dict(DATABASE_URL=TEST_URL, ADMIN_TOKEN="secret", PORT="0", WRITE_RATE_PER_MIN="1000", **extra)
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        srv = app.build_server(app.Config())
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]

class TelegramIdentity(unittest.TestCase):
    TOKEN = "123456:TEST-TOKEN"

    def init_data(self, user, tamper=False):
        import hashlib
        import hmac
        import time
        from urllib.parse import urlencode
        pairs = {"auth_date": str(int(time.time())), "query_id": "AAH", "user": json.dumps(user)}
        check = "\n".join("%s=%s" % (k, pairs[k]) for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", self.TOKEN.encode(), hashlib.sha256).digest()
        pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if tamper:
            pairs["user"] = json.dumps(dict(user, id=999))        # signature no longer matches
        return urlencode(pairs)

    def test_verified_telegram_user_is_recorded_and_forgery_is_not(self):
        srv, base = make_server(TELEGRAM_BOT_TOKEN=self.TOKEN)
        def post(init, cid):
            req = urllib.request.Request(base + "/api/mosques", method="POST", data=json.dumps({"lat": 35.0 + len(cid) / 100.0, "lon": 60.0}).encode(),
                                         headers={"Content-Type": "application/json", "X-Client-Id": cid, "X-Telegram-Init-Data": init})
            with urllib.request.urlopen(req) as r: return json.loads(r.read())
        user = {"id": 777001, "username": "nodir_test", "first_name": "Nodir", "last_name": "T", "language_code": "uz"}
        m = post(self.init_data(user), "cccccccc-1")
        with psycopg.connect(TEST_URL, autocommit=True) as c:
            row = c.execute("SELECT kind, telegram_id, tg_username, tg_first_name, tg_language FROM actors WHERE actor_id='tg:777001'").fetchall()
            self.assertEqual(row, [("telegram", 777001, "nodir_test", "Nodir", "uz")])
            self.assertEqual(c.execute("SELECT creator_id FROM mosques WHERE id=%s", (m["id"],)).fetchone()[0], "tg:777001")
            # forged initData: ignored, falls back to the browser id, and NO Telegram profile is stored
            m2 = post(self.init_data(user, tamper=True), "dddddddd-12")
            self.assertEqual(c.execute("SELECT creator_id FROM mosques WHERE id=%s", (m2["id"],)).fetchone()[0], "client:dddddddd-12")
            self.assertEqual(c.execute("SELECT count(*) FROM actors WHERE telegram_id=999").fetchone()[0], 0)
        srv.shutdown()

class AdminLocalOnly(unittest.TestCase):
    def test_tunnelled_requests_cannot_reach_admin(self):
        srv, base = make_server(ADMIN_LOCAL_ONLY="1")
        def get(path, **h):
            req = urllib.request.Request(base + path, headers=dict({"Authorization": "Bearer secret"}, **h))
            try: return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e: return e.code
        self.assertEqual(get("/api/admin/overview"), 200)                                   # direct, local
        self.assertEqual(get("/admin.html"), 200)
        for h in ({"X-Forwarded-For": "1.2.3.4"}, {"Host": "abc.ngrok-free.app"}, {"Forwarded": "for=1.2.3.4"}, {"CF-Connecting-IP": "1.2.3.4"}):
            self.assertEqual(get("/api/admin/overview", **h), 404, h)                       # via tunnel
            self.assertEqual(get("/admin.html", **h), 404, h)
            self.assertEqual(get("/api/config", **h), 200, h)                               # public parts still work
        srv.shutdown()

class BruteForce(unittest.TestCase):
    """Own server instance: the failed-login counter is per process and must not leak into other tests."""
    def test_admin_bruteforce_limit(self):
        srv, base = make_server()
        def hit(token):
            req = urllib.request.Request(base + "/api/admin/overview", headers={"Authorization": "Bearer " + token})
            try: return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e: return e.code
        codes = [hit("wrong") for _ in range(12)]
        self.assertEqual(codes[:10], [401] * 10); self.assertEqual(codes[10:], [429, 429])
        self.assertEqual(hit("secret"), 429)          # locked out for the rest of the minute, even with the right token
        srv.shutdown()

if __name__ == "__main__":
    unittest.main()
