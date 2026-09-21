# Ninja — System Design

**What this is:** the map of the whole system — what runs when you type a
message, which model is in charge at each step, what memory it has, where that
memory physically lives, and how the control plane keeps it from misbehaving.

**How to read it:** Part 1 is one message traced end to end. Parts 2–6 open up
each subsystem it touched. Part 7 marks what is built versus planned. Part 8
compares this design to waku-agent. Part 9 is the LangGraph/LangSmith migration
path.

Every box in this document maps to a file. Where it does, the file is named.

---

## 0. The one idea

**The model remembers nothing. Working memory is rebuilt from storage on every
single turn, then thrown away.**

If you hold one sentence from this document, hold that one. Everything else —
three memory stores, a retrieval gate, a router, consolidation — exists to
answer one question well: *given this message, what should be in the context
window this time?*

An LLM call is stateless. It sees a system prompt and a message list, and
nothing else. "The agent remembers I'm a QA engineer" is never true of the
model; it is true of the assembly step that put that fact into the prompt
before the call.

---

## 1. One message, end to end

You type `what am I building?` into the REPL (`ninja/cli.py` → `ninja/agent.py`)
or the cockpit (`ninja/server.py`).

```
 1. GATEWAY        ninja/cli.py, ninja/server.py
       │           Captures the text. Opens a Trace.
       ▼
 2. ROUTE          ninja/router.py         ← MODEL CALL #1 (cheap classifier)
       │           "Which conversation does this belong to?"
       │           Reads each persona's `description`. Sticky by default.
       ▼
 3. LOAD PERSONA   ninja/personas.py
       │           personas/<name>/PERSONA.md → instructions, tool allowlist, model
       ▼
 4. GATE           ninja/semantic.py
       │           "Is this turn worth retrieving facts for?"
       │           FTS5 keyword search, top-3. A skip is RECORDED, not silent.
       ▼
 5. ASSEMBLE       ninja/agent.py :: build_system()
       │           system prompt = persona instructions
       │                         + retrieved facts (if the gate said yes)
       │           messages      = episodic.recall(thread) + your message
       ▼
 6. LOOP           ninja/agent.py :: run_turn()   ← MODEL CALL #2..N (the agent)
       │           ┌──────────────────────────────────────────┐
       │           │ model replies                            │
       │           │   ├─ wants a tool? → tools.run() → result│
       │           │   │                    └─ back to model ─┤
       │           │   └─ no tool? → done                     │
       │           └──────────────────────────────────────────┘
       │           Hard stop at MAX_STEPS = 6.
       ▼
 7. PERSIST        ninja/episodic.py, ninja/trace.py
       │           chat_log ← your message + the reply
       │           traces   ← one row: duration, tokens, cost, every event
       ▼
 8. REPLY          back to the terminal or the browser
```

### Which model is in charge, and when

| Step | Who | Model | Why that one |
|---|---|---|---|
| 2. Route | Router | `personas.DEFAULT_MODEL` (`claude-haiku-4-5`) | One short classification. Cheap and fast beats smart. |
| 6. Loop | The persona | `persona.model` from its `PERSONA.md` | The persona owns its model. A coach can be cheap; a researcher can be expensive. |
| — Consolidate | Summarizer (layer 10, not built) | cheap | Batch work, quality matters less than cost. |
| — Judge | Eval (layer 11, not built) | strong | Judging output needs more capability than producing it. |

Two model calls minimum per turn: one to route, one to answer. Tool use adds one
call per round trip.

---

## 2. Memory — four kinds, three stores

The names come from cognitive science and they are worth keeping straight,
because they have different lifetimes and different failure modes.

```
┌─ WORKING MEMORY ────────────────────────────────────────────┐
│  The context window. Assembled fresh, discarded at turn end. │
│  Not stored anywhere. This is RAM, not disk.                 │
└──────────────────────────────────────────────────────────────┘
          ▲              ▲                  ▲
          │              │                  │
  ┌───────┴──────┐ ┌─────┴────────┐ ┌───────┴────────┐
  │ PROCEDURAL   │ │ SEMANTIC     │ │ EPISODIC       │
  │ how to act   │ │ what is true │ │ what happened  │
  │              │ │              │ │                │
  │ PERSONA.md   │ │ facts table  │ │ chat_log table │
  │ (+ SKILL.md, │ │ FTS5 keyword │ │ ordered by id  │
  │  layer 14)   │ │ top-k = 3    │ │ last N turns   │
  └──────────────┘ └──────────────┘ └────────────────┘
       files           .ninja/state.db (SQLite)
```

### Procedural — *how to act*
**Lives in:** `personas/<name>/PERSONA.md` — markdown with YAML frontmatter.
**Read:** every turn, by `personas.load()`.
**Written:** by hand today. By the agent itself from layer 14 (`SKILL.md`).

```yaml
---
name: assistant
description: The default. Use for anything about this project...   # ← the router reads THIS
tools: [list_files, read_file, remember]                            # ← the allowlist
model: claude-haiku-4-5
---
You are a helpful assistant with read access to this project's files.
```

The `description` is a tool description in disguise — it says *when to use this
persona*, not what it is, because the router chooses on it.

### Semantic — *what is durably true about you*
**Lives in:** `facts` table in `.ninja/state.db`.
**Today:** an FTS5 virtual table — the index *is* the storage, so there are no
triggers to keep in sync. Porter stemming, so "deployment" matches "deploy".
**Read:** only when the gate says so (see §3).
**Written:** by the `remember` tool, during a turn, when the model judges
something durably true.

```sql
CREATE VIRTUAL TABLE facts USING fts5(
    content,
    source UNINDEXED, trace_id UNINDEXED, created_at UNINDEXED,
    tokenize = 'porter unicode61'
);
```

**No embeddings, deliberately.** See §8 and §10 — this matches the reference
architecture, and there is measured evidence for it.

### Episodic — *what was said, in order*
**Lives in:** `chat_log` table.
**Read:** `episodic.recall(thread)` — recent turns for the active thread.
**Written:** after every turn.

Tool calls are *not* stored here. They live in the trace, because replaying a
`tool_use` without its result is invalid, and yesterday's file contents are
stale anyway.

### Working memory — *this turn's context*
Assembled in `agent.build_system()`. Never persisted. Its size is the real
constraint you are managing: everything above is about deciding what earns a
place in it.

---

## 3. The retrieval gate — the most interesting 20 lines

Most turns need no facts at all. Retrieving anyway costs tokens *and* pushes
irrelevant context at the model, which makes answers worse, not merely slower.

So `semantic.gate()` decides, and **records its decision either way**:

```python
if count() == 0:      return False, "nothing remembered yet", []
hits = search(text)
if not hits:          return False, "no fact matched", []
return True, f"{len(hits)} fact(s) matched", hits
```

Two design choices worth understanding:

**The search runs before the decision.** With a local index that costs
microseconds, so what the gate protects is *context*, not latency. Backed by a
vector API the order would have to flip.

**A skip is a recorded event, not an absence.** `trace.gate(...)` writes it
down. An absence is invisible; a decision can be audited. This is the same
instinct as recording an explicit persona override rather than silently
skipping the router.

---

## 4. Orchestration — personas, threads, routing

### Personas are not prompts
A persona bundles four things: instructions (procedural memory), a **tool
allowlist**, a model, and a `description` the router reads. That bundle is the
unit of specialisation — "psychologist", "health coach", "researcher" are each
a `PERSONA.md`.

### Threads: one chat, several conversations underneath
Each persona keeps its own transcript. Interrupting a coaching session to make
a note and coming back works because the coach's thread was never touched.

```
you ──► router ──► assistant         "make a note about the call"
                   interview-coach   "so why would you pick that?"
```

### The router is sticky by construction
Most turns continue what you were already doing, and a follow-up like *"why
would you pick that?"* contains nothing naming coaching. A router without
stickiness misfiles constantly; one with it only has to notice genuine changes
of subject.

**The router never fails a turn.** If the API call raises, or the reply is
truncated, or the name is unrecognised — it stays put and records why.

### Delegation (layer 8b, not built)
`delegate` becomes a *tool*, so a sub-agent is just another loop started from
inside a loop. Two guardrails are already specified: a depth cap, and the
**subset rule** — a child's allowlist must be a subset of its parent's. A child
may never hold more privilege than its caller.

---

## 5. The control plane — what stops it

Every one of these is live in the cockpit's guardrail panel, read from the
running code rather than a copy.

| Guardrail | Value | Stops |
|---|---|---|
| Step cap | `MAX_STEPS = 6` | One loop spinning forever |
| Path boundary | project root | Tools reading outside the project |
| Hidden paths | refuse any segment starting with `.` | Reading `.env` into the transcript |
| Retrieval gate | top-3, stopword-filtered | Paying context tokens on turns needing no facts |
| Tool allowlist | `persona.tools`, enforced in `tools.run()` | The interview coach calling `list_files` |
| Depth cap *(L8)* | planned | A → B → C → A delegation chains |
| Subset rule *(L8)* | planned | A child holding more privilege than its caller |
| Guarded set *(L13)* | planned | The system editing its own enforcement |

### The allowlist is enforced once, at the gate
```python
if isinstance(allowed, str) or name not in allowed:
    raise ValueError(f"{name} is not in this persona's allowlist")
```
Filtering the schemas the model *sees* is advisory — a model that saw a tool
name earlier in the conversation can still emit it. So the check is at dispatch,
before any branch touches its arguments. The `isinstance(allowed, str)` guard
catches an allowlist flattened into one string, which would otherwise grant
every tool whose name appears anywhere in it.

---

## 6. LLMOps — the outer loop

The inner loop answers your message. The **outer loop improves the system that
answers it.** This is the half that makes it engineering rather than prompting.

```
  reply ──► TRACE ──► EVAL ──────► GATE ──passed──► RELEASE ──┐
            (L3 ✅)   "was it        │                        │
                       good?"        │                        │
              │       LLM-as-judge   │ failed                 │
              │       (L11)          ▼                        │
              ├──► OBSERVE      fix, re-run,                  │
              │    tokens,      re-trace, re-eval             │
              │    latency,          │                        │
              │    errors ◄──────────┘                        │
              │                                                │
              └──► DIAGNOSE "where/why did it break?"          │
                                                               ▼
                             improved system prompt + config ──┘
                             (new prompt version, model config,
                              tool change, retrieval top-k)
```

### Trace — built (layer 3)
One row per turn in `traces`. A turn is everything between your message and the
reply, however many model and tool calls that took.

```sql
CREATE TABLE traces (
    id, started_at, duration_ms, user_input, reply,
    model_calls, input_tokens, output_tokens, cost_usd,
    events TEXT NOT NULL   -- JSON array, in the order they happened
);
```

`events` holds every decision: `route`, `gate`, `model`, `tool`. That array is
what makes a turn reconstructable after the fact.

**Cost is computed locally**, from a `PRICING` table, not reported by the API —
so an unknown model shows as zero and is *flagged as unpriced*, rather than
silently lying that it was free.

### Eval, diagnose, release — layer 11, not built
Two kinds of test, side by side: deterministic assertions, and LLM-as-judge for
things you cannot assert on. The release gate is what turns "it seems better"
into "it passed".

---

## 7. Self-improvement — how the system grows

This is the part you described as the point of the whole thing: *the agent can
request a new skill, tool, or agent; you approve; it designs; you approve the
PR.*

The architecture splits it into two stages, and the split is the safety
property.

### Stage 1 — proposals without power (layer 9)
The architect persona can **write a proposal and nothing else.** It has no write
tools. Its output is a structured document, not a change.

### Stage 2 — the pipeline (layers 13–15)
```
  proposal ──► YOU APPROVE ──► design ──► build ──► PR ──► YOU APPROVE ──► merge
                                            │
                                   runs inside the guarded set
```

**The guarded set** (layer 13) is an allowlist over roots: everything under
these paths is guarded, *except* these specific extension points. The system may
add a new tool at an extension point; it may not edit the code that enforces the
boundary. Roles are split so that the thing proposing a change is never the
thing approving it.

**Status: none of this is built.** It is fully specified in `ARCHITECTURE.md`
§"Self-extension", which is worth reading in full — it is the most carefully
reasoned part of the design.

---

## 8. Where this stands against waku-agent

Your reference architecture. Parity is good, and the gaps are all in the outer
loop.

| Component | waku | ninja | Status |
|---|---|---|---|
| Gateway | Telegram / WhatsApp / Slack | CLI + FastAPI cockpit | ⚠️ different surface, same role |
| Working memory | assembled per run | assembled per run | ✅ match |
| Loop + tools | tool calls, `max_iterations = 10` | same shape, `MAX_STEPS = 6` | ✅ same design, different cap |
| Retrieval gate | **small-model call** decides whether to retrieve *and* writes the search query; fails open | keyword match, skips when nothing matches | ⚠️ **differs** — see below |
| Procedural | `~/.jarvis/skills/*/SKILL.md` | `personas/*/PERSONA.md` | ⚠️ partial — no SKILL.md yet (L14) |
| **Semantic** | **FTS5 keyword top-k by default; pluggable** (`WAKU_SEMANTIC_STORE`: supabase/pgvector, mem0, langmem, zep) | FTS5 keyword top-k, one backend | ✅ default matches · ❌ no pluggable backends |
| Episodic | dated events + chat history | `chat_log` | ✅ match |
| Storage | `state.db` (SQLite + FTS5) | `.ninja/state.db` (SQLite + FTS5) | ✅ match |
| Consolidation | after N chats → distill to facts | — | ❌ layer 10 |
| Sub-agents | `delegate_task` | — | ❌ layer 8b |
| Tools | ~17 modules (calendar, GitHub, MCP, search, notes…) | 3 (`list_files`, `read_file`, `remember`) | ❌ far smaller |
| Size | ~28,600 lines of Python | ~1,400 | ⚠️ ~20× — waku is a product, ninja a study copy |
| Cron | scheduled runs | — | ❌ not planned yet |
| Trace | 1 per run | 1 per run | ✅ match |
| Eval / judge | LLM-as-judge → scores | — | ❌ layer 11 |
| Observe | tokens, latency, errors | tokens, cost, duration | ⚠️ partial |
| Diagnose → Gate → Release | full loop | — | ❌ layer 11 |

**The semantic row matches on the default, not on the whole design.** Waku's
default is FTS5 keyword search with no embedding, and so is `main`'s. But waku
treats that as "a boring default and a documented upgrade": pgvector and three
third-party memory services plug in behind the same interface. The parked vector
branch was therefore not a departure from waku's design — it was the upgrade
path, built before the base was finished. See §10 and
`docs/superpowers/specs/2026-09-21-layer-5-vectors-PARKED.md`.

**The gate differs, and it is the more important difference.** Waku's gate is a
small-model call (`waku/memory/retrieval_gate.py`) that answers "does this
message need the user's memory?" and writes the search query itself. It fails
*open*: if the gate errors, it retrieves anyway, on the reasoning that a stale
memory beats a lost one. Ninja's gate is a keyword match that runs the search
first and skips when nothing matches. Ninja's is free; waku's costs one small
call per turn but can turn "when am I meeting Alex?" into a good query.

### The real gap
Ninja's **inner loop is close to complete**. Its **outer loop does not exist**:
no consolidation, no eval, no judge, no release gate. That is where the next
work belongs, and it maps exactly to layers 10 and 11.

---

## 9. Migration path — LangGraph, LangSmith, LangChain

Layer 12 is already "LangGraph port" for a reason: **build it by hand once, then
port it.** You cannot judge what a framework does for you until you have done
it yourself. That is the whole pedagogy here, and it is correct.

### What maps to what

| Hand-rolled today | LangGraph / LangSmith equivalent | What you gain | What you lose |
|---|---|---|---|
| `agent.run_turn()` while-loop | `StateGraph` + conditional edges | Retries, branching, resumability | A 40-line loop you can read in one sitting |
| `MAX_STEPS = 6` | recursion limit | Same thing, declared | Nothing |
| `messages` list | `MessagesState` / checkpointer | Persistence, time-travel debugging | Direct control of what's in the window |
| `router.route()` | a routing node | Graph-native, visualisable | Nothing |
| `personas/*.md` | per-node config / subgraphs | Composability | File-as-unit simplicity |
| `episodic.py` | `SqliteSaver` checkpointer | Free thread persistence | Your own schema |
| `semantic.py` + FTS5 | a retriever | Swappable backends | The gate, unless you rebuild it |
| `trace.py` | **LangSmith** | Real UI, diffing, dataset building | Local-only, zero-dependency traces |
| layer 11 evals | LangSmith datasets + evaluators | LLM-as-judge out of the box | — |

### Honest recommendation

**Adopt LangSmith early. Adopt LangGraph late.**

- **LangSmith** replaces something you have *not* built (eval, judge, diagnose,
  release gate) and would take layers 11's worth of work to build badly. It is
  additive — you can point it at the current code without restructuring
  anything.
- **LangGraph** replaces something you *have* built and understand. Porting now
  costs you the thing you are here to learn. Port at layer 12 as planned, once
  the hand-rolled version is feature-complete — then the diff *is* the lesson.
- **LangChain** (the base library) — take it piecemeal or not at all. Its
  abstractions are the ones most likely to obscure the mechanics you want to
  see.

---

## 10. Evidence from the parked vector work

Worth keeping, because it explains *why* the keyword design is defensible rather
than merely simpler.

**Embeddings rank well.** Top-1 correct 5/5 with a bi-encoder, 6/6 with a
cross-encoder reranker — including cases FTS5 provably cannot reach, like *"tell
me about my testing background"* matching a QA fact that shares no words with it.

**Embeddings cannot gate.** No cosine floor separates relevant from irrelevant
queries, at any model size:

| Model | dim | lowest RELEVANT | highest IRRELEVANT | separation |
|---|---|---|---|---|
| bge-small-en-v1.5 | 384 | 0.512 | 0.651 | −0.139 |
| bge-base-en-v1.5 | 768 | 0.464 | 0.543 | −0.079 |
| gte-base | 768 | 0.749 | 0.776 | −0.027 |
| mxbai-embed-large-v1 | 1024 | 0.412 | 0.519 | −0.108 |

Never positive. Margin and z-score separate *worse*. Cross-encoders rank better
and gate no better.

**Conclusion: ranking is cheap and solvable; judging relevance is not a
similarity problem.** Which is a good argument for cheap keyword retrieval plus
an LLM judgement — and that is what waku's gate does: a small model decides
whether to retrieve, and keyword search does the retrieving.

**A second finding, backend-independent:** a fact-to-fact dedup threshold at
0.85 silently destroys distinct facts — *"standup at 9am"* vs *"standup at
10am"* score **0.947**. The only safe window measured was 0.947–0.981. If dedup
is ever built, on any backend, it needs that calibration or it will quietly eat
your data.

---

## 11. Where to look in the code

| You want to understand… | Read |
|---|---|
| The whole turn | `ninja/agent.py` (198 lines) |
| Which conversation a message goes to | `ninja/router.py` |
| What the agent can do | `ninja/tools.py` |
| What it remembers, and when | `ninja/semantic.py`, `ninja/episodic.py` |
| What a run cost and why | `ninja/trace.py` |
| How specialisation works | `ninja/personas.py`, `personas/*/PERSONA.md` |
| The schema | `sql/schema.sql`, `sql/migrations/` |
| The dashboard | `ninja/server.py`, `ui/` |
| The reasoning behind all of it | `ARCHITECTURE.md` |

The whole system is ~1,400 lines. That is deliberate — it is small enough to
hold in your head, which is the only way to learn what each piece is for.
