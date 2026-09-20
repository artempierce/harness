"""Layer 7: a persona is data.

A persona supplies the three things the loop would otherwise hardcode — the
model, the system prompt, and the tool list. Nothing about the loop changes.

The object is frozen deliberately. It is resolved once per turn and read for
the whole of it: the cockpit is a server, a switch can land while a loop is on
step 3 of 6, and a persona that could change mid-turn would change the toolset
underneath a request already in flight.
"""

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
    meta = yaml.safe_load(frontmatter) or {}
    missing = REQUIRED - set(meta)
    if missing:
        raise ValueError(f"{source}: frontmatter is missing {sorted(missing)}")
    return Persona(
        name=meta["name"],
        description=" ".join(str(meta["description"]).split()),
        instructions=body.strip(),
        tools=tuple(meta["tools"]),
        model=meta["model"],
    )


def _fallback() -> Persona:
    """personas/ is absent. Behave exactly as layer 6 did."""
    return Persona(
        name=DEFAULT,
        description="The default assistant.",
        instructions=DEFAULT_INSTRUCTIONS,
        tools=tuple(s["name"] for s in tools.SCHEMAS),
        model=DEFAULT_MODEL,
    )


def load(name: str) -> Persona:
    path = DIR / name / "PERSONA.md"
    if not path.exists():
        if name == DEFAULT:
            return _fallback()
        raise ValueError(f"no persona named {name!r} in {DIR}")
    return _parse(path.read_text(), path)


def all() -> list[Persona]:  # noqa: A001 — reads as personas.all()
    if not DIR.is_dir():
        return [_fallback()]
    found = [load(d.name) for d in sorted(DIR.iterdir()) if (d / "PERSONA.md").exists()]
    return found or [_fallback()]
