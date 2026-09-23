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
    """A fresh, short random id for one process run."""
    return uuid.uuid4().hex[:12]


def save(
    session_id: str, role: str, content: str, trace_id: int | None, thread: str
) -> None:
    """Write one chat_log row — either the user's message or the reply."""
    conn = connect()
    conn.execute(
        "INSERT INTO chat_log (session_id, role, content, created_at, trace_id, thread)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            session_id,
            role,
            content,
            datetime.now(UTC).isoformat(timespec="seconds"),
            trace_id,
            thread,
        ),
    )
    conn.commit()
    conn.close()


def save_exchange(
    session_id: str, user_text: str, reply: str, trace_id: int | None, thread: str
) -> None:
    """Both halves of one exchange, in one transaction.

    Two separate writes can be torn apart — by a crash between them, or by a
    concurrent turn on the same thread landing its user message in the gap.
    Either leaves the thread reading user, user, assistant, assistant, which
    the Messages API rejects, so every later turn in that thread fails until
    the pair scrolls out of the recall window. It is written to disk, so a
    restart does not clear it.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn = connect()
    conn.executemany(
        "INSERT INTO chat_log (session_id, role, content, created_at, trace_id, thread)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (session_id, "user", user_text, now, trace_id, thread),
            (session_id, "assistant", reply, now, trace_id, thread),
        ],
    )
    conn.commit()
    conn.close()


def recall(thread: str, limit: int = RECALL) -> list[dict]:
    """One thread's most recent messages, oldest first, shaped for the array."""
    conn = connect()
    rows = conn.execute(
        "SELECT role, content FROM chat_log WHERE thread = ?"
        " ORDER BY id DESC LIMIT ?",
        (thread, limit),
    ).fetchall()
    conn.close()
    # Roles have to alternate, starting with user — the API rejects anything
    # else. Dropping only a leading assistant message is not enough: two turns
    # interleaving on one thread leave user, user, assistant, assistant, and
    # every later turn is then refused until the pair scrolls out of the
    # window. Keeping only what alternates makes a torn thread recover on the
    # next turn instead of staying broken.
    messages: list[dict] = []
    for role, content in reversed(rows):
        expected = "user" if len(messages) % 2 == 0 else "assistant"
        if role == expected:
            messages.append({"role": role, "content": content})
    return messages


def current_thread(default: str) -> str:
    """The thread the last message went to.

    Derived rather than stored. A remembered name has a write path that can get
    out of step with the transcript it describes; the last row cannot, because
    the thread and the messages are the same rows.
    """
    conn = connect()
    row = conn.execute(
        "SELECT thread FROM chat_log WHERE thread IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else default


def history(limit: int = 50) -> list[dict]:
    """The most recent chat_log rows across all threads, newest first, as dicts."""
    conn = connect()
    rows = conn.execute(
        "SELECT id, session_id, role, content, created_at, trace_id, thread"
        " FROM chat_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    keys = ("id", "session_id", "role", "content", "created_at", "trace_id", "thread")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def stats() -> dict:
    """Summary counts for the memory panel: total messages, distinct sessions,
    and the earliest timestamp on record."""
    conn = connect()
    messages, sessions, first = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT session_id), MIN(created_at) FROM chat_log"
    ).fetchone()
    conn.close()
    return {"messages": messages, "sessions": sessions, "since": first, "recall": RECALL}
