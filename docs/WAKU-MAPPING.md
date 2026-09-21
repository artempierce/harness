# Waku → Ninja: a study guide

Written from reading waku-agent's source (`761c420`), not from its diagram.
**Read:** `waku/loop/agent.py`, `waku/app.py` (turn assembly),
`waku/memory/__init__.py`, `retrieval_gate.py`, `consolidation.py`,
`semantic/store.py`. **Not read:** `waku/graph/`, `waku/ops/`, `waku/gateway/`,
`session.build_system`, the eval suite. Nothing below claims to cover those.

---

## 0. The whole project, in one screen

```
waku-agent/                         ~28,600 lines of Python
├── waku/
│   ├── loop/         agent.py (114)        THE loop. models.py (469): provider adapters
│   ├── runtime/      session.py (127)      builds the system prompt each turn
│   ├── memory/       the three pillars + gate + consolidation   (~1,700)
│   ├── tools/        ~17 modules: calendar, github, MCP, search, notes,
│   │                 memory_admin (update_soul, create_skill, manage_memory)
│   ├── gateway/      cli, telegram, whatsapp, discord, voice, slack
│   ├── graph/        optional triage: quick reply vs full agent  (flag, off)
│   ├── ops/          LLMOps: tracing, judge, release_gate, arena, pricing,
│   │                 dashboard (1,209), compare_history, show_trace
│   ├── app.py (172)  the turn orchestrator — respond()
│   ├── config.py     every knob, all WAKU_* env vars
│   └── db.py         SQLite schema
├── skills/           SKILL.md files that ship with it (weekly-brief, schedule-meeting…)
├── evals/            deterministic tests + dataset.jsonl + memory_arena.json
├── docs/             architecture.md, loop-vs-graph.md, evals.md, roadmap.md…
└── sql/              init_supabase.sql (pgvector upgrade path)

~/.waku/  (or ./.waku — WAKU_HOME; runtime data, gitignored)
├── SOUL.md           who the agent is. Created on first run from DEFAULT_SOUL.
├── state.db          SQLite + FTS5: facts, episodes, chat_log
├── MEMORY.md         generated, human-readable mirror of facts + episodes
├── skills/<name>/SKILL.md    skills the agent or you created
├── traces/           one trace file per turn
└── outbox/           drafted messages
```

### The system prompt, assembled (`session.build_system`)

```
SOUL.md                     ← who it is + standing rules + "Learned rules"
+ current date/time + tz    ← so "in 30 minutes" resolves
+ "your model is X"         ← the first thing curious users ask
+ "Relevant memory:"        ← only if the gate said retrieve
+ "Relevant skill instructions:"   ← SKILL.md bodies keyword-matched to the message
```

Five parts, in a fixed order, all plain text. **That is the whole mechanism** —
no hidden state, and every part is a file or a table you can open.

### How waku "improves itself" — and how thin that is

Three tools let the agent change its own behaviour:

| Tool | Does | Guard |
|---|---|---|
| `update_soul(rule)` | appends one line to `SOUL.md` under "## Learned rules" | a size cap only |
| `create_skill(name, description, body)` | writes `skills/<name>/SKILL.md`, live this session | refuses to overwrite; validates the format; **"only after the user agrees" is a sentence in the tool description, not code** |
| `manage_memory(action, kind, id)` | correct or forget a fact/episode | — |

That is a real self-improvement loop, but a *light* one: no proposal document,
no design step, no review, no PR, no guarded set. The agent writes straight into
its own prompt and skills. The consent gate lives in the model's compliance with
a tool description.

**Ninja's planned design (layers 9 and 13–15) is much stricter** — proposals
with no write access, two human approval gates, a guarded set the system cannot
edit. Those are two different points on a real spectrum, and you get to choose.

---

## 1. One turn in waku, in the order it runs

`app.py: respond()` — every step named after the code that does it.

```
respond(user_message)
 │
 ├─ tracer.turn(...)                        opens a trace
 │
 ├─ [optional] graph triage                 flag off → skipped. Flag on: a graph
 │     quick vs full path; ANY failure      picks a cheap "quick reply" or the
 │     falls open to the plain loop         full agent.                (not read)
 │
 └─ _run_full_turn()
      │
      ├─ session.build_system(message)      system prompt: soul + memory + skills
      │     └─ memory.gated_retrieve()        ← MODEL CALL (small model)
      │           should_retrieve() → {retrieve, query, reason}
      │           if yes: facts.search(query, top_k)
      │                 + episodes.search(query, 3)
      │     └─ memory.matching_skills()      keyword match → SKILL.md bodies
      │
      ├─ messages = history[-N*2:] + user   bounded window, N = history_turns
      │
      └─ run_loop(max_iterations=10)         ← MODEL CALL 2..N
            reason → tool_use? → tools.execute() → observe → repeat

 then, after the reply:
      session.add_exchange()                 → chat_log (user row + assistant row)
      memory.maybe_consolidate()             ← MODEL CALL (small), every N exchanges
      memory.export_markdown()               regenerates MEMORY.md
 tracer.end_turn()
```

**Model calls per turn: gate (small) + loop (main, 1..10) + occasionally
consolidation (small).** Ninja: router (small) + loop.

---

## 2. File-for-file

| Concept | Waku | Ninja | Fit |
|---|---|---|---|
| The loop | `loop/agent.py` (114 lines) | `agent.py::run_turn` | Same algorithm. Waku's takes an *observer* callback instead of writing to a trace directly. |
| Turn assembly | `app.py::respond`, `_run_full_turn` | `agent.py::build_system` + `cli.py`/`server.py` | Same job, waku's is split out. |
| Retrieval gate | `memory/retrieval_gate.py` — small-model call | `semantic.py::gate` — keyword match | **Different mechanism.** |
| Fact store | `memory/semantic/store.py` (FTS5) behind a `FactStore` interface | `semantic.py` (FTS5), no interface | Same default; waku has the seam. |
| Alt. backends | `supabase_store`, `mem0_store`, `zep_store`, `langmem_store` | — | Not built. |
| Episodic | `episodic/store.py` — **`episodes`**: dated one-line summaries | `episodic.py` — `chat_log` only | **Different thing.** See §3. |
| Raw chat | `chat_log` table | `chat_log` table | Same. |
| Procedural | `procedural/loader.py` — `SKILL.md`, matched **per message** | `personas.py` — `PERSONA.md`, picked by router | **Different unit.** See §3. |
| Consolidation | `memory/consolidation.py` (82 lines) | — | Layer 10. |
| Window | `history[-history_turns*2:]` | `episodic.recall(thread)` | Both bounded. |
| Trace | `tracer.turn()` / `.event` | `trace.py::Trace` | Same idea. |
| Human-readable memory | `export_markdown()` → `MEMORY.md` | — | Not built. Cheap and worth it. |

---

## 3. Three differences that change how you think

### 3a. Skills vs personas — different axes
Waku's `SKILL.md` files are **capabilities matched against the message**: the
loader keyword-matches and injects the bodies of whichever skills apply. *Many
can apply to one turn.* Ninja's `PERSONA.md` is a **whole identity chosen once
per turn by a router**: one persona, one allowlist, one model.

They are not competing designs. They answer different questions ("what
knowledge applies?" vs "who is speaking?"). Your goal — a psychologist, a
health coach, a researcher — is the persona axis. "How to run a
weekly review" is the skill axis. **Ninja has the first and lacks the second**;
layer 14 is where skills would arrive.

### 3b. Two kinds of "what happened"
Waku separates the **raw log** (`chat_log`, every message, replayed as history)
from **episodes** (one dated sentence per consolidated conversation, searched
by the gate). Ninja has only the log. So a fact from two hundred messages ago
can surface in waku through *either* a distilled fact *or* an episode summary;
in ninja only through a fact someone explicitly `remember`ed.

### 3c. The gate writes the query
`should_retrieve` returns `{"retrieve": bool, "query": "...", "reason": "..."}`.
The model doesn't just say yes or no — it **rewrites the message into search
keywords**. "when am I meeting Alex?" becomes something FTS5 can match. That is
what a keyword store needs from a model, and it is the piece that makes cheap
keyword retrieval good enough. It **fails open**: on error, it retrieves,
because "a stale memory beats a lost one." Ninja's gate fails *closed*.

---

## 4. Consolidation, exactly

```
chat_log rows WHERE consolidated = 0
   │  fewer than N exchanges (2 rows each)? → return, do nothing
   ▼
small model reads the WHOLE unconsolidated log
   → {"facts": [{"subject", "content"}], "episode": "one sentence"}
   ▼
facts.add(..., source="consolidation")     → semantic memory
episodes.add(episode, happened_at=today)   → episodic memory
UPDATE chat_log SET consolidated = 1       → so it is not distilled twice
```

Three properties worth copying:
- **Loss-safe.** Any failure returns 0 and leaves rows unconsolidated, so the
  next turn retries. The log is never the thing that gets lost.
- **Batched.** One call per N exchanges, not per message.
- **Idempotent.** The `consolidated` flag is the guard against double-writing.

Note what it does *not* do: no dedup, no contradiction handling. If the model
extracts the same fact twice, it is stored twice. The parked vector branch found
that a naive similarity-based dedup would be worse than that — see
`2026-09-21-layer-5-vectors-PARKED.md`.

---

## 5. What this implies for ninja's next layer

**Layer 10 (consolidation) is a small, well-bounded port.** The waku version is
82 lines and the shape is above. What ninja needs first:

1. `chat_log.consolidated` column — a migration (`003`; the vector branch's
   `003` is parked, so on `main` this number is free).
2. An `episodes` table — dated one-line summaries, FTS5-indexed.
3. `consolidate_if_due()` — same fail-safe shape.

**The gate is a separate, optional upgrade.** Swapping ninja's keyword gate for
waku's small-model gate is self-contained, but it adds a model call per turn.
Decide it after consolidation, when there are enough facts for retrieval
quality to be measurable.

**Do this from `main`, not from `layer-5-vectors`.**
