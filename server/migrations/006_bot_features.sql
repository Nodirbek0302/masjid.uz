-- Telegram bot: reminder settings, mosque subscriptions, and a ledger so a reminder is never sent twice (also across restarts).
CREATE TABLE bot_users (
  telegram_id bigint PRIMARY KEY,
  region smallint CHECK (region BETWEEN 0 AND 13),         -- index of the region used for the reminders (see server/prayertimes.py)
  daily boolean NOT NULL DEFAULT false,                    -- one summary message each morning
  before_prayer boolean NOT NULL DEFAULT false,            -- a message shortly before every prayer
  lang text NOT NULL DEFAULT 'uz',
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE bot_subs (
  telegram_id bigint NOT NULL,
  mosque_id uuid NOT NULL REFERENCES mosques(id),
  last_notified timestamptz NOT NULL DEFAULT now(),        -- congregation-time changes newer than this are still to be announced
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (telegram_id, mosque_id)
);
CREATE INDEX idx_bot_subs_mosque ON bot_subs (mosque_id);
CREATE TABLE bot_sent (
  telegram_id bigint NOT NULL,
  key text NOT NULL,
  day date NOT NULL,
  PRIMARY KEY (telegram_id, key, day)
);
