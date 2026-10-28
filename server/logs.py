"""Logging in the TIMUS format, one line per event on stdout:

    2026-10-28T07:10:00.123Z INFO  [rid=<uuid>] [masjiduz] <logger> - <message>

The request id of the request being handled is attached to every line written while it runs. Never log tokens, Authorization,
Telegram initData, full IP addresses or free text typed by users; run any external input that must appear through `mask()`.
"""
import logging
import re
import sys
import threading
from datetime import datetime, timezone

_local = threading.local()


def set_rid(rid):
    _local.rid = rid


def get_rid():
    return getattr(_local, "rid", None) or "-"


class _Format(logging.Formatter):
    def format(self, record):
        t = datetime.fromtimestamp(record.created, timezone.utc)
        head = "%s.%03dZ %-5s [rid=%s] [masjiduz] %s - " % (t.strftime("%Y-%m-%dT%H:%M:%S"), record.msecs, record.levelname, get_rid(), record.name)
        text = record.getMessage().replace("\r", " ").replace("\n", " ")        # one line per event ...
        out = head + text
        if record.exc_info:                                                     # ... except the stack trace of an unhandled error
            out += "\n" + "\n".join(mask(line, 400) for line in self.formatException(record.exc_info).split("\n"))
        return out


def setup(level=logging.INFO):
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_Format())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)


_SECRETS = re.compile(r"(?i)(bearer\s+)\S+|(password|token|secret|hash|key)=[^\s&]+|\bbot\d+:[\w-]+|\b\d{1,3}(?:\.\d{1,3}){3}\b|[0-9a-f:]{2,}:[0-9a-f:]{2,}")


def mask(value, limit=80):
    """Safe form of external text for a log line: secrets and addresses replaced, control characters removed, shortened."""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    s = _SECRETS.sub(lambda m: (m.group(1) or (m.group(2) + "=" if m.group(2) else "")) + "***", s)
    return s[:limit] + ("..." if len(s) > limit else "")
