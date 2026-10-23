#!/bin/sh
# Docker'dagi Postgres'ning zaxirasi (custom format). Qo'lda:  ./deploy/backup-docker.sh   |  cron: bootstrap.sh o'rnatadi.
# Tiklash: docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean' < backups/masjid-XXXX.dump
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
f="backups/masjid-$(date +%F-%H%M).dump"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$f"
[ -s "$f" ] || { rm -f "$f"; echo "✗ zaxira bo'sh chiqdi" >&2; exit 1; }
find backups -name 'masjid-*.dump' -mtime +14 -delete        # 14 kun saqlanadi
echo "✓ $f ($(du -h "$f" | cut -f1))"
