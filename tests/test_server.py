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
