"""The tool boundary. These are the security tests — they must not be relaxed
to make a feature work."""

import pytest

from ninja import tools


def test_schemas_are_well_formed():
    for schema in tools.SCHEMAS:
        assert schema["name"] and schema["description"]
        assert schema["input_schema"]["type"] == "object"
        # The description is the API the model programs against. An empty or
        # placeholder one means the tool silently never gets called.
        assert len(schema["description"]) > 20


def test_list_and_read_work():
    listing = tools.run("list_files", {"path": "."})
    assert "pyproject.toml" in listing
    assert "[project]" in tools.run("read_file", {"path": "pyproject.toml"})


def test_hidden_files_are_refused():
    # .env holds the API key. Anything a tool reads lands in the transcript
    # and gets sent to the API on the next turn.
    for path in [".env", ".git/config", "ninja/../.env"]:
        with pytest.raises(ValueError, match="hidden files"):
            tools.run("read_file", {"path": path})


def test_listing_hides_dotfiles():
    assert ".env" not in tools.run("list_files", {"path": "."}).split("\n")


def test_path_escape_is_refused():
    for path in ["../../../etc/passwd", "/etc/passwd", "ninja/../../.."]:
        with pytest.raises(ValueError):
            tools.run("read_file", {"path": path})


def test_unknown_tool_raises():
    with pytest.raises(ValueError, match="unknown tool"):
        tools.run("rm_rf", {"path": "/"})
