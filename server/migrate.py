"""Migration runner: `server/migrations/V0001__name.sql`, `V0002__...` applied in order into schema `masjiduz`.

* history table `masjiduz.schema_history` (version, name, checksum, applied_at) lives inside the schema;
* one transaction per file; idempotent (a second run does nothing);
* refuses to run when the checksum of an already applied file changed (released files are never edited);
* `check()` is used by `serve`: it exits with a clear message while migrations are pending (serve never migrates).
"""
import hashlib
import logging
import re
from pathlib import Path

import psycopg

SCHEMA = "masjiduz"
DIR = Path(__file__).with_name("migrations")
LOCK = 727001
log = logging.getLogger("migrate")


class MigrationError(Exception):
    pass


def files():
    out = []
    for f in sorted(DIR.glob("V*.sql")):
        m = re.fullmatch(r"V(\d{4})__([\w-]+)\.sql", f.name)
        if not m:
            raise MigrationError("bad migration file name: " + f.name)
        out.append((int(m.group(1)), f.name, hashlib.sha256(f.read_bytes()).hexdigest(), f))
    if [v for v, *_ in out] != list(range(1, len(out) + 1)):
        raise MigrationError("migration numbers must run 1, 2, 3 ... without gaps")
    return out


def _connect(cfg):
    return psycopg.connect(cfg.conninfo(), autocommit=True, options="-c search_path=%s -c application_name=masjiduz-migrate" % SCHEMA)


def _applied(c):
    if not c.execute("SELECT to_regclass(%s)", (SCHEMA + ".schema_history",)).fetchone()[0]:
        return {}
    return {r[0]: (r[1], r[2]) for r in c.execute("SELECT version, name, checksum FROM schema_history")}


def migrate(cfg):
    """Applies what is pending; returns the list of applied file names."""
    wanted = files()
    done_now = []
    with _connect(cfg) as c:
        if not c.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (SCHEMA,)).fetchone():
            c.execute("CREATE SCHEMA " + SCHEMA)           # on TIMUS the schema already exists (owned by the app role)
        c.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
        try:
            c.execute("CREATE TABLE IF NOT EXISTS schema_history (version integer PRIMARY KEY, name text NOT NULL, "
                      "checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
            done = _applied(c)
            for version, name, checksum, f in wanted:
                if version in done:
                    if done[version][1] != checksum:
                        raise MigrationError("%s was changed after it was applied (checksum differs); never edit a released migration" % name)
                    continue
                with c.transaction():
                    c.execute(f.read_text())
                    c.execute("INSERT INTO schema_history(version, name, checksum) VALUES (%s,%s,%s)", (version, name, checksum))
                log.info("applied migration %s", name)
                done_now.append(name)
        finally:
            c.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
    return done_now


def pending(cfg):
    """Names of migration files not applied yet (also raises when an applied one changed)."""
    wanted = files()
    with _connect(cfg) as c:
        done = _applied(c)
    out = []
    for version, name, checksum, _ in wanted:
        if version not in done:
            out.append(name)
        elif done[version][1] != checksum:
            raise MigrationError("%s was changed after it was applied" % name)
    return out
