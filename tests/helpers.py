"""Shared test plumbing: test database, Config/server factories, a plain http.client based client, Telegram initData signing.

Nothing here touches the network. The developer's .env is never read (config.load_dotenv is disabled), and every
TIMUS_* variable of the real environment is hidden while a Config is built.
"""
import gzip
import hashlib
import hmac
import http.client
import itertools
import json
import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from urllib.parse import unquote, urlencode, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import psycopg  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import migrate  # noqa: E402

config.real_load_dotenv = config.load_dotenv          # kept for the .env parser tests
config.load_dotenv = lambda *a, **k: None          # the developer's .env must not leak into tests

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://masjid@127.0.0.1:54330/masjid_test")
P = config.P
API = "/masjiduz/v1"
WEB = "/masjiduz/web"
BOT_TOKEN = "123456:TEST-TOKEN"
ADMIN_ID = 424242
SALT = "test-actor-salt"
_RAW_KEYS = ("TIMUS_ENV", "PORT", "HOST")


def _db_env():
    u = urlparse(TEST_URL)
    env = {"DB_HOST": u.hostname or "127.0.0.1", "DB_PORT": str(u.port or 5432), "DB_NAME": (u.path or "/masjid_test").lstrip("/"),
           "DB_USER": unquote(u.username or "masjid")}
    if u.password:
        env["DB_PASSWORD"] = unquote(u.password)
    return env


def base_env():
    env = {"TIMUS_ENV": "dev", "PORT": "0", "HOST": "127.0.0.1", "TELEGRAM_BOT_TOKEN": BOT_TOKEN, "MODERATOR_IDS": str(ADMIN_ID),
           "WRITE_RATE_PER_HOUR": "1000", "REPORT_THRESHOLD": "2", "ACTOR_SALT": SALT}
    env.update(_db_env())
    return env


@contextmanager
def environment(env):
    """Replace every TIMUS_* variable (and PORT/HOST) by `env`: keys in _RAW_KEYS are used as they are, others get the app prefix."""
    full = {(k if k in _RAW_KEYS else P + k): v for k, v in env.items() if v is not None}
    hidden = [k for k in os.environ if k.startswith("TIMUS_") or k in ("PORT", "HOST")]
    saved = {k: os.environ[k] for k in hidden}
    for k in hidden:
        del os.environ[k]
    os.environ.update(full)
    try:
        yield
    finally:
        for k in full:
            os.environ.pop(k, None)
        os.environ.update(saved)


def make_cfg(role="serve", **extra):
    env = base_env()
    env.update(extra)
    with environment(env):
        cfg = config.Config(role)
    return cfg


def ensure_database():
    u = urlparse(TEST_URL)
    name = (u.path or "/masjid_test").lstrip("/")
    try:
        psycopg.connect(TEST_URL).close()
        return
    except psycopg.OperationalError:
        pass
    admin = TEST_URL.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute('CREATE DATABASE "%s"' % name)


_ready = []


def reset_db():
    """Fresh schema `masjiduz` at the latest migration."""
    if not _ready:
        ensure_database()
        _ready.append(1)
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS masjiduz CASCADE")
    return migrate.migrate(make_cfg())


def db(sql, *args):
    """Raw SQL against the test database with the app's search_path."""
    with psycopg.connect(TEST_URL, autocommit=True, options="-c search_path=masjiduz") as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else None


def sign_init_data(user, token=BOT_TOKEN, age=0):
    """Telegram WebApp initData exactly as Telegram signs it (HMAC-SHA256 with key HMAC('WebAppData', bot token))."""
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "AAH", "user": json.dumps(user)}
    check = "\n".join("%s=%s" % (k, pairs[k]) for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class Resp:
    def __init__(self, status, headers, raw):
        self.status, self.headers, self.raw = status, headers, raw

    @property
    def body(self):
        raw = gzip.decompress(self.raw) if self.headers.get("Content-Encoding") == "gzip" else self.raw
        return raw

    @property
    def text(self):
        return self.body.decode("utf-8")

    @property
    def json(self):
        return json.loads(self.body)

    def header(self, name):
        return self.headers.get(name)


def http_request(addr, method, path, body=None, headers=None, timeout=30):
    """One request over http.client: nothing is followed, decoded or normalised behind the test's back."""
    conn = http.client.HTTPConnection(addr[0], addr[1], timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return Resp(r.status, {k: v for k, v in r.getheaders()}, r.read())
    finally:
        conn.close()


def headers_only(addr, method, path, content_length, headers=None):
    """Sends only the request head, claiming a body of `content_length` bytes (never sent) -> Resp. Shows what the server decides before reading."""
    import socket
    h = {"Host": "x", "Content-Length": str(content_length), "Connection": "close"}
    h.update(headers or {})
    with socket.create_connection(addr, timeout=10) as s:
        s.sendall(("%s %s HTTP/1.1\r\n%s\r\n\r\n" % (method, path, "\r\n".join("%s: %s" % kv for kv in h.items()))).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        hdrs = dict(ln.split(": ", 1) for ln in lines[1:] if ": " in ln)
        want = int(hdrs.get("Content-Length", "0"))
        while len(rest) < want:
            chunk = s.recv(65536)
            if not chunk:
                break
            rest += chunk
        return Resp(int(lines[0].split()[1]), hdrs, rest)


_ips = itertools.count(1)


def fresh_ip():
    n = next(_ips)
    return "203.0.%d.%d" % (n // 250 % 250 + 1, n % 250 + 1)      # documentation-range style public addresses, never a trusted proxy


def start(cfg, store=None):
    srv = app.build_server(cfg, store)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, ("127.0.0.1", srv.server_address[1])


def stop(srv):
    srv.shutdown()
    srv.server_close()
    srv.store.close()


class ServerCase(unittest.TestCase):
    """Mixin: `self.serve(**env)` starts a server on a free port (stopped after the test); `self.call` talks to it."""
    srv = addr = None

    def serve(self, **env):
        cfg = make_cfg(**env)
        srv, addr = start(cfg)
        self.addCleanup(stop, srv)
        return srv, addr

    def use(self, **env):
        self.srv, self.addr = self.serve(**env)
        return self.srv

    def req(self, method, path, body=None, headers=None, addr=None):
        return http_request(addr or self.addr, method, path, body, headers)

    def call(self, method, path, body=None, admin=False, client="aaaaaaaa-1", headers=None, ip=None, addr=None, full=False, ctype="application/json"):
        """JSON call on an API path relative to /masjiduz/v1 -> (status, envelope), or the whole Resp with full=True."""
        h = {"X-Client-Id": client}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h["Content-Type"] = ctype
        if admin:
            h["X-Telegram-Init-Data"] = sign_init_data({"id": ADMIN_ID, "first_name": "Owner"})
        if ip is None and path.startswith("/admin/") and not admin:
            ip = fresh_ip()                          # failed admin attempts are counted per address: do not let tests share one
        if ip:
            h["X-Forwarded-For"] = ip
        h.update(headers or {})
        r = self.req(method, API + path, data, h, addr)
        return r if full else (r.status, r.json)

    def add(self, lat, lon, **kw):
        return self.call("POST", "/mosques", dict(lat=lat, lon=lon, **kw))

    def made(self, lat, lon, **kw):
        s, r = self.add(lat, lon, **kw)
        self.assertEqual(s, 201, r)
        return r["data"]
