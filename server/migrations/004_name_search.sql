-- Search key: the name in every script/apostrophe form (see server/translit.py). Filled by the application.
ALTER TABLE mosques ADD COLUMN name_search text;
CREATE INDEX idx_mosques_name_search ON mosques USING btree (name_search text_pattern_ops);
