-- Who changed what, and from which kind of client.
-- `actors` holds the mutable personal details (Telegram profile, device/app info, hashed IP). It can be corrected or
-- erased on request without touching the append-only audit log, which only references actors by id and keeps a
-- non-personal device snapshot (`client`) per event.
CREATE TABLE actors (
  actor_id text PRIMARY KEY,                       -- 'tg:<telegram id>' | 'device:<mobile app device id>' | 'client:<browser uuid>'
  kind text NOT NULL CHECK (kind IN ('telegram','mobile','web')),
  telegram_id bigint,                              -- only from signature-verified Telegram initData
  tg_username text,
  tg_first_name text,
  tg_last_name text,
  tg_language text,
  device_id text,                                  -- mobile app installation id
  platform text,                                   -- web | ios | android
  os_version text,
  app_version text,
  device_model text,
  user_agent text,
  ip_hash text,                                    -- salted HMAC, never the raw address
  edits_count integer NOT NULL DEFAULT 0,
  reports_count integer NOT NULL DEFAULT 0,
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_actors_telegram ON actors (telegram_id) WHERE telegram_id IS NOT NULL;
CREATE INDEX idx_actors_last_seen ON actors (last_seen DESC);

-- Non-personal snapshot of the client at the moment of each event: {kind, platform, os_version, app_version, device_model, user_agent}
ALTER TABLE mosque_history ADD COLUMN client jsonb;
ALTER TABLE reports ADD COLUMN client jsonb;

-- Backfill ids already present in the audit log
INSERT INTO actors (actor_id, kind)
SELECT DISTINCT actor,
       CASE WHEN actor LIKE 'tg:%' THEN 'telegram' WHEN actor LIKE 'device:%' THEN 'mobile' ELSE 'web' END
FROM mosque_history
WHERE actor LIKE 'tg:%' OR actor LIKE 'client:%' OR actor LIKE 'device:%'
ON CONFLICT DO NOTHING;
