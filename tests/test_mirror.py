"""MEMORY.md: the readable mirror of facts, episodes and rules."""

import os
import re
import sqlite3

import pytest

from ninja import agent, consolidation, episodic, mirror, rules, semantic, server
from ninja.trace import connect

from .conftest import StubClient, block, response


@pytest.fixture(autouse=True)
def rules_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "DIR", tmp_path / "rules")
    return tmp_path / "rules"


def episode(summary, thread="assistant", day="2026-09-21"):
    conn = connect()
    conn.execute(
        "INSERT INTO episodes (thread, summary, happened_on, created_at) VALUES (?, ?, ?, ?)",
        (thread, summary, day, "2026-09-21T00:00:00"),
    )
    conn.close()


def headings(text):
    return re.findall(r"^#{1,6} .*$", text, re.M)


def test_an_empty_database_gives_a_valid_file_with_every_section_empty():
    # Fails if an empty section renders nothing, or the rules section is skipped
    # when there is no rules directory.
    mirror.write()
    text = mirror.PATH.read_text()
    assert headings(text) == [
        "# Ninja memory", "## Facts (0)", "## Episodes (0)", "## Learned rules",
    ]
    assert text.count("_none yet_") == 3


def test_facts_episodes_and_rules_appear_in_documented_order(rules_dir):
    # Fails if the order is oldest-first, or a persona's rules are not grouped.
    semantic.remember("first fact", source="told")
    semantic.remember("second fact", source="distilled")
    episode("older", day="2026-09-20")
    episode("newer")
    rules.add_rule("interview-coach", "Ask what role I am interviewing for.")
    rules.add_rule("assistant", "Keep answers short.")
    mirror.write()
    text = mirror.PATH.read_text()
    today = semantic.all_facts(1)[0]["created_at"][:10]
    assert text.index(f"- second fact  distilled · {today}") < text.index("- first fact  told")
    assert text.index("2026-09-21 · assistant — newer") < text.index("— older")
    assert headings(text)[-3:] == ["## Learned rules", "### assistant", "### interview-coach"]
    assert "- Keep answers short." in text
    assert "- Ask what role I am interviewing for." in text
    assert text.index("## Facts") < text.index("## Episodes") < text.index("## Learned rules")


def test_facts_over_the_cap_say_how_many_are_not_shown():
    # Fails if the cap truncates silently, or the count is not hidden = total - shown.
    for i in range(250):
        semantic.remember(f"fact number {i}")
    mirror.write()
    text = mirror.PATH.read_text()
    assert "## Facts (250)" in text
    assert text.count("\n- fact number") == 200
    assert "_50 more not shown_" in text
    assert "fact number 249" in text and "fact number 49 " not in text


def test_episodes_over_the_cap_say_how_many_are_not_shown():
    for i in range(130):
        episode(f"episode {i}")
    mirror.write()
    text = mirror.PATH.read_text()
    assert "## Episodes (130)" in text
    assert text.count("— episode") == 100
    assert "_30 more not shown_" in text


def test_nothing_hidden_means_no_overflow_line():
    # Fails if the overflow line is always printed.
    semantic.remember("one")
    mirror.write()
    assert "more not shown" not in mirror.PATH.read_text()


def test_a_failed_replace_keeps_the_old_file_and_leaves_no_temp(monkeypatch):
    # Fails if the file is written in place instead of via a temp file + replace,
    # or if the temp file is not cleaned up on failure.
    mirror.PATH.write_text("previous good version")
    semantic.remember("new fact")

    def boom(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "replace", boom)
    mirror.write()
    assert mirror.PATH.read_text() == "previous good version"
    assert [p.name for p in mirror.PATH.parent.iterdir() if p.suffix == ".tmp"] == []


def test_a_failure_is_one_line_on_stderr_and_does_not_raise(monkeypatch, capsys):
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    mirror.write()
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "nope" in err


def test_one_unreadable_rules_file_does_not_hide_the_rest(rules_dir, monkeypatch):
    # Fails if a rules read error fails the whole write (the old file stays, stale)
    # or hides the readable files.
    rules_dir.mkdir()
    (rules_dir / "assistant.md").write_text("- Keep answers short.\n")
    (rules_dir / "interview-coach.md").write_bytes(b"\xff\xfe not utf-8")
    semantic.remember("added this test")
    mirror.PATH.write_text("stale")
    mirror.write()
    text = mirror.PATH.read_text()
    assert "- Keep answers short." in text
    assert re.search(r"### interview-coach\n_could not read: UnicodeDecodeError", text)
    assert "added this test" in text and "## Episodes (0)" in text


def test_a_facts_failure_is_shown_and_the_other_sections_are_current(monkeypatch, rules_dir):
    # Fails if a facts error fails the whole write.
    def locked(limit=50):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(semantic, "all_facts", locked)
    episode("still here")
    rules_dir.mkdir()
    (rules_dir / "assistant.md").write_text("- a rule\n")
    mirror.write()
    text = mirror.PATH.read_text()
    assert "## Facts\n_could not read: OperationalError: database is locked_" in text
    assert "— still here" in text and "- a rule" in text


def test_an_episodes_failure_is_shown_and_the_other_sections_are_current():
    # Fails if an episodes error fails the whole write.
    semantic.remember("a fact")
    conn = connect()
    conn.execute("DROP TABLE episodes")
    conn.close()
    mirror.write()
    text = mirror.PATH.read_text()
    assert "## Episodes\n_could not read: OperationalError" in text
    assert "- a fact" in text and "## Learned rules" in text


def test_a_failure_reason_cannot_fake_a_heading(monkeypatch):
    # Fails if the exception message is written through unprocessed.
    def boom(limit=50):
        raise RuntimeError("boom\n## Facts (999)")

    monkeypatch.setattr(semantic, "all_facts", boom)
    mirror.write()
    text = mirror.PATH.read_text()
    assert [h for h in headings(text) if h.startswith("## Facts")] == ["## Facts"]
    assert "boom ## Facts (999)" in text


def test_a_long_failure_reason_is_capped(monkeypatch):
    def boom(limit=50):
        raise RuntimeError("x" * 1000)

    monkeypatch.setattr(semantic, "all_facts", boom)
    mirror.write()
    line = [ln for ln in mirror.PATH.read_text().splitlines() if "could not read" in ln][0]
    assert len(line) < 200


def test_a_rendering_failure_is_still_the_last_resort(monkeypatch, capsys):
    monkeypatch.setattr(mirror, "_render", lambda: 1 / 0)
    mirror.write()
    assert "memory mirror not updated" in capsys.readouterr().err


def test_stored_text_cannot_fake_a_section_heading(rules_dir):
    # Fails if a newline in a fact/episode, or a `#` line in a rules file, is
    # written through raw: each would add a heading the file never wrote.
    semantic.remember("x\n## Episodes\nmore")
    semantic.remember("## Episodes")
    episode("done\n# Ninja memory")
    rules_dir.mkdir()
    (rules_dir / "assistant.md").write_text("- ok\n## Facts (999)\n  ### interview-coach\n")
    mirror.write()
    text = mirror.PATH.read_text()
    assert headings(text) == [
        "# Ninja memory", "## Facts (2)", "## Episodes (1)", "## Learned rules", "### assistant",
    ]
    assert "\\## Facts (999)" in text  # still readable, just not a heading


def test_markdown_in_text_is_otherwise_kept():
    semantic.remember("uses `uv run` and **bold**")
    mirror.write()
    assert "uses `uv run` and **bold**" in mirror.PATH.read_text()


def test_the_file_is_rewritten_each_time():
    mirror.write()
    semantic.remember("later")
    mirror.write()
    assert "later" in mirror.PATH.read_text()


def _spy(monkeypatch, order):
    def wrap(owner, name, label):
        real = getattr(owner, name)

        def spy(*a, **k):
            order.append(label)
            return real(*a, **k)

        monkeypatch.setattr(owner, name, spy)

    wrap(consolidation, "run_if_due", "consolidate")
    wrap(episodic, "save", "save")
    wrap(episodic, "save_exchange", "save")
    wrap(mirror, "write", "mirror")


def test_the_server_mirrors_after_consolidation_and_the_save(monkeypatch):
    # Fails if the hook is missing, before the save, or before B1's hook.
    from fastapi.testclient import TestClient

    from ninja import router

    order = []
    _spy(monkeypatch, order)
    monkeypatch.setattr(router, "route", lambda client, text, current, cast, turn: current)
    stub = StubClient([response([block(type="text", text="hi")], "end_turn")])
    monkeypatch.setattr(server, "_client", stub)
    assert TestClient(server.app).post("/api/chat", json={"text": "hello"}).status_code == 200
    assert order == ["consolidate", "save", "mirror"]
    assert mirror.PATH.exists()


def test_the_repl_mirrors_after_consolidation_and_the_save(monkeypatch):
    from ninja import router

    order = []
    _spy(monkeypatch, order)
    monkeypatch.setattr(router, "route", lambda *a, **k: "assistant")
    stub = StubClient([response([block(type="text", text="hi")], "end_turn")])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)
    lines = iter(["hello"])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)
    agent.main()
    # REPL saves user and assistant separately; the mirror follows both.
    assert order == ["consolidate", "save", "save", "mirror"]
    assert mirror.PATH.exists()


def test_a_broken_mirror_does_not_break_the_turn(monkeypatch):
    # Fails if the hook is called outside write()'s catch-all.
    from fastapi.testclient import TestClient

    from ninja import router

    monkeypatch.setattr(router, "route", lambda client, text, current, cast, turn: current)
    monkeypatch.setattr(mirror, "_render", lambda: 1 / 0)
    stub = StubClient([response([block(type="text", text="hi")], "end_turn")])
    monkeypatch.setattr(server, "_client", stub)
    body = TestClient(server.app).post("/api/chat", json={"text": "hello"}).json()
    assert body["reply"] == "hi"
