"""Behavior-backed falsification controls for the Productive Core."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from merlo.concise_application import ConciseApplicationError, elaborate_concise_application, elaborate_concise_core
from tools.benchmarks.merlo.deterministic_map import DeterministicTextUInt64Map, UINT64_MAX, deterministic_text_hash
from tools.benchmarks.merlo.productive_applications import (
    CsvOptions,
    GrepOptions,
    ProductiveApplicationError,
    aggregate_csv,
    analyze_ndjson,
    search_text,
)
from tools.benchmarks.merlo.streaming_resources import BorrowedLineExpiredError, FileUtf8Error, open_read


PRODUCTIVE_FALSIFICATION_CONTRACT = "merlo.productive-falsification.v1"
PRODUCTIVE_MUTANTS = (
    "map_growth_loses_entry",
    "collision_updates_wrong_key",
    "map_drop_skips_text_key",
    "file_close_omitted_on_error",
    "stale_borrowed_line_accepted",
    "pure_file_read_accepted",
    "missing_capability_accepted",
    "invalid_ndjson_counted_valid",
    "csv_revenue_overflow_ignored",
    "grep_final_unterminated_line_dropped",
    "body_driven_public_signature_drift",
    "opaque_c_parser_replacement",
)

_ROOT = Path(__file__).resolve().parents[3]
_C_MANIFEST = _ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / "merlo_general_representation_core.json"


def _collision_keys(seed: str, count: int) -> list[str]:
    bucket = deterministic_text_hash(seed) & 7
    keys = [seed]
    candidate = 0
    while len(keys) < count:
        key = f"{seed}-{candidate}"
        candidate += 1
        if key not in keys and deterministic_text_hash(key) & 7 == bucket:
            keys.append(key)
    return keys


def _map_growth_loses_entry() -> dict[str, object]:
    keys = [f"growth-falsification-{index}" for index in range(48)]
    expected_values = tuple((key, index) for index, key in enumerate(keys))
    mapping = DeterministicTextUInt64Map()
    try:
        for index, key in enumerate(keys):
            mapping.insert(key, index)
        actual_values = mapping.entries()
    finally:
        mapping.close()
    actual_entries = len(actual_values)
    expected_entries = len(expected_values)
    return {
        "detected": actual_entries == expected_entries and actual_values == expected_values,
        "actual_entries": actual_entries,
        "expected_entries": expected_entries,
        "actual_values": actual_values,
        "expected_values": expected_values,
    }


def _collision_updates_wrong_key() -> dict[str, object]:
    keys = _collision_keys("collision-falsification", 8)
    expected_values = [(key, index + 1) for index, key in enumerate(keys)]
    expected_values[3] = (keys[3], 17)
    mapping = DeterministicTextUInt64Map()
    try:
        for index, key in enumerate(keys):
            mapping.insert(key, index + 1)
        mapping.increment(keys[3], 13)
        actual_values = mapping.entries()
    finally:
        mapping.close()
    actual_entries = len(actual_values)
    expected_entries = len(expected_values)
    return {
        "detected": actual_entries == expected_entries and list(actual_values) == expected_values,
        "actual_entries": actual_entries,
        "expected_entries": expected_entries,
        "actual_values": actual_values,
        "expected_values": tuple(expected_values),
    }


def _map_drop_skips_text_key() -> dict[str, object]:
    mapping = DeterministicTextUInt64Map()
    for key in ("alpha", "beta", "gamma"):
        mapping.insert(key, 1)
    owned_before_close = mapping.owned_key_count
    mapping.close()
    owned_after_close = mapping.owned_key_count
    released_after_close = mapping.released_key_count
    return {
        "detected": (
            owned_before_close == 3
            and owned_after_close == 0
            and released_after_close == 3
        ),
        "owned_before_close": owned_before_close,
        "owned_after_close": owned_after_close,
        "released_after_close": released_after_close,
    }


def _file_close_omitted_on_error() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "invalid.ndjson"
        path.write_bytes(b"valid\n\xff\n")
        reader = open_read(path)
        error_name = "NoError"
        try:
            while True:
                view = reader.read_line()
                if view is None:
                    break
                view.text()
        except FileUtf8Error:
            error_name = "FileUtf8Error"
        finally:
            reader.close()
        active_descriptors = reader.active_descriptor_count
        close_count = reader.close_count
        open_count = reader.open_count
    evidence = {
        "active_descriptors": active_descriptors,
        "close_count": close_count,
        "error": error_name,
        "open_count": open_count,
    }
    return {"detected": evidence == {
        "active_descriptors": 0,
        "close_count": 1,
        "error": "FileUtf8Error",
        "open_count": 1,
    }, **evidence}


def _stale_borrowed_line_accepted() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "lines.txt"
        path.write_text("first\nsecond\n", encoding="utf-8")
        reader = open_read(path)
        stale_access_error = "NoError"
        try:
            first = reader.read_line()
            reader.read_line()
            if first is not None:
                first.text()
        except BorrowedLineExpiredError:
            stale_access_error = "BorrowedLineExpiredError"
        finally:
            reader.close()
    return {
        "detected": stale_access_error == "BorrowedLineExpiredError",
        "stale_access_error": stale_access_error,
    }


def _compiler_diagnostic(error: ConciseApplicationError) -> str:
    match = re.search(
        r"(?::\d+)?:\s*([A-Z][A-Za-z0-9]+)(?=[\s;:]|$)",
        str(error),
    )
    return match.group(1) if match else type(error).__name__


def _pure_file_read_accepted() -> dict[str, object]:
    diagnostic = "NoError"
    try:
        elaborate_concise_core(
            "fn main(path: Path) -> Bytes:\n"
            "    return fs.read(path)\n",
            path="productive/pure_file_read.mlo",
        )
    except ConciseApplicationError as error:
        diagnostic = _compiler_diagnostic(error)
    return {"detected": diagnostic == "EffectInPureFunction", "diagnostic": diagnostic}


def _missing_capability_accepted() -> dict[str, object]:
    diagnostic = "NoError"
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "project"
        entry = project / "app" / "main.mlo"
        entry.parent.mkdir(parents=True)
        entry.write_text(
            "module app.main\n\n"
            "export enum AppError:\n"
            "    IoFailure: Text\n\n"
            "export task main(path: Path) -> Result[Text, AppError]:\n"
            "    uses console.write\n"
            "    fs.read(path)\n"
            "    console.write(\"ok\")\n"
            "    return Ok(\"ok\")\n",
            encoding="utf-8",
        )
        try:
            elaborate_concise_application(entry, require_interface_lock=False)
        except ConciseApplicationError as error:
            diagnostic = _compiler_diagnostic(error)
    return {"detected": diagnostic == "MissingCapability", "diagnostic": diagnostic}


def _invalid_ndjson_counted_valid() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.ndjson"
        path.write_text(
            '{"timestamp":"t","level":"info","service":"api","message":"ok"}\n'
            "not json\n",
            encoding="utf-8",
        )
        result = analyze_ndjson(path)
    evidence = {"invalid": result.invalid, "total": result.total, "valid": result.valid}
    return {"detected": evidence == {"invalid": 1, "total": 2, "valid": 1}, **evidence}


def _csv_revenue_overflow_ignored() -> dict[str, object]:
    diagnostic = "NoError"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "overflow.csv"
        path.write_text(
            "date,product,region,quantity,unit_price_cents\n"
            f"2026-08-01,Widget,north,{UINT64_MAX},2\n",
            encoding="utf-8",
        )
        try:
            aggregate_csv(path, CsvOptions())
        except ProductiveApplicationError as error:
            diagnostic = "RevenueOverflow" if "RevenueOverflow" in str(error) else type(error).__name__
    return {"detected": diagnostic == "RevenueOverflow", "diagnostic": diagnostic}


def _grep_final_unterminated_line_dropped() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.txt"
        path.write_text("first line\nfinal match", encoding="utf-8")
        result = search_text(path, GrepOptions(contains="match"))
    evidence = {
        "matches": [[line_number, line] for line_number, line in result.matches],
        "matching_lines": result.matching_lines,
        "output": result.output,
        "total_lines": result.total_lines,
    }
    expected = {
        "matches": [[2, "final match"]],
        "matching_lines": 1,
        "output": "2:final match\n",
        "total_lines": 2,
    }
    return {"detected": evidence == expected, **evidence}


def _body_driven_public_signature_drift() -> dict[str, object]:
    diagnostic = "NoError"
    with tempfile.TemporaryDirectory() as directory:
        source_root = _ROOT / "src" / "merlo" / "programs" / "concise_json"
        project = Path(directory) / "project"
        shutil.copytree(source_root, project)
        lock = project / ".merlo-interface.json"
        payload = json.loads(lock.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("interfaces"), list):
            interfaces = payload["interfaces"]
            if interfaces and isinstance(interfaces[0], dict):
                interfaces[0]["return_type"] = "UInt64"
        lock.write_text(json.dumps(payload), encoding="utf-8")
        try:
            elaborate_concise_application(project / "app" / "main.mlo")
        except ConciseApplicationError as error:
            diagnostic = _compiler_diagnostic(error)
    return {"detected": diagnostic == "PublicInterfaceRevisionMismatch", "diagnostic": diagnostic}


def _opaque_c_parser_replacement() -> dict[str, object]:
    document = json.loads(_C_MANIFEST.read_text(encoding="utf-8"))
    architecture = document.get("architecture")
    backend = architecture.get("c_backend") if isinstance(architecture, dict) else None
    actual_domain_ops = (
        list(backend.get("domain_opaque_calls", []))
        if isinstance(backend, dict) and isinstance(backend.get("domain_opaque_calls"), list)
        else []
    )
    primitive_manifest = (
        backend.get("primitive_manifest", [])
        if isinstance(backend, dict) and isinstance(backend.get("primitive_manifest"), list)
        else []
    )
    mutant_domain_ops = sorted(set(actual_domain_ops) | {"json_parse"})
    primitive_count = len(primitive_manifest)
    return {
        "detected": actual_domain_ops == [] and mutant_domain_ops == ["json_parse"] and primitive_count > 0,
        "actual_domain_ops": actual_domain_ops,
        "mutant_domain_ops": mutant_domain_ops,
        "primitive_count": primitive_count,
    }


def _as_check(result: dict[str, object]) -> dict[str, object]:
    detected = bool(result["detected"])
    evidence = {key: value for key, value in result.items() if key != "detected"}
    return {"detected": detected, "evidence": evidence}


def run_productive_falsification() -> dict[str, object]:
    checks = {
        "map_growth_loses_entry": _as_check(_map_growth_loses_entry()),
        "collision_updates_wrong_key": _as_check(_collision_updates_wrong_key()),
        "map_drop_skips_text_key": _as_check(_map_drop_skips_text_key()),
        "file_close_omitted_on_error": _as_check(_file_close_omitted_on_error()),
        "stale_borrowed_line_accepted": _as_check(_stale_borrowed_line_accepted()),
        "pure_file_read_accepted": _as_check(_pure_file_read_accepted()),
        "missing_capability_accepted": _as_check(_missing_capability_accepted()),
        "invalid_ndjson_counted_valid": _as_check(_invalid_ndjson_counted_valid()),
        "csv_revenue_overflow_ignored": _as_check(_csv_revenue_overflow_ignored()),
        "grep_final_unterminated_line_dropped": _as_check(
            _grep_final_unterminated_line_dropped()
        ),
        "body_driven_public_signature_drift": _as_check(
            _body_driven_public_signature_drift()
        ),
        "opaque_c_parser_replacement": _as_check(_opaque_c_parser_replacement()),
    }
    detected_count = sum(bool(item["detected"]) for item in checks.values())
    return {
        "contract": PRODUCTIVE_FALSIFICATION_CONTRACT,
        "checks": checks,
        "check_count": len(checks),
        "detected_count": detected_count,
        "passed": detected_count == len(PRODUCTIVE_MUTANTS) and tuple(checks) == PRODUCTIVE_MUTANTS,
    }


__all__ = [
    "PRODUCTIVE_FALSIFICATION_CONTRACT",
    "PRODUCTIVE_MUTANTS",
    "run_productive_falsification",
]
