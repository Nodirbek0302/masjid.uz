"""Telegram bot (long polling, standard library only).

/start and /help reply in Uzbek; when WEBAPP_URL (public HTTPS) is set, they carry a button that opens the Mini App
and the chat menu button is set to it. Telegram requires HTTPS for Mini Apps, so without WEBAPP_URL the bot only
explains that the app is not connected yet. The token is read from the environment/.env and never logged.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402  (load_dotenv)

app.load_dotenv()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_TG_IDS", "").replace(",", " ").split() if x.lstrip("-").isdigit()}
API = "https://api.telegram.org/bot%s/" % TOKEN


def call(method, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(API + method, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError("%s failed: %s" % (method, out.get("description")))
    return out["result"]


def log(msg):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stderr.flush()


TEXTS = {
    "uz": {"hello": "Assalomu alaykum! Bu bepul masjid xaritasi: masjidlarni toping, jamoat namozi vaqtlarini koʻring, "
                    "yangi masjid qoʻshing yoki notoʻgʻri maʼlumot haqida xabar bering.",
           "pending": "(Mini App hali ulanmagan: HTTPS manzil kutilmoqda.)", "open": "🕌 Xaritani ochish"},
    "ru": {"hello": "Ассаламу алайкум! Это бесплатная карта мечетей: находите мечети, смотрите время джамаат-намазов, "
                    "добавляйте новые мечети и сообщайте о неверных данных.",
           "pending": "(Mini App ещё не подключено: ожидается HTTPS-адрес.)", "open": "🕌 Открыть карту"},
    "en": {"hello": "Assalamu alaykum! This is a free mosque map: find mosques, see congregation prayer times, "
                    "add new mosques and report incorrect information.",
           "pending": "(The Mini App is not connected yet: waiting for an HTTPS address.)", "open": "🕌 Open the map"},
}


ADMIN_TEXTS = {
    "uz": {"btn": "🛠 Moderator paneli", "text": "Moderator paneli:", "id": "Sizning Telegram ID: {id}"},
    "ru": {"btn": "🛠 Панель модератора", "text": "Панель модератора:", "id": "Ваш Telegram ID: {id}"},
    "en": {"btn": "🛠 Moderator panel", "text": "Moderator panel:", "id": "Your Telegram ID: {id}"},
}


def pick_lang(code):
    """Telegram language_code -> one of our texts (Uzbek is the default; the Mini App itself also offers Cyrillic)."""
    code = (code or "").lower()
    if code.startswith("ru") or code[:2] in ("kk", "ky", "tg", "tk", "be", "uk"):
        return "ru"
    if code.startswith("en"):
        return "en"
    return "uz"


def reply_start(chat_id, language_code=None):
    tx = TEXTS[pick_lang(language_code)]
    if WEBAPP_URL.startswith("https://"):
        markup = {"inline_keyboard": [[{"text": tx["open"], "web_app": {"url": WEBAPP_URL}}]]}
        call("sendMessage", chat_id=chat_id, text=tx["hello"], reply_markup=markup)
    else:
        call("sendMessage", chat_id=chat_id, text=tx["hello"] + "\n\n" + tx["pending"])


def reply_admin(chat_id, user_id, language_code=None):
    """/admin: the moderator panel button, only for allow-listed Telegram ids. Everyone else gets no reply."""
    if user_id not in ADMIN_IDS or not WEBAPP_URL.startswith("https://"):
        return
    tx = ADMIN_TEXTS[pick_lang(language_code)]
    markup = {"inline_keyboard": [[{"text": tx["btn"], "web_app": {"url": WEBAPP_URL.rstrip("/") + "/admin.html"}}]]}
    call("sendMessage", chat_id=chat_id, text=tx["text"], reply_markup=markup)


def reply_id(chat_id, user_id, language_code=None):
    call("sendMessage", chat_id=chat_id, text=ADMIN_TEXTS[pick_lang(language_code)]["id"].format(id=user_id))


def main():
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN is not set")
    me = call("getMe")
    log("bot @%s started; mini app url: %s" % (me["username"], WEBAPP_URL or "(not set)"))
    call("deleteWebhook", drop_pending_updates=False)    # long polling and webhooks are mutually exclusive
    cmds = {"uz": ("Boshlash", "Yordam"), "ru": ("Начать", "Помощь"), "en": ("Start", "Help")}
    for lc, (d1, d2) in cmds.items():             # command menu in the user's own language; Uzbek is the default
        kw = {} if lc == "uz" else {"language_code": lc}
        call("setMyCommands", commands=[{"command": "start", "description": d1}, {"command": "help", "description": d2}], **kw)
    for aid in ADMIN_IDS:                         # the extra /admin command is visible to allow-listed admins only
        try:
            call("setMyCommands", commands=[{"command": "start", "description": "Start"}, {"command": "admin", "description": "Moderator"},
                                            {"command": "id", "description": "Telegram ID"}], scope={"type": "chat", "chat_id": aid})
        except Exception as e:
            log("could not set admin commands for %s: %s" % (aid, type(e).__name__))
    if WEBAPP_URL.startswith("https://"):
        call("setChatMenuButton", menu_button={"type": "web_app", "text": "Xarita", "web_app": {"url": WEBAPP_URL}})
    offset, backoff = None, 1
    while True:
        try:
            for u in call("getUpdates", offset=offset, timeout=30, allowed_updates=["message"]):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                text = (msg.get("text") or "").strip()
                if msg.get("chat", {}).get("type") != "private":
                    continue
                cmd = text.split("@")[0].split(" ")[0]
                uid, lc = (msg.get("from") or {}).get("id"), (msg.get("from") or {}).get("language_code")
                if cmd in ("/start", "/help"):
                    reply_start(msg["chat"]["id"], lc)
                elif cmd == "/id":
                    reply_id(msg["chat"]["id"], uid, lc)
                elif cmd == "/admin":
                    reply_admin(msg["chat"]["id"], uid, lc)
                else:
                    continue
                log("handled %s" % cmd)
            backoff = 1
        except Exception as e:                             # network blips, rate limits: retry with backoff
            log("poll error: %s" % type(e).__name__)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
