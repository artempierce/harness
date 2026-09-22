# Testing

The suite is free, offline and fast — 100 tests in under a second, no API key,
no network, a throwaway database per test. That is not an accident of scale. It
is the property everything else here is designed around, because a suite that
costs money or takes a minute is a suite that stops being run.

```bash
uv run pytest -q          # the whole thing, minus the two that spend money
uv run ruff check .       # E,F,B,S,SIM,UP,I
```

`CONTRIBUTING.md` covers how changes land. This covers what a test in this repo
has to be.

---

## Why this document exists

Nearly every bug this project has had was **silent rather than loud**. Not a
crash, not a stack trace — a wrong answer that looked like a right one:

| The bug | What it looked like |
|---|---|
| A dashboard rendering `undefined` | `fetch` does not reject on a 4xx, so the error path never ran |
| A corrupted message list | Every request after it failed, until a restart |
| A test asserting on a list the code never received | Green, forever |
| `test_query_survives_punctuation` | Called the gate, asserted nothing about the result |
| `undefined(undefined)` in every trace since layer 5 | A renderer that knew two of three event types |
| A `live` test reading a deleted constant | Deselected by default, so nothing ran it |
| A typo in a persona's `tools:` | The persona loaded and listed, holding one capability less |

Six of the seven are invisible to a test that only checks a status code, an
exception type, or that nothing blew up. So the bar here is not coverage. It is
whether a test **can tell you something you did not already know**.

---

## What each file is responsible for

| File | Owns | Depends on |
|---|---|---|
| `tests/conftest.py` | The four seams: temp DB, `StubClient`, `response()`/`block()`, `a_persona()` | — |
| `tests/test_tools.py` | **The security boundary.** Path escapes, symlinks, hidden files, the allowlist | Nothing stubbed — real filesystem |
| `tests/test_loop.py` | `run_turn`: tool rounds, result batching, the step cap, the model-error/harness-bug line | `StubClient` |
| `tests/test_personas.py` | The loader as a parser of hostile input, and `schemas()` as enforcement point one | Real files in `tmp_path` |
| `tests/test_skills.py` | The frontmatter parser as a parser of hostile input, the keyword matcher, and rendering | Real files in `tmp_path` |
| `tests/test_semantic.py` | The gate's decisions and FTS5 query building | Real SQLite + FTS5 |
| `tests/test_episodic.py` | The recall window, and the rule that it cannot start on an assistant message | Real SQLite |
| `tests/test_trace.py` | Pricing, totals, truncation, the unpriced flag, and `ninja trace <id>` rendering | — |
| `tests/test_server.py` | Every endpoint, both outcomes: the panels, the 400s, the 502, and what a failure leaves behind | `TestClient` + `StubClient` |
| `tests/test_repl.py` | `/persona` as a command: it never becomes a user turn, and one bad file does not end the session | `capsys` |
| `tests/test_live_wiring.py` | That the deselected tests are still wired to the code they test, and still deselected | `ast`, free |
| `tests/test_live.py` | **The only file that spends money.** One real turn, one model-id check | The real API |

### What nothing covers

Written down because an unlisted gap is a gap somebody assumes is filled.

- **`ui/index.html`.** Zero tests. Every UI bug this project has had — the
  `undefined(undefined)` trace step, panel errors reduced to a status code,
  half-stale counters — was invisible to the suite and will be again. The
  Python-side mitigation is that `ninja trace <id>` renders the same three event
  kinds and now has a test; the browser does not.
- ~~**Migration.**~~ Covered since `tests/test_migrations.py`. `sql/schema.sql`
  is now frozen at its original shape and every change is a numbered file in
  `sql/migrations/`, applied when `PRAGMA user_version` is behind. A fresh
  database replays the whole chain rather than being shortcut to the current
  shape, so every test run exercises every migration — the property that stops
  this gap reopening. The guard against it reopening by another route is
  `test_the_frozen_schema_does_not_carry_migrated_columns`: adding a column to
  `schema.sql` instead of writing a migration looks like it works and silently
  does nothing for databases that already exist.
- **Concurrency.** Layer 8a deleted `server.messages` and `server.active`, so
  there is no shared mutable state left in the process — but the race moved
  into `chat_log` rather than disappearing. Two concurrent turns on one thread
  can still interleave their writes. `save_exchange` puts both halves of an
  exchange in one transaction so a crash cannot tear them, and `recall`
  enforces alternation so a thread that does get torn recovers on the next
  turn instead of refusing every later one. `TestClient` calls are sequential,
  so no test here can produce the interleaving; the tests cover the recovery,
  not the race.
- **Scale.** bm25 over three facts is not bm25 over three thousand. The gate's
  "no relevance floor" decision is correct at the size the tests run at and is
  untested at the size that would change it.
- **`ninja/cli.py`.** Argument parsing has no test.
- **`/api/traces/{id}` answers 404-shaped data with a 200** and a test pins that
  behaviour. It is the anti-pattern below, kept because the cockpit reads it;
  when the UI is changed, change both.
- **Answer quality.** Nothing here knows whether a reply was any good. That is
  layers 9–11, below.

---

## The four seams

Each one makes something cheap and hides something. Knowing which is the
difference between a fast suite and a false one.

### `StubClient` — the model

Replays a scripted list of responses and records what it was sent. Every loop
path — a plain answer, six tool rounds, a refused tool, the step cap — costs
nothing and is deterministic.

**It hides the request being right.** A retired model id, a renamed keyword
argument, a `stop_reason` nobody scripted, a content block type the loop does
not handle: the stub accepts all of it happily. It also invents token counts
(100 in, 20 out), so any test asserting on tokens is asserting on the fixture.
`tests/test_live.py` is the only cover for that, and `tests/test_live_wiring.py`
is what keeps it honest.

### The temp database — autouse, every test

`monkeypatch.setattr(trace, "DB", tmp_path / "state.db")`. Real SQLite, real
FTS5, real bm25 — the queries are genuinely exercised, not mocked — and no test
can see another's rows.

**It hides scale**, described above.

It used to hide migration too, and the fix for that is worth knowing about
because it is not a fixture. `ninja/server.py` opens the database at *import*
time — `messages = episodic.recall()` at module level — and pytest imports test
modules during collection, before any fixture runs. So importing
`tests/test_server.py` reached the real `.ninja/state.db`. That was harmless
while `connect()` only ran `CREATE TABLE IF NOT EXISTS`; once it also ran
migrations, collecting the suite would migrate the developer's own database.
`conftest.py` therefore redirects `trace.DB` at **import**, not in a fixture,
and the autouse fixture still gives each test its own file. Fixtures cannot
protect against something that happens before fixtures.

### The frozen `Persona` — `a_persona()`

One line to get a coach with a single tool. Loop tests never touch the disk.
`frozen=True` is itself load-bearing: a persona is resolved once per turn and
read for the whole of it.

**It hides the file format.** `a_persona()` never parses YAML, so everything
about how a `PERSONA.md` becomes a `Persona` is covered only by
`tests/test_personas.py`. If the fixture's defaults drift from what a real file
produces, every loop test keeps passing against a persona no file can express.

### `TestClient` — the cockpit

Real routing, real pydantic validation, real status codes, in-process — so
monkeypatching `server.messages` or `personas.DIR` works, which is what makes
the failure-path tests possible at all.

**It hides the browser entirely** (`ui/index.html` is served as a file and never
executed) **and it re-raises server exceptions by default**, so an unhandled
error surfaces in pytest as a test error rather than as the bare 500 a real user
would get. When a test asserts a 500 *with a body*, that is the distinction it
is defending.

---

## The bar

**Every test must be able to fail.** If you cannot make it fail by breaking the
thing it covers, it is not a test — delete it or replace it.

For anything touching security, an invariant, or a failure path, prove it by
mutation and say so in the commit message:

```bash
# 1. sabotage the code under test — one realistic wrong line, not a syntax error
# 2. run only that test:      uv run pytest tests/test_x.py::test_y -q   → must be RED
# 3. restore
# 4. uv run pytest -q                                                    → must be GREEN
```

A realistic mutation matters. `if allowed and name not in allowed` is a
mutation. Deleting the function body is not — anything catches that, and passing
against it proves nothing.

### Doing it by machine

`mutmut` automates the loop above. It is configured in `pyproject.toml` and run
from the **mutation testing** workflow, which is manual: mutmut runs the whole
suite once per mutant, so it takes minutes where the gate takes seconds.

```bash
uv run mutmut run              # everything under ninja/
uv run mutmut run ninja.tools  # one module
uv run mutmut results          # what got away
```

Two things about reading the output, both of which look alarming and are not:

**`mutmut results` lists only survivors.** Killed mutants are absent, so an
empty list is the good outcome. A tally that says "0 killed" is a tally that
counted the wrong thing.

**Survivors are expected and are not a to-do list.** Most are string-literal
changes no assertion should reasonably pin — `"\n".join` becoming `"XX\nXX".join`
survives because `test_list_and_read_work` checks that a filename appears in the
listing, not how the lines are separated, and pinning the separator would make
the test worse. Others are equivalent mutants that cannot be killed at all.
Read the list for the one that makes you wince; do not chase it to zero.

The survivor worth acting on is the one where the mutation changes behaviour a
caller would notice. When `or` became `and` in the allowlist guard, a test
caught it — that is the class this tool exists to police.

## Coverage

Coverage runs on every `pytest` invocation and prints uncovered line numbers.
The gate holds it at **89%** via `--cov-fail-under`, which is what the suite
measures today rather than a target. It is a ratchet: raise it deliberately when
tests are added, never lower it quietly to make a build pass.

Two details that will otherwise cost you an afternoon:

- The table prints a **rounded** figure. It reads 90%; the real number is
  89.58%, and `--cov-fail-under` compares against the real one. A threshold set
  from the printed number fails the build on a run the report calls passing.
- `--cov-fail-under` is in the workflow, **not** in `addopts`, because in
  `addopts` it would also apply to `pytest -m live` — two tests that cover
  almost nothing and would fail every smoke run.

**Coverage finds absence, not weakness.** It answers "did this line run", which
is a different question from "would anything notice if it ran differently".
Every anti-pattern named above executed the code it failed to test and would
have shown green lines. What coverage is genuinely good at is the gap it found
the day it was added: `ninja/cli.py` at 0%, five layers old and never once
exercised. Use it to find code nothing touches, and mutation testing to find
code nothing checks.

### Named anti-patterns

All seven have happened here.

1. **The assertion that was never made.** Calling the function under test and
   asserting only that it returned something, or that a type is a type.
   *Tell:* an `assert isinstance(...)` or a bare `assert x` carrying the weight.
2. **The test that never received the code under test.** Asserting on a list
   the function never got, on the fixture rather than the result.
   *Tell:* the expected value appears literally in the test's own setup.
3. **Status-code-only.** A 200 can carry an error. `fetch` does not reject on a
   4xx. If the body is what the UI renders, the body is what the test reads.
4. **The fallback branch that renders garbage.** A renderer, parser or
   dispatcher that handles two of three cases does not crash on the third — it
   produces something plausible. Assert that each case came out of *its own*
   branch, and that the output contains no `None` and no `undefined`.
5. **The deselected test.** Anything excluded from the default run has no net
   under it. Either something free checks its wiring, or it is dead code that
   costs money to discover.
6. **The duplicate that claims more than it proves.** Two sequential calls with
   no shared state do not prove there is no shared state. Ask what
   implementation would pass this test and still be wrong.
7. **Relaxing a security test to make a feature work.** The path boundary and
   hidden-file assertions in `tests/test_tools.py` are not adjustable. If a
   feature needs them changed, that is the discussion, not the workaround.

### Style

Test names read as sentences —
`test_a_refused_tool_never_reaches_its_side_effect`, not `test_allowlist_2`. A
comment above the test says **why the case matters**, ideally naming the bug
shape it is there to catch. The name says what; the comment says what it costs
when it breaks.

---

## Money

**Exactly one file may spend money: `tests/test_live.py`.** Two tests today —
one real turn, and a model-id check across every persona on disk.

The rules:

1. **Deselected by default.** `addopts = "-m 'not live'"` in `pyproject.toml`,
   and `tests/test_live_wiring.py` asserts that line still exists. A default run
   is free, always, including in the PR gate.
2. **Run deliberately.** `.github/workflows/smoke.yml`, `workflow_dispatch`
   only. Never in a loop, never as part of an automated agent session, never
   "just to check".
3. **A live test earns its place only if it crosses a boundary nothing free
   can.** The key, the model id, the endpoint, the wire shape of a request.
   Everything else — the loop, the gate, the allowlist, the trace — is stubbed
   and free, and that is not a compromise, it is better: deterministic and
   instant.
4. **Every live test has a free counterpart** that covers as much as can be
   covered offline. `test_every_persona_names_a_model_that_exists` costs a
   retrieve per persona; its free half,
   `test_every_persona_on_disk_names_a_model_the_harness_can_price`, cannot
   confirm the id exists but does catch the model this harness cannot bill.
5. **Smallest thing that still crosses the boundary.** The live turn asks for
   one word on haiku and asserts nothing about the wording.

---

## Evals — layers 9 to 11

Not built. This is the position to build from, because the decisions that make
evals cheap have to be made before there are two hundred cases.

### Two scores, and only one of them needs a model

`ARCHITECTURE.md` already splits it: **was it good**, and **was it healthy**.

*Healthy* comes straight from the trace and is **always deterministic**: model
calls, tool calls, which tools, gate retrieve/skip and why, tokens, cost,
duration, `unpriced` flags, `ok: false` events. No judge, ever. Most of what
goes wrong in an agent is a health failure — six model calls where one would
do, a retrieval that should have skipped, a tool refused and retried four times
— and every one of those is a `==` against a number this repo already records.

*Good* is about the reply, and most of it is still deterministic.

### The golden dataset

YAML under `evals/cases/`, one file per case, in the shape of the trace rather
than the shape of a conversation:

```yaml
id: gate-skips-arithmetic
persona: assistant
seed_facts:
  - Sol is building an agent harness called Ninja
input: what is 2 + 2?
expect:
  gate: skip                 # from the trace, exact
  tools_called: []           # from the trace, exact
  max_model_calls: 1         # from the trace, a bound
  reply_contains: ["4"]      # cheap textual
  max_cost_usd: 0.002        # from trace.price — and see the unpriced rule
```

Properties worth keeping:

- **Cases are files, not code.** Layer 14 says a new tool ships with YAML eval
  cases; this is that format, arriving early.
- **The expectations name trace facts.** A case that can only be checked by
  reading prose is a case that will need a judge, which is the expensive path —
  so write the trace assertion first and see whether the prose one is still
  needed.
- **Seeded state is part of the case.** `seed_facts` and an optional
  `transcript` go into the temp database, so a case is reproducible and cannot
  be polluted by whatever was remembered yesterday.
- **A case that has never failed is a candidate for deletion.** Cases earn their
  runtime by discriminating.

### When a judge is warranted

In order. Stop at the first one that works:

1. **A structural assertion on the trace.** Prefer always.
2. **A cheap textual assertion** — `contains`, `not_contains`, a regex, a length
   bound. "The coach's reply ends in a question mark" is a real assertion about
   a real behaviour and costs nothing.
3. **A model judging.** Only when the criterion is about prose that has many
   correct surface forms and no invariant to pin: *did the coach follow up on
   the thin part of the answer rather than hand over the answer*. There is no
   regex for that.

A judge is a worse test than an assertion at every level — slower, costs money,
and non-deterministic, so a flake is indistinguishable from a regression. Use
one where it is the only option, and hold it to rules:

- **Binary, against a named criterion per case.** Not a 1–10 score. "Did it ask
  a question instead of answering?" is auditable; "quality: 7" is not.
- **The judge never sees the expected prose**, only the criterion and the output.
- **The judge is a persona** — it has instructions, a tool list (empty), and a
  model, and it lives in `personas/` like the rest of the cast. It gets versioned
  and reviewed like the rest of the cast too.
- **Every verdict is reproducible from what was stored**: the judge's model id,
  its prompt, and its answer go into the run record beside the trace id.
- **A judge failure is a diagnosis prompt, not a result.** Read the trace.

### Keeping it near zero

- **Two tiers.** The deterministic tier replays recorded traces — no API at all
  — and runs in the PR gate as ordinary pytest cases parametrized over the YAML
  files. The live tier runs manually before a release, `live`-marked, under the
  money rules above.
- **Record once, replay forever.** A `ReplayClient` is `StubClient` reading its
  script out of a stored trace's events. The loop, the gate, the allowlist, the
  prompt assembly, the tool dispatch and the cost arithmetic are all exercised
  for free; only the model's wording is frozen. Re-record a case when the
  behaviour it covers changes on purpose — that re-recording is the review.
- **Judge only what tier 1 could not decide**, and only the cases whose criterion
  is prose. If half the suite needs a judge, the cases are written wrong.
- **Bound the run.** Compute the cost from `trace.price` and stop at a ceiling.
  Treat an `unpriced: true` event as *no bound available*, not as free — a cost
  ceiling that reads an unknown model as zero is a ceiling with a hole in it,
  and from layer 9 the agent writes the file that names the model.
- **Haiku for the live tier** unless the case is specifically about a larger
  model.

### What layers 9 and 10 get for free

Layer 9's architect proposes `PERSONA.md` files, and the loader is already a
strict judge of those: invalid YAML, a non-mapping, a blank required value, an
unknown tool name, a `tools:` that is not a list, a name that disagrees with its
directory, a name that is a path. A proposal dataset is therefore mostly
deterministic — *does it load, does it name a model the harness can price, does
it pass the "strip the instructions and see if the output gets worse" test* —
and only the last of those needs a human or a judge.

Layer 10's consolidation is the opposite: transcripts in, facts out. Structure
is deterministic (no duplicates, third person, self-contained, a count bound);
whether the fact is actually *entailed* by the transcript is the clearest case
in this whole document for a judge.

---

## When a bug gets through

Write the test that would have caught it, first, and watch it fail. Then fix it.
The test is the deliverable; the fix is the easy part.
