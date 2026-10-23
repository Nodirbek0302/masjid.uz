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


def reply_start(chat_id):
    text = ("Assalomu alaykum! Bu bepul masjid xaritasi: masjidlarni toping, jamoat namozi vaqtlarini ko‘ring, "
            "yangi masjid qo‘shing yoki noto‘g‘ri ma’lumot haqida xabar bering.")
    if WEBAPP_URL.startswith("https://"):
        markup = {"inline_keyboard": [[{"text": "🕌 Xaritani ochish", "web_app": {"url": WEBAPP_URL}}]]}
        call("sendMessage", chat_id=chat_id, text=text, reply_markup=markup)
    else:
        call("sendMessage", chat_id=chat_id, text=text + "\n\n(Mini App hali ulanmagan: HTTPS manzil kutilmoqda.)")


def main():
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN is not set")
    me = call("getMe")
    log("bot @%s started; mini app url: %s" % (me["username"], WEBAPP_URL or "(not set)"))
    call("deleteWebhook", drop_pending_updates=False)    # long polling and webhooks are mutually exclusive
    call("setMyCommands", commands=[{"command": "start", "description": "Boshlash"},
                                    {"command": "help", "description": "Yordam"}])
    if WEBAPP_URL.startswith("https://"):
        call("setChatMenuButton", menu_button={"type": "web_app", "text": "Xarita", "web_app": {"url": WEBAPP_URL}})
    offset, backoff = None, 1
    while True:
        try:
            for u in call("getUpdates", offset=offset, timeout=30, allowed_updates=["message"]):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                text = (msg.get("text") or "").strip()
                if msg.get("chat", {}).get("type") == "private" and text.split("@")[0] in ("/start", "/help"):
                    reply_start(msg["chat"]["id"])
                    log("replied to /start in chat %s" % msg["chat"]["id"])
            backoff = 1
        except Exception as e:                             # network blips, rate limits: retry with backoff
            log("poll error: %s" % type(e).__name__)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
