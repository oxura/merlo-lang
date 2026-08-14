from __future__ import annotations

from dataclasses import replace
from pathlib import Path


from research.archive.historical_protocol.merlo.analyzer import scan_python
from research.archive.historical_protocol.merlo.evidence import create_evidence
from research.archive.historical_protocol.merlo.incremental import profile_incremental
from research.archive.historical_protocol.merlo.model import Position, Reference, Resolution, Span


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _three_module_world(root: Path):
    _write(root, "api.py", "def compute(value):\n    return value + 1\n")
    _write(
        root,
        "consumer.py",
        "from api import compute\n\ndef use(value):\n    return compute(value)\n",
    )
    _write(root, "isolated.py", "def untouched(value):\n    return value * 2\n")
    return scan_python(root)


def test_single_file_semantic_closure_is_smaller_than_full_world(tmp_path: Path):
    old = _three_module_world(tmp_path)
    _write(tmp_path, "api.py", "def compute(value):\n    return value + 2\n")
    new = scan_python(tmp_path, old)

    profile = profile_incremental(old, new, full_scan_seconds=0.25)

    assert profile.changed_files == ("api.py",)
    assert "consumer.py" in profile.affected_file_paths
    assert "isolated.py" not in profile.affected_file_paths
    assert profile.file_ratio.affected < profile.file_ratio.total
    assert profile.entity_ratio.affected < profile.entity_ratio.total
    assert profile.timing.full_scan_measured
    assert not profile.timing.speedup_claimed


def test_public_change_expands_closure_through_dynamic_uncertainty(tmp_path: Path):
    _write(tmp_path, "public_api.py", "def exposed(value):\n    return value\n")
    _write(tmp_path, "private_api.py", "def _hidden(value):\n    return value\n")
    _write(tmp_path, "dynamic_user.py", "def invoke(value):\n    return value\n")
    old = scan_python(tmp_path)
    owner = old.entity("dynamic_user.invoke")
    dynamic = Reference(
        id="ref_dynamic_unknown",
        target_id=None,
        possible_target_ids=(),
        owner_id=owner.id,
        file="dynamic_user.py",
        span=Span(Position(2, 4), Position(2, 9)),
        kind="dynamic",
        expected="runtime binding",
        resolution=Resolution.DYNAMIC,
    )
    old = replace(old, references=old.references + (dynamic,)).with_world_revision()

    _write(tmp_path, "private_api.py", "def _hidden(value):\n    return value + 1\n")
    private_new = scan_python(tmp_path, old)
    private_new = replace(
        private_new, references=private_new.references + (dynamic,)
    ).with_world_revision()
    private_profile = profile_incremental(old, private_new)

    _write(tmp_path, "private_api.py", "def _hidden(value):\n    return value\n")
    _write(tmp_path, "public_api.py", "def exposed(value):\n    return value + 1\n")
    public_new = scan_python(tmp_path, old)
    public_new = replace(
        public_new, references=public_new.references + (dynamic,)
    ).with_world_revision()
    public_profile = profile_incremental(old, public_new)

    assert "ref_dynamic_unknown" not in private_profile.uncertain_reference_ids
    assert "ref_dynamic_unknown" in public_profile.uncertain_reference_ids
    assert owner.id in public_profile.affected_entity_ids
    assert len(public_profile.affected_entity_ids) > len(private_profile.affected_entity_ids)


def test_unchanged_world_has_zero_affected_ratios(tmp_path: Path):
    program = _three_module_world(tmp_path)

    profile = profile_incremental(program, program)

    assert profile.file_ratio.affected == 0
    assert profile.file_ratio.ratio == 0
    assert profile.entity_ratio.affected == 0
    assert profile.reference_ratio.affected == 0
    assert profile.call_ratio.affected == 0
    assert profile.theoretical_work_set == ()


def test_profile_lists_revision_invalidated_evidence_ids(tmp_path: Path):
    old = _three_module_world(tmp_path)
    evidence = create_evidence(
        old,
        "change-1",
        "StaticAnalysis",
        "Observed",
        "compute revision checked",
        entity_ids=(old.entity("api.compute").id,),
        files=("api.py",),
    )
    _write(tmp_path, "api.py", "def compute(value):\n    return value + 3\n")
    new = scan_python(tmp_path, old)

    profile = profile_incremental(old, new, evidence=(evidence,))

    assert profile.invalidated_evidence_ids == (evidence.id,)


