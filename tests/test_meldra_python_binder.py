from __future__ import annotations

from merlo.python_binder import bind_python_sources


SOURCES = {
    "pkg/model.py": """class User:
    name: str
    score: int

class Status:
    Active = "active"
    Disabled = "disabled"

def normalize(value: str) -> str:
    return value
""",
    "pkg/other.py": """def normalize(value: str) -> str:
    return value
""",
    "pkg/service.py": """from pkg.model import User, Status, normalize as clean
from pkg.other import normalize as other_clean

def render(user: User) -> str:
    local = user.name
    if user.score > 0:
        shadow = clean(local)
        return shadow
    return other_clean(local)

def active(user: User) -> bool:
    return user.name == Status.Active
""",
}


def test_structural_python_binder_resolves_annotations_aliases_and_fields():
    report = bind_python_sources(SOURCES)
    repeated = bind_python_sources(SOURCES)

    assert report == repeated
    assert report.to_json() == repeated.to_json()
    assert report.unknown_count == 0
    assert report.exact_count == len(report.references)
    assert report.diagnostics == ()
    targets = {
        (item.spelling, report.symbol(item.target_symbol_id).locator)
        for item in report.references
        if item.target_symbol_id is not None
    }
    assert ("User", "pkg.model.User") in targets
    assert ("name", "pkg.model.User.name") in targets
    assert ("score", "pkg.model.User.score") in targets
    assert ("clean", "pkg.model.normalize") in targets
    assert ("other_clean", "pkg.other.normalize") in targets
    assert ("Active", "pkg.model.Status.Active") in targets


def test_unknown_attribute_is_reported_in_the_shared_reference_stream():
    sources = dict(SOURCES)
    sources["pkg/service.py"] = sources["pkg/service.py"].replace(
        "user.name", "user.missing", 1
    )

    report = bind_python_sources(sources)
    unknown = [item for item in report.references if item.status == "Unknown"]

    assert len(unknown) == 1
    assert unknown[0].spelling == "missing"
    assert unknown[0].usage == "Field"
    assert unknown[0].target_symbol_id is None


def test_local_shadowing_wins_without_changing_import_target_identity():
    sources = {
        "pkg/lib.py": "def value(number: int) -> int:\n    return number\n",
        "pkg/main.py": """from pkg.lib import value

def run(number: int) -> int:
    value = number
    return value
""",
    }

    report = bind_python_sources(sources)
    value_references = [
        item
        for item in report.references
        if item.path == "pkg/main.py" and item.spelling == "value"
    ]

    assert len(value_references) == 1
    assert value_references[0].status == "Exact"
    assert value_references[0].target_symbol_id is None
    assert value_references[0].target_binding_id is not None


def test_private_import_and_out_of_profile_constructs_are_explicit():
    sources = {
        "pkg/lib.py": "def _hidden(value: int) -> int:\n    return value\n",
        "pkg/main.py": """from pkg.lib import _hidden

def run(values: list) -> int:
    for value in values:
        return _hidden(value)
    return 0
""",
    }

    report = bind_python_sources(sources)
    codes = [item.code for item in report.diagnostics]

    assert "PrivateImport" in codes
    assert "OutOfProfileStatement" in codes


def test_same_names_in_different_modules_bind_to_explicit_aliases():
    sources = {
        "a.py": "def format(value: str) -> str:\n    return value\n",
        "b.py": "def format(value: str) -> str:\n    return value\n",
        "main.py": """from a import format as format_a
from b import format as format_b

def render(value: str) -> str:
    return format_a(format_b(value))
""",
    }

    report = bind_python_sources(sources)
    aliases = {
        item.spelling: report.symbol(item.target_symbol_id).locator
        for item in report.references
        if item.spelling in {"format_a", "format_b"}
    }

    assert aliases == {"format_a": "a.format", "format_b": "b.format"}
