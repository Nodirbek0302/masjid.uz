#!/usr/bin/env bash
# Joylashtirishdan keyin tashqaridan tekshiradi (kompyuteringizdan yoki serverdan):
#   ./deploy/verify.sh https://nodir0302.duckdns.org      (argument bo'lmasa lokal: http://127.0.0.1:8080)
set -uo pipefail
base="${1:-http://127.0.0.1:8080}"; base="${base%/}"
pass=0; fail=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
chk() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
code() { curl -s -o /dev/null -m 15 -w '%{http_code}' "$@"; }

echo "→ $base"
chk "health: /api/health 200"                    '[ "$(code "$base/api/health")" = 200 ]'
chk "bosh sahifa xarita kutubxonasini yuklaydi"  'curl -fsS -m 15 "$base/" | grep -qi maplibre'
chk "/api/config style_url va prayer qaytaradi"  'curl -fsS -m 15 "$base/api/config" | grep -q style_url && curl -fsS -m 15 "$base/api/config" | grep -q prayer'
n=$(curl -fsS -m 20 "$base/api/mosques?bbox=55,37,74,46" 2>/dev/null | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["items"]))' 2>/dev/null || echo 0)
if [ "$n" -gt 0 ]; then ok "masjidlar bazada: $n ta"; else bad "bazada masjid yo'q (osm-sync hali ishlamagan bo'lishi mumkin: docker compose logs osm-sync)"; fi
chk "qidiruv ishlaydi (/api/search)"             '[ "$(code "$base/api/search?q=ma")" = 200 ]'
chk "gzip siqish yoqilgan"                       'curl -sI -m 15 -H "Accept-Encoding: gzip" "$base/vendor/maplibre-gl.js" | grep -qi "content-encoding: gzip"'
chk "xavfsizlik sarlavhalari (CSP)"              'curl -sI -m 15 "$base/" | grep -qi content-security-policy'
chk "admin ommaviy manzildan YOPIQ (404 kutiladi)" '[ "$(code -H "X-Forwarded-For: 1.2.3.4" "$base/admin.html")" = 404 ] || [ "$base" != "${base#http://127}" ]'
chk "admin API ommaviy manzildan YOPIQ"          '[ "$(code -H "X-Forwarded-For: 1.2.3.4" "$base/api/admin/overview")" = 404 ] || [ "$base" != "${base#http://127}" ]'
chk "o'chirib bo'lmaydi: DELETE 404/405"         'c=$(code -X DELETE "$base/api/mosques/00000000-0000-0000-0000-000000000000"); [ "$c" = 404 ] || [ "$c" = 405 ]'
case "$base" in
  https://*) chk "HTTPS sertifikati yaroqli"     'curl -fsS -m 15 -o /dev/null "$base/"'
             chk "HSTS sarlavhasi"               'curl -sI -m 15 "$base/" | grep -qi strict-transport-security' ;;
  *) echo "  · HTTPS tekshiruvlari o'tkazib yuborildi (manzil https:// emas)" ;;
esac
echo; echo "natija: $pass o'tdi, $fail yiqildi"; [ "$fail" = 0 ]
