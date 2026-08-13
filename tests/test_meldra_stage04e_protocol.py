from __future__ import annotations

import json
import shutil
from pathlib import Path

from merlo.stage04e_protocol import (
    assert_stage04e_protocol,
    load_stage04e_protocol,
    load_stage04e_protocol_version,
    verify_stage04e_protocol,
)


ROOT = Path(__file__).parents[1]
_REQUIRED_FILES = (
    "merlo/core_semantics.py",
    "merlo/frontend_bench.py",
    "merlo/frontend_evaluator.py",
    "merlo/frontend_semantics.py",
    "merlo/frontend_syntax.py",
    "merlo/python_binder.py",
    "benchmarks/frozen/stage04/meldra/frontend_semantics.py",
    "benchmarks/frozen/stage04/meldra/frontend_syntax.py",
    "merlo/core_ir_schema_v1.json",
    "merlo/STAGE_0_4_FREEZE.json",
    "benchmarks/meldra_stage04_support_profile.json",
    "benchmarks/meldra_stage04_frontend_benchmark.json",
    "benchmarks/meldra_stage04_freeze.json",
    "benchmarks/meldra_stage04_freeze_lock.json",
    "benchmarks/meldra_stage04e_protocol.json",
    "benchmarks/meldra_stage04e_protocol_lock.json",
    "benchmarks/meldra_stage04e_protocol_v1.json",
    "benchmarks/meldra_stage04e_protocol_v1_lock.json",
    "benchmarks/meldra_stage04e_protocol_v2.json",
    "benchmarks/meldra_stage04e_protocol_v2_lock.json",
)


def _copy_protocol(tmp_path: Path) -> Path:
    for relative in _REQUIRED_FILES:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def test_stage04e_protocol_is_preregistered_and_locked():
    protocol = load_stage04e_protocol(ROOT)
    verification = assert_stage04e_protocol(ROOT)

    assert verification.ok is True
    assert protocol["language_alpha_go"]["all_required"] is True
    assert protocol["statistical_policy"]["primary_units"]["runtime_binding"] == (
        "runtime callsite scenario"
    )
    assert len(protocol["runtime_binding_categories"]) == 23
    assert protocol["corpus"]["selection"] == {
        "program_count_min": 30,
        "program_count_max": 50,
        "paired_change_count_min": 200,
        "adversarial_negative_count_min": 300,
        "runtime_observation_count_min": 300,
        "interface_change_count_min": 100,
        "capability_attack_count_min": 100,
        "external_safe_trials_each_operation_min": 30,
        "runtime_callsite_category_min": 23,
    }


def test_protocol_lock_detects_posthoc_gate_change(tmp_path: Path):
    root = _copy_protocol(tmp_path)
    path = root / "benchmarks" / "meldra_stage04e_protocol.json"
    protocol = json.loads(path.read_text(encoding="utf-8"))
    protocol["language_alpha_go"]["agent_value"]["success_gain_points_min"] = 0.0
    path.write_text(json.dumps(protocol), encoding="utf-8")

    verification = verify_stage04e_protocol(root)

    assert verification.ok is False
    subjects = {item.subject for item in verification.mismatches}
    assert subjects == {"lock.protocol_sha256", "v2.copy.sha256"}
    assert load_stage04e_protocol_version(1, ROOT)["schema_version"] == 1
    assert load_stage04e_protocol_version(2, ROOT)["schema_version"] == 2
