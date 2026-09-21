# Phase D1: skills — procedures that load only when they apply

**Status:** proposed — awaiting review, no code written
**Branch (when approved):** `phase-d1-skills`, from `main`
**Depends on:** nothing unbuilt. Uses `agent.build_system`, `personas.py`'s frontmatter pattern and `semantic.STOPWORDS`
**Reference:** waku's `SkillLoader` (`waku/memory/procedural/loader.py`), `docs/WAKU-MAPPING.md` §3a

---

## What this adds

A persona is *who is speaking*, chosen once per turn. A skill is *what knowledge
applies to this message*, and several can apply at once. D1 gives ninja the
second axis: a folder of procedures whose text enters the system prompt **only
when the message looks like it needs them**.

```
skills/weekly-review/SKILL.md

---
name: weekly-review
description: Run a weekly review — look back at the week, list wins and blockers,
  pick next week's three priorities.
---

When the user asks for a weekly review:
1. Ask what got done, if they have not said.
2. …
```

Nothing about a skill is loaded into the prompt on a turn it does not match. A
skill costs tokens only on the turns it is used, the same rule the retrieval
gate applies to facts.

## The format

One directory per skill, one file in it: `skills/<name>/SKILL.md`.

| Field | Where | Rule |
|---|---|---|
| `name` | frontmatter | required; must equal the directory name |
| `description` | frontmatter | required; **this is the trigger** — matching reads it |
| body | after the closing `---` | markdown; what the model is shown when the skill matches |

Same shape as `PERSONA.md` and as Anthropic's Agent Skills format. Skills are
**git-tracked and reviewed**, like personas. D2 (`create_skill`) will add a
proposal path; D1 has no write path at all.

## Behaviour

**Load.** `skills.load_all()` reads every `skills/*/SKILL.md` on each turn. There
are a handful of small files, so there is no cache and no invalidation to get
wrong: an edit is live on the next message.

**Match.** `skills.match(message)`:
1. Split the message and each skill's `name` + `description` into words — three
   or more letters or digits, lowercased, minus `semantic.STOPWORDS`.
2. A skill matches when the two sets share **at least 2 words**.
3. Return the best matches, most overlap first, ties broken by name, **at most 2**.

You can compute the score in your head. That is the point of choosing it over a
model: a wrong match is explainable.

**Inject.** In `agent.build_system`, after the persona instructions, learned
rules and retrieved facts:

```
Skills that apply to this request:

### weekly-review
<body, cut at 3,000 characters with a "[truncated]" marker>
```

Delegated children get no skills, the same as they get no facts: their context
is the task brief and nothing else.

**Record.** A new trace event, `{"type": "skills", "names": [...]}`, is written
every turn a skill matches, so `ninja trace` and the dashboard show *which*
skills shaped an answer. `trace.print_one` today treats any event type it does
not know as a tool call and would raise `KeyError`; it gains a `skills` branch.

## Failure modes

| Case | Behaviour |
|---|---|
| `skills/` missing or empty | no skills, silently — it is optional and grants nothing |
| one `SKILL.md` malformed (no frontmatter, missing field, name ≠ directory) | that skill is skipped and `! skipped skill <path>: <reason>` goes to stderr **every turn**; the turn proceeds |
| a skill's text asks for a tool the persona lacks | the allowlist in `tools.run` refuses it. A skill cannot grant a tool |

Skipping rather than failing is deliberate: a persona that fails to load loses
tool *restrictions*, so that failure had to be loud and narrow. A skill only
adds text, so the worst a broken one can do is not appear — and the stderr line
makes that visible.

## Where it runs

- `ninja/skills.py` (new): `Skill` (frozen dataclass), `load_all()`, `match()`.
- `ninja/semantic.py`: extract the word-splitting already inside `_query` into a
  public `words(text)`, so skills and the fact gate cannot drift apart. `_query`
  calls it; behaviour is unchanged.
- `ninja/agent.py`: `build_system` calls `skills.match` and appends the section.
- `ninja/trace.py`: `Trace.skills(names)` and the `print_one` branch.
- `skills/weekly-review/SKILL.md`: one seed skill, so the feature runs end to end.

## Testing

Test first; the suite stays offline and free.

- `match`: overlap of 2 matches, overlap of 1 does not; stopwords do not count;
  best first; capped at 2; ties by name.
- Parse: a valid skill loads; missing frontmatter, missing field, and name ≠
  directory are each skipped with a stderr line and the rest still load.
- `build_system`: a matching message injects the skill; a non-matching one does
  not; the body cap truncates with the marker.
- A delegated child's system prompt contains no skills.
- The `skills` trace event is recorded, and `print_one` prints it instead of
  raising.
- `semantic.words` is a pure extraction: the existing `_query` tests still pass.
- The seed skill parses and matches "let's do my weekly review".

## Not in this change

- **D2, `create_skill`.** The agent proposing a skill for you to approve.
- **Per-persona skill lists** (a `skills:` field in `PERSONA.md`). Skills are
  global in D1; revisit if two personas need different sets.
- **Model-based matching** — a `use_skill` tool, or a small classifier. `match()`
  is one function so either can replace it; that decision waits for evals (E1),
  which can say whether keyword matching misses too much.
- **Skill-referenced files, scripts, or tool grants.**

## Decisions

1. **Global, not per persona.** Simplest; skills are reviewed text.
2. **Keyword overlap ≥ 2 on name + description, at most 2 skills.** Waku's rule,
   which is explainable and costs no model call. Its weakness — a paraphrase can
   miss — is the same as the facts gate's, and is measured in E1, not guessed at.
3. **Body cap 3,000 characters (about 750 tokens)** per skill, so two matched
   skills add at most about 1,500 tokens to a turn.
4. **Load on every turn, no cache.** A handful of files; nothing to invalidate.
5. **Broken skill: skip and warn, never fail.** See Failure modes.
