-- Community mosque database (PostgreSQL 14+). Third-party map data is NEVER stored here.
CREATE TABLE mosques (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
  lon double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),
  name text CHECK (char_length(name) <= 200),
  address text CHECK (char_length(address) <= 300),
  prayer_times jsonb NOT NULL DEFAULT '{}'::jsonb,      -- fajr,dhuhr,asr,maghrib,isha,jumuah = "HH:MM"
  source text NOT NULL,                                  -- e.g. 'community-app'
  origin text NOT NULL CHECK (origin IN ('community','imported')),
  status text NOT NULL CHECK (status IN ('unverified','reported','verified','archived')),
  verification_note text,                                -- evidence, required for 'verified'
  version integer NOT NULL DEFAULT 1,
  creator_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'verified' OR verification_note IS NOT NULL)
);
CREATE INDEX idx_mosques_geo ON mosques (lat, lon) WHERE status <> 'archived';

-- Append-only audit log.
CREATE TABLE mosque_history (
  seq bigserial PRIMARY KEY,
  mosque_id uuid NOT NULL REFERENCES mosques(id),
  version integer NOT NULL,
  action text NOT NULL,                                  -- create|edit|report_threshold|archive|restore|verify|dismiss
  actor text,
  at timestamptz NOT NULL DEFAULT now(),
  changes jsonb NOT NULL,                                -- {field: [old, new]}
  note text
);
CREATE INDEX idx_history_mosque ON mosque_history (mosque_id, seq);

CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% on % is not allowed (append-only / archive instead)', TG_OP, TG_TABLE_NAME; END $$;
CREATE TRIGGER history_append_only BEFORE UPDATE OR DELETE ON mosque_history
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER history_no_truncate BEFORE TRUNCATE ON mosque_history
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER mosques_no_delete BEFORE DELETE ON mosques
  FOR EACH ROW EXECUTE FUNCTION forbid_change();

CREATE TABLE reports (
  id bigserial PRIMARY KEY,
  mosque_id uuid NOT NULL REFERENCES mosques(id),
  kind text NOT NULL CHECK (kind IN ('incorrect','duplicate','relocated','closed','other')),
  reason text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 1000),
  reporter_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolution text CHECK (resolution IN ('accepted','rejected')),   -- NULL = open
  resolved_by text,
  resolved_at timestamptz
);
CREATE INDEX idx_reports_mosque ON reports (mosque_id, kind);
-- one open report per reporter/mosque/kind
CREATE UNIQUE INDEX uq_open_report ON reports (mosque_id, kind, reporter_id)
  WHERE resolution IS NULL AND reporter_id IS NOT NULL;
