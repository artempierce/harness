# Phase A1 ‖ B1 — running in parallel

**Status:** proposed. Read this with the two specs.

## Independence, checked against the code

| | A1 learned rules | B1 consolidation |
|---|---|---|
| New files | `.ninja/rules/` (runtime) | migration `003`, `ninja/consolidation.py` |
| `tools.py` | new `add_rule`, `run(..., persona=)` | — |
| `personas/*/PERSONA.md` | both gain `add_rule` | — |
| `agent.py` | `build_system` (line ~32), tool call (line ~86) | post-turn, between `run_turn` and `finish` (line ~186) |
| `server.py` | — | `chat`, post-turn (line ~90) |
| `semantic.py` | — | `remember(..., conn=None)` |
| Migrations | none | `003` |
| Tests | `test_tools`, `test_personas` additions | new `test_consolidation` |

Only `agent.py` is touched by both, in **different regions** (lines ~32 and ~86 vs
~186). Git will merge that cleanly. No shared migration number, no shared table.

## How to run them without collisions

Two git worktrees, one branch each, both from `main`:

```
../ninja-agent-a1   →  phase-a1-learned-rules
../ninja-agent-b1   →  phase-b1-consolidation
```

Each has its own `.ninja/` database, so a test run in one cannot disturb the
other. The suite already redirects to a temp database per test.

## Merge order

1. Either A1 or B1 first — no dependency between them.
2. The second rebases onto `main` and re-runs the suite. Expected conflicts: none.
3. **A2 starts only after both merge.** It mirrors rules (from A1) *and* episodes
   (from B1) into `MEMORY.md`, and hooks the same post-turn spot B1 uses.

## What parallelism does and does not buy

It saves wall-clock time. It does **not** reduce tokens — total work is the same
and two streams consume it twice as fast. The specs are sized so each PR is
reviewable alone.

## Process for each stream (per PLAN.md §5)

One implementer, **one** review of the finished PR, not per-task. A single
verification pass at the end: full suite, ruff, coverage ≥ 89%.
