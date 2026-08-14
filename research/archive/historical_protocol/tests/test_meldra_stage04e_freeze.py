from __future__ import annotations

import json
import shutil
from pathlib import Path

from research.archive.historical_protocol.merlo.stage04e_freeze import (
    assert_stage04_frozen,
    load_stage04_freeze,
    verify_stage04_freeze,
)


ROOT = Path(__file__).parents[4]
_FROZEN_PATHS = (
    "research/archive/historical_protocol/merlo/core_semantics.py",
    "research/archive/alpha1/merlo/frontend_bench.py",
    "research/archive/historical_protocol/merlo/frontend_evaluator.py",
    "research/archive/historical_protocol/merlo/frontend_semantics.py",
    "research/archive/historical_protocol/merlo/frontend_syntax.py",
    "research/archive/historical_protocol/benchmarks/frozen/stage04/meldra/frontend_semantics.py",
    "research/archive/historical_protocol/benchmarks/frozen/stage04/meldra/frontend_syntax.py",
    "research/archive/historical_protocol/merlo/python_binder.py",
    "research/archive/alpha1/merlo/core_ir_schema_v1.json",
    "research/archive/alpha1/merlo/STAGE_0_4_FREEZE.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage04_support_profile.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage04_frontend_benchmark.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage04_freeze.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage04_freeze_lock.json",
)


def _copy_freeze(tmp_path: Path) -> Path:
    for relative in _FROZEN_PATHS:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def test_stage04_freeze_is_complete_and_byte_valid():
    manifest = load_stage04_freeze(ROOT)
    verification = assert_stage04_frozen(ROOT)

    assert verification.ok is True
    assert verification.freeze_id == manifest["freeze_id"]
    assert manifest["benchmark"]["generation_seed"] == 20260810
    assert manifest["grammar"]["version"] == 1
    assert manifest["core_ir"]["schema_version"] == 1
    assert set(manifest["semantics"]) == {
        "binding",
        "capabilities",
        "effects",
        "identity",
        "interfaces",
        "types",
    }


def test_freeze_detects_frozen_implementation_change(tmp_path: Path):
    root = _copy_freeze(tmp_path)
    syntax = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / "frozen" / "stage04" / "meldra" / "frontend_syntax.py"
    syntax.write_text(syntax.read_text(encoding="utf-8") + "# mutation\n", encoding="utf-8")

    verification = verify_stage04_freeze(root)

    assert verification.ok is False
    assert [item.subject for item in verification.mismatches] == [
        "implementation.frontend_syntax"
    ]


def test_freeze_ignores_active_frontend_evolution(tmp_path: Path):
    root = _copy_freeze(tmp_path)
    syntax = root / "research" / "archive" / "historical_protocol" / "merlo" / "frontend_syntax.py"
    semantics = root / "research" / "archive" / "historical_protocol" / "merlo" / "frontend_semantics.py"
    syntax.write_text(syntax.read_text(encoding="utf-8") + "# active syntax\n", encoding="utf-8")
    semantics.write_text(
        semantics.read_text(encoding="utf-8") + "# active semantics\n",
        encoding="utf-8",
    )

    assert verify_stage04_freeze(root).ok is True


def test_freeze_detects_posthoc_rule_change(tmp_path: Path):
    root = _copy_freeze(tmp_path)
    path = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_stage04_freeze.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["semantics"]["binding"]["rules"].append("posthoc exception")
    path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = verify_stage04_freeze(root)
    subjects = {item.subject for item in verification.mismatches}

    assert "freeze_id" in subjects
    assert "semantics.binding.sha256" in subjects


def test_freeze_detects_canonical_manifest_change(tmp_path: Path):
    root = _copy_freeze(tmp_path)
    path = root / "research" / "archive" / "alpha1" / "merlo" / "STAGE_0_4_FREEZE.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["versions"]["binder_rules"] = 2
    path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = verify_stage04_freeze(root)

    assert [item.subject for item in verification.mismatches] == [
        "canonical_lock.sha256"
    ]
