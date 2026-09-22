"""Phase D1: skills — procedures that load only when they apply.

A persona is who is speaking, chosen once per turn. A skill is what knowledge
applies to this message, and several can apply at once. Matching is keyword
overlap, not a model call: the score is small enough to compute in your head,
which is the point of choosing it over a classifier — a wrong match is
explainable.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from ninja import semantic

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "skills"
PENDING_DIR = ROOT / ".ninja" / "pending_skills"

REQUIRED = {"name", "description"}
MIN_OVERLAP = 2
MAX_MATCHES = 2
BODY_CAP = 3000
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


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


def load_all(directory: Path | None = None) -> list[Skill]:
    """Every <directory>/*/SKILL.md, freshly read. A handful of small files —
    no cache, so an edit is live on the next message. A broken one is skipped
    with a line to stderr; it never stops the rest from loading.

    Defaults to DIR (the live skills/); passing PENDING_DIR lists staged
    drafts with the same parse-or-skip behavior, instead of a near-duplicate
    function.
    """
    if directory is None:
        directory = DIR
    if not directory.is_dir():
        return []
    found = []
    for d in sorted(directory.iterdir()):
        path = d / "SKILL.md"
        if not path.exists():
            continue
        try:
            found.append(_parse(path.read_text(), path))
        except (ValueError, OSError) as exc:
            print(f"! skipped skill: {exc}", file=sys.stderr)
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


def _safe_name(name: str) -> str:
    """A skill name is a directory name, not a path — the same guard
    personas.load applies to a persona name."""
    if name != Path(name).name or name.startswith("."):
        raise ValueError(f"a skill name is a directory name, not a path: {name!r}")
    return name


def _render(skill: Skill) -> str:
    # yaml.safe_dump, not string interpolation: a description containing a
    # colon ("Use when: planning a week ahead") would otherwise corrupt the
    # frontmatter _parse has to read back.
    frontmatter = yaml.safe_dump(
        {"name": skill.name, "description": skill.description}, sort_keys=False
    )
    return f"---\n{frontmatter}---\n\n{skill.body}\n"


def propose(name: str, description: str, body: str) -> Skill:
    """Stage a draft at PENDING_DIR/<name>/SKILL.md. Nothing reads PENDING_DIR
    except load_all(PENDING_DIR) itself — match() and format_section() only
    ever see DIR — so a proposal cannot affect a turn until approve() moves it.
    """
    name = _safe_name(name.strip())
    if not NAME_RE.match(name):
        raise ValueError(
            f"a skill name must be lowercase letters, digits and hyphens, "
            f"starting with a letter: {name!r}"
        )
    description = " ".join(description.split())
    if not description:
        raise ValueError("description is empty")
    body = body.strip()
    if not body:
        raise ValueError("body is empty")
    skill = Skill(name=name, description=description, body=body)
    target = PENDING_DIR / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render(skill))
    return skill


def approve(name: str) -> Skill:
    """Move a staged draft into DIR, re-validating it on the way — a draft
    could have been hand-edited on disk since it was proposed.

    Overwrites an existing skill of the same name. That's deliberate: a
    re-proposal of an existing skill is the update path, and typing
    /approve-skill is the confirmation an overwrite needs.
    """
    name = _safe_name(name)
    path = PENDING_DIR / name / "SKILL.md"
    if not path.exists():
        raise ValueError(f"no pending skill proposal named {name!r}")
    skill = _parse(path.read_text(), path)
    target = DIR / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(path.read_text())
    path.unlink()
    path.parent.rmdir()
    return skill


def reject(name: str) -> None:
    """Discard a staged draft."""
    name = _safe_name(name)
    path = PENDING_DIR / name / "SKILL.md"
    if not path.exists():
        raise ValueError(f"no pending skill proposal named {name!r}")
    path.unlink()
    path.parent.rmdir()
