"""Layer 2: the tools the agent can call, and the dispatcher that runs them.

SCHEMAS is what the model sees — it picks a tool by reading these descriptions.
run() is what actually happens. The model never executes anything itself.
"""

from collections.abc import Sequence
from pathlib import Path

from ninja import semantic

# The model chooses the path, so the path needs a boundary.
ROOT = Path(__file__).resolve().parent.parent

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


def run(name: str, args: dict, allowed: Sequence[str]) -> str:
    # The allowlist is enforced here as well as by filtering the schemas,
    # because filtering is advisory: a model that has seen a tool name earlier
    # in the conversation can still emit it. One gate, first — a per-branch
    # check is fail-open the moment a new tool forgets to repeat it.
    # `str` is a Sequence[str], so an allowlist that arrived flattened into one
    # string would be tested by substring — granting every tool whose name
    # appears anywhere in it. Refuse the shape rather than the symptom.
    if isinstance(allowed, str) or name not in allowed:
        raise ValueError(f"{name} is not in this persona's allowlist")
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
        return _resolve(args["path"]).read_text()
    if name == "remember":
        semantic.remember(args["fact"])
        return f"remembered: {args['fact']}"
    raise ValueError(f"unknown tool: {name}")
