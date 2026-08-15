from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from merlo.refactor import ChangeIR, ChangeTarget, RefactorEdit, preview_move
from merlo.semantic_impact import (
    SEMANTIC_IMPACT_CONTRACT,
    ImpactDiagnostic,
    SemanticImpactReport,
    compute_semantic_impact,
)
from merlo.semantic_world import SemanticWorld, WorldError


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _world(tmp_path: Path) -> SemanticWorld:
    src = tmp_path / "src.mlo"
    other = tmp_path / "other.mlo"
    test = tmp_path / "tests" / "smoke.mlo"
    src.write_text("fn a\nfn b\n", encoding="utf-8")
    other.write_text("fn c\nfn d\n", encoding="utf-8")
    test.parent.mkdir()
    test.write_text("test smoke\n", encoding="utf-8")
    def symbol(sid: str, name: str, path: Path, line: int, exported: bool, module: str) -> dict[str, object]:
        return {
            "symbol_id": sid,
            "name": name,
            "qualified_name": f"{module}.{name}",
            "module": module,
            "kind": "function",
            "exported": exported,
            "public": exported,
            "signature": "() -> Unit",
            "revision_id": f"rev-{sid}",
            "interface_revision_id": f"iface-{module}",
            "implementation_revision_id": f"impl-{module}",
            "source": {"path": str(path), "line": line, "column": 0, "end_line": line + 1, "end_column": 0},
            "definition": f"fn {name}\n",
            "types": [], "effects": [], "capabilities": [], "requirements": [], "ensures": [], "invariants": [], "holes": [], "obligations": [], "ownership": [], "resources": [],
        }
    symbols = [
        symbol("sym-a", "a", src, 1, True, "app"),
        symbol("sym-b", "b", src, 2, False, "app"),
        symbol("sym-c", "c", other, 1, False, "other"),
        symbol("sym-d", "d", other, 2, True, "other"),
    ]
    modules = [
        {"name": "app", "path": str(src), "imports": ["other"], "symbols": ["sym-a", "sym-b"], "interface_revision_id": "iface-app", "implementation_revision_id": "impl-app"},
        {"name": "other", "path": str(other), "imports": [], "symbols": ["sym-c", "sym-d"], "interface_revision_id": "iface-other", "implementation_revision_id": "impl-other"},
    ]
    calls = [
        {"call_id": "call-ba", "caller_id": "sym-b", "callee_id": "sym-a", "source": {"path": str(src), "line": 2, "column": 0}},
        {"call_id": "call-cb", "caller_id": "sym-c", "callee_id": "sym-b", "source": {"path": str(other), "line": 1, "column": 0}},
        {"call_id": "call-ad", "caller_id": "sym-a", "callee_id": "sym-d", "source": {"path": str(src), "line": 1, "column": 0}},
    ]
    payload: dict[str, object] = {
        "schema_version": 11, "contract": "merlo.semantic-world.v11", "root": str(tmp_path), "entry_path": str(src), "versions": {},
        "source_hashes": {"src.mlo": hashlib.sha256(src.read_bytes()).hexdigest(), "other.mlo": hashlib.sha256(other.read_bytes()).hexdigest()},
        "lockfile_path": None, "lockfile_sha256": None, "modules": modules, "symbols": symbols, "revisions": [], "definitions": [], "references": [], "calls": calls, "types": [], "data_dependencies": [], "module_dependencies": [], "effects": [], "capabilities": [], "ownership": [], "resources": [], "interfaces": [], "obligations": [], "range_analysis": {}, "bounded_symbolic": {}, "smt": {}, "property_evidence": {}, "verification_metrics": {},
        "tests": [{"path": str(test), "name": "smoke", "source_sha256": hashlib.sha256(test.read_bytes()).hexdigest()}],
    }
    payload["world_digest"] = _digest(payload)
    return SemanticWorld(tmp_path, tmp_path / ".merlo" / "world.json", payload)


def _change(world: SemanticWorld, *, symbol_id: str = "sym-a") -> ChangeIR:
    target = world.resolve(symbol_id)
    edit = RefactorEdit(str(Path(target["source"]["path"]).resolve()), 0, 1, "x", symbol_id, "definition", "syntax", "token", 0)
    return ChangeIR(
        operation="rename", status="ready",
        target=ChangeTarget(symbol_id, target["revision_id"], target["interface_revision_id"], target["implementation_revision_id"]),
        expected_world_digest=world.digest, edits=(edit,), world=world,
    )


def test_rename_impact_partitions_symbols_and_reasons(tmp_path: Path) -> None:
    world = _world(tmp_path)
    report = compute_semantic_impact(world, _change(world))
    assert report.contract == SEMANTIC_IMPACT_CONTRACT
    assert [item.symbol_id for item in report.directly_changed] == ["sym-a", "sym-b"]
    assert [item.symbol_id for item in report.transitively_affected] == ["sym-c", "sym-d"]
    assert report.callers == ("sym-b", "sym-c")
    assert report.callees == ("sym-a", "sym-d")
    assert "sym-a" not in report.callers
    assert {edge.reason for edge in report.edges} == {"caller", "callee", "dependency"}
    assert report.public_interface_revision_ids == ("iface-app",)
    assert {item.path for item in report.files} == {str(tmp_path / "src.mlo"), str(tmp_path / "other.mlo")}
    assert report.tests[0].name == "smoke"


def test_private_direct_change_has_no_public_interface_or_tests(tmp_path: Path) -> None:
    world = _world(tmp_path)
    symbol = dict(world.data["symbols"][0])
    symbol["exported"] = False
    symbol["public"] = False
    data = dict(world.data)
    data["symbols"] = [symbol, *world.data["symbols"][1:]]
    data.pop("world_digest")
    data["world_digest"] = _digest(data)
    private_world = SemanticWorld(world.root, world.state_path, data)
    report = compute_semantic_impact(private_world, _change(private_world))
    assert report.interfaces == ()
    assert report.tests == ()


def test_roundtrip_and_tamper_detection(tmp_path: Path) -> None:
    world = _world(tmp_path)
    report = compute_semantic_impact(world, _change(world))
    assert SemanticImpactReport.from_json(report.to_json()) == report
    payload = report.to_dict()
    payload["status"] = "unsupported"
    with pytest.raises(WorldError, match="DigestMismatch"):
        SemanticImpactReport.from_dict(payload)



def test_noncanonical_records_are_rejected(tmp_path: Path) -> None:
    world = _world(tmp_path)
    report = compute_semantic_impact(world, _change(world))
    with pytest.raises(WorldError, match="NonCanonical"):
        SemanticImpactReport(
            world_digest=report.world_digest,
            change_digest=report.change_digest,
            target_symbol_id=report.target_symbol_id,
            target_revision_id=report.target_revision_id,
            target_interface_revision_id=report.target_interface_revision_id,
            target_implementation_revision_id=report.target_implementation_revision_id,
            status="ready",
            directly_changed=tuple(reversed(report.directly_changed)),
            transitively_affected=report.transitively_affected,
            callers=report.callers,
            references=report.references,
            callees=report.callees,
            dependencies=report.dependencies,
            edges=report.edges,
            files=report.files,
            tests=report.tests,
            interfaces=report.interfaces,
        )

def test_stale_or_mismatched_change_is_rejected(tmp_path: Path) -> None:
    world = _world(tmp_path)
    change = _change(world)
    object.__setattr__(change, "expected_world_digest", "wrong")
    with pytest.raises(WorldError, match="ChangeIRDigestMismatch"):
        compute_semantic_impact(world, change)


def test_unsupported_change_is_valid_empty_noop(tmp_path: Path) -> None:
    world = _world(tmp_path)
    report = compute_semantic_impact(world, preview_move(world, "sym-a", "other"))
    assert report.status == "unsupported"
    assert report.directly_changed == ()
    assert report.transitively_affected == ()
    assert report.files == () and report.tests == () and report.interfaces == ()
    assert isinstance(report.diagnostic, ImpactDiagnostic)
    assert report.diagnostic.code == "UnsupportedMigration"
