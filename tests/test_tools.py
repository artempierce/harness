"""The tool boundary. These are the security tests — they must not be relaxed
to make a feature work."""

import pytest

from ninja import tools

# Every tool. Tests that are not about the allowlist pass this.
ALL = tuple(s["name"] for s in tools.SCHEMAS)


def test_schemas_are_well_formed():
    for schema in tools.SCHEMAS:
        assert schema["name"] and schema["description"]
        assert schema["input_schema"]["type"] == "object"
        # The description is the API the model programs against. An empty or
        # placeholder one means the tool silently never gets called.
        assert len(schema["description"]) > 20


def test_list_and_read_work():
    listing = tools.run("list_files", {"path": "."}, ALL)
    assert "pyproject.toml" in listing
    assert "[project]" in tools.run("read_file", {"path": "pyproject.toml"}, ALL)


def test_hidden_files_are_refused():
    # .env holds the API key. Anything a tool reads lands in the transcript
    # and gets sent to the API on the next turn.
    for path in [".env", ".git/config", "ninja/../.env"]:
        with pytest.raises(ValueError, match="hidden files"):
            tools.run("read_file", {"path": path}, ALL)


def test_listing_hides_dotfiles():
    assert ".env" not in tools.run("list_files", {"path": "."}, ALL).split("\n")


def test_path_escape_is_refused():
    for path in ["../../../etc/passwd", "/etc/passwd", "ninja/../../.."]:
        with pytest.raises(ValueError):
            tools.run("read_file", {"path": path}, ALL)


def test_an_unknown_tool_raises():
    # Reachable only when the name IS allowed but has no implementation — a
    # persona file naming a tool that was since renamed or removed.
    with pytest.raises(ValueError, match="unknown tool"):
        tools.run("rm_rf", {"path": "/"}, ["rm_rf"])


def test_an_unlisted_tool_is_refused_before_dispatch():
    # The gate is the first statement in run(), so a name that is neither
    # allowed nor implemented is refused as an allowlist violation.
    with pytest.raises(ValueError, match="allowlist"):
        tools.run("rm_rf", {"path": "/"}, ALL)


def test_a_tool_outside_the_allowlist_is_refused():
    # The second enforcement point. The first is that the model was never
    # shown this tool; this catches the name it guessed anyway.
    with pytest.raises(ValueError, match="allowlist"):
        tools.run("remember", {"fact": "x"}, allowed=["read_file"])


def test_an_allowed_tool_still_runs():
    assert "pyproject.toml" in tools.run("list_files", {"path": "."}, allowed=ALL)


def test_an_empty_allowlist_grants_nothing():
    # A persona may hold no tools at all, and "nothing allowed" must not be
    # read as "no restriction". `if allowed and name not in allowed` is the
    # one-word version of this bug, and it fails open for every tool at once.
    # Arguments are omitted deliberately: the gate has to refuse before it
    # looks at them, or the refusal depends on the model's spelling.
    for schema in tools.SCHEMAS:
        with pytest.raises(ValueError, match="allowlist"):
            tools.run(schema["name"], {}, allowed=[])


def test_a_refused_tool_never_reaches_its_side_effect():
    # Raising is not the claim. The claim is that nothing happened — a gate
    # moved below the dispatch would still raise, after the fact was stored.
    from ninja import semantic

    with pytest.raises(ValueError, match="allowlist"):
        tools.run("remember", {"fact": "the allowlist leaked"}, allowed=["read_file"])
    assert semantic.count() == 0


def test_an_allowlist_flattened_into_a_string_grants_nothing():
    # `str` is a Sequence[str], so `name in allowed` becomes a substring test.
    # A tools list that arrived as text rather than a list would then grant
    # every tool whose name appears anywhere in it — silently, and looking
    # exactly like a correct allowlist.
    for allowed in ["read_file", "list_files, read_file, remember"]:
        with pytest.raises(ValueError, match="allowlist"):
            tools.run("read_file", {"path": "pyproject.toml"}, allowed=allowed)


def test_list_files_enforces_the_same_boundary_as_read_file():
    # Only read_file's boundary was covered. Listing a directory leaks its
    # names, which is how you find out what is worth reading next, and a
    # listing of .git or of / is a disclosure in its own right.
    for path in [".git", ".ninja", "../..", "/etc", "ninja/../.ninja"]:
        with pytest.raises(ValueError):
            tools.run("list_files", {"path": path}, ALL)


def test_a_symlink_out_of_the_project_is_refused(tmp_path, monkeypatch):
    # The boundary is checked on the resolved path, so a link is followed
    # before it is judged. A check against the typed string would pass this.
    root = tmp_path.resolve()
    monkeypatch.setattr(tools, "ROOT", root)
    (root / "shortcut").symlink_to("/etc")
    with pytest.raises(ValueError, match="escapes"):
        tools.run("list_files", {"path": "shortcut"}, ALL)


def test_a_symlink_to_a_hidden_file_is_refused(tmp_path, monkeypatch):
    # The same point for the .env rule, which is the one that holds the API
    # key. `config.txt` has no dot in it anywhere; what it points at does.
    root = tmp_path.resolve()
    monkeypatch.setattr(tools, "ROOT", root)
    (root / ".env").write_text("ANTHROPIC_API_KEY=not-a-real-key")
    (root / "config.txt").symlink_to(root / ".env")
    with pytest.raises(ValueError, match="hidden files"):
        tools.run("read_file", {"path": "config.txt"}, ALL)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("read_file", {"path": 5}),
        ("list_files", {"path": None}),
        ("remember", {"fact": 5}),
        ("add_rule", {"rule": ["not", "a", "string"]}),
        ("propose_skill", {"name": 5, "description": "d", "body": "b"}),
        ("propose_skill", {"name": "n", "description": 5, "body": "b"}),
        ("propose_skill", {"name": "n", "description": "d", "body": 5}),
    ],
)
def test_a_non_string_argument_is_refused_as_a_tool_error(name, args):
    # A model can emit any JSON for an argument. A wrong type is its mistake and
    # belongs back in the transcript as an error, not as a TypeError or a
    # sqlite3 error that takes the whole turn down.
    with pytest.raises(ValueError, match="must be a string"):
        tools.run(name, args, ALL, persona="assistant")


def test_a_large_file_is_truncated_not_sent_whole(tmp_path, monkeypatch):
    # Whatever a tool returns is in the history, and the history is re-sent on
    # every later step — a big file is a cost paid again on each round trip.
    root = tmp_path.resolve()
    monkeypatch.setattr(tools, "ROOT", root)
    (root / "big.log").write_text("x" * (tools.MAX_READ * 5))

    out = tools.run("read_file", {"path": "big.log"}, ALL)

    assert len(out) < tools.MAX_READ * 2
    assert "truncated" in out


def test_a_small_file_is_returned_whole(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(tools, "ROOT", root)
    (root / "small.txt").write_text("hello")

    assert tools.run("read_file", {"path": "small.txt"}, ALL) == "hello"


def test_propose_skill_stages_a_draft(tmp_path, monkeypatch):
    from ninja import skills

    monkeypatch.setattr(skills, "PENDING_DIR", tmp_path / "pending")
    result = tools.run(
        "propose_skill",
        {"name": "weekly-review", "description": "Run a weekly review.", "body": "1. Ask."},
        ALL,
    )
    assert "staged: weekly-review" in result
    assert "/approve-skill weekly-review" in result
    (staged,) = skills.load_all(skills.PENDING_DIR)
    assert staged.name == "weekly-review"
