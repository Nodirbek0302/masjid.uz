-- Which map the clients draw: OpenStreetMap (the default, free) or Yandex (limited by key). Yandex keys form a pool: a client is handed the
-- first key of its kind (web = JavaScript API key, app = MapKit key) that still has room today, and when every key is used up or switched
-- off everybody gets OpenStreetMap again, automatically.
CREATE TABLE map_settings (
    id         int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    provider   text NOT NULL DEFAULT 'osm' CHECK (provider IN ('osm', 'yandex')),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO map_settings DEFAULT VALUES;

CREATE TABLE map_keys (
    id           serial PRIMARY KEY,
    label        text NOT NULL DEFAULT '',
    api_key      text NOT NULL UNIQUE,
    kind         text NOT NULL DEFAULT 'web' CHECK (kind IN ('web', 'app')),
    daily_limit  int NOT NULL DEFAULT 25000 CHECK (daily_limit > 0),     -- distinct users (devices) per day this key may serve
    enabled      boolean NOT NULL DEFAULT true,
    exhausted_on date,                                                   -- Yandex itself refused this key on that day (reported by clients)
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- who was handed which key today (sticky for the day, so a user is counted once); kept 2 days
CREATE TABLE map_key_users (
    day       date NOT NULL,
    client_id text NOT NULL,
    key_id    int NOT NULL REFERENCES map_keys(id) ON DELETE CASCADE,
    PRIMARY KEY (day, client_id)
);
CREATE INDEX map_key_users_key ON map_key_users (day, key_id);

-- clients telling that Yandex refused a key (quota used up / key rejected); enough distinct reports switch the key off for the day
CREATE TABLE map_key_reports (
    day       date NOT NULL,
    key_id    int NOT NULL REFERENCES map_keys(id) ON DELETE CASCADE,
    client_id text NOT NULL,
    PRIMARY KEY (day, key_id, client_id)
);
