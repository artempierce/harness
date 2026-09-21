"""Layer 8a: the classifier that decides which conversation a turn belongs to."""

import types

from ninja import personas, router, trace

from .conftest import StubClient, block


def answer(text, tokens=(80, 4)):
    """A router reply. Shaped like an anthropic Message, as StubClient expects."""
    return types.SimpleNamespace(
        content=[block(type="text", text=text)],
        stop_reason="end_turn",
        usage=types.SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


def cast():
    return personas.all()


def test_it_returns_the_persona_the_model_named():
    client = StubClient([answer("interview-coach")])
    turn = trace.Trace("x")
    chosen = router.route(client, "how do I test a flaky API?", "assistant", cast(), turn)
    assert chosen == "interview-coach"


def test_the_prompt_carries_the_descriptions_and_the_current_thread():
    # description is what the router decides on — it was written as a tool
    # description in disguise for exactly this.
    client = StubClient([answer("assistant")])
    router.route(client, "hello", "interview-coach", cast(), trace.Trace("x"))

    sent = client.seen[0]
    prompt = sent["system"] + str(sent["messages"])
    assert "interview-coach" in prompt
    assert "rehearse" in prompt        # from interview-coach's description
    assert "interview-coach" in sent["system"]   # the current thread is stated


def test_an_unknown_name_keeps_the_current_thread():
    # A router that can strand you is worse than no router.
    client = StubClient([answer("marketing-department")])
    chosen = router.route(client, "hi", "assistant", cast(), trace.Trace("x"))
    assert chosen == "assistant"


def test_a_blank_answer_keeps_the_current_thread():
    client = StubClient([answer("")])
    turn = trace.Trace("x")
    assert router.route(client, "hi", "interview-coach", cast(), turn) == "interview-coach"


def test_a_failed_call_keeps_the_current_thread_and_does_not_raise():
    class Exploding:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise RuntimeError("router upstream is down")

    turn = trace.Trace("x")
    assert router.route(Exploding(), "hi", "assistant", cast(), turn) == "assistant"
    event = next(e for e in turn.events if e["type"] == "route")
    assert "down" in event["why"]


def test_the_answer_is_matched_leniently():
    # Models add punctuation and capitals. Do not fail a turn over a full stop.
    client = StubClient([answer("  Interview-Coach.  ")])
    assert router.route(client, "hi", "assistant", cast(), trace.Trace("x")) == "interview-coach"


def test_the_decision_is_recorded_with_its_reason():
    client = StubClient([answer("interview-coach")])
    turn = trace.Trace("x")
    router.route(client, "quiz me", "assistant", cast(), turn)

    event = next(e for e in turn.events if e["type"] == "route")
    assert event["chosen"] == "interview-coach"
    assert event["previous"] == "assistant"
    assert event["why"]


def test_the_routers_tokens_are_in_the_turns_totals():
    # An invisible per-turn spend is what makes a cost ceiling wrong.
    client = StubClient([answer("assistant", tokens=(80, 4))])
    turn = trace.Trace("x")
    router.route(client, "hi", "assistant", cast(), turn)

    assert turn.input_tokens == 80
    assert turn.output_tokens == 4
    assert turn.cost > 0
