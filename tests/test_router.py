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
    turn = trace.Trace("x")
    assert router.route(client, "hi", "assistant", cast(), turn) == "assistant"

    # And the decision is recorded, not just the fallback taken. An unrecorded
    # fallback is a router that silently ignores the model.
    event = next(e for e in turn.events if e["type"] == "route")
    assert event["chosen"] == "assistant"
    assert event["previous"] == "assistant"
    assert "marketing-department" in event["why"]


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


def test_a_truncated_reply_says_so_rather_than_looking_unrecognised():
    # max_tokens is a different problem from the model naming something that
    # does not exist, and the trace should not conflate them.
    truncated = types.SimpleNamespace(
        content=[block(type="text", text="interview")],
        stop_reason="max_tokens",
        usage=types.SimpleNamespace(input_tokens=80, output_tokens=20),
    )
    turn = trace.Trace("x")
    client = StubClient([truncated])
    assert router.route(client, "hi", "assistant", cast(), turn) == "assistant"

    event = next(e for e in turn.events if e["type"] == "route")
    assert "truncated" in event["why"]
    assert event["stop"] == "max_tokens"


def test_the_routers_tokens_are_in_the_turns_totals():
    # An invisible per-turn spend is what makes a cost ceiling wrong.
    client = StubClient([answer("assistant", tokens=(80, 4))])
    turn = trace.Trace("x")
    router.route(client, "hi", "assistant", cast(), turn)

    assert turn.input_tokens == 80
    assert turn.output_tokens == 4
    assert turn.cost > 0


def test_a_router_on_an_unpriced_model_is_flagged_rather_than_counted_as_free(monkeypatch):
    # An unpriced model adds nothing to the total, which is indistinguishable
    # from a free call unless the absence is recorded. Trace.model has carried
    # this flag since layer 7; the router's call is no different.
    monkeypatch.setattr(router, "MODEL", "some-model-with-no-price")
    turn = trace.Trace("x")
    router.route(StubClient([answer("assistant")]), "hi", "assistant", cast(), turn)

    event = next(e for e in turn.events if e["type"] == "route")
    assert event["unpriced"] is True
    assert turn.cost == 0.0
    # The tokens are still counted even though the money is not.
    assert turn.input_tokens == 80


def test_a_router_on_a_priced_model_is_not_flagged():
    turn = trace.Trace("x")
    router.route(StubClient([answer("assistant")]), "hi", "assistant", cast(), turn)

    event = next(e for e in turn.events if e["type"] == "route")
    assert event["unpriced"] is False
    assert turn.cost > 0
