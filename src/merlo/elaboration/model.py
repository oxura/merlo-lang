from __future__ import annotations

from dataclasses import dataclass, field

from merlo.canonical_ast import (
    CanonicalCallable,
    CanonicalClosure,
    CanonicalOptionFallback,
    CanonicalProgram,
)
from merlo.surface_ast import SurfaceBinding, SurfaceFunction


@dataclass(frozen=True)
class InferenceDecision:
    owner: str
    name: str
    kind: str
    type_name: str
    mutable: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class SurfaceElaboration:
    canonical: CanonicalProgram
    decisions: tuple[InferenceDecision, ...]


@dataclass
class FunctionState:
    source: SurfaceFunction
    parameters: dict[str, str]
    return_term: str
    locals: dict[str, str]
    assignments: dict[str, int]
    first_bindings: dict[str, SurfaceBinding]
    evidence: dict[str, set[str]]
    calls: set[str] = field(default_factory=set)
    collection_callbacks: set[str] = field(default_factory=set)
    error_calls: set[str] = field(default_factory=set)
    effects: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    errors: set[str] = field(default_factory=set)
    implicit_callables: dict[str, CanonicalCallable] = field(default_factory=dict)
    option_fallbacks: dict[str, CanonicalOptionFallback] = field(default_factory=dict)
    closures: dict[str, CanonicalClosure] = field(default_factory=dict)
    read_counts: dict[str, int] = field(default_factory=dict)


__all__ = ["FunctionState", "InferenceDecision", "SurfaceElaboration"]
