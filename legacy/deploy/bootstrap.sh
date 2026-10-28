#!/usr/bin/env bash
# Yangi (Ubuntu/Debian) serverda bir buyruq bilan hamma narsani ko'taradi. Qayta ishga tushirish xavfsiz (idempotent).
#
#   1) loyiha papkasini serverga yuboring (git clone yoki scp/rsync), .env.example dan .env yarating va to'ldiring
#   2) ./deploy/bootstrap.sh
#
# Server qayta yonganda konteynerlar o'zi qaytib ko'tariladi: docker xizmati yoqilgan va compose'da
# `restart: unless-stopped` bor. Hech qanday qo'shimcha systemd fayl shart emas.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

[ -f .env ] || { echo "✗ .env yo'q: cp .env.example .env  va to'ldiring (docs/DEPLOY-DOCKER.md)"; exit 1; }
chmod 600 .env
get() { sed -n "s/^$1=//p" .env | tail -n1 | sed -e "s/^['\"]//" -e "s/['\"]\$//" ; }

say ".env tekshiruvi"
pw="$(get POSTGRES_PASSWORD)"; adm="$(get ADMIN_TG_IDS)"; prof="$(get COMPOSE_PROFILES)"
[ -n "$pw" ] && [ "$pw" != "masjid" ] || { echo "✗ POSTGRES_PASSWORD bering (standart 'masjid' ishlatilmaydi): openssl rand -hex 24"; exit 1; }
[ -n "$adm" ] || { echo "✗ ADMIN_TG_IDS bering: moderator Telegram ID'lari (botga /id yozing), vergul bilan"; exit 1; }
[ -n "$(get TELEGRAM_BOT_TOKEN)" ] || echo "! TELEGRAM_BOT_TOKEN bo'sh: moderator paneli ishlamaydi (Telegram imzosini tekshirib bo'lmaydi)."
case ",$prof," in
  *,https,*|*,cloudflare,*) ;;
  *) echo "! COMPOSE_PROFILES da https yoki cloudflare yo'q: sayt faqat serverning 127.0.0.1:8080 manzilida ochiladi." ;;
esac
case ",$prof," in *,https,*) [ -n "$(get DOMAIN)" ] || { echo "✗ https profili uchun DOMAIN bering"; exit 1; } ;; esac
case ",$prof," in *,osm,*) [ -n "$(get OSM_CONTACT)" ] || echo "! OSM_CONTACT bo'sh: Overpass'ga aloqa manzilisiz murojaat qilinadi (email yozish ahloqiy talab)." ;; esac
case ",$prof," in *,bot,*) [ -n "$(get TELEGRAM_BOT_TOKEN)" ] || { echo "✗ bot profili uchun TELEGRAM_BOT_TOKEN bering"; exit 1; } ;; esac

if ! command -v docker >/dev/null 2>&1; then
  say "Docker o'rnatilmoqda"
  curl -fsSL https://get.docker.com | $SUDO sh
fi
$SUDO systemctl enable --now docker >/dev/null 2>&1 || true     # server qayta yonganda docker o'zi ko'tariladi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"

# Kichik serverda (1GB) build paytida RAM yetmay qolmasligi uchun swap.
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mb" -lt 1800 ] && [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  say "RAM ${mem_mb}MB va swap yo'q: 1GB swap yaratilmoqda"
  $SUDO fallocate -l 1G /swapfile && $SUDO chmod 600 /swapfile && $SUDO mkswap /swapfile >/dev/null && $SUDO swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
fi

# Ixtiyoriy firewall: ENABLE_UFW=1 ./deploy/bootstrap.sh  (avval SSH ruxsat beriladi, keyin yoqiladi)
if [ "${ENABLE_UFW:-0}" = "1" ] && command -v ufw >/dev/null 2>&1; then
  say "ufw: 22, 80, 443 ochiladi, qolgani yopiladi"
  $SUDO ufw allow OpenSSH >/dev/null; $SUDO ufw allow 80/tcp >/dev/null; $SUDO ufw allow 443/tcp >/dev/null
  $SUDO ufw --force enable >/dev/null
fi

say "Konteynerlar qurilmoqda va ko'tarilmoqda"
$DOCKER compose up -d --build --remove-orphans

say "web sog'lomligi kutilmoqda"
ok=0
for _ in $(seq 1 45); do
  if $DOCKER compose exec -T web curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "$ok" != "1" ]; then echo "✗ web javob bermadi:"; $DOCKER compose logs --tail=60 web; exit 1; fi
echo "✓ web sog'lom"

# Kunlik zaxira (03:15), 14 kun saqlanadi. O'chirish: sudo rm /etc/cron.d/masjid-uz-backup
if [ "${BACKUP_CRON:-1}" = "1" ] && [ -d /etc/cron.d ]; then
  echo "15 3 * * * root cd $(pwd) && ./deploy/backup-docker.sh >> backups/backup.log 2>&1" | $SUDO tee /etc/cron.d/masjid-uz-backup >/dev/null
  $SUDO chmod 644 /etc/cron.d/masjid-uz-backup
  echo "✓ kunlik zaxira o'rnatildi (/etc/cron.d/masjid-uz-backup)"
fi

$DOCKER image prune -f >/dev/null
$DOCKER compose ps
domain="$(get DOMAIN)"
case "$domain" in
  http://*|https://*) url="$domain/" ;;
  "")                 url="http://127.0.0.1:8080/ (SSH tunnel: ssh -L 8080:127.0.0.1:8080 user@server)" ;;
  *)                  url="https://$domain/" ;;
esac
echo; echo "Tayyor. $url"
