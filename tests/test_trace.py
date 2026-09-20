from ninja import trace

from .conftest import response


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
