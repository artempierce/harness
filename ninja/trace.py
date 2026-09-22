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
MIGRATIONS = ROOT / "sql" / "migrations"

# USD per million tokens: (input, output). Cost is computed here rather than
# reported by the API, so an unknown model shows as zero rather than lying.
PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}


def _pending(conn: sqlite3.Connection) -> list[Path]:
    """Migration files numbered above what this database has already run."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    found = sorted(MIGRATIONS.glob("*.sql")) if MIGRATIONS.is_dir() else []
    return [p for p in found if int(p.name.split("_", 1)[0]) > version]


def migrate(conn: sqlite3.Connection) -> None:
    """Bring a database up to the latest schema, one migration at a time.

    Deliberately not executescript(): it issues a COMMIT before running and
    ignores an explicit BEGIN, so a migration that fails on its third statement
    leaves the first two applied and still reports failure. Statements run one
    at a time inside a transaction that also carries the version bump, so a
    half-finished migration takes its version number down with it and the next
    connection retries it rather than skipping it.
    """
    for path in _pending(conn):
        number = int(path.name.split("_", 1)[0])
        body = "\n".join(
            line for line in path.read_text().splitlines()
            if not line.strip().startswith("--")
        )
        try:
            conn.execute("BEGIN")
            for statement in (s.strip() for s in body.split(";") if s.strip()):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    # isolation_level=None hands transaction control to migrate(), which needs
    # its BEGIN to mean what it says. Everything else here is single-statement,
    # so autocommit costs nothing and the existing commit() calls are no-ops.
    conn = sqlite3.connect(DB, isolation_level=None)
    # The schema is frozen at its original shape and every later change is a
    # migration, so a fresh database replays the whole chain rather than being
    # shortcut to the current one. That is what keeps migrations exercised by
    # every test run instead of only on the machine that wrote them.
    conn.executescript(SCHEMA.read_text())
    migrate(conn)
    _check_facts_table(conn)
    return conn


def _check_facts_table(conn: sqlite3.Connection) -> None:
    """Refuse a database whose tables do not match this code.

    The version number can lie: another branch's migration with the same number
    marks this code's migration as already applied while the tables say
    otherwise. Found the hard way — a database migrated by the parked vectors
    branch made `facts` a plain table, and every chat then died mid-turn on "no
    such column: facts". Say what happened at connect instead.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'facts'").fetchone()
    if row and "fts5" not in (row[0] or "").lower():
        raise RuntimeError(
            f"the database at {DB} does not match this code: 'facts' is not a "
            "full-text table, so it was probably migrated by a different branch. "
            "Restore the right database, or move it aside to start a fresh one."
        )


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
        # Per turn, not per loop: a child's delegations count against the same cap.
        self.delegations = 0

    def model(
        self, model: str, response, ms: int, persona: str | None = None, depth: int = 0
    ) -> None:
        used = response.usage
        self.input_tokens += used.input_tokens
        self.output_tokens += used.output_tokens
        self.cost += price(model, used.input_tokens, used.output_tokens)
        self.events.append(
            {
                "type": "model",
                # Since layer 7 both of these vary per turn, so a trace that
                # records neither cannot answer what the coach cost, or which
                # persona made the call that went wrong.
                "model": model,
                "persona": persona,
                "depth": depth,
                "ms": ms,
                "in": used.input_tokens,
                "out": used.output_tokens,
                "stop": response.stop_reason,
                # PRICING is keyed by model id and a persona names whatever
                # model it likes. An unpriced one adds nothing to the total,
                # which on the dashboard is indistinguishable from a turn that
                # was free — so the absence is recorded rather than rounded off.
                "unpriced": model not in PRICING,
            }
        )

    def route(
        self, chosen: str, previous: str, why: str, model: str, response, ms: int
    ) -> None:
        """Which conversation this turn was filed under, and what it cost.

        The classifier is a real model call, so its tokens belong in the turn's
        receipt like any other. A router whose spend is invisible is the first
        thing that would make a cost ceiling wrong.
        """
        if response is not None:
            used = response.usage
            self.input_tokens += used.input_tokens
            self.output_tokens += used.output_tokens
            self.cost += price(model, used.input_tokens, used.output_tokens)
        self.events.append(
            {
                "type": "route",
                "chosen": chosen,
                "previous": previous,
                "why": why,
                "model": model,
                "in": response.usage.input_tokens if response is not None else 0,
                "out": response.usage.output_tokens if response is not None else 0,
                "ms": ms,
                "stop": response.stop_reason if response is not None else None,
                # Same hazard as Trace.model: an unpriced model adds nothing to
                # the total, which is indistinguishable on the dashboard from a
                # call that was free.
                "unpriced": response is not None and model not in PRICING,
            }
        )

    def consolidation(self, ok: bool, why: str, ms: int) -> None:
        """What a consolidation attempt did. Its tokens are a `model` event.

        Kept apart from the cost so a failed attempt still says why. The
        rows stay unconsolidated on every failure, so without this the retry
        that follows would be the only sign anything had gone wrong.
        """
        self.events.append({"type": "consolidation", "ok": ok, "why": why, "ms": ms})

    def gate(self, retrieve: bool, why: str, hits: int) -> None:
        # A skip is a decision, not an absence — record it so the ratio is
        # visible and the reason is readable afterwards.
        self.events.append(
            {"type": "gate", "retrieve": retrieve, "why": why, "hits": hits, "ms": 0}
        )

    def skills(self, names: list[str]) -> None:
        # Recorded only when something matched — an empty turn says nothing
        # extra, the same choice consolidation's silence-on-nothing-due makes.
        self.events.append({"type": "skills", "names": names})

    def judge(self, criterion: str, passed: bool) -> None:
        """The verdict this trace's own model call reached. The prompt, the
        model, and the answer are already this trace's user_input/model
        event/reply; this only names the criterion and the result."""
        self.events.append({"type": "judge", "criterion": criterion, "passed": passed})

    def delegate(self, persona: str, depth: int, task: str, ok: bool, ms: int) -> None:
        """A child loop that ran. Its calls are already `model` and `tool` events
        at `depth`; this is the summary that ties them to who was asked.
        A refusal never starts a child and is recorded as the failed `tool` call."""
        self.events.append(
            {
                "type": "delegate",
                "persona": persona,
                "depth": depth,
                "task": task[:200],
                "ok": ok,
                "ms": ms,
            }
        )

    def tool(
        self, name: str, args: dict, ok: bool, output: str, ms: int, depth: int = 0
    ) -> None:
        self.events.append(
            {
                "type": "tool",
                "depth": depth,
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
            " model_calls, input_tokens, output_tokens, cost_usd, events,"
            " persona)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                # A turn runs as one persona — it is resolved once, before the
                # loop — so the first model event that names one names them all.
                next((e.get("persona") for e in self.events if e.get("persona")), None),
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
    # Named columns rather than SELECT * and a positional unpack. The first
    # migration to add a column broke that unpack, which is a good argument
    # against writing it that way in a schema that can now change.
    row = conn.execute(
        "SELECT id, started_at, duration_ms, user_input, reply, model_calls,"
        " input_tokens, output_tokens, cost_usd, events, persona"
        " FROM traces WHERE id = ?",
        (trace_id,),
    ).fetchone()
    conn.close()
    if row is None:
        raise SystemExit(f"no trace {trace_id}")

    (tid, when, ms, ask, reply, calls, tin, tout, cost, events, persona) = row
    ran_as = f" · {persona}" if persona else ""
    print(f"trace {tid} · {when} · {ms / 1000:.1f}s · {calls} model calls{ran_as}")
    print(f"       {tin} in / {tout} out · ${cost:.5f}\n")
    print(f"you>   {ask}\n")

    for i, e in enumerate(json.loads(events), 1):
        # Rows written before delegation have no depth.
        pad = "  " * e.get("depth", 0)
        if e["type"] == "route":
            moved = "stayed in" if e["chosen"] == e["previous"] else f"{e['previous']} →"
            print(f"  {i}. route  {moved} {e['chosen']} · {e['why']}")
            continue
        if e["type"] == "consolidation":
            print(f"  {i:>2}. consolidate {'ok' if e['ok'] else 'FAILED'} · {e['why']}")
            continue
        if e["type"] == "gate":
            verdict = f"retrieve {e['hits']}" if e["retrieve"] else "skip"
            print(f"  {i:>2}. gate      {verdict:<12} {e['why']}")
        elif e["type"] == "delegate":
            mark = "ok" if e["ok"] else "ERROR"
            # `depth` is the child's; the line sits with the caller that asked.
            print(
                f"{'  ' * (e['depth'] - 1)}  {i:>2}. delegate {e['ms']:>5}ms  "
                f"→ {e['persona']} (depth {e['depth']}) {mark}: {e['task']!r}"
            )
        elif e["type"] == "model":
            # .get throughout: rows written before these fields existed are
            # still in the database and still have to print.
            ran_as = " ".join(x for x in (e.get("persona"), e.get("model")) if x)
            flag = "  [unpriced — not in the total]" if e.get("unpriced") else ""
            print(
                f"{pad}  {i:>2}. model   {e['ms']:>6}ms  "
                f"{e['in']:>5} in / {e['out']:<5} out  → {e['stop']}  {ran_as}{flag}"
            )
        elif e["type"] == "skills":
            print(f"  {i:>2}. skills    {', '.join(e['names'])}")
        elif e["type"] == "judge":
            mark = "PASS" if e["passed"] else "FAIL"
            print(f"  {i:>2}. judge     {mark:<4}  {e['criterion']}")
        else:
            mark = "ok" if e["ok"] else "ERROR"
            preview = e["preview"].replace("\n", "⏎")[:56]
            print(f"{pad}  {i:>2}. tool    {e['ms']:>6}ms  {e['name']}({e['args']}) {mark}")
            print(f"{pad}      {e['bytes']} bytes: {preview}")

    print(f"\nagent> {reply}")
