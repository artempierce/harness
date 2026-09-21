# Ninja — the plan

**This is the one document.** What Ninja is, what is built, how it works today,
what comes next, and why it is the way it is. If it disagrees with the code, the
code wins — fix this file.

*Last updated with PR #16 (delegation) open. Everything older that described the
architecture or the plan has been folded in here and removed; it stays in git
history (`git log --diff-filter=D --name-only -- ARCHITECTURE.md docs/`).*

---

## 1. What Ninja is

A personal assistant built as a **cast of agents**, from scratch, one layer at a
time, to learn how an agent harness actually works. The goal is that you can
build systems like this yourself, so every piece is small enough to read.

What it is meant to become: something you can talk to about anything, that
remembers you, that takes different roles (coach, researcher, psychologist…) each
with its own skills, that shows you everything it knows and does, and that can
*propose* improvements to itself which you approve.

The reference design is [waku-agent](https://github.com/ShenSeanChen/waku-agent).

### The one idea

> **The model remembers nothing.** Working memory is rebuilt from storage on every
> turn and thrown away. "The agent remembers I'm a QA engineer" is never true of
> the model — it is true of the assembly step that put that fact in the prompt.

Everything else (three memory stores, a gate, a router, consolidation) exists to
answer one question well: *what should be in the context window this time?*

---

## 2. Status

| Layer | What | State |
|---|---|---|
| 1–2 | Bare agent run · loop, tools, stop condition | ✅ |
| 3 | Tracing (one row per turn, every decision as an event) | ✅ |
| 4 | Episodic memory (`chat_log`) | ✅ |
| 5 | Semantic memory + retrieval gate (FTS5 keyword) | ✅ |
| 6 | Dashboard (the cockpit) | ✅ |
| 7 | Personas + tool allowlists | ✅ |
| 8a | Threads + routing | ✅ |
| **8b** | **Delegation** | **PR #16 open, CI green** |
| 9 | The architect — proposals, no write access | not started |
| 10 | Consolidation | ✅ |
| 11 | Eval, diagnose, release | not started |
| 12 | LangGraph + LangSmith port | not started |
| 13–15 | Registry + guarded set · tool authoring · build pipeline | not started |

Also built, beyond the original layers:

- **Learned rules** — the agent saves standing preferences per persona (`add_rule`).
- **`MEMORY.md`** — a readable mirror of everything it knows.
- **Agent hardening** — non-string tool arguments refused, `read_file` output capped, the REPL recovers from errors and saves atomically.
- Migrations, coverage gate (89%), mutation testing.

**Parked, not merged:** an embeddings rewrite of layer 5 (findings in §7).
**Not started, by design:** skills (§4), everything in §5.

---

## 3. How it works today

### 3.1 One message, end to end

```
you type
  │
  ▼  ROUTE          router.py            MODEL CALL 1 (haiku): which conversation?
  │                                      sticky; never fails a turn
  ▼  LOAD PERSONA   personas.py          personas/<name>/PERSONA.md → prompt, tools, model
  │
  ▼  GATE           semantic.py          keyword search over facts; a skip is RECORDED
  │
  ▼  ASSEMBLE       agent.py             system prompt =
  │                                        persona instructions
  │                                      + learned rules (.ninja/rules/<persona>.md)
  │                                      + who it may delegate to (if it holds `delegate`)
  │                                      + retrieved facts (if the gate said yes)
  │                                      messages = recent turns of this thread + your message
  ▼  LOOP           agent.py::run_turn   MODEL CALL 2..N (persona's own model)
  │     model ⇄ tool ─ ─ ─ ─ delegate ─► a child loop: fresh context, read-only,
  │     stop at MAX_STEPS = 6                depth+1, its own model
  ▼  CONSOLIDATE    consolidation.py     every 6 exchanges in a thread: small model
  │                                      distils facts + a dated episode. Inside the
  │                                      turn, so its cost is in this turn's receipt
  ▼  FINISH         trace.py             one row: tokens, cost, every event
  │
  ▼  SAVE           episodic.py          chat_log ← your message and the reply
  ▼  MIRROR         mirror.py            regenerate .ninja/MEMORY.md
  │
  ▼  reply
```

Two model calls minimum per turn (route + answer). Each tool round trip adds one.
Consolidation adds one small call every sixth exchange; a delegation adds a whole
child loop.

### 3.2 Memory — four kinds

| Kind | Meaning | Lives in | Read | Written |
|---|---|---|---|---|
| **Working** | this turn's context | nowhere — rebuilt each turn | — | — |
| **Procedural** | how to act | `personas/*/PERSONA.md` (reviewed, in git) + `.ninja/rules/<persona>.md` (agent-written, not in git) | every turn | rules: by `add_rule`; personas: by hand |
| **Semantic** | what is durably true | `facts` table, FTS5 keyword index, `source` = told / distilled | top 3, only if the gate says so | `remember` tool; consolidation |
| **Episodic** | what happened | `chat_log` (raw, replayed as history) and `episodes` (dated one-line summaries) | log: recent turns. Episodes: **not yet retrieved** | log: every turn. Episodes: consolidation |

`.ninja/MEMORY.md` shows facts, episodes and every persona's rules in one file.
It is one-way (edits are overwritten), written atomically, and shows
`could not read: <reason>` for a section it cannot read rather than going stale.
It exists because rules and distilled facts are text the agent writes into its own
future prompts — the mitigation for that risk is being able to *see* it.

### 3.3 The control plane

| Guardrail | Value | Stops |
|---|---|---|
| Step cap | `MAX_STEPS = 6` per loop | one loop spinning forever |
| Delegation depth | `MAX_DEPTH = 2` | A → B → C → A chains |
| Delegations per turn | 3 | an unbounded fan-out (steps × depth alone allow ~216 calls) |
| Turn budget | `$0.25`, checked before each call at every depth | runaway spend. **A guess, not measured** |
| Tool allowlist | `persona.tools`, enforced at dispatch in `tools.run` | a persona calling a tool it was not given, even by name |
| Subset rule | a child's effective tools ⊆ its parent's; refused, never trimmed | delegating to more privilege than the caller holds |
| Read-only children | `remember`, `add_rule` stripped; `delegate` stripped at max depth | a hijacked orchestrator writing through another persona |
| Unpriced models | delegation refused if either persona's model has no price | the budget reading unknown spend as free |
| Path boundary / hidden paths | project root; refuse any segment starting with `.` | reading `.env` into the transcript |
| Input caps | `read_file` output; rules 200 chars each, 2000 per file; consolidation 10 facts × 300 chars per batch | what one bad response can cost |
| Failure behaviour | router, consolidation and the mirror never fail a turn; failures are recorded | a helper breaking the chat |

The allowlist is enforced **once, at dispatch, first** — before any branch reads
its arguments. Filtering the schemas the model *sees* is advisory: a model that saw
a tool name earlier can still emit it.

### 3.4 Data

One SQLite file, `.ninja/state.db`, changed only by numbered migrations in
`sql/migrations/`: `traces`, `chat_log` (with `thread`, `consolidated`), `facts`
(FTS5), `episodes`. Plus `.ninja/rules/*.md` and `.ninja/MEMORY.md`. All of `.ninja/`
is gitignored — it is your data, not the repo's.

### 3.5 Where to look

| To understand… | Read |
|---|---|
| the turn and delegation | `ninja/agent.py` |
| which conversation a message goes to | `ninja/router.py` |
| what the agent can do | `ninja/tools.py` |
| memory | `semantic.py`, `episodic.py`, `consolidation.py`, `rules.py`, `mirror.py` |
| cost and events | `ninja/trace.py` |
| specialisation | `ninja/personas.py`, `personas/*/PERSONA.md` |
| the dashboard | `ninja/server.py`, `ui/index.html` |
| how tests are written | `docs/TESTING.md` |

---

## 4. The roadmap

In order. Each item is its own branch and PR.

| # | Step | Why here |
|---|---|---|
| 1 | **Merge #16, then try it for real.** A few minutes on the live API: facts appear after 6 exchanges, `add_rule` changes a prompt, `MEMORY.md` shows both, the assistant delegates. Spends a few real tokens — ask first | Nothing built so far has met a real conversation. Tests cannot say whether distilled facts are any good, or whether `$0.25` is the right number |
| 2 | **Skills.** `SKILL.md` files matched to each message and injected into the prompt; `create_skill` where the agent proposes and **you approve** before anything is written | Your "different sets of skills". A different axis from personas: a persona is *who is speaking* (chosen once per turn); a skill is *what knowledge applies* (several can apply at once). Needs a spec first |
| 3 | **Gate upgrade + episode retrieval** (deferred until step 1 shows whether retrieval is actually weak). A small model reads the message and writes the search query, failing open; episodes become searchable | Waku does this. Costs one small call per turn |
| 4 | **Layer 11 — eval, judge, release gate**, pointing LangSmith at the current code | You cannot judge whether a change helped without it. Comes before self-extension so a proposer is never built before an evaluator |
| 5 | **Layer 12 — LangGraph port** | Late on purpose, once the hand-written version is complete: then the diff *is* the lesson |
| 6 | **Layers 9, 13–15 — self-extension** (§5) | Highest risk, most specified, last |

Also cheap, and mostly content rather than code: new personas (health coach,
psychologist, researcher). A persona is a `PERSONA.md`; the design work is in the
prompts and the tool grants, and any new tool grant is a privilege decision (§5).

### The framework question

**LangSmith early, LangGraph late.** LangSmith replaces something not yet built
(eval, judge, diagnose) and is additive — point it at the current code without
restructuring. LangGraph replaces a loop you already understand and would cost you
the thing you are here to learn if done early. LangChain's base library: piecemeal
or not at all, since its abstractions are the likeliest to hide the mechanics.

---

## 5. Self-extension (designed, not built)

The agent can *propose* changes to itself — new tools, personas, skills. It
cannot apply them. This comes in two stages, far apart in risk; thinking about
improving yourself and being able to modify yourself are different capabilities,
and collapsing them is how this goes wrong.

**Stage 1 — proposals without power (layer 9).** An architect persona holding only
`read_file` and `list_files`. It reads the repo and writes a proposal; it cannot
write, run, branch or delegate to anything that can. You carry the proposal to a
separate session and implement it by hand. **The human carrying it is an air gap:**
no automated path crosses it, so no guard list or sandbox is needed. It also earns
the evidence stage 2 depends on — run it for months and learn whether its proposals
are any good before building the dangerous part. A proposal is model-written text:
treat it as data to evaluate, never as a prompt to run.

**Stage 2 — the pipeline (layers 13–15).** The architect gains the ability to
`delegate` to implementers that hold write access, inside machinery that exists to
make that safe. Stage 2 exists only to remove the air gap, and buys only
convenience — pay for it once the proposals have proven worth automating.
**The persona is data; the privilege belongs to the system.**

```
you ─► ARCHITECT (spec, no code) ─► GATE 1: approve the shape, before tokens are spent
        ─► CODER (branch + worktree; holds run_command → the security perimeter)
        ◄─► TESTER (writes eval cases first, cannot write ninja/; ≤3 rounds)
        ─► CRITIC (reads only the diff, fresh context; a rejection BLOCKS)
        ─► GATE 2: a pull request with spec, diff, evals, critique, cost delta ─► merge
```

Every arrow is a `delegate` call — layer 8b's machinery pointed at the repo.

The parts that matter:

- **An extension point, separate from the enforcer.** `ninja/registry.py` (guarded,
  never written by an agent) scans `ninja/tools/` and `personas/` and `evals/cases/`
  at startup. A new capability is a new file. Dropping a file into `ninja/tools/`
  is code execution at next start — accepted deliberately, which is why that
  directory needs a PR.
- **The guarded set is an allowlist, not a denylist.** Everything under `ninja/`,
  `sql/`, `.github/`, `.git/`, `pyproject.toml`, `uv.lock` is guarded *except* the
  extension points. A denylist fails open and rots as files move.
- **Three tiers, cut by privilege, not file type.** *Instructions* (persona prose,
  `tools:` unchanged) → confirmation in chat. *Privilege* (any new or widened
  `tools:`, any `model:` change, any new tool) → a reviewed PR. *Enforcement* (the
  loop, caps, registry, guard) → a human writes it. `tools:` in frontmatter is a
  capability grant; `model:` is a cost grant.
- **Delegation de-escalates** (built in #16) and **the tool universe is bounded by
  a human**: the union of all allowlists cannot exceed a set in a guarded file.
- **The guard cannot restrict a shell.** `run_command` defeats any path check
  (`sed -i`, `python -c`, `git checkout … -- ninja/agent.py`). The real boundary
  must be OS-level — a container, a separate user, a read-only mount — and until it
  exists the coder persona is *trusted* and the PR is the only thing between it and
  `main`. Unresolved.
- **Evals are data, not Python.** A tester that can write `conftest.py` can disable
  the guard before any test runs. Cases are YAML; the runner is guarded.
- **Red-then-green, checked by git.** The eval commit must be an ancestor of the
  implementation, and CI asserts the new cases *fail* there.
- **The pipeline is a resumable state machine** (`proposals` table: `spec` →
  `approved` → `building` → `testing` → `review` → `pr` → `merged`/`abandoned`), one
  open proposal at a time, with a cost ceiling that *aborts* rather than reports.
- **Gates are itemized, not binary.** A proposal is a list of decisions — new tool,
  privilege grant, dependency, tests, permanent token cost — each accepted, declined
  or changed with a reason that becomes the next round's brief. Padding the list with
  tasks is how it becomes fatigue.
- **Gate 2 must be rare and high-information.** The likeliest failure is not a clever
  escape but **approval fatigue**: the gate works on turns 1–20 and by turn 40 the
  diffs are skimmed. A real PR with everything in one place, not a stream of
  confirmations.
- **Rollback is incomplete.** `git revert` reverts code, not effects — rows a tool
  wrote, a migration it ran. And a restart ends the conversation that asked for the
  capability.

**What this is not:** autonomy. Nothing merges without a human and nothing reaches
enforcement at all. The reason to build it this way is inspectability: a system that
proposes specs, diffs, failing-then-passing tests and critiques is one whose reasoning
you can read. An agent that quietly edits itself is one you have to trust.

---

## 6. Reference designs

### waku-agent, compared from its source

| | waku | ninja |
|---|---|---|
| Loop | ~114 lines, `max_iterations = 10`, observer callbacks | same algorithm, `MAX_STEPS = 6` |
| Semantic memory | FTS5 keyword by default; **pluggable** (pgvector, mem0, zep, langmem) | FTS5 keyword only |
| Retrieval gate | **a small-model call that writes the search query**, fails open | keyword match, skips when nothing matches |
| Episodic | raw `chat_log` **plus** `episodes` (dated summaries), both searched | both exist; episodes not yet searched |
| Skills | `SKILL.md`, keyword-matched **per message**, agent can create them | not built (§4 step 2) |
| Persona | one `SOUL.md` the agent can append to | per-persona `PERSONA.md` + agent-written rules file |
| Consolidation | every N chats, small model → facts + episode, loss-safe | built (layer 10) |
| Self-modification | agent writes `SOUL.md` / `SKILL.md` directly; consent is a sentence in a tool description | designed to be far stricter (§5) |
| Sub-agents | `delegate_task` | built (#16), read-only, capped |
| LLMOps | trace, judge, release gate, arena | trace only |
| Size | ~28,600 lines | ~2,000 |

Waku treats FTS5 as "a boring default and a documented upgrade". The gap that
matters is the outer loop: no eval, judge or release gate yet.

### Hermes Agent (Nous Research)

The same shape at product scale. Worth reading when layer 13 arrives: it runs tool
execution across five sandbox backends (local, Docker, SSH, Singularity, Modal) —
the answer to "where does `run_command` run". It generates skills automatically;
here they pass a gate, because the privilege grant inside a persona should be a thing
you see.

---

## 7. Decisions and findings — why it is the way it is

**Memory**
- **Rules live outside git** (`.ninja/rules/`), not in `PERSONA.md`. A persona change
  needs approval and a commit; a rule is the user's data, like a fact. *Risk accepted:*
  anything the agent writes into its own prompt is a persistent prompt-injection
  surface. Bounded by size caps, one line per rule, visible in the trace and in `MEMORY.md`.
- **Consolidation runs inside the turn, before the trace closes**, so its spend is in
  that turn's receipt rather than invisible. It writes facts, episode and flags in one
  transaction. It shows the model existing facts and asks it not to repeat them — dedup
  without embeddings.
- **A failing consolidation backs off.** Retrying on every turn re-sent and re-paid for
  the same batch forever (5 turns = 5 calls, reproduced). A failed thread now waits for
  6 more exchanges; one bad thread cannot starve the others. Held in memory, so a restart
  earns one fresh retry.
- **Summarisers can be steered.** Consolidation reads assistant replies, which can echo
  text from files the agent read. Bounded, not prevented. Same class of risk as rules.

**Delegation (#16)**
- Children are **read-only**, get a **fresh context** and **no retrieved facts** — the
  brief is the whole interface. Subset violations are **refused, not trimmed**.
- **`$0.25` is a guess**: ~3 delegations × 6 steps × ~6k input tokens at haiku pricing ≈
  $0.11 plus output. Replace it with a measured number.
- **Fail closed on price.** With an unpriced model a 400,000-token call cost $0.00 and the
  ceiling never tripped. Delegation is now refused unless both models are priced.
- Persona frontmatter must be typed (`model` a string, `tools` a list of strings): layer 9
  will have the agent writing these files, so every wrong shape must arrive as a ValueError.

**The parked embeddings branch — what it proved**
- Embeddings **rank** well: 5/5 top-1 with a bi-encoder, 6/6 with a cross-encoder,
  including a vocabulary-mismatch case FTS5 cannot reach.
- Embeddings **cannot gate.** No cosine floor separates relevant from irrelevant queries at
  any model size:

  | Model | dim | lowest relevant | highest irrelevant | separation |
  |---|---|---|---|---|
  | bge-small-en-v1.5 | 384 | 0.512 | 0.651 | −0.139 |
  | bge-base-en-v1.5 | 768 | 0.464 | 0.543 | −0.079 |
  | gte-base | 768 | 0.749 | 0.776 | −0.027 |
  | mxbai-embed-large-v1 | 1024 | 0.412 | 0.519 | −0.108 |

  Margin and z-score separate worse; cross-encoders rank better and gate no better.
  Ranking is cheap; judging relevance is not a similarity problem — which is why waku pairs
  keyword retrieval with a small-model judgement.
- **A dedup threshold destroys data.** At 0.85 it merged "standup at 9am" with "standup at
  10am" (0.947), "dark mode" with "light mode" (0.931). The only safe window measured was
  0.947–0.981. If dedup is ever built, on any backend, it needs that calibration.
- The vector branch (`layer-5-vectors`) was never pushed. Revisit only if step 4's evals
  can say whether embeddings help.

**Process lessons that changed the code**
- Tests must fail against the bug they target. Several tests written into specs passed
  against the exact bug they were meant to catch; see `docs/TESTING.md`.
- A migration's `migrate()` strips only whole comment lines, then splits on `;` — a
  semicolon in an inline comment cut a `CREATE TABLE` in half.
- A new trace event type needs a dashboard branch or it renders as `undefined(undefined)`.
- A name check meant for writing a file was reused for reading it, and crashed every turn
  for a persona called `QA_Coach`. Validate on the write path; reading must not raise.

---

## 8. How we work

- **Nothing goes to `main` directly:** branch → PR → CI gate → your approval → merge.
- **A short spec you approve comes before code.** It would have caught the embeddings detour.
- **One implementer, one review per PR** for ordinary work. **Privilege and loop changes
  get a second, adversarial review** whose only job is to break the rules with runnable
  scripts (delegation did; layers 9 and 13–15 will).
- **Verify, don't trust the report.** Re-run the reviewer's own scripts against the fix.
- **Anything that calls the real API costs money — ask first.** Tests use a stub client.
- Parallel streams get their own git worktree. Commit subjects are plain statements of
  what changed; no attribution lines.
- Gate before pushing: `uv run pytest -q`, `uv run ruff check .`, `uv run pip-audit --skip-editable`.

---

## 9. Open questions

- **Are facts global or per persona?** Today global. The coach probably should not read
  the psychologist's notes. Undecided.
- **Should skills and personas coexist as separate axes, or should one absorb the other?**
  Leaning coexist (§4 step 2).
- **Retrieving episodes** — needs the gate work in step 3, and searching two tables with
  incomparable scores changes the gate's skip semantics.
- **Where does `run_command` actually run?** Unresolved; it bounds how much of stage 2 can
  run unattended.
- **Parallel delegation.** Several `delegate` calls in one reply already run sequentially and
  are capped; true parallelism is not scheduled.
- **Capture from a phone** (Telegram or an app) — a gateway, not a new subsystem; the work is
  an authenticated API in front of the loop. Not scheduled.
- **Who writes the decision log** when the system extends itself: generated in the turn, or
  reconstructed from the trace afterwards? An agent narrating its reasoning is not reporting it.
- **`trace.PRICING` is hand-maintained.** A new model id silently blocks delegation until priced.
  Fine while it is three models; worth revisiting if it grows.
