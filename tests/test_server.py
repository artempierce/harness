from fastapi.testclient import TestClient

from ninja import server

client = TestClient(server.app)


def test_the_page_is_served():
    page = client.get("/")
    assert page.status_code == 200
    assert "ninja cockpit" in page.text


def test_panels_respond_with_data_rather_than_an_error_inside_a_200():
    # A status code is not an answer. The first dashboard bug in this project
    # rendered the string "undefined" because fetch does not reject on a 4xx;
    # an error carried inside a 200 is the same failure one step earlier, and
    # a test that reads only the status cannot see either. Two panels were
    # also missing from this list.
    for path in ["/api/stats", "/api/traces", "/api/tools", "/api/guardrails",
                 "/api/system", "/api/memory", "/api/personas", "/api/facts"]:
        reply = client.get(path)
        assert reply.status_code == 200, path
        body = reply.json()
        assert body is not None, path
        assert not (isinstance(body, dict) and "error" in body), path


def test_tools_panel_matches_the_real_tools():
    from ninja import tools
    names = [t["name"] for t in client.get("/api/tools").json()]
    assert names == [t["name"] for t in tools.SCHEMAS]


def test_guardrails_panel_reports_the_live_step_cap():
    from ninja import agent
    rails = client.get("/api/guardrails").json()
    step_cap = next(r for r in rails if r["name"] == "Step cap")
    # The panel must read the running value, not a copy that can drift.
    assert str(agent.MAX_STEPS) in step_cap["value"]
    assert step_cap["live"] is True


def test_missing_trace_is_handled():
    assert "error" in client.get("/api/traces/99999").json()


class ExplodingClient:
    """A model client that fails the way a network blip or a 529 does."""

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        raise RuntimeError("upstream is down")


def test_a_failed_turn_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "client", ExplodingClient)
    reply = client.post("/api/chat", json={"text": "hi"})
    assert reply.status_code == 502
    assert "upstream is down" in reply.json()["detail"]


def test_a_failed_turn_does_not_poison_working_memory(monkeypatch):
    # run_turn appends as it goes. If the live list keeps a half-finished turn,
    # the next request carries a tool_use block with no matching tool_result and
    # the API rejects every turn from then on, until restart.
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "client", ExplodingClient)
    client.post("/api/chat", json={"text": "hi"})
    assert server.messages == []


def test_a_good_turn_still_grows_working_memory(monkeypatch):
    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="four")], "end_turn")])
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "client", lambda: stub)
    body = client.post("/api/chat", json={"text": "what is 2+2?"}).json()
    assert body["reply"] == "four"
    assert body["working_memory"] == 2
    assert len(server.messages) == 2


def test_the_personas_panel_lists_the_cast():
    body = client.get("/api/personas").json()
    names = sorted(p["name"] for p in body["personas"])
    assert names == ["assistant", "interview-coach"]
    assert body["active"] == "assistant"


def test_the_panel_reports_the_tools_the_harness_enforces():
    # The panel must not claim a capability the allowlist does not grant.
    from ninja import personas

    body = client.get("/api/personas").json()
    shown = {p["name"]: p["tools"] for p in body["personas"]}
    assert shown["interview-coach"] == list(personas.load("interview-coach").tools)


def test_setting_the_persona_changes_the_default(monkeypatch):
    monkeypatch.setattr(server, "active", "assistant")
    assert client.post("/api/persona", json={"name": "interview-coach"}).json() == {
        "active": "interview-coach"
    }
    assert client.get("/api/personas").json()["active"] == "interview-coach"


def test_an_unknown_persona_is_a_400(monkeypatch):
    monkeypatch.setattr(server, "active", "assistant")
    assert client.post("/api/persona", json={"name": "nonesuch"}).status_code == 400
    # The active persona is unchanged by a failed switch.
    assert server.active == "assistant"


def test_a_chat_request_can_name_its_persona(monkeypatch):
    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "active", "assistant")
    monkeypatch.setattr(server, "client", lambda: stub)

    client.post("/api/chat", json={"text": "hi", "persona": "interview-coach"})

    assert [t["name"] for t in stub.seen[0]["tools"]] == ["read_file", "remember"]
    # Naming a persona on a request also makes it the default for the next one.
    assert server.active == "interview-coach"


def test_system_panel_no_longer_carries_a_personas_stub():
    # One source for one fact. /api/personas is the panel's source.
    assert "personas" not in client.get("/api/system").json()


def test_a_broken_persona_file_is_reported_with_its_reason(monkeypatch, tmp_path):
    # An uncaught load error reaches the browser as a bare 500, and the panel
    # can only say that something went wrong. The file and the reason have to
    # survive into the body.
    from ninja import personas

    monkeypatch.setattr(personas, "DIR", tmp_path)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "PERSONA.md").write_text("---\nname: broken\n---\n\nbody\n")

    reply = client.get("/api/personas")
    assert reply.status_code == 500
    assert "broken" in reply.json()["detail"]


def test_a_switch_that_lands_mid_turn_reaches_neither_this_turn_nor_the_next(monkeypatch):
    # The claim layer 7 rests on: a turn holds a resolved Persona, so a switch
    # arriving while it is on step 1 of 2 cannot change the toolset underneath
    # it — and, the other half, the turn must not undo the switch on its way
    # out.
    from .conftest import StubClient, block, response

    class SwitchesMidTurn(StubClient):
        def create(self, **kw):
            if not self.seen:
                # Exactly what POST /api/persona does, minus the re-entrant
                # request a TestClient cannot make from inside a handler.
                server.active = "interview-coach"
            return super().create(**kw)

    stub = SwitchesMidTurn([
        response([block(type="tool_use", id="t1", name="list_files", input={"path": "."})],
                 "tool_use"),
        response([block(type="text", text="done")], "end_turn"),
    ])
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "active", "assistant")
    monkeypatch.setattr(server, "client", lambda: stub)

    client.post("/api/chat", json={"text": "list the files"})

    # Both model calls ran as the persona the turn started with.
    for call in stub.seen:
        assert [t["name"] for t in call["tools"]] == ["list_files", "read_file", "remember"]
    assert server.active == "interview-coach"


def test_the_allowlist_is_reported_as_enforced():
    # It shipped in this layer. The panel went on listing it beside the layer-8
    # and layer-13 rails as something that had not arrived yet, in a cockpit
    # whose own Growth tab marks layer 7 built.
    rails = client.get("/api/guardrails").json()
    rail = next(r for r in rails if r["name"] == "Tool allowlist")
    assert rail["live"] is True
    assert "layer" not in rail


def test_the_system_panel_no_longer_claims_one_model_for_the_harness():
    # Since layer 7 a model belongs to a persona. A second copy of that fact is
    # how the brand line ends up naming a model no turn has run on.
    assert "model" not in client.get("/api/system").json()
    cast = client.get("/api/personas").json()
    active = next(p for p in cast["personas"] if p["name"] == cast["active"])
    assert active["model"]


def test_a_chat_naming_an_unknown_persona_is_refused_before_the_model_is_called(monkeypatch):
    # The persona is resolved first, so a bad name must cost nothing: no
    # request, no message in the transcript, and no change to the default. A
    # 400 that has already spent a turn is a 400 you pay for.
    from .conftest import StubClient

    # An empty script: if a turn were attempted, create() would raise
    # IndexError rather than passing quietly.
    stub = StubClient([])
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "active", "assistant")
    monkeypatch.setattr(server, "client", lambda: stub)

    reply = client.post("/api/chat", json={"text": "hi", "persona": "nonesuch"})

    assert reply.status_code == 400
    assert "nonesuch" in reply.json()["detail"]
    assert stub.seen == []
    assert server.messages == []
    assert server.active == "assistant"


def test_a_failed_turn_leaves_nothing_in_episodic_memory(monkeypatch):
    # Working memory and the chat log have to fail together. A save that
    # happened before the loop returned would leave a question in the log with
    # no answer after it, and the next restart recalls that into the transcript
    # — the two stores disagreeing, which is invisible until a turn is refused.
    from ninja import episodic

    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "client", ExplodingClient)

    assert client.post("/api/chat", json={"text": "hi"}).status_code == 502
    assert episodic.history() == []


def test_a_good_turn_writes_both_halves_of_the_exchange_to_the_log(monkeypatch):
    # The other side of the same invariant: what the user saw is what the log
    # holds, both messages tied to the trace that produced them.
    from ninja import episodic

    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="four")], "end_turn")])
    monkeypatch.setattr(server, "messages", [])
    monkeypatch.setattr(server, "client", lambda: stub)

    body = client.post("/api/chat", json={"text": "what is 2+2?"}).json()
    logged = episodic.history()

    assert [row["role"] for row in logged] == ["assistant", "user"]  # newest first
    assert [row["content"] for row in logged] == ["four", "what is 2+2?"]
    assert {row["trace_id"] for row in logged} == {body["trace_id"]}


def test_the_stats_panel_counts_the_events_it_claims_to_count():
    # Every counter here is derived by re-parsing the events JSON of every
    # trace, so a renamed event type or a changed key does not raise — it
    # reports zero, and a zero on a dashboard reads as "this never happens"
    # rather than as "this is no longer being counted".
    from ninja import trace

    from .conftest import response

    first = trace.Trace("list the files")
    first.gate(False, "no fact matched", 0)
    first.model("claude-haiku-4-5", response([], "tool_use", (100, 20)), 30)
    first.tool("list_files", {"path": "."}, True, "ninja", 1)
    first.model("claude-haiku-4-5", response([], "end_turn", (140, 25)), 40)
    first.finish("done")

    second = trace.Trace("what am I building?")
    second.gate(True, "1 fact(s) matched", 1)
    second.finish("Ninja")

    body = client.get("/api/stats").json()
    assert body["turns"] == 2
    assert body["tool_calls"] == 1
    assert body["gate_retrieve"] == 1
    assert body["gate_skip"] == 1
    assert body["input_tokens"] == 240
    assert body["cost"] > 0


def test_a_persona_file_with_broken_yaml_is_reported_with_its_reason(monkeypatch, tmp_path):
    # The parser's own error type is not a ValueError, so before the loader
    # translated it this escaped the panel's handler entirely and reached the
    # browser as an unhandled exception with no body at all.
    from ninja import personas

    monkeypatch.setattr(personas, "DIR", tmp_path)
    broken = tmp_path / "unclosed"
    broken.mkdir()
    (broken / "PERSONA.md").write_text(
        "---\nname: unclosed\ndescription: [oops\ntools: [read_file]\nmodel: m\n---\n\nbody\n"
    )

    reply = client.get("/api/personas")
    assert reply.status_code == 500
    assert "unclosed" in reply.json()["detail"]
