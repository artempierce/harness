"""The loop. These tests are why layer 2 can be trusted without spending money."""

from ninja import agent, trace

from .conftest import StubClient, block, response


def text(t):
    return response([block(type="text", text=t)], "end_turn")


def tool_call(tool_id, name, args):
    return response([block(type="tool_use", id=tool_id, name=name, input=args)], "tool_use")


def test_answers_without_tools():
    client = StubClient([text("four")])
    messages = [{"role": "user", "content": "what is 2+2?"}]
    assert agent.run_turn(client, messages, trace.Trace("x")) == "four"
    assert len(client.seen) == 1


def test_loops_until_the_model_stops_asking():
    client = StubClient([
        tool_call("t1", "list_files", {"path": "."}),
        tool_call("t2", "read_file", {"path": "pyproject.toml"}),
        text("anthropic and python-dotenv."),
    ])
    messages = [{"role": "user", "content": "what does this depend on?"}]
    reply = agent.run_turn(client, messages, trace.Trace("x"))

    assert reply == "anthropic and python-dotenv."
    assert len(client.seen) == 3
    # The whole array is re-sent every round, and it grows by two each time:
    # the model's request, then our result.
    assert [len(call["messages"]) for call in client.seen] == [1, 3, 5]


def test_tool_results_go_back_as_one_user_message():
    # Splitting them across messages teaches the model to stop asking for
    # parallel calls.
    client = StubClient([
        response([block(type="tool_use", id="a", name="list_files", input={"path": "."}),
                  block(type="tool_use", id="b", name="list_files", input={"path": "ninja"})],
                 "tool_use"),
        text("done"),
    ])
    messages = [{"role": "user", "content": "list both"}]
    agent.run_turn(client, messages, trace.Trace("x"))

    results = messages[2]
    assert results["role"] == "user"
    assert len(results["content"]) == 2
    assert {r["tool_use_id"] for r in results["content"]} == {"a", "b"}


def test_a_failing_tool_comes_back_as_an_error_not_a_crash():
    client = StubClient([tool_call("t1", "read_file", {"path": ".env"}),
                         text("I can't read that.")])
    messages = [{"role": "user", "content": "read my .env"}]
    turn = trace.Trace("x")
    assert agent.run_turn(client, messages, turn) == "I can't read that."

    result = messages[2]["content"][0]
    assert result["is_error"] is True
    assert "hidden files" in result["content"]
    assert turn.events[1]["ok"] is False


def test_the_step_cap_stops_a_runaway_loop():
    client = StubClient([tool_call(f"t{i}", "list_files", {"path": "."})
                         for i in range(agent.MAX_STEPS + 5)])
    messages = [{"role": "user", "content": "go forever"}]
    reply = agent.run_turn(client, messages, trace.Trace("x"))

    assert "guardrail" in reply
    assert len(client.seen) == agent.MAX_STEPS


def test_the_turn_is_traced():
    client = StubClient([tool_call("t1", "list_files", {"path": "."}), text("done")])
    turn = trace.Trace("list please")
    agent.run_turn(client, [{"role": "user", "content": "list please"}], turn)

    kinds = [e["type"] for e in turn.events]
    assert kinds == ["model", "tool", "model"]
    assert turn.input_tokens == 200
