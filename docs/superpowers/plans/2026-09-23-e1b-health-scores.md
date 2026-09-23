# E1b: Health Scores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure `health_scores(verdicts: list[tuple[str, Verdict]]) -> dict[str, HealthScore]` function that groups E1a's `Verdict`s by persona name and computes a pass rate per persona.

**Architecture:** One function and one frozen dataclass added to the existing `ninja/judge.py`, next to `Verdict`. No new file, no `Trace`/database/network involvement — it's a dict-in, dict-out aggregation over whatever verdicts the caller already collected. Tests extend the existing `tests/test_judge.py`. One task: the whole change is small enough, and tightly enough coupled (the dataclass and the function that returns it), that splitting it would only fragment one review.

**Tech Stack:** Python ≥3.11, `uv`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-23-e1b-health-scores-design.md`

## Global Constraints

- Python ≥3.11, managed with `uv`. Lint: `ruff check .` (E,F,B,S,SIM,UP,I), line length 100.
- The suite stays free and offline. `health_scores()` never touches the network, a `Trace`, or the database — no `StubClient` needed in its tests.
- `HealthScore` stores `rate` rather than computing it as a property, so callers get both the ratio and the raw counts (spec §3.2).
- A persona with zero verdicts in the input does not appear in the result — no `0/0` entries (spec §3.1).
- Comments explain *why*, matching this repo's existing density — not a restatement of the code.

---

### Task 1: `health_scores()` and `HealthScore`

**Files:**
- Modify: `ninja/judge.py`
- Modify: `tests/test_judge.py`

**Interfaces:**
- Consumes: `Verdict` (already in `ninja/judge.py`, from E1a: `passed: bool`, `criterion: str`, `trace_id: int`).
- Produces: `judge.HealthScore` (frozen dataclass: `persona: str`, `passed: int`, `total: int`, `rate: float`); `judge.health_scores(verdicts: list[tuple[str, Verdict]]) -> dict[str, HealthScore]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_judge.py`, add these tests after `test_the_judges_tokens_are_priced` (the last existing test in the file):

```python
def _verdict(passed):
    return judge.Verdict(passed=passed, criterion="x", trace_id=1)


def test_health_scores_of_an_empty_list_is_an_empty_dict():
    assert judge.health_scores([]) == {}


def test_health_scores_of_one_persona_all_passing():
    verdicts = [("assistant", _verdict(True)), ("assistant", _verdict(True))]
    scores = judge.health_scores(verdicts)
    assert scores == {"assistant": judge.HealthScore("assistant", passed=2, total=2, rate=1.0)}


def test_health_scores_rate_is_a_true_float_division():
    # 1/3 rounds wrong under integer division if the cast is forgotten.
    verdicts = [
        ("assistant", _verdict(True)),
        ("assistant", _verdict(False)),
        ("assistant", _verdict(False)),
    ]
    scores = judge.health_scores(verdicts)
    assert scores["assistant"].rate == 1 / 3


def test_health_scores_groups_interleaved_personas_separately():
    # Interleaving in the input proves grouping, not just filtering.
    verdicts = [
        ("assistant", _verdict(True)),
        ("interview-coach", _verdict(False)),
        ("assistant", _verdict(False)),
        ("interview-coach", _verdict(True)),
        ("interview-coach", _verdict(True)),
    ]
    scores = judge.health_scores(verdicts)
    assert scores["assistant"] == judge.HealthScore("assistant", passed=1, total=2, rate=0.5)
    assert scores["interview-coach"] == judge.HealthScore(
        "interview-coach", passed=2, total=3, rate=2 / 3
    )


def test_health_scores_omits_personas_with_no_verdicts_in_the_input():
    # There is nothing to omit here — the input simply never mentions a
    # third persona — but the contract (no 0/0 entries) is worth pinning,
    # not assuming.
    verdicts = [("assistant", _verdict(True))]
    scores = judge.health_scores(verdicts)
    assert "interview-coach" not in scores
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_judge.py -v`
Expected: FAIL — `AttributeError: module 'ninja.judge' has no attribute 'health_scores'` (and `has no attribute 'HealthScore'` from the helper referencing it).

- [ ] **Step 3: Implement in `ninja/judge.py`**

Add directly after the `Verdict` class (before `JudgeParseError`):

```python
@dataclass(frozen=True)
class HealthScore:
    persona: str
    passed: int
    total: int
    rate: float


def health_scores(verdicts: list[tuple[str, Verdict]]) -> dict[str, HealthScore]:
    """Group verdicts by persona and compute a pass rate per persona. Pure —
    no Trace, no database, no side effects; the money and the writes already
    happened when each Verdict was produced. A persona absent from `verdicts`
    is absent from the result: there's no rate to report for zero cases.
    """
    by_persona: dict[str, list[Verdict]] = {}
    for persona, verdict in verdicts:
        by_persona.setdefault(persona, []).append(verdict)

    scores = {}
    for persona, group in by_persona.items():
        passed = sum(v.passed for v in group)
        total = len(group)
        scores[persona] = HealthScore(persona, passed, total, passed / total)
    return scores
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_judge.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, no new lint findings, coverage at or above the gate's floor.

- [ ] **Step 6: Commit**

```bash
git add ninja/judge.py tests/test_judge.py
git commit -m "E1b: health_scores(verdicts) -> dict[str, HealthScore]"
```
