-- Layer 10. Which chat_log rows have been distilled, and what they became.
--
-- consolidated is a flag on the row rather than a high-water mark, because
-- consolidation is per thread and a thread's rows are interleaved with the
-- others in id order. A mark would either skip another thread's rows or
-- re-read this one's.
ALTER TABLE chat_log ADD COLUMN consolidated INTEGER NOT NULL DEFAULT 0;

-- One dated sentence per consolidated batch. Not the raw log, which is
-- chat_log and stays that way.
CREATE TABLE episodes (
    id          INTEGER PRIMARY KEY,
    thread      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    happened_on TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
