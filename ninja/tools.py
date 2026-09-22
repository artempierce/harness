"""Layer 2: the tools the agent can call, and the dispatcher that runs them.

SCHEMAS is what the model sees — it picks a tool by reading these descriptions.
run() is what actually happens. The model never executes anything itself.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from ninja import rules, semantic, skills

# The model chooses the path, so the path needs a boundary.
ROOT = Path(__file__).resolve().parent.parent

# What a file read may put in the transcript. Everything a tool returns is
# re-sent on every later step of the turn, so a big file is a cost paid again
# on each round trip.
MAX_READ = 20_000

SCHEMAS = [
    {
        "name": "list_files",
        "description": "List the files and directories at a path inside the project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the project root. Use '.' for the root.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a text file inside the project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the project root.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Store a durable fact about the user or their work, so it can be "
            "recalled in a later conversation. Use for things that stay true — "
            "who they are, what they are building, decisions they have made. "
            "Do not use for passing details or for what was just said."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "One self-contained sentence, in the third person.",
                }
            },
            "required": ["fact"],
        },
    },
    {
        "name": "add_rule",
        "description": (
            "Save a standing preference about how this person wants you to behave, "
            "so it applies in every later conversation. Use when they tell you how "
            "to act from now on. Do not use for facts about them (use remember) or "
            "for one-off requests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule": {
                    "type": "string",
                    "description": "One short instruction on a single line, e.g. "
                    "'Keep answers under five lines.'",
                }
            },
            "required": ["rule"],
        },
    },
    {
        "name": "propose_skill",
        "description": (
            "Draft a new skill for this person to review and approve before it "
            "takes effect. Use when you notice something they do repeatedly that "
            "none of the current skills cover. This only stages the draft — "
            "nothing is saved until they approve it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Lowercase, hyphenated identifier, e.g. 'weekly-review'.",
                },
                "description": {
                    "type": "string",
                    "description": "One sentence: when this skill applies.",
                },
                "body": {
                    "type": "string",
                    "description": "The skill's instructions, in full.",
                },
            },
            "required": ["name", "description", "body"],
        },
    },
    {
        "name": "delegate",
        "description": (
            "Hand a self-contained job to another persona and get its answer back. "
            "It starts with a blank slate: it sees only the task you write here, not "
            "this conversation or anything you know about the user, so put everything "
            "it needs in the task. It can read but not write, and it may not hold "
            "tools you do not have."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "persona": {"type": "string", "description": "Name of the persona to hand it to."},
                "task": {
                    "type": "string",
                    "description": "The complete brief, including the shape of answer you want.",
                },
            },
            "required": ["persona", "task"],
        },
    },
]


def _resolve(path: str) -> Path:
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError(f"path escapes the project root: {path}")
    # .env lives inside the project. Anything the agent reads ends up in the
    # message history, which gets sent to the API — so the key must stay out.
    if any(part.startswith(".") for part in target.relative_to(ROOT).parts):
        raise ValueError(f"hidden files are off limits: {path}")
    return target


def run(
    name: str,
    args: dict,
    allowed: Sequence[str],
    *,
    persona: str | None = None,
    spawn: Callable[[str, str], str] | None = None,
) -> str:
    # The allowlist is enforced here as well as by filtering the schemas,
    # because filtering is advisory: a model that has seen a tool name earlier
    # in the conversation can still emit it. One gate, first — a per-branch
    # check is fail-open the moment a new tool forgets to repeat it.
    # `str` is a Sequence[str], so an allowlist that arrived flattened into one
    # string would be tested by substring — granting every tool whose name
    # appears anywhere in it. Refuse the shape rather than the symptom.
    if isinstance(allowed, str) or name not in allowed:
        raise ValueError(f"{name} is not in this persona's allowlist")
    # The model can emit any JSON for an argument. A wrong type is its mistake,
    # so refuse it here as one instead of letting it surface as a TypeError or
    # a database error that the loop files under "our bug".
    for key in ("path", "fact", "rule", "name", "description", "body"):
        if key in args and not isinstance(args[key], str):
            raise ValueError(f"{key} must be a string")
    if name == "list_files":
        entries = _resolve(args["path"]).iterdir()
        return "\n".join(
            sorted(
                p.name + ("/" if p.is_dir() else "")
                for p in entries
                if not p.name.startswith(".")
            )
        )
    if name == "read_file":
        text = _resolve(args["path"]).read_text()
        if len(text) > MAX_READ:
            return f"{text[:MAX_READ]}\n[truncated: {len(text) - MAX_READ} more characters]"
        return text
    if name == "remember":
        semantic.remember(args["fact"])
        return f"remembered: {args['fact']}"
    if name == "add_rule":
        return rules.add_rule(persona, args["rule"])
    if name == "propose_skill":
        skill = skills.propose(args["name"], args["description"], args["body"])
        return (
            f"staged: {skill.name}\n\n"
            f"Tell them to run `/approve-skill {skill.name}` to save it, "
            f"or `/reject-skill {skill.name}` to discard it."
        )
    if name == "delegate":
        # `spawn` is handed in rather than imported: starting a loop is
        # agent.py's job, and agent imports this module. Both arguments come
        # from the model, so a null or a number is checked here — .get() only
        # supplies a default for an absent key, and Path() on a non-string
        # would be a TypeError, which is not one of the model's mistakes we catch.
        target, task = args.get("persona"), args.get("task")
        if not isinstance(target, str) or not isinstance(task, str) or not task.strip():
            raise ValueError("delegate needs a persona name and a non-empty task, both text.")
        if spawn is None:
            raise ValueError("delegate needs a runtime to spawn into.")
        return spawn(target, task)
    raise ValueError(f"unknown tool: {name}")
