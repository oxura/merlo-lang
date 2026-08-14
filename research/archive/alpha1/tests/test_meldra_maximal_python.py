from __future__ import annotations

from research.archive.alpha1.merlo.maximal_python import (
    MaximalPythonChange,
    MaximalPythonManifest,
    MaximalPythonPackageManifest,
    analyze_maximal_python,
    apply_maximal_python_change,
    run_restricted_python,
)


def _manifest(*exports: str, **values) -> MaximalPythonManifest:
    return MaximalPythonManifest(
        (
            MaximalPythonPackageManifest(
                "pkg",
                "pkg",
                tuple(exports),
                **values,
            ),
        )
    )


def test_strict_profile_accepts_explicit_types_effects_capabilities_and_exports():
    sources = {
        "pkg/api.py": '''def effects(*names: str) -> object:
    return lambda function: function

def requires(*names: str) -> object:
    return lambda function: function

class Database:
    def read(self, user_id: int) -> str:
        return "user"

@effects("db.users.read")
@requires("db.users.read")
def load_user(db: Database, user_id: int) -> str:
    return db.read(user_id)
'''
    }
    manifest = _manifest(
        "pkg.api.Database",
        "pkg.api.effects",
        "pkg.api.load_user",
        "pkg.api.requires",
        effect_bindings=(("db.read", "db.users.read"),),
    )

    report = analyze_maximal_python(sources, manifest)

    assert report.ok is True
    assert report.lsp_status == "UNMEASURED_NO_LANGUAGE_SERVER"
    load_user = report.symbol("pkg.api.load_user")
    assert load_user.effects == ("db.users.read",)
    assert load_user.capabilities == ("db.users.read",)
    assert load_user.exported is True
    assert report.security_boundary is False


def test_strict_profile_rejects_missing_types_and_real_type_mismatch():
    sources = {
        "pkg/api.py": '''def missing(value):
    return value

def wrong() -> int:
    return "wrong"
'''
    }
    report = analyze_maximal_python(
        sources, _manifest("pkg.api.missing", "pkg.api.wrong")
    )
    codes = {item.code for item in report.blocking_diagnostics}

    assert report.ok is False
    assert codes >= {
        "MissingParameterType",
        "MissingReturnType",
        "ReturnTypeMismatch",
    }


def test_strict_profile_requires_explicit_exports():
    sources = {
        "pkg/api.py": "def public(value: int) -> int:\n    return value\n"
    }

    report = analyze_maximal_python(sources, _manifest())

    assert report.ok is False
    assert [item.code for item in report.blocking_diagnostics] == [
        "UnmanifestedPublicDeclaration"
    ]


def test_strict_profile_blocks_ambient_and_dynamic_bypasses_before_runtime():
    cases = {
        "ambient": "import socket\n\ndef run() -> int:\n    return 1\n",
        "dynamic_import": "def run() -> int:\n    __import__('socket')\n    return 1\n",
        "globals": "def target() -> int:\n    return 1\n\ndef run() -> int:\n    globals()['target'] = run\n    return target()\n",
        "monkey_patch": "class Service:\n    def run(self) -> int:\n        return 1\n\ndef replacement(self) -> int:\n    return 2\n\nService.run = replacement\n",
    }

    observed = {}
    for name, source in cases.items():
        sources = {"pkg/api.py": source}
        tree_exports = {
            "ambient": ("pkg.api.run",),
            "dynamic_import": ("pkg.api.run",),
            "globals": ("pkg.api.run", "pkg.api.target"),
            "monkey_patch": ("pkg.api.Service", "pkg.api.replacement"),
        }[name]
        report = analyze_maximal_python(sources, _manifest(*tree_exports))
        observed[name] = {item.code for item in report.blocking_diagnostics}
        assert run_restricted_python(
            sources, _manifest(*tree_exports), entry_path="pkg/api.py"
        ).status == "STATIC_POLICY_BLOCK"

    assert "ForbiddenAmbientImport" in observed["ambient"]
    assert "DynamicRuntimeEscape" in observed["dynamic_import"]
    assert "DynamicRuntimeEscape" in observed["globals"]
    assert "RuntimeBindingMutation" in observed["monkey_patch"]


def test_runtime_audit_blocks_a_static_scan_escape_without_claiming_sandbox_security():
    sources = {
        "pkg/api.py": '''import builtins

def run() -> int:
    builtins.open("/tmp/meldra-strict-forbidden", "w")
    return 1

if __name__ == "__main__":
    run()
'''
    }
    manifest = _manifest("pkg.api.run")

    report = analyze_maximal_python(sources, manifest)
    result = run_restricted_python(sources, manifest, entry_path="pkg/api.py")

    assert report.ok is True
    assert result.status == "RUNTIME_POLICY_BLOCK"
    assert result.runtime_escape is False
    assert result.audit_events == ("open:/tmp/meldra-strict-forbidden",)
    assert result.to_dict()["security_boundary"] is False


def test_interface_and_implementation_revisions_have_separate_locality():
    original = {
        "pkg/api.py": '''def public(value: int) -> int:
    return helper(value)

def helper(value: int) -> int:
    return value + 1
'''
    }
    private_edit = {
        "pkg/api.py": original["pkg/api.py"].replace("value + 1", "value + 2")
    }
    public_edit = {
        "pkg/api.py": original["pkg/api.py"].replace(
            "def public(value: int) -> int:",
            "def public(value: str) -> int:",
        )
    }
    manifest = _manifest("pkg.api.public", "pkg.api.helper")
    first = analyze_maximal_python(original, manifest)
    private = analyze_maximal_python(private_edit, manifest)
    public = analyze_maximal_python(public_edit, manifest)

    assert first.package("pkg").interface_revision_id == private.package(
        "pkg"
    ).interface_revision_id
    assert first.package("pkg").implementation_revision_id != private.package(
        "pkg"
    ).implementation_revision_id
    assert first.package("pkg").interface_revision_id != public.package(
        "pkg"
    ).interface_revision_id
    assert first.symbol("pkg.api.helper").symbol_id == private.symbol(
        "pkg.api.helper"
    ).symbol_id
    assert first.symbol("pkg.api.helper").revision_id != private.symbol(
        "pkg.api.helper"
    ).revision_id


def test_source_preserving_changeir_rename_preserves_identity_and_manifest():
    sources = {
        "pkg/api.py": '''def greet(name: str) -> str:
    return "hello " + name
''',
        "pkg/client.py": '''from pkg.api import greet

def render(name: str) -> str:
    return greet(name)
''',
    }
    manifest = _manifest("pkg.api.greet", "pkg.client.render")

    result = apply_maximal_python_change(
        sources,
        manifest,
        MaximalPythonChange.rename("pkg.api.greet", "salute"),
    )
    changed = dict(result.sources)

    assert result.applied is True
    assert result.target_symbol_id_before == result.target_symbol_id_after
    assert result.source_preserved_outside_edits is True
    assert "def salute" in changed["pkg/api.py"]
    assert "from pkg.api import salute" in changed["pkg/client.py"]
    assert "salute(name)" in changed["pkg/client.py"]
    assert "pkg.api.salute" in result.manifest.packages[0].exports
    assert "pkg.api.greet" not in result.manifest.packages[0].exports


def test_source_preserving_changeir_move_migrates_imports():
    sources = {
        "pkg/api.py": "def greet(name: str) -> str:\n    return name\n",
        "pkg/other.py": "",
        "pkg/client.py": (
            "from pkg.api import greet\n\n"
            "def render(name: str) -> str:\n"
            "    return greet(name)\n"
        ),
    }
    manifest = _manifest("pkg.api.greet", "pkg.client.render")

    result = apply_maximal_python_change(
        sources,
        manifest,
        MaximalPythonChange.move("pkg.api.greet", "pkg.other"),
    )
    changed = dict(result.sources)

    assert result.applied is True
    assert result.target_symbol_id_before == result.target_symbol_id_after
    assert "def greet" not in changed["pkg/api.py"]
    assert "def greet" in changed["pkg/other.py"]
    assert "from pkg.other import greet" in changed["pkg/client.py"]
    assert "pkg.other.greet" in result.manifest.packages[0].exports


def test_source_preserving_changeir_signature_migrates_exact_callers():
    sources = {
        "pkg/api.py": "def greet(name: str) -> str:\n    return name\n",
        "pkg/client.py": (
            "from pkg.api import greet\n\n"
            "def render(name: str) -> str:\n"
            "    return greet(name)\n"
        ),
    }
    manifest = _manifest("pkg.api.greet", "pkg.client.render")

    result = apply_maximal_python_change(
        sources,
        manifest,
        MaximalPythonChange.change_signature(
            "pkg.api.greet",
            "(name: str, suffix: str)",
            {"suffix": '"!"'},
        ),
    )
    changed = dict(result.sources)

    assert result.applied is True
    assert result.target_symbol_id_before == result.target_symbol_id_after
    assert "def greet(name: str, suffix: str)" in changed["pkg/api.py"]
    assert 'greet(name, suffix=\"!\")' in changed["pkg/client.py"]
