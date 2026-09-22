# D2: `create_skill` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the model draft a new skill (`propose_skill`), and let Sol approve or reject it from the REPL (`/approve-skill`, `/reject-skill`) before it becomes a real `skills/<name>/SKILL.md`.

**Architecture:** A draft is staged at `.ninja/pending_skills/<name>/SKILL.md` — the same shape a real skill has, parsed by the same `_parse` D1 already wrote. `propose_skill` (a new tool) only ever writes there. Two new REPL commands, mirroring `/persona`, move a draft into `skills/` or discard it. Nothing about `build_system`, `match`, or tracing changes — a staged draft is invisible to a turn until approved.

**Tech Stack:** Python ≥3.11, `uv`, `PyYAML` (already a dependency).

**Spec:** `docs/superpowers/specs/2026-09-21-d2-create-skill-design.md`

## Global Constraints

- Python ≥3.11, managed with `uv`. Lint: `ruff check .` (E,F,B,S,SIM,UP,I), line length 100.
- Never relax the security tests in `tests/test_tools.py` (path boundary, hidden-file refusal).
- The suite stays free and offline: no real API calls, no network. Every new test uses `tmp_path`/`monkeypatch`, never the real `.ninja/`.
- `.ninja/` is wholly gitignored already (`.gitignore:10`); `.ninja/pending_skills/` needs no new entry.
- `Skill` and `_parse` (both in `ninja/skills.py`, from D1) are unchanged — every new function is built on top of them, not a parallel implementation.
- `WRITE_TOOLS` (in `ninja/agent.py`) gains `"propose_skill"` — a delegated child must never be able to call it, the same rule that already holds for `remember` and `add_rule`.
- `load_all(directory: Path = DIR)` in `ninja/skills.py` is a **widening** of the existing function (a default argument), not a new one — every existing call site (`skills.load_all()`) must keep working unchanged.
- Comments explain *why*, matching this repo's existing density — not a restatement of the code.

---

### Task 1: Staging — propose, approve, reject in `ninja/skills.py`

**Files:**
- Modify: `ninja/skills.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_skills.py`

**Interfaces:**
- Consumes: `Skill` (frozen dataclass: `name`, `description`, `body`), `_parse(text: str, source: Path) -> Skill` — both from D1, unchanged.
- Produces (used by Task 2 and Task 3):
  - `skills.PENDING_DIR: Path`
  - `skills.load_all(directory: Path = DIR) -> list[Skill]` (widened signature)
  - `skills.propose(name: str, description: str, body: str) -> Skill`
  - `skills.approve(name: str) -> Skill`
  - `skills.reject(name: str) -> None`
  - All three raise `ValueError` on a problem the caller (a tool dispatch or a REPL command) can show back to whoever caused it.

- [ ] **Step 1: Write the new tests**

Add `import pytest` to the top of `tests/test_skills.py` (it currently has none), then append this section at the end of the file:

```python
# --- staging: propose / approve / reject -----------------------------------


def test_propose_stages_a_draft_without_touching_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    proposed = skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")
    assert proposed.name == "weekly-review"
    assert skills.load_all() == []  # nothing in the live directory yet
    (staged,) = skills.load_all(skills.PENDING_DIR)
    assert staged == proposed


def test_a_description_with_a_colon_round_trips_through_yaml(tmp_path, monkeypatch):
    # Naive string interpolation into frontmatter breaks on this; yaml.safe_dump
    # must not.
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("planning", "Use when: planning a week ahead.", "body")
    (staged,) = skills.load_all(skills.PENDING_DIR)
    assert staged.description == "Use when: planning a week ahead."


@pytest.mark.parametrize("name", ["Weekly-Review", "weekly_review", "-weekly", "../etc", ""])
def test_a_malformed_name_is_refused(tmp_path, monkeypatch, name):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    with pytest.raises(ValueError):
        skills.propose(name, "d", "body")


def test_an_empty_description_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    with pytest.raises(ValueError, match="description"):
        skills.propose("weekly-review", "   ", "body")


def test_an_empty_body_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    with pytest.raises(ValueError, match="body"):
        skills.propose("weekly-review", "d", "   ")


def test_approve_moves_the_draft_into_the_live_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")
    approved = skills.approve("weekly-review")
    assert approved.name == "weekly-review"
    (live,) = skills.load_all()
    assert live == approved
    assert skills.load_all(skills.PENDING_DIR) == []  # the draft is gone


def test_approve_overwrites_an_existing_skill_of_the_same_name(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    _write(tmp_path / "skills", monkeypatch, "weekly-review",
           "name: weekly-review\ndescription: old version.\n", body="old body\n")
    skills.propose("weekly-review", "new version.", "new body")
    skills.approve("weekly-review")
    (live,) = skills.load_all()
    assert live.description == "new version."


def test_approving_an_unknown_name_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    with pytest.raises(ValueError, match="no pending"):
        skills.approve("nonesuch")


def test_approve_re_validates_and_refuses_a_hand_corrupted_draft(tmp_path, monkeypatch):
    # A draft hand-edited on disk between proposal and approval must not be
    # trusted just because propose() once validated it.
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "d", "body")
    (skills.PENDING_DIR / "weekly-review" / "SKILL.md").write_text("no frontmatter here\n")
    with pytest.raises(ValueError, match="frontmatter"):
        skills.approve("weekly-review")
    # It's left in place, not silently dropped.
    assert (skills.PENDING_DIR / "weekly-review" / "SKILL.md").exists()


def test_reject_discards_the_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "d", "body")
    skills.reject("weekly-review")
    assert skills.load_all(skills.PENDING_DIR) == []


def test_rejecting_an_unknown_name_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    with pytest.raises(ValueError, match="no pending"):
        skills.reject("nonesuch")


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", ".hidden"])
def test_approve_and_reject_refuse_a_path_like_name(tmp_path, monkeypatch, name):
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    with pytest.raises(ValueError, match="directory name"):
        skills.approve(name)
    with pytest.raises(ValueError, match="directory name"):
        skills.reject(name)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_skills.py -k "propose or approve or reject" -v`
Expected: FAIL — `AttributeError: module 'ninja.skills' has no attribute 'propose'` (and similarly for `approve`/`reject`/`PENDING_DIR`).

- [ ] **Step 3: Implement staging in `ninja/skills.py`**

At the top of the file, add `import re` (alphabetically before the existing `import sys`), and add `PENDING_DIR` and `NAME_RE` next to the existing module constants:

```python
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from ninja import semantic

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "skills"
PENDING_DIR = ROOT / ".ninja" / "pending_skills"

REQUIRED = {"name", "description"}
MIN_OVERLAP = 2
MAX_MATCHES = 2
BODY_CAP = 3000
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
```

Replace `load_all` (it currently hardcodes `DIR`) with the widened version:

```python
def load_all(directory: Path = DIR) -> list[Skill]:
    """Every <directory>/*/SKILL.md, freshly read. A handful of small files —
    no cache, so an edit is live on the next message. A broken one is skipped
    with a line to stderr; it never stops the rest from loading.

    Defaults to DIR (the live skills/); passing PENDING_DIR lists staged
    drafts with the same parse-or-skip behavior, instead of a near-duplicate
    function.
    """
    if not directory.is_dir():
        return []
    found = []
    for d in sorted(directory.iterdir()):
        path = d / "SKILL.md"
        if not path.exists():
            continue
        try:
            found.append(_parse(path.read_text(), path))
        except (ValueError, OSError) as exc:
            print(f"! skipped skill: {exc}", file=sys.stderr)
    return found
```

Then, after `format_section` at the end of the file, add:

```python
def _safe_name(name: str) -> str:
    """A skill name is a directory name, not a path — the same guard
    personas.load applies to a persona name."""
    if name != Path(name).name or name.startswith("."):
        raise ValueError(f"a skill name is a directory name, not a path: {name!r}")
    return name


def _render(skill: Skill) -> str:
    # yaml.safe_dump, not string interpolation: a description containing a
    # colon ("Use when: planning a week ahead") would otherwise corrupt the
    # frontmatter _parse has to read back.
    frontmatter = yaml.safe_dump(
        {"name": skill.name, "description": skill.description}, sort_keys=False
    )
    return f"---\n{frontmatter}---\n\n{skill.body}\n"


def propose(name: str, description: str, body: str) -> Skill:
    """Stage a draft at PENDING_DIR/<name>/SKILL.md. Nothing reads PENDING_DIR
    except load_all(PENDING_DIR) itself — match() and format_section() only
    ever see DIR — so a proposal cannot affect a turn until approve() moves it.
    """
    name = _safe_name(name.strip())
    if not NAME_RE.match(name):
        raise ValueError(
            f"a skill name must be lowercase letters, digits and hyphens, "
            f"starting with a letter: {name!r}"
        )
    description = " ".join(description.split())
    if not description:
        raise ValueError("description is empty")
    body = body.strip()
    if not body:
        raise ValueError("body is empty")
    skill = Skill(name=name, description=description, body=body)
    target = PENDING_DIR / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render(skill))
    return skill


def approve(name: str) -> Skill:
    """Move a staged draft into DIR, re-validating it on the way — a draft
    could have been hand-edited on disk since it was proposed.

    Overwrites an existing skill of the same name. That's deliberate: a
    re-proposal of an existing skill is the update path, and typing
    /approve-skill is the confirmation an overwrite needs.
    """
    name = _safe_name(name)
    path = PENDING_DIR / name / "SKILL.md"
    if not path.exists():
        raise ValueError(f"no pending skill proposal named {name!r}")
    skill = _parse(path.read_text(), path)
    target = DIR / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(path.read_text())
    path.unlink()
    path.parent.rmdir()
    return skill


def reject(name: str) -> None:
    """Discard a staged draft."""
    name = _safe_name(name)
    path = PENDING_DIR / name / "SKILL.md"
    if not path.exists():
        raise ValueError(f"no pending skill proposal named {name!r}")
    path.unlink()
    path.parent.rmdir()
```

- [ ] **Step 4: Widen the `temp_skills` fixture**

`tests/test_skills.py`'s new tests each set `skills.PENDING_DIR` themselves, but every *other* test in the suite needs it redirected too — otherwise a test that never mentions skills could still touch a developer's real `.ninja/pending_skills/` the moment any code path calls `propose`. In `tests/conftest.py`, extend the existing `temp_skills` fixture:

```python
@pytest.fixture(autouse=True)
def temp_skills(tmp_path, monkeypatch):
    # Otherwise build_system would silently load the real skills/weekly-review
    # SKILL.md off disk in every test that doesn't care about skills. A
    # nonexistent directory behaves like "no skills/", the same as a real repo
    # with none.
    monkeypatch.setattr(skills, "DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending_skills")
```

(Only the second `monkeypatch.setattr` line is new.)

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_skills.py -v`
Expected: PASS — all tests, including the pre-existing ones from D1.

- [ ] **Step 6: Full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, no new lint findings.

- [ ] **Step 7: Commit**

```bash
git add ninja/skills.py tests/conftest.py tests/test_skills.py
git commit -m "D2: propose/approve/reject skill drafts in ninja/skills.py"
```

---

### Task 2: The `propose_skill` tool

**Files:**
- Modify: `ninja/tools.py`
- Modify: `personas/assistant/PERSONA.md`
- Modify: `tests/test_tools.py`

**Interfaces:**
- Consumes: `skills.propose(name, description, body) -> Skill` (Task 1).
- Produces (used by Task 3): the `"propose_skill"` schema in `tools.SCHEMAS`,
  dispatched through `tools.run`.

- [ ] **Step 1: Write the new tests**

In `tests/test_tools.py`, add a new test after `test_a_large_file_is_truncated_not_sent_whole`'s block (anywhere after the existing tool tests is fine) and extend the existing parametrized test:

```python
def test_propose_skill_stages_a_draft(tmp_path, monkeypatch):
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    result = tools.run(
        "propose_skill",
        {"name": "weekly-review", "description": "Run a weekly review.", "body": "1. Ask."},
        ALL,
    )
    assert "staged: weekly-review" in result
    assert "/approve-skill weekly-review" in result
    (staged,) = skills.load_all(skills.PENDING_DIR)
    assert staged.name == "weekly-review"
```

Replace the existing `test_a_non_string_argument_is_refused_as_a_tool_error`'s
`@pytest.mark.parametrize` list with:

```python
@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("read_file", {"path": 5}),
        ("list_files", {"path": None}),
        ("remember", {"fact": 5}),
        ("add_rule", {"rule": ["not", "a", "string"]}),
        ("propose_skill", {"name": 5, "description": "d", "body": "b"}),
        ("propose_skill", {"name": "n", "description": 5, "body": "b"}),
        ("propose_skill", {"name": "n", "description": "d", "body": 5}),
    ],
)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `test_propose_skill_stages_a_draft` errors with `ValueError: unknown tool: propose_skill` (it's not in `SCHEMAS` yet); the three new parametrize cases fail because `propose_skill` isn't recognized either.

- [ ] **Step 3: Add the tool**

In `ninja/tools.py`, change the import line:

```python
from ninja import rules, semantic
```
to:
```python
from ninja import rules, semantic, skills
```

Add the schema to `SCHEMAS`, between the existing `add_rule` and `delegate` entries (grouping the three tools that queue something for a human next to each other):

```python
    {
        "name": "propose_skill",
        "description": (
            "Draft a new skill for this person to review and approve before it "
            "takes effect. Use when you notice something they do repeatedly that "
            "none of the current skills cover. This only stages the draft — "
            "nothing is saved until they approve it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Lowercase, hyphenated identifier, e.g. 'weekly-review'.",
                },
                "description": {
                    "type": "string",
                    "description": "One sentence: when this skill applies.",
                },
                "body": {
                    "type": "string",
                    "description": "The skill's instructions, in full.",
                },
            },
            "required": ["name", "description", "body"],
        },
    },
```

Extend the non-string-argument guard inside `run()`:

```python
    for key in ("path", "fact", "rule", "name", "description", "body"):
        if key in args and not isinstance(args[key], str):
            raise ValueError(f"{key} must be a string")
```

Add the dispatch branch, between the existing `add_rule` branch and the `delegate` branch:

```python
    if name == "propose_skill":
        skill = skills.propose(args["name"], args["description"], args["body"])
        return (
            f"staged: {skill.name}\n\n"
            f"Tell them to run `/approve-skill {skill.name}` to save it, "
            f"or `/reject-skill {skill.name}` to discard it."
        )
```

- [ ] **Step 4: Grant the tool to the assistant persona**

`test_the_default_persona_holds_every_tool` (`tests/test_personas.py`) asserts
the assistant persona holds *every* tool in `tools.SCHEMAS` — adding
`propose_skill` to `SCHEMAS` without granting it here would break that test.
In `personas/assistant/PERSONA.md`, change the `tools:` line:

```yaml
tools: [list_files, read_file, remember, add_rule, delegate]
```
to:
```yaml
tools: [list_files, read_file, remember, add_rule, propose_skill, delegate]
```

And add a paragraph to the body, after the existing one about `remember`:

```markdown
When you notice something they do repeatedly that none of the current
skills cover, `propose_skill` a draft. Never write to `skills/` yourself —
they need to approve it first.
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_tools.py tests/test_personas.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ninja/tools.py personas/assistant/PERSONA.md tests/test_tools.py
git commit -m "D2: propose_skill tool, granted to the assistant persona"
```

---

### Task 3: REPL commands, delegation safety, docs

**Files:**
- Modify: `ninja/agent.py`
- Modify: `ninja/cli.py`
- Modify: `docs/TESTING.md`
- Modify: `tests/test_repl.py`
- Modify: `tests/test_delegation.py`

**Interfaces:**
- Consumes: `skills.load_all(skills.PENDING_DIR)`, `skills.approve`,
  `skills.reject` (Task 1); `"propose_skill"` in `tools.SCHEMAS` (Task 2).
- Produces: `agent.review_skill(command: str) -> None`, `"propose_skill"` in
  `agent.WRITE_TOOLS`.

- [ ] **Step 1: Write the new tests**

In `tests/test_repl.py`, add this section (placement anywhere after the
existing `/persona` tests is fine — e.g. right before `def _drive_main`):

```python
# --- /approve-skill and /reject-skill ---------------------------------------


def test_a_bare_approve_skill_lists_pending_proposals(tmp_path, monkeypatch, capsys):
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/approve-skill")
    out = capsys.readouterr().out
    assert "weekly-review" in out
    assert "Run a weekly review." in out


def test_a_bare_approve_skill_with_nothing_pending_says_so(capsys):
    agent.review_skill("/approve-skill")
    assert "no pending" in capsys.readouterr().out


def test_approve_skill_writes_the_draft_and_clears_it_from_pending(tmp_path, monkeypatch, capsys):
    from ninja import skills

    monkeypatch.setattr(skills, "DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/approve-skill weekly-review")

    (live,) = skills.load_all()
    assert live.name == "weekly-review"
    assert skills.load_all(skills.PENDING_DIR) == []
    assert "approved" in capsys.readouterr().out


def test_reject_skill_discards_the_draft(tmp_path, monkeypatch, capsys):
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/reject-skill weekly-review")

    assert skills.load_all(skills.PENDING_DIR) == []
    assert "rejected" in capsys.readouterr().out


def test_approving_an_unknown_skill_prints_the_error_and_does_not_raise(capsys):
    agent.review_skill("/approve-skill nonesuch")
    assert "no pending" in capsys.readouterr().out


def test_a_skill_review_command_never_becomes_a_user_turn(tmp_path, monkeypatch, capsys):
    # Same invariant as /persona, and for the same reason: an uncaught error
    # here must not take the REPL down mid-conversation, and the command
    # itself must never reach the model as a message.
    from ninja import skills
    from .conftest import StubClient

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    stub = StubClient([])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)
    monkeypatch.setattr(agent.episodic, "recall", lambda thread: [])

    lines = iter(["/approve-skill weekly-review", ""])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)

    agent.main()

    assert stub.seen == []
    assert "approved" in capsys.readouterr().out
```

In `tests/test_delegation.py`, add this test after
`test_a_child_cannot_write_even_when_its_persona_declares_the_tools` (before
the `# --- per-turn cap ---` section):

```python
def test_a_child_cannot_propose_a_skill_even_when_its_persona_declares_it(
    cast, tmp_path, monkeypatch
):
    # Same rule as remember/add_rule above: propose_skill queues something for
    # a human to approve, and a delegated child does not get to queue anything.
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    cast("writer", ["read_file", "propose_skill"])
    client = StubClient([
        delegate_call("d1", "writer"),
        call("w1", "propose_skill", {"name": "leaked", "description": "d", "body": "b"}),
        text("could not"),
        text("done"),
    ])
    run(client)

    assert [t["name"] for t in client.seen[1]["tools"]] == ["read_file"]
    (error,) = results_of(client.seen[2])
    assert error["is_error"] and "allowlist" in error["content"]
    assert skills.load_all(skills.PENDING_DIR) == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_repl.py tests/test_delegation.py -v`
Expected: FAIL — `AttributeError: module 'ninja.agent' has no attribute 'review_skill'`, and the delegation test fails because `WRITE_TOOLS` doesn't drop `propose_skill` yet, so the child's call would wrongly succeed instead of being refused.

- [ ] **Step 3: Implement**

In `ninja/agent.py`, change:

```python
WRITE_TOOLS = {"remember", "add_rule"}
```
to:
```python
WRITE_TOOLS = {"remember", "add_rule", "propose_skill"}
```

Add `review_skill`, directly after the existing `switch` function (before `main`):

```python
def review_skill(command: str) -> None:
    """Handle /approve-skill and /reject-skill. Both list what's pending when
    given no name — there is one shared queue, so either command is a fair
    way to ask what's in it. Like /persona, this never becomes part of the
    conversation.
    """
    verb, _, name = command.partition(" ")
    name = name.strip()
    if not name:
        pending = skills.load_all(skills.PENDING_DIR)
        if not pending:
            print("  no pending skill proposals")
            return
        print("  pending skill proposals:")
        for s in pending:
            print(f"   - {s.name}: {s.description}")
        return
    action = skills.approve if verb == "/approve-skill" else skills.reject
    try:
        action(name)
    except ValueError as exc:
        print(f"  {exc}")
        return
    verdict = "approved" if verb == "/approve-skill" else "rejected"
    print(f"  ↳ skill {verdict}: {name}")
```

In `main()`, add the dispatch right after the existing `/persona` block:

```python
        if user_input.startswith("/persona"):
            persona = switch(user_input, persona)
            forced = user_input.strip() != "/persona"
            continue
        if user_input.startswith("/approve-skill") or user_input.startswith("/reject-skill"):
            review_skill(user_input)
            continue
```

In `ninja/cli.py`, update the module docstring:

```python
"""The `ninja` command.

    ninja              talk to Ninja in the terminal
    ninja trace        the last 10 turns
    ninja trace 7      one turn, step by step
    ninja dashboard    the browser cockpit (layer 6)

In the REPL: /persona lists the cast, /persona <name> switches.
/approve-skill and /reject-skill review what the model has proposed;
either bare lists what's pending.
"""
```

In `docs/TESTING.md`, update two rows of the ownership table:

```
| `tests/test_skills.py` | The frontmatter parser as a parser of hostile input, the keyword matcher, and rendering | Real files in `tmp_path` |
```
becomes:
```
| `tests/test_skills.py` | The frontmatter parser as a parser of hostile input, the keyword matcher, rendering, and the propose/approve/reject staging flow | Real files in `tmp_path` |
```

and:
```
| `tests/test_repl.py` | `/persona` as a command: it never becomes a user turn, and one bad file does not end the session | `capsys` |
```
becomes:
```
| `tests/test_repl.py` | `/persona`, `/approve-skill` and `/reject-skill` as commands: none ever becomes a user turn, and one bad file does not end the session | `capsys` |
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_repl.py tests/test_delegation.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, coverage still at or above the gate's `--cov-fail-under` floor.

- [ ] **Step 6: Commit**

```bash
git add ninja/agent.py ninja/cli.py docs/TESTING.md tests/test_repl.py tests/test_delegation.py
git commit -m "D2: /approve-skill and /reject-skill REPL commands, delegation safety"
```
