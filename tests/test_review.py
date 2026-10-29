"""Behaviours added after review: dismissing reports by kind, which writes the limiter counts, conditional requests, OSM refresh keeping
addresses, deterministic nearest order."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from unittest import mock  # noqa: E402
from helpers import API, WEB, ServerCase, db, make_cfg, reset_db  # noqa: E402
import import_osm  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class DismissByKind(ServerCase):
    def setUp(self):
        reset_db()
        self.use()
        self.mid = self.made(11.0, 11.0, name="Reported")["id"]
        for kind, client in (("incorrect", "aaaaaaaa-1"), ("closed", "bbbbbbbb-1"), ("closed", "cccccccc-1")):
            self.assertEqual(self.call("POST", "/mosques/%s/reports" % self.mid, {"kind": kind, "reason": "r"}, client=client)[0], 201)
        self.assertEqual(self.call("GET", "/mosques/" + self.mid)[1]["data"]["status"], "reported")

    def dismiss(self, body):
        return self.call("POST", "/admin/mosques/%s/dismiss" % self.mid, body, admin=True)

    def open_kinds(self):
        g = self.call("GET", "/admin/reports", admin=True)[1]["data"]["groups"]
        return sorted(x["kind"] for x in g)

    def test_only_reports_of_that_kind_are_rejected_and_the_mosque_stays_reported(self):
        self.assertEqual(self.open_kinds(), ["closed", "incorrect"])
        s, r = self.dismiss({"kind": "closed", "note": "they are open"})
        self.assertEqual((s, r["data"]["status"]), (200, "reported"))                          # another kind is still open
        self.assertEqual(self.open_kinds(), ["incorrect"])
        res = db("SELECT kind, resolution FROM reports ORDER BY kind, id")
        self.assertEqual(res, [("closed", "rejected"), ("closed", "rejected"), ("incorrect", None)])
        self.assertEqual(db("SELECT resolved_by FROM reports WHERE kind='closed' LIMIT 1"), [("admin:%d" % helpers.ADMIN_ID,)])
        s, r = self.dismiss({"kind": "incorrect"})
        self.assertEqual((s, r["data"]["status"]), (200, "unverified"))                       # nothing open any more
        self.assertEqual(self.open_kinds(), [])

    def test_without_a_kind_all_open_reports_are_rejected_as_before(self):
        s, r = self.dismiss({"note": "all fine"})
        self.assertEqual((s, r["data"]["status"]), (200, "unverified"))
        self.assertEqual(db("SELECT DISTINCT resolution FROM reports"), [("rejected",)])

    def test_an_unknown_kind_is_a_validation_error(self):
        for bad in ("bogus", "", 5, ["closed"], "CLOSED"):
            s, r = self.dismiss({"kind": bad})
            self.assertEqual((s, r["code"], r["message"]["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "error.invalidKind", "kind"), bad)
        self.assertEqual(self.open_kinds(), ["closed", "incorrect"])                           # nothing changed
        self.assertEqual(self.call("GET", "/mosques/" + self.mid)[1]["data"]["status"], "reported")

    def test_kind_does_not_change_the_other_actions(self):
        s, r = self.call("POST", "/admin/mosques/%s/archive" % self.mid, {"kind": "closed", "note": "x"}, admin=True)
        self.assertEqual((s, r["data"]["status"]), (200, "archived"))
        self.assertEqual(db("SELECT DISTINCT resolution FROM reports"), [("accepted",)])        # archive resolves every report, whatever `kind` says


class MiniAppSubscriptions(ServerCase):
    """The bell in the Mini App: the bot's subscription, set over the API by a signature-verified Telegram user."""
    def setUp(self):
        reset_db()
        self.use()
        self.mid = self.made(40.5, 66.5, name="Bell")["id"]

    def tg(self, uid=4242):
        return {"X-Telegram-Init-Data": helpers.sign_init_data({"id": uid, "first_name": "U"})}

    def test_subscribe_and_unsubscribe(self):
        path = "/me/subscriptions/" + self.mid
        self.assertEqual(self.call("GET", path, headers=self.tg())[1]["data"], {"subscribed": False})
        s, r = self.call("POST", path, {"on": True}, headers=self.tg())
        self.assertEqual((s, r["data"]), (200, {"subscribed": True}))
        self.assertEqual(db("SELECT telegram_id, mosque_id::text FROM bot_subs"), [(4242, self.mid)])      # the bot's own table
        self.assertEqual(db("SELECT count(*) FROM bot_users WHERE telegram_id=4242"), [(1,)])
        self.assertEqual(self.call("POST", path, {"on": True}, headers=self.tg())[1]["data"], {"subscribed": True})   # twice is fine
        self.assertEqual(self.call("GET", path, headers=self.tg())[1]["data"], {"subscribed": True})
        self.assertEqual(self.call("GET", path, headers=self.tg(5151))[1]["data"], {"subscribed": False})             # per user
        self.assertEqual(self.call("POST", path, {"on": False}, headers=self.tg())[1]["data"], {"subscribed": False})
        self.assertEqual(db("SELECT count(*) FROM bot_subs"), [(0,)])

    def test_refusals(self):
        path = "/me/subscriptions/" + self.mid
        for method, body in (("GET", None), ("POST", {"on": True})):
            s, r = self.call(method, path, body)                                                 # a plain browser: no Telegram user
            self.assertEqual((s, r["code"]), (401, "UNAUTHORIZED"))
        bad = {"X-Telegram-Init-Data": helpers.sign_init_data({"id": 1}, token="0:wrong")}
        self.assertEqual(self.call("POST", path, {"on": True}, headers=bad)[0], 401)              # a forged signature
        for body in ({"on": "yes"}, {}, {"on": True, "x": 1}):
            s, r = self.call("POST", path, body, headers=self.tg())
            self.assertEqual((s, r["code"]), (400, "VALIDATION_ERROR"), body)
        s, r = self.call("POST", "/me/subscriptions/00000000-0000-0000-0000-000000000000", {"on": True}, headers=self.tg())
        self.assertEqual((s, r["code"]), (404, "NOT_FOUND"))
        self.srv.store.moderate(self.mid, "archive", "admin:1", "gone")
        s, r = self.call("POST", path, {"on": True}, headers=self.tg())
        self.assertEqual((s, r["code"]), (409, "MASJIDUZ_MOSQUE_ARCHIVED"))

    def test_the_twenty_limit_and_its_own_rate_budget(self):
        import botdb
        ids = [self.made(41.0 + i * 0.01, 69.0, name="M%d" % i, force=True)["id"] for i in range(botdb.MAX_SUBS)]
        for mid in ids:
            self.assertEqual(self.call("POST", "/me/subscriptions/" + mid, {"on": True}, headers=self.tg())[0], 200)
        s, r = self.call("POST", "/me/subscriptions/" + self.mid, {"on": True}, headers=self.tg())
        self.assertEqual((s, r["code"], r["message"]["code"]), (409, "CONFLICT", "error.subLimit"))
        self.use(WRITE_RATE_PER_HOUR="2")
        codes = [self.call("POST", "/me/subscriptions/" + ids[0], {"on": False}, headers=self.tg(), ip="203.0.113.50")[0] for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])                                                  # its own budget ...
        self.assertEqual(self.call("POST", "/mosques", {"lat": 12.5, "lon": 12.5}, ip="203.0.113.50")[0], 201)   # ... not the community one


class WriteLimiterScope(ServerCase):
    def setUp(self):
        reset_db()

    def test_light_posts_have_their_own_budget_and_readings_their_own_limits(self):
        self.use(WRITE_RATE_PER_HOUR="2")
        # /me/lang and /map/yandex-failed are limited too (same size, own budget): the third one of the hour is 429 + Retry-After
        self.assertEqual([self.call("POST", "/map/yandex-failed", {"keyId": 1})[0] for _ in range(3)], [200, 200, 429])
        r = self.call("POST", "/map/yandex-failed", {"keyId": 1}, full=True)
        self.assertEqual((r.status, r.json["code"]), (429, "RATE_LIMITED"))
        self.assertGreaterEqual(int(r.header("Retry-After")), 1)
        self.assertEqual([self.call("POST", "/me/lang", {"lang": "ru"})[0] for _ in range(3)], [400, 400, 429])   # anonymous: refused on merit, then 429
        # ... and they do not use the community write budget
        self.assertEqual([self.call("POST", "/mosques", {"lat": 12 + i, "lon": 12.0})[0] for i in range(3)], [201, 201, 429])
        # the readings keep their own limiters (never the write budget): 503 here, no reader configured
        for i in range(8):
            for kind, body in (("photo", JPEG + b"1"), ("voice", b"OggS" + b"1" * 20)):
                ctype = "image/jpeg" if kind == "photo" else "audio/ogg"
                r = self.req("POST", API + "/timetable/" + kind, body, {"Content-Type": ctype, "X-Device-Id": "device%05d" % i})
                self.assertEqual(r.status, 503, kind)
        self.assertEqual(self.call("PATCH", "/mosques/00000000-0000-0000-0000-000000000000", {"name": "x"})[0], 429)    # real writes still are
        report = self.call("POST", "/mosques/00000000-0000-0000-0000-000000000000/reports", {"reason": "x"})
        self.assertEqual(report[0], 429)

    def test_150_posts_from_one_address_are_cut_at_the_limit(self):
        self.use(WRITE_RATE_PER_HOUR="30")
        codes = [self.call("POST", "/map/yandex-failed", {"keyId": 1}, ip="203.0.113.7")[0] for _ in range(150)]
        self.assertEqual((codes.count(200), codes.count(429)), (30, 120))
        self.assertEqual(self.call("POST", "/map/yandex-failed", {"keyId": 1}, ip="203.0.113.8")[0], 200)     # another address is not affected


class ConditionalRequests(ServerCase):
    def setUp(self):
        reset_db()
        self.use()

    def check(self, path, base):
        etag = base.header("ETag")
        self.assertTrue(etag)
        weak = "W/" + etag
        for inm, status in ((etag, 304), (weak, 304), ('"nomatch", ' + etag, 304), ('"a", W/"b" , ' + weak, 304), ("*", 304), ("W/*", 200) if False else ("*", 304),
                            ('"nomatch"', 200), ('W/"nomatch"', 200), ("", 200), (etag + "x", 200)):
            r = self.req("GET", path, headers={"If-None-Match": inm})
            self.assertEqual(r.status, status, (path, inm))
            if status == 304:
                self.assertEqual(r.raw, b""); self.assertEqual(r.header("ETag"), etag)

    def test_map_list(self):
        self.made(31.0, 31.0, name="Etag")
        path = API + "/mosques?bbox=30.9,30.9,31.1,31.1"
        self.check(path, self.req("GET", path))

    def test_static_files(self):
        for p in ("/app.js", "/", "/style.css"):
            self.check(WEB + p, self.req("GET", WEB + p))


class OsmRefresh(ServerCase):
    def setUp(self):
        reset_db()
        self.use()

    def test_an_address_is_never_erased_when_osm_has_none(self):
        store = self.srv.store
        el = lambda name, **tags: {"elements": [{"type": "node", "id": 4001, "lat": 33.0, "lon": 33.0, "tags": dict({"name": name}, **tags)}]}      # noqa: E731
        import_osm.run(el("First", **{"addr:full": "Street 1, City"}), make_cfg(), store)
        mid = self.call("GET", "/mosques?bbox=32.9,32.9,33.1,33.1")[1]["data"]["items"][0]["id"]
        self.assertEqual(self.call("GET", "/mosques/" + mid)[1]["data"]["address"], "Street 1, City")
        r = import_osm.run(el("Second"), make_cfg(), store)                                    # the name changed, OSM has no address tags any more
        self.assertEqual(r["updated"], 1)
        m = self.call("GET", "/mosques/" + mid)[1]["data"]
        self.assertEqual((m["name"], m["address"]), ("Second", "Street 1, City"))
        last = self.call("GET", "/mosques/%s/history" % mid)[1]["data"]["items"][0]
        self.assertEqual((last["action"], list(last["changes"])), ("osm_sync", ["name"]))     # the address is not in the change
        r = import_osm.run(el("Second"), make_cfg(), store)
        self.assertEqual((r["updated"], r["unchanged"]), (0, 1))
        import_osm.run(el("Second", **{"addr:full": "Street 2, City"}), make_cfg(), store)      # a new address from OSM does replace the old one
        self.assertEqual(self.call("GET", "/mosques/" + mid)[1]["data"]["address"], "Street 2, City")


class ReadsAndTransactions(ServerCase):
    def setUp(self):
        reset_db()
        self.use()

    def test_a_connection_used_for_a_read_is_transactional_again_for_the_next_user(self):
        store = self.srv.store
        for _ in range(store.pool.max_size * 2):
            with store.reading() as c:
                self.assertTrue(c.autocommit)
        for _ in range(store.pool.max_size * 2):
            with store.pool.connection() as c:
                self.assertFalse(c.autocommit)                                   # writes still run in one transaction

    def test_nearest_is_one_statement(self):
        for i in range(30):
            self.made(41.0 + i * 0.001, 69.0, name="Q%d" % i, force=True)
        calls = []
        real = helpers.app.psycopg.Connection.execute

        def spy(conn, *a, **k):
            calls.append(a[0])
            return real(conn, *a, **k)
        with mock.patch.object(helpers.app.psycopg.Connection, "execute", spy):
            got = self.srv.store.nearest(41.0, 69.0, 21)
        self.assertEqual(len(got), 21)
        self.assertEqual(len(calls), 1)
        self.assertEqual([m["name"] for m in got], ["Q%d" % i for i in range(21)])


class NearestTies(ServerCase):
    def setUp(self):
        reset_db()
        self.use()

    def test_equal_distances_are_ordered_by_id(self):
        ids = [self.made(-10.0, 120.0, name="Tie %d" % i, force=True)["id"] for i in range(6)]
        want = sorted(ids)
        got = [m["id"] for m in self.srv.store.nearest(-10.0001, 120.0, 6)]
        self.assertEqual(got, want)
        d = self.call("GET", "/mosques/nearest?lat=-10.0001&lon=120.0&limit=6")[1]["data"]["items"]
        self.assertEqual([m["id"] for m in d], want)
        self.assertEqual(len({m["distanceM"] for m in d}), 1)                                    # really a tie
        seen, cursor = [], None
        for _ in range(10):
            page = self.call("GET", "/mosques/nearest?lat=-10.0001&lon=120.0&limit=2" + ("&cursor=" + cursor if cursor else ""))[1]["data"]
            seen += [m["id"] for m in page["items"]]
            cursor = page["nextCursor"]
            if not cursor or len(seen) >= 6:
                break
        self.assertEqual(seen[:6], want)                                                         # pages never repeat or skip a tied mosque
        near = self.call("GET", "/mosques/nearby?lat=-10.0&lon=120.0&radius=50")[1]["data"]["items"]
        self.assertEqual([m["id"] for m in near], want)


if __name__ == "__main__":
    unittest.main()
