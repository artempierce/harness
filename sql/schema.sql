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
