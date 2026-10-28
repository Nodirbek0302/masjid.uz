"""Timetable reading from a photo / a recording: server/aipool.py (slots, limits, fallback to the next model, cache, second opinion) behind server/gemini.py and
the two HTTP routes. The network is never touched: `aipool.call_provider` (or `urllib.request.urlopen` for the request-shape tests) is replaced."""
import io
import itertools
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from helpers import ServerCase, db, make_cfg, reset_db  # noqa: E402
import aipool  # noqa: E402
import gemini  # noqa: E402

GOOD = {"fajr": "05:10", "dhuhr": "12:30", "asr": "16:00", "maghrib": "18:40", "isha": "20:00"}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
_n = itertools.count()


SPOKEN = {"fajr": {"hour": 5, "minute": 10}, "dhuhr": {"hour": 12, "minute": 30}, "asr": {"hour": 4}, "maghrib": {"hour": 6, "minute": 40},
          "isha": {"hour": 8, "minute": None}}              # what the model says for GOOD, hours as spoken (no AM/PM, a missing minute is 0)


def say(heard="Bomdod besh o'n", **over):
    return dict(SPOKEN, heard=heard, **over)


def photo():
    """A new, distinct 'photo' every time (the reader caches by content)."""
    return JPEG + str(next(_n)).encode()


class Provider:
    """Fake of aipool.call_provider. `script(slot, kind, n)` returns the model's dict or raises aipool.Refused; every call is recorded."""
    def __init__(self, script=None):
        self.script = script or (lambda slot, kind, n: dict(GOOD) if kind == "image" else say())
        self.calls = []

    def __call__(self, cfg, slot, kind, data, mime):
        self.calls.append((slot["id"], kind, mime))
        return self.script(slot, kind, len(self.calls))

    def slots(self):
        return [c[0] for c in self.calls]


def two_models(**extra):
    """Two models: the first reading is cross-checked by the second."""
    return reader_cfg(GEMINI_MODELS="m1:15:500,m2:15:500", **extra)


def reader_cfg(**extra):
    env = {"GEMINI_API_KEY": "key-one", "GEMINI_MODELS": "m1:15:500"}      # one model: a clean reading is not cross-checked, one call
    env.update(extra)
    return make_cfg(**env)


class ReaderCase(unittest.TestCase):
    def setUp(self):
        reset_db()
        aipool._cache.clear()
        self.addCleanup(aipool._cache.clear)

    def use(self, provider):
        p = mock.patch.object(aipool, "call_provider", provider)
        p.start()
        self.addCleanup(p.stop)
        return provider


class Pool(ReaderCase):
    def test_success_is_reserved_in_the_database_and_complete(self):
        fake = self.use(Provider())
        r = gemini.read_photo(reader_cfg(), photo(), "image/jpeg")
        self.assertEqual(r, {"times": GOOD, "alt": [], "complete": True, "warnings": []})
        self.assertEqual(fake.slots(), ["gemini:m1#0"])
        self.assertEqual(db("SELECT slot, count(*) FROM ai_calls WHERE slot<>'reading' GROUP BY slot"), [("gemini:m1#0", 1)])

    def test_daily_reading_limit_is_kept_for_everybody_together(self):
        fake = self.use(Provider())
        cfg = reader_cfg(AI_DAILY_LIMIT="2")
        self.assertEqual(cfg.ai_daily_limit, 2)
        self.assertEqual(make_cfg().ai_daily_limit, 300)
        first, second, third = photo(), photo(), photo()
        for data in (first, second):
            gemini.read_photo(cfg, data, "image/jpeg")
        self.assertEqual(len(fake.calls), 2)
        gemini.read_photo(cfg, first, "image/jpeg")                             # the same file: from the cache, not counted
        with self.assertRaises(gemini.Unavailable) as cm:
            gemini.read_photo(cfg, third, "image/jpeg")                      # the third new reading of the day: refused before any request to Google
        self.assertTrue(0 < cm.exception.retry_after <= 86400)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(db("SELECT count(*) FROM ai_calls WHERE slot='reading'"), [(2,)])
        db("UPDATE ai_calls SET at = at - interval '2 days'")                   # a new day: reading is possible again
        gemini.read_photo(cfg, third, "image/jpeg")
        self.assertEqual(len(fake.calls), 3)

    def test_no_key_is_unavailable_and_calls_nothing(self):
        fake = self.use(Provider())
        cfg = make_cfg()
        self.assertFalse(gemini.available(cfg))
        with self.assertRaises(gemini.Unavailable):
            gemini.read_photo(cfg, photo(), "image/jpeg")
        self.assertEqual(fake.calls, [])
        self.assertTrue(gemini.available(reader_cfg()))

    def test_voice_has_heard_truncated_and_audio_mime_is_passed(self):
        fake = self.use(Provider(lambda s, k, n: say("x" * 500)))
        r = gemini.read_voice(reader_cfg(), b"audio" + photo(), "audio/m4a")
        self.assertEqual((len(r["heard"]), r["complete"]), (300, True))
        self.assertEqual(fake.calls, [("gemini:m1#0", "audio", "audio/m4a")])
        r = gemini.read_photo(reader_cfg(), photo(), "image/jpeg")
        self.assertNotIn("heard", r)                                          # photos carry no `heard`

    def test_a_refused_slot_rests_and_the_next_model_is_used_at_once(self):
        def script(slot, kind, n):
            if slot["model"] == "m1":
                raise aipool.Refused(60, "rate limit")
            return dict(GOOD)
        fake = self.use(Provider(script))
        cfg = two_models()
        self.assertTrue(gemini.read_photo(cfg, photo(), "image/jpeg")["complete"])
        self.assertEqual(fake.slots(), ["gemini:m1#0", "gemini:m2#0"])
        self.assertEqual([r[0] for r in db("SELECT slot FROM ai_cooldown")], ["gemini:m1#0"])
        fake.calls.clear()
        self.assertTrue(gemini.read_photo(cfg, photo(), "image/jpeg")["complete"])
        self.assertEqual(fake.slots(), ["gemini:m2#0"])                       # m1 is resting: not even tried
        db("UPDATE ai_cooldown SET until = now() - interval '1 second'")
        fake.calls.clear()
        gemini.read_photo(cfg, photo(), "image/jpeg")
        self.assertEqual(fake.slots()[0], "gemini:m1#0")                      # rest is over

    def test_all_slots_refusing_is_unavailable(self):
        def script(slot, kind, n):
            raise aipool.Refused(30, "down")
        fake = self.use(Provider(script))
        with self.assertRaises(gemini.Unavailable):
            gemini.read_photo(two_models(), photo(), "image/jpeg")
        self.assertEqual(fake.slots(), ["gemini:m1#0", "gemini:m2#0"])

    def test_several_keys_add_their_limits_up_and_the_second_opinion_comes_from_another_model(self):
        cfg = make_cfg(GEMINI_API_KEY="k1, k2", GEMINI_MODELS="m1:5:100,m2:5:100")
        self.assertEqual([s["id"] for s in aipool.slots(cfg, "image")], ["gemini:m1#0", "gemini:m1#1", "gemini:m2#0", "gemini:m2#1"])
        fake = self.use(Provider())
        for _ in range(4):
            gemini.read_photo(cfg, photo(), "image/jpeg")          # first reading on m1 (key 0), cross-check on ANOTHER model, never another key of m1
        self.assertEqual(fake.slots(), ["gemini:m1#0", "gemini:m2#0"] * 4)
        fake.calls.clear()
        gemini.read_photo(cfg, photo(), "image/jpeg")              # key 0 of both models is at its 80 %: the second keys take over
        self.assertEqual(fake.slots(), ["gemini:m1#1", "gemini:m2#1"])

    def test_another_key_of_the_same_model_is_used_only_when_no_other_model_exists(self):
        cfg = make_cfg(GEMINI_API_KEY="k1,k2", GEMINI_MODELS="m1:5:100")
        fake = self.use(Provider())
        gemini.read_photo(cfg, photo(), "image/jpeg")
        self.assertEqual(fake.slots(), ["gemini:m1#0", "gemini:m1#1"])
        # with a second model that is resting, the other key of m1 is NOT used for the cross-check... it is the only one left, so it is
        cfg2 = make_cfg(GEMINI_API_KEY="k1,k2", GEMINI_MODELS="m1:15:500,m2:15:500")
        db("INSERT INTO ai_cooldown(slot, until) VALUES ('gemini:m2#0', now() + interval '1 hour'), ('gemini:m2#1', now() + interval '1 hour')")
        fake.calls.clear()
        gemini.read_photo(cfg2, photo(), "image/jpeg")
        self.assertEqual(fake.slots(), ["gemini:m1#0", "gemini:m1#1"])

    def test_per_minute_limit_is_respected_at_80_percent(self):
        fake = self.use(Provider())
        cfg = make_cfg(GEMINI_API_KEY="k", GEMINI_MODELS="m1:5:100")
        for _ in range(4):                                                    # 80 % of 5 = 4
            self.assertTrue(gemini.read_photo(cfg, photo(), "image/jpeg")["complete"])
        with self.assertRaises(gemini.Unavailable) as e:
            gemini.read_photo(cfg, photo(), "image/jpeg")
        self.assertGreaterEqual(e.exception.retry_after, 1); self.assertLessEqual(e.exception.retry_after, 61)
        self.assertEqual(len(fake.calls), 4)                                  # the fifth never reached the provider
        self.assertEqual(db("SELECT count(*) FROM ai_calls WHERE slot<>'reading'")[0][0], 4)
        db("UPDATE ai_calls SET at = now() - interval '61 seconds'")          # the minute has passed
        self.assertTrue(gemini.read_photo(cfg, photo(), "image/jpeg")["complete"])

    def test_per_day_limit_is_respected(self):
        fake = self.use(Provider())
        cfg = make_cfg(GEMINI_API_KEY="k", GEMINI_MODELS="m1:100:5")
        for _ in range(4):
            gemini.read_photo(cfg, photo(), "image/jpeg")
        with self.assertRaises(gemini.Unavailable):
            gemini.read_photo(cfg, photo(), "image/jpeg")
        self.assertEqual(len(fake.calls), 4)

    def test_a_busy_model_hands_over_to_the_next_one(self):
        fake = self.use(Provider())
        cfg = make_cfg(GEMINI_API_KEY="k", GEMINI_MODELS="m1:5:100,m2:5:100")
        db("INSERT INTO ai_calls(slot) SELECT 'gemini:m1#0' FROM generate_series(1, 4)")        # m1 is at its 80 % for this minute
        r = gemini.read_photo(cfg, photo(), "image/jpeg")
        self.assertTrue(r["complete"])
        self.assertEqual(fake.slots(), ["gemini:m2#0"])                                         # handed over; no other slot is free to cross-check

    def test_spoken_hours_become_24h_times_by_the_prayers_own_time_of_day(self):
        f = aipool.from_spoken
        got = aipool.clean(f(say()))
        self.assertEqual(got, (GOOD, []))
        # hour only -> minute 0; 12-hour numbers land in the right half of the day
        t, _ = aipool.clean(f({"fajr": {"hour": 4}, "dhuhr": {"hour": 1}, "asr": {"hour": 6}, "maghrib": {"hour": 7}, "isha": {"hour": 9, "minute": 30}}))
        self.assertEqual(t, {"fajr": "04:00", "dhuhr": "13:00", "asr": "18:00", "maghrib": "19:00", "isha": "21:30"})
        t, w = aipool.clean(f({"fajr": {"hour": 10, "minute": 0}}))              # no way to read 10 as a fajr time
        self.assertEqual(t, {})
        t, w = aipool.clean(f({"fajr": {"hour": 5, "minute": 75}, "asr": {"hour": 4}, "isha": {"hour": 30}}))
        self.assertEqual(sorted(t), ["asr"])
        t, w = aipool.clean(f(dict(say(), unclear=["isha"])))
        self.assertNotIn("isha", t)
        self.assertEqual(aipool.clean(f(None)), ({}, []))
        self.assertEqual(f({"fajr": "05:10"})["unclear"], [])                    # a flat answer for audio is not a spoken one: nothing read

    def test_the_same_file_is_answered_from_the_cache_without_a_provider_call(self):
        fake = self.use(Provider())
        cfg = reader_cfg()
        data = photo()
        a = gemini.read_photo(cfg, data, "image/jpeg")
        b = gemini.read_photo(cfg, data, "image/jpeg")
        self.assertEqual(a, b); self.assertEqual(len(fake.calls), 1)
        self.assertEqual(db("SELECT count(*) FROM ai_calls WHERE slot<>'reading'")[0][0], 1)
        gemini.read_photo(cfg, data + b"x", "image/jpeg")                    # other content: asked again
        self.assertEqual(len(fake.calls), 2)
        gemini.read_voice(cfg, data, "audio/ogg")                            # same bytes as audio is another question
        self.assertEqual(len(fake.calls), 3)
        # a doubtful answer is not remembered
        fake.script = lambda s, k, n: {"fajr": "05:10"}
        d2 = photo()
        gemini.read_photo(cfg, d2, "image/jpeg")
        before = len(fake.calls)
        gemini.read_photo(cfg, d2, "image/jpeg")
        self.assertGreater(len(fake.calls), before)

    def test_plausibility_windows_and_order(self):
        clean = aipool.clean
        self.assertEqual(clean(GOOD), (GOOD, []))
        self.assertEqual(clean({"fajr": "5.20", "dhuhr": " 12:30 "})[0], {"fajr": "05:20", "dhuhr": "12:30"})
        edge = {"fajr": "02:30", "dhuhr": "11:00", "asr": "14:00", "maghrib": "16:30", "isha": "23:59"}
        self.assertEqual(clean(edge), (edge, []))                              # window edges are inside
        edge2 = {"fajr": "07:30", "dhuhr": "14:30", "asr": "18:45", "maghrib": "21:30", "isha": "18:00"}
        self.assertEqual(clean({k: v for k, v in edge2.items() if k != "isha"})[1], [])
        for k, v in (("fajr", "02:29"), ("fajr", "07:31"), ("fajr", "12:00"), ("dhuhr", "10:59"), ("dhuhr", "14:31"), ("asr", "05:00"),
                     ("asr", "18:46"), ("maghrib", "16:29"), ("maghrib", "21:31"), ("isha", "17:59"), ("isha", "03:00")):
            times, warnings = clean({k: v})
            self.assertEqual((times, len(warnings)), ({}, 1), (k, v))
        times, warnings = clean(dict(GOOD, fajr="12:00"))
        self.assertEqual((sorted(times), len(warnings)), (["asr", "dhuhr", "isha", "maghrib"], 1))
        for bad in ("25:00", "12:60", "noon", "1230", "12:3", "-"):
            self.assertEqual(clean({"dhuhr": bad}), ({}, ["dhuhr was not a time"]), bad)
        times, warnings = clean(dict(GOOD, maghrib="20:00", isha="18:30"))    # each plausible, but not in the order of the day
        self.assertEqual(times, {}); self.assertTrue(any("order" in w for w in warnings))
        times, warnings = clean(dict(GOOD, asr="16:00", unclear=["Asr", 5, None]))
        self.assertNotIn("asr", times); self.assertEqual(len(times), 4); self.assertTrue(any("asr is not clear" in w for w in warnings))
        for junk in (None, [], "x", 5):
            self.assertEqual(clean(junk), ({}, []))
        self.assertEqual(clean({"fajr": None, "dhuhr": "", "asr": 0}), ({}, ["asr was not a time"]))

    def test_implausible_reading_is_not_complete(self):
        self.use(Provider(lambda s, k, n: dict(GOOD, fajr="12:00")))
        r = gemini.read_photo(make_cfg(GEMINI_API_KEY="k", GEMINI_MODELS="m1:15:500"), photo(), "image/jpeg")   # one slot: no second opinion
        self.assertEqual((r["complete"], "fajr" in r["times"], len(r["times"])), (False, False, 4))
        self.assertTrue(r["warnings"])


class SecondOpinion(ReaderCase):
    def cfg(self):
        return two_models()

    def test_a_clean_first_reading_is_cross_checked_when_another_slot_is_free(self):
        fake = self.use(Provider())
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual(fake.slots(), ["gemini:m1#0", "gemini:m2#0"])        # two calls, on two different slots
        self.assertEqual((r["times"], r["alt"], r["complete"], r["warnings"]), (GOOD, [], True, []))       # they agree

    def test_no_free_slot_for_a_second_opinion_the_clean_first_reading_stands(self):
        fake = self.use(Provider())
        r = gemini.read_photo(reader_cfg(), photo(), "image/jpeg")           # a single model
        self.assertEqual((len(fake.calls), r["complete"]), (1, True))
        db("INSERT INTO ai_calls(slot) SELECT 'gemini:m2#0' FROM generate_series(1, 12)")     # m2 is at its limit right now
        fake.calls.clear()
        t0 = __import__("time").monotonic()
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual((fake.slots(), r["times"], r["complete"]), (["gemini:m1#0"], GOOD, True))
        self.assertLess(__import__("time").monotonic() - t0, 3)               # a clean reading never waits for a second opinion
        db("DELETE FROM ai_calls"); db("INSERT INTO ai_cooldown(slot, until) VALUES ('gemini:m2#0', now() + interval '1 hour')")
        fake.calls.clear()
        self.assertTrue(gemini.read_photo(self.cfg(), photo(), "image/jpeg")["complete"])
        self.assertEqual(fake.slots(), ["gemini:m1#0"])                       # a resting slot is not asked either

    def test_second_slot_refusing_after_a_clean_reading_leaves_the_first(self):
        def script(slot, kind, n):
            if slot["model"] == "m2":
                raise aipool.Refused(60, "limit")
            return dict(GOOD)
        self.use(Provider(script))
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual((r["times"], r["complete"]), (GOOD, True))

    def test_nothing_sure_in_the_first_reading_and_a_clean_second_one_is_accepted(self):
        answers = {"m1": {"fajr": None, "dhuhr": None, "asr": None, "maghrib": None, "isha": None}, "m2": dict(GOOD)}
        fake = self.use(Provider(lambda s, k, n: answers[s["model"]]))
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual(fake.slots(), ["gemini:m1#0", "gemini:m2#0"])        # the second opinion comes from ANOTHER slot
        self.assertEqual((r["times"], r["complete"], r["alt"]), (GOOD, False, []))        # only one model could read it: the user checks
        self.assertTrue(any("only one model" in w for w in r["warnings"]))

    def test_first_misses_one_time_second_has_all_five_marked_not_complete(self):
        answers = {"m1": dict(GOOD, isha=None, unclear=["isha"]), "m2": dict(GOOD)}
        fake = self.use(Provider(lambda s, k, n: answers[s["model"]]))
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual((r["times"], r["complete"]), (GOOD, False))          # five times, but one model alone saw one of them: the user checks
        self.assertTrue(r["warnings"])

    def test_disagreement_keeps_only_what_both_models_agree_on(self):
        answers = {"m1": dict(GOOD, isha=None), "m2": dict(GOOD, fajr="05:40")}
        self.use(Provider(lambda s, k, n: answers[s["model"]]))
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual(r["times"], {k: v for k, v in GOOD.items() if k not in ("fajr", "isha")})
        self.assertFalse(r["complete"]); self.assertTrue(any("fajr" in w for w in r["warnings"]))

    def test_unclear_fields_with_no_second_opinion_available(self):
        fake = self.use(Provider(lambda s, k, n: dict(GOOD, asr="16:00", unclear=["asr"])))
        r = gemini.read_photo(make_cfg(GEMINI_API_KEY="k", GEMINI_MODELS="m1:15:500"), photo(), "image/jpeg")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(sorted(r["times"]), ["dhuhr", "fajr", "isha", "maghrib"]); self.assertFalse(r["complete"])

    def test_second_slot_refusing_leaves_the_first_reading(self):
        def script(slot, kind, n):
            if slot["model"] == "m2":
                raise aipool.Refused(60, "limit")
            return dict(GOOD, isha=None)
        self.use(Provider(script))
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual((len(r["times"]), r["complete"]), (4, False))

    def test_two_complete_readings_that_differ_give_alt(self):
        answers = {"m1": dict(GOOD), "m2": dict(GOOD, fajr="05:40")}
        fake = self.use(Provider(lambda s, k, n: answers[s["model"]]))
        r = gemini.read_photo(self.cfg(), photo(), "image/jpeg")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual((r["times"], r["alt"], r["complete"]), (GOOD, dict(GOOD, fajr="05:40"), False))
        self.assertTrue(r["warnings"])
        d = photo()                                                            # not cached: the user must choose, the next try asks again
        gemini.read_photo(self.cfg(), d, "image/jpeg")
        n = len(fake.calls)
        gemini.read_photo(self.cfg(), d, "image/jpeg")
        self.assertGreater(len(fake.calls), n)

    def test_agreeing_voice_readings_are_complete_and_keep_what_was_heard(self):
        fake = self.use(Provider())
        r = gemini.read_voice(self.cfg(), b"a" + photo(), "audio/ogg")
        self.assertEqual((fake.slots(), r["times"], r["complete"], r["heard"]), (["gemini:m1#0", "gemini:m2#0"], GOOD, True, "Bomdod besh o'n"))


class Provider_HTTP(ReaderCase):
    """What actually goes to the providers, with urlopen replaced (no network)."""

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def slot(self, provider="gemini", model="m1"):
        return {"id": "%s:%s#0" % (provider, model), "provider": provider, "model": model, "key": 0, "rpm": 15, "rpd": 500}

    def test_audio_and_images_share_the_same_slots(self):
        cfg = make_cfg(GEMINI_API_KEY="k1,k2", GEMINI_MODELS="m1:5:100,m2:5:100")
        self.assertEqual(aipool.slots(cfg, "audio"), aipool.slots(cfg, "image"))
        self.assertFalse(hasattr(cfg, "grok_key"))
        self.assertFalse(gemini.available(make_cfg()))

    def test_gemini_request_and_answer(self):
        seen = {}

        def fake(req, timeout=None):
            seen.update(url=req.full_url, key=req.get_header("X-goog-api-key"), body=json.loads(req.data))
            return self.Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}}]}).encode())
        cfg = make_cfg(GEMINI_API_KEY="the-key", GEMINI_MODELS="m1:15:500")
        with mock.patch("urllib.request.urlopen", fake):
            out = aipool.call_provider(cfg, self.slot(), "audio", b"abc", "audio/m4a")
        self.assertEqual(out, GOOD)
        self.assertEqual(seen["url"], "https://generativelanguage.googleapis.com/v1beta/models/m1:generateContent")
        self.assertEqual(seen["key"], "the-key")
        self.assertNotIn("the-key", json.dumps(seen["body"]))
        self.assertEqual(seen["body"]["contents"][0]["parts"][1]["inline_data"]["mime_type"], "audio/mp4")    # m4a is sent as audio/mp4
        self.assertEqual(seen["body"]["generationConfig"]["temperature"], 0)

    def test_blocked_or_empty_answers_read_as_nothing(self):
        cfg = make_cfg(GEMINI_API_KEY="k")
        for payload in ({}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}):
            with mock.patch("urllib.request.urlopen", lambda req, timeout=None, p=payload: self.Resp(json.dumps(p).encode())):
                self.assertEqual(aipool.call_provider(cfg, self.slot(), "image", b"x", "image/jpeg"), {})

    def test_http_errors_become_refusals_with_a_rest_time(self):
        def raising(code, body=b"", headers=None):
            def f(req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, code, "x", headers or {}, io.BytesIO(body))
            return f
        cfg = make_cfg(GEMINI_API_KEY="k")

        def refusal(fn):
            with mock.patch("urllib.request.urlopen", fn):
                with self.assertRaises(aipool.Refused) as e:
                    aipool.call_provider(cfg, self.slot(), "image", b"x", "image/jpeg")
            return e.exception
        e = refusal(raising(429, b'{"error":{"message":"quota exceeded per minute"}}', {"Retry-After": "42"}))
        self.assertEqual(e.cooldown_s, 42)
        self.assertEqual(refusal(raising(429, b'{"details":[{"retryDelay":"17s"}]}')).cooldown_s, 17)
        self.assertGreaterEqual(refusal(raising(429)).cooldown_s, 5)
        self.assertEqual(refusal(raising(429, b"GenerateRequestsPerDayPerProjectPerModel")).cooldown_s, 6 * 3600)
        for code in (401, 403, 404):
            self.assertEqual(refusal(raising(code)).cooldown_s, 600, code)
        for body in (b'{"error": {"message": "API key not valid. Please pass a valid API key."}}', b"models/zzz is not found", b"PERMISSION_DENIED",
                     b"model is not supported for generateContent", b"invalid model name"):
            self.assertEqual(refusal(raising(400, body)).cooldown_s, 600, body)           # a 400 about the key or the model rests the slot
        self.assertEqual(refusal(raising(500)).cooldown_s, 15)
        self.assertEqual(refusal(lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("down"))).cooldown_s, 15)
        self.assertEqual(refusal(lambda req, timeout=None: (_ for _ in ()).throw(TimeoutError())).cooldown_s, 15)

    def test_a_400_about_the_input_is_bad_input_not_a_broken_slot(self):
        def bad(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "x", {}, io.BytesIO(b'{"error": {"message": "Unable to process input image."}}'))
        cfg = make_cfg(GEMINI_API_KEY="k", GEMINI_MODELS="m1:15:500,m2:15:500")
        with mock.patch("urllib.request.urlopen", bad):
            with self.assertRaises(aipool.BadInput):
                aipool.call_provider(cfg, self.slot(), "image", b"x", "image/jpeg")
            with self.assertRaises(gemini.Unreadable) as e:
                gemini.read_photo(cfg, photo(), "image/jpeg")
            self.assertEqual(e.exception.code, "bad_image")
            with self.assertRaises(gemini.Unreadable) as e:
                gemini.read_voice(cfg, b"OggS" + photo(), "audio/ogg")
            self.assertEqual(e.exception.code, "bad_audio")
        self.assertEqual(db("SELECT count(*) FROM ai_cooldown"), [(0,)])                   # nothing rested
        self.assertEqual(db("SELECT slot FROM ai_calls WHERE slot<>'reading' ORDER BY id"), [("gemini:m1#0",)] * 2)   # the other model was never asked

    def test_http_failure_end_to_end_is_unavailable_and_rests_the_slot(self):
        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "x", {}, io.BytesIO(b""))
        cfg = make_cfg(GEMINI_API_KEY="k", GEMINI_MODELS="m1:15:500")
        with mock.patch("urllib.request.urlopen", boom):
            with self.assertRaises(gemini.Unavailable):
                gemini.read_photo(cfg, photo(), "image/jpeg")
        self.assertEqual([r[0] for r in db("SELECT slot FROM ai_cooldown")], ["gemini:m1#0"])


class Routes(ServerCase):
    """POST /timetable/photo and /timetable/voice."""

    def setUp(self):
        reset_db()
        aipool._cache.clear()
        self.addCleanup(aipool._cache.clear)

    def reader(self, script=None, **env):
        self.fake = Provider(script)
        p = mock.patch.object(aipool, "call_provider", self.fake)
        p.start()
        self.addCleanup(p.stop)
        env.setdefault("GEMINI_API_KEY", "key-one")
        env.setdefault("GEMINI_MODELS", "m1:15:500")
        return self.use(**env)

    def post(self, kind="photo", data=None, ctype=None, device="devicephoto01", addr=None, **kw):
        ctype = ctype or ("image/jpeg" if kind == "photo" else "audio/mp4")
        data = (photo() if kind == "photo" else b"OggS" + photo()) if data is None else data
        h = {"Content-Type": ctype, "X-Device-Id": device}
        r = self.req("POST", helpers.API + "/timetable/" + kind, data, h, addr)
        return r

    def test_photo_success_stores_nothing_and_config_announces_reading(self):
        self.reader()
        r = self.post()
        b = r.json
        self.assertEqual((r.status, b["success"], b["code"]), (200, True, "OK"))
        self.assertEqual(b["data"], {"times": GOOD, "alt": [], "complete": True, "warnings": []})
        self.assertEqual(db("SELECT (SELECT count(*) FROM mosques), (SELECT count(*) FROM mosque_history)"), [(0, 0)])
        c = self.call("GET", "/config")[1]["data"]
        self.assertEqual((c["photoReading"], c["voiceReading"]), (True, True))

    def test_images_may_be_larger_than_64_kib(self):
        self.reader()
        self.assertEqual(self.post(data=JPEG + b"0" * 200000).status, 200)
        self.assertEqual(self.post(data=b"\x89PNG\r\n\x1a\n" + b"1" * 50, ctype="image/png").status, 200)
        self.assertEqual(self.post(data=b"RIFF0000WEBP" + b"2" * 50, ctype="image/webp").status, 200)

    def test_voice_success_with_heard(self):
        self.reader(lambda s, k, n: say("Bomdod besh o'n"))
        r = self.post("voice", ctype="audio/m4a")
        self.assertEqual(r.status, 200)
        self.assertEqual((r.json["data"]["heard"], r.json["data"]["complete"], r.json["data"]["times"]), ("Bomdod besh o'n", True, GOOD))
        self.assertEqual(self.fake.calls[0][1:], ("audio", "audio/m4a"))

    def test_incomplete_and_out_of_order_readings_are_returned_not_complete(self):
        self.reader(lambda s, k, n: {"fajr": "05:10", "dhuhr": "12:30"}, GEMINI_MODELS="m1:15:500")
        d = self.post().json["data"]
        self.assertEqual((d["complete"], d["times"]), (False, {"fajr": "05:10", "dhuhr": "12:30"}))
        self.fake.script = lambda s, k, n: dict(GOOD, maghrib="20:00", isha="18:30")
        d = self.post().json["data"]
        self.assertEqual((d["complete"], d["times"]), (False, {})); self.assertTrue(d["warnings"])

    def test_no_key_is_503_reader_unavailable(self):
        self.use()
        r = self.post()
        self.assertEqual((r.status, r.json["code"], r.json["message"]["code"]), (503, "SERVICE_UNAVAILABLE", "error.readerUnavailable"))
        self.assertEqual(self.post("voice").json["message"]["code"], "error.readerUnavailable")
        c = self.call("GET", "/config")[1]["data"]
        self.assertEqual((c["photoReading"], c["voiceReading"]), (False, False))

    def test_providers_down_is_503(self):
        def down(slot, kind, n):
            raise aipool.Refused(30, "down")
        self.reader(down)
        r = self.post()
        self.assertEqual((r.status, r.json["message"]["code"], r.json["data"]), (503, "error.readerUnavailable", None))
        self.assertNotIn("key-one", r.text)

    def test_all_limits_used_up_is_503_with_retry_after(self):
        self.reader(GEMINI_MODELS="m1:5:100")
        codes = [self.post().status for _ in range(5)]
        self.assertEqual(codes[:4], [200] * 4)
        r = self.post()
        self.assertEqual((r.status, r.json["message"]["code"]), (503, "error.readerUnavailable"))
        self.assertRegex(r.header("Retry-After") or "", r"^\d+$")             # the client is told when to come back
        self.assertEqual(len(self.fake.calls), 4)

    def test_fallback_to_the_next_model_through_the_api(self):
        def script(slot, kind, n):
            if slot["model"] == "m1":
                raise aipool.Refused(60, "x")
            return dict(GOOD)
        self.reader(script, GEMINI_MODELS="m1:15:500,m2:15:500")
        self.assertEqual(self.post().status, 200)
        self.assertEqual(self.fake.slots(), ["gemini:m1#0", "gemini:m2#0"])

    def test_bad_image_content_and_empty_bodies(self):
        self.reader()
        r = self.post(data=b"%PDF-1.4 not an image")                          # the content type is not trusted
        self.assertEqual((r.status, r.json["code"], r.json["message"]["code"]), (400, "BAD_REQUEST", "error.badImage"))
        self.assertEqual(self.post(data=b"").json["message"]["code"], "error.badImage")
        self.assertEqual(self.post("voice", data=b"").json["message"]["code"], "error.badAudio")
        self.assertEqual(self.fake.calls, [])

    def test_voice_bodies_must_be_real_audio_by_their_first_bytes(self):
        self.reader()
        real = {
            "ogg": b"OggS" + b"\0" * 30,
            "wav": b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\0" * 20,
            "mp3 id3": b"ID3\x03" + b"\0" * 30,
            "mp3 frame": b"\xff\xfb\x90\x00" + b"\0" * 30,
            "m4a": b"\x00\x00\x00\x20ftypM4A " + b"\0" * 20,
            "mp4": b"\x00\x00\x00\x18ftypmp42" + b"\0" * 20,
            "webm": b"\x1a\x45\xdf\xa3" + b"\0" * 30,
            "aac": b"\xff\xf1\x50\x80" + b"\0" * 30,
        }
        for name, data in real.items():
            self.assertEqual(self.post("voice", data=data + name.encode()).status, 200, name)
        fake_audio = {"text": b"hello this is not audio", "pdf": b"%PDF-1.4 something", "jpeg": JPEG, "zeros": b"\0" * 100,
                      "riff not wave": b"RIFF\0\0\0\0AVI " + b"\0" * 20, "html": b"<html><script>alert(1)</script>"}
        for name, data in fake_audio.items():
            r = self.post("voice", data=data, ctype="audio/mp4")
            self.assertEqual((r.status, r.json["code"], r.json["message"]["code"]), (400, "BAD_REQUEST", "error.badAudio"), name)
        self.assertEqual(len(self.fake.calls), len(real))                      # only the real recordings reached the provider
        n = len(self.fake.calls)
        self.post("voice", data=b"not audio at all")
        self.assertEqual(len(self.fake.calls), n)                              # refused before any provider call

    def test_input_the_model_cannot_use_is_400_and_asks_nobody_else(self):
        def script(slot, kind, n):
            raise aipool.BadInput()
        self.reader(script, GEMINI_MODELS="m1:15:500,m2:15:500")
        r = self.post()
        self.assertEqual((r.status, r.json["code"], r.json["message"]["code"]), (400, "BAD_REQUEST", "error.badImage"))
        r = self.post("voice")
        self.assertEqual((r.status, r.json["message"]["code"]), (400, "error.badAudio"))
        self.assertEqual(self.fake.slots(), ["gemini:m1#0", "gemini:m1#0"])
        self.assertEqual(db("SELECT count(*) FROM ai_cooldown"), [(0,)])

    def test_bad_input_on_the_second_opinion_leaves_the_first_reading(self):
        def script(slot, kind, n):
            if n == 2:
                raise aipool.BadInput()
            return dict(GOOD)
        self.reader(script, GEMINI_MODELS="m1:15:500,m2:15:500")
        d = self.post().json["data"]
        self.assertEqual((d["times"], d["complete"]), (GOOD, True))

    def test_content_type_and_size_limits(self):
        self.reader(MAX_PHOTO_BYTES="2048", MAX_VOICE_BYTES="2048")
        for kind, bad in (("photo", "application/pdf"), ("photo", "application/json"), ("photo", "audio/mp4"), ("voice", "image/jpeg"), ("voice", "text/plain")):
            r = self.post(kind, ctype=bad)
            self.assertEqual((r.status, r.json["code"]), (415, "UNSUPPORTED_MEDIA_TYPE"), (kind, bad))
        r = self.post(data=JPEG + b"0" * 4000)
        self.assertEqual((r.status, r.json["code"], r.json["message"]["code"]), (413, "PAYLOAD_TOO_LARGE", "error.tooLarge"))
        self.assertEqual(self.post("voice", data=b"x" * 4000).status, 413)
        self.assertEqual(self.post(data=JPEG + b"0" * 1000).status, 200)
        self.assertEqual(self.fake.calls[0][0], "gemini:m1#0")
        self.assertEqual(len(self.fake.calls), 1)

    def test_default_limits_are_5_mib_and_2_mib(self):
        cfg = make_cfg()
        self.assertEqual((cfg.max_photo_bytes, cfg.max_voice_bytes), (5 * 1024 * 1024, 2 * 1024 * 1024))
        self.reader()
        r = helpers.headers_only(self.addr, "POST", helpers.API + "/timetable/voice", 2 * 1024 * 1024 + 1, {"Content-Type": "audio/mp4"})
        self.assertEqual(r.status, 413)                                       # decided from Content-Length, the body is never read
        r = helpers.headers_only(self.addr, "POST", helpers.API + "/timetable/photo", 5 * 1024 * 1024 + 1, {"Content-Type": "image/jpeg"})
        self.assertEqual(r.status, 413)

    def test_per_device_rate_limit_30_an_hour(self):
        self.reader()
        data = photo()                                                         # the same photo: cached after the first, cheap
        codes = [self.post(data=data).status for _ in range(31)]
        self.assertEqual(codes, [200] * 30 + [429])
        r = self.post(data=data)
        self.assertEqual((r.status, r.json["code"], r.json["message"]["code"]), (429, "RATE_LIMITED", "error.rateLimited"))
        self.assertRegex(r.header("Retry-After"), r"^\d+$")
        self.assertEqual(self.post(data=data, device="devicephoto02").status, 200)       # another device is not affected

    def test_per_address_rate_limit_120_an_hour(self):
        self.reader()
        data = photo()
        codes = [self.post(data=data, device="device%05d" % i).status for i in range(121)]
        self.assertEqual(codes, [200] * 120 + [429])

    def test_busy_reader_slots_are_503_not_queued(self):
        srv = self.reader()
        held = [srv.state.read_slots.acquire(blocking=False) for _ in range(4)]
        self.assertTrue(all(held))
        r = self.post()
        self.assertEqual((r.status, r.json["message"]["code"]), (503, "error.readerUnavailable"))
        for _ in held:
            srv.state.read_slots.release()
        self.assertEqual(self.post().status, 200)


if __name__ == "__main__":
    unittest.main()
