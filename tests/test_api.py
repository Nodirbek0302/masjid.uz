"""The public and moderation API of masjid.uz against the real PostgreSQL test database (no network).

Paths are relative to /masjiduz/v1 (`self.call`); bodies and answers are camelCase; every answer is the envelope, the payload is `["data"]`.
"""
import hashlib
import json
import math
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from helpers import ADMIN_ID, ServerCase, db, make_cfg, reset_db, sign_init_data, start, stop  # noqa: E402
import app  # noqa: E402
import import_osm  # noqa: E402
import osm_photo  # noqa: E402


class Api(ServerCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.srv, cls.addr = start(make_cfg())

    @classmethod
    def tearDownClass(cls):
        stop(cls.srv)

    def test_full_flow(self):
        # add without name/times -> missing info allowed, community origin, unverified
        s, r = self.add(41.3111, 69.2797)
        m = r["data"]
        self.assertEqual(s, 201)
        self.assertEqual((m["origin"], m["status"], m["version"], m["name"]), ("community", "unverified", 1, None))
        self.assertNotIn("creatorId", m)                        # personal ids never appear in public responses
        self.assertNotIn("creator_id", m)
        # duplicate detection
        s, d = self.add(41.31112, 69.27972, name="Dup")
        self.assertEqual((s, d["code"]), (409, "MASJIDUZ_POSSIBLE_DUPLICATE")); self.assertEqual(d["data"]["duplicates"][0]["id"], m["id"])
        self.assertEqual(self.add(41.31112, 69.27972, name="Dup", force=True)[0], 201)       # resend with force
        # far away is fine
        self.assertEqual(self.add(41.35, 69.35)[0], 201)
        # edit name and times
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"name": "Test", "prayerTimes": {"fajr": "05:10", "jumuah": "13:00"}})
        m2 = r["data"]
        self.assertEqual((s, m2["version"], m2["prayerTimes"]["fajr"]), (200, 2, "05:10"))
        # concurrent edits never fail: a second edit is applied on top of the latest state
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"name": "X"})
        self.assertEqual((s, r["data"]["name"], r["data"]["prayerTimes"]["fajr"]), (200, "X", "05:10"))     # the times saved meanwhile survive
        # validation
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"prayerTimes": {"fajr": "25:99"}})
        self.assertEqual((s, r["code"], r["message"]["code"]), (400, "VALIDATION_ERROR", "error.invalidPrayerTimes"))
        s, r = self.add(100, 0)
        self.assertEqual((s, r["code"], r["message"]["code"]), (400, "VALIDATION_ERROR", "error.invalidCoordinates"))
        # history: newest first, only roles
        s, h = self.call("GET", "/mosques/%s/history" % m["id"])
        self.assertEqual([x["action"] for x in h["data"]["items"]], ["edit", "edit", "create"])
        self.assertIsNone(h["data"]["nextCursor"])
        # reports: grouped, threshold (2 in this server) -> reported
        rep = "/mosques/%s/reports" % m["id"]
        self.assertEqual(self.call("POST", rep, {"kind": "closed", "reason": "yopilgan"}, client="bbbbbbbb-1"), (201, helpers_ok({"ok": True}, 201)))
        s, r = self.call("POST", rep, {"kind": "closed", "reason": "yopilgan"}, client="bbbbbbbb-1")
        self.assertEqual((s, r["code"], r["message"]["code"]), (409, "CONFLICT", "error.alreadyReported"))
        s, r = self.call("POST", rep, {"kind": "closed"}, client="cccccccc-1")
        self.assertEqual((s, r["code"], r["message"]["code"]), (400, "VALIDATION_ERROR", "error.reasonRequired"))
        self.assertEqual(self.call("POST", rep, {"kind": "bogus", "reason": "x"}, client="cccccccc-1")[1]["message"]["code"], "error.invalidKind")
        self.call("POST", rep, {"kind": "closed", "reason": "yo'q"}, client="cccccccc-1")
        self.assertEqual(self.call("GET", "/mosques/" + m["id"])[1]["data"]["status"], "reported")
        # admin only
        self.assertEqual(self.call("GET", "/admin/reports")[0], 401)
        s, g = self.call("GET", "/admin/reports", admin=True)
        self.assertEqual(g["data"]["groups"][0]["count"], 2)
        # archive keeps history, hides from the lists, restorable
        s, a = self.call("POST", "/admin/mosques/%s/archive" % m["id"], {"note": "closed"}, admin=True)
        self.assertEqual(a["data"]["status"], "archived")
        self.assertNotIn(m["id"], [x["id"] for x in self.call("GET", "/mosques?limit=100")[1]["data"]["items"]])
        self.assertEqual(len(self.call("GET", "/mosques/%s/history" % m["id"])[1]["data"]["items"]), 5)
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"name": "z"})                           # archived: not editable
        self.assertEqual((s, r["code"], r["message"]["code"]), (409, "MASJIDUZ_MOSQUE_ARCHIVED", "error.archived"))
        s, r = self.call("POST", "/admin/mosques/%s/archive" % m["id"], {}, admin=True)
        self.assertEqual((s, r["code"], r["message"]["code"]), (409, "CONFLICT", "error.alreadyArchived"))
        self.assertEqual(self.call("POST", "/admin/mosques/%s/restore" % m["id"], {}, admin=True)[1]["data"]["status"], "unverified")
        s, r = self.call("POST", "/admin/mosques/%s/restore" % m["id"], {}, admin=True)
        self.assertEqual((s, r["message"]["code"]), (409, "error.notArchived"))
        # verify needs evidence
        s, r = self.call("POST", "/admin/mosques/%s/verify" % m["id"], {}, admin=True)
        self.assertEqual((s, r["code"], r["message"]["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "error.evidenceRequired", "note"))
        s, r = self.call("POST", "/admin/mosques/%s/verify" % m["id"], {"note": "seen on site"}, admin=True)
        self.assertEqual((s, r["data"]["status"], r["data"]["verificationNote"]), (200, "verified", "seen on site"))

    def test_immutability(self):
        m = self.made(40.0, 65.0)
        for sql in ("DELETE FROM mosque_history", "UPDATE mosque_history SET actor='x'", "TRUNCATE mosque_history", "DELETE FROM mosques"):
            with self.assertRaises(helpers.psycopg.errors.RaiseException):
                db(sql)
        s, r = self.call("DELETE", "/mosques/" + m["id"])                      # there is no way to delete over HTTP
        self.assertEqual((s, r["code"]), (405, "METHOD_NOT_ALLOWED"))

    def test_osm_import_is_idempotent_and_exported(self):
        self.add(39.0, 66.0, name="Ours")
        data = {"elements": [
            {"type": "node", "id": 1, "lat": 39.5, "lon": 66.5, "tags": {"name": "OSM masjid", "amenity": "place_of_worship"}},
            {"type": "way", "id": 2, "center": {"lat": 39.6, "lon": 66.6}, "tags": {}},
            {"type": "node", "id": 3, "lat": 39.00005, "lon": 66.00005, "tags": {"name": "Too close"}}]}
        first = import_osm.run(data, make_cfg(), self.srv.store)
        second = import_osm.run(data, make_cfg(), self.srv.store)
        self.assertEqual((first["added"], first["near_existing"]), (2, 1))
        self.assertEqual((second["added"], second["updated"], second["unchanged"]), (0, 0, 2))
        s, r = self.call("GET", "/mosques?bbox=66.4,39.4,66.7,39.7")
        self.assertEqual(len(r["data"]["items"]), 2)
        for it in r["data"]["items"]:
            full = self.call("GET", "/mosques/" + it["id"])[1]["data"]
            self.assertEqual((full["origin"], full["source"]), ("imported", "openstreetmap"))
        self.assertEqual(self.call("GET", "/mosques?bbox=65.9,38.9,66.1,39.1")[1]["data"]["items"][0]["name"], "Ours")
        s, g = self.call("GET", "/export/osm-derived")
        self.assertEqual(s, 200)
        refs = {f["properties"]["osm_ref"] for f in g["data"]["features"]}
        self.assertLessEqual({"node/1", "way/2"}, refs)
        self.assertNotIn("node/3", refs)
        self.assertIn("ODbL", g["data"]["license"])
        self.assertEqual(g["data"]["type"], "FeatureCollection")

    def test_osm_resync_updates_untouched_and_protects_edited(self):
        def el(i, lat, name): return {"type": "node", "id": i, "lat": lat, "lon": 70.0, "tags": {"name": name}}
        store = self.srv.store
        first = {"elements": [el(901, 38.1, "Old A"), el(902, 38.2, "Old B"), el(903, 38.3, "Old C")]}
        self.assertEqual(import_osm.run(first, make_cfg(), store)["added"], 3)
        lst = self.call("GET", "/mosques?bbox=69.9,38.0,70.1,38.4")[1]["data"]["items"]
        by_name = {m["name"]: m for m in lst}
        # a human edits B; C gets archived by a moderator; A is untouched
        s, _ = self.call("PATCH", "/mosques/" + by_name["Old B"]["id"], {"prayerTimes": {"fajr": "05:00"}})
        self.assertEqual(s, 200)
        self.call("POST", "/admin/mosques/%s/archive" % by_name["Old C"]["id"], {}, admin=True)
        second = {"elements": [el(901, 38.1, "New A"), el(902, 38.2, "OSM B changed"), el(903, 38.3, "New C")]}
        r = import_osm.run(second, make_cfg(), store)
        self.assertEqual((r["updated"], r["protected_edited"]), (1, 2))
        get = lambda n: self.call("GET", "/mosques/" + by_name[n]["id"])[1]["data"]      # noqa: E731
        self.assertEqual(get("Old A")["name"], "New A")                                      # followed OSM
        self.assertEqual(get("Old B")["prayerTimes"]["fajr"], "05:00")                       # human edit wins
        self.assertEqual(get("Old B")["name"], "Old B")
        self.assertEqual(get("Old C")["status"], "archived")
        actions = [h["action"] for h in self.call("GET", "/mosques/%s/history" % by_name["Old A"]["id"])[1]["data"]["items"]]
        self.assertEqual(actions, ["osm_sync", "create"])                                    # audit trail kept
        self.assertEqual([h["actor"] for h in self.call("GET", "/mosques/%s/history" % by_name["Old A"]["id"])[1]["data"]["items"]], ["osm", "osm"])
        self.assertEqual(import_osm.run(second, make_cfg(), store)["updated"], 0)            # idempotent
        third = {"elements": [el(901, 38.1, "New A")] + [el(1000 + i, 38.5 + i / 100.0, "X%d" % i) for i in range(3)]}
        self.assertGreaterEqual(import_osm.run(third, make_cfg(), store)["missing_in_osm"], 2)   # vanished: counted only

    def test_actor_tracking_web_and_mobile(self):
        # web client: actor row + non-personal snapshot in the audit log
        m = self.made(36.1, 61.1, name="Actor test")
        row = db("SELECT kind, platform, edits_count, ip_hash FROM actors WHERE actor_id='client:aaaaaaaa-1'")[0]
        self.assertEqual((row[0], row[1]), ("web", "web")); self.assertGreaterEqual(row[2], 1); self.assertEqual(len(row[3]), 24)
        # mobile app: device id + platform/app/model/os headers
        mob = {"X-Device-Id": "dev-ABCDEF123456", "X-Client-Platform": "ios", "X-App-Version": "1.4.2", "X-OS-Version": "iOS 19.1", "X-Device-Model": "iPhone16,2"}
        s, _ = self.call("PATCH", "/mosques/" + m["id"], {"name": "Changed on phone"}, headers=mob)
        self.assertEqual(s, 200)
        a = db("SELECT kind, platform, app_version, os_version, device_model, device_id, edits_count FROM actors WHERE actor_id='device:dev-ABCDEF123456'")[0]
        self.assertEqual(a, ("mobile", "ios", "1.4.2", "iOS 19.1", "iPhone16,2", "dev-ABCDEF123456", 1))
        h = db("SELECT actor, client FROM mosque_history WHERE mosque_id=%s AND action='edit'", m["id"])[0]
        self.assertEqual(h[0], "device:dev-ABCDEF123456")
        self.assertEqual({k: h[1][k] for k in ("kind", "platform", "app_version", "device_model")},
                         {"kind": "mobile", "platform": "ios", "app_version": "1.4.2", "device_model": "iPhone16,2"})
        self.assertNotIn("ip_hash", h[1])
        # reports keep the snapshot too and bump the counter
        self.call("POST", "/mosques/%s/reports" % m["id"], {"kind": "other", "reason": "test"}, headers=dict(mob, **{"X-Device-Id": "dev-REPORTER9999"}))
        self.assertEqual(db("SELECT reports_count FROM actors WHERE actor_id='device:dev-REPORTER9999'")[0][0], 1)
        # PUBLIC history exposes roles only: no ids, no device info
        items = self.call("GET", "/mosques/%s/history" % m["id"])[1]["data"]["items"]
        self.assertEqual([i["actor"] for i in items], ["user", "user"])
        for i in items:
            self.assertNotIn("client", i)
        raw = json.dumps(self.call("GET", "/mosques?bbox=61,36,61.2,36.2")[1]) + json.dumps(self.call("GET", "/mosques/" + m["id"])[1])
        for needle in ("dev-ABCDEF123456", "aaaaaaaa-1", "creator"):
            self.assertNotIn(needle, raw)
        # moderators see the details
        ov = self.call("GET", "/admin/overview", admin=True)[1]["data"]
        mine = [r for r in ov["recent"] if r["mosqueId"] == m["id"] and r["action"] == "edit"][0]
        self.assertEqual((mine["actorKind"], mine["platform"], mine["appVersion"]), ("mobile", "ios", "1.4.2"))
        ac = self.call("GET", "/admin/actors?q=ABCDEF123456", admin=True)[1]["data"]
        self.assertEqual([x["actorId"] for x in ac["items"]], ["device:dev-ABCDEF123456"])
        self.assertEqual(self.call("GET", "/admin/actors")[0], 401)
        lst = self.call("GET", "/admin/mosques?status=unverified&limit=100", admin=True)[1]["data"]
        self.assertTrue(any(x.get("creatorId") for x in lst["items"]))                      # moderators may see who added it

    def test_times_updated_at_tracks_only_changes_of_the_times(self):
        m = self.made(31.0, 73.0, name="Times stamp")                          # no times yet
        self.assertIsNone(m["timesUpdatedAt"])
        m = self.call("PATCH", "/mosques/" + m["id"], {"name": "Times stamp 2"})[1]["data"]
        self.assertIsNone(m["timesUpdatedAt"])                                 # a name edit is not a times change
        m2 = self.call("PATCH", "/mosques/" + m["id"], {"prayerTimes": {"fajr": "05:10"}})[1]["data"]
        stamp = m2["timesUpdatedAt"]
        self.assertRegex(stamp, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        m3 = self.call("PATCH", "/mosques/" + m["id"], {"address": "Somewhere"})[1]["data"]
        self.assertEqual(m3["timesUpdatedAt"], stamp)                          # unrelated edit keeps the stamp
        m4 = self.call("PATCH", "/mosques/" + m["id"], {"prayerTimes": {"fajr": None}})[1]["data"]
        self.assertIsNone(m4["timesUpdatedAt"])                                # all times cleared -> "not entered"
        c = self.made(31.5, 73.5, name="Born with times", prayerTimes={"isha": "20:00"})
        self.assertTrue(c["timesUpdatedAt"])                                   # entered at creation

    def test_many_users_at_once_never_get_errors_and_nothing_is_lost(self):
        mid = self.made(30.0, 74.0, name="Busy mosque")["id"]
        keys = ["fajr", "dhuhr", "asr", "maghrib", "isha"]
        times = {"fajr": "05:10", "dhuhr": "12:20", "asr": "16:00", "maghrib": "17:30", "isha": "19:00"}
        jobs = [("name", "Name %02d" % i) for i in range(20)] + [("time", k) for k in keys] + [("report", i) for i in range(6)]

        def work(job):
            kind, v = job
            if kind == "name":
                return self.call("PATCH", "/mosques/" + mid, {"name": v}, client="eeeeeeee-1")[0]
            if kind == "time":
                return self.call("PATCH", "/mosques/" + mid, {"prayerTimes": {v: times[v]}}, client="ffffffff-1")[0]
            return self.call("POST", "/mosques/%s/reports" % mid, {"kind": "other", "reason": "r%d" % v}, client="%08d-aa" % (100 + v))[0]
        with ThreadPoolExecutor(12) as ex:
            codes = list(ex.map(work, jobs))
        self.assertEqual(sorted(set(codes)), [200, 201])                       # no 409/429/500/503 for anybody
        final = self.call("GET", "/mosques/" + mid)[1]["data"]
        self.assertEqual(final["prayerTimes"], times)                          # all five writers' times kept
        self.assertTrue(final["name"].startswith("Name "))
        hist = self.call("GET", "/mosques/%s/history?limit=100" % mid)[1]["data"]["items"]
        edits = [x for x in hist if x["action"] == "edit"]
        self.assertEqual(final["version"], 1 + len(edits) + 1)                 # +1: the status change by the reports; versions are gapless
        self.assertEqual(len({x["version"] for x in edits}), len(edits))       # no two edits share a version
        self.assertEqual(final["status"], "reported")
        self.assertEqual(len([x for x in hist if x["action"] == "report_threshold"]), 1)    # the status flipped exactly once
        # many people adding the same missing mosque at the same moment: exactly one record, the rest are told about the duplicate
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda i: self.add(30.5, 74.5, name="Same spot", force=False)[0], range(8)))
        self.assertEqual(sorted(res), [201] + [409] * 7)
        self.assertEqual(len(self.call("GET", "/mosques?bbox=74.4,30.4,74.6,30.6")[1]["data"]["items"]), 1)

    def test_nearest_mosques_are_really_the_nearest(self):
        pts = [(34.0000, 72.0000), (34.0030, 72.0000), (34.0100, 72.0100), (34.1000, 72.1000), (34.5000, 72.5000)]
        for i, (la, lo) in enumerate(pts):
            self.add(la, lo, name="N%d" % i, force=True)
        me = (34.0005, 72.0001)
        s, r = self.call("GET", "/mosques/nearest?lat=%s&lon=%s&limit=3" % me)
        self.assertEqual(s, 200)
        items = r["data"]["items"]
        self.assertEqual([m["name"] for m in items], ["N0", "N1", "N2"])                    # ordered by distance
        d = [m["distanceM"] for m in items]
        self.assertEqual(d, sorted(d))

        def hav(a, b):
            R = 6371000.0
            p1, p2 = math.radians(a[0]), math.radians(b[0])
            dl = math.radians(b[1] - a[1])
            x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * R * math.asin(math.sqrt(x))
        self.assertAlmostEqual(d[0], hav(me, pts[0]), delta=1.0)                           # distance in metres is right
        self.assertNotIn("creatorId", items[0])
        far = self.call("GET", "/mosques/nearest?lat=34.4&lon=72.4&limit=1")[1]["data"]["items"]      # only the far ones qualify
        self.assertEqual(far[0]["name"], "N4" if hav((34.4, 72.4), pts[4]) < hav((34.4, 72.4), pts[3]) else "N3")
        s, r = self.call("GET", "/mosques/nearest?lat=95&lon=0")
        self.assertEqual((s, r["message"]["code"]), (400, "error.invalidCoordinates"))
        self.assertEqual(self.call("GET", "/mosques/nearest?lat=34&lon=72&limit=x")[0], 400)
        self.assertEqual(self.call("GET", "/mosques/nearest")[0], 400)

    def test_nearest_list_is_read_page_by_page_with_a_cursor(self):
        pts = [(-33.0000 + i * 0.001, 151.0000) for i in range(7)]
        for i, (la, lo) in enumerate(pts):
            self.add(la, lo, name="P%d" % i, force=True)

        def page(cursor=None, lim=3):
            q = "/mosques/nearest?lat=-33.0003&lon=151&limit=%d" % lim + ("&cursor=" + cursor if cursor else "")
            d = self.call("GET", q)[1]["data"]
            return [m["name"] for m in d["items"]], d["nextCursor"]
        first, c1 = page()
        self.assertEqual(first, ["P0", "P1", "P2"])
        self.assertTrue(c1)
        second, c2 = page(c1)
        self.assertEqual(second, ["P3", "P4", "P5"])
        third, _ = page(c2)
        self.assertEqual(third[0], "P6")                                                   # the next page goes on with the next nearest
        self.assertTrue({"P0", "P1", "P2", "P3", "P4", "P5"}.isdisjoint(third))
        self.assertEqual(self.call("GET", "/mosques/nearest?lat=-33&lon=151&cursor=x")[0], 400)      # not a cursor
        bad = app.enc_cursor("abc")
        s, r = self.call("GET", "/mosques/nearest?lat=-33&lon=151&cursor=" + bad)
        self.assertEqual((s, r["message"]["reason"]), (400, "cursor"))
        self.assertEqual(self.call("GET", "/mosques/nearest?lat=-33&lon=151&limit=101")[0], 400)

    def test_nearby_radius(self):
        a = self.made(28.0, 77.0, name="Near A")
        self.made(28.0004, 77.0, name="Near B", force=True)                                   # ~45 m
        self.made(28.01, 77.0, name="Far", force=True)
        d = self.call("GET", "/mosques/nearby?lat=28.0&lon=77.0&radius=100")[1]["data"]
        self.assertEqual([m["name"] for m in d["items"]], ["Near A", "Near B"])
        self.assertIsNone(d["nextCursor"]); self.assertEqual(d["items"][0]["id"], a["id"]); self.assertIn("distanceM", d["items"][0])
        self.assertEqual(self.call("GET", "/mosques/nearby?lat=28&lon=77&radius=501")[0], 400)
        self.assertEqual(self.call("GET", "/mosques/nearby?lat=28&lon=77&radius=0")[0], 400)
        self.assertEqual(self.call("GET", "/mosques/nearby?lat=28&lon=77&radius=abc")[0], 400)

    def test_photos_come_only_from_commons_tags_and_are_not_editable(self):
        # the Wikimedia thumbnail address is worked out from the file name (md5 path), no request is made
        self.assertEqual(osm_photo.commons_title({"wikimedia_commons": "File:Kamolon_masjidi.jpg"}), ("title", "File:Kamolon_masjidi.jpg"))
        self.assertEqual(osm_photo.commons_title({"image": "https://commons.wikimedia.org/wiki/File:A%20b.jpg"}), ("title", "File:A b.jpg"))
        self.assertEqual(osm_photo.commons_title({"image": "https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg?x=1"}),
                         ("url", "https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg"))
        for tags in ({}, {"image": "https://evil.example/x.jpg"}, {"wikimedia_commons": "Deggaron mosque"}, {"wikimedia_commons": "Category:Novza mosque"},
                     {"image": "http://upload.wikimedia.org/x.jpg"}):
            self.assertIsNone(osm_photo.commons_title(tags), tags)                         # any other site, plain http, a category or a bare name: no photo
            self.assertIsNone(osm_photo.photo_for(tags))
        name = "Kamolon_masjidi.jpg"
        h = hashlib.md5(name.encode()).hexdigest()
        self.assertEqual(osm_photo.thumb_for("File:Kamolon masjidi.jpg"),
                         ("https://upload.wikimedia.org/wikipedia/commons/thumb/%s/%s/%s/500px-%s" % (h[0], h[:2], name, name),
                          "https://commons.wikimedia.org/wiki/File:" + name))
        t, credit = osm_photo.thumb_for("File:Oʻzbek (1).JPG")                             # quoted, extension case-insensitive
        self.assertIn("%CA%BB", t); self.assertIn("(1)", t); self.assertTrue(credit.startswith("https://commons.wikimedia.org/wiki/File:"))
        for bad in ("File:map.svg", "File:doc.pdf", "File:", "File:a/b.jpg"):
            self.assertIsNone(osm_photo.thumb_for(bad), bad)                               # not a picture a list can show
        self.assertEqual(osm_photo.photo_for({"image": "https://upload.wikimedia.org/x/y.jpg"}), ("https://upload.wikimedia.org/x/y.jpg", None))
        self.assertEqual(osm_photo.photo_for({"wikimedia_commons": "File:Z.jpg"}), osm_photo.thumb_for("File:Z.jpg"))

        data = {"elements": [
            {"type": "node", "id": 7901, "lat": -38.5, "lon": 147.5, "tags": {"name": "With photo", "wikimedia_commons": "File:Z.jpg"}},
            {"type": "node", "id": 7902, "lat": -38.6, "lon": 147.6, "tags": {"name": "Without"}}]}
        store = self.srv.store
        self.assertEqual(import_osm.run(data, make_cfg(), store)["photos"], 1)
        self.assertEqual(import_osm.sync_photos(data, store), 0)                            # set once, not at every sync
        items = {m["name"]: m for m in self.call("GET", "/mosques?bbox=147.4,-38.8,147.8,-38.4")[1]["data"]["items"]}
        with_photo = self.call("GET", "/mosques/" + items["With photo"]["id"])[1]["data"]
        self.assertEqual(with_photo["photoUrl"], osm_photo.thumb_for("File:Z.jpg")[0])
        self.assertTrue(with_photo["photoCredit"].endswith("File:Z.jpg"))
        self.assertIsNone(self.call("GET", "/mosques/" + items["Without"]["id"])[1]["data"]["photoUrl"])
        s, r = self.call("PATCH", "/mosques/" + items["With photo"]["id"], {"photoUrl": "https://evil.example/x.jpg", "prayerTimes": {"fajr": "05:00"}})
        self.assertEqual((s, r["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "photoUrl"))      # not part of the API's edit
        s, r = self.call("PATCH", "/mosques/" + items["With photo"]["id"], {"prayerTimes": {"fajr": "05:00"}})
        self.assertEqual((s, r["data"]["photoUrl"]), (200, with_photo["photoUrl"]))

    def test_imported_mosques_only_allow_time_edits(self):
        import_osm.run({"elements": [{"type": "node", "id": 7501, "lat": 32.0, "lon": 74.0, "tags": {"name": "Map mosque", "addr:full": "Map street 1"}}]},
                       make_cfg(), self.srv.store)
        mid = self.call("GET", "/mosques?bbox=73.9,31.9,74.1,32.1")[1]["data"]["items"][0]["id"]
        self.assertEqual(self.call("GET", "/mosques/" + mid)[1]["data"]["origin"], "imported")
        for field, value in (("name", "Renamed"), ("address", "Other street"), ("lat", 32.001), ("lon", 74.001)):
            s, r = self.call("PATCH", "/mosques/" + mid, {field: value})
            self.assertEqual((s, r["code"], r["message"]["code"], r["message"]["reason"]), (403, "MASJIDUZ_FIELD_LOCKED", "error.fieldLocked", field), field)
        same = {"name": "Map mosque", "address": "Map street 1", "prayerTimes": {"fajr": "05:10"}}
        s, r = self.call("PATCH", "/mosques/" + mid, same)                                   # unchanged values are fine
        self.assertEqual((s, r["data"]["prayerTimes"]["fajr"], r["data"]["name"]), (200, "05:10", "Map mosque"))
        # a community-added mosque keeps full editing
        c = self.made(32.5, 74.5, name="Mine")
        s, r = self.call("PATCH", "/mosques/" + c["id"], {"name": "Mine 2", "address": "Somewhere", "lat": 32.5001})
        self.assertEqual((s, r["data"]["name"], r["data"]["address"]), (200, "Mine 2", "Somewhere"))

    def test_osm_address_building(self):
        a = import_osm.osm_address
        self.assertEqual(a({"addr:full": "  Оқлон маҳалла, Кўкча дарвоза кўчаси 27 "}), "Оқлон маҳалла, Кўкча дарвоза кўчаси 27")
        self.assertEqual(a({"addr:street": "Quva ko'chasi", "addr:housenumber": "81a", "addr:city": "Farg'ona"}), "Quva ko'chasi 81a, Farg'ona")
        self.assertEqual(a({"addr:city": "Angren", "addr:postcode": "110200"}), "Angren, 110200")
        self.assertIsNone(a({"addr:postcode": "110200"}))              # postcode alone is not an address
        self.assertIsNone(a({"name": "x"}))
        self.assertEqual(a({"addr:full": "A\u202eB\x00C"}), "ABC")                # control/bidi characters are stripped

    def test_search_and_admin_views(self):
        self.add(37.5, 64.5, name="Alisher Navoiy jome masjidi")
        self.add(37.6, 64.6, name="100%_special")
        s, r = self.call("GET", "/mosques/search?q=navoiy")
        self.assertEqual([m["name"] for m in r["data"]["items"]], ["Alisher Navoiy jome masjidi"])
        self.assertIsNone(r["data"]["nextCursor"])
        self.assertEqual(set(r["data"]["items"][0]), {"id", "name", "address", "photoUrl", "lat", "lon", "status"})
        cyr = self.call("GET", "/mosques/search?q=" + "%D0%BD%D0%B0%D0%B2%D0%BE%D0%B8%D0%B9")[1]["data"]["items"]
        self.assertEqual([m["name"] for m in cyr], ["Alisher Navoiy jome masjidi"])
        self.assertEqual(self.call("GET", "/mosques/search?q=%25")[1]["data"]["items"], [])      # '%' is literal, not a wildcard
        self.assertEqual(self.call("GET", "/mosques/search?q=a")[1]["data"]["items"], [])        # too short
        self.assertEqual(self.call("GET", "/mosques/search")[1]["data"]["items"], [])
        self.assertEqual(self.call("GET", "/admin/overview")[0], 401)
        ov = self.call("GET", "/admin/overview", admin=True)[1]["data"]
        self.assertIn("recent", ov); self.assertIn("openReports", ov); self.assertGreaterEqual(ov["counts"]["community/unverified"], 2)
        self.assertNotIn("import:osm", [h["actor"] for h in ov["recent"]])
        s, lst = self.call("GET", "/admin/mosques?status=archived", admin=True)
        self.assertEqual(s, 200); self.assertIn("total", lst["data"]); self.assertIn("nextCursor", lst["data"])
        s, r = self.call("GET", "/admin/mosques?status=bogus", admin=True)
        self.assertEqual((s, r["code"]), (400, "VALIDATION_ERROR"))

    def test_admin_lists_page_with_a_cursor(self):
        for i in range(3):
            self.made(26.0 + i * 0.01, 79.0, name="Admin page %d" % i)
        seen, cursor = [], None
        for _ in range(100):
            d = self.call("GET", "/admin/mosques?status=unverified&limit=2" + ("&cursor=" + cursor if cursor else ""), admin=True)[1]["data"]
            seen += [m["id"] for m in d["items"]]
            cursor = d["nextCursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), d["total"])

    def test_config_and_me_for_anonymous(self):
        c = self.call("GET", "/config")[1]["data"]
        self.assertEqual(set(c), {"styleUrl", "duplicateRadiusM", "showTileMosques", "prayer", "botUsername", "adminTokenLogin", "photoReading", "voiceReading", "map"})
        self.assertEqual(set(c["prayer"]), {"fajrAngle", "ishaAngle", "asrFactor", "dhuhrOffsetMin"})
        self.assertEqual((c["adminTokenLogin"], c["photoReading"], c["voiceReading"]), (False, False, False))      # no admin token, no Gemini key here
        self.assertEqual(c["duplicateRadiusM"], 50)
        self.assertEqual(self.call("GET", "/me")[1]["data"], {"lang": None, "moderator": False})

    def test_mosque_lists_are_cursor_lists_and_the_map_list_is_compact(self):
        for i in range(4):
            self.made(25.0 + i * 0.01, 80.0, name="Listed %d" % i)
        seen, cursor, pages = [], None, 0
        while True:
            d = self.call("GET", "/mosques?limit=100" + ("&cursor=" + cursor if cursor else ""))[1]["data"]
            self.assertLessEqual(len(d["items"]), 100)
            self.assertEqual(set(d), {"items", "nextCursor"})
            seen += [m["id"] for m in d["items"]]
            pages += 1
            cursor = d["nextCursor"]
            if not cursor:
                break
            self.assertLess(pages, 500)
        self.assertEqual(len(seen), len(set(seen))); self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(seen), db("SELECT count(*) FROM mosques WHERE status<>'archived'")[0][0])
        full = self.call("GET", "/mosques?limit=1")[1]["data"]["items"][0]
        self.assertLessEqual({"id", "lat", "lon", "name", "address", "prayerTimes", "source", "origin", "status", "verificationNote", "version", "osmRef", "photoUrl",
                              "photoCredit", "createdAt", "updatedAt", "timesUpdatedAt"}, set(full))
        for bad in ("limit=0", "limit=101", "limit=abc", "cursor=" + app.enc_cursor("not-a-uuid")):
            s, r = self.call("GET", "/mosques?" + bad)
            self.assertEqual((s, r["code"]), (400, "VALIDATION_ERROR"), bad)
        # the map list
        s, r = self.call("GET", "/mosques?bbox=79.9,24.9,80.1,25.2", full=True).status, self.call("GET", "/mosques?bbox=79.9,24.9,80.1,25.2")[1]
        self.assertEqual(s, 200)
        self.assertEqual(set(r["data"]), {"items", "truncated"}); self.assertFalse(r["data"]["truncated"]); self.assertEqual(len(r["data"]["items"]), 4)
        self.assertEqual(set(r["data"]["items"][0]), {"id", "lat", "lon", "name", "status"})
        for bad in ("bbox=1,2,3", "bbox=a,b,c,d", "bbox=10,10,5,5", "bbox=181,0,182,1", "bbox=0,-91,1,0", "bbox=nan,0,1,1"):
            s, r = self.call("GET", "/mosques?" + bad)
            self.assertEqual((s, r["code"], r["message"]["code"]), (400, "VALIDATION_ERROR", "error.invalidBbox"), bad)

    def test_map_list_is_cached_and_revalidated_with_an_etag(self):
        q = "/mosques?bbox=81.9,20.9,82.1,21.1"
        r = self.call("GET", q, full=True)
        self.assertEqual((r.status, r.header("Cache-Control")), (200, "public, max-age=60"))
        etag = r.header("ETag")
        self.assertTrue(etag)
        again = self.call("GET", q, full=True, headers={"If-None-Match": etag})
        self.assertEqual((again.status, again.raw), (304, b""))
        self.assertEqual(again.header("ETag"), etag)
        self.assertTrue(again.header("X-Request-Id"))
        self.made(21.0, 82.0, name="Appears")                                               # the content changed: the old ETag no longer matches
        r2 = self.call("GET", q, full=True, headers={"If-None-Match": etag})
        self.assertEqual(r2.status, 200); self.assertNotEqual(r2.header("ETag"), etag)
        self.assertEqual([m["name"] for m in r2.json["data"]["items"]], ["Appears"])
        m = r2.json["data"]["items"][0]
        self.call("POST", "/admin/mosques/%s/archive" % m["id"], {}, admin=True)
        self.assertEqual(self.call("GET", q)[1]["data"]["items"], [])                       # archived records are not on the map

    def test_map_list_truncates_at_3000(self):
        with helpers.psycopg.connect(helpers.TEST_URL, autocommit=True, options="-c search_path=masjiduz") as c:
            c.execute("INSERT INTO mosques(lat,lon,source,origin,status) SELECT 10 + g * 0.00001, 100, 'import', 'imported', 'unverified' "
                      "FROM generate_series(1, 3005) g")
        d = self.call("GET", "/mosques?bbox=99.9,9.9,100.1,10.2")[1]["data"]
        self.assertEqual((len(d["items"]), d["truncated"]), (3000, True))
        c2 = self.call("GET", "/mosques?bbox=99.9,9.9,100.1,10.2", full=True)
        self.assertEqual(c2.header("Cache-Control"), "public, max-age=60")

    def test_head_requests(self):
        for path in (helpers.API + "/health", helpers.WEB + "/", helpers.WEB + "/vendor/maplibre-gl.js"):
            r = self.req("HEAD", path, headers={"Accept-Encoding": "gzip"})
            self.assertEqual((r.status, r.raw), (200, b""), path)
            self.assertGreater(int(r.header("Content-Length")), 0)

    def test_validation_of_text_fields(self):
        m = self.made(24.0, 81.0, name="Text rules")
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"name": "x" * 201})
        self.assertEqual((s, r["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "name"))
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"address": 5})
        self.assertEqual((s, r["message"]["reason"]), (400, "address"))
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"name": "bad \ud83d surrogate"}, ctype="application/json")
        self.assertEqual(s, 400)
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"prayerTimes": {"nope": "05:00"}})
        self.assertEqual((s, r["message"]["code"]), (400, "error.invalidPrayerTimes"))
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"prayerTimes": "05:00"})
        self.assertEqual((s, r["message"]["code"]), (400, "error.invalidPrayerTimes"))
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"name": "A\u202eB\x00C  "})
        self.assertEqual(r["data"]["name"], "ABC")
        s, r = self.call("PATCH", "/mosques/" + m["id"], {"name": "   "})                  # blank clears
        self.assertIsNone(r["data"]["name"])
        self.assertEqual(self.call("PATCH", "/mosques/00000000-0000-0000-0000-000000000000", {"name": "x"})[0], 404)


def helpers_ok(data, status=200):
    return app.ok_body(status, data)


class Moderation(ServerCase):
    def setUp(self):
        reset_db()

    def test_admin_only_for_allow_listed_verified_telegram_id(self):
        _, addr = self.serve()                      # own server: its failed-login counter must not interact with other tests
        owner = {"id": ADMIN_ID, "first_name": "Owner"}

        def get(headers):
            return self.call("GET", "/admin/overview", headers=headers, addr=addr)[0]
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data(owner)}), 200)                            # allow-listed + signed
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data(owner, age=23 * 3600)}), 200)             # up to 24 h old is fine
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data({"id": 111, "first_name": "Eve"})}), 401)   # signed, not allow-listed
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data(owner, token="999:OTHER")}), 401)         # signed with another bot token
        self.assertEqual(get({"X-Telegram-Init-Data": sign_init_data(owner, age=25 * 3600)}), 401)             # older than 24 h
        forged = sign_init_data({"id": 111, "first_name": "Eve"}).replace("111", str(ADMIN_ID))
        self.assertEqual(get({"X-Telegram-Init-Data": forged}), 401)                                           # claiming the owner's id breaks the signature
        self.assertEqual(get({"Authorization": "Bearer secret"}), 401)                                         # token login is off without ADMIN_TOKEN
        self.assertEqual(get({"X-Client-Id": "aaaaaaaa-1"}), 401)
        # moderator actions are attributed to the Telegram id, shown publicly only as the role
        m = self.made(36.5, 62.5, addr=addr, name="Role test")
        self.call("POST", "/admin/mosques/%s/archive" % m["id"], {"note": "t"}, admin=True, addr=addr)
        self.assertEqual(db("SELECT actor FROM mosque_history WHERE mosque_id=%s AND action='archive'", m["id"])[0][0], "admin:%d" % ADMIN_ID)
        items = self.call("GET", "/mosques/%s/history" % m["id"], addr=addr)[1]["data"]["items"]
        self.assertEqual(items[0]["actor"], "moderator"); self.assertNotIn(str(ADMIN_ID), json.dumps(items))

    def made(self, lat, lon, addr=None, **kw):
        s, r = self.call("POST", "/mosques", dict(lat=lat, lon=lon, **kw), addr=addr)
        self.assertEqual(s, 201, r)
        return r["data"]

    def test_error_is_401_envelope(self):
        _, addr = self.serve()
        r = self.call("GET", "/admin/overview", addr=addr, full=True)
        b = r.json
        self.assertEqual((r.status, b["success"], b["code"], b["message"]["code"], b["data"]), (401, False, "UNAUTHORIZED", "error.unauthorized", None))

    def test_bearer_token_login_when_configured(self):
        tok = "t0ken-" + "x" * 40
        _, addr = self.serve(ADMIN_TOKEN=tok)
        auth = lambda t: {"Authorization": t}                                                                    # noqa: E731
        self.assertEqual(self.call("GET", "/admin/overview", headers=auth("Bearer " + tok), addr=addr)[0], 200)
        for bad in ("Bearer " + tok[:-1], "Bearer " + tok + "x", tok, "Basic " + tok, "bearer " + tok, "Bearer "):
            self.assertEqual(self.call("GET", "/admin/overview", headers=auth(bad), addr=addr)[0], 401, bad)
        self.assertTrue(self.call("GET", "/config", addr=addr)[1]["data"]["adminTokenLogin"])
        m = self.made(35.0, 63.0, addr=addr, name="By token")
        s, r = self.call("POST", "/admin/mosques/%s/archive" % m["id"], {"note": "x"}, headers=auth("Bearer " + tok), addr=addr)
        self.assertEqual((s, r["data"]["status"]), (200, "archived"))
        self.assertEqual(db("SELECT actor FROM mosque_history WHERE mosque_id=%s AND action='archive'", m["id"])[0][0], "admin:token")
        items = self.call("GET", "/mosques/%s/history" % m["id"], addr=addr)[1]["data"]["items"]
        self.assertEqual(items[0]["actor"], "moderator"); self.assertNotIn(tok, json.dumps(items))

    def test_admin_disabled_without_allow_list_or_token(self):
        _, addr = self.serve(MODERATOR_IDS="")
        h = {"X-Telegram-Init-Data": sign_init_data({"id": ADMIN_ID})}
        self.assertEqual(self.call("GET", "/admin/overview", headers=h, addr=addr)[0], 401)          # empty allow-list = nobody is a moderator

    def test_admin_bruteforce_limit(self):
        _, addr = self.serve()
        bad = {"X-Telegram-Init-Data": "hash=bad&user=%7B%7D"}
        ip = "198.51.100.9"
        codes = [self.call("GET", "/admin/overview", headers=bad, ip=ip, addr=addr)[0] for _ in range(12)]
        self.assertEqual(codes[:10], [401] * 10); self.assertEqual(codes[10:], [429, 429])
        r = self.call("GET", "/admin/overview", admin=True, ip=ip, addr=addr, full=True)
        self.assertEqual(r.status, 429)                                                    # locked out, even with valid data
        self.assertEqual(r.json["code"], "RATE_LIMITED")
        self.assertRegex(r.header("Retry-After"), r"^\d+$"); self.assertGreaterEqual(int(r.header("Retry-After")), 1)
        self.assertEqual(self.call("GET", "/admin/overview", admin=True, ip="198.51.100.10", addr=addr)[0], 200)   # another address is not affected

    def test_good_logins_are_not_counted_as_failures(self):
        _, addr = self.serve()
        for _ in range(15):
            self.assertEqual(self.call("GET", "/admin/overview", admin=True, ip="198.51.100.20", addr=addr)[0], 200)

    def test_moderation_actions_and_dismiss(self):
        _, addr = self.serve()
        m = self.made(23.0, 82.0, addr=addr, name="To moderate")
        for i, c in enumerate(("bbbbbbbb-1", "cccccccc-1")):
            self.call("POST", "/mosques/%s/reports" % m["id"], {"kind": "incorrect", "reason": "r%d" % i}, client=c, addr=addr)
        self.assertEqual(self.call("GET", "/mosques/" + m["id"], addr=addr)[1]["data"]["status"], "reported")
        g = self.call("GET", "/admin/reports", admin=True, addr=addr)[1]["data"]["groups"]
        self.assertEqual((g[0]["mosqueId"], g[0]["kind"], g[0]["count"]), (m["id"], "incorrect", 2))
        r = self.call("POST", "/admin/mosques/%s/dismiss" % m["id"], {"note": "fine"}, admin=True, addr=addr)[1]["data"]
        self.assertEqual(r["status"], "unverified")
        self.assertEqual(self.call("GET", "/admin/reports", admin=True, addr=addr)[1]["data"]["groups"], [])
        self.assertEqual(len(self.call("GET", "/admin/reports?all=1", admin=True, addr=addr)[1]["data"]["groups"]), 1)
        s, r = self.call("POST", "/admin/mosques/%s/explode" % m["id"], {}, admin=True, addr=addr)
        self.assertEqual((s, r["code"]), (404, "NOT_FOUND"))
        s, r = self.call("POST", "/admin/mosques/%s/verify" % m["id"], {"note": "ok", "extra": 1}, admin=True, addr=addr)
        self.assertEqual((s, r["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "extra"))
        s, r = self.call("POST", "/admin/mosques/00000000-0000-0000-0000-000000000000/archive", {}, admin=True, addr=addr)
        self.assertEqual((s, r["code"]), (404, "NOT_FOUND"))

    def test_mini_app_follows_the_language_chosen_in_the_bot(self):
        import botdb
        srv, addr = self.serve()
        user = {"id": 880001, "first_name": "Lang"}
        h = {"X-Telegram-Init-Data": sign_init_data(user)}
        call = lambda method, path, headers=None, body=None: self.call(method, path, body, headers=headers, addr=addr)    # noqa: E731
        self.assertEqual(call("GET", "/me", h)[1]["data"], {"lang": None, "moderator": False})  # not asked yet in the bot
        botdb.BotDB(srv.store).set_lang(880001, "uz-cyrl")
        self.assertEqual(call("GET", "/me", h)[1]["data"], {"lang": "uz-cyrl", "moderator": False})
        forged = {"X-Telegram-Init-Data": sign_init_data(user, token="1:OTHER")}
        self.assertEqual(call("GET", "/me", forged)[1]["data"], {"lang": None, "moderator": False})       # forged: nothing
        self.assertEqual(call("GET", "/me")[1]["data"], {"lang": None, "moderator": False})
        s, r = call("POST", "/me/lang", h, {"lang": "ru"})                                      # chosen on the web: the bot follows
        self.assertEqual((s, r["data"]), (200, {"lang": "ru"}))
        self.assertEqual(botdb.BotDB(srv.store).lang_of(880001), "ru")
        s, r = call("POST", "/me/lang", h, {"lang": "xx"})
        self.assertEqual((s, r["code"], r["message"]["code"]), (400, "VALIDATION_ERROR", "error.invalidLang"))
        self.assertEqual(call("POST", "/me/lang", None, {"lang": "ru"})[0], 400)                # anonymous cannot set anything
        self.assertEqual(call("POST", "/me/lang", h, {"lang": "ru", "x": 1})[1]["message"]["reason"], "x")


class TelegramIdentity(ServerCase):
    TOKEN = "123456:TEST-TOKEN"

    def setUp(self):
        reset_db()

    def test_verified_telegram_user_is_recorded_and_forgery_is_not(self):
        _, addr = self.serve(TELEGRAM_BOT_TOKEN=self.TOKEN)

        def post(init, cid, lat):
            s, r = self.call("POST", "/mosques", {"lat": lat, "lon": 60.0}, client=cid, headers={"X-Telegram-Init-Data": init}, addr=addr)
            self.assertEqual(s, 201, r)
            return r["data"]
        user = {"id": 777001, "username": "nodir_test", "first_name": "Nodir", "last_name": "T", "language_code": "uz"}
        m = post(sign_init_data(user), "cccccccc-1", 35.0)
        self.assertEqual(db("SELECT kind, telegram_id, tg_username, tg_first_name, tg_language FROM actors WHERE actor_id='tg:777001'"),
                         [("telegram", 777001, "nodir_test", "Nodir", "uz")])
        self.assertEqual(db("SELECT creator_id FROM mosques WHERE id=%s", m["id"])[0][0], "tg:777001")
        # forged initData: ignored, falls back to the browser id, and NO Telegram profile is stored
        forged = sign_init_data(user)
        forged = forged.replace("777001", "999")
        m2 = post(forged, "dddddddd-12", 35.5)
        self.assertEqual(db("SELECT creator_id FROM mosques WHERE id=%s", m2["id"])[0][0], "client:dddddddd-12")
        self.assertEqual(db("SELECT count(*) FROM actors WHERE telegram_id=999")[0][0], 0)

    def test_verify_telegram_unit(self):
        user = {"id": 5, "first_name": "A"}
        self.assertEqual(app.verify_telegram(sign_init_data(user, self.TOKEN), self.TOKEN), user)
        self.assertIsNone(app.verify_telegram(sign_init_data(user, self.TOKEN), "1:OTHER"))
        self.assertIsNone(app.verify_telegram(sign_init_data(user, self.TOKEN, age=90000), self.TOKEN))          # older than a day
        self.assertIsNotNone(app.verify_telegram(sign_init_data(user, self.TOKEN, age=90000), self.TOKEN, max_age=200000))
        self.assertIsNone(app.verify_telegram(sign_init_data({"id": "5"}, self.TOKEN), self.TOKEN))              # id must be a number
        for junk in ("", "hash=", "a=b", "hash=zz&user=%7B%7D", "\x00"):
            self.assertIsNone(app.verify_telegram(junk, self.TOKEN), junk)


class Concurrency(ServerCase):
    def test_threads_share_a_small_pool_without_503(self):
        reset_db()
        self.use(DB_POOL_MAX="4")
        mid = self.made(22.0, 83.0, name="Pooled")["id"]
        with ThreadPoolExecutor(16) as ex:
            codes = list(ex.map(lambda i: self.call("GET", "/mosques/" + mid)[0], range(120)))
        self.assertEqual(set(codes), {200})


if __name__ == "__main__":
    unittest.main()
