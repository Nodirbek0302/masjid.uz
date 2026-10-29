"""Settings come from environment variables only (TIMUS_ENV, TIMUS_APP_MASJIDUZ_*). A `.env` file is read in `dev` only.

Secrets have no default: in staging/prod a missing one stops the process with one log line naming the variable (never its value).
"""
import ipaddress
import os
import re
from pathlib import Path

import version

ROOT = Path(__file__).resolve().parent.parent
P = "TIMUS_APP_MASJIDUZ_"
ENVS = ("dev", "staging", "prod")
DEFAULT_PROXIES = "127.0.0.0/8,::1/128,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16"


class ConfigError(Exception):
    pass


def load_dotenv(path=ROOT / ".env"):
    """Minimal .env loader (KEY=VALUE) for local development. Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            if v and v[0] in "'\"":
                v = v.strip(v[0])
            else:
                v = re.split(r"(?:^|\s+)#", v, 1)[0].strip()   # a comment after an unquoted value (or instead of one)
            os.environ.setdefault(k.strip(), v)


def _int(name, default, lo=None, hi=None):
    raw = os.environ.get(name, "")
    try:
        n = int(raw) if raw.strip() else default
    except ValueError:
        raise ConfigError("%s must be a whole number" % name) from None
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise ConfigError("%s is out of range" % name)
    return n


def _float(name, default):
    raw = os.environ.get(name, "")
    try:
        return float(raw) if raw.strip() else default
    except ValueError:
        raise ConfigError("%s must be a number" % name) from None


def _models(spec):
    out = []
    for part in spec.split(","):
        bits = part.strip().split(":")
        if len(bits) == 3 and bits[0] and bits[1].isdigit() and bits[2].isdigit():
            out.append((bits[0], int(bits[1]), int(bits[2])))
        elif part.strip():
            raise ConfigError("a model list entry must look like name:requestsPerMinute:requestsPerDay")
    return out


class Config:
    def __init__(self, role="serve"):
        self.env = os.environ.get("TIMUS_ENV", "dev").strip() or "dev"
        if self.env not in ENVS:
            raise ConfigError("TIMUS_ENV must be dev, staging or prod")
        if self.env == "dev":
            load_dotenv()
        e = lambda name, default="": os.environ.get(P + name, default).strip()     # noqa: E731
        self.role = role
        self.warnings = []
        self.version = version.VERSION
        self.commit = e("COMMIT", "unknown") or "unknown"

        # -- database
        self.db = {"host": e("DB_HOST", "127.0.0.1"), "port": _int(P + "DB_PORT", 5432, 1, 65535), "dbname": e("DB_NAME", "masjid"),
                   "user": e("DB_USER", "masjid"), "password": e("DB_PASSWORD") or None}
        self.db_pool_max = _int(P + "DB_POOL_MAX", 10, 1, 100)          # connections of the whole container (split between the workers)
        self.web_concurrency = _int(P + "WEB_CONCURRENCY", 4, 1, 16)      # serve worker processes sharing one socket
        self.worker_fd = _int(P + "WORKER_FD", 0, 0, 65535)               # set by the leader process for its workers (the inherited socket); never by hand

        # -- secrets
        self.bot_token = e("TELEGRAM_BOT_TOKEN")
        self.actor_salt = e("ACTOR_SALT")
        self.admin_token = e("ADMIN_TOKEN")
        self.gemini_keys = [k.strip() for k in e("GEMINI_API_KEY").split(",") if k.strip()]    # optional; several keys (separate projects) add their limits up
        self.gemini_key = self.gemini_keys[0] if self.gemini_keys else ""
        need = {"serve": ("DB_PASSWORD", "ACTOR_SALT", "TELEGRAM_BOT_TOKEN"), "bot": ("DB_PASSWORD", "TELEGRAM_BOT_TOKEN")}.get(role, ("DB_PASSWORD",))
        if self.env != "dev":
            for name in need:
                if not e(name):
                    raise ConfigError("missing required secret %s%s" % (P, name))
        if not self.actor_salt:
            self.actor_salt = "dev-only-salt"              # only reachable in dev (or for roles that never hash an address)
        if self.admin_token and len(self.admin_token) < 32:
            if self.env == "dev":
                self.warnings.append("%sADMIN_TOKEN is shorter than 32 characters" % P)
            else:
                raise ConfigError("%sADMIN_TOKEN must be at least 32 characters" % P)

        # -- moderators: Telegram ids (signature-verified login); the bearer token is an optional second way in
        self.moderator_ids = {int(x) for x in re.split(r"[,\s]+", e("MODERATOR_IDS")) if x.lstrip("-").isdigit()}
        self.admin_session_s = 24 * 3600                        # auth_date of the signed launch data may be up to 24 h old

        # -- web / http
        self.cors_origins = [o.strip().rstrip("/") for o in e("CORS_ORIGINS").split(",") if o.strip()]
        if "*" in self.cors_origins:
            if self.env == "prod":
                raise ConfigError("%sCORS_ORIGINS=* is not allowed in prod" % P)
            if self.env == "staging":
                self.warnings.append("%sCORS_ORIGINS=* is on" % P)
        self.api_base = e("API_BASE", "/masjiduz/v1")
        self.proxies = [ipaddress.ip_network(x.strip(), strict=False) for x in (e("TRUSTED_PROXIES") or DEFAULT_PROXIES).split(",") if x.strip()]
        self.host = "0.0.0.0" if self.env != "dev" else os.environ.get("HOST", "0.0.0.0")      # the container always listens on 0.0.0.0:8080
        self.port = _int("PORT", 8080, 0, 65535) if self.env == "dev" else 8080
        self.rate_limit = _int(P + "WRITE_RATE_PER_HOUR", 30, 1)

        # -- application
        self.dup_radius_m = _float(P + "DUPLICATE_RADIUS_M", 50.0)
        self.report_threshold = _int(P + "REPORT_THRESHOLD", 3, 1)
        self.show_tile_mosques = e("SHOW_TILE_MOSQUES", "1") == "1"
        self.tile_style_url = e("TILE_STYLE_URL", "https://tiles.openfreemap.org/styles/liberty")
        self.bot_username = e("BOT_USERNAME").lstrip("@")
        self.webapp_url = e("WEBAPP_URL")
        # model:requests-per-minute:requests-per-day, in the order they are tried (the limits of the free tier; edit when Google changes them)
        self.gemini_models = _models(e("GEMINI_MODELS", "gemini-3.5-flash-lite:15:500,gemini-3.1-flash-lite:15:500,gemini-3.6-flash:5:20"))
        self.max_photo_bytes = _int(P + "MAX_PHOTO_BYTES", 5 * 1024 * 1024, 1024)
        self.max_voice_bytes = _int(P + "MAX_VOICE_BYTES", 2 * 1024 * 1024, 1024)
        self.ai_daily_limit = _int(P + "AI_DAILY_LIMIT", 300, 1)          # photo/voice readings per day for everybody together (cache hits are free)
        self.overpass_url = e("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
        self.prayer = {"fajr_angle": _float(P + "PRAYER_FAJR_ANGLE", 15.0), "isha_angle": _float(P + "PRAYER_ISHA_ANGLE", 15.0),
                       "asr_factor": _int(P + "PRAYER_ASR_FACTOR", 2, 1, 2),
                       "dhuhr_offset_min": _int(P + "PRAYER_DHUHR_OFFSET_MIN", 5)}       # Asr: 1 = Shafi'i, 2 = Hanafi

    def conninfo(self, timeout_s=None):
        from psycopg.conninfo import make_conninfo
        return make_conninfo(**{k: v for k, v in self.db.items() if v is not None}, connect_timeout=timeout_s or 10)
