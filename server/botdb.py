"""Database access for the Telegram bot's own features (reminders, subscriptions). Personal data: only the Telegram id and the choices
the user made; deleting a user (/stop, or when Telegram says the bot was blocked) removes every row."""
MAX_SUBS = 20


class BotDB:
    def __init__(self, store):
        self.pool = store.pool

    # ---- users / reminder settings
    def user(self, tid):
        with self.pool.connection() as c:
            return c.execute("SELECT * FROM bot_users WHERE telegram_id=%s", (tid,)).fetchone()

    def set_user(self, tid, **fields):
        allowed = {"region", "daily", "before_prayer", "lang", "lang_set"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        with self.pool.connection() as c:
            c.execute("INSERT INTO bot_users(telegram_id%s) VALUES (%%s%s) ON CONFLICT (telegram_id) DO UPDATE SET %s, updated_at=now()" % (
                "".join(", " + k for k in fields), ", %s" * len(fields),
                ", ".join("%s=EXCLUDED.%s" % (k, k) for k in fields) or "lang=bot_users.lang"), (tid, *fields.values()))
        return self.user(tid)

    def lang_of(self, tid):
        """The language the user chose in the bot, or None when they have not been asked yet."""
        u = self.user(tid)
        return u["lang"] if u and u.get("lang_set") else None

    def set_lang(self, tid, lang):
        return self.set_user(tid, lang=lang, lang_set=True)

    def reminder_users(self):
        with self.pool.connection() as c:
            return c.execute("SELECT * FROM bot_users WHERE region IS NOT NULL AND (daily OR before_prayer)").fetchall()

    def forget(self, tid):
        with self.pool.connection() as c:
            c.execute("DELETE FROM bot_subs WHERE telegram_id=%s", (tid,))
            c.execute("DELETE FROM bot_users WHERE telegram_id=%s", (tid,))
            c.execute("DELETE FROM bot_sent WHERE telegram_id=%s", (tid,))

    # ---- subscriptions
    def subs(self, tid):
        with self.pool.connection() as c:
            return [dict(r, mosque_id=str(r["mosque_id"])) for r in c.execute(
                "SELECT s.mosque_id, m.name FROM bot_subs s JOIN mosques m ON m.id=s.mosque_id "
                "WHERE s.telegram_id=%s ORDER BY s.created_at", (tid,))]

    def is_subscribed(self, tid, mosque_id):
        with self.pool.connection() as c:
            return c.execute("SELECT 1 FROM bot_subs WHERE telegram_id=%s AND mosque_id=%s", (tid, mosque_id)).fetchone() is not None

    def subscribe(self, tid, mosque_id):
        """-> "ok" | "already" | "limit" (the race between two taps is settled by the primary key)."""
        with self.pool.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtext('botsub:' || %s))", (str(tid),))     # one user's taps are settled one by one
            if c.execute("SELECT 1 FROM bot_subs WHERE telegram_id=%s AND mosque_id=%s", (tid, mosque_id)).fetchone():
                return "already"
            if c.execute("SELECT count(*) AS n FROM bot_subs WHERE telegram_id=%s", (tid,)).fetchone()["n"] >= MAX_SUBS:
                return "limit"
            c.execute("INSERT INTO bot_subs(telegram_id, mosque_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (tid, mosque_id))
        return "ok"

    def unsubscribe(self, tid, mosque_id):
        with self.pool.connection() as c:
            c.execute("DELETE FROM bot_subs WHERE telegram_id=%s AND mosque_id=%s", (tid, mosque_id))

    def due_changes(self):
        """Subscriptions whose mosque's congregation times changed after the last announcement, with who made that change."""
        with self.pool.connection() as c:
            rows = c.execute(
                "SELECT s.telegram_id, s.mosque_id, m.name, m.prayer_times, m.times_updated_at, "
                "(SELECT h.actor FROM mosque_history h WHERE h.mosque_id=m.id AND h.changes ? 'prayer_times' ORDER BY h.seq DESC LIMIT 1) AS editor "
                "FROM bot_subs s JOIN mosques m ON m.id=s.mosque_id "
                "WHERE m.status<>'archived' AND m.times_updated_at IS NOT NULL AND m.times_updated_at > s.last_notified").fetchall()
        return [dict(r, mosque_id=str(r["mosque_id"])) for r in rows]

    def mark_notified(self, tid, mosque_id, ts):
        with self.pool.connection() as c:
            c.execute("UPDATE bot_subs SET last_notified=%s WHERE telegram_id=%s AND mosque_id=%s AND last_notified < %s", (ts, tid, mosque_id, ts))

    # ---- reminder ledger
    def claim(self, tid, key, day):
        """True exactly once per (user, key, day): the insert is the lock, so two threads or a restart cannot send twice."""
        with self.pool.connection() as c:
            return c.execute("INSERT INTO bot_sent(telegram_id, key, day) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1", (tid, key, day)).fetchone() is not None

    def purge_sent(self):
        with self.pool.connection() as c:
            c.execute("DELETE FROM bot_sent WHERE day < current_date - 3")
