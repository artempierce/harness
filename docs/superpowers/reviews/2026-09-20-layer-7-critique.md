# Layer 7 (personas) — expert critique

Reviewed: `718fcfe..64fe08c` on `layer-7-personas`, against the design spec,
`ARCHITECTURE.md` and `CONTRIBUTING.md`. Fixes applied in `3eb834b..8abe0fa`.
Final state: `uv run pytest -q` → 77 passed, 2 deselected. `uv run ruff check .`
→ clean. No test in `tests/test_tools.py` was touched.

The line numbers below are from `64fe08c` (the branch as submitted).

---

## The verdict on the central claim

**The claim holds, and it is the best thing about this layer.** `Persona` is a
frozen dataclass with a tuple for `tools`; `server.chat` resolves it once at
line 68 and hands the object to `run_turn`, which never reads `active` again.
I sabotaged `run_turn` to re-resolve the persona from `server.active` on every
step and the new test at `tests/test_server.py` catches it. The claim was
untested as submitted — see F3.

**The two enforcement points are both load-bearing, and one of them is more
load-bearing than the spec realises.** The transcript is kept across a switch
(by design), so a coach turn that follows an assistant turn is sent a message
array in which `list_files` visibly exists, was called, and returned useful
output — while `persona.schemas()` omits it. Filtering is not merely advisory in
the abstract; this design actively teaches the model to ask for tools it does
not hold. `tools.run`'s gate is the only thing standing there. The spec's
sentence "swapping hats costs nothing in the transcript"
(`ninja/agent.py:107`) is wrong in exactly this respect: it costs the new
persona a worked demonstration of a capability it has been denied. Not a bug —
but the comment should say so, because someone will one day read it and decide
the dispatch gate is redundant.

Nothing here is theatre. The weak point is not enforcement, it is what the
harness knows about itself.

---

## Findings, by severity

### F1 — Trace records nothing about who ran the turn (HIGH, partly fixed)

`ninja/trace.py:47-62`. A model event held `ms`, `in`, `out`, `stop`. Not the
model. Not the persona. That was correct while both were module constants; this
branch made both per-turn and left the observability layer describing a world
with one agent in it. A trace of a coach turn and a trace of an assistant turn
are byte-identical in shape, so "what did the coach cost" and "which persona
made the call that went wrong" are both unanswerable — and layer 11's evals and
layer 8's delegation are both built out of exactly those questions.

**Fixed** (`1afc530`): model events now carry `model`, `persona` and `unpriced`.
The trace viewer and the cockpit read them with `.get`, so rows written before
the change still print.

**Not fixed, and it is the thing to do next**: this had to go in the `events`
JSON blob because the `traces` table cannot gain a column. `trace.connect()`
runs `sql/schema.sql` on every connection and every statement in it is
`CREATE TABLE IF NOT EXISTS`, so a column added to that file never reaches an
existing `.ninja/state.db` — and an `INSERT` naming it would then fail on every
turn for anyone with a database older than the change. There is no migration
mechanism and no version marker. Layer 8 needs `persona`, `depth` and
`parent_trace_id` as real columns to be able to query them; layer 11 needs to
join on them. **Write the migration story before layer 8, not during it.**

### F2 — An unpriced model bills as free (HIGH, fixed)

`ninja/trace.py:31-33`. `PRICING.get(model, (0.0, 0.0))` — a persona names any
model string it likes, and one that is not in the dict contributes exactly
nothing to the turn's cost. The comment claimed this shows "zero rather than
lying". A zero in the dashboard's `spent` tile, summed with real money, *is* the
lie: it is indistinguishable from a turn that cost nothing. Today that is a
wrong number. At layer 15 it is a hole in a guardrail — a per-turn dollar budget
computed from this function is fail-open, and the cheapest way to escape a cost
ceiling becomes writing a model id it has never heard of into a `PERSONA.md`
that the agent itself authors at layer 9.

**Fixed** (`1afc530`): the event records whether the call was priced, so the
shortfall is visible in `ninja trace <id>` and in the cockpit. I did **not**
change `price()`'s return type — `tests/test_trace.py:11` asserts it returns
`0.0` for an unknown model and that assertion is correct as far as it goes.
Layer 15 must read `unpriced` as "no bound available", not as nothing to bound.

### F3 — A finished turn silently undid a switch (HIGH, fixed)

`ninja/server.py:84`, `messages, active = working, persona.name`. Unconditional.
For a request that named no persona this looks like a no-op, and is one right up
until somebody clicks switch while a turn is in flight: the POST returns 200,
the chat log says "persona → interview-coach", the panel repaints, and then a
turn that started ten seconds earlier writes the old name back. The next message
goes to the persona the user just moved away from, and nothing anywhere says so.

This is also the exact scenario the spec's own motivating paragraph describes
("one slow turn and one impatient click does it") — it got the in-flight
direction right and missed the outbound one.

**Fixed** (`454f811`): the write-back happens only when the request named a
persona, which is the behaviour it was added for.

### F4 — M3's test did not test what it was named for (HIGH, fixed)

`tests/test_loop.py:129`. Two personas, two sequential `run_turn` calls, no
shared anything — it would pass against an implementation caching a persona in a
module global, which is the one thing it claims to exclude. It also duplicated
`test_the_request_carries_only_the_personas_tools` and
`test_the_personas_model_is_the_one_called`, so deleting it loses no coverage.

**Fixed** (`454f811`): deleted, and replaced at the level where the claim
actually lives — `test_a_switch_that_lands_mid_turn_reaches_neither_this_turn_nor_the_next`
in `tests/test_server.py`. A stub client flips `server.active` between the first
model call and the second and the test asserts both calls carried the toolset
the turn started with. Verified to fail against a `run_turn` that re-resolves the
persona each step, and against the F3 write-back.

### F5 — The cockpit said the allowlist was not enforced (HIGH, fixed)

`ninja/server.py:179-180`. The Guardrails panel listed `Tool allowlist` with
`"live": False, "layer": 7`, which the UI renders under the heading "Arrives
with the capability it constrains" — in a cockpit whose Growth tab, three
functions further down the same file, marks layer 7 **built**. The panel's own
section label says "values read from the running code". This is the guardrail
the layer exists to add and the product shipped saying it had not arrived. The
example given — "The tutor calling run_command" — names a persona and a tool
that do not exist in this repo.

There is a test asserting the step cap reports itself as live
(`tests/test_server.py:26`). The equivalent assertion for the thing this branch
built was never written.

**Fixed** (`65096c5`), plus a test.

### F6 — Every trace in the cockpit rendered a failure that had not happened (HIGH, fixed)

`ui/index.html:378-387`, `show()`. A trace holds three kinds of event and the
renderer branched on two: `e.type === 'model'` or else the tool branch. A gate
event has no `name`, no `args`, no `bytes` and no `ok`, so it fell into the tool
branch and drew `undefined(undefined)` with a red left border — the colour
reserved for a failed tool call — followed by `undefinedb`. The gate runs before
every turn, so **every trace anyone has ever opened in the browser** showed a
phantom error as its first step. `trace.print_one` has printed this step
correctly since layer 5; only the browser was wrong, which is why it survived.

This is the precise shape the brief asked me to hunt, and it was sitting in the
panel the branch's own feature is displayed in.

**Fixed** (`8b39633`).

### F7 — The guardrail exit lost the reply it returned (HIGH, fixed)

`ninja/agent.py:101`. Every exit from the loop appends the assistant message
before returning it except this one, which returns the string straight out of
the `for`. The transcript is left ending on a batch of `tool_result` blocks with
no assistant turn after them. The cockpit then adopts that transcript, saves the
reply to episodic memory, and reports a working-memory count that includes a
message the list does not contain. The next question is appended directly after
the tool results, so the two stores disagree and the model is asked to continue a
conversation that stops mid-exchange.

Pre-existing (layer 2), invisible in the chat, and the same family as the
corrupted message list PR #5 fixed. **Fixed** (`8abe0fa`), one line and a test.

### F8 — A typo in `tools:` removed a capability silently (MED-HIGH, fixed — this is M1)

`ninja/personas.py:47-56`. Confirmed, and worse than reported in two ways.

First, `tools: read_file` written without brackets is valid YAML and a *string*;
`tuple()` shreds it into nine one-letter tool names, producing a persona that
holds nothing at all, loads cleanly, and lists in the cockpit looking normal.

Second, `name:` was never checked against the directory. The directory is the
identity every lookup uses — `active`, `/persona <name>`, the cockpit's switch
button — so `personas/coach/PERSONA.md` declaring `name: interview-coach` loads
exactly once and is unreachable afterwards. Set it as the active persona and
every subsequent chat request 400s on a persona the panel is still listing.

**Fixed** (`3eb834b`): all three refused at load, with the file and the offending
value named.

### F9 — One bad persona file took down the whole cast (MED-HIGH, fixed)

Consequence of F8 being fixed, and a real failure on its own. `personas.all()`
loads every file, so one malformed `PERSONA.md` raises out of `agent.switch`'s
bare-`/persona` branch, out of `main()`, and ends the REPL with the transcript
in it. In the cockpit it escaped `personas_panel` as an unhandled exception,
which reaches the browser as a bare 500 with no body — so the reason the loader
took care to name never got shown. From layer 9 the agent writes these files.

**Fixed** (`78d6185`): both call sites catch and surface the message.

### F10 — A harness bug arrived as an ordinary tool error (MED, fixed — this is M2)

`ninja/agent.py:83-86`. Confirmed. Worth adding a nuance the issue list did not
have: narrowing to `ValueError` alone would be wrong, because a model that omits
a required argument produces `KeyError`, and a model that asks for a file that
is not there produces `OSError` — both are the model's mistakes and both must
come back as results. The boundary is not "our exceptions vs theirs" by type
name; it is the three shapes a model-supplied name or argument can take.

**Fixed** (`76d79f6`): catches `(ValueError, KeyError, OSError)`, everything else
propagates — a 502 from the cockpit, a traceback in the REPL, both loud. Two
tests, one per side of the boundary.

### F11 — Panel errors were reduced to a status code (MED, fixed)

`ui/index.html:190-194`. `get()` threw `` `${path} → ${r.status}` `` and dropped
the body. A `detail()` helper that extracts the server's explanation already
existed two functions below and was used by the chat composer and the persona
switcher but never by the twelve panel reads. So the server names the broken
file, FastAPI puts it in the body, and the browser prints "→ 500".

**Fixed** (`8b39633`).

### F12 — `readCounters` left half the counters stale (MED, fixed — this is M5)

`ui/index.html:404-424`. Confirmed. It also fetched `/api/stats` twice, once at
the top and again five lines later for a field the first response already
contained, which is a second chance to fail for no benefit. And the failure
handler was `console.error`, which in a dashboard is the same as no handler.

**Fixed** (`8b39633`): every read in one `Promise.all`, every write after, and
a failure says so in the subtitle instead of in a console nobody has open.

### F13 — `/api/system` claimed one model for the harness (MED, fixed)

`ninja/server.py:252`, `"model": agent.MODEL`. The brand line under the ninja
logo rendered this, so the cockpit named a model that no longer has anything to
do with which model a turn runs on. The spec kept `agent.MODEL` and
`agent.SYSTEM` on the stated grounds that "the cockpit's guardrails panel reads
live values" — it does not and never did; `guardrails_panel` never references
either constant. They were aliases of `personas.DEFAULT_MODEL` and
`DEFAULT_INSTRUCTIONS`, and `SYSTEM` had no readers at all.

**Fixed** (`65096c5`): the brand reads the active persona's model from
`/api/personas`, which already carried it, and the two orphaned aliases are
deleted. This is a deliberate deviation from the approved spec — flagging it
here rather than burying it. The fallback constants still exist where the
fallback is, in `personas.py`, which is where layer 12's port should read them.

### F14 — `test_query_survives_punctuation` asserted nothing (LOW-MED, fixed — this is M6)

`tests/test_semantic.py:40-44`. Confirmed. **Fixed** (`71ec92f`): checks the
shape, checks `retrieve` and `hits` agree, and pins the two cases that carry the
claim — an apostrophe query still finds its fact, all-punctuation reports no
match. Verified against a `gate()` stubbed to return a fixed non-empty result,
which the old test passed and the new one fails. Local imports hoisted (M7) in
the same commit.

### F15 — `live` tests are invisible to the gate, and I broke one proving it (MED-HIGH, fixed)

`tests/test_live.py:39` read `agent.MODEL`, which F13 deleted. My grep for
callers found it in no module and I did not think about the test that is
deselected by default. `addopts = "-m 'not live'"` in `pyproject.toml:44` means
`uv run pytest -q` reported 77 passed over a test that raised `AttributeError`
the moment anything ran it — and the only thing that runs it is
`.github/workflows/smoke.yml`, which is `workflow_dispatch`-only until a key is
configured (`c20034f`). So the breakage had no net under it at all: not the
local run, not the PR gate, not the post-merge job.

This is a structural gap, not a one-off. Any refactor can break a `live` test
silently, and the deselection that makes the suite free is the same thing that
makes those tests unreachable by every automatic check the repo has. Worth
closing with a collection-only pass over the whole suite — `pytest --collect-only`
imports every module and would have caught this one in under a second, for no
money — wired into the gate alongside ruff.

**Fixed**: the test is replaced rather than repointed, because checking one
module constant was already the wrong check after layer 7 — the model is a
per-persona property now. `test_every_persona_names_a_model_that_exists` walks
`personas.all()` and retrieves each one's model id, which also covers personas
the agent writes for itself from layer 9. Verified by running `pytest -m live`
with a deliberately invalid key: both live tests reached the API and failed with
`anthropic.AuthenticationError`, which proves the bodies executed and
`personas.all()` resolved, without spending anything.

---

## Not fixed, deliberately

### M4 — the unlocked module globals in `server.py`

**Won't fix.** Not because it is not real — two sync FastAPI endpoints run in a
threadpool and `messages, active = ...` is genuinely racy — but because a
`threading.Lock` around the turn is the wrong fix and would be worse than the
bug. It would make a second chat request block for the full duration of the
first, ten seconds of a dead UI with no feedback, in order to protect a
single-user dashboard bound to `127.0.0.1` whose send button is disabled while a
request is out.

The real fix is that a transcript should belong to a conversation rather than to
the module, which is a structural change layer 8 has to make anyway: delegation
means several turns in flight at once with their own message lists, and at that
point "the messages" stops being a sensible module global regardless of locking.
Doing it now would be building layer 8's data model on layer 7's schedule.

Note that this branch did widen the exposure — `active` now rides the same
unprotected write-back — and F3's fix narrows it back a little, since `active`
is now written only by requests that asked for it.

### Things worth knowing, below the fix threshold

- **`/api/tools` is persona-blind.** `ninja/server.py:153`. The Tools panel
  always lists all three tools with no indication that the active persona may
  only call two. It is not wrong (it describes the harness, not the persona) and
  the Personas panel has the per-persona list, but the two panels now disagree
  in a way a reader has to reconcile themselves.
- **`Persona.schemas()` returns shared mutable dicts.** `ninja/personas.py:40`.
  The list is fresh, the dicts inside it are the ones in `tools.SCHEMAS`.
  `frozen=True` does not reach them. Nothing mutates them today.
- **The fallback has already drifted.** `personas.DEFAULT_INSTRUCTIONS`
  (`ninja/personas.py:26`) and `personas/assistant/PERSONA.md` are supposed to
  be the same assistant; the file tells the model when to use `remember` and the
  constant does not. Two sources for one fact, which is the thing the spec
  correctly refuses to do for the personas panel.
- **`str(KeyError('path'))` is `"'path'"`.** A tool error whose whole content is
  a quoted word is a poor thing to hand back to a model. One line
  (`f"{type(exc).__name__}: {exc}"`) would fix it; left alone as unrelated to any
  finding above.

---

## Can layer 8 be built on this?

Mostly yes, with two pieces of rework that are cheaper to do deliberately now
than to discover later.

**What is already right.** `run_turn` takes its message list as a parameter and
never reaches for module state, so "fresh context" is `run_turn(client, [], ...)`
and nothing else. The persona is already an immutable value passed down a call
stack, which is exactly what a `depth + 1` recursion needs. The subset rule is
`set(child.tools) <= set(parent.tools)` against data that already exists. The
`description` field layer 8 routes on is in place and tested for length.

**Rework 1: `tools.run` has nowhere to put the context.** Its signature is
`run(name, args, allowed)`. A `delegate(persona, task)` tool needs the client,
the calling persona, the current depth and the trace to do its job, and none of
them can reach it. That means either a context parameter threaded through every
call site, or `delegate` dispatched somewhere other than `tools.run` — which
would put the one tool that creates privilege outside the gate that enforces it.
Decide which before writing the tool. The spec's own rejection of a permissive
default for `allowed` is the right instinct to apply here too.

**Rework 2: the traces table cannot describe a tree.** See F1. `depth`,
`parent_trace_id` and `persona` want to be columns, and today they cannot become
ones without a migration mechanism the repo does not have. This is the single
biggest thing standing between this codebase and layer 8.

**One thing to watch.** Delegation multiplies model calls, and cost is computed
from a dict keyed by model id (F2). A fan-out of sub-agents each naming an
unpriced model costs, as far as this harness can tell, nothing at all.
