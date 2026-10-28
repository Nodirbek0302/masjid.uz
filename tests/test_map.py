"""The map provider: OpenStreetMap by default, optionally Yandex with a pool of keys (web = JavaScript API key, app = MapKit key)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from helpers import ServerCase, db, reset_db, sign_init_data  # noqa: E402

K1, K2 = "KEY-ONE-1234567890", "KEY-TWO-1234567890"
PHONE = "devicedevice0001"


class MapCase(ServerCase):
    def setUp(self):
        reset_db()
        self.use()

    def cfg_map(self, client="a0000001-1", **kw):
        s, r = self.call("GET", "/config", client=client, **kw)
        self.assertEqual(s, 200)
        return r["data"]["map"]

    def adm(self, method, path, body=None, **kw):
        return self.call(method, "/admin/map" + path, body, admin=True, **kw)

    def csp(self):
        return self.req("GET", helpers.WEB + "/").header("Content-Security-Policy")

    def yandex_on(self, *keys):
        self.assertEqual(self.adm("POST", "/provider", {"provider": "yandex"})[0], 200)
        for k in keys:
            s, r = self.adm("POST", "/keys", k)
            self.assertEqual(s, 201, r)
        return r["data"] if keys else None


class Defaults(MapCase):
    def test_default_is_osm_with_a_strict_csp(self):
        self.assertEqual(self.cfg_map(), {"provider": "osm"})
        c = self.csp()
        self.assertNotIn("unsafe-eval", c)
        self.assertNotIn("yandex", c)
        self.assertIn("script-src 'self' https://telegram.org;", c)
        d = self.adm("GET", "")[1]["data"]
        self.assertEqual(d, {"provider": "osm", "keys": [], "effective": {"web": "osm", "app": "osm"}})
        self.assertEqual(db("SELECT provider FROM map_settings"), [("osm",)])

    def test_the_four_map_tables_exist(self):
        for t in ("map_settings", "map_keys", "map_key_users", "map_key_reports"):
            self.assertEqual(db("SELECT to_regclass(%s) IS NOT NULL", "masjiduz." + t), [(True,)], t)
        self.assertEqual(db("SELECT count(*) FROM map_settings"), [(1,)])

    def test_yandex_chosen_but_no_key_is_still_osm_and_csp_still_opens(self):
        d = self.adm("POST", "/provider", {"provider": "yandex"})[1]["data"]
        self.assertEqual((d["provider"], d["effective"]), ("yandex", {"web": "osm", "app": "osm"}))
        self.assertEqual(self.cfg_map(), {"provider": "osm"})


class Admin(MapCase):
    def test_every_admin_map_route_needs_a_moderator(self):
        for method, path, body in (("GET", "", None), ("POST", "/provider", {"provider": "osm"}), ("POST", "/keys", {"key": K1}),
                                   ("PATCH", "/keys/1", {"enabled": False}), ("POST", "/keys/1/delete", {})):
            s, r = self.call(method, "/admin/map" + path, body)
            self.assertEqual((s, r["code"], r["message"]["code"]), (401, "UNAUTHORIZED", "error.unauthorized"), (method, path))
            s, r = self.call(method, "/admin/map" + path, body, headers={"X-Telegram-Init-Data": sign_init_data({"id": 111, "first_name": "Eve"})})
            self.assertEqual(s, 401, (method, path))
        self.assertEqual(db("SELECT count(*) FROM map_keys"), [(0,)])
        self.assertEqual(self.call("DELETE", "/admin/map/keys/1")[0], 405)                          # no DELETE method anywhere

    def test_the_bearer_token_works_too(self):
        tok = "t0ken-" + "x" * 40
        _, addr = self.serve(ADMIN_TOKEN=tok)
        s, r = self.call("POST", "/admin/map/provider", {"provider": "yandex"}, headers={"Authorization": "Bearer " + tok}, addr=addr)
        self.assertEqual((s, r["data"]["provider"]), (200, "yandex"))

    def test_keys_are_masked_in_the_listing_and_everything_is_camel_case(self):
        d = self.yandex_on({"key": K1, "label": "one", "dailyLimit": 2, "kind": "web"})
        k = d["keys"][0]
        self.assertEqual(set(k), {"id", "label", "kind", "key", "dailyLimit", "enabled", "exhausted", "usersToday"})
        self.assertEqual((k["label"], k["kind"], k["dailyLimit"], k["enabled"], k["exhausted"], k["usersToday"]), ("one", "web", 2, True, False, 0))
        self.assertEqual(k["key"], "KEY-…7890")
        self.assertNotIn(K1, json.dumps(d))
        self.assertNotIn(K1, json.dumps(self.adm("GET", "")[1]))
        self.assertEqual(set(d), {"provider", "keys", "effective"})
        self.assertEqual(d["effective"], {"web": "yandex", "app": "osm"})

    def test_add_validation(self):
        add = lambda body: self.adm("POST", "/keys", body)       # noqa: E731
        for bad in ("short", "has space in it!", "x" * 201, "", None, 5, "bad/slash/chars/xx"):
            s, r = add({"key": bad})
            self.assertEqual((s, r["code"], r["message"]["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "error.invalidKey", "key"), bad)
        s, r = add({"key": K1, "kind": "tablet"})
        self.assertEqual((s, r["code"], r["message"]["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "error.invalidKind", "kind"))
        for bad in (0, -5, "abc", None, 10_000_001, 1.5e99, []):
            s, r = add({"key": K1, "dailyLimit": bad})
            self.assertEqual((s, r["code"], r["message"]["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "error.invalidLimit", "dailyLimit"), bad)
        s, r = add({"key": K1, "daily_limit": 5})                                     # snake_case is not the API
        self.assertEqual((s, r["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "daily_limit"))
        s, r = add({"key": K1, "extra": 1})
        self.assertEqual((s, r["message"]["reason"]), (400, "extra"))
        self.assertEqual(db("SELECT count(*) FROM map_keys"), [(0,)])
        self.assertEqual(add({"key": K1, "label": "l" * 100})[0], 201)
        self.assertEqual(db("SELECT char_length(label), kind, daily_limit FROM map_keys"), [(60, "web", 25000)])        # label clipped, defaults
        s, r = add({"key": K1})
        self.assertEqual((s, r["code"], r["message"]["code"]), (409, "CONFLICT", "error.keyExists"))
        self.assertEqual(db("SELECT count(*) FROM map_keys"), [(1,)])

    def test_update_and_delete(self):
        self.yandex_on({"key": K1, "label": "one"}, {"key": K2, "label": "two", "kind": "app"})
        ids = [k["id"] for k in self.adm("GET", "")[1]["data"]["keys"]]
        s, r = self.adm("PATCH", "/keys/%d" % ids[0], {"enabled": False, "dailyLimit": 7, "label": "renamed"})
        k = r["data"]["keys"][0]
        self.assertEqual((s, k["enabled"], k["dailyLimit"], k["label"]), (200, False, 7, "renamed"))
        self.assertEqual(r["data"]["effective"], {"web": "osm", "app": "yandex"})              # the only web key is off
        self.assertEqual(self.cfg_map(), {"provider": "osm"})
        for body, reason in (({"dailyLimit": 0}, "dailyLimit"), ({"dailyLimit": "x"}, "dailyLimit"), ({"bogus": 1}, "bogus"),
                             ({"daily_limit": 3}, "daily_limit"), ({"key": K2}, "key")):
            s, r = self.adm("PATCH", "/keys/%d" % ids[0], body)
            self.assertEqual((s, r["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", reason), body)
        s, r = self.adm("PATCH", "/keys/%d" % ids[0], {})
        self.assertEqual((s, r["code"]), (400, "VALIDATION_ERROR"))                            # nothing to change
        s, r = self.adm("PATCH", "/keys/99999", {"enabled": True})
        self.assertEqual((s, r["code"], r["message"]["code"]), (404, "NOT_FOUND", "error.notFound"))
        s, r = self.adm("POST", "/keys/99999/delete", {})
        self.assertEqual((s, r["code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.adm("PATCH", "/keys/abc", {"enabled": True})[0], 404)           # not a key id: no such route
        s, r = self.adm("POST", "/keys/%d/delete" % ids[0], {})
        self.assertEqual((s, [k["label"] for k in r["data"]["keys"]]), (200, ["two"]))
        self.assertEqual(self.adm("POST", "/keys/%d/delete" % ids[0], {})[0], 404)            # gone
        self.assertEqual(self.adm("GET", "/keys")[0], 405)                                     # only POST is routed there

    def test_provider_validation(self):
        for bad in ("google", "", None, 5, "YANDEX"):
            s, r = self.adm("POST", "/provider", {"provider": bad})
            self.assertEqual((s, r["code"], r["message"]["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "error.invalidProvider", "provider"), bad)
        s, r = self.adm("POST", "/provider", {"provider": "yandex", "x": 1})
        self.assertEqual((s, r["message"]["reason"]), (400, "x"))
        self.assertEqual(self.adm("GET", "")[1]["data"]["provider"], "osm")


class Pool(MapCase):
    def test_pool_rotation_and_fallback(self):
        self.assertEqual(self.cfg_map("a0000001-1"), {"provider": "osm"})                   # the default: OpenStreetMap
        s, a = self.adm("POST", "/provider", {"provider": "yandex"})
        self.assertEqual((s, a["data"]["provider"], a["data"]["effective"]), (200, "yandex", {"web": "osm", "app": "osm"}))   # chosen, no key yet: OSM
        self.assertEqual(self.cfg_map("a0000001-1")["provider"], "osm")
        self.assertEqual(self.adm("POST", "/keys", {"key": "short"})[0], 400)
        self.assertEqual(self.adm("POST", "/keys", {"key": K1, "label": "one", "dailyLimit": 2})[0], 201)
        self.assertEqual(self.adm("POST", "/keys", {"key": K1})[0], 409)
        a = self.adm("POST", "/keys", {"key": K2, "label": "two", "dailyLimit": 1})[1]["data"]
        self.assertEqual(a["effective"], {"web": "yandex", "app": "osm"})                   # web keys only: the app has none
        self.assertNotIn(K1, json.dumps(a))
        # two users fill key one (limit 2), the third gets key two (limit 1), the fourth finds nothing left -> OSM
        c1, c2, c3, c4 = (self.cfg_map("a000000%d-1" % i) for i in (1, 2, 3, 4))
        self.assertEqual((c1["yandexKey"], c2["yandexKey"]), (K1, K1))
        self.assertEqual((c1["provider"], set(c1)), ("yandex", {"provider", "yandexKey", "keyId"}))
        self.assertEqual(c3["yandexKey"], K2)
        self.assertEqual(c4, {"provider": "osm"})
        self.assertEqual(self.cfg_map("a0000001-1")["yandexKey"], K1)                       # a user already counted keeps their key
        a = self.adm("GET", "")[1]["data"]
        self.assertEqual([k["usersToday"] for k in a["keys"]], [2, 1])
        # clients tell that Yandex refused key two: three different devices switch it off for the day
        kid = a["keys"][1]["id"]
        self.adm("PATCH", "/keys/%d" % kid, {"dailyLimit": 10})                              # room for the reporting clients (key one is full)
        for i, who in enumerate(("b0000001-1", "b0000002-1", "b0000003-1")):
            self.assertEqual(self.cfg_map(who)["yandexKey"], K2)                             # only a client that was handed the key can report it
            s, r = self.call("POST", "/map/yandex-failed", {"keyId": kid}, client=who)
            self.assertEqual((s, r["data"]["exhausted"]), (200, i == 2))
            self.assertEqual(set(r["data"]), {"exhausted", "map"})
            self.assertNotEqual(r["data"]["map"].get("keyId"), kid)                         # the reply already carries the next key (or OSM)
        a = self.adm("GET", "")[1]["data"]
        self.assertTrue(a["keys"][1]["exhausted"])
        self.assertEqual(self.cfg_map("a0000003-1"), {"provider": "osm"})                   # its user falls back (key one is full)
        # the owner raises key one's limit; then switches key two back on
        self.adm("PATCH", "/keys/%d" % a["keys"][0]["id"], {"dailyLimit": 50})
        self.assertEqual(self.cfg_map("a0000003-1")["yandexKey"], K1)
        s, a = self.adm("PATCH", "/keys/%d" % kid, {"reset": True, "enabled": False})
        self.assertEqual((a["data"]["keys"][1]["exhausted"], a["data"]["keys"][1]["enabled"]), (False, False))
        self.assertEqual(db("SELECT count(*) FROM map_key_reports"), [(0,)])
        # switching the provider off sends everybody to OSM, whatever the pool holds
        self.adm("POST", "/provider", {"provider": "osm"})
        self.assertEqual(self.cfg_map("a0000001-1"), {"provider": "osm"})
        self.assertEqual(self.adm("POST", "/provider", {"provider": "google"})[0], 400)
        s, a = self.adm("POST", "/keys/%d/delete" % kid, {})
        self.assertEqual([k["label"] for k in a["data"]["keys"]], ["one"])

    def test_three_accounts_switch_without_a_gap(self):
        self.adm("POST", "/provider", {"provider": "yandex"})
        for i in (1, 2, 3):
            self.adm("POST", "/keys", {"key": "ACCOUNT-%d-0123456789" % i, "label": "acc %d" % i})
        ids = [k["id"] for k in self.adm("GET", "")[1]["data"]["keys"]]
        me = "c0000001-1"
        self.assertEqual(self.cfg_map(me)["yandexKey"], "ACCOUNT-1-0123456789")
        # Yandex stops key 1 for this device: the reply already holds key 2, then key 3, then OSM
        r = self.call("POST", "/map/yandex-failed", {"keyId": ids[0]}, client=me)[1]["data"]
        self.assertEqual(r["map"]["yandexKey"], "ACCOUNT-2-0123456789")
        r = self.call("POST", "/map/yandex-failed", {"keyId": ids[1]}, client=me)[1]["data"]
        self.assertEqual(r["map"]["yandexKey"], "ACCOUNT-3-0123456789")
        r = self.call("POST", "/map/yandex-failed", {"keyId": ids[2]}, client=me)[1]["data"]
        self.assertEqual(r["map"], {"provider": "osm"})
        self.assertEqual(self.cfg_map(me), {"provider": "osm"})                             # stays on OSM for that device today
        self.assertEqual(self.cfg_map("c0000002-1")["yandexKey"], "ACCOUNT-1-0123456789")   # others are not affected by one device's reports

    def test_three_distinct_clients_exhaust_a_key_but_one_client_repeating_does_not(self):
        self.yandex_on({"key": K1}, {"key": K2})
        kid = self.adm("GET", "")[1]["data"]["keys"][0]["id"]
        for who in ("d0000001-1", "d0000002-1", "d0000003-1"):
            self.assertEqual(self.cfg_map(who)["yandexKey"], K1)
        for _ in range(5):
            r = self.call("POST", "/map/yandex-failed", {"keyId": kid}, client="d0000001-1")[1]["data"]
        self.assertFalse(r["exhausted"])
        self.assertEqual(db("SELECT count(*) FROM map_key_reports"), [(1,)])
        self.call("POST", "/map/yandex-failed", {"keyId": kid}, client="d0000002-1")
        r = self.call("POST", "/map/yandex-failed", {"keyId": kid}, client="d0000003-1")[1]["data"]
        self.assertTrue(r["exhausted"])
        self.assertEqual(r["map"]["yandexKey"], K2)                                         # the next key at once
        self.assertEqual(self.cfg_map("d0000009-1")["yandexKey"], K2)                       # everybody now gets key two

    def test_made_up_clients_cannot_exhaust_a_key(self):
        self.yandex_on({"key": K1}, {"key": K2})
        a = self.adm("GET", "")[1]["data"]["keys"]
        kid = a[0]["id"]
        for who in ("e10000001-1", "e10000002-1", "e10000003-1", "e10000004-1", "e10000005-1"):      # never handed a key: their reports count for nothing
            r = self.call("POST", "/map/yandex-failed", {"keyId": kid}, client=who)[1]["data"]
            self.assertFalse(r["exhausted"])
        self.assertEqual(db("SELECT count(*) FROM map_key_reports"), [(0,)])
        self.assertFalse(self.adm("GET", "")[1]["data"]["keys"][0]["exhausted"])
        self.assertEqual(self.cfg_map("e20000001-1")["yandexKey"], K1)                           # the pool is untouched
        # a client that holds key ONE cannot report key TWO either, and a device client cannot report a web key
        for who in ("e20000001-1", "e20000002-1", "e20000003-1"):
            self.cfg_map(who)
            r = self.call("POST", "/map/yandex-failed", {"keyId": a[1]["id"]}, client=who)[1]["data"]
            self.assertFalse(r["exhausted"])
        self.assertEqual(db("SELECT count(*) FROM map_key_reports"), [(0,)])
        for dev in ("devicedevice0021", "devicedevice0022", "devicedevice0023"):
            r = self.call("POST", "/map/yandex-failed", {"keyId": kid}, headers={"X-Device-Id": dev})[1]["data"]
            self.assertFalse(r["exhausted"])
        self.assertEqual(db("SELECT count(*) FROM map_key_reports"), [(0,)])
        # the genuine three do exhaust it
        for who in ("e20000001-1", "e20000002-1", "e20000003-1"):
            r = self.call("POST", "/map/yandex-failed", {"keyId": kid}, client=who)[1]["data"]
        self.assertTrue(r["exhausted"])

    def test_map_choice_uses_its_own_advisory_lock(self):
        import inspect
        src = inspect.getsource(helpers.app.Store.map_choice)
        self.assertIn("727005", src)
        self.assertNotIn("727002", src)                                                          # that one serialises duplicate checks of new mosques
        self.yandex_on({"key": K1})
        import threading
        with helpers.psycopg.connect(helpers.TEST_URL, autocommit=True) as c:
            c.execute("SELECT pg_advisory_lock(727002)")                                          # a mosque being added holds this
            out = []
            t = threading.Thread(target=lambda: out.append(self.cfg_map("e30000001-1")))
            t.start(); t.join(5)
            self.assertFalse(t.is_alive())
            self.assertEqual(out[0]["yandexKey"], K1)                                             # not blocked by it
            c.execute("SELECT pg_advisory_unlock(727002)")
        with helpers.psycopg.connect(helpers.TEST_URL, autocommit=True) as c:
            c.execute("SELECT pg_advisory_lock(727005)")
            out = []
            t = threading.Thread(target=lambda: out.append(self.cfg_map("e30000002-1")))
            t.start(); t.join(1)
            self.assertTrue(t.is_alive())                                                         # but it does wait for its own lock
            c.execute("SELECT pg_advisory_unlock(727005)")
            t.join(5)
            self.assertFalse(t.is_alive())

    def test_failed_report_validation(self):
        self.yandex_on({"key": K1})
        for bad in ('"abc"', "true", "null", "1.5", "[1]", '{"a":1}'):
            s, r = self.call("POST", "/map/yandex-failed", ('{"keyId": %s}' % bad).encode())
            self.assertEqual((s, r["code"], r["message"]["reason"]), (400, "VALIDATION_ERROR", "keyId"), bad)
        s, r = self.call("POST", "/map/yandex-failed", {"keyId": 1, "key_id": 1})
        self.assertEqual((s, r["code"]), (400, "VALIDATION_ERROR"))
        s, r = self.call("POST", "/map/yandex-failed", {"keyId": 424242})                    # unknown key: nothing happens
        self.assertEqual((s, r["data"]["exhausted"]), (200, False))
        s, r = self.call("POST", "/map/yandex-failed", {"keyId": 1}, headers={"X-Client-Id": ""})   # no identity: nothing to count
        self.assertEqual((s, r["data"]["exhausted"]), (200, False))
        self.assertEqual(db("SELECT count(*) FROM map_key_reports"), [(0,)])

    def test_web_and_app_keys_are_not_mixed(self):
        self.adm("POST", "/provider", {"provider": "yandex"})
        self.adm("POST", "/keys", {"key": "WEB-KEY-1234567890", "kind": "web"})
        a = self.adm("POST", "/keys", {"key": "APP-KEY-1234567890", "kind": "app"})[1]["data"]
        self.assertEqual(a["effective"], {"web": "yandex", "app": "yandex"})
        self.assertEqual([k["kind"] for k in a["keys"]], ["web", "app"])
        phone = {"X-Device-Id": PHONE}
        self.assertEqual(self.cfg_map("d0000001-1", headers=phone)["yandexKey"], "APP-KEY-1234567890")      # a mobile client: the MapKit key
        self.assertEqual(self.cfg_map("d0000002-1")["yandexKey"], "WEB-KEY-1234567890")                      # a browser never gets the app's key
        tg = {"X-Telegram-Init-Data": sign_init_data({"id": 5150, "first_name": "T"})}
        self.assertEqual(self.cfg_map("d0000003-1", headers=tg)["yandexKey"], "WEB-KEY-1234567890")          # Telegram Mini App: web key
        # the app's only key is refused: the app falls back to OSM, the web keeps its own key
        kid = a["keys"][1]["id"]
        for who in ("devicedevice0002", "devicedevice0003", "devicedevice0004"):
            self.assertEqual(self.cfg_map("d0000009-1", headers={"X-Device-Id": who})["yandexKey"], "APP-KEY-1234567890")
            r = self.call("POST", "/map/yandex-failed", {"keyId": kid}, client="d0000009-1", headers={"X-Device-Id": who})[1]["data"]
        self.assertEqual(r["map"], {"provider": "osm"})
        self.assertEqual(self.cfg_map("d0000002-1")["yandexKey"], "WEB-KEY-1234567890")
        self.assertEqual(self.cfg_map("d0000001-1", headers=phone), {"provider": "osm"})
        # only an app key: a browser gets OSM
        self.adm("POST", "/keys/%d/delete" % a["keys"][0]["id"], {})
        self.adm("PATCH", "/keys/%d" % kid, {"reset": True})
        self.assertEqual(self.cfg_map("d0000011-1"), {"provider": "osm"})
        self.assertEqual(self.cfg_map("d0000012-1", headers={"X-Device-Id": "devicedevice0012"})["yandexKey"], "APP-KEY-1234567890")

    def test_a_daily_limit_counts_distinct_users_once(self):
        self.yandex_on({"key": K1, "dailyLimit": 2})
        for _ in range(4):
            self.assertEqual(self.cfg_map("e0000001-1")["yandexKey"], K1)                  # the same user is counted once
        self.assertEqual(self.cfg_map("e0000002-1")["yandexKey"], K1)
        self.assertEqual(self.cfg_map("e0000003-1"), {"provider": "osm"})
        self.assertEqual(self.adm("GET", "")[1]["data"]["keys"][0]["usersToday"], 2)
        self.assertEqual(self.adm("GET", "")[1]["data"]["effective"]["web"], "osm")        # full for today
        self.assertEqual(self.cfg_map("e0000001-1")["yandexKey"], K1)                       # the sticky ones still draw
        self.assertEqual(self.cfg_map(headers={"X-Client-Id": ""})["provider"], "osm")           # no identity: cannot be counted, so no key is handed out

    def test_deleting_a_key_cleans_its_counters(self):
        self.yandex_on({"key": K1})
        self.cfg_map("f0000001-1")
        kid = self.adm("GET", "")[1]["data"]["keys"][0]["id"]
        self.call("POST", "/map/yandex-failed", {"keyId": kid}, client="f0000001-1")
        self.assertEqual(db("SELECT count(*) FROM map_key_users"), [(1,)])
        self.adm("POST", "/keys/%d/delete" % kid, {})
        self.assertEqual((db("SELECT count(*) FROM map_key_users"), db("SELECT count(*) FROM map_key_reports")), ([(0,)], [(0,)]))
        self.assertEqual(self.cfg_map("f0000001-1"), {"provider": "osm"})


class CspFollowsTheChoice(MapCase):
    def test_csp_opens_for_yandex_only_while_it_is_chosen(self):
        for path in ("/", "/app.js", "/admin.html", "/config.js"):
            c = self.req("GET", helpers.WEB + path).header("Content-Security-Policy")
            self.assertNotIn("yandex", c, path)
            self.assertNotIn("unsafe-eval", c, path)
        self.adm("POST", "/provider", {"provider": "yandex"})                              # the flag is reset at once, not after 10 s
        self.adm("POST", "/keys", {"key": K1})
        for path in ("/", "/app.js", "/admin.html"):
            c = self.req("GET", helpers.WEB + path).header("Content-Security-Policy")
            for host in ("https://api-maps.yandex.ru", "https://*.api-maps.yandex.ru", "https://yastatic.net", "https://*.maps.yandex.net"):
                self.assertIn(host, c, (path, host))
            self.assertIn("'unsafe-eval'", c)
            self.assertRegex(c, r"script-src 'self' https://telegram.org [^;]*https://api-maps.yandex.ru")
            self.assertIn("https://tiles.openfreemap.org", c)                              # the OSM hosts stay
            self.assertIn("frame-ancestors 'self' https://web.telegram.org", c)
            self.assertEqual(self.req("GET", helpers.WEB + path).header("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.adm("POST", "/provider", {"provider": "osm"})
        c = self.csp()
        self.assertNotIn("yandex", c)
        self.assertNotIn("unsafe-eval", c)
        self.assertEqual(c, helpers.app.csp(self.srv.store.cfg))                           # exactly the strict policy again

    def test_the_flag_is_cached_for_ten_seconds_but_a_provider_change_resets_it(self):
        self.assertFalse(self.srv.store.map_uses_yandex())
        db("UPDATE map_settings SET provider='yandex'")                                    # behind the server's back: the cache still says no
        self.assertFalse(self.srv.store.map_uses_yandex())
        self.assertNotIn("yandex", self.csp())
        self.srv.store._map_flag = None
        self.assertTrue(self.srv.store.map_uses_yandex())
        self.assertIn("yandex", self.csp())
        self.adm("POST", "/provider", {"provider": "osm"})
        self.assertFalse(self.srv.store.map_uses_yandex())

    def test_a_database_error_never_opens_the_csp(self):
        from unittest import mock
        self.srv.store._map_flag = None
        with mock.patch.object(self.srv.store, "pool", mock.Mock(connection=mock.Mock(side_effect=RuntimeError("db")))):
            self.assertFalse(self.srv.store.map_uses_yandex())

    def test_the_default_csp_helper_is_strict(self):
        c = helpers.app.csp(self.srv.store.cfg)
        self.assertNotIn("unsafe-eval", c)
        self.assertNotIn("yandex", c)
        y = helpers.app.csp(self.srv.store.cfg, True)
        self.assertIn("'unsafe-eval'", y)


class ConfigShape(MapCase):
    def test_config_carries_the_map_choice(self):
        c = self.call("GET", "/config")[1]["data"]
        self.assertEqual(set(c), {"styleUrl", "duplicateRadiusM", "showTileMosques", "prayer", "botUsername", "adminTokenLogin", "photoReading", "voiceReading", "map"})
        self.assertEqual(c["map"], {"provider": "osm"})


if __name__ == "__main__":
    unittest.main()
