"""Keeps the community database fresh from OpenStreetMap: one polite download per interval (default 24 h).

Runs forever (docker role `osm-sync`). A failed download is retried after an hour, never in a tight loop; the
shared Overpass service is not hammered. Imported rows that nobody has edited follow OSM; edited ones are never touched.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402
import import_osm  # noqa: E402

INTERVAL_H = float(os.environ.get("OSM_SYNC_HOURS", "24"))
RETRY_H = 1.0


def log(msg):
    sys.stderr.write("%s osm-sync: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stderr.flush()


def main():
    app.load_dotenv()
    cfg = app.Config()
    store = app.Store(cfg)
    endpoint = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    while True:
        wait = INTERVAL_H
        try:
            log("downloading from %s" % endpoint)
            data = import_osm.fetch(endpoint)
            n = len(data.get("elements", []))
            if n < 100:                                   # an empty/partial answer must never look like a real sync
                raise RuntimeError("suspiciously small answer (%d elements), ignoring" % n)
            log("elements=%d; syncing" % n)
            import_osm.run(data, cfg, store)
        except Exception as e:                            # network, Overpass busy, parse errors
            log("failed: %s: %s; retry in %.0f h" % (type(e).__name__, e, RETRY_H))
            wait = RETRY_H
        time.sleep(wait * 3600)


if __name__ == "__main__":
    main()
