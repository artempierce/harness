"""Layer 7: a persona is data, and the allowlist is enforced from it."""

import dataclasses

import pytest

from ninja import personas, tools


def test_a_persona_loads_from_disk():
    coach = personas.load("interview-coach")
    assert coach.name == "interview-coach"
    assert coach.model
    # The body after the frontmatter is the system prompt.
    assert "coach" in coach.instructions.lower()
    assert coach.tools == ("read_file", "remember", "add_rule")


def test_the_description_reads_like_a_tool_description():
    # Layer 8 routes on this field, so it says when to use the persona.
    coach = personas.load("interview-coach")
    assert len(coach.description) > 20


def test_schemas_are_filtered_to_the_allowlist():
    coach = personas.load("interview-coach")
    names = [s["name"] for s in coach.schemas()]
    assert names == ["read_file", "remember", "add_rule"]
    # list_files exists but this persona never sees it.
    assert "list_files" in [s["name"] for s in tools.SCHEMAS]
    assert "list_files" not in names


def test_the_default_persona_holds_every_tool():
    assistant = personas.load("assistant")
    assert len(assistant.schemas()) == len(tools.SCHEMAS)


def test_a_persona_is_frozen():
    # It is resolved once per turn and read for the whole of it, so it must not
    # be mutable. FrozenInstanceError subclasses AttributeError.
    coach = personas.load("interview-coach")
    with pytest.raises(dataclasses.FrozenInstanceError):
        coach.model = "something-else"


def test_all_returns_the_cast():
    names = sorted(p.name for p in personas.all())
    assert names == ["assistant", "interview-coach"]


def test_an_unknown_persona_names_itself():
    with pytest.raises(ValueError, match="nonesuch"):
        personas.load("nonesuch")


def test_malformed_frontmatter_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(personas, "DIR", tmp_path)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "PERSONA.md").write_text("no frontmatter here\n")
    with pytest.raises(ValueError, match="frontmatter"):
        personas.load("broken")


def test_missing_keys_name_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(personas, "DIR", tmp_path)
    thin = tmp_path / "thin"
    thin.mkdir()
    (thin / "PERSONA.md").write_text("---\nname: thin\n---\n\nbody\n")
    with pytest.raises(ValueError, match="description"):
        personas.load("thin")


def test_a_missing_personas_dir_falls_back_read_only_and_says_so(tmp_path, monkeypatch):
    # The harness must start even with no personas/ directory at all. But the
    # fallback stands in for a persona whose tool restrictions are gone, so it
    # gets the least it needs to be useful — reading — and it says so, rather
    # than quietly handing the model every tool.
    monkeypatch.setattr(personas, "DIR", tmp_path / "does-not-exist")
    with pytest.warns(RuntimeWarning, match="personas"):
        fallback = personas.load(personas.DEFAULT)
    assert fallback.name == personas.DEFAULT
    assert set(fallback.tools) == {"list_files", "read_file"}
    assert fallback.model == personas.DEFAULT_MODEL


def test_a_deleted_default_persona_also_falls_back_read_only_and_says_so(tmp_path, monkeypatch):
    # personas/ exists and other personas load, but assistant/ is gone — the
    # same lost restrictions, reached by a different path.
    monkeypatch.setattr(personas, "DIR", tmp_path)
    (tmp_path / "other").mkdir()
    with pytest.warns(RuntimeWarning, match="personas"):
        fallback = personas.load(personas.DEFAULT)
    assert set(fallback.tools) == {"list_files", "read_file"}


def test_an_empty_personas_dir_falls_back_read_only_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(personas, "DIR", tmp_path)
    with pytest.warns(RuntimeWarning, match="personas"):
        cast = personas.all()
    assert [set(p.tools) for p in cast] == [{"list_files", "read_file"}]


def _write(tmp_path, monkeypatch, name, frontmatter):
    monkeypatch.setattr(personas, "DIR", tmp_path)
    (tmp_path / name).mkdir()
    (tmp_path / name / "PERSONA.md").write_text(f"---\n{frontmatter}---\n\nbody\n")


def test_an_unknown_tool_name_is_refused(tmp_path, monkeypatch):
    # A typo in `tools:` used to remove a capability silently — the persona
    # loaded, and the tool it named simply never appeared in any request.
    _write(tmp_path, monkeypatch, "typo",
           "name: typo\ndescription: d\ntools: [read_file, reed_file]\nmodel: m\n")
    with pytest.raises(ValueError, match="reed_file"):
        personas.load("typo")


def test_a_tool_list_written_as_a_string_is_refused(tmp_path, monkeypatch):
    # `tools: read_file` is valid YAML and a string. tuple() would turn it into
    # nine one-letter tool names, leaving a persona that holds nothing at all.
    _write(tmp_path, monkeypatch, "flat",
           "name: flat\ndescription: d\ntools: read_file\nmodel: m\n")
    with pytest.raises(ValueError, match="must be a list"):
        personas.load("flat")


def test_a_name_that_disagrees_with_its_directory_is_refused(tmp_path, monkeypatch):
    # Every lookup goes through the directory name, so a mismatch makes the
    # persona unreachable the moment anything tries to load it back by name.
    _write(tmp_path, monkeypatch, "coach",
           "name: interview-coach\ndescription: d\ntools: [read_file]\nmodel: m\n")
    with pytest.raises(ValueError, match="directory"):
        personas.load("coach")


def test_invalid_yaml_arrives_as_the_error_every_caller_catches(tmp_path, monkeypatch):
    # An unclosed bracket is the likeliest way a PERSONA.md is wrong, and yaml
    # reports it as a YAMLError — which is not a ValueError, so it sails past
    # `except ValueError` at both call sites that exist to contain it. The
    # loader's care in naming the file is worth nothing if the error it raises
    # is not the one anybody is catching.
    _write(tmp_path, monkeypatch, "unclosed",
           "name: unclosed\ndescription: [oops\ntools: [read_file]\nmodel: m\n")
    with pytest.raises(ValueError, match="valid YAML"):
        personas.load("unclosed")


def test_frontmatter_that_is_not_a_mapping_is_refused(tmp_path, monkeypatch):
    # `- name` is valid YAML and a list. Everything after the parse indexes it
    # by key, so a non-mapping either raises a TypeError from inside the loader
    # or — for a set or a string — reads as a bag of characters that happens to
    # answer `in`.
    for name, frontmatter in [("listy", "- name\n- description\n"),
                              ("scalar", "just a sentence\n")]:
        _write(tmp_path, monkeypatch, name, frontmatter)
        with pytest.raises(ValueError, match="mapping"):
            personas.load(name)


def test_a_required_key_with_no_value_is_refused(tmp_path, monkeypatch):
    # `model:` with nothing after it is a key that is present and holds None, so
    # a check for missing keys passes it. The persona then loads, lists in the
    # cockpit looking complete, and fails at the API on every single turn with
    # nothing in the error naming the file that caused it.
    _write(tmp_path, monkeypatch, "blank",
           "name: blank\ndescription: d\ntools: [read_file]\nmodel:\n")
    with pytest.raises(ValueError, match="empty"):
        personas.load("blank")


def test_a_persona_name_cannot_walk_out_of_the_personas_directory(tmp_path, monkeypatch):
    # The name is not a label, it is a path segment — and it arrives from a chat
    # request body, a /persona line and the cockpit's switcher. A relative path
    # in it reads a PERSONA.md from anywhere on disk, and the name check does
    # not stop it: the file simply declares the traversal as its own name.
    monkeypatch.setattr(personas, "DIR", tmp_path / "personas")
    (tmp_path / "personas").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "PERSONA.md").write_text(
        "---\nname: ../outside\ndescription: a persona that is not in the cast\n"
        "tools: [read_file, remember]\nmodel: claude-haiku-4-5\n---\n\nbody\n"
    )
    for name in ["../outside", "/etc", "../../etc/passwd", ".", ".."]:
        with pytest.raises(ValueError, match="not a path"):
            personas.load(name)


def test_every_persona_on_disk_names_a_model_the_harness_can_price():
    # cost_usd is computed from trace.PRICING, keyed by model id, so a persona
    # naming a model that is not in it adds exactly zero to the dashboard's
    # total — indistinguishable from a turn that was free, and at layer 15 a
    # cost ceiling with a hole in it. This is the free half of the live
    # model-id check: it cannot say the id still exists at the API, only that
    # the harness can bill what it runs.
    from ninja import trace

    for persona in personas.all():
        assert persona.model in trace.PRICING, persona.name


def test_the_fallback_is_never_wider_than_the_assistant_on_disk(tmp_path, monkeypatch):
    # Two sources for one fact. With personas/ absent the harness runs the
    # constants in personas.py instead of the file. The model must not drift,
    # and the tools may only ever be a subset: a fallback that could do more
    # than the persona it replaces is the fail-open this guards against. (The
    # instructions already differ, deliberately — the file says when to use
    # `remember` and the constant does not.)
    on_disk = personas.load("assistant")
    monkeypatch.setattr(personas, "DIR", tmp_path / "does-not-exist")
    with pytest.warns(RuntimeWarning):
        fallback = personas.load(personas.DEFAULT)
    assert fallback.model == on_disk.model
    assert set(fallback.tools) <= set(on_disk.tools)
