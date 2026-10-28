#!/bin/sh
# Bitta image — bir nechta rol:  web (standart) | bot | osm-sync | import-osm [args] | istalgan buyruq
set -eu

cmd="${1:-web}"
[ "$#" -gt 0 ] && shift

wait_for_db() {
  python - <<'PY'
import os, sys, time
import psycopg
url = os.environ["DATABASE_URL"]
for i in range(60):
    try:
        psycopg.connect(url, connect_timeout=3).close()
        print("postgres tayyor"); sys.exit(0)
    except Exception as e:
        print("postgres kutilmoqda (%d/60): %s" % (i + 1, type(e).__name__)); time.sleep(2)
sys.exit("postgres javob bermadi")
PY
}

case "$cmd" in
  web)        wait_for_db; exec python server/app.py ;;          # migratsiyalar app ishga tushganda qo'llanadi
  bot)        exec python server/bot.py ;;
  osm-sync)   wait_for_db; exec python server/osm_sync.py ;;     # kuniga 1 marta OSM'dan yangilaydi
  import-osm) wait_for_db; exec python server/import_osm.py "$@" ;;
  *)          exec "$cmd" "$@" ;;
esac
