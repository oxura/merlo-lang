"""Deterministic integrated corpus for the General Representation Core."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JsonCase:
    case_id: str
    family: str
    payload: bytes
    valid: bool
    partition: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "family": self.family,
            "payload_sha256_input_size": len(self.payload),
            "valid": self.valid,
            "partition": self.partition,
        }


def _encode(value: Any, *, ensure_ascii: bool = False) -> bytes:
    return json.dumps(value, ensure_ascii=ensure_ascii, separators=(",", ":")).encode("utf-8")


def valid_json_cases() -> list[JsonCase]:
    rng = random.Random(0x4D45524C4F)
    generators = {
        "null": lambda i: (b" \t\nnull\r" if i % 2 else b"null"),
        "boolean": lambda i: b"true" if i % 2 else b"false",
        "integer": lambda i: str(i * 7919).encode(),
        "negative_integer": lambda i: str(-(i * 104729 + 1)).encode(),
        "decimal": lambda i: f"{i + 1}.{(i * 17) % 1000:03d}".encode(),
        "exponent": lambda i: f"-{i + 1}.{i % 10}e+{i % 9}".encode(),
        "ascii_string": lambda i: _encode(f"merlo-{i}-{'x' * (i % 11)}"),
        "unicode_string": lambda i: _encode(f"Привет-{i}-λ-😀"),
        "escaped_string": lambda i: f'"line\\n{i}\\t\\u0041\\uD83D\\uDE00"'.encode(),
        "empty_containers": lambda i: b"[]" if i % 2 else b"{}",
        "flat_array": lambda i: _encode([i, True, None, f"s{i}", -i]),
        "flat_object": lambda i: _encode({"z": i, "a": True, "text": f"v{i}"}),
        "nested_array": lambda i: _encode([[[i, i + 1]], [None, [False]]]),
        "nested_object": lambda i: _encode({"a": {"b": {"c": i}}, "d": {"e": None}}),
        "mixed_nesting": lambda i: _encode({"items": [{"id": i}, [True, {"x": f"y{i}"}]], "ok": True}),
        "duplicate_keys": lambda i: f'{{"a":{i},"a":{i + 1},"b":{{"a":{i + 2},"a":null}}}}'.encode(),
        "large_string": lambda i: _encode("x" * (1024 + i * 7) + "😀"),
        "large_array": lambda i: _encode([(j * (i + 1)) % 997 for j in range(128 + i)]),
        "large_object": lambda i: _encode({f"k{j}": (j + i) % 101 for j in range(96 + i)}),
        "depth_spectrum": lambda i: ((b"[" * (1 if i < 10 else 64 if i < 20 else 128)) + b"null" + (b"]" * (1 if i < 10 else 64 if i < 20 else 128))),
    }
    cases: list[JsonCase] = []
    for family, generator in generators.items():
        for index in range(30):
            payload = generator(index)
            partition = "generated_internal" if index < 20 else "held_out_internal"
            cases.append(JsonCase(f"valid-{family}-{index:03d}", family, payload, True, partition))
    rng.shuffle(cases)
    assert len(cases) == 600
    return cases


def invalid_json_cases() -> list[JsonCase]:
    def depth(_: int) -> bytes:
        return b"[" * 129 + b"null" + b"]" * 129

    generators = {
        "empty_input": lambda i: b"" if i % 2 else b" \t\n",
        "truncated_literal": lambda i: (b"n" if i % 3 == 0 else b"tru" if i % 3 == 1 else b"fals"),
        "invalid_literal": lambda i: f"nulx{i}".encode(),
        "truncated_string": lambda i: f'"unterminated-{i}'.encode(),
        "invalid_escape": lambda i: f'"bad\\q{i}"'.encode(),
        "invalid_unicode_escape": lambda i: f'"bad\\u12G{i % 10}"'.encode(),
        "unpaired_surrogate": lambda i: (b'"\\uD800"' if i % 2 else b'"\\uDC00"'),
        "control_in_string": lambda i: b'"a' + bytes([i % 32]) + b'b"',
        "missing_colon": lambda i: f'{{"a" {i}}}'.encode(),
        "missing_comma": lambda i: f'[{i} {i + 1}]'.encode(),
        "extra_array_comma": lambda i: f'[{i},]'.encode(),
        "extra_object_comma": lambda i: f'{{"a":{i},}}'.encode(),
        "trailing_token": lambda i: f'{{"a":{i}}} true'.encode(),
        "leading_zero": lambda i: f"0{i + 1}".encode(),
        "missing_fraction_digits": lambda i: f"{i + 1}.".encode(),
        "missing_exponent_digits": lambda i: f"{i + 1}e+".encode(),
        "mismatched_delimiter": lambda i: (b"[}" if i % 2 else b"{]"),
        "unexpected_closing": lambda i: (b"]" if i % 2 else b"}"),
        "depth_129": depth,
        "invalid_utf8": lambda i: b'"' + bytes([0x80 + (i % 64)]) + b'"',
    }
    cases: list[JsonCase] = []
    for family, generator in generators.items():
        for index in range(20):
            cases.append(JsonCase(f"invalid-{family}-{index:03d}", family, generator(index), False, "generated_internal" if index < 15 else "held_out_internal"))
    random.Random(0xBAD5EED).shuffle(cases)
    assert len(cases) == 400
    return cases


def layout_sources() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    valid = []
    invalid = []
    for index in range(100):
        valid.extend(
            [
                (f"scalar_record_{index}", f"record Value{index}:\n    item: UInt64\nfn main() -> Unit:\n    return\n"),
                (f"boxed_recursive_{index}", f"record Node{index}:\n    next: Box[Node{index}]\nfn main() -> Unit:\n    return\n"),
                (f"vec_recursive_{index}", f"enum Tree{index}:\n    Leaf: UInt64\n    Branch: Vec[Tree{index}]\nfn main() -> Unit:\n    return\n"),
                (f"indirect_pair_{index}", f"record A{index}:\n    b: Vec[B{index}]\nrecord B{index}:\n    a: A{index}\nfn main() -> Unit:\n    return\n"),
                (f"acyclic_chain_{index}", f"record Leaf{index}:\n    value: UInt64\nrecord Root{index}:\n    leaf: Leaf{index}\nfn main() -> Unit:\n    return\n"),
            ]
        )
    for index in range(100):
        invalid.extend(
            [
                (f"self_cycle_{index}", f"record Bad{index}:\n    next: Bad{index}\nfn main() -> Unit:\n    return\n"),
                (f"pair_cycle_{index}", f"record Left{index}:\n    right: Right{index}\nrecord Right{index}:\n    left: Left{index}\nfn main() -> Unit:\n    return\n"),
                (f"enum_cycle_{index}", f"enum Loop{index}:\n    Again: Loop{index}\nfn main() -> Unit:\n    return\n"),
            ]
        )
    assert len(valid) == 500 and len(invalid) == 300
    return valid, invalid


__all__ = ["JsonCase", "invalid_json_cases", "layout_sources", "valid_json_cases"]
