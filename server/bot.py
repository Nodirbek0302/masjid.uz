"""Telegram bot (long polling, standard library only).

Commands: /start /help /nearest /reminders (/region) /subs /stop /id /admin /cancel.
Update flow: the user shares a mosque's location -> picks the mosque (or adds a new one) -> sends a photo of the timetable (read with
Gemini) or types the five times -> checks the preview -> the times are saved, and the bot says they changed and offers a button to
open that mosque in the Mini App. Also: a nearest-mosque card for every shared location, subscriptions (a message when a mosque's
congregation times change) and daily / 15-minutes-before prayer reminders (Notifier). A photo is never stored and a wrong reading is never saved without the user's confirmation.
The token comes from the environment (TIMUS_APP_MASJIDUZ_TELEGRAM_BOT_TOKEN) and is never logged. Container command `bot`: a single replica.
"""
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402  (Store, clean_coord)
import psycopg  # noqa: E402
import botdb  # noqa: E402
import bottexts  # noqa: E402
import gemini  # noqa: E402
import logs  # noqa: E402
import prayertimes  # noqa: E402
import timetable  # noqa: E402
import translit  # noqa: E402

_log = logging.getLogger("bot")
TOKEN = ""                        # set by main(cfg)
WEBAPP_URL = ""
ADMIN_IDS = set()
API = ""
NEAR_M = 200.0
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTOS_PER_HOUR = 30
SESSION_S = 1800
MSGS_PER_MIN = 40
MAX_VOICE_S = 60
TZ_HOURS = 5                      # Uzbekistan has no daylight saving time: UTC+5 all year
REMIND_BEFORE_MIN = 15


class TelegramError(RuntimeError):
    def __init__(self, method, code, description):
        super().__init__("%s failed: %s" % (method, description))
        self.code, self.description = code, description or ""

    @property
    def gone(self):
        """The user blocked the bot / deleted the chat: their data is removed instead of retrying forever."""
        d = self.description.lower()
        return self.code == 403 or "chat not found" in d or "deactivated" in d


def call(method, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(API + method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {}
        raise TelegramError(method, e.code, body.get("description")) from None
    if not out.get("ok"):
        raise TelegramError(method, out.get("error_code"), out.get("description"))
    return out["result"]


def download(file_id):
    info = call("getFile", file_id=file_id)
    if info.get("file_size", 0) > MAX_PHOTO_BYTES:
        raise ValueError("too large")
    with urllib.request.urlopen("https://api.telegram.org/file/bot%s/%s" % (TOKEN, info["file_path"]), timeout=60) as r:
        data = r.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("too large")
    return data


def log(msg):
    _log.info("%s", logs.mask(msg, 200))


LANG_NAMES = [("uz", "Oʻzbekcha"), ("uz-cyrl", "Ўзбекча"), ("ru", "Русский"), ("en", "English")]
LANG_ASK = "🌐 Tilni tanlang · Тилни танланг · Выберите язык · Choose a language"


def web_url(base, lang, path="/", **q):
    """Link into the Mini App that opens in the user's chosen language (the web page reads ?lang=)."""
    return base.rstrip("/") + path + "?" + urlencode(dict(q, lang=lang))


TEXTS = {
    "uz": {
        "hello": "Assalomu alaykum! Bu bepul masjid xaritasi: masjidlarni toping, jamoat namozi vaqtlarini koʻring, yangi masjid qoʻshing yoki "
                 "notoʻgʻri maʼlumot haqida xabar bering.\n\n📍 Vaqtlarni yangilash: masjid lokatsiyasini yuboring (📎 → Joylashuv), keyin "
                 "namoz vaqtlari jadvalining rasmini yuboring yoki vaqtlarni ovozli xabar bilan ayting. Oʻzim oʻqib yangilayman.",
        "pending": "(Mini App hali ulanmagan: HTTPS manzil kutilmoqda.)", "open": "🕌 Xaritani ochish",
        "pick": "200 m ichida quyidagi masjidlar bor. Qaysi biri?", "none": "200 m ichida masjid topilmadi. Yangi masjid sifatida qoʻshamizmi?",
        "new": "➕ Yangi masjid qoʻshish", "noname": "Nomsiz masjid",
        "ask_name": "Masjid nomini yozing (yoki /skip: nomsiz qoʻshish):", "created": "✅ Masjid qoʻshildi.",
        "send_photo": "📷 «{name}» uchun jadval rasmini yuboring yoki 🎤 5 ta namoz vaqtini ovozli xabar bilan ayting (masalan: «Bomdod 5 20, Peshin 12 30, Asr 4, Shom 5 35, Xufton 7»; AM/PM aytish shart emas).\nYoki matn bilan yozing: 05:20 12:30 16:00 17:35 19:00",
        "voice_reading": "🎧 Ovoz tinglanmoqda…", "no_stt": "Ovozni tanish hozircha ishlamayapti. Rasm yuboring yoki vaqtlarni matn bilan yozing: 05:20 12:30 16:00 17:35 19:00",
        "voice_heard": "🎤 Eshitilgan: «{t}»", "voice_long": "Ovozli xabar 60 soniyadan oshmasin. Qisqaroq ayting.",
        "voice_unclear": "Ovozdan 5 ta vaqtni aniq ajrata olmadim.{found}\nQayta ayting (masalan: «Bomdod 5 20, Peshin 12 30, Asr 4, Shom 5 35, Xufton 7») yoki matn bilan yozing.",
        "need_loc": "Avval masjidni tanlang: lokatsiyasini yuboring (📎 → Joylashuv) yoki masjid nomini yozing.",
        "search_pick": "Topilgan masjidlar. Qaysi biri?", "search_none": "Masjid topilmadi. Boshqa nom yozing yoki lokatsiya yuboring.", "reading": "🔎 Rasm oʻqilmoqda…",
        "no_ocr": "Rasmni oʻqish hozircha ishlamayapti. Vaqtlarni matn bilan yozing: 05:20 12:30 16:00 17:35 19:00",
        "unreadable": "Rasmdan vaqtlarni ishonchli oʻqiy olmadim.{found}\nVaqtlarni matn bilan yozing: 05:20 12:30 16:00 17:35 19:00",
        "found": " Topilganlari: {t}.", "confirm": "«{name}» uchun jamoat vaqtlari:\n{t}\n\nToʻgʻrimi?",
        "two_cols": "Jadvalda ikki ustun bor. Jamoat vaqti qaysi?\n\n1) {a}\n2) {b}",
        "v1": "1-ustun", "v2": "2-ustun", "yes": "✅ Ha, saqlash", "manual": "✏️ Qoʻlda yozaman", "cancel": "❌ Bekor",
        "manual_ask": "Vaqtlarni yozing: Bomdod Peshin Asr Shom Xufton, masalan 05:20 12:30 16:00 17:35 19:00",
        "saved": "✅ Saqlandi. «{name}» masjidi uchun jamoat vaqtlari oʻzgardi, tekshirib koʻrishingiz mumkin. Rahmat, boshqalarga ham foydali boʻladi!",
        "see": "🕌 Masjidni koʻrish", "cancelled": "Bekor qilindi.", "expired": "Sessiya tugadi. Masjid lokatsiyasini qayta yuboring.",
        "error": "Xatolik yuz berdi. Keyinroq urinib koʻring.",
        "archived": "Bu masjid arxivlangan, vaqtlarni yangilab boʻlmaydi.", "limit": "Juda koʻp urinish. Biroz kuting.",
        "bad_times": "Vaqtlar tushunarsiz. 5 ta vaqtni shu tartibda yozing: 05:20 12:30 16:00 17:35 19:00",
        "names": ["Bomdod", "Peshin", "Asr", "Shom", "Xufton"],
        "loc_btn": "📍 Joylashuv orqali vaqtlarni yangilash",
        "loc_hint": "Masjidda boʻlsangiz, pastdagi tugma bilan joylashuvingizni yuboring, keyin jadval rasmini yuboring 👇\nKompyuterda tugma ishlamasa, masjid nomini yozing.",
        "admin_btn": "🛠 Moderator paneli", "admin_text": "Moderator paneli:", "id": "Sizning Telegram ID: {id}",
    },
    "ru": {
        "hello": "Ассаламу алайкум! Это бесплатная карта мечетей: находите мечети, смотрите время джамаат-намазов, добавляйте новые мечети "
                 "и сообщайте о неверных данных.\n\n📍 Обновить время: отправьте геопозицию мечети (📎 → Геопозиция), затем фото расписания "
                 "намазов или назовите время голосом. Я прочитаю и обновлю.",
        "pending": "(Mini App ещё не подключено: ожидается HTTPS-адрес.)", "open": "🕌 Открыть карту",
        "pick": "В радиусе 200 м есть такие мечети. Какая из них?", "none": "В радиусе 200 м мечетей нет. Добавить как новую?",
        "new": "➕ Добавить новую мечеть", "noname": "Мечеть без названия",
        "ask_name": "Напишите название мечети (или /skip: добавить без названия):", "created": "✅ Мечеть добавлена.",
        "send_photo": "📷 Отправьте фото расписания для «{name}» или 🎤 назовите 5 времён намаза голосом (например: «Фаджр 5 20, Зухр 12 30, Аср 4, Магриб 5 35, Иша 7»; AM/PM говорить не нужно).\nИли напишите текстом: 05:20 12:30 16:00 17:35 19:00",
        "voice_reading": "🎧 Слушаю голосовое…", "no_stt": "Распознавание голоса сейчас недоступно. Отправьте фото или напишите время текстом: 05:20 12:30 16:00 17:35 19:00",
        "voice_heard": "🎤 Услышано: «{t}»", "voice_long": "Голосовое не длиннее 60 секунд. Скажите короче.",
        "voice_unclear": "Не удалось чётко выделить 5 времён из голоса.{found}\nСкажите ещё раз (например: «Фаджр 5 20, Зухр 12 30, Аср 4, Магриб 5 35, Иша 7») или напишите текстом.",
        "need_loc": "Сначала выберите мечеть: отправьте её геопозицию (📎 → Геопозиция) или напишите название.",
        "search_pick": "Найденные мечети. Какая из них?", "search_none": "Мечеть не найдена. Напишите другое название или отправьте геопозицию.", "reading": "🔎 Читаю фото…",
        "no_ocr": "Чтение фото сейчас недоступно. Напишите время текстом: 05:20 12:30 16:00 17:35 19:00",
        "unreadable": "Не удалось надёжно прочитать время на фото.{found}\nНапишите время текстом: 05:20 12:30 16:00 17:35 19:00",
        "found": " Найдено: {t}.", "confirm": "Время джамаата для «{name}»:\n{t}\n\nВерно?",
        "two_cols": "В расписании два столбца. Какой из них время джамаата?\n\n1) {a}\n2) {b}",
        "v1": "1-й столбец", "v2": "2-й столбец", "yes": "✅ Да, сохранить", "manual": "✏️ Введу вручную", "cancel": "❌ Отмена",
        "manual_ask": "Напишите время: Фаджр Зухр Аср Магриб Иша, например 05:20 12:30 16:00 17:35 19:00",
        "saved": "✅ Сохранено. Время джамаата мечети «{name}» изменено, можете проверить. Спасибо, это поможет и другим!",
        "see": "🕌 Посмотреть мечеть", "cancelled": "Отменено.", "expired": "Сессия истекла. Отправьте геопозицию мечети ещё раз.",
        "error": "Произошла ошибка. Попробуйте позже.",
        "archived": "Эта мечеть в архиве, обновить время нельзя.", "limit": "Слишком много попыток. Подождите немного.",
        "bad_times": "Время не распознано. Напишите 5 времён в таком порядке: 05:20 12:30 16:00 17:35 19:00",
        "names": ["Фаджр", "Зухр", "Аср", "Магриб", "Иша"],
        "loc_btn": "📍 Обновить время по геопозиции",
        "loc_hint": "Если вы в мечети, отправьте геопозицию кнопкой ниже, затем фото расписания 👇\nЕсли на компьютере кнопка не работает, напишите название мечети.",
        "admin_btn": "🛠 Панель модератора", "admin_text": "Панель модератора:", "id": "Ваш Telegram ID: {id}",
    },
    "en": {
        "hello": "Assalamu alaykum! This is a free mosque map: find mosques, see congregation prayer times, add new mosques and report "
                 "incorrect information.\n\n📍 To update times: share the mosque's location (📎 → Location), then send a photo of its "
                 "prayer timetable or say the times in a voice message. I will read them and update.",
        "pending": "(The Mini App is not connected yet: waiting for an HTTPS address.)", "open": "🕌 Open the map",
        "pick": "These mosques are within 200 m. Which one?", "none": "No mosque within 200 m. Add it as a new mosque?",
        "new": "➕ Add a new mosque", "noname": "Unnamed mosque",
        "ask_name": "Type the mosque's name (or /skip to add it without a name):", "created": "✅ Mosque added.",
        "send_photo": "📷 Send a photo of the timetable for “{name}” or 🎤 say the 5 prayer times in a voice message (e.g. “Fajr 5 20, Dhuhr 12 30, Asr 4, Maghrib 5 35, Isha 7”; no need to say AM/PM).\nOr type them: 05:20 12:30 16:00 17:35 19:00",
        "voice_reading": "🎧 Listening…", "no_stt": "Voice recognition is not available right now. Send a photo or type the times: 05:20 12:30 16:00 17:35 19:00",
        "voice_heard": "🎤 Heard: “{t}”", "voice_long": "Please keep the voice message under 60 seconds.",
        "voice_unclear": "I could not clearly pick out the 5 times from your voice.{found}\nSay them again (e.g. “Fajr 5 20, Dhuhr 12 30, Asr 4, Maghrib 5 35, Isha 7”) or type them.",
        "need_loc": "First choose the mosque: share its location (📎 → Location) or type its name.",
        "search_pick": "Mosques found. Which one?", "search_none": "No mosque found. Type another name or share a location.", "reading": "🔎 Reading the photo…",
        "no_ocr": "Photo reading is not available right now. Type the times: 05:20 12:30 16:00 17:35 19:00",
        "unreadable": "I could not read the times from the photo reliably.{found}\nType the times: 05:20 12:30 16:00 17:35 19:00",
        "found": " Found: {t}.", "confirm": "Congregation times for “{name}”:\n{t}\n\nIs this right?",
        "two_cols": "The timetable has two columns. Which one is the congregation time?\n\n1) {a}\n2) {b}",
        "v1": "Column 1", "v2": "Column 2", "yes": "✅ Yes, save", "manual": "✏️ I'll type them", "cancel": "❌ Cancel",
        "manual_ask": "Type the times: Fajr Dhuhr Asr Maghrib Isha, e.g. 05:20 12:30 16:00 17:35 19:00",
        "saved": "✅ Saved. The congregation times for “{name}” have changed, you can check them. Thank you, it helps others too!",
        "see": "🕌 View the mosque", "cancelled": "Cancelled.", "expired": "The session expired. Please share the mosque's location again.",
        "error": "Something went wrong. Please try again later.",
        "archived": "This mosque is archived, its times cannot be updated.", "limit": "Too many attempts. Please wait a moment.",
        "bad_times": "I could not understand the times. Type 5 times in this order: 05:20 12:30 16:00 17:35 19:00",
        "names": ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"],
        "loc_btn": "📍 Update times by location",
        "loc_hint": "If you are at the mosque, send your location with the button below, then a photo of the timetable 👇\nIf the button does nothing on desktop, type the mosque's name.",
        "admin_btn": "🛠 Moderator panel", "admin_text": "Moderator panel:", "id": "Your Telegram ID: {id}",
    },
}


for _lc, _more in bottexts.MORE.items():
    TEXTS[_lc].update(_more)
for _lc, _v in {"uz": "Xarita", "ru": "Карта", "en": "Map"}.items():
    TEXTS[_lc]["menu"] = _v
CMDS = {   # command menu per language (Telegram shows it under "Menu")
    "uz": [("start", "Boshlash"), ("nearest", "Eng yaqin masjid"), ("reminders", "Eslatmalar"), ("subs", "Obunalarim"), ("lang", "Til"),
           ("help", "Yordam"), ("stop", "Maʼlumotlarimni oʻchirish")],
    "ru": [("start", "Начать"), ("nearest", "Ближайшая мечеть"), ("reminders", "Напоминания"), ("subs", "Мои подписки"), ("lang", "Язык"),
           ("help", "Помощь"), ("stop", "Удалить мои данные")],
    "en": [("start", "Start"), ("nearest", "Nearest mosque"), ("reminders", "Reminders"), ("subs", "My subscriptions"), ("lang", "Language"),
           ("help", "Help"), ("stop", "Delete my data")],
}


def _cyr(v):
    return [translit.cyrillize(x) for x in v] if isinstance(v, list) else translit.cyrillize(v)


# Uzbek in Cyrillic is derived from the Latin texts (same rules as the web interface), so the two can never drift apart.
TEXTS["uz-cyrl"] = {k: _cyr(v) for k, v in TEXTS["uz"].items() if isinstance(v, (str, list))}
bottexts.REGION_NAMES["uz-cyrl"] = [translit.cyrillize(n) for n in bottexts.REGION_NAMES["uz"]]
CMDS["uz-cyrl"] = [(c, translit.cyrillize(d)) for c, d in CMDS["uz"]]
for _lc, _t in TEXTS.items():
    _t["_lang"] = _lc
    _t["lang_ok"] = "✅ " + dict(LANG_NAMES)[_lc]


def fmt_times(tx, times):
    return "\n".join("%s  %s" % (n, times.get(k, "—")) for n, k in zip(tx["names"], timetable.ORDER))


class Bot:
    """All conversation logic; the Telegram API, the database and the OCR are injected so it can be tested without a network."""

    def __init__(self, api, store, ocr_read=None, fetch_photo=None, webapp_url="", admin_ids=(), stt_read=None):
        self.api, self.store, self.ocr_read, self.fetch_photo, self.stt_read = api, store, ocr_read, fetch_photo, stt_read
        self.webapp_url, self.admin_ids = webapp_url, set(admin_ids)
        self.db = botdb.BotDB(store)
        self.state, self.photo_log, self.msg_log, self.pending = {}, {}, {}, {}
        self._locks, self._locks_guard = {}, threading.Lock()

    # ---- helpers
    def say(self, chat_id, text, buttons=None, reply_keyboard=None):
        kw = {}
        if buttons:
            kw["reply_markup"] = {"inline_keyboard": buttons}
        elif reply_keyboard:
            kw["reply_markup"] = {"keyboard": reply_keyboard, "resize_keyboard": True, "is_persistent": True}
        self.api("sendMessage", chat_id=chat_id, text=text, **kw)

    def say_card(self, chat_id, text, buttons, photo_url=None):
        """A mosque card; with the mosque's picture above it when there is one (Telegram loads it from Wikimedia). If Telegram
        cannot take the picture, the card goes out as plain text: the answer is never lost because of a photo."""
        if photo_url and photo_url.startswith("https://") and len(text) <= 1024:
            try:
                return self.api("sendPhoto", chat_id=chat_id, photo=photo_url, caption=text, reply_markup={"inline_keyboard": buttons})
            except Exception:
                pass
        self.say(chat_id, text, buttons)

    def identity(self, user):
        actor = "tg:%d" % user["id"]
        info = {"kind": "telegram", "platform": "telegram-bot", "user_agent": "telegram-bot",
                "telegram": {k: user.get(k) for k in ("id", "username", "first_name", "last_name", "language_code")}}
        return actor, info

    def session(self, uid):
        st = self.state.get(uid)
        if st and time.time() - st["ts"] > SESSION_S:
            self.state.pop(uid, None)
            return None
        return st

    def lock_for(self, uid):
        """One conversation is handled by one thread at a time (a slow photo must not let a second tap race the first)."""
        with self._locks_guard:
            return self._locks.setdefault(uid, threading.Lock())

    def allow_msg(self, uid):
        now = time.time()
        q = self.msg_log.setdefault(uid, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= MSGS_PER_MIN:
            return False
        q.append(now)
        return True

    def purge(self):
        """Forget finished sessions and old counters so a long-running bot does not grow without bound."""
        now = time.time()
        for uid in [u for u, st in list(self.state.items()) if now - st["ts"] > SESSION_S]:
            self.state.pop(uid, None)
        for log_ in (self.photo_log, self.msg_log):
            for uid in [u for u, q in list(log_.items()) if not q or now - max(list(q)) > 3600]:
                log_.pop(uid, None)

    def allow_photo(self, uid):
        now = time.time()
        q = [t for t in self.photo_log.get(uid, []) if now - t < 3600]
        ok = len(q) < PHOTOS_PER_HOUR
        if ok:
            q.append(now)
        self.photo_log[uid] = q
        return ok

    def web(self, tx, path="/", **q):
        return web_url(self.webapp_url, tx["_lang"], path, **q)

    def open_button(self, mosque_id, tx):
        if not self.webapp_url.startswith("https://"):
            return None
        return [[{"text": tx["see"], "web_app": {"url": self.web(tx, "/", m=mosque_id)}}]]

    # ---- language: asked on the first contact, remembered, used for every message and for the Mini App links
    def lang_of(self, uid):
        try:
            return self.db.lang_of(uid)
        except Exception:
            return None

    def ask_language(self, chat):
        rows = [[{"text": n, "callback_data": "lang:" + c} for c, n in LANG_NAMES[i:i + 2]] for i in (0, 2)]
        self.say(chat, LANG_ASK, rows)

    def apply_language(self, chat, user, lang):
        """Store the choice and switch the chat's command menu and Mini App menu button to that language."""
        self.db.set_lang(user["id"], lang)
        tx = TEXTS[lang]
        cmds = [{"command": c, "description": d} for c, d in CMDS[lang]]
        if user["id"] in self.admin_ids:
            cmds += [{"command": "admin", "description": "Moderator"}, {"command": "id", "description": "Telegram ID"}]
        try:
            self.api("setMyCommands", commands=cmds, scope={"type": "chat", "chat_id": chat})
            if self.webapp_url.startswith("https://"):
                self.api("setChatMenuButton", chat_id=chat, menu_button={"type": "web_app", "text": tx["menu"], "web_app": {"url": self.web(tx)}})
        except Exception as e:
            log("language menu not applied: %s" % type(e).__name__)
        return tx

    # ---- entry point
    def handle(self, update):
        who = (update.get("callback_query") or update.get("message") or {}).get("from") or {}
        if not who.get("id"):
            return
        with self.lock_for(who["id"]):
            self._handle(update)

    def _handle(self, update):
        if "callback_query" in update:
            return self.on_callback(update["callback_query"])
        msg = update.get("message") or {}
        if msg.get("chat", {}).get("type") != "private" or not msg.get("from"):
            return
        user, chat = msg["from"], msg["chat"]["id"]
        if not self.allow_msg(user["id"]):                     # flood protection: silently drop the excess
            return
        lang = self.lang_of(user["id"])
        if lang is None:                                       # first contact: ask the language, then carry on with this very message
            if len(self.pending) > 2000:
                self.pending.pop(next(iter(self.pending)))
            self.pending[user["id"]] = update
            try:
                return self.ask_language(chat)
            except Exception as e:
                return log("language question failed: %s" % type(e).__name__)
        tx = TEXTS[lang]
        try:
            if msg.get("location"):
                return self.on_location(chat, user, tx, msg["location"])
            if msg.get("photo"):
                return self.on_photo(chat, user, tx, msg["photo"])
            voice = msg.get("voice") or msg.get("audio")
            if voice:
                return self.on_voice(chat, user, tx, voice)
            doc = msg.get("document")
            if doc and str(doc.get("mime_type", "")).startswith("image/"):       # a picture sent "as a file" (common on desktop)
                return self.on_photo(chat, user, tx, [{"file_id": doc["file_id"], "file_size": doc.get("file_size", 0)}])
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                return self.on_command(chat, user, tx, text)
            if text:
                return self.on_text(chat, user, tx, text)
        except Exception as e:                                 # never let one conversation crash the bot
            log("handler error: %s" % type(e).__name__)
            try:
                self.say(chat, tx["error"])
            except Exception:
                pass

    def on_command(self, chat, user, tx, text):
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        uid = user["id"]
        if cmd == "/start" and arg.startswith("s_") and len(arg) == 38:       # deep link: subscribe to this mosque's time changes
            return self.subscribe(chat, user, tx, arg[2:])
        if cmd == "/start" and arg.startswith("m_") and len(arg) == 38:       # deep link from the mosque page: /start m_<mosque id>
            try:
                m = self.store.get(arg[2:])
            except app.ApiError:
                return self.say(chat, tx["error"])
            self.state[uid] = {"step": "await_times", "mosque_id": m["id"], "name": m["name"] or tx["noname"], "ts": time.time()}
            return self.say(chat, self.ask_times(tx, self.state[uid]["name"]))
        if cmd in ("/start", "/help"):
            if self.webapp_url.startswith("https://"):
                self.say(chat, tx["hello"], [[{"text": tx["open"], "web_app": {"url": self.web(tx)}}]])
            else:
                self.say(chat, tx["hello"] + "\n\n" + tx["pending"])
            # persistent button under the input box: one tap sends the user's current location (they are standing at the mosque)
            self.say(chat, tx["loc_hint"], reply_keyboard=[[{"text": tx["loc_btn"], "request_location": True}]])
        elif cmd == "/lang":
            self.ask_language(chat)
        elif cmd == "/id":
            self.say(chat, tx["id"].format(id=uid))
        elif cmd == "/admin":
            if uid in self.admin_ids:
                if self.webapp_url.startswith("https://"):
                    self.say(chat, tx["admin_text"], [[{"text": tx["admin_btn"], "web_app": {"url": self.web(tx, "/admin.html")}}]])
                else:
                    self.say(chat, tx["pending"])
        elif cmd == "/nearest":
            self.say(chat, tx["near_ask"], reply_keyboard=[[{"text": tx["loc_btn"], "request_location": True}]])
        elif cmd == "/subs":
            self.subs_list(chat, user, tx)
        elif cmd in ("/reminders", "/region"):
            self.reminders_menu(chat, user, tx)
        elif cmd == "/stop":
            self.db.forget(uid)
            self.state.pop(uid, None)
            self.say(chat, tx["stop_msg"])
        elif cmd == "/cancel":
            self.state.pop(uid, None)
            self.say(chat, tx["cancelled"])
        elif cmd == "/skip":
            st = self.session(uid)
            if st and st["step"] == "await_name":
                self.create_mosque(chat, user, tx, st, None)

    # ---- location -> choose mosque
    def on_location(self, chat, user, tx, loc):
        lat, lon = app.clean_coord(loc.get("latitude"), loc.get("longitude"))
        found = self.store.nearest(lat, lon, 3)
        if found:                                          # the nearest mosque as a card (even when it is further than 200 m)
            self.say_card(chat, tx["card_head"] + "\n\n" + self.card_text(tx, found[0]), self.card_buttons(tx, found[0], user["id"]), found[0].get("photo_url"))
        near = [m for m in found if m["distance_m"] <= NEAR_M]
        rows = [[{"text": "%s · %d m" % (m["name"] or tx["noname"], round(m["distance_m"])), "callback_data": "pick:" + m["id"]}] for m in near]
        rows.append([{"text": tx["new"], "callback_data": "new:%.6f,%.6f" % (lat, lon)}])
        self.say(chat, tx["pick"] if near else tx["none"], rows)

    def on_callback(self, cb):
        user, data = cb["from"], cb.get("data", "")
        if not cb.get("message"):
            return
        chat = cb["message"]["chat"]["id"]
        uid = user["id"]
        try:
            self.api("answerCallbackQuery", callback_query_id=cb["id"])
        except Exception:
            pass
        if not self.allow_msg(uid):                            # taps count towards the flood limit too (answered above so the button stops spinning)
            return
        kind, _, arg = data.partition(":")
        lang = self.lang_of(uid)
        if lang is None and kind != "lang":
            return self.ask_language(chat)
        tx = TEXTS[lang or "uz"]
        try:
            if kind == "lang" and arg in TEXTS:
                tx = self.apply_language(chat, user, arg)
                self.say(chat, tx["lang_ok"])
                pending = self.pending.pop(uid, None)
                if pending:
                    self._handle(pending)                      # the message that started the conversation, now in the chosen language
                else:
                    self.on_command(chat, user, tx, "/start")
            elif kind == "pick":
                m = self.store.get(arg)
                self.state[uid] = {"step": "await_times", "mosque_id": m["id"], "name": m["name"] or tx["noname"], "ts": time.time()}
                self.say(chat, self.ask_times(tx, self.state[uid]["name"]))
            elif kind == "new":
                lat, lon = (float(x) for x in arg.split(","))
                self.state[uid] = {"step": "await_name", "lat": lat, "lon": lon, "ts": time.time()}
                self.say(chat, tx["ask_name"])
            elif kind in ("save", "col1", "col2"):
                st = self.session(uid)
                if not st or "times" not in st:
                    return self.say(chat, tx["expired"])
                times = st["alt"] if kind == "col2" and st.get("alt") else st["times"]
                if kind in ("col1", "col2"):
                    st["times"] = times
                    return self.preview(chat, tx, st)
                self.save(chat, user, tx, st)
            elif kind == "sub":
                self.subscribe(chat, user, tx, arg)
            elif kind == "unsub":
                m = self.store.get(arg)
                self.db.unsubscribe(uid, m["id"])
                self.say(chat, tx["unsubscribed"].format(name=m["name"] or tx["noname"]))
            elif kind == "reg" and arg.isdigit() and int(arg) < len(prayertimes.REGIONS):
                self.db.set_user(uid, region=int(arg))
                self.reminders_menu(chat, user, tx)
            elif kind == "rm":
                self.reminder_toggle(chat, user, tx, arg)
            elif kind == "manual":
                self.say(chat, tx["manual_ask"])
            elif kind == "cancel":
                self.state.pop(uid, None)
                self.say(chat, tx["cancelled"])
        except app.ApiError as e:
            self.say(chat, tx["archived"] if e.code == "archived" else tx["error"])
        except Exception as e:
            log("callback error: %s" % type(e).__name__)
            self.say(chat, tx["error"])

    def create_mosque(self, chat, user, tx, st, name):
        actor, info = self.identity(user)
        m = self.store.create({"lat": st["lat"], "lon": st["lon"], "name": name, "force": True}, actor, info)   # the user chose "new" explicitly
        self.state[user["id"]] = {"step": "await_times", "mosque_id": m["id"], "name": m["name"] or tx["noname"], "ts": time.time()}
        self.say(chat, tx["created"])
        self.say(chat, self.ask_times(tx, self.state[user["id"]]["name"]))

    # ---- text: a name for a new mosque, or typed times
    def on_text(self, chat, user, tx, text):
        st = self.session(user["id"])
        if not st:                                       # no mosque chosen yet: treat the text as a name search (works on desktop too)
            found = self.store.search(text, 5) if len(text) >= 2 else []
            if found:
                return self.say(chat, tx["search_pick"], [[{"text": m["name"], "callback_data": "pick:" + m["id"]}] for m in found])
            return self.say(chat, tx["need_loc"] if len(text) < 2 else tx["search_none"])
        if st["step"] == "await_name":
            return self.create_mosque(chat, user, tx, st, text[:200])
        r = timetable.parse(text)
        if not r["complete"] or r["alt"]:
            return self.say(chat, tx["bad_times"])
        st.update(step="confirm", times=r["times"], alt=None, ts=time.time())
        self.preview(chat, tx, st)

    def ask_times(self, tx, name):
        """The request for a photo/voice message; where reading is on, it says before anything is sent that Google reads the file."""
        text = tx["send_photo"].format(name=name)
        return text + "\n\n" + tx["ai_notice"] if (self.ocr_read or self.stt_read) and tx.get("ai_notice") else text

    # ---- photo -> OCR -> preview
    def on_photo(self, chat, user, tx, photos):
        st = self.session(user["id"])
        if not st or st["step"] not in ("await_times", "confirm"):
            return self.say(chat, tx["need_loc"])
        if not self.allow_photo(user["id"]):
            return self.say(chat, tx["limit"])
        if not self.ocr_read or not self.fetch_photo:
            return self.say(chat, tx["no_ocr"])
        self.say(chat, tx["reading"])
        best = max(photos, key=lambda p: p.get("file_size", 0) or p.get("width", 0))
        try:
            r = self.ocr_read(self.fetch_photo(best["file_id"]))
        except gemini.Unavailable:
            return self.say(chat, tx["no_ocr"])
        if r.get("alt"):
            st.update(step="confirm", times=r["times"], alt=r["alt"], ts=time.time())
            return self.say(chat, tx["two_cols"].format(a=fmt_times(tx, r["times"]), b=fmt_times(tx, r["alt"])),
                            [[{"text": tx["v1"], "callback_data": "col1"}, {"text": tx["v2"], "callback_data": "col2"}],
                             [{"text": tx["manual"], "callback_data": "manual"}, {"text": tx["cancel"], "callback_data": "cancel"}]])
        if not r["complete"]:
            found = tx["found"].format(t=", ".join("%s %s" % (n, r["times"][k]) for n, k in zip(tx["names"], timetable.ORDER) if k in r["times"])) if r["times"] else ""
            return self.say(chat, tx["unreadable"].format(found=found))
        st.update(step="confirm", times=r["times"], alt=None, ts=time.time())
        self.preview(chat, tx, st)

    # ---- voice -> speech-to-text -> spoken numbers (AM/PM resolved) -> preview
    def on_voice(self, chat, user, tx, voice):
        st = self.session(user["id"])
        if not st or st["step"] not in ("await_times", "confirm"):
            return self.say(chat, tx["need_loc"])
        if voice.get("duration", 0) > MAX_VOICE_S:
            return self.say(chat, tx["voice_long"])
        if not self.allow_photo(user["id"]):                  # the same hourly budget as photos
            return self.say(chat, tx["limit"])
        if not self.stt_read or not self.fetch_photo:
            return self.say(chat, tx["no_stt"])
        self.say(chat, tx["voice_reading"])
        try:
            r = self.stt_read(self.fetch_photo(voice["file_id"]))
        except gemini.Unavailable:
            return self.say(chat, tx["no_stt"])
        heard = r.get("heard") or ""
        if not r["complete"] or r["alt"]:
            found = tx["found"].format(t=", ".join("%s %s" % (n, r["times"][k]) for n, k in zip(tx["names"], timetable.ORDER) if k in r["times"])) if r["times"] else ""
            return self.say(chat, tx["voice_heard"].format(t=heard[:300]) + "\n" + tx["voice_unclear"].format(found=found))
        st.update(step="confirm", times=r["times"], alt=None, ts=time.time())
        self.preview(chat, tx, st, heard=heard)

    def preview(self, chat, tx, st, heard=None):
        head = (tx["voice_heard"].format(t=heard[:300]) + "\n\n") if heard else ""
        self.say(chat, head + tx["confirm"].format(name=st["name"], t=fmt_times(tx, st["times"])),
                 [[{"text": tx["yes"], "callback_data": "save"}], [{"text": tx["manual"], "callback_data": "manual"}, {"text": tx["cancel"], "callback_data": "cancel"}]])

    # ---- save
    def save(self, chat, user, tx, st):
        actor, info = self.identity(user)
        # no version check: concurrent edits are merged by the store (the five times are applied on top of the latest state)
        self.store.edit(st["mosque_id"], {"prayer_times": st["times"], "note": "Telegram bot (photo/text)"}, actor, info)
        self.state.pop(user["id"], None)
        rows = self.open_button(st["mosque_id"], tx) or []
        if not self.db.is_subscribed(user["id"], st["mosque_id"]):
            rows = rows + [[{"text": tx["sub_offer"], "callback_data": "sub:" + st["mosque_id"]}]]
        self.say(chat, tx["saved"].format(name=st["name"]), rows or None)

    # ---- nearest-mosque card
    @staticmethod
    def local_stamp(iso_utc):
        t = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=TZ_HOURS)
        return t.strftime("%d.%m.%Y %H:%M")

    def card_text(self, tx, m):
        d = m.get("distance_m")
        where = ("%d %s" % (round(d), tx["m"]) if d < 1000 else "%.1f %s" % (d / 1000.0, tx["km"])) if d is not None else ""
        lines = ["🕌 " + (m["name"] or tx["noname"])]
        if where or m.get("address"):
            lines.append("📍 " + " · ".join(x for x in (where, m.get("address")) if x))
        times = m.get("prayer_times") or {}
        lines += ["", fmt_times(tx, times) if any(k in times for k in timetable.ORDER) else tx["no_times"]]
        if m.get("times_updated_at"):
            lines.append(tx["updated"].format(d=self.local_stamp(m["times_updated_at"])))
        return "\n".join(lines)

    def card_buttons(self, tx, m, uid):
        subbed = self.db.is_subscribed(uid, m["id"])
        return [[{"text": tx["route"], "url": "https://yandex.com/maps/?rtext=~%s,%s&rtt=auto" % (m["lat"], m["lon"])}],
                [{"text": tx["upd_btn"], "callback_data": "pick:" + m["id"]},
                 {"text": tx["unsub_btn"] if subbed else tx["sub_btn"], "callback_data": ("unsub:" if subbed else "sub:") + m["id"]}]]

    # ---- subscriptions
    def subscribe(self, chat, user, tx, mosque_id):
        m = self.store.get(mosque_id)
        if m["status"] == "archived":
            return self.say(chat, tx["archived"])
        res = self.db.subscribe(user["id"], m["id"])
        if res == "limit":
            return self.say(chat, tx["sub_limit"].format(n=botdb.MAX_SUBS))
        key = "sub_already" if res == "already" else "subscribed"
        self.say(chat, tx[key].format(name=m["name"] or tx["noname"]), [[{"text": tx["unsub_btn"], "callback_data": "unsub:" + m["id"]}]])

    def subs_list(self, chat, user, tx):
        subs = self.db.subs(user["id"])
        if not subs:
            return self.say(chat, tx["subs_none"])
        self.say(chat, tx["subs_title"], [[{"text": "🔕 " + (s["name"] or tx["noname"]), "callback_data": "unsub:" + s["mosque_id"]}] for s in subs])

    # ---- reminders
    def reminders_menu(self, chat, user, tx):
        lang = tx["_lang"]
        u = self.db.set_user(user["id"], lang=lang)
        if u["region"] is None:
            return self.region_picker(chat, lang, tx)
        onoff = lambda v: tx["on"] if v else tx["off"]  # noqa: E731
        text = tx["rem_title"].format(region=bottexts.REGION_NAMES[lang][u["region"]], daily=onoff(u["daily"]), before=onoff(u["before_prayer"]))
        self.say(chat, text, [[{"text": tx["rem_region"], "callback_data": "rm:region"}],
                              [{"text": tx["rem_daily"].format(s=onoff(not u["daily"])), "callback_data": "rm:daily"}],
                              [{"text": tx["rem_before"].format(s=onoff(not u["before_prayer"])), "callback_data": "rm:before"}],
                              [{"text": tx["rem_off_all"], "callback_data": "rm:off"}]])

    def region_picker(self, chat, lang, tx):
        names = bottexts.REGION_NAMES[lang]
        btns = [{"text": n, "callback_data": "reg:%d" % i} for i, n in enumerate(names)]
        self.say(chat, tx["rem_pick_region"], [btns[i:i + 2] for i in range(0, len(btns), 2)])

    def reminder_toggle(self, chat, user, tx, what):
        lang = tx["_lang"]
        u = self.db.set_user(user["id"], lang=lang)
        if what == "region":
            return self.region_picker(chat, lang, tx)
        if what == "off":
            self.db.set_user(user["id"], daily=False, before_prayer=False)
        elif what in ("daily", "before"):
            if u["region"] is None:
                return self.region_picker(chat, lang, tx)
            field = "daily" if what == "daily" else "before_prayer"
            self.db.set_user(user["id"], **{field: not u[field]})
        self.reminders_menu(chat, user, tx)


class Notifier:
    """Sends what the bot promised on its own: the daily prayer-times message, the 15-minutes-before message and the announcements
    that a subscribed mosque's congregation times changed. `tick(now)` is called every 30 s; every message is claimed in the database
    first (bot_sent), so it goes out exactly once per day even if the bot restarts or ticks twice."""

    def __init__(self, api, db, webapp_url=""):
        self.api, self.db, self.webapp_url = api, db, webapp_url
        self.done, self.day, self.pause = set(), None, 0.04

    def tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        local = now + timedelta(hours=TZ_HOURS)
        if local.date() != self.day:                       # a new day: forget yesterday's in-memory marks and old ledger rows
            self.day, self.done = local.date(), set()
            self.db.purge_sent()
        self.reminders(local)
        self.changes()

    def send(self, tid, text, buttons=None):
        """-> True when delivered. A user who blocked the bot is forgotten; other failures are only logged."""
        kw = {"reply_markup": {"inline_keyboard": buttons}} if buttons else {}
        try:
            self.api("sendMessage", chat_id=tid, text=text, **kw)
            time.sleep(self.pause)                         # stay far below Telegram's broadcast limit (~30 messages/s)
            return True
        except TelegramError as e:
            if e.gone:
                self.db.forget(tid)
            else:
                log("notify error: %s" % e.code)
        except Exception as e:
            log("notify error: %s" % type(e).__name__)
        return False

    def reminders(self, local):
        day, mins, cache = local.date(), local.hour * 60 + local.minute, {}
        for u in self.db.reminder_users():
            tid, region = u["telegram_id"], u["region"]
            if region not in cache:
                lat, lon = prayertimes.REGIONS[region]
                cache[region] = prayertimes.times(day.year, day.month, day.day, lat, lon)
            tt = cache[region]
            tx = TEXTS[u["lang"] if u["lang"] in TEXTS else "uz"]
            name = bottexts.REGION_NAMES[u["lang"] if u["lang"] in TEXTS else "uz"][region]
            at = {k: int(round(v * 60)) for k, v in tt.items()}
            if u["daily"] and at["fajr"] - 30 <= mins < at["dhuhr"] and (tid, "daily") not in self.done:
                self.done.add((tid, "daily"))
                if self.db.claim(tid, "daily", day):
                    labels = [(tx["names"][0], "fajr"), (tx["sun"], "sunrise")] + list(zip(tx["names"][1:], timetable.ORDER[1:]))
                    rows = ["%s  %s" % (n, prayertimes.hm(tt[k])) for n, k in labels]
                    if not self.send(tid, tx["daily_msg"].format(region=name, t="\n".join(rows))) and self.db.user(tid):
                        self.db.unclaim(tid, "daily", day)             # a passing failure: try again at the next tick
                        self.done.discard((tid, "daily"))
            if u["before_prayer"]:
                for n, k in zip(tx["names"], timetable.ORDER):
                    if 0 < at[k] - mins <= REMIND_BEFORE_MIN and (tid, k) not in self.done:
                        self.done.add((tid, k))
                        if self.db.claim(tid, "pre:" + k, day):
                            if not self.send(tid, tx["pre_msg"].format(prayer=n, time=prayertimes.hm(tt[k]), region=name)) and self.db.user(tid):
                                self.db.unclaim(tid, "pre:" + k, day)
                                self.done.discard((tid, k))

    def changes(self):
        for r in self.db.due_changes():
            tid = r["telegram_id"]
            if r["editor"] != "tg:%d" % tid:               # the person who just changed the times already knows
                u = self.db.user(tid)
                tx = TEXTS[u["lang"] if u and u["lang"] in TEXTS else "uz"]
                buttons = None
                if self.webapp_url.startswith("https://"):
                    buttons = [[{"text": tx["see"], "web_app": {"url": web_url(self.webapp_url, tx["_lang"], "/", m=r["mosque_id"])}}]]
                if not self.send(tid, tx["change_msg"].format(name=r["name"] or tx["noname"], t=fmt_times(tx, r["prayer_times"])), buttons):
                    continue                               # try again on the next tick (unless the user is gone)
            self.db.mark_notified(tid, r["mosque_id"], r["times_updated_at"])


def main(cfg):
    global TOKEN, WEBAPP_URL, ADMIN_IDS, API
    TOKEN, WEBAPP_URL, ADMIN_IDS = cfg.bot_token, cfg.webapp_url, set(cfg.moderator_ids)
    API = "https://api.telegram.org/bot%s/" % TOKEN
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    real_ocr = (lambda data: gemini.read_photo(cfg, data, "image/jpeg")) if gemini.available(cfg) else None
    real_stt = (lambda data: gemini.read_voice(cfg, data, "audio/ogg")) if gemini.available(cfg) else None
    me = call("getMe")
    log("bot @%s started; mini app url: %s; photo and voice reading: %s" % (me["username"], WEBAPP_URL or "(not set)", "on" if real_ocr else "off (no Gemini key)"))
    call("deleteWebhook", drop_pending_updates=False)    # long polling and webhooks are mutually exclusive
    for lc, table in CMDS.items():                # global command menu (users who chose a language get their own, see apply_language)
        if lc == "uz-cyrl":
            continue
        kw = {} if lc == "uz" else {"language_code": lc}
        call("setMyCommands", commands=[{"command": c, "description": d} for c, d in table], **kw)
    for aid in ADMIN_IDS:                         # the extra /admin command is visible to allow-listed admins only
        try:
            call("setMyCommands", commands=[{"command": "start", "description": "Start"}, {"command": "admin", "description": "Moderator"},
                                            {"command": "id", "description": "Telegram ID"}], scope={"type": "chat", "chat_id": aid})
        except Exception as e:
            log("could not set admin commands for %s: %s" % (aid, type(e).__name__))
    if WEBAPP_URL.startswith("https://"):
        call("setChatMenuButton", menu_button={"type": "web_app", "text": "Xarita", "web_app": {"url": WEBAPP_URL}})
    cfg.db_pool_max = min(cfg.db_pool_max, 8)             # the bot needs a handful of connections: 4 handlers, the notifier, the poller
    store = app.Store(cfg, name="masjiduz-bot")
    lock = psycopg.connect(cfg.conninfo(), autocommit=True)       # the bot runs as ONE replica: a second one would fight over getUpdates
    if not lock.execute("SELECT pg_try_advisory_lock(727004)").fetchone()[0]:
        raise RuntimeError("another bot instance is already running (only one replica is allowed)")
    bot = Bot(call, store, real_ocr, download, WEBAPP_URL, ADMIN_IDS, real_stt)
    notifier = Notifier(call, bot.db, WEBAPP_URL)

    def notify_loop():
        while True:
            try:
                notifier.tick()
            except Exception as e:
                log("notifier error: %s" % type(e).__name__)
            time.sleep(30)
    threading.Thread(target=notify_loop, daemon=True).start()
    offset, backoff = None, 1
    pool = ThreadPoolExecutor(max_workers=4)             # a slow photo (OCR) must not block everybody else
    stopping = threading.Event()
    def on_stop(*a):
        stopping.set()                                             # finish the current poll and the running handlers, then exit 0 ...
        t = threading.Timer(8.0, lambda: os._exit(0))              # ... but never later than 8 s (a slow reading must not hold the shutdown)
        t.daemon = True
        t.start()
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    while not stopping.is_set():
        try:
            for u in call("getUpdates", offset=offset, timeout=5, allowed_updates=["message", "callback_query"]):
                offset = u["update_id"] + 1
                pool.submit(bot.handle, u)
            bot.purge()
            backoff = 1
        except Exception as e:                             # network blips, rate limits: retry with backoff
            log("poll error: %s" % type(e).__name__)
            stopping.wait(backoff)
            backoff = min(backoff * 2, 60)
    log("SIGTERM: waiting for running handlers")
    pool.shutdown(wait=True)
    try:
        call("getUpdates", offset=offset, timeout=0)       # confirm what was handled, so the next start does not deliver it again
    except Exception as e:  # noqa
        log("could not confirm the last updates: %s" % type(e).__name__)
    store.close()
    return 0
