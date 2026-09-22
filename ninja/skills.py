"""Phase D1: skills — procedures that load only when they apply.

A persona is who is speaking, chosen once per turn. A skill is what knowledge
applies to this message, and several can apply at once. Matching is keyword
overlap, not a model call: the score is small enough to compute in your head,
which is the point of choosing it over a classifier — a wrong match is
explainable.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from ninja import semantic

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "skills"

REQUIRED = {"name", "description"}
MIN_OVERLAP = 2
MAX_MATCHES = 2
BODY_CAP = 3000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def _parse(text: str, source: Path) -> Skill:
    if not text.lstrip().startswith("---"):
        raise ValueError(f"{source}: no frontmatter — a SKILL.md starts with ---")
    _, frontmatter, body = text.lstrip().split("---", 2)
    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{source}: frontmatter is not valid YAML — {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"{source}: frontmatter is not a mapping of {sorted(REQUIRED)}")
    missing = REQUIRED - set(meta)
    if missing:
        raise ValueError(f"{source}: frontmatter is missing {sorted(missing)}")
    blank = sorted(k for k in REQUIRED if not str(meta[k] or "").strip())
    if blank:
        raise ValueError(f"{source}: frontmatter {blank} is present but empty")
    name = str(meta["name"])
    if name != source.parent.name:
        raise ValueError(
            f"{source}: name is {name!r} but the directory is {source.parent.name!r}"
        )
    return Skill(
        name=name,
        description=" ".join(str(meta["description"]).split()),
        body=body.strip(),
    )


def load_all() -> list[Skill]:
    """Every skills/*/SKILL.md, freshly read. A handful of small files — no
    cache, so an edit is live on the next message. A broken one is skipped
    with a line to stderr; it never stops the rest from loading."""
    if not DIR.is_dir():
        return []
    found = []
    for d in sorted(DIR.iterdir()):
        path = d / "SKILL.md"
        if not path.exists():
            continue
        try:
            found.append(_parse(path.read_text(), path))
        except ValueError as exc:
            print(f"! skipped skill {path}: {exc}", file=sys.stderr)
    return found


def match(message: str, skills: list[Skill]) -> list[Skill]:
    """The best MAX_MATCHES skills whose name+description shares at least
    MIN_OVERLAP words with the message, after stopword removal. Best overlap
    first, ties broken by name — deterministic, so a match is explainable."""
    message_words = set(semantic.words(message))
    scored = []
    for skill in skills:
        skill_words = set(semantic.words(f"{skill.name} {skill.description}"))
        overlap = len(message_words & skill_words)
        if overlap >= MIN_OVERLAP:
            scored.append((overlap, skill))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [skill for _, skill in scored[:MAX_MATCHES]]


def format_section(matched: list[Skill]) -> str:
    """The block build_system appends when one or more skills matched."""
    blocks = []
    for skill in matched:
        body = skill.body
        if len(body) > BODY_CAP:
            body = body[:BODY_CAP] + "\n[truncated]"
        blocks.append(f"### {skill.name}\n{body}")
    return "Skills that apply to this request:\n\n" + "\n\n".join(blocks)
