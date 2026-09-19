# harness

A personal assistant that is a cast of agents rather than one — built from scratch,
one layer at a time, to learn how an agent harness actually works.

Each **persona** is a markdown file: instructions, the tools it's allowed to use,
and a model setting. You can talk to one directly, let the assistant hand work off
to another mid-answer, or ask it to draft a new persona and approve it before it's
written to disk.

There is no framework underneath. The loop, the memory, the guardrails and the
eval harness are about four hundred lines of Python you can read in a sitting.
A LangChain/LangGraph port lives alongside it in `harness_lc/`, so the two can be
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

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and an Anthropic API key.

```bash
git clone git@github.com:artempierce/harness.git
cd harness
cp .env.example .env        # then paste your key into .env
uv run python -m harness
```

`uv run` creates the virtualenv and installs dependencies on first use — there's
no separate setup step.

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

### Coming as layers land

```
you> /persona researcher          # switch personas          (layer 7)
you> compare these two libraries  # delegates automatically  (layer 8)
you> make me an interview coach   # drafts one, asks first   (layer 9)
```

---

## Layout

```
harness/          the agent — raw Python
  __main__.py     the loop and the REPL
  tools.py        what the model may call
harness_lc/       the same system on LangGraph        (layer 12)
personas/         one PERSONA.md per persona          (layer 7)
sql/              schema, shared by both              (layer 4)
evals/            test cases, shared by both          (layer 11)
ui/               the dashboard                       (layer 6)
```

---

## Configuration

`.env` holds your key and is gitignored — it never enters the repo. `.env.example`
shows the shape.

The model is a constant at the top of `harness/__main__.py`. It defaults to
`claude-haiku-4-5` to keep the cost of learning near zero; swap it for
`claude-opus-5` when you want better answers.

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
