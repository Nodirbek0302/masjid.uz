"""Container entry point: `python server/cli.py <command>`.

  serve                 HTTP API + web (default). Never migrates; exits while migrations are pending.
  bot                   Telegram bot (one replica only).
  migrate               apply server/migrations/V*.sql into schema masjiduz (idempotent).
  import-osm [--file F] nightly OpenStreetMap sync (one-off container, idempotent, one summary line).
  import --file F.json  one-off data import (docs/IMPORT.md).
Configuration is environment only (see .env.example). A missing secret in staging/prod stops the process with one log line.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logs  # noqa: E402
from config import Config, ConfigError  # noqa: E402
import migrate  # noqa: E402

log = logging.getLogger("cli")


def main(argv):
    logs.setup()
    ap = argparse.ArgumentParser(prog="cli.py")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("bot")
    sub.add_parser("migrate")
    p = sub.add_parser("import-osm")
    p.add_argument("--file")
    p = sub.add_parser("import")
    p.add_argument("--file", required=True)
    a = ap.parse_args(argv)
    cmd = a.cmd or "serve"
    try:
        cfg = Config("serve" if cmd == "serve" else "bot" if cmd == "bot" else "job")
        import app
        if cmd == "serve":
            return app.serve(cfg)
        if cmd == "bot":
            import bot
            return bot.main(cfg)
        if cmd == "migrate":
            done = migrate.migrate(cfg)
            log.info("migrate done: %d applied, schema is up to date", len(done))
            return 0
        if cmd == "import-osm":
            import import_osm
            store = app.Store(cfg, name="masjiduz-import", statement_timeout_ms=60000)
            if a.file:
                with open(a.file, encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = import_osm.download(cfg)
            import_osm.run(data, cfg, store)
            return 0
        if cmd == "import":
            import data_import
            data_import.run(data_import.load(a.file), cfg)
            return 0
    except (ConfigError, migrate.MigrationError) as e:
        log.error("%s", e)
        return 2
    except Exception as e:  # noqa
        log.error("%s failed: %s: %s", cmd, type(e).__name__, logs.mask(e, 200), exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
