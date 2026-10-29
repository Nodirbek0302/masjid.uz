"""The Telegram bot conversation logic with a fake Telegram API and a fake timetable reader (no network, no Gemini)."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402,F401  (puts server/ on sys.path)
from helpers import make_cfg, reset_db, db as raw_db  # noqa: E402
import app  # noqa: E402
import gemini  # noqa: E402
import bot as _botmod  # noqa: E402

TEXTS_UZ = _botmod.TEXTS["uz"]

FIVE = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}


def result(times=None, alt=None, complete=True, heard=None):
    r = {"times": dict(FIVE if times is None else times), "alt": alt or [], "complete": complete, "warnings": []}
    if heard is not None:
        r["heard"] = heard
    return r


class BotFlow(unittest.TestCase):
    """The conversation logic with fake Telegram/Gemini: location -> pick -> photo -> confirm -> saved, and the safety rules."""

    def setUp(self):
        import bot as botmod
        self.botmod = botmod
        reset_db()
        self.store = app.Store(make_cfg())
        self.addCleanup(self.store.close)
        self.sent = []
        self.read = result()                                  # what the (fake) Gemini photo reader answers
        self.bot = botmod.Bot(lambda m, **kw: self.sent.append((m, kw)), self.store, lambda data: self.read, lambda fid: b"img",
                              "https://example.org", admin_ids=[7])
        self.user = {"id": 555, "first_name": "Ali", "username": "ali_u", "language_code": "uz"}
        self.m = self.store.create({"lat": 40.0, "lon": 66.0, "name": "Bot mosque"}, "client:test0000", {"kind": "web", "platform": "web"})

    def msgs(self):
        return [kw["text"] for m, kw in self.sent if m == "sendMessage"]

    def buttons(self):
        for m, kw in reversed(self.sent):
            if m == "sendMessage" and "inline_keyboard" in kw.get("reply_markup", {}):
                return [b for row in kw["reply_markup"]["inline_keyboard"] for b in row]
        return []

    autolang = True        # most tests are not about the language question: give the user a language first (like Telegram's own, as before)

    def ensure_lang(self):
        if self.autolang:                                      # the language follows the test user's language_code, as these tests assume
            code = (self.user.get("language_code") or "").lower()
            want = "ru" if code.startswith("ru") else "en" if code.startswith("en") else "uz"
            if self.bot.db.lang_of(self.user["id"]) != want:
                self.bot.db.set_lang(self.user["id"], want)

    def send(self, **kw):
        self.ensure_lang()
        base = {"chat": {"id": 555, "type": "private"}, "from": self.user}
        self.bot.handle({"update_id": 1, "message": dict(base, **kw)})

    def click(self, data):
        self.ensure_lang()
        self.bot.handle({"update_id": 2, "callback_query": {"id": "q", "from": self.user, "data": data, "message": {"chat": {"id": 555}}}})

    def calls(self, method):
        return [kw for m, kw in self.sent if m == method]

    def test_language_is_asked_first_then_everything_follows_it(self):
        self.autolang = False
        self.user = {"id": 900, "first_name": "Sam", "language_code": "en"}                      # English phone, but wants the bot in Uzbek
        self.send(text="/start")
        self.assertEqual(len(self.msgs()), 1)                                                   # only the question, no hello yet
        langs = [b["callback_data"] for b in self.buttons()]
        self.assertEqual(langs, ["lang:uz", "lang:uz-cyrl", "lang:ru", "lang:en"])
        self.assertIsNone(self.bot.db.lang_of(900))
        self.click("lang:uz-cyrl")
        self.assertEqual(self.bot.db.lang_of(900), "uz-cyrl")
        sent = "\n".join(self.msgs())
        self.assertIn("Ассалому алайкум", sent)                                                 # hello in Cyrillic Uzbek, not in English
        self.assertTrue(any("lang=uz-cyrl" in b.get("web_app", {}).get("url", "") for b in self.buttons()))     # Mini App opens in it too
        cmds = self.calls("setMyCommands")[-1]
        self.assertEqual(cmds["scope"], {"type": "chat", "chat_id": 555}); self.assertIn("Бошлаш", [c["description"] for c in cmds["commands"]])
        menu = self.calls("setChatMenuButton")[-1]
        self.assertEqual(menu["chat_id"], 555); self.assertIn("lang=uz-cyrl", menu["menu_button"]["web_app"]["url"])
        self.send(text="/id"); self.assertIn("Telegram ID", self.msgs()[-1]); self.assertIn("Сизнинг", self.msgs()[-1])
        self.send(text="/lang"); self.assertEqual([b["callback_data"] for b in self.buttons()][0], "lang:uz")      # can be changed any time
        self.click("lang:ru")
        self.assertIn("Ассаламу алайкум", "\n".join(self.msgs())); self.assertTrue(any("lang=ru" in b.get("web_app", {}).get("url", "") for b in self.buttons()))
        self.send(text="/id"); self.assertIn("Ваш Telegram ID", self.msgs()[-1])
        self.user = {"id": 7901, "first_name": "Other", "language_code": "uz"}; self.sent.clear()          # another person is asked on their own
        self.send(text="/id"); self.assertEqual([b["callback_data"] for b in self.buttons()][:1], ["lang:uz"])

    def test_the_first_message_is_continued_after_the_language_is_chosen(self):
        self.autolang = False
        self.user = {"id": 7902, "first_name": "Dee", "language_code": "ru"}
        self.send(text="/start m_" + self.m["id"])                                              # opened from a mosque page: deep link first
        self.assertEqual(len(self.msgs()), 1)
        self.click("lang:en")
        self.assertIn("Bot mosque", self.msgs()[-1]); self.assertIn("Send a photo", self.msgs()[-1])      # carried on, in English
        self.autolang = False
        self.send(location={"latitude": 40.0004, "longitude": 66.0})
        self.assertIn("Nearest mosque", "\n".join(self.msgs()))

    def test_cyrillic_bot_texts_keep_placeholders_commands_and_brands(self):
        import re
        uz, cy = self.botmod.TEXTS["uz"], self.botmod.TEXTS["uz-cyrl"]
        ph = lambda v: sorted(re.findall(r"\{\w+\}", v if isinstance(v, str) else " ".join(v)))   # noqa: E731
        for k, v in uz.items():
            if isinstance(v, (str, list)):
                self.assertEqual(ph(cy[k]), ph(v), k)
        self.assertIn("/subs", cy["sub_limit"]); self.assertIn("/skip", cy["ask_name"]); self.assertIn("05:20 12:30 16:00 17:35 19:00", cy["send_photo"])
        self.assertEqual(cy["names"][0], "Бомдод"); self.assertEqual(len(self.botmod.bottexts.REGION_NAMES["uz-cyrl"]), 14)
        self.assertEqual(self.botmod.bottexts.REGION_NAMES["uz-cyrl"][0], "Тошкент шаҳри")

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
        self.assertEqual(link, "https://example.org/?m=" + self.m["id"] + "&lang=uz")
        self.assertEqual(raw_db("SELECT tg_username, platform FROM actors WHERE actor_id='tg:555'"), [("ali_u", "telegram-bot")])        # who/what recorded
        self.assertEqual(raw_db("SELECT actor FROM mosque_history WHERE mosque_id=%s AND action='edit'", self.m["id"])[0][0], "tg:555")

    def test_without_a_web_address_the_bot_says_nothing_about_https(self):
        self.bot.webapp_url = ""                                                                # TIMUS v1: API + bot only, WEBAPP_URL empty
        for code in ("uz", "uz-cyrl", "ru", "en"):
            self.user = {"id": 701, "first_name": "A", "language_code": code}; self.sent.clear()
            self.bot.db.set_lang(701, code)
            self.send(text="/start")
            text = "\n".join(self.msgs())
            self.assertNotIn("HTTPS", text, code); self.assertNotIn("Mini App", text, code); self.assertNotIn("WEBAPP_URL", text, code)
            self.assertFalse([b for b in self.buttons() if "web_app" in b], code)                  # no button that cannot work
        self.user = {"id": 7, "first_name": "Mod", "language_code": "uz"}; self.sent.clear()
        self.bot.db.set_lang(7, "uz")
        self.send(text="/admin")
        self.assertIn("Moderator paneli hozircha ulanmagan", self.msgs()[-1])                   # only a moderator hears why
        self.user = {"id": 702, "first_name": "B", "language_code": "uz"}; self.sent.clear()
        self.bot.db.set_lang(702, "uz")
        self.send(text="/admin")
        self.assertEqual(self.msgs(), [])

    def tap(self, data, message_id=42, markup=None, photo=False):
        """A button press on a real message (with its id), as Telegram sends it."""
        self.ensure_lang()
        msg = {"chat": {"id": 555}, "message_id": message_id}
        if markup:
            msg["reply_markup"] = {"inline_keyboard": markup}
        if photo:
            msg["photo"] = [{"file_id": "p"}]
        self.sent.clear()
        self.bot.handle({"update_id": 2, "callback_query": {"id": "q%d" % message_id, "from": self.user, "data": data, "message": msg}})

    def test_a_button_press_updates_its_message_instead_of_sending_a_new_one(self):
        self.send(location={"latitude": 40.0004, "longitude": 66.0})
        self.tap("pick:" + self.m["id"])
        self.assertEqual(self.calls("sendMessage"), [])                                          # nothing new in the chat
        e = self.calls("editMessageText")
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["chat_id"], e[0]["message_id"]), (555, 42))
        self.assertIn("Bot mosque", e[0]["text"])
        self.assertEqual(e[0]["reply_markup"], {"inline_keyboard": []})                           # the old buttons are gone
        self.assertEqual(len(self.calls("answerCallbackQuery")), 1)                              # the press is answered exactly once
        # the reminders menu: every toggle redraws the same message
        self.bot.db.set_user(555, region=0)
        self.tap("rm:daily", message_id=50)
        self.assertEqual(self.calls("sendMessage"), [])
        self.assertEqual(self.calls("editMessageText")[0]["message_id"], 50)
        self.assertIn("rm:daily", [b["callback_data"] for row in self.calls("editMessageText")[0]["reply_markup"]["inline_keyboard"] for b in row])

    def test_subscribing_on_a_card_flips_the_button_and_keeps_the_card(self):
        card = self.bot.card_buttons(TEXTS_UZ, self.store.get(self.m["id"]), 555)
        self.tap("sub:" + self.m["id"], message_id=60, markup=card, photo=True)
        self.assertEqual((self.calls("sendMessage"), self.calls("editMessageText"), self.calls("editMessageCaption")), ([], [], []))
        rm = self.calls("editMessageReplyMarkup")
        self.assertEqual(rm[0]["message_id"], 60)
        self.assertIn("unsub:" + self.m["id"], [b.get("callback_data") for row in rm[0]["reply_markup"]["inline_keyboard"] for b in row])
        a = self.calls("answerCallbackQuery")
        self.assertEqual(len(a), 1); self.assertIn("obuna", a[0]["text"])                          # said as a short notice
        self.assertTrue(self.bot.db.is_subscribed(555, self.m["id"]))
        self.tap("unsub:" + self.m["id"], message_id=60, markup=self.bot.card_buttons(TEXTS_UZ, self.store.get(self.m["id"]), 555))
        self.assertFalse(self.bot.db.is_subscribed(555, self.m["id"]))
        self.assertIn("sub:" + self.m["id"], [b.get("callback_data") for row in self.calls("editMessageReplyMarkup")[0]["reply_markup"]["inline_keyboard"] for b in row])

    def test_the_subs_list_is_redrawn_without_the_one_removed(self):
        other = self.store.create({"lat": 41.0, "lon": 69.0, "name": "Ikkinchi"}, "client:test0000", {"kind": "web", "platform": "web"})
        for mid in (self.m["id"], other["id"]):
            self.bot.db.subscribe(555, mid)
        listing = [[{"text": "x", "callback_data": "unsub:" + self.m["id"]}], [{"text": "y", "callback_data": "unsub:" + other["id"]}]]
        self.tap("unsub:" + self.m["id"], message_id=70, markup=listing)
        e = self.calls("editMessageText")
        self.assertEqual([b["callback_data"] for row in e[0]["reply_markup"]["inline_keyboard"] for b in row], ["unsub:" + other["id"]])
        self.assertEqual(self.calls("sendMessage"), [])

    def test_when_telegram_cannot_edit_a_new_message_is_sent(self):
        import bot as botmod

        def api(m, **kw):
            self.sent.append((m, kw))
            if m == "editMessageText":
                raise botmod.TelegramError(m, 400, "Bad Request: message to edit not found")
        self.bot.api = api
        self.tap("cancel")
        self.assertEqual(len(self.calls("sendMessage")), 1)

        def same(m, **kw):
            self.sent.append((m, kw))
            if m == "editMessageText":
                raise botmod.TelegramError(m, 400, "Bad Request: message is not modified")
        self.bot.api = same
        self.tap("cancel")
        self.assertEqual(self.calls("sendMessage"), [])                                          # the same text again is fine

    def test_a_failed_press_is_a_notice_and_the_message_stays(self):
        self.tap("pick:00000000-0000-0000-0000-000000000000")
        self.assertEqual((self.calls("sendMessage"), self.calls("editMessageText")), ([], []))
        a = self.calls("answerCallbackQuery")
        self.assertEqual(len(a), 1); self.assertTrue(a[0]["show_alert"])

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

    def test_a_photo_gets_reactions_and_a_draft_instead_of_a_reading_message(self):
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        self.sent.clear()
        self.send(photo=[{"file_id": "a"}], message_id=77)
        reacts = [kw["reaction"][0]["emoji"] for kw in self.calls("setMessageReaction") if kw["message_id"] == 77]
        self.assertEqual(reacts, ["👀", "👌"])                                                  # seen, then read
        self.assertTrue(self.calls("sendChatAction"))
        self.assertTrue(all(kw["action"] == "typing" for kw in self.calls("sendChatAction")))
        d = self.calls("sendMessageDraft")
        self.assertTrue(d and d[0]["draft_id"] > 0 and d[0]["chat_id"] == 555)
        self.assertEqual(len(self.msgs()), 1)                                                   # only the preview stays in the chat
        self.assertIn("05:20", self.msgs()[0])
        styles = {b["callback_data"]: b.get("style") for b in self.buttons()}
        self.assertEqual((styles["save"], styles["cancel"]), ("success", "danger"))
        # an unclear reading: 🤔 instead of 👌
        self.read = result(times={"fajr": "05:20"}, complete=False); self.sent.clear()
        self.send(photo=[{"file_id": "b"}], message_id=78)
        self.assertEqual([kw["reaction"][0]["emoji"] for kw in self.calls("setMessageReaction")], ["👀", "🤔"])

    def test_telegram_refusing_reactions_or_drafts_changes_nothing(self):
        import bot as botmod

        def api(m, **kw):
            self.sent.append((m, kw))
            if m in ("setMessageReaction", "sendMessageDraft", "sendChatAction"):
                raise botmod.TelegramError(m, 400, "Bad Request: method not available")
        self.bot.api = api
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        self.send(photo=[{"file_id": "a"}], message_id=79)
        self.assertIn("05:20", self.msgs()[-1])

    def test_card_has_copy_and_telegram_map_buttons(self):
        self.store.edit(self.m["id"], {"prayer_times": FIVE}, "client:test0000", {"kind": "web", "platform": "web"})
        m = self.store.get(self.m["id"])
        flat = [b for row in self.bot.card_buttons(TEXTS_UZ, m, 555) for b in row]
        copy = [b for b in flat if "copy_text" in b][0]
        self.assertIn("Bot mosque", copy["copy_text"]["text"]); self.assertIn("05:20", copy["copy_text"]["text"])
        self.assertLessEqual(len(copy["copy_text"]["text"]), 256)
        self.assertIn("loc:" + m["id"], [b.get("callback_data") for b in flat])
        self.assertEqual([b.get("style") for b in flat if b.get("callback_data") == "pick:" + m["id"]], ["primary"])
        bare = self.store.create({"lat": 41.5, "lon": 69.5, "name": "Bo'sh"}, "client:test0000", {"kind": "web", "platform": "web"})
        self.assertFalse([b for row in self.bot.card_buttons(TEXTS_UZ, bare, 555) for b in row if "copy_text" in b])   # nothing to copy yet
        self.tap("loc:" + m["id"], message_id=90, markup=self.bot.card_buttons(TEXTS_UZ, m, 555), photo=True)
        v = self.calls("sendVenue")
        self.assertEqual((v[0]["latitude"], v[0]["longitude"], v[0]["title"]), (40.0, 66.0, "Bot mosque"))
        self.assertEqual((self.calls("editMessageText"), self.calls("editMessageCaption")), ([], []))   # the card stays

    def test_the_menu_button_opens_the_page_with_its_trailing_slash(self):
        for base in ("https://api.example/masjiduz/web", "https://api.example/masjiduz/web/", "https://api.example/masjiduz/web//"):
            self.assertEqual(self.botmod.web_root(base), "https://api.example/masjiduz/web/")
        self.assertEqual(self.botmod.web_url("https://api.example/masjiduz/web", "ru"), "https://api.example/masjiduz/web/?lang=ru")

    def test_moderator_button_updates_the_mosques(self):
        import threading as th
        gate, calls = th.Event(), []

        def osm():
            calls.append(1)
            gate.wait(5)
            return {"added": 3, "updated": 1, "unchanged": 2200, "protected_edited": 4, "near_existing": 70, "photos": 1}
        self.bot.osm_import = osm
        self.user = {"id": 7, "first_name": "Mod", "language_code": "uz"}; self.sent.clear()
        self.send(text="/admin")
        self.assertIn("osm:sync", [b.get("callback_data") for b in self.buttons()])
        self.tap("osm:sync", message_id=11)
        self.assertIn("1–8 daqiqa", self.calls("editMessageText")[0]["text"])                 # the /admin message says it started
        self.tap("osm:sync", message_id=11)                                                    # a second press while it runs
        a = self.calls("answerCallbackQuery")
        self.assertTrue(a[0]["show_alert"]); self.assertIn("allaqachon", a[0]["text"])
        gate.set()
        for _ in range(50):
            if any("yangilandi" in t for t in self.msgs()):
                break
            __import__("time").sleep(0.05)
        self.assertIn("3 ta qoʻshildi", self.msgs()[-1]); self.assertEqual(len(calls), 1)
        # it fails: said so, and the button works again
        self.bot.osm_import = lambda: (_ for _ in ()).throw(TimeoutError())
        self.tap("osm:sync", message_id=12)
        for _ in range(50):
            if any("Yangilab boʻlmadi" in t for t in self.msgs()):
                break
            __import__("time").sleep(0.05)
        self.assertIn("TimeoutError", self.msgs()[-1])

    def test_update_command_for_moderators(self):
        import urllib.error
        done = []
        self.bot.osm_import = lambda: done.append(1) or {"added": 5}
        self.user = {"id": 556, "first_name": "Eve", "language_code": "uz"}; self.sent.clear()
        self.send(text="/update")                                                              # not a moderator: no answer, nothing runs
        __import__("time").sleep(0.2)
        self.assertEqual((self.msgs(), done), ([], []))
        self.user = {"id": 7, "first_name": "Mod", "language_code": "uz"}; self.sent.clear()
        self.send(text="/update")
        for _ in range(50):
            if any("yangilandi" in t for t in self.msgs()):
                break
            __import__("time").sleep(0.05)
        self.assertIn("1–8 daqiqa", self.msgs()[0]); self.assertIn("5 ta qoʻshildi", self.msgs()[-1]); self.assertEqual(done, [1])
        # the deployment does not let the bot out to Overpass: said plainly
        self.bot.osm_import = lambda: (_ for _ in ()).throw(urllib.error.URLError("no route"))
        self.sent.clear(); self.send(text="/update")
        for _ in range(50):
            if any("OpenStreetMapʼga ulana olmadi" in t for t in self.msgs()):
                break
            __import__("time").sleep(0.05)
        self.assertIn("overpass-api.de", self.msgs()[-1])
        # the moderator's command menu lists it
        self.bot.osm_import = lambda: {}
        self.bot.apply_language(555, self.user, "uz")
        cmds = [c["command"] for c in self.calls("setMyCommands")[-1]["commands"]]
        self.assertIn("update", cmds)

    def test_only_moderators_see_and_use_the_update_button(self):
        ran = []
        self.bot.osm_import = lambda: ran.append(1) or {}
        self.user = {"id": 556, "first_name": "Eve", "language_code": "uz"}; self.sent.clear()
        self.send(text="/admin")
        self.assertEqual(self.msgs(), [])
        self.tap("osm:sync", message_id=13)                                                    # a forged press
        __import__("time").sleep(0.2)
        self.assertEqual(ran, [])
        self.bot.osm_import = None                                                             # switched off (the default)
        self.user = {"id": 7, "first_name": "Mod", "language_code": "uz"}; self.sent.clear()
        self.send(text="/admin")
        self.assertNotIn("osm:sync", [b.get("callback_data") for b in self.buttons()])
        self.assertEqual((make_cfg().bot_osm_import, make_cfg(BOT_OSM_IMPORT="0").bot_osm_import), (True, False))   # on unless switched off
        self.send(text="/update")
        self.assertIn("BOT_OSM_IMPORT=0", self.msgs()[-1])

    def test_descriptions_fit_telegram_limits(self):
        import bottexts
        self.assertEqual(set(bottexts.DESCRIPTIONS), {"uz", "ru", "en"})
        for lc, (desc, short) in bottexts.DESCRIPTIONS.items():
            self.assertLessEqual(len(desc), 512, lc); self.assertLessEqual(len(short), 120, lc)
        for lang in ("uz", "uz-cyrl", "ru", "en"):
            self.assertTrue(self.botmod.TEXTS[lang]["copy_btn"] and self.botmod.TEXTS[lang]["venue_btn"])

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
        bot.db.set_lang(7, "uz")
        bot.handle({"update_id": 9, "message": {"chat": {"id": 7, "type": "private"}, "from": self.user, "text": "/admin"}})
        self.assertIn("ADMIN_URL", self.msgs()[-1])
        self.store.moderate(self.m["id"], "archive", "admin:7", "t")
        self.user = {"id": 8, "first_name": "A", "language_code": "uz"}; bot.db.set_lang(8, "uz")
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
        said = {"r": result(heard="Bomdod besh yigirma, Peshin o'n ikki o'ttiz, Asr to'rt, Shom besh o'ttiz besh, Xufton yetti")}
        def stt(data):
            if isinstance(said["r"], Exception):
                raise said["r"]
            return said["r"]
        bot = self.botmod.Bot(lambda m, **kw: self.sent.append((m, kw)), self.store, None, lambda fid: b"ogg", "https://example.org", stt_read=stt)
        bot.db.set_lang(555, "uz")
        def say(**kw):
            base = {"chat": {"id": 555, "type": "private"}, "from": self.user}
            bot.handle({"update_id": 1, "message": dict(base, **kw)})
        def pick():
            say(location={"latitude": 40.0, "longitude": 66.0})
            bot.handle({"update_id": 2, "callback_query": {"id": "q", "from": self.user, "data": "pick:" + self.m["id"], "message": {"chat": {"id": 555}}}})
        say(voice={"file_id": "v", "duration": 5})                                            # no mosque chosen yet
        self.assertIn("lokatsiya", self.msgs()[-1])
        pick()
        say(voice={"file_id": "v", "duration": 120}); self.assertIn("60", self.msgs()[-1])    # too long
        say(voice={"file_id": "v", "duration": 8})
        txt = self.msgs()[-1]
        self.assertIn("Eshitilgan", txt); self.assertIn("16:00", txt); self.assertIn("19:00", txt)    # heard text and the times from the reader
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"], {})                    # nothing saved before confirmation
        bot.handle({"update_id": 3, "callback_query": {"id": "q", "from": self.user, "data": "save", "message": {"chat": {"id": 555}}}})
        got = self.store.get(self.m["id"])["prayer_times"]
        self.assertEqual((got["asr"], got["maghrib"], got["isha"]), ("16:00", "17:35", "19:00"))
        # unclear speech: says what it heard, asks again and saves nothing
        said["r"] = result({"fajr": "05:20"}, complete=False, heard="salom qalaysiz")
        self.store.edit(self.m["id"], {"prayer_times": {"fajr": None, "dhuhr": None, "asr": None, "maghrib": None, "isha": None}}, "tg:1", {"kind": "telegram"})
        pick()
        say(voice={"file_id": "v", "duration": 3}); self.assertIn("ajrata olmadim", self.msgs()[-1]); self.assertIn("salom qalaysiz", self.msgs()[-1])
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"], {})
        # Gemini unreachable: the user is told it does not work now, nothing crashes
        said["r"] = gemini.Unavailable()
        say(voice={"file_id": "v", "duration": 3}); self.assertIn("ishlamayapti", self.msgs()[-1])
        # no reader configured (no Gemini key): the same message
        nobot = self.botmod.Bot(lambda m, **kw: self.sent.append((m, kw)), self.store, None, lambda fid: b"x", "", stt_read=None)
        nobot.state[555] = {"step": "await_times", "mosque_id": self.m["id"], "name": "x", "ts": __import__("time").time()}
        nobot.handle({"update_id": 5, "message": {"chat": {"id": 555, "type": "private"}, "from": self.user, "voice": {"file_id": "v", "duration": 3}}})
        self.assertIn("ishlamayapti", self.msgs()[-1])

    def test_photo_reader_unavailable_or_missing_tells_the_user_to_type(self):
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        def down(data): raise gemini.Unavailable()
        self.bot.ocr_read = down
        self.send(photo=[{"file_id": "a", "file_size": 10}]); self.assertIn("ishlamayapti", self.msgs()[-1])
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"], {})
        self.bot.ocr_read = None
        self.send(photo=[{"file_id": "a", "file_size": 10}]); self.assertIn("ishlamayapti", self.msgs()[-1])
        self.send(text="05:20 12:30 16:00 17:35 19:00"); self.click("save")                 # typing still works
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"]["isha"], "19:00")

    def test_unreadable_photo_and_typed_times(self):
        self.send(location={"latitude": 40.0, "longitude": 66.0}); self.click("pick:" + self.m["id"])
        self.read = result({"fajr": "05:20", "asr": "16:00"}, complete=False)
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
        self.read = result(alt={"fajr": "05:40", "dhuhr": "12:45", "asr": "16:15", "maghrib": "17:40", "isha": "19:15"})
        self.send(photo=[{"file_id": "a", "file_size": 10}])
        self.assertEqual([b["callback_data"] for b in self.buttons()][:2], ["col1", "col2"])
        self.click("col2"); self.click("save")
        self.assertEqual(self.store.get(self.m["id"])["prayer_times"]["fajr"], "05:40")

    def test_new_mosque_from_location_and_safety(self):
        self.send(location={"latitude": 41.5, "longitude": 67.5})                               # nothing within 200 m
        self.assertEqual([b["callback_data"][:4] for b in self.buttons()], ["new:"])
        self.click(self.buttons()[0]["callback_data"]); self.send(text="Yangi jome")
        new = self.store.map_items((67.4, 41.4, 67.6, 41.6))[0]
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
        self.assertIn("ADMIN_URL", self.msgs()[-1])                                              # the web is on, the moderator page is not
        self.assertFalse([b for b in self.buttons() if "web_app" in b and "admin" in b["web_app"]["url"]])
        self.assertEqual((make_cfg().admin_url, make_cfg(ADMIN_URL="https://a.example/x").admin_url), ("", "https://a.example/x"))
        self.bot.admin_url = "https://admin.example.org/masjiduz/web/admin.html"                # its own domain (TIMUS: not on the API domain)
        self.send(text="/admin")
        self.assertEqual(self.buttons()[0]["web_app"]["url"], "https://admin.example.org/masjiduz/web/admin.html?lang=ru")
        self.bot.admin_url = "http://admin.example.org/admin.html"                              # Telegram opens https only
        self.send(text="/admin")
        self.assertIn("ADMIN_URL", self.msgs()[-1])

    # ---- nearest card, subscriptions, reminders
    def set_times(self, mid=None, times=None, who=("tg:900", {"kind": "telegram", "platform": "telegram-bot"})):
        times = times or {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        self.store.edit(mid or self.m["id"], {"prayer_times": times}, who[0], who[1])

    def test_card_goes_out_with_the_mosque_picture_and_falls_back_to_text(self):
        with self.store.pool.connection() as c:
            c.execute("UPDATE mosques SET photo_url='https://upload.wikimedia.org/p.jpg' WHERE id=%s", (self.m["id"],))
        self.send(location={"latitude": 40.0027, "longitude": 66.0})
        photo = [kw for m, kw in self.sent if m == "sendPhoto"]
        self.assertEqual(len(photo), 1)
        self.assertEqual(photo[0]["photo"], "https://upload.wikimedia.org/p.jpg")
        self.assertIn("Bot mosque", photo[0]["caption"]); self.assertIn("inline_keyboard", photo[0]["reply_markup"])
        self.assertFalse([1 for m, kw in self.sent if m == "sendMessage" and kw["text"].startswith("Eng yaqin masjid")])
        real = self.bot.api
        def refuse(method, **kw):
            if method == "sendPhoto": raise RuntimeError("wrong file identifier/HTTP URL")
            return real(method, **kw)
        self.bot.api = refuse
        self.sent.clear(); self.send(location={"latitude": 40.0027, "longitude": 66.0})
        self.bot.api = real
        # Telegram refused the picture: the card still arrives
        self.assertTrue([1 for m, kw in self.sent if m == "sendMessage" and kw["text"].startswith("Eng yaqin masjid")])
        with self.store.pool.connection() as c:
            c.execute("UPDATE mosques SET photo_url=NULL WHERE id=%s", (self.m["id"],))

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
        n = self.botmod.Notifier(lambda m, **kw: self.sent.append((m, kw)), self.bot.db, "https://example.org")
        n.pause = 0
        return n

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
        n3 = self.botmod.Notifier(api, self.bot.db); n3.pause = 0; self.sent.clear()
        n3.tick(local(tt["maghrib"] - 0.2) + timedelta(days=3))
        self.assertIsNone(self.bot.db.user(555)); self.assertTrue(self.bot.db.user(556)); self.assertEqual(len(sent_to(556)), 1)

    def test_a_passing_send_failure_gives_the_claim_back_and_the_next_tick_retries(self):
        import prayertimes as P
        from datetime import datetime, timedelta, timezone
        tt = P.times(2026, 10, 26, *P.REGIONS[0])
        local = lambda h: datetime(2026, 10, 26, tzinfo=timezone.utc) + timedelta(hours=h - 5)      # noqa: E731
        self.bot.db.set_user(555, region=0, daily=True, before_prayer=True, lang="uz")
        state = {"fail": True}
        def api(method, **kw):
            if state["fail"]:
                raise self.botmod.TelegramError(method, 429, "Too Many Requests")
            self.sent.append((method, kw))
        n = self.botmod.Notifier(api, self.bot.db); n.pause = 0
        n.tick(local(tt["fajr"] - 0.4))                                                             # the daily message fails for a passing reason
        self.assertEqual(self.sent, [])
        self.assertEqual(raw_db("SELECT count(*) FROM bot_sent"), [(0,)])                           # the claim was given back
        self.assertTrue(self.bot.db.user(555))
        state["fail"] = False
        n.tick(local(tt["fajr"] - 0.35))                                                            # the same Notifier retries at the next tick
        self.assertEqual(len([1 for m, kw in self.sent if "Bugungi" in kw["text"]]), 1)
        n.tick(local(tt["fajr"] - 0.3)); n.tick(local(tt["fajr"] - 0.3))
        self.assertEqual(len([1 for m, kw in self.sent if "Bugungi" in kw["text"]]), 1)             # and then exactly once
        # the same for a before-prayer reminder
        self.sent.clear(); state["fail"] = True
        n.tick(local(tt["asr"] - 0.2))
        self.assertEqual(self.sent, [])
        self.assertEqual(raw_db("SELECT count(*) FROM bot_sent WHERE key LIKE 'pre:%%'"), [(0,)])
        state["fail"] = False
        n.tick(local(tt["asr"] - 0.15))
        self.assertEqual(len([1 for m, kw in self.sent if "Asr" in kw["text"]]), 1)
        n.tick(local(tt["asr"] - 0.1))
        self.assertEqual(len([1 for m, kw in self.sent if "Asr" in kw["text"]]), 1)
        # a restarted bot (fresh Notifier) also finds nothing left to claim twice
        n2 = self.notifier(); total = len(self.sent)
        n2.tick(local(tt["asr"] - 0.12)); self.assertEqual(len(self.sent), total)

    def test_a_blocked_user_is_forgotten_not_retried(self):
        import prayertimes as P
        from datetime import datetime, timedelta, timezone
        tt = P.times(2026, 10, 26, *P.REGIONS[0])
        self.bot.db.set_user(555, region=0, daily=True, lang="uz")
        def api(method, **kw):
            raise self.botmod.TelegramError(method, 403, "Forbidden: bot was blocked by the user")
        n = self.botmod.Notifier(api, self.bot.db); n.pause = 0
        n.tick(datetime(2026, 10, 26, tzinfo=timezone.utc) + timedelta(hours=tt["fajr"] - 0.4 - 5))
        self.assertIsNone(self.bot.db.user(555))
        self.assertEqual(raw_db("SELECT count(*) FROM bot_sent"), [(0,)])

    def test_unclaim_gives_exactly_that_claim_back(self):
        db_ = self.bot.db
        self.assertTrue(db_.claim(555, "daily", "2026-10-26"))
        self.assertFalse(db_.claim(555, "daily", "2026-10-26"))
        self.assertTrue(db_.claim(555, "pre:asr", "2026-10-26"))
        db_.unclaim(555, "daily", "2026-10-26")
        self.assertTrue(db_.claim(555, "daily", "2026-10-26"))                                       # claimable again
        self.assertFalse(db_.claim(555, "pre:asr", "2026-10-26"))                                    # the other claim is untouched
        db_.unclaim(555, "nothing", "2026-10-26")                                                    # no claim: no error

    def test_purge_survives_concurrent_changes_to_its_dictionaries(self):
        import collections
        import time
        stop = threading.Event()
        def churn():
            i = 0
            while not stop.is_set():
                i += 1
                self.bot.state[i] = {"step": "await_times", "ts": 0}
                self.bot.msg_log[i] = collections.deque([0])
                self.bot.photo_log[i] = collections.deque([0])
                self.bot.state.pop(i - 50, None); self.bot.msg_log.pop(i - 50, None); self.bot.photo_log.pop(i - 50, None)
        t = threading.Thread(target=churn); t.start()
        try:
            end = time.time() + 1.0
            while time.time() < end:
                self.bot.purge()
        finally:
            stop.set(); t.join()

    def test_idle_tick_with_many_users_is_fast(self):
        import time
        from datetime import datetime, timezone
        for tid in range(1000, 1060):
            self.bot.db.set_user(tid, region=tid % 14, daily=True, before_prayer=True)
        n = self.botmod.Notifier(lambda m, **kw: self.sent.append((m, kw)), self.bot.db)           # default pause between messages
        t0 = time.time(); n.tick(datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc))               # 14:00 local: nothing is due
        self.assertEqual(self.sent, []); self.assertLess(time.time() - t0, 2.0)                  # no sleeping while nothing is sent

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
        self.assertEqual(link, "https://example.org/?m=" + mid + "&lang=uz")
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
        f = self.botmod.Notifier(flaky, self.bot.db); f.pause = 0; f.tick()
        self.assertEqual(sorted(calls), [555, 556, 557])
        self.assertIsNone(self.bot.db.user(556)); self.assertEqual(self.bot.db.subs(556), [])  # blocked: forgotten
        calls.clear(); f.tick(); self.assertEqual(calls, [555])                                # only the failed one is retried



if __name__ == "__main__":
    unittest.main()
