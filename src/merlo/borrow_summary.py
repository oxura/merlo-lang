"""Deterministic interprocedural borrow summaries.

The ownership checker needs one small piece of information that is not
present in a function signature alone: a returned view may be backed by an
owning parameter.  This module records that relationship in a versioned,
serializable contract and computes it to a fixed point over the local call
graph.  It intentionally describes provenance only; it does not change the
place/lifetime model or lower calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from merlo import native_syntax as ast
from merlo.type_parser import generic_parts
from merlo.type_properties import TypePropertyResolver


BORROW_SUMMARY_SCHEMA_VERSION = 1
BORROW_SUMMARY_CONTRACT = "merlo.borrow-summary.v1"
BORROW_SUMMARY_STATUSES = frozenset({"known", "opaque"})
BORROW_SUMMARY_KINDS = frozenset({"direct", "contained"})
OWNERSHIP_VOCABULARY = frozenset(
    {"value", "borrow", "borrow_mut", "owned", "contained_borrow", "owned_contained_borrow"}
)


def _canonical_path(value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError("borrow summary paths must contain non-empty text")
    return result


@dataclass(frozen=True, order=True)
class BorrowSummaryEntry:
    """One returned-borrow origin relative to a formal parameter."""

    source_parameter_index: int
    source_path: tuple[str, ...]
    borrow_type: str
    result_path: tuple[str, ...]
    kind: str
    ownership: str
    call_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_parameter_index < 0:
            raise ValueError("borrow summary source parameter index must be non-negative")
        if not self.source_path or any(not isinstance(item, str) or not item for item in self.source_path):
            raise ValueError("borrow summary source path must be non-empty text")
        if not self.borrow_type:
            raise ValueError("borrow summary borrow type must not be empty")
        if self.kind not in BORROW_SUMMARY_KINDS:
            raise ValueError(f"invalid borrow summary kind: {self.kind}")
        if self.ownership not in OWNERSHIP_VOCABULARY:
            raise ValueError(f"invalid borrow summary ownership: {self.ownership}")
        object.__setattr__(self, "source_path", _canonical_path(self.source_path))
        object.__setattr__(self, "result_path", _canonical_path(self.result_path))
        object.__setattr__(self, "call_path", _canonical_path(self.call_path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_parameter_index": self.source_parameter_index,
            "source_path": list(self.source_path),
            "borrow_type": self.borrow_type,
            "result_path": list(self.result_path),
            "kind": self.kind,
            "ownership": self.ownership,
            "call_path": list(self.call_path),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BorrowSummaryEntry":
        expected = {
            "source_parameter_index", "source_path", "borrow_type",
            "result_path", "kind", "ownership", "call_path",
        }
        if set(value) != expected:
            raise ValueError("invalid borrow summary entry keys")
        source_index = value["source_parameter_index"]
        if not isinstance(source_index, int) or isinstance(source_index, bool):
            raise ValueError("borrow summary source parameter index must be an integer")
        source_path = value["source_path"]
        result_path = value["result_path"]
        call_path = value["call_path"]
        if not isinstance(source_path, list) or not isinstance(result_path, list) or not isinstance(call_path, list):
            raise ValueError("borrow summary paths must be lists")
        if not all(isinstance(item, str) for item in (*source_path, *result_path, *call_path)):
            raise ValueError("borrow summary paths must contain text")
        for key in ("borrow_type", "kind", "ownership"):
            if not isinstance(value[key], str):
                raise ValueError(f"borrow summary {key} must be text")
        return cls(
            source_index,
            tuple(source_path),
            value["borrow_type"],
            tuple(result_path),
            value["kind"],
            value["ownership"],
            tuple(call_path),
        )


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
        canonical = tuple(sorted(set(self.entries)))
        if canonical != self.entries:
            raise ValueError("borrow summary entries must be sorted and unique")
        if any(not isinstance(item, BorrowSummaryEntry) for item in self.entries):
            raise ValueError("invalid borrow summary entry")

    @property
    def opaque(self) -> bool:
        return self.status == "opaque"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "status": self.status,
            "reason": self.reason,
            "entries": [item.to_dict() for item in self.entries],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BorrowSummary":
        expected = {"schema_version", "contract", "status", "reason", "entries"}
        if set(value) != expected:
            raise ValueError("invalid borrow summary keys")
        version = value["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("borrow summary schema version must be an integer")
        contract = value["contract"]
        status = value["status"]
        reason = value["reason"]
        entries = value["entries"]
        if not isinstance(contract, str) or not isinstance(status, str):
            raise ValueError("borrow summary contract and status must be text")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("borrow summary reason must be text or null")
        if not isinstance(entries, list) or not all(isinstance(item, Mapping) for item in entries):
            raise ValueError("borrow summary entries must be objects")
        return cls(
            tuple(BorrowSummaryEntry.from_dict(item) for item in entries),
            status,
            reason,
            version,
            contract,
        )


@dataclass(frozen=True, order=True)
class _Origin:
    source_parameter_index: int
    source_path: tuple[str, ...]
    borrow_type: str
    result_path: tuple[str, ...]
    kind: str
    ownership: str
    call_path: tuple[str, ...] = ()

    def entry(self) -> BorrowSummaryEntry | None:
        if self.source_parameter_index < 0:
            return None
        return BorrowSummaryEntry(
            self.source_parameter_index,
            self.source_path,
            self.borrow_type,
            self.result_path,
            self.kind,
            self.ownership,
            self.call_path,
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


def _append(origin: _Origin, *steps: str) -> _Origin:
    return _Origin(
        origin.source_parameter_index,
        origin.source_path,
        origin.borrow_type,
        (*origin.result_path, *steps),
        origin.kind,
        origin.ownership,
        origin.call_path,
    )


def _unique(origins: list[_Origin] | tuple[_Origin, ...]) -> tuple[_Origin, ...]:
    return tuple(sorted(set(origins)))


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
                for field in getattr(declaration, "fields", ()):
                    if field.name == node.attr:
                        return field.type_name
            return None
        if isinstance(node, ast.Subscript):
            owner = self._expr_type(node.value, flow)
            parts = generic_parts(owner, "Vec", arity=1) if owner else None
            return parts[0] if parts is not None else expected
        if isinstance(node, ast.Call):
            return self._call_type(node, flow, expected)
        return expected

    def _formal_origins(self, function: ast.FunctionDef, flow: _Flow) -> None:
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
                    _Origin(index, (argument.arg,), borrow_type, (), kind, ownership)
                    for borrow_type in properties.borrow_types
                ]
            )

    def _actual_origins(self, argument: ast.AST, flow: _Flow) -> tuple[_Origin, ...]:
        if isinstance(argument, ast.Name):
            tracked = flow.origins.get(argument.id)
            if tracked:
                return tracked
            type_name = flow.env.get(argument.id)
            # An owning actual can be a fresh source for the callee summary;
            # it is represented as local/unknown until a caller maps it.
            if self._owner(type_name) and not self._contains(type_name):
                return tuple(
                    _Origin(
                        flow.parameters.get(argument.id, -1),
                        (argument.id,),
                        "",
                        (),
                        "direct",
                        "borrow",
                    )
                    for _ in (0,)
                )
            if type_name and self._contains(type_name):
                return ()
            return ()
        # A temporary owner has no stable place.  Keep it unresolved so the
        # call-site checker can issue BorrowFromTemporaryEscapes.
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
        for entry in summary.entries:
            if entry.source_parameter_index >= len(actuals):
                flow.unresolved.append(f"arity:{callee_name}")
                continue
            actual = self._actual_origins(actuals[entry.source_parameter_index], flow)
            if not actual:
                # An owning Name is represented above as an unknown local;
                # non-name temporaries remain unresolved and are rejected by
                # the source checker rather than treated as safe.
                flow.unresolved.append(f"actual:{callee_name}:{entry.source_parameter_index}")
                continue
            for origin in actual:
                result.append(
                    _Origin(
                        origin.source_parameter_index,
                        origin.source_path,
                        entry.borrow_type,
                        entry.result_path,
                        entry.kind,
                        entry.ownership,
                        (*entry.call_path, callee_name),
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
            return tuple(_append(item, ast.unparse(node)) for item in (flow.origins.get(root, ()) if root else ()))
        if isinstance(node, ast.Call):
            result_type = self._call_type(node, flow, expected)
            direct_name = _qualified_name(node.func)
            if direct_name in {"Some", "Ok", "Err", "Box.new"}:
                collected: list[_Origin] = []
                for argument in node.args:
                    collected.extend(_append(item, "payload") for item in self._expr_origins(argument, flow))
                return _unique(collected)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                return self._call_origins(node, flow, result_type)
            if isinstance(node.func, ast.Attribute):
                receiver = self._expr_type(node.func.value, flow)
                method = node.func.attr
                receiver_origins = self._expr_origins(node.func.value, flow)
                if receiver in {"Text", "Bytes"} and method in {"view", "as_view", "slice_bytes"}:
                    root = _root_name(node.func.value)
                    if root is not None:
                        borrow_type = "TextView" if receiver == "Text" else "BytesView"
                        return _unique(
                            [
                                _Origin(
                                    flow.parameters.get(root, -1),
                                    (root,),
                                    borrow_type,
                                    (),
                                    "direct",
                                    "borrow",
                                )
                            ]
                        )
                if method == "view":
                    vec = generic_parts(receiver or "", "Vec", arity=1)
                    root = _root_name(node.func.value)
                    if vec is not None and root is not None:
                        return _unique(
                            [
                                _Origin(
                                    flow.parameters.get(root, -1),
                                    (root,),
                                    f"Slice[{vec[0]}]",
                                    (),
                                    "direct",
                                    "borrow",
                                )
                            ]
                        )
                if receiver_origins:
                    return _unique([_append(item, direct_name or method) for item in receiver_origins])
                collected: list[_Origin] = []
                for argument in node.args:
                    collected.extend(_append(item, direct_name or method) for item in self._expr_origins(argument, flow))
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
                        existing.extend(_append(item, "elements") for item in self._expr_origins(call.args[0], flow, element[0]))
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

    def _compute_one(self, name: str, final: bool = False) -> BorrowSummary:
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
                entry = origin.entry()
                if entry is not None:
                    # The result shape, rather than the expression that
                    # produced the origin, determines whether the returned
                    # borrow is direct or contained.
                    if result_type in {"TextView", "BytesView", "FileLines"} or result_type and result_type.startswith(("Borrow[", "Slice[")):
                        entries.append(entry)
                    else:
                        entries.append(
                            BorrowSummaryEntry(
                                entry.source_parameter_index,
                                entry.source_path,
                                entry.borrow_type,
                                entry.result_path,
                                "contained",
                                "owned_contained_borrow"
                                if self._owner(result_type)
                                else "contained_borrow",
                                entry.call_path,
                            )
                        )
                else:
                    # A function-local owner cannot soundly back a returned
                    # borrow.  The ownership checker reports the precise
                    # source diagnostic; the summary remains opaque too.
                    flow.unresolved.append("local-owner")
        if final and self._contains(result_type) and flow.unresolved:
            reason = ";".join(sorted(set(flow.unresolved)))
            return BorrowSummary((), "opaque", reason)
        if final and self._contains(result_type) and not entries:
            return BorrowSummary((), "opaque", "no-provenance")
        return BorrowSummary(tuple(sorted(set(entries))))

    def compute(self) -> dict[str, BorrowSummary]:
        # Monotone finite fixed point: each iteration only adds summary
        # entries.  The final pass converts unresolved/opaque paths to a
        # fail-closed contract after the graph has stopped changing.
        for _ in range(max(1, len(self.functions) * 2 + 1)):
            changed = False
            next_values: dict[str, BorrowSummary] = {}
            for name in sorted(self.functions):
                summary = self._compute_one(name)
                next_values[name] = summary
                if summary != self._summaries[name]:
                    changed = True
            self._summaries = next_values
            if not changed:
                break
        return {
            name: self._compute_one(name, final=True)
            for name in sorted(self.functions)
        }


def compute_borrow_summaries(
    functions: Mapping[str, ast.FunctionDef],
    declarations: Mapping[str, object] | None = None,
) -> dict[str, BorrowSummary]:
    """Compute canonical summaries for all local functions."""

    return _SummaryComputer(functions, declarations or {}).compute()


__all__ = [
    "BORROW_SUMMARY_CONTRACT",
    "BORROW_SUMMARY_KINDS",
    "BORROW_SUMMARY_SCHEMA_VERSION",
    "BORROW_SUMMARY_STATUSES",
    "OWNERSHIP_VOCABULARY",
    "BorrowSummary",
    "BorrowSummaryEntry",
    "compute_borrow_summaries",
]
