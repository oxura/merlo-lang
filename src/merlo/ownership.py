"""Ownership analysis for structured typed HIR.

The checker is kept separate from HIR construction while preserving the
legacy private symbols re-exported by :mod:`merlo.structured_hir_v2`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from merlo import native_syntax as ast
from merlo.borrow_summary import BorrowSummary
from merlo.collection_protocol import collection_shape
from merlo.place import IndexClass, OverlapRelation, Place, PlaceRoot, PlaceStep, overlap_relation
from merlo.intrinsics import CONTRACT_GRAPH, intrinsic_signature
from merlo.type_parser import generic_parts
from merlo.type_properties import TypePropertyResolver

if TYPE_CHECKING:
    from merlo.structured_hir_v2 import HIRTypeDecl

def _stable_id(prefix: str, *parts: Any) -> str:
    import hashlib
    import json

    payload = json.dumps(
        parts,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"


def _is_borrowed(type_name: str | None) -> bool:
    return bool(type_name) and (
        type_name in {"BytesView", "TextView", "FileLines"}
        or type_name.startswith(("Slice[", "Borrow["))
    )


def _fallback_type_name(node: ast.AST | None) -> str:
    if node is None:
        return "Unit"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        arguments = node.slice.elts if isinstance(node.slice, ast.Tuple) else (node.slice,)
        return f"{node.value.id}[{','.join(_fallback_type_name(item) for item in arguments)}]"
    return ast.unparse(node)
def _assigned_parameter_names(function: ast.FunctionDef) -> set[str]:
    parameters = {item.arg for item in function.args.args}
    assigned: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            root: ast.AST = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in parameters:
                assigned.add(root.id)
        if isinstance(node, ast.Call) and isinstance(
            node.func,
            ast.Attribute,
        ):
            root = node.func.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if (
                isinstance(root, ast.Name)
                and root.id in parameters
                and node.func.attr
                in {
                    "push",
                    "get_mut",
                    "append_byte",
                    "append_scalar",
                    "append_text",
                    "append_uint64",
                    "insert",
                }
            ):
                assigned.add(root.id)
    return assigned


@dataclass
class _OwnershipState:
    statuses: dict[str, str]
    borrows: dict[str, tuple["_BorrowProvenance", ...]]
    places: dict[str, Place]
    terminal: bool = False
    borrow_places: dict[str, Place] = field(default_factory=dict)
    break_paths: tuple["_OwnershipState", ...] = ()
    backedge_paths: tuple["_OwnershipState", ...] = ()

    def clone(self) -> "_OwnershipState":
        return _OwnershipState(
            dict(self.statuses),
            dict(self.borrows),
            dict(self.places),
            self.terminal,
            dict(self.borrow_places),
            tuple(path.clone() for path in self.break_paths),
            tuple(path.clone() for path in self.backedge_paths),
        )


@dataclass(frozen=True, order=True)
class _BorrowProvenance:
    backing_place: Place
    borrow_type: str
    escape_path: tuple[str, ...]
    backing_owner_name: str = field(default="", compare=False)

    @property
    def backing_owner(self) -> str:
        """Legacy diagnostic spelling; semantic identity is ``backing_place``."""
        return self.backing_owner_name or self.backing_place.root.symbol_id
class _OwnershipChecker:
    """Conservative source ownership analysis used to gate HIR construction."""

    def __init__(
        self,
        path: str,
        types: dict[str, HIRTypeDecl],
        functions: dict[str, ast.FunctionDef],
        borrow_summaries: dict[str, BorrowSummary] | None = None,
        binding_kinds: Mapping[int, str] | None = None,
        *,
        compile_error: Callable[[str], Exception] = ValueError,
        type_name: Callable[[ast.AST | None], str] = _fallback_type_name,
        stable_id: Callable[..., str] = _stable_id,
        is_borrowed: Callable[[str | None], bool] = _is_borrowed,
        qualified_name: Callable[[ast.AST], str] = ast.unparse,
    ) -> None:
        self.path = path
        self.types = types
        self.functions = functions
        self.borrow_summaries = borrow_summaries or {}
        self.binding_kinds = binding_kinds or {}
        self._compile_error = compile_error
        self._type_name = type_name
        self._stable_id = stable_id
        self._is_borrowed = is_borrowed
        self._qualified_name = qualified_name
        self.binding_kinds_by_name: dict[str, str] = {}
        self.current: ast.FunctionDef | None = None
        self.type_properties = TypePropertyResolver(types)
        self.env: dict[str, str] = {}
        self.parameters: set[str] = set()
    def _error(self, name: str, variable: str | None = None) -> None:
        suffix = f": {variable}" if variable else ""
        raise self._compile_error(f"{name}{suffix}")

    def _owner(self, type_name: str | None) -> bool:
        return self.type_properties.resolve(type_name).needs_drop

    def _contains_borrow(self, type_name: str | None) -> bool:
        return self.type_properties.resolve(type_name).contains_borrow

    def _borrow_type(self, type_name: str | None) -> str:
        properties = self.type_properties.resolve(type_name)
        return properties.borrow_types[0] if properties.borrow_types else str(type_name)
    def _root_place(self, name: str) -> Place:
        is_parameter = name in self.parameters
        kind = "parameter" if is_parameter else "local"
        root = (
            PlaceRoot.param
            if is_parameter
            else PlaceRoot.local
        )(
            self._stable_id("shirs",
            self.path,
            self.current.name if self.current is not None else "",
            kind,
            name,)
        )
        return Place(root)
    def _is_parameter_place(self, place: Place) -> bool:
        return any(
            place.root == self._root_place(name).root
            for name in self.parameters
        )

    def _field_id(self, type_name: str | None, field_name: str) -> str | None:
        declaration = self.types.get(type_name or "")
        if declaration is None or declaration.kind != "record":
            return None
        field = next(
            (
                item
                for item in declaration.fields
                if item.name == field_name or item.symbol_id == field_name
            ),
            None,
        )
        return field.symbol_id if field is not None else None

    def _field_type(self, type_name: str | None, field_name: str) -> str | None:
        declaration = self.types.get(type_name or "")
        if declaration is None or declaration.kind != "record":
            return None
        field = next(
            (
                item
                for item in declaration.fields
                if item.name == field_name or item.symbol_id == field_name
            ),
            None,
        )
        return field.type_name if field is not None else None

    def _variant_id(self, type_name: str | None, variant_name: str) -> str:
        declaration = self.types.get(type_name or "")
        if declaration is not None and declaration.kind == "enum":
            variant = next(
                (
                    item
                    for item in declaration.variants
                    if item.name == variant_name or item.symbol_id == variant_name
                ),
                None,
            )
            if variant is not None:
                return variant.symbol_id
        return self._stable_id("variant", type_name or "", variant_name)

    def _variant_payload_type(
        self,
        type_name: str | None,
        variant_name: str,
    ) -> str | None:
        declaration = self.types.get(type_name or "")
        if declaration is not None and declaration.kind == "enum":
            variant = next(
                (
                    item
                    for item in declaration.variants
                    if item.name == variant_name or item.symbol_id == variant_name
                ),
                None,
            )
            return variant.payload_type if variant is not None else None
        option = generic_parts(type_name or "", "Option", arity=1)
        if option is not None and variant_name in {"Some", "unwrap"}:
            return option[0]
        result = generic_parts(type_name or "", "Result", arity=2)
        if result is not None and variant_name in {"Ok", "Err", "unwrap", "unwrap_err"}:
            return result[0 if variant_name in {"Ok", "unwrap"} else 1]
        return None

    def _substitute_summary_place(
        self,
        place: Place,
        type_name: str | None,
        source_path: Any,
    ) -> Place | None:
        result = place
        current_type = type_name
        for step in source_path.steps[1:]:
            if step.kind == "Field":
                field_name = str(step.value)
                field_id = self._field_id(current_type, field_name)
                if field_id is None:
                    return None
                result = result.project(PlaceStep.field(field_id))
                current_type = self._field_type(current_type, field_name)
            elif step.kind == "Element":
                shape = collection_shape(current_type)
                if shape is None:
                    return None
                result = result.project(PlaceStep.index(IndexClass.dynamic()))
                current_type = shape.element_type
            elif step.kind == "VariantPayload":
                variant_name = str(step.value)
                if variant_name == "unwrap":
                    variant_name = "Some"
                elif variant_name == "unwrap_err":
                    variant_name = "Err"
                result = result.project(
                    PlaceStep.variant_payload(
                        self._variant_id(current_type, variant_name)
                    )
                )
                current_type = self._variant_payload_type(
                    current_type,
                    variant_name,
                )
                if current_type is None:
                    return None
            elif step.kind == "Deref":
                parts = generic_parts(current_type or "", "Box", arity=1)
                if parts is None:
                    return None
                result = result.project(PlaceStep.dereference())
                current_type = parts[0]
            elif step.kind == "RecursiveTail":
                return None
            else:
                return None
        return result

    def _storage_type(self, name: str) -> str | None:
        parts = name.split(".")
        current = self.env.get(parts[0])
        for field_name in parts[1:]:
            current = self._field_type(current, field_name)
            if current is None:
                return None
        return current
    def _binding_place(
        self,
        node: ast.AST | None,
        state: _OwnershipState,
    ) -> Place | None:
        if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            return self._place_for_expr(node, state)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in {"get", "get_mut"}
                and not self._contains_borrow(self._expr_type(node))
            ):
                return None
            if node.func.attr in {
                "get",
                "get_mut",
                "byte",
                "view",
                "as_view",
                "slice",
                "slice_bytes",
                "lines",
                "entries",
            }:
                return self._place_for_expr(node, state)
        return None
    def _register_record_borrows(
        self,
        state: _OwnershipState,
        name: str,
        type_name: str,
        value: ast.AST,
        fallback: tuple[_BorrowProvenance, ...],
    ) -> None:
        declaration = self.types.get(type_name)
        if not (
            declaration is not None
            and declaration.kind == "record"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == type_name
        ):
            self._register_borrows(
                state,
                name,
                self._extend_provenance(fallback, f"bind({name}:{type_name})"),
                place=self._root_place(name),
            )
            return
        root_place = self._root_place(name)
        registered = False
        for field_decl, argument in zip(declaration.fields, value.args, strict=False):
            field_provenances = self._borrow_provenances(
                argument,
                field_decl.type_name,
                state,
            )
            if not field_provenances:
                continue
            registered = True
            field_place = root_place.project(PlaceStep.field(field_decl.symbol_id))
            self._register_borrows(
                state,
                f"{name}.{field_decl.name}",
                self._extend_provenance(
                    field_provenances,
                    f"bind({name}.{field_decl.name}:{field_decl.type_name})",
                ),
                place=field_place,
            )
        if not registered and fallback:
            self._register_borrows(
                state,
                name,
                self._extend_provenance(fallback, f"bind({name}:{type_name})"),
                place=root_place,
            )
        return None


    @staticmethod
    def _index_class(node: ast.AST | None) -> IndexClass:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            try:
                return IndexClass.constant(node.value)
            except ValueError:
                pass
        return IndexClass.dynamic()

    def _place_for_expr(
        self,
        node: ast.AST | None,
        state: _OwnershipState,
    ) -> Place | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return state.places.get(node.id) or (
                self._root_place(node.id) if node.id in self.env else None
            )
        if isinstance(node, ast.Attribute):
            receiver = self._place_for_expr(node.value, state)
            field_id = self._field_id(self._expr_type(node.value), node.attr)
            if receiver is None or field_id is None:
                return None
            return receiver.project(PlaceStep.field(field_id))
        if isinstance(node, ast.Subscript):
            receiver = self._place_for_expr(node.value, state)
            if receiver is None:
                return None
            return receiver.project(PlaceStep.index(self._index_class(node.slice)))
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                receiver = self._place_for_expr(node.func.value, state)
                if receiver is None:
                    return None
                method = node.func.attr
                if method in {"get", "get_mut", "byte"} and node.args:
                    return receiver.project(
                        PlaceStep.index(self._index_class(node.args[0]))
                    )
                if (
                    method in {"get", "get_mut"}
                    and not node.args
                    and generic_parts(
                        self._expr_type(node.func.value) or "",
                        "Box",
                        arity=1,
                    )
                ):
                    return receiver.project(PlaceStep.dereference())
                if method in {"unwrap", "unwrap_err"}:
                    receiver_type = self._expr_type(node.func.value)
                    variant = (
                        "Err"
                        if method == "unwrap_err"
                        else "Ok"
                        if generic_parts(receiver_type, "Result", arity=2)
                        else "Some"
                    )
                    return receiver.project(
                        PlaceStep.variant_payload(
                            self._variant_id(receiver_type, variant)
                        )
                    )
                if method in {
                    "view",
                    "as_view",
                    "slice",
                    "slice_bytes",
                    "lines",
                    "entries",
                }:
                    return receiver
            for argument in node.args:
                candidate = self._place_for_expr(argument, state)
                if candidate is not None:
                    return candidate
        return None

    def _contained_borrow_error(
        self,
        code: str,
        *,
        container_type: str | None,
        provenance: _BorrowProvenance,
        path: str,
    ) -> None:
        complete_path = " -> ".join((*provenance.escape_path, path))
        callee = next(
            (
                item
                for item in provenance.escape_path
                if item.startswith("formal[")
            ),
            None,
        )
        callee_prefix = ""
        if callee is not None:
            index = provenance.escape_path.index(callee)
            if index:
                callee_prefix = f"callee={provenance.escape_path[index - 1]}; "
        raise self._compile_error(f"{code}: {callee_prefix}container={container_type}; "
        f"contained_borrow={provenance.borrow_type}; "
        f"backing_owner={provenance.backing_owner}; "
        f"backing_place={provenance.backing_place.to_json().rstrip()}; "
        f"escape_path={complete_path}")

    def _borrow_summary_error(
        self,
        code: str,
        *,
        callee: str,
        formal: str,
        actual: str,
        result_type: str | None,
        borrow_type: str | None = None,
        path: tuple[str, ...] = (),
    ) -> None:
        escape = " -> ".join(path) if path else "call"
        raise self._compile_error(f"{code}: callee={callee}; formal={formal}; actual_owner={actual}; "
        f"returned_container={result_type}; contained_borrow={borrow_type or result_type}; "
        f"escape_path={escape}")

    def _summary_provenances(
        self,
        node: ast.Call,
        result_type: str | None,
        state: _OwnershipState,
    ) -> tuple[_BorrowProvenance, ...]:
        if not isinstance(node.func, ast.Name) or node.func.id not in self.functions:
            return ()
        callee_name = node.func.id
        summary = self.borrow_summaries.get(callee_name)
        if summary is None:
            if self._contains_borrow(result_type):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal="<unknown>",
                    actual="<unknown>",
                    result_type=result_type,
                    path=(callee_name, "opaque"),
                )
            return ()
        if summary.opaque:
            if self._contains_borrow(result_type):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal="<summary>",
                    actual="<unknown>",
                    result_type=result_type,
                    path=(callee_name, summary.reason or "opaque"),
                )
            return ()
        provenances: list[_BorrowProvenance] = []
        for entry in summary.entries:
            relation = entry.relation
            parameter_index = relation.source_parameter_index
            if parameter_index >= len(node.args):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal=f"parameter[{parameter_index}]",
                    actual="<missing>",
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, "arity"),
                )
            argument = node.args[parameter_index]
            formal = self.functions[callee_name].args.args[parameter_index].arg
            actual_name = self._root_name(argument) or self._stable_borrow_root(argument)
            actual_place = self._place_for_expr(argument, state)
            actual_type = self._expr_type(argument)
            if actual_name is None or actual_place is None:
                # A temporary owner has no stable place, regardless of which
                # function contains the call. The HIR ownership stage rejects
                # it before a backend escape gate can be reached.
                self._borrow_summary_error(
                    "BorrowFromTemporaryEscapes",
                    callee=callee_name,
                    formal=formal,
                    actual=ast.unparse(argument),
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, formal, *relation.result_path.rendered()),
                )
            source_place = self._substitute_summary_place(
                actual_place,
                actual_type,
                relation.source_path,
            )
            if source_place is None:
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal=formal,
                    actual=actual_name,
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, formal, "unsupported-place"),
                )
            tracked = self._borrow_provenances_at(
                state,
                source_place,
                actual_name,
            )
            path = (
                callee_name,
                f"formal[{parameter_index}]={formal}",
                *relation.source_path.rendered()[1:],
                *relation.result_path.rendered(),
                *entry.witness_path,
            )
            if tracked and (
                relation.kind == "contained"
                or self._is_borrowed(actual_type)
                or self._contains_borrow(actual_type)
            ):
                for item in tracked:
                    provenances.append(
                        _BorrowProvenance(
                            item.backing_place,
                            relation.borrow_type,
                            (*path, *item.escape_path),
                            item.backing_owner_name,
                        )
                    )
                continue
            if self._owner(actual_type) or self._is_borrowed(actual_type):
                provenances.append(
                    _BorrowProvenance(
                        source_place,
                        relation.borrow_type,
                        (*path, actual_name),
                        actual_name,
                    )
                )
                continue
            if self._contains_borrow(result_type):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal=formal,
                    actual=actual_name,
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, formal),
                )
        if self._contains_borrow(result_type) and summary.entries and not provenances:
            self._borrow_summary_error(
                "OpaqueBorrowSummary",
                callee=callee_name,
                formal="<summary>",
                actual="<unknown>",
                result_type=result_type,
                path=(callee_name, "unmapped"),
            )
        if self._contains_borrow(result_type) and not summary.entries:
            self._borrow_summary_error(
                "OpaqueBorrowSummary",
                callee=callee_name,
                formal="<summary>",
                actual="<unknown>",
                result_type=result_type,
                path=(callee_name, "missing-origin"),
            )
        return tuple(sorted(set(provenances)))

    def _reject_unknown_borrow_call(
        self,
        node: ast.Call,
        result_type: str | None,
    ) -> None:
        if not (
            isinstance(node.func, ast.Name)
            and node.func.id not in self.functions
            and node.func.id not in self.types
            and node.func.id not in {"Some", "None", "Ok", "Err", "drop"}
            and self._contains_borrow(result_type)
        ):
            return
        self._borrow_summary_error(
            "OpaqueBorrowSummary",
            callee=node.func.id,
            formal="<external>",
            actual="<unknown>",
            result_type=result_type,
            path=(node.func.id, "opaque"),
        )

    @staticmethod
    def _extend_provenance(
        provenances: tuple[_BorrowProvenance, ...],
        step: str,
    ) -> tuple[_BorrowProvenance, ...]:
        return tuple(
            _BorrowProvenance(
                item.backing_place,
                item.borrow_type,
                (*item.escape_path, step),
                item.backing_owner_name,
            )
            for item in provenances
        )
    def _borrow_provenances_at(
        self,
        state: _OwnershipState,
        target: Place,
        fallback_name: str | None = None,
    ) -> tuple[_BorrowProvenance, ...]:
        collected: list[_BorrowProvenance] = []
        for name, provenances in state.borrows.items():
            storage = state.borrow_places.get(name)
            if storage is None:
                if name == fallback_name:
                    collected.extend(provenances)
                continue
            relation = overlap_relation(target, storage)
            if relation in {
                OverlapRelation.EQUAL,
                OverlapRelation.ANCESTOR,
            }:
                collected.extend(provenances)
        return tuple(sorted(set(collected)))

    @staticmethod
    def _remove_borrows_at_place(
        state: _OwnershipState,
        target: Place,
    ) -> None:
        for name, place in tuple(state.borrow_places.items()):
            if place == target:
                state.borrows.pop(name, None)
                state.borrow_places.pop(name, None)

    @staticmethod
    def _borrow_storage_overlaps(
        state: _OwnershipState,
        target: Place,
    ) -> bool:
        return any(
            overlap_relation(target, place) is not OverlapRelation.DISJOINT
            for place in state.borrow_places.values()
            if place is not None
        )
    @staticmethod
    def _register_borrows(
        state: _OwnershipState,
        name: str,
        provenances: tuple[_BorrowProvenance, ...],
        place: Place | None = None,
    ) -> None:
        if provenances:
            state.borrows[name] = tuple(sorted(set(provenances)))
            state.borrow_places[name] = place or state.places.get(name)
        else:
            state.borrows.pop(name, None)
            state.borrow_places.pop(name, None)


    def _borrow_provenances(
        self,
        node: ast.AST | None,
        type_name: str | None,
        state: _OwnershipState,
    ) -> tuple[_BorrowProvenance, ...]:
        if node is None or not self._contains_borrow(type_name):
            return ()
        if isinstance(node, ast.Name):
            place = self._place_for_expr(node, state)
            tracked = (
                self._borrow_provenances_at(state, place, node.id)
                if place is not None
                else ()
            )
            if tracked:
                return self._extend_provenance(tracked, node.id)
            if place is not None:
                return (
                    _BorrowProvenance(
                        place,
                        self._borrow_type(type_name),
                        (node.id,),
                        node.id,
                    ),
                )
            return ()
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            root = self._root_name(node)
            place = self._place_for_expr(node, state)
            tracked = (
                self._borrow_provenances_at(state, place, root)
                if place is not None
                else ()
            )
            if tracked:
                return self._extend_provenance(tracked, ast.unparse(node))
            place = self._place_for_expr(node, state)
            if place is not None:
                return (
                    _BorrowProvenance(
                        place,
                        self._borrow_type(type_name),
                        (ast.unparse(node),),
                        root or ast.unparse(node),
                    ),
                )
        if isinstance(node, ast.Call):
            step = ast.unparse(node.func)
            result_type = self._expr_type(node, type_name)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                summary_provenances = self._summary_provenances(
                    node,
                    result_type,
                    state,
                )
                if summary_provenances:
                    return tuple(
                        self._extend_provenance(summary_provenances, step)
                    )
            self._reject_unknown_borrow_call(node, result_type)
            if isinstance(node.func, ast.Attribute):
                receiver_root = self._root_name(node.func.value)
                candidate = self._place_for_expr(node.func.value, state)
                tracked = (
                    self._borrow_provenances_at(
                        state,
                        candidate,
                        receiver_root,
                    )
                    if candidate is not None
                    else ()
                )
                if tracked:
                    return self._extend_provenance(tracked, step)
                receiver_type = self._expr_type(node.func.value)
                if candidate is not None and (
                    self._owner(receiver_type)
                    or self._is_borrowed(receiver_type)
                    or self._contains_borrow(type_name)
                ):
                    return (
                        _BorrowProvenance(
                            candidate,
                            self._borrow_type(type_name),
                            (ast.unparse(node.func.value), step),
                            receiver_root or ast.unparse(node.func.value),
                        ),
                    )
            collected: list[_BorrowProvenance] = []
            for argument in node.args:
                argument_type = self._expr_type(argument)
                # Constructors carry the only useful expected type for a
                # payload in this syntax. Preserve that type while walking
                # the argument so a borrow nested in Box/Option/Result is
                # attributed to its real backing place.
                if argument_type is None:
                    qualified = self._qualified_name(node.func)
                    if qualified == "Box.new":
                        parts = generic_parts(result_type or "", "Box", arity=1)
                        argument_type = parts[0] if parts is not None else None
                    elif qualified == "Some":
                        parts = generic_parts(result_type or "", "Option", arity=1)
                        argument_type = parts[0] if parts is not None else None
                    elif qualified in {"Ok", "Err"}:
                        parts = generic_parts(result_type or "", "Result", arity=2)
                        if parts is not None:
                            argument_type = parts[0 if qualified == "Ok" else 1]
                tracked = self._borrow_provenances(
                    argument,
                    argument_type,
                    state,
                )
                collected.extend(self._extend_provenance(tracked, step))
            return tuple(sorted(set(collected)))
        return ()
    def _live_borrow_of(
        self,
        state: _OwnershipState,
        owner: str | Place,
        *,
        nested_only: bool = False,
    ) -> tuple[str, _BorrowProvenance] | None:
        target = (
            state.places.get(owner) or self._root_place(owner)
            if isinstance(owner, str)
            else owner
        )
        return next(
            (
                (container, provenance)
                for container, provenances in sorted(state.borrows.items())
                for provenance in provenances
                if overlap_relation(provenance.backing_place, target)
                is not OverlapRelation.DISJOINT
                and (
                    not nested_only
                    or not self._is_borrowed(self.env.get(container))
                    or any(
                        step.startswith("formal[")
                        for step in provenance.escape_path
                    )
                )
            ),
            None,
        )

    def _expr_type(self, node: ast.AST | None, expected: str | None = None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
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
            owner = self._expr_type(node.value)
            if owner in self.types and self.types[owner].kind == "record":
                return next(
                    (field.type_name for field in self.types[owner].fields if field.name == node.attr),
                    None,
                )
            return owner if isinstance(node.value, ast.Name) and node.value.id in self.types else None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                if name in self.functions:
                    return self._type_name(self.functions[name].returns)
                if name in self.types:
                    return name
                if name in {"Text", "Bytes", "Path", "TextBuilder"}:
                    return name
                if name == "drop":
                    return "Unit"
            if isinstance(node.func, ast.Attribute):
                receiver = self._expr_type(node.func.value)
                method = node.func.attr
                receiver_text = self._qualified_name(node.func.value)
                static_signature = CONTRACT_GRAPH.static_method(
                    receiver_text,
                    method,
                )
                if static_signature is not None:
                    resolved_static = CONTRACT_GRAPH.resolve_static_method(
                        receiver_text,
                        method,
                        tuple(self._expr_type(argument) for argument in node.args),
                        expected,
                    )
                    return (
                        resolved_static.result_type
                        if resolved_static is not None
                        else expected
                    )
                method_signature = CONTRACT_GRAPH.method(
                    receiver or "",
                    method,
                )
                if method_signature is not None:
                    return method_signature.result_for(expected)
        if isinstance(node, ast.Subscript):
            owner = self._expr_type(node.value)
            shape = collection_shape(owner)
            if shape is not None:
                return shape.element_type
            return expected
        return expected

    @staticmethod
    def _root_name(node: ast.AST | None) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    @classmethod
    def _stable_borrow_root(cls, node: ast.AST | None) -> str | None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"view", "as_view", "slice", "slice_bytes"}
        ):
            return cls._root_name(node.func.value)
        return None

    @staticmethod
    def _borrow_source(node: ast.AST | None) -> ast.AST | None:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.value
        return node

    def _check_name(self, name: str, state: _OwnershipState) -> None:
        status = state.statuses.get(name)
        if status == "ambiguous":
            self._error("OwnershipAmbiguity", name)
        if status == "moved":
            self._error("UseAfterMove", name)
        if status == "dropped":
            self._error("UseAfterDrop", name)
    def _consume_place(
        self,
        place: Place,
        state: _OwnershipState,
        *,
        name: str | None = None,
        display: str | None = None,
    ) -> None:
        if name is not None:
            self._check_name(name, state)
        live = self._live_borrow_of(state, place)
        if live is not None:
            container, provenance = live
            self._contained_borrow_error(
                "BackingOwnerMoveWhileBorrowed",
                container_type=self._storage_type(container),
                provenance=provenance,
                path=f"move({display or name or place})",
            )
        if (
            name is not None
            and name in state.statuses
            and place == (state.places.get(name) or self._root_place(name))
        ):
            state.statuses[name] = "moved"
            state.borrows.pop(name, None)
            state.borrow_places.pop(name, None)

    def _consume(self, name: str, state: _OwnershipState) -> None:
        self._consume_place(
            state.places.get(name) or self._root_place(name),
            state,
            name=name,
            display=name,
        )
    def _reject_projected_owner_move(
        self,
        place: Place | None,
        type_name: str | None,
        *,
        allow_borrowed_or_temporary_parent: bool = False,
    ) -> None:
        if (
            place is not None
            and place.steps
            and self._owner(type_name)
            and not self._is_borrowed(type_name)
            and not allow_borrowed_or_temporary_parent
        ):
            self._error("ProjectedOwnerMoveRequiresPartialMoveSupport")


    def _consume_expr(
        self,
        node: ast.AST,
        state: _OwnershipState,
        *,
        expected: str | None = None,
        allow_projected_owner_move: bool = False,
    ) -> None:
        if isinstance(node, ast.Call):
            qualified = self._qualified_name(node.func)
            if qualified in {"Some", "Ok", "Err"}:
                for argument in node.args:
                    argument_type = self._expr_type(argument)
                    if self._owner(argument_type):
                        self._consume_expr(
                            argument,
                            state,
                            expected=argument_type,
                            allow_projected_owner_move=allow_projected_owner_move,
                        )
            elif isinstance(node.func, ast.Name) and node.func.id in self.types:
                declaration = self.types[node.func.id]
                if declaration.kind == "record":
                    for field_decl, argument in zip(
                        declaration.fields,
                        node.args,
                        strict=False,
                    ):
                        if self._owner(field_decl.type_name):
                            self._consume_expr(
                                argument,
                                state,
                                expected=field_decl.type_name,
                                allow_projected_owner_move=allow_projected_owner_move,
                            )
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                receiver = node.func.value
                receiver_type = self._expr_type(receiver)
                receiver_generic = generic_parts(receiver_type or "", "Box", arity=1)
                result_type = self._expr_type(node, expected)
                if (
                    receiver_generic is not None
                    and self._owner(result_type)
                ):
                    self._reject_projected_owner_move(
                        self._place_for_expr(node, state),
                        result_type,
                        allow_borrowed_or_temporary_parent=isinstance(
                            receiver,
                            ast.Call,
                        ),
                    )
            return
        if not isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            return
        root = self._root_name(node)
        if root is not None:
            self._check_name(root, state)
        place = self._place_for_expr(node, state)
        self._reject_projected_owner_move(
            place,
            self._expr_type(node, expected),
            allow_borrowed_or_temporary_parent=(
                allow_projected_owner_move and root in self.parameters
            ),
        )
        self._consume_place(
            place,
            state,
            name=root,
            display=ast.unparse(node),
        )

    def _reject_projected_owner_drop(
        self,
        place: Place | None,
        type_name: str | None,
    ) -> None:
        if (
            place is not None
            and place.steps
            and self._owner(type_name)
            and not self._is_borrowed(type_name)
        ):
            self._error("ProjectedOwnerDropRequiresPartialMoveSupport")

    def _check_mutation(
        self,
        name: str,
        state: _OwnershipState,
        *,
        place: Place | None = None,
        display: str | None = None,
    ) -> None:
        self._check_name(name, state)
        target = place or state.places.get(name) or self._root_place(name)
        if (
            self._live_borrow_of(state, target) is not None
            or self._borrow_provenances_at(state, target)
            or self._borrow_storage_overlaps(state, target)
        ):
            self._error("MutationDuringBorrow", display or name)

    def _check_mutation_expr(self, node: ast.AST, state: _OwnershipState) -> None:
        root = self._root_name(node)
        place = self._place_for_expr(node, state)
        if root is not None:
            self._check_name(root, state)
        if place is None:
            return
        if (
            self._live_borrow_of(state, place)
            or self._borrow_provenances_at(state, place)
            or self._borrow_storage_overlaps(state, place)
        ):
            self._error("MutationDuringBorrow", ast.unparse(node))

    def _borrow_result(
        self,
        expression: ast.AST,
        result_type: str | None,
        state: _OwnershipState,
    ) -> None:
        if not self._contains_borrow(result_type):
            return
        root = self._root_name(self._borrow_source(expression))
        if root is not None:
            self._check_name(root, state)

    def _check_expr(
        self,
        node: ast.AST | None,
        state: _OwnershipState,
        *,
        expected: str | None = None,
    ) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            self._check_name(node.id, state)
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            root = self._root_name(node)
            if root is not None:
                self._check_name(root, state)
            self._check_expr(node.value, state)
            return self._expr_type(node)
        if isinstance(node, ast.Subscript):
            root = self._root_name(node.value)
            if root is not None:
                self._check_name(root, state)
            self._check_expr(node.value, state)
            self._check_expr(node.slice, state)
            return self._expr_type(node, expected)
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else self._qualified_name(node.func)
            )
            if isinstance(node.func, ast.Name) and node.func.id == "drop":
                if len(node.args) != 1:
                    self._error("InvalidDrop")
                target_node = node.args[0]
                target_name = (
                    target_node.id if isinstance(target_node, ast.Name) else None
                )
                target_place = self._place_for_expr(target_node, state)
                if target_name is not None and target_name in state.borrows and target_name not in state.statuses:
                    del state.borrows[target_name]
                    state.borrow_places.pop(target_name, None)
                    return "Unit"
                if target_name is not None and state.statuses.get(target_name) == "dropped":
                    self._error("DuplicateDrop", target_name)
                if target_name is not None:
                    self._check_name(target_name, state)
                self._reject_projected_owner_drop(
                    target_place,
                    self._expr_type(target_node),
                )
                if target_place is None:
                    self._error("InvalidDrop")
                if target_name is None and self._is_borrowed(self._expr_type(target_node)):
                    self._remove_borrows_at_place(state, target_place)
                    return "Unit"
                live = self._live_borrow_of(state, target_place)
                if live is not None:
                    container, provenance = live
                    self._contained_borrow_error(
                        "BackingOwnerDropWhileBorrowed",
                        container_type=self._storage_type(container),
                        provenance=provenance,
                        path=f"drop({ast.unparse(target_node)})",
                    )
                if target_name is not None:
                    state.borrows.pop(target_name, None)
                    state.borrow_places.pop(target_name, None)
                    state.statuses[target_name] = "dropped"
                return "Unit"
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            receiver_type = self._expr_type(receiver)
            method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            map_types = generic_parts(receiver_type or "", "Map", arity=2)
            if (
                map_types is not None
                and self._owner(map_types[1])
                and method in {"get", "insert", "increment", "entries"}
            ):
                self._error("MapOperationsRequireScalarValues", receiver_type)
            receiver_root = self._root_name(receiver)
            receiver_place = self._place_for_expr(receiver, state)
            if receiver_root is not None:
                self._check_name(receiver_root, state)
            if receiver_root and method in {
                "push", "get_mut", "insert", "increment",
                "append_byte", "append_scalar", "append_text", "append_uint64",
            }:
                self._check_mutation(
                    receiver_root,
                    state,
                    place=receiver_place,
                    display=ast.unparse(receiver),
                )
            if receiver_root and method in {"view", "get", "get_mut", "entries", "as_view"}:
                self._check_name(receiver_root, state)
            if receiver is not None:
                self._check_expr(receiver, state)
            for argument in node.args:
                if getattr(argument, "_merlo_implicit_callable", None) is None:
                    self._check_expr(argument, state)
            method_signature = (
                CONTRACT_GRAPH.method(receiver_type, method)
                if receiver_type is not None and method
                else None
            )
            if method_signature is None and method:
                static_receiver = self._qualified_name(receiver)
                static_signature = CONTRACT_GRAPH.static_method(
                    static_receiver,
                    method,
                )
                if static_signature is not None:
                    method_signature = static_signature
            if method_signature is not None:
                if not method_signature.accepts_arity(len(node.args)):
                    self._error(
                        "ArityMismatch",
                        f"{receiver_type}.{method}",
                    )
                if receiver_root is not None and not method_signature.static:
                    if method_signature.receiver_ownership == "borrow_mut":
                        self._check_mutation(
                            receiver_root,
                            state,
                            place=receiver_place,
                            display=ast.unparse(receiver),
                        )
                    elif method_signature.receiver_ownership == "consuming":
                        self._consume_place(
                            receiver_place or self._root_place(receiver_root),
                            state,
                            name=receiver_root,
                            display=ast.unparse(receiver),
                        )
                for argument, parameter_ownership in zip(
                    node.args,
                    method_signature.ownership_for(len(node.args)),
                    strict=True,
                ):
                    root = self._root_name(argument)
                    place = self._place_for_expr(argument, state)
                    if root is None and place is None:
                        continue
                    if parameter_ownership == "borrow_mut":
                        if root is not None:
                            self._check_mutation(
                                root,
                                state,
                                place=place,
                                display=ast.unparse(argument),
                            )
                    elif parameter_ownership in {"owned", "consuming"}:
                        self._consume_expr(
                            argument,
                            state,
                            expected=self._expr_type(argument),
                        )
            signature = intrinsic_signature(name)
            if signature is not None:
                for argument, parameter_ownership in zip(
                    node.args, signature.parameter_ownership, strict=True
                ):
                    root = self._root_name(argument)
                    place = self._place_for_expr(argument, state)
                    if root is None and place is None:
                        continue
                    if parameter_ownership == "borrow_mut":
                        if root is not None:
                            self._check_mutation(
                                root,
                                state,
                                place=place,
                                display=ast.unparse(argument),
                            )
                    elif parameter_ownership in {"owned", "consuming"}:
                        self._consume_expr(
                            argument,
                            state,
                            expected=self._expr_type(argument),
                        )
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                callee = self.functions[node.func.id]
                for argument, parameter in zip(node.args, callee.args.args):
                    parameter_type = self._type_name(parameter.annotation)
                    returned = any(
                        isinstance(item, ast.Return)
                        and isinstance(item.value, ast.Name)
                        and item.value.id == parameter.arg
                        for item in ast.walk(callee)
                    )
                    if self._owner(parameter_type) and returned:
                        self._consume_expr(
                            argument,
                            state,
                            expected=parameter_type,
                        )
            elif (
                method_signature is None
                and receiver_root
                and method == "push"
                and node.args
            ):
                vec_parts = generic_parts(receiver_type, "Vec", arity=1)
                element = vec_parts[0] if vec_parts is not None else None
                if self._owner(element):
                    self._consume_expr(
                        node.args[0],
                        state,
                        expected=element,
                    )
            if receiver_root and method == "push" and node.args:
                vec_parts = generic_parts(receiver_type, "Vec", arity=1)
                element_type = vec_parts[0] if vec_parts is not None else None
                if self._contains_borrow(element_type):
                    provenances = self._borrow_provenances(
                        node.args[0],
                        element_type,
                        state,
                    )
                    if not provenances:
                        self._error(
                            "ContainedBorrowProvenanceUnknown",
                            f"{receiver_type}.push",
                        )
                    self._register_borrows(
                        state,
                        receiver_root,
                        tuple(
                            sorted(
                                set(
                                    self._borrow_provenances_at(
                                        state,
                                        receiver_place or self._root_place(receiver_root),
                                        receiver_root,
                                    )
                                )
                                | set(
                                    self._extend_provenance(
                                        provenances,
                                        f"{receiver_type}.push",
                                    )
                                )
                            )
                        ),
                        place=receiver_place,
                    )
            result_type = self._expr_type(node, expected)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                self._summary_provenances(node, result_type, state)
            self._reject_unknown_borrow_call(node, result_type)
            self._borrow_result(node.func.value if receiver is not None else node, result_type, state)
            return result_type
        if isinstance(node, ast.Lambda):
            metadata = getattr(node, "_merlo_closure_metadata", None)
            if metadata is None:
                self._error("CapturingClosureUnsupported")
            for name, capture_type, _ownership in metadata[3]:
                self._check_name(name, state)
                properties = self.type_properties.resolve(capture_type)
                if properties.contains_borrow:
                    capture_place = state.places.get(name) or self._root_place(name)
                    provenances = self._borrow_provenances_at(
                        state,
                        capture_place,
                        name,
                    ) or (
                        _BorrowProvenance(
                            capture_place,
                            self._borrow_type(capture_type),
                            (name,),
                            name,
                        ),
                    )
                    self._contained_borrow_error(
                        "BorrowedClosureCaptureEscapes",
                        container_type=capture_type,
                        provenance=provenances[0],
                        path=f"closure_capture({name})",
                    )
                if properties.is_resource or properties.contains_resource:
                    self._error("ResourceClosureCaptureForbidden", name)
            return expected
    def _merge(self, before: _OwnershipState, branches: tuple[_OwnershipState, ...]) -> _OwnershipState:
        live = tuple(branch for branch in branches if not branch.terminal)
        break_paths = tuple(
            path
            for branch in branches
            for path in branch.break_paths
        )
        backedge_paths = tuple(
            path
            for branch in branches
            for path in branch.backedge_paths
        )
        if not live:
            return _OwnershipState(
                dict(before.statuses),
                dict(before.borrows),
                dict(before.places),
                True,
                dict(before.borrow_places),
                break_paths,
                backedge_paths,
            )
        merged_statuses = dict(live[0].statuses)
        for name in sorted(before.statuses):
            statuses = {
                branch.statuses.get(name, "absent")
                for branch in live
            }
            if len(statuses) > 1:
                if self.binding_kinds_by_name.get(name) != "binding":
                    self._error("OwnershipAmbiguity", name)
                # Inferred rebindings use drop-flag slots; keep the ambiguity
                # visible to any use until a later assignment resolves it.
                merged_statuses[name] = "ambiguous"
        borrows = [branch.borrows for branch in live]
        if borrows and any(item != borrows[0] for item in borrows[1:]):
            self._error("OwnershipAmbiguity")
        tracked_place_names = set(before.statuses) | set(before.borrow_places)
        branch_places = tuple(
            tuple(
                (name, branch.places.get(name))
                for name in sorted(tracked_place_names)
            )
            for branch in live
        )
        if branch_places and any(item != branch_places[0] for item in branch_places[1:]):
            self._error("OwnershipAmbiguity")
        borrow_places = [branch.borrow_places for branch in live]
        if borrow_places and any(item != borrow_places[0] for item in borrow_places[1:]):
            self._error("OwnershipAmbiguity")
        return _OwnershipState(
            merged_statuses,
            dict(live[0].borrows),
            dict(live[0].places),
            False,
            dict(live[0].borrow_places),
            break_paths,
            backedge_paths,
        )
    @staticmethod
    def _snapshot_state(state: _OwnershipState) -> _OwnershipState:
        return _OwnershipState(
            dict(state.statuses),
            dict(state.borrows),
            dict(state.places),
            False,
            dict(state.borrow_places),
        )
    @staticmethod
    def _loop_tracked_borrow_names(
        before: _OwnershipState,
        candidate: _OwnershipState,
    ) -> set[str]:
        """Return borrow-storage names rooted in places visible at loop entry.

        A pre-existing container may acquire its first contained borrow inside
        a loop. Such a new entry is ownership-visible even though its key was
        absent from ``before.borrows``. Projection is therefore driven by
        pre-loop storage roots rather than only by pre-existing borrow keys.
        """
        preloop_roots = {place.root for place in before.places.values()}
        names = set(before.borrows) | set(before.borrow_places)
        for name in set(candidate.borrows) | set(candidate.borrow_places):
            storage = candidate.borrow_places.get(name) or candidate.places.get(name)
            if storage is not None and storage.root in preloop_roots:
                names.add(name)
            elif name in before.places:
                names.add(name)
        return names

    @staticmethod
    def _loop_visible_state(
        before: _OwnershipState,
        candidate: _OwnershipState,
    ) -> _OwnershipState:
        tracked_borrows = _OwnershipChecker._loop_tracked_borrow_names(
            before,
            candidate,
        )
        return _OwnershipState(
            {
                name: candidate.statuses.get(name, "absent")
                for name in before.statuses
            },
            {
                name: candidate.borrows[name]
                for name in tracked_borrows
                if name in candidate.borrows
            },
            {
                name: candidate.places[name]
                for name in before.places
                if name in candidate.places
            },
            False,
            {
                name: candidate.borrow_places[name]
                for name in tracked_borrows
                if name in candidate.borrow_places
            },
        )
    @staticmethod
    def _loop_assignment_names(
        statements: list[ast.stmt],
        binding_kinds: Mapping[int, str],
    ) -> set[str]:
        """Find compiler-managed inferred bindings in a loop body."""
        names = {
            target.id
            for statement in statements
            for node in ast.walk(statement)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        names.update(
            node.target.id
            for statement in statements
            for node in ast.walk(statement)
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and getattr(
                    node,
                    "_merlo_binding_kind",
                    binding_kinds.get(node.lineno),
                )
                not in {"let", "var"}
            )
        )
        return names

    @staticmethod
    def _loop_implicit_cleanup_names(statements: list[ast.stmt]) -> set[str]:
        """Recognize owned collection values whose scope cleanup is deterministic."""
        return {
            node.target.id
            for statement in statements
            for node in ast.walk(statement)
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "get"
            )
        }
    def _require_loop_backedge_stable(
        self,
        before: _OwnershipState,
        candidate: _OwnershipState,
        *,
        assignment_names: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        """Require every ownership-visible backedge to reach the loop entry."""
        for name in sorted(set(candidate.statuses) - set(before.statuses)):
            if (
                candidate.statuses[name] not in {"dropped", "moved"}
                and name not in assignment_names
            ):
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.statuses):
            if candidate.statuses.get(name, "absent") != before.statuses[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.places):
            if candidate.places.get(name) != before.places[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(self._loop_tracked_borrow_names(before, candidate)):
            if candidate.borrows.get(name) != before.borrows.get(name):
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
            if candidate.borrow_places.get(name) != before.borrow_places.get(name):
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
    def _loop_exit_state(
        self,
        before: _OwnershipState,
        candidate: _OwnershipState,
        *,
        assignment_names: set[str] | frozenset[str] = frozenset(),
    ) -> _OwnershipState:
        """Project a break exit only after cleaning iteration-local owners."""
        for name in sorted(set(candidate.statuses) - set(before.statuses)):
            if (
                candidate.statuses[name] not in {"dropped", "moved"}
                and name not in assignment_names
            ):
                self._error("OwnershipAmbiguity", name)
        return self._loop_visible_state(before, candidate)
    def _join_loop_states(
        self,
        before: _OwnershipState,
        candidates: tuple[_OwnershipState, ...],
    ) -> _OwnershipState:
        joined = self._merge(
            before,
            tuple(
                self._loop_visible_state(before, candidate)
                for candidate in candidates
            ),
        )
        return _OwnershipState(
            dict(joined.statuses),
            dict(joined.borrows),
            dict(joined.places),
            joined.terminal,
            dict(joined.borrow_places),
        )
    def _check_statements(self, statements: list[ast.stmt], state: _OwnershipState) -> _OwnershipState:
        for node in statements:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.binding_kinds_by_name.setdefault(
                    node.target.id,
                    getattr(
                        node,
                        "_merlo_binding_kind",
                        self.binding_kinds.get(node.lineno, "let"),
                    ),
                )
                type_name = self._type_name(node.annotation)
                if node.value is not None:
                    provenances = self._borrow_provenances(
                        node.value,
                        type_name,
                        state,
                    )
                    self._check_expr(node.value, state, expected=type_name)
                    if self._owner(type_name):
                        self._consume_expr(
                            node.value,
                            state,
                            expected=type_name,
                        )
                    if self._contains_borrow(type_name):
                        self._register_record_borrows(
                            state,
                            node.target.id,
                            type_name,
                            node.value,
                            provenances,
                        )
                self.env[node.target.id] = type_name
                existing_place = state.places.get(node.target.id)
                if existing_place is not None:
                    state.places[node.target.id] = existing_place
                else:
                    binding_place = self._binding_place(node.value, state)
                    state.places[node.target.id] = (
                        binding_place or self._root_place(node.target.id)
                        if (
                            self._owner(type_name)
                            and not self._contains_borrow(type_name)
                            and not self._is_borrowed(type_name)
                        )
                        else self._root_place(node.target.id)
                    )
                if self._owner(type_name):
                    state.statuses[node.target.id] = "available"
                continue
            if isinstance(node, ast.Assign):
                predicted_type = self._expr_type(node.value)
                provenances = self._borrow_provenances(
                    node.value,
                    predicted_type,
                    state,
                )
                value_type = self._check_expr(node.value, state)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.binding_kinds_by_name.setdefault(target.id, "binding")
                        target_type = self.env.get(target.id)
                        target_place = (
                            state.places.get(target.id)
                            or self._root_place(target.id)
                        )
                        state.places[target.id] = target_place
                        if target_type and self._owner(target_type):
                            self._consume_expr(
                                node.value,
                                state,
                                expected=target_type,
                            )
                            state.statuses[target.id] = "available"
                        if target_type and self._contains_borrow(target_type):
                            self._register_borrows(
                                state,
                                target.id,
                                self._extend_provenance(
                                    provenances,
                                    f"assign({target.id}:{target_type})",
                                ),
                                place=state.places.get(target.id),
                            )
                    elif isinstance(target, (ast.Attribute, ast.Subscript)):
                        self._check_mutation_expr(target, state)
                        target_root = self._root_name(target)
                        target_type = self._expr_type(target)
                        if (
                            target_root is not None
                            and self._contains_borrow(target_type or value_type)
                        ):
                            if (
                                target_root in self.parameters
                                and any(
                                    not self._is_parameter_place(item.backing_place)
                                    for item in provenances
                                )
                            ):
                                provenance = next(
                                    item
                                    for item in provenances
                                    if not self._is_parameter_place(item.backing_place)
                                )
                                self._contained_borrow_error(
                                    "ContainedBorrowStoredInEscapingOwner",
                                    container_type=self._storage_type(target_root),
                                    provenance=provenance,
                                    path=f"store({ast.unparse(target)})",
                                )
                            self._register_borrows(
                                state,
                                target_root,
                                self._extend_provenance(
                                    provenances,
                                    f"store({ast.unparse(target)})",
                                ),
                                place=self._place_for_expr(target, state),
                            )
                continue
            if isinstance(node, ast.Expr):
                self._check_expr(node.value, state)
                continue
            if isinstance(node, ast.Contract):
                self._check_expr(node.condition, state)
                continue
            if isinstance(node, ast.Break):
                state.break_paths = (
                    *state.break_paths,
                    self._snapshot_state(state),
                )
                state.terminal = True
                break
            if isinstance(node, ast.Continue):
                state.backedge_paths = (
                    *state.backedge_paths,
                    self._snapshot_state(state),
                )
                state.terminal = True
                break
            if isinstance(node, ast.Return):
                result_type = self._type_name(self.current.returns if self.current else None)
                provenances = self._borrow_provenances(
                    node.value,
                    result_type,
                    state,
                )
                value_type = self._check_expr(node.value, state, expected=result_type)
                if self._contains_borrow(result_type):
                    escaping = next(
                        (
                            item
                            for item in provenances
                            if not self._is_parameter_place(item.backing_place)
                        ),
                        None,
                    )
                    if escaping is not None:
                        self._contained_borrow_error(
                            (
                                f"EscapedView: {escaping.backing_owner}"
                                if self._is_borrowed(result_type)
                                else "EscapedContainedBorrow"
                            ),
                            container_type=result_type,
                            provenance=escaping,
                            path=f"return({result_type})",
                        )
                    if not provenances:
                        root = self._root_name(self._borrow_source(node.value))
                        place = self._place_for_expr(self._borrow_source(node.value), state)
                        if root is not None and (
                            place is None or not self._is_parameter_place(place)
                        ):
                            self._error("EscapedView", root)
                self._borrow_result(node.value, value_type or result_type, state)
                if self._owner(result_type):
                    target_place = self._place_for_expr(node.value, state)
                    if target_place is not None:
                        for name, provenances in tuple(state.borrows.items()):
                            if (
                                self._is_borrowed(self.env.get(name))
                                and any(
                                    overlap_relation(
                                        target_place,
                                        provenance.backing_place,
                                    )
                                    is not OverlapRelation.DISJOINT
                                    for provenance in provenances
                                )
                            ):
                                state.borrows.pop(name, None)
                                state.borrow_places.pop(name, None)
                    self._consume_expr(
                        node.value,
                        state,
                        expected=result_type,
                        allow_projected_owner_move=True,
                    )
                state.terminal = True
                break
            if isinstance(node, ast.If):
                self._check_expr(node.test, state)
                before_statuses = set(state.statuses)
                before_borrows = set(state.borrows)
                before_borrow_places = set(state.borrow_places)
                before_places = set(state.places)
                then_state = self._check_statements(node.body, state.clone())
                else_state = self._check_statements(node.orelse, state.clone())
                for branch in (then_state, else_state):
                    for name in set(branch.statuses) - before_statuses:
                        branch.statuses.pop(name, None)
                    for name in set(branch.borrows) - before_borrows:
                        branch.borrows.pop(name, None)
                    for name in set(branch.borrow_places) - before_borrow_places:
                        branch.borrow_places.pop(name, None)
                    for name in set(branch.places) - before_places:
                        branch.places.pop(name, None)
                state = self._merge(state, (then_state, else_state))
                continue
            if isinstance(node, ast.While):
                loop_entry = state.clone()
                test_state = loop_entry.clone()
                self._check_expr(node.test, test_state)
                self._require_loop_backedge_stable(loop_entry, test_state)
                body_state = self._check_statements(
                    node.body,
                    test_state.clone(),
                )
                assignment_names = (
                    self._loop_assignment_names(
                        node.body,
                        self.binding_kinds,
                    )
                    | self._loop_implicit_cleanup_names(node.body)
                )
                exit_candidates = [test_state]
                backedge_candidates = []
                if not body_state.terminal:
                    backedge_candidates.append(body_state)
                backedge_candidates.extend(body_state.backedge_paths)
                for candidate in backedge_candidates:
                    self._require_loop_backedge_stable(
                        test_state,
                        candidate,
                        assignment_names=assignment_names,
                    )
                exit_candidates.extend(
                    self._loop_exit_state(
                        test_state,
                        path,
                        assignment_names=assignment_names,
                    )
                    for path in body_state.break_paths
                )
                state = self._join_loop_states(
                    test_state,
                    tuple(exit_candidates),
                )
                continue
            if isinstance(node, ast.For):
                iterable_type = self._check_expr(node.iter, state)
                before_loop = state.clone()
                loop_state = before_loop.clone()
                if isinstance(node.target, ast.Name):
                    shape = collection_shape(iterable_type)
                    target_type = (
                        shape.element_type
                        if shape is not None
                        else "TextView"
                        if iterable_type == "FileLines"
                        else "Inferred"
                    )
                    self.env[node.target.id] = target_type
                    loop_state.places[node.target.id] = self._root_place(node.target.id)
                    if (
                        not iterable_type.startswith("Borrow[")
                        and target_type != "Inferred"
                        and self._owner(target_type)
                    ):
                        loop_state.statuses[node.target.id] = "available"
                body_state = self._check_statements(node.body, loop_state)
                assignment_names = (
                    self._loop_assignment_names(
                        node.body,
                        self.binding_kinds,
                    )
                    | self._loop_implicit_cleanup_names(node.body)
                )
                exit_candidates = [before_loop]
                backedge_candidates = []
                if not body_state.terminal:
                    backedge_candidates.append(body_state)
                backedge_candidates.extend(body_state.backedge_paths)
                for candidate in backedge_candidates:
                    self._require_loop_backedge_stable(
                        before_loop,
                        candidate,
                        assignment_names=assignment_names,
                    )
                exit_candidates.extend(
                    self._loop_exit_state(
                        before_loop,
                        path,
                        assignment_names=assignment_names,
                    )
                    for path in body_state.break_paths
                )
                state = self._join_loop_states(
                    before_loop,
                    tuple(exit_candidates),
                )
                continue
            if isinstance(node, ast.Match):
                subject_type = self._check_expr(node.subject, state)
                if isinstance(node.subject, ast.Name) and self._owner(subject_type):
                    self._consume(node.subject.id, state)
                branches = []
                before_statuses = set(state.statuses)
                before_borrows = set(state.borrows)
                before_borrow_places = set(state.borrow_places)
                before_places = set(state.places)
                for case in node.cases:
                    branch = state.clone()
                    self._check_statements(case.body, branch)
                    for name in set(branch.statuses) - before_statuses:
                        branch.statuses.pop(name, None)
                    for name in set(branch.borrows) - before_borrows:
                        branch.borrows.pop(name, None)
                    for name in set(branch.places) - before_places:
                        branch.places.pop(name, None)
                    for name in set(branch.borrow_places) - before_borrow_places:
                        branch.borrow_places.pop(name, None)
                    branches.append(branch)
                if branches:
                    state = self._merge(state, tuple(branches))
                continue
        return state

    def _validate_function_end(self, state: _OwnershipState) -> None:
        for name, status in sorted(state.statuses.items()):
            if (
                status == "ambiguous"
                and self.binding_kinds_by_name.get(name) != "binding"
            ):
                self._error("OwnershipAmbiguity", name)
    def check(self) -> None:
        for function in self.functions.values():
            self.current = function
            self.binding_kinds_by_name = {}
            self.env = {
                argument.arg: self._type_name(argument.annotation)
                for argument in function.args.args
            }
            self.parameters = set(self.env)
            state = _OwnershipState(
                {
                    name: "available"
                    for name, type_name in self.env.items()
                    if self._owner(type_name)
                },
                {},
                {
                    name: self._root_place(name)
                    for name in self.env
                },
            )
            final_state = self._check_statements(function.body, state)
            self._validate_function_end(final_state)
