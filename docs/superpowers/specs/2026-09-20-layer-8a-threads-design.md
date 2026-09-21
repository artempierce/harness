# Layer 8a: Threads

**Status:** design approved, not implemented
**Depends on:** layer 4 (episodic), layer 7 (personas), the migration mechanism (PR #9)
**Unblocks:** layer 8b (`delegate`), and closes the concurrency finding from the layer 7 critique

---

## What this layer adds

One chat, several conversations underneath it.

Each persona keeps its own transcript. A router decides, per turn, which one the
message belongs to. Interrupting a coaching session to make a note and then
coming back works because the coach's thread was never touched.

```
you ──► router ──► assistant        "make a note about the call"
                   interview-coach  "so why would you pick that?"
```

## Why a thread is a persona

The smallest thing that delivers interrupt-and-resume. A persona already has
instructions, an allowlist and a model; giving it a transcript as well makes it
a complete conversational unit rather than a set of settings.

The alternative considered was `persona + topic` — separate threads for
`interview-coach:qa-sdet` and `interview-coach:ai-engineering`. That is the
better long-term shape and it needs the topic resolver designed and deferred as
a layer 5 extension. It is additive: `thread` is a string, and a later layer can
make it `<persona>:<topic>` without changing anything here.

## This reverses a layer 7 decision, on purpose

Layer 7 chose to **keep** the transcript across a `/persona` switch, because
losing the conversation when you change hats felt wrong. Threads reach the same
goal by a better route: nothing is lost, because each persona keeps its own and
you return to it.

`/persona` stops being "swap instructions, keep talking" and becomes "go to that
persona's conversation".

## Schema

`chat_log` gains a `thread` column, via migration `002`.

`session_id` already exists and is **not** reused for this. It means "one process
run" — `episodic.stats()` counts distinct sessions and reports it as such. A
thread is orthogonal: one run can touch several threads, and one thread spans
many runs. Overloading the column would silently change what the Episodic panel
has been reporting since layer 4.

This is also the migration mechanism's second customer, which is the point of
having built it.

## The router

One classifier call per turn, before the loop.

```python
route(client, user_input, current, cast, trace) -> tuple[str, str]
```

It sends a small prompt — the persona names and `description` fields, the
current thread, and the message — and gets back a name. `description` was
written for exactly this: *a tool description in disguise, and it should be
written like one: when to use this, not what it is.*

**Sticky by construction.** The prompt states the current thread and instructs
the model to stay there unless the message clearly belongs elsewhere. Without
this, an ordinary follow-up — "why would you pick that?" — has nothing in it
that names coaching and lands in the wrong thread. Stickiness is what makes a
router usable rather than a source of misfiling.

**An unknown or malformed answer keeps the current thread.** Failing to route is
not a reason to fail the turn, and a router that can strand you is worse than no
router.

### Cost, recorded not hidden

The classifier is a real model call and its tokens belong in the turn's receipt.
It becomes a `route` event in the trace, alongside `gate`, carrying the chosen
thread, the previous one, the reason, and its own token counts. The turn's
totals include it.

Layer 15 plans a cost ceiling. A router whose spend is invisible would be the
first thing to make that ceiling wrong, and this repo has already shipped one
fail-open cost path (an unpriced model reporting `$0.00000`).

### The override

`/persona <name>` forces a thread and the router does not overrule it for that
turn. The route decision — including a skip — is visible in the trace and in the
cockpit, the same way a gate skip is. A silent router is untrustworthy in
exactly the way a silent gate would be.

## Server state disappears

Today `ninja/server.py` holds three module globals: `session`, `messages` and
`active`. The layer 7 critique found that `messages` and `active` are mutated
from a threadpool without a lock, and ruled against a mutex on the grounds that
the real fix is per-conversation ownership at layer 8. This is that.

```python
messages = episodic.recall(thread)      # per request, from the database
```

The transcript stops living in module memory. Each request reads its thread's
messages, runs the turn, and `episodic.save()` writes the result — which already
only happens after the turn returns whole, so layer 7's commit-on-success
property is preserved rather than reimplemented.

Two things follow. The race is gone, because there is no shared mutable state
left to race on. And a restart no longer needs to reconstruct anything: the
threads were always in the database.

The cost is one small SQLite read per request. At six rows, against a local
file, this is not a trade worth thinking about.

### Where "current thread" lives

The router is sticky, so it needs to know which thread the last turn used — and
that cannot be a module global without reintroducing the state this section just
removed.

It is derived, not stored: the current thread is the `thread` of the most recent
row in `chat_log`.

```python
def current_thread(default: str = personas.DEFAULT) -> str:
    """The thread the last message went to, or the default if there is none."""
```

This is correct across a restart, correct across two processes sharing one
database, and has no write path to get out of step with the transcript it
describes — the thread and the messages are the same rows. The alternative,
a remembered name, is the shape of the bug layer 7 shipped in `server.py:84`,
where a finished turn wrote back a persona and silently undid a switch.

## Entry points

**REPL.** The prompt shows the active thread. The router runs each turn;
`/persona` overrides it. Switching reloads that thread's messages rather than
carrying the current ones across.

**Cockpit.** `POST /api/chat` takes an optional `persona` as it does now, and
that remains an override. The active thread appears in the chat header, and the
route decision appears in the trace view beside the gate decision.

## Testing

Stubbed and free, in the existing style:

- A message in one thread does not appear in another's recall.
- Switching threads and switching back restores the first thread's messages.
- The router's choice is what `recall` is called with.
- An unknown or malformed router answer keeps the current thread and records why.
- An explicit persona on the request overrides the router, and the trace says so.
- The router's tokens are in the turn's totals, not dropped.
- Two requests naming different threads do not see each other's messages —
  the property the module globals could not offer.
- `episodic.stats()` still reports sessions, not threads.

The router is a model call, so it is a `StubClient` in tests like every other
model call. One live test already covers that real calls work at all.

## Not in this layer

| Thing | Where |
|---|---|
| `delegate(persona, task)`, depth cap, subset rule | 8b |
| The context parameter `tools.run` needs for `delegate` | 8b, and it is 8b's blocker, not this one |
| Topic sub-threads (`interview-coach:qa-sdet`) | layer 5 extension, additive to this |
| A threads panel listing every conversation | when there are more than two |

## Risks

**The router is a new per-turn cost and latency.** Roughly 200 input tokens on
the cheapest model, before the real turn starts. It is the price of not having
to say which hat you are wearing, and the trace shows what it cost so the
judgement can be revisited with numbers rather than impressions.

**A wrong route writes to the wrong transcript.** Not silently — the decision is
in the trace — but it is written before you read it. Stickiness makes the common
case safe; the override is the fix for the uncommon one.

**Two personas is a thin test of a router.** With a cast this small the
classifier has an easy job, and it will get harder as the cast grows. The eval
harness at layer 11 is where routing accuracy stops being anecdote.
