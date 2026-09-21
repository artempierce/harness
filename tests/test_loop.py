"""The loop. These tests are why layer 2 can be trusted without spending money."""

import pytest

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



def test_a_harness_bug_is_not_disguised_as_a_tool_error(monkeypatch):
    # A wrong signature or a bug inside a tool is ours, not the model's. Filed
    # as a tool_result it is indistinguishable from a tool legitimately
    # refusing, and the loop carries on around it.
    def broken(name, args, allowed):
        raise TypeError("run() got an unexpected keyword argument 'depth'")

    monkeypatch.setattr(agent.tools, "run", broken)
    client = StubClient([tool_call("t1", "list_files", {"path": "."}), text("done")])
    with pytest.raises(TypeError):
        agent.run_turn(client, [{"role": "user", "content": "x"}], trace.Trace("x"), a_persona())


def test_a_missing_argument_is_still_the_models_mistake():
    # The other side of the same boundary: the model omitted a required field,
    # which it can see and correct, so it comes back as a result not a crash.
    client = StubClient([tool_call("t1", "read_file", {}), text("Sorry, my mistake.")])
    messages = [{"role": "user", "content": "read it"}]
    assert agent.run_turn(client, messages, trace.Trace("x"), a_persona()) == "Sorry, my mistake."
    assert messages[2]["content"][0]["is_error"] is True


def test_the_trace_records_which_persona_ran_the_call():
    # Model and instructions are per-turn from layer 7 on, and per-turn from
    # layer 8 on within a single trace. A trace that records neither cannot say
    # what the coach cost or which persona made the call that went wrong.
    client = StubClient([text("ok")])
    turn = trace.Trace("x")
    coach = a_persona(name="interview-coach", model="claude-sonnet-5")
    agent.run_turn(client, [{"role": "user", "content": "hi"}], turn, coach)

    assert turn.events[0]["persona"] == "interview-coach"
    assert turn.events[0]["model"] == "claude-sonnet-5"


def test_the_step_cap_leaves_a_transcript_the_next_turn_can_use():
    # The reply the user was shown has to be in the transcript as well. Without
    # it the conversation ends on a batch of tool results, and the next message
    # is appended after them with no assistant turn in between.
    client = StubClient([tool_call(f"t{i}", "list_files", {"path": "."})
                         for i in range(agent.MAX_STEPS + 5)])
    messages = [{"role": "user", "content": "go forever"}]
    reply = agent.run_turn(client, messages, trace.Trace("x"), a_persona())

    assert messages[-1] == {"role": "assistant", "content": reply}


def test_a_non_string_tool_argument_comes_back_as_an_error_not_a_crash():
    client = StubClient([tool_call("t1", "read_file", {"path": 5}), text("My mistake.")])
    messages = [{"role": "user", "content": "read it"}]
    assert agent.run_turn(client, messages, trace.Trace("x"), a_persona()) == "My mistake."

    result = messages[2]["content"][0]
    assert result["is_error"] is True
    assert "must be a string" in result["content"]


def test_a_reply_with_no_text_is_not_returned_empty():
    # "" gets saved as the assistant's message and replayed on every later turn
    # in that thread. The API rejects an empty text block, so the thread would
    # fail until the message scrolled out of the recall window.
    client = StubClient([response([], "end_turn")])
    messages = [{"role": "user", "content": "hello"}]
    reply = agent.run_turn(client, messages, trace.Trace("x"), a_persona())

    assert reply.strip() != ""
    assert "end_turn" in reply


@pytest.mark.parametrize("reason", ["max_tokens", "refusal", "pause_turn"])
def test_an_abnormal_stop_is_marked_not_passed_off_as_a_finished_reply(reason):
    # A reply cut off at the token cap, refused, or paused reads as a complete
    # answer if the text alone is returned — and it is saved and replayed as
    # one. The reason has to be in the text the user and the thread see.
    client = StubClient([response([block(type="text", text="The answer is")], reason)])
    messages = [{"role": "user", "content": "what is it?"}]
    reply = agent.run_turn(client, messages, trace.Trace("x"), a_persona())

    assert reply.startswith("The answer is")
    assert reason in reply


@pytest.mark.parametrize("reason", ["end_turn", "stop_sequence"])
def test_a_normal_stop_is_returned_unmarked(reason):
    client = StubClient([response([block(type="text", text="Done.")], reason)])
    messages = [{"role": "user", "content": "hi"}]

    assert agent.run_turn(client, messages, trace.Trace("x"), a_persona()) == "Done."
