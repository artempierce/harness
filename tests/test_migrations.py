"""The schema's ability to change after a database already exists.

Every statement in sql/schema.sql is CREATE TABLE IF NOT EXISTS, so before
this existed a column added to that file reached a fresh database and never
reached anyone's .ninja/state.db. The suite could not fail on it either, since
every test gets a new database — the gap and the blindness to it were the same
fact. These tests are the cover for both.
"""

import sqlite3

import pytest

from ninja import trace


def columns(conn, table="traces"):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def latest_version() -> int:
    return max(int(p.name.split("_", 1)[0]) for p in trace.MIGRATIONS.glob("*.sql"))


def test_a_fresh_database_replays_the_whole_chain():
    # A fresh database is not shortcut to the current shape. It is built from
    # the frozen schema and then migrated, so every test run exercises every
    # migration — which is what stops one going untested.
    conn = trace.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_version()
    assert "persona" in columns(conn)
    conn.close()


def test_a_database_that_predates_migrations_is_brought_forward(temp_db):
    # The bug this exists for: an existing .ninja/state.db, created before the
    # column was invented, with user_version still at 0.
    old = sqlite3.connect(temp_db)
    old.executescript(trace.SCHEMA.read_text())
    old.commit()
    assert old.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "persona" not in columns(old)
    old.close()

    conn = trace.connect()
    assert "persona" in columns(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_version()
    conn.close()


def test_the_backfill_populates_rows_written_before_the_migration(temp_db):
    # A database already at user_version 1 — schema plus migration 001 only —
    # the shape of an existing .ninja/state.db right before this layer landed.
    old = sqlite3.connect(temp_db)
    old.executescript(trace.SCHEMA.read_text())
    old.execute("ALTER TABLE traces ADD COLUMN persona TEXT")
    old.execute("PRAGMA user_version = 1")
    old.execute(
        "INSERT INTO chat_log (session_id, role, content, created_at, trace_id)"
        " VALUES ('old', 'user', 'said before threads existed', '2026-01-01T00:00:00', NULL)"
    )
    old.commit()
    old.close()

    conn = trace.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_version()
    row = conn.execute(
        "SELECT thread FROM chat_log WHERE content = 'said before threads existed'"
    ).fetchone()
    assert row[0] == "assistant"
    conn.close()


def test_migrations_do_not_run_a_second_time():
    # ALTER TABLE ADD COLUMN is not idempotent — a second run raises. Connecting
    # twice is the ordinary case, so this is the ordinary case working.
    trace.connect().close()
    conn = trace.connect()
    assert columns(conn).count("persona") == 1
    conn.close()


def test_a_failing_migration_leaves_the_database_untouched(tmp_path, monkeypatch):
    # sqlite3.executescript() issues a COMMIT before it runs and ignores an
    # explicit BEGIN, so a migration written that way half-applies and then
    # reports failure. The version must not move either, or the next connect
    # skips the migration that did not finish.
    broken = tmp_path / "migrations"
    broken.mkdir()
    (broken / "001_first.sql").write_text("ALTER TABLE traces ADD COLUMN ok TEXT;")
    (broken / "002_bad.sql").write_text(
        "ALTER TABLE traces ADD COLUMN fine TEXT;\n"
        "ALTER TABLE does_not_exist ADD COLUMN boom TEXT;"
    )
    monkeypatch.setattr(trace, "MIGRATIONS", broken)

    with pytest.raises(sqlite3.OperationalError):
        trace.connect()

    conn = sqlite3.connect(trace.DB)
    after = columns(conn)
    # 001 committed and stands; 002 rolled back entirely, column and version.
    assert "ok" in after
    assert "fine" not in after
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_the_frozen_schema_does_not_carry_migrated_columns():
    # The regression guard. Adding a column to schema.sql instead of writing a
    # migration looks like it works — fresh databases get it, tests pass — and
    # silently does nothing for every database that already exists. If this
    # fails, the column belongs in sql/migrations/, not in the schema.
    frozen = trace.SCHEMA.read_text()
    migrated = set()
    for path in trace.MIGRATIONS.glob("*.sql"):
        for line in path.read_text().splitlines():
            parts = line.strip().split()
            # Any table, not just traces — a guard hardcoded to one table gives
            # no protection to the next migration that touches another.
            if len(parts) > 5 and parts[:2] == ["ALTER", "TABLE"]:
                migrated.add(parts[5].rstrip(";"))
    assert migrated, "no ALTER found — this guard would pass vacuously"
    for column in migrated:
        assert column not in frozen, (
            f"{column!r} is in sql/schema.sql and in a migration. A frozen "
            f"schema is what makes the migration run on existing databases."
        )
