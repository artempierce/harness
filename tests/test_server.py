from fastapi.testclient import TestClient

from ninja import server

client = TestClient(server.app)


def test_the_page_is_served():
    page = client.get("/")
    assert page.status_code == 200
    assert "ninja cockpit" in page.text


def test_panels_respond():
    for path in ["/api/stats", "/api/traces", "/api/tools", "/api/guardrails",
                 "/api/system", "/api/memory"]:
        assert client.get(path).status_code == 200, path


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
