"""General type descriptors, layout validation, drop plans, and Representation IR v1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable

from merlo.structured_hir_v2 import (
    HIRNode,
    HIRTypeDecl,
    SourceSpan,
    StructuredHIRProgram,
)
from merlo.ffi import pointer_type
from merlo.type_parser import generic_parts, parse_type


REPRESENTATION_IR_SCHEMA_VERSION = 2
REPRESENTATION_IR_CONTRACT = "merlo.representation-ir.v2"
MAX_U64 = (1 << 64) - 1

def _type_leaf(type_name: str) -> str:
    return type_name.rsplit("__", 1)[-1].rsplit(".", 1)[-1]


class RepresentationCompileError(ValueError):
    """Typed representation/layout/ownership failure."""


@dataclass(frozen=True)
class TypeDescriptor:
    name: str
    kind: str
    size: int
    alignment: int
    abi_class: str
    copy_class: str
    move_class: str
    drop_class: str
    inline_dependencies: tuple[str, ...]
    indirect_dependencies: tuple[str, ...]
    source_type_identity: str
    fields: tuple[tuple[str, str, int], ...] = ()
    variants: tuple[tuple[str, str | None, int], ...] = ()
    element_type: str | None = None
    payload_type: str | None = None
    key_type: str | None = None
    value_type: str | None = None
    length: int | None = None
    invariants: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": type(self).__name__,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "alignment": self.alignment,
            "abi_class": self.abi_class,
            "copy_class": self.copy_class,
            "move_class": self.move_class,
            "drop_class": self.drop_class,
            "inline_dependencies": list(self.inline_dependencies),
            "indirect_dependencies": list(self.indirect_dependencies),
            "source_type_identity": self.source_type_identity,
            "fields": [
                {"name": name, "type": type_name, "offset": offset}
                for name, type_name, offset in self.fields
            ],
            "variants": [
                {"name": name, "payload_type": payload, "tag": tag}
                for name, payload, tag in self.variants
            ],
            "element_type": self.element_type,
            "payload_type": self.payload_type,
            "key_type": self.key_type,
            "value_type": self.value_type,
            "length": self.length,
            "invariants": [
                {"function": function, "line": line}
                for function, line in self.invariants
            ],
        }


@dataclass(frozen=True)
class ScalarDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class RecordDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class EnumDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class VecDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class ArrayDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class SliceDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class CallbackDesc(TypeDescriptor):
    pass

@dataclass(frozen=True)
class MapDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class BoxDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class BytesDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class TextDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class BorrowDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class StoragePolicy:
    storage: str
    copy: str
    move: str
    drop: str
    partial_initialization: str
    shared_ownership: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage": self.storage,
            "copy": self.copy,
            "move": self.move,
            "drop": self.drop,
            "partial_initialization": self.partial_initialization,
            "shared_ownership": self.shared_ownership,
        }




@dataclass(frozen=True)
class DropPlan:
    type_name: str
    action: str
    children: tuple["DropPlan", ...] = ()
    field_name: str | None = None
    variant_name: str | None = None
    depth_limited_by_program: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "action": self.action,
            "field_name": self.field_name,
            "variant_name": self.variant_name,
            "depth_limited_by_program": self.depth_limited_by_program,
            "children": [item.to_dict() for item in self.children],
        }



@dataclass(frozen=True)
class RIROperation:
    id: str
    op: str
    type_name: str | None
    source: SourceSpan
    symbol_id: str | None
    revision_id: str
    ownership_provenance: str
    effects: tuple[str, ...]
    attributes: tuple[tuple[str, Any], ...] = ()
    children: tuple["RIROperation", ...] = ()

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)

    def walk(self) -> Iterable["RIROperation"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "type": self.type_name,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "ownership_provenance": self.ownership_provenance,
            "effects": list(self.effects),
            "attributes": dict(self.attributes),
            "children": [item.to_dict() for item in self.children],
        }


@dataclass(frozen=True)
class RIRFunction:
    name: str
    symbol_id: str
    revision_id: str
    parameters: tuple[tuple[str, str, str], ...]
    return_type: str
    effects: tuple[str, ...]
    operations: tuple[RIROperation, ...]
    source: SourceSpan

    def walk(self) -> Iterable[RIROperation]:
        for operation in self.operations:
            yield from operation.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "parameters": [
                {"name": name, "type": type_name, "ownership": ownership}
                for name, type_name, ownership in self.parameters
            ],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "source": self.source.to_dict(),
            "operations": [item.to_dict() for item in self.operations],
        }


@dataclass(frozen=True)
class RepresentationProgram:
    source_hir_digest: str
    source_sha256: str
    entry_function: str
    descriptors: tuple[TypeDescriptor, ...]
    drop_plans: tuple[DropPlan, ...]
    functions: tuple[RIRFunction, ...]
    schema_version: int = REPRESENTATION_IR_SCHEMA_VERSION
    contract: str = REPRESENTATION_IR_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != REPRESENTATION_IR_SCHEMA_VERSION:
            raise ValueError("Representation IR schema version drift")
        names = [item.name for item in self.descriptors]
        if len(names) != len(set(names)):
            raise ValueError("duplicate type descriptor")
        function_names = [item.name for item in self.functions]
        if len(function_names) != len(set(function_names)):
            raise ValueError("duplicate Representation IR function")
        if self.entry_function not in set(function_names):
            raise ValueError("missing Representation IR entry function")
        if any(item.op in {"json_parse", "json_tokenize", "json_token_checksum", "json_build_ast"} for function in self.functions for item in function.walk()):
            raise ValueError("domain-level intrinsic escaped into Representation IR")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def descriptor(self, name: str) -> TypeDescriptor:
        return next(item for item in self.descriptors if item.name == name)

    def function(self, name: str) -> RIRFunction:
        return next(item for item in self.functions if item.name == name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "source_hir_digest": self.source_hir_digest,
            "source_sha256": self.source_sha256,
            "entry_function": self.entry_function,
            "descriptors": [item.to_dict() for item in self.descriptors],
            "drop_plans": [item.to_dict() for item in self.drop_plans],
            "functions": [item.to_dict() for item in self.functions],
            "invariants": {
                "general_descriptors": True,
                "inline_cycles_rejected": True,
                "owning_indirection_cycles_allowed": True,
                "drop_plans_type_directed": True,
                "symbol_revision_source_preserved": True,
                "ownership_provenance_preserved": True,
                "domain_intrinsics_absent": True,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class LayoutValidation:
    accepted: bool
    inline_graph: tuple[tuple[str, tuple[str, ...]], ...]
    minimal_cycle_path: tuple[str, ...] = ()
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "inline_graph": {name: list(edges) for name, edges in self.inline_graph},
            "minimal_cycle_path": list(self.minimal_cycle_path),
            "diagnostic": self.diagnostic,
        }


_SCALARS: dict[str, tuple[int, int]] = {
    "Unit": (0, 1),
    "Bool": (1, 1),
    "Byte": (1, 1),
    "Int8": (1, 1),
    "UInt8": (1, 1),
    "Int16": (2, 2),
    "UInt16": (2, 2),
    "Int32": (4, 4),
    "UInt32": (4, 4),
    "Int64": (8, 8),
    "UInt64": (8, 8),
    "Float32": (4, 4),
    "Float64": (8, 8),
}


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"

def _generic(type_name: str) -> tuple[str, str] | None:
    try:
        parsed = parse_type(type_name)
    except ValueError:
        return None
    if not parsed.args:
        return None
    return parsed.name, ",".join(item.canonical for item in parsed.args)


def _map_types(type_name: str) -> tuple[str, str] | None:
    parts = generic_parts(type_name, "Map", arity=2)
    return parts if parts is not None else None  # type: ignore[return-value]


def _array_parts(type_name: str) -> tuple[str, int] | None:
    parts = generic_parts(type_name, "Array", arity=2)
    if parts is None:
        return None
    try:
        length = int(parts[1])
    except ValueError:
        return None
    if length < 0 or length > MAX_U64:
        return None
    return parts[0], length


def _callback_parts(type_name: str) -> tuple[tuple[str, ...], str] | None:
    parts = generic_parts(type_name, "Fn")
    if parts is None or len(parts) < 2:
        return None
    return parts[:-1], parts[-1]

def _referenced_type(type_name: str) -> str | None:
    if pointer_type(type_name) is not None:
        return None
    if _map_types(type_name) is not None or _callback_parts(type_name) is not None:
        return None
    array = _array_parts(type_name)
    if array is not None:
        return array[0]
    generic = _generic(type_name)
    if generic:
        return generic[1]
    if type_name in _SCALARS or type_name in {
        "Text",
        "TextBuilder",
        "Bytes",
        "BytesView",
        "TextView",
        "Path",
    }:
        return None
    return type_name


def _is_indirection(type_name: str) -> bool:
    if pointer_type(type_name) is not None:
        return True
    if _map_types(type_name) is not None or _callback_parts(type_name) is not None:
        return True
    generic = _generic(type_name)
    return bool(generic and generic[0] in {"Vec", "Box", "Borrow", "Slice"})
def validate_recursive_layouts(types: Iterable[HIRTypeDecl]) -> LayoutValidation:
    declarations = {item.name: item for item in types}
    graph: dict[str, list[str]] = {name: [] for name in declarations}
    for declaration in declarations.values():
        references = (
            [field.type_name for field in declaration.fields]
            if declaration.kind == "record"
            else [variant.payload_type for variant in declaration.variants if variant.payload_type]
        )
        for type_name in references:
            assert type_name is not None
            referenced = _referenced_type(type_name)
            if referenced in declarations and not _is_indirection(type_name):
                graph[declaration.name].append(referenced)
    best_cycle: tuple[str, ...] | None = None
    for start in sorted(graph):
        queue: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
        while queue:
            current, path = queue.pop(0)
            for successor in sorted(graph[current]):
                if successor == start:
                    cycle = path + (start,)
                    if best_cycle is None or (len(cycle), cycle) < (len(best_cycle), best_cycle):
                        best_cycle = cycle
                    queue.clear()
                    break
                if successor not in path:
                    queue.append((successor, path + (successor,)))
    ordered_graph = tuple((name, tuple(sorted(edges))) for name, edges in sorted(graph.items()))
    if best_cycle:
        text = " -> ".join(best_cycle)
        return LayoutValidation(False, ordered_graph, best_cycle, f"InlineRecursiveLayout: {text}; add Box or Vec indirection")
    return LayoutValidation(True, ordered_graph)


def require_valid_recursive_layouts(types: Iterable[HIRTypeDecl]) -> LayoutValidation:
    validation = validate_recursive_layouts(types)
    if not validation.accepted:
        raise RepresentationCompileError(validation.diagnostic or "invalid recursive layout")
    return validation


class _DescriptorBuilder:
    def __init__(self, hir: StructuredHIRProgram) -> None:
        self.hir = hir
        self.declarations = {item.name: item for item in hir.types}
        self.descriptors: dict[str, TypeDescriptor] = {}
        for name, (size, alignment) in _SCALARS.items():
            self.descriptors[name] = ScalarDesc(
                name, "scalar", size, alignment, "void" if name == "Unit" else "scalar", "trivial", "copy", "trivial", (), (), _stable_id("type", "builtin", name)
            )
        self.descriptors["Bytes"] = BytesDesc(
            "Bytes", "bytes", 24, 8, "aggregate", "forbidden", "bitwise_then_invalidate", "owner_free", (), (), _stable_id("type", "builtin", "Bytes")
        )
        self.descriptors["BytesView"] = BorrowDesc(
            "BytesView", "borrow", 16, 8, "aggregate", "trivial", "copy", "trivial", (), ("Bytes",), _stable_id("type", "builtin", "BytesView"), payload_type="Bytes"
        )
        self.descriptors["TextView"] = BorrowDesc(
            "TextView", "borrow", 16, 8, "aggregate", "trivial", "copy", "trivial", (), ("Text",), _stable_id("type", "builtin", "TextView"), payload_type="Text"
        )
        self.descriptors["Text"] = TextDesc(
            "Text", "text", 16, 8, "aggregate", "forbidden", "bitwise_then_invalidate", "owner_free", (), (), _stable_id("type", "builtin", "Text")
        )
        self.descriptors["TextBuilder"] = RecordDesc(
            "TextBuilder", "record", 32, 8, "aggregate", "forbidden", "bitwise_then_invalidate", "builder_free", (), (), _stable_id("type", "builtin", "TextBuilder")
        )
        self.descriptors["Path"] = TextDesc(
            "Path", "text", 16, 8, "aggregate", "forbidden", "bitwise_then_invalidate", "owner_free", (), (), _stable_id("type", "builtin", "Path")
        )

    def build(self) -> tuple[TypeDescriptor, ...]:
        for declaration in self.hir.types:
            self._nominal(declaration.name)
        referenced = {
            type_name
            for declaration in self.hir.types
            for type_name in (
                [field.type_name for field in declaration.fields]
                + [variant.payload_type for variant in declaration.variants if variant.payload_type]
            )
        }
        referenced.update(
            type_name
            for function in self.hir.functions
            for type_name in (
                [parameter.type_name for parameter in function.parameters]
                + [function.return_type]
                + [node.type_name for node in function.walk() if node.type_name]
            )
            if type_name != "Inferred" and "Inferred]" not in type_name
        )
        for type_name in sorted(referenced):
            self.get(type_name)
        completed: set[str] = set()
        for declaration in self.hir.types:
            self._finalize_nominal(declaration.name, completed)
        return tuple(sorted(self.descriptors.values(), key=lambda item: item.name))

    def get(self, type_name: str) -> TypeDescriptor:
        if type_name in self.descriptors:
            return self.descriptors[type_name]
        pointer = pointer_type(type_name)
        if pointer is not None:
            descriptor = BorrowDesc(
                type_name,
                "raw_pointer",
                8,
                8,
                "pointer",
                "trivial",
                "copy",
                "trivial",
                (),
                (),
                _stable_id("type", "raw_pointer", pointer.pointee, pointer.mutable),
                payload_type=pointer.pointee,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if type_name == "FileReader":
            descriptor = RecordDesc(
                "FileReader", "file_reader", 64, 8, "aggregate", "forbidden",
                "bitwise_then_invalidate", "file_close", (), (),
                _stable_id("type", "builtin", "FileReader"),
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if type_name == "FileWriter":
            descriptor = RecordDesc(
                "FileWriter", "file_writer", 16, 8, "aggregate", "forbidden",
                "bitwise_then_invalidate", "file_close", (), (),
                _stable_id("type", "builtin", "FileWriter"),
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if type_name == "FileLines":
            self.get("FileReader")
            descriptor = BorrowDesc(
                "FileLines", "file_lines", 16, 8, "aggregate", "trivial", "copy",
                "trivial", (), ("FileReader",),
                _stable_id("type", "builtin", "FileLines"),
                payload_type="FileReader",
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        array = _array_parts(type_name)
        if array is not None:
            element_type, length = array
            element = self.get(element_type)
            if element.size and length > MAX_U64 // element.size:
                raise RepresentationCompileError(
                    f"array layout overflow: {type_name}"
                )
            descriptor = ArrayDesc(
                type_name,
                "array",
                element.size * length,
                element.alignment,
                "aggregate",
                element.copy_class,
                "bitwise_then_invalidate"
                if element.copy_class == "forbidden"
                else "copy",
                "array_elements"
                if element.drop_class != "trivial"
                else "trivial",
                (element_type,),
                (),
                _stable_id(
                    "type",
                    "array",
                    element.source_type_identity,
                    length,
                ),
                element_type=element_type,
                length=length,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        callback = _callback_parts(type_name)
        if callback is not None:
            parameter_types, return_type = callback
            dependencies = tuple(parameter_types) + (return_type,)
            resolved = tuple(self.get(item) for item in dependencies)
            descriptor = CallbackDesc(
                type_name,
                "closure",
                32,
                8,
                "aggregate",
                "refcounted",
                "move_then_invalidate",
                "closure_environment",
                (),
                dependencies,
                _stable_id(
                    "type",
                    "callback",
                    [item.source_type_identity for item in resolved],
                ),
                payload_type=return_type,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if type_name.startswith("Shared["):
            raise RepresentationCompileError(
                f"SharedOwnershipUnsupported: {type_name}"
            )
        map_types = _map_types(type_name)
        if map_types is not None:
            key_type, value_type = map_types
            if key_type != "Text" or value_type not in _SCALARS:
                raise RepresentationCompileError(
                    f"unsupported Map specialization {type_name}; alpha Map "
                    "requires Text keys and scalar values"
                )
            key = self.get(key_type)
            value = self.get(value_type)
            descriptor = MapDesc(
                type_name, "map", 40, 8, "aggregate", "forbidden",
                "bitwise_then_invalidate", "map_owned_keys_then_buffers",
                (), (key_type, value_type),
                _stable_id("type", "map", key.source_type_identity, value.source_type_identity),
                key_type=key_type, value_type=value_type,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        generic = _generic(type_name)
        if generic:
            base, argument = generic
            result_parts = generic_parts(type_name, "Result", arity=2)
            if result_parts is not None:
                ok_type, err_type = result_parts
                ok = self.get(ok_type)
                err = self.get(err_type)
                canonical_ok = ok.name
                canonical_err = err.name
                descriptor = EnumDesc(
                    type_name, "enum", max(ok.size, err.size) + 8,
                    max(ok.alignment, err.alignment), "aggregate", "forbidden",
                    "bitwise_then_invalidate", "tag_switch",
                    tuple(sorted({canonical_ok, canonical_err})), (),
                    _stable_id("type", "result", ok.source_type_identity, err.source_type_identity),
                    variants=(("Ok", canonical_ok, 0), ("Err", canonical_err, 1)),
                )
                self.descriptors[type_name] = descriptor
                return descriptor
            if base == "Option":
                payload = self.get(argument)
                descriptor = EnumDesc(
                    type_name, "enum", payload.size + 8,
                    max(4, payload.alignment), "aggregate", "forbidden",
                    "bitwise_then_invalidate", "tag_switch",
                    (argument,), (),
                    _stable_id("type", "option", payload.source_type_identity),
                    variants=(("NoneValue", None, 0), ("Some", argument, 1)),
                )
                self.descriptors[type_name] = descriptor
                return descriptor
            if base == "Slice":
                payload = self.get(argument)
                descriptor = SliceDesc(
                    type_name,
                    "slice",
                    16,
                    8,
                    "aggregate",
                    "trivial",
                    "copy",
                    "trivial",
                    (),
                    (argument,),
                    _stable_id("type", "slice", payload.source_type_identity),
                    element_type=argument,
                    payload_type=argument,
                )
                self.descriptors[type_name] = descriptor
                return descriptor
            payload = self.get(argument)
            if base == "Vec":
                descriptor = VecDesc(
                    type_name, "vec", 32, 8, "aggregate", "forbidden",
                    "bitwise_then_invalidate", "vec_elements_then_buffer", (),
                    (argument,), _stable_id("type", "vec", payload.source_type_identity),
                    element_type=argument,
                )
            elif base == "Box":
                descriptor = BoxDesc(
                    type_name, "box", 8, 8, "pointer", "forbidden",
                    "pointer_then_invalidate", "payload_then_free", (),
                    (argument,), _stable_id("type", "box", payload.source_type_identity),
                    payload_type=argument,
                )
            elif base == "Borrow":
                descriptor = BorrowDesc(
                    type_name, "borrow", 8, 8, "pointer", "trivial", "copy",
                    "trivial", (), (argument,),
                    _stable_id("type", "borrow", payload.source_type_identity),
                    payload_type=argument,
                )
            else:
                raise RepresentationCompileError(f"unsupported representation constructor: {base}")
            self.descriptors[type_name] = descriptor
            return descriptor
        if type_name in self.declarations:
            return self._nominal(type_name)
        aliases = [
            name for name in self.declarations
            if _type_leaf(name) == type_name
        ]
        if len(aliases) == 1:
            return self.get(aliases[0])
        raise RepresentationCompileError(f"unknown representation type: {type_name}")

    def _finalize_nominal(self, name: str, completed: set[str]) -> TypeDescriptor:
        if name in completed:
            return self.descriptors[name]
        declaration = self.declarations[name]
        references = (
            [field.type_name for field in declaration.fields]
            if declaration.kind == "record"
            else [variant.payload_type for variant in declaration.variants if variant.payload_type]
        )
        for type_name in references:
            assert type_name is not None
            referenced = _referenced_type(type_name)
            if referenced in self.declarations and not _is_indirection(type_name):
                self._finalize_nominal(referenced, completed)
        if declaration.kind == "record":
            offset = 0
            alignment = 1
            fields = []
            inline = []
            indirect = []
            trivial = True
            for field in declaration.fields:
                field_descriptor = self.get(field.type_name)
                alignment = max(alignment, field_descriptor.alignment)
                offset = _align(offset, field_descriptor.alignment)
                fields.append((field.name, field.type_name, offset))
                offset += field_descriptor.size
                if _is_indirection(field.type_name):
                    indirect.extend(field_descriptor.indirect_dependencies)
                else:
                    referenced = _referenced_type(field.type_name)
                    if referenced:
                        inline.append(referenced)
                trivial = trivial and field_descriptor.drop_class == "trivial"
            descriptor: TypeDescriptor = RecordDesc(
                name,
                "record",
                _align(offset, alignment),
                alignment,
                "aggregate",
                "trivial" if trivial else "forbidden",
                "copy" if trivial else "bitwise_then_invalidate",
                "trivial" if trivial else "fieldwise",
                tuple(sorted(set(inline))),
                tuple(sorted(set(indirect))),
                declaration.symbol_id,
                fields=tuple(fields),
                invariants=tuple(
                    (
                        invariant.function_name,
                        invariant.source.line,
                    )
                    for invariant in declaration.invariants
                ),
            )
        else:
            maximum_size = 0
            maximum_alignment = 1
            variants = []
            inline = []
            indirect = []
            trivial = True
            for variant in declaration.variants:
                if variant.payload_type is not None:
                    payload_descriptor = self.get(variant.payload_type)
                    maximum_size = max(maximum_size, payload_descriptor.size)
                    maximum_alignment = max(maximum_alignment, payload_descriptor.alignment)
                    if _is_indirection(variant.payload_type):
                        indirect.extend(payload_descriptor.indirect_dependencies)
                    else:
                        referenced = _referenced_type(variant.payload_type)
                        if referenced:
                            inline.append(referenced)
                    trivial = trivial and payload_descriptor.drop_class == "trivial"
                variants.append((variant.name, variant.payload_type, variant.tag))
            payload_offset = _align(4, maximum_alignment)
            descriptor = EnumDesc(
                name,
                "enum",
                _align(payload_offset + maximum_size, maximum_alignment),
                maximum_alignment,
                "aggregate",
                "trivial" if trivial else "forbidden",
                "copy" if trivial else "bitwise_then_invalidate",
                "trivial" if trivial else "tag_switch",
                tuple(sorted(set(inline))),
                tuple(sorted(set(indirect))),
                declaration.symbol_id,
                variants=tuple(variants),
            )
        self.descriptors[name] = descriptor
        completed.add(name)
        return descriptor

    def _nominal(self, name: str) -> TypeDescriptor:
        if name in self.descriptors:
            return self.descriptors[name]
        declaration = self.declarations[name]
        placeholder = (
            RecordDesc(name, "record", 0, 1, "aggregate", "forbidden", "bitwise_then_invalidate", "fieldwise", (), (), declaration.symbol_id)
            if declaration.kind == "record"
            else EnumDesc(name, "enum", 0, 1, "aggregate", "forbidden", "bitwise_then_invalidate", "tag_switch", (), (), declaration.symbol_id)
        )
        self.descriptors[name] = placeholder
        if declaration.kind == "record":
            offset = 0
            alignment = 1
            fields = []
            inline = []
            indirect = []
            trivial = True
            for field in declaration.fields:
                descriptor = self.get(field.type_name)
                alignment = max(alignment, descriptor.alignment)
                offset = _align(offset, descriptor.alignment)
                fields.append((field.name, field.type_name, offset))
                offset += descriptor.size
                if _is_indirection(field.type_name):
                    indirect.extend(descriptor.indirect_dependencies)
                else:
                    referenced = _referenced_type(field.type_name)
                    if referenced:
                        inline.append(referenced)
                trivial = trivial and descriptor.drop_class == "trivial"
            size = _align(offset, alignment)
            descriptor = RecordDesc(
                name,
                "record",
                size,
                alignment,
                "aggregate",
                "trivial" if trivial else "forbidden",
                "copy" if trivial else "bitwise_then_invalidate",
                "trivial" if trivial else "fieldwise",
                tuple(sorted(set(inline))),
                tuple(sorted(set(indirect))),
                declaration.symbol_id,
                fields=tuple(fields),
            )
        else:
            maximum_size = 0
            maximum_alignment = 1
            variants = []
            inline = []
            indirect = []
            trivial = True
            for variant in declaration.variants:
                if variant.payload_type is not None:
                    payload = self.get(variant.payload_type)
                    maximum_size = max(maximum_size, payload.size)
                    maximum_alignment = max(maximum_alignment, payload.alignment)
                    if _is_indirection(variant.payload_type):
                        indirect.extend(payload.indirect_dependencies)
                    else:
                        referenced = _referenced_type(variant.payload_type)
                        if referenced:
                            inline.append(referenced)
                    trivial = trivial and payload.drop_class == "trivial"
                variants.append((variant.name, variant.payload_type, variant.tag))
            payload_offset = _align(4, maximum_alignment)
            descriptor = EnumDesc(
                name,
                "enum",
                _align(payload_offset + maximum_size, maximum_alignment),
                maximum_alignment,
                "aggregate",
                "trivial" if trivial else "forbidden",
                "copy" if trivial else "bitwise_then_invalidate",
                "trivial" if trivial else "tag_switch",
                tuple(sorted(set(inline))),
                tuple(sorted(set(indirect))),
                declaration.symbol_id,
                variants=tuple(variants),
            )
        self.descriptors[name] = descriptor
        return descriptor


def _align(value: int, alignment: int) -> int:
    remainder = value % alignment
    return value if remainder == 0 else value + alignment - remainder


def build_type_descriptors(hir: StructuredHIRProgram) -> tuple[TypeDescriptor, ...]:
    require_valid_recursive_layouts(hir.types)
    return _DescriptorBuilder(hir).build()


def storage_policy_matrix(
    descriptors: Iterable[TypeDescriptor],
) -> dict[str, StoragePolicy]:
    policies: dict[str, StoragePolicy] = {}
    for descriptor in descriptors:
        if descriptor.kind in {"borrow", "slice"}:
            storage = "borrowed_view"
        elif descriptor.copy_class == "forbidden":
            storage = "unique_owner"
        elif descriptor.abi_class == "aggregate":
            storage = "inline_value"
        else:
            storage = "inline_copy"
        partial_initialization = (
            "initialized_fields"
            if descriptor.kind == "record"
            else "active_variant"
            if descriptor.kind == "enum"
            else "initialized_elements"
            if descriptor.kind in {"array", "vec", "map"}
            else "all_or_nothing"
        )
        policies[descriptor.name] = StoragePolicy(
            storage,
            descriptor.copy_class,
            descriptor.move_class,
            descriptor.drop_class,
            partial_initialization,
        )
    return policies


def build_drop_plans(descriptors: Iterable[TypeDescriptor], *, recursion_limit: int = 128) -> tuple[DropPlan, ...]:
    table = {item.name: item for item in descriptors}

    def plan(type_name: str, visiting: tuple[str, ...] = ()) -> DropPlan:
        descriptor = table[type_name]
        if descriptor.drop_class == "trivial":
            return DropPlan(type_name, "trivial")
        if type_name in visiting:
            return DropPlan(type_name, "recursive_reference", depth_limited_by_program=recursion_limit)
        path = visiting + (type_name,)
        if descriptor.kind in {"text", "bytes"}:
            return DropPlan(type_name, "owner_free")
        if descriptor.kind == "record":
            children = tuple(
                replace(plan(field_type, path), field_name=field_name)
                for field_name, field_type, _ in descriptor.fields
                if table[field_type].drop_class != "trivial"
            )
            return DropPlan(type_name, "record_fieldwise", children)
        if descriptor.kind == "enum":
            children = tuple(
                replace(plan(payload, path), variant_name=variant)
                for variant, payload, _ in descriptor.variants
                if payload is not None and table[payload].drop_class != "trivial"
            )
            return DropPlan(type_name, "enum_active_payload", children)
        if descriptor.kind == "array":
            assert descriptor.element_type is not None
            return DropPlan(
                type_name,
                "array_elements",
                (plan(descriptor.element_type, path),),
            )
        if descriptor.kind == "vec":
            assert descriptor.element_type is not None
            return DropPlan(type_name, "vec_initialized_elements_then_buffer", (plan(descriptor.element_type, path),))
        if descriptor.kind == "box":
            assert descriptor.payload_type is not None
            return DropPlan(type_name, "box_payload_then_free", (plan(descriptor.payload_type, path),))
        if descriptor.kind == "map":
            assert descriptor.key_type is not None
            return DropPlan(
                type_name,
                "map_owned_keys_then_buffers",
                (replace(plan(descriptor.key_type, path), field_name="key"),),
            )
        if descriptor.name == "TextBuilder":
            return DropPlan(type_name, "builder_buffer_free")
        return DropPlan(type_name, descriptor.drop_class)

    return tuple(plan(item.name) for item in sorted(table.values(), key=lambda item: item.name))


_HIR_TO_RIR = {
    "TypedHole": "typed_hole",
    "RecordConstruct": "construct_record",
    "FieldAccess": "get_field",
    "SetField": "set_field",
    "EnumConstruct": "construct_enum",
    "EnumTag": "read_enum_tag",
    "Match": "match_enum",
    "VecOperation": "vec_operation",
    "CollectionOperation": "collection_operation",
    "ImplicitCallable": "implicit_callable",
    "BoxOperation": "box_operation",
    "BytesTextOperation": "bytes_text_operation",
    "NumericIntrinsic": "numeric_intrinsic",
    "ScalarCast": "scalar_cast",
    "ArrayLiteral": "array_literal",
    "MapOperation": "map_operation",
    "FileOpen": "file_open_read",
    "FileRead": "file_read",
    "FileLines": "file_lines",
    "ResultPropagation": "try_result",
    "AugAssign": "aug_assign",
    "DirectCall": "call",
    "ForeignCall": "foreign_call",
    "UnsafeBlock": "unsafe_block",
    "Continue": "continue",
    "Break": "break",
    "Pass": "pass",
    "Return": "return",
    "LetBinding": "bind_value",
    "VarBinding": "bind_mutable",
    "TypedError": "typed_error",
    "DropValue": "drop_value",
    "Assign": "store_value",
    "If": "if",
    "While": "while",
    "For": "for",
    "CallbackCall": "callback_call",
    "ClosureCreate": "closure_create",
    "Literal": "const",
    "Name": "load_name",
    "Binary": "binary",
    "Boolean": "boolean",
    "Compare": "compare",
    "Unary": "unary",
    "Expression": "expression",
    "Then": "then",
    "Else": "else",
    "LoopBody": "loop_body",
    "MatchCase": "enum_case",
    "Index": "bounds_checked_index",
}


def _lower_operation(node: HIRNode) -> RIROperation:
    op = _HIR_TO_RIR.get(node.kind)
    if op is None:
        raise RepresentationCompileError(f"Structured HIR operation has no Representation IR lowering: {node.kind}")
    attributes = dict(node.attributes)
    if node.kind == "VecOperation":
        method = str(attributes.get("callee", "")).rsplit(".", 1)[-1]
        op = {
            "new": "vec_new",
            "push": "vec_push",
            "get": "vec_get",
            "get_mut": "vec_get_mut",
            "len": "vec_len",
            "capacity": "vec_capacity",
            "view": "vec_view",
        }.get(method, "vec_operation")
    elif node.kind == "BoxOperation":
        method = str(attributes.get("callee", "")).rsplit(".", 1)[-1]
        op = {"new": "box_new", "get": "box_get", "get_mut": "box_get_mut"}.get(method, "box_operation")
    elif node.kind == "MapOperation":
        method = attributes.get("map_operation")
        specialization = attributes.get("map_specialization")
        if _map_types(str(specialization)) is None:
            raise RepresentationCompileError(
                f"invalid Map specialization {specialization}"
            )
        operations = {
            "new": "map_new",
            "increment": "map_increment",
            "get": "map_get",
            "insert": "map_insert",
            "entries": "map_entries",
        }
        if method not in operations:
            raise RepresentationCompileError(f"MapOperation has no lowering: {method}")
        op = operations[method]
    provenance = {
        "owned": "unique_owner",
        "borrow": "shared_borrow",
        "borrow_mut": "unique_borrow",
        "value": "plain_value",
    }.get(node.ownership, node.ownership)
    revision = _stable_id("rev", "rir", node.revision_id, op, provenance)
    return RIROperation(
        _stable_id("rirop", node.id, op),
        op,
        node.type_name,
        node.source,
        node.symbol_id,
        revision,
        provenance,
        node.effects,
        node.attributes,
        tuple(_lower_operation(item) for item in node.children),
    )


def lower_structured_hir_to_rir(hir: StructuredHIRProgram) -> RepresentationProgram:
    descriptors = build_type_descriptors(hir)
    plans = build_drop_plans(descriptors)
    functions = tuple(
        RIRFunction(
            function.name,
            function.symbol_id,
            _stable_id("rev", "rir-function", function.revision_id),
            tuple((item.name, item.type_name, item.ownership) for item in function.parameters),
            function.return_type,
            function.effects,
            tuple(_lower_operation(item) for item in function.body),
            function.source,
        )
        for function in hir.functions
    )
    return RepresentationProgram(
        hir.digest,
        hir.source_sha256,
        hir.entry_function,
        descriptors,
        plans,
        functions,
    )


__all__ = [
    "BorrowDesc",
    "BoxDesc",
    "BytesDesc",
    "DropPlan",
    "EnumDesc",
    "LayoutValidation",
    "MAX_U64",
    "RIROperation",
    "MapDesc",
    "RIRFunction",
    "RecordDesc",
    "RepresentationCompileError",
    "RepresentationProgram",
    "REPRESENTATION_IR_CONTRACT",
    "REPRESENTATION_IR_SCHEMA_VERSION",
    "ScalarDesc",
    "TextDesc",
    "StoragePolicy",
    "TypeDescriptor",
    "VecDesc",
    "build_drop_plans",
    "build_type_descriptors",
    "lower_structured_hir_to_rir",
    "storage_policy_matrix",
    "require_valid_recursive_layouts",
    "validate_recursive_layouts",
]
