"""Layer 8b: delegation. Scripted replies for parent and child; no live API."""

import json

import pytest
from fastapi.testclient import TestClient

from ninja import agent, episodic, personas, rules, semantic, server, tools, trace

from .conftest import StubClient, a_persona, block, response


def text(t, tokens=(100, 20)):
    return response([block(type="text", text=t)], "end_turn", tokens)


def call(tool_id, name, args, tokens=(100, 20)):
    return response(
        [block(type="tool_use", id=tool_id, name=name, input=args)], "tool_use", tokens
    )


def delegate_call(tool_id, persona, task="do the job", tokens=(100, 20)):
    return call(tool_id, "delegate", {"persona": persona, "task": task}, tokens)


BOSS = ("read_file", "list_files", "remember", "add_rule", "delegate")


def boss(**kw):
    return a_persona(name="boss", instructions="BOSS-INSTRUCTIONS", tools=BOSS, **kw)


@pytest.fixture
def cast(tmp_path, monkeypatch):
    """A throwaway personas/ and rules/, so no test depends on the real cast."""
    root = tmp_path / "personas"
    root.mkdir()
    monkeypatch.setattr(personas, "DIR", root)
    monkeypatch.setattr(rules, "DIR", tmp_path / "rules")

    def add(name, tools, body="Instructions for {name}.", description="Does a job."):
        d = root / name
        d.mkdir()
        (d / "PERSONA.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n"
            f"tools: [{', '.join(tools)}]\nmodel: claude-haiku-4-5\n---\n\n"
            f"{body.format(name=name)}\n"
        )

    return add


def results_of(call_record):
    """The tool results the model was shown in this request."""
    return call_record["messages"][-1]["content"]


def run(client, persona=None, depth=0, messages=None, t=None):
    messages = messages if messages is not None else [{"role": "user", "content": "go"}]
    t = t or trace.Trace("go")
    reply = agent.run_turn(client, messages, t, persona or boss(), "PARENT-SYSTEM", depth)
    return reply, messages, t


# --- context isolation -------------------------------------------------------
# Fails against: passing the parent's messages (or system prompt, or facts) down.


def test_the_child_sees_only_the_task(cast):
    cast("helper", ["read_file"], body="HELPER-INSTRUCTIONS")
    client = StubClient([
        delegate_call("d1", "helper", "count the files"),
        text("three"),
        text("it found three"),
    ])
    history = [
        {"role": "user", "content": "PARENT-HISTORY-QUESTION"},
        {"role": "assistant", "content": "PARENT-HISTORY-ANSWER"},
        {"role": "user", "content": "go"},
    ]
    parent_system = "PARENT-SYSTEM with fact: SECRET-FACT"
    reply = agent.run_turn(client, history, trace.Trace("go"), boss(), parent_system)

    assert reply == "it found three"
    child_call = client.seen[1]
    assert child_call["messages"] == [{"role": "user", "content": "count the files"}]
    assert child_call["system"] == "HELPER-INSTRUCTIONS"
    for leaked in ("PARENT-HISTORY", "SECRET-FACT", "PARENT-SYSTEM", "BOSS-INSTRUCTIONS"):
        assert leaked not in repr(child_call)
    # And the answer comes back as the tool result.
    assert results_of(client.seen[2])[0]["content"] == "three"


def test_the_child_gets_its_own_learned_rules_and_no_facts(cast, monkeypatch):
    # Fails against: reusing build_system for the child (retrieved facts, gate call)
    # or dropping the child's rules.
    cast("helper", ["read_file"], body="HELPER-INSTRUCTIONS")
    rules.add_rule("helper", "Answer in one line.")
    semantic.remember("The user is called Ninja-Fact-Marker.")
    t = trace.Trace("go")
    client = StubClient([delegate_call("d1", "helper", "Ninja-Fact-Marker?"), text("x"), text("y")])
    agent.run_turn(client, [{"role": "user", "content": "go"}], t, boss(), "S")
    system = client.seen[1]["system"]
    assert system.startswith("HELPER-INSTRUCTIONS")
    assert "- Answer in one line." in system
    assert "know about this person" not in system
    assert not any(e["type"] == "gate" for e in t.events)


# --- subset rule -------------------------------------------------------------


def test_a_child_with_a_tool_the_parent_lacks_is_refused_and_never_called(cast):
    # Fails against: no subset check, or clamping the child instead of refusing.
    cast("wide", ["read_file", "list_files"], body="WIDE-INSTRUCTIONS")
    parent = a_persona(name="narrow", tools=("read_file", "delegate"))
    client = StubClient([delegate_call("d1", "wide"), text("ok, I will not")])
    reply, *_ = run(client, parent)

    assert reply == "ok, I will not"
    assert len(client.seen) == 2, "the refused child made an API call"
    (result,) = results_of(client.seen[1])
    assert result["is_error"] is True
    assert "list_files" in result["content"]
    assert "read_file" not in result["content"], "only the excess is named"
    assert all("WIDE-INSTRUCTIONS" not in c["system"] for c in client.seen)


def test_a_child_whose_tools_fit_runs(cast):
    cast("narrow", ["read_file"])
    client = StubClient([delegate_call("d1", "narrow"), text("done"), text("fine")])
    reply, *_ = run(client)
    assert reply == "fine"
    assert results_of(client.seen[2])[0]["is_error"] is False


def test_the_rule_is_checked_on_effective_tools_not_declared_ones(cast):
    # Fails against: comparing child.tools before the write tools are removed.
    # This child declares remember, which the parent lacks, but can never call it.
    cast("declares-writes", ["read_file", "remember", "add_rule"])
    parent = a_persona(name="reader", tools=("read_file", "delegate"))
    client = StubClient([delegate_call("d1", "declares-writes"), text("ok"), text("fine")])
    run(client, parent)
    assert results_of(client.seen[2])[0]["is_error"] is False


# --- depth -------------------------------------------------------------------


def test_depth_two_runs_but_depth_three_is_refused_and_depth_two_cannot_delegate(cast):
    # Fails against: no MAX_DEPTH check; not stripping delegate at MAX_DEPTH.
    cast("mid", ["read_file", "delegate"])
    cast("leaf", ["read_file", "delegate"])
    client = StubClient([
        delegate_call("d1", "mid", "mid task"),
        delegate_call("d2", "leaf", "leaf task"),
        # The depth-2 model tries to delegate anyway, by name.
        delegate_call("d3", "leaf", "too deep"),
        text("leaf done"),
        text("mid done"),
        text("boss done"),
    ])
    reply, *_ = run(client)

    assert reply == "boss done"
    leaf_call = client.seen[2]
    assert leaf_call["messages"] == [{"role": "user", "content": "leaf task"}]
    assert [t["name"] for t in leaf_call["tools"]] == ["read_file"]
    (refused,) = results_of(client.seen[3])
    assert refused["is_error"] and "allowlist" in refused["content"]
    assert len(client.seen) == 6, "a depth-3 loop must never start"


def test_a_delegation_that_would_be_depth_three_is_refused_by_the_depth_check(cast):
    # A persona run at depth 2 that still holds delegate (only reachable when the
    # caller skips the reduction) is stopped by the depth check itself.
    # Fails against: relying on the strip alone.
    cast("leaf", ["read_file"])
    client = StubClient([delegate_call("d1", "leaf"), text("ok")])
    reply, *_ = run(client, depth=2)
    assert reply == "ok"
    (result,) = results_of(client.seen[1])
    assert result["is_error"] and "levels deep" in result["content"]
    assert len(client.seen) == 2


def test_a_depth_two_child_cannot_call_delegate_through_the_dispatcher():
    # Fails against: reducing the schemas but not the allowlist that tools.run reads.
    child = agent._reduced(a_persona(tools=BOSS), 2)
    assert "delegate" not in child.tools
    assert "delegate" not in [s["name"] for s in child.schemas()]
    with pytest.raises(ValueError, match="allowlist"):
        tools.run("delegate", {"persona": "x", "task": "y"}, child.tools, spawn=_boom)


# --- read-only children ------------------------------------------------------


def test_a_child_cannot_write_even_when_its_persona_declares_the_tools(cast):
    # Fails against: an empty WRITE_TOOLS. Asserted at the dispatcher, and by the
    # absence of the side effect, not only by what schemas were sent.
    cast("writer", ["read_file", "remember", "add_rule"])
    client = StubClient([
        delegate_call("d1", "writer"),
        response(
            [
                block(type="tool_use", id="w1", name="remember", input={"fact": "leaked"}),
                block(type="tool_use", id="w2", name="add_rule", input={"rule": "leaked"}),
            ],
            "tool_use",
        ),
        text("could not"),
        text("done"),
    ])
    run(client)

    assert [t["name"] for t in client.seen[1]["tools"]] == ["read_file"]
    errors = results_of(client.seen[2])
    assert [e["is_error"] for e in errors] == [True, True]
    assert all("allowlist" in e["content"] for e in errors)
    assert semantic.count() == 0
    assert rules.rules_for("writer") == ""


# --- per-turn cap ------------------------------------------------------------


def test_the_fourth_delegation_in_a_turn_is_refused(cast):
    # Fails against: no counter, or a counter that resets per loop.
    cast("helper", ["read_file"])
    four = response(
        [
            block(type="tool_use", id=f"d{i}", name="delegate",
                  input={"persona": "helper", "task": f"job {i}"})
            for i in range(4)
        ],
        "tool_use",
    )
    client = StubClient([four, text("a"), text("b"), text("c"), text("all done")])
    reply, *_ = run(client)

    assert reply == "all done"
    results = results_of(client.seen[-1])
    assert [r["is_error"] for r in results] == [False, False, False, True]
    assert "at most 3" in results[3]["content"]
    assert len(client.seen) == 5


def test_a_refused_delegation_does_not_use_up_the_cap(cast):
    # Fails against: counting before the checks pass.
    cast("helper", ["read_file"])
    client = StubClient([
        response(
            [block(type="tool_use", id=f"d{i}", name="delegate",
                   input={"persona": name, "task": "t"})
             for i, name in enumerate(["nope"] * 3 + ["helper"])],
            "tool_use",
        ),
        text("a"),
        text("end"),
    ])
    run(client)
    assert [r["is_error"] for r in results_of(client.seen[-1])] == [True, True, True, False]


# --- budget ------------------------------------------------------------------

# 300k input tokens on haiku is $0.30, past the $0.25 ceiling in one call.
EXPENSIVE = (300_000, 0)
STOPPED = "[stopped: turn budget of $0.25 reached]"


def valid_transcript(messages):
    roles = [m["role"] for m in messages]
    return roles[-1] == "assistant" and all(a != b for a, b in zip(roles, roles[1:], strict=False))


def test_the_budget_stops_the_loop_before_the_next_call():
    # Fails against: no check, or a check after the call.
    client = StubClient([call("t1", "list_files", {"path": "."}, EXPENSIVE)])
    reply, messages, _ = run(client)
    assert reply == STOPPED
    assert len(client.seen) == 1
    assert messages[-1] == {"role": "assistant", "content": STOPPED}
    assert valid_transcript(messages)


def test_the_budget_applies_inside_a_child_and_ends_the_parent_too(cast):
    # Fails against: checking only at depth 0.
    cast("helper", ["read_file", "list_files"])
    client = StubClient([
        delegate_call("d1", "helper"),
        call("c1", "list_files", {"path": "."}, EXPENSIVE),
    ])
    reply, messages, _ = run(client)

    assert reply == STOPPED
    assert len(client.seen) == 2, "the child, then the parent, must both have stopped"
    assert messages[-1] == {"role": "assistant", "content": STOPPED}
    assert valid_transcript(messages)
    # The child's stop is what the parent read as the tool result.
    assert STOPPED in repr(messages[-2])


def test_a_cheap_turn_is_not_stopped():
    client = StubClient([text("hi", (1000, 100))])
    assert run(client)[0] == "hi"


# --- allowlist first, unknown names, malformed input -------------------------


def _boom(*_):
    raise AssertionError("spawn was reached")


def test_a_persona_without_delegate_cannot_spawn(cast):
    # Fails against: a delegate branch placed above the allowlist gate.
    cast("helper", ["read_file"])
    client = StubClient([delegate_call("d1", "helper"), text("ok")])
    reply, *_ = run(client, a_persona(tools=("read_file",)))
    assert reply == "ok"
    (result,) = results_of(client.seen[1])
    assert result["is_error"] and "allowlist" in result["content"]
    assert len(client.seen) == 2
    with pytest.raises(ValueError, match="allowlist"):
        tools.run("delegate", {}, ["read_file"], spawn=_boom)


def test_delegate_without_a_runtime_is_an_error_not_a_crash():
    with pytest.raises(ValueError, match="runtime"):
        tools.run("delegate", {"persona": "a", "task": "b"}, ["delegate"])


@pytest.mark.parametrize("name", ["nope", "../personas/assistant", "/etc/passwd", ".hidden", "a/b"])
def test_unknown_and_path_like_names_are_refused_before_any_file_is_read(name, cast, monkeypatch):
    # Fails against: a persona lookup that joins the name into a path first.
    def no_read(*_a, **_k):
        raise AssertionError("a persona file was read")

    monkeypatch.setattr(personas, "_parse", no_read)
    client = StubClient([delegate_call("d1", name), text("ok")])
    reply, *_ = run(client)
    assert reply == "ok"
    (result,) = results_of(client.seen[1])
    assert result["is_error"] is True
    assert len(client.seen) == 2


@pytest.mark.parametrize("args", [
    {}, {"persona": "helper"}, {"task": "x"},
    {"persona": None, "task": "x"}, {"persona": "helper", "task": None},
    {"persona": 123, "task": "x"}, {"persona": ["helper"], "task": "x"},
    {"persona": "helper", "task": 5}, {"persona": "helper", "task": "   "},
])
def test_malformed_delegate_input_is_a_tool_error_not_a_crash(args, cast):
    # Fails against: dict.get defaults (null slips through) or no type check
    # (Path(123) is a TypeError, which the loop deliberately does not catch).
    cast("helper", ["read_file"])
    client = StubClient([call("d1", "delegate", args), text("ok")])
    reply, *_ = run(client)
    assert reply == "ok"
    (result,) = results_of(client.seen[1])
    assert result["is_error"] is True
    assert len(client.seen) == 2
    with pytest.raises(ValueError):
        tools.run("delegate", args, ["delegate"], spawn=_boom)


def test_an_api_error_in_a_child_fails_the_turn_and_is_still_traced(cast):
    # Fails against: swallowing the child's exception into a tool result.
    cast("helper", ["read_file"])

    class Failing(StubClient):
        def create(self, **kw):
            if len(self.seen) == 1:
                self.seen.append(kw)
                raise RuntimeError("api down")
            return super().create(**kw)

    t = trace.Trace("go")
    with pytest.raises(RuntimeError, match="api down"):
        run(Failing([delegate_call("d1", "helper")]), t=t)
    (event,) = [e for e in t.events if e["type"] == "delegate"]
    assert event["ok"] is False


# --- the target list in the system prompt ------------------------------------


def test_delegation_targets_are_listed_only_when_eligible(cast):
    cast("fits", ["read_file"], description="A fitting helper.")
    cast("wide", ["read_file", "list_files"], description="Too wide.")
    cast("writer", ["read_file", "remember"], description="Only writes extra.")
    parent = a_persona(name="narrow", tools=("read_file", "delegate"))
    system = agent.build_system("hello", trace.Trace("hello"), parent)
    assert "- fits: A fitting helper." in system
    assert "- writer: Only writes extra." in system  # its remember is dropped, so it fits
    assert "wide" not in system
    assert "- narrow:" not in system


def test_a_persona_without_delegate_is_told_of_no_targets(cast):
    cast("fits", ["read_file"])
    system = agent.build_system("hello", trace.Trace("hello"), a_persona())
    assert "delegate" not in system


def test_a_depth_two_child_is_offered_no_targets(cast):
    cast("fits", ["read_file"])
    assert "fits" not in agent._instructions(boss(), agent.MAX_DEPTH)


# --- trace -------------------------------------------------------------------


def test_the_receipt_holds_every_depth_and_the_events_say_who_and_where(cast):
    # Fails against: cost recorded only for the depth-0 loop, or a second trace
    # for the child, or events without depth.
    cast("helper", ["read_file"])
    client = StubClient([
        delegate_call("d1", "helper", "T" * 300, (1000, 100)),
        text("child", (2000, 200)),
        text("parent", (3000, 300)),
    ])
    reply, _, t = run(client)

    assert t.input_tokens == 6000 and t.output_tokens == 600
    assert t.cost == pytest.approx(trace.price("claude-haiku-4-5", 6000, 600))
    models = [(e["persona"], e["depth"]) for e in t.events if e["type"] == "model"]
    assert models == [("boss", 0), ("helper", 1), ("boss", 0)]
    (d,) = [e for e in t.events if e["type"] == "delegate"]
    assert (d["persona"], d["depth"], d["ok"]) == ("helper", 1, True)
    assert len(d["task"]) == 200 and d["ms"] >= 0
    assert [e["depth"] for e in t.events if e["type"] == "tool"] == [0]

    t.finish(reply)
    conn = trace.connect()
    assert conn.execute("SELECT count(*), sum(model_calls) FROM traces").fetchone() == (1, 3)
    conn.close()


def test_print_one_indents_by_depth_and_renders_delegations_and_old_rows(capsys):
    t = trace.Trace("x")
    # Written before depth existed.
    t.events = [
        {"type": "model", "persona": "a", "model": "m", "ms": 1, "in": 1, "out": 1,
         "stop": "end_turn"},
        {"type": "tool", "name": "read_file", "args": {}, "ms": 1, "ok": True, "bytes": 1,
         "preview": "p"},
    ]
    old = t.finish("r")
    t2 = trace.Trace("y")
    t2.tool("read_file", {}, True, "o", 1, 0)
    t2.tool("read_file", {}, True, "o", 1, 1)
    t2.delegate("helper", 1, "the brief", True, 5)
    new = t2.finish("r")

    trace.print_one(old)
    trace.print_one(new)
    out = capsys.readouterr().out.splitlines()
    tool_lines = [line for line in out if "read_file" in line and "tool" in line]
    assert len(tool_lines) == 3
    assert tool_lines[1].index("tool") == tool_lines[0].index("tool")
    assert tool_lines[2].index("tool") == tool_lines[1].index("tool") + 2
    assert any("delegate" in line and "helper" in line and "the brief" in line for line in out)


def test_the_dashboard_has_a_branch_for_delegate_events():
    # The last new event type rendered as undefined(undefined) until it got one.
    page = (trace.ROOT / "ui" / "index.html").read_text()
    assert "e.type === 'delegate'" in page


# --- end to end through the server, with the real personas -------------------


def test_the_child_leaves_no_trace_in_the_conversation_log(monkeypatch):
    # Fails against: run_turn or the server writing the child's messages to chat_log.
    stub = StubClient([
        delegate_call("d1", "interview-coach", "CHILD-BRIEF-MARKER"),
        text("CHILD-ANSWER-MARKER"),
        text("here is what it said"),
    ])
    monkeypatch.setattr(server, "client", lambda: stub)
    http = TestClient(server.app)
    reply = http.post("/api/chat", json={"text": "ask the coach", "persona": "assistant"})

    assert reply.status_code == 200
    assert reply.json()["reply"] == "here is what it said"
    logged = [m["content"] for m in episodic.recall("assistant")]
    assert logged == ["ask the coach", "here is what it said"]
    conn = trace.connect()
    assert conn.execute("SELECT count(*) FROM chat_log").fetchone()[0] == 2
    # The real assistant may hand to the real coach, and the coach ran read-only.
    assert [t["name"] for t in stub.seen[1]["tools"]] == ["read_file"]
    assert stub.seen[1]["messages"] == [{"role": "user", "content": "CHILD-BRIEF-MARKER"}]
    events = json.loads(conn.execute("SELECT events FROM traces").fetchone()[0])
    conn.close()
    assert any(e["type"] == "delegate" and e["persona"] == "interview-coach" for e in events)


def test_the_assistant_offers_the_coach_and_the_coach_is_a_leaf():
    assistant = personas.load("assistant")
    coach = personas.load("interview-coach")
    assert "delegate" in assistant.tools
    assert "delegate" not in coach.tools
    system = agent._instructions(assistant, 0)
    assert "- interview-coach:" in system
    assert "- assistant:" not in system
