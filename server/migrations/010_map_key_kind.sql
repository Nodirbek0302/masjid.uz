-- Yandex issues different keys for different products: "JavaScript API" keys work on the web, "MapKit Mobile SDK" keys in the app.
-- Each key in the pool says which one it is, and a client is only ever handed a key of its own kind.
ALTER TABLE map_keys ADD COLUMN kind text NOT NULL DEFAULT 'web' CHECK (kind IN ('web', 'app'));
