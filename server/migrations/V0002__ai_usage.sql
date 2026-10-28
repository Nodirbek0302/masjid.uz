-- Shared request budget of the timetable readers. Only counters: no photo, voice, text or user data is stored here.
-- Both long-running processes (serve and bot) write here, so the per-minute and per-day limits of each model hold for the two together.
CREATE TABLE ai_calls (
  id bigserial PRIMARY KEY,
  slot text NOT NULL,                              -- provider:model#key-number
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_calls_slot_at ON ai_calls (slot, at);

-- A slot the provider refused (limit reached, bad key, outage) is skipped until `until`.
CREATE TABLE ai_cooldown (
  slot text PRIMARY KEY,
  until timestamptz NOT NULL
);
