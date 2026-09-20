"""A free net under the tests that cost money.

`tests/test_live.py` is deselected by `addopts` and run only by a manual
workflow, so nothing in the PR gate exercises it. A refactor can delete
something it references and the suite still reports green — which is not
hypothetical: layer 7 deleted `agent.MODEL`, a live test went on reading it,
and 77 tests passed over the top of a module that would have raised
AttributeError the moment anything ran it.

`pytest --collect-only` is not the net for this. Measured against this exact
bug it exits 0 with everything collected: it imports the module, so it catches
a missing *import*, but the attribute is read inside a function body and is
never evaluated at collection time.

So read the references out of the source instead and check them against the
live modules. No key, no network, nothing spent — and it fails for the same
reason the real run would.
"""

import ast
import importlib
from pathlib import Path

LIVE = Path(__file__).resolve().parent / "test_live.py"


def ninja_attributes(source: str) -> list[tuple[str, str]]:
    """Every `some_ninja_module.attribute` written in the file.

    Only ninja's own modules: a third-party attribute that moved is the
    dependency's business and pip-audit's, not this test's.
    """
    tree = ast.parse(source)
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ninja":
            for alias in node.names:
                bound[alias.asname or alias.name] = f"ninja.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ninja" or alias.name.startswith("ninja."):
                    bound[alias.asname or alias.name.split(".")[0]] = alias.name
    return sorted(
        {
            (bound[node.value.id], node.attr)
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in bound
        }
    )


def test_every_ninja_attribute_the_live_tests_use_still_exists():
    references = ninja_attributes(LIVE.read_text())
    # Without this the test passes on an empty list — the exact shape of a test
    # that cannot fail, in the file whose whole job is to stop one.
    assert references, "found no ninja.* references in test_live.py"
    for module_name, attribute in references:
        module = importlib.import_module(module_name)
        assert hasattr(module, attribute), (
            f"{module_name}.{attribute} is gone, and tests/test_live.py still uses it"
        )


def test_the_live_tests_are_still_marked_live():
    # The marker is the whole mechanism: it is what `-m live` selects and what
    # the default `-m 'not live'` deselects. Renamed or dropped, the smoke
    # workflow selects nothing and reports green having called no API at all.
    module = importlib.import_module("tests.test_live")
    marks = module.pytestmark
    names = {m.name for m in (marks if isinstance(marks, list) else [marks])}
    assert "live" in names
    assert [n for n in dir(module) if n.startswith("test_")]


def test_the_suite_still_deselects_the_tests_that_cost_money(pytestconfig):
    # The other direction, and the more expensive one to get wrong: the default
    # run is free because of one line of config, and nothing asserted it.
    assert "not live" in " ".join(pytestconfig.getini("addopts"))
