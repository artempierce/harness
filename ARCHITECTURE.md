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
| Guarded paths | The system editing its own guardrails (see Self-extension) |

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

This is the most dangerous thing in the architecture and the reason the rest of
it is shaped the way it is.

### Three tiers of extension

Not all changes carry the same risk, and treating them identically means either
paranoid friction on the safe ones or dangerous ease on the rest.

| Tier | What it is | Applied by | Restart? |
|---|---|---|---|
| **Data** | A new persona — a `PERSONA.md` with instructions and a tool allowlist | Confirmation in chat, then written to disk | No — loaded per turn |
| **Code** | A new tool — Python, a schema, and eval cases | A reviewed pull request | Yes |
| **Core** | The loop, the guardrails, the gates themselves | A human writes it | Yes |

The tiers exist so that the common case — "I want a persona that helps me prep
for interviews" — stays a thirty-second conversation, while "add a tool that
runs shell commands" takes the long route through review.

**Core is not merely gated. It is off-limits.** No agent-driven path reaches it.

### The guarded core

An agent that can write tools can write a tool that removes its own restrictions.
It does not need to intend this; a plausible-sounding instruction is enough, and
the failure is silent — a weakened check looks exactly like a working one.

So the rule is:

> A guardrail must live somewhere the thing it constrains cannot reach.

In practice, `write_file` refuses a fixed list of paths regardless of who asks,
what the persona's instructions say, or how the request is phrased:

```
GUARDED = [
    "ninja/tools.py",      # the allowlist and the path boundary
    "ninja/agent.py",      # the loop and its step cap
    "ninja/guard.py",      # this list
    "pyproject.toml",      # entry points and dependencies
    ".github/",            # branch protection, CI
]
```

`guard.py` guards itself. That is not clever; it is the minimum. A guard list
the agent can edit is decoration.

Three properties make this hold:

1. **It's a check, not an instruction.** Prompt-level restrictions are requests.
   This is an `if` statement that raises.
2. **It's enforced at the tool, not the persona.** A persona's allowlist decides
   *which* tools it may use; the tool decides what it will do for anyone.
3. **Git is the outer layer.** The builder works on a branch and cannot push to
   `main`. Branch protection on GitHub is enforced by GitHub, not by anything
   running on this laptop.

Changing what's guarded is a human editing a file and committing it. There is no
chat command for it, deliberately.

### The build pipeline

A capability request becomes a pull request by passing through several personas,
each with narrow tool access. Ninja orchestrates; it does not build.

```
  you: "ninja, could you learn to read my calendar?"
        │
        ▼
  ┌──────────────┐
  │  ARCHITECT   │  reads the repo. Writes no code.
  │              │  Produces a spec: what it touches, what could
  │              │  break, what "done" means.
  └──────────────┘
        │
        ▼
     ── HUMAN GATE 1 ──  you read the spec and approve the shape
        │
        ▼
  ┌──────────────┐
  │    CODER     │  works on a branch, in a worktree.
  │              │  write_file is guarded. Cannot touch main.
  └──────────────┘
        │
        ▼
  ┌──────────────┐
  │   TESTER     │  writes eval cases FIRST, then runs them.
  │              │  Cannot edit the implementation — only evals/.
  └──────────────┘
        │
        ▼
  ┌──────────────┐
  │    CRITIC    │  reads only the diff. Fresh context, no memory
  │              │  of the conversation that produced it.
  └──────────────┘
        │
        ▼
     ── HUMAN GATE 2 ──  a real pull request, with the spec, the
        │                diff, the eval results and the critique
        ▼
     you merge. you restart. the capability exists.
```

Each arrow is a `delegate` call. The pipeline is layer 8's machinery pointed at
the repo — there is no new orchestration engine, and if delegation works, this
mostly works.

### Why the roles are split

Four personas rather than one agent doing all four, for reasons that are about
evidence rather than tidiness:

- **The architect writes no code**, so the spec is a real artifact you can reject
  cheaply — before any tokens are spent building the wrong thing.
- **The tester cannot edit the implementation.** An agent that can change both
  the code and the test will make them agree, and the passing suite means
  nothing. Separating them is the entire value of the gate.
- **The critic sees only the diff.** Fresh context, no attachment to the plan
  that produced it, no memory of the reasoning that made a bad idea sound
  reasonable an hour ago.
- **Ninja orchestrates but does not build**, so the conversation stays readable
  while the work happens in traces you can open.

### What "tested" has to mean

A capability is not accepted because it ran once in a chat. The pull request must
carry:

- **eval cases written before the implementation**, in `evals/`, that fail
  without the change and pass with it
- **the existing suite still passing** — layer 11's regression check, because a
  new tool changes what the model sees on every turn, and the failure usually
  shows up somewhere else
- **a trace of the capability working**, linked from the PR
- **cost and latency deltas**, because a new tool in the schema costs input
  tokens on every single request whether it's called or not

That last one is the non-obvious tax on self-extension: tools are not free when
idle. A system that can add tools forever will slow itself down and never say so.

### What this is not

It is not autonomy. Nothing merges without a human, and nothing reaches the core
at all. The reason to build it this way is not safety theatre — it is that a
system which proposes changes as specs, diffs, tests and critiques is a system
whose reasoning you can inspect. An agent that quietly edits itself is one you
have to trust; this one you can read.

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
13. Tool authoring: the builder writes code, on a branch, with tests
14. The build pipeline: architect → coder → tester → critic → pull request
15. The guarded core: what the system may never change, enforced in code

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
- **Who writes the decision log.** Ninja writing its own entries is the point,
  but an agent narrating its reasoning is not the same as reporting it. Whether
  entries should be generated in the turn or reconstructed from the trace
  afterwards is undecided, and it matters.
