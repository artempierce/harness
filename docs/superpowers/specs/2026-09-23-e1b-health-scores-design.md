# E1b: health scores — design

**Status:** approved by Sol.

**Roadmap position:** `docs/PLAN.md` §4, Phase E, E1b. Second slice of Phase
E. Ticketed as GitHub issue #27, blocked by E1a (#26, merged — `ninja/judge.py`).

## 1. Problem

E1a built `judge()`: one criterion, one output, one binary verdict, its own
trace row. E1b turns a set of those verdicts into a number worth looking at —
a per-persona rate, the metric F1's architect persona will later reason about
when deciding whether a change helped.

## 2. Scope

**In scope:**
- `health_scores(verdicts: list[tuple[str, Verdict]]) -> dict[str, HealthScore]`
  in `ninja/judge.py` — pure aggregation over whatever verdicts the caller
  already has.
- `HealthScore`, a frozen dataclass: `persona: str`, `passed: int`,
  `total: int`, `rate: float`.

**Out of scope, explicitly:**
- **The eval-case runner** (`evals/cases/*.yaml`, actually running personas
  against golden cases and judging the outputs). Nothing in this repo
  produces a stream of `(persona, Verdict)` pairs yet — `docs/TESTING.md`
  describes the runner at length, but it was never ticketed on its own, and
  E1a's spec explicitly scoped it out too. `health_scores()` doesn't need
  the runner to exist any more than `judge()` needed a golden dataset to
  exist — it takes what the caller hands it. The runner is a real gap;
  it's a separate, still-unticketed piece of work, not something to invent
  the shape of here.
- **`docs/TESTING.md`'s "healthy" metric** (deterministic trace facts — model
  calls, tool calls, gate retrieve/skip, tokens, cost, `unpriced`/`ok: false`
  flags — "no judge, ever"). That is a different, smaller, purely-SQL-over-
  `traces` piece of work that isn't blocked on E1a at all. Issue #27's title
  says "health scores" but its body says "aggregate judged scores" — this
  spec follows the body: it's the judge-based ("good", in `docs/TESTING.md`'s
  vocabulary) side, not the deterministic one. If the deterministic side
  turns out to be wanted too, it's a new ticket, not a silent addition here.
- **The release gate** (block/allow a merge on a threshold). That's E1c
  (#28), blocked by this ticket.
- **Persistence.** `health_scores()` returns a dict; it doesn't write
  anywhere. Nothing yet needs a stored history of health scores over time —
  that's a call to make once there's a real caller (the runner, or E1c).

## 3. Design

### 3.1 The function

```python
def health_scores(verdicts: list[tuple[str, Verdict]]) -> dict[str, HealthScore]
```

- Each tuple pairs a persona name with a `Verdict` E1a's `judge()` produced.
  `health_scores()` has no opinion on how the pairing happened — the same
  design stance `judge()` takes toward `output`'s source (§3.1 of the E1a
  spec: "source-agnostic... has no opinion on where it came from").
- Groups by persona name, computes `passed = sum(v.passed for _, v in group)`,
  `total = len(group)`, `rate = passed / total`.
- A persona with zero verdicts simply doesn't appear in the result — there is
  no "0/0" entry to define a rate for.
- Pure function: no `Trace`, no database, no side effects. Nothing here
  costs money or writes anything — the money and the writes already happened
  when each `Verdict` was produced.

### 3.2 `HealthScore`

```python
@dataclass(frozen=True)
class HealthScore:
    persona: str
    passed: int
    total: int
    rate: float
```

`rate` is stored rather than computed on read because a frozen dataclass has
no cheap way to expose a derived property without also exposing the raw
counts — and callers (F1, eventually) want both: the rate to reason about,
and `passed`/`total` so a rate of `1.0` from one case reads differently from
`1.0` from fifty.

### 3.3 Where it lives

`ninja/judge.py`, alongside `judge()` and `Verdict` — not a new module. The
whole point of a `Verdict` per verdict and a `HealthScore` per persona is
that one is built directly from a list of the other; splitting them across
files would separate two things that only make sense read together, the
same reasoning `docs/TESTING.md` itself uses when it says "healthy" and
"good" belong in one framing even though they're computed differently.

## 4. Testing

- `tests/test_judge.py` (extended, not a new file — same module):
  - An empty list returns an empty dict.
  - One persona, all passing → `rate == 1.0`.
  - One persona, mixed → `rate` matches `passed/total` exactly (a case that
    would round wrong under integer division if the implementation forgot
    the float cast).
  - Two personas' verdicts interleaved in the input list are still grouped
    correctly — proves grouping, not just filtering.
  - A persona with zero verdicts among the pairs given doesn't appear as a
    `0/0` entry (this one is really "there's nothing to omit," since the
    input only contains what it contains — worth a test anyway so the
    contract is pinned, not assumed).
  - No `StubClient` needed anywhere in these tests — `health_scores()` never
    touches the network or a `Trace`.

## 5. Decision log

- **"Health scores" (the ticket title) means the judge-based aggregation,
  not `docs/TESTING.md`'s deterministic "healthy" metric** (§2). Ruled during
  brainstorming from the ticket's own body text ("aggregate judged scores"),
  not the title. The deterministic metric is real, described, and unbuilt —
  it just isn't this ticket.
- **No eval-case runner built here** (§2). `health_scores()` is intentionally
  agnostic to how its input was produced, exactly mirroring `judge()`'s own
  stance toward `output`. The runner is a genuine, currently-unticketed gap.
- **No persistence** (§2): YAGNI until a real caller exists to say what
  storage shape it needs.
