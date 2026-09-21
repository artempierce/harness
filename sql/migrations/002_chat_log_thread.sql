-- Layer 8a. Which conversation a message belongs to.
--
-- Deliberately not session_id, which already exists and means one process run.
-- The Episodic panel has counted distinct session_id as "sessions" since layer
-- 4, and overloading it would quietly change a number the dashboard reports.
-- One run touches several threads; one thread spans many runs.
ALTER TABLE chat_log ADD COLUMN thread TEXT;

-- Everything said before threads existed was said to the default persona, and
-- `WHERE thread = ?` never matches NULL. Without this line every message in an
-- existing database becomes invisible to recall() under any name — which in a
-- layer whose purpose is memory that survives a restart is the whole of it.
UPDATE chat_log SET thread = 'assistant' WHERE thread IS NULL;
