"""Targeted executable safety evidence for Productive Core memory contracts."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir, optimize_general_mir
from merlo.structured_hir_v2 import compile_structured_hir


SAFETY_SCHEMA_VERSION = 1
SAFETY_KIND = "merlo.productive-safety"
SAFETY_STATUSES = ("PASSED", "FAILED", "UNSUPPORTED")
SAFETY_SANITIZERS = ("asan", "ubsan", "lsan", "none")
SAFETY_RECORD_FIELDS = (
    "scenario",
    "sanitizer",
    "command",
    "exit_code",
    "stdout_sha256",
    "stderr_sha256",
    "status",
    "expectation",
    "reason",
)

MAP_SCENARIOS = ("map_collisions_growth_updates", "map_uint64_overflow")
RESOURCE_SCENARIOS = (
    "resource_repeated_processing",
    "resource_early_failure",
    "resource_late_failure",
    "resource_cleanup",
    "resource_line_reuse",
    "resource_unicode",
    "resource_large_input",
    "resource_fd_stability",
)
SAFETY_INVARIANTS = (
    "map_collisions",
    "map_growth",
    "map_updates",
    "map_cleanup",
    "map_checked_overflow",
    "resource_repeated_processing",
    "resource_early_failure",
    "resource_late_failure",
    "resource_cleanup",
    "resource_line_reuse",
    "resource_unicode",
    "resource_large_input",
    "resource_fd_stability",
)

_SANITIZER_FLAGS = {
    "asan": ("-fsanitize=address", "ASAN_OPTIONS", "halt_on_error=1:detect_leaks=0"),
    "ubsan": ("-fsanitize=undefined", "UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=0"),
    "lsan": ("-fsanitize=leak", "LSAN_OPTIONS", "exitcode=23:report_objects=0"),
}
_SANITIZER_MARKERS = ("AddressSanitizer", "UndefinedBehaviorSanitizer", "LeakSanitizer", "runtime error:")
_MAP_SANITIZERS = ("asan", "ubsan", "lsan")

_MAP_SOURCE = """
fn main(input: BytesView) -> UInt64:
    let counts: Map[Text, UInt64] = Map.new()
    let k0: Text = Text.from_bytes(input, 0, 1)
    let k1: Text = Text.from_bytes(input, 1, 2)
    let k2: Text = Text.from_bytes(input, 2, 3)
    let k3: Text = Text.from_bytes(input, 3, 4)
    let k4: Text = Text.from_bytes(input, 4, 5)
    let k5: Text = Text.from_bytes(input, 5, 6)
    let k6: Text = Text.from_bytes(input, 6, 7)
    let k7: Text = Text.from_bytes(input, 7, 8)
    counts.insert(k0, 10)
    counts.insert(k1, 1)
    counts.insert(k2, 30)
    counts.insert(k3, 40)
    counts.insert(k4, 50)
    counts.insert(k5, 60)
    counts.insert(k6, 70)
    counts.insert(k7, 80)
    counts.increment(k1, 4)
    counts.insert(k0, 11)
    var checksum: UInt64 = 0
    for entry in counts.entries():
        checksum = checksum * 131 + entry.value
    return checksum + counts.get(k6)
""".strip()

_OVERFLOW_SOURCE = """
fn main(input: BytesView) -> UInt64:
    let counts: Map[Text, UInt64] = Map.new()
    let key: Text = Text.from_bytes(input, 0, 1)
    counts.insert(key, 18446744073709551615)
    counts.increment(key, 1)
    return 0
""".strip()

_RESOURCE_PROBE = r'''
from __future__ import annotations

import os
import sys
from pathlib import Path

from tools.benchmarks.merlo.streaming_resources import (
    BorrowedLineExpiredError,
    FileReaderClosedError,
    FileUtf8Error,
    open_read,
)


def lines(path: Path, buffer_size: int = 8) -> list[str]:
    with open_read(path, buffer_size=buffer_size) as reader:
        return [line.text() for line in reader]


def run(name: str, path: Path) -> None:
    if name == "resource_repeated_processing":
        expected = ["one", "two", "three"]
        for _ in range(16):
            if lines(path) != expected:
                raise AssertionError("repeated processing mismatch")
    elif name == "resource_early_failure":
        reader = open_read(path, buffer_size=3)
        try:
            first = reader.read_line()
            if first is None or first.text() != "valid":
                raise AssertionError("missing valid prefix")
            try:
                invalid = reader.read_line()
                if invalid is None:
                    raise AssertionError("missing invalid line")
                invalid.text()
            except FileUtf8Error:
                pass
            else:
                raise AssertionError("invalid UTF-8 was accepted")
            if reader.close_count != 0:
                raise AssertionError("early probe closed before context")
        finally:
            reader.close()
        if reader.active_descriptor_count != 0 or reader.close_count != 1:
            raise AssertionError("early failure did not close")
    elif name == "resource_late_failure":
        reader = open_read(path, buffer_size=5)
        try:
            seen = 0
            while True:
                view = reader.read_line()
                if view is None:
                    break
                try:
                    view.text()
                except FileUtf8Error:
                    break
                seen += 1
            if seen < 32:
                raise AssertionError("late failure was not late")
        finally:
            reader.close()
        if reader.active_descriptor_count != 0 or reader.close_count != 1:
            raise AssertionError("late failure did not close")
    elif name == "resource_cleanup":
        reader = open_read(path)
        try:
            with reader:
                first = reader.read_line()
                if first is None:
                    raise AssertionError("cleanup input was empty")
                raise RuntimeError("intentional early exit")
        except RuntimeError:
            pass
        if reader.active_descriptor_count != 0 or reader.close_count != 1:
            raise AssertionError("exception cleanup failed")
    elif name == "resource_line_reuse":
        reader = open_read(path)
        try:
            first = reader.read_line()
            second = reader.read_line()
            if first is None or second is None or second.text() != "second":
                raise AssertionError("line setup failed")
            try:
                first.text()
            except BorrowedLineExpiredError:
                pass
            else:
                raise AssertionError("stale line remained usable")
        finally:
            reader.close()
        try:
            second.text()
        except BorrowedLineExpiredError:
            pass
        else:
            raise AssertionError("closed line remained usable")
    elif name == "resource_unicode":
        if lines(path, buffer_size=2) != ["κόσμος", "漢字😀"]:
            raise AssertionError("Unicode decoding mismatch")
    elif name == "resource_large_input":
        expected = 20000
        with open_read(path, buffer_size=257) as reader:
            count = sum(1 for _ in reader)
        if count != expected:
            raise AssertionError("large input count mismatch")
    elif name == "resource_fd_stability":
        fd_root = Path("/proc/self/fd")
        if not fd_root.is_dir():
            print(f"SAFETY_PROBE UNSUPPORTED {name}")
            return
        before = len(tuple(fd_root.iterdir()))
        for _ in range(64):
            with open_read(path) as reader:
                for line in reader:
                    line.text()
                if reader.active_descriptor_count != 1:
                    raise AssertionError("reader descriptor count mismatch")
        after = len(tuple(fd_root.iterdir()))
        if before != after:
            raise AssertionError(f"fd count changed: {before} -> {after}")
    else:
        raise ValueError(f"unknown scenario: {name}")
    print(f"SAFETY_PROBE PASSED {name}")


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]))
'''


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _compiler() -> str | None:
    return next((shutil.which(name) for name in ("clang", "cc", "gcc") if shutil.which(name)), None)


def _sanitizer_environment(sanitizer: str) -> dict[str, str]:
    environment = dict(os.environ)
    if sanitizer in _SANITIZER_FLAGS:
        _, variable, value = _SANITIZER_FLAGS[sanitizer]
        environment[variable] = value
    return environment


def _execute(
    command: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            list(command),
            input=input_bytes,
            capture_output=True,
            check=False,
            shell=False,
            env=dict(environment) if environment is not None else None,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _record(
    *,
    scenario: str,
    sanitizer: str,
    command: Sequence[str],
    completed: subprocess.CompletedProcess[bytes] | None,
    status: str,
    expectation: str,
    reason: str,
) -> dict[str, object]:
    stdout = completed.stdout if completed is not None else b""
    stderr = completed.stderr if completed is not None else b""
    return {
        "scenario": scenario,
        "sanitizer": sanitizer,
        "command": list(command),
        "exit_code": completed.returncode if completed is not None else None,
        "stdout_sha256": _digest(stdout),
        "stderr_sha256": _digest(stderr),
        "status": status,
        "expectation": expectation,
        "reason": reason,
    }


def _support_probe(
    compiler: str | None,
    sanitizer: str,
    directory: Path,
) -> tuple[list[str], subprocess.CompletedProcess[bytes] | None]:
    source = directory / f"support_{sanitizer}.c"
    binary = directory / f"support_{sanitizer}"
    source.write_text("#include <stdint.h>\nint main(void) { return (int)UINT64_C(0); }\n", encoding="utf-8")
    flag = _SANITIZER_FLAGS[sanitizer][0]
    command = [compiler or "cc", "-std=c11", "-O1", flag, str(source), "-o", str(binary)]
    if compiler is None:
        return command, None
    built = _execute(command)
    if built is None or built.returncode != 0:
        return command, built
    run_command = [str(binary)]
    return run_command, _execute(run_command, environment=_sanitizer_environment(sanitizer))


def _map_layers(source: str):
    hir = compile_structured_hir(source, path="productive-safety-map.mlo")
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    return hir, representation, optimize_general_mir(mir)


def _map_record(
    *,
    compiler: str | None,
    sanitizer: str,
    scenario: str,
    source: str,
    directory: Path,
    support: tuple[list[str], subprocess.CompletedProcess[bytes] | None] | None,
) -> dict[str, object]:
    expectation = "typed_failure" if scenario == "map_uint64_overflow" else "success"
    if compiler is None:
        command = ["cc", "-std=c11", "-fsanitize=" + sanitizer, "<generated-map.c>", "-o", "<generated-map>"]
        return _record(
            scenario=scenario,
            sanitizer=sanitizer,
            command=command,
            completed=None,
            status="UNSUPPORTED",
            expectation=expectation,
            reason="no C compiler observed",
        )
    if support is None:
        raise RuntimeError("sanitizer support probe missing")
    support_command, support_result = support
    if support_result is None or support_result.returncode != 0:
        return _record(
            scenario=scenario,
            sanitizer=sanitizer,
            command=support_command,
            completed=None,
            status="UNSUPPORTED",
            expectation=expectation,
            reason="sanitizer instrumentation is not supported by the observed toolchain",
        )

    generated = emit_general_c(*_map_layers(source))
    c_path = directory / f"{scenario}_{sanitizer}.c"
    binary = directory / f"{scenario}_{sanitizer}"
    c_path.write_text(generated.source, encoding="utf-8")
    flag = _SANITIZER_FLAGS[sanitizer][0]
    build_command = [compiler, "-std=c11", "-O1", "-fno-omit-frame-pointer", flag, str(c_path), "-o", str(binary)]
    built = _execute(build_command)
    if built is None or built.returncode != 0:
        return _record(
            scenario=scenario,
            sanitizer=sanitizer,
            command=build_command,
            completed=built,
            status="UNSUPPORTED" if built is None else "FAILED",
            expectation=expectation,
            reason="generated Map C scenario could not be compiled",
        )

    run_command = [str(binary)]
    completed = _execute(run_command, input_bytes=b"!)19AIQY" if expectation == "success" else b"x", environment=_sanitizer_environment(sanitizer))
    if completed is None:
        return _record(
            scenario=scenario,
            sanitizer=sanitizer,
            command=run_command,
            completed=None,
            status="UNSUPPORTED",
            expectation=expectation,
            reason="generated Map executable could not be exercised",
        )
    stderr = completed.stderr.decode("utf-8", errors="replace")
    sanitizer_failure = any(marker in stderr for marker in _SANITIZER_MARKERS)
    if expectation == "success":
        passed = completed.returncode == 0 and b"OK result=" in completed.stdout and not sanitizer_failure
        marker = "OK result="
    else:
        passed = completed.returncode != 0 and b"MerloOverflow:MapUInt64" in completed.stderr and not sanitizer_failure
        marker = "MerloOverflow:MapUInt64"
    status = "PASSED" if passed else "FAILED"
    reason = (
        f"marker {marker} observed; expected Map behavior and clean sanitizer run observed"
        if passed
        else "Map behavior or sanitizer cleanliness check failed"
    )
    return _record(
        scenario=scenario,
        sanitizer=sanitizer,
        command=run_command,
        completed=completed,
        status=status,
        expectation=expectation,
        reason=reason,
    )


def _resource_payload(scenario: str) -> bytes:
    if scenario == "resource_repeated_processing":
        return b"one\ntwo\nthree\n"
    if scenario == "resource_early_failure":
        return b"valid\ninvalid-\xff\n"
    if scenario == "resource_late_failure":
        return (b"valid\n" * 32) + b"invalid-\xff\n"
    if scenario == "resource_cleanup":
        return b"first\nsecond\n"
    if scenario == "resource_line_reuse":
        return b"first\nsecond\n"
    if scenario == "resource_unicode":
        return "κόσμος\n漢字😀\n".encode("utf-8")
    if scenario == "resource_large_input":
        return b"value\n" * 20000
    if scenario == "resource_fd_stability":
        return b"fd-check\n" * 8
    raise ValueError(f"unknown resource scenario: {scenario}")


def _resource_record(scenario: str, directory: Path) -> dict[str, object]:
    payload = directory / f"{scenario}.input"
    payload.write_bytes(_resource_payload(scenario))
    probe = directory / "streaming_resource_probe.py"
    probe.write_text(_RESOURCE_PROBE, encoding="utf-8")
    command = [sys.executable, str(probe), scenario, str(payload)]
    environment = dict(os.environ)
    workspace = str(Path(__file__).resolve().parents[3])
    environment["PYTHONPATH"] = workspace + (
        os.pathsep + environment["PYTHONPATH"]
        if environment.get("PYTHONPATH")
        else ""
    )
    completed = _execute(command, environment=environment)
    if completed is None:
        return _record(
            scenario=scenario,
            sanitizer="none",
            command=command,
            completed=None,
            status="UNSUPPORTED",
            expectation="success",
            reason="streaming resource child probe could not be exercised",
        )
    marker = f"SAFETY_PROBE PASSED {scenario}".encode()
    unsupported_marker = f"SAFETY_PROBE UNSUPPORTED {scenario}".encode()
    if unsupported_marker in completed.stdout:
        status = "UNSUPPORTED"
        reason = "child probe lacks /proc/self/fd support"
    else:
        status = "PASSED" if completed.returncode == 0 and marker in completed.stdout else "FAILED"
        reason = (
            f"marker SAFETY_PROBE PASSED {scenario} observed; streaming resource invariant observed"
            if status == "PASSED"
            else "streaming resource child probe failed"
        )
    return _record(
        scenario=scenario,
        sanitizer="none",
        command=command,
        completed=completed,
        status=status,
        expectation="success",
        reason=reason,
    )


def _invariant_status(records: Sequence[Mapping[str, object]], scenarios: Sequence[str]) -> str:
    selected = [record for record in records if record.get("scenario") in scenarios]
    if not selected:
        return "UNSUPPORTED"
    statuses = [str(record["status"]) for record in selected]
    if "FAILED" in statuses:
        return "FAILED"
    if "UNSUPPORTED" in statuses:
        return "UNSUPPORTED"
    return "PASSED"


def _recompute_invariants(records: Sequence[Mapping[str, object]]) -> dict[str, str]:
    map_scenario = MAP_SCENARIOS[0]
    return {
        "map_collisions": _invariant_status(records, (map_scenario,)),
        "map_growth": _invariant_status(records, (map_scenario,)),
        "map_updates": _invariant_status(records, (map_scenario,)),
        "map_cleanup": _invariant_status(records, (map_scenario,)),
        "map_checked_overflow": _invariant_status(records, (MAP_SCENARIOS[1],)),
        **{scenario: _invariant_status(records, (scenario,)) for scenario in RESOURCE_SCENARIOS},
    }


def _recompute_status(records: Sequence[Mapping[str, object]]) -> str:
    statuses = [str(record["status"]) for record in records]
    return "FAILED" if "FAILED" in statuses else "UNSUPPORTED" if "UNSUPPORTED" in statuses else "PASSED"


def _recompute_proofs(
    records: Sequence[Mapping[str, object]], invariants: Mapping[str, str]
) -> dict[str, bool]:
    proofs: dict[str, bool] = {}
    if all(invariants[name] == "PASSED" for name in ("map_collisions", "map_growth", "map_updates", "map_cleanup", "map_checked_overflow")):
        proofs["map_safety"] = True
    if all(invariants[name] == "PASSED" for name in RESOURCE_SCENARIOS):
        proofs["streaming_resource_safety"] = True
    if records and all(record["status"] == "PASSED" for record in records):
        proofs["all_relevant_executable_checks"] = True
    return proofs


def run_productive_safety(root: str | Path = ".") -> dict[str, object]:
    """Build and exercise the targeted Map and streaming-resource safety corpus."""
    root_path = Path(root).resolve()
    compiler = _compiler()
    records: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="productive-safety-", dir=str(root_path)) as temporary:
        directory = Path(temporary)
        supports = {
            sanitizer: _support_probe(compiler, sanitizer, directory)
            for sanitizer in _MAP_SANITIZERS
        }
        for scenario, source in ((MAP_SCENARIOS[0], _MAP_SOURCE), (MAP_SCENARIOS[1], _OVERFLOW_SOURCE)):
            for sanitizer in _MAP_SANITIZERS:
                records.append(
                    _map_record(
                        compiler=compiler,
                        sanitizer=sanitizer,
                        scenario=scenario,
                        source=source,
                        directory=directory,
                        support=supports[sanitizer],
                    )
                )
        records.extend(_resource_record(scenario, directory) for scenario in RESOURCE_SCENARIOS)

    record_views: tuple[Mapping[str, object], ...] = tuple(records)
    invariants = _recompute_invariants(record_views)
    status = _recompute_status(record_views)
    proofs = _recompute_proofs(record_views, invariants)
    report: dict[str, object] = {
        "schema_version": SAFETY_SCHEMA_VERSION,
        "kind": SAFETY_KIND,
        "status": status,
        "records": records,
        "invariants": invariants,
        "aggregate_proofs": proofs,
    }
    validate_productive_safety_report(report)
    return report


def validate_productive_safety_record(record: Mapping[str, object]) -> None:
    """Reject malformed or semantically forged executable evidence records."""
    if set(record) != set(SAFETY_RECORD_FIELDS):
        raise ValueError("safety record fields are missing or forged")
    scenario = record.get("scenario")
    sanitizer = record.get("sanitizer")
    if scenario not in (*MAP_SCENARIOS, *RESOURCE_SCENARIOS) or sanitizer not in SAFETY_SANITIZERS:
        raise ValueError("unknown safety scenario or sanitizer")
    command = record.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("safety record command must be argv")
    if scenario in MAP_SCENARIOS:
        if sanitizer not in _MAP_SANITIZERS:
            raise ValueError("Map safety record sanitizer is inconsistent")
        command_needles = (sanitizer, _SANITIZER_FLAGS[sanitizer][0])
        if not any(needle in item for item in command for needle in command_needles):
            raise ValueError("Map safety command lacks sanitizer evidence")
    elif sanitizer != "none":
        raise ValueError("resource safety record must use sanitizer none")
    if not isinstance(record.get("stdout_sha256"), str) or not isinstance(record.get("stderr_sha256"), str):
        raise ValueError("safety record digest fields are invalid")
    for name in ("stdout_sha256", "stderr_sha256"):
        digest = record[name]
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("safety record digest is invalid")
    status = record.get("status")
    expectation = record.get("expectation")
    exit_code = record.get("exit_code")
    expected_expectation = "typed_failure" if scenario == MAP_SCENARIOS[1] else "success"
    if status not in SAFETY_STATUSES or expectation != expected_expectation:
        raise ValueError("safety record status or expectation is invalid")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise ValueError("safety record exit code is invalid")
    if status == "UNSUPPORTED" and exit_code is not None:
        raise ValueError("unsupported safety record must not have an exit code")
    if status == "FAILED" and exit_code is None:
        raise ValueError("failed safety record must be executed")
    if scenario in MAP_SCENARIOS:
        marker = "MerloOverflow:MapUInt64" if expectation == "typed_failure" else "OK result="
    else:
        marker = f"SAFETY_PROBE PASSED {scenario}"
    if status == "PASSED":
        expected_exit = 0 if expectation == "success" else None
        if (expectation == "success" and exit_code != expected_exit) or (
            expectation == "typed_failure" and (exit_code is None or exit_code == 0)
        ):
            raise ValueError("passed safety record has an inconsistent exit code")
        reason = record.get("reason")
        if not isinstance(reason, str) or marker not in reason:
            raise ValueError("passed safety record reason lacks an observed marker")
    reason = record.get("reason")
    if not isinstance(reason, str) or not reason:
        raise ValueError("safety record reason is required")


def validate_productive_safety_report(report: Mapping[str, object]) -> None:
    """Reject missing, forged, incomplete, or unsupported safety proofs."""
    required = {
        "schema_version",
        "kind",
        "status",
        "records",
        "invariants",
        "aggregate_proofs",
    }
    if set(report) != required:
        raise ValueError("productive safety report fields are missing or forged")
    if report.get("schema_version") != SAFETY_SCHEMA_VERSION or report.get("kind") != SAFETY_KIND:
        raise ValueError("unsupported productive safety schema")
    records = report.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("productive safety records are required")
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("safety record must be a mapping")
        validate_productive_safety_record(record)

    expected_keys = {
        (scenario, sanitizer)
        for scenario in MAP_SCENARIOS
        for sanitizer in _MAP_SANITIZERS
    } | {(scenario, "none") for scenario in RESOURCE_SCENARIOS}
    actual_keys = [(str(record["scenario"]), str(record["sanitizer"])) for record in records]
    if (
        len(records) != len(expected_keys)
        or len(actual_keys) != len(set(actual_keys))
        or set(actual_keys) != expected_keys
    ):
        raise ValueError("productive safety record coverage has duplicates, omissions, or extras")

    invariants = report.get("invariants")
    if (
        not isinstance(invariants, Mapping)
        or set(invariants) != set(SAFETY_INVARIANTS)
    ):
        raise ValueError("productive safety invariants are incomplete or forged")
    if any(value not in SAFETY_STATUSES for value in invariants.values()):
        raise ValueError("productive safety invariant status is invalid")
    expected_invariants = _recompute_invariants(tuple(records))
    if dict(invariants) != expected_invariants:
        raise ValueError("productive safety invariant status is forged")

    expected_status = _recompute_status(tuple(records))
    if report.get("status") != expected_status:
        raise ValueError("productive safety status is forged")

    proofs = report.get("aggregate_proofs")
    if not isinstance(proofs, dict):
        raise ValueError("productive safety aggregate proofs are invalid")
    expected_proofs = _recompute_proofs(tuple(records), expected_invariants)
    if proofs != expected_proofs:
        raise ValueError("aggregate proof is forged")


__all__ = [
    "MAP_SCENARIOS",
    "RESOURCE_SCENARIOS",
    "SAFETY_INVARIANTS",
    "SAFETY_KIND",
    "SAFETY_RECORD_FIELDS",
    "SAFETY_SANITIZERS",
    "SAFETY_SCHEMA_VERSION",
    "run_productive_safety",
    "validate_productive_safety_record",
    "validate_productive_safety_report",
]
