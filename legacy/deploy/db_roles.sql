-- Run once as the postgres superuser. Migrations run as the owner role; the app uses the limited role.
CREATE ROLE masjid_owner LOGIN PASSWORD 'CHANGE_ME_OWNER';
CREATE ROLE masjid_app   LOGIN PASSWORD 'CHANGE_ME_APP';
CREATE DATABASE masjid OWNER masjid_owner;
\c masjid
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO masjid_app;
-- After the first start (tables exist), run:
--   GRANT SELECT, INSERT, UPDATE ON mosques, reports TO masjid_app;
--   GRANT SELECT, INSERT ON mosque_history TO masjid_app;      -- no UPDATE/DELETE: audit log is append-only
--   GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO masjid_app;
-- The app currently runs migrations at start-up, so set DATABASE_URL to masjid_owner for the first run,
-- then switch it to masjid_app.
