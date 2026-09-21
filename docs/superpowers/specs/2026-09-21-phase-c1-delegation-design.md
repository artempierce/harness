# Phase C1: Delegation (layer 8b)

**Status:** proposed — awaiting approval, no code written
**Branch (when approved):** `phase-c1-delegation`, from `main`
**Depends on:** A1 (`tools.run` already takes `persona=`), 8a (personas, threads)
**Design authority:** `ARCHITECTURE.md` §Delegation, §Guardrails, §"When a delegation needs a gate". This spec
follows those decisions and works out the mechanics they leave open.
**Reference:** waku's `delegate_task` (`waku/tools/workspace.py`, not read in detail)

**This is the wide-blast-radius phase.** It changes the shape of the loop, adds a tool that
spawns loops, and adds the first privilege rules. It gets a fuller review than A and B did.

---

## What this adds

The assistant can hand a self-contained job to another persona and get its answer back.

```
you:        "check the interview-coach persona file and tell me if its tone is consistent"
assistant:  delegate(persona="interview-coach", task="Read personas/interview-coach/PERSONA.md
                    and list any instructions that contradict each other. Reply in under 5 lines.")
              │
              ▼   a whole new loop, depth 1, the coach's model and instructions,
              │   a FRESH context holding only that task — not this conversation
              ▼
            "Two contradictions: …"          ← returned as the tool result
assistant:  "Here's what it found: …"
```

## What is already decided (ARCHITECTURE.md)

| Decision | Source |
|---|---|
| A delegation is a **tool** starting the same loop at `depth + 1` | §Delegation |
| The child gets a **fresh** working memory holding the task brief, **not** the parent's conversation | §Delegation |
| `MAX_DEPTH = 2` | §Guardrails |
| **A child's tool list must be a subset of its parent's** | §Guardrails, §Personas |
| Read-only sub-agents: no gate, but traced. Writes: gated. Spend past the budget: stop. | §"When a delegation needs a gate" |
| A per-turn cost ceiling, checked **before** each call | §Guardrails |

## The mechanics

### Getting `delegate` into `tools.run` without a circular import

`delegate` has to start a loop, but the loop lives in `agent.py`, which imports `tools`.
So `tools.py` never imports `agent`. Instead `agent.run_turn` builds a closure and hands it
down:

```python
def run(name, args, allowed, *, persona=None, spawn=None) -> str:   # spawn: Callable[[str, str], str]
    ...
    if name == "delegate":
        if spawn is None: raise ValueError("delegate needs a runtime to spawn into.")
        return spawn(args["persona"], args["task"])
```

The allowlist gate still runs **first**. `spawn=None` keeps every existing call working
unchanged — the same backward-compatibility check A1 used.

### `agent.py`

`run_turn` gains `depth: int = 0`. It builds `spawn` as a closure over `client`, `trace`,
the current `persona` and `depth`, and passes it (with `persona=persona.name`) to
`tools.run`. The closure calls a new `_delegate(...)`, which does all the checking and
then calls `run_turn` again:

```
_delegate(client, trace, parent, depth, name, task):
  1. child_depth = depth + 1;  child_depth > MAX_DEPTH            → refuse
  2. trace.delegations >= MAX_DELEGATIONS_PER_TURN                → refuse
  3. child = personas.load(name)          # unknown / path-like names raise here already
  4. effective tools for the child      (see below)
  5. effective(child) ⊄ parent.tools                              → refuse, naming the excess
  6. run_turn(client, [{"role":"user","content": task}], trace, child_eff,
              system=<child instructions + its learned rules>, depth=child_depth)
  7. return that loop's final text
```

Every refusal is a `ValueError`, which the existing loop already turns into a tool error
the orchestrator reads and can recover from.

### Effective tools — where "read-only" and "no deeper" are enforced

A child runs with a **reduced copy** of its persona (`Persona` is a frozen dataclass, so
`dataclasses.replace`). Its `tools` are:

```
child.tools  −  WRITE_TOOLS {remember, add_rule}
             −  {delegate}  when child_depth >= MAX_DEPTH
```

Because the reduced persona is what the child loop runs with, `persona.schemas()` and the
dispatch-time allowlist in `tools.run` both enforce it with no new code path. A child
cannot call a tool it was never given even if it guesses the name.

**The subset rule is checked on effective lists** — what each side could actually call:
`effective(child) ⊆ parent.tools`. Refuse, do not clamp: silently trimming a persona changes
what it does without anyone deciding that. The error names the excess tools.

### What the child sees

- **System prompt:** its own persona instructions + its own learned rules (A1). **No retrieved
  facts, no gate.** The orchestrator's job is to write a good brief; memory isolation is the point.
- **Messages:** exactly one — the task.
- **Nothing** from the parent's conversation.

The orchestrator is told who it can delegate to. `build_system` appends, for any persona
holding `delegate`, a list of eligible personas (`name: description`, excluding itself and any
that would fail the subset rule). This is the same "description as a tool description in
disguise" the router already relies on.

### Bounding the fan-out

`MAX_STEPS` and `MAX_DEPTH` bound *shape*, not *spend*: 3 levels of 6 steps is up to 216 calls.
Two more limits, both counters on the `Trace` (already the per-turn object that carries cost):

| Limit | Value | Effect |
|---|---|---|
| `MAX_DELEGATIONS_PER_TURN` | 3 | 4th `delegate` is refused |
| `MAX_TURN_COST_USD` | 0.25 | checked **before each model call**, at every depth |

When the budget is hit the loop stops the same way the step cap does: it appends an assistant
message `[stopped: turn budget of $0.25 reached]` and returns it, so the transcript stays valid.
Because the parent's next call checks too, a child that exhausts the budget also ends the turn.

**`0.25` is a guess, not a measurement.** Rough arithmetic: 3 delegations × 6 steps × ~6k input
tokens at haiku pricing is about $0.11 plus output. It should trip on runaway turns and not on
heavy legitimate ones. It is a named constant, and the first real measurements should replace it.

### Trace

One trace per turn, as now. A delegation adds:

- a `delegate` event: `{persona, depth, task (≤200 chars), ok, ms}`
- a `depth` field on `model` and `tool` events (default 0; old rows read it with `.get`)

Child model calls are already tagged with the child's persona name and roll into the turn's
tokens and cost through `Trace.model`, so **cost lands once, in the receipt of the turn that
caused it.**

`print_one` indents by depth. `ui/index.html` gets a branch for `delegate` — B1 shipped a new
event type and had to add this after the dashboard rendered it as `undefined(undefined)`, so it is
in the spec from the start.

### What is *not* persisted

The child's messages are not written to `chat_log`. Only the parent's exchange is, so recall,
consolidation and the mirror see one conversation. The child's answer survives as the
`delegate` tool's output in the trace.

## Who gets `delegate`

`assistant` only. `interview-coach` does not — it is a leaf, and keeping it one shows the
subset rule working (`coach.tools ⊆ assistant.tools`). Persona files are edited to match.

## Testing

`StubClient` with scripted replies for parent and child; no live API, no spend. Each test names
its bug and gets the break-it-and-see-RED treatment.

- **Context isolation (the one that matters most).** The child's API call receives exactly one
  message — the task — and the parent's earlier history and facts appear nowhere in `client.seen`.
- **Subset rule, both directions:** a child whose tools fit is allowed; a child with one extra tool
  is refused and the error names it. A refused delegation makes no API call.
- **Depth:** a grandchild at depth 2 runs; a delegation that would be depth 3 is refused; a
  depth-2 child does not see `delegate` in its schemas.
- **Read-only children:** a child persona that lists `remember` and `add_rule` cannot call either,
  even by name (asserted through `tools.run`'s allowlist, not just the schema).
- **Per-turn cap:** the 4th delegation is refused.
- **Budget:** a stub priced to cross the ceiling stops the loop with the documented message, at
  depth 0 and inside a child.
- **The allowlist gate still runs first:** a persona without `delegate` cannot spawn, and the
  existing all-schemas test (`{}` args, empty allowlist) still passes.
- **Unknown and path-like persona names** are refused before any file is read.
- **Trace:** child model calls carry the child's persona name and depth; turn tokens and cost
  equal the sum across depths; `delegate` events appear; `print_one` renders them.
- **No transcript leak:** after a turn with a delegation, `chat_log` holds only the parent's exchange.
- **Existing suite passes with no test rewrites** except the four tests that pin the on-disk
  personas' exact tool lists (`assistant` gains `delegate`), which change deliberately.

## Not in this change

- Parallel delegation.
- A tree view in the dashboard beyond the event and indentation.
- Asking you to raise the budget mid-turn (ARCHITECTURE's "stops and asks"). This aborts; asking
  needs a confirmation channel that does not exist yet.
- Write-capable children — those wait for the proposal/branch gate in layers 9 and 13–15.
- Cheaper-model defaults for children. `model:` stays a per-persona declaration.

## The risks

- **Prompt injection gets one more hop.** Text the orchestrator read from a file becomes a brief;
  the child's answer returns as an ordinary tool result. The subset rule, read-only children, the
  depth and count caps and the budget bound what a hijacked chain can *do*; they do not stop it
  being asked. `ARCHITECTURE.md` already flags that a persona `description:` is text the
  orchestrator reads.
- **Cost is now multiplicative.** The budget is the backstop, and it is untested against real usage.
- **The loop's signature changed.** `run_turn` is called from the REPL and the server; both pass
  `depth` by default, but a mistake here affects every turn, not just delegating ones.

## Decisions

1. **`MAX_DEPTH = 2`**, as ARCHITECTURE.md says. Recommended. (`1` would forbid grandchildren; nothing
   here needs them yet, but the documented number stands unless you change it.)
2. **Read-only children** in v1. Recommended: it is what the gate table implies, and it removes the
   cross-persona write path (a hijacked orchestrator asking the coach to `add_rule`).
3. **Caps:** 3 delegations per turn, $0.25 per turn. Recommended as starting values, with the budget
   flagged as unmeasured.
4. **Refuse, don't clamp**, on a subset violation. Recommended.
5. **No facts for children.** Recommended; the brief is the interface.
6. **`delegate` for `assistant` only.** Recommended.
