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
