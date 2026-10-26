"""Write the Python client's reference for the web app's Docs page.

    python scripts/build_docs.py          # rewrite the file
    python scripts/build_docs.py --check  # fail if it is out of date

The page cannot import this package or read this directory: the frontend image
is built from src/frontend alone. So the reference is generated from the code
itself — every public method's signature and docstring, every model's fields —
plus the README's guide sections, and committed as JSON next to the page.
`tests/test_docs_reference.py` fails when it drifts from the code.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import testbench_client  # noqa: E402
from testbench_client import errors, models  # noqa: E402
from testbench_client.client import TestBench  # noqa: E402
from testbench_client.devices import Devices  # noqa: E402
from testbench_client.software import SoftwareResource  # noqa: E402
from testbench_client.test_runs import TestRuns  # noqa: E402
from testbench_client.tests import Tests  # noqa: E402
from testbench_client.vendor_devices import VendorDevices  # noqa: E402

OUTPUT = ROOT.parent / "frontend" / "src" / "docs" / "pythonClient.json"

# The README sections that are about developing this package, not using it.
SKIPPED_SECTIONS = {"Testing this library"}

# (accessor, class): how each part of the client is reached from a `tb`.
RESOURCES = [
    ("TestBench", TestBench),
    ("tb.devices", Devices),
    ("tb.software", SoftwareResource),
    ("tb.tests", Tests),
    ("tb.test_runs", TestRuns),
    ("tb.vendor_devices", VendorDevices),
]

# Plumbing a caller has no use for, public only so the resources can share it.
INTERNAL = {"cache_get", "cache_put", "paginate", "download"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def annotation(value) -> str | None:
    if value is inspect.Parameter.empty:
        return None
    return value if isinstance(value, str) else inspect.formatannotation(value)


def signature(function) -> str:
    """`(name: str, *, version: str | None = None) -> Software`, without self."""
    sig = inspect.signature(function)
    parts: list[str] = []
    star_done = False
    for param in sig.parameters.values():
        if param.name in {"self", "cls"}:
            continue
        if param.kind is param.KEYWORD_ONLY and not star_done:
            parts.append("*")
            star_done = True
        if param.kind is param.VAR_POSITIONAL:
            star_done = True
            text = f"*{param.name}"
        elif param.kind is param.VAR_KEYWORD:
            text = f"**{param.name}"
        else:
            text = param.name
        kind = annotation(param.annotation)
        if kind:
            text += f": {kind}"
        if param.default is not param.empty:
            text += f" = {param.default!r}" if kind else f"={param.default!r}"
        parts.append(text)
    result = f"({', '.join(parts)})"
    returns = annotation(sig.return_annotation)
    return f"{result} -> {returns}" if returns else result


def members(cls) -> list[dict]:
    """Public methods and properties, in the order the class defines them."""
    out = []
    for name, value in cls.__dict__.items():
        if name.startswith("_") or name in INTERNAL:
            continue
        if isinstance(value, property):
            out.append({"name": name, "kind": "property",
                        "signature": f": {annotation(inspect.signature(value.fget).return_annotation) or 'Any'}",
                        "doc": inspect.getdoc(value) or ""})
        elif isinstance(value, (classmethod, staticmethod)):
            out.append({"name": name, "kind": "classmethod", "signature": signature(value.__func__),
                        "doc": inspect.getdoc(value.__func__) or ""})
        elif inspect.isfunction(value):
            out.append({"name": name, "kind": "method", "signature": signature(value),
                        "doc": inspect.getdoc(value) or ""})
    return out


def resource(accessor: str, cls) -> dict:
    entry = {"slug": slug(accessor), "accessor": accessor, "class": cls.__name__,
             "doc": inspect.getdoc(cls) or "", "members": members(cls)}
    if cls is TestBench:
        entry["init_signature"] = signature(cls.__init__)
    return entry


def model(cls) -> dict:
    fields = []
    for field in dataclasses.fields(cls):
        if field.name.startswith("_"):
            continue
        if field.default is not dataclasses.MISSING:
            default = repr(field.default)
        elif field.default_factory is not dataclasses.MISSING:
            default = repr(field.default_factory())
        else:
            default = None
        fields.append({"name": field.name, "type": annotation(field.type) or "", "default": default})
    return {"slug": slug(cls.__name__), "name": cls.__name__, "doc": inspect.getdoc(cls) or "",
            "fields": fields, "members": members(cls)}


def guide() -> list[dict]:
    """The README, one page per `##` section; the text above the first is the intro."""
    text = (ROOT / "README.md").read_text()
    body = text.split("\n", 1)[1] if text.startswith("# ") else text
    sections = re.split(r"^## ", body, flags=re.MULTILINE)
    pages = [{"slug": "overview", "title": "Overview", "markdown": sections[0].strip()}]
    for section in sections[1:]:
        title, _, markdown = section.partition("\n")
        if title.strip() in SKIPPED_SECTIONS:
            continue
        pages.append({"slug": slug(title), "title": title.strip(), "markdown": markdown.strip()})
    return pages


def build() -> dict:
    exported = [getattr(testbench_client, name) for name in testbench_client.__all__]
    model_classes = [item for item in exported if dataclasses.is_dataclass(item)]
    error_classes = [item for item in exported if inspect.isclass(item) and issubclass(item, Exception)]
    constants = [name for name in testbench_client.__all__ if name.isupper()]
    return {
        "version": testbench_client.__version__,
        "guide": guide(),
        "reference": [resource(accessor, cls) for accessor, cls in RESOURCES],
        "models": [model(cls) for cls in model_classes],
        "errors": [{"name": cls.__name__, "base": cls.__bases__[0].__name__,
                    "doc": inspect.getdoc(cls) or "", "members": members(cls)}
                   for cls in error_classes],
        "constants": [{"name": name, "value": repr(getattr(testbench_client, name))} for name in constants],
    }


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    content = render()
    if "--check" in sys.argv:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            print(f"{OUTPUT} is out of date; run python scripts/build_docs.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Kept for the linters: these modules are read through `testbench_client`.
_ = (errors, models)
