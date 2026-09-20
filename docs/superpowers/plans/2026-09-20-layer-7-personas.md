# Layer 7: Personas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the model, system prompt and tool list come from a markdown file
instead of module constants, with the tool allowlist enforced by the harness.

**Architecture:** A `Persona` is a frozen dataclass loaded from
`personas/<name>/PERSONA.md`. It is resolved once per turn at the entry point and
passed into `run_turn` — never reached for from inside the loop. The allowlist is
enforced twice: `persona.schemas()` filters what the request carries, and
`tools.run` refuses a name outside it.

**Tech Stack:** Python 3.12, `anthropic`, `pyyaml` (new), `fastapi`, `pytest`, `ruff`, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-20-layer-7-personas-design.md`

## Global Constraints

- Two personas ship: `assistant` and `interview-coach`. No third.
- `yaml.safe_load`, never `yaml.load` — ruff's `S506` enforces it.
- `tools.run`'s `allowed` parameter is **required**. No permissive default.
- The security tests in `tests/test_tools.py` (path boundary, hidden-file
  refusal) are updated for the new signature but their assertions must not be
  weakened. Per `CONTRIBUTING.md` they are not relaxed to make a feature work.
- `personas.py` must not import `agent` — `agent` imports `personas`, and the
  reverse creates a cycle.
- Every task ends green on `uv run pytest -q` and `uv run ruff check .`.
- Line length 100 (`[tool.ruff] line-length = 100`).

---

## File Structure

| File | Responsibility |
|---|---|
| `ninja/personas.py` *(new)* | The `Persona` object, the loader, the fallback |
| `personas/assistant/PERSONA.md` *(new)* | Default orchestrator |
| `personas/interview-coach/PERSONA.md` *(new)* | Coaching judgment policy |
| `ninja/tools.py` | `run()` gains the allowlist check |
| `ninja/agent.py` | `run_turn` and `build_system` take a persona; REPL switches |
| `ninja/server.py` | Per-request persona; `/api/personas`, `POST /api/persona` |
| `ui/index.html` | Personas panel, switcher, active persona in chat header |
| `tests/test_personas.py` *(new)* | Loading, parsing, filtering, failure modes |
| `tests/conftest.py` | `a_persona()` builder shared by loop and server tests |

---

### Task 1: The Persona object and the two persona files

**Files:**
- Create: `ninja/personas.py`
- Create: `personas/assistant/PERSONA.md`
- Create: `personas/interview-coach/PERSONA.md`
- Create: `tests/test_personas.py`
- Modify: `pyproject.toml` (add `pyyaml` to `[project].dependencies`)

**Interfaces:**
- Consumes: `tools.SCHEMAS` (existing, a list of dicts each with a `"name"` key).
- Produces: `personas.Persona` (frozen dataclass with fields `name: str`,
  `description: str`, `instructions: str`, `tools: tuple[str, ...]`,
  `model: str`, and method `schemas() -> list[dict]`);
  `personas.load(name: str) -> Persona`; `personas.all() -> list[Persona]`;
  `personas.DEFAULT: str`; `personas.DEFAULT_MODEL: str`;
  `personas.DEFAULT_INSTRUCTIONS: str`.

- [ ] **Step 1: Add the dependency**

```bash
uv add pyyaml
```

Expected: `pyproject.toml` gains `"pyyaml>=6.0"` under `[project].dependencies`
and `uv.lock` updates.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_personas.py`:

```python
"""Layer 7: a persona is data, and the allowlist is enforced from it."""

import dataclasses

import pytest

from ninja import personas, tools


def test_a_persona_loads_from_disk():
    coach = personas.load("interview-coach")
    assert coach.name == "interview-coach"
    assert coach.model
    # The body after the frontmatter is the system prompt.
    assert "coach" in coach.instructions.lower()
    assert coach.tools == ("read_file", "remember")


def test_the_description_reads_like_a_tool_description():
    # Layer 8 routes on this field, so it says when to use the persona.
    coach = personas.load("interview-coach")
    assert len(coach.description) > 20


def test_schemas_are_filtered_to_the_allowlist():
    coach = personas.load("interview-coach")
    names = [s["name"] for s in coach.schemas()]
    assert names == ["read_file", "remember"]
    # list_files exists but this persona never sees it.
    assert "list_files" in [s["name"] for s in tools.SCHEMAS]
    assert "list_files" not in names


def test_the_default_persona_holds_every_tool():
    assistant = personas.load("assistant")
    assert len(assistant.schemas()) == len(tools.SCHEMAS)


def test_a_persona_is_frozen():
    # It is resolved once per turn and read for the whole of it, so it must not
    # be mutable. FrozenInstanceError subclasses AttributeError.
    coach = personas.load("interview-coach")
    with pytest.raises(dataclasses.FrozenInstanceError):
        coach.model = "something-else"


def test_all_returns_the_cast():
    names = sorted(p.name for p in personas.all())
    assert names == ["assistant", "interview-coach"]


def test_an_unknown_persona_names_itself():
    with pytest.raises(ValueError, match="nonesuch"):
        personas.load("nonesuch")


def test_malformed_frontmatter_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(personas, "DIR", tmp_path)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "PERSONA.md").write_text("no frontmatter here\n")
    with pytest.raises(ValueError, match="frontmatter"):
        personas.load("broken")


def test_missing_keys_name_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(personas, "DIR", tmp_path)
    thin = tmp_path / "thin"
    thin.mkdir()
    (thin / "PERSONA.md").write_text("---\nname: thin\n---\n\nbody\n")
    with pytest.raises(ValueError, match="description"):
        personas.load("thin")


def test_a_missing_personas_dir_falls_back_to_layer_6_behaviour(tmp_path, monkeypatch):
    # The harness must start even with no personas/ directory at all.
    monkeypatch.setattr(personas, "DIR", tmp_path / "does-not-exist")
    fallback = personas.load(personas.DEFAULT)
    assert fallback.name == personas.DEFAULT
    assert len(fallback.schemas()) == len(tools.SCHEMAS)
    assert fallback.model == personas.DEFAULT_MODEL
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_personas.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ninja.personas'`

- [ ] **Step 4: Write the persona files**

Create `personas/assistant/PERSONA.md`:

```markdown
---
name: assistant
description: The default. Use for anything about this project, this codebase,
  or the user's own work — reading files, answering questions, remembering
  what stays true.
tools: [list_files, read_file, remember]
model: claude-haiku-4-5
---

You are a helpful assistant with read access to this project's files.

Keep answers short. Read before you answer rather than guessing at what a file
contains.

When something about the user or their work is durably true — who they are,
what they are building, a decision they have made — `remember` it. Not what was
just said, and not passing detail.
```

Create `personas/interview-coach/PERSONA.md`:

```markdown
---
name: interview-coach
description: Use to rehearse for an interview — practice questions, follow-ups
  on thin answers, and tracking what still needs revising. Not for looking
  things up.
tools: [read_file, remember]
model: claude-haiku-4-5
---

## How to coach

Ask, then wait. One question at a time, and a follow-up on the part of the
answer that was thin rather than the part that was strong.

Do not hand over the answer. If they are stuck, narrow the question until they
can reach it themselves.

Ask for the reasoning, not the definition. "Why would you pick that?" tells you
more than "what is it?" does.

When a gap shows up twice, `remember` it — the weakness, not the exchange.
```

- [ ] **Step 5: Write `ninja/personas.py`**

```python
"""Layer 7: a persona is data.

A persona supplies the three things the loop would otherwise hardcode — the
model, the system prompt, and the tool list. Nothing about the loop changes.

The object is frozen deliberately. It is resolved once per turn and read for
the whole of it: the cockpit is a server, a switch can land while a loop is on
step 3 of 6, and a persona that could change mid-turn would change the toolset
underneath a request already in flight.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from ninja import tools

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "personas"
DEFAULT = "assistant"

# Used only when personas/ is absent, so the harness still starts. These live
# here rather than in agent.py because agent imports personas, not the reverse.
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_INSTRUCTIONS = (
    "You are a helpful assistant with read access to this project's files. "
    "Keep answers short."
)

REQUIRED = {"name", "description", "tools", "model"}


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    model: str

    def schemas(self) -> list[dict]:
        """What this persona's model is allowed to see.

        The first of two enforcement points: a tool that is not in the request
        cannot be asked for. tools.run refuses the ones it guesses anyway.
        """
        return [s for s in tools.SCHEMAS if s["name"] in self.tools]


def _parse(text: str, source: Path) -> Persona:
    if not text.lstrip().startswith("---"):
        raise ValueError(f"{source}: no frontmatter — a PERSONA.md starts with ---")
    _, frontmatter, body = text.lstrip().split("---", 2)
    meta = yaml.safe_load(frontmatter) or {}
    missing = REQUIRED - set(meta)
    if missing:
        raise ValueError(f"{source}: frontmatter is missing {sorted(missing)}")
    return Persona(
        name=meta["name"],
        description=" ".join(str(meta["description"]).split()),
        instructions=body.strip(),
        tools=tuple(meta["tools"]),
        model=meta["model"],
    )


def _fallback() -> Persona:
    """personas/ is absent. Behave exactly as layer 6 did."""
    return Persona(
        name=DEFAULT,
        description="The default assistant.",
        instructions=DEFAULT_INSTRUCTIONS,
        tools=tuple(s["name"] for s in tools.SCHEMAS),
        model=DEFAULT_MODEL,
    )


def load(name: str) -> Persona:
    path = DIR / name / "PERSONA.md"
    if not path.exists():
        if name == DEFAULT:
            return _fallback()
        raise ValueError(f"no persona named {name!r} in {DIR}")
    return _parse(path.read_text(), path)


def all() -> list[Persona]:  # noqa: A001 — reads as personas.all()
    if not DIR.is_dir():
        return [_fallback()]
    found = [load(d.name) for d in sorted(DIR.iterdir()) if (d / "PERSONA.md").exists()]
    return found or [_fallback()]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_personas.py -q`
Expected: PASS, 10 tests.

- [ ] **Step 7: Run the whole gate**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 46 passed, 2 deselected. All ruff checks pass.

If ruff flags `A001` on `all`, the `# noqa` comment above covers it. If it flags
an unused import, remove it rather than adding a noqa.

- [ ] **Step 8: Commit**

```bash
git add ninja/personas.py personas/ tests/test_personas.py pyproject.toml uv.lock
git commit -m "Layer 7: load a persona from a file

A persona supplies the model, the system prompt and the tool list. The
object is frozen because it is resolved once per turn and read for the
whole of it.

Two ship. interview-coach differs from assistant on both axes at once —
it cannot call list_files, and its instructions change what a good answer
looks like — which is what makes it a test of the mechanism rather than a
demo of it.

Nothing consumes this yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Enforce the allowlist at dispatch

**Files:**
- Modify: `ninja/tools.py` (`run`)
- Modify: `tests/test_tools.py` (6 existing call sites)

**Interfaces:**
- Consumes: nothing from Task 1 — `tools.py` must not import `personas`
  (`personas` imports `tools`; the reverse is a cycle).
- Produces: `tools.run(name: str, args: dict, allowed: Sequence[str]) -> str`,
  raising `ValueError` when `name not in allowed`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
def test_a_tool_outside_the_allowlist_is_refused():
    # The second enforcement point. The first is that the model was never
    # shown this tool; this catches the name it guessed anyway.
    with pytest.raises(ValueError, match="allowlist"):
        tools.run("remember", {"fact": "x"}, allowed=["read_file"])


def test_an_allowed_tool_still_runs():
    assert "pyproject.toml" in tools.run("list_files", {"path": "."}, allowed=ALL)
```

And add near the top of the file, after the imports:

```python
# Every tool. Tests that are not about the allowlist pass this.
ALL = tuple(s["name"] for s in tools.SCHEMAS)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools.py -q`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'allowed'`

- [ ] **Step 3: Update `run()` in `ninja/tools.py`**

Add `Sequence` to the imports at the top of the file:

```python
from collections.abc import Sequence
from pathlib import Path
```

Replace the `run` signature and add the check as its first statement:

```python
def run(name: str, args: dict, allowed: Sequence[str]) -> str:
    # The allowlist is enforced here as well as by filtering the schemas,
    # because filtering is advisory: a model that has seen a tool name earlier
    # in the conversation can still emit it. Required, not defaulted — a
    # permissive default is how an allowlist quietly stops being enforced.
    if name not in allowed:
        raise ValueError(f"{name} is not in this persona's allowlist")
    if name == "list_files":
        ...
```

Leave the rest of the body unchanged.

- [ ] **Step 4: Update the six existing call sites**

In `tests/test_tools.py`, every existing `tools.run(a, b)` becomes
`tools.run(a, b, ALL)`. The assertions do not change:

```python
def test_list_and_read_work():
    listing = tools.run("list_files", {"path": "."}, ALL)
    assert "pyproject.toml" in listing
    assert "[project]" in tools.run("read_file", {"path": "pyproject.toml"}, ALL)


def test_hidden_files_are_refused():
    for path in [".env", ".git/config", "ninja/../.env"]:
        with pytest.raises(ValueError, match="hidden files"):
            tools.run("read_file", {"path": path}, ALL)


def test_listing_hides_dotfiles():
    assert ".env" not in tools.run("list_files", {"path": "."}, ALL).split("\n")


def test_path_escape_is_refused():
    for path in ["../../../etc/passwd", "/etc/passwd", "ninja/../../.."]:
        with pytest.raises(ValueError):
            tools.run("read_file", {"path": path}, ALL)


def test_unknown_tool_raises():
    with pytest.raises(ValueError, match="unknown tool"):
        tools.run("rm_rf", {"path": "/"}, ALL)
```

Note `test_unknown_tool_raises` passes `ALL`, so it still reaches the
`unknown tool` branch rather than being caught by the allowlist first. That
distinction is the point of the test — keep it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -q`
Expected: PASS, 8 tests.

`tests/test_loop.py` will now fail — `agent.run_turn` still calls
`tools.run(block.name, block.input)` with two arguments. That is expected and
Task 3 fixes it. Do not patch `agent.py` here.

- [ ] **Step 6: Commit**

```bash
git add ninja/tools.py tests/test_tools.py
git commit -m "Enforce the tool allowlist at dispatch

Filtering the schemas a request carries is the first enforcement point
and it is advisory: a model that has seen a tool name earlier in the
conversation can still emit it. run() now refuses a name outside the
allowlist it was given.

Required rather than defaulted. A permissive default is how an allowlist
quietly stops being enforced.

The security tests are updated for the signature and not otherwise
touched. test_unknown_tool_raises still passes the full tool list so it
reaches the unknown-tool branch rather than being caught by the allowlist
first — that distinction is what the test is for.

tests/test_loop.py fails until the loop is threaded in the next commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Thread the persona through the loop

**Files:**
- Modify: `ninja/agent.py` (`build_system`, `run_turn`, `MODEL`, `SYSTEM`)
- Modify: `tests/conftest.py` (add `a_persona`)
- Modify: `tests/test_loop.py` (every `run_turn` call)

**Interfaces:**
- Consumes: `personas.Persona`, `personas.DEFAULT_MODEL`,
  `personas.DEFAULT_INSTRUCTIONS` from Task 1; `tools.run(..., allowed)` from Task 2.
- Produces: `agent.run_turn(client, messages, trace, persona, system=None) -> str`;
  `agent.build_system(user_input, trace, persona) -> str`;
  `conftest.a_persona(**overrides) -> Persona`.

- [ ] **Step 1: Add the persona builder to `tests/conftest.py`**

```python
def a_persona(**overrides):
    """A Persona for tests that are not about loading one."""
    from ninja.personas import Persona

    fields = {
        "name": "test",
        "description": "A persona used by tests.",
        "instructions": "You are a test persona.",
        "tools": ("list_files", "read_file", "remember"),
        "model": "claude-haiku-4-5",
    }
    fields.update(overrides)
    fields["tools"] = tuple(fields["tools"])
    return Persona(**fields)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_loop.py`:

```python
def test_the_request_carries_only_the_personas_tools():
    client = StubClient([text("ok")])
    coach = a_persona(tools=("read_file", "remember"))
    agent.run_turn(client, [{"role": "user", "content": "hi"}], trace.Trace("x"), coach)

    sent = [t["name"] for t in client.seen[0]["tools"]]
    assert sent == ["read_file", "remember"]


def test_the_personas_model_is_the_one_called():
    client = StubClient([text("ok")])
    coach = a_persona(model="claude-sonnet-5")
    agent.run_turn(client, [{"role": "user", "content": "hi"}], trace.Trace("x"), coach)

    assert client.seen[0]["model"] == "claude-sonnet-5"


def test_the_personas_instructions_are_the_system_prompt():
    client = StubClient([text("ok")])
    coach = a_persona(instructions="Coach, do not lecture.")
    agent.run_turn(client, [{"role": "user", "content": "hi"}], trace.Trace("x"), coach)

    assert client.seen[0]["system"] == "Coach, do not lecture."


def test_a_tool_outside_the_allowlist_comes_back_as_an_error():
    # The model asks for a tool this persona does not hold. The turn must
    # survive it — the refusal is a result the model can explain, not a crash.
    client = StubClient([tool_call("t1", "remember", {"fact": "x"}),
                         text("I can't store that.")])
    coach = a_persona(tools=("read_file",))
    messages = [{"role": "user", "content": "remember this"}]
    assert agent.run_turn(client, messages, trace.Trace("x"), coach) == "I can't store that."

    result = messages[2]["content"][0]
    assert result["is_error"] is True
    assert "allowlist" in result["content"]


def test_two_personas_in_one_turn_do_not_share_state():
    first = a_persona(name="a", tools=("read_file",), model="m-a")
    second = a_persona(name="b", tools=("remember",), model="m-b")

    c1, c2 = StubClient([text("1")]), StubClient([text("2")])
    agent.run_turn(c1, [{"role": "user", "content": "x"}], trace.Trace("x"), first)
    agent.run_turn(c2, [{"role": "user", "content": "y"}], trace.Trace("y"), second)

    assert [t["name"] for t in c1.seen[0]["tools"]] == ["read_file"]
    assert [t["name"] for t in c2.seen[0]["tools"]] == ["remember"]
    assert c1.seen[0]["model"] == "m-a"
    assert c2.seen[0]["model"] == "m-b"
```

Add `a_persona` to the existing import at the top of `tests/test_loop.py`:

```python
from .conftest import StubClient, a_persona, block, response
```

- [ ] **Step 3: Update every existing `run_turn` call in `tests/test_loop.py`**

Each existing call gains `a_persona()` as the fourth argument. For example:

```python
def test_answers_without_tools():
    client = StubClient([text("four")])
    messages = [{"role": "user", "content": "what is 2+2?"}]
    assert agent.run_turn(client, messages, trace.Trace("x"), a_persona()) == "four"
    assert len(client.seen) == 1
```

Apply the same change to `test_loops_until_the_model_stops_asking`,
`test_tool_results_go_back_as_one_user_message`,
`test_a_failing_tool_comes_back_as_an_error_not_a_crash`,
`test_the_step_cap_stops_a_runaway_loop` and `test_the_turn_is_traced`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop.py -q`
Expected: FAIL — `run_turn() takes 3 to 4 positional arguments but 5 were given`

- [ ] **Step 5: Update `ninja/agent.py`**

Add the import:

```python
from ninja import episodic, personas, semantic, tools
from ninja.personas import Persona
from ninja.trace import Trace
```

Point the constants at `personas` so there is one source for the fallback
values. The cockpit's guardrails panel and `cli.py`'s banner still read
`agent.MODEL`:

```python
# Kept for the guardrails panel and the CLI banner. The live values now come
# from whichever persona the turn is running as.
MODEL = personas.DEFAULT_MODEL
SYSTEM = personas.DEFAULT_INSTRUCTIONS
```

Replace `build_system`:

```python
def build_system(user_input: str, trace: Trace, persona: Persona) -> str:
    """Assemble the system prompt for this turn.

    The persona supplies the instructions; the retrieval gate decides whether
    facts are worth their tokens on top of them.
    """
    retrieve, why, hits = semantic.gate(user_input)
    trace.gate(retrieve, why, len(hits))
    if not retrieve:
        return persona.instructions
    return persona.instructions + "\n\nWhat you know about this person:\n" + semantic.as_context(hits)
```

Replace the `run_turn` signature and the two lines inside it that read the
hardcoded values:

```python
def run_turn(
    client: anthropic.Anthropic,
    messages: list,
    trace: Trace,
    persona: Persona,
    system: str | None = None,
) -> str:
    """Loop until the model stops asking for tools. Returns its final text."""
    for _ in range(MAX_STEPS):
        started = time.perf_counter()
        response = client.messages.create(
            model=persona.model,
            max_tokens=2048,
            system=system or persona.instructions,
            tools=persona.schemas(),
            messages=messages,
        )
        trace.model(persona.model, response, ms_since(started))
```

and the dispatch line:

```python
                output, failed = tools.run(block.name, block.input, persona.tools), False
```

Leave everything else in the loop unchanged.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop.py tests/test_tools.py -q`
Expected: PASS.

`tests/test_server.py` now fails — `server.chat` still calls `run_turn` without
a persona. Task 5 fixes it.

- [ ] **Step 7: Commit**

```bash
git add ninja/agent.py tests/conftest.py tests/test_loop.py
git commit -m "Run the loop as a persona

The three things run_turn hardcoded — model, system prompt, tool list —
now come from the persona it is handed. The loop does not change shape.

A tool the persona does not hold comes back as a tool_result with
is_error set, the same way a failing tool already does, so the turn
survives it and the model can explain itself.

agent.MODEL and agent.SYSTEM stay as the fallback the guardrails panel
and the CLI banner read, now sourced from personas so there is one
definition.

tests/test_server.py fails until the cockpit is threaded.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `/persona` in the REPL

**Files:**
- Modify: `ninja/agent.py` (`main`)
- Modify: `ninja/cli.py` (docstring only)
- Create: `tests/test_repl.py`

**Interfaces:**
- Consumes: `personas.load`, `personas.all`, `personas.DEFAULT` from Task 1;
  `agent.run_turn(..., persona, ...)` from Task 3.
- Produces: `agent.switch(command: str, current: Persona) -> Persona` — parses a
  `/persona ...` line and returns the persona to use next, or `current`
  unchanged when the name is unknown.

- [ ] **Step 1: Write the failing test**

Create `tests/test_repl.py`:

```python
"""The /persona command. Switching swaps instructions and tools, not the thread."""

import pytest

from ninja import agent, personas


def test_switching_returns_the_named_persona():
    started = personas.load("assistant")
    switched = agent.switch("/persona interview-coach", started)
    assert switched.name == "interview-coach"


def test_an_unknown_name_keeps_the_current_persona(capsys):
    started = personas.load("assistant")
    # A typo must not drop you into a broken state mid-conversation.
    assert agent.switch("/persona nonesuch", started) is started
    assert "nonesuch" in capsys.readouterr().out


def test_a_bare_persona_command_lists_the_cast(capsys):
    started = personas.load("assistant")
    assert agent.switch("/persona", started) is started
    out = capsys.readouterr().out
    assert "interview-coach" in out
    assert "assistant" in out


def test_switching_does_not_touch_working_memory():
    # The transcript is kept across a switch — swapping hats should not lose
    # the conversation. The persona lives in the system prompt, not in messages.
    messages = [{"role": "user", "content": "earlier"}]
    before = list(messages)
    agent.switch("/persona interview-coach", personas.load("assistant"))
    assert messages == before
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_repl.py -q`
Expected: FAIL — `AttributeError: module 'ninja.agent' has no attribute 'switch'`

- [ ] **Step 3: Add `switch` to `ninja/agent.py`**

Place it above `main`:

```python
def switch(command: str, current: Persona) -> Persona:
    """Handle a /persona line. Returns the persona for the next turn.

    Working memory is untouched: the persona lives in the system prompt, which
    is rebuilt every turn, so swapping hats costs nothing in the transcript.
    """
    _, _, name = command.partition(" ")
    name = name.strip()
    if not name:
        print("  personas:")
        for p in personas.all():
            mark = "*" if p.name == current.name else " "
            print(f"   {mark} {p.name:16} {len(p.tools)} tools · {p.description}")
        return current
    try:
        chosen = personas.load(name)
    except ValueError as exc:
        print(f"  {exc}")
        return current
    print(f"  ↳ persona: {chosen.name} ({len(chosen.tools)} tools)")
    return chosen
```

- [ ] **Step 4: Wire it into the REPL**

In `agent.main`, replace the setup and loop body. The persona is resolved once
before the loop and reassigned only by `switch`:

```python
def main() -> None:
    client = anthropic.Anthropic()
    session = episodic.new_session()
    messages = episodic.recall()
    persona = personas.load(personas.DEFAULT)

    print(f"ninja | {persona.name} | model={persona.model} | ctrl-d to quit")
    if messages:
        print(f"remembering {len(messages)} earlier messages")
    print()

    while True:
        try:
            user_input = input(f"{persona.name}> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue
        if user_input.startswith("/persona"):
            persona = switch(user_input, persona)
            continue

        messages.append({"role": "user", "content": user_input})
        trace = Trace(user_input)
        reply = run_turn(client, messages, trace, persona,
                         build_system(user_input, trace, persona))
        trace_id = trace.finish(reply)

        episodic.save(session, "user", user_input, trace_id)
        episodic.save(session, "assistant", reply, trace_id)

        print(f"\nagent> {reply}")
        print(
            f"[trace {trace_id} · {len(messages)} messages · "
            f"{trace.input_tokens} in / {trace.output_tokens} out · "
            f"${trace.cost:.5f}]\n"
        )
```

- [ ] **Step 5: Update the `cli.py` docstring**

```python
"""The `ninja` command.

    ninja              talk to Ninja in the terminal
    ninja trace        the last 10 turns
    ninja trace 7      one turn, step by step
    ninja dashboard    the browser cockpit (layer 6)

In the REPL: /persona lists the cast, /persona <name> switches.
"""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_repl.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 7: Commit**

```bash
git add ninja/agent.py ninja/cli.py tests/test_repl.py
git commit -m "Switch personas from the REPL

/persona lists the cast, /persona <name> switches, and an unknown name
keeps the current one rather than dropping you into a broken state
mid-conversation.

Working memory is untouched across a switch. The persona lives in the
system prompt, which is rebuilt every turn, so swapping hats costs
nothing in the transcript.

One consequence worth knowing rather than discovering: because both
tools and system change, a switch invalidates the whole prompt cache.
Irrelevant at this scale, and the reason layer 8 delegates to a
sub-agent with a fresh context instead of switching the current one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The cockpit API

**Files:**
- Modify: `ninja/server.py` (`Message`, `chat`, `system_panel`; add
  `personas_panel` and `set_persona`)
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `personas.load`, `personas.all`, `personas.DEFAULT` from Task 1;
  `agent.run_turn(..., persona, ...)` and `agent.build_system(..., persona)` from Task 3.
- Produces: `GET /api/personas` returning
  `{"active": str, "personas": [{"name", "description", "model", "tools"}]}`;
  `POST /api/persona` taking `{"name": str}` and returning `{"active": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
def test_the_personas_panel_lists_the_cast():
    body = client.get("/api/personas").json()
    names = sorted(p["name"] for p in body["personas"])
    assert names == ["assistant", "interview-coach"]
    assert body["active"] == "assistant"


def test_the_panel_reports_the_tools_the_harness_enforces():
    # The panel must not claim a capability the allowlist does not grant.
    from ninja import personas

    body = client.get("/api/personas").json()
    shown = {p["name"]: p["tools"] for p in body["personas"]}
    assert shown["interview-coach"] == list(personas.load("interview-coach").tools)


def test_setting_the_persona_changes_the_default(monkeypatch):
    monkeypatch.setattr(server, "active", "assistant")
    assert client.post("/api/persona", json={"name": "interview-coach"}).json() == {
        "active": "interview-coach"
    }
    assert client.get("/api/personas").json()["active"] == "interview-coach"


def test_an_unknown_persona_is_a_400(monkeypatch):
    monkeypatch.setattr(server, "active", "assistant")
    assert client.post("/api/persona", json={"name": "nonesuch"}).status_code == 400
    # The active persona is unchanged by a failed switch.
    assert server.active == "assistant"


def test_a_chat_request_can_name_its_persona(monkeypatch):
    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "active", "assistant")
    monkeypatch.setattr(server, "client", lambda: stub)

    client.post("/api/chat", json={"text": "hi", "persona": "interview-coach"})

    assert [t["name"] for t in stub.seen[0]["tools"]] == ["read_file", "remember"]
    # Naming a persona on a request also makes it the default for the next one.
    assert server.active == "interview-coach"


def test_system_panel_no_longer_carries_a_personas_stub():
    # One source for one fact. /api/personas is the panel's source.
    assert "personas" not in client.get("/api/system").json()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_server.py -q`
Expected: FAIL — 404 on `/api/personas`, and the existing chat tests fail with
`run_turn() missing 1 required positional argument: 'persona'`.

- [ ] **Step 3: Update `ninja/server.py`**

Add `personas` to the import:

```python
from ninja import agent, episodic, personas, semantic, tools, trace
```

Add the active-persona name beside the other module state:

```python
# Seeded from episodic memory at import, so a restart picks the thread back up.
session = episodic.new_session()
messages: list = episodic.recall()
# Which persona an unspecified request defaults to. A *name*, not a resolved
# Persona: it is read once at the top of a request and immediately turned into
# a frozen object, so a switch cannot reach a turn already in flight.
active: str = personas.DEFAULT
_client: anthropic.Anthropic | None = None
```

Extend the request model and add one for the switch:

```python
class Message(BaseModel):
    text: str
    persona: str | None = None


class PersonaName(BaseModel):
    name: str
```

Replace `chat`:

```python
@app.post("/api/chat")
def chat(message: Message):
    # The persona is resolved once, here, before the loop starts. run_turn is
    # handed the object; nothing inside the loop reads `active` again.
    global messages, active
    try:
        persona = personas.load(message.persona or active)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    working = [*messages, {"role": "user", "content": message.text}]
    turn = trace.Trace(message.text)
    try:
        reply = agent.run_turn(
            client(), working, turn, persona, agent.build_system(message.text, turn, persona)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"the turn failed: {exc}") from exc
    messages, active = working, persona.name
    trace_id = turn.finish(reply)
    episodic.save(session, "user", message.text, trace_id)
    episodic.save(session, "assistant", reply, trace_id)
    return {
        "reply": reply,
        "trace_id": trace_id,
        "working_memory": len(messages),
        "persona": persona.name,
    }
```

Add the two endpoints next to the other panels:

```python
@app.get("/api/personas")
def personas_panel():
    return {
        "active": active,
        "personas": [
            {
                "name": p.name,
                # What layer 8 will route on.
                "description": p.description,
                "model": p.model,
                # Read from the file, so the panel cannot claim a capability
                # the allowlist does not grant.
                "tools": list(p.tools),
            }
            for p in personas.all()
        ],
    }


@app.post("/api/persona")
def set_persona(body: PersonaName):
    global active
    try:
        active = personas.load(body.name).name
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"active": active}
```

Remove the `personas` stub from `system_panel` — `/api/personas` is the single
source:

```python
@app.get("/api/system")
def system_panel():
    return {
        "model": agent.MODEL,
        "layers": [
            {"n": n, "name": name, "phase": phase,
             "status": "built" if n in BUILT else "scaffold" if n in SCAFFOLD else "planned"}
            for n, name, phase in LAYERS
        ],
        "evals": [],      # layer 11
    }
```

Update `BUILT` to include layer 7:

```python
BUILT = {1, 2, 3, 4, 5, 6, 7}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole gate**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add ninja/server.py tests/test_server.py
git commit -m "Serve the cast, and run each request as a persona

The persona is resolved once at the top of a request and handed to the
loop as a frozen object. `active` is a name, not a resolved Persona: it
is read once and never again during the turn, so a switch landing
mid-turn changes what the next request defaults to and cannot reach a
turn already in flight.

/api/personas is the panel's single source. The personas stub in
/api/system is removed rather than populated in parallel — two sources
for one fact is how a panel drifts from the harness.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The cockpit panel

**Files:**
- Modify: `ui/index.html` (nav, `render`/`paint`, `counters`, chat header)

**Interfaces:**
- Consumes: `GET /api/personas` and `POST /api/persona` from Task 5.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Promote the nav item**

Replace the `Personas` entry under `Not built yet` — it moves into the `System`
list, above `Growth`:

```html
  <a data-view="guardrails">Guardrails <span class="count" id="c-rails">0</span></a>
  <a data-view="memory">Episodic <span class="count" id="c-mem">0</span></a>
  <a data-view="facts">Semantic <span class="count" id="c-facts">0</span></a>
  <a data-view="personas">Personas <span class="count" id="c-personas">0</span></a>
  <a data-view="growth">Growth <span class="count" id="c-built">0/15</span></a>
  <div class="nav-label">Not built yet</div>
  <a data-view="soon" data-layer="11">Testing <span class="tag">L11</span></a>
```

- [ ] **Step 2: Add the view to `paint`**

Inside `async function paint(el)`, add one line before the `soon` line:

```js
  if (view === 'personas')   return el.innerHTML = await personasView();
```

- [ ] **Step 3: Add the panel and the switcher**

Add these two functions next to `toolsView`:

```js
async function personasView() {
  const p = await get('/api/personas');
  return `<p class="section-label">${p.personas.length} personas — tools are read from
    the file, so this panel cannot claim a capability the allowlist does not grant</p>` +
    p.personas.map(x => `<div class="card"><div class="card-h">
      <b>${esc(x.name)}</b>
      <span class="chip ${x.name === p.active ? 'live' : 'soon'}">${x.name === p.active ? 'active' : 'idle'}</span>
      <span class="args">${esc(x.model)} · ${x.tools.map(esc).join(', ')}</span>
      ${x.name === p.active ? '' :
        `<button class="switch" onclick="switchPersona('${esc(x.name)}')">switch</button>`}
      </div><p>${esc(x.description)}</p></div>`).join('');
}

async function switchPersona(name) {
  const res = await fetch('/api/persona', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})});
  const body = await res.json().catch(() => ({}));
  if (!res.ok) return say('sys', 'switch failed: ' + detail(body, res.status));
  say('sys', 'persona → ' + name);
  await counters();
  await render();
}
```

- [ ] **Step 4: Style the switch button**

Add next to the `.chip` rules in the stylesheet:

```css
  .switch { background: none; border: 1px solid var(--line); border-radius: 3px;
            color: var(--dim); font: 500 9.5px/1 var(--mono); padding: 4px 7px;
            text-transform: uppercase; letter-spacing: .08em; cursor: pointer; }
  .switch:hover { color: var(--ink); border-color: var(--ops); }
```

- [ ] **Step 5: Show the active persona in the chat header**

Replace the chat header markup:

```html
  <div class="chat-head"><h3 id="chat-persona">Chat</h3><span id="wm-badge">working memory: 0</span></div>
```

and add to `readCounters`, after the existing `$('c-facts')` line:

```js
  const cast = await get('/api/personas');
  $('c-personas').textContent = cast.personas.length;
  $('chat-persona').textContent = cast.active;
```

- [ ] **Step 6: Verify in a real browser**

```bash
uv run ninja dashboard &
sleep 4
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --disable-gpu --no-sandbox --dump-dom --virtual-time-budget=6000 \
  http://localhost:7777 > /tmp/rendered.html
grep -o 'id="c-personas">[^<]*' /tmp/rendered.html
grep -o 'id="chat-persona">[^<]*' /tmp/rendered.html
```

Expected: `id="c-personas">2` and `id="chat-persona">assistant`.

Then confirm the JavaScript still parses:

```bash
node --check <(python3 -c "
import re, pathlib
print(re.findall(r'<script[^>]*>(.*?)</script>', pathlib.Path('ui/index.html').read_text(), re.S)[0])
")
```

Expected: no output (syntax OK). Stop the server afterwards.

- [ ] **Step 7: Update the README status table**

Change the layer 7 row from `—` to `✅`, and update the count in the line above
the table from "Six of fifteen so far." to "Seven of fifteen so far."

Add `/persona` to the Commands section:

```
ninja               talk to Ninja in the terminal
ninja trace         the last 10 turns
ninja trace 7       one turn, step by step
ninja dashboard     the browser cockpit → localhost:7777

In the REPL: /persona lists the cast, /persona <name> switches.
```

In the Layout section, change the `personas/` line from
`personas/         one PERSONA.md per persona           (layer 7)` to
`personas/         one PERSONA.md per persona`.

- [ ] **Step 8: Run the whole gate**

Run: `uv run pytest -q && uv run ruff check . && uv run pip-audit --progress-spinner off --skip-editable`
Expected: all green.

- [ ] **Step 9: Commit and open the PR**

```bash
git add ui/index.html README.md
git commit -m "Render the cast in the cockpit

The reserved Personas nav item becomes real: a card per persona with its
model, its description, and the tools it may call — all read from
/api/personas, which reads the files, so the panel cannot drift from what
the harness enforces.

The active persona shows in the chat header and can be switched from the
panel. A failed switch surfaces the reason rather than doing nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"

git push -u origin layer-7-personas
gh pr create --base main --title "Layer 7: personas"
```

---

## Not in this layer

Deliberately excluded. Each has its own spec or layer.

| Thing | Layer |
|---|---|
| `delegate(persona, task)`, routing, briefs | 8 |
| Topic-scoped memory (`facts.topic`, sticky topic, per-topic recall) | 5 extension, own PR |
| An always-on user profile in the system prompt | 5 extension, own PR |
| Persona authoring by the agent | 9 |
| LLM-as-judge, golden dataset | 11 |
