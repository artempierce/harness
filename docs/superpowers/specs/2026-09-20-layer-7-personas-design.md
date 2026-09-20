# Layer 7: Personas

**Status:** design approved, not implemented
**Depends on:** layers 2 (loop, tools), 3 (tracing), 5 (retrieval gate), 6 (cockpit)
**Unblocks:** layer 8 (delegation), layer 9 (the architect)

---

## What this layer adds

Three things that `run_turn` currently hardcodes start coming from a file:

```python
response = client.messages.create(
    model=MODEL,              # module constant  → persona.model
    system=system or SYSTEM,  # module constant  → persona.instructions
    tools=tools.SCHEMAS,      # ALL tools always → persona.schemas()
    messages=messages,
)
```

That is the whole layer. A persona is data; the loop does not change shape.

## Why a persona is an argument, not module state

The persona is resolved once per turn, at the entry point, and passed in. Nothing
reaches for it.

The cockpit is a server and a turn takes seconds, so two requests overlap: a
`/persona` switch can land while a loop is on step 3 of 6. If the loop reads the
active persona each step, the tool list and the model change underneath a turn
already in flight — the model is mid-flow having been offered one toolset, and
the next request advertises another. Dispatch can then refuse a call the harness
itself solicited one step earlier.

This is not hypothetical and does not need two users. One slow turn and one
impatient click does it. It is also the same failure shape as the working-memory
bug fixed in PR #5: module-level mutable state read from several request paths,
breaking silently.

There is a second reason, from Anthropic's own caching guidance. The prompt cache
is a prefix match rendered `tools` → `system` → `messages`, and any byte change
invalidates everything after it. Tools are the *first* thing in the prefix, so a
persona that can change mid-turn rewrites the front of the prompt while the loop
is running — a destroyed cache and a turn billed across two configurations.

`Persona` is therefore a frozen dataclass, and `frozen=True` is load-bearing.

## The file format

One directory per persona, matching `ARCHITECTURE.md`:

```
personas/
  assistant/       PERSONA.md
  codebase-guide/  PERSONA.md
  archivist/       PERSONA.md
```

```markdown
---
name: codebase-guide
description: Use for questions about how this project works — structure, where
  something lives, what a file does. Cannot store facts.
tools: [list_files, read_file]
model: claude-haiku-4-5
---

## How to answer

Read before you answer...
```

Frontmatter is parsed with `yaml.safe_load` — `pyyaml` becomes the fifth runtime
dependency. Hand-rolling ~20 lines was the alternative and was rejected: the
four keys are simple today, but quoting and multi-line descriptions are exactly
where a hand parser starts lying, and `pip-audit` already covers the dependency.
`safe_load`, never `load`; ruff's bandit rules enforce it.

`description` is what the orchestrator will read in layer 8 when deciding where
to route. It is a tool description in disguise and should be written like one —
when to use this, not what it is.

## The object

```python
@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    model: str

    def schemas(self) -> list[dict]:
        return [s for s in tools.SCHEMAS if s["name"] in self.tools]
```

## Enforcement

Two points, deliberately. Anthropic's guidance is blunt about why: *Claude does
not know your application's security boundary. Claude emits tool calls; your
harness handles them.* A constraint that lives in the prompt is not enforced.

**1. Filtering.** `persona.schemas()` is what goes in the request. The model
never sees a tool it may not call, so it cannot ask for one.

**2. Dispatch.** `tools.run(name, args, allowed=persona.tools)` refuses a name
outside the allowlist, whatever the instructions say and whatever anyone types
into the chat.

Point 2 exists because point 1 is advisory: a model that has seen a tool name in
conversation history can emit it. The refusal returns as a normal
`tool_result` with `is_error: true`, so the model can explain itself rather than
the turn dying — the loop already handles this
(`test_a_failing_tool_comes_back_as_an_error_not_a_crash`).

An allowlist, not a blocklist. A blocklist is not sufficient and never becomes
sufficient by being longer.

## The initial cast

`ARCHITECTURE.md` names `researcher` and `tutor-en`, both of which need
`search_web` — a tool the cockpit's own Tools panel places at layers 8–14. A
researcher that cannot reach the web is not a researcher, so the designed cast is
deferred and the first cast is built from the three tools that exist:

| Persona | Tools | Exists to show |
|---|---|---|
| `assistant` | `list_files`, `read_file`, `remember` | the default; unchanged behaviour from layer 6 |
| `codebase-guide` | `list_files`, `read_file` | an allowlist that genuinely removes a capability |
| `archivist` | `read_file`, `remember` | a different allowlist, overlapping but not nested |

The point of the cast is that the allowlist is **observable**: ask
`codebase-guide` to remember something and it cannot, because the tool is not in
its request. That is the layer demonstrating itself, which no amount of prose
achieves.

`assistant` is the default. If `personas/` is missing or empty, the harness falls
back to today's behaviour rather than failing to start.

## Switching

`/persona <name>` in the REPL; a switcher in the cockpit. The transcript is
**kept** across a switch — swapping hats should not lose the conversation.

One consequence to record rather than discover later: because both `tools` and
`system` change, a switch invalidates the entire prompt cache. Irrelevant now (no
caching, haiku turns cost fractions of a cent) but it is the reason layer 8
delegates to a sub-agent with a fresh context instead of switching the current
one — which is also Anthropic's documented recommendation for running part of a
task on a different model.

A comment at the switch site records this. No code changes because of it.

## Entry points

Both resolve the persona once, before the loop.

```python
# cli.py — REPL
persona = personas.load(personas.DEFAULT)
if user_input.startswith("/persona "):
    persona = personas.load(user_input.split(None, 1)[1])
    continue
reply = run_turn(client, messages, turn, persona,
                 build_system(user_input, turn, persona))
```

```python
# server.py — cockpit
class Message(BaseModel):
    text: str
    persona: str | None = None

persona = personas.load(message.persona or active)   # once, up front
```

`active` is a module-level *name*, and the distinction from the rejected design
matters. It is a remembered default — which persona the next unspecified request
should use — read exactly once at the top of a request and immediately resolved
into a frozen `Persona`. It is never read again during the turn. A switch landing
mid-turn changes which persona the *next* request defaults to and cannot reach a
turn already in flight, because that turn is holding an object, not a name.

## Signature changes

| Function | Change |
|---|---|
| `agent.run_turn` | gains `persona`; reads `persona.model` and `persona.schemas()` |
| `agent.build_system` | gains `persona`; returns `persona.instructions` instead of `SYSTEM` |
| `tools.run` | gains `allowed`; refuses a name outside it |
| `server.chat` | resolves a persona per request; `Message` gains an optional field |

`agent.MODEL` and `agent.SYSTEM` become the fallback used only when `personas/`
is absent. They are not deleted — the cockpit's guardrails panel reads live
values, and layer 12's port needs the same constants.

## Cockpit

The reserved `Personas` nav item becomes real, and `/api/system` stops returning
`personas: []`.

- A card per persona: name, description, model, and the tools it may call —
  read from the file, so the panel cannot drift from what the harness enforces.
- A switcher; the active persona shows in the chat header.
- Every panel reads the running system. This one is no exception.

## Testing

Stubbed and free, in the existing style:

- A persona loads from disk: frontmatter parsed, instructions kept.
- `schemas()` returns only allowed tools — and the request carries only those.
- `tools.run` refuses a name outside the allowlist, and the refusal arrives as
  `is_error: true` rather than an exception escaping the loop.
- Two personas in one test, proving no shared state.
- A switch keeps the transcript.
- Malformed frontmatter fails loudly with the file named, not silently with a
  half-built persona.
- `/api/personas` matches what `personas/` actually contains.

The existing security tests in `tests/test_tools.py` — the path boundary and the
hidden-file refusal — are unchanged and stay unchanged. Per `CONTRIBUTING.md`
they are not relaxed to make a feature work.

## Not in this layer

Recorded here because they came up while designing it, and because knowing where
they land is what keeps this layer small.

| Thing | Layer | Note |
|---|---|---|
| Intent routing — summarise the input, decide where it goes | 8 | This is the orchestrator reading each persona's `description` and writing a brief. `ARCHITECTURE.md` already frames the brief as "the same skill as writing a good ticket". Layer 7 supplies the descriptions it will route on. |
| Asking a clarifying question instead of guessing | 8 | A routing decision, not a persona property |
| `delegate(persona, task)` | 8 | Fresh working memory, `depth + 1`, subset rule |
| LLM-as-judge, golden dataset, eval scenarios | 11 | The judge is a persona too, once personas exist |
| LangGraph loops, LangChain agent calls, LangSmith | 12 | Deliberately after the hand-built version, per the README's stated order |
| Calendar, weather, notes, lessons | 14 | Each is a tool, and each needs the tool-authoring gate first |
| Apple Notes specifically | 14 | Notes has no public API. The supported path is AppleScript/JXA via `osascript`, or the macOS `shortcuts` CLI; both need Automation permission granted once per app. Both also require a `run_command`-shaped tool, which is a security boundary this repo does not yet have — which is exactly why it waits for the guarded set at layer 13. |

## Risks

**`pyyaml` is a new dependency.** Mitigated by `safe_load` and `pip-audit` in the
gate. It is the first dependency added for convenience rather than necessity, and
that is worth noticing.

**The cast is illustrative, not useful.** `codebase-guide` and `archivist` exist
to make the allowlist visible, not because anyone needs them daily. The useful
cast arrives with the tools that make it useful. Layer 7 is the mechanism.

**`tools.run` grows a required parameter.** Four existing tests call it; they are
updated in the same commit. A default of "all tools" was considered and rejected
— a permissive default is how allowlists quietly stop being enforced.
