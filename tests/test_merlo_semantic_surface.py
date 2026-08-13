from __future__ import annotations

import json
import subprocess

import pytest

from merlo.semantic_surface import (
    SemanticSurfaceError,
    build_semantic_surface,
    compile_semantic_surface,
    elaborate_semantic_surface,
)
from merlo.semantic_surface_experiment import run_semantic_surface_experiment


SCRIPT = """n = args[0]
value = 1
checksum = 0
for i in 0..n:
    value = value * 1664525 + 1013904223
    checksum = checksum ^ (value + i)
checksum
"""


def expected_checksum(n: int) -> int:
    mask = (1 << 64) - 1
    value = 1
    checksum = 0
    for index in range(n):
        value = (value * 1664525 + 1013904223) & mask
        checksum = (checksum ^ (value + index)) & mask
    return checksum


def test_top_level_script_infers_types_and_mutability():
    result = elaborate_semantic_surface(SCRIPT, path="script.mlo")

    assert result.top_level_script is True
    assert "fn main(n: UInt64) -> UInt64:" in result.canonical_source
    assert "var value: UInt64 = 1" in result.canonical_source
    assert "var checksum: UInt64 = 0" in result.canonical_source
    assert "for i in 0..n:" in result.canonical_source
    assert result.inferred_annotation_count == 5
    assert result.inferred_mutability_count == 2


def test_mutated_input_gets_explicit_internal_storage():
    result = elaborate_semantic_surface(
        "n = args[0]\nx = args[1]\nfor i in 0..n:\n    x = x + 1\nx\n"
    )

    assert "fn main(n: UInt64, __input_x: UInt64) -> UInt64:" in result.canonical_source
    assert "var x: UInt64 = __input_x" in result.canonical_source


def test_ambiguous_and_conflicting_programs_are_rejected():
    with pytest.raises(SemanticSurfaceError, match="AmbiguousType"):
        compile_semantic_surface(
            "value = args[0]\nfn identity(item):\n    item\n\nidentity(value)\n"
        )
    with pytest.raises(SemanticSurfaceError, match="TypeConflict"):
        compile_semantic_surface(
            "flag = args[0]\nif flag:\n    flag = flag + 1\nflag\n"
        )


def test_concise_script_reaches_real_native_binary(tmp_path):
    build = build_semantic_surface(
        SCRIPT,
        output_dir=tmp_path,
        path="script.mlo",
        stem="script",
    )

    completed = subprocess.run(
        [str(build.native.binary_path), "10000"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert int(completed.stdout.strip()) == expected_checksum(10000)
    assert build.compilation.hir.entry_function == "main"
    assert build.compilation.mir.entry_function == "main"


def test_experiment_covers_four_domains_and_passes_gates(tmp_path):
    report = run_semantic_surface_experiment(
        output_dir=tmp_path / "run",
        report_path=tmp_path / "report.json",
        repetitions=5,
        warmups=1,
    )

    assert report["status"] == "SEMANTIC_COMPRESSION_SUPPORTED"
    assert all(report["gates"].values())
    assert {item["category"] for item in report["observations"]} == {
        "script",
        "research",
        "business",
        "systems",
    }
    assert all(
        item["surface"]["token_ratio_vs_python"] <= 1.0
        for item in report["observations"]
    )
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["report_sha256"] == report["report_sha256"]
