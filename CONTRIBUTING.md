# How changes land

Nothing is committed to `main` directly. Every layer, fix, or experiment goes
through the same path.

```
branch  →  push  →  pull request  →  quality gate  →  your approval  →  merge
```

## The branch

One per unit of work, named for it:

```bash
git checkout -b layer-5-semantic-memory
```

## The gate

`.github/workflows/gate.yml` runs on every pull request into `main`. Four
checks, all of which must pass:

| Check | Tool | Catches |
|---|---|---|
| Tests | `pytest` | Behaviour changing without anyone noticing |
| Code smells | `ruff` (E,F,B,S,SIM,UP,I) | Bug shapes, insecure patterns, dead imports |
| Vulnerabilities | `pip-audit` | Known CVEs in dependencies |
| Secrets | `git grep` | A tracked `.env`, or an API key in the diff |

Run all four locally before pushing — it's faster than waiting for CI:

```bash
uv run pytest -q
uv run ruff check .
uv run pip-audit --skip-editable
```

## The approval

A green gate is necessary, not sufficient. Merging is a human decision, and the
pull request is where it gets made — the diff, the test results, and the reason
for the change in one place.

This is deliberately the same shape the architecture describes for layers 13–15,
where an agent proposes changes to this repo. The workflow exists first, with a
human writing the code, so the gate is proven before anything else is trusted to
pass through it.

## Tests

`tests/` runs against a throwaway database and a stubbed model, so the suite is
free and offline. Nothing in it spends money or needs an API key.

Security tests in `tests/test_tools.py` — the path boundary and the hidden-file
refusal — must never be relaxed to make a feature work. If a feature needs them
changed, that is the discussion, not the workaround.
