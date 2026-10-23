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

BOT_TOKEN = "123456:TEST-TOKEN"
ADMIN_ID = 424242


def sign_init_data(user, token=BOT_TOKEN, age=0):
    """Telegram WebApp initData exactly as Telegram signs it (HMAC-SHA256 with key HMAC('WebAppData', bot token))."""
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "AAH", "user": json.dumps(user)}
    check = "\n".join("%s=%s" % (k, pairs[k]) for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        with psycopg.connect(TEST_URL, autocommit=True) as c:   # fresh schema for every run
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        os.environ.update(DATABASE_URL=TEST_URL, ADMIN_TOKEN="secret", PORT="0", TELEGRAM_BOT_TOKEN=BOT_TOKEN,
                          ADMIN_TG_IDS=str(ADMIN_ID), ADMIN_ALLOW_TOKEN="0", WRITE_RATE_PER_MIN="1000", REPORT_THRESHOLD="2")
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
        if admin: h["X-Telegram-Init-Data"] = sign_init_data({"id": ADMIN_ID, "first_name": "Owner"})
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
        refs = {f["properties"]["osm_ref"] for f in g["features"]}
        self.assertLessEqual({"node/1", "way/2"}, refs); self.assertIn("ODbL", g["license"])      # other tests also import: do not rely on the total

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

    def test_address_enrichment_from_nearest_osm_objects(self):
        import enrich_address as en
        import import_osm
        def el(i, lat, lon, name): return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": {"name": name}}
        data = {"elements": [el(7001, 33.0000, 71.0000, "Near number"), el(7002, 33.1000, 71.1000, "Near road"), el(7003, 33.2000, 71.2000, "Nothing near"),
                             el(7004, 33.3000, 71.3000, "Edited by human")]}
        import_osm.run(data, app.Config())
        s, lst = self.call("GET", "/api/mosques?bbox=70.9,32.9,71.4,33.4")
        ids = {m["name"]: m["id"] for m in lst["items"]}
        self.call("PATCH", "/api/mosques/" + ids["Edited by human"], {"expected_version": 1, "name": "Edited by human"})   # no-op edit
        self.call("PATCH", "/api/mosques/" + ids["Edited by human"], {"expected_version": 1, "name": "Edited by human 2"})
        addr_idx = {en.cell(33.00005, 71.0): [(33.00005, 71.0, "Muqimiy ko'chasi", "166", "Toshkent")],        # ~5.5 m from the first mosque
                    en.cell(33.3, 71.3): [(33.3, 71.3, "Should not apply", "1", "")]}                         # next to the human-edited one
        road_idx = {}
        pts = [(33.1003, 71.0990), (33.1003, 71.1010)]                                                        # a road ~33 m north of the second
        for c in {en.cell(a, b) for a, b in pts} | {en.cell(33.1003, 71.1000)}:
            road_idx.setdefault(c, []).append(("Alisher Navoiy ko'chasi", pts))
        r = en.run(addr_idx, road_idx, app.Config())
        self.assertEqual((r["street_and_number"], r["street_only"], r["still_without"]), (1, 1, 1))
        got = {n: self.call("GET", "/api/mosques/" + i)[1]["address"] for n, i in ids.items()}
        self.assertEqual(got["Near number"], "Muqimiy ko'chasi 166, Toshkent")
        self.assertEqual(got["Near road"], "Alisher Navoiy ko'chasi")
        self.assertIsNone(got["Nothing near"])
        self.assertIsNone(got["Edited by human"])                                                             # human-touched: never enriched
        notes = self.db("SELECT note FROM mosque_history WHERE mosque_id=%s AND action='osm_sync'", ids["Near number"])
        self.assertIn("approximate", notes[0][0])
        # a later OSM sync must not erase the enriched address just because OSM has no address tags
        r2 = import_osm.run(data, app.Config())
        self.assertEqual(r2["updated"], 0)
        self.assertEqual(self.call("GET", "/api/mosques/" + ids["Near number"])[1]["address"], "Muqimiy ko'chasi 166, Toshkent")

    def test_times_updated_at_tracks_only_changes_of_the_times(self):
        s, m = self.add(31.0, 73.0, name="Times stamp")                       # no times yet
        self.assertIsNone(m["times_updated_at"])
        s, m = self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": 1, "name": "Times stamp 2"})
        self.assertIsNone(m["times_updated_at"])                              # a name edit is not a times change
        s, m2 = self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": m["version"], "prayer_times": {"fajr": "05:10"}})
        stamp = m2["times_updated_at"]
        self.assertTrue(stamp)
        s, m3 = self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": m2["version"], "address": "Somewhere"})
        self.assertEqual(m3["times_updated_at"], stamp)                       # unrelated edit keeps the stamp
        s, m4 = self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": m3["version"], "prayer_times": {"fajr": None}})
        self.assertIsNone(m4["times_updated_at"])                             # all times cleared -> "not entered"
        s, c = self.add(31.5, 73.5, name="Born with times", prayer_times={"isha": "20:00"})
        self.assertTrue(c["times_updated_at"])                                # entered at creation

    def test_nearest_mosques_are_really_the_nearest(self):
        import math
        pts = [(34.0000, 72.0000), (34.0030, 72.0000), (34.0100, 72.0100), (34.1000, 72.1000), (34.5000, 72.5000)]
        for i, (la, lo) in enumerate(pts):
            self.add(la, lo, name="N%d" % i, force=True)
        me = (34.0005, 72.0001)
        s, r = self.call("GET", "/api/mosques/nearest?lat=%s&lon=%s&limit=3" % me)
        self.assertEqual(s, 200)
        names = [m["name"] for m in r["items"]]
        self.assertEqual(names, ["N0", "N1", "N2"])                                  # ordered by distance
        d = [m["distance_m"] for m in r["items"]]
        self.assertEqual(d, sorted(d))
        def hav(a, b):
            R = 6371000.0; p1, p2 = math.radians(a[0]), math.radians(b[0]); dl = math.radians(b[1] - a[1])
            x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * R * math.asin(math.sqrt(x))
        self.assertAlmostEqual(d[0], hav(me, pts[0]), delta=1.0)                   # distance in metres is right
        self.assertNotIn("creator_id", r["items"][0])
        far = self.call("GET", "/api/mosques/nearest?lat=34.4&lon=72.4&limit=1")[1]["items"]      # only the far ones qualify
        self.assertEqual(far[0]["name"], "N4" if hav((34.4, 72.4), pts[4]) < hav((34.4, 72.4), pts[3]) else "N3")
        self.assertEqual(self.call("GET", "/api/mosques/nearest?lat=95&lon=0")[0], 400)
        self.assertEqual(self.call("GET", "/api/mosques/nearest?lat=34&lon=72&limit=x")[0], 400)

    def test_osm_address_building(self):
        import import_osm as io_
        a = io_.osm_address
        self.assertEqual(a({"addr:full": "  Оқлон маҳалла, Кўкча дарвоза кўчаси 27 "}), "Оқлон маҳалла, Кўкча дарвоза кўчаси 27")
        self.assertEqual(a({"addr:street": "Quva ko'chasi", "addr:housenumber": "81a", "addr:city": "Farg'ona"}), "Quva ko'chasi 81a, Farg'ona")
        self.assertEqual(a({"addr:city": "Angren", "addr:postcode": "110200"}), "Angren, 110200")
        self.assertIsNone(a({"addr:postcode": "110200"}))              # postcode alone is not an address
        self.assertIsNone(a({"name": "x"}))
        self.assertEqual(a({"addr:full": "A\u202eB\x00C"}), "ABC")                # control/bidi characters are stripped

    def test_admin_only_for_allow_listed_verified_telegram_id(self):
        srv, base = make_server()                      # own server: its failed-login counter must not interact with other tests
        url = base + "/api/admin/overview"
        def get(headers):
            req = urllib.request.Request(url, headers=headers)
            try: return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e: return e.code
        owner = {"id": ADMIN_ID, "first_name": "Owner"}
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data(owner)}), 200)                       # allow-listed + signed
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data({"id": 111, "first_name": "Eve"})}), 401)   # signed, not allow-listed
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data(owner, token="999:OTHER")}), 401)    # signed with another bot token
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data(owner, age=3 * 3600)}), 401)         # launch data older than the session limit
        forged = sign_init_data({"id": 111, "first_name": "Eve"}).replace("111", str(ADMIN_ID))
        self.assertEqual(get({"X-Telegram-Init-Data": forged}), 401)                                      # claiming the owner's id breaks the signature
        self.assertEqual(get({"Authorization": "Bearer secret"}), 401)                                    # token login is off by default
        self.assertEqual(get({"X-Client-Id": "aaaaaaaa-1"}), 401)
        srv.shutdown()
        # moderator actions are attributed to the Telegram id, shown publicly only as the role
        s, m = self.add(36.5, 62.5, name="Role test")
        self.call("POST", "/api/admin/mosques/%s/archive" % m["id"], {"note": "t"}, admin=True)
        self.assertEqual(self.db("SELECT actor FROM mosque_history WHERE mosque_id=%s AND action='archive'", m["id"])[0][0], "admin:%d" % ADMIN_ID)
        items = self.call("GET", "/api/mosques/%s/history" % m["id"])[1]["items"]
        self.assertEqual(items[0]["actor"], "moderator"); self.assertNotIn(str(ADMIN_ID), json.dumps(items))

    def test_admin_disabled_without_allow_list(self):
        srv, base = make_server(ADMIN_TG_IDS="")
        req = urllib.request.Request(base + "/api/admin/overview", headers={"X-Telegram-Init-Data": sign_init_data({"id": ADMIN_ID})})
        try: code = urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e: code = e.code
        self.assertEqual(code, 401)                                   # empty allow-list = nobody is a moderator
        srv.shutdown()

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
    env = dict(DATABASE_URL=TEST_URL, ADMIN_TOKEN="secret", PORT="0", WRITE_RATE_PER_MIN="1000", TELEGRAM_BOT_TOKEN=BOT_TOKEN,
               ADMIN_TG_IDS=str(ADMIN_ID), ADMIN_ALLOW_TOKEN="0", ADMIN_LOCAL_ONLY="0")   # explicit: the developer's .env must not leak into tests
    env.update(extra)
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

class TimetableParser(unittest.TestCase):
    def test_parser_cases(self):
        import timetable as T
        five = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        cases = {
            "Bomdod 05:20\nQuyosh 06:40\nPeshin 12:30\nAsr 16:00\nShom 17:35\nXufton 19:00": five,
            "Бомдод 5.20\nПешин 12-30\nАср 16:00\nШом 17:35\nХуфтон 19:00": five,
            "Фаджр 05:20\nВосход 06:40\nЗухр 12:30\nАср 16:00\nМагриб 17:35\nИша 19:00": five,
            "05:20 12:30 16:00 17:35 19:00": five,
            "05:20 06:40 12:30 16:00 17:35 19:00": five,
            "Bomdod O5:2O\nPeshin l2:30\nAsr l6:OO\nShom l7:35\nXufton l9:OO": five,       # OCR confuses O/0 and l/1
        }
        for text, want in cases.items():
            r = T.parse(text)
            self.assertTrue(r["complete"], text); self.assertEqual(r["times"], want, text)
        two = T.parse("Bomdod 05:20 05:40\nPeshin 12:30 12:45\nAsr 16:00 16:15\nShom 17:35 17:40\nXufton 19:00 19:15")
        self.assertFalse(two["complete"]); self.assertEqual(two["alt"]["fajr"], "05:40"); self.assertIn("two_columns", two["warnings"])
        rep = T.parse("Bomdod 05:20\nPeshin 12:30\nAsr 6:00\nShom 17:35\nXufton 19:00")           # OCR lost the "1" of 16:00
        self.assertTrue(rep["complete"]); self.assertEqual(rep["times"]["asr"], "16:00"); self.assertIn("repaired", rep["warnings"])
        self.assertFalse(T.parse("05:20 12:30 11:00 03:00 19:00")["complete"])                  # not repairable -> never complete
        for bad in ("Bomdod 05:20\nAsr 16:00", "Assalomu alaykum 2026", "05:20 12:30 11:00 17:35 19:00", "99:99 88:88", ""):
            self.assertFalse(T.parse(bad)["complete"], bad)                # never "complete" on doubtful input


class BotFlow(unittest.TestCase):
    """The conversation logic with fake Telegram/OCR: location -> pick -> photo -> confirm -> saved, and the safety rules."""

    def setUp(self):
        import bot as botmod
        self.botmod = botmod
        with psycopg.connect(TEST_URL, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        os.environ.update(DATABASE_URL=TEST_URL)
        self.store = app.Store(app.Config())
        self.sent = []
        self.ocr_text = ["Bomdod 05:20\nPeshin 12:30\nAsr 16:00\nShom 17:35\nXufton 19:00"]
        self.bot = botmod.Bot(lambda m, **kw: self.sent.append((m, kw)), self.store, lambda data: self.ocr_text, lambda fid: b"img",
                              "https://example.org", admin_ids=[7])
        self.user = {"id": 555, "first_name": "Ali", "username": "ali_u", "language_code": "uz"}
        self.m = self.store.create({"lat": 40.0, "lon": 66.0, "name": "Bot mosque"}, "client:test0000", {"kind": "web", "platform": "web"})

    def msgs(self):
        return [kw["text"] for m, kw in self.sent if m == "sendMessage"]

    def buttons(self):
        for m, kw in reversed(self.sent):
            if m == "sendMessage" and "reply_markup" in kw:
                return [b for row in kw["reply_markup"]["inline_keyboard"] for b in row]
        return []

    def send(self, **kw):
        base = {"chat": {"id": 555, "type": "private"}, "from": self.user}
        self.bot.handle({"update_id": 1, "message": dict(base, **kw)})

    def click(self, data):
        self.bot.handle({"update_id": 2, "callback_query": {"id": "q", "from": self.user, "data": data, "message": {"chat": {"id": 555}}}})

    def test_location_photo_confirm_save_and_link(self):
        self.send(location={"latitude": 40.0004, "longitude": 66.0})
        picks = [b for b in self.buttons() if b["callback_data"].startswith("pick:")]
        self.assertEqual(len(picks), 1); self.assertIn("Bot mosque", picks[0]["text"])
        self.click(picks[0]["callback_data"])
        self.send(photo=[{"file_id": "a", "file_size": 10}, {"file_id": "b", "file_size": 99}])
        self.assertIn("05:20", self.msgs()[-1]); self.assertIn("19:00", self.msgs()[-1])
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"], {})                      # NOT saved before confirmation
        self.click("save")
        cur = self.store.get(self.m["id"])
        self.assertEqual(cur["prayer_times"]["asr"], "16:00"); self.assertTrue(cur["times_updated_at"])
        self.assertIn("oʻzgardi", self.msgs()[-1])                                              # "the times changed, you can check"
        link = self.buttons()[0]["web_app"]["url"]
        self.assertEqual(link, "https://example.org/?m=" + self.m["id"])
        with psycopg.connect(TEST_URL, autocommit=True) as c:                                  # who/what recorded
            self.assertEqual(c.execute("SELECT tg_username, platform FROM actors WHERE actor_id='tg:555'").fetchone(), ("ali_u", "telegram-bot"))
            self.assertEqual(c.execute("SELECT actor FROM mosque_history WHERE mosque_id=%s AND action='edit'", (self.m["id"],)).fetchone()[0], "tg:555")

    def test_unreadable_photo_and_typed_times(self):
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        self.ocr_text = ["Bomdod 05:20\nAsr 16:00", "garbage"]
        self.send(photo=[{"file_id": "a", "file_size": 10}])
        self.assertIn("ishonchli", self.msgs()[-1]); self.assertEqual(self.store.get(self.m["id"])["prayer_times"], {})
        self.send(text="05:20 12:30 16:00 17:35 19:00"); self.click("save")
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"]["isha"], "19:00")
        self.send(text="hello")                                                                 # session is over
        self.assertIn("lokatsiya", self.msgs()[-1])

    def test_best_of_prefers_two_columns_and_majority(self):
        import timetable as T
        one = "Bomdod 05:40\nPeshin 12:45\nAsr 16:15\nShom 17:40\nXufton 19:15"                # a pass that lost the second column
        two = "Bomdod 05:40 05:20\nPeshin 12:45 12:30\nAsr 16:15 16:00\nShom 17:40 17:35\nXufton 19:15 19:00"
        self.assertTrue(T.best_of([one, one, two])["alt"])                                      # one pass seeing two columns is enough
        good, odd = "05:20 12:30 16:00 17:35 19:00", "05:20 12:31 16:00 17:35 19:00"
        self.assertEqual(T.best_of([odd, good, good])["times"]["dhuhr"], "12:30")               # majority wins
        self.assertEqual(T.best_of([])["complete"], False)

    def test_two_columns_asks_which_one(self):
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        self.ocr_text = ["Bomdod 05:20 05:40\nPeshin 12:30 12:45\nAsr 16:00 16:15\nShom 17:35 17:40\nXufton 19:00 19:15"]
        self.send(photo=[{"file_id": "a", "file_size": 10}])
        self.assertEqual([b["callback_data"] for b in self.buttons()][:2], ["col1", "col2"])
        self.click("col2"); self.click("save")
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"]["fajr"], "05:40")

    def test_new_mosque_from_location_and_safety(self):
        self.send(location={"latitude": 41.5, "longitude": 67.5})                               # nothing within 200 m
        self.assertEqual([b["callback_data"][:4] for b in self.buttons()], ["new:"])
        self.click(self.buttons()[0]["callback_data"]); self.send(text="Yangi jome")
        new = [m for m in self.store.list((67.4, 41.4, 67.6, 41.6))]
        self.assertEqual([m["name"] for m in new], ["Yangi jome"])
        self.send(photo=[{"file_id": "a"}]); self.click("save")
        self.assertTrue(self.store.get(new[0]["id"])["prayer_times"])
        # a photo with no session is refused; groups are ignored; admin button only for admins
        self.sent.clear(); self.user = {"id": 556, "first_name": "Eve", "language_code": "en"}
        self.send(photo=[{"file_id": "a"}]); self.assertIn("location", self.msgs()[-1])
        self.send(text="/admin"); self.assertEqual(len(self.msgs()), 1)                         # no reply to a non-admin
        self.bot.handle({"update_id": 3, "message": {"chat": {"id": -1, "type": "group"}, "from": self.user, "text": "/id"}})
        self.assertEqual(len(self.msgs()), 1)
        self.user = {"id": 7, "first_name": "Owner", "language_code": "ru"}; self.send(text="/admin")
        self.assertTrue(self.buttons()[0]["web_app"]["url"].endswith("/admin.html"))


@unittest.skipUnless(__import__("ocr").available() and os.path.exists("/System/Library/Fonts/Supplemental/Arial Bold.ttf"), "needs tesseract and a font")
class RealOcr(unittest.TestCase):
    def test_generated_timetable_photo_is_read(self):
        import io
        import ocr
        import timetable
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 44)
        im = Image.new("RGB", (900, 600), (236, 246, 238)); d = ImageDraw.Draw(im)
        for i, (n, v) in enumerate((("Bomdod", "05:20"), ("Peshin", "12:30"), ("Asr", "16:00"), ("Shom", "17:35"), ("Xufton", "19:00"))):
            d.text((80, 60 + i * 100), n, font=font, fill=(10, 60, 40)); d.text((600, 60 + i * 100), v, font=font, fill=(10, 60, 40))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=60)
        r = timetable.best_of(ocr.read_texts(buf.getvalue()))
        self.assertTrue(r["complete"]); self.assertEqual(r["times"]["asr"], "16:00"); self.assertEqual(r["times"]["isha"], "19:00")


class AdminLocalOnly(unittest.TestCase):
    def test_tunnelled_requests_cannot_reach_admin(self):
        srv, base = make_server(ADMIN_LOCAL_ONLY="1", ADMIN_ALLOW_TOKEN="1")
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
        def hit(init):
            req = urllib.request.Request(base + "/api/admin/overview", headers={"X-Telegram-Init-Data": init})
            try: return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e: return e.code
        codes = [hit("hash=bad&user=%7B%7D") for _ in range(12)]
        self.assertEqual(codes[:10], [401] * 10); self.assertEqual(codes[10:], [429, 429])
        self.assertEqual(hit(sign_init_data({"id": ADMIN_ID})), 429)   # locked out for the rest of the minute, even with valid data
        srv.shutdown()

if __name__ == "__main__":
    unittest.main()
