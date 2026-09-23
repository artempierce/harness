"""Phase E1a: the judge — a binary verdict against a named criterion, never
the expected answer. Stubbed and free, like every other model call tested
in this suite; docs/TESTING.md's money rules keep it that way."""

import json

import pytest

from ninja import judge
from ninja.trace import connect

from .conftest import StubClient, block, response


def reply(text, tokens=(100, 20)):
    """A judge reply. Shaped like an anthropic Message, as StubClient expects."""
    return response([block(type="text", text=text)], "end_turn", tokens)


def _events(trace_id):
    conn = connect()
    row = conn.execute("SELECT events FROM traces WHERE id = ?", (trace_id,)).fetchone()
    conn.close()
    return json.loads(row[0])


def test_a_passing_output_is_recorded_as_passed():
    client = StubClient([reply("PASS")])
    verdict = judge.judge(client, "mentions ninja", "this is about ninja")
    assert verdict.passed is True
    assert verdict.criterion == "mentions ninja"


def test_a_failing_output_is_recorded_as_failed():
    client = StubClient([reply("FAIL")])
    verdict = judge.judge(client, "mentions ninja", "this is about pizza")
    assert verdict.passed is False


def test_the_reply_is_matched_leniently():
    # Models add punctuation and capitals. Do not fail a verdict over a full stop.
    client = StubClient([reply("  pass.  ")])
    assert judge.judge(client, "x", "y").passed is True


def test_an_unparseable_reply_raises_and_still_records_the_trace():
    # A judge failure is a diagnosis prompt, not a result — the trace must
    # exist so `ninja trace <id>` can show what the judge actually said.
    client = StubClient([reply("maybe?")])
    with pytest.raises(judge.JudgeParseError) as exc_info:
        judge.judge(client, "x", "y")
    assert _events(exc_info.value.trace_id)


def test_the_prompt_contains_only_the_criterion_and_the_output():
    # No facts, no skills, no conversational persona instructions — a judge
    # biased by unrelated context is not judging the criterion anymore.
    client = StubClient([reply("PASS")])
    judge.judge(client, "mentions ninja", "SECRET-OUTPUT-MARKER")
    sent = client.seen[0]
    assert sent["system"] == judge.PERSONA.instructions
    assert "mentions ninja" in str(sent["messages"])
    assert "SECRET-OUTPUT-MARKER" in str(sent["messages"])


def test_the_judge_holds_no_tools():
    # It must never be able to act — only score. Also what keeps it safely
    # out of the delegation subset rule's reach if it were ever named there.
    assert judge.PERSONA.tools == ()


def test_the_verdict_is_recorded_on_its_own_trace():
    client = StubClient([reply("PASS")])
    verdict = judge.judge(client, "mentions ninja", "yes, ninja")
    judge_events = [e for e in _events(verdict.trace_id) if e["type"] == "judge"]
    assert judge_events == [
        {"type": "judge", "criterion": "mentions ninja", "passed": True}
    ]


def test_the_judges_tokens_are_priced():
    client = StubClient([reply("PASS", tokens=(50, 3))])
    verdict = judge.judge(client, "x", "y")
    conn = connect()
    cost = conn.execute(
        "SELECT cost_usd FROM traces WHERE id = ?", (verdict.trace_id,)
    ).fetchone()[0]
    conn.close()
    assert cost > 0
