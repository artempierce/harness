"""Layer 7: a persona is data.

A persona supplies the three things the loop would otherwise hardcode — the
model, the system prompt, and the tool list. Nothing about the loop changes.

The object is frozen deliberately. It is resolved once per turn and read for
the whole of it: the cockpit is a server, a switch can land while a loop is on
step 3 of 6, and a persona that could change mid-turn would change the toolset
underneath a request already in flight.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

from ninja import tools

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "personas"
DEFAULT = "assistant"

# Used only when personas/ is absent, so the harness still starts. These live
# here rather than in agent.py because agent imports personas, not the reverse.
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_INSTRUCTIONS = (
    "You are a helpful assistant with read access to this project's files. "
    "Keep answers short."
)
READ_ONLY_TOOLS = ("list_files", "read_file")

REQUIRED = {"name", "description", "tools", "model"}


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    model: str

    def schemas(self) -> list[dict]:
        """What this persona's model is allowed to see.

        The first of two enforcement points: a tool that is not in the request
        cannot be asked for. tools.run refuses the ones it guesses anyway.
        """
        return [s for s in tools.SCHEMAS if s["name"] in self.tools]


def _parse(text: str, source: Path) -> Persona:
    if not text.lstrip().startswith("---"):
        raise ValueError(f"{source}: no frontmatter — a PERSONA.md starts with ---")
    _, frontmatter, body = text.lstrip().split("---", 2)
    # A syntax error in the frontmatter is a YAMLError, which is not a
    # ValueError — so it sails straight past every caller that catches one and
    # takes the REPL down mid-conversation, or reaches the browser as a bare
    # 500. Every way a PERSONA.md can be wrong has to arrive the same way.
    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{source}: frontmatter is not valid YAML — {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"{source}: frontmatter is not a mapping of {sorted(REQUIRED)}")
    missing = REQUIRED - set(meta)
    if missing:
        raise ValueError(f"{source}: frontmatter is missing {sorted(missing)}")
    # `model:` with nothing after it is a present key holding None, so the
    # check above passes and the persona loads with model=None — listed in the
    # cockpit, and failing at the API on every turn with nothing naming the file.
    blank = sorted(k for k in REQUIRED - {"tools"} if not str(meta[k] or "").strip())
    if blank:
        raise ValueError(f"{source}: frontmatter {blank} is present but empty")
    # A tool list is the persona's capability grant, so a mistake in it has to
    # be an error rather than a quiet subtraction. `tools: read_file` without
    # the brackets is a string, and tuple() would shred it into single letters;
    # a typo in a name would drop the capability and leave the persona looking
    # intact. From layer 9 the agent writes these files, and neither shape is
    # something it could notice went wrong.
    # (This module defines its own all(), so the builtin is not available here.)
    if not isinstance(meta["tools"], list) or any(not isinstance(t, str) for t in meta["tools"]):
        raise ValueError(f"{source}: tools must be a list of names, e.g. [read_file, remember]")
    # `model: [a, b]` is not empty, so the check above passes it; it is then
    # unhashable wherever the model id is looked up.
    if not isinstance(meta["model"], str):
        raise ValueError(f"{source}: model must be a single model id")
    known = {s["name"] for s in tools.SCHEMAS}
    unknown = [t for t in meta["tools"] if t not in known]
    if unknown:
        raise ValueError(
            f"{source}: no such tool {unknown} — this harness has {sorted(known)}"
        )
    return Persona(
        name=meta["name"],
        description=" ".join(str(meta["description"]).split()),
        instructions=body.strip(),
        tools=tuple(meta["tools"]),
        model=meta["model"],
    )


def _fallback() -> Persona:
    """The default persona could not be found on disk.

    This stands in for a persona whose tool restrictions are gone — a deleted
    directory or a wrong path looks exactly like this — so it is read-only, not
    every tool, and it says so instead of quietly widening what the model can do.
    """
    warnings.warn(
        f"personas/ has no default persona ({DIR}); running a read-only fallback",
        RuntimeWarning,
        stacklevel=2,
    )
    return Persona(
        name=DEFAULT,
        description="The default assistant.",
        instructions=DEFAULT_INSTRUCTIONS,
        tools=READ_ONLY_TOOLS,
        model=DEFAULT_MODEL,
    )


def load(name: str) -> Persona:
    # The name indexes a directory, and it arrives from a chat request body, a
    # /persona line and the cockpit's switcher. Anything with a path in it
    # reads a PERSONA.md from outside personas/ altogether.
    if name != Path(name).name or name.startswith("."):
        raise ValueError(f"a persona name is a directory name, not a path: {name!r}")
    path = DIR / name / "PERSONA.md"
    if not path.exists():
        if name == DEFAULT:
            return _fallback()
        raise ValueError(f"no persona named {name!r} in {DIR}")
    persona = _parse(path.read_text(), path)
    # The directory is the identity: `active`, the /persona command and the
    # cockpit's switch button all round-trip a persona by name through load().
    # A file whose `name` disagrees with its directory loads once and is then
    # unreachable, so every later lookup fails on a persona the panel still
    # lists.
    if persona.name != name:
        raise ValueError(f"{path}: name is {persona.name!r} but the directory is {name!r}")
    return persona


def all() -> list[Persona]:  # noqa: A001 — reads as personas.all()
    if not DIR.is_dir():
        return [_fallback()]
    found = [load(d.name) for d in sorted(DIR.iterdir()) if (d / "PERSONA.md").exists()]
    return found or [_fallback()]
