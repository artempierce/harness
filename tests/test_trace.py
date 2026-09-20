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
