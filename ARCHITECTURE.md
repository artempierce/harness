# Architecture

A personal assistant that is a cast of agents rather than one.

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

How a message gets in. The terminal REPL today; the web chat panel later. A gateway
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

## Data

One SQLite file, `.harness/state.db`:

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
harness/          raw Python — the version you can read
harness_lc/       the LangGraph port — same layers, framework version
personas/         PERSONA.md per persona
sql/              schema, shared by both
evals/            test cases, shared by both
ui/               the dashboard
ARCHITECTURE.md   this file
```

`harness/` and `harness_lc/` implement the same system twice — once by hand, once
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
12. `harness_lc/` on LangGraph, traces to LangSmith, the two compared

---

## Open questions

- **Switching vs routing.** Layer 7 gives manual switching, which is predictable.
  Automatic routing — the orchestrator picking a persona without being asked — is
  a layer 8 decision, and it trades predictability for convenience.
- **Where memory is scoped.** Are facts global, or per persona? The tutor probably
  shouldn't read the wellbeing coach's notes. Undecided.
- **Parallel delegation.** The loop can return several tool calls at once, so
  fan-out is possible. Whether it's worth the complexity is a layer 8 question.
- **Capture from the phone.** Telegram is the likely channel, as a gateway rather
  than a new subsystem. Not scheduled.
