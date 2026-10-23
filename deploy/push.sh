#!/usr/bin/env bash
# Loyihani serverga yuboradi (kod + compose). Serverdagi .env ga HECH QACHON tegmaydi (faqat yo'q bo'lsa .env.server'dan yaratadi).
#
#   ./deploy/push.sh user@SERVER_IP            # yuborish
#   ./deploy/push.sh user@SERVER_IP --run      # yuborish + ./deploy/bootstrap.sh ni serverda ishga tushirish
set -euo pipefail
cd "$(dirname "$0")/.."
target="${1:?foydalanish: ./deploy/push.sh user@host [--run]}"
dir="${REMOTE_DIR:-masjid-uz}"

echo "→ fayllar yuborilmoqda: $target:~/$dir"
rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '.pgdata/' --exclude '.env' --exclude '.env.*' \
  --exclude 'osm-mosques.json' --exclude 'backups/' --exclude '__pycache__/' --exclude '.ruff_cache/' --exclude '.DS_Store' \
  ./ "$target:~/$dir/"

if ssh "$target" "test -f ~/$dir/.env"; then
  echo "→ serverdagi .env saqlandi (o'zgartirilmadi)"
else
  [ -f .env.server ] || { echo "✗ .env.server yo'q"; exit 1; }
  scp -q .env.server "$target:~/$dir/.env" && ssh "$target" "chmod 600 ~/$dir/.env"
  echo "→ .env serverda .env.server'dan yaratildi"
fi

if [ "${2:-}" = "--run" ]; then
  ssh -t "$target" "cd ~/$dir && ./deploy/bootstrap.sh"
else
  echo "Keyingi: ssh $target 'cd ~/$dir && ./deploy/bootstrap.sh'   (yoki shu skriptni --run bilan qayta ishga tushiring)"
fi
