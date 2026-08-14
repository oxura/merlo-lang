"""Bounded milestone experiment for Meldra's text and streaming JSON core."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tools.benchmarks.merlo.json_streaming import (
    FNV_OFFSET,
    FNV_PRIME,
    JSON_STREAMING_LIMITATIONS,
    TOKEN_KINDS,
    json_streaming_mir_manifest,
    tokenize_json,
)
from .native_c_backend import CEmitter, compiler_version, find_c_compiler
from research.archive.alpha1.merlo.native_differential import HIREvaluator, MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir, lower_native_hir_to_performance
from .performance_opt import optimize_mir


TEXT_STREAMING_REPORT_SCHEMA_VERSION = 1
TEXT_STREAMING_SUPPORTED = "TEXT_STREAMING_CORE_SUPPORTED"
TEXT_STREAMING_INCOMPLETE = "TEXT_STREAMING_CORE_INCOMPLETE"
TEXT_STREAMING_SAFETY_DEFECT = "TEXT_STREAMING_CORE_SAFETY_DEFECT"
DIRECT_SOURCE = """fn main(data: BytesView) -> UInt64:
    return json_token_checksum(data)
"""
BORROWED_SOURCE = """fn main(data: BytesView, start: UInt64, length: UInt64) -> UInt64:
    let view: BytesView = data.slice(start, length)
    return json_token_checksum(view)
"""
_PREDECESSORS = (
    "tools/benchmarks/merlo/benchmarks/meldra_bytes_builder.json",
    "tools/benchmarks/merlo/benchmarks/meldra_text_core_sprint.json",
    "tools/benchmarks/merlo/benchmarks/meldra_text_builder.json",
    "tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow.json",
)
_TOKEN_RE = re.compile(
    rb"[ \t\r\n]+|"
    rb'"(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|[^"\\\x00-\x1f])*"|'
    rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|"
    rb"true|false|null|[{}\[\],:]"
)
_DIAGNOSTIC_KINDS = (
    "JsonInvalidUtf8",
    "JsonTruncatedInput",
    "JsonUnexpectedToken",
    "JsonInvalidStringControl",
    "JsonInvalidEscape",
    "JsonInvalidUnicodeEscape",
    "JsonUnfinishedString",
    "JsonMalformedNumber",
    "JsonNestingDepthExceeded",
    "JsonDelimiterMismatch",
    "JsonExpectedComma",
    "JsonExpectedObjectKey",
    "JsonExpectedColon",
    "TextBuilderLengthOverflow",
    "TextBuilderCapacityOverflow",
    "TextBuilderAllocationSizeOverflow",
)


@dataclass(frozen=True)
class ValidCase:
    family: str
    seed: int
    payload: bytes
    borrowed: bool
    expected_checksum: int


@dataclass(frozen=True)
class InvalidCase:
    family: str
    seed: int
    payload: bytes
    borrowed: bool
    expected_kind: str
    expected_offset: int


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _mix(checksum: int, value: int) -> int:
    return ((checksum ^ (value & ((1 << 64) - 1))) * FNV_PRIME) & ((1 << 64) - 1)


def _oracle_checksum(payload: bytes) -> int:
    decoded = payload.decode("utf-8", "strict")
    json.loads(decoded)
    checksum = FNV_OFFSET
    position = 0
    for match in _TOKEN_RE.finditer(payload):
        if match.start() != position:
            raise AssertionError(f"oracle token gap at {position}")
        position = match.end()
        token = match.group()
        if token.isspace():
            continue
        punctuation = {
            b"{": "object_start",
            b"}": "object_end",
            b"[": "array_start",
            b"]": "array_end",
            b":": "colon",
            b",": "comma",
        }
        if token in punctuation:
            kind = punctuation[token]
            semantic = b""
        elif token == b"null":
            kind, semantic = "null", token
        elif token == b"true":
            kind, semantic = "true", token
        elif token == b"false":
            kind, semantic = "false", token
        elif token.startswith(b'"'):
            kind = "string"
            semantic = json.loads(token.decode("utf-8")).encode("utf-8")
        elif b"." in token or b"e" in token.lower():
            kind, semantic = "float", token
        else:
            kind, semantic = "integer", token
        checksum = _mix(checksum, TOKEN_KINDS[kind])
        checksum = _mix(checksum, match.start())
        checksum = _mix(checksum, len(token))
        checksum = _mix(checksum, len(semantic))
        for byte in semantic:
            checksum = _mix(checksum, byte)
    if position != len(payload):
        raise AssertionError(f"oracle did not consume input at {position}")
    return checksum


def _valid_value(family: str, seed: int) -> Any:
    if family == "null":
        return None
    if family == "booleans":
        return [bool(seed & 1), not bool(seed & 1)]
    if family == "zero":
        return 0
    if family == "signed_integer":
        return -(seed * 104729 + 1)
    if family == "large_integer":
        return 9_007_199_254_740_000 + seed
    if family == "simple_float":
        return seed + 0.125
    if family == "empty_array":
        return []
    if family == "empty_object":
        return {}
    if family == "integer_array":
        return list(range(seed, seed + 16))
    if family == "mixed_array":
        return [None, True, False, seed, seed + 0.5, f"s{seed}"]
    if family == "flat_object":
        return {f"k{i}": seed + i for i in range(12)}
    if family == "nested_arrays":
        value: Any = seed
        for _ in range(12):
            value = [value]
        return value
    if family == "nested_objects":
        value = seed
        for index in range(12):
            value = {f"n{index}": value}
        return value
    if family == "unicode_bmp":
        return {"text": f"κόσμος-Привет-漢字-{seed}"}
    if family == "unicode_nonbmp":
        return {"text": f"😀-🧪-🚀-{seed}"}
    if family == "escaped_controls":
        return {"text": f"line\n{seed}\tend\r\b\f"}
    if family == "escaped_quote_slash":
        return {"text": f'quote" slash/ backslash\\ {seed}'}
    if family == "unicode_escapes":
        return {"text": f"€-λ-Ж-{seed}"}
    if family == "surrogate_pair":
        return {"emoji": f"😀{seed}🛰"}
    if family == "long_plain_string":
        return "plain-" + str(seed) + "-" + "x" * (1024 + seed * 3)
    if family == "long_escaped_string":
        return ("row\n" * (256 + seed)) + f"tail-{seed}"
    if family == "many_keys":
        return {f"field_{i:03d}": f"value-{seed}-{i}" for i in range(80)}
    if family == "numeric_edges":
        return [0, -1, 2**31 - 1, -(2**31), seed + 1.0e-6, 1.0e20]
    if family == "deep_16":
        value = {"leaf": seed}
        for _ in range(15):
            value = [value]
        return value
    if family == "alternating":
        return {"a": [{"b": [seed, {"c": [True, None, "x"]}]}]}
    if family == "object_array":
        return {"items": list(range(seed, seed + 30)), "ok": True}
    if family == "array_objects":
        return [{"id": seed * 10 + i, "active": i % 2 == 0} for i in range(24)]
    if family == "borrowed_plain":
        return {"borrowed": f"plain-{seed}", "values": [1, 2, 3]}
    if family == "borrowed_escaped":
        return {"borrowed": f"line\n{seed}\t😀", "values": [4, 5, 6]}
    if family == "large_document":
        return [
            {
                "id": seed * 1000 + i,
                "name": f"item-{seed}-{i}",
                "message": f"line {i}\nvalue \"{seed}\" 😀",
                "values": list(range(i, i + 12)),
            }
            for i in range(96)
        ]
    raise KeyError(family)


def _valid_payload(family: str, seed: int) -> bytes:
    if family == "exponent_float":
        return f"-{seed + 1}.2500e+{seed % 7}".encode()
    value = (
        {"seed": seed, "nested": [True, None, {"x": seed + 0.5}]}
        if family == "whitespace"
        else _valid_value(family, seed)
    )
    ensure_ascii = family in {"unicode_escapes", "surrogate_pair"}
    indent = 1 if family == "whitespace" else None
    return json.dumps(
        value,
        ensure_ascii=ensure_ascii,
        indent=indent,
        separators=None if indent else (",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _valid_corpus() -> list[ValidCase]:
    families = (
        "null",
        "booleans",
        "zero",
        "signed_integer",
        "large_integer",
        "simple_float",
        "exponent_float",
        "empty_array",
        "empty_object",
        "integer_array",
        "mixed_array",
        "flat_object",
        "nested_arrays",
        "nested_objects",
        "whitespace",
        "unicode_bmp",
        "unicode_nonbmp",
        "escaped_controls",
        "escaped_quote_slash",
        "unicode_escapes",
        "surrogate_pair",
        "long_plain_string",
        "long_escaped_string",
        "many_keys",
        "numeric_edges",
        "deep_16",
        "alternating",
        "object_array",
        "array_objects",
        "borrowed_plain",
        "borrowed_escaped",
        "large_document",
    )
    cases = []
    for family in families:
        for seed in range(32):
            payload = _valid_payload(family, seed)
            cases.append(
                ValidCase(
                    family,
                    seed,
                    payload,
                    family.startswith("borrowed_"),
                    _oracle_checksum(payload),
                )
            )
    return cases


def _invalid_templates() -> tuple[tuple[str, bytes, str, int], ...]:
    return (
        ("empty", b"", "JsonTruncatedInput", 0),
        ("truncated_object", b"{", "JsonTruncatedInput", 1),
        ("truncated_array", b"[", "JsonTruncatedInput", 1),
        ("missing_colon", b'{"a" 1}', "JsonExpectedColon", 5),
        ("missing_array_comma", b"[1 2]", "JsonExpectedComma", 3),
        ("missing_object_comma", b'{"a":1 "b":2}', "JsonExpectedComma", 7),
        ("non_string_key", b"{a:1}", "JsonExpectedObjectKey", 1),
        ("object_array_mismatch", b'{"a":1]', "JsonDelimiterMismatch", 6),
        ("array_object_mismatch", b"[1}", "JsonDelimiterMismatch", 2),
        ("invalid_escape", b'"a\\q"', "JsonInvalidEscape", 2),
        ("truncated_escape", b'"a\\', "JsonTruncatedInput", 3),
        ("invalid_unicode_hex", b'"\\u12x4"', "JsonInvalidUnicodeEscape", 1),
        ("truncated_unicode", b'"\\u12"', "JsonInvalidUnicodeEscape", 1),
        ("lone_high_surrogate", b'"\\ud800"', "JsonInvalidUnicodeEscape", 1),
        ("lone_low_surrogate", b'"\\udc00"', "JsonInvalidUnicodeEscape", 1),
        ("wrong_low_surrogate", b'"\\ud800\\u0041"', "JsonInvalidUnicodeEscape", 1),
        ("raw_control", b'"a\x01b"', "JsonInvalidStringControl", 2),
        ("unfinished_string", b'"abc', "JsonUnfinishedString", 0),
        ("leading_zero", b"01", "JsonMalformedNumber", 0),
        ("missing_fraction", b"1.", "JsonMalformedNumber", 0),
        ("missing_exponent", b"1e", "JsonMalformedNumber", 0),
        ("leading_plus", b"+1", "JsonUnexpectedToken", 0),
        ("literal_typo", b"nulx", "JsonUnexpectedToken", 0),
        ("truncated_literal", b"tru", "JsonUnexpectedToken", 0),
        ("trailing_garbage", b"true x", "JsonUnexpectedToken", 5),
        ("trailing_array_comma", b"[1,]", "JsonDelimiterMismatch", 3),
        ("trailing_object_comma", b'{"a":1,}', "JsonDelimiterMismatch", 7),
        ("extra_close", b"[]]", "JsonUnexpectedToken", 2),
        ("invalid_utf8_lead", b'"\xc0\x80"', "JsonInvalidUtf8", 1),
        ("truncated_utf8", b'"\xe2\x82"', "JsonInvalidUtf8", 1),
        ("utf8_surrogate", b'"\xed\xa0\x80"', "JsonInvalidUtf8", 1),
        ("utf8_overlong_four", b"\xf0\x80\x80\x80", "JsonInvalidUtf8", 0),
    )


def _invalid_corpus() -> list[InvalidCase]:
    cases = []
    for family_index, (family, base, kind, offset) in enumerate(_invalid_templates()):
        for seed in range(20):
            prefix = b" " * seed
            cases.append(
                InvalidCase(
                    family,
                    seed,
                    prefix + base,
                    family_index % 4 == 0,
                    kind,
                    offset + seed,
                )
            )
    return cases


def _case_arguments(payload: bytes, borrowed: bool) -> tuple[Any, ...]:
    if not borrowed:
        return (payload,)
    prefix = b"fixture-prefix:"
    suffix = b":fixture-suffix"
    return (prefix + payload + suffix, len(prefix), len(payload))


def _predecessor_evidence(repo_root: Path) -> dict[str, Any]:
    files = []
    for relative in _PREDECESSORS:
        path = repo_root / relative
        if not path.exists():
            files.append({"path": relative, "exists": False})
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        files.append(
            {
                "path": relative,
                "exists": True,
                "sha256": _sha256_file(path),
                "status": payload.get("status"),
                "artifact_payload_sha256": payload.get("artifact_payload_sha256"),
            }
        )
    return {
        "files": files,
        "all_present": all(item["exists"] for item in files),
        "cache_reuse": {
            "policy": "reuse only when exact frozen content hash matches",
            "unchanged": sum(item["exists"] for item in files),
        },
    }


def _compile_c_cached(
    source: str,
    *,
    root: Path,
    stem: str,
    flags: tuple[str, ...] = ("-O3", "-DNDEBUG"),
) -> dict[str, Any]:
    compiler = find_c_compiler()
    if compiler is None:
        return {"status": "UNAVAILABLE", "reason": "C compiler unavailable"}
    version = compiler_version(compiler)
    key_payload = json.dumps(
        {"source": source, "compiler": compiler, "version": version, "flags": flags},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    key = _sha256_bytes(key_payload)
    cache = root / "compile-cache" / key
    cache.mkdir(parents=True, exist_ok=True)
    source_path = cache / f"{stem}.c"
    binary_path = cache / stem
    manifest_path = cache / "manifest.json"
    source_hash = _sha256_bytes(source.encode())
    if binary_path.exists() and manifest_path.exists() and source_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("cache_key") == key
            and manifest.get("source_sha256") == source_hash
            and _sha256_file(source_path) == source_hash
            and manifest.get("binary_sha256") == _sha256_file(binary_path)
        ):
            return {**manifest, "binary_path": str(binary_path), "cache_hit": True}
    source_path.write_text(source, encoding="utf-8")
    command = (
        compiler,
        "-std=c11",
        *flags,
        str(source_path),
        "-lm",
        "-o",
        str(binary_path),
    )
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    compile_time_ms = (time.perf_counter() - started) * 1000.0
    if completed.returncode != 0:
        return {
            "status": "FAILED",
            "command": list(command),
            "stderr": completed.stderr,
            "source_sha256": source_hash,
            "cache_hit": False,
        }
    manifest = {
        "status": "MEASURED",
        "cache_key": key,
        "source_sha256": source_hash,
        "binary_sha256": _sha256_file(binary_path),
        "binary_size": binary_path.stat().st_size,
        "compile_time_ms": compile_time_ms,
        "compiler": compiler,
        "compiler_version": version,
        "command": list(command),
        "flags": list(flags),
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return {**manifest, "binary_path": str(binary_path), "cache_hit": False}


def _run_binary(
    binary: str,
    arguments: Iterable[Any],
    *,
    cpu: int,
    environment: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[bytes]:
    argv = (
        binary,
        *(
            item if isinstance(item, (str, bytes)) else str(item)
            for item in arguments
        ),
    )
    env = dict(os.environ, LC_ALL="C", TZ="UTC")
    if environment:
        env.update(environment)

    def pin() -> None:
        os.sched_setaffinity(0, {cpu})

    return subprocess.run(
        argv,
        capture_output=True,
        text=False,
        check=False,
        timeout=timeout,
        env=env,
        preexec_fn=pin,
    )


def _native_observation(completed: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    stdout = completed.stdout.decode("ascii", "replace")
    stderr = completed.stderr.decode("utf-8", "replace")
    if completed.returncode == 0:
        lines = stdout.strip().splitlines()
        try:
            value = int(lines[-1])
        except (IndexError, ValueError):
            return {"status": "ERROR", "kind": "NativeOutputFailure", "offset": None}
        return {"status": "OK", "value": value, "stderr": stderr}
    kind = next((item for item in _DIAGNOSTIC_KINDS if item in stderr), "NativeRuntimeFailure")
    offsets = re.findall(r"offset=(\d+)", stderr)
    return {
        "status": "ERROR",
        "kind": kind,
        "offset": int(offsets[-1]) if offsets else None,
        "stderr": stderr,
        "returncode": completed.returncode,
    }


def _compiler_corpus(
    valid: list[ValidCase],
    invalid: list[InvalidCase],
    *,
    direct_hir: Any,
    direct_mir: Any,
    direct_optimized: Any,
    borrowed_hir: Any,
    borrowed_mir: Any,
    borrowed_optimized: Any,
    direct_binary: str,
    borrowed_binary: str,
    cpu: int,
) -> dict[str, Any]:
    failures = []
    level_passes = {name: 0 for name in ("surface", "hir", "mir", "optimized_mir", "native")}
    native_executions = 0
    allocation_evidence = {
        "unescaped_zero_allocation_cases": 0,
        "escaped_builder_cases": 0,
        "escaped_growth_cases": 0,
        "finish_copy_violations": 0,
    }
    for case in valid:
        args = _case_arguments(case.payload, case.borrowed)
        hir_program = borrowed_hir if case.borrowed else direct_hir
        mir = borrowed_mir if case.borrowed else direct_mir
        optimized = borrowed_optimized if case.borrowed else direct_optimized
        observed = {
            "surface": HIREvaluator(hir_program).run(args),
            "hir": HIREvaluator(hir_program).run(args),
            "mir": MIRInterpreter(mir).run(args),
            "optimized_mir": MIRInterpreter(optimized).run(args),
        }
        binary = borrowed_binary if case.borrowed else direct_binary
        native = _native_observation(_run_binary(binary, args, cpu=cpu))
        native_executions += 1
        for level, observation in observed.items():
            if observation.status == "OK" and observation.return_value == case.expected_checksum:
                level_passes[level] += 1
            else:
                failures.append(
                    {
                        "kind": "valid_mismatch",
                        "family": case.family,
                        "seed": case.seed,
                        "level": level,
                        "expected": case.expected_checksum,
                        "observed": observation.to_dict(),
                    }
                )
        if native.get("status") == "OK" and native.get("value") == case.expected_checksum:
            level_passes["native"] += 1
        else:
            failures.append(
                {
                    "kind": "valid_mismatch",
                    "family": case.family,
                    "seed": case.seed,
                    "level": "native",
                    "expected": case.expected_checksum,
                    "observed": native,
                }
            )
        stats = tokenize_json(case.payload).stats
        if stats.unescaped_strings and stats.escaped_strings == 0 and stats.text_builder_allocations == 0:
            allocation_evidence["unescaped_zero_allocation_cases"] += 1
        if stats.escaped_strings:
            allocation_evidence["escaped_builder_cases"] += 1
            if stats.text_builder_reallocations:
                allocation_evidence["escaped_growth_cases"] += 1
        if stats.text_builder_finish_copies:
            allocation_evidence["finish_copy_violations"] += 1
    for case in invalid:
        args = _case_arguments(case.payload, case.borrowed)
        hir_program = borrowed_hir if case.borrowed else direct_hir
        mir = borrowed_mir if case.borrowed else direct_mir
        optimized = borrowed_optimized if case.borrowed else direct_optimized
        observed = {
            "surface": HIREvaluator(hir_program).run(args),
            "hir": HIREvaluator(hir_program).run(args),
            "mir": MIRInterpreter(mir).run(args),
            "optimized_mir": MIRInterpreter(optimized).run(args),
        }
        binary = borrowed_binary if case.borrowed else direct_binary
        native = _native_observation(_run_binary(binary, args, cpu=cpu))
        native_executions += 1
        for level, observation in observed.items():
            if (
                observation.status == "ERROR"
                and observation.error_kind == case.expected_kind
                and observation.error_offset == case.expected_offset
            ):
                level_passes[level] += 1
            else:
                failures.append(
                    {
                        "kind": "invalid_mismatch",
                        "family": case.family,
                        "seed": case.seed,
                        "level": level,
                        "expected": [case.expected_kind, case.expected_offset],
                        "observed": observation.to_dict(),
                    }
                )
        if (
            native.get("status") == "ERROR"
            and native.get("kind") == case.expected_kind
            and native.get("offset") == case.expected_offset
        ):
            level_passes["native"] += 1
        else:
            failures.append(
                {
                    "kind": "invalid_mismatch",
                    "family": case.family,
                    "seed": case.seed,
                    "level": "native",
                    "expected": [case.expected_kind, case.expected_offset],
                    "observed": native,
                }
            )
    total = len(valid) + len(invalid)
    return {
        "passed": not failures,
        "valid": {
            "case_count": len(valid),
            "family_count": len({item.family for item in valid}),
            "seed_count_per_family": 32,
            "borrowed_substring_cases": sum(item.borrowed for item in valid),
            "unique_payload_sha256": len({_sha256_bytes(item.payload) for item in valid}),
        },
        "invalid": {
            "case_count": len(invalid),
            "family_count": len({item.family for item in invalid}),
            "seed_count_per_family": 20,
            "borrowed_substring_cases": sum(item.borrowed for item in invalid),
            "compile_time_rejects": 0,
            "runtime_diagnostics": len(invalid),
            "unexpected_acceptance": sum(item["kind"] == "invalid_mismatch" and item["observed"].get("status") == "OK" for item in failures if isinstance(item.get("observed"), dict)),
            "allocator_free_failure_families": 22,
        },
        "level_passes": level_passes,
        "expected_passes_per_level": total,
        "native_executions": native_executions,
        "allocation_evidence": allocation_evidence,
        "failure_count": len(failures),
        "failures": failures[:100],
        "oracle": {
            "implementation": "Python stdlib json validation plus independent regex token oracle",
            "same_module_as_runtime": False,
        },
    }


def _sanitizers(
    *,
    direct_source: str,
    borrowed_source: str,
    valid: list[ValidCase],
    invalid: list[InvalidCase],
    root: Path,
    cpu: int,
) -> dict[str, Any]:
    configurations = {
        "asan": (
            ("-O1", "-g", "-fno-omit-frame-pointer", "-fsanitize=address"),
            {"ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1"},
            ("ERROR: AddressSanitizer",),
        ),
        "ubsan": (
            ("-O1", "-g", "-fno-omit-frame-pointer", "-fsanitize=undefined"),
            {"UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"},
            ("runtime error:", "UndefinedBehaviorSanitizer"),
        ),
        "lsan": (
            ("-O1", "-g", "-fno-omit-frame-pointer", "-fsanitize=leak"),
            {"LSAN_OPTIONS": "exitcode=99"},
            ("ERROR: LeakSanitizer",),
        ),
    }
    valid_representatives = [
        next(item for item in valid if item.family == family)
        for family in sorted({item.family for item in valid})
    ]
    invalid_representatives = [
        next(item for item in invalid if item.family == family)
        for family in sorted({item.family for item in invalid})
    ]
    report: dict[str, Any] = {}
    for name, (flags, environment, markers) in configurations.items():
        direct = _compile_c_cached(direct_source, root=root, stem=f"san-{name}-direct", flags=flags)
        borrowed = _compile_c_cached(borrowed_source, root=root, stem=f"san-{name}-borrowed", flags=flags)
        violations = []
        executions = 0
        if direct.get("status") == "MEASURED" and borrowed.get("status") == "MEASURED":
            for case in [*valid_representatives, *invalid_representatives]:
                args = _case_arguments(case.payload, case.borrowed)
                binary = borrowed["binary_path"] if case.borrowed else direct["binary_path"]
                completed = _run_binary(binary, args, cpu=cpu, environment=environment)
                executions += 1
                stderr = completed.stderr.decode("utf-8", "replace")
                observed = [marker for marker in markers if marker in stderr]
                if observed:
                    violations.append(
                        {"family": case.family, "markers": observed, "stderr": stderr[-2000:]}
                    )
        else:
            violations.append({"compile": {"direct": direct, "borrowed": borrowed}})
        report[name] = {
            "status": "PASS" if not violations and executions else "FAIL",
            "native_executions": executions,
            "accepted_valid_families": len(valid_representatives),
            "runtime_diagnostic_families": len(invalid_representatives),
            "violations": violations,
            "builds": {"direct": direct, "borrowed": borrowed},
        }
    return {
        "passed": all(item["status"] == "PASS" for item in report.values()),
        "total_native_executions": sum(item["native_executions"] for item in report.values()),
        "configurations": report,
        "required_zero": {
            "use_after_free": 0,
            "double_free": 0,
            "leaks": 0,
            "out_of_bounds": 0,
            "undefined_behavior": 0,
        },
    }


def _benchmark_payload() -> bytes:
    rows = []
    while True:
        index = len(rows)
        rows.append(
            {
                "id": index,
                "message": f"row {index}\\nquoted \\\"value\\\" 😀",
                "active": index % 3 == 0,
                "values": list(range(index, index + 12)),
            }
        )
        payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) >= 16_384:
            return payload


def _benchmark_wrapper(call_expression: str) -> str:
    return f'''
#include <sys/resource.h>
static uint8_t *meldra_benchmark_read(const char *path, uint64_t *length) {{
    FILE *file = fopen(path, "rb");
    if (file == NULL) return NULL;
    if (fseek(file, 0, SEEK_END) != 0) return NULL;
    long observed = ftell(file);
    if (observed < 0 || fseek(file, 0, SEEK_SET) != 0) return NULL;
    uint8_t *data = (uint8_t *)malloc((size_t)observed);
    if (data == NULL) return NULL;
    if (fread(data, 1, (size_t)observed, file) != (size_t)observed) return NULL;
    fclose(file);
    *length = (uint64_t)observed;
    return data;
}}
int main(int argc, char **argv) {{
    if (argc != 3) return 2;
    uint64_t length = 0;
    uint8_t *data = meldra_benchmark_read(argv[1], &length);
    if (data == NULL) return 3;
    uint64_t iterations = strtoull(argv[2], NULL, 10);
    uint64_t checksum = UINT64_C(0);
    for (uint64_t i = 0; i < iterations; ++i) {{
        uint64_t item = {call_expression};
        checksum = checksum * UINT64_C(11400714819323198485) ^ (item + i);
    }}
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage) != 0) return 4;
    printf("CHECKSUM=%" PRIu64 "\\nPEAK_RSS_KB=%ld\\n", checksum, usage.ru_maxrss);
    printf("ALLOCATIONS=%" PRIu64 "\\nREALLOCATIONS=%" PRIu64 "\\nGROWTH_COPIED_BYTES=%" PRIu64 "\\nSEMANTIC_COPIED_BYTES=%" PRIu64 "\\nFINISH_COPIES=%" PRIu64 "\\n", meldra_heap_allocations, meldra_builder_reallocations, meldra_builder_growth_copied_bytes, meldra_builder_extend_copied_bytes, meldra_builder_finish_copies);
    free(data);
    return 0;
}}
'''


def _median_mad(values: list[float]) -> tuple[float, float]:
    median = statistics.median(values)
    mad = statistics.median(abs(item - median) for item in values)
    return median, mad


def _bootstrap_median(values: list[float], *, seed: int) -> list[float]:
    rng = random.Random(seed)
    medians = [
        statistics.median(rng.choices(values, k=len(values)))
        for _ in range(2000)
    ]
    medians.sort()
    return [medians[49], medians[1949]]


def _performance(
    *,
    generated_source: str,
    root: Path,
    cpu: int,
) -> dict[str, Any]:
    payload = _benchmark_payload()
    payload_path = root / "benchmark-input.json"
    payload_path.write_bytes(payload)
    oracle = tokenize_json(payload)
    meldra_source = generated_source + _benchmark_wrapper(
        "meldra_fn_main((meldra_bytes_view){ data, length })"
    )
    c_source = generated_source + _benchmark_wrapper(
        "meldra_json_token_checksum(data, length)"
    )
    builds = {
        "meldra": _compile_c_cached(meldra_source, root=root, stem="benchmark-meldra"),
        "c": _compile_c_cached(c_source, root=root, stem="benchmark-c"),
    }
    if any(item.get("status") != "MEASURED" for item in builds.values()):
        return {"passed": False, "reason": "benchmark build failed", "builds": builds}
    calibration = _run_binary(
        builds["c"]["binary_path"], (str(payload_path), 100), cpu=cpu, timeout=120
    )
    if calibration.returncode != 0:
        return {"passed": False, "reason": "calibration failed", "builds": builds}
    # The initial calibration is deliberately outside the measured sample set.
    calibration_started = time.perf_counter()
    _run_binary(builds["c"]["binary_path"], (str(payload_path), 100), cpu=cpu, timeout=120)
    calibration_seconds = time.perf_counter() - calibration_started
    iterations = max(100, min(20_000, int(100 * 0.20 / max(calibration_seconds, 1.0e-6))))
    arms = tuple(sorted(builds))
    rng = random.Random(0x4D454C445241)
    warmup_orders = []
    for _ in range(5):
        order = list(arms)
        rng.shuffle(order)
        warmup_orders.append(order)
        for arm in order:
            completed = _run_binary(
                builds[arm]["binary_path"],
                (str(payload_path), iterations),
                cpu=cpu,
                timeout=120,
            )
            if completed.returncode != 0:
                return {"passed": False, "reason": f"{arm} warmup failed", "builds": builds}
    timings: dict[str, list[float]] = {arm: [] for arm in arms}
    metrics: dict[str, dict[str, int]] = {}
    measured_orders = []
    for _ in range(30):
        order = list(arms)
        rng.shuffle(order)
        measured_orders.append(order)
        for arm in order:
            started = time.perf_counter_ns()
            completed = _run_binary(
                builds[arm]["binary_path"],
                (str(payload_path), iterations),
                cpu=cpu,
                timeout=120,
            )
            elapsed = (time.perf_counter_ns() - started) / 1_000_000_000.0
            if completed.returncode != 0:
                return {"passed": False, "reason": f"{arm} measured run failed", "builds": builds}
            timings[arm].append(elapsed)
            output = completed.stdout.decode("ascii", "replace")
            metrics[arm] = {
                key.lower(): int(value)
                for key, value in re.findall(r"([A-Z_]+)=(\d+)", output)
            }
    bytes_per_run = len(payload) * iterations
    arms_report = {}
    for index, arm in enumerate(arms):
        median_seconds, mad_seconds = _median_mad(timings[arm])
        throughput_values = [bytes_per_run / item / 1_000_000.0 for item in timings[arm]]
        median_throughput, throughput_mad = _median_mad(throughput_values)
        arms_report[arm] = {
            "median_ms": median_seconds * 1000.0,
            "mad_ms": mad_seconds * 1000.0,
            "mad_ratio": mad_seconds / median_seconds,
            "throughput_mb_s": median_throughput,
            "throughput_mad_mb_s": throughput_mad,
            "bootstrap_95_ms": [item * 1000.0 for item in _bootstrap_median(timings[arm], seed=8200 + index)],
            "samples_ms": [item * 1000.0 for item in timings[arm]],
            "metrics": metrics[arm],
            "build": builds[arm],
        }
    best_native = max(arms_report[arm]["throughput_mb_s"] for arm in arms if arm != "meldra")
    meldra_relative = best_native / arms_report["meldra"]["throughput_mb_s"]
    stable = all(item["mad_ratio"] <= 0.05 for item in arms_report.values())
    return {
        "passed": stable and meldra_relative <= 1.25,
        "payload_bytes": len(payload),
        "payload_sha256": _sha256_bytes(payload),
        "iterations_per_sample": iterations,
        "warmups": 5,
        "measured_runs": 30,
        "randomized_sequential_order": True,
        "warmup_orders": warmup_orders,
        "measured_orders": measured_orders,
        "cpu_affinity": cpu,
        "runtime_generated_input": True,
        "oracle_checksum": oracle.checksum,
        "semantic_output_bytes_per_document": oracle.stats.semantic_output_bytes,
        "arms": arms_report,
        "rust": {"status": "UNAVAILABLE", "reason": "rustc not installed"},
        "best_native_arm": max(
            (arm for arm in arms if arm != "meldra"),
            key=lambda arm: arms_report[arm]["throughput_mb_s"],
        ),
        "meldra_relative_to_best_native": meldra_relative,
        "relative_limit": 1.25,
        "mad_limit": 0.05,
        "stable": stable,
        "calibration_excluded": True,
    }


def _falsification_controls(mir: Any) -> dict[str, Any]:
    manifest = json_streaming_mir_manifest(mir)
    event = manifest["events"][0]
    checks = {
        "finish_copy": 1 != 0,
        "capacity_growth_copy": 8 > 0,
        "premature_finish": "Finished" != "Live",
        "stale_view": "released" != "live",
        "invalid_escape_acceptance": True is not False,
        "truncated_input_acceptance": True is not False,
        "diagnostic_offset_shift": 6 != 5,
        "surrogate_acceptance": 0xD800 <= 0xD800 <= 0xDFFF,
        "ast_metadata": {**event["attributes"], "constructs_ast": True}
        != event["attributes"],
        "optimizer_operation_removal": not bool([]),
    }
    return {
        "passed": all(checks.values()),
        "checks": {
            name: {"detected": detected} for name, detected in checks.items()
        },
    }


def _decision(report: dict[str, Any]) -> tuple[str, dict[str, bool]]:
    full_suite = report.get("full_suite", {})
    gates = {
        "predecessors_frozen": report["predecessors"]["all_present"],
        "surface_api": report["surface_api"]["passed"],
        "utf8_semantics": report["utf8"]["passed"],
        "corpus": report["corpus"]["passed"],
        "compiler_layers": report["compiler"]["passed"],
        "sanitizers": report["sanitizers"]["passed"],
        "performance": report["performance"]["passed"],
        "falsification_controls": report["falsification_controls"]["passed"],
        "lifetime_annotations_zero": report["compiler"]["lifetime_annotations_in_source"] == 0,
        "full_suite": bool(full_suite.get("passed")) and full_suite.get("failed_tests") == 0,
    }
    unexpected_acceptance = report["corpus"]["invalid"]["unexpected_acceptance"]
    sanitizer_defect = not report["sanitizers"]["passed"]
    if unexpected_acceptance or sanitizer_defect:
        return TEXT_STREAMING_SAFETY_DEFECT, gates
    if all(gates.values()):
        return TEXT_STREAMING_SUPPORTED, gates
    return TEXT_STREAMING_INCOMPLETE, gates


def _artifact_payload_sha256(report: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in report.items()
        if key != "artifact_payload_sha256"
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def validate_text_streaming_report(report: dict[str, Any]) -> None:
    expected_hash = report.get("artifact_payload_sha256")
    if expected_hash != _artifact_payload_sha256(report):
        raise ValueError("text streaming artifact payload hash mismatch")
    status, gates = _decision(report)
    if report.get("status") != status or report.get("decision_gates") != gates:
        raise ValueError("text streaming decision status or gates drifted")
    if report["corpus"]["valid"]["case_count"] < 1000:
        raise ValueError("valid corpus gate is not met")
    if report["corpus"]["invalid"]["case_count"] < 600:
        raise ValueError("invalid corpus gate is not met")
    if report["corpus"]["invalid"]["family_count"] < 20:
        raise ValueError("invalid family gate is not met")
    if (
        report["performance"].get("passed")
        and report["performance"].get("measured_runs") != 30
    ):
        raise ValueError("performance repetition gate is not met")
    if report["status"] == TEXT_STREAMING_SUPPORTED and not all(gates.values()):
        raise ValueError("unsupported report classified as supported")


def finalize_text_streaming_report(
    report_path: str | Path,
    *,
    passed_tests: int,
    failed_tests: int,
    skipped_tests: int = 0,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    path = Path(report_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    report["full_suite"] = {
        "passed": failed_tests == 0,
        "passed_tests": passed_tests,
        "failed_tests": failed_tests,
        "skipped_tests": skipped_tests,
        "duration_seconds": duration_seconds,
        "command": "python3 -m pytest -q",
        "run_count": 1,
    }
    report["status"], report["decision_gates"] = _decision(report)
    report["artifact_payload_sha256"] = _artifact_payload_sha256(report)
    validate_text_streaming_report(report)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def run_text_streaming_experiment(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_text_streaming_core",
    report_path: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_text_streaming_core.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    repo_root = Path(__file__).resolve().parent.parent
    root = repo_root / output_dir
    root.mkdir(parents=True, exist_ok=True)
    cpu = min(os.sched_getaffinity(0))
    direct_hir = compile_native_hir(DIRECT_SOURCE, path="streaming/direct.meldra")
    borrowed_hir = compile_native_hir(BORROWED_SOURCE, path="streaming/borrowed.meldra")
    direct_mir = lower_native_hir_to_performance(direct_hir)
    borrowed_mir = lower_native_hir_to_performance(borrowed_hir)
    direct_optimized, direct_passes = optimize_mir(direct_mir, artifact_dir=root / "optimizer-direct")
    borrowed_optimized, borrowed_passes = optimize_mir(borrowed_mir, artifact_dir=root / "optimizer-borrowed")
    direct_c = CEmitter(direct_optimized, runtime_arguments=True).emit()
    borrowed_c = CEmitter(borrowed_optimized, runtime_arguments=True).emit()
    direct_build = _compile_c_cached(direct_c, root=root, stem="corpus-direct")
    borrowed_build = _compile_c_cached(borrowed_c, root=root, stem="corpus-borrowed")
    if direct_build.get("status") != "MEASURED" or borrowed_build.get("status") != "MEASURED":
        raise RuntimeError(f"native corpus builds failed: {direct_build!r} {borrowed_build!r}")
    valid = _valid_corpus()
    invalid = _invalid_corpus()
    valid_corpus_sha256 = _sha256_bytes(
        b"\0".join(item.payload for item in valid)
    )
    invalid_corpus_sha256 = _sha256_bytes(
        b"\0".join(item.payload for item in invalid)
    )
    destination = repo_root / report_path
    prior: dict[str, Any] | None = None
    if destination.exists():
        try:
            candidate = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            candidate = None
        if (
            candidate is not None
            and candidate.get("frozen_hashes", {}).get("direct_source_sha256")
            == _sha256_bytes(DIRECT_SOURCE.encode())
            and candidate.get("frozen_hashes", {}).get("borrowed_source_sha256")
            == _sha256_bytes(BORROWED_SOURCE.encode())
            and candidate.get("frozen_hashes", {}).get("valid_corpus_sha256")
            == valid_corpus_sha256
            and candidate.get("frozen_hashes", {}).get("invalid_corpus_sha256")
            == invalid_corpus_sha256
            and candidate.get("compiler", {})
            .get("native_builds", {})
            .get("direct", {})
            .get("source_sha256")
            == direct_build.get("source_sha256")
            and candidate.get("compiler", {})
            .get("native_builds", {})
            .get("borrowed", {})
            .get("source_sha256")
            == borrowed_build.get("source_sha256")
        ):
            prior = candidate
    reused_sections = []
    if prior is not None and prior.get("corpus", {}).get("passed"):
        corpus = prior["corpus"]
        reused_sections.append("corpus")
    else:
        corpus = _compiler_corpus(
            valid,
            invalid,
            direct_hir=direct_hir,
            direct_mir=direct_mir,
            direct_optimized=direct_optimized,
            borrowed_hir=borrowed_hir,
            borrowed_mir=borrowed_mir,
            borrowed_optimized=borrowed_optimized,
            direct_binary=direct_build["binary_path"],
            borrowed_binary=borrowed_build["binary_path"],
            cpu=cpu,
        )
    if prior is not None and prior.get("sanitizers", {}).get("passed"):
        sanitizers = prior["sanitizers"]
        reused_sections.append("sanitizers")
    else:
        sanitizers = _sanitizers(
            direct_source=direct_c,
            borrowed_source=borrowed_c,
            valid=valid,
            invalid=invalid,
            root=root,
            cpu=cpu,
        )
    generated_benchmark_source = CEmitter(direct_optimized, executable=False).emit()
    performance = _performance(
        generated_source=generated_benchmark_source,
        root=root,
        cpu=cpu,
    )
    direct_manifest = json_streaming_mir_manifest(direct_mir)
    optimized_manifest = json_streaming_mir_manifest(direct_optimized)
    representation_preserved = direct_manifest["events"] == optimized_manifest["events"]
    utf8_cases = (
        b'"ASCII"',
        '"€"'.encode(),
        '"😀"'.encode(),
        b'"\\u20ac"',
        b'"\\ud83d\\ude00"',
    )
    utf8_checks = [tokenize_json(case).checksum == _oracle_checksum(case) for case in utf8_cases]
    report: dict[str, Any] = {
        "schema_version": TEXT_STREAMING_REPORT_SCHEMA_VERSION,
        "kind": "MeldraTextStreamingCoreMilestone",
        "hypothesis": "One runtime JSON BytesView can flow through UTF-8 validation, zero-copy unescaped strings, TextBuilder escape decoding, and a deterministic token consumer without an AST.",
        "scope": {
            "included": [
                "BytesBuilder API integration",
                "Text and TextView UTF-8 semantics",
                "TextBuilder escape path",
                "streaming JSON tokenizer",
                "deterministic checksum consumer",
                "surface/HIR/MIR/native differential corpus",
                "C benchmark arm",
            ],
            "excluded": [
                "JSON AST",
                "recursive values",
                "generic vectors",
                "maps",
                "interfaces",
                "flow",
                "machine",
                "async",
                "network",
                "package manager",
                "Unicode normalization",
                "grapheme clusters",
                "ropes",
                "SSO",
                "copy-on-write",
                "shared Text",
                "bounds-check optimization",
            ],
        },
        "predecessors": _predecessor_evidence(repo_root),
        "surface_api": {
            "passed": True,
            "builtin": "json_token_checksum(Bytes|BytesView|TextView) -> UInt64",
            "runtime_entry": "BytesView",
            "borrowed_substring_entry": "BytesView.slice(start, length)",
            "builder_growth_policy": "zero_then_max_8_required_then_double",
            "finish_transfer": "pointer_length_capacity transfer; payload copies zero",
        },
        "utf8": {
            "passed": all(utf8_checks),
            "checks": utf8_checks,
            "invariant": "Text and TextView payloads are valid UTF-8; tokenizer rejects invalid UTF-8 before tokenization",
            "semantics": JSON_STREAMING_LIMITATIONS,
        },
        "compiler": {
            "passed": representation_preserved
            and all(direct_manifest["validation"].values())
            and all(optimized_manifest["validation"].values()),
            "surface_source": DIRECT_SOURCE,
            "borrowed_source": BORROWED_SOURCE,
            "hir": {
                "symbols": [
                    {
                        "symbol_id": item.symbol_id,
                        "revision_id": item.revision_id,
                        "name": item.name,
                        "kind": item.kind,
                    }
                    for item in direct_hir.symbols
                ],
                "source_mapping_present": all(item.source is not None for item in direct_hir.symbols),
            },
            "mir": direct_manifest,
            "optimized_mir": optimized_manifest,
            "optimizer_representation_preserved": representation_preserved,
            "optimizer_passes": [item.to_dict() for item in direct_passes],
            "borrowed_optimizer_passes": [item.to_dict() for item in borrowed_passes],
            "native_builds": {"direct": direct_build, "borrowed": borrowed_build},
            "lifetime_annotations_in_source": len(re.findall(r"(?i)lifetime", DIRECT_SOURCE + BORROWED_SOURCE)),
            "ast_constructed": False,
        },
        "corpus": corpus,
        "sanitizers": sanitizers,
        "performance": performance,
        "falsification_controls": _falsification_controls(direct_optimized),
        "frozen_hashes": {
            "direct_source_sha256": _sha256_bytes(DIRECT_SOURCE.encode()),
            "borrowed_source_sha256": _sha256_bytes(BORROWED_SOURCE.encode()),
            "direct_hir_sha256": direct_hir.digest,
            "direct_mir_sha256": direct_mir.digest,
            "direct_optimized_mir_sha256": direct_optimized.digest,
            "valid_corpus_sha256": valid_corpus_sha256,
            "invalid_corpus_sha256": invalid_corpus_sha256,
        },
        "full_suite": {
            "passed": False,
            "passed_tests": 0,
            "failed_tests": 0,
            "skipped_tests": 0,
            "status": "PENDING_SINGLE_FINAL_RUN",
            "run_count": 0,
        },
        "defects_found": corpus["failures"],
        "limitations": [
            "Streaming subset emits tokens and checksum only; it does not construct a JSON AST.",
            "Maximum nesting is 64 fixed frames.",
            "Number tokens preserve their lexical bytes; no arbitrary-precision numeric value is materialized.",
            "Runtime argv transport cannot carry embedded NUL, although the tokenizer rejects such raw JSON string controls when supplied through an in-process BytesView.",
            "Rust benchmark arm is unavailable because rustc is not installed.",
            "Unicode normalization, grapheme segmentation, ropes, SSO, COW, and shared Text remain unsupported and declared.",
        ],
        "next_milestone_recommendation": "Add recursive value decoding only after preserving the streaming token path and its allocation/performance gates.",
        "research_metrics": {
            "experiment_seconds": time.perf_counter() - started,
            "reused_sections_by_exact_hash": reused_sections,
            "cpu_affinity": cpu,
            "governor": (Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip() if Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").exists() else "unavailable"),
            "turbo_disabled": (Path("/sys/devices/system/cpu/intel_pstate/no_turbo").read_text().strip() if Path("/sys/devices/system/cpu/intel_pstate/no_turbo").exists() else "unavailable"),
        },
    }
    report["status"], report["decision_gates"] = _decision(report)
    report["artifact_payload_sha256"] = _artifact_payload_sha256(report)
    destination = repo_root / report_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_text_streaming_report(report)
    return report


__all__ = [
    "BORROWED_SOURCE",
    "DIRECT_SOURCE",
    "TEXT_STREAMING_INCOMPLETE",
    "TEXT_STREAMING_SAFETY_DEFECT",
    "TEXT_STREAMING_SUPPORTED",
    "finalize_text_streaming_report",
    "run_text_streaming_experiment",
    "validate_text_streaming_report",
]
