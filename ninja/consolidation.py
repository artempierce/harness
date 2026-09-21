"""Layer 10: turn what was said into what is known.

Facts only reach the store when the agent decides to `remember` them, so most
of what you say is never kept. Every few exchanges a cheap model reads the ones
not yet read and writes down what is durably true about you, plus one dated
line saying what the conversation was.

It runs inside the turn that triggers it, before the receipt is written, so
its spend lands in that turn's trace instead of nowhere.
"""

import json
import time
from datetime import UTC, datetime

from ninja import personas, semantic
from ninja.trace import Trace, connect

MODEL = personas.DEFAULT_MODEL

CONSOLIDATE_EVERY = 6   # unconsolidated exchanges in one thread
MAX_BATCH = 20          # exchanges per call, so a long backlog drains over many turns
MAX_KNOWN = 50          # existing facts shown to the model
MAX_FACTS = 10
MAX_FACT_CHARS = 300

SYSTEM = """You keep a person's long-term memory. Read the conversation below and
reply with one JSON object and nothing else:

{{"facts": ["..."], "episode": "..."}}

facts: durable things that are true about the person, each one self-contained, one
sentence, third person. Skip anything passing or only useful in this conversation.
Do not restate anything already known.
episode: one sentence saying what this conversation was about.
Either may be empty if nothing is worth keeping.

Already known:
{known}"""


def _due(conn) -> str | None:
    """The thread with the oldest unread exchanges, if any has enough of them."""
    row = conn.execute(
        "SELECT thread FROM chat_log WHERE consolidated = 0 AND role = 'user'"
        " AND thread IS NOT NULL GROUP BY thread HAVING COUNT(*) >= ?"
        " ORDER BY MIN(id) LIMIT 1",
        (CONSOLIDATE_EVERY,),
    ).fetchone()
    return row[0] if row else None


def _parse(text: str) -> tuple[list[str], str] | None:
    """Facts and episode from the reply, or None if the reply is unusable.

    Shape is checked, not just presence: `{"facts": null}` is valid JSON with
    the key there, and dict.get(key, []) hands back None for it.
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("facts"), list):
        return None
    episode = data.get("episode")
    if episode is None:
        episode = ""
    if not isinstance(episode, str):
        return None
    facts = [
        f.strip() for f in data["facts"]
        if isinstance(f, str) and f.strip() and len(f.strip()) <= MAX_FACT_CHARS
    ]
    return facts[:MAX_FACTS], episode.strip()


def _run(client, trace: Trace) -> None:
    conn = connect()
    try:
        thread = _due(conn)
        if thread is None:
            return
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM chat_log"
            " WHERE thread = ? AND consolidated = 0 ORDER BY id LIMIT ?",
            (thread, MAX_BATCH * 2),
        ).fetchall()
        known = "\n".join(f"- {f['content']}" for f in semantic.all_facts(MAX_KNOWN)) or "(nothing)"
        transcript = "\n".join(f"{role}: {content}" for _, role, content, _ in rows)

        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM.format(known=known),
                messages=[{"role": "user", "content": transcript}],
            )
        except Exception as exc:
            trace.consolidation(False, f"call failed: {exc}", 0)
            return
        ms = int((time.perf_counter() - started) * 1000)
        # The call is paid for whatever the reply turns out to be.
        trace.model(MODEL, response, ms, "consolidation")

        said = "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason == "max_tokens":
            # A cut-off reply is a cap set too low, not a model that disagreed.
            trace.consolidation(False, "reply truncated at max_tokens", ms)
            return
        parsed = _parse(said)
        if parsed is None:
            trace.consolidation(False, f"unusable reply {said.strip()[:80]!r}", ms)
            return
        facts, episode = parsed

        # Dated by what was said, not by when it was read: the first run
        # consolidates old history, and stamping it today would misdate it.
        happened_on = rows[-1][3][:10]
        now = datetime.now(UTC).isoformat(timespec="seconds")
        # One transaction for all three writes. A fact stored with its rows
        # still unflagged would be stored again by the retry.
        conn.execute("BEGIN")
        try:
            for fact in facts:
                semantic.remember(fact, source="distilled", conn=conn)
            if episode:
                conn.execute(
                    "INSERT INTO episodes (thread, summary, happened_on, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (thread, episode, happened_on, now),
                )
            conn.executemany(
                "UPDATE chat_log SET consolidated = 1 WHERE id = ?", [(r[0],) for r in rows]
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        trace.consolidation(
            True, f"{thread}: {len(rows) // 2} exchanges, {len(facts)} fact(s)", ms
        )
    finally:
        conn.close()


def run_if_due(client, trace: Trace) -> None:
    """Consolidate one thread if it has enough unread exchanges. Never raises.

    A failed consolidation loses nothing — the rows stay unflagged and the next
    turn retries — so it is never worth failing the turn that happened to
    trigger it.
    """
    try:
        _run(client, trace)
    except Exception as exc:
        trace.consolidation(False, f"failed: {exc}", 0)
