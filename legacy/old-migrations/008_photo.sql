-- A photo of the mosque taken from the OpenStreetMap tags (image / wikimedia_commons): the thumbnail address on
-- upload.wikimedia.org and the Commons page of the file (the credit link). Nothing is downloaded or stored here.
ALTER TABLE mosques ADD COLUMN photo_url text;
ALTER TABLE mosques ADD COLUMN photo_credit text;
