"""Deterministic interprocedural borrow summaries.

The ownership checker needs one small piece of information that is not
present in a function signature alone: a returned view may be backed by an
owning parameter.  This module records that relationship in a versioned,
serializable contract and computes it to a finite fixed point over deterministic
strongly connected components of the local call graph. Semantic relations are
separate from bounded diagnostic witnesses. It intentionally describes
provenance only; it does not change the place/lifetime model or lower calls.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from merlo import native_syntax as ast
from merlo.type_parser import generic_parts
from merlo.type_properties import TypePropertyResolver


BORROW_SUMMARY_SCHEMA_VERSION = 3
BORROW_SUMMARY_CONTRACT = "merlo.borrow-summary.v3"
BORROW_SUMMARY_STATUSES = frozenset({"known", "opaque"})
BORROW_SUMMARY_KINDS = frozenset({"direct", "contained"})
BORROW_SUMMARY_CYCLE_MARKER = "<cycle>"
BORROW_PLACE_STEP_KINDS = frozenset(
    {"Parameter", "Field", "Element", "VariantPayload", "Deref", "RecursiveTail"}
)
OWNERSHIP_VOCABULARY = frozenset(
    {"value", "borrow", "borrow_mut", "owned", "contained_borrow", "owned_contained_borrow"}
)


@dataclass(frozen=True, order=True)
class BorrowPlaceStep:
    """One finite structural step in a borrow provenance path."""

    kind: str
    value: str | int | None = None

    def __post_init__(self) -> None:
        if self.kind not in BORROW_PLACE_STEP_KINDS:
            raise ValueError(f"invalid borrow place step: {self.kind}")
        if self.kind == "Parameter":
            if type(self.value) is not int or self.value < 0:
                raise ValueError("borrow parameter step requires a non-negative index")
        elif self.kind in {"Field", "VariantPayload", "RecursiveTail"}:
            if not isinstance(self.value, str) or not self.value:
                raise ValueError(f"borrow {self.kind} step requires text")
        elif self.value is not None:
            raise ValueError(f"borrow {self.kind} step cannot carry a value")

    @classmethod
    def parameter(cls, index: int) -> "BorrowPlaceStep":
        return cls("Parameter", index)

    @classmethod
    def field(cls, name: str) -> "BorrowPlaceStep":
        return cls("Field", name)

    @classmethod
    def element(cls) -> "BorrowPlaceStep":
        return cls("Element")

    @classmethod
    def variant_payload(cls, variant: str) -> "BorrowPlaceStep":
        return cls("VariantPayload", variant)

    @classmethod
    def dereference(cls) -> "BorrowPlaceStep":
        return cls("Deref")

    @classmethod
    def recursive_tail(cls, scc_id: str) -> "BorrowPlaceStep":
        return cls("RecursiveTail", scc_id)

    def render(self) -> str:
        if self.kind == "Parameter":
            return f"parameter[{self.value}]"
        if self.kind == "Field":
            return str(self.value)
        if self.kind == "Element":
            return "elements"
        if self.kind == "VariantPayload":
            return f"payload[{self.value}]"
        if self.kind == "Deref":
            return "dereference"
        return f"RecursiveTail({self.value})"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BorrowPlaceStep":
        if not isinstance(value, Mapping) or set(value) != {"kind", "value"}:
            raise ValueError("invalid borrow place step")
        return cls(value["kind"], value["value"])


@dataclass(frozen=True, order=True)
class BorrowPlacePath:
    """Canonical finite path over structural program places."""

    steps: tuple[BorrowPlaceStep, ...] = ()

    def __post_init__(self) -> None:
        steps = tuple(self.steps)
        if any(not isinstance(item, BorrowPlaceStep) for item in steps):
            raise ValueError("borrow place path requires structural steps")
        recursive = [index for index, item in enumerate(steps) if item.kind == "RecursiveTail"]
        if recursive and recursive != [len(steps) - 1]:
            raise ValueError("RecursiveTail must be the terminal borrow place step")
        object.__setattr__(self, "steps", steps)

    @classmethod
    def parameter(cls, index: int) -> "BorrowPlacePath":
        return cls((BorrowPlaceStep.parameter(index),))

    def append(self, *steps: BorrowPlaceStep) -> "BorrowPlacePath":
        if self.steps and self.steps[-1].kind == "RecursiveTail":
            return self
        return BorrowPlacePath((*self.steps, *steps))

    def with_recursive_tail(self, scc_id: str) -> "BorrowPlacePath":
        if self.steps and self.steps[-1].kind == "RecursiveTail":
            return self
        return self.append(BorrowPlaceStep.recursive_tail(scc_id))

    def rendered(self) -> tuple[str, ...]:
        return tuple(item.render() for item in self.steps)


    def to_dict(self) -> dict[str, Any]:
        return {"steps": [item.to_dict() for item in self.steps]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BorrowPlacePath":
        if not isinstance(value, Mapping) or set(value) != {"steps"}:
            raise ValueError("invalid borrow place path")
        steps = value["steps"]
        if not isinstance(steps, list) or not all(isinstance(item, Mapping) for item in steps):
            raise ValueError("invalid borrow place path steps")
        return cls(tuple(BorrowPlaceStep.from_dict(item) for item in steps))


@dataclass(frozen=True, order=True)
class BorrowRelation:
    """Witness-free element of the finite semantic BorrowSummary lattice."""

    source_parameter_index: int
    source_path: BorrowPlacePath
    borrow_type: str
    result_path: BorrowPlacePath
    kind: str
    ownership: str

    def __post_init__(self) -> None:
        if type(self.source_parameter_index) is not int or self.source_parameter_index < 0:
            raise ValueError("borrow relation source parameter index must be non-negative")
        if not isinstance(self.source_path, BorrowPlacePath) or not self.source_path.steps:
            raise ValueError("borrow relation source path must not be empty")
        if self.source_path.steps[0] != BorrowPlaceStep.parameter(self.source_parameter_index):
            raise ValueError("borrow relation source path must start at its parameter")
        if not isinstance(self.result_path, BorrowPlacePath):
            raise ValueError("borrow relation result path must be structural")
        if not isinstance(self.borrow_type, str) or not self.borrow_type:
            raise ValueError("borrow relation type must not be empty")
        if self.kind not in BORROW_SUMMARY_KINDS:
            raise ValueError(f"invalid borrow summary kind: {self.kind}")
        if self.ownership not in OWNERSHIP_VOCABULARY:
            raise ValueError(f"invalid borrow summary ownership: {self.ownership}")

    def semantic_key(self) -> tuple[object, ...]:
        return (
            self.source_parameter_index,
            self.source_path,
            self.borrow_type,
            self.result_path,
            self.kind,
            self.ownership,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_parameter_index": self.source_parameter_index,
            "source_path": self.source_path.to_dict(),
            "borrow_type": self.borrow_type,
            "result_path": self.result_path.to_dict(),
            "kind": self.kind,
            "ownership": self.ownership,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BorrowRelation":
        expected = {
            "source_parameter_index", "source_path", "borrow_type",
            "result_path", "kind", "ownership",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("invalid borrow relation")
        return cls(
            value["source_parameter_index"],
            BorrowPlacePath.from_dict(value["source_path"]),
            value["borrow_type"],
            BorrowPlacePath.from_dict(value["result_path"]),
            value["kind"],
            value["ownership"],
        )


@dataclass(frozen=True, order=True)
class BorrowSummaryEntry:
    """A semantic relation paired with a non-semantic diagnostic witness."""

    relation: BorrowRelation
    witness_path: tuple[str, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.relation, BorrowRelation):
            raise ValueError("borrow summary entry requires a relation")
        witness = tuple(self.witness_path)
        if any(not isinstance(item, str) or not item for item in witness):
            raise ValueError("borrow witness paths require non-empty text")
        object.__setattr__(self, "witness_path", witness)


    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation.to_dict(),
            "witness_path": list(self.witness_path),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BorrowSummaryEntry":
        if not isinstance(value, Mapping) or set(value) != {"relation", "witness_path"}:
            raise ValueError("invalid borrow summary entry")
        witness = value["witness_path"]
        if not isinstance(witness, list) or not all(isinstance(item, str) for item in witness):
            raise ValueError("invalid borrow summary witness")
        return cls(BorrowRelation.from_dict(value["relation"]), tuple(witness))


def _canonical_entries(
    entries: tuple[BorrowSummaryEntry, ...] | list[BorrowSummaryEntry],
) -> tuple[BorrowSummaryEntry, ...]:
    best: dict[BorrowRelation, BorrowSummaryEntry] = {}
    for entry in entries:
        previous = best.get(entry.relation)
        if previous is None or _witness_key(entry.witness_path) < _witness_key(previous.witness_path):
            best[entry.relation] = entry
    return tuple(sorted(best.values(), key=lambda item: item.relation.semantic_key()))


@dataclass(frozen=True)
class BorrowSummary:
    """Versioned function-level borrow contract."""

    entries: tuple[BorrowSummaryEntry, ...] = ()
    status: str = "known"
    reason: str | None = None
    schema_version: int = BORROW_SUMMARY_SCHEMA_VERSION
    contract: str = BORROW_SUMMARY_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != BORROW_SUMMARY_SCHEMA_VERSION:
            raise ValueError("borrow summary schema version drift")
        if self.contract != BORROW_SUMMARY_CONTRACT:
            raise ValueError("borrow summary contract drift")
        if self.status not in BORROW_SUMMARY_STATUSES:
            raise ValueError(f"invalid borrow summary status: {self.status}")
        if self.status == "known" and self.reason is not None:
            raise ValueError("known borrow summary cannot have an opaque reason")
        if self.status == "opaque" and not self.reason:
            raise ValueError("opaque borrow summary requires a reason")
        if any(not isinstance(item, BorrowSummaryEntry) for item in self.entries):
            raise ValueError("invalid borrow summary entry")
        canonical = _canonical_entries(self.entries)
        if [(item.relation, item.witness_path) for item in canonical] != [
            (item.relation, item.witness_path) for item in self.entries
        ]:
            raise ValueError("borrow summary entries must be sorted and unique")

    @property
    def opaque(self) -> bool:
        return self.status == "opaque"

    @property
    def relations(self) -> tuple[BorrowRelation, ...]:
        return tuple(item.relation for item in self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "status": self.status,
            "reason": self.reason,
            "entries": [item.to_dict() for item in self.entries],
        }

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "status": self.status,
            "reason": self.reason,
            "relations": [item.to_dict() for item in self.relations],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BorrowSummary":
        expected = {"schema_version", "contract", "status", "reason", "entries"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("invalid borrow summary")
        entries = value["entries"]
        if not isinstance(entries, list) or not all(isinstance(item, Mapping) for item in entries):
            raise ValueError("invalid borrow summary entries")
        return cls(
            tuple(BorrowSummaryEntry.from_dict(item) for item in entries),
            value["status"],
            value["reason"],
            value["schema_version"],
            value["contract"],
        )


@dataclass(frozen=True, order=True)
class _Origin:
    source_parameter_index: int
    source_path: BorrowPlacePath
    borrow_type: str
    result_path: BorrowPlacePath
    kind: str
    ownership: str
    pending_recursive_tail: str | None = field(default=None, compare=False)
    witness_path: tuple[str, ...] = field(default=(), compare=False)
    witness_known: bool = field(default=False, compare=False)

    def semantic_key(self) -> tuple[object, ...]:
        return (
            self.source_parameter_index,
            self.source_path,
            self.borrow_type,
            self.result_path,
            self.kind,
            self.ownership,
        )

    def relation(self) -> BorrowRelation | None:
        if self.source_parameter_index < 0:
            return None
        return BorrowRelation(
            self.source_parameter_index,
            self.source_path,
            self.borrow_type,
            self.result_path,
            self.kind,
            self.ownership,
        )


@dataclass
class _Flow:
    env: dict[str, str]
    origins: dict[str, tuple[_Origin, ...]]
    unresolved: list[str]
    parameters: dict[str, int]

    def copy(self) -> "_Flow":
        return _Flow(
            dict(self.env),
            dict(self.origins),
            list(self.unresolved),
            dict(self.parameters),
        )


def _annotation_type(node: ast.AST | None) -> str:
    if node is None:
        return "Unit"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _annotation_type(node.value)
        return f"{owner}.{node.attr}"
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return str(node.value)
    if isinstance(node, ast.Subscript):
        parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else (node.slice,)
        return f"{_annotation_type(node.value)}[{','.join(_annotation_type(item) for item in parts)}]"
    return "Unit"


def _root_name(node: ast.AST | None) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _qualified_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def _stable_borrow_root(node: ast.AST | None) -> str | None:
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return _root_name(node)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"view", "as_view", "slice", "slice_bytes"}
    ):
        return _root_name(node.func.value)
    return None


def _projection_steps(node: ast.AST) -> tuple[BorrowPlaceStep, ...]:
    if isinstance(node, ast.Attribute):
        return (BorrowPlaceStep.field(node.attr),)
    if isinstance(node, ast.Subscript):
        return (BorrowPlaceStep.element(),)
    return ()


def _method_step(method: str) -> BorrowPlaceStep:
    if method in {"get", "view"}:
        return BorrowPlaceStep.element()
    if method in {"unwrap", "unwrap_err"}:
        return BorrowPlaceStep.variant_payload(method)
    return BorrowPlaceStep.field(method)


def _append(origin: _Origin, *steps: BorrowPlaceStep) -> _Origin:
    if origin.pending_recursive_tail is not None:
        result_path = origin.result_path.with_recursive_tail(origin.pending_recursive_tail)
        pending = None
    else:
        result_path = origin.result_path.append(*steps)
        pending = None
    return _Origin(
        origin.source_parameter_index,
        origin.source_path,
        origin.borrow_type,
        result_path,
        origin.kind,
        origin.ownership,
        pending,
        origin.witness_path,
        origin.witness_known,
    )


def _unique(origins: list[_Origin] | tuple[_Origin, ...]) -> tuple[_Origin, ...]:
    best: dict[tuple[object, ...], _Origin] = {}
    for origin in origins:
        key = (*origin.semantic_key(), origin.pending_recursive_tail)
        previous = best.get(key)
        better_witness = (
            origin.witness_known
            and (
                previous is None
                or not previous.witness_known
                or _witness_key(origin.witness_path) < _witness_key(previous.witness_path)
            )
        )
        if previous is None or better_witness:
            best[key] = origin
    return tuple(sorted(best.values(), key=lambda item: (*item.semantic_key(), item.pending_recursive_tail or "")))


def _witness_key(path: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
    return (len(path), path)


def _witness_step(
    path: tuple[str, ...],
    callee: str,
    component: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if BORROW_SUMMARY_CYCLE_MARKER in path:
        return path
    if callee in path:
        return (*path[: path.index(callee)], BORROW_SUMMARY_CYCLE_MARKER)
    if component and callee in component:
        component_steps = sum(item in component for item in path)
        if component_steps >= len(component):
            return (*path, BORROW_SUMMARY_CYCLE_MARKER)
    return (*path, callee)


class _SummaryComputer:
    def __init__(
        self,
        functions: Mapping[str, ast.FunctionDef],
        declarations: Mapping[str, object],
    ) -> None:
        self.functions = dict(functions)
        self.resolver = TypePropertyResolver(declarations)
        self._summaries: dict[str, BorrowSummary] = {
            name: BorrowSummary() for name in sorted(self.functions)
        }
        self._call_graph = self._build_call_graph()
        self._reverse_call_graph = self._build_reverse_call_graph()
        self._finish_order = self._iterative_finish_order(self._call_graph)
        self._sccs = self._iterative_sccs(
            self._call_graph,
            self._reverse_call_graph,
            self._finish_order,
        )
        self._scc_of = {
            name: component
            for component in self._sccs
            for name in component
        }
        self._scc_ids = {
            component: "|".join(component)
            for component in self._sccs
        }
        self._phase = "semantic"
        self._witnesses: dict[str, dict[BorrowRelation, tuple[str, ...] | None]] = {
            name: {} for name in self.functions
        }

    def _build_call_graph(self) -> dict[str, tuple[str, ...]]:
        local = set(self.functions)
        return {
            name: tuple(
                sorted(
                    {
                        node.func.id
                        for node in ast.walk(function)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in local
                    }
                )
            )
            for name, function in sorted(self.functions.items())
        }

    def _build_reverse_call_graph(self) -> dict[str, tuple[str, ...]]:
        reverse: dict[str, set[str]] = {name: set() for name in self.functions}
        for caller, callees in self._call_graph.items():
            for callee in callees:
                reverse[callee].add(caller)
        return {
            name: tuple(sorted(callers))
            for name, callers in sorted(reverse.items())
        }

    @staticmethod
    def _iterative_finish_order(
        graph: Mapping[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        """Return DFS postorder without consuming Python recursion depth."""

        visited: set[str] = set()
        finished: list[str] = []
        for root in sorted(graph):
            if root in visited:
                continue
            visited.add(root)
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack:
                node, child_index = stack[-1]
                children = graph.get(node, ())
                if child_index < len(children):
                    child = children[child_index]
                    stack[-1] = (node, child_index + 1)
                    if child not in visited:
                        visited.add(child)
                        stack.append((child, 0))
                    continue
                stack.pop()
                finished.append(node)
        return tuple(finished)

    @staticmethod
    def _iterative_sccs(
        graph: Mapping[str, tuple[str, ...]],
        reverse: Mapping[str, tuple[str, ...]],
        finish_order: tuple[str, ...] | None = None,
    ) -> tuple[tuple[str, ...], ...]:
        """Deterministic iterative Kosaraju SCC decomposition."""

        order = finish_order or _SummaryComputer._iterative_finish_order(graph)
        assigned: set[str] = set()
        components: list[tuple[str, ...]] = []
        for root in reversed(order):
            if root in assigned:
                continue
            assigned.add(root)
            component: list[str] = []
            stack = [root]
            while stack:
                node = stack.pop()
                component.append(node)
                for parent in reversed(reverse.get(node, ())):
                    if parent not in assigned:
                        assigned.add(parent)
                        stack.append(parent)
            components.append(tuple(sorted(component)))
        return tuple(sorted(components))

    def _recursive_scc_id(self, caller: str, callee: str) -> str | None:
        component = self._scc_of.get(caller, ())
        if callee not in component:
            return None
        if len(component) == 1 and caller not in self._call_graph.get(caller, ()):
            return None
        return self._scc_ids[component]

    def _properties(self, type_name: str | None):
        return self.resolver.resolve(type_name)

    def _contains(self, type_name: str | None) -> bool:
        return self._properties(type_name).contains_borrow

    def _borrow_types(self, type_name: str | None) -> tuple[str, ...]:
        return self._properties(type_name).borrow_types

    def _owner(self, type_name: str | None) -> bool:
        return self._properties(type_name).needs_drop

    def _call_type(self, node: ast.Call, flow: _Flow, expected: str | None = None) -> str | None:
        if isinstance(node.func, ast.Name):
            if node.func.id in self.functions:
                return _annotation_type(self.functions[node.func.id].returns)
            if node.func.id in {"Some", "None", "Ok", "Err"}:
                return expected
            if node.func.id in {"Text", "Bytes", "TextBuilder", "Path"}:
                return node.func.id
            if node.func.id in {"drop"}:
                return "Unit"
        if isinstance(node.func, ast.Attribute):
            receiver = self._expr_type(node.func.value, flow)
            method = node.func.attr
            # Keep this table deliberately narrow and deterministic.  The
            # ownership checker remains the authority for full method typing.
            if receiver in {"Text", "Bytes"} and method in {"view", "as_view", "slice_bytes"}:
                return "TextView" if receiver == "Text" else "BytesView"
            if method == "view":
                vec = generic_parts(receiver or "", "Vec", arity=1)
                if vec is not None:
                    return expected or f"Slice[{vec[0]}]"
            if receiver in {"TextView", "BytesView"} and method in {"slice", "view"}:
                return receiver
            if method == "new" and receiver in {"Vec", "Map", "Box"}:
                return expected
            if receiver and method in {"get", "unwrap", "unwrap_err"}:
                parts = generic_parts(receiver, "Vec", arity=1) or generic_parts(receiver, "Box", arity=1)
                if parts is not None:
                    return parts[0]
                option = generic_parts(receiver, "Option", arity=1)
                if option is not None and method == "unwrap":
                    return option[0]
                result = generic_parts(receiver, "Result", arity=2)
                if result is not None:
                    return result[0] if method == "unwrap" else result[1]
            if receiver and method == "view":
                return receiver
        return expected

    def _expr_type(self, node: ast.AST | None, flow: _Flow, expected: str | None = None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return flow.env.get(node.id)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "Bool"
            if isinstance(node.value, int):
                return "UInt64"
            if isinstance(node.value, float):
                return "Float64"
            if isinstance(node.value, str):
                return "Text"
            return "Unit"
        if isinstance(node, ast.Attribute):
            owner = self._expr_type(node.value, flow)
            declaration = self.resolver.declarations.get(owner) if owner else None
            if declaration is not None and getattr(declaration, "kind", "") == "record":
                for declaration_field in getattr(declaration, "fields", ()):
                    if declaration_field.name == node.attr:
                        return declaration_field.type_name
            return None
        if isinstance(node, ast.Subscript):
            owner = self._expr_type(node.value, flow)
            parts = generic_parts(owner, "Vec", arity=1) if owner else None
            return parts[0] if parts is not None else expected
        if isinstance(node, ast.Call):
            return self._call_type(node, flow, expected)
        return expected

    def _formal_origins(self, function: ast.FunctionDef, flow: _Flow) -> None:
        witness_known = self._phase == "witness"
        for index, argument in enumerate(function.args.args):
            type_name = _annotation_type(argument.annotation)
            flow.env[argument.arg] = type_name
            flow.parameters[argument.arg] = index
            properties = self._properties(type_name)
            if not properties.contains_borrow:
                continue
            kind = "direct" if type_name in {"TextView", "BytesView", "FileLines"} or type_name.startswith("Borrow[") or type_name.startswith("Slice[") else "contained"
            ownership = "contained_borrow" if kind == "contained" else "borrow"
            flow.origins[argument.arg] = _unique(
                [
                    _Origin(
                        index,
                        BorrowPlacePath.parameter(index),
                        borrow_type,
                        BorrowPlacePath(),
                        kind,
                        ownership,
                        witness_known=witness_known,
                    )
                    for borrow_type in properties.borrow_types
                ]
            )

    def _actual_origins(self, argument: ast.AST, flow: _Flow) -> tuple[_Origin, ...]:
        if isinstance(argument, ast.Name):
            tracked = flow.origins.get(argument.id)
            if tracked:
                return tracked
            type_name = flow.env.get(argument.id)
            if self._owner(type_name) and not self._contains(type_name):
                parameter_index = flow.parameters.get(argument.id, -1)
                source_path = (
                    BorrowPlacePath.parameter(parameter_index)
                    if parameter_index >= 0
                    else BorrowPlacePath()
                )
                return (
                    _Origin(
                        parameter_index,
                        source_path,
                        "",
                        BorrowPlacePath(),
                        "direct",
                        "borrow",
                        witness_known=self._phase == "witness",
                    ),
                )
            return ()
        stable_root = _stable_borrow_root(argument)
        if stable_root is not None and stable_root in flow.env:
            return self._expr_origins(argument, flow)
        # A borrow expression over a temporary owner has no stable place.
        return ()

    def _call_origins(self, node: ast.Call, flow: _Flow, result_type: str | None) -> tuple[_Origin, ...]:
        if not isinstance(node.func, ast.Name) or node.func.id not in self.functions:
            return ()
        callee_name = node.func.id
        summary = self._summaries[callee_name]
        if summary.status != "known":
            flow.unresolved.append(f"opaque:{callee_name}")
            return ()
        actuals = tuple(node.args)
        result: list[_Origin] = []
        caller = self._current.name if self._current is not None else ""
        recursive_scc = self._recursive_scc_id(caller, callee_name)
        for entry in summary.entries:
            relation = entry.relation
            if relation.source_parameter_index >= len(actuals):
                flow.unresolved.append(f"arity:{callee_name}")
                continue
            actual = self._actual_origins(
                actuals[relation.source_parameter_index],
                flow,
            )
            if not actual:
                flow.unresolved.append(
                    f"actual:{callee_name}:{relation.source_parameter_index}"
                )
                continue
            witness = None
            if self._phase == "witness":
                witness = self._witnesses[callee_name].get(relation)
            for origin in actual:
                witness_known = origin.witness_known and witness is not None
                result.append(
                    _Origin(
                        origin.source_parameter_index,
                        origin.source_path,
                        relation.borrow_type,
                        relation.result_path,
                        relation.kind,
                        relation.ownership,
                        recursive_scc or origin.pending_recursive_tail,
                        (
                            _witness_step(
                                witness,
                                callee_name,
                                self._scc_of.get(caller, ()),
                            )
                            if witness_known
                            else ()
                        ),
                        witness_known,
                    )
                )
        if result_type and self._contains(result_type) and not result and summary.entries:
            flow.unresolved.append(f"unmapped:{callee_name}")
        return _unique(result)

    def _expr_origins(self, node: ast.AST | None, flow: _Flow, expected: str | None = None) -> tuple[_Origin, ...]:
        if node is None:
            return ()
        if isinstance(node, ast.Name):
            return flow.origins.get(node.id, ())
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            root = _root_name(node)
            return tuple(
                _append(item, *_projection_steps(node))
                for item in (flow.origins.get(root, ()) if root else ())
            )
        if isinstance(node, ast.Call):
            result_type = self._call_type(node, flow, expected)
            direct_name = _qualified_name(node.func)
            if direct_name in {"Some", "Ok", "Err", "Box.new"}:
                step = (
                    BorrowPlaceStep.dereference()
                    if direct_name == "Box.new"
                    else BorrowPlaceStep.variant_payload(direct_name)
                )
                collected: list[_Origin] = []
                for argument in node.args:
                    collected.extend(_append(item, step) for item in self._expr_origins(argument, flow))
                return _unique(collected)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                return self._call_origins(node, flow, result_type)
            if isinstance(node.func, ast.Attribute):
                receiver = self._expr_type(node.func.value, flow)
                method = node.func.attr
                receiver_origins = self._expr_origins(node.func.value, flow)
                if receiver in {"Text", "Bytes"} and method in {"view", "as_view", "slice_bytes"}:
                    root = _root_name(node.func.value)
                    if root is not None and root in flow.env:
                        parameter_index = flow.parameters.get(root, -1)
                        borrow_type = "TextView" if receiver == "Text" else "BytesView"
                        return _unique(
                            [
                                _Origin(
                                    parameter_index,
                                    (
                                        BorrowPlacePath.parameter(parameter_index)
                                        if parameter_index >= 0
                                        else BorrowPlacePath()
                                    ),
                                    borrow_type,
                                    BorrowPlacePath(),
                                    "direct",
                                    "borrow",
                                    witness_known=self._phase == "witness",
                                )
                            ]
                        )
                if method == "view":
                    vec = generic_parts(receiver or "", "Vec", arity=1)
                    root = _root_name(node.func.value)
                    if vec is not None and root is not None and root in flow.env:
                        parameter_index = flow.parameters.get(root, -1)
                        return _unique(
                            [
                                _Origin(
                                    parameter_index,
                                    (
                                        BorrowPlacePath.parameter(parameter_index)
                                        if parameter_index >= 0
                                        else BorrowPlacePath()
                                    ),
                                    f"Slice[{vec[0]}]",
                                    BorrowPlacePath(),
                                    "direct",
                                    "borrow",
                                    witness_known=self._phase == "witness",
                                )
                            ]
                        )
                if receiver_origins:
                    return _unique([_append(item, _method_step(method)) for item in receiver_origins])
                collected: list[_Origin] = []
                for argument in node.args:
                    collected.extend(_append(item, _method_step(method)) for item in self._expr_origins(argument, flow))
                return _unique(collected)
            collected = []
            for argument in node.args:
                collected.extend(self._expr_origins(argument, flow))
            return _unique(collected)
        return ()

    def _assign(self, target: ast.AST, value: ast.AST | None, flow: _Flow, expected: str | None = None) -> None:
        if not isinstance(target, ast.Name):
            return
        type_name = expected or self._expr_type(value, flow)
        if type_name:
            flow.env[target.id] = type_name
        origins = self._expr_origins(value, flow, type_name)
        if origins:
            flow.origins[target.id] = origins
        else:
            flow.origins.pop(target.id, None)

    def _walk_statements(self, statements: list[ast.stmt], flow: _Flow) -> list[tuple[ast.AST | None, tuple[_Origin, ...], str | None]]:
        returns: list[tuple[ast.AST | None, tuple[_Origin, ...], str | None]] = []
        for node in statements:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self._assign(node.target, node.value, flow, _annotation_type(node.annotation))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    self._assign(target, node.value, flow)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == "push":
                    root = _root_name(call.func.value)
                    receiver_type = self._expr_type(call.func.value, flow)
                    element = generic_parts(receiver_type, "Vec", arity=1) if receiver_type else None
                    if root and element is not None and call.args:
                        existing = list(flow.origins.get(root, ()))
                        existing.extend(_append(item, BorrowPlaceStep.element()) for item in self._expr_origins(call.args[0], flow, element[0]))
                        flow.origins[root] = _unique(existing)
                self._expr_origins(call, flow)
            elif isinstance(node, ast.Return):
                result_type = _annotation_type(self._current.returns) if self._current is not None else None
                returns.append((node.value, self._expr_origins(node.value, flow, result_type), result_type))
            elif isinstance(node, ast.If):
                left = flow.copy()
                right = flow.copy()
                returns.extend(self._walk_statements(node.body, left))
                returns.extend(self._walk_statements(node.orelse, right))
                # Union branch origins; environments with the same name are
                # retained only when both branches agree on the type.
                for name in sorted(set(left.origins) | set(right.origins)):
                    flow.origins[name] = _unique((*left.origins.get(name, ()), *right.origins.get(name, ())))
                flow.env.update(left.env)
                flow.env.update(right.env)
                flow.unresolved.extend(left.unresolved)
                flow.unresolved.extend(right.unresolved)
            elif isinstance(node, (ast.While, ast.For)):
                body = node.body
                loop = flow.copy()
                returns.extend(self._walk_statements(body, loop))
                for name, origins in loop.origins.items():
                    flow.origins[name] = _unique((*flow.origins.get(name, ()), *origins))
                flow.unresolved.extend(loop.unresolved)
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    branch = flow.copy()
                    returns.extend(self._walk_statements(case.body, branch))
                    for name, origins in branch.origins.items():
                        flow.origins[name] = _unique((*flow.origins.get(name, ()), *origins))
                    flow.unresolved.extend(branch.unresolved)
            elif isinstance(node, ast.Contract):
                self._expr_origins(node.condition, flow)
        return returns

    def _compute_one(
        self,
        name: str,
        *,
        final: bool = False,
        witness_only: bool = False,
    ) -> BorrowSummary:
        function = self.functions[name]
        self._current = function
        flow = _Flow({}, {}, [], {})
        self._formal_origins(function, flow)
        returns = self._walk_statements(function.body, flow)
        entries: list[BorrowSummaryEntry] = []
        result_type = _annotation_type(function.returns)
        if not self._contains(result_type):
            return BorrowSummary()
        for _node, origins, _ in returns:
            for origin in origins:
                relation = origin.relation()
                if relation is None:
                    flow.unresolved.append("local-owner")
                    continue
                if witness_only and not origin.witness_known:
                    continue
                if not (
                    result_type in {"TextView", "BytesView", "FileLines"}
                    or result_type.startswith(("Borrow[", "Slice["))
                ):
                    relation = BorrowRelation(
                        relation.source_parameter_index,
                        relation.source_path,
                        relation.borrow_type,
                        relation.result_path,
                        "contained",
                        "owned_contained_borrow"
                        if self._owner(result_type)
                        else "contained_borrow",
                    )
                entries.append(
                    BorrowSummaryEntry(
                        relation,
                        origin.witness_path if origin.witness_known else (),
                    )
                )
        if final and flow.unresolved:
            return BorrowSummary((), "opaque", ";".join(sorted(set(flow.unresolved))))
        if final and not entries:
            return BorrowSummary((), "opaque", "no-provenance")
        return BorrowSummary(_canonical_entries(entries))

    def _fail_closed(self, reason: str) -> dict[str, BorrowSummary]:
        return {
            name: (
                BorrowSummary((), "opaque", reason)
                if self._contains(_annotation_type(function.returns))
                else BorrowSummary()
            )
            for name, function in sorted(self.functions.items())
        }

    def _compute_witnesses(self) -> dict[str, BorrowSummary]:
        """Compute shortest canonical witnesses after semantic convergence."""

        self._phase = "witness"
        self._witnesses = {
            name: {relation: None for relation in summary.relations}
            for name, summary in self._summaries.items()
            if not summary.opaque
        }
        pending = deque(self._finish_order)
        queued = set(pending)
        while pending:
            name = pending.popleft()
            queued.remove(name)
            summary = self._summaries[name]
            if summary.opaque:
                continue
            candidate = self._compute_one(name, witness_only=True)
            semantic_relations = set(summary.relations)
            if not set(candidate.relations) <= semantic_relations:
                return self._fail_closed("BorrowSummaryNonMonotone")
            improved = False
            witnesses = self._witnesses[name]
            for entry in candidate.entries:
                previous = witnesses[entry.relation]
                if previous is None or _witness_key(entry.witness_path) < _witness_key(previous):
                    witnesses[entry.relation] = entry.witness_path
                    improved = True
            if improved:
                for caller in self._reverse_call_graph[name]:
                    if caller not in queued:
                        pending.append(caller)
                        queued.add(caller)
        return {
            name: (
                summary
                if summary.opaque
                else BorrowSummary(
                    tuple(
                        BorrowSummaryEntry(
                            relation,
                            self._witnesses[name].get(relation) or (),
                        )
                        for relation in summary.relations
                    )
                )
            )
            for name, summary in sorted(self._summaries.items())
        }

    def compute(self) -> dict[str, BorrowSummary]:
        """Compute a monotone semantic fixed point, then diagnostic witnesses."""

        self._phase = "semantic"
        pending = deque(self._finish_order)
        queued = set(pending)
        while pending:
            name = pending.popleft()
            queued.remove(name)
            candidate = self._compute_one(name)
            previous = self._summaries[name]
            previous_relations = set(previous.relations)
            candidate_relations = set(candidate.relations)
            if not previous_relations <= candidate_relations:
                return self._fail_closed("BorrowSummaryNonMonotone")
            added = candidate_relations - previous_relations
            if added:
                self._summaries[name] = BorrowSummary(
                    tuple(BorrowSummaryEntry(item) for item in sorted(candidate_relations))
                )
                for caller in self._reverse_call_graph[name]:
                    if caller not in queued:
                        pending.append(caller)
                        queued.add(caller)
        final = {
            name: self._compute_one(name, final=True)
            for name in sorted(self.functions)
        }
        for name, summary in final.items():
            if summary.opaque:
                continue
            if set(summary.relations) != set(self._summaries[name].relations):
                return self._fail_closed("BorrowSummaryNonMonotone")
        self._summaries = final
        return self._compute_witnesses()


def compute_borrow_summaries(
    functions: Mapping[str, ast.FunctionDef],
    declarations: Mapping[str, object] | None = None,
) -> dict[str, BorrowSummary]:
    """Compute canonical summaries for all local functions."""

    return _SummaryComputer(functions, declarations or {}).compute()


__all__ = [
    "BORROW_SUMMARY_CONTRACT",
    "BORROW_SUMMARY_CYCLE_MARKER",
    "BORROW_SUMMARY_KINDS",
    "BORROW_SUMMARY_SCHEMA_VERSION",
    "BORROW_SUMMARY_STATUSES",
    "BORROW_PLACE_STEP_KINDS",
    "OWNERSHIP_VOCABULARY",
    "BorrowPlacePath",
    "BorrowPlaceStep",
    "BorrowRelation",
    "BorrowSummary",
    "BorrowSummaryEntry",
    "compute_borrow_summaries",
]
