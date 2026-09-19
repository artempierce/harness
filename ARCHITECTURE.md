# Architecture

**Ninja** — a personal assistant that is a cast of agents rather than one.

This document describes what the system is meant to become. It is written ahead of
the code on purpose — the project is being built layer by layer to learn how an
agent harness works, and this file is the map. Sections describing layers that
aren't built yet say so.

---

## The one idea

Everything here follows from a single observation:

> A **persona** is data, not code. An **agent call** is a tool, not a new subsystem.

A persona is a markdown file — instructions, a description of when it applies, a
list of tools it may use, and a model setting. Switching personas swaps which file
gets loaded into the system prompt. Delegating to a persona is a tool call whose
implementation happens to run another loop.

This means the loop never changes. One loop, written once, runs the orchestrator
and every sub-agent. Adding the tenth persona costs one file and zero lines of
Python.

---

## A turn, end to end

```
you type
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ WORKING MEMORY — assembled fresh every turn, then discarded   │
│                                                               │
│  active persona's instructions      ← procedural memory       │
│  facts retrieved, if the gate says so ← semantic memory       │
│  recent turns                       ← episodic memory         │
│  this conversation so far                                     │
└──────────────────────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ LOOP                                                          │
│                                                               │
│   model ──► asks for a tool? ──► run it ──► result ──► model  │
│     │                                                    ▲    │
│     │              one of those tools is `delegate`,     │    │
│     │              which starts a whole new loop ────────┘    │
│     ▼                                                         │
│   no more tools → done                                        │
└──────────────────────────────────────────────────────────────┘
   │
   ├──► reply to you
   ├──► append the turn to episodic memory
   └──► write a trace record
```

Everything in working memory is thrown away when the turn ends. Nothing the model
"remembers" is remembered by the model — it is re-assembled from storage on the
next turn. That is the single most important sentence in this document.

---

## Components

### Gateway

How a message gets in. `ninja` in the terminal today; `ninja dashboard` and the
web chat panel later. A gateway
does one job: turn an incoming message into a turn, and a reply into whatever the
channel needs. No logic lives here.

### Working memory

A Python list of messages, built fresh each turn and discarded after. The API is
stateless — the entire conversation is re-uploaded on every single request. Most of
this architecture exists to decide what goes into this list.

### The loop

Ask the model. If it replied with a tool request, run the tool, append the result,
ask again. Stop when it stops asking.

Guardrails:

| Guardrail | Stops |
|---|---|
| `MAX_STEPS` (6) | One loop spinning forever |
| `MAX_DEPTH` (2) | A → B → C → A delegation chains |
| Tool allowlist per persona | The tutor calling `run_command` |
| Path boundary | Tools reading outside the project, or reading `.env` |
| Confirmation gate | Any write the user hasn't seen first |
| Guarded set (allowlist) | The system editing its own guardrails (see Self-extension) |
| Allowlist subset rule | Delegating to a persona with more privilege than the caller |
| Per-turn cost ceiling | A pipeline fan-out with no dollar bound |

### Tools

A schema the model reads, and a function it can't. The model never executes
anything — it asks, the harness decides. Planned set:

- `list_files`, `read_file` — built
- `write_file`, `run_command` — gated behind confirmation
- `search_web`
- `delegate(persona, task)` — the interesting one
- `create_persona(...)` — the risky one

### Memory

Three stores, three different questions.

| Store | Answers | How it's read | Where it lives |
|---|---|---|---|
| **Procedural** | "How should I behave?" | Loaded by persona | `personas/*/PERSONA.md` |
| **Semantic** | "What's true about this person?" | Vector similarity | `facts` table |
| **Episodic** | "What happened, and when?" | SQL for recency, vectors for relevance | `episodes`, `chat_log` |

**The retrieval gate** sits in front of semantic memory and decides whether to
search at all. "What's 2+2" does not need a vector search. Without the gate, every
turn pays retrieval latency and pollutes its own context. This is the highest
leverage decision in the memory system and the one most implementations skip.

**The decision log** is a fourth store, append-only, and the one that makes
self-extension auditable. Every time Ninja makes a call that isn't obvious —
which persona to delegate to, whether to skip retrieval, what a proposed tool
should be named, why a spec was written one way — it writes a short entry: what
it decided, what it rejected, and why. Entries are never edited or deleted, only
superseded.

It is not memory in the retrieval sense; nothing reads it back into working
memory automatically. It exists so that when the system behaves strangely three
weeks from now, there is a record of reasoning to read rather than a trace of
actions to infer from. The dashboard shows it as a timeline.

**Consolidation** runs every N conversations: a cheaper model reads recent episodes
and distils them into durable facts. Memory gets denser rather than merely longer.

### Personas

A directory per persona:

```
personas/
  assistant/     PERSONA.md      the default orchestrator
  researcher/    PERSONA.md
  tutor-en/      PERSONA.md
```

Each `PERSONA.md` carries frontmatter and instructions:

```markdown
---
name: researcher
description: Use for open questions needing sources, comparison, or current info.
tools: [search_web, read_file]
model: claude-haiku-4-5
---

## How to research

Start by naming what would count as an answer...
```

`description` is what the orchestrator reads when deciding whether to delegate — it
is a tool description in disguise, and it should be written like one: when to use
this, not what it is.

`tools` is an allowlist, enforced by the harness rather than requested in the
prompt. A persona cannot use a tool that isn't listed, regardless of what its
instructions say or what anyone types into the chat.

> A persona covering health, money, or emotional wellbeing is where the
> instructions matter most and where eval cases are worth writing first. Put the
> boundaries in `PERSONA.md` explicitly — what it should decline, and what it
> should point the user toward — rather than assuming the model will infer them.

### Delegation

`delegate(persona, task)` is a tool. Its implementation:

1. Loads that persona's `PERSONA.md` and tool allowlist.
2. Builds a **fresh** working memory containing the task brief — **not** the
   parent's conversation.
3. Runs the same loop, at `depth + 1`.
4. Returns the sub-agent's final text as the tool result.

Context isolation is deliberate. Passing the transcript down multiplies cost,
leaks one persona's framing into another, and makes traces unreadable. The
orchestrator's job is to write a good brief — which is the same skill as writing
a good ticket.

Sub-agents default to a cheaper model than the orchestrator. Fan-out is where
cost stops being theoretical.

### Persona authoring

The agent can propose new personas. It may not create them silently.

```
you   → "I need something that helps me prep for interviews"
agent → asks what kind, what tone, what it should never do
agent → drafts PERSONA.md and shows it to you
you   → approve
agent → writes personas/interview-prep/PERSONA.md, git commits it
```

The gate is the feature. A system that can rewrite its own instructions needs an
approval step and an audit trail; git provides both, and `git revert` is the
undo button.

### Trace

One record per turn, written to SQLite and to a file. A tree: every model call,
every tool call and its result, every delegation as a nested subtree, with tokens,
cost, and duration at each node.

Tracing is built early — before the memory layers — because every layer after it
is invisible without it. You cannot tell whether retrieval returned the right
thing by reading the final answer.

### Eval, diagnose, release

Fixed cases run through the agent; each result scored two ways — *was it good*
(a second model judging against expectations) and *was it healthy* (tokens,
latency, errors, straight from the trace). A failing score is diagnosed from the
trace, not guessed at. Fixes ship behind a gate: new prompt version, model change,
different top-k, re-run the evals, then release.

### The dashboard

A local web app. Chat on the right, infrastructure on the left:

- **Overview** — cost, turns, tool calls, facts, events; the retrieval gate's
  skip/retrieve ratio
- **Architecture** — this diagram, live, clickable, with real counters on the boxes
- **Loop** — the current turn unfolding: each model call, tool call, and delegation
- **Memory** — browse and edit procedural, semantic, episodic
- **Personas** — the cast, their tools, their usage
- **Database** — tables and a read-only SQL console
- **Ops** — traces, eval scores, config

The dashboard is not a ninth subsystem. Every panel is an existing layer, rendered.

---

## Self-extension

Ninja can propose changes to itself: new tools, new personas, new capabilities.
It cannot apply them. Every path from "good idea" to "running code" passes
through a human, and the checks that enforce that are written in Python, not in
a prompt.

This is the most dangerous thing in the architecture. The design below has been
adversarially reviewed once; the notes marked **⚠** are holes that review found
in an earlier version, kept visible because they are the instructive part.

### The extension point

A self-extending system needs somewhere to extend *into*. That place must be
separate from the place that enforces the rules, or the two requirements
collide: tools have to be registered, but the registry is exactly what an agent
must not edit.

```
ninja/registry.py     guarded — scans and loads. Never written by an agent.
ninja/tools/          the extension point. One file per tool.
  read_file.py        each exports SCHEMA and run()
  calendar.py         ← a new capability is a new file here
personas/             the extension point for personas. One directory each.
evals/cases/          eval cases, as YAML data
```

Nothing is wired by hand. `registry.py` scans both directories at startup and
builds the schema list and the dispatcher. Adding a capability is adding a file.

> **⚠ The hole this replaces.** The first version put `SCHEMAS` and `run()` in a
> single guarded `tools.py`. A new tool was an entry in that list and a branch in
> that dispatcher — so the pipeline could not produce a complete tool without
> editing a file it was forbidden to touch. A self-extension design with no
> extension point. Either the human hand-wires every result, or the guard comes
> off the file that enforces the path boundary.

Note what this trades: **dropping a file into `ninja/tools/` is arbitrary code
execution at next startup.** That is accepted deliberately, and it is why that
directory is code tier — PR required, no exceptions — rather than a convenience.

### The guarded set is an allowlist

```python
# Everything under these is guarded.
GUARDED_ROOTS  = ["ninja/", "sql/", ".github/", ".git/", "pyproject.toml", "uv.lock"]
# Except these, which are the extension points.
WRITABLE       = ["ninja/tools/", "personas/", "evals/cases/"]
```

> **⚠** The first version was a denylist naming five specific files. Denylists
> fail open, and this one would have rotted on a schedule: when the depth cap
> moves out of `agent.py` into `delegate.py` at layer 8, a list naming
> `agent.py` silently stops covering it — and a weakened check looks exactly
> like a working one. Every layer from 3 to 12 adds a file the old list did not
> know to name.

Two related traps, both in the current code:

- `ROOT = Path(__file__).resolve().parent.parent` is the repo under an editable
  install and `site-packages` under a normal one — where the "project boundary"
  becomes every installed package, and `sitecustomize.py` is code execution at
  next start. The boundary must be anchored explicitly, not derived from
  `__file__`.
- `.venv` is inside `ROOT` and protected only incidentally, by the rule that
  refuses dotfiles. Rename it `venv/` and that protection silently vanishes.

### What the guard cannot do

**`run_command` defeats all of the above.** The guard is an `if` inside
`write_file`; a shell is a different function. `python -c`, `sed -i`, `tee`,
`>`, `git checkout origin/x -- ninja/agent.py` — a process that can spawn a
shell cannot be path-restricted. This is not a gap to patch; it is the shape of
the problem.

So the real boundary for anything holding `run_command` is OS-level — a
separate user, a container, or a read-only bind mount over `ninja/` — and until
that exists, the honest statement is that the coder persona is trusted, and the
pull request is the only thing standing between it and `main`.

Likewise **evals must be data, not Python.** A tester that can write
`evals/conftest.py` can set `guard.GUARDED = []` in a file pytest imports before
any test, in the same interpreter that later imports the guard. Monkeypatching
in `conftest` is ordinary practice, not an exploit. Cases are YAML; the runner
is guarded.

### Three tiers, cut by privilege

The tiers are not about file type. A markdown file and a Python file can grant
identical power. What separates them is whether the change **grants**, **uses**,
or **enforces** privilege.

| Tier | What it is | Gate |
|---|---|---|
| **Instructions** | Persona prose. `tools:` unchanged, or a subset of an already-approved set | Confirmation in chat |
| **Privilege** | Any new or widened `tools:` list, any `model:` change, any new tool | Reviewed pull request |
| **Enforcement** | The loop, the caps, the registry, the guard, the path boundary | A human writes it |

> **⚠** The first version cut by artifact type and called personas low-risk
> because "allowlists are enforced by the harness." But the harness reads the
> allowlist *from the file the agent just wrote*. `tools:` in frontmatter is a
> capability grant. Under that model, asking for an interview-prep persona could
> hand out shell access through a thirty-second chat confirmation — with honest
> reasoning, an approving skim, and no malice anywhere in the chain.

Two rules fall out, and both are enforced in `registry.py`:

1. **Delegation de-escalates.** A child's tool list must be a subset of its
   parent's. Without this, an orchestrator holding `read_file` and `delegate`
   can reach `run_command` through a persona — privilege escalation as a
   documented feature.
2. **The tool universe is bounded by a human.** The union of every persona's
   allowlist cannot exceed a set defined in a guarded file. Personas select from
   that set; they never extend it.

`model:` is a cost grant, not a style choice. And `description:` is text the
orchestrator reads when routing — so if web results ever reach `episodes`, get
consolidated into `facts`, and land in working memory, that is a path from a web
page to routing behaviour. Worth knowing before `search_web` exists.

### The build pipeline

```
  you: "ninja, could you learn to read my calendar?"
        │
        ▼
  ┌──────────────┐
  │  ARCHITECT   │  reads the repo, writes a spec, writes no code
  └──────────────┘
        │
     ── HUMAN GATE 1 ──  approve the shape, before tokens are spent
        │
        ▼
  ┌──────────────┐
  │    CODER     │◄──────────┐  branch + worktree. Holds run_command,
  │              │           │  and is therefore the security perimeter.
  └──────────────┘           │
        │                    │ evals fail → back, max 3 rounds
        ▼                    │
  ┌──────────────┐           │
  │   TESTER     │───────────┘  writes evals/cases/*.yaml FIRST.
  │              │              Cannot write ninja/.
  └──────────────┘
        │  red-then-green verified by git, not by trust
        ▼
  ┌──────────────┐
  │    CRITIC    │  reads only the diff, fresh context.
  │              │  A rejection BLOCKS — it is a gate, not a comment.
  └──────────────┘
        │
     ── HUMAN GATE 2 ──  a pull request: spec, diff, eval results, critique,
        │                cost and latency deltas
        ▼
     merge → restart → the capability exists
```

Every arrow is a `delegate` call. This is layer 8's machinery pointed at the
repo, not a new engine.

**Red-then-green, mechanically.** "Eval cases written before the implementation"
is a prompt-level claim in a document that correctly says prompt-level
restrictions are requests. It is cheaply checkable instead: the eval commit must
be an ancestor of the implementation commit, and CI checks out that ancestor and
asserts the new cases **fail** there. Verified by git. Without it — and given
the tester also authors the expectations a model-judge grades against — the
whole test story is circular.

**The pipeline is a state machine, not a line.** A `proposals` table holds one
row per request with a status: `spec` → `approved` → `building` → `testing` →
`review` → `pr` → `merged` / `abandoned`. Without it, an API error or a step cap
halfway through leaves a branch, a worktree, and a spec that existed only in a
discarded working memory. With it, the pipeline is resumable, visible in the
dashboard, and the conflict story is free: **one open proposal at a time.**

**A cost ceiling, checked before each call.** `MAX_STEPS` and `MAX_DEPTH` bound
shape, not spend. Four personas, a loop each, up to three rounds, and no dollar
cap — while the trace records cost only *after* it was spent. The pipeline needs
a budget that aborts, not a report that explains.

### Why the roles are split

- **The architect writes no code**, so the spec is rejectable cheaply — the gate
  that catches "you asked for the wrong thing."
- **The critic sees only the diff**, with no memory of the reasoning that made a
  bad idea sound good an hour ago. Highest value per token in the pipeline.
- **The tester cannot write `ninja/`.** What protects you is the write scope, not
  the personality — an agent that can edit both an implementation and its test
  will make them agree. Keeping it a separate call also means it gets its own
  step budget.
- **The coder is the perimeter**, because it is the only role that needs
  `run_command`.

Three of the four do work the others cannot. A leaner version — reviewer and
coder, with evals locked — would be defensible; four is kept because exercising
delegation is the point. But the thing actually worth building here is the state
machine around them, not the personas.

### What rollback does not cover

`git revert` reverts code. It does not revert effects. A merged tool that wrote
rows to `facts`, migrated a table, or touched the filesystem leaves all of that
behind, and `sql/` has no migration story yet. Decide whether the database is
versioned before the first tool writes to it.

And a restart discards the conversation that asked for the capability. "You
merge, you restart, the capability exists" is true, and it also ends the session
that motivated it.

### Gates are itemized, not binary

A gate that asks "approve this whole thing, yes or no?" is a bad gate twice
over. It forces all-or-nothing on a proposal where you object to one line, and
when you say no it hands the agent no information about *which* line.

So a proposal is **a list of decisions**, and each one is accepted, declined, or
changed independently:

```
PROPOSAL #7 · "could you read my calendar?"
────────────────────────────────────────────────────────────
 1 ▸ New tool  calendar_read                  [accept] decline change
     Reads events from a local .ics export. No network, no write.

 2 ▸ Grant     calendar_read → assistant      [accept] decline change
     Orchestrator only. Not available to delegates.

 3 ▸ Dependency  icalendar (PyPI)              accept [decline] change
     ~40kb, no transitive deps.
     ⚠ new supply-chain surface, and it parses untrusted input

 4 ▸ Tests     6 cases in evals/cases/calendar/  [accept] decline change
     malformed .ics · empty calendar · timezone boundary

 5 ▸ Cost      +180 input tokens on every turn  accept decline [change]
     The schema sits in context whether the tool is called or not.
────────────────────────────────────────────────────────────
A declined or changed item needs a reason. The reason becomes the brief
for the next round, and an entry in the decision log.
```

Three properties matter:

**Declining is instructive.** "Don't add the dependency — parse the .ics by
hand, it's one format" is a far better next brief than "no." The reason you give
is the input to round two, which is why it is required rather than optional.

**Every item is a real decision.** Padding the list with tasks rather than
choices is how this becomes fatigue. "Write the function" is not an item. A
dependency, a privilege grant, a permanent token cost, and a test plan are.

**Cost is an item.** A tool's schema is in context on every turn whether it is
ever called or not, so every accepted capability is a permanent tax on every
future request. Making that a line you approve rather than a number you discover
later is the whole reason it's listed.

### When a delegation needs a gate

Not every sub-agent call should stop and ask — gating a research question makes
the system unusable, and a gate that fires constantly is the one that erodes.
The subset rule already makes privilege *escalation* impossible, so the gate is
about effects, not authority:

| The sub-agent will… | Gate |
|---|---|
| Only read and reason | None. Traced, visible in the dashboard, not interrupted |
| Write anything | Writes land on a branch; the gate is the proposal |
| Spend past the turn budget | Stops and asks, with the spend so far |
| Reach outside — network, calendar, messages | Asks, every time, naming what it will touch |

### The gate that erodes

The most likely failure in this whole design is not a clever escape. It is
**approval fatigue**: the confirmation gate works on turn 1 through 20, and on
turn 40 the diffs are being skimmed and approved. A gate that fires constantly
stops being a gate.

So gate 2 should be rare and high-information — a real pull request, with
everything needed to judge it in one place — rather than a stream of small
confirmations that train the habit of saying yes.

### What this is not

It is not autonomy. Nothing merges without a human and nothing reaches
enforcement at all. The reason to build it this way is not safety theatre — a
system that proposes changes as specs, diffs, failing-then-passing tests and
critiques is a system whose reasoning you can inspect. An agent that quietly
edits itself is one you have to trust; this one you can read.

---

## Data

One SQLite file, `.ninja/state.db`:

| Table | Holds |
|---|---|
| `chat_log` | Every message, with persona and session |
| `episodes` | Dated events — what happened and when |
| `facts` | Durable facts, with embeddings |
| `traces` | One row per turn, tree stored as JSON |
| `evals` | Case, score, judge reasoning, run id |

---

## Repo layout

```
ninja/            raw Python — the version you can read
  cli.py          the `ninja` command
  agent.py        the loop
  tools.py        what the model may call
ninja_lc/         the LangGraph port — same layers, framework version
personas/         PERSONA.md per persona
sql/              schema, shared by both
evals/            test cases, shared by both
ui/               the dashboard
ARCHITECTURE.md   this file
```

`ninja/` and `ninja_lc/` implement the same system twice — once by hand, once
with LangChain/LangGraph/LangSmith. They share the database, the personas, and the
eval suite, so the same tests can run against both and the traces compared. The
raw version is never deleted; it is the reference for what the framework is doing.

---

## Build order

Each layer runs end to end before the next begins.

**Phase 1 — one agent**
1. Bare agent run · *built*
2. Loop, tools, stop condition · *built*
3. Tracing
4. Episodic memory
5. Semantic memory + the retrieval gate

**Phase 2 — seeing it**
6. The dashboard, first version — chat, live trace, database browser

**Phase 3 — the cast**
7. Personas: procedural memory, tool allowlists, manual switching
8. Delegation: `delegate` as a tool, depth limits, context isolation
9. Persona authoring, behind the confirmation gate

**Phase 4 — the system**
10. Consolidation
11. Eval, diagnose, release

**Phase 5 — the port**
12. `ninja_lc/` on LangGraph, traces to LangSmith, the two compared

**Phase 6 — self-extension**
13. The registry and the guarded set: an extension point, and an allowlist
    around it — built before anything can extend
14. Tool authoring: a new tool is a new file in `ninja/tools/`, on a branch,
    with YAML eval cases and red-then-green verified by git
15. The build pipeline: architect → coder → tester → critic, as a resumable
    state machine with a `proposals` table, back edges and a cost ceiling

---

## Open questions

- **Switching vs routing.** Layer 7 gives manual switching, which is predictable.
  Automatic routing — the orchestrator picking a persona without being asked — is
  a layer 8 decision, and it trades predictability for convenience.
- **Where memory is scoped.** Are facts global, or per persona? The tutor probably
  shouldn't read the wellbeing coach's notes. Undecided.
- **Parallel delegation.** The loop can return several tool calls at once, so
  fan-out is possible. Whether it's worth the complexity is a layer 8 question.
- **Capture from the phone.** Telegram or an iOS app, as a gateway rather than a
  new subsystem. The app is the easy half — the work is an authenticated HTTP API
  in front of the loop and deciding what runs on the laptop versus a server.
  Not scheduled.
- **Where `run_command` actually runs.** The guard cannot restrict a shell, so
  the coder persona is trusted until it runs somewhere isolated — a container, a
  separate user, or a read-only mount over `ninja/`. Unresolved, and it bounds
  how much of the pipeline can run unattended.
- **Whether `sql/` is versioned.** `git revert` does not undo a migration or the
  rows a merged tool wrote. Needs deciding before the first tool writes.
- **Who writes the decision log.** Ninja writing its own entries is the point,
  but an agent narrating its reasoning is not the same as reporting it. Whether
  entries should be generated in the turn or reconstructed from the trace
  afterwards is undecided, and it matters.
