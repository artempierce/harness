-- Layer 8a. Which conversation a message belongs to.
--
-- Deliberately not session_id, which already exists and means one process run.
-- The Episodic panel has counted distinct session_id as "sessions" since layer
-- 4, and overloading it would quietly change a number the dashboard reports.
-- One run touches several threads; one thread spans many runs.
ALTER TABLE chat_log ADD COLUMN thread TEXT;
