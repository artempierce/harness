# E1a: the judge — design

**Status:** approved by Sol.

**Roadmap position:** `docs/PLAN.md` §4, Phase E, E1a. First slice of Phase E
("did that change help?"). Ticketed as GitHub issue #26. Unblocked — nothing
in Phase D gates it.

## 1. Problem

`docs/TESTING.md`'s "Evals — layers 9 to 11" section already designed most of
this (written during D1, not built yet): a judge is a binary verdict, against
a named criterion, that never sees the expected answer — used only when a
structural or textual assertion on the trace can't decide the question. E1a
builds that primitive: a function that scores one piece of text against one
criterion and returns a reproducible verdict. Nothing else — not the golden
dataset (`evals/cases/*.yaml`), not the replay tier, not aggregation across
cases. Those are later E1 work and are not blocked on this being any bigger
than it needs to be.

## 2. Scope

**In scope:**
- `ninja/judge.py`: `judge(client, criterion: str, output: str) -> Verdict`.
- A dedicated judge model config (persona-shaped, not a conversational one —
  see §3.2 for why it's not a `PERSONA.md` file).
- A new `Trace.judge(criterion, passed)` event, rendered in `ninja trace` and
  the cockpit.
- Tests, stubbed, free, offline — no live judge calls in the suite (matches
  `docs/TESTING.md`'s money rules: only `tests/test_live.py` spends real
  money).

**Out of scope, explicitly:**
- **The golden dataset and eval-case runner** (`evals/cases/*.yaml`, the
  deterministic replay tier). E1a is the primitive those will call; building
  the runner without a working primitive to call would be inventing structure
  ahead of the thing it structures.
- **Aggregation, health scores, the release gate.** That's E1b/E1c, both
  already filed and both blocked on this ticket.
- **A cost ceiling for a run of many judge calls.** `judge()` is a
  single-call primitive; a ceiling is a property of a *run* of them, which
  belongs in whatever calls this in a loop (the eval runner, later).
- **Wiring into the live turn loop.** `judge()` is never called automatically
  after a real conversational turn. It costs money only when a caller
  explicitly asks it to.

## 3. Design

### 3.1 The function

```python
def judge(client, criterion: str, output: str) -> Verdict
```

- `criterion`: one sentence naming what's being checked — never the expected
  answer, only the question.
- `output`: the text being judged. Source-agnostic: a live reply, a stored
  trace's `reply` column, anything. `judge()` has no opinion on where it came
  from.
- Builds a prompt of exactly `criterion` + `output`, nothing else — no facts,
  no skills, no persona instructions beyond the judge's own strict-binary
  prompt. A judge that saw "what you know about this person" could be biased
  by context that has nothing to do with the criterion it's checking.
- Runs through the existing `run_turn` loop (imported from `ninja.agent`) —
  not a new call path. The judge holds no tools, so the loop's tool-round
  branch never triggers; it returns on the first `stop_reason != "tool_use"`,
  getting `CUT_SHORT`/refusal handling for free instead of a second
  implementation of it.
- Creates and finishes its **own** `Trace` — every judge call is its own row
  in `traces`, inspectable with `ninja trace <id>` like any turn. This is
  where `docs/TESTING.md`'s "every verdict is reproducible from what was
  stored: the judge's model id, its prompt, and its answer go into the run
  record beside the trace id" is satisfied — the trace *is* that record
  (`user_input` is the prompt, the `model` event names the model, `reply` is
  the answer); `Trace.judge()` only needs to add the criterion and the
  parsed verdict, not duplicate what the trace already carries.
- Parses the reply as exactly `PASS` or `FAIL` (case/punctuation-insensitive,
  same leniency `router.py` already uses for its own single-word replies). A
  reply that parses as neither raises `JudgeParseError` — **after** the trace
  is still finished and recorded, so the failure is diagnosable
  (`docs/TESTING.md`: "a judge failure is a diagnosis prompt, not a result").

### 3.2 The judge's "persona"

`docs/TESTING.md` describes the judge as "a persona — it has instructions, a
tool list (empty), and a model, and it lives in `personas/` like the rest of
the cast." E1a keeps the *shape* (instructions + empty tools + a model — a
plain `Persona` dataclass) but **not** the file: it's a Python constant in
`ninja/judge.py`, not a `personas/judge/PERSONA.md`.

**Why the deviation:** `personas.all()` — read by the router (`router.route`,
called every turn to pick a thread), `/persona`'s listing, the delegation
subset rule, and the cockpit's cast panel — has no concept of "internal
only." A real `PERSONA.md` would make "judge" a routable conversation thread:
a real message that happens to read as "can you judge whether X is good"
could get filed into it by the router, where its strict PASS/FAIL-only
instructions would produce a useless reply to a real person. Fixing that
properly means inventing an exclusion convention (a frontmatter flag, a
directory prefix) for a single use case — real scope, not owed to this
ticket. Keeping it out of `personas/` entirely sidesteps the whole problem:
zero router/delegate/cockpit exposure, zero new filtering code anywhere.
It's still "reviewed like the rest of the cast" (`docs/TESTING.md`'s other
stated reason) — a Python constant goes through the same review as any other
line of code, arguably more rigorously than a markdown file would.

```python
PERSONA = Persona(
    name="judge",
    description="Internal: scores one output against one criterion. Never "
                 "routed to, never delegated to — not a conversational persona.",
    instructions=(
        "You are a strict binary judge. You will be given a criterion and an "
        "output to check it against. Reply with exactly one word: PASS if the "
        "output satisfies the criterion, FAIL if it does not. Nothing else — "
        "no explanation, no punctuation."
    ),
    tools=(),
    model="claude-haiku-4-5",
)
```

### 3.3 `Verdict` and error handling

```python
@dataclass(frozen=True)
class Verdict:
    passed: bool
    criterion: str
    trace_id: int


class JudgeParseError(ValueError):
    """The judge's reply didn't parse as PASS/FAIL. `trace_id` points at the
    full prompt/model/reply — `ninja trace <id>` to see why."""
    def __init__(self, reply: str, trace_id: int):
        super().__init__(
            f"could not parse PASS/FAIL from the judge's reply — see trace "
            f"{trace_id}: {reply!r}"
        )
        self.trace_id = trace_id
```

`trace_id` is the whole reproducibility story — no separate storage of model
id/prompt/answer on `Verdict` itself, because the trace already has all
three.

### 3.4 `ninja/trace.py`

One new method, following `skills()`'s shape (record only what the generic
event fields don't already carry):

```python
def judge(self, criterion: str, passed: bool) -> None:
    """The verdict this trace's own model call reached. The prompt, the
    model, and the answer are already this trace's user_input/model
    event/reply; this only names the criterion and the result."""
    self.events.append({"type": "judge", "criterion": criterion, "passed": passed})
```

`print_one` gains a branch (placed alongside the existing `skills` branch):

```python
elif e["type"] == "judge":
    mark = "PASS" if e["passed"] else "FAIL"
    print(f"  {i:>2}. judge     {mark:<4}  {e['criterion']}")
```

### 3.5 `ui/index.html`

One new branch in `step()`, in the same position as the equivalent `skills`
branch, checked proactively this time rather than caught by a final review
(the exact bug class D1 shipped with once already):

```js
if (e.type === 'judge') return `<div class="step"><span class="k">${i+1}</span>
  <span class="k">judge</span>
  <span class="d">${e.passed ? 'PASS' : 'FAIL'} — ${esc(e.criterion)}</span></div>`;
```

### 3.6 Failure modes

| Situation | Behavior |
|---|---|
| The judge's API call fails outright (network, auth) | Propagates as an exception — `run_turn`'s own call is unguarded here, same as every other direct model call in the codebase; there is no "stay put" fallback the way `router.route` has, because there is no current state to stay in |
| The reply doesn't parse as PASS/FAIL | `JudgeParseError`, trace still recorded and finished first |
| The judge is asked to judge something enormous | No special handling — `output` goes straight into the prompt; a caller judging a huge blob pays for a huge prompt, same as any other model call. Not a new failure mode this ticket introduces |

## 4. Testing

- `tests/test_judge.py` (new, mirrors `tests/test_router.py`'s shape and
  `StubClient` usage): a passing verdict, a failing verdict, lenient
  matching (case/punctuation), an unparseable reply raises `JudgeParseError`
  and still leaves a readable trace row, the prompt contains only the
  criterion and the output (no facts/skills/persona leakage), tokens/cost
  land on the judge's own trace, `PERSONA.tools == ()`.
- `tests/test_trace.py`: extend
  `test_the_trace_viewer_prints_every_kind_of_event` with a `t.judge(...)`
  call and its expected output — the same regression class the existing
  test's own comment names.
- No changes to `docs/TESTING.md`'s "What nothing covers" list needed for
  `ui/index.html` — it's already listed there as zero-tested; this ticket
  doesn't change that boundary, only checks the specific claim by direct
  inspection during the final review, the way D2 did for the same file.

## 5. Decision log

- **Not a `PERSONA.md` file** (§3.2): the practical realization that
  `personas.all()` has no "internal" concept, and a real judge file would
  leak into router/delegate/cockpit surfaces a real user could hit. Ruled
  during brainstorming, not left for a reviewer to catch.
- **`judge()` always creates its own `Trace`**, rather than accepting an
  existing one to append into: keeps the primitive fully self-contained and
  independently inspectable. A future eval runner composing many verdicts
  collects the returned `Verdict` objects (each with its own `trace_id`)
  rather than sharing trace state — revisit only if that turns out to be
  awkward in practice, which nothing yet says it will be.
- **No cost ceiling in `judge()` itself**: a ceiling is a property of a run
  of many calls, which this ticket doesn't build yet (§2).
