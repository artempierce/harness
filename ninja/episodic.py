"""Layer 4: memory that survives a restart.

Layer 1's working memory is a Python list that dies with the process. This
writes each turn to SQLite as it completes, and reads the recent ones back
before the next conversation starts.

What goes in is what was *said* — the tool calls stay in `traces`. Replaying a
tool_use block without its matching result is an invalid request, and a file
you read yesterday is stale today.
"""

import uuid
from datetime import UTC, datetime

from ninja.trace import connect

# How many past messages to put back into working memory at startup. Every one
# of these is re-uploaded on every request for the rest of the session, so this
# number is a direct, permanent cost per turn.
RECALL = 6


def new_session() -> str:
    return uuid.uuid4().hex[:12]


def save(session_id: str, role: str, content: str, trace_id: int | None = None) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO chat_log (session_id, role, content, created_at, trace_id)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            session_id,
            role,
            content,
            datetime.now(UTC).isoformat(timespec="seconds"),
            trace_id,
        ),
    )
    conn.commit()
    conn.close()


def recall(limit: int = RECALL) -> list[dict]:
    """The most recent messages, oldest first, shaped for the messages array."""
    conn = connect()
    rows = conn.execute(
        "SELECT role, content FROM chat_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    # An assistant message cannot lead the array, so drop it if the window
    # happens to start mid-exchange.
    messages = [{"role": role, "content": content} for role, content in reversed(rows)]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def history(limit: int = 50) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT id, session_id, role, content, created_at, trace_id"
        " FROM chat_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    keys = ("id", "session_id", "role", "content", "created_at", "trace_id")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def stats() -> dict:
    conn = connect()
    messages, sessions, first = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT session_id), MIN(created_at) FROM chat_log"
    ).fetchone()
    conn.close()
    return {"messages": messages, "sessions": sessions, "since": first, "recall": RECALL}
