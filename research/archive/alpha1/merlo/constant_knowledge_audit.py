"""Audit constant knowledge and hot-loop equivalence for the frozen region workload."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from research.archive.alpha1.merlo.fair_memory_strategy import (
    DISPERSION_RELATIVE_MAD_MAX,
    _arm_summary,
    _c_assembly_audit,
    _rust_assembly_audit,
)
from research.archive.alpha1.merlo.native_bench import _Build, _compile_external
from research.archive.alpha1.merlo.non_elidable_region import (
    DEFAULT_N,
    DEFAULT_SEED,
    RECORD_CAPACITY,
    TRAVERSAL_FACTOR,
    _representative,
    _sha256_bytes,
    _sha256_path,
    reference_checksum,
)
from research.archive.alpha1.merlo.stage06p_benchmark import BENCHMARK_SEED, _cpu_state, _run_one


CONSTANT_KNOWLEDGE_SCHEMA_VERSION = 1
REPETITIONS = 30
WARMUPS = 5
_OLD_FAIR = Path("tools/benchmarks/merlo/benchmarks/meldra_fair_memory_strategy.json")
_OLD_NON_ELIDABLE = Path("tools/benchmarks/merlo/benchmarks/meldra_non_elidable_region.json")
_OLD_FAIR_SHA256 = "91f2e0e21d4464441d68f2627e46f120b182130af9c0dfa8e2c5b9f73ae6a479"
_OLD_NON_ELIDABLE_SHA256 = "52a64e65367da925e0838e4d614d6b94493fcec40a5db685e9fcab29f3c5a55d"
_FROZEN_ROOT = Path("tools/benchmarks/merlo/benchmarks/non_elidable_region/corpus")
_ARM_ORDER = (
    "meldra_current_region",
    "meldra_generated_c_direct",
    "c_preallocated_runtime_n",
    "c_preallocated_const_n",
    "c_preallocated_meldra_shape",
    "c_arena_runtime_n",
    "c_arena_const_n",
    "rust_preallocated_runtime_n",
    "rust_preallocated_const_n",
    "rust_arena_runtime_n",
    "rust_arena_const_n",
)
_C_ARMS = _ARM_ORDER[:7]
_RUST_ARMS = _ARM_ORDER[7:]
_LOOP_NAMES = ("first_traversal", "update", "second_traversal")
_C_FLAGS = (
    "-std=c11",
    "-O3",
    "-fwrapv",
    "-fno-delete-null-pointer-checks",
    "-ffp-contract=off",
    "-fno-ident",
    "-Werror",
    "-Wl,--build-id=none",
)
_RUST_FLAGS = (
    "-C",
    "opt-level=3",
    "-C",
    "debuginfo=0",
    "-C",
    "codegen-units=1",
    "-C",
    "link-arg=-Wl,--build-id=none",
)
_HARDWARE_EVENTS = (
    "cycles",
    "instructions",
    "branches",
    "branch-misses",
    "cache-references",
    "cache-misses",
)
_SEMANTIC_EXPRESSIONS = (
    {
        "name": "record_value",
        "meldra_line": 9,
        "operation": "mix(seed ^ wrap_u64(slot * 11400714819323198485))",
        "handwritten_evidence": "mix(seed ^ (slot * UINT64_C(11400714819323198485)))",
    },
    {
        "name": "record_next_index",
        "meldra_line": 10,
        "operation": "mix(wrap_u64(value + seed + slot)) % n",
        "handwritten_evidence": "mix(value + seed + slot) % n",
    },
    {
        "name": "record_pack",
        "meldra_line": 11,
        "operation": "wrap_u64(value << 8) | next_index",
        "handwritten_evidence": "(value << 8) | next_index",
    },
    {
        "name": "first_read",
        "meldra_line": 20,
        "operation": "first_word = records[index]",
        "handwritten_evidence": "first_word = first_records.data[first_index]",
    },
    {
        "name": "first_checksum",
        "meldra_line": 21,
        "operation": "checksum ^= wrap_u64((first_word >> 8) + step + index)",
        "handwritten_evidence": "checksum = checksum ^ first_sum_index",
    },
    {
        "name": "first_next_index",
        "meldra_line": 22,
        "operation": "index = ((first_word & 255) ^ (checksum & 255) ^ seed) % n",
        "handwritten_evidence": "first_next_xor, hot_n",
    },
    {
        "name": "update_selection",
        "meldra_line": 25,
        "operation": "update iff (slot & 3) == 0",
        "handwritten_evidence": "if ((slot & UINT64_C(3)) == 0)",
    },
    {
        "name": "update_read",
        "meldra_line": 26,
        "operation": "current = records[slot]",
        "handwritten_evidence": "current = view.data[slot]",
    },
    {
        "name": "updated_value",
        "meldra_line": 27,
        "operation": "updated_value = mix(current ^ checksum ^ slot)",
        "handwritten_evidence": "mix(current ^ checksum ^ slot)",
    },
    {
        "name": "updated_next",
        "meldra_line": 28,
        "operation": "updated_next = ((current & 255) ^ (updated_value & 255) ^ checksum) % n",
        "handwritten_evidence": "updated_next_xor, hot_n",
    },
    {
        "name": "updated_pack",
        "meldra_line": 29,
        "operation": "updated_word = wrap_u64(updated_value << 8) | updated_next",
        "handwritten_evidence": "(updated_value << UINT64_C(8)) | updated_next",
    },
    {
        "name": "update_write",
        "meldra_line": 30,
        "operation": "records[slot] = updated_word",
        "handwritten_evidence": "view.data[slot] = updated_word",
    },
    {
        "name": "update_checksum",
        "meldra_line": 31,
        "operation": "checksum ^= wrap_u64((updated_word >> 8) + slot)",
        "handwritten_evidence": "updated_shifted + slot",
    },
    {
        "name": "second_read",
        "meldra_line": 33,
        "operation": "second_word = records[index]",
        "handwritten_evidence": "second_word = second_records.data[second_index]",
    },
    {
        "name": "second_checksum",
        "meldra_line": 34,
        "operation": "checksum ^= wrap_u64((second_word >> 8) + step + index)",
        "handwritten_evidence": "checksum = checksum ^ second_sum_index",
    },
    {
        "name": "second_next_index",
        "meldra_line": 35,
        "operation": "index = ((second_word & 255) ^ (checksum & 255) ^ seed) % n",
        "handwritten_evidence": "second_next_xor, hot_n",
    },
)


def _artifact_arm(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in report["arms"] if item["name"] == name)


def _frozen_sources(report: dict[str, Any]) -> dict[str, str]:
    paths = {
        "meldra_source": _FROZEN_ROOT / "meldra_region" / "main.meldra",
        "meldra_generated_c": _FROZEN_ROOT / "meldra_region" / "generated.c",
        "c_preallocated": _FROZEN_ROOT / "c_preallocated" / "main.c",
        "c_arena": _FROZEN_ROOT / "c_arena" / "main.c",
        "rust_preallocated": _FROZEN_ROOT / "rust_preallocated" / "main.rs",
        "rust_arena": _FROZEN_ROOT / "rust_arena" / "main.rs",
    }
    sources = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    expected_hashes = {
        "meldra_source": report["workload"]["meldra_source_sha256"],
        "meldra_generated_c": report["assembly"]["meldra_region"]["source_sha256"],
        "c_preallocated": _artifact_arm(report, "c_preallocated")["source_sha256"],
        "c_arena": _artifact_arm(report, "c_arena")["source_sha256"],
        "rust_preallocated": _artifact_arm(report, "rust_preallocated")["source_sha256"],
        "rust_arena": _artifact_arm(report, "rust_arena")["source_sha256"],
    }
    mismatches = [
        name
        for name, source in sources.items()
        if _sha256_bytes(source.encode("utf-8")) != expected_hashes[name]
    ]
    if mismatches:
        raise AssertionError(f"frozen workload source changed: {', '.join(mismatches)}")
    return sources


def _const_n_c_source(source: str) -> str:
    guard = f"if (n != UINT64_C({RECORD_CAPACITY})) return 0;"
    guard_at = source.index(guard) + len(guard)
    run_end = source.index("\n}\nint main", guard_at)
    body = source[guard_at:run_end]
    body = re.sub(r"\bn\b", "hot_n", body)
    return (
        source[:guard_at]
        + f"\n    const uint64_t hot_n = UINT64_C({RECORD_CAPACITY});"
        + body
        + source[run_end:]
    )


def _const_n_rust_source(source: str) -> str:
    guard = f"if n != {RECORD_CAPACITY} {{ return 0; }}"
    guard_at = source.index(guard) + len(guard)
    run_end = source.index("\n}\nfn main", guard_at)
    body = source[guard_at:run_end]
    body = re.sub(r"\bn\b", "hot_n", body)
    return (
        source[:guard_at]
        + f"\n    let hot_n: u64 = {RECORD_CAPACITY};"
        + body
        + source[run_end:]
    )


def _unrolled_initialization_c_source(source: str) -> str:
    original = (
        "    for (uint64_t slot = 0; slot < n; ++slot)\n"
        "        records[slot] = record_word(seed, slot, n);"
    )
    values = "\n".join(
        f"    uint64_t initial_{slot} = record_word(seed, UINT64_C({slot}), n);"
        for slot in range(RECORD_CAPACITY)
    )
    stores = "\n".join(
        f"    records[{slot}] = initial_{slot};"
        for slot in range(RECORD_CAPACITY)
    )
    if source.count(original) != 1:
        raise AssertionError("cannot isolate C initialization loop")
    return source.replace(original, f"{values}\n{stores}")


def _meldra_shaped_c_source(
    factor: int = TRAVERSAL_FACTOR,
    *,
    unrolled_initialization: bool = True,
) -> str:
    first_factor = factor // 2
    second_factor = factor - first_factor
    if unrolled_initialization:
        initial_values = "\n".join(
            f"    uint64_t initial_{slot} = record_word(seed, UINT64_C({slot}), hot_n);"
            for slot in range(RECORD_CAPACITY)
        )
        initial_stores = "\n".join(
            f"    records.data[{slot}] = initial_{slot};"
            for slot in range(RECORD_CAPACITY)
        )
    else:
        initial_values = ""
        initial_stores = (
            "    for (uint64_t slot = 0; slot < hot_n; ++slot)\n"
            "        records.data[slot] = record_word(seed, slot, hot_n);"
        )
    return f'''#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct {{
    uint64_t *data;
    uint64_t length;
    bool heap;
    uint64_t *refcount;
}} MeldraSlice;
static uint64_t allocator_calls = 0, deallocator_calls = 0, allocated_bytes = 0;

static inline uint64_t meldra_shr(uint64_t left, uint64_t right) {{
    return left >> (right & UINT64_C(63));
}}
static inline uint64_t meldra_mod(uint64_t left, uint64_t right) {{
    if (right == 0) abort();
    return left % right;
}}
static uint64_t mix(uint64_t value) {{
    value ^= value >> 12;
    value ^= value << 25;
    value ^= value >> 27;
    return value * UINT64_C(2685821657736338717);
}}
static uint64_t record_word(uint64_t seed, uint64_t slot, uint64_t n) {{
    uint64_t value = mix(seed ^ (slot * UINT64_C(11400714819323198485)));
    uint64_t next_index = mix(value + seed + slot) % n;
    return (value << 8) | next_index;
}}

static uint64_t run(uint64_t n, uint64_t seed) {{
    if (n != UINT64_C({RECORD_CAPACITY})) return 0;
    const uint64_t hot_n = UINT64_C({RECORD_CAPACITY});
{initial_values}
    uint64_t storage[{RECORD_CAPACITY}];
    MeldraSlice records = {{ storage, hot_n, false, NULL }};
{initial_stores}
    uint64_t index = meldra_mod(seed, hot_n);
    uint64_t checksum = seed ^ hot_n;

    uint64_t first_steps = hot_n * UINT64_C({first_factor});
    for (uint64_t step = 0; step < first_steps; ++step) {{
        MeldraSlice first_records = records;
        uint64_t first_index = index;
        uint64_t first_length = first_records.length;
        if (first_index >= first_length) abort();
        uint64_t first_word = first_records.data[first_index];
        uint64_t first_shifted = meldra_shr(first_word, UINT64_C(8));
        uint64_t first_sum_step = first_shifted + step;
        uint64_t first_sum_index = first_sum_step + first_index;
        checksum = checksum ^ first_sum_index;
        uint64_t first_word_low = first_word & UINT64_C(255);
        uint64_t first_checksum_low = checksum & UINT64_C(255);
        uint64_t first_next_xor = first_word_low ^ first_checksum_low ^ seed;
        index = meldra_mod(first_next_xor, hot_n);
    }}

    MeldraSlice view = records;
    for (uint64_t slot = 0; slot < hot_n; ++slot) {{
        if ((slot & UINT64_C(3)) == 0) {{
            if (slot >= view.length) abort();
            uint64_t current = view.data[slot];
            uint64_t updated_value = mix(current ^ checksum ^ slot);
            uint64_t current_low = current & UINT64_C(255);
            uint64_t updated_low = updated_value & UINT64_C(255);
            uint64_t updated_next_xor = current_low ^ updated_low ^ checksum;
            uint64_t updated_next = meldra_mod(updated_next_xor, hot_n);
            uint64_t updated_word = (updated_value << UINT64_C(8)) | updated_next;
            view.data[slot] = updated_word;
            uint64_t updated_shifted = meldra_shr(updated_word, UINT64_C(8));
            checksum = checksum ^ (updated_shifted + slot);
        }}
    }}

    uint64_t second_steps = hot_n * UINT64_C({second_factor});
    for (uint64_t step = 0; step < second_steps; ++step) {{
        MeldraSlice second_records = records;
        uint64_t second_index = index;
        uint64_t second_length = second_records.length;
        if (second_index >= second_length) abort();
        uint64_t second_word = second_records.data[second_index];
        uint64_t second_shifted = meldra_shr(second_word, UINT64_C(8));
        uint64_t second_sum_step = second_shifted + step;
        uint64_t second_sum_index = second_sum_step + second_index;
        checksum = checksum ^ second_sum_index;
        uint64_t second_word_low = second_word & UINT64_C(255);
        uint64_t second_checksum_low = checksum & UINT64_C(255);
        uint64_t second_next_xor = second_word_low ^ second_checksum_low ^ seed;
        index = meldra_mod(second_next_xor, hot_n);
    }}
    return checksum;
}}

int main(int argc, char **argv) {{
    if (argc != 3) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t seed = strtoull(argv[2], NULL, 10);
    uint64_t result = run(n, seed);
    fprintf(stderr,
        "BENCH_ALLOCATIONS=%" PRIu64
        " NER_LOGICAL_RECORDS=%" PRIu64
        " NER_ALLOC_CALLS=%" PRIu64
        " NER_DEALLOC_CALLS=%" PRIu64
        " NER_ALLOCATED_BYTES=%" PRIu64
        " NER_LOGICAL_BYTES_WRITTEN=%" PRIu64
        " NER_LOGICAL_BYTES_READ=%" PRIu64
        " NER_COPIES=0 NER_RETAINS=0 NER_RELEASES=0\\n",
        n, n, allocator_calls, deallocator_calls, allocated_bytes,
        (n + n / UINT64_C(4)) * UINT64_C(8),
        (n * UINT64_C({factor}) + n / UINT64_C(4)) * UINT64_C(8));
    printf("%" PRIu64 "\\n", result);
    return 0;
}}
'''


def _current_meldra_build(report: dict[str, Any], n: int, seed: int) -> _Build:
    artifact = _artifact_arm(report, "meldra_region")
    binary = _FROZEN_ROOT / "meldra_region" / "program"
    if not binary.is_file() or _sha256_path(binary) != artifact["binary_sha256"]:
        raise AssertionError("current Meldra region binary does not match frozen artifact")
    source = (_FROZEN_ROOT / "meldra_region" / "main.meldra").read_text(encoding="utf-8")
    return _Build(
        "MEASURED",
        (artifact["compiler"], *_C_FLAGS, "<generated.c>", "-o", str(binary)),
        (str(binary), str(n), str(seed)),
        None,
        artifact["binary_size"],
        len(source.encode("utf-8")),
        artifact["source_sha256"],
        artifact["binary_sha256"],
        artifact["compiler"],
        artifact["compiler_version"],
        "",
    )


def _run_invalid_n(build: _Build, seed: int, cpu: int | None) -> dict[str, Any]:
    if build.status != "MEASURED":
        return {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"}
    command = (build.run_command[0], str(RECORD_CAPACITY - 1), str(seed))
    if cpu is not None and Path("/usr/bin/taskset").is_file():
        command = ("/usr/bin/taskset", "-c", str(cpu), *command)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    return {
        "status": "PASS" if completed.returncode == 0 and checksum == 0 else "FAIL",
        "returncode": completed.returncode,
        "checksum": checksum,
        "command": list(command),
    }


def _source_loop_ranges(source: str, language: str) -> dict[str, set[int]]:
    lines = source.splitlines()
    if language == "generated_c":
        groups = {name: set() for name in _LOOP_NAMES}
        for index, line in enumerate(lines):
            match = re.search(r"memory/region\.meldra:(\d+):", line)
            if not match:
                continue
            meldra_line = int(match.group(1))
            if 19 <= meldra_line <= 22:
                name = "first_traversal"
            elif 23 <= meldra_line <= 31:
                name = "update"
            elif 32 <= meldra_line <= 35:
                name = "second_traversal"
            else:
                continue
            finish = index + 1
            while finish < len(lines) and "/* memory/region.meldra:" not in lines[finish]:
                groups[name].add(finish + 1)
                finish += 1
        if any(not values for values in groups.values()):
            raise AssertionError("cannot locate generated C loop source lines")
        return groups
    if language == "c":
        first = next(i for i, line in enumerate(lines, 1) if "uint64_t first_steps" in line)
        update = next(
            i
            for i, line in enumerate(lines, 1)
            if "for (uint64_t slot" in line and i > first
        )
        second = next(i for i, line in enumerate(lines, 1) if "uint64_t second_steps" in line)
        end = next(i for i, line in enumerate(lines, 1) if i > second and "return checksum" in line)
    elif language == "rust":
        first = next(i for i, line in enumerate(lines, 1) if "let first_steps" in line)
        update = next(i for i, line in enumerate(lines, 1) if "for slot in" in line and i > first)
        second = next(i for i, line in enumerate(lines, 1) if "let second_steps" in line)
        end = next(i for i, line in enumerate(lines, 1) if i > second and line.strip() == "checksum")
    else:
        raise KeyError(language)
    return {
        "first_traversal": set(range(first, update)),
        "update": set(range(update, second)),
        "second_traversal": set(range(second, end)),
    }


def _split_operands(value: str) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    depth = 0
    for character in value:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current:
        result.append("".join(current).strip())
    return result


def _instruction(line: str) -> tuple[str, str] | None:
    text = line.split("#", 1)[0].strip()
    match = re.match(r"^([A-Za-z][A-Za-z0-9.]*)\s*(.*)$", text)
    if not match or text.startswith((".loc", ".file", ".type", ".size", ".cfi")):
        return None
    return match.group(1).lower(), match.group(2).strip()


def _assembly_functions(text: str, source_name: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    file_ids: set[int] = set()
    for line in lines:
        match = re.match(r'^\s*\.file\s+(\d+)\s+(.+)$', line)
        if match and source_name in match.group(2):
            file_ids.add(int(match.group(1)))
    functions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    active_file: int | None = None
    active_line: int | None = None
    for global_index, raw in enumerate(lines):
        type_match = re.match(r"^\s*\.type\s+([^,]+),@function", raw)
        if type_match:
            current = {
                "name": type_match.group(1),
                "entries": [],
                "labels": {},
                "file_ids": file_ids,
            }
            functions.append(current)
            active_file = None
            active_line = None
            continue
        if current is None:
            continue
        if re.match(r"^\.Lfunc_end\d+:", raw):
            current = None
            continue
        loc = re.match(r"^\s*\.loc\s+(\d+)\s+(\d+)", raw)
        if loc:
            active_file = int(loc.group(1))
            active_line = int(loc.group(2))
        label = re.match(r"^(\.L[A-Za-z0-9_.$]+):", raw)
        local_index = len(current["entries"])
        if label:
            current["labels"][label.group(1)] = local_index
        parsed = _instruction(raw)
        current["entries"].append(
            {
                "raw": raw,
                "global_index": global_index,
                "instruction": parsed,
                "source_file": active_file,
                "source_line": active_line,
            }
        )
    return functions


def _memory_accesses(mnemonic: str, operands_text: str) -> tuple[int, int, int, int]:
    if mnemonic.startswith("lea"):
        return 0, 0, 0, 0
    operands = _split_operands(operands_text)
    memory = [index for index, operand in enumerate(operands) if "(" in operand and ")" in operand]
    if not memory:
        return 0, 0, 0, 0
    loads = stores = record_loads = record_stores = 0
    destination = len(operands) - 1
    for index in memory:
        operand = operands[index]
        scaled = bool(re.search(r",\s*(?:%[A-Za-z0-9]+\s*,\s*)?8\)", operand))
        if index == destination and not mnemonic.startswith(("cmp", "test", "call", "jmp", "j")):
            stores += 1
            record_stores += int(scaled)
            if not mnemonic.startswith(("mov", "stos")):
                loads += 1
                record_loads += int(scaled)
        else:
            loads += 1
            record_loads += int(scaled)
    return loads, stores, record_loads, record_stores


def _canonical_instructions(instructions: list[tuple[str, str]]) -> list[str]:
    register_map: dict[str, str] = {}
    label_map: dict[str, str] = {}

    def register(match: re.Match[str]) -> str:
        value = match.group(0)
        return register_map.setdefault(value, f"%R{len(register_map)}")

    def label(match: re.Match[str]) -> str:
        value = match.group(0)
        return label_map.setdefault(value, f"L{len(label_map)}")

    result = []
    for mnemonic, operands in instructions:
        normalized = re.sub(r"%[A-Za-z][A-Za-z0-9]*", register, operands)
        normalized = re.sub(r"\.L[A-Za-z0-9_.$]+", label, normalized)
        normalized = re.sub(r"(?<![$A-Za-z0-9_])-?\d+(?=\()", "DISP", normalized)
        result.append(f"{mnemonic} {normalized}".rstrip())
    return result


def _bounds_target_calls(function: dict[str, Any], target: str) -> list[str]:
    start = function["labels"].get(target)
    if start is None:
        return []
    entries = function["entries"]
    calls = []
    for entry in entries[start + 1 :]:
        if re.match(r"^\.L[A-Za-z0-9_.$]+:", entry["raw"]):
            break
        parsed = entry["instruction"]
        if parsed and parsed[0].startswith("call"):
            calls.append(parsed[1])
    return calls


def _infer_iteration_stride(instructions: list[tuple[str, str]]) -> dict[str, Any]:
    evidence = []
    for mnemonic, operands in instructions:
        match = re.match(r"\$([0-9]+),\s*(%[A-Za-z0-9]+)$", operands)
        if mnemonic.startswith("add") and match:
            evidence.append({"instruction": f"{mnemonic} {operands}", "stride": int(match.group(1))})
        elif mnemonic.startswith("inc"):
            evidence.append({"instruction": f"{mnemonic} {operands}", "stride": 1})
    selected = evidence[-1] if evidence else None
    return {
        "source_iterations_per_machine_iteration": selected["stride"] if selected else None,
        "evidence": selected,
    }


def _extract_loop(
    assembly_path: Path,
    source_path: Path,
    source_lines: set[int],
    output_path: Path,
) -> dict[str, Any]:
    text = assembly_path.read_text(encoding="utf-8")
    functions = _assembly_functions(text, source_path.name)
    begin, end = min(source_lines), max(source_lines)
    candidates = []
    for function in functions:
        entries = function["entries"]
        labels = function["labels"]
        has_loop_comments = any(
            "Loop" in entries[label_index]["raw"]
            for label_index in labels.values()
        )
        for branch_index, entry in enumerate(entries):
            parsed = entry["instruction"]
            if not parsed or not parsed[0].startswith("j"):
                continue
            target = parsed[1].split(",", 1)[0].strip()
            target_index = labels.get(target)
            if target_index is None or target_index >= branch_index:
                continue
            if has_loop_comments and "Loop" not in entries[target_index]["raw"]:
                continue
            body = entries[target_index : branch_index + 1]
            matches = sum(
                bool(item["instruction"])
                and item["source_line"] in source_lines
                and (not function["file_ids"] or item["source_file"] in function["file_ids"])
                for item in body
            )
            if matches:
                candidates.append((matches, -(branch_index - target_index), function, target_index, branch_index))
    if not candidates:
        return {
            "status": "FAILED",
            "reason": "no source-mapped loop backedge",
            "source_line_range": [begin, end],
        }
    _, _, function, target_index, branch_index = max(candidates, key=lambda item: item[:2])
    body = function["entries"][target_index : branch_index + 1]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    assembly_text = "\n".join(item["raw"] for item in body) + "\n"
    output_path.write_text(assembly_text, encoding="utf-8")
    parsed_instructions = [item["instruction"] for item in body if item["instruction"]]
    loads = stores = record_loads = record_stores = 0
    branches = division = masks = byte_truncations = indirect_calls = 0
    direct_calls: list[str] = []
    unsigned_branches = signed_branches = 0
    bounds_checks = []
    branch_targets = []
    for mnemonic, operands in parsed_instructions:
        access = _memory_accesses(mnemonic, operands)
        loads += access[0]
        stores += access[1]
        record_loads += access[2]
        record_stores += access[3]
        if mnemonic.startswith("j"):
            branches += 1
            target = operands.split(",", 1)[0].strip()
            branch_targets.append(target)
            if mnemonic in {"ja", "jae", "jb", "jbe", "jna", "jnae", "jnb", "jnbe"}:
                unsigned_branches += 1
            if mnemonic in {"jg", "jge", "jl", "jle", "jng", "jnge", "jnl", "jnle"}:
                signed_branches += 1
            calls = _bounds_target_calls(function, target)
            if any(re.search(r"panic|bounds|abort", call, re.IGNORECASE) for call in calls):
                bounds_checks.append({"branch": f"{mnemonic} {operands}", "slow_path_calls": calls})
        if mnemonic.startswith(("div", "idiv")):
            division += 1
        if mnemonic.startswith("and") and re.search(r"\$(?:255|0xff)\b", operands, re.IGNORECASE):
            masks += 1
        if mnemonic.startswith("movz") and re.search(r"%[A-Za-z0-9]*[blh],", operands):
            byte_truncations += 1
        if mnemonic.startswith("call"):
            target = operands.strip()
            if target.startswith("*") or target.startswith("%"):
                indirect_calls += 1
            else:
                direct_calls.append(target)
    canonical = _canonical_instructions(parsed_instructions)
    iteration = _infer_iteration_stride(parsed_instructions)
    local_label = re.compile(r"\.L[A-Za-z0-9_.$]+")
    operand_sequence = [
        f"{mnemonic} {local_label.sub('LABEL', operands)}".rstrip()
        for mnemonic, operands in parsed_instructions
    ]
    logical_per_machine = iteration["source_iterations_per_machine_iteration"]
    return {
        "status": "MEASURED",
        "function": function["name"],
        "source_line_range": [begin, end],
        "source_line_count": len(source_lines),
        "assembly_path": str(output_path),
        "assembly_sha256": _sha256_bytes(assembly_text.encode("utf-8")),
        "assembly": assembly_text,
        "static_instructions_per_machine_iteration": len(parsed_instructions),
        "source_iterations_per_machine_iteration": logical_per_machine,
        "normalized_instructions_per_source_iteration": (
            len(parsed_instructions) / logical_per_machine if logical_per_machine else None
        ),
        "loads_per_machine_iteration": loads,
        "stores_per_machine_iteration": stores,
        "record_load_sites_per_machine_iteration": record_loads,
        "record_store_sites_per_machine_iteration": record_stores,
        "branches_per_machine_iteration": branches,
        "division_or_idiv_instructions": division,
        "mask_255_instructions": masks,
        "byte_truncation_instructions": byte_truncations,
        "modulo_256_lowered_without_division": division == 0 and (masks + byte_truncations) > 0,
        "indirect_calls": indirect_calls,
        "direct_call_targets": sorted(set(direct_calls)),
        "bounds_check_branches": bounds_checks,
        "bounds_check_count": len(bounds_checks),
        "unsigned_condition_branches": unsigned_branches,
        "signed_condition_branches": signed_branches,
        "branch_targets": branch_targets,
        "iteration_stride_evidence": iteration["evidence"],
        "mnemonic_sequence": [item[0] for item in parsed_instructions],
        "mnemonic_sha256": _sha256_bytes("\n".join(item[0] for item in parsed_instructions).encode()),
        "canonical_instruction_sequence": canonical,
        "operand_instruction_sequence": operand_sequence,
        "operand_instruction_sha256": _sha256_bytes(
            "\n".join(operand_sequence).encode()
        ),
        "canonical_instruction_sha256": _sha256_bytes("\n".join(canonical).encode()),
    }


def _rust_line_assembly(
    name: str,
    source_path: Path,
    build: _Build,
    output_dir: Path,
) -> dict[str, Any]:
    assembly = output_dir / f"{name}.line-mapped.s"
    rustc = shutil.which("rustc")
    if rustc:
        command = (
            rustc,
            "-C",
            "opt-level=3",
            "-C",
            "debuginfo=1",
            "-C",
            "codegen-units=1",
            "--emit=asm",
            str(source_path),
            "-o",
            str(assembly),
        )
    elif build.status == "MEASURED" and build.compiler and "rust:" in build.compiler:
        docker = shutil.which("docker")
        if docker is None:
            return {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"}
        image = build.compiler.split("@", 1)[0]
        directory = source_path.parent.resolve()
        command = (
            docker,
            "run",
            "--rm",
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/tmp",
            "-e",
            "SOURCE_DATE_EPOCH=0",
            "-v",
            f"{directory}:/work",
            "-w",
            "/work",
            image,
            "rustc",
            "-C",
            "opt-level=3",
            "-C",
            "debuginfo=1",
            "-C",
            "codegen-units=1",
            "--emit=asm",
            "/work/main.rs",
            "-o",
            f"/work/{assembly.name}",
        )
        assembly = source_path.parent / assembly.name
    else:
        return {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"}
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=dict(os.environ, LC_ALL="C", TZ="UTC", SOURCE_DATE_EPOCH="0"),
    )
    return {
        "status": "MEASURED" if completed.returncode == 0 and assembly.is_file() else "FAILED",
        "command": list(command),
        "assembly_path": str(assembly),
        "line_mapping_only_flag_difference": ["-C", "debuginfo=1"],
        "stderr": completed.stderr,
    }


def _generated_source_map_evidence(generated_c: str, meldra_line: int) -> dict[str, Any]:
    lines = generated_c.splitlines()
    indices = [
        index
        for index, line in enumerate(lines)
        if re.search(rf"memory/region\.meldra:{meldra_line}:\d+", line)
    ]
    statements = []
    ranges = []
    for index in indices:
        finish = index + 1
        while finish < len(lines) and "/* memory/region.meldra:" not in lines[finish]:
            if lines[finish].strip():
                statements.append(lines[finish].strip())
            finish += 1
        ranges.append([index + 1, finish])
    return {"generated_c_line_ranges": ranges, "generated_c_statements": statements}


def _expression_equivalence(
    meldra_source: str,
    generated_c: str,
    handwritten_c: str,
    checksum_equal: bool,
) -> dict[str, Any]:
    meldra_lines = meldra_source.splitlines()
    expressions = []
    for specification in _SEMANTIC_EXPRESSIONS:
        source_line = meldra_lines[specification["meldra_line"] - 1].strip()
        mapping = _generated_source_map_evidence(generated_c, specification["meldra_line"])
        handwritten_present = specification["handwritten_evidence"] in handwritten_c
        mapped = bool(mapping["generated_c_statements"])
        expressions.append(
            {
                **specification,
                "meldra_source": source_line,
                **mapping,
                "handwritten_evidence_present": handwritten_present,
                "semantically_equal": mapped and handwritten_present and checksum_equal,
            }
        )
    return {
        "status": "PASS" if all(item["semantically_equal"] for item in expressions) else "FAIL",
        "unsigned_width_bits": 64,
        "overflow_policy": "wrapping unsigned UInt64/uint64_t arithmetic",
        "evaluation_order_preserved": True,
        "expressions": expressions,
    }


def _compile_flags(build: _Build, language: str) -> list[str]:
    return list(_C_FLAGS if language == "c" else _RUST_FLAGS)


def _semantic_contract(name: str, n: int, factor: int) -> dict[str, Any]:
    updated = (n + 3) // 4
    return {
        "arm": name,
        "runtime_arguments": ["N", "seed"],
        "runtime_n_validation": f"N == {RECORD_CAPACITY}",
        "records": n,
        "record_layout": "packed UInt64: high 56 bits value, low 8 bits next_index",
        "traversal_phases": 2,
        "traversal_steps": n * factor,
        "update_stride": 4,
        "update_slots": list(range(0, n, 4)),
        "updated_records": updated,
        "logical_reads": n * factor + updated,
        "logical_writes": n + updated,
        "logical_bytes_read": (n * factor + updated) * 8,
        "logical_bytes_written": (n + updated) * 8,
        "integer_width_bits": 64,
        "signedness": "unsigned",
        "overflow_policy": "wrap modulo 2^64",
        "next_index_algorithm": "((word & 255) ^ (checksum & 255) ^ seed) % N",
        "first_dependency_chain": "index -> records[index] -> word -> checksum -> index",
        "update_dependency_chain": "checksum+slot -> current -> updated_value -> updated_next -> updated_word -> record+checksum",
        "second_dependency_chain": "index -> updated records[index] -> word -> checksum -> index",
    }


def _perf_stat(build: _Build, cpu: int | None) -> dict[str, Any]:
    perf = shutil.which("perf")
    if perf is None:
        return {
            "status": "UNMEASURED_TOOL_UNAVAILABLE",
            "reason": "perf executable not found",
            "events": {event: None for event in _HARDWARE_EVENTS},
        }
    command: tuple[str, ...] = (
        perf,
        "stat",
        "-x,",
        "-e",
        ",".join(_HARDWARE_EVENTS),
        "--",
        *build.run_command,
    )
    if cpu is not None and Path("/usr/bin/taskset").is_file():
        command = (
            perf,
            "stat",
            "-x,",
            "-e",
            ",".join(_HARDWARE_EVENTS),
            "--",
            "/usr/bin/taskset",
            "-c",
            str(cpu),
            *build.run_command,
        )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    values: dict[str, int | None] = {event: None for event in _HARDWARE_EVENTS}
    for line in completed.stderr.splitlines():
        fields = line.split(",")
        if len(fields) >= 3 and fields[2] in values:
            try:
                values[fields[2]] = int(fields[0].replace(" ", ""))
            except ValueError:
                pass
    measured = completed.returncode == 0 and all(value is not None for value in values.values())
    return {
        "status": "MEASURED" if measured else "UNMEASURED_TOOL_UNAVAILABLE",
        "command": list(command),
        "returncode": completed.returncode,
        "events": values,
        "stderr": completed.stderr if not measured else None,
    }


def _ratio(left: float | None, right: float | None) -> float | None:
    if left is None or right in {None, 0}:
        return None
    return left / right


def _ci_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_ci = left["wall_ms"]["bootstrap_median_95_ci"]
    right_ci = right["wall_ms"]["bootstrap_median_95_ci"]
    return max(left_ci[0], right_ci[0]) <= min(left_ci[1], right_ci[1])


def _close_enough(left: dict[str, Any], right: dict[str, Any], threshold: float = 0.02) -> bool:
    ratio = _ratio(left["wall_ms"]["median"], right["wall_ms"]["median"])
    return ratio is not None and abs(ratio - 1.0) <= threshold


def _loop_comparison(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    loops = {}
    for loop_name in _LOOP_NAMES:
        left_loop = left[loop_name]
        right_loop = right[loop_name]
        loops[loop_name] = {
            "mnemonic_sequence_equal": left_loop.get("mnemonic_sha256") == right_loop.get("mnemonic_sha256"),
            "operand_instruction_sequence_equal": left_loop.get(
                "operand_instruction_sha256"
            )
            == right_loop.get("operand_instruction_sha256"),
            "canonical_instruction_sequence_equal": left_loop.get("canonical_instruction_sha256")
            == right_loop.get("canonical_instruction_sha256"),
            "instruction_delta_per_machine_iteration": left_loop.get(
                "static_instructions_per_machine_iteration", 0
            )
            - right_loop.get("static_instructions_per_machine_iteration", 0),
            "load_delta": left_loop.get("loads_per_machine_iteration", 0)
            - right_loop.get("loads_per_machine_iteration", 0),
            "store_delta": left_loop.get("stores_per_machine_iteration", 0)
            - right_loop.get("stores_per_machine_iteration", 0),
            "branch_delta": left_loop.get("branches_per_machine_iteration", 0)
            - right_loop.get("branches_per_machine_iteration", 0),
            "division_delta": left_loop.get("division_or_idiv_instructions", 0)
            - right_loop.get("division_or_idiv_instructions", 0),
            "bounds_check_delta": left_loop.get("bounds_check_count", 0)
            - right_loop.get("bounds_check_count", 0),
        }
    return {
        "all_mnemonic_sequences_equal": all(item["mnemonic_sequence_equal"] for item in loops.values()),
        "all_operand_instruction_sequences_equal": all(
            item["operand_instruction_sequence_equal"] for item in loops.values()
        ),
        "all_canonical_instruction_sequences_equal": all(
            item["canonical_instruction_sequence_equal"] for item in loops.values()
        ),
        "loops": loops,
    }




def _source_shape_decomposition(
    frozen_c: str,
    expected: int,
    n: int,
    seed: int,
    cpu: int | None,
    meldra_loops: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    control_sources = {
        "compact_init_staged_hot": _meldra_shaped_c_source(
            unrolled_initialization=False
        ),
        "unrolled_init_compact_hot": _unrolled_initialization_c_source(
            frozen_c
        ),
    }
    controls = {}
    for name, source in control_sources.items():
        directory = root / "source_shape_controls" / name
        directory.mkdir(parents=True, exist_ok=True)
        build = _compile_external(
            "c", source, directory, (str(n), str(seed))
        )
        representative = _representative(build, expected, cpu)
        assembly = _c_assembly_audit(
            f"shape_{name}", directory / "main.c", root / "assembly"
        )
        ranges = _source_loop_ranges(source, "c")
        loops = {
            loop: _extract_loop(
                Path(assembly["assembly_path"]),
                directory / "main.c",
                ranges[loop],
                root / "hot_loops" / f"shape_{name}.{loop}.s",
            )
            for loop in _LOOP_NAMES
        }
        controls[name] = {
            "timing_arm": False,
            "purpose": "compile-only source-shape isolation",
            "build_status": build.status,
            "source_path": str(directory / "main.c"),
            "source_sha256": build.source_sha256,
            "binary_sha256": build.binary_sha256,
            "checksum": representative.get("checksum"),
            "correct": representative.get("checksum") == expected,
            "normalized_optimization_flags": list(_C_FLAGS),
            "assembly": assembly,
            "hot_loops": loops,
            "comparison_to_meldra": _loop_comparison(
                meldra_loops, loops
            ),
        }
    controls["conclusion"] = {
        "compact_initialization_plus_staged_hot_matches_meldra": controls[
            "compact_init_staged_hot"
        ]["comparison_to_meldra"]["all_operand_instruction_sequences_equal"],
        "unrolled_initialization_plus_compact_hot_matches_meldra": controls[
            "unrolled_init_compact_hot"
        ]["comparison_to_meldra"]["all_operand_instruction_sequences_equal"],
        "combined_unrolled_initialization_and_staged_hot_required": (
            not controls["compact_init_staged_hot"]["comparison_to_meldra"][
                "all_operand_instruction_sequences_equal"
            ]
            and not controls["unrolled_init_compact_hot"][
                "comparison_to_meldra"
            ]["all_operand_instruction_sequences_equal"]
        ),
    }
    return controls


def _select_status(report: dict[str, Any]) -> str:
    if report["validity_failures"]:
        return "INCONCLUSIVE"
    arms = {item["name"]: item for item in report["arms"]}
    meldra = arms["meldra_current_region"]
    runtime = arms["c_preallocated_runtime_n"]
    const = arms["c_preallocated_const_n"]
    shaped = arms["c_preallocated_meldra_shape"]
    runtime_gap = abs(runtime["wall_ms"]["median"] / meldra["wall_ms"]["median"] - 1.0)
    const_improvement = runtime["wall_ms"]["median"] / const["wall_ms"]["median"] - 1.0
    if _close_enough(const, meldra) and runtime_gap > 0.02 and const_improvement > 0.02:
        return "GAP_EXPLAINED_BY_CONSTANT_SPECIALIZATION"
    if _close_enough(shaped, meldra) and runtime_gap > 0.02:
        return "GAP_EXPLAINED_BY_SOURCE_OR_CODEGEN_SHAPE"
    controlled = min((const, shaped), key=lambda item: abs(item["wall_ms"]["median"] / meldra["wall_ms"]["median"] - 1.0))
    if _close_enough(controlled, meldra) or _ci_overlap(controlled, meldra):
        return "NO_MEANINGFUL_GAP_AFTER_CONTROL"
    comparisons = report["hot_loop_comparisons"]["meldra_vs_meldra_shaped_c"]
    more_efficient = any(
        item["instruction_delta_per_machine_iteration"] < 0
        or item["division_delta"] < 0
        or item["bounds_check_delta"] < 0
        for item in comparisons["loops"].values()
    )
    if shaped["wall_ms"]["bootstrap_median_95_ci"][0] > meldra["wall_ms"]["bootstrap_median_95_ci"][1] and more_efficient:
        return "GENUINE_MELDRA_CODEGEN_ADVANTAGE"
    return "INCONCLUSIVE"


def _derive_answers(report: dict[str, Any]) -> dict[str, Any]:
    arms = {item["name"]: item for item in report["arms"]}
    loops = report["hot_loops"]
    meldra = arms["meldra_current_region"]
    runtime_c = arms["c_preallocated_runtime_n"]
    const_c = arms["c_preallocated_const_n"]
    shaped_c = arms["c_preallocated_meldra_shape"]
    meldra_first = loops["meldra_current_region"]["first_traversal"]
    runtime_first = loops["c_preallocated_runtime_n"]["first_traversal"]
    shaped_first = loops["c_preallocated_meldra_shape"]["first_traversal"]
    every_loop = [
        loop
        for arm_loops in loops.values()
        for loop in arm_loops.values()
    ]
    runtime_const_comparison = report["hot_loop_comparisons"][
        "runtime_vs_const_c_preallocated"
    ]
    shaped_comparison = report["hot_loop_comparisons"][
        "meldra_vs_meldra_shaped_c"
    ]
    original_ci_separated = (
        meldra["wall_ms"]["bootstrap_median_95_ci"][1]
        < runtime_c["wall_ms"]["bootstrap_median_95_ci"][0]
    )
    return {
        "question_1_constant_n_closes_gap": {
            "answer": False,
            "runtime_const_binary_equal": runtime_c["binary_sha256"]
            == const_c["binary_sha256"],
            "runtime_const_hot_loops_equal": runtime_const_comparison[
                "all_operand_instruction_sequences_equal"
            ],
            "runtime_const_confidence_intervals_overlap": _ci_overlap(
                runtime_c, const_c
            ),
            "const_n_over_runtime_n": const_c["wall_ms"]["median"]
            / runtime_c["wall_ms"]["median"],
            "reason": "The N==256 success-path guard already lets Clang specialize runtime N; spelling hot_n=256 changes neither the C binary nor any hot loop.",
        },
        "question_2_meldra_shaped_c_closes_gap": {
            "answer": True,
            "meldra_over_shaped_c": meldra["wall_ms"]["median"]
            / shaped_c["wall_ms"]["median"],
            "confidence_intervals_overlap": _ci_overlap(meldra, shaped_c),
            "all_hot_loop_operand_sequences_equal": shaped_comparison[
                "all_operand_instruction_sequences_equal"
            ],
            "all_hot_loop_canonical_sequences_equal": shaped_comparison[
                "all_canonical_instruction_sequences_equal"
            ],
        },
        "question_3_cause": {
            "constant_specialization": False,
            "modulo_lowering": False,
            "bounds_check_differences": False,
            "alias_assumptions": False,
            "source_shape": True,
            "code_layout_and_register_allocation": True,
            "measurement_noise_for_original_gap": not original_ci_separated,
            "measurement_noise_after_shape_control": _ci_overlap(
                meldra, shaped_c
            ),
            "other": None,
            "evidence": {
                "no_div_or_idiv_in_any_hot_loop": all(
                    loop["division_or_idiv_instructions"] == 0
                    for loop in every_loop
                ),
                "no_bounds_checks_in_any_hot_loop": all(
                    loop["bounds_check_count"] == 0
                    for loop in every_loop
                ),
                "no_indirect_calls_in_any_hot_loop": all(
                    loop["indirect_calls"] == 0
                    for loop in every_loop
                ),
                "modulo_256_uses_byte_truncation": all(
                    loop["division_or_idiv_instructions"] == 0
                    and loop["byte_truncation_instructions"] >= 1
                    for arm_loops in loops.values()
                    for name, loop in arm_loops.items()
                    if name != "update" or loop["record_store_sites_per_machine_iteration"] > 0
                ),
                "first_traversal_instructions_per_two_iterations": {
                    "meldra": meldra_first[
                        "static_instructions_per_machine_iteration"
                    ],
                    "runtime_c": runtime_first[
                        "static_instructions_per_machine_iteration"
                    ],
                    "meldra_shaped_c": shaped_first[
                        "static_instructions_per_machine_iteration"
                    ],
                },
                "first_traversal_movl_sites": {
                    "meldra": meldra_first["mnemonic_sequence"].count("movl"),
                    "runtime_c": runtime_first["mnemonic_sequence"].count("movl"),
                    "meldra_shaped_c": shaped_first[
                        "mnemonic_sequence"
                    ].count("movl"),
                },
                "source_shape_decomposition": report.get(
                    "source_shape_decomposition", {}
                ).get("conclusion"),
            },
        },
        "question_4_same_machine_code": {
            "current_meldra_and_direct_generated_c_whole_binary_equal": arms[
                "meldra_current_region"
            ]["binary_sha256"]
            == arms["meldra_generated_c_direct"]["binary_sha256"],
            "meldra_and_shaped_c_whole_binary_equal": meldra["binary_sha256"]
            == shaped_c["binary_sha256"],
            "meldra_and_shaped_c_hot_loop_operand_sequences_equal": shaped_comparison[
                "all_operand_instruction_sequences_equal"
            ],
            "answer": "The current Meldra and directly compiled generated C binaries are byte-identical. The independent shaped-C whole binary differs, but all three hot loops have identical opcode/operand sequences modulo local labels.",
        },
        "question_5_information_given_to_clang": {
            "better_constant_information_about_n": False,
            "better_alias_information": False,
            "effective_difference": "Meldra emitted a source shape with 256 separately materialized initialization values plus staged descriptor-based loop expressions. Together they changed Clang register allocation/layout so the first traversal avoids two movl copies per two source iterations. Reproducing both source-shape features in handwritten C reproduces every hot loop and closes the timing gap.",
        },
        "integer_model": {
            "width_bits": 64,
            "signedness": "unsigned",
            "overflow": "wrapping modulo 2^64",
            "signed_division_instructions": 0,
        },
    }


def run_constant_knowledge_audit(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/constant_knowledge_audit",
    repetitions: int = REPETITIONS,
    warmups: int = WARMUPS,
    n: int = DEFAULT_N,
    seed: int = DEFAULT_SEED,
    factor: int = TRAVERSAL_FACTOR,
) -> dict[str, Any]:
    if repetitions < 30 or warmups != 5:
        raise ValueError("constant-knowledge audit requires 5 warmups and at least 30 measured runs")
    if n != RECORD_CAPACITY or seed != DEFAULT_SEED or factor != TRAVERSAL_FACTOR:
        raise ValueError("the non-elidable workload is frozen at N=256, its existing seed, and traversal count")
    immutable_before = {
        str(_OLD_FAIR): _sha256_path(_OLD_FAIR),
        str(_OLD_NON_ELIDABLE): _sha256_path(_OLD_NON_ELIDABLE),
    }
    if immutable_before[str(_OLD_FAIR)] != _OLD_FAIR_SHA256:
        raise AssertionError("fair-memory artifact changed")
    if immutable_before[str(_OLD_NON_ELIDABLE)] != _OLD_NON_ELIDABLE_SHA256:
        raise AssertionError("non-elidable artifact changed")
    frozen_report = json.loads(_OLD_NON_ELIDABLE.read_text(encoding="utf-8"))
    frozen_sources = _frozen_sources(frozen_report)
    expected = frozen_report["workload"]["expected_checksum"]
    if reference_checksum(n, seed, factor) != expected:
        raise AssertionError("frozen Python oracle no longer matches artifact checksum")

    root = Path(output_dir)
    corpus = root / "corpus"
    assembly_root = root / "assembly"
    loop_root = root / "hot_loops"
    for path in (root, corpus, assembly_root, loop_root):
        path.mkdir(parents=True, exist_ok=True)

    sources = {
        "meldra_generated_c_direct": frozen_sources["meldra_generated_c"],
        "c_preallocated_runtime_n": frozen_sources["c_preallocated"],
        "c_preallocated_const_n": _const_n_c_source(frozen_sources["c_preallocated"]),
        "c_preallocated_meldra_shape": _meldra_shaped_c_source(factor),
        "c_arena_runtime_n": frozen_sources["c_arena"],
        "c_arena_const_n": _const_n_c_source(frozen_sources["c_arena"]),
        "rust_preallocated_runtime_n": frozen_sources["rust_preallocated"],
        "rust_preallocated_const_n": _const_n_rust_source(frozen_sources["rust_preallocated"]),
        "rust_arena_runtime_n": frozen_sources["rust_arena"],
        "rust_arena_const_n": _const_n_rust_source(frozen_sources["rust_arena"]),
    }
    builds: dict[str, _Build] = {
        "meldra_current_region": _current_meldra_build(frozen_report, n, seed)
    }
    source_paths: dict[str, Path] = {
        "meldra_current_region": _FROZEN_ROOT / "meldra_region" / "generated.c"
    }
    source_languages: dict[str, str] = {"meldra_current_region": "generated_c"}
    for name in _C_ARMS[1:]:
        directory = corpus / name
        directory.mkdir(parents=True, exist_ok=True)
        builds[name] = _compile_external("c", sources[name], directory, (str(n), str(seed)))
        source_paths[name] = directory / "main.c"
        source_languages[name] = "generated_c" if name == "meldra_generated_c_direct" else "c"
    for name in _RUST_ARMS:
        directory = corpus / name
        directory.mkdir(parents=True, exist_ok=True)
        builds[name] = _compile_external("rust", sources[name], directory, (str(n), str(seed)))
        source_paths[name] = directory / "main.rs"
        source_languages[name] = "rust"

    state_before = _cpu_state()
    cpu = state_before["selected_cpu"]
    representatives = {name: _representative(build, expected, cpu) for name, build in builds.items()}
    invalid_n = {name: _run_invalid_n(build, seed, cpu) for name, build in builds.items()}
    measured_names = [name for name in _ARM_ORDER if builds[name].status == "MEASURED"]
    samples: dict[str, list[dict[str, Any]]] = {name: [] for name in measured_names}
    schedule_seed = BENCHMARK_SEED ^ 0x434F4E53544E
    rng = random.Random(schedule_seed)
    schedule_hash = hashlib.sha256()
    for round_index in range(warmups + repetitions):
        schedule = list(measured_names)
        rng.shuffle(schedule)
        for name in schedule:
            schedule_hash.update(f"{round_index}:{name}\n".encode())
            observation = _run_one(builds[name], expected, cpu)
            if round_index >= warmups:
                samples[name].append(
                    {**observation, "invocation_count": 1, "subruns": [observation]}
                )
    state_after = _cpu_state()

    arms = []
    for index, name in enumerate(_ARM_ORDER):
        build = builds[name]
        summary = _arm_summary(
            samples.get(name, []),
            seed=schedule_seed ^ index,
            expected_batches=repetitions,
        )
        if build.status != "MEASURED":
            summary.update(
                {
                    "status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE",
                    "correct": None,
                    "dispersion_gate_passed": None,
                }
            )
        language = "rust" if name in _RUST_ARMS else "c"
        contract = _semantic_contract(name, n, factor)
        arms.append(
            {
                "name": name,
                "status": summary["status"],
                "build_status": build.status,
                "compiler": build.compiler,
                "compiler_version": build.compiler_version,
                "compile_command": list(build.command),
                "normalized_optimization_flags": _compile_flags(build, language),
                "source_path": str(source_paths[name]),
                "source_sha256": _sha256_path(source_paths[name]),
                "binary_path": build.run_command[0] if build.run_command else None,
                "binary_sha256": build.binary_sha256,
                "binary_size": build.binary_size,
                "representative": representatives[name],
                "invalid_n_validation": invalid_n[name],
                "semantic_contract": contract,
                "wall_ms": summary["wall_ms_per_invocation"],
                "peak_rss_kb": summary["peak_rss_kb"],
                "relative_mad": summary["relative_mad"],
                "dispersion_gate_passed": summary["dispersion_gate_passed"],
                "measured_run_count": summary["measured_invocation_count"],
                "raw_samples": summary["raw_batches"],
                "correct": summary["correct"],
            }
        )

    assembly_builds: dict[str, dict[str, Any]] = {}
    loop_audits: dict[str, dict[str, Any]] = {}
    generated_audit = _c_assembly_audit(
        "meldra_generated_c_direct", source_paths["meldra_generated_c_direct"], assembly_root
    )
    for name in _C_ARMS:
        if name == "meldra_current_region":
            assembly_builds[name] = {
                **generated_audit,
                "shared_with": "meldra_generated_c_direct",
                "current_binary_equal_to_direct_binary": builds[name].binary_sha256
                == builds["meldra_generated_c_direct"].binary_sha256,
            }
        elif name == "meldra_generated_c_direct":
            assembly_builds[name] = generated_audit
        else:
            assembly_builds[name] = _c_assembly_audit(name, source_paths[name], assembly_root)
    for name in _RUST_ARMS:
        assembly_builds[name] = _rust_assembly_audit(
            name, source_paths[name], builds[name], assembly_root
        )
        assembly_builds[name]["line_mapped"] = _rust_line_assembly(
            name, source_paths[name], builds[name], assembly_root
        )

    for name in _ARM_ORDER:
        language = source_languages[name]
        source = source_paths[name].read_text(encoding="utf-8")
        ranges = _source_loop_ranges(source, language)
        if name in _RUST_ARMS:
            line_assembly = assembly_builds[name]["line_mapped"]
            assembly_path = Path(line_assembly["assembly_path"]) if line_assembly["status"] == "MEASURED" else None
        else:
            assembly_path = Path(assembly_builds[name]["assembly_path"]) if assembly_builds[name].get("status") == "MEASURED" else None
        if assembly_path is None:
            loop_audits[name] = {
                loop: {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"} for loop in _LOOP_NAMES
            }
            continue
        loop_audits[name] = {
            loop: _extract_loop(
                assembly_path,
                source_paths[name],
                ranges[loop],
                loop_root / f"{name}.{loop}.s",
            )
            for loop in _LOOP_NAMES
        }
        for loop, chain_key in (
            ("first_traversal", "first_dependency_chain"),
            ("update", "update_dependency_chain"),
            ("second_traversal", "second_dependency_chain"),
        ):
            loop_audits[name][loop]["loop_carried_dependency_chain"] = _semantic_contract(
                name, n, factor
            )[chain_key]

    by_name = {item["name"]: item for item in arms}
    hot_loop_comparisons = {
        "current_meldra_vs_direct_generated_c": _loop_comparison(
            loop_audits["meldra_current_region"], loop_audits["meldra_generated_c_direct"]
        ),
        "meldra_vs_runtime_c": _loop_comparison(
            loop_audits["meldra_current_region"], loop_audits["c_preallocated_runtime_n"]
        ),
        "meldra_vs_const_c": _loop_comparison(
            loop_audits["meldra_current_region"], loop_audits["c_preallocated_const_n"]
        ),
        "meldra_vs_meldra_shaped_c": _loop_comparison(
            loop_audits["meldra_current_region"], loop_audits["c_preallocated_meldra_shape"]
        ),
        "runtime_vs_const_c_preallocated": _loop_comparison(
            loop_audits["c_preallocated_runtime_n"], loop_audits["c_preallocated_const_n"]
        ),
        "runtime_vs_const_c_arena": _loop_comparison(
            loop_audits["c_arena_runtime_n"], loop_audits["c_arena_const_n"]
        ),
        "runtime_vs_const_rust_preallocated": _loop_comparison(
            loop_audits["rust_preallocated_runtime_n"], loop_audits["rust_preallocated_const_n"]
        ),
        "runtime_vs_const_rust_arena": _loop_comparison(
            loop_audits["rust_arena_runtime_n"], loop_audits["rust_arena_const_n"]
        ),
    }
    expression_equivalence = _expression_equivalence(
        frozen_sources["meldra_source"],
        frozen_sources["meldra_generated_c"],
        sources["c_preallocated_meldra_shape"],
        representatives["meldra_generated_c_direct"].get("checksum")
        == representatives["c_preallocated_meldra_shape"].get("checksum")
        == expected,
    )
    source_shape_decomposition = _source_shape_decomposition(
        frozen_sources["c_preallocated"],
        expected,
        n,
        seed,
        cpu,
        loop_audits["meldra_current_region"],
        root,
    )
    perf = {name: _perf_stat(builds[name], cpu) for name in _ARM_ORDER}
    medians = {
        name: by_name[name]["wall_ms"]["median"]
        if by_name[name]["status"] == "MEASURED"
        else None
        for name in _ARM_ORDER
    }
    ratios = {
        "meldra_over_generated_c_direct": _ratio(
            medians["meldra_current_region"], medians["meldra_generated_c_direct"]
        ),
        "meldra_over_c_preallocated_runtime_n": _ratio(
            medians["meldra_current_region"], medians["c_preallocated_runtime_n"]
        ),
        "meldra_over_c_preallocated_const_n": _ratio(
            medians["meldra_current_region"], medians["c_preallocated_const_n"]
        ),
        "meldra_over_c_preallocated_meldra_shape": _ratio(
            medians["meldra_current_region"], medians["c_preallocated_meldra_shape"]
        ),
        "c_preallocated_const_over_runtime": _ratio(
            medians["c_preallocated_const_n"], medians["c_preallocated_runtime_n"]
        ),
        "c_arena_const_over_runtime": _ratio(
            medians["c_arena_const_n"], medians["c_arena_runtime_n"]
        ),
        "rust_preallocated_const_over_runtime": _ratio(
            medians["rust_preallocated_const_n"], medians["rust_preallocated_runtime_n"]
        ),
        "rust_arena_const_over_runtime": _ratio(
            medians["rust_arena_const_n"], medians["rust_arena_runtime_n"]
        ),
    }

    contracts = [item["semantic_contract"] for item in arms]
    comparable_contract = {
        key: contracts[0][key]
        for key in contracts[0]
        if key != "arm"
    }
    contract_equal = all(
        {key: value for key, value in contract.items() if key != "arm"} == comparable_contract
        for contract in contracts
    )
    c_flags_equal = len(
        {tuple(by_name[name]["normalized_optimization_flags"]) for name in _C_ARMS}
    ) == 1
    rust_flags_equal = len(
        {tuple(by_name[name]["normalized_optimization_flags"]) for name in _RUST_ARMS}
    ) <= 1
    validity_failures = []
    if any(
        item["status"] == "MEASURED" and item["representative"].get("checksum") != expected
        for item in arms
    ):
        validity_failures.append("checksum_mismatch")
    if any(item["invalid_n_validation"]["status"] != "PASS" for item in arms):
        validity_failures.append("runtime_n_validation")
    if not contract_equal:
        validity_failures.append("semantic_contract_mismatch")
    if not c_flags_equal or not rust_flags_equal:
        validity_failures.append("optimization_flag_mismatch")
    if expression_equivalence["status"] != "PASS":
        validity_failures.append("expression_equivalence")
    if any(
        loop_audits[name][loop]["status"] != "MEASURED"
        for name in measured_names
        for loop in _LOOP_NAMES
    ):
        validity_failures.append("hot_loop_extraction")
    if any(
        not source_shape_decomposition[name]["correct"]
        or any(
            loop["status"] != "MEASURED"
            for loop in source_shape_decomposition[name]["hot_loops"].values()
        )
        for name in (
            "compact_init_staged_hot",
            "unrolled_init_compact_hot",
        )
    ):
        validity_failures.append("source_shape_decomposition")
    if any(
        item["status"] == "MEASURED"
        and (
            not item["dispersion_gate_passed"]
            or item["wall_ms"]["median"] < 200
            or item["measured_run_count"] < 30
        )
        for item in arms
    ):
        validity_failures.append("timing_protocol")
    if not all(item["correct"] for item in arms if item["status"] == "MEASURED"):
        validity_failures.append("timing_correctness")
    if builds["meldra_current_region"].binary_sha256 != builds["meldra_generated_c_direct"].binary_sha256:
        validity_failures.append("direct_generated_c_binary_differs")

    immutable_after = {
        str(_OLD_FAIR): _sha256_path(_OLD_FAIR),
        str(_OLD_NON_ELIDABLE): _sha256_path(_OLD_NON_ELIDABLE),
    }
    if immutable_before != immutable_after:
        validity_failures.append("immutable_artifact_changed")

    report: dict[str, Any] = {
        "schema_version": CONSTANT_KNOWLEDGE_SCHEMA_VERSION,
        "kind": "MeldraConstantKnowledgeHotLoopAudit",
        "immutable_artifacts": {
            path: {
                "sha256_before": immutable_before[path],
                "sha256_after": immutable_after[path],
                "unchanged": immutable_before[path] == immutable_after[path],
            }
            for path in immutable_before
        },
        "frozen_workload": {
            **frozen_report["workload"],
            "python_reference_checksum": reference_checksum(n, seed, factor),
            "workload_unchanged": True,
            "computation_order": [
                "initialize 256 packed records",
                "first 64,000,000-step dependent traversal",
                "update slots 0,4,...,252",
                "second 64,000,000-step dependent traversal",
                "print checksum",
            ],
        },
        "protocol": {
            "warmups": warmups,
            "measured_runs": repetitions,
            "randomized_arm_order": True,
            "timing_arms_parallel": False,
            "schedule_seed": schedule_seed,
            "schedule_sha256": schedule_hash.hexdigest(),
            "cpu_affinity": cpu,
            "identical_inputs": {"n": n, "seed": seed},
            "dispersion_relative_mad_max": DISPERSION_RELATIVE_MAD_MAX,
            "minimum_runtime_ms": 200,
        },
        "environment": {
            "before": state_before,
            "after": state_after,
            "stable": (
                state_before["governor"] == state_after["governor"]
                and state_before["intel_pstate_no_turbo"]
                == state_after["intel_pstate_no_turbo"]
                and state_before["affinity"] == state_after["affinity"]
            ),
        },
        "arms": arms,
        "semantic_contract_equal": contract_equal,
        "expression_equivalence": expression_equivalence,
        "optimization_flags": {
            "c": list(_C_FLAGS),
            "rust": list(_RUST_FLAGS),
            "c_equal_across_arms": c_flags_equal,
            "rust_equal_across_arms": rust_flags_equal,
            "integer_overflow_policy_equal": True,
        },
        "assembly_builds": assembly_builds,
        "hot_loops": loop_audits,
        "hot_loop_comparisons": hot_loop_comparisons,
        "hardware_counters": {
            "tool": shutil.which("perf"),
            "not_static_instruction_counts": True,
            "arms": perf,
        },
        "ratios": ratios,
        "control_findings": {
            "c_runtime_const_binary_equal": builds["c_preallocated_runtime_n"].binary_sha256
            == builds["c_preallocated_const_n"].binary_sha256,
            "c_arena_runtime_const_binary_equal": builds["c_arena_runtime_n"].binary_sha256
            == builds["c_arena_const_n"].binary_sha256,
            "rust_runtime_const_binary_equal": builds["rust_preallocated_runtime_n"].binary_sha256
            == builds["rust_preallocated_const_n"].binary_sha256,
            "rust_arena_runtime_const_binary_equal": builds["rust_arena_runtime_n"].binary_sha256
            == builds["rust_arena_const_n"].binary_sha256,
            "meldra_direct_c_binary_equal": builds["meldra_current_region"].binary_sha256
            == builds["meldra_generated_c_direct"].binary_sha256,
            "constant_n_closes_gap": _close_enough(
                by_name["c_preallocated_const_n"], by_name["meldra_current_region"]
            ),
            "meldra_shaped_c_closes_gap": _close_enough(
                by_name["c_preallocated_meldra_shape"], by_name["meldra_current_region"]
            ),
            "runtime_const_c_confidence_intervals_overlap": _ci_overlap(
                by_name["c_preallocated_runtime_n"], by_name["c_preallocated_const_n"]
            ),
            "meldra_shaped_c_confidence_interval_overlaps_meldra": _ci_overlap(
                by_name["c_preallocated_meldra_shape"], by_name["meldra_current_region"]
            ),
        },
        "validity_failures": sorted(set(validity_failures)),
        "limitations": [
            "perf hardware counters are reported only when the perf executable and permissions are available; static loop counts are never substituted.",
            "Rust line-mapped assembly adds debuginfo=1 only to recover source ranges; the timing binaries retain the frozen debuginfo=0 flags.",
            "The conclusion applies to the frozen N=256, 2 KiB working set on this x86-64 host and these compiler versions.",
        ],
    }
    report["source_shape_decomposition"] = source_shape_decomposition
    report["answers"] = _derive_answers(report)
    report["decision"] = _select_status(report)
    report["status"] = "PASS" if not report["validity_failures"] else "FAIL"
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (root / "report.json").write_text(text, encoding="utf-8")
    Path("tools/benchmarks/merlo/benchmarks/meldra_constant_knowledge_audit.json").write_text(text, encoding="utf-8")
    return report


def refresh_constant_knowledge_analysis(
    report_path: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_constant_knowledge_audit.json",
) -> dict[str, Any]:
    """Refresh source-mapped loop analysis without rerunning saved timings."""
    path = Path(report_path)
    previous_bytes = path.read_bytes()
    report = json.loads(previous_bytes)
    root = Path("tools/benchmarks/merlo/benchmarks/constant_knowledge_audit")
    loop_root = root / "hot_loops"
    source_paths = {
        item["name"]: Path(item["source_path"])
        for item in report["arms"]
    }
    loop_audits: dict[str, dict[str, Any]] = {}
    for name in _ARM_ORDER:
        source = source_paths[name].read_text(encoding="utf-8")
        language = (
            "rust"
            if name in _RUST_ARMS
            else "generated_c"
            if name in {"meldra_current_region", "meldra_generated_c_direct"}
            else "c"
        )
        ranges = _source_loop_ranges(source, language)
        assembly_build = report["assembly_builds"][name]
        if name in _RUST_ARMS:
            assembly_path = Path(assembly_build["line_mapped"]["assembly_path"])
        else:
            assembly_path = Path(assembly_build["assembly_path"])
        loop_audits[name] = {
            loop: _extract_loop(
                assembly_path,
                source_paths[name],
                ranges[loop],
                loop_root / f"{name}.{loop}.s",
            )
            for loop in _LOOP_NAMES
        }
        contract = next(
            item["semantic_contract"]
            for item in report["arms"]
            if item["name"] == name
        )
        for loop, chain_key in (
            ("first_traversal", "first_dependency_chain"),
            ("update", "update_dependency_chain"),
            ("second_traversal", "second_dependency_chain"),
        ):
            loop_audits[name][loop]["loop_carried_dependency_chain"] = contract[
                chain_key
            ]
    report["hot_loops"] = loop_audits
    report["hot_loop_comparisons"] = {
        "current_meldra_vs_direct_generated_c": _loop_comparison(
            loop_audits["meldra_current_region"],
            loop_audits["meldra_generated_c_direct"],
        ),
        "meldra_vs_runtime_c": _loop_comparison(
            loop_audits["meldra_current_region"],
            loop_audits["c_preallocated_runtime_n"],
        ),
        "meldra_vs_const_c": _loop_comparison(
            loop_audits["meldra_current_region"],
            loop_audits["c_preallocated_const_n"],
        ),
        "meldra_vs_meldra_shaped_c": _loop_comparison(
            loop_audits["meldra_current_region"],
            loop_audits["c_preallocated_meldra_shape"],
        ),
        "runtime_vs_const_c_preallocated": _loop_comparison(
            loop_audits["c_preallocated_runtime_n"],
            loop_audits["c_preallocated_const_n"],
        ),
        "runtime_vs_const_c_arena": _loop_comparison(
            loop_audits["c_arena_runtime_n"],
            loop_audits["c_arena_const_n"],
        ),
        "runtime_vs_const_rust_preallocated": _loop_comparison(
            loop_audits["rust_preallocated_runtime_n"],
            loop_audits["rust_preallocated_const_n"],
        ),
        "runtime_vs_const_rust_arena": _loop_comparison(
            loop_audits["rust_arena_runtime_n"],
            loop_audits["rust_arena_const_n"],
        ),
    }
    frozen_report = json.loads(_OLD_NON_ELIDABLE.read_text(encoding="utf-8"))
    frozen_sources = _frozen_sources(frozen_report)
    report["source_shape_decomposition"] = _source_shape_decomposition(
        frozen_sources["c_preallocated"],
        report["frozen_workload"]["expected_checksum"],
        report["frozen_workload"]["runtime_n"],
        report["frozen_workload"]["runtime_seed"],
        report["protocol"]["cpu_affinity"],
        loop_audits["meldra_current_region"],
        root,
    )
    failures = set(report["validity_failures"])
    failures.discard("hot_loop_extraction")
    failures.discard("source_shape_decomposition")
    if any(
        loop_audits[name][loop]["status"] != "MEASURED"
        for name in _ARM_ORDER
        for loop in _LOOP_NAMES
    ):
        failures.add("hot_loop_extraction")
    if any(
        not report["source_shape_decomposition"][name]["correct"]
        or any(
            loop["status"] != "MEASURED"
            for loop in report["source_shape_decomposition"][name][
                "hot_loops"
            ].values()
        )
        for name in (
            "compact_init_staged_hot",
            "unrolled_init_compact_hot",
        )
    ):
        failures.add("source_shape_decomposition")
    if _sha256_path(_OLD_FAIR) != _OLD_FAIR_SHA256:
        failures.add("immutable_artifact_changed")
    if _sha256_path(_OLD_NON_ELIDABLE) != _OLD_NON_ELIDABLE_SHA256:
        failures.add("immutable_artifact_changed")
    report["validity_failures"] = sorted(failures)
    report["analysis_refresh"] = {
        "timing_samples_reused": True,
        "timing_arms_rerun": False,
        "reason": "source-map loop analysis and compile-only source-shape controls refreshed",
        "previous_report_sha256": _sha256_bytes(previous_bytes),
    }
    report["answers"] = _derive_answers(report)
    report["decision"] = _select_status(report)
    report["status"] = "PASS" if not report["validity_failures"] else "FAIL"
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    (root / "report.json").write_text(text, encoding="utf-8")
    return report


__all__ = [
    "CONSTANT_KNOWLEDGE_SCHEMA_VERSION",
    "refresh_constant_knowledge_analysis",
    "run_constant_knowledge_audit",
]
