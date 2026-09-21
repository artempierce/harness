# Ninja — Plan v2 (for approval)

**Status: proposal. Nothing here is built. Approve, change, or reject each
numbered decision in §6 and I will start.**

**Why a new plan:** the original 15 layers were written from a whiteboard. Having
now read waku-agent's actual source (`docs/WAKU-MAPPING.md`), some layers are
already right, some are missing pieces waku has, and one (semantic vectors) was
built and parked. This plan re-orders the remaining work around what waku
proved works, keeps the parts of your design that are stricter than waku's, and
says plainly what is finished and what is not.

---

## 1. Where we are

```
DONE on main                                     NOT DONE
────────────────────────────────────────────     ─────────────────────────────────
 1  Bare agent run                                8b Delegation  ← half-designed
 2  Loop, tools, stop condition                    9 The architect (proposals)
 3  Tracing                                       10 Consolidation
 4  Episodic memory  (chat_log only)              11 Eval, diagnose, release
 5  Semantic memory  (FTS5, keyword gate)         12 LangGraph port
 6  Dashboard                                     13 Registry + guarded set
 7  Personas + tool allowlists                    14 Tool authoring
 8a Threads + routing                             15 Build pipeline
    + migrations, coverage, mutation testing
```

Parked, not merged: `layer-5-vectors` (embeddings). Findings kept in
`docs/superpowers/specs/2026-09-21-layer-5-vectors-PARKED.md`.

### What 8b is, and why it stalled
`delegate(persona, task)` is a **tool** that starts a whole new loop for another
persona, with a fresh context holding only the task brief — not the parent's
conversation — at `depth + 1`. Two guardrails come with it: a **depth cap** and
the **subset rule** (a child's tools must be a subset of its parent's).

**Its blocker is already named in the 8a spec:** `tools.run(name, args, allowed)`
has no way to know who is calling or how deep it is, and `delegate` needs both.
That is a signature change to the one function every tool goes through, so it is
small to write and wide to review. 8b was deferred for that reason, not because
it is unclear.

---

## 2. What waku has that you do not (ranked by value for you)

| # | Waku piece | Size in waku | Serves your goal |
|---|---|---|---|
| 1 | `SOUL.md` + `update_soul` tool — agent appends learned rules to its own persona | ~30 lines | "it remembers *how I want it to behave*" |
| 2 | `MEMORY.md` — readable mirror of everything it knows, regenerated each turn | ~25 lines | "a system to see all my data about myself" |
| 3 | Consolidation + `episodes` table — distil chats into facts every N exchanges | ~80 lines | "it will remember information about us" |
| 4 | `SKILL.md` matched per message, plus `create_skill` | ~90 + ~40 lines | "different sets of skills" |
| 5 | Small-model gate that **writes the search query** and fails open | ~55 lines | better retrieval from a keyword store |
| 6 | LLM-as-judge, release gate, trace viewer | ~350 lines | "LLMOps", knowing if a change helped |
| — | Graph triage, pluggable memory backends, chat gateways | large | **skip** — scale, not understanding |

Line counts are waku's, read from source; ninja's will differ.

---

## 3. What stays stricter than waku, on purpose

Waku lets the agent write straight into its own prompt and skills. Its only
consent gate on `create_skill` is *a sentence in the tool description* — the
model's compliance, not code.

Ninja's design (layers 9, 13–15) is stricter and **this plan keeps it**:
proposals with no write access, two human approvals, a guarded set the system
cannot edit. Both are legitimate points on a spectrum. The plan uses waku's
light mechanism for *low-risk, per-user data* and keeps the strict pipeline for
*code*. See Decision 1 — it is the one design question that matters.

---

## 4. The plan, in phases

Each item is its own branch and its own PR, as before.

```
 PHASE A — identity & visibility      small, visible, low risk
   A1  Learned rules      update_soul tool → per-persona rules file
   A2  MEMORY.md export   generated mirror of facts (+ episodes after B)

 PHASE B — the memory loop            completes the "it remembers me" story
   B1  Layer 10           consolidated flag, episodes table, consolidate_if_due()
   B2  Gate upgrade       small model writes the query, fails open   (optional)

 PHASE C — orchestration              finishes what 8a started
   C1  8b                 tools.run context param → delegate, depth cap, subset rule

 PHASE D — skills                     "different sets of skills"
   D1  SKILL.md loader    matched per message, injected into the prompt
   D2  create_skill       agent proposes, YOU approve, then it writes

 PHASE E — LLMOps                     "did that change help?"
   E1  Layer 11           judge, health scores, release gate
   E2  Layer 12           LangGraph + LangSmith port

 PHASE F — self-extension             the strict pipeline
   F1  Layer 9            architect persona: proposals, no write access
   F2  Layers 13–15       registry, guarded set, tool authoring, build pipeline
```

### Why this order

- **A first** because it is the smallest work that makes the system *visible* —
  you will see `MEMORY.md` and a rules file change as you talk to it, which is
  the fastest way to learn how it works. It also gives you a limited first slice
  of self-improvement with no new safety surface.
- **B before C** because 8b's sub-agents read memory; better to have the memory
  loop settled before agents start multiplying reads of it.
- **C before D** because skills that delegate need `delegate` to exist.
- **E before F** because a system that can propose changes to itself must first
  be able to *measure* whether a change helped. Building the proposer before the
  evaluator is how you get changes nobody can judge.
- **F last** because it is the highest-risk and most specified.

### Rough size and cost
Waku's equivalents total under 500 lines for A, B and D. Ninja's will be
larger — migrations, tests, dashboard panels — but Phases A–B should each be a
single small PR. C is wider because `tools.run`'s signature is touched by every
tool and every test that calls it.

---

## 5. How the work gets done — a process change

The last session cost far more tokens than the code justified: a fresh
implementer, a fresh reviewer, a re-reviewer and a fix round *per task*, on
tasks small enough to hold in one head. It did catch real bugs — but it also
built a whole layer that was then parked.

**Proposed:** for Phases A, B, D, one implementer and **one** review per PR, not
per task. Reserve the full loop for C (wide blast radius) and F (safety-critical).
And **write a short spec you approve before any code**, which is the thing that
would have caught the vector detour.

---

## 6. Decisions I need from you

**1. Where do learned rules live?**
   - **(a) recommended — a runtime file, not in git:** `.ninja/rules/<persona>.md`.
     Treated as *your data*, like facts. The agent may append without a gate, because
     it is a preference, not code, and `PERSONA.md` stays the reviewed, versioned
     source of truth.
   - (b) append into `PERSONA.md` as waku does. Simpler, but it puts agent-written
     text into a git-tracked file your design says needs approval — it contradicts
     `ARCHITECTURE.md` §Persona authoring.

**2. Per-persona or one global soul?** Recommended: per-persona. A psychologist
and a researcher should not share "be terse", and personas already own their
identity. Costs a file per persona.

**3. Order.** Recommended: A → B1 → C → D → E → F. Alternative: do 8b (C1) first
since it is already designed. Say if you want that.

**4. The gate upgrade (B2).** Recommended: **defer**. It adds a model call per
turn, and retrieval quality only becomes measurable once there are enough facts
from consolidation. Decide after B1.

**5. Process (§5).** Recommended: the lighter one for A, B, D.

**6. The parked `layer-5-vectors` branch.** Recommended: leave parked; revisit
after E, when evals can actually say whether embeddings help.

**7. First step.** Recommended: **A1 + A2 together** — write their spec next and
stop for your review before any code.
