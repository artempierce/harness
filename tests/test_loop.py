"""The loop. These tests are why layer 2 can be trusted without spending money."""

from ninja import agent, trace

from .conftest import StubClient, a_persona, block, response


def text(t):
    return response([block(type="text", text=t)], "end_turn")


def tool_call(tool_id, name, args):
    return response([block(type="tool_use", id=tool_id, name=name, input=args)], "tool_use")


def test_answers_without_tools():
    client = StubClient([text("four")])
    messages = [{"role": "user", "content": "what is 2+2?"}]
    assert agent.run_turn(client, messages, trace.Trace("x"), a_persona()) == "four"
    assert len(client.seen) == 1


def test_loops_until_the_model_stops_asking():
    client = StubClient([
        tool_call("t1", "list_files", {"path": "."}),
        tool_call("t2", "read_file", {"path": "pyproject.toml"}),
        text("anthropic and python-dotenv."),
    ])
    messages = [{"role": "user", "content": "what does this depend on?"}]
    reply = agent.run_turn(client, messages, trace.Trace("x"), a_persona())

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
    agent.run_turn(client, messages, trace.Trace("x"), a_persona())

    results = messages[2]
    assert results["role"] == "user"
    assert len(results["content"]) == 2
    assert {r["tool_use_id"] for r in results["content"]} == {"a", "b"}


def test_a_failing_tool_comes_back_as_an_error_not_a_crash():
    client = StubClient([tool_call("t1", "read_file", {"path": ".env"}),
                         text("I can't read that.")])
    messages = [{"role": "user", "content": "read my .env"}]
    turn = trace.Trace("x")
    assert agent.run_turn(client, messages, turn, a_persona()) == "I can't read that."

    result = messages[2]["content"][0]
    assert result["is_error"] is True
    assert "hidden files" in result["content"]
    assert turn.events[1]["ok"] is False


def test_the_step_cap_stops_a_runaway_loop():
    client = StubClient([tool_call(f"t{i}", "list_files", {"path": "."})
                         for i in range(agent.MAX_STEPS + 5)])
    messages = [{"role": "user", "content": "go forever"}]
    reply = agent.run_turn(client, messages, trace.Trace("x"), a_persona())

    assert "guardrail" in reply
    assert len(client.seen) == agent.MAX_STEPS


def test_the_turn_is_traced():
    client = StubClient([tool_call("t1", "list_files", {"path": "."}), text("done")])
    turn = trace.Trace("list please")
    agent.run_turn(client, [{"role": "user", "content": "list please"}], turn, a_persona())

    kinds = [e["type"] for e in turn.events]
    assert kinds == ["model", "tool", "model"]
    assert turn.input_tokens == 200


def test_the_request_carries_only_the_personas_tools():
    client = StubClient([text("ok")])
    coach = a_persona(tools=("read_file", "remember"))
    agent.run_turn(client, [{"role": "user", "content": "hi"}], trace.Trace("x"), coach)

    sent = [t["name"] for t in client.seen[0]["tools"]]
    assert sent == ["read_file", "remember"]


def test_the_personas_model_is_the_one_called():
    client = StubClient([text("ok")])
    coach = a_persona(model="claude-sonnet-5")
    agent.run_turn(client, [{"role": "user", "content": "hi"}], trace.Trace("x"), coach)

    assert client.seen[0]["model"] == "claude-sonnet-5"


def test_the_personas_instructions_are_the_system_prompt():
    client = StubClient([text("ok")])
    coach = a_persona(instructions="Coach, do not lecture.")
    agent.run_turn(client, [{"role": "user", "content": "hi"}], trace.Trace("x"), coach)

    assert client.seen[0]["system"] == "Coach, do not lecture."


def test_a_tool_outside_the_allowlist_comes_back_as_an_error():
    # The model asks for a tool this persona does not hold. The turn must
    # survive it — the refusal is a result the model can explain, not a crash.
    client = StubClient([tool_call("t1", "remember", {"fact": "x"}),
                         text("I can't store that.")])
    coach = a_persona(tools=("read_file",))
    messages = [{"role": "user", "content": "remember this"}]
    assert agent.run_turn(client, messages, trace.Trace("x"), coach) == "I can't store that."

    result = messages[2]["content"][0]
    assert result["is_error"] is True
    assert "allowlist" in result["content"]


def test_two_personas_in_one_turn_do_not_share_state():
    first = a_persona(name="a", tools=("read_file",), model="m-a")
    second = a_persona(name="b", tools=("remember",), model="m-b")

    c1, c2 = StubClient([text("1")]), StubClient([text("2")])
    agent.run_turn(c1, [{"role": "user", "content": "x"}], trace.Trace("x"), first)
    agent.run_turn(c2, [{"role": "user", "content": "y"}], trace.Trace("y"), second)

    assert [t["name"] for t in c1.seen[0]["tools"]] == ["read_file"]
    assert [t["name"] for t in c2.seen[0]["tools"]] == ["remember"]
    assert c1.seen[0]["model"] == "m-a"
    assert c2.seen[0]["model"] == "m-b"
