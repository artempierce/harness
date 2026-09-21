"""Shared fixtures.

Every test runs against a throwaway database, so the suite never touches
.ninja/state.db and tests can't see each other's rows.
"""

import tempfile
import types
from pathlib import Path

import pytest

from ninja import mirror, trace

# Redirected at import, not in a fixture, and that ordering is the point.
# pytest imports conftest before any test module, and ninja/server.py opens the
# database at ITS import — `messages = episodic.recall()` runs during
# collection, before a single fixture has been set up. Without this line,
# importing tests/test_server.py reaches the real ~/.ninja/state.db, which was
# harmless when connect() only ran CREATE TABLE IF NOT EXISTS and is not now
# that it also runs migrations: collecting the suite would migrate the
# developer's own database. The autouse fixture below still gives every
# individual test its own file.
trace.DB = Path(tempfile.mkdtemp(prefix="ninja-tests-")) / "collection.db"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "DB", tmp_path / "state.db")
    return tmp_path / "state.db"


def block(**kw):
    return types.SimpleNamespace(**kw)


def response(content, stop_reason, tokens=(100, 20)):
    """Shaped like an anthropic Message — only the fields run_turn reads."""
    return types.SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=types.SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


def a_persona(**overrides):
    """A Persona for tests that are not about loading one."""
    from ninja.personas import Persona

    fields = {
        "name": "test",
        "description": "A persona used by tests.",
        "instructions": "You are a test persona.",
        "tools": ("list_files", "read_file", "remember"),
        "model": "claude-haiku-4-5",
    }
    fields.update(overrides)
    fields["tools"] = tuple(fields["tools"])
    return Persona(**fields)


class StubClient:
    """Replays a scripted list of responses. Records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.messages = self

    def create(self, **kw):
        # Snapshot the messages list — the loop mutates it in place, so storing
        # the reference would make every recorded call look identical.
        self.seen.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)


@pytest.fixture(autouse=True)
def temp_mirror(tmp_path, monkeypatch):
    # The turn tests run the real hook, which would otherwise overwrite the
    # developer's own .ninja/MEMORY.md.
    monkeypatch.setattr(mirror, "PATH", tmp_path / "MEMORY.md")
