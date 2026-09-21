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

**[docs/PLAN.md](docs/PLAN.md)** is the one document: what is built, how it works,
and what comes next.

---

## Status

See [docs/PLAN.md](docs/PLAN.md) §2 — it is kept current; this file is not.

---

## Install

Requires [uv](https://docs.astral.sh/uv/) and an Anthropic API key.

```bash
git clone git@github.com:artempierce/ninja-agent.git
cd ninja-agent
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
ninja trace         the last 10 turns
ninja trace 7       one turn, step by step
ninja dashboard     the browser cockpit → localhost:7777
```

In the REPL: `/persona` lists the cast, `/persona <name>` switches.

The cockpit has chat on the right and the system on the left: overview, the
loop, tools, guardrails, episodic and semantic memory, and a growth panel
listing all fifteen layers. Every panel reads the running code — change
`MAX_STEPS` and the guardrails panel moves.

---

## Using it

Talk to it. Ctrl-D quits.

```
you> what does this project depend on?
  ↳ list_files({'path': '.'})
  ↳ read_file({'path': 'pyproject.toml'})

agent> anthropic and python-dotenv.
[trace 12 · 5 messages · 910 in / 58 out · $0.00120]
```

The `↳` lines are tool calls — the loop turning. You didn't name
`pyproject.toml`; it found that from the listing and decided to read it.

The bracket line is the turn's receipt. `5 messages` is the conversation being
re-sent to the API — it grows every turn, because the model is stateless and
remembers nothing between requests. `ninja trace 12` replays what happened.

Today Ninja has three tools: `list_files`, `read_file` and `remember`. The
first two are read-only and rooted at this project; the third writes a durable
fact. It cannot run commands or reach the web.

It does remember. Episodic memory replays the last few messages at startup, and
semantic memory holds durable facts retrieved by relevance — but only when the
**retrieval gate** decides the turn needs them. Most turns it skips, which is
context tokens not spent and irrelevant facts not injected.

### Coming as layers land

```
you> quiz me on api testing       # routed to interview-coach
you> make a note about the call   # routed to assistant, the coach untouched
you> and back to flaky tests      # the coach's thread, as you left it

you> compare these two libraries  # delegates to a sub-agent  (layer 8b)
you> make me an interview coach   # drafts one, asks first    (layer 9)
```

---

## Layout

```
ninja/            the agent — raw Python
  cli.py          the `ninja` command and its subcommands
  agent.py        the loop, the REPL, the retrieval gate
  tools.py        what the model is allowed to call
  trace.py        one record per turn
  episodic.py     what was said, and recalling it
  semantic.py     durable facts, and the gate in front of them
  server.py       the cockpit's API
tests/            33 tests — stubbed model, throwaway db, free and offline
ninja_lc/         the same system on LangGraph         (layer 12)
personas/         one PERSONA.md per persona
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

## Contributing

Nothing lands on `main` directly. Branch, open a PR, and the
[quality gate](.github/workflows/gate.yml) runs tests, `ruff`, `pip-audit` and a
secret scan before it can merge. See [CONTRIBUTING.md](CONTRIBUTING.md).

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
