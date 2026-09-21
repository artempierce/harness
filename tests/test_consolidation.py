"""Layer 10: consolidation. StubClient only, so no call is ever paid for."""

import json
import types

import pytest

from ninja import consolidation, episodic, semantic, trace

from .conftest import StubClient, block, response


def reply(facts=("Sol prefers tea.",), episode="Talked about drinks.", **kw):
    body = json.dumps({"facts": list(facts), "episode": episode})
    return response([block(type="text", text=body)], kw.get("stop", "end_turn"), (500, 60))


def raw(text, stop="end_turn"):
    return response([block(type="text", text=text)], stop, (500, 60))


def talk(thread, n, start=0):
    for i in range(start, start + n):
        episodic.save_exchange("s", f"q{i} {thread}", f"a{i} {thread}", None, thread)


def rows(sql, *args):
    conn = trace.connect()
    out = conn.execute(sql, args).fetchall()
    conn.close()
    return out


def flagged():
    return rows("SELECT COUNT(*) FROM chat_log WHERE consolidated = 1")[0][0]


def facts():
    return [r[0] for r in rows("SELECT content FROM facts")]


def last_event(turn):
    return [e for e in turn.events if e["type"] == "consolidation"][-1]


def test_below_threshold_makes_no_call():
    # Fails against an implementation that consolidates every turn: the empty
    # script makes an unwarranted call raise IndexError (caught inside, but
    # client.seen would be non-empty).
    talk("assistant", consolidation.CONSOLIDATE_EVERY - 1)
    client, turn = StubClient([]), trace.Trace("x")
    consolidation.run_if_due(client, turn)
    assert client.seen == []
    assert turn.events == []


def test_at_threshold_writes_distilled_facts_one_episode_and_flags_rows():
    # Fails if source is left as "told", the episode is skipped, or rows unflagged.
    talk("assistant", 6)
    client, turn = StubClient([reply()]), trace.Trace("x")
    consolidation.run_if_due(client, turn)
    assert len(client.seen) == 1
    assert rows("SELECT content, source FROM facts") == [("Sol prefers tea.", "distilled")]
    ep = rows("SELECT thread, summary, happened_on FROM episodes")
    assert ep[0][:2] == ("assistant", "Talked about drinks.")
    assert len(ep[0][2]) == 10
    assert flagged() == 12
    assert last_event(turn)["ok"] is True


def test_it_is_per_thread():
    # Fails against a global counter, which would consolidate all 9 exchanges.
    talk("assistant", 6)
    talk("interview-coach", 3)
    consolidation.run_if_due(StubClient([reply()]), trace.Trace("x"))
    assert rows("SELECT DISTINCT thread FROM chat_log WHERE consolidated = 1") == [("assistant",)]
    assert rows("SELECT COUNT(*) FROM chat_log WHERE consolidated = 0")[0][0] == 6
    assert rows("SELECT thread FROM episodes") == [("assistant",)]


def test_the_batch_is_capped_and_oldest_first():
    # Fails without the LIMIT (30 consolidated) or with newest-first (q0 left).
    talk("assistant", 30)
    client = StubClient([reply()])
    consolidation.run_if_due(client, trace.Trace("x"))
    assert flagged() == 40
    assert rows("SELECT COUNT(*) FROM chat_log WHERE consolidated = 0")[0][0] == 20
    sent = client.seen[0]["messages"][0]["content"]
    assert "q0 assistant" in sent and "q19 assistant" in sent and "q20 assistant" not in sent


def test_a_second_call_makes_no_api_call():
    # Fails if rows are not flagged (or the flag is not consulted).
    talk("assistant", 6)
    consolidation.run_if_due(StubClient([reply()]), trace.Trace("x"))
    again = StubClient([])
    consolidation.run_if_due(again, trace.Trace("x"))
    assert again.seen == []
    assert len(facts()) == 1


def test_the_prompt_carries_the_existing_facts():
    # Fails if the known facts are left out, which is the whole dedup mechanism.
    semantic.remember("Sol lives in Auckland.")
    talk("assistant", 6)
    client = StubClient([reply()])
    consolidation.run_if_due(client, trace.Trace("x"))
    assert "Sol lives in Auckland." in client.seen[0]["system"]
    assert client.seen[0]["model"] == consolidation.MODEL


def test_only_the_most_recent_facts_are_shown():
    for i in range(consolidation.MAX_KNOWN + 5):
        semantic.remember(f"fact number {i}")
    talk("assistant", 6)
    client = StubClient([reply()])
    consolidation.run_if_due(client, trace.Trace("x"))
    system = client.seen[0]["system"]
    assert f"fact number {consolidation.MAX_KNOWN + 4}" in system
    assert "fact number 4\n" not in system + "\n"


def test_cost_lands_once_in_the_turns_receipt():
    # Fails if consolidation writes its own trace row or its tokens are dropped.
    talk("assistant", 6)
    turn = trace.Trace("x")
    turn.model("claude-haiku-4-5", response([], "end_turn", (100, 20)), 1, "assistant")
    consolidation.run_if_due(StubClient([reply()]), turn)
    trace_id = turn.finish("done")
    assert turn.input_tokens == 600
    assert rows("SELECT COUNT(*) FROM traces")[0][0] == 1
    row = rows("SELECT input_tokens, output_tokens, model_calls, persona FROM traces")[0]
    assert row == (600, 80, 2, "assistant")
    events = json.loads(rows("SELECT events FROM traces WHERE id = ?", trace_id)[0][0])
    assert [e["persona"] for e in events if e["type"] == "model"] == ["assistant", "consolidation"]


# Failure table: (script entry, what the trace says)
def _attempt(script_entry):
    talk("assistant", 6)
    turn = trace.Trace("x")
    consolidation.run_if_due(StubClient([script_entry]), turn)
    return turn


def assert_untouched():
    assert flagged() == 0
    assert facts() == []
    assert rows("SELECT COUNT(*) FROM episodes")[0][0] == 0


def test_a_raising_call_leaves_rows_and_says_why():
    class Boom:
        messages = types.SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(OSError("net")))

    talk("assistant", 6)
    turn = trace.Trace("x")
    consolidation.run_if_due(Boom(), turn)
    assert_untouched()
    assert "call failed: net" in last_event(turn)["why"]
    assert turn.cost == 0


def test_a_truncated_reply_is_distinguished_from_bad_json():
    turn = _attempt(raw('{"facts": ["cut off', stop="max_tokens"))
    assert_untouched()
    assert "truncated" in last_event(turn)["why"]
    assert turn.input_tokens == 500


@pytest.mark.parametrize("text", [
    "no json at all",
    '{"facts": ["unterminated"',
    '{"facts": null, "episode": "x"}',
    '{"facts": "a string", "episode": "x"}',
    '{"facts": {"a": 1}, "episode": "x"}',
    '{"episode": "x"}',
    '{"facts": [], "episode": {"a": 1}}',
    '["facts"]',
])
def test_unusable_replies_leave_rows_untouched(text):
    # facts: null is valid JSON with the key present. Fails against
    # data.get("facts", []) followed by iteration, which raises on None.
    turn = _attempt(raw(text))
    assert_untouched()
    event = last_event(turn)
    assert event["ok"] is False and "unusable" in event["why"]
    assert turn.input_tokens == 500      # still paid for, still recorded


def test_bad_and_oversized_facts_are_dropped_and_the_rest_kept():
    long = "x" * (consolidation.MAX_FACT_CHARS + 1)
    exact = "y" * consolidation.MAX_FACT_CHARS
    _attempt(reply(facts=["good", 5, None, long, exact, "  "]))
    assert facts() == ["good", exact]
    assert flagged() == 12


def test_more_than_ten_facts_keeps_the_first_ten():
    _attempt(reply(facts=[f"fact {i}" for i in range(15)]))
    assert facts() == [f"fact {i}" for i in range(10)]


def test_empty_facts_and_episode_still_marks_rows():
    # Fails if empty is treated as a failure: every turn would pay again.
    turn = _attempt(reply(facts=[], episode=""))
    assert flagged() == 12
    assert facts() == []
    assert rows("SELECT COUNT(*) FROM episodes")[0][0] == 0
    assert last_event(turn)["ok"] is True


def test_a_null_episode_is_treated_as_empty():
    _attempt(raw('{"facts": ["kept"], "episode": null}'))
    assert facts() == ["kept"]
    assert rows("SELECT COUNT(*) FROM episodes")[0][0] == 0
    assert flagged() == 12


def test_a_fenced_reply_is_still_read():
    _attempt(raw('```json\n{"facts": ["kept"], "episode": "e"}\n```'))
    assert facts() == ["kept"]


def test_a_failed_write_rolls_back_facts_episode_and_flags():
    # Fails against non-atomic code: facts are inserted before the episode, so
    # without one transaction "Sol prefers tea." survives and the retry stores
    # it a second time.
    talk("assistant", 6)
    conn = trace.connect()
    conn.execute(
        "CREATE TRIGGER no_episodes BEFORE INSERT ON episodes"
        " BEGIN SELECT RAISE(ABORT, 'disk on fire'); END"
    )
    conn.close()
    turn = trace.Trace("x")
    consolidation.run_if_due(StubClient([reply()]), turn)
    assert_untouched()
    event = last_event(turn)
    assert event["ok"] is False and "disk on fire" in event["why"]
    assert turn.input_tokens == 500


def test_the_turn_survives_and_retry_succeeds_after_a_failure():
    talk("assistant", 6)
    turn = trace.Trace("x")
    consolidation.run_if_due(StubClient([raw("garbage")]), turn)
    consolidation.run_if_due(StubClient([reply()]), turn)
    assert flagged() == 12
    assert len(facts()) == 1


def test_remember_without_a_connection_is_unchanged():
    semantic.remember("Sol likes tea.", "told", 7)
    assert rows("SELECT content, source, trace_id FROM facts") == [("Sol likes tea.", "told", 7)]


def test_remember_on_a_passed_connection_waits_for_the_caller_to_commit():
    conn = trace.connect()
    conn.execute("BEGIN")
    semantic.remember("held", conn=conn)
    assert facts() == []          # not visible from another connection yet
    conn.execute("ROLLBACK")
    conn.close()
    assert facts() == []


def test_existing_rows_come_through_the_migration_unconsolidated(temp_db):
    import sqlite3

    old = sqlite3.connect(temp_db)
    old.executescript(trace.SCHEMA.read_text())
    old.executescript(
        "ALTER TABLE traces ADD COLUMN persona TEXT;"
        "ALTER TABLE chat_log ADD COLUMN thread TEXT;"
    )
    old.execute("PRAGMA user_version = 2")
    old.execute(
        "INSERT INTO chat_log (session_id, role, content, created_at, thread)"
        " VALUES ('old', 'user', 'hi', '2026-01-01T00:00:00', 'assistant')"
    )
    old.commit()
    old.close()
    assert rows("SELECT consolidated FROM chat_log") == [(0,)]
    assert rows("PRAGMA table_info(episodes)")


def test_the_server_hook_runs_before_finish(monkeypatch):
    # Fails if the hook sits after finish(): the trace row would already be
    # written, so its tokens (500 in of the 1100) would land nowhere.
    from fastapi.testclient import TestClient

    from ninja import server

    talk("assistant", 6)
    stub = StubClient([
        raw("assistant"),
        response([block(type="text", text="hi")], "end_turn", (100, 20)),
        reply(),
    ])
    monkeypatch.setattr(server, "_client", stub)
    body = TestClient(server.app).post("/api/chat", json={"text": "hello"}).json()
    row = rows("SELECT input_tokens, model_calls FROM traces WHERE id = ?", body["trace_id"])[0]
    assert row == (1100, 2)    # router 500, turn 100, consolidation 500
    assert rows("SELECT COUNT(*) FROM traces")[0][0] == 1
    assert flagged() == 12


def test_the_repl_hook_runs_before_finish(monkeypatch):
    # Same, for agent.main(). Fails if the hook is missing or after finish().
    from ninja import agent

    talk("assistant", 6)
    stub = StubClient([
        raw("assistant"),
        response([block(type="text", text="hi")], "end_turn", (100, 20)),
        reply(),
    ])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)
    lines = iter(["hello"])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)
    agent.main()
    assert rows("SELECT input_tokens, model_calls FROM traces") == [(1100, 2)]
    assert flagged() == 12


def test_the_receipt_prints_a_consolidation_event(capsys):
    # Fails if print_one treats the event as a tool call (KeyError on "name").
    turn = trace.Trace("x")
    turn.consolidation(False, "call failed: net", 3)
    trace.print_one(turn.finish("r"))
    assert "consolidate FAILED" in capsys.readouterr().out
