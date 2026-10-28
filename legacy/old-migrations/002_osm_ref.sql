-- Imported records from OpenStreetMap (ODbL). osm_ref like 'node/123' keeps imports idempotent.
ALTER TABLE mosques ADD COLUMN osm_ref text UNIQUE;
