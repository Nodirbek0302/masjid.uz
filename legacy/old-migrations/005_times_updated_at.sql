-- When the congregation prayer times of a mosque were last set or changed (NULL = never entered).
ALTER TABLE mosques ADD COLUMN times_updated_at timestamptz;
UPDATE mosques m SET times_updated_at = (
  SELECT max(h.at) FROM mosque_history h
  WHERE h.mosque_id = m.id AND h.changes ? 'prayer_times' AND jsonb_typeof(h.changes->'prayer_times'->1) = 'object'
    AND h.changes->'prayer_times'->1 <> '{}'::jsonb)
WHERE m.prayer_times <> '{}'::jsonb;
