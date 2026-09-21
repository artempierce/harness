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


def test_a_persona_command_never_becomes_a_user_turn(monkeypatch, capsys):
    # The invariant lives in main()'s ordering: a /persona line is handled and
    # continue'd before messages.append, so the command never reaches the
    # conversation. Testing switch() alone cannot prove this — switch() is not
    # given the transcript.
    from .conftest import StubClient

    # No scripted responses: if a turn were attempted, .create() would raise
    # IndexError off the empty script rather than passing quietly.
    stub = StubClient([])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)
    monkeypatch.setattr(agent.episodic, "recall", lambda thread: [])

    lines = iter(["/persona interview-coach", ""])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)

    agent.main()

    # The command was intercepted, so the model was never called.
    assert stub.seen == []
    out = capsys.readouterr().out
    assert "interview-coach" in out


def test_one_broken_persona_file_does_not_end_the_session(tmp_path, monkeypatch, capsys):
    # Listing the cast reads every file on disk. An uncaught load error here
    # would take the REPL down mid-conversation, transcript and all.
    monkeypatch.setattr(personas, "DIR", tmp_path)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "PERSONA.md").write_text("no frontmatter here\n")
    started = personas.load("assistant")

    assert agent.switch("/persona", started) is started
    assert "frontmatter" in capsys.readouterr().out


def test_a_persona_file_with_broken_yaml_does_not_end_the_session(tmp_path, monkeypatch, capsys):
    # The same containment as above, for the malformation that does not come
    # out of the loader's own checks. yaml raises its own error type, and a
    # `/persona` listing that lets it through ends the REPL with the transcript
    # in it.
    monkeypatch.setattr(personas, "DIR", tmp_path)
    broken = tmp_path / "unclosed"
    broken.mkdir()
    (broken / "PERSONA.md").write_text(
        "---\nname: unclosed\ndescription: [oops\ntools: [read_file]\nmodel: m\n---\n\nbody\n"
    )
    started = personas.load("assistant")

    assert agent.switch("/persona", started) is started
    assert "YAML" in capsys.readouterr().out
