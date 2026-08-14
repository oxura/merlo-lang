from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from merlo.concise_application import CONCISE_SURFACE_VERSION
from tools.benchmarks.merlo.concise_precedence import PRECEDENCE_TABLE
from merlo.runtime_contract import ALPHA_EFFECTS


CONCISE_SURFACE_FREEZE_SCHEMA_VERSION = 2
CONCISE_SURFACE_FREEZE_FILENAME = "merlo_concise_surface_v0_2.json"
CONCISE_SURFACE_FREEZE_STATUS = "CONCISE_SURFACE_V0_2_FROZEN"


@dataclass(frozen=True)
class ConciseSurfaceFreezeMismatch:
    subject: str
    expected: str
    observed: str

    def to_dict(self) -> dict[str, str]:
        return {
            "subject": self.subject,
            "expected": self.expected,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class ConciseSurfaceFreezeVerification:
    freeze_id: str
    mismatches: tuple[ConciseSurfaceFreezeMismatch, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "freeze_id": self.freeze_id,
            "ok": self.ok,
            "mismatches": [item.to_dict() for item in self.mismatches],
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _freeze_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": CONCISE_SURFACE_FREEZE_SCHEMA_VERSION,
        "status": CONCISE_SURFACE_FREEZE_STATUS,
        "surface_version": CONCISE_SURFACE_VERSION,
        "grammar": {
            "indentation": "four spaces define blocks",
            "forms": [
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
            ],
            "declaration_order": (
                "module first; use declarations before all other declarations"
            ),
            "function_boundary": (
                "name(parameters) = expression or name(parameters): block"
            ),
            "public_boundary": (
                "exported declarations materialize complete inferred contracts"
            ),
            "unsupported": [
                "async",
                "flow",
                "interface",
                "machine",
                "macro",
                "ui",
                "web",
            ],
        },
        "operator_precedence": {
            "direction": "lowest to highest",
            "table": [list(item) for item in PRECEDENCE_TABLE],
        },
        "type_inference": {
            "literals": {
                "boolean": "Bool",
                "nonnegative_integer": "UInt64",
                "negative_integer": "Int64",
                "floating_point": "Float64",
                "string": "Text",
            },
            "aliases": {
                "Int": "Int64",
                "UInt": "UInt64",
                "Float": "Float64",
            },
            "constraints": [
                "annotations",
                "assignment values",
                "call arguments",
                "call return context",
                "record fields",
                "enum payloads",
                "operators",
                "match patterns",
                "range indices",
            ],
            "ambiguity": "reject and request one constraining boundary annotation",
            "conflicts": "reject instead of coercing",
            "public_signatures": (
                "inference may use internal call sites, but every inferred public "
                "parameter and return type is materialized in canonical source"
            ),
            "recursive_groups": "require at least one annotated parameter or return boundary",
            "dynamic_any": "forbidden",
        },
        "bindings": {
            "let": "exactly one whole-function assignment",
            "var": "two or more whole-function assignments",
            "source_rule": (
                "the first plain assignment declares a local; the compiler "
                "materializes let or var"
            ),
            "shadowing": "duplicate bindings in one scope are rejected",
            "mutation": "mutation of an unresolved binding is rejected",
        },
        "public_signature_policy": {
            "records_enums_and_functions": "only export declarations are public",
            "tasks": "export task is public",
            "annotations": "canonical public parameters and returns are explicit",
            "revision": "hash module, name, kind, parameters, return type, effects, and capabilities",
            "lock": ".merlo-interface.json must exactly match all public interfaces",
            "drift": "PublicInterfaceRevisionMismatch",
        },
        "task_effects": {
            "allowed": sorted(ALPHA_EFFECTS),
            "declaration": (
                "direct host calls introduce effects and capabilities"
            ),
            "closure": (
                "private call-graph effects converge to a fixed point"
            ),
            "capabilities": "closed inferred authority set",
            "pure_functions": "an empty inferred effect row becomes fn",
            "cli_main": "exactly one task main with one Path parameter",
            "read_visibility": (
                "public locks materialize every inferred authority change"
            ),
        },
        "ownership": {
            "owners": ["Bytes", "Text", "TextBuilder", "Vec[T]"],
            "borrows": ["BytesView", "TextView", "Borrow[Vec[T]]"],
            "moves": "owned values move into owning constructors",
            "drops": "owned locals are dropped on every exit",
            "fs_read": "returns owned Bytes",
            "ordinary_lifetime_annotations": 0,
            "manual_memory_operations": 0,
        },
        "canonical_expansion": {
            "types": (
                "all inferred local, parameter, return, and public types are explicit"
            ),
            "bindings": "all assignments are emitted as let or var",
            "tasks": (
                "kind, effects, capabilities, errors, and ownership are explicit"
            ),
            "sum_types": (
                "Option and Result lower deterministically to closed tagged sums"
            ),
            "production_lowering": (
                "Canonical Typed AST lowers directly without canonical text reparsing"
            ),
            "semantic_ast_equal": True,
        },
        "diagnostics": {
            "format": "path:line: diagnostic detail",
            "source_projection": "generated canonical lines map to concise module paths and source lines",
            "generated_only_diagnostics": "forbidden",
            "ambiguity_names": [
                "AmbiguousType",
                "PublicBoundaryAnnotationRequired",
                "RecursiveBoundaryAnnotationRequired",
            ],
        },
        "syntax_version_change": {
            "explicit_reason_required": True,
            "reason_field": "syntax_version_change_reason",
            "process": "change the version constant and freeze manifest in the same reviewed change",
        },
    }
    payload["contract_sha256"] = _sha256(_canonical(payload))
    payload["freeze_id"] = "concise_surface_" + _sha256(_canonical(payload))
    return payload


def build_concise_surface_freeze() -> dict[str, Any]:
    return _freeze_payload()


def write_concise_surface_freeze(root: str | Path = ".") -> Path:
    path = Path(root).resolve() / "research" / "archive" / "alpha1" / "benchmarks" / CONCISE_SURFACE_FREEZE_FILENAME
    path.write_text(
        json.dumps(build_concise_surface_freeze(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def verify_concise_surface_freeze(root: str | Path = ".") -> ConciseSurfaceFreezeVerification:
    expected = build_concise_surface_freeze()
    path = Path(root).resolve() / "research" / "archive" / "alpha1" / "benchmarks" / CONCISE_SURFACE_FREEZE_FILENAME
    mismatches: list[ConciseSurfaceFreezeMismatch] = []
    if not path.is_file():
        mismatches.append(ConciseSurfaceFreezeMismatch("manifest", "present", "MISSING"))
    else:
        try:
            observed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            mismatches.append(ConciseSurfaceFreezeMismatch("manifest", "valid JSON", str(exc)))
        else:
            if observed != expected:
                mismatches.append(
                    ConciseSurfaceFreezeMismatch(
                        "manifest",
                        _sha256(_canonical(expected)),
                        _sha256(_canonical(observed)),
                    )
                )
    return ConciseSurfaceFreezeVerification(expected["freeze_id"], tuple(mismatches))


__all__ = [
    "CONCISE_SURFACE_FREEZE_FILENAME",
    "CONCISE_SURFACE_VERSION",
    "ConciseSurfaceFreezeMismatch",
    "ConciseSurfaceFreezeVerification",
    "build_concise_surface_freeze",
    "verify_concise_surface_freeze",
    "write_concise_surface_freeze",
]
