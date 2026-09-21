# Phase A2: MEMORY.md — a readable mirror of everything it knows

**Status:** proposed — awaiting approval, no code written
**Branch (when approved):** `phase-a2-memory-mirror`, from `main` **after A1 and B1 have merged**
**Depends on:** A1 (`ninja/rules.py`, `.ninja/rules/`) and B1 (`episodes` table)
**Reference:** waku's `export_markdown()` (`waku/memory/__init__.py`)

---

## What this adds

One plain-text file you can open in any editor to see **everything the agent has
learned about you**, regenerated after every turn:

```
.ninja/MEMORY.md

# Ninja memory
_A generated view. Edits here are overwritten — the database and rules files
are the source of truth._

## Facts (12)
- Sol is building an agent harness called Ninja.        told · 2026-09-21
- Sol worked at Sam's Club on cart and checkout.        distilled · 2026-09-21
…

## Episodes (4)
- 2026-09-21 · assistant — Sol asked how the retrieval gate works.
…

## Learned rules
### assistant
- Keep answers under five lines.
### interview-coach
- Always ask what role I'm interviewing for.
```

It serves your goal of *"a system to see all my data about myself"* at almost no
cost: no model call, three SQLite reads and one file write.

It is also the **second line of defence for the risk both A1 and B1 carry**: text
the agent writes into its own future prompts (rules, distilled facts) is a
persistent prompt-injection surface, and the mitigation for that is being able
to *see it*. This file is where you would notice a rule or fact you never
intended.

## Behaviour

`mirror.write()` builds the document from `facts`, `episodes` and every file in
`.ninja/rules/`, and writes `.ninja/MEMORY.md`.

- **Facts:** all of them, newest first, with source (`told` / `distilled`) and date. Capped at `MAX_FACTS_SHOWN = 200`, with a final line saying how many more exist — never silently truncated.
- **Episodes:** newest first, capped at `MAX_EPISODES_SHOWN = 100`, same overflow line.
- **Rules:** every persona's rules file, grouped by persona, verbatim.
- **Empty sections** say `_none yet_`, so a fresh install produces a valid file.
- The header states that it is generated and that edits are overwritten.

**Written atomically:** to a temp file in the same directory, then `os.replace`.
A reader (or editor) never sees half a file, and a crash mid-write leaves the
previous good version.

**Never fails a turn.** It is called after the exchange is saved, wrapped so any
error is swallowed (recorded nowhere costly — a one-line `print` to stderr is
enough; there is no trace left to write to at that point). A mirror that is
stale is an inconvenience; a mirror that breaks the chat is a bug.

## Where it runs

`agent.py` (REPL) and `server.py::chat`, **after** `episodic.save(...)` /
`save_exchange(...)`, so the mirror includes what this very turn produced.
One line each. This is the same region B1 hooks, but B1's hook is *before*
`turn.finish()` and this one is *after* the save, so they do not touch each other.

## Testing

Real files under `tmp_path`; redirect `.ninja` with `monkeypatch`. No model.

- Empty database: valid file, every section says `_none yet_`.
- Facts, episodes and two personas' rules all appear, in the documented order.
- **Overflow:** 250 facts → 200 shown plus "50 more"; asserts the count line, not just the length.
- **Atomicity:** make `os.replace` raise; the previous `MEMORY.md` is unchanged and no temp file is left behind.
- **Never raises:** unreadable rules file and a locked database each leave the turn intact.
- Text containing markdown (`# heading`, backticks) is written verbatim and does not corrupt the section structure the file depends on — a fact reading `## Episodes` must not fake a new section. *(See decision 3.)*
- Both hook sites call it after the save (REPL and server), asserted with the same stub-client pattern the B1 hook tests use.

## Not in this change

- Editing memory by hand and having it round-trip. The file is one-way, like waku's.
- A `forget` tool (parked with the vector branch), so there are no fact ids in the file yet.
- Showing traces, costs or personas — the dashboard already does.
- A dashboard link to the file.

## Decisions

1. **Location:** `.ninja/MEMORY.md`, gitignored. Recommended — it is personal data.
2. **Regenerate every turn** vs. only when something changed. Recommended: every turn. It is
   three small reads; tracking "changed" is more code than the work it saves.
3. **Section-spoofing.** A fact whose text is `## Episodes` would break the file's structure.
   Recommended: indent or escape a leading `#` in fact/episode text when rendering.
   Alternative: accept it, since it only affects a file for reading. Cheap either way.
4. **Caps** of 200 facts / 100 episodes. Recommended; the overflow line makes them safe.
