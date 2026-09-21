# Phase B1: Consolidation (layer 10)

**Status:** proposed — awaiting approval, no code written
**Branch (when approved):** `phase-b1-consolidation`, from `main`
**Depends on:** nothing in Phase A. Touches `agent.py`, `server.py`, `semantic.py`.
**Feeds:** A2 (`MEMORY.md` mirrors episodes), B2 (gate upgrade)
**Reference:** waku's `waku/memory/consolidation.py` (82 lines)

---

## What this adds

After every N exchanges in a conversation, a cheap model reads them and writes
down what is worth keeping: durable **facts** about you, and one dated
**episode** summarising what the conversation was.

```
chat_log ──(N unconsolidated exchanges)──► small model reads them
                                              │
                    ┌─────────────────────────┴──────────────────────┐
                    ▼                                                ▼
              facts  (source = "distilled")                episodes  (one line, dated)
              what is durably true about you               what happened, when
                    │                                                │
                    └──────────────  chat_log rows marked  ──────────┘
                                     consolidated = 1, in the SAME transaction
```

This is what turns "it remembers what I explicitly told it to `remember`" into
"it remembers what I *said*". `schema.sql` already anticipates it — the `facts`
table documents `source` as `told | distilled`.

## Names, because two of them collide

`ninja/episodic.py` stores the **raw log** (`chat_log`) and calls it episodic
memory. Waku keeps the raw log *separate* from **episodes** — one dated sentence
per consolidated conversation. Adding an `episodes` table beside a module called
`episodic` invites confusion, so this spec fixes the vocabulary:

| Word | Means | Where |
|---|---|---|
| chat log | every message, replayed as recent history | `chat_log`, `episodic.py` |
| episode | a distilled, dated one-line summary | `episodes`, `consolidation.py` |
| fact | a durable statement about you | `facts`, `semantic.py` |

`episodic.py` is **not renamed** here — that is churn across the dashboard for no
behavioural gain. The table and module for the new thing are named so they
cannot be mistaken for it.

## Schema — migration `003`

```sql
ALTER TABLE chat_log ADD COLUMN consolidated INTEGER NOT NULL DEFAULT 0;

CREATE TABLE episodes (
    id          INTEGER PRIMARY KEY,
    thread      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    happened_on TEXT NOT NULL,    -- YYYY-MM-DD
    created_at  TEXT NOT NULL
);
```

Two migration hazards this repo has already been bitten by, both applying here:
`migrate()` strips only whole `--` comment lines then splits on `;`, so **no
semicolons inside inline comments**; and `schema.sql` stays frozen and every
change is a migration.

Existing `chat_log` rows default to `consolidated = 0`, so the first run after
upgrading will consolidate your real history — in **bounded batches** (below),
never one giant call.

## When it runs, and what it costs

**Trigger:** a thread has at least `CONSOLIDATE_EVERY = 6` unconsolidated
exchanges (12 rows). Per thread, because the persona is the unit of
conversation — a coaching session and a note-taking session are different
episodes.

**Where in the turn:** *inside* the turn, after `run_turn` succeeds and **before
`turn.finish()`**. Not after.

This is the design's most consequential choice, so the reasoning is spelled out.
Consolidation is a model call and costs money. If it ran after `finish()`, its
spend would have no trace to land on — invisible, which is the exact failure the
router's cost accounting was built to avoid. Running it before `finish()` puts
its tokens, cost and latency in the receipt of the turn that triggered it, using
the existing `Trace.model(...)` with persona label `"consolidation"`. No new
trace kind, no inflated turn counts on the dashboard, no schema change to
`traces`.

The price: that one turn is slower by one cheap call, once every 6 exchanges.
Waku does the same inline. A background thread would hide the latency but adds
SQLite concurrency this project has deliberately avoided.

**Lag, by design:** it consolidates exchanges saved *before* this turn. The
current exchange is saved after `finish()` (it needs the trace id), so it joins
the *next* batch. Batching makes this invisible.

**Model:** `personas.DEFAULT_MODEL` (haiku), like the router. **Bounded input:**
at most `MAX_BATCH = 20` exchanges per call, oldest first; the rest wait for the
next turn. A 300-message history backlog therefore drains 20 exchanges per
triggering turn — slowly and cheaply — rather than one call with a 300-message
prompt.

## The prompt, and the dedup it buys for free

The summariser is shown the log and **the existing facts**, with an instruction
not to restate any of them. Without this, the agent's own `remember` calls and
consolidation would store the same fact twice — and `main` has no dedup.

The vector branch measured why real dedup is hard: an embedding-similarity
threshold silently merged *"standup at 9am"* with *"standup at 10am"*. Telling
the model what is already known sidesteps that entirely, at the cost of a longer
prompt. It is imperfect (paraphrases can slip through) and that is acceptable:
a duplicate is visible and removable; a wrongly merged fact is neither.

Existing facts are capped at the most recent `50` in the prompt.

Output contract, one JSON object:

```json
{"facts": ["one self-contained sentence, third person", "..."], "episode": "one sentence"}
```

## Failure is quiet and lossless

Consolidation **must never fail a turn and must never lose data.** Every failure
below leaves the rows unconsolidated, so the next trigger retries:

| Failure | What happens |
|---|---|
| API call raises | rows untouched; trace event says why |
| Reply truncated at `max_tokens` | rows untouched; distinguished from bad JSON |
| No JSON / unparseable | rows untouched |
| Valid JSON, wrong shape (`facts` is `null`, a string, or a dict) | treated as unusable; rows untouched |
| Some `facts` not strings, or over 300 chars | those entries dropped, the rest kept |
| More than 10 facts | first 10 kept |
| Empty `facts` **and** empty `episode` | rows *are* marked consolidated — nothing was worth keeping, and retrying would just pay again |
| Write fails midway | **one transaction** covers facts, episode and the `consolidated` flags, so it rolls back whole |

The last row is why `semantic.remember` gets one optional argument,
`conn=None`: consolidation has to insert facts on *its* connection, inside *its*
transaction. With no argument, behaviour is unchanged.

The `facts: null` row is listed on purpose. The vector branch's router shipped a
parser that crashed on exactly that input — `dict.get(key, [])` returns the
default only for an *absent* key, not an explicit `null`. Validating shape, not
just presence, is a requirement here.

## Hook sites

`agent.py` (REPL) and `server.py::chat`, between `run_turn` and `turn.finish()`.
One line each: `consolidation.run_if_due(client, turn)`. It catches everything
itself, so neither site needs a try/except.

## Testing

`StubClient` only — **no live API call, no spend**. Each test names its bug.

- Below threshold → no call made (the stub's script is empty; an unwarranted call raises).
- At threshold → one call; facts written with `source="distilled"`, one episode, rows flagged.
- **Per thread:** 6 exchanges in `assistant` and 3 in `interview-coach` consolidates only the first.
- **Batch cap:** 30 exchanges → 20 consolidated, 10 left.
- **Idempotent:** a second call immediately after does nothing and makes no API call.
- The prompt contains the existing facts (asserted on `client.seen`).
- Each failure row above, from both sides: what the DB looks like *and* what the trace recorded.
- **Atomicity:** force the episode insert to fail; assert *no* fact was written and no row flagged. A test that only checks the happy path would pass against non-atomic code.
- **Cost lands once, in the turn's receipt:** the turn's `input_tokens` includes consolidation's, and no second trace row exists.
- Migration: an existing database with `chat_log` rows comes through `003` with `consolidated = 0` everywhere.

## Not in this change

- **Retrieving episodes.** They are written and visible (via A2's `MEMORY.md` and the
  DB) but the gate does not search them. Doing that means searching two tables with
  incomparable bm25 scores and changing the gate's skip semantics — that is B2's
  job. Until then episodes are write-only; that is stated, not hidden.
- Real dedup / contradiction handling. See the prompt section.
- Background execution.
- Dashboard panel changes.

## Decisions

1. **`CONSOLIDATE_EVERY = 6`, `MAX_BATCH = 20`.** Recommended; waku's default is 6.
2. **Per-thread triggering** rather than one global counter. Recommended.
3. **Inside the turn, before `finish()`** rather than a separate trace or a
   background thread. Recommended, for the cost-visibility reason above.
4. **Episodes write-only until B2.** Recommended over widening this PR to touch
   the gate. Say if you would rather retrieve them now.
5. **Consolidate your real history on first run**, in batches. Alternative: mark all
   existing rows consolidated in the migration and only distil new conversation.
   Recommended: distil it — that is where your existing facts come from — but it
   spends real tokens (roughly one call per 20 exchanges of history).
