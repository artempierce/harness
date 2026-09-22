from ninja import trace

from .conftest import block, response


def test_cost_is_computed_per_model():
    # Haiku 4.5 is $1/$5 per million.
    assert trace.price("claude-haiku-4-5", 1_000_000, 0) == 1.00
    assert trace.price("claude-haiku-4-5", 0, 1_000_000) == 5.00
    # An unknown model reports zero rather than guessing.
    assert trace.price("some-future-model", 1_000_000, 1_000_000) == 0.0


def test_turn_is_recorded_with_totals():
    t = trace.Trace("hello")
    t.model("claude-haiku-4-5", response([], "tool_use", (400, 50)), 800)
    t.tool("read_file", {"path": "x"}, True, "contents", 3)
    t.model("claude-haiku-4-5", response([], "end_turn", (500, 30)), 600)
    trace_id = t.finish("hi")

    assert t.input_tokens == 900
    assert t.output_tokens == 80
    assert len(t.events) == 3

    conn = trace.connect()
    row = conn.execute("SELECT model_calls, input_tokens, reply FROM traces WHERE id = ?",
                       (trace_id,)).fetchone()
    conn.close()
    assert row == (2, 900, "hi")


def test_large_tool_output_is_truncated():
    t = trace.Trace("read something big")
    t.tool("read_file", {"path": "big"}, True, "x" * 50_000, 4)
    event = t.events[0]
    # The shape is kept, the payload is not — otherwise traces become the
    # biggest thing in the database.
    assert event["bytes"] == 50_000
    assert len(event["preview"]) == 200


def test_an_unpriced_model_is_flagged_rather_than_counted_as_free():
    # PRICING is keyed by model id, and a persona file can name any model at
    # all. Zero is the honest number for a rate we do not have, but on the
    # dashboard zero reads as "this turn cost nothing" — so the event says
    # which it is.
    t = trace.Trace("x")
    t.model("claude-next-9", response([], "end_turn", (1_000_000, 1_000_000)), 10)
    assert t.cost == 0.0
    assert t.events[0]["unpriced"] is True


def test_a_priced_model_is_not_flagged():
    t = trace.Trace("x")
    t.model("claude-haiku-4-5", response([], "end_turn", (100, 10)), 10)
    assert t.events[0]["unpriced"] is False


def test_skills_event_records_the_matched_names():
    t = trace.Trace("weekly review please")
    t.skills(["weekly-review"])
    assert t.events[0] == {"type": "skills", "names": ["weekly-review"]}


def test_the_trace_viewer_prints_every_kind_of_event(capsys):
    # A trace holds three kinds of event, and a renderer that knows two of them
    # does not fail — it draws the third as whatever its fallback branch is.
    # That is exactly what the cockpit did with the gate step from layer 5 on:
    # a phantom failed tool call, undefined(undefined), at the top of every
    # trace anyone opened. `ninja trace <id>` is the same three branches in
    # Python, and nothing covered it.
    t = trace.Trace("what am I building?")
    t.gate(True, "1 fact(s) matched", 1)
    t.skills(["weekly-review"])
    t.judge("replies in one word", True)
    t.route("interview-coach", "assistant", "moved on 'quiz me'", "claude-haiku-4-5",
             response([block(type="text", text="interview-coach")], "end_turn"), 12)
    t.model("claude-haiku-4-5", response([], "tool_use", (120, 18)), 340, "assistant")
    t.tool("read_file", {"path": "README.md"}, True, "# Ninja", 2)
    t.model("claude-haiku-4-5", response([], "end_turn", (200, 30)), 410, "assistant")
    trace_id = t.finish("An agent harness.")

    trace.print_one(trace_id)
    out = capsys.readouterr().out

    for expected in ["gate", "retrieve 1", "1 fact(s) matched", "model",
                      "read_file", "assistant", "An agent harness.",
                      "skills", "weekly-review", "judge", "PASS",
                      "replies in one word"]:
        assert expected in out, expected
    assert "route" in out
    assert "assistant → interview-coach" in out
    # An event that fell through to a branch meant for another kind shows up as
    # a missing key here rather than as a plausible-looking line.
    assert "None" not in out


def test_the_viewer_says_when_a_call_was_not_priced(capsys):
    # The flag exists so the shortfall is readable afterwards. A flag nothing
    # renders is a flag nobody sees.
    t = trace.Trace("x")
    t.model("claude-next-9", response([], "end_turn", (1000, 100)), 10, "assistant")
    trace.print_one(t.finish("hi"))
    assert "unpriced" in capsys.readouterr().out


def test_the_viewer_shows_a_failed_judge_verdict(capsys):
    t = trace.Trace("x")
    t.judge("mentions ninja", False)
    trace.print_one(t.finish("no mention"))
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "mentions ninja" in out
