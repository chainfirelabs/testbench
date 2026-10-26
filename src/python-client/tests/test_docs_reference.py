"""The web app's Docs page shows this client from a generated file; it must
match the code. Regenerate with `python scripts/build_docs.py`."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_docs.py"


def load():
    spec = importlib.util.spec_from_file_location("build_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_docs_page_reference_matches_the_code():
    build_docs = load()
    if not build_docs.OUTPUT.exists():
        pytest.skip("not in a TestBench checkout: no frontend to compare with")
    assert build_docs.OUTPUT.read_text() == build_docs.render(), (
        "src/frontend/src/docs/pythonClient.json is out of date; run python scripts/build_docs.py")


def test_every_public_method_is_documented():
    reference = load().build()
    tests = next(item for item in reference["reference"] if item["accessor"] == "tb.tests")
    names = [member["name"] for member in tests["members"]]
    assert "create" in names and "record" in names
    assert all(not name.startswith("_") for name in names)
    assert any(page["title"] == "Install" for page in reference["guide"])
