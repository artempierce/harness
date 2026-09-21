# Layer 8a: Threads — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each persona its own transcript, and route each turn to the right one automatically, so interrupting a coaching session and coming back works.

**Architecture:** `chat_log` gains a `thread` column via migration `002`. `episodic.recall(thread)` scopes to it and `episodic.current_thread()` derives the active one from the most recent row rather than remembering it. A classifier call before each turn picks the thread, sticky by construction, recorded in the trace with its own token cost. The server's module globals go away — each request reads its thread from the database.

**Tech Stack:** Python 3.12, `anthropic`, `fastapi`, `pytest`, `ruff`, `uv`, SQLite.

**Spec:** `docs/superpowers/specs/2026-09-20-layer-8a-threads-design.md`

## Global Constraints

- `session_id` keeps its meaning — one process run. `thread` is a new, separate column. `episodic.stats()` must still report **sessions**, not threads.
- `sql/schema.sql` is **frozen**. Every schema change is a numbered file in `sql/migrations/`. A column added to `schema.sql` never reaches an existing database, and `tests/test_migrations.py::test_the_frozen_schema_does_not_carry_migrated_columns` will fail if you try.
- The router's tokens and cost go into the turn's totals. An invisible per-turn spend is what makes layer 15's cost ceiling wrong.
- A failed or unparseable route keeps the current thread. Routing must never fail a turn.
- `/persona <name>` (REPL) and `"persona"` on the chat request are **overrides** — the router does not overrule them for that turn.
- Every new trace event kind needs a branch in **both** renderers: `trace.print_one` and `ui/index.html`. A renderer that knows two of three event types is how `undefined(undefined)` shipped for two layers.
- Tests stay free and offline. No `live`-marked tests, never run `pytest -m live`.
- Line length 100. `uv run pytest -q` and `uv run ruff check .` green at every task.

---

## File Structure

| File | Responsibility |
|---|---|
| `sql/migrations/002_chat_log_thread.sql` *(new)* | The `thread` column |
| `ninja/episodic.py` | Thread-scoped save/recall, and deriving the current thread |
| `ninja/router.py` *(new)* | The classifier that picks a thread |
| `ninja/trace.py` | A `route` event, its token accounting, and its line in the viewer |
| `ninja/agent.py` | The REPL routes each turn; `/persona` overrides |
| `ninja/server.py` | Per-request thread; module globals removed |
| `ui/index.html` | Active thread in the header, route step in the trace view |
| `tests/test_threads.py` *(new)* | Thread isolation and `current_thread` |
| `tests/test_router.py` *(new)* | Routing, stickiness, and failure modes |

---

### Task 1: The thread column and thread-scoped memory

**Files:**
- Create: `sql/migrations/002_chat_log_thread.sql`
- Create: `tests/test_threads.py`
- Modify: `ninja/episodic.py`
- Modify: `ninja/server.py:24` and `ninja/agent.py` (call sites only — keep them working, do not add routing yet)

**Interfaces:**
- Consumes: `trace.connect()` (applies migrations), `trace.MIGRATIONS`.
- Produces:
  - `episodic.save(session_id: str, role: str, content: str, trace_id: int | None, thread: str) -> None`
  - `episodic.recall(thread: str, limit: int = RECALL) -> list[dict]`
  - `episodic.current_thread(default: str) -> str`

- [ ] **Step 1: Write the migration**

Create `sql/migrations/002_chat_log_thread.sql`:

```sql
-- Layer 8a. Which conversation a message belongs to.
--
-- Deliberately not session_id, which already exists and means one process run.
-- The Episodic panel has counted distinct session_id as "sessions" since layer
-- 4, and overloading it would quietly change a number the dashboard reports.
-- One run touches several threads; one thread spans many runs.
ALTER TABLE chat_log ADD COLUMN thread TEXT;
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_threads.py`:

```python
"""Layer 8a: each persona keeps its own transcript.

The point of a thread is that interrupting one conversation does not disturb
another. These are the tests for "does not disturb".
"""

from ninja import episodic


def test_a_message_stays_in_its_own_thread():
    episodic.save("s1", "user", "how do I test a flaky API?", None, "interview-coach")
    episodic.save("s1", "user", "make a note about the call", None, "assistant")

    coach = [m["content"] for m in episodic.recall("interview-coach")]
    assistant = [m["content"] for m in episodic.recall("assistant")]

    assert coach == ["how do I test a flaky API?"]
    assert assistant == ["make a note about the call"]


def test_returning_to_a_thread_finds_it_as_it_was():
    # The interrupt story: talk to the coach, go away, come back.
    episodic.save("s1", "user", "first coaching message", None, "interview-coach")
    episodic.save("s1", "assistant", "a coaching reply", None, "interview-coach")
    episodic.save("s1", "user", "unrelated errand", None, "assistant")
    episodic.save("s1", "assistant", "errand done", None, "assistant")

    assert [m["content"] for m in episodic.recall("interview-coach")] == [
        "first coaching message",
        "a coaching reply",
    ]


def test_an_empty_thread_recalls_nothing():
    episodic.save("s1", "user", "hello", None, "assistant")
    assert episodic.recall("interview-coach") == []


def test_recall_still_refuses_to_start_on_an_assistant_message():
    # Pre-existing rule, per-thread now: an assistant message cannot lead the
    # messages array, so a window starting mid-exchange drops it.
    episodic.save("s1", "assistant", "dangling reply", None, "assistant")
    episodic.save("s1", "user", "a question", None, "assistant")
    assert [m["role"] for m in episodic.recall("assistant")] == ["user"]


def test_the_current_thread_is_the_one_the_last_message_used():
    # Derived, not remembered. A remembered name is the shape of the layer 7
    # bug where a finished turn wrote back a persona and undid a switch.
    episodic.save("s1", "user", "a", None, "assistant")
    episodic.save("s1", "user", "b", None, "interview-coach")
    assert episodic.current_thread("assistant") == "interview-coach"


def test_the_current_thread_falls_back_when_there_is_no_history():
    assert episodic.current_thread("assistant") == "assistant"


def test_stats_still_counts_sessions_not_threads():
    # session_id means one process run and the Episodic panel reports it.
    episodic.save("s1", "user", "a", None, "assistant")
    episodic.save("s1", "user", "b", None, "interview-coach")
    episodic.save("s2", "user", "c", None, "assistant")
    assert episodic.stats()["sessions"] == 2


def test_history_exposes_the_thread():
    # The cockpit's Episodic panel reads this.
    episodic.save("s1", "user", "a", None, "interview-coach")
    assert episodic.history()[0]["thread"] == "interview-coach"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_threads.py -q --no-cov`
Expected: FAIL — `TypeError: save() takes 3 positional arguments but 5 were given`

- [ ] **Step 4: Update `ninja/episodic.py`**

Replace `save`, `recall`, `history` and add `current_thread`:

```python
def save(
    session_id: str, role: str, content: str, trace_id: int | None, thread: str
) -> None:
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


def recall(thread: str, limit: int = RECALL) -> list[dict]:
    """One thread's most recent messages, oldest first, shaped for the array."""
    conn = connect()
    rows = conn.execute(
        "SELECT role, content FROM chat_log WHERE thread = ?"
        " ORDER BY id DESC LIMIT ?",
        (thread, limit),
    ).fetchall()
    conn.close()
    # An assistant message cannot lead the array, so drop it if the window
    # happens to start mid-exchange.
    messages = [{"role": role, "content": content} for role, content in reversed(rows)]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
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
```

And add `thread` to `history`'s select and keys:

```python
def history(limit: int = 50) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT id, session_id, role, content, created_at, trace_id, thread"
        " FROM chat_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    keys = ("id", "session_id", "role", "content", "created_at", "trace_id", "thread")
    return [dict(zip(keys, row, strict=True)) for row in rows]
```

`stats()` is unchanged — it counts `session_id`, which still means one run.

- [ ] **Step 5: Keep the two existing call sites working**

Routing arrives in Tasks 3 and 4. For now both callers pass the default thread so
nothing is red in between.

In `ninja/server.py`, line 24:

```python
messages: list = episodic.recall(personas.DEFAULT)
```

In `ninja/agent.py`, inside `main()`:

```python
    messages = episodic.recall(persona.name)
```

and both `episodic.save(...)` calls in `main()` gain the thread:

```python
        episodic.save(session, "user", user_input, trace_id, persona.name)
        episodic.save(session, "assistant", reply, trace_id, persona.name)
```

In `ninja/server.py`'s `chat()`, both saves gain it too:

```python
    episodic.save(session, "user", message.text, trace_id, persona.name)
    episodic.save(session, "assistant", reply, trace_id, persona.name)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass. The count rises by 8.

- [ ] **Step 7: Run the gate**

Run: `uv run pytest -q --cov-fail-under=89 && uv run ruff check .`
Expected: both green.

- [ ] **Step 8: Commit**

```bash
git add sql/migrations/002_chat_log_thread.sql tests/test_threads.py ninja/episodic.py ninja/server.py ninja/agent.py
git commit -m "Give each persona its own transcript

chat_log gains a thread column and recall scopes to it, so a message in
one conversation no longer appears in another's memory.

Deliberately not session_id. That column already exists, means one
process run, and the Episodic panel has reported a count of it as
\"sessions\" since layer 4 — one run touches several threads and one
thread spans many runs, so overloading it would quietly change a number
the dashboard shows.

The current thread is derived from the most recent message rather than
remembered anywhere. A remembered name has a write path that can get out
of step with the transcript it describes, which is the shape of the layer
7 bug where a finished turn wrote back a persona and undid a switch.

Nothing routes yet: both callers pass the persona they already had.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The router

**Files:**
- Create: `ninja/router.py`
- Create: `tests/test_router.py`
- Modify: `ninja/trace.py` (a `route` event, its accounting, and its line in `print_one`)
- Modify: `tests/test_trace.py` (the viewer test covers every event kind)

**Interfaces:**
- Consumes: `personas.Persona`, `personas.all()`, `personas.DEFAULT_MODEL`; `trace.Trace`.
- Produces:
  - `router.route(client, user_input: str, current: str, cast: list[Persona], trace: Trace) -> str`
  - `router.MODEL: str`
  - `trace.Trace.route(chosen: str, previous: str, why: str, model: str, response, ms: int) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_router.py`:

```python
"""Layer 8a: the classifier that decides which conversation a turn belongs to."""

import types

from ninja import personas, router, trace

from .conftest import StubClient, block


def answer(text, tokens=(80, 4)):
    """A router reply. Shaped like an anthropic Message, as StubClient expects."""
    return types.SimpleNamespace(
        content=[block(type="text", text=text)],
        stop_reason="end_turn",
        usage=types.SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


def cast():
    return personas.all()


def test_it_returns_the_persona_the_model_named():
    client = StubClient([answer("interview-coach")])
    chosen = router.route(client, "how do I test a flaky API?", "assistant", cast(), trace.Trace("x"))
    assert chosen == "interview-coach"


def test_the_prompt_carries_the_descriptions_and_the_current_thread():
    # description is what the router decides on — it was written as a tool
    # description in disguise for exactly this.
    client = StubClient([answer("assistant")])
    router.route(client, "hello", "interview-coach", cast(), trace.Trace("x"))

    sent = client.seen[0]
    prompt = sent["system"] + str(sent["messages"])
    assert "interview-coach" in prompt
    assert "rehearse" in prompt        # from interview-coach's description
    assert "interview-coach" in sent["system"]   # the current thread is stated


def test_an_unknown_name_keeps_the_current_thread():
    # A router that can strand you is worse than no router.
    client = StubClient([answer("marketing-department")])
    chosen = router.route(client, "hi", "assistant", cast(), trace.Trace("x"))
    assert chosen == "assistant"


def test_a_blank_answer_keeps_the_current_thread():
    client = StubClient([answer("")])
    assert router.route(client, "hi", "interview-coach", cast(), trace.Trace("x")) == "interview-coach"


def test_a_failed_call_keeps_the_current_thread_and_does_not_raise():
    class Exploding:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise RuntimeError("router upstream is down")

    turn = trace.Trace("x")
    assert router.route(Exploding(), "hi", "assistant", cast(), turn) == "assistant"
    event = next(e for e in turn.events if e["type"] == "route")
    assert "down" in event["why"]


def test_the_answer_is_matched_leniently():
    # Models add punctuation and capitals. Do not fail a turn over a full stop.
    client = StubClient([answer("  Interview-Coach.  ")])
    assert router.route(client, "hi", "assistant", cast(), trace.Trace("x")) == "interview-coach"


def test_the_decision_is_recorded_with_its_reason():
    client = StubClient([answer("interview-coach")])
    turn = trace.Trace("x")
    router.route(client, "quiz me", "assistant", cast(), turn)

    event = next(e for e in turn.events if e["type"] == "route")
    assert event["chosen"] == "interview-coach"
    assert event["previous"] == "assistant"
    assert event["why"]


def test_the_routers_tokens_are_in_the_turns_totals():
    # An invisible per-turn spend is what makes a cost ceiling wrong.
    client = StubClient([answer("assistant", tokens=(80, 4))])
    turn = trace.Trace("x")
    router.route(client, "hi", "assistant", cast(), turn)

    assert turn.input_tokens == 80
    assert turn.output_tokens == 4
    assert turn.cost > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_router.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'ninja.router'`

- [ ] **Step 3: Add the `route` event to `ninja/trace.py`**

Next to `gate`, inside `class Trace`:

```python
    def route(
        self, chosen: str, previous: str, why: str, model: str, response, ms: int
    ) -> None:
        """Which conversation this turn was filed under, and what it cost.

        The classifier is a real model call, so its tokens belong in the turn's
        receipt like any other. A router whose spend is invisible is the first
        thing that would make a cost ceiling wrong.
        """
        if response is not None:
            used = response.usage
            self.input_tokens += used.input_tokens
            self.output_tokens += used.output_tokens
            self.cost += price(model, used.input_tokens, used.output_tokens)
        self.events.append(
            {
                "type": "route",
                "chosen": chosen,
                "previous": previous,
                "why": why,
                "model": model,
                "in": response.usage.input_tokens if response is not None else 0,
                "out": response.usage.output_tokens if response is not None else 0,
                "ms": ms,
            }
        )
```

- [ ] **Step 4: Give `print_one` a branch for it**

In `trace.print_one`, the event loop already branches on `gate` and then on
model/tool. Add `route` as the first branch, beside `gate`:

```python
        if e["type"] == "route":
            moved = "stayed in" if e["chosen"] == e["previous"] else f"{e['previous']} →"
            print(f"  {i}. route  {moved} {e['chosen']} · {e['why']}")
            continue
```

Without this the route event falls through to the tool branch and prints
`undefined`-shaped nonsense, which is the bug this repo shipped for two layers.

- [ ] **Step 5: Write `ninja/router.py`**

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_router.py -q --no-cov`
Expected: PASS, 8 tests.

- [ ] **Step 7: Extend the viewer test to cover the new event kind**

`tests/test_trace.py::test_the_trace_viewer_prints_every_kind_of_event` asserts
that each event came from its own branch and that the output contains no
`"None"`. Add a `route` event to the trace it builds, and assert the rendered
output names both threads:

```python
    turn.route("interview-coach", "assistant", "moved on 'quiz me'", "claude-haiku-4-5",
               response([block(type="text", text="interview-coach")], "end_turn"), 12)
```

and in the assertions:

```python
    assert "route" in out
    assert "assistant → interview-coach" in out
    assert "None" not in out
```

- [ ] **Step 8: Run the gate**

Run: `uv run pytest -q --cov-fail-under=89 && uv run ruff check .`
Expected: both green.

- [ ] **Step 9: Commit**

```bash
git add ninja/router.py ninja/trace.py tests/test_router.py tests/test_trace.py
git commit -m "Decide which conversation a turn belongs to

One small classifier call before the turn, reading each persona's
description — which was written as a tool description in disguise, when
to use this rather than what it is, for exactly this purpose.

Sticky by construction. Most turns continue what you were already doing,
and a follow-up like \"why would you pick that?\" contains nothing that
names coaching. Without stickiness the router misfiles constantly; with
it, it only has to notice a genuine change of subject.

It never raises. An upstream failure, an unrecognised name and a blank
answer all keep the current thread and say so in the trace, because a
router that can strand you is worse than no router.

Its tokens go into the turn's totals rather than off the books. Layer 15
plans a cost ceiling and this repo has already shipped one fail-open cost
path, where an unpriced model reported zero.

The trace viewer gets a branch for the new event in the same commit. A
renderer that knows two of three event kinds is how undefined(undefined)
shipped for two layers.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Route the REPL

**Files:**
- Modify: `ninja/agent.py` (`main`, and `switch` gains nothing — it already returns a Persona)
- Modify: `tests/test_repl.py`

**Interfaces:**
- Consumes: `router.route(...)`, `episodic.recall(thread)`, `episodic.current_thread(default)`.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repl.py`:

```python
def test_the_repl_routes_each_turn(monkeypatch, capsys):
    # The router picks the thread; the turn runs in it.
    from ninja import episodic, router

    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)
    monkeypatch.setattr(router, "route", lambda *a, **k: "interview-coach")

    lines = iter(["quiz me on api testing", ""])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)
    agent.main()

    # The message was filed under the thread the router named.
    assert [m["content"] for m in episodic.recall("interview-coach")] == [
        "quiz me on api testing"
    ]
    assert episodic.recall("assistant") == []


def test_an_explicit_persona_overrides_the_router(monkeypatch):
    # /persona is an override. The router does not overrule it for that turn.
    from ninja import episodic, router

    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)

    called = []
    monkeypatch.setattr(router, "route", lambda *a, **k: called.append(1) or "assistant")

    lines = iter(["/persona interview-coach", "a coaching question", ""])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)
    agent.main()

    assert called == [], "the router ran despite an explicit /persona override"
    assert [m["content"] for m in episodic.recall("interview-coach")] == [
        "a coaching question"
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_repl.py -q --no-cov`
Expected: FAIL — the messages land in `assistant`, because `main()` does not route yet.

- [ ] **Step 3: Rewrite `main()`'s loop in `ninja/agent.py`**

```python
def main() -> None:
    client = anthropic.Anthropic()
    session = episodic.new_session()
    cast = personas.all()
    persona = personas.load(episodic.current_thread(personas.DEFAULT))
    forced = False

    print(f"ninja | {persona.name} | model={persona.model} | ctrl-d to quit")
    print()

    while True:
        try:
            user_input = input(f"{persona.name}> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue
        if user_input.startswith("/persona"):
            persona = switch(user_input, persona)
            # An override: the next turn runs where you put it, without the
            # router second-guessing the instruction. A bare /persona only
            # lists the cast, so it is not an instruction to go anywhere.
            forced = user_input.strip() != "/persona"
            continue

        turn = Trace(user_input)
        if not forced:
            persona = personas.load(
                router.route(client, user_input, persona.name, cast, turn)
            )
        forced = False

        # The transcript comes from the thread, not from a list carried across
        # switches. Returning to a conversation finds it as it was.
        messages = [*episodic.recall(persona.name), {"role": "user", "content": user_input}]
        reply = run_turn(client, messages, turn, persona,
                         build_system(user_input, turn, persona))
        trace_id = turn.finish(reply)

        episodic.save(session, "user", user_input, trace_id, persona.name)
        episodic.save(session, "assistant", reply, trace_id, persona.name)

        print(f"\n{persona.name}> {reply}")
        print(
            f"[trace {trace_id} · {len(messages)} messages · "
            f"{turn.input_tokens} in / {turn.output_tokens} out · "
            f"${turn.cost:.5f}]\n"
        )
```

Add `router` to the imports at the top of the file:

```python
from ninja import episodic, personas, router, semantic, tools
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_repl.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Smoke the REPL non-interactively**

```bash
echo "" | uv run ninja 2>&1 | head -3
```

Expected: the banner prints and it exits cleanly, no traceback. It must not make
a model call — an empty line is skipped and then EOF ends the loop.

- [ ] **Step 6: Run the gate**

Run: `uv run pytest -q --cov-fail-under=89 && uv run ruff check .`
Expected: both green.

- [ ] **Step 7: Commit**

```bash
git add ninja/agent.py tests/test_repl.py
git commit -m "Route each turn in the REPL

The router picks the conversation and the turn runs in it, so asking
something unrelated mid-lesson goes to the assistant and the next
coaching message resumes the coach's thread untouched.

/persona stays an override: the router does not overrule an explicit
instruction on the turn that follows it.

Working memory is no longer a list carried across switches. It is read
from the thread each turn, which is what makes returning to a
conversation find it as it was rather than as it was left in memory.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Route the cockpit, and delete the module globals

**Files:**
- Modify: `ninja/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `router.route(...)`, `episodic.recall(thread)`, `episodic.current_thread(default)`.
- Produces: `GET /api/personas` gains `"active"` derived from `episodic.current_thread`; `POST /api/chat` returns `"persona"` as it already does.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
def test_a_request_is_routed_when_it_names_no_persona(monkeypatch):
    from ninja import episodic, router

    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr(server, "client", lambda: stub)
    monkeypatch.setattr(router, "route", lambda *a, **k: "interview-coach")

    body = client.post("/api/chat", json={"text": "quiz me"}).json()

    assert body["persona"] == "interview-coach"
    assert [m["content"] for m in episodic.recall("interview-coach")] == ["quiz me"]


def test_naming_a_persona_skips_the_router(monkeypatch):
    from ninja import router

    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr(server, "client", lambda: stub)

    called = []
    monkeypatch.setattr(router, "route", lambda *a, **k: called.append(1) or "assistant")

    body = client.post("/api/chat", json={"text": "hi", "persona": "interview-coach"}).json()

    assert called == [], "the router ran despite an explicit persona"
    assert body["persona"] == "interview-coach"


def test_two_threads_do_not_see_each_others_messages(monkeypatch):
    # The property module globals could not offer.
    from ninja import router

    from .conftest import StubClient, block, response

    monkeypatch.setattr(router, "route", lambda *a, **k: "assistant")

    for text, persona in [("coach one", "interview-coach"), ("assistant one", "assistant")]:
        stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
        monkeypatch.setattr(server, "client", lambda s=stub: s)
        client.post("/api/chat", json={"text": text, "persona": persona})

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr(server, "client", lambda: stub)
    client.post("/api/chat", json={"text": "coach two", "persona": "interview-coach"})

    sent = [m["content"] for m in stub.seen[0]["messages"]]
    assert "assistant one" not in sent
    assert "coach one" in sent


def test_the_server_holds_no_transcript_of_its_own():
    # The transcript lives in the database. There is nothing left to race on.
    assert not hasattr(server, "messages")
    assert not hasattr(server, "active")


def test_the_active_persona_follows_the_last_message(monkeypatch):
    from ninja import episodic

    episodic.save("s1", "user", "x", None, "interview-coach")
    assert client.get("/api/personas").json()["active"] == "interview-coach"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_server.py -q --no-cov`
Expected: FAIL — `server.messages` and `server.active` still exist, and no routing happens.

- [ ] **Step 3: Delete the globals and route in `ninja/server.py`**

Replace lines 22–28 (the `session` / `messages` / `active` block) with:

```python
# A session is one process run, and it is the only thing worth holding in
# memory. The transcript is not: it lives in chat_log, one thread per persona,
# and is read per request. There is no shared mutable state here to race on,
# and a restart reconstructs nothing because nothing was ever only in memory.
session = episodic.new_session()
_client: anthropic.Anthropic | None = None
```

Add `router` to the import:

```python
from ninja import agent, episodic, personas, router, semantic, tools, trace
```

Replace `chat()`:

```python
@app.post("/api/chat")
def chat(message: Message):
    turn = trace.Trace(message.text)
    current = episodic.current_thread(personas.DEFAULT)
    # Naming a persona is an override; the router does not overrule it.
    name = message.persona or router.route(
        client(), message.text, current, personas.all(), turn
    )
    try:
        persona = personas.load(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    messages = [
        *episodic.recall(persona.name),
        {"role": "user", "content": message.text},
    ]
    try:
        reply = agent.run_turn(
            client(), messages, turn, persona,
            agent.build_system(message.text, turn, persona),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"the turn failed: {exc}") from exc

    # Nothing is adopted on failure: the turn's messages were a local list, and
    # these two writes are the only thing that makes the turn part of a thread.
    trace_id = turn.finish(reply)
    episodic.save(session, "user", message.text, trace_id, persona.name)
    episodic.save(session, "assistant", reply, trace_id, persona.name)
    return {
        "reply": reply,
        "trace_id": trace_id,
        "working_memory": len(messages),
        "persona": persona.name,
    }
```

- [ ] **Step 4: Fix the two panels that read the deleted globals**

In `stats()`, `"working_memory": len(messages)` no longer resolves. Replace it
with the current thread's size:

```python
        "working_memory": len(episodic.recall(episodic.current_thread(personas.DEFAULT))),
```

In `personas_panel()`, `"active": active` becomes:

```python
        "active": episodic.current_thread(personas.DEFAULT),
```

And delete `set_persona` together with the `PersonaName` model — with the
active thread derived from the transcript, an endpoint that sets it would be
writing a value the next message overwrites. The cockpit switches by naming a
persona on the chat request instead.

Remove the route from the UI in Task 5.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server.py -q --no-cov`
Expected: PASS. `test_setting_the_persona_changes_the_default` and
`test_an_unknown_persona_is_a_400` refer to the deleted endpoint — delete the
first, and rewrite the second to post an unknown persona to `/api/chat`:

```python
def test_an_unknown_persona_is_a_400():
    assert client.post("/api/chat", json={"text": "hi", "persona": "nonesuch"}).status_code == 400
```

- [ ] **Step 6: Run the gate**

Run: `uv run pytest -q --cov-fail-under=89 && uv run ruff check .`
Expected: both green.

- [ ] **Step 7: Commit**

```bash
git add ninja/server.py tests/test_server.py
git commit -m "Give the cockpit threads and take away its globals

Each request reads its thread from the database, runs the turn and
writes the result. The transcript stops living in module memory.

That removes the race the layer 7 critique found and told us not to paper
over with a mutex: messages and active were mutated from a threadpool
without a lock, and the fix it named was per-conversation ownership at
layer 8. This is that, and it arrives by deleting state rather than
guarding it.

The active persona is derived from the last message rather than stored,
so POST /api/persona goes with it — an endpoint that set a value the next
message overwrites was a second source of truth waiting to disagree. The
cockpit names a persona on the chat request instead, which is the same
override the REPL's /persona already is.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Show the thread and the routing decision

**Files:**
- Modify: `ui/index.html`

**Interfaces:**
- Consumes: `GET /api/personas` (`active`), `GET /api/traces/{id}` (a `route` event), `POST /api/chat` (`persona` in the response).
- Produces: nothing.

- [ ] **Step 1: Give the trace viewer a branch for the route event**

In `show(id)`, the event map currently branches on `model` and everything else.
A `route` event has no `name`, `args` or `ok`, so it falls into the tool branch
and renders `undefined(undefined)` — the exact bug fixed one layer ago for the
gate event. Add it as the first case:

```js
    t.events.map((e, i) => e.type === 'route'
      ? `<div class="step"><span class="k">${i+1}</span><span class="k">route</span>
         <span class="d">${e.chosen === e.previous
             ? 'stayed in ' + esc(e.chosen)
             : esc(e.previous) + ' → ' + esc(e.chosen)} · ${esc(e.why)}</span>
         <span class="t">${e.ms}ms</span></div>`
      : e.type === 'model'
```

- [ ] **Step 2: Remove the switcher's endpoint call**

`switchPersona()` posts to `/api/persona`, which Task 4 deleted. A switch is now
a property of a message, so the button sets a pending persona that the next
message carries:

```js
let pendingPersona = null;

async function switchPersona(name) {
  pendingPersona = name;
  say('sys', 'next message goes to ' + name);
  await counters();
  await render();
}
```

And in the composer's submit handler, include it and clear it:

```js
    const payload = {text};
    if (pendingPersona) { payload.persona = pendingPersona; pendingPersona = null; }
    const res = await fetch('/api/chat', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
```

- [ ] **Step 3: Show the thread the reply came from**

After a successful reply, the response carries `persona`. Update the header from
it so the active thread is right without waiting for the next counter refresh:

```js
    say('bot', r.reply);
    if (r.persona) $('chat-persona').textContent = r.persona;
```

- [ ] **Step 4: Verify the JavaScript still parses**

```bash
node --check <(python3 -c "
import re, pathlib
print(re.findall(r'<script[^>]*>(.*?)</script>', pathlib.Path('ui/index.html').read_text(), re.S)[0])
")
```

Expected: no output.

- [ ] **Step 5: Verify in a real browser**

```bash
pkill -f "ninja dashboard" 2>/dev/null; sleep 1
(uv run ninja dashboard > /tmp/ninja-t5.log 2>&1 &) ; sleep 5
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --disable-gpu --no-sandbox --dump-dom --virtual-time-budget=6000 \
  http://localhost:7777 > /tmp/t5.html
grep -o 'id="chat-persona">[^<]*' /tmp/t5.html
grep -o 'id="c-personas">[^<]*' /tmp/t5.html
pkill -f "ninja dashboard"
```

Expected: `id="chat-persona">assistant` and `id="c-personas">2`. This renders a
real page and makes no model call — do not send a chat message.

- [ ] **Step 6: Update the README**

Change the layer 8 row in the status table from `—` to `✅`, rename it
`Delegation` → `Threads and routing`, and change "Seven of fifteen so far." to
"Eight of fifteen so far."

Add to the "Coming as layers land" block, replacing the `/persona` line:

```
you> quiz me on api testing      # routed to interview-coach
you> make a note about the call  # routed to assistant, coach untouched
you> and back to flaky tests     # the coach's thread, as you left it
```

- [ ] **Step 7: Run the gate**

Run: `uv run pytest -q --cov-fail-under=89 && uv run ruff check . && uv run pip-audit --progress-spinner off --skip-editable`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add ui/index.html README.md
git commit -m "Show which conversation a turn was filed under

The trace viewer gets a branch for the route event. Without one it falls
into the tool branch and draws undefined(undefined), which is the bug
this cockpit shipped for two layers and fixed one layer ago for the gate
event — the same mistake is available every time an event kind is added.

The switcher no longer posts to a deleted endpoint. A switch is a
property of a message now, so the button marks the next message and the
composer carries it, which is the same override the REPL's /persona is.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Not in this layer

| Thing | Where |
|---|---|
| `delegate(persona, task)`, depth cap, subset rule | 8b |
| The context parameter `tools.run` needs for `delegate` | 8b — it is 8b's blocker, not this one |
| Topic sub-threads (`interview-coach:qa-sdet`) | layer 5 extension, additive to `thread` |
| A panel listing every thread | when there are more than two |
| Routing accuracy as a measured number | layer 11, where it stops being anecdote |
