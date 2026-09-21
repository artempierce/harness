-- Layer 7 could not give traces a persona column, because every statement in
-- schema.sql is CREATE TABLE IF NOT EXISTS and a new column there would never
-- reach a database that already existed. The persona went into the events JSON
-- instead, as a stopgap, where nothing can query it.
--
-- This is that stopgap's removal, and the first thing the migration mechanism
-- was built to carry.
ALTER TABLE traces ADD COLUMN persona TEXT;
