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
        # an old form (stale version) is NOT an error: its fields are applied on top of the latest state
        s, m_x = self.call("PATCH", "/api/mosques/" + m["id"], {"expected_version": 1, "name": "X"})
        self.assertEqual((s, m_x["name"], m_x["prayer_times"]["fajr"]), (200, "X", "05:10"))          # the times saved meanwhile survive
        # validation
        self.assertEqual(self.call("PATCH", "/api/mosques/" + m["id"],
                                   {"expected_version": 2, "prayer_times": {"fajr": "25:99"}})[0], 400)
        self.assertEqual(self.add(100, 0)[0], 400)
        # history
        s, h = self.call("GET", "/api/mosques/%s/history" % m["id"])
        self.assertEqual([x["action"] for x in h["items"]], ["edit", "edit", "create"])
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
        self.assertEqual(len(self.call("GET", "/api/mosques/%s/history" % m["id"])[1]["items"]), 5)
        self.assertEqual(self.call("PATCH", "/api/mosques/" + m["id"], {"name": "z"})[0], 409)                  # archived: not editable
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

    def test_many_users_at_once_never_get_errors_and_nothing_is_lost(self):
        from concurrent.futures import ThreadPoolExecutor
        s, m = self.add(30.0, 74.0, name="Busy mosque")
        mid = m["id"]
        keys = ["fajr", "dhuhr", "asr", "maghrib", "isha"]
        jobs = [("name", "Name %02d" % i) for i in range(20)] + [("time", k) for k in keys] + [("report", i) for i in range(6)] * 1
        def work(job):
            kind, v = job
            if kind == "name":                                                      # every writer holds the same stale version 1
                return self.call("PATCH", "/api/mosques/" + mid, {"expected_version": 1, "name": v}, client="e%07d-1" % hash(v) if False else "eeeeeeee-1")[0]
            if kind == "time":
                times = {"fajr": "05:10", "dhuhr": "12:20", "asr": "16:00", "maghrib": "17:30", "isha": "19:00"}
                return self.call("PATCH", "/api/mosques/" + mid, {"expected_version": 1, "prayer_times": {v: times[v]}}, client="ffffffff-1")[0]
            return self.call("POST", "/api/mosques/%s/reports" % mid, {"kind": "other", "reason": "r%d" % v}, client="%08d-aa" % (100 + v))[0]
        with ThreadPoolExecutor(12) as ex:
            codes = list(ex.map(work, jobs))
        self.assertEqual(sorted(set(codes)), [200, 201])                            # no 409/429/500 for anybody
        final = self.call("GET", "/api/mosques/" + mid)[1]
        self.assertEqual(final["prayer_times"], {"fajr": "05:10", "dhuhr": "12:20", "asr": "16:00", "maghrib": "17:30", "isha": "19:00"})   # all five writers' times kept
        self.assertTrue(final["name"].startswith("Name "))
        edits = [x for x in self.call("GET", "/api/mosques/%s/history" % mid)[1]["items"] if x["action"] == "edit"]
        self.assertEqual(final["version"], 1 + len(edits) + 1)                      # +1: the status change by the reports; versions are gapless
        self.assertEqual(len({x["version"] for x in edits}), len(edits))            # no two edits share a version
        self.assertEqual(final["status"], "reported")
        thr = [x for x in self.call("GET", "/api/mosques/%s/history" % mid)[1]["items"] if x["action"] == "report_threshold"]
        self.assertEqual(len(thr), 1)                                               # the status flipped exactly once
        # many people adding the same missing mosque at the same moment: exactly one record, the rest are told about the duplicate
        def add_same(i):
            return self.add(30.5, 74.5, name="Same spot", force=False)[0]
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(add_same, range(8)))
        self.assertEqual(sorted(res), [201] + [409] * 7)
        self.assertEqual(len(self.call("GET", "/api/mosques?bbox=74.4,30.4,74.6,30.6")[1]["items"]), 1)

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
        five_ = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        wide_ = {                                                                       # long boards: names in one line, times below
            "Bomdod Quyosh Peshin Asr Shom Xufton\n05:20 06:40 12:30 16:00 17:35 19:00": five_,
            "Бомдод Пешин Аср Шом Хуфтон\n05:20 12:30 16:00 17:35 19:00": five_,
            "NAMOZ VAQTLARI\nBomdod  Peshin  Asr  Shom  Xufton\n05:20   12:30   16:00   17:35   19:00": five_,
        }
        for text, want in wide_.items():
            r = T.parse(text); self.assertTrue(r["complete"], text); self.assertEqual(r["times"], want, text)
        rows = T.parse("Bomdod Peshin Asr Shom Xufton\nAzon 05:40 12:45 16:15 17:40 19:15\nJamoat 05:20 12:30 16:00 17:35 19:00")
        self.assertFalse(rows["complete"]); self.assertIn("two_columns", rows["warnings"])          # two rows -> the user picks
        self.assertEqual({rows["times"]["fajr"], rows["alt"]["fajr"]}, {"05:40", "05:20"})
        two = T.parse("Bomdod 05:20 05:40\nPeshin 12:30 12:45\nAsr 16:00 16:15\nShom 17:35 17:40\nXufton 19:00 19:15")
        self.assertFalse(two["complete"]); self.assertEqual(two["alt"]["fajr"], "05:40"); self.assertIn("two_columns", two["warnings"])
        dated = T.parse("JOME MASJIDI\n23.10.2026\n05:20\n06:40\n12:30\n16:00\n17:35\n19:00")           # a date line must not become 23:10
        self.assertTrue(dated["complete"]); self.assertEqual(dated["times"]["fajr"], "05:20"); self.assertIn("sunrise_dropped", dated["warnings"])
        self.assertTrue(T.parse("23/10/2026 05:20 12:30 16:00 17:35 19:00")["complete"])
        rep = T.parse("Bomdod 05:20\nPeshin 12:30\nAsr 6:00\nShom 17:35\nXufton 19:00")           # OCR lost the "1" of 16:00
        self.assertTrue(rep["complete"]); self.assertEqual(rep["times"]["asr"], "16:00"); self.assertIn("ampm_resolved", rep["warnings"])
        self.assertFalse(T.parse("05:20 12:30 11:00 03:00 19:00")["complete"])                  # not repairable -> never complete
        weird = T.parse("Bomdod 09:20\nPeshin 12:30\nAsr 16:00\nShom 17:35\nXufton 19:00")      # fajr at 09:20 cannot be right
        self.assertFalse(weird["complete"]); self.assertIn("implausible", weird["warnings"])
        for bad in ("Bomdod 05:20\nAsr 16:00", "Assalomu alaykum 2026", "05:20 12:30 11:00 17:35 19:00", "99:99 88:88", ""):
            self.assertFalse(T.parse(bad)["complete"], bad)                # never "complete" on doubtful input


class SpokenAndAmPm(unittest.TestCase):
    FIVE = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}

    def test_twelve_hour_times_without_am_pm_land_in_the_right_half_of_the_day(self):
        import timetable as T
        r = T.parse("5:20 12:30 4:00 5:35 7:00")
        self.assertTrue(r["complete"]); self.assertEqual(r["times"], self.FIVE)
        r = T.parse("Bomdod 5:20\nPeshin 1:15\nAsr 4:05\nShom 5:40\nXufton 7:10")
        self.assertEqual(r["times"], {"fajr": "05:20", "dhuhr": "13:15", "asr": "16:05", "maghrib": "17:40", "isha": "19:10"})
        r = T.parse("Fajr 4:10\nDhuhr 12:00\nAsr 5:30\nMaghrib 8:20\nIsha 10:15")              # summer: late sunset, isha 22:15
        self.assertEqual(r["times"], {"fajr": "04:10", "dhuhr": "12:00", "asr": "17:30", "maghrib": "20:20", "isha": "22:15"})
        r = T.parse("Fajr 5:20 AM\nDhuhr 12:30 PM\nAsr 4:00 PM\nMaghrib 5:35 PM\nIsha 7:00 PM")  # explicit markers
        self.assertEqual(r["times"], self.FIVE)
        r = T.parse("5:20 6:00 7:00 8:00 9:00")                                                # nonsense stays unsaved
        self.assertFalse(r["complete"])

    def test_spoken_times_in_uzbek_russian_english(self):
        import spoken as S
        said = [
            "Bomdod besh yigirma, Peshin o'n ikki o'ttiz, Asr o'n olti, Shom o'n yetti o'ttiz besh, Xufton o'n to'qqiz",
            "Бомдод беш йигирма. Пешин ўн икки ўттиз. Аср ўн олти нол нол. Шом ўн етти ўттиз беш. Хуфтон ўн тўққиз",
            "Фаджр пять двадцать, зухр двенадцать тридцать, аср шестнадцать ноль ноль, магриб семнадцать тридцать пять, иша девятнадцать ноль ноль",
            "Fajr five twenty, Dhuhr twelve thirty, Asr sixteen hundred, Maghrib seventeen thirty five, Isha nineteen oh oh",
            "Bomdod 5 20 Peshin 12 30 Asr 16 00 Shom 17 35 Xufton 19 00",
            "Bomdod 5:20, Peshin 12:30, Asr 16:00, Shom 17:35, Xufton 19:00",
            # no AM/PM spoken: 12-hour numbers are placed by the prayer's own time of day
            "Bomdod besh yigirma, Peshin o'n ikki o'ttiz, Asr to'rt, Shom besh o'ttiz besh, Xufton yetti",
            "Fajr five twenty, Dhuhr twelve thirty, Asr four, Maghrib five thirty five, Isha seven",
            "Bomdod 5 20 Peshin 12 30 Asr 4 00 Shom 5 35 Xufton 7 00",
            # explicit markers in words
            "Fajr five twenty am, Dhuhr twelve thirty pm, Asr four pm, Maghrib five thirty five pm, Isha seven pm",
            "Фаджр пять двадцать утра, зухр двенадцать тридцать дня, аср четыре дня, магриб пять тридцать пять вечера, иша семь вечера",
        ]
        for t in said:
            r = S.parse(t)
            self.assertTrue(r["complete"], t); self.assertEqual(r["times"], self.FIVE, t)
        r = S.parse("зухр час пятнадцать дня, фаджр пять двадцать, аср четыре, магриб пять тридцать пять, иша семь")
        self.assertEqual(r["times"]["dhuhr"], "13:15")                                         # Russian "час" = one o'clock
        self.assertEqual(S.normalize("Bomdod nol besh yigirma besh"), "Bomdod 05:25")
        for bad in ("salom, qalaysiz", "Bomdod besh yigirma, Asr to'rt", "", "bir ikki uch"):
            self.assertFalse(S.parse(bad)["complete"], bad)                                    # never "complete" on doubtful speech

    def test_asr_six_oclock_is_16_or_18_depending_on_the_rest_of_the_day(self):
        import spoken as S
        import timetable as T
        winter = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        summer = {"fajr": "04:10", "dhuhr": "12:30", "asr": "18:00", "maghrib": "19:45", "isha": "21:30"}
        typed = {
            "Bomdod 5:20\nPeshin 12:30\nAsr 6:00\nShom 5:35\nXufton 7:00": winter,                 # photo text / typed, no markers
            "Bomdod 4:10\nPeshin 12:30\nAsr 6:00\nShom 7:45\nXufton 9:30": summer,
            "Bomdod 4:10\nPeshin 12:30\nAsr 6:00 PM\nShom 7:45 PM\nXufton 9:30 PM": summer,
            "Bomdod 5:20\nPeshin 12:30\nAsr 16:00\nShom 17:35\nXufton 19:00": winter,              # 24-hour is left alone
            "Bomdod 4:10\nPeshin 12:30\nAsr 18:00\nShom 19:45\nXufton 21:30": summer,
            "Bomdod 5:20\nPeshin 12:30\nAsr l6:00\nShom l7:35\nXufton l9:00": winter,              # OCR "l" for "1"
            "4:10 12:30 6:00 7:45 9:30": summer,                                                  # no names at all
            "5:20 12:30 6:00 5:35 7:00": winter,
        }
        for text, want in typed.items():
            r = T.parse(text); self.assertTrue(r["complete"], text); self.assertEqual(r["times"], want, text)
        spoken = {
            "Bomdod besh yigirma, Peshin o'n ikki o'ttiz, Asr olti, Shom besh o'ttiz besh, Xufton yetti": winter,
            "Bomdod to'rt o'n, Peshin o'n ikki o'ttiz, Asr olti, Shom yetti qirq besh, Xufton to'qqiz o'ttiz": summer,
            "Bomdod besh yigirma, Peshin o'n ikki o'ttiz, Asr o'n olti, Shom o'n yetti o'ttiz besh, Xufton o'n to'qqiz": winter,
            "Fajr four ten, Dhuhr twelve thirty, Asr six, Maghrib seven forty five, Isha nine thirty": summer,
            "Fajr five twenty, Dhuhr twelve thirty, Asr six, Maghrib five thirty five, Isha seven": winter,
            "Fajr four ten, Dhuhr twelve thirty, Asr six pm, Maghrib seven forty five pm, Isha nine thirty pm": summer,
            "Фаджр четыре десять, зухр двенадцать тридцать, аср шесть, магриб семь сорок пять, иша девять тридцать": summer,
            "Фаджр пять двадцать, зухр двенадцать тридцать, аср шесть, магриб пять тридцать пять, иша семь": winter,
            "Фаджр четыре десять, зухр двенадцать тридцать, аср шесть вечера, магриб семь сорок пять вечера, иша девять тридцать вечера": summer,
        }
        for text, want in spoken.items():
            r = S.parse(text); self.assertTrue(r["complete"], text); self.assertEqual(r["times"], want, text)

    def test_every_region_and_month_in_every_notation(self):
        """Real prayer times (14 regions x 12 months x 3 days) written the ways boards and speakers write them must come back exactly."""
        import prayertimes as P
        import timetable as T
        names = {"fajr": "Bomdod", "dhuhr": "Peshin", "asr": "Asr", "maghrib": "Shom", "isha": "Xufton"}
        def h12(t):
            return "%d:%s" % ((int(t[:2]) % 12) or 12, t[3:])
        notations = {
            "24h": lambda t: t,
            "24h without zero": lambda t: t.lstrip("0") if t[0] == "0" else t,
            "12h without marker": h12,
            "12h with marker": lambda t: h12(t) + (" AM" if int(t[:2]) < 12 else " PM"),
            "lost leading 1": lambda t: t[1:] if t[0] == "1" else t,
        }
        for lat, lon in P.REGIONS:
            for month in range(1, 13):
                for day in (3, 12, 24):
                    tt = P.times(2026, month, day, lat, lon)
                    truth = {k: P.hm(tt[k]) for k in names}
                    for nn, f in notations.items():
                        r = T.parse("\n".join("%s %s" % (names[k], f(truth[k])) for k in names))
                        self.assertTrue(r["complete"], (nn, lat, month, day, truth, r))
                        self.assertEqual(r["times"], truth, (nn, lat, month, day))


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

    def test_start_offers_the_update_by_location_button(self):
        for code, word in (("uz", "Joylashuv"), ("ru", "геопозиции"), ("en", "location")):
            self.user = {"id": 700, "first_name": "A", "language_code": code}; self.sent.clear()
            self.send(text="/start")
            kb = [kw["reply_markup"] for m, kw in self.sent if m == "sendMessage" and "keyboard" in kw.get("reply_markup", {})]
            self.assertEqual(len(kb), 1, code)
            btn = kb[0]["keyboard"][0][0]
            self.assertTrue(btn["request_location"]); self.assertIn(word, btn["text"]); self.assertTrue(kb[0]["is_persistent"])
        # pressing it sends a location message -> the normal flow starts
        self.send(location={"latitude": 40.0004, "longitude": 66.0})
        self.assertTrue(any(b["callback_data"].startswith("pick:") for b in self.buttons()))

    def test_image_sent_as_file_is_read_like_a_photo(self):
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        self.send(document={"file_id": "f1", "mime_type": "image/jpeg", "file_size": 1234})
        self.assertIn("05:20", self.msgs()[-1])                                                 # read, preview shown
        self.sent.clear(); self.send(document={"file_id": "f2", "mime_type": "application/pdf"})   # other files are ignored
        self.assertEqual(self.msgs(), [])

    def test_flood_limit_and_cleanup(self):
        for _ in range(self.botmod.MSGS_PER_MIN + 15):
            self.send(text="/id")
        self.assertEqual(len(self.msgs()), self.botmod.MSGS_PER_MIN)                            # the rest is dropped
        self.bot.state[1] = {"step": "await_times", "ts": 0}; self.bot.msg_log[2] = __import__("collections").deque([0])
        self.bot.purge()
        self.assertNotIn(1, self.bot.state); self.assertNotIn(2, self.bot.msg_log)             # old sessions/counters are forgotten

    def test_admin_without_mini_app_url_gets_an_answer_and_archived_is_explained(self):
        bot = self.botmod.Bot(lambda m, **kw: self.sent.append((m, kw)), self.store, None, None, "", admin_ids=[7])
        self.user = {"id": 7, "first_name": "Owner", "language_code": "uz"}; self.sent.clear()
        bot.handle({"update_id": 9, "message": {"chat": {"id": 7, "type": "private"}, "from": self.user, "text": "/admin"}})
        self.assertIn("Mini App", self.msgs()[-1])
        self.store.moderate(self.m["id"], "archive", "admin:7", "t")
        self.user = {"id": 8, "first_name": "A", "language_code": "uz"}
        five = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        bot.state[8] = {"step": "confirm", "mosque_id": self.m["id"], "name": "x", "times": five, "ts": __import__("time").time()}
        bot.handle({"update_id": 10, "callback_query": {"id": "q", "from": self.user, "data": "save", "message": {"chat": {"id": 8}}}})
        self.assertIn("arxivlangan", self.msgs()[-1])

    def test_desktop_paths_name_search_and_deep_link(self):
        # no location button on desktop: typing a name finds the mosque
        self.send(text="Bot mos")
        picks = [b for b in self.buttons() if b["callback_data"].startswith("pick:")]
        self.assertEqual([b["text"] for b in picks], ["Bot mosque"])
        self.click(picks[0]["callback_data"]); self.send(text="05:20 12:30 16:00 17:35 19:00"); self.click("save")
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"]["fajr"], "05:20")
        self.send(text="zzzz-nothing"); self.assertIn("topilmadi", self.msgs()[-1])
        # deep link from the mosque page: /start m_<id> selects that mosque directly
        self.bot.state.clear(); self.send(text="/start m_" + self.m["id"])
        self.assertIn("Bot mosque", self.msgs()[-1])
        self.send(text="06:00 12:30 16:00 17:35 19:00"); self.click("save")
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"]["fajr"], "06:00")
        self.send(text="/start m_not-a-real-id-xxxxxxxxxxxxxxxxxxxxxxxx")                    # junk payload must not crash
        self.assertTrue(self.msgs())

    def test_voice_message_flow(self):
        heard = {"text": "Bomdod besh yigirma, Peshin o'n ikki o'ttiz, Asr to'rt, Shom besh o'ttiz besh, Xufton yetti"}
        bot = self.botmod.Bot(lambda m, **kw: self.sent.append((m, kw)), self.store, None, lambda fid: b"ogg", "https://example.org",
                              stt_read=lambda data: heard["text"])
        def say(**kw):
            base = {"chat": {"id": 555, "type": "private"}, "from": self.user}
            bot.handle({"update_id": 1, "message": dict(base, **kw)})
        say(voice={"file_id": "v", "duration": 5})                                            # no mosque chosen yet
        self.assertIn("lokatsiya", self.msgs()[-1])
        say(location={"latitude": 40.0, "longitude": 66.0})
        bot.handle({"update_id": 2, "callback_query": {"id": "q", "from": self.user, "data": "pick:" + self.m["id"], "message": {"chat": {"id": 555}}}})
        say(voice={"file_id": "v", "duration": 120}); self.assertIn("60", self.msgs()[-1])    # too long
        say(voice={"file_id": "v", "duration": 8})
        txt = self.msgs()[-1]
        self.assertIn("Eshitilgan", txt); self.assertIn("16:00", txt); self.assertIn("19:00", txt)    # heard text and the resolved times
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"], {})                    # nothing saved before confirmation
        bot.handle({"update_id": 3, "callback_query": {"id": "q", "from": self.user, "data": "save", "message": {"chat": {"id": 555}}}})
        got = self.store.get(self.m["id"])["prayer_times"]
        self.assertEqual((got["asr"], got["maghrib"], got["isha"]), ("16:00", "17:35", "19:00"))   # AM/PM resolved without being spoken
        # unclear speech: asks again and saves nothing; recogniser missing: says so
        heard["text"] = "salom qalaysiz"
        say(location={"latitude": 40.0, "longitude": 66.0})
        bot.handle({"update_id": 4, "callback_query": {"id": "q", "from": self.user, "data": "pick:" + self.m["id"], "message": {"chat": {"id": 555}}}})
        say(voice={"file_id": "v", "duration": 3}); self.assertIn("ajrata olmadim", self.msgs()[-1])
        nobot = self.botmod.Bot(lambda m, **kw: self.sent.append((m, kw)), self.store, None, lambda fid: b"x", "", stt_read=None)
        nobot.state[555] = {"step": "await_times", "mosque_id": self.m["id"], "name": "x", "ts": __import__("time").time()}
        nobot.handle({"update_id": 5, "message": {"chat": {"id": 555, "type": "private"}, "from": self.user, "voice": {"file_id": "v", "duration": 3}}})
        self.assertIn("ishlamayapti", self.msgs()[-1])

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

    # ---- nearest card, subscriptions, reminders
    def set_times(self, mid=None, times=None, who=("tg:900", {"kind": "telegram", "platform": "telegram-bot"})):
        times = times or {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        self.store.edit(mid or self.m["id"], {"prayer_times": times}, who[0], who[1])

    def test_nearest_card_has_name_distance_times_route_and_subscribe(self):
        self.store.edit(self.m["id"], {"address": "Muqimiy 166"}, "tg:900", {"kind": "telegram", "platform": "telegram-bot"})
        self.set_times()
        self.send(location={"latitude": 40.0027, "longitude": 66.0})                          # about 300 m away: card yes, pick-list "none"
        card = [kw for m, kw in self.sent if m == "sendMessage" and kw["text"].startswith("Eng yaqin masjid")][0]
        for part in ("Bot mosque", "Muqimiy 166", " m", "05:20", "19:00", "Yangilangan"):
            self.assertIn(part, card["text"])
        urls = [b["url"] for row in card["reply_markup"]["inline_keyboard"] for b in row if "url" in b]
        self.assertEqual(urls, ["https://yandex.com/maps/?rtext=~40.0,66.0&rtt=auto"])
        self.assertIn("sub:" + self.m["id"], [b.get("callback_data") for row in card["reply_markup"]["inline_keyboard"] for b in row])
        self.assertEqual([b["callback_data"][:4] for b in self.buttons()], ["new:"])          # the update flow still follows
        self.sent.clear(); self.user = {"id": 556, "first_name": "R", "language_code": "ru"}   # Russian, and a mosque without times
        m2 = self.store.create({"lat": 40.5, "lon": 66.5, "name": "Dalnyaya"}, "client:test0001", {"kind": "web", "platform": "web"})
        self.send(location={"latitude": 40.501, "longitude": 66.5})
        txt = [kw["text"] for m, kw in self.sent if m == "sendMessage"][0]
        self.assertTrue(txt.startswith("Ближайшая мечеть")); self.assertIn("Dalnyaya", txt); self.assertIn("Время джамаата ещё не указано", txt)
        self.assertTrue(m2["id"])
        self.sent.clear(); self.send(text="/nearest")
        self.assertTrue(self.sent[-1][1]["reply_markup"]["keyboard"][0][0]["request_location"])

    def test_subscribe_unsubscribe_limits_and_deep_link(self):
        mid = self.m["id"]
        self.click("sub:" + mid)
        self.assertIn("obuna boʻldingiz", self.msgs()[-1]); self.assertEqual([s["mosque_id"] for s in self.bot.db.subs(555)], [mid])
        self.click("sub:" + mid); self.assertIn("allaqachon", self.msgs()[-1])
        self.send(text="/subs"); self.assertEqual(self.buttons()[0]["callback_data"], "unsub:" + mid)
        self.click("unsub:" + mid); self.assertIn("bekor qilindi", self.msgs()[-1]); self.assertEqual(self.bot.db.subs(555), [])
        self.send(text="/subs"); self.assertIn("obunalaringiz yoʻq", self.msgs()[-1])
        self.send(text="/start s_" + mid); self.assertIn("obuna boʻldingiz", self.msgs()[-1])   # deep link from the web page
        self.assertTrue(self.bot.db.is_subscribed(555, mid))
        self.send(text="/start s_" + "0" * 36); self.assertIn("Xatolik", self.msgs()[-1])       # unknown mosque: an answer, no crash
        self.store.moderate(self.m["id"], "archive", "admin:7", "t")
        self.user = {"id": 600, "first_name": "B", "language_code": "en"}; self.click("sub:" + mid); self.assertIn("archived", self.msgs()[-1])
        # the limit holds even when many taps arrive at once
        ms = [self.store.create({"lat": 41 + i * 0.01, "lon": 67, "name": "M%d" % i}, "client:test0002", {"kind": "web", "platform": "web"}) for i in range(30)]
        results = []
        def tap(m):
            results.append(self.bot.db.subscribe(777, m["id"]))
        threads = [threading.Thread(target=tap, args=(m,)) for m in ms]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(results.count("ok"), 20); self.assertEqual(results.count("limit"), 10); self.assertEqual(len(self.bot.db.subs(777)), 20)
        results.clear(); same = [threading.Thread(target=tap, args=(ms[0],)) for _ in range(10)]
        [t.start() for t in same]; [t.join() for t in same]
        self.assertEqual(len(self.bot.db.subs(777)), 20)                                      # two taps on one mosque never double-subscribe

    def test_save_offers_the_subscription_and_stop_deletes_everything(self):
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        self.send(text="05:20 12:30 16:00 17:35 19:00"); self.click("save")
        self.assertEqual([b.get("callback_data") for b in self.buttons()], [None, "sub:" + self.m["id"]])    # open link first, then subscribe
        self.click("sub:" + self.m["id"]); self.click("reg:3"); self.click("rm:daily")
        self.send(text="/stop"); self.assertIn("oʻchirildi", self.msgs()[-1])
        self.assertEqual(self.bot.db.subs(555), []); self.assertIsNone(self.bot.db.user(555))

    def test_reminder_menu_regions_and_toggles_in_three_languages(self):
        self.send(text="/reminders")                                                           # no region yet: the 14 regions to choose from
        regs = [b for b in self.buttons() if b["callback_data"].startswith("reg:")]
        self.assertEqual([b["callback_data"] for b in regs], ["reg:%d" % i for i in range(14)]); self.assertEqual(regs[0]["text"], "Toshkent shahri")
        self.click("rm:daily"); self.assertEqual(self.buttons()[0]["callback_data"], "reg:0")  # toggling needs a region first
        self.click("reg:11"); self.assertIn("Samarqand", self.msgs()[-1]); self.assertEqual(self.bot.db.user(555)["region"], 11)
        self.click("rm:daily"); self.click("rm:before")
        u = self.bot.db.user(555); self.assertTrue(u["daily"] and u["before_prayer"])
        self.click("rm:daily"); self.assertFalse(self.bot.db.user(555)["daily"])
        self.click("rm:off"); u = self.bot.db.user(555); self.assertFalse(u["daily"] or u["before_prayer"])
        self.assertEqual(u["region"], 11)                                                      # the region is remembered
        self.click("reg:99"); self.click("reg:x"); self.assertEqual(self.bot.db.user(555)["region"], 11)   # junk data is ignored
        for code, word in (("ru", "Напоминания"), ("en", "Reminders")):
            self.user = {"id": 800, "first_name": "L", "language_code": code}; self.send(text="/region"); self.click("reg:0"); self.assertIn(word, self.msgs()[-1])

    def notifier(self):
        return self.botmod.Notifier(lambda m, **kw: self.sent.append((m, kw)), self.bot.db, "https://example.org")

    def test_scheduler_sends_each_reminder_once_in_the_users_language(self):
        import prayertimes as P
        from datetime import datetime, timedelta, timezone
        tt = P.times(2026, 10, 26, *P.REGIONS[0])
        local = lambda h: datetime(2026, 10, 26, tzinfo=timezone.utc) + timedelta(hours=h - 5)   # local (UTC+5) clock hour -> UTC instant  # noqa: E731
        self.bot.db.set_user(555, region=0, daily=True, before_prayer=True, lang="uz")
        self.bot.db.set_user(556, region=0, daily=False, before_prayer=True, lang="ru")
        self.bot.db.set_user(557, region=None, daily=True, before_prayer=True, lang="en")      # no region: nothing can be sent
        n = self.notifier()
        sent_to = lambda tid: [kw["text"] for m, kw in self.sent if kw.get("chat_id") == tid]  # noqa: E731
        n.tick(local(tt["fajr"] - 1.0))                                                        # 60 min before fajr: too early
        self.assertEqual(self.sent, [])
        n.tick(local(tt["fajr"] - 0.4))                                                        # 24 min before fajr: the daily message
        self.assertEqual(len(sent_to(555)), 1); self.assertIn("Toshkent shahri", sent_to(555)[0]); self.assertIn(P.hm(tt["asr"]), sent_to(555)[0])
        self.assertIn(P.hm(tt["sunrise"]), sent_to(555)[0]); self.assertEqual(sent_to(556), [])
        n.tick(local(tt["fajr"] - 0.3)); n.tick(local(tt["fajr"] - 0.35))                      # more ticks: no second daily message
        self.assertEqual(len(sent_to(555)), 1)
        n.tick(local(tt["asr"] - 0.2))                                                         # 12 min before asr
        self.assertIn("Asr", sent_to(555)[-1]); self.assertIn("15 daqiqa", sent_to(555)[-1])
        self.assertIn("Аср", sent_to(556)[-1]); self.assertIn("15 минут", sent_to(556)[-1])   # Russian user, Russian text
        total = len(self.sent)
        n.tick(local(tt["asr"] - 0.1)); self.assertEqual(len(self.sent), total)                # same prayer, same day: once
        n2 = self.notifier(); n2.tick(local(tt["asr"] - 0.15)); self.assertEqual(len(self.sent), total)   # restarted bot: still once
        n2.tick(local(tt["asr"] + 0.1)); self.assertEqual(len(self.sent), total)               # prayer already started: no reminder
        for k in ("fajr", "dhuhr", "maghrib", "isha"):                                         # every prayer gets its reminder
            n2.tick(local(tt[k] - 0.2))
        self.assertEqual(len([t for t in sent_to(555) if "15 daqiqa" in t]), 5)
        tomorrow = local(tt["fajr"] - 0.4) + timedelta(days=1)
        n2.tick(tomorrow); self.assertEqual(len([t for t in sent_to(555) if "Bugungi" in t]), 2)   # a new day: sent again
        self.assertEqual(sent_to(557), [])
        # a user who blocked the bot is forgotten, the others still get their messages
        def api(method, **kw):
            if kw.get("chat_id") == 555:
                raise self.botmod.TelegramError(method, 403, "Forbidden: bot was blocked by the user")
            self.sent.append((method, kw))
        n3 = self.botmod.Notifier(api, self.bot.db); self.sent.clear()
        n3.tick(local(tt["maghrib"] - 0.2) + timedelta(days=3))
        self.assertIsNone(self.bot.db.user(555)); self.assertTrue(self.bot.db.user(556)); self.assertEqual(len(sent_to(556)), 1)

    def test_subscribers_hear_about_time_changes_but_not_the_editor(self):
        mid = self.m["id"]
        self.bot.db.set_user(555, lang="uz"); self.bot.db.set_user(556, lang="en"); self.bot.db.set_user(557, lang="ru")
        for tid in (555, 556, 557):
            self.bot.db.subscribe(tid, mid)
        n = self.notifier(); n.tick(); self.assertEqual(self.sent, [])                         # nothing changed yet
        self.set_times(who=("tg:556", {"kind": "telegram", "platform": "telegram-bot"}))       # 556 is the one who edits
        n.tick()
        got = {kw["chat_id"]: kw["text"] for m, kw in self.sent}
        self.assertEqual(sorted(got), [555, 557])                                              # not the editor
        self.assertIn("oʻzgardi", got[555]); self.assertIn("16:00", got[555]); self.assertIn("изменилось", got[557]); self.assertIn("Bot mosque", got[557])
        link = [kw for m, kw in self.sent if kw["chat_id"] == 555][0]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
        self.assertEqual(link, "https://example.org/?m=" + mid)
        count = len(self.sent); n.tick(); n.tick(); self.assertEqual(len(self.sent), count)    # announced once
        later = {"fajr": "05:25", "dhuhr": "12:30", "asr": "16:05", "maghrib": "17:35", "isha": "19:00"}
        self.set_times(times=later, who=("tg:555", {"kind": "telegram", "platform": "telegram-bot"}))
        n.tick(); self.assertEqual(sorted(kw["chat_id"] for m, kw in self.sent[count:]), [556, 557])   # now 555 is the editor
        # an edit that does not touch the times is not announced
        count = len(self.sent)
        self.store.edit(mid, {"name": "Renamed"}, "tg:900", {"kind": "telegram", "platform": "telegram-bot"}); n.tick()
        self.assertEqual(len(self.sent), count)
        # failed delivery is retried on the next tick; a blocked user is dropped
        self.set_times(times=dict(later, fajr="05:30"), who=("tg:900", {"kind": "telegram", "platform": "telegram-bot"}))
        calls = []
        def flaky(method, **kw):
            calls.append(kw["chat_id"])
            if kw["chat_id"] == 555:
                raise self.botmod.TelegramError(method, 429, "Too Many Requests")
            if kw["chat_id"] == 556:
                raise self.botmod.TelegramError(method, 403, "Forbidden: bot was blocked by the user")
        f = self.botmod.Notifier(flaky, self.bot.db); f.tick()
        self.assertEqual(sorted(calls), [555, 556, 557])
        self.assertIsNone(self.bot.db.user(556)); self.assertEqual(self.bot.db.subs(556), [])  # blocked: forgotten
        calls.clear(); f.tick(); self.assertEqual(calls, [555])                                # only the failed one is retried


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
