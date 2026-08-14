from __future__ import annotations

import os
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from merlo.frontend_model import ConciseApplicationError
from merlo.concise_services import (
    elaborate_concise_application,
    elaborate_concise_core,
)
from merlo.compiler import compile_project
from research.archive.alpha1.merlo.concise_application_milestone import (
    paired_corpus,
    run_concise_application_milestone,
)
from tools.benchmarks.merlo.concise_precedence import (
    PRECEDENCE_TABLE,
    canonical_expression,
    roundtrip_expression,
    validate_precedence_corpus,
)
from tools.benchmarks.merlo.script_arguments import (
    SCRIPT_ARGUMENT_TYPES,
    ScriptArgumentBoundary,
    ScriptArgumentError,
    parse_script_argument,
)
from tools.benchmarks.merlo.concise_surface_freeze import (
    CONCISE_SURFACE_VERSION,
    build_concise_surface_freeze,
    verify_concise_surface_freeze,
)
from merlo.runtime_contract import ALPHA_EFFECTS
from tools.benchmarks.merlo.deterministic_map import (
    DeterministicTextUInt64Map,
    FNV1A64_OFFSET_BASIS,
    MAP_MAX_CAPACITY,
    deterministic_text_hash,
    map_contract,
)
from merlo.native_c_backend import CEmitter, compile_c_source
from research.archive.alpha1.merlo.native_hir import (
    compile_native_hir,
    lower_native_hir_to_performance,
)
from tools.benchmarks.merlo.performance_opt import optimize_mir
from research.archive.alpha1.merlo.semantic_surface import build_semantic_surface
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir


ENTRY = Path("src/merlo/programs/concise_json/app/main.mlo")


def copy_application(tmp_path: Path) -> Path:
    source = ENTRY.parent.parent
    destination = tmp_path / "project"
    shutil.copytree(source, destination)
    return destination / "app" / "main.mlo"


def test_formal_precedence_roundtrip_covers_generated_corpus():
    report = validate_precedence_corpus(1024)

    assert len(PRECEDENCE_TABLE) == 12
    assert report["count"] == 1024
    assert report["all_semantic_ast_equal"] is True
    assert roundtrip_expression("checksum ^ (value + i)").equal
    assert roundtrip_expression("items[i + 1] ^ f(a * b)").equal
    assert canonical_expression("(a + b) * c") == "(a + b) * c"


def test_multifile_application_expands_to_one_machine_core():
    elaborated = elaborate_concise_application(ENTRY)

    assert elaborated.modules == ("app.json", "app.stats", "app.main")
    assert elaborated.semantic_ast_equal is True
    assert elaborated.canonical_reference_equal is True
    assert elaborated.effects == ("console.write", "fs.read")
    assert elaborated.capabilities == ("console.write", "fs.read")
    assert elaborated.interface_lock_valid is True
    assert "task main(path: Path)" in elaborated.canonical_source
    assert elaborated.machine_source == elaborated.canonical_source
    assert "let data: Bytes = fs.read(path)?" in elaborated.canonical_source
    assert "Any" not in elaborated.canonical_source


def test_bare_imported_function_can_be_passed_as_callback(tmp_path: Path) -> None:
    root = tmp_path / "callbacks"
    (root / "app").mkdir(parents=True)
    (root / "lib.mlo").write_text(
        "module lib\n\n"
        "export fn increment(value: Int) -> Int:\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    entry = root / "app" / "main.mlo"
    entry.write_text(
        "module app.main\n\n"
        "use lib\n\n"
        "fn apply(callback: Fn[Int,Int], value: Int) -> Int:\n"
        "    return callback(value)\n\n"
        "export task main(path: Path) -> Int:\n"
        "    uses console.write\n"
        "    console.write(\"\")\n"
        "    return apply(increment, 1)\n",
        encoding="utf-8",
    )

    elaborated = elaborate_concise_application(
        entry, require_interface_lock=False
    )

    assert "__increment" in elaborated.canonical_source


def test_concise_surface_version_zero_two_is_frozen_and_verified():
    freeze = build_concise_surface_freeze()
    verification = verify_concise_surface_freeze()

    assert CONCISE_SURFACE_VERSION == "0.2"
    assert freeze["surface_version"] == "0.2"
    assert freeze["status"] == "CONCISE_SURFACE_V0_2_FROZEN"
    assert freeze["grammar"]["forms"] == [
        "module",
        "use",
        "capitalized record",
        "enum",
        "inferred function expression",
        "inferred function block",
        "plain binding",
        "if",
        "while",
        "for",
        "match case",
        "print",
        "postfix try",
        "tail result",
    ]
    assert freeze["operator_precedence"]["table"] == [
        list(item) for item in PRECEDENCE_TABLE
    ]
    assert freeze["type_inference"]["public_signatures"] == (
        "inference may use internal call sites, but every inferred public "
        "parameter and return type is materialized in canonical source"
    )
    assert freeze["bindings"]["let"] == "exactly one whole-function assignment"
    assert freeze["bindings"]["var"] == "two or more whole-function assignments"
    assert freeze["task_effects"]["allowed"] == sorted(ALPHA_EFFECTS)
    assert freeze["ownership"]["ordinary_lifetime_annotations"] == 0
    assert freeze["canonical_expansion"]["semantic_ast_equal"] is True
    assert freeze["diagnostics"]["source_projection"] == (
        "generated canonical lines map to concise module paths and source lines"
    )
    assert freeze["syntax_version_change"]["explicit_reason_required"] is True
    assert verification.ok is True
    assert verification.mismatches == ()



def test_deterministic_map_text_uint64_owns_keys_and_preserves_insertion_order():
    counts = DeterministicTextUInt64Map()
    expected = {}
    for index in range(80):
        key = f"service-{index}"
        counts.increment(key, index + 1)
        expected[key] = index + 1
    counts.insert("service-7", 700)
    expected["service-7"] = 700
    counts.increment("service-8", 8)
    expected["service-8"] += 8

    assert counts.get("service-7") == 700
    assert counts.get("missing") is None
    assert counts.entries() == tuple(expected.items())
    assert counts.capacity >= 128
    assert counts.owned_key_count == len(expected)
    assert counts.lookup_key_copies == 0

    counts.close()
    assert counts.owned_key_count == 0


def test_deterministic_map_collision_growth_views_and_overflow_are_checked():
    same_bucket = [
        key
        for key in (f"k{index}" for index in range(1000))
        if deterministic_text_hash(key) & 7 == 0
    ][:3]
    counts = DeterministicTextUInt64Map(initial_capacity=8)
    for index, key in enumerate(same_bucket):
        counts.insert(key, index)
    assert counts.entries() == tuple(zip(same_bucket, range(3), strict=True))

    view = counts.borrow_entries()
    with pytest.raises(RuntimeError, match="MapReallocationDuringActiveView"):
        for index in range(10):
            counts.insert(f"grow-{index}", index)
    view.close()

    with pytest.raises(OverflowError, match="MapUInt64Overflow"):
        counts.increment(same_bucket[0], 1 << 64)
    with pytest.raises(OverflowError, match="MapCapacityOverflow"):
        DeterministicTextUInt64Map(initial_capacity=MAP_MAX_CAPACITY + 1)


def test_deterministic_map_contract_freezes_hash_collision_and_growth_policy():
    contract = map_contract()

    assert deterministic_text_hash("") == FNV1A64_OFFSET_BASIS
    assert contract["key_type"] == "Text"
    assert contract["value_type"] == "UInt64"
    assert contract["hash"] == "FNV-1a-64 over UTF-8 bytes"
    assert contract["collision"] == "open addressing with linear probing"
    assert contract["growth"] == "double at 75 percent occupancy"
    assert contract["duplicate"] == "replace value without changing insertion order"
    assert contract["iteration"] == "insertion order"


def test_concise_map_text_uint64_elaborates_without_dynamic_typing():
    result = elaborate_concise_core(
        "fn main(key: Text) -> UInt64:\n"
        "    let counts = Map.new()\n"
        "    counts.increment(key)\n"
        "    return counts.get(key)\n"
    )

    assert "let counts: Map[Text,UInt64] = Map.new()" in result["canonical_source"]
    assert "Any" not in result["canonical_source"]

def test_every_json_function_preserves_the_compiler_lineage():
    concise = compile_project(ENTRY)

    assert concise.representation.source_hir_digest == concise.hir.digest
    assert concise.mir.source_hir_digest == concise.hir.digest
    assert (
        concise.mir.representation_ir_digest
        == concise.representation.digest
    )
    assert (
        concise.optimized_mir.representation_ir_digest
        == concise.representation.digest
    )
    assert {
        Path(item["concise"]["path"]).name
        for item in concise.diagnostic_source_map
    } == {"json.mlo", "main.mlo", "stats.mlo"}
    assert "system(" not in concise.generated_c
    assert "socket(" not in concise.generated_c
    assert "fopen(name, \"rb\")" in concise.generated_c
    assert concise.generated.domain_opaque_calls == ()
    hir_functions = {
        item.name: item
        for item in concise.hir.functions
    }
    rir_functions = {
        item.name: item
        for item in concise.representation.functions
    }
    mir_functions = {
        item.name: item
        for item in concise.mir.functions
    }
    assert hir_functions.keys() == rir_functions.keys() == mir_functions.keys()
    for name, hir_function in hir_functions.items():
        rir_function = rir_functions[name]
        mir_function = mir_functions[name]
        assert (
            hir_function.symbol_id
            == rir_function.symbol_id
            == mir_function.symbol_id
        )
        assert hir_function.revision_id.startswith("rev_")
        assert rir_function.revision_id.startswith("rev_")
        assert mir_function.revision_id.startswith("rev_")
        assert hir_function.effects == rir_function.effects == mir_function.effects
        parameters = tuple(
            (item.name, item.type_name, item.ownership)
            for item in hir_function.parameters
        )
        assert parameters == rir_function.parameters == mir_function.parameters


def test_concise_json_cli_reads_path_without_debug_side_channels(tmp_path: Path):
    payload = tmp_path / "input.json"
    payload.write_text('{"a":[1,true,null]}', encoding="utf-8")
    build = compile_project(ENTRY, emit_native=True, output=tmp_path / "app")

    completed = subprocess.run(
        [str(build.native.binary_path), str(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == (
        "OK checksum=15459945301471017088 nodes=5 "
        "arrays=1 objects=1 fields=1\n"
    )
    assert completed.stderr == ""


def test_concise_json_cli_returns_typed_host_errors(tmp_path: Path):
    build = compile_project(ENTRY, emit_native=True, output=tmp_path / "app")

    missing = subprocess.run(
        [str(build.native.binary_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    unreadable = subprocess.run(
        [str(build.native.binary_path), str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 64
    assert missing.stderr == "AppError.MissingArgument: expected one Path\n"
    assert unreadable.returncode == 74
    assert unreadable.stderr.startswith("AppError.ReadFailure:")


    payload = tmp_path / "input.json"
    payload.write_text("[1,2,3]", encoding="utf-8")
    output = tmp_path / "application"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path.cwd() / "src") + os.pathsep + environment.get("PYTHONPATH", "")

    check = subprocess.run(
        ["python3", "-m", "merlo", "check", str(ENTRY)],
        capture_output=True,
        text=True,
        env=environment,
    )
    expand = subprocess.run(
        ["python3", "-m", "merlo", "expand", str(ENTRY)],
        capture_output=True,
        text=True,
        env=environment,
    )
    explain = subprocess.run(
        ["python3", "-m", "merlo", "explain", str(ENTRY)],
        capture_output=True,
        text=True,
        env=environment,
    )
    build = subprocess.run(
        ["python3", "-m", "merlo", "build", str(ENTRY), "-o", str(output)],
        capture_output=True,
        text=True,
        env=environment,
    )
    run = subprocess.run(
        ["python3", "-m", "merlo", "run", str(ENTRY), "--", str(payload)],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert check.returncode == 0 and check.stdout.startswith("ok ")
    assert expand.returncode == 0 and "task main(path: Path)" in expand.stdout
    assert explain.returncode == 0
    assert f"path: {ENTRY.resolve()}" in explain.stdout
    assert "semantic digest:" in explain.stdout
    assert "semantic AST preserved: yes" in explain.stdout
    assert build.returncode == 0 and output.is_file()
    assert run.returncode == 0 and run.stdout.startswith("OK checksum=")


def test_checked_argument_parsers_support_only_six_types():
    assert SCRIPT_ARGUMENT_TYPES == {
        "Text", "UInt64", "Int64", "Float64", "Bool", "Path"
    }
    assert parse_script_argument("Text", "Merlo") == "Merlo"
    assert parse_script_argument("UInt64", str((1 << 64) - 1)) == (1 << 64) - 1
    assert parse_script_argument("Int64", str(-(1 << 63))) == -(1 << 63)
    assert parse_script_argument("Float64", "-1.25e2") == -125.0
    assert parse_script_argument("Bool", "false") is False
    assert str(parse_script_argument("Path", "data/input.json")) == "data/input.json"
    assert ScriptArgumentBoundary(0, "n", "UInt64").canonical_source == (
        "let n: UInt64 = args.parse<UInt64>(0)?"
    )
    for type_name, raw in (
        ("UInt64", "-1"),
        ("UInt64", str(1 << 64)),
        ("Int64", str(1 << 63)),
        ("Float64", "nan"),
        ("Bool", "yes"),
        ("Path", ""),
    ):
        with pytest.raises(ScriptArgumentError, match="ArgumentParseError"):
            parse_script_argument(type_name, raw)
    with pytest.raises(ScriptArgumentError, match="UnsupportedArgumentType"):
        parse_script_argument("Decimal", "1")


def test_native_uint64_boundary_parse_is_checked(tmp_path: Path):
    build = build_semantic_surface(
        "n = args[0]\nn + 1\n",
        output_dir=tmp_path,
        path="checked.mlo",
        stem="checked",
    )

    valid = subprocess.run(
        [str(build.native.binary_path), "41"],
        capture_output=True,
        text=True,
        check=False,
    )
    invalid = subprocess.run(
        [str(build.native.binary_path), "not-a-number"],
        capture_output=True,
        text=True,
        check=False,
    )
    overflow = subprocess.run(
        [str(build.native.binary_path), str(1 << 64)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert valid.returncode == 0 and valid.stdout == "42\n"
    assert invalid.returncode == 2
    assert invalid.stderr == "ArgumentParseError: index=0 expected=UInt64\n"
    assert overflow.returncode == 2


def test_native_checked_int_float_and_bool_boundaries(
    tmp_path: Path,
):
    specifications = {
        "int": (
            "fn main(value: Int64) -> Int64:\n"
            "    return value\n",
            "-7",
            "-7\n",
        ),
        "float": (
            "fn main(value: Float64) -> Float64:\n"
            "    return value\n",
            "1.25",
            "1.25\n",
        ),
        "bool": (
            "fn main(value: Bool) -> Bool:\n"
            "    return value\n",
            "true",
            "1\n",
        ),
    }
    for name, (source, argument, expected) in specifications.items():
        hir = compile_native_hir(source, path=f"{name}.mlo")
        optimized, _ = optimize_mir(
            lower_native_hir_to_performance(hir)
        )
        build = compile_c_source(
            CEmitter(optimized, runtime_arguments=True).emit(),
            output_dir=tmp_path,
            stem=name,
        )
        valid = subprocess.run(
            [str(build.binary_path), argument],
            capture_output=True,
            text=True,
            check=False,
        )
        invalid = subprocess.run(
            [str(build.binary_path), "invalid"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert valid.returncode == 0
        assert valid.stdout == expected
        assert invalid.returncode == 2
        assert "ArgumentParseError" in invalid.stderr


def test_records_payload_enums_vec_option_and_result_elaborate():
    source = """record Event:
    level: Text
    duration: UInt64

enum AppError:
    MissingArgument
    InvalidJson: UInt64

fn collect(value: UInt64) -> Vec[UInt64]:
    let values = Vec.new()
    values.push(value)
    return values

fn main(event: Event) -> UInt64:
    match AppError.InvalidJson(event.duration):
        case AppError.MissingArgument:
            return 0
        case AppError.InvalidJson(offset):
            return offset
"""
    result = elaborate_concise_core(source, path="forms.mlo")

    assert result["semantic_ast_equal"] is True
    assert (
        "let values: Vec[UInt64] = Vec.new()"
        in result["canonical_source"]
    )
    assert "record Event:" in result["canonical_source"]
    assert "enum AppError:" in result["canonical_source"]


def test_option_and_result_reach_hir_as_structural_types():
    source = """enum AppError:
    Invalid

fn optional(value: UInt64) -> Option[UInt64]:
    if value == 0:
        return None
    return Some(value)

fn result(value: UInt64) -> Result[UInt64, AppError]:
    if value == 0:
        return Err(AppError.Invalid)
    return Ok(value)

fn unwrap(value: Option[UInt64]) -> UInt64:
    match value:
        case None:
            return 0
        case Some(item):
            return item

fn main(data: BytesView, value: UInt64) -> UInt64:
    let marker = Text.from_bytes(data, 0, 0)
    return unwrap(optional(value))
"""
    elaborated = elaborate_concise_core(
        source,
        path="sum-types.mlo",
    )
    hir = compile_canonical_hir(
        elaborated["canonical_program"],
        entry_function="main",
    )
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(
        hir,
        representation,
    )

    assert hir.function("optional").return_type == "Option[UInt64]"
    assert hir.function("result").return_type == "Result[UInt64,AppError]"
    assert "Option_UInt64_" not in elaborated["canonical_source"]
    assert "Result_UInt64_AppError_" not in elaborated["canonical_source"]
    assert mir.representation_ir_digest == representation.digest




def test_ambiguity_effect_escalation_and_deferred_features_are_rejected():
    with pytest.raises(ConciseApplicationError, match="AmbiguousType"):
        elaborate_concise_core("fn identity(value):\n    return value\n")
    with pytest.raises(ConciseApplicationError, match="EffectInPureFunction"):
        elaborate_concise_core(
            "fn main(path: Path) -> Bytes:\n    return fs.read(path)\n"
        )
    with pytest.raises(
        ConciseApplicationError,
        match="RecursiveBoundaryAnnotationRequired",
    ):
        elaborate_concise_core(
            "fn loop(value):\n    return loop(value)\n"
        )
    with pytest.raises(
        ConciseApplicationError,
        match="NonExhaustiveMatch",
    ):
        elaborate_concise_core(
            "enum Choice:\n    A\n    B\n"
            "fn main(value: Choice) -> UInt64:\n"
            "    match value:\n"
            "        case Choice.A:\n"
            "            return 0\n"
        )
    inferred_public = elaborate_concise_core(
        "export fn double(value):\n"
        "    return value + value\n"
        "fn main(n: UInt64) -> UInt64:\n"
        "    return double(n)\n"
    )
    assert (
        "fn double(value: UInt64) -> UInt64:"
        in inferred_public["canonical_source"]
    )
    with pytest.raises(ConciseApplicationError, match="DynamicAnyForbidden"):
        elaborate_concise_core("fn main(value: Any) -> Any:\n    return value\n")
    with pytest.raises(ConciseApplicationError, match="UnsupportedMapType"):
        elaborate_concise_core("fn main(values: Map[Text, Text]) -> UInt64:\n    return 0\n")


def test_public_interface_drift_is_a_check_error(tmp_path: Path):
    entry = copy_application(tmp_path)
    lock = entry.parent.parent / ".merlo-interface.json"
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["interfaces"][0]["return_type"] = "UInt64"
    lock.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConciseApplicationError, match="PublicInterfaceRevisionMismatch"):
        elaborate_concise_application(entry)


def test_paired_corpus_keeps_unfavorable_real_application_case():
    corpus = paired_corpus()

    assert len(corpus) >= 30
    assert len({item.category for item in corpus}) >= 12
    assert any(item.id == "real_general_json_cli" for item in corpus)
    assert all(item.concise and item.canonical and item.python for item in corpus)


def test_integrated_decision_run_meets_every_gate(tmp_path: Path):
    report = run_concise_application_milestone(
        artifact_dir=tmp_path / "artifacts",
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == "CONCISE_APPLICATION_SURFACE_SUPPORTED"
    assert all(report["gates"].values())
    assert report["correctness"]["valid_count"] >= 1000
    assert report["correctness"]["invalid_count"] >= 600
    assert report["falsification"]["detected"] == 11
    assert report["simplicity"]["lexical_ratio"]["median"] <= 0.80
    assert report["simplicity"]["punctuation_ratio"]["median"] <= 0.80
    assert report["performance"]["concise_surface_runtime_overhead"] == 0
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    claimed = saved.pop("report_sha256")
    actual = __import__("hashlib").sha256(
        json.dumps(
            saved,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert claimed == actual
