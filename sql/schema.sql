-- Layer 3. One row per turn.
-- A turn is everything between your message and the reply, however many model
-- calls and tool calls that took.

CREATE TABLE IF NOT EXISTS traces (
    id             INTEGER PRIMARY KEY,
    started_at     TEXT    NOT NULL,
    duration_ms    INTEGER NOT NULL,
    user_input     TEXT    NOT NULL,
    reply          TEXT    NOT NULL,
    model_calls    INTEGER NOT NULL,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    cost_usd       REAL    NOT NULL,
    events         TEXT    NOT NULL   -- JSON array, in the order they happened
);

-- Layer 4. What was said, in order. Not how it was done — the tool calls live
-- in `traces`, because replaying a tool_use without its result is invalid, and
-- yesterday's file contents are stale anyway.
CREATE TABLE IF NOT EXISTS chat_log (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL,   -- user | assistant
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    trace_id    INTEGER             -- the turn this came from, if any
);

CREATE INDEX IF NOT EXISTS chat_log_recent ON chat_log (id DESC);
