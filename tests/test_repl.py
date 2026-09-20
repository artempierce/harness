"""The /persona command. Switching swaps instructions and tools, not the thread."""

from ninja import agent, personas


def test_switching_returns_the_named_persona():
    started = personas.load("assistant")
    switched = agent.switch("/persona interview-coach", started)
    assert switched.name == "interview-coach"


def test_an_unknown_name_keeps_the_current_persona(capsys):
    started = personas.load("assistant")
    # A typo must not drop you into a broken state mid-conversation.
    assert agent.switch("/persona nonesuch", started) is started
    assert "nonesuch" in capsys.readouterr().out


def test_a_bare_persona_command_lists_the_cast(capsys):
    started = personas.load("assistant")
    assert agent.switch("/persona", started) is started
    out = capsys.readouterr().out
    assert "interview-coach" in out
    assert "assistant" in out


def test_switching_does_not_touch_working_memory():
    # The transcript is kept across a switch — swapping hats should not lose
    # the conversation. The persona lives in the system prompt, not in messages.
    messages = [{"role": "user", "content": "earlier"}]
    before = list(messages)
    agent.switch("/persona interview-coach", personas.load("assistant"))
    assert messages == before
