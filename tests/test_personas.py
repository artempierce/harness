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
    assert coach.tools == ("read_file", "remember")


def test_the_description_reads_like_a_tool_description():
    # Layer 8 routes on this field, so it says when to use the persona.
    coach = personas.load("interview-coach")
    assert len(coach.description) > 20


def test_schemas_are_filtered_to_the_allowlist():
    coach = personas.load("interview-coach")
    names = [s["name"] for s in coach.schemas()]
    assert names == ["read_file", "remember"]
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


def test_a_missing_personas_dir_falls_back_to_layer_6_behaviour(tmp_path, monkeypatch):
    # The harness must start even with no personas/ directory at all.
    monkeypatch.setattr(personas, "DIR", tmp_path / "does-not-exist")
    fallback = personas.load(personas.DEFAULT)
    assert fallback.name == personas.DEFAULT
    assert len(fallback.schemas()) == len(tools.SCHEMAS)
    assert fallback.model == personas.DEFAULT_MODEL
