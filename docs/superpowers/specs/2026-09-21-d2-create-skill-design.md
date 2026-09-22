# D2: `create_skill` — design

**Status:** approved by Sol (async — see decision log at the end).

**Roadmap position:** `docs/PLAN.md` §4, Phase D, D2. Follows D1 (the `SKILL.md`
loader, built and PR'd as #22, merged to `main`). Precedes Phase E.

## 1. Problem

D1 lets the harness *use* skills — `skills/<name>/SKILL.md` files, matched by
keyword, injected into the system prompt. Nothing lets the harness *create*
one. Right now the only way a new skill enters `skills/` is Sol hand-writing
the file.

D2 closes that: during a conversation, the model can draft a new skill from
what it's noticed, but the draft is not the file. It becomes the file only
after Sol looks at it and says yes.

## 2. Scope

**In scope:**
- A `propose_skill` tool the model calls to draft a skill (name, one-sentence
  description, body).
- A staging area, outside `skills/`, holding drafts nothing reads yet.
- Two REPL commands, `/approve-skill` and `/reject-skill`, that move a draft
  into `skills/` or discard it. Bare (no name) lists what's staged.

**Out of scope, explicitly:**
- **Dashboard visibility for staged drafts.** Sol's call (2026-09-21): the
  model's tool-call result already lands in the transcript and in
  `ninja trace`/the cockpit's tool-call rendering the same way `remember` and
  `add_rule` do today — nothing new is needed for *that* to be visible. What's
  out of scope is a dedicated "pending proposals" panel. If that turns out to
  matter once this is in use, it's a small follow-up, not a redesign.
- **The general feature-request pipeline** Sol described mid-brainstorm
  (dashboard-surfaced request → approved design → an implementing agent that
  asks questions → later, full repo/PR access). That is F1/F2's shape, not
  D2's. Recorded on issue #30 for when F1 is actually brainstormed.
- Editing a staged draft before approving it. Reject and re-propose is the
  only path for now — this only matters once a draft is routinely wrong in a
  way worth a partial fix, which nothing yet says is true.

## 3. Design

### 3.1 Where a draft lives

`.ninja/pending_skills/<name>/SKILL.md` — the same shape as `skills/<name>/SKILL.md`,
one directory below `.ninja/`, which is already wholly gitignored (`rules/`,
`state.db`, `MEMORY.md` all live there for the same reason: runtime state,
never committed).

### 3.2 `ninja/skills.py`

Four additions, all built on the parser D1 already has. No change to `Skill`
or `_parse`.

- `PENDING_DIR = ROOT / ".ninja" / "pending_skills"`
- `load_all(directory: Path = DIR)` — **widened**, not replaced. Today it
  always reads `DIR`; a default argument makes `load_all(PENDING_DIR)` list
  staged drafts with the exact same parse-or-skip-and-warn behavior, instead
  of a near-duplicate function. Every existing call (`skills.load_all()`)
  is unaffected.
- `propose(name: str, description: str, body: str) -> Skill` — validates the
  three fields the model supplied (not a whole markdown blob — asking a model
  to hand-write YAML frontmatter is asking for a parse error), writes the
  draft, returns it.
- `approve(name: str) -> Skill` — moves a draft into `skills/`.
- `reject(name: str) -> None` — discards a draft.

Validation in `propose`:
- `name` must match `^[a-z][a-z0-9-]*$` — the same shape as the one skill
  that exists today (`weekly-review`), and simple enough to also rule out a
  path (`/`, `..`, a leading `.`) without a separate check.
- `description` and `body`, after normalizing whitespace, must be non-empty.
- Frontmatter is written with `yaml.safe_dump({"name": ..., "description": ...})`,
  not string interpolation — a description containing a colon (a real
  sentence like "Use when: planning a week ahead") would otherwise corrupt
  the YAML `_parse` has to read back.

`approve(name)`:
- Re-parses the draft with `_parse` before moving it — defense in depth
  against a draft hand-edited on disk between proposal and approval.
- Writes the same text to `skills/<name>/SKILL.md`, creating the directory.
  **Approving a name that already exists in `skills/` overwrites it.** That's
  a deliberate choice, not an oversight: a re-proposal of an existing skill is
  the update path, and Sol typing `/approve-skill` is the confirmation an
  overwrite needs.
- Deletes the draft file and its now-empty directory.
- Raises `ValueError` if there's no draft by that name.

`reject(name)`:
- Deletes the draft directory. Raises `ValueError` if there's no draft by
  that name.

`approve` and `reject` both validate `name` the same way `personas.load` does
today (`name != Path(name).name or name.startswith(".")` → refuse) — this
name comes from Sol typing at the REPL rather than from the model, but the
check is one line and free, and a typo'd `../` is worth refusing rather than
resolving.

### 3.3 `ninja/tools.py`

One schema:

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

One dispatch branch in `run()`, following the existing shape (`remember`,
`add_rule`): calls `skills.propose(...)`, returns a confirmation string that
names the two REPL commands, so the model can relay them in its reply. The
existing non-string-argument guard (`for key in (...)`) gains `"name"`,
`"description"`, `"body"`.

### 3.4 `ninja/agent.py`

- `WRITE_TOOLS` gains `"propose_skill"`. Same reasoning as `remember` and
  `add_rule`: a delegated child reads and reasons, and does not queue
  anything — proposing a skill is exactly that, staged or not.
- One new top-level function, `review_skill(command: str) -> None`, called
  from `main()`'s REPL loop for both `/approve-skill` and `/reject-skill`,
  mirroring how `switch()` handles `/persona`. Bare (no name) lists pending
  drafts by calling `skills.load_all(skills.PENDING_DIR)` — one shared queue,
  so either command is a fair way to ask what's in it. With a name, it calls
  `skills.approve` or `skills.reject`, prints the result or the `ValueError`,
  and — like `/persona` — never becomes part of the conversation.
- `personas/assistant/PERSONA.md` gains `propose_skill` in its `tools:` list,
  and a short paragraph in the body (alongside the existing one for
  `remember`) saying when to reach for it.

### 3.5 Tracing

No changes. `propose_skill` is dispatched through `tools.run` inside
`run_turn` exactly like `remember` and `add_rule`, so the existing generic
`trace.tool(...)` call already records it — visible in `ninja trace <id>` and
the cockpit today, same as any other tool call. `/approve-skill` and
`/reject-skill` are REPL-level, outside a turn, and are not traced — the same
position `/persona` is already in.

### 3.6 Failure modes

| Situation | Behavior |
|---|---|
| `propose_skill` with a malformed name | `ValueError`, becomes a tool error the model sees and can retry |
| `propose_skill` with empty description/body | `ValueError`, same |
| `/approve-skill <name>` with no such draft | Printed error, REPL continues |
| `/approve-skill <name>` on a draft hand-corrupted since proposal | Printed `_parse` error (e.g. bad YAML), draft is left in place, REPL continues |
| `/reject-skill <name>` with no such draft | Printed error, REPL continues |
| Re-proposing an already-staged name | Overwrites the draft — `propose` always writes, never checks for an existing draft. Matches `approve`'s overwrite behavior for `skills/` itself. |

## 4. Testing

- `tests/test_skills.py`: `propose`/`approve`/`reject` round trip; name
  validation (bad shape, path-like); YAML-safe rendering (a description with
  a colon survives `propose` → `_parse`); `approve` moves the file and leaves
  no pending directory behind; `approve`/`reject` of an unknown name raise;
  `approve` re-validates and refuses a hand-corrupted draft without deleting
  it; `load_all(PENDING_DIR)` behaves exactly like `load_all()` against a
  different directory.
- `tests/test_tools.py`: `propose_skill` dispatch stages a draft;
  `test_a_non_string_argument_is_refused_as_a_tool_error` gains
  `("propose_skill", {"name": 5})`-shaped cases (the existing
  `test_an_empty_allowlist_grants_nothing` already iterates every schema, so
  it covers `propose_skill` with no plan changes needed).
- `tests/test_delegation.py`: a case mirroring the existing "writer" test —
  a child that declares `propose_skill` can never call it, the same shape as
  the existing `remember`/`add_rule` leak test.
- `tests/test_repl.py`: `/approve-skill` and `/reject-skill`, bare and named,
  including "never becomes a user turn" — the same invariant `/persona` is
  tested for, and for the same reason (an uncaught error here must not take
  the REPL down mid-conversation).
- `tests/conftest.py`: `temp_skills` widens to also redirect
  `skills.PENDING_DIR` into `tmp_path`, so no test can touch a developer's
  real `.ninja/pending_skills/`.

## 5. Decision log

- **Approval mechanism** (2026-09-21): propose-and-stage, approved out of
  band via a REPL command — not a synchronous y/n blocking the turn, and not
  unstructured free text with no tool at all. Chosen because it matches the
  one human-in-the-loop pattern that already exists (`/persona`) and leaves
  `run_turn` untouched, which is worth a lot given how tightly that loop is
  already reasoned about (`docs/TESTING.md`'s `tests/test_loop.py` row).
- **Dashboard visibility**: deferred (§2). Async call — Sol was stepping away
  for the night and asked to keep moving rather than wait on a reply to a
  question with a reasonable default. The default taken: the tool call is
  already visible where every other tool call is; a dedicated staging panel
  is new surface area nothing yet asks for.
- **Scope boundary against F1/F2**: Sol's mid-brainstorm description of a
  general feature-request pipeline (dashboard-surfaced request → approved
  design → an agent that implements and asks questions → later, full
  repo/PR access) is F1/F2's shape, confirmed explicitly deferred ("f1f2
  later"). Recorded on GitHub issue #30 rather than folded into D2.
