"""Phase D1: skills — procedures matched to a message and injected only when
they apply. Parsing mirrors ninja/personas.py; matching is pure and offline."""

from pathlib import Path

from ninja import agent, skills, trace

from .conftest import a_persona


def _write(tmp_path, monkeypatch, name, frontmatter, body="do the thing\n"):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n{body}")


def a_skill(**overrides):
    fields = {"name": "weekly-review", "description": "Run a weekly review.",
              "body": "1. Ask what got done."}
    fields.update(overrides)
    return skills.Skill(**fields)


# --- parsing -------------------------------------------------------------


def test_a_skill_loads_from_disk(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "weekly-review",
           "name: weekly-review\ndescription: Run a weekly review.\n",
           body="1. Ask what got done.\n2. List blockers.\n")
    (loaded,) = skills.load_all()
    assert loaded.name == "weekly-review"
    assert loaded.description == "Run a weekly review."
    assert "Ask what got done" in loaded.body


def test_missing_frontmatter_is_skipped_and_warned(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter here\n")
    assert skills.load_all() == []
    assert "skipped skill" in capsys.readouterr().err


def test_a_missing_field_is_skipped_and_warned(tmp_path, monkeypatch, capsys):
    _write(tmp_path, monkeypatch, "thin", "name: thin\n")
    assert skills.load_all() == []
    assert "description" in capsys.readouterr().err


def test_a_name_that_disagrees_with_its_directory_is_skipped_and_warned(
    tmp_path, monkeypatch, capsys
):
    _write(tmp_path, monkeypatch, "thedir",
           "name: other-name\ndescription: d\n")
    assert skills.load_all() == []
    assert "directory" in capsys.readouterr().err


def test_one_broken_skill_does_not_stop_the_rest_loading(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "SKILL.md").write_text(
        "---\nname: good\ndescription: A fine skill.\n---\n\nbody\n"
    )
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text("no frontmatter\n")
    names = [s.name for s in skills.load_all()]
    assert names == ["good"]
    assert "skipped skill" in capsys.readouterr().err


def test_a_missing_skills_dir_is_silently_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "DIR", tmp_path / "does-not-exist")
    assert skills.load_all() == []


def test_an_unreadable_skill_is_skipped_and_warned_not_raised(tmp_path, monkeypatch, capsys):
    # read_text can raise OSError (e.g. PermissionError, IsADirectoryError),
    # not just ValueError — that must be skipped-and-warned like any other
    # broken skill, not propagate and fail the whole turn.
    monkeypatch.setattr(skills, "DIR", tmp_path)
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "SKILL.md").write_text(
        "---\nname: good\ndescription: A fine skill.\n---\n\nbody\n"
    )
    (tmp_path / "locked").mkdir()
    locked = tmp_path / "locked" / "SKILL.md"
    locked.write_text("---\nname: locked\ndescription: d\n---\n\nbody\n")

    real_read_text = Path.read_text

    def flaky_read_text(self, *args, **kwargs):
        if self == locked:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    names = [s.name for s in skills.load_all()]
    assert names == ["good"]
    assert "skipped skill" in capsys.readouterr().err


# --- matching --------------------------------------------------------------


def test_two_shared_words_match():
    weekly = a_skill(name="weekly-review", description="Run a weekly review of the week")
    assert skills.match("time for my weekly review", [weekly]) == [weekly]


def test_one_shared_word_does_not_match():
    weekly = a_skill(name="weekly-review", description="Run a weekly review of the week")
    assert skills.match("review this please", [weekly]) == []


def test_stopwords_do_not_count_toward_overlap():
    weekly = a_skill(name="weekly-review", description="Run a weekly review of the week")
    # Shares only "the" and "review" with the description — "the" is a stopword.
    assert skills.match("what about the review", [weekly]) == []


def test_best_overlap_matches_first():
    # Both must clear the >=2 threshold on their own, or the weaker one would
    # not appear in the result at all rather than merely sort second.
    weak = a_skill(name="weak", description="weekly review of things")
    strong = a_skill(name="strong", description="weekly review of wins and blockers")
    assert skills.match("weekly review of wins", [weak, strong]) == [strong, weak]


def test_at_most_two_skills_match():
    all_three = [
        a_skill(name=n, description="weekly review of wins and blockers")
        for n in ("a", "b", "c")
    ]
    assert len(skills.match("weekly review of wins", all_three)) == 2


def test_ties_are_broken_by_name():
    z = a_skill(name="zzz", description="weekly review")
    a = a_skill(name="aaa", description="weekly review")
    assert skills.match("weekly review", [z, a]) == [a, z]


# --- rendering ---------------------------------------------------------


def test_format_section_lists_each_matched_skill():
    weekly = a_skill(name="weekly-review", description="d", body="1. Ask what got done.")
    section = skills.format_section([weekly])
    assert "Skills that apply to this request:" in section
    assert "### weekly-review" in section
    assert "1. Ask what got done." in section


def test_format_section_truncates_a_long_body():
    long = a_skill(body="x" * 4000)
    section = skills.format_section([long])
    assert "[truncated]" in section
    assert "x" * 4000 not in section


def test_format_section_does_not_truncate_a_short_body():
    short = a_skill(body="short body")
    assert "[truncated]" not in skills.format_section([short])


# --- wired into build_system ------------------------------------------------


def _seed(tmp_path, monkeypatch, name="weekly-review",
          description="Run a weekly review of wins and blockers",
          body="1. Ask what got done.\n2. List blockers.\n3. Pick three priorities."):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n")


def test_a_matching_message_gets_the_skill_injected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    turn = trace.Trace("x")
    system = agent.build_system("time for my weekly review", turn, a_persona())
    assert "### weekly-review" in system
    assert "Ask what got done" in system
    assert [e for e in turn.events if e["type"] == "skills"][0]["names"] == ["weekly-review"]


def test_a_non_matching_message_gets_nothing_injected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    turn = trace.Trace("x")
    system = agent.build_system("what is 2 + 2?", turn, a_persona())
    assert "weekly-review" not in system
    assert not [e for e in turn.events if e["type"] == "skills"]


def test_the_seed_skill_matches_a_natural_request(tmp_path, monkeypatch):
    # Loads the real skills/weekly-review/SKILL.md, not a throwaway one.
    monkeypatch.setattr(skills, "DIR", skills.ROOT / "skills")
    matched = skills.match("let's do my weekly review", skills.load_all())
    assert [s.name for s in matched] == ["weekly-review"]
