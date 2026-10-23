# Production deployment (owner-controlled Linux server, Ubuntu/Debian)

1. **Packages:** `apt install postgresql python3-venv caddy`
2. **Code:** copy repo to `/opt/masjid-uz`; `useradd -r masjid`; `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. **Database:** edit passwords in `deploy/db_roles.sql`, run as postgres (`sudo -u postgres psql -f deploy/db_roles.sql`).
   PostgreSQL must listen on localhost only (`listen_addresses='localhost'`), firewall port 5432.
4. **Config:** `cp .env.example /etc/masjid-uz.env; chmod 600; chown masjid` and fill `DATABASE_URL`, `ADMIN_TOKEN` (and bot token).
   Secrets stay on the server; never commit them.
5. **Migrations:** run automatically at start (`server/migrations/*.sql`, tracked in `schema_migrations`, guarded by an advisory lock).
   First start with the owner role, then apply the GRANTs from `db_roles.sql` and switch `DATABASE_URL` to `masjid_app`.
6. **Service:** `cp deploy/masjid-uz.service /etc/systemd/system/; systemctl enable --now masjid-uz`
7. **HTTPS/domain:** set your domain in `deploy/Caddyfile`, install to `/etc/caddy/Caddyfile`, `systemctl reload caddy`. Open ports 80/443 only.
8. **Telegram Mini App:** in @BotFather set the Web App URL to `https://<domain>/`; put the bot token in the env file so
   edits get a verified `tg:<id>` identity.
9. **Backups:** schedule `deploy/backup.sh` (daily); copy dumps off-site; test a restore (`pg_restore`) quarterly.
   Archived records and full history are in the dump; archived records are restorable via the admin API.
10. **Logging/monitoring:** `journalctl -u masjid-uz -f`; Caddy access log; uptime check on `GET /api/health`;
    watch disk and `pg_stat_activity`. 
11. **Database security:** limited app role (no UPDATE/DELETE on history), triggers forbid history edits and mosque deletes,
    localhost-only listener, encrypted backups if stored off-site.

## Moderator panel
Open `https://<domain>/admin.html` and enter `ADMIN_TOKEN` (kept in sessionStorage only, sent as a Bearer header, never in a URL).
Tabs: open reports (grouped by mosque + kind), reported mosques, archive (restore), recent activity. 10 failed logins
per minute per client address lock the panel for the rest of the minute. Set `TRUST_PROXY=1` in the env file behind Caddy,
otherwise every visitor shares Caddy's address in the rate limiters. Consider also restricting `/admin.html` and
`/api/admin/*` by IP in the Caddyfile.

## Moderation (admin API, `Authorization: Bearer $ADMIN_TOKEN`)
- `GET  /api/admin/reports` – open reports grouped by mosque + kind
- `POST /api/admin/mosques/{id}/archive|restore|dismiss|verify` with `{"note": "..."}` (`verify` requires an evidence note)
