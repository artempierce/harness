"""Layer 8a: which conversation does this turn belong to?

One small classifier call before the turn. It reads each persona's
`description` — written as a tool description in disguise, when to use this
rather than what it is — and names the thread the message belongs in.

Sticky by construction. Most turns continue what you were already doing, and a
follow-up like "why would you pick that?" contains nothing that names coaching.
A router without stickiness misfiles constantly; one with it only has to notice
genuine changes of subject.
"""

import time

from ninja import personas
from ninja.trace import Trace

MODEL = personas.DEFAULT_MODEL

SYSTEM = """You file a message into one ongoing conversation.

The conversations:
{cast}

The current conversation is "{current}".

Stay with the current conversation unless the message clearly belongs to a
different one. A follow-up, a short answer, or a question about what was just
said all belong to the current conversation.

Reply with one conversation name and nothing else."""


def route(client, user_input: str, current: str, cast: list, trace: Trace) -> str:
    """Name the thread this message belongs to. Never raises."""
    names = {p.name.lower(): p.name for p in cast}
    described = "\n".join(f"- {p.name}: {p.description}" for p in cast)
    started = time.perf_counter()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=20,
            system=SYSTEM.format(cast=described, current=current),
            messages=[{"role": "user", "content": user_input}],
        )
    except Exception as exc:
        # Routing must never fail a turn. Staying put is always a valid answer.
        trace.route(current, current, f"router call failed: {exc}", MODEL, None, 0)
        return current

    ms = int((time.perf_counter() - started) * 1000)
    said = "".join(b.text for b in response.content if b.type == "text")
    # Models add punctuation and capitals. Do not lose a turn to a full stop.
    cleaned = said.strip().strip(".").strip().lower()
    chosen = names.get(cleaned)
    if chosen is None:
        trace.route(current, current, f"unrecognised answer {said.strip()!r}", MODEL, response, ms)
        return current
    why = "stayed" if chosen == current else f"moved on {said.strip()!r}"
    trace.route(chosen, current, why, MODEL, response, ms)
    return chosen
