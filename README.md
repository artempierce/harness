# ninja

A personal assistant that is a cast of agents rather than one — built from scratch,
one layer at a time, to learn how an agent harness actually works.

Each **persona** is a markdown file: instructions, the tools it's allowed to use,
and a model setting. You can talk to one directly, let the assistant hand work off
to another mid-answer, or ask it to draft a new persona and approve it before it's
written to disk.

There is no framework underneath. The loop, the memory, the guardrails and the
eval harness are a few hundred lines of Python you can read in a sitting. A
LangChain/LangGraph port lives alongside it in `ninja_lc/`, so the two can be
compared directly.

**[ARCHITECTURE.md](ARCHITECTURE.md)** describes the whole design, including the
parts not built yet.

---

## Status

Built layer by layer. Two of twelve so far.

| | Layer | |
|---|---|---|
| 1 | Bare agent run | ✅ |
| 2 | Loop, tools, stop condition | ✅ |
| 3 | Tracing | — |
| 4 | Episodic memory | — |
| 5 | Semantic memory + retrieval gate | — |
| 6 | Dashboard | — |
| 7 | Personas | — |
| 8 | Delegation | — |
| 9 | Persona authoring | — |
| 10 | Consolidation | — |
| 11 | Eval, diagnose, release | — |
| 12 | LangGraph port | — |

---

## Install

Requires [uv](https://docs.astral.sh/uv/) and an Anthropic API key.

```bash
git clone git@github.com:artempierce/harness.git
cd harness
cp .env.example .env        # then paste your key into .env
```

Run it from the project without installing anything:

```bash
uv run ninja
```

Or put `ninja` on your PATH, pointed at your working copy so edits take effect
immediately:

```bash
uv tool install --editable .
ninja
```

`uv run` creates the virtualenv and installs dependencies on first use — there is
no separate setup step.

---

## Commands

```
ninja               talk to Ninja in the terminal
ninja dashboard     the browser cockpit → localhost:7777   (layer 6)
```

---

## Using it

Talk to it. Ctrl-D quits.

```
you> what does this project depend on?
  ↳ list_files({'path': '.'})
  ↳ read_file({'path': 'pyproject.toml'})

agent> anthropic and python-dotenv.
[working memory: 5 messages]
```

The `↳` lines are tool calls — the loop turning. You didn't name
`pyproject.toml`; it found that from the listing and decided to read it.

`[working memory: N messages]` is the conversation being re-sent to the API. It
grows every turn, because the model is stateless and remembers nothing between
requests. Quit and restart and it will have forgotten you — until layer 4.

Today Ninja has two tools, `list_files` and `read_file`, both read-only and both
rooted at this project. It cannot write, run commands, reach the web, or remember
anything past the session.

### Coming as layers land

```
you> /persona researcher          # switch personas          (layer 7)
you> compare these two libraries  # delegates automatically  (layer 8)
you> make me an interview coach   # drafts one, asks first   (layer 9)
```

---

## Layout

```
ninja/            the agent — raw Python
  cli.py          the `ninja` command and its subcommands
  agent.py        the loop and the REPL
  tools.py        what the model is allowed to call
ninja_lc/         the same system on LangGraph         (layer 12)
personas/         one PERSONA.md per persona           (layer 7)
sql/              schema, shared by both               (layer 4)
evals/            test cases, shared by both           (layer 11)
ui/               the dashboard                        (layer 6)
```

---

## Configuration

`.env` holds your key and is gitignored — it never enters the repo.
`.env.example` shows the shape.

The model is a constant at the top of `ninja/agent.py`. It defaults to
`claude-haiku-4-5` to keep the cost of learning near zero; swap it for
`claude-opus-5` when you want better answers.

---

## About the name

The distribution is `ninja-agent`; the command and the import are `ninja`. They
differ because `ninja` on PyPI is already the C build tool, so `pip install ninja`
would get you something else entirely. `pyproject.toml` maps one to the other via
`[project.scripts]` and `[tool.hatch.build.targets.wheel]`.

---

## Why it's built this way

Every layer is built by hand first, from an architecture diagram rather than from
someone else's source, and only ported to a framework afterwards. The raw version
is never deleted.

A framework's job is to hide the mechanism. That's the right trade in production
and the wrong one while learning — so the order is: write the loop, understand the
loop, then watch what `create_react_agent` does with it.

Modelled on the architecture in
[waku-agent](https://github.com/ShenSeanChen/waku-agent), deliberately not read
while building.
