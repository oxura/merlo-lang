from __future__ import annotations

import json
import base64
import hashlib
import io
import tempfile
import zipfile

import pytest
from merlo.compiler import compile_project

from merlo.package_registry import (
    PackageMetadata,
    PackageResolver,
    RegistryError,
    RegistryIndex,
    ResolvedLock,
    satisfies,
)
from merlo.project import Project, resolve_dependencies
from merlo.wasm_backend import WasmBackend, WasmCompileError
from merlo.web_facet import Component, Route, WebFacetError, WebFacetManifest, WebBundler


def _archive(name: str = "src/main.mlo", content: bytes = b"main") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, content)
    return output.getvalue()


def test_registry_verifies_archive_and_rejects_escape() -> None:
    payload = _archive()
    package = PackageMetadata("demo", "1.0.0", hashlib.sha256(payload).hexdigest(), archive="https://registry.invalid/demo.zip")
    with tempfile.TemporaryDirectory() as directory:
        resolver = PackageResolver(RegistryIndex((package,)), transport=lambda _: payload, cache_dir=directory)
        assert resolver.fetch(package).is_file()
    escaped = _archive("../outside", b"bad")
    with pytest.raises(RegistryError, match="ArchivePathEscape"):
        PackageResolver.verify_archive(escaped, hashlib.sha256(escaped).hexdigest())


def test_registry_lock_is_canonical_and_offline() -> None:
    payload = _archive()
    package = PackageMetadata("demo", "1.0.0", hashlib.sha256(payload).hexdigest(), archive="https://registry.invalid/demo.zip")
    with tempfile.TemporaryDirectory() as directory:
        resolver = PackageResolver(RegistryIndex((package,)), transport=lambda _: payload, cache_dir=directory)
        resolver.fetch(package)
        first = resolver.resolve({"demo": "^1.0"}, offline=True)
        second = resolver.resolve({"demo": "^1.0"}, offline=True)
        assert first.to_json() == second.to_json()
        assert first.digest == second.digest


def test_registry_semver_lock_closure_and_immutability() -> None:
    stable = PackageMetadata("dep", "1.0.0", "1" * 64)
    prerelease = PackageMetadata("dep", "1.0.0-alpha", "2" * 64)
    root = PackageMetadata("root", "1.0.0", "3" * 64, dependencies={"dep": "^1.0"})
    index = RegistryIndex((prerelease, stable, root))
    resolver = PackageResolver(index)

    assert index.find("dep", "^1.0") == stable
    complete = resolver.resolve({"root": "^1.0"})
    assert [item.name for item in complete.packages] == ["dep", "root"]
    incomplete = ResolvedLock((root,), (("root", "^1.0"),))
    with pytest.raises(RegistryError, match="LockTampered"):
        resolver.resolve(lock=incomplete)
    canonical = ResolvedLock(tuple(reversed(complete.packages)), tuple(reversed(complete.roots)))
    assert canonical.to_json() == complete.to_json()
    with pytest.raises(TypeError):
        root.dependencies["other"] = "*"



def test_registry_aggregates_transitive_constraints_and_backtracks() -> None:
    b_old = PackageMetadata("b", "1.0.0", "1" * 64)
    b_new = PackageMetadata("b", "2.0.0", "2" * 64)
    left = PackageMetadata("left", "1.0.0", "3" * 64, dependencies={"b": ">=1"})
    right = PackageMetadata("right", "1.0.0", "4" * 64, dependencies={"b": "<2"})
    lock = PackageResolver(RegistryIndex((b_old, b_new, left, right))).resolve({"left": "*", "right": "*"})
    assert [(item.name, item.version) for item in lock.packages] == [
        ("b", "1.0.0"),
        ("left", "1.0.0"),
        ("right", "1.0.0"),
    ]


def test_registry_reports_true_constraint_conflict() -> None:
    left = PackageMetadata("left", "1.0.0", "1" * 64, dependencies={"b": ">=2"})
    right = PackageMetadata("right", "1.0.0", "2" * 64, dependencies={"b": "<2"})
    b = PackageMetadata("b", "1.0.0", "3" * 64)
    with pytest.raises(RegistryError, match="ConflictingConstraint"):
        PackageResolver(RegistryIndex((left, right, b))).resolve({"left": "*", "right": "*"})

@pytest.mark.parametrize(
    ("left_constraint", "right_constraint", "expected"),
    [
        (">=2", "<2", "ConflictingConstraint"),
        (">=2", "<3", "UnsatisfiedConstraint"),
        (">=2", None, "UnsatisfiedConstraint"),
        ("=2", "=3", "ConflictingConstraint"),
        ("=2", "!=2", "ConflictingConstraint"),
        (">=1.0.0 <1.0.1 !=1.0.0", None, "ConflictingConstraint"),
        (">1.0.0 <1.0.2 !=1.0.1", None, "ConflictingConstraint"),
        ("1.0.x >=1.0.0-alpha <1.0.0", None, "UnsatisfiedConstraint"),
    ],
)
def test_registry_classifies_no_candidate_constraint_intersections(
    left_constraint: str,
    right_constraint: str | None,
    expected: str,
) -> None:
    b = PackageMetadata("b", "1.0.0", "3" * 64)
    left = PackageMetadata("left", "1.0.0", "1" * 64, dependencies={"b": left_constraint})
    packages = [b, left]
    roots = {"left": "*"}
    if right_constraint is not None:
        right = PackageMetadata("right", "1.0.0", "2" * 64, dependencies={"b": right_constraint})
        packages.append(right)
        roots["right"] = "*"
    with pytest.raises(RegistryError) as raised:
        PackageResolver(RegistryIndex(tuple(packages))).resolve(roots)
    assert raised.value.code == expected

@pytest.mark.parametrize(
    "constraints",
    [
        ("x", ">=2", "<3"),
        ("1.x", ">=1.5", "<1.6"),
    ],
)
def test_registry_classifies_wildcard_intersections_as_unsatisfied(
    constraints: tuple[str, ...],
) -> None:
    b = PackageMetadata("b", "1.0.0", "3" * 64)
    packages = [b]
    roots = {}
    for index, constraint in enumerate(constraints):
        name = f"root{index}"
        packages.append(PackageMetadata(name, "1.0.0", f"{index + 1:x}" * 64, dependencies={"b": constraint}))
        roots[name] = "*"
    with pytest.raises(RegistryError) as raised:
        PackageResolver(RegistryIndex(tuple(packages))).resolve(roots)
    assert raised.value.code == "UnsatisfiedConstraint"

@pytest.mark.parametrize(
    "constraints",
    [
        ("<0",),
        ("<0-0",),
        ("=1.0.0-alpha", "1.x"),
    ],
)
def test_registry_rejects_empty_prerelease_and_exact_intersections(
    constraints: tuple[str, ...],
) -> None:
    b = PackageMetadata("b", "1.0.0", "3" * 64)
    packages = [b]
    roots = {}
    for index, constraint in enumerate(constraints):
        name = f"root{index}"
        packages.append(PackageMetadata(name, "1.0.0", f"{index + 1:x}" * 64, dependencies={"b": constraint}))
        roots[name] = "*"
    with pytest.raises(RegistryError, match="ConflictingConstraint"):
        PackageResolver(RegistryIndex(tuple(packages))).resolve(roots)




def test_registry_missing_transitive_candidate_is_unsatisfied() -> None:
    root = PackageMetadata("root", "1.0.0", "1" * 64, dependencies={"missing": "*"})
    with pytest.raises(RegistryError) as raised:
        PackageResolver(RegistryIndex((root,))).resolve({"root": "*"})
    assert raised.value.code == "UnsatisfiedConstraint"


def test_registry_accepts_prerelease_caret_and_tilde_lower_bounds() -> None:
    assert satisfies("1.0.0-alpha", "^1.0.0-alpha")
    assert satisfies("1.0.0", "^1.0.0-alpha")
    assert satisfies("1.0.0-alpha", "~1.0.0-alpha")
    assert not satisfies("1.0.0-alpha", "^1.0.0")
    index = RegistryIndex(
        (
            PackageMetadata("dep", "1.0.0-alpha", "1" * 64),
            PackageMetadata("dep", "1.0.0", "2" * 64),
        )
    )
    assert PackageResolver(index).resolve({"dep": "^1.0.0-alpha"}).packages[0].version == "1.0.0"


def test_registry_online_lock_revalidates_current_index_metadata() -> None:
    locked = PackageMetadata("demo", "1.0.0", "1" * 64)
    lock = ResolvedLock((locked,), (("demo", "^1"),))
    changed = PackageMetadata("demo", "1.0.0", "2" * 64)
    with pytest.raises(RegistryError, match="LockTampered"):
        PackageResolver(RegistryIndex((changed,))).resolve(lock=lock)


def test_registry_resolves_deep_chain_without_python_recursion() -> None:
    packages = []
    for index in reversed(range(1105)):
        dependencies = {f"p{index + 1:04d}": "*"} if index < 1104 else {}
        packages.append(PackageMetadata(f"p{index:04d}", "1.0.0", f"{index:064x}", dependencies=dependencies))
    lock = PackageResolver(RegistryIndex(tuple(packages))).resolve({"p0000": "*"})
    assert len(lock.packages) == 1105


def test_registry_lock_cycle_validation_is_iterative() -> None:
    first = PackageMetadata("a", "1.0.0", "1" * 64, dependencies={"b": "*"})
    second = PackageMetadata("b", "1.0.0", "2" * 64, dependencies={"a": "*"})
    lock = ResolvedLock((first, second), (("a", "*"),))
    with pytest.raises(RegistryError, match="DependencyCycle"):
        PackageResolver(RegistryIndex((first, second))).resolve(lock=lock)


@pytest.mark.parametrize("constraint", ["1.2.3.4", "1.*.2", ">=1.x", "1,,2", ""])
def test_registry_rejects_malformed_constraints(constraint: str) -> None:
    with pytest.raises(RegistryError, match="InvalidConstraint"):
        satisfies("1.0.0", constraint)


def test_registry_choice_is_deterministic_and_maximal() -> None:
    versions = tuple(
        PackageMetadata("b", version, f"{index:064x}")
        for index, version in enumerate(("1.0.0", "1.2.0", "1.1.0"), 1)
    )
    resolver = PackageResolver(RegistryIndex(versions))
    assert resolver.resolve({"b": "^1"}).packages[0].version == "1.2.0"
    assert resolver.resolve({"b": "^1"}).to_json() == resolver.resolve({"b": "^1"}).to_json()


def test_registry_offline_lock_uses_lock_closure_not_current_index(tmp_path) -> None:
    payload = _archive()
    package = PackageMetadata("demo", "1.0.0", hashlib.sha256(payload).hexdigest())
    cache = tmp_path / "cache"
    source = PackageResolver(RegistryIndex((package,)), transport=lambda _: payload, cache_dir=cache)
    source.fetch(package, url="https://registry.invalid/demo.zip")
    lock = source.resolve({"demo": "^1"}, offline=True)
    offline = PackageResolver(RegistryIndex(()), cache_dir=cache)
    assert offline.resolve(lock=lock, offline=True).to_json() == lock.to_json()

def test_registry_rejects_malformed_lock_metadata_and_cache_symlink(tmp_path) -> None:
    with pytest.raises(RegistryError, match="InvalidMetadata"):
        PackageMetadata.from_dict({"name": "bad", "version": "1.0.0", "sha256": "1" * 64, "size": "large"})
    with pytest.raises(RegistryError, match="InvalidLock"):
        ResolvedLock.from_json("[]")
    with pytest.raises(RegistryError, match="SchemaMismatch"):
        ResolvedLock.from_dict({"lockfile": 99, "packages": [], "roots": []})

    payload = _archive()
    package = PackageMetadata("demo", "1.0.0", hashlib.sha256(payload).hexdigest())
    resolver = PackageResolver(RegistryIndex((package,)), transport=lambda _: payload, cache_dir=tmp_path)
    target = resolver._cache_path(package)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(payload)
    target.symlink_to(outside)
    with pytest.raises(RegistryError, match="CacheTampered"):
        resolver.fetch(package, url="https://registry.invalid/demo.zip")


def test_registry_extracts_verified_archive_atomically(tmp_path) -> None:
    payload = _archive(content=b"source")
    package = PackageMetadata("demo", "1.0.0", hashlib.sha256(payload).hexdigest())
    resolver = PackageResolver(
        RegistryIndex((package,)),
        transport=lambda _: payload,
        cache_dir=tmp_path / "cache",
    )
    resolver.fetch(package, url="https://registry.invalid/demo.zip")
    destination = tmp_path / "installed"

    assert resolver.extract(package, destination) == destination.absolute()
    assert (destination / "src" / "main.mlo").read_bytes() == b"source"
    with pytest.raises(RegistryError, match="DestinationExists"):
        resolver.extract(package, destination)


def _scalar_mir():
    return {"functions": [{"name": "main", "parameters": [], "return_type": "UInt64", "effects": [], "instructions": [{"id": "x", "op": "const", "attributes": {"value": 7}}, {"op": "return", "operands": ["x"]}]}]}

def test_project_compiler_emits_selected_pure_wasm_entry(tmp_path) -> None:
    project = Project.create(tmp_path / "project")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn value() -> UInt64:\n"
        "    7\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"ok\")\n"
        "    Ok(\"ok\")\n",
        encoding="utf-8",
    )
    resolve_dependencies(project)
    output = tmp_path / "value.wasm"

    compilation = compile_project(
        project.root,
        emit_wasm=True,
        wasm_entry="value",
        wasm_output=output,
        require_interface_lock=False,
    )

    assert compilation.wasm is not None
    assert compilation.wasm.exports == ("value",)
    assert output.read_bytes() == compilation.wasm.wasm
    assert compilation.to_dict()["wasm"]["artifact_digest"] == compilation.wasm.artifact_digest



def test_wasm_scalar_artifact_is_deterministic_and_effects_reject() -> None:
    first = WasmBackend().compile(_scalar_mir(), source="scalar")
    second = WasmBackend().compile(_scalar_mir(), source="scalar")
    assert first.wasm == second.wasm
    assert first.wasm[:8] == b"\0asm\1\0\0\0"
    assert first.artifact_digest == hashlib.sha256(first.wasm).hexdigest()
    effectful = {"functions": [{"name": "main", "effects": ["fs.read"], "instructions": []}]}
    with pytest.raises(WasmCompileError, match="WasmEffectful"):
        WasmBackend().compile(effectful)


def test_wasm_rejects_unsafe_arithmetic_types_and_tampered_artifacts() -> None:
    checked = {
        "functions": [{
            "name": "main",
            "return_type": "UInt64",
            "effects": [],
            "instructions": [{
                "id": "sum",
                "op": "checked_uint64_add",
                "operands": ["left", "right"],
            }],
        }]
    }
    with pytest.raises(WasmCompileError, match="WasmCheckedArithmeticUnsupported"):
        WasmBackend().compile(checked)
    with pytest.raises(WasmCompileError, match="WasmUnsupportedType"):
        WasmBackend().compile({"functions": [{"name": "main", "return_type": "Text", "instructions": []}]})
    with pytest.raises(WasmCompileError, match="WasmUnknownEntry"):
        WasmBackend().compile(_scalar_mir(), entry="missing")

    i32 = {
        "functions": [{
            "name": "main",
            "return_type": "UInt32",
            "instructions": [
                {"id": "x", "op": "const", "type": "UInt32", "attributes": {"value": 2**32 - 1}},
                {"op": "return", "operands": ["x"]},
            ],
        }]
    }
    artifact = WasmBackend().compile(i32)
    with pytest.raises(WasmCompileError, match="WasmConstantOutOfRange"):
        WasmBackend().compile({
            "functions": [{
                "name": "main",
                "return_type": "UInt32",
                "instructions": [{"id": "x", "op": "const", "attributes": {"value": 2**32}}],
            }]
        })
    with pytest.raises(WasmCompileError, match="WasmArtifactTampered"):
        type(artifact)(
            artifact.wasm + b"x",
            artifact.source_digest,
            artifact.compiler_digest,
            artifact.artifact_digest,
            artifact.exports,
        )




def test_web_bundle_escapes_title_and_binds_hash() -> None:
    artifact = WasmBackend().compile(_scalar_mir(), source="scalar")
    manifest = WebFacetManifest("demo", routes=(Route("/", "Root"),), components=(Component("Root"),), capabilities=("dom",), wasm_sha256=artifact.artifact_digest, title="<unsafe>")
    bundle = WebBundler().bundle(manifest, artifact)
    assert bundle.file("module.wasm") == artifact.wasm
    assert bundle.hashes["module.wasm"] == artifact.artifact_digest
    assert b"<unsafe>" not in bundle.file("index.html")
    assert b"Content-Security-Policy" in bundle.file("index.html")
    js_integrity = base64.b64encode(
        bytes.fromhex(bundle.hashes["app.js"])
    )
    assert b'integrity="sha256-' + js_integrity + b'"' in bundle.file("index.html")
    wasm_integrity = base64.b64encode(bytes.fromhex(artifact.artifact_digest))
    assert b'"sha256-' + wasm_integrity + b'"' in bundle.file("app.js")
    with pytest.raises(WebFacetError, match="ArtifactTampered"):
        WebBundler().bundle(manifest, artifact.wasm + b"x")
    assert json.loads(bundle.file("manifest.json"))["wasm_sha256"] == artifact.artifact_digest
    with pytest.raises(TypeError):
        bundle.files["app.js"] = b"tampered"
    with pytest.raises(WebFacetError, match="InvalidArtifact"):
        WebBundler().bundle(manifest, artifact, wasm_name="..\\outside.wasm")
