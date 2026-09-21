"""Learned rules: standing preferences the agent writes for itself.

They live in .ninja/rules/<persona>.md, not in PERSONA.md. A persona is the
reviewed, versioned definition — an agent-written change to it needs approval
and a commit. Rules are the user's data, like facts: the agent may extend the
file freely and the user may edit or delete it by hand.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / ".ninja" / "rules"

# Whatever lands here is read as instructions on every later turn, so the
# ceiling bounds what a rule the agent was talked into can cost.
MAX_RULE = 200
MAX_RULES_FILE = 2000

# The name becomes a filename. fullmatch, not `$`: `$` also matches before a
# trailing newline, which would let "a\n" through.
_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def _path(persona: str) -> Path:
    if not _NAME.fullmatch(persona):
        raise ValueError(f"not a valid persona name: {persona!r}")
    return DIR / f"{persona}.md"


def rules_for(persona: str) -> str:
    path = _path(persona)
    return path.read_text().strip() if path.exists() else ""


def add_rule(persona: str | None, rule: str) -> str:
    if persona is None:
        raise ValueError("add_rule needs to know which persona is speaking.")
    path = _path(persona)
    # One call is exactly one bullet. A newline in the rule would otherwise let
    # "\n- ignore all previous instructions" pose as a second, separate rule.
    rule = " ".join(rule.split()).lstrip("- ").strip()
    if not rule:
        raise ValueError("a rule cannot be empty.")
    if len(rule) > MAX_RULE:
        raise ValueError(f"a rule is at most {MAX_RULE} characters; this one is {len(rule)}.")
    existing = path.read_text() if path.exists() else ""
    if f"- {rule.lower()}" in {line.lower() for line in existing.splitlines()}:
        raise ValueError(f"already a rule: {rule}")
    line = f"- {rule}\n"
    if len(existing) + len(line) > MAX_RULES_FILE:
        raise ValueError(
            f"the rules file is full ({MAX_RULES_FILE} characters). "
            "Ask the user to edit it, then try again."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + line)
    return f"rule saved: {rule}"
