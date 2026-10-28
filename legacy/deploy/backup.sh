#!/bin/sh
# Daily: crontab -e ->  15 3 * * * /opt/masjid-uz/deploy/backup.sh
# Restore: createdb masjid_restore && pg_restore -d masjid_restore backups/<file>.dump
set -eu
DIR="${BACKUP_DIR:-/var/backups/masjid-uz}"; mkdir -p "$DIR"
pg_dump --format=custom --file="$DIR/masjid-$(date +%F).dump" "$DATABASE_URL"
find "$DIR" -name 'masjid-*.dump' -mtime +30 -delete
# Copy $DIR off the server too (rsync/rclone to storage you control).
