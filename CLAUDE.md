# CLAUDE.md

Ninja: a personal assistant built as a cast of agents, from scratch, one layer at a time (learning project — see `docs/PLAN.md`). Python ≥3.11, managed with `uv`. Package is `ninja/`, distribution is `ninja-agent`.

## Commands

```bash
uv sync --dev                  # install
uv run pytest -q               # tests (free, offline, ~2s, coverage on)
uv run ruff check .            # lint (E,F,B,S,SIM,UP,I; line length 100)
uv run pip-audit --skip-editable
uv run ninja dashboard         # cockpit at localhost:7777
uv run ninja trace [id]        # inspect a turn
```

Run the first three before pushing; they mirror the PR gate (`.github/workflows/gate.yml`).

## Costs real money — ask first

- `pytest -m live` calls the real Anthropic API. It is deselected by default and runs only in the post-merge smoke workflow. Never run it, or anything else that hits the API, without asking.
- `mutmut` is slow (suite runs once per mutant) and manual only — not part of the gate.

## Rules

- Nothing goes to `main` directly: branch → PR → gate → user approval → merge (`CONTRIBUTING.md`).
- Never read, print or commit `.env`. Only `.env.example` is tracked.
- Never relax the security tests in `tests/test_tools.py` (path boundary, hidden-file refusal) to make a feature work — raise it instead.
- Tests use a throwaway DB and a stubbed model. See `docs/TESTING.md` for what each test file owns and the anti-patterns to avoid.
- No frameworks in the core loop; the point is to keep it readable.
