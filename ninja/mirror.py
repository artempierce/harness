"""A readable copy of everything the agent knows about you.

Facts and episodes live in the database and rules in .ninja/rules/, none of
which you would open by hand. This writes them into one markdown file after
every turn. It is one-way: the database and the rules files stay the source of
truth, and editing the mirror changes nothing.

Rules and distilled facts are text the agent puts into its own future prompts,
so this file is also where a rule or fact nobody intended would show up.
"""

import os
import sys
import tempfile
from pathlib import Path

from ninja import rules, semantic
from ninja.trace import connect

PATH = rules.ROOT / ".ninja" / "MEMORY.md"

# Capped so the file stays openable. The overflow line says what was left out.
MAX_FACTS_SHOWN = 200
MAX_EPISODES_SHOWN = 100

HEADER = (
    "# Ninja memory\n"
    "_A generated view. Edits here are overwritten — the database and rules files\n"
    "are the source of truth._\n"
)
NONE = "_none yet_"


def _safe(text: str) -> str:
    """Stop a line of stored text from posing as one of this file's headings.

    A line that begins with `#` would otherwise start a section the file never
    wrote. A backslash keeps it readable and makes it plain text.
    """
    return "\n".join(
        "\\" + line if line.lstrip().startswith("#") else line for line in text.splitlines()
    )


def _one_line(text: str) -> str:
    # One bullet is one line, as with rules: a newline would start a new line
    # of the file that the text could then use to pose as structure.
    return " ".join(text.split())


def _unreadable(exc: Exception) -> str:
    # The reason is text from outside this file, so it gets the same treatment
    # as stored text: one line, and no leading `#`.
    return _safe(f"_could not read: {_one_line(f'{type(exc).__name__}: {exc}')[:120]}_")


def _section(heading: str, build) -> list[str]:
    # A section that cannot be read says so in the file. Failing the whole write
    # would leave the previous file in place looking fine, and hide exactly the
    # rule or fact that broke it.
    try:
        return build()
    except Exception as exc:
        return [heading, _unreadable(exc)]


def _more(hidden: int) -> list[str]:
    return [f"_{hidden} more not shown_"] if hidden > 0 else []


def _facts() -> list[str]:
    total = semantic.count()
    shown = semantic.all_facts(MAX_FACTS_SHOWN)
    lines = [f"- {_one_line(f['content'])}  {f['source']} · {f['created_at'][:10]}" for f in shown]
    return [f"## Facts ({total})", *(lines or [NONE]), *_more(total - len(shown))]


def _episodes() -> list[str]:
    conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        rows = conn.execute(
            "SELECT happened_on, thread, summary FROM episodes ORDER BY id DESC LIMIT ?",
            (MAX_EPISODES_SHOWN,),
        ).fetchall()
    finally:
        conn.close()
    lines = [f"- {day} · {thread} — {_one_line(summary)}" for day, thread, summary in rows]
    return [f"## Episodes ({total})", *(lines or [NONE]), *_more(total - len(rows))]


def _rules() -> list[str]:
    out = ["## Learned rules"]
    for path in sorted(rules.DIR.glob("*.md")) if rules.DIR.is_dir() else []:
        try:
            body = _safe(path.read_text().strip())
        except Exception as exc:
            body = _unreadable(exc)
        if body:
            out += [f"### {path.stem}", body]
    return out if len(out) > 1 else [*out, NONE]


def _render() -> str:
    parts = [
        HEADER.splitlines(),
        _section("## Facts", _facts),
        _section("## Episodes", _episodes),
        _section("## Learned rules", _rules),
    ]
    return "\n\n".join("\n".join(p) for p in parts) + "\n"


def _replace(text: str) -> None:
    # Same directory as the target: os.replace is only atomic within one
    # filesystem, and a reader must never see half a file.
    PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PATH.parent, prefix=".MEMORY-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, PATH)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write() -> None:
    """Regenerate the mirror. Never raises.

    A stale mirror is an inconvenience; one that breaks the chat is a bug. By
    the time this runs the turn is saved and its trace written, so there is
    nowhere left to record a failure but stderr.
    """
    try:
        _replace(_render())
    except Exception as exc:
        print(f"memory mirror not updated: {exc}", file=sys.stderr)
