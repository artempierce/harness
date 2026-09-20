"""Layer 6 (scaffold): the cockpit.

Chat on the right, the system on the left and in the middle. Everything the
middle shows comes out of the traces table — layer 3 is the data source, which
is why this could not have been built before it.
"""

import json
from pathlib import Path

import anthropic
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ninja import agent, episodic, semantic, tools, trace

UI = Path(__file__).resolve().parent.parent / "ui"

app = FastAPI(title="ninja cockpit")

# Seeded from episodic memory at import, so a restart picks the thread back up.
session = episodic.new_session()
messages: list = episodic.recall()
_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    # Built on first use, not at import, so `ninja trace` works without a key.
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def rows_to_dicts(cursor) -> list[dict]:
    keys = [c[0] for c in cursor.description]
    return [dict(zip(keys, row, strict=True)) for row in cursor.fetchall()]


class Message(BaseModel):
    text: str


@app.get("/")
def index():
    return FileResponse(UI / "index.html")


@app.post("/api/chat")
def chat(message: Message):
    messages.append({"role": "user", "content": message.text})
    turn = trace.Trace(message.text)
    reply = agent.run_turn(client(), messages, turn, agent.build_system(message.text, turn))
    trace_id = turn.finish(reply)
    episodic.save(session, "user", message.text, trace_id)
    episodic.save(session, "assistant", reply, trace_id)
    return {"reply": reply, "trace_id": trace_id, "working_memory": len(messages)}


@app.get("/api/traces")
def traces(limit: int = 30):
    conn = trace.connect()
    out = rows_to_dicts(
        conn.execute(
            "SELECT id, started_at, duration_ms, model_calls, input_tokens,"
            " output_tokens, cost_usd, user_input FROM traces"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    )
    conn.close()
    return out


@app.get("/api/traces/{trace_id}")
def one_trace(trace_id: int):
    conn = trace.connect()
    found = rows_to_dicts(
        conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,))
    )
    conn.close()
    if not found:
        return {"error": f"no trace {trace_id}"}
    found[0]["events"] = json.loads(found[0]["events"])
    return found[0]


@app.get("/api/stats")
def stats():
    conn = trace.connect()
    turns, cost, tin, tout, avg = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0), COALESCE(SUM(input_tokens), 0),"
        " COALESCE(SUM(output_tokens), 0), COALESCE(AVG(duration_ms), 0) FROM traces"
    ).fetchone()
    events = conn.execute("SELECT events FROM traces").fetchall()
    conn.close()
    parsed = [e for (blob,) in events for e in json.loads(blob)]
    tool_calls = sum(1 for e in parsed if e["type"] == "tool")
    gates = [e for e in parsed if e["type"] == "gate"]
    return {
        "turns": turns,
        "tool_calls": tool_calls,
        "cost": round(cost, 5),
        "input_tokens": tin,
        "output_tokens": tout,
        "avg_ms": int(avg),
        "working_memory": len(messages),
        "facts": semantic.count(),
        "gate_retrieve": sum(1 for g in gates if g["retrieve"]),
        "gate_skip": sum(1 for g in gates if not g["retrieve"]),
    }


# Every panel below reads the running system rather than a copy of it, so the
# cockpit cannot drift out of date with the code it describes.

@app.get("/api/tools")
def tools_panel():
    return [
        {
            "name": t["name"],
            # This is the text the model reads when deciding to call it — the
            # actual API the model programs against.
            "description": t["description"],
            "params": list(t["input_schema"]["properties"]),
            "writes": False,
        }
        for t in tools.SCHEMAS
    ]


@app.get("/api/guardrails")
def guardrails_panel():
    return [
        {"name": "Step cap", "value": f"MAX_STEPS = {agent.MAX_STEPS}",
         "stops": "One loop spinning forever", "live": True},
        {"name": "Path boundary", "value": str(tools.ROOT),
         "stops": "Tools reading outside the project", "live": True},
        {"name": "Hidden paths", "value": "refuse any segment starting with '.'",
         "stops": "Reading .env into the transcript", "live": True},
        {"name": "Retrieval gate", "value": f"top-{semantic.TOP_K}, stopword-filtered",
         "stops": "Paying context tokens on turns that need no facts", "live": True},
        {"name": "Tool allowlist", "value": "per persona", "layer": 7,
         "stops": "The tutor calling run_command", "live": False},
        {"name": "Depth cap", "value": "MAX_DEPTH", "layer": 8,
         "stops": "A → B → C → A delegation chains", "live": False},
        {"name": "Subset rule", "value": "child ⊆ parent", "layer": 8,
         "stops": "A child holding more privilege than its caller", "live": False},
        {"name": "Guarded set", "value": "allowlist over roots", "layer": 13,
         "stops": "The system editing its own enforcement", "live": False},
        {"name": "Cost ceiling", "value": "per-turn budget", "layer": 15,
         "stops": "A fan-out with no dollar bound", "live": False},
    ]


LAYERS = [
    (1, "Bare agent run", "One agent"), (2, "Loop, tools, stop condition", "One agent"),
    (3, "Tracing", "One agent"), (4, "Episodic memory", "One agent"),
    (5, "Semantic memory + gate", "One agent"), (6, "The dashboard", "Seeing it"),
    (7, "Personas", "The cast"), (8, "Delegation", "The cast"),
    (9, "The architect", "The cast"), (10, "Consolidation", "The system"),
    (11, "Eval, diagnose, release", "The system"), (12, "LangGraph port", "The port"),
    (13, "Registry + guarded set", "Self-extension"), (14, "Tool authoring", "Self-extension"),
    (15, "The build pipeline", "Self-extension"),
]
BUILT = {1, 2, 3, 4, 5, 6}
SCAFFOLD = set()


@app.get("/api/memory")
def memory_panel():
    return {"stats": episodic.stats(), "history": episodic.history()}


@app.get("/api/facts")
def facts_panel():
    conn = trace.connect()
    reasons = rows_to_dicts(conn.execute("SELECT events FROM traces"))
    conn.close()
    gates = [e for r in reasons for e in json.loads(r["events"]) if e["type"] == "gate"]
    return {"facts": semantic.all_facts(), "gate": gates[-20:]}


@app.get("/api/system")
def system_panel():
    return {
        "model": agent.MODEL,
        "layers": [
            {"n": n, "name": name, "phase": phase,
             "status": "built" if n in BUILT else "scaffold" if n in SCAFFOLD else "planned"}
            for n, name, phase in LAYERS
        ],
        "personas": [],   # layer 7
        "evals": [],      # layer 11
    }


def serve(port: int = 7777) -> None:
    import uvicorn

    print(f"ninja cockpit → http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
