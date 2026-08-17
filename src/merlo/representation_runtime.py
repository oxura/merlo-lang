"""Descriptor-driven runtime and Structured HIR evaluator for representation values.

This module is an executable reference for the compiler layers. JSON behavior is
provided by the ordinary Merlo source program; the runtime only implements
primitive Bytes/Text, Vec, Box, records, enums, moves, and descriptor-directed
drop.
"""

from __future__ import annotations

import ast
import enum
import types
from dataclasses import dataclass
from typing import Any

from merlo.representation_ir import RepresentationProgram, TypeDescriptor
from merlo.structured_hir_v2 import StructuredHIRProgram, _preprocess


class RuntimeOwnershipError(RuntimeError):
    pass


class RuntimeBoundsError(RuntimeError):
    pass


@dataclass
class RuntimeMetrics:
    ast_nodes_allocated: int = 0
    ast_nodes_freed: int = 0
    text_allocations: int = 0
    text_frees: int = 0
    text_builder_allocations: int = 0
    text_builder_frees: int = 0
    vec_allocations: int = 0
    vec_frees: int = 0
    vec_reallocations: int = 0
    vec_growths: int = 0
    vec_initialized: int = 0
    vec_elements_dropped: int = 0
    box_allocations: int = 0
    box_frees: int = 0
    bytes_copied: int = 0
    drops: int = 0
    duplicate_owner_errors: int = 0
    use_after_move_errors: int = 0
    double_drop_errors: int = 0
    stale_view_errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)

    def balanced(self) -> bool:
        return (
            self.ast_nodes_allocated == self.ast_nodes_freed
            and self.text_allocations == self.text_frees
            and self.text_builder_allocations == self.text_builder_frees
            and self.vec_allocations == self.vec_frees
            and self.box_allocations == self.box_frees
        )


class RuntimeContext:
    def __init__(self, representation: RepresentationProgram) -> None:
        self.representation = representation
        self.descriptors = {item.name: item for item in representation.descriptors}
        self.metrics = RuntimeMetrics()
        self.record_classes: dict[str, type] = {}
        self.enum_classes: dict[str, type] = {}
        self._build_nominal_classes()

    def descriptor(self, type_name: str) -> TypeDescriptor:
        return self.descriptors[type_name]

    def _build_nominal_classes(self) -> None:
        for descriptor in self.descriptors.values():
            if descriptor.kind == "record" and descriptor.name != "TextBuilder":
                self.record_classes[descriptor.name] = self._record_class(descriptor)
        for descriptor in self.descriptors.values():
            if descriptor.kind == "enum":
                if all(payload is None for _, payload, _ in descriptor.variants):
                    values = {name: tag for name, _, tag in descriptor.variants}
                    self.enum_classes[descriptor.name] = enum.IntEnum(descriptor.name, values)
                else:
                    self.enum_classes[descriptor.name] = self._payload_enum_class(descriptor)

    def _record_class(self, descriptor: TypeDescriptor) -> type:
        context = self
        field_names = tuple(name for name, _, _ in descriptor.fields)
        field_types = tuple(type_name for _, type_name, _ in descriptor.fields)

        class RecordValue:
            __slots__ = field_names + ("__owner_state__",)
            __match_args__ = field_names
            __merlo_type__ = descriptor.name
            __merlo_field_types__ = field_types

            def __init__(self, *values: Any) -> None:
                if len(values) != len(field_names):
                    raise TypeError(f"{descriptor.name} expects {len(field_names)} fields")
                self.__owner_state__ = "Active"
                for name, type_name, value in zip(field_names, field_types, values, strict=True):
                    setattr(self, name, context.move_value(value, type_name))

            def __repr__(self) -> str:
                body = ", ".join(f"{name}={getattr(self, name)!r}" for name in field_names)
                return f"{descriptor.name}({body})"

        RecordValue.__name__ = descriptor.name
        RecordValue.__qualname__ = descriptor.name
        return RecordValue

    def _payload_enum_class(self, descriptor: TypeDescriptor) -> type:
        context = self

        class EnumValue:
            __slots__ = ("__owner_state__",)
            __merlo_type__ = descriptor.name

            def tag(self) -> int:
                return int(type(self).__merlo_tag__)

        EnumValue.__name__ = descriptor.name
        EnumValue.__qualname__ = descriptor.name
        for variant_name, payload_type, tag in descriptor.variants:
            if payload_type is None:
                def init_empty(self: Any, *, _context: RuntimeContext = context) -> None:
                    self.__owner_state__ = "Active"
                    _context.metrics.ast_nodes_allocated += 1

                namespace = {
                    "__slots__": (),
                    "__match_args__": (),
                    "__merlo_tag__": tag,
                    "__merlo_payload_type__": None,
                    "__init__": init_empty,
                }
            else:
                def init_payload(
                    self: Any,
                    value: Any,
                    *,
                    _context: RuntimeContext = context,
                    _payload_type: str = payload_type,
                ) -> None:
                    self.__owner_state__ = "Active"
                    self.value = _context.move_value(value, _payload_type)
                    _context.metrics.ast_nodes_allocated += 1

                namespace = {
                    "__slots__": ("value",),
                    "__match_args__": ("value",),
                    "__merlo_tag__": tag,
                    "__merlo_payload_type__": payload_type,
                    "__init__": init_payload,
                }
            variant = type(variant_name, (EnumValue,), namespace)
            setattr(EnumValue, variant_name, variant)
        return EnumValue

    def move_value(self, value: Any, type_name: str) -> Any:
        descriptor = self.descriptor(type_name)
        if descriptor.move_class == "copy":
            return value
        if isinstance(value, TextValue):
            return value.move()
        if isinstance(value, VecValue):
            if value.element_type == "Inferred":
                generic = descriptor.element_type
                if generic:
                    value.element_type = generic
            return value.move()
        if isinstance(value, BoxValue):
            if value.payload_type == "Inferred" and descriptor.payload_type:
                value.payload_type = descriptor.payload_type
            return value.move()
        state = getattr(value, "__owner_state__", None)
        if state != "Active":
            self.metrics.use_after_move_errors += 1
            raise RuntimeOwnershipError(f"InvalidMove<{type_name}>:{state}")
        if descriptor.kind == "record":
            clone = type(value).__new__(type(value))
            clone.__owner_state__ = "Active"
            for field_name, field_type, _ in descriptor.fields:
                setattr(clone, field_name, self.move_value(getattr(value, field_name), field_type))
            value.__owner_state__ = "Moved"
            return clone
        if descriptor.kind == "enum":
            clone = type(value).__new__(type(value))
            clone.__owner_state__ = "Active"
            payload_type = getattr(type(value), "__merlo_payload_type__", None)
            if payload_type is not None:
                clone.value = self.move_value(value.value, payload_type)
            value.__owner_state__ = "Moved"
            return clone
        return value

    def drop_value(self, value: Any, type_name: str) -> None:
        descriptor = self.descriptor(type_name)
        if descriptor.drop_class == "trivial" or value is None:
            return
        state = getattr(value, "__owner_state__", None)
        if state == "Moved":
            return
        if state == "Dropped":
            self.metrics.double_drop_errors += 1
            raise RuntimeOwnershipError(f"DoubleDrop<{type_name}>")
        if state != "Active":
            raise RuntimeOwnershipError(f"InvalidOwnerState<{type_name}>:{state}")
        self.metrics.drops += 1
        if isinstance(value, TextValue):
            value.drop()
            return
        if isinstance(value, TextBuilderValue):
            value.drop()
            return
        if isinstance(value, VecValue):
            value.drop()
            return
        if isinstance(value, BoxValue):
            value.drop()
            return
        if descriptor.kind == "record":
            for field_name, field_type, _ in descriptor.fields:
                self.drop_value(getattr(value, field_name), field_type)
            value.__owner_state__ = "Dropped"
            return
        if descriptor.kind == "enum":
            payload_type = getattr(type(value), "__merlo_payload_type__", None)
            if payload_type is not None:
                self.drop_value(value.value, payload_type)
            value.__owner_state__ = "Dropped"
            self.metrics.ast_nodes_freed += 1
            return
        raise RuntimeOwnershipError(f"UnsupportedDrop<{type_name}>")


class BytesViewValue:
    __slots__ = ("data", "context")

    def __init__(self, data: bytes, context: RuntimeContext) -> None:
        self.data = data
        self.context = context

    def len(self) -> int:
        return len(self.data)

    def byte(self, index: int) -> int:
        if index < 0 or index >= len(self.data):
            raise RuntimeBoundsError(f"BytesBounds:{index}:{len(self.data)}")
        return self.data[index]


class TextValue:
    __slots__ = ("data", "context", "__owner_state__")

    def __init__(self, data: bytes, context: RuntimeContext, *, allocation: bool = True) -> None:
        self.data = data
        self.context = context
        self.__owner_state__ = "Active"
        if allocation:
            context.metrics.text_allocations += 1

    @classmethod
    def from_bytes(cls, data: BytesViewValue, start: int, end: int) -> "TextValue":
        if start < 0 or end < start or end > len(data.data):
            raise RuntimeBoundsError(f"TextFromBytes:{start}:{end}:{len(data.data)}")
        payload = bytes(data.data[start:end])
        data.context.metrics.bytes_copied += len(payload)
        return cls(payload, data.context)

    def len(self) -> int:
        self.check()
        return len(self.data)

    def byte(self, index: int) -> int:
        self.check()
        if index < 0 or index >= len(self.data):
            raise RuntimeBoundsError(f"TextBounds:{index}:{len(self.data)}")
        return self.data[index]

    def check(self) -> None:
        if self.__owner_state__ != "Active":
            self.context.metrics.use_after_move_errors += 1
            raise RuntimeOwnershipError(f"TextUse:{self.__owner_state__}")

    def move(self) -> "TextValue":
        self.check()
        clone = TextValue(self.data, self.context, allocation=False)
        self.data = b""
        self.__owner_state__ = "Moved"
        return clone

    def drop(self) -> None:
        self.check()
        self.data = b""
        self.__owner_state__ = "Dropped"
        self.context.metrics.text_frees += 1


class TextBuilderValue:
    __slots__ = ("data", "context", "__owner_state__")

    def __init__(self, context: RuntimeContext) -> None:
        self.data = bytearray()
        self.context = context
        self.__owner_state__ = "Active"
        context.metrics.text_builder_allocations += 1

    def append_byte(self, byte: int) -> None:
        self.check()
        if byte < 0 or byte > 255:
            raise RuntimeBoundsError(f"InvalidByte:{byte}")
        self.data.append(byte)

    def append_scalar(self, scalar: int) -> None:
        self.check()
        if scalar < 0 or scalar > 0x10FFFF or 0xD800 <= scalar <= 0xDFFF:
            raise RuntimeBoundsError(f"InvalidScalar:{scalar}")
        self.data.extend(chr(scalar).encode("utf-8"))

    def finish(self) -> TextValue:
        self.check()
        payload = bytes(self.data)
        self.data.clear()
        self.__owner_state__ = "Moved"
        self.context.metrics.text_builder_frees += 1
        return TextValue(payload, self.context)

    def check(self) -> None:
        if self.__owner_state__ != "Active":
            self.context.metrics.use_after_move_errors += 1
            raise RuntimeOwnershipError(f"TextBuilderUse:{self.__owner_state__}")

    def drop(self) -> None:
        self.check()
        self.data.clear()
        self.__owner_state__ = "Dropped"
        self.context.metrics.text_builder_frees += 1


class VecViewValue:
    __slots__ = ("owner", "generation", "closed")

    def __init__(self, owner: "VecValue") -> None:
        self.owner = owner
        self.generation = owner.generation
        self.closed = False
        owner.active_views += 1

    def get(self, index: int) -> Any:
        if self.closed or self.generation != self.owner.generation:
            self.owner.context.metrics.stale_view_errors += 1
            raise RuntimeOwnershipError("StaleVecView")
        return self.owner.get(index)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.owner.active_views -= 1


class VecValue:
    __slots__ = (
        "data", "length", "capacity_value", "element_type", "context",
        "active_views", "generation", "__owner_state__", "has_buffer",
    )

    def __init__(self, context: RuntimeContext, element_type: str = "Inferred") -> None:
        self.data: list[Any] = []
        self.length = 0
        self.capacity_value = 0
        self.element_type = element_type
        self.context = context
        self.active_views = 0
        self.generation = 0
        self.__owner_state__ = "Active"
        self.has_buffer = False

    def check(self) -> None:
        if self.__owner_state__ != "Active":
            self.context.metrics.use_after_move_errors += 1
            raise RuntimeOwnershipError(f"VecUse:{self.__owner_state__}")

    def len(self) -> int:
        self.check()
        return self.length

    def capacity(self) -> int:
        self.check()
        return self.capacity_value

    def push(self, value: Any) -> None:
        self.check()
        required = self.length + 1
        if required > (1 << 63) - 1:
            raise RuntimeOwnershipError("VecCapacityOverflow")
        if required > self.capacity_value:
            if self.active_views:
                raise RuntimeOwnershipError("VecGrowthDuringActiveView")
            doubled = self.capacity_value * 2
            capacity = max(required, max(4, doubled))
            if capacity < required or capacity > (1 << 63) - 1:
                raise RuntimeOwnershipError("VecCapacityOverflow")
            if not self.has_buffer:
                self.context.metrics.vec_allocations += 1
                self.has_buffer = True
            else:
                self.context.metrics.vec_reallocations += 1
            self.context.metrics.vec_growths += 1
            self.capacity_value = capacity
            self.generation += 1
        if self.element_type == "Inferred":
            inferred = getattr(value, "__merlo_type__", None)
            if inferred:
                self.element_type = inferred
        if self.element_type == "Inferred":
            raise RuntimeOwnershipError("VecElementTypeUnresolved")
        self.data.append(self.context.move_value(value, self.element_type))
        self.length += 1
        self.context.metrics.vec_initialized += 1

    def get(self, index: int) -> Any:
        self.check()
        if index < 0 or index >= self.length:
            raise RuntimeBoundsError(f"VecBounds:{index}:{self.length}")
        return self.data[index]

    def get_mut(self, index: int) -> Any:
        return self.get(index)

    def view(self) -> VecViewValue:
        self.check()
        return VecViewValue(self)

    def move(self) -> "VecValue":
        self.check()
        if self.active_views:
            raise RuntimeOwnershipError("VecMoveDuringActiveView")
        clone = VecValue(self.context, self.element_type)
        clone.data = self.data
        clone.length = self.length
        clone.capacity_value = self.capacity_value
        clone.generation = self.generation
        clone.has_buffer = self.has_buffer
        self.data = []
        self.length = 0
        self.capacity_value = 0
        self.has_buffer = False
        self.__owner_state__ = "Moved"
        return clone

    def drop(self) -> None:
        self.check()
        if self.active_views:
            raise RuntimeOwnershipError("VecDropDuringActiveView")
        for index in range(self.length):
            self.context.drop_value(self.data[index], self.element_type)
            self.context.metrics.vec_elements_dropped += 1
        self.data.clear()
        self.length = 0
        self.capacity_value = 0
        if self.has_buffer:
            self.context.metrics.vec_frees += 1
        self.has_buffer = False
        self.__owner_state__ = "Dropped"
        self.generation += 1


class BoxValue:
    __slots__ = ("payload", "payload_type", "context", "__owner_state__")

    def __init__(self, payload: Any, context: RuntimeContext, payload_type: str = "Inferred") -> None:
        self.context = context
        self.payload_type = payload_type
        if payload_type == "Inferred":
            inferred = getattr(payload, "__merlo_type__", None)
            if inferred:
                payload_type = inferred
                self.payload_type = inferred
        self.payload = payload if payload_type == "Inferred" else context.move_value(payload, payload_type)
        self.__owner_state__ = "Active"
        context.metrics.box_allocations += 1

    def check(self) -> None:
        if self.__owner_state__ != "Active":
            self.context.metrics.use_after_move_errors += 1
            raise RuntimeOwnershipError(f"BoxUse:{self.__owner_state__}")

    def get(self) -> Any:
        self.check()
        return self.payload

    def get_mut(self) -> Any:
        return self.get()

    def move(self) -> "BoxValue":
        self.check()
        clone = BoxValue.__new__(BoxValue)
        clone.payload = self.payload
        clone.payload_type = self.payload_type
        clone.context = self.context
        clone.__owner_state__ = "Active"
        self.payload = None
        self.__owner_state__ = "Moved"
        return clone

    def drop(self) -> None:
        self.check()
        if self.payload_type == "Inferred":
            raise RuntimeOwnershipError("BoxPayloadTypeUnresolved")
        self.context.drop_value(self.payload, self.payload_type)
        self.payload = None
        self.__owner_state__ = "Dropped"
        self.context.metrics.box_frees += 1


class _AutoDropTransformer(ast.NodeTransformer):
    def __init__(self, descriptors: dict[str, TypeDescriptor]) -> None:
        self.descriptors = descriptors
        self.owned_stack: list[dict[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        owned: dict[str, str] = {}
        for child in ast.walk(node):
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                type_name = ast.unparse(child.annotation).replace(" ", "")
                descriptor = self.descriptors.get(type_name)
                if descriptor is not None and descriptor.drop_class != "trivial":
                    owned.setdefault(child.target.id, type_name)
        self.owned_stack.append(owned)
        node.body = [self.visit(statement) for statement in node.body]
        node.body = [item for statement in node.body for item in (statement if isinstance(statement, list) else [statement]) if item is not None]
        self.owned_stack.pop()
        return node

    def visit_Return(self, node: ast.Return) -> list[ast.stmt]:
        if not self.owned_stack:
            return [node]
        result_name = f"__merlo_result_{node.lineno}"
        assign = ast.Assign(targets=[ast.Name(id=result_name, ctx=ast.Store())], value=node.value or ast.Constant(None))
        cleanup = [
            ast.Expr(
                ast.Call(
                    func=ast.Name(id="__merlo_auto_drop_bound", ctx=ast.Load()),
                    args=[ast.Constant(name), ast.Call(ast.Name("locals", ast.Load()), [], []), ast.Constant(type_name)],
                    keywords=[],
                )
            )
            for name, type_name in reversed(tuple(self.owned_stack[-1].items()))
        ]
        return [assign, *cleanup, ast.Return(ast.Name(result_name, ast.Load()))]


@dataclass(frozen=True)
class EvaluationResult:
    status: str
    result: dict[str, Any] | None
    diagnostic: str | None
    metrics: dict[str, int]
    ownership_balanced: bool

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _runtime_namespace(context: RuntimeContext) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    namespace.update(context.record_classes)
    namespace.update(context.enum_classes)

    class VecFactory:
        @staticmethod
        def new() -> VecValue:
            return VecValue(context)

    class BoxFactory:
        @staticmethod
        def new(value: Any) -> BoxValue:
            return BoxValue(value, context)

    class TextFactory:
        from_bytes = staticmethod(TextValue.from_bytes)

    class TextBuilderFactory:
        @staticmethod
        def new() -> TextBuilderValue:
            return TextBuilderValue(context)

    def auto_drop_bound(name: str, local_values: dict[str, Any], type_name: str) -> None:
        if name in local_values:
            context.drop_value(local_values[name], type_name)

    def wrapping_mul(left: int, right: int) -> int:
        return (left * right) & ((1 << 64) - 1)

    namespace.update(
        {
            "Vec": VecFactory,
            "Box": BoxFactory,
            "Text": TextFactory,
            "TextBuilder": TextBuilderFactory,
            "wrapping_mul": wrapping_mul,
            "__merlo_auto_drop_bound": auto_drop_bound,
        }
    )
    return namespace


_EXECUTABLE_CACHE: dict[tuple[str, str], types.CodeType] = {}


def _compile_executable(hir: StructuredHIRProgram, representation: RepresentationProgram) -> types.CodeType:
    key = (hir.digest, representation.digest)
    cached = _EXECUTABLE_CACHE.get(key)
    if cached is not None:
        return cached
    preprocessed = _preprocess(hir.source)
    module = ast.parse(preprocessed.source, filename=hir.path)
    transformed = _AutoDropTransformer({item.name: item for item in representation.descriptors}).visit(module)
    ast.fix_missing_locations(transformed)
    compiled = compile(transformed, hir.path, "exec")
    _EXECUTABLE_CACHE[key] = compiled
    return compiled


def evaluate_structured_hir(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    payload: bytes,
) -> EvaluationResult:
    context = RuntimeContext(representation)
    namespace = _runtime_namespace(context)
    try:
        exec(_compile_executable(hir, representation), namespace)
        result_value = namespace[hir.entry_function](BytesViewValue(payload, context))
        result = {
            field_name: int(getattr(result_value, field_name)) if isinstance(getattr(result_value, field_name), enum.IntEnum) else getattr(result_value, field_name)
            for field_name, _, _ in representation.descriptor("ProgramResult").fields
        }
        context.drop_value(result_value, "ProgramResult")
        return EvaluationResult(
            "OK" if result.get("ok") else "ERROR",
            result,
            None,
            context.metrics.to_dict(),
            context.metrics.balanced(),
        )
    except Exception as exc:
        return EvaluationResult(
            "RUNTIME_FAILURE",
            None,
            f"{type(exc).__name__}: {exc}",
            context.metrics.to_dict(),
            context.metrics.balanced(),
        )


def exercise_vec_box_runtime(representation: RepresentationProgram) -> dict[str, Any]:
    context = RuntimeContext(representation)
    vector = VecValue(context, "UInt64")
    for value in range(9):
        vector.push(value)
    view = vector.view()
    blocked_growth = False
    try:
        while vector.len() < vector.capacity():
            vector.push(vector.len())
        vector.push(99)
    except RuntimeOwnershipError:
        blocked_growth = True
    view.close()
    vector.push(99)
    values = [vector.get(index) for index in range(vector.len())]
    vector.drop()
    box = BoxValue(41, context, "UInt64")
    moved = box.move()
    box_move_preserved = moved.get() == 41
    moved.drop()
    return {
        "values": values,
        "blocked_growth_with_view": blocked_growth,
        "box_move_preserved": box_move_preserved,
        "metrics": context.metrics.to_dict(),
        "balanced": context.metrics.balanced(),
    }


__all__ = [
    "BoxValue",
    "BytesViewValue",
    "EvaluationResult",
    "RuntimeBoundsError",
    "RuntimeContext",
    "RuntimeMetrics",
    "RuntimeOwnershipError",
    "TextBuilderValue",
    "TextValue",
    "VecValue",
    "VecViewValue",
    "evaluate_structured_hir",
    "exercise_vec_box_runtime",
]
