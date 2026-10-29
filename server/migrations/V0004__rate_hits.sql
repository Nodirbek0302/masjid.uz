-- Request counters of the rate limits (writes, light writes, timetable readings, failed admin logins), shared by every `serve` process
-- of one container (WEB_CONCURRENCY) and by replicas. `key` is a salted hash of the client address or actor id, never the address itself.
-- UNLOGGED: counters only, they may be lost in a crash. Rows older than an hour are deleted as the limiters run.
CREATE UNLOGGED TABLE rate_hits (
  bucket text NOT NULL,
  key    text NOT NULL,
  at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_rate_hits_key ON rate_hits (bucket, key, at);
