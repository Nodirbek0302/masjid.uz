-- The language is now chosen by the user in the bot (first /start) instead of being guessed from Telegram's own language.
-- lang_set=false means "not asked yet". The same choice is used by the Mini App (/api/me).
ALTER TABLE bot_users ADD COLUMN lang_set boolean NOT NULL DEFAULT false;
