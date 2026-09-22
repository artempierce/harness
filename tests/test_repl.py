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


def test_the_repl_routes_each_turn(monkeypatch, capsys):
    # The router picks the thread; the turn runs in it.
    from ninja import episodic, router

    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)
    monkeypatch.setattr(router, "route", lambda *a, **k: "interview-coach")

    lines = iter(["quiz me on api testing", ""])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)
    agent.main()

    # The message was filed under the thread the router named, reply and all.
    assert [m["content"] for m in episodic.recall("interview-coach")] == [
        "quiz me on api testing",
        "ok",
    ]
    assert episodic.recall("assistant") == []


def test_an_explicit_persona_overrides_the_router(monkeypatch):
    # /persona is an override. The router does not overrule it for that turn.
    from ninja import episodic, router

    from .conftest import StubClient, block, response

    stub = StubClient([response([block(type="text", text="ok")], "end_turn")])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)

    called = []
    monkeypatch.setattr(router, "route", lambda *a, **k: called.append(1) or "assistant")

    lines = iter(["/persona interview-coach", "a coaching question", ""])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)
    agent.main()

    assert called == [], "the router ran despite an explicit /persona override"
    assert [m["content"] for m in episodic.recall("interview-coach")] == [
        "a coaching question",
        "ok",
    ]


# --- /approve-skill and /reject-skill ---------------------------------------


def test_a_bare_approve_skill_lists_pending_proposals(tmp_path, monkeypatch, capsys):
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/approve-skill")
    out = capsys.readouterr().out
    assert "weekly-review" in out
    assert "Run a weekly review." in out


def test_a_bare_approve_skill_with_nothing_pending_says_so(capsys):
    agent.review_skill("/approve-skill")
    assert "no pending" in capsys.readouterr().out


def test_approve_skill_writes_the_draft_and_clears_it_from_pending(tmp_path, monkeypatch, capsys):
    from ninja import skills

    monkeypatch.setattr(skills, "DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/approve-skill weekly-review")

    (live,) = skills.load_all()
    assert live.name == "weekly-review"
    assert skills.load_all(skills.PENDING_DIR) == []
    assert "approved" in capsys.readouterr().out


def test_reject_skill_discards_the_draft(tmp_path, monkeypatch, capsys):
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/reject-skill weekly-review")

    assert skills.load_all(skills.PENDING_DIR) == []
    assert "rejected" in capsys.readouterr().out


def test_approving_an_unknown_skill_prints_the_error_and_does_not_raise(capsys):
    agent.review_skill("/approve-skill nonesuch")
    assert "no pending" in capsys.readouterr().out


def test_a_typo_d_verb_is_not_treated_as_approve_or_reject(tmp_path, monkeypatch, capsys):
    # /approve-skills (typo) must not silently fall into the reject branch and
    # destroy a draft the user meant to keep.
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/approve-skills weekly-review")

    (staged,) = skills.load_all(skills.PENDING_DIR)
    assert staged.name == "weekly-review"
    assert "unknown command" in capsys.readouterr().out


def test_an_oserror_from_approve_does_not_take_down_the_repl(monkeypatch, capsys):
    from ninja import skills

    def boom(name):
        raise OSError("disk full")

    monkeypatch.setattr(skills, "approve", boom)

    agent.review_skill("/approve-skill weekly-review")
    assert "disk full" in capsys.readouterr().out


def test_approving_a_second_version_of_the_same_skill_announces_the_replacement(
    tmp_path, monkeypatch, capsys
):
    from ninja import skills

    monkeypatch.setattr(skills, "DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    agent.review_skill("/approve-skill weekly-review")
    first_out = capsys.readouterr().out
    assert "replaced" not in first_out

    skills.propose("weekly-review", "v2.", "1. Ask what got done.\n2. New step.")
    agent.review_skill("/approve-skill weekly-review")
    second_out = capsys.readouterr().out
    assert "replaced an existing skill" in second_out


def test_a_skill_review_command_never_becomes_a_user_turn(tmp_path, monkeypatch, capsys):
    # Same invariant as /persona, and for the same reason: an uncaught error
    # here must not take the REPL down mid-conversation, and the command
    # itself must never reach the model as a message.
    from ninja import skills

    from .conftest import StubClient

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    skills.propose("weekly-review", "Run a weekly review.", "1. Ask what got done.")

    stub = StubClient([])
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: stub)
    monkeypatch.setattr(agent.episodic, "recall", lambda thread: [])

    lines = iter(["/approve-skill weekly-review", ""])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)

    agent.main()

    assert stub.seen == []
    assert "approved" in capsys.readouterr().out


def _drive_main(monkeypatch, client, lines):
    """Run the real REPL with a scripted client and scripted input."""
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: client)
    typed = iter([*lines, ""])

    def fake_input(prompt=""):
        try:
            return next(typed)
        except StopIteration as end:
            raise EOFError from end

    monkeypatch.setattr("builtins.input", fake_input)
    agent.main()


def _thread():
    from ninja import episodic

    return [m["content"] for m in episodic.recall("assistant", limit=50)]


def test_a_finished_turn_saves_both_halves_in_one_write(monkeypatch):
    # Two separate writes can be torn apart, leaving user, user, assistant. The
    # server already writes the pair in one transaction; the REPL must too.
    from .conftest import StubClient, block, response

    def refuse(*a, **k):
        raise AssertionError("episodic.save writes one half; use save_exchange")

    monkeypatch.setattr(agent.episodic, "save", refuse)
    stub = StubClient([response([block(type="text", text="hi back")], "end_turn")])

    _drive_main(monkeypatch, stub, ["/persona assistant", "hello"])

    assert _thread() == ["hello", "hi back"]


class _FailingClient:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        raise RuntimeError("boom")


def test_an_api_error_does_not_end_the_session(monkeypatch, capsys):
    # The turn's earlier spend is already real. The REPL must survive, say what
    # went wrong, and carry on — as the server does with a 502.
    _drive_main(monkeypatch, _FailingClient(), ["/persona assistant", "hello", "again"])

    assert "boom" in capsys.readouterr().out


def test_a_failed_turn_is_still_recorded_and_saves_nothing(monkeypatch):
    from ninja.trace import connect

    _drive_main(monkeypatch, _FailingClient(), ["/persona assistant", "hello", "again"])

    conn = connect()
    replies = [r[0] for r in conn.execute("SELECT reply FROM traces ORDER BY id")]
    conn.close()
    assert len(replies) == 2
    assert all(r.startswith("[failed:") for r in replies)
    assert _thread() == []
