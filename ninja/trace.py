"""Layer 3: one record per turn.

Without this the loop is a black box — the tool calls scroll past and then
they're gone. A trace is what you read afterwards to find out which step was
slow, which one was wrong, and what the whole thing cost.
"""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / ".ninja" / "state.db"
SCHEMA = ROOT / "sql" / "schema.sql"

# USD per million tokens: (input, output). Cost is computed here rather than
# reported by the API, so an unknown model shows as zero rather than lying.
PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA.read_text())
    return conn


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = PRICING.get(model, (0.0, 0.0))
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


class Trace:
    """Records one turn, then writes it as a single row."""

    def __init__(self, user_input: str):
        self.user_input = user_input
        self.t0 = time.perf_counter()
        self.started_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.events: list[dict] = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0

    def model(self, model: str, response, ms: int) -> None:
        used = response.usage
        self.input_tokens += used.input_tokens
        self.output_tokens += used.output_tokens
        self.cost += price(model, used.input_tokens, used.output_tokens)
        self.events.append(
            {
                "type": "model",
                "ms": ms,
                "in": used.input_tokens,
                "out": used.output_tokens,
                "stop": response.stop_reason,
            }
        )

    def gate(self, retrieve: bool, why: str, hits: int) -> None:
        # A skip is a decision, not an absence — record it so the ratio is
        # visible and the reason is readable afterwards.
        self.events.append(
            {"type": "gate", "retrieve": retrieve, "why": why, "hits": hits, "ms": 0}
        )

    def tool(self, name: str, args: dict, ok: bool, output: str, ms: int) -> None:
        self.events.append(
            {
                "type": "tool",
                "name": name,
                "args": args,
                "ms": ms,
                "ok": ok,
                # A tool can return a whole file. Keep the shape, not the payload —
                # a trace table that stores every byte read becomes the biggest
                # thing in the database.
                "bytes": len(output),
                "preview": output[:200],
            }
        )

    def finish(self, reply: str) -> int:
        conn = connect()
        cur = conn.execute(
            "INSERT INTO traces (started_at, duration_ms, user_input, reply,"
            " model_calls, input_tokens, output_tokens, cost_usd, events)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.started_at,
                int((time.perf_counter() - self.t0) * 1000),
                self.user_input,
                reply,
                sum(1 for e in self.events if e["type"] == "model"),
                self.input_tokens,
                self.output_tokens,
                round(self.cost, 6),
                json.dumps(self.events),
            ),
        )
        conn.commit()
        conn.close()
        return cur.lastrowid


def print_recent(limit: int = 10) -> None:
    conn = connect()
    rows = conn.execute(
        "SELECT id, started_at, duration_ms, model_calls, input_tokens,"
        " output_tokens, cost_usd, user_input FROM traces"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        print("no traces yet — run `ninja` and ask it something")
        return

    print(f"{'id':>4}  {'when':<20} {'time':>7} {'calls':>5} {'tokens':>12} {'cost':>9}  what")
    for tid, when, ms, calls, tin, tout, cost, ask in rows:
        print(
            f"{tid:>4}  {when:<20} {ms / 1000:>6.1f}s {calls:>5} "
            f"{tin:>5}/{tout:<6} ${cost:>8.5f}  {ask[:44]}"
        )


def print_one(trace_id: int) -> None:
    conn = connect()
    row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
    conn.close()
    if row is None:
        raise SystemExit(f"no trace {trace_id}")

    (tid, when, ms, ask, reply, calls, tin, tout, cost, events) = row
    print(f"trace {tid} · {when} · {ms / 1000:.1f}s · {calls} model calls")
    print(f"       {tin} in / {tout} out · ${cost:.5f}\n")
    print(f"you>   {ask}\n")

    for i, e in enumerate(json.loads(events), 1):
        if e["type"] == "gate":
            verdict = f"retrieve {e['hits']}" if e["retrieve"] else "skip"
            print(f"  {i:>2}. gate      {verdict:<12} {e['why']}")
        elif e["type"] == "model":
            print(
                f"  {i:>2}. model   {e['ms']:>6}ms  "
                f"{e['in']:>5} in / {e['out']:<5} out  → {e['stop']}"
            )
        else:
            mark = "ok" if e["ok"] else "ERROR"
            preview = e["preview"].replace("\n", "⏎")[:56]
            print(f"  {i:>2}. tool    {e['ms']:>6}ms  {e['name']}({e['args']}) {mark}")
            print(f"      {e['bytes']} bytes: {preview}")

    print(f"\nagent> {reply}")
