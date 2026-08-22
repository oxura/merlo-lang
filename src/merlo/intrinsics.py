"""Immutable canonical contracts for host intrinsics.

Every frontend and runtime adapter uses this table for intrinsic identity,
argument validation, result adaptation, effects, and ownership metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from merlo.type_parser import (
    GenericTypeSyntaxError,
    TypeExpr,
    parse_type,
    validate_type_expr,
)
from merlo.type_arena import (
    TypeArenaError,
    TypeContext,
    TypeContextBuilder,
    TypeId,
    TypeRef,
)


@dataclass(frozen=True, order=True)
class TypeConstructorId:
    """Identity of a bare static type constructor."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.isidentifier():
            raise ValueError("TypeConstructorId must contain one constructor name")


@dataclass(frozen=True, order=True)
class TypeVarId:
    """Stable identity for one type variable in a structural scheme."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("TypeVarId must contain a non-empty name")


@dataclass(frozen=True)
class TypeSchemeVar:
    variable: TypeVarId

    def __post_init__(self) -> None:
        if not isinstance(self.variable, TypeVarId):
            raise ValueError("type-scheme variable must be a TypeVarId")


@dataclass(frozen=True)
class TypeSchemeConcrete:
    type_id: TypeId

    def __post_init__(self) -> None:
        if not isinstance(self.type_id, TypeId):
            raise ValueError("type-scheme concrete node requires a TypeId")


@dataclass(frozen=True)
class TypeSchemeConst:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value < 0:
            raise ValueError("type-scheme constants must be non-negative integers")


@dataclass(frozen=True)
class TypeSchemeApplied:
    constructor: str
    arguments: tuple["TypeSchemeNode", ...]

    def __post_init__(self) -> None:
        if not isinstance(self.constructor, str) or not self.constructor:
            raise ValueError("applied type-scheme constructor must be text")
        arguments = tuple(self.arguments)
        if any(
            not isinstance(
                item,
                (TypeSchemeVar, TypeSchemeConcrete, TypeSchemeConst, TypeSchemeApplied),
            )
            for item in arguments
        ):
            raise ValueError("invalid applied type-scheme argument")
        object.__setattr__(self, "arguments", arguments)


TypeSchemeNode = (
    TypeSchemeVar
    | TypeSchemeConcrete
    | TypeSchemeConst
    | TypeSchemeApplied
)
TypeScheme = TypeSchemeNode


@dataclass(frozen=True)
class IntrinsicSignature:
    name: str
    parameters: tuple[str, ...]
    result_type: str
    effect: str
    capability: str
    parameter_ownership: tuple[str, ...] = ()
    result_ownership: str = "value"

    def __post_init__(self) -> None:
        if not self.parameter_ownership:
            object.__setattr__(self, "parameter_ownership", tuple("value" for _ in self.parameters))
        if len(self.parameter_ownership) != len(self.parameters):
            raise ValueError(f"ownership arity mismatch for {self.name}")
        if self.capability != self.effect:
            raise ValueError(f"capability/effect mismatch for {self.name}")

    @property
    def arity(self) -> int:
        return len(self.parameters)

    @property
    def ownership(self) -> tuple[str, ...]:
        """Compatibility spelling for consumers inspecting parameter ownership."""
        return self.parameter_ownership


@dataclass(frozen=True)
class InstanceMethodSignature:
    """A monomorphic method contract owned by the language, not the backend."""

    receiver_type: str
    name: str
    parameters: tuple[str, ...]
    result_type: str
    parameter_ownership: tuple[str, ...] = ()
    result_ownership: str = "value"
    receiver_ownership: str = "borrow"
    effects: tuple[str, ...] = ()
    static: bool = False
    abi_lowering: str | None = None
    representation_lowering: str | None = None
    operation_family: str | None = None
    contextual_numeric_result: bool = False
    optional_parameters: int = 0
    fallback_result_type: str | None = None
    parameter_type_ids: tuple[TypeId, ...] = ()
    result_type_id: TypeId | None = None
    generic_variables: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        variables = frozenset(self.generic_variables)
        if any(not isinstance(item, str) or not item for item in variables):
            raise ValueError(
                f"generic variables must be named explicitly for {self.receiver_type}.{self.name}"
            )
        object.__setattr__(self, "generic_variables", variables)
        if not self.parameter_ownership:
            object.__setattr__(
                self,
                "parameter_ownership",
                tuple("value" for _ in self.parameters),
            )
        if len(self.parameter_ownership) != len(self.parameters):
            raise ValueError(
                f"ownership arity mismatch for {self.receiver_type}.{self.name}"
            )
        if self.receiver_ownership not in {"borrow", "borrow_mut", "consuming"}:
            raise ValueError(
                f"invalid receiver ownership for {self.receiver_type}.{self.name}"
            )
        if tuple(sorted(set(self.effects))) != self.effects:
            raise ValueError(
                f"effects must be canonical for {self.receiver_type}.{self.name}"
            )
        if self.static and self.receiver_ownership != "borrow":
            raise ValueError(
                f"static method cannot own a receiver: {self.receiver_type}.{self.name}"
            )
        if self.operation_family not in {
            None,
            "vec",
            "map",
            "box",
            "bytes_text",
            "resource",
        }:
            raise ValueError(
                f"invalid operation family for {self.receiver_type}.{self.name}"
            )
        if self.contextual_numeric_result:
            allowed_results = (
                {"UInt64"}
                if self.result_type_id is None
                else {"Byte", "UInt64", "Int64", "Float32", "Float64"}
            )
            if self.result_type not in allowed_results:
                raise ValueError(
                    "invalid contextual numeric result for "
                    f"{self.receiver_type}.{self.name}"
                )
        if not 0 <= self.optional_parameters <= len(self.parameters):
            raise ValueError(
                f"invalid optional parameter count for "
                f"{self.receiver_type}.{self.name}"
            )
        if self.fallback_result_type is not None and not self.static:
            raise ValueError(
                "fallback result type is only valid for static methods: "
                f"{self.receiver_type}.{self.name}"
            )
        if self.parameter_type_ids and (
            len(self.parameter_type_ids) != len(self.parameters)
            or any(not isinstance(item, TypeId) for item in self.parameter_type_ids)
        ):
            raise ValueError(
                f"invalid bound parameter identities for "
                f"{self.receiver_type}.{self.name}"
            )
        if self.result_type_id is not None and not isinstance(
            self.result_type_id, TypeId
        ):
            raise ValueError(
                f"invalid bound result identity for "
                f"{self.receiver_type}.{self.name}"
            )

    @property
    def arity(self) -> int:
        return len(self.parameters)

    def result_for(self, expected: str | None) -> str:
        if self.contextual_numeric_result and expected in {
            "Byte",
            "UInt64",
            "Int64",
            "Float32",
            "Float64",
        }:
            return expected
        return self.result_type

    @property
    def minimum_arity(self) -> int:
        return self.arity - self.optional_parameters

    def accepts_arity(self, actual: int) -> bool:
        return self.minimum_arity <= actual <= self.arity

    def parameters_for(self, actual: int) -> tuple[str, ...]:
        if not self.accepts_arity(actual):
            raise ValueError(
                f"arity mismatch for {self.receiver_type}.{self.name}: {actual}"
            )
        return self.parameters[:actual]

    def ownership_for(self, actual: int) -> tuple[str, ...]:
        if not self.accepts_arity(actual):
            raise ValueError(
                f"arity mismatch for {self.receiver_type}.{self.name}: {actual}"
            )
        return self.parameter_ownership[:actual]


@dataclass(frozen=True)
class BuiltinFunctionSignature:
    """A symbolic typed contract for constructors and language-level calls."""

    name: str
    parameters: tuple[str, ...]
    result_type: str
    parameter_ownership: tuple[str, ...] = ()
    variadic: bool = False
    generic_variables: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        variables = frozenset(self.generic_variables)
        if any(not isinstance(item, str) or not item for item in variables):
            raise ValueError(f"generic variables must be named explicitly for {self.name}")
        object.__setattr__(self, "generic_variables", variables)
        if not self.parameter_ownership:
            object.__setattr__(
                self,
                "parameter_ownership",
                tuple("value" for _ in self.parameters),
            )
        if len(self.parameter_ownership) != len(self.parameters):
            raise ValueError(f"ownership arity mismatch for {self.name}")


def _signature(
    name: str,
    parameters: tuple[str, ...],
    result_type: str,
    effect: str,
    *,
    parameter_ownership: tuple[str, ...] | None = None,
    result_ownership: str = "value",
) -> IntrinsicSignature:
    return IntrinsicSignature(
        name,
        parameters,
        result_type,
        effect,
        effect,
        tuple("value" for _ in parameters) if parameter_ownership is None else parameter_ownership,
        result_ownership,
    )


# The mapping and all entries are immutable after module initialization.
_INTRINSIC_ROWS = (
    _signature("console.read", (), "Bytes", "console.read", result_ownership="owned"),
    _signature("console.read_line", (), "Text", "console.read", result_ownership="owned"),
    _signature("console.read_all", (), "Text", "console.read", result_ownership="owned"),
    _signature("console.write", ("TextView",), "Unit", "console.write", parameter_ownership=("borrow",)),
    _signature("fs.open_read", ("Path",), "Result[FileReader,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read", ("Path",), "Result[Bytes,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read_text", ("Path",), "Result[Text,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read_chunk", ("FileReader", "UInt64"), "Result[Bytes,FileError]", "fs.read", parameter_ownership=("borrow_mut", "value"), result_ownership="owned"),
    _signature("fs.open_write", ("Path",), "Result[FileWriter,FileError]", "fs.write", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.write", ("Path", "BytesView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow", "borrow")),
    _signature("fs.write_text", ("Path", "TextView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow", "borrow")),
    _signature("fs.write_chunk", ("FileWriter", "BytesView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow_mut", "borrow")),
    _signature("fs.close_read", ("FileReader",), "Result[Unit,FileError]", "fs.read", parameter_ownership=("consuming",)),
    _signature("fs.close_write", ("FileWriter",), "Result[Unit,FileError]", "fs.write", parameter_ownership=("consuming",)),
    _signature("env.read", ("Text",), "Text", "env.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("env.get", ("Text",), "Text", "env.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("clock.now", (), "UInt64", "clock.now"),
    _signature("random.read", ("UInt64",), "Bytes", "random.read", result_ownership="owned"),
    _signature("network.tcp_connect", ("Text", "UInt64"), "Result[UInt64,AppError]", "network.tcp", parameter_ownership=("borrow", "value")),
    _signature("network.tcp_send", ("UInt64", "BytesView"), "Result[UInt64,AppError]", "network.tcp", parameter_ownership=("value", "borrow")),
    _signature("network.tcp_receive", ("UInt64", "UInt64"), "Result[Bytes,AppError]", "network.tcp", result_ownership="owned"),
    _signature("network.tcp_close", ("UInt64",), "Result[Unit,AppError]", "network.tcp"),
    _signature("network.http_request", ("Text",), "Result[Bytes,AppError]", "network.http", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("process.args", (), "UInt64", "process.args"),
    _signature("process.arg", ("UInt64",), "Text", "process.args", result_ownership="owned"),
)
INTRINSIC_SIGNATURES: Mapping[str, IntrinsicSignature] = MappingProxyType(
    {row.name: row for row in _INTRINSIC_ROWS}
)


_INSTANCE_METHOD_ROWS = (
    InstanceMethodSignature(
        "Vec",
        "new",
        (),
        "Vec[T]",
        result_ownership="owned",
        effects=("allocate", "may_fail"),
        static=True,
        representation_lowering="vec_new",
        operation_family="vec",
        fallback_result_type="Vec[Inferred]",
    ),
    InstanceMethodSignature(
        "Map",
        "new",
        (),
        "Map[K,V]",
        result_ownership="owned",
        effects=("allocate", "may_fail"),
        static=True,
        representation_lowering="map_new",
        operation_family="map",
        fallback_result_type="Map[Text,UInt64]",
    ),
    InstanceMethodSignature(
        "Box",
        "new",
        ("T",),
        "Box[T]",
        parameter_ownership=("consuming",),
        result_ownership="owned",
        effects=("allocate", "may_fail"),
        static=True,
        representation_lowering="box_new",
        operation_family="box",
    ),
    InstanceMethodSignature(
        "Text",
        "from_bytes",
        ("BytesView", "UInt64", "UInt64"),
        "Text",
        ("borrow", "value", "value"),
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        static=True,
        abi_lowering="merlo_text_from_bytes",
    ),
    InstanceMethodSignature(
        "TextBuilder",
        "new",
        (),
        "TextBuilder",
        result_ownership="owned",
        effects=("allocate", "may_fail"),
        static=True,
        abi_lowering="merlo_text_builder_new",
    ),
    InstanceMethodSignature(
        "Path",
        "to_text",
        (),
        "Text",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Bytes",
        "to_text",
        (),
        "Text",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Bytes",
        "len",
        (),
        "UInt64",
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Bytes",
        "view",
        (),
        "BytesView",
        result_ownership="borrow",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "BytesView",
        "len",
        (),
        "UInt64",
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "BytesView",
        "byte",
        ("UInt64",),
        "UInt64",
        effects=("bounds_check",),
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "BytesView",
        "slice",
        ("UInt64", "UInt64"),
        "BytesView",
        result_ownership="borrow",
        effects=("bounds_check",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "clone",
        (),
        "Text",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "len",
        (),
        "UInt64",
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Text",
        "byte",
        ("UInt64",),
        "UInt64",
        effects=("bounds_check",),
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Text",
        "as_view",
        (),
        "TextView",
        result_ownership="borrow",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "view",
        (),
        "TextView",
        result_ownership="borrow",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "contains",
        ("Text",),
        "Bool",
        parameter_ownership=("borrow",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "contains_ascii_case_insensitive",
        ("Text",),
        "Bool",
        parameter_ownership=("borrow",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text", "starts_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text", "ends_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text", "slice_bytes", ("UInt64", "UInt64"), "TextView",
        result_ownership="borrow", effects=("bounds_check",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "parse_uint64", (), "Result[UInt64,UInt64]",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "is_ascii", (), "Bool", operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "is_digits", (), "Bool", operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "len", (), "UInt64", operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "TextView", "byte", ("UInt64",), "UInt64",
        effects=("bounds_check",), operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "TextView", "contains", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView",
        "contains_ascii_case_insensitive",
        ("Text",),
        "Bool",
        parameter_ownership=("borrow",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "slice_bytes", ("UInt64", "UInt64"), "TextView",
        result_ownership="borrow", effects=("bounds_check",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "starts_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "ends_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "to_text", (), "Text", result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_text", ("Text",), "Unit",
        parameter_ownership=("borrow",), receiver_ownership="borrow_mut",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_byte", ("UInt64",), "Unit",
        receiver_ownership="borrow_mut", effects=("allocate", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_scalar", ("UInt64",), "Unit",
        receiver_ownership="borrow_mut", effects=("allocate", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "finish", (), "Text", result_ownership="owned",
        receiver_ownership="consuming", operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_uint64", ("UInt64",), "Unit",
        receiver_ownership="borrow_mut", effects=("allocate", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "FileReader",
        "lines",
        (),
        "FileLines",
        result_ownership="borrow",
        receiver_ownership="borrow_mut",
        effects=("borrow",),
        representation_lowering="file_lines",
        operation_family="resource",
    ),
    InstanceMethodSignature("FileLines", "count_text", (), "Text", result_ownership="owned", receiver_ownership="borrow_mut"),
    InstanceMethodSignature(
        "Option[T]",
        "is_none",
        (),
        "Bool",
        representation_lowering="option_is_none",
    ),
    InstanceMethodSignature(
        "Option[T]",
        "is_some",
        (),
        "Bool",
        representation_lowering="option_is_some",
    ),
    InstanceMethodSignature(
        "Option[T]",
        "unwrap",
        (),
        "T",
        result_ownership="payload_clone",
        effects=("allocate", "copy", "may_fail"),
        representation_lowering="option_unwrap_clone",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "is_ok",
        (),
        "Bool",
        representation_lowering="result_is_ok",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "is_err",
        (),
        "Bool",
        representation_lowering="result_is_err",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "unwrap",
        (),
        "T",
        result_ownership="payload_clone",
        effects=("allocate", "copy", "may_fail"),
        representation_lowering="result_unwrap_clone",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "unwrap_err",
        (),
        "E",
        result_ownership="payload_clone",
        effects=("allocate", "copy", "may_fail"),
        representation_lowering="result_unwrap_err_clone",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "clone",
        (),
        "Vec[T]",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "push",
        ("T",),
        "Unit",
        parameter_ownership=("consuming",),
        receiver_ownership="borrow_mut",
        effects=("allocate", "may_fail"),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "len",
        (),
        "UInt64",
        operation_family="vec",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "capacity",
        (),
        "UInt64",
        operation_family="vec",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "get",
        ("UInt64",),
        "T",
        result_ownership="borrow",
        effects=("bounds_check",),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "get_mut",
        ("UInt64",),
        "T",
        result_ownership="borrow_mut",
        receiver_ownership="borrow_mut",
        effects=("bounds_check",),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "view",
        (),
        "Borrow[Vec[T]]",
        result_ownership="borrow",
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Map[K,V]",
        "insert",
        ("K", "V"),
        "Unit",
        parameter_ownership=("borrow", "value"),
        receiver_ownership="borrow_mut",
        effects=("allocate", "copy", "may_fail"),
        operation_family="map",
    ),
    InstanceMethodSignature(
        "Map[K,V]",
        "get",
        ("K",),
        "V",
        parameter_ownership=("borrow",),
        operation_family="map",
    ),
    InstanceMethodSignature(
        "Map[K,UInt64]",
        "increment",
        ("K", "UInt64"),
        "Unit",
        parameter_ownership=("borrow", "value"),
        receiver_ownership="borrow_mut",
        effects=("allocate", "copy", "may_fail"),
        operation_family="map",
        optional_parameters=1,
    ),
    InstanceMethodSignature(
        "Map[K,V]",
        "entries",
        (),
        "Borrow[Map[K,V]]",
        result_ownership="borrow",
        operation_family="map",
    ),
    InstanceMethodSignature(
        "Box[T]",
        "get",
        (),
        "T",
        result_ownership="borrow",
        operation_family="box",
    ),
    InstanceMethodSignature(
        "Box[T]",
        "get_mut",
        (),
        "T",
        result_ownership="borrow_mut",
        receiver_ownership="borrow_mut",
        operation_family="box",
    ),
)
_EXPLICIT_METHOD_VARIABLES = {
    ("Vec", "new"): frozenset({"T"}),
    ("Map", "new"): frozenset({"K", "V"}),
    ("Box", "new"): frozenset({"T"}),
    ("Option[T]", "is_none"): frozenset({"T"}),
    ("Option[T]", "is_some"): frozenset({"T"}),
    ("Option[T]", "unwrap"): frozenset({"T"}),
    ("Result[T,E]", "is_ok"): frozenset({"T", "E"}),
    ("Result[T,E]", "is_err"): frozenset({"T", "E"}),
    ("Result[T,E]", "unwrap"): frozenset({"T", "E"}),
    ("Result[T,E]", "unwrap_err"): frozenset({"T", "E"}),
    ("Vec[T]", "clone"): frozenset({"T"}),
    ("Vec[T]", "push"): frozenset({"T"}),
    ("Vec[T]", "len"): frozenset({"T"}),
    ("Vec[T]", "capacity"): frozenset({"T"}),
    ("Vec[T]", "get"): frozenset({"T"}),
    ("Vec[T]", "get_mut"): frozenset({"T"}),
    ("Vec[T]", "view"): frozenset({"T"}),
    ("Map[K,V]", "insert"): frozenset({"K", "V"}),
    ("Map[K,V]", "get"): frozenset({"K", "V"}),
    ("Map[K,UInt64]", "increment"): frozenset({"K"}),
    ("Map[K,V]", "entries"): frozenset({"K", "V"}),
    ("Box[T]", "get"): frozenset({"T"}),
    ("Box[T]", "get_mut"): frozenset({"T"}),
}
_INSTANCE_METHOD_ROWS = tuple(
    replace(
        row,
        generic_variables=_EXPLICIT_METHOD_VARIABLES.get(
            (row.receiver_type, row.name), frozenset()
        ),
    )
    for row in _INSTANCE_METHOD_ROWS
)
INSTANCE_METHOD_SIGNATURES: Mapping[
    tuple[str, str], InstanceMethodSignature
] = MappingProxyType(
    {(row.receiver_type, row.name): row for row in _INSTANCE_METHOD_ROWS}
)
INSTANCE_METHOD_NAMES = frozenset(row.name for row in _INSTANCE_METHOD_ROWS)

# These symbolic variables are resolved by type-directed elaboration. Keeping
# constructors and language calls beside host/method contracts prevents the
# binder from growing a second manually synchronized builtin API table.
_BUILTIN_FUNCTION_ROWS = (
    BuiltinFunctionSignature("Path", ("Text",), "Path", ("borrow",)),
    BuiltinFunctionSignature("Unit", (), "Unit"),
    BuiltinFunctionSignature("Ok", ("T",), "Result[T,E]"),
    BuiltinFunctionSignature("Err", ("E",), "Result[T,E]"),
    BuiltinFunctionSignature("Some", ("T",), "Option[T]"),
    BuiltinFunctionSignature("None", (), "Option[T]"),
    BuiltinFunctionSignature("not", ("Bool",), "Bool"),
    BuiltinFunctionSignature("and", ("Bool", "Bool"), "Bool"),
    BuiltinFunctionSignature("or", ("Bool", "Bool"), "Bool"),
    BuiltinFunctionSignature("drop", ("T",), "Unit", ("consuming",)),
    BuiltinFunctionSignature("move", ("T",), "T", ("consuming",)),
    BuiltinFunctionSignature("map", ("Collection[T]", "Fn[T,U]"), "Collection[U]"),
    BuiltinFunctionSignature("filter", ("Collection[T]", "Fn[T,Bool]"), "Collection[T]"),
    BuiltinFunctionSignature("fold", ("Collection[T]", "U", "Fn[U,T,U]"), "U"),
    BuiltinFunctionSignature("len", ("Collection[T]",), "UInt64", ("borrow",)),
    BuiltinFunctionSignature("release", ("Borrow[T]",), "Unit"),
    BuiltinFunctionSignature("wrapping_add", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("wrapping_sub", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("wrapping_mul", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("checked_add", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("checked_sub", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("checked_mul", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("__merlo_try__", ("Result[T,E]",), "T"),
    BuiltinFunctionSignature("Byte", ("Scalar",), "Byte"),
    BuiltinFunctionSignature("UInt64", ("Scalar",), "UInt64"),
    BuiltinFunctionSignature("Int64", ("Scalar",), "Int64"),
    BuiltinFunctionSignature("Float32", ("Scalar",), "Float32"),
    BuiltinFunctionSignature("Float64", ("Scalar",), "Float64"),
)
_EXPLICIT_FUNCTION_VARIABLES = {
    "Ok": frozenset({"T", "E"}),
    "Err": frozenset({"T", "E"}),
    "Some": frozenset({"T"}),
    "None": frozenset({"T"}),
    "drop": frozenset({"T"}),
    "move": frozenset({"T"}),
    "map": frozenset({"T", "U"}),
    "filter": frozenset({"T"}),
    "fold": frozenset({"T", "U"}),
    "len": frozenset({"T"}),
    "release": frozenset({"T"}),
    "__merlo_try__": frozenset({"T", "E"}),
}
_BUILTIN_FUNCTION_ROWS = tuple(
    replace(
        row,
        generic_variables=_EXPLICIT_FUNCTION_VARIABLES.get(row.name, frozenset()),
    )
    for row in _BUILTIN_FUNCTION_ROWS
)
BUILTIN_FUNCTION_SIGNATURES: Mapping[str, BuiltinFunctionSignature] = MappingProxyType(
    {row.name: row for row in _BUILTIN_FUNCTION_ROWS}
)
BUILTIN_FUNCTIONS = frozenset(BUILTIN_FUNCTION_SIGNATURES)
BUILTIN_RECEIVERS = frozenset(
    {name.partition(".")[0] for name in INTRINSIC_SIGNATURES}
    | {
        "Text", "Bytes", "TextBuilder", "Vec", "Map", "Box", "Option",
        "Result",
    }
)


_ABI_LOWERINGS: Mapping[str, str] = MappingProxyType(
    {
        "console.read": "merlo_console_read",
        "console.read_line": "merlo_console_read_line",
        "console.read_all": "merlo_console_read_all",
        "console.write": "merlo_console_write_view",
        "fs.open_read": "merlo_file_open_read",
        "fs.read": "merlo_file_read_all",
        "fs.read_text": "merlo_file_read_text",
        "fs.read_chunk": "merlo_file_read_chunk",
        "fs.open_write": "merlo_file_open_write",
        "fs.write": "merlo_file_write_all",
        "fs.write_text": "merlo_file_write_text",
        "fs.write_chunk": "merlo_file_write_chunk",
        "fs.close_read": "merlo_file_close",
        "fs.close_write": "merlo_file_close_writer",
        "env.read": "merlo_env_read",
        "env.get": "merlo_env_read",
        "clock.now": "merlo_clock_now",
        "random.read": "merlo_random_read",
        "network.tcp_connect": "merlo_network_tcp_connect",
        "network.tcp_send": "merlo_network_tcp_send",
        "network.tcp_receive": "merlo_network_tcp_receive",
        "network.tcp_close": "merlo_network_tcp_close",
        "network.http_request": "merlo_network_http_request",
        "process.args": "merlo_process_args_count",
        "process.arg": "merlo_process_arg",
    }
)


def _scheme_from_expr(
    expression: TypeExpr,
    context: TypeContext | TypeContextBuilder,
    generic_variables: frozenset[str],
) -> TypeSchemeNode:
    if not expression.args:
        if expression.name.isdigit():
            return TypeSchemeConst(int(expression.name))
        if expression.name in generic_variables:
            return TypeSchemeVar(TypeVarId(expression.name))
        return TypeSchemeConcrete(context.type_id(expression.name))
    return TypeSchemeApplied(
        expression.name,
        tuple(
            _scheme_from_expr(argument, context, generic_variables)
            for argument in expression.args
        ),
    )


def _scheme_from_spelling(
    spelling: str,
    context: TypeContext | TypeContextBuilder,
    generic_variables: frozenset[str] = frozenset(),
) -> TypeSchemeNode:
    try:
        expression = validate_type_expr(parse_type(spelling))
    except GenericTypeSyntaxError as exc:
        # A bare generic constructor is the receiver of a static contract
        # (for example ``Vec.new``); it is not a complete source type.
        if "[" not in spelling:
            return TypeSchemeApplied(spelling, ())
        raise TypeArenaError(f"invalid contract type scheme {spelling!r}") from exc
    return _scheme_from_expr(expression, context, generic_variables)

def _preintern_scheme(
    expression: TypeExpr,
    context: TypeContextBuilder,
    generic_variables: frozenset[str],
) -> bool:
    if not expression.args:
        if expression.name in generic_variables:
            return False
        context.intern_expr(expression)
        return True
    concrete = all(
        _preintern_scheme(argument, context, generic_variables)
        for argument in expression.args
    )
    if concrete:
        context.intern_expr(expression)
    return concrete




def _scheme_spellings(
    methods: Mapping[tuple[str, str], InstanceMethodSignature],
) -> tuple[str, ...]:
    values: list[str] = []
    for signature in methods.values():
        values.extend(signature.parameters)
        values.extend((signature.receiver_type, signature.result_type))
        if signature.fallback_result_type is not None:
            values.append(signature.fallback_result_type)
    return tuple(values)


def _scheme_specificity(pattern: TypeSchemeNode) -> int:
    if isinstance(pattern, TypeSchemeVar):
        return 0
    if isinstance(pattern, TypeSchemeConcrete):
        return 2
    if isinstance(pattern, TypeSchemeConst):
        return 1
    return 1 + sum(_scheme_specificity(item) for item in pattern.arguments)

@dataclass(frozen=True)
class BoundContractGraph:
    """Structural contract view bound to one compiler type authority."""

    definitions: BuiltinContractGraph
    context: TypeContext | TypeContextBuilder
    _method_schemes: Mapping[tuple[str, str], tuple[TypeSchemeNode, tuple[TypeSchemeNode, ...], TypeSchemeNode]]
    _fallback_schemes: Mapping[tuple[str, str], TypeSchemeNode | None]

    def _resolve(self, type_id: TypeId) -> TypeRef:
        if not isinstance(type_id, TypeId):
            raise TypeArenaError("contract matching requires TypeId")
        return self.context.resolve(type_id)

    def _match(
        self,
        pattern: TypeSchemeNode,
        actual: TypeId | None,
        substitutions: dict[TypeVarId, TypeId],
    ) -> bool:
        if actual is None:
            return False
        actual_ref = self._resolve(actual)
        if isinstance(pattern, TypeSchemeVar):
            previous = substitutions.get(pattern.variable)
            if previous is not None:
                return previous == actual
            substitutions[pattern.variable] = actual
            return True
        if isinstance(pattern, TypeSchemeConst):
            return not actual_ref.arguments and actual_ref.constructor == str(pattern.value)
        if isinstance(pattern, TypeSchemeConcrete):
            expected = self._resolve(pattern.type_id)
            if expected == actual_ref:
                return True
            return (
                (expected.constructor, actual_ref.constructor)
                in {("BytesView", "Bytes"), ("TextView", "Text")}
                and not expected.arguments
                and not actual_ref.arguments
            )
        if pattern.constructor != actual_ref.constructor:
            return False
        if len(pattern.arguments) != len(actual_ref.arguments):
            return False
        candidate = dict(substitutions)
        for item, concrete in zip(
            pattern.arguments,
            actual_ref.arguments,
            strict=True,
        ):
            if not self._match(item, concrete, candidate):
                return False
        substitutions.clear()
        substitutions.update(candidate)
        return True

    def _instantiate(
        self,
        pattern: TypeSchemeNode,
        substitutions: Mapping[TypeVarId, TypeId],
    ) -> TypeId | None:
        if isinstance(pattern, TypeSchemeVar):
            return substitutions.get(pattern.variable)
        if isinstance(pattern, TypeSchemeConcrete):
            return pattern.type_id
        if isinstance(pattern, TypeSchemeConst):
            return self.context.type_id(str(pattern.value))
        arguments = tuple(
            self._instantiate(item, substitutions)
            for item in pattern.arguments
        )
        if any(item is None for item in arguments):
            return None
        if isinstance(self.context, TypeContextBuilder):
            return self.context.intern_node(pattern.constructor, arguments)
        return self.context.arena.identity(TypeRef(pattern.constructor, arguments))

    def method(
        self,
        receiver_type_id: TypeId,
        name: str,
        expected_type_id: TypeId | None = None,
    ) -> InstanceMethodSignature | None:
        candidates = sorted(
            self._method_schemes.items(),
            key=lambda item: (-_scheme_specificity(item[1][0]), item[0]),
        )
        for key, schemes in candidates:
            pattern, parameters, result = schemes
            receiver, method_name = key
            if method_name != name:
                continue
            substitutions: dict[TypeVarId, TypeId] = {}
            if not self._match(pattern, receiver_type_id, substitutions):
                continue
            receiver_text = self.context.render(receiver_type_id)
            parameter_ids = tuple(
                self._instantiate(item, substitutions) for item in parameters
            )
            result_id = self._instantiate(result, substitutions)
            if any(item is None for item in parameter_ids) or result_id is None:
                continue
            signature = self.definitions.methods[key]
            if (
                signature.contextual_numeric_result
                and expected_type_id is not None
            ):
                expected = self._resolve(expected_type_id)
                if (
                    not expected.arguments
                    and expected.constructor
                    in {"Byte", "UInt64", "Int64", "Float32", "Float64"}
                ):
                    result_id = expected_type_id
            return replace(
                signature,
                receiver_type=receiver_text,
                parameters=tuple(self.context.render(item) for item in parameter_ids),
                result_type=self.context.render(result_id),
                parameter_type_ids=parameter_ids,
                result_type_id=result_id,
            )
        return None

    def static_method(
        self,
        receiver_type_id: TypeId | TypeConstructorId,
        name: str,
    ) -> InstanceMethodSignature | None:
        if isinstance(receiver_type_id, TypeConstructorId):
            signature = self.definitions.methods.get((receiver_type_id.value, name))
            return signature if signature is not None and signature.static else None
        if not isinstance(receiver_type_id, TypeId):
            raise TypeArenaError("static contract receiver requires TypeId or TypeConstructorId")
        signature = self.method(receiver_type_id, name)
        return signature if signature is not None and signature.static else None
    def static_parameter_type_ids(
        self,
        receiver: TypeConstructorId,
        name: str,
        arity: int,
    ) -> tuple[TypeId | None, ...] | None:
        if not isinstance(receiver, TypeConstructorId):
            raise TypeArenaError("static parameter lookup requires TypeConstructorId")
        key = (receiver.value, name)
        signature = self.definitions.methods.get(key)
        if signature is None or not signature.static:
            return None
        if not signature.accepts_arity(arity):
            raise ValueError(
                f"arity mismatch for {receiver}.{name}: {arity}"
            )
        schemes = self._method_schemes[key][1][:arity]
        return tuple(self._instantiate(item, {}) for item in schemes)


    def resolve_static_method(
        self,
        receiver_type_id: TypeId | TypeConstructorId,
        name: str,
        argument_type_ids: tuple[TypeId | None, ...],
        expected_type_id: TypeId | None = None,
    ) -> InstanceMethodSignature | None:
        if not isinstance(receiver_type_id, (TypeId, TypeConstructorId)):
            raise TypeArenaError(
                "static contract receiver requires TypeId or TypeConstructorId"
            )
        signature = self.static_method(receiver_type_id, name)
        receiver_text = (
            receiver_type_id.value
            if isinstance(receiver_type_id, TypeConstructorId)
            else self.context.render(receiver_type_id)
        )
        if signature is None:
            return None
        if not signature.accepts_arity(len(argument_type_ids)):
            raise ValueError(
                f"arity mismatch for {receiver_text}.{name}: "
                f"{len(argument_type_ids)}"
            )
        key = None
        substitutions: dict[TypeVarId, TypeId] = {}
        candidates = sorted(
            self._method_schemes.items(),
            key=lambda item: (-_scheme_specificity(item[1][0]), item[0]),
        )
        for candidate, candidate_schemes in candidates:
            if candidate[1] != name or not self.definitions.methods[candidate].static:
                continue
            candidate_substitutions: dict[TypeVarId, TypeId] = {}
            if isinstance(receiver_type_id, TypeConstructorId):
                if candidate[0] != receiver_type_id.value:
                    continue
            else:
                if not self._match(
                    candidate_schemes[0],
                    receiver_type_id,
                    candidate_substitutions,
                ):
                    continue
            key = candidate
            substitutions = candidate_substitutions
            break
        if key is None:
            return None
        signature = self.definitions.methods[key]
        schemes = self._method_schemes[key]
        if expected_type_id is not None and not self._match(
            schemes[2], expected_type_id, substitutions
        ):
            raise ValueError(
                f"result type mismatch for {receiver_text}.{name}"
            )
        for parameter, actual in zip(
            schemes[1][: len(argument_type_ids)],
            argument_type_ids,
            strict=True,
        ):
            if not self._match(parameter, actual, substitutions):
                raise ValueError(
                    f"argument type mismatch for {receiver_text}.{name}"
                )
        result_id = self._instantiate(schemes[2], substitutions)
        if result_id is None:
            fallback = self._fallback_schemes[key]
            if fallback is None:
                return None
            result_id = self._instantiate(fallback, substitutions)
        if result_id is None:
            return None
        parameter_ids = tuple(
            self._instantiate(item, substitutions) for item in schemes[1]
        )
        if any(item is None for item in parameter_ids):
            return None
        result = replace(
            signature,
            parameters=tuple(self.context.render(item) for item in parameter_ids),
            result_type=self.context.render(result_id),
            parameter_type_ids=parameter_ids,
            result_type_id=result_id,
        )
        if signature.contextual_numeric_result and expected_type_id is not None:
            expected_name = self.context.render(expected_type_id)
            if expected_name in {"Byte", "UInt64", "Int64", "Float32", "Float64"}:
                result = replace(
                    result,
                    result_type=expected_name,
                    result_type_id=expected_type_id,
                )
        return result


@dataclass(frozen=True)
class BuiltinContractGraph:
    """Immutable static definitions; matching lives in BoundContractGraph."""

    intrinsics: Mapping[str, IntrinsicSignature]
    methods: Mapping[tuple[str, str], InstanceMethodSignature]
    functions: Mapping[str, BuiltinFunctionSignature]
    abi_lowerings: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "intrinsics",
            MappingProxyType(dict(self.intrinsics)),
        )
        object.__setattr__(
            self,
            "methods",
            MappingProxyType(dict(self.methods)),
        )
        object.__setattr__(
            self,
            "functions",
            MappingProxyType(dict(self.functions)),
        )
        object.__setattr__(
            self,
            "abi_lowerings",
            MappingProxyType(dict(self.abi_lowerings)),
        )
        missing = set(self.intrinsics) - set(self.abi_lowerings)
        if missing:
            raise ValueError(f"intrinsics without ABI lowering: {sorted(missing)}")

    def intrinsic(self, symbol: str) -> IntrinsicSignature | None:
        return self.intrinsics.get(symbol)

    def required_type_spellings(self) -> tuple[str, ...]:
        return _scheme_spellings(self.methods)

    def prepare(self, context: TypeContextBuilder) -> BoundContractGraph:
        if not isinstance(context, TypeContextBuilder):
            raise TypeArenaError(
                "contract graph preparation requires TypeContextBuilder"
            )
        for signature in self.methods.values():
            spellings = (
                *signature.parameters,
                signature.receiver_type,
                signature.result_type,
                *(
                    (signature.fallback_result_type,)
                    if signature.fallback_result_type is not None
                    else ()
                ),
            )
            for spelling in spellings:
                try:
                    expression = validate_type_expr(parse_type(spelling))
                except GenericTypeSyntaxError:
                    continue
                _preintern_scheme(
                    expression,
                    context,
                    signature.generic_variables,
                )
        return self.bind(context)


    def bind(
        self,
        context: TypeContext | TypeContextBuilder,
    ) -> BoundContractGraph:
        if not isinstance(context, (TypeContext, TypeContextBuilder)):
            raise TypeArenaError("contract graph requires a TypeContext")
        method_schemes: dict[tuple[str, str], tuple[TypeSchemeNode, tuple[TypeSchemeNode, ...], TypeSchemeNode]] = {}
        fallback_schemes: dict[tuple[str, str], TypeSchemeNode | None] = {}
        for key, signature in self.methods.items():
            method_schemes[key] = (
                _scheme_from_spelling(
                    signature.receiver_type,
                    context,
                    signature.generic_variables,
                ),
                tuple(
                    _scheme_from_spelling(
                        item,
                        context,
                        signature.generic_variables,
                    )
                    for item in signature.parameters
                ),
                _scheme_from_spelling(
                    signature.result_type,
                    context,
                    signature.generic_variables,
                ),
            )
            fallback_schemes[key] = (
                _scheme_from_spelling(
                    signature.fallback_result_type,
                    context,
                    signature.generic_variables,
                )
                if signature.fallback_result_type is not None
                else None
            )
        bound = BoundContractGraph(
            self,
            context,
            MappingProxyType(method_schemes),
            MappingProxyType(fallback_schemes),
        )
        return bound

    def _legacy_context(self, values: tuple[str, ...]) -> TypeContextBuilder:
        builder = TypeContextBuilder(allow_unresolved=False)
        for spelling in values:
            if not spelling:
                continue
            try:
                builder.intern_text(spelling)
            except TypeArenaError:
                continue
        self.prepare(builder)
        return builder

    def method(self, receiver_type: str, name: str) -> InstanceMethodSignature | None:
        context = self._legacy_context((receiver_type,))
        try:
            receiver_id = context.type_id(receiver_type)
        except TypeArenaError:
            return None
        return self.bind(context).method(receiver_id, name)

    def static_method(self, receiver_type: str, name: str) -> InstanceMethodSignature | None:
        context = self._legacy_context((receiver_type,))
        return self.bind(context).static_method(
            TypeConstructorId(receiver_type),
            name,
        )

    def resolve_static_method(
        self,
        receiver_type: str,
        name: str,
        argument_types: tuple[str | None, ...],
        expected: str | None = None,
    ) -> InstanceMethodSignature | None:
        values = (receiver_type, *(item for item in argument_types if item is not None), expected or "")
        context = self._legacy_context(values)
        argument_ids = tuple(
            context.type_id(item) if item is not None else None
            for item in argument_types
        )
        expected_id = context.type_id(expected) if expected is not None else None
        return self.bind(context).resolve_static_method(
            TypeConstructorId(receiver_type),
            name,
            argument_ids,
            expected_id,
        )

    def has_representation_method(self, name: str) -> bool:
        return any(
            method_name == name and signature.representation_lowering is not None
            for (_receiver, method_name), signature in self.methods.items()
        )

    def abi_lowering(self, symbol: str) -> str | None:
        lowering = self.abi_lowerings.get(symbol)
        if lowering is not None:
            return lowering
        receiver, separator, method = symbol.partition(".")
        if not separator:
            return None
        signature = self.methods.get((receiver, method))
        return signature.abi_lowering if signature is not None else None


CONTRACT_GRAPH = BuiltinContractGraph(
    INTRINSIC_SIGNATURES,
    INSTANCE_METHOD_SIGNATURES,
    BUILTIN_FUNCTION_SIGNATURES,
    _ABI_LOWERINGS,
)


def intrinsic_signature(name: str) -> IntrinsicSignature | None:
    return CONTRACT_GRAPH.intrinsic(name)


def contextual_result_type(result_type: str, expected: str | None) -> str:
    """Preserve a caller's Result error row while using canonical Ok type."""
    if not result_type.startswith("Result[") or not expected or not expected.startswith("Result["):
        return result_type
    canonical = result_type[7:-1].split(",", 1)
    contextual = expected[7:-1].split(",", 1)
    if len(canonical) == 2 and len(contextual) == 2 and canonical[0] == contextual[0]:
        return f"Result[{canonical[0]},{contextual[1]}]"
    return result_type


def format_intrinsic_arity(signature: IntrinsicSignature, actual: int) -> str:
    return (
        f"IntrinsicArityMismatch: {signature.name} expects {signature.arity} "
        f"argument(s), got {actual}"
    )


INTRINSIC_EFFECTS = frozenset(row.effect for row in _INTRINSIC_ROWS)

__all__ = [
    "BUILTIN_FUNCTIONS",
    "BUILTIN_FUNCTION_SIGNATURES",
    "BUILTIN_RECEIVERS",
    "BoundContractGraph",
    "BuiltinFunctionSignature",
    "BuiltinContractGraph",
    "CONTRACT_GRAPH",
    "INTRINSIC_EFFECTS",
    "INTRINSIC_SIGNATURES",
    "INSTANCE_METHOD_NAMES",
    "INSTANCE_METHOD_SIGNATURES",
    "InstanceMethodSignature",
    "IntrinsicSignature",
    "TypeScheme",
    "TypeConstructorId",
    "TypeSchemeApplied",
    "TypeSchemeConcrete",
    "TypeSchemeConst",
    "TypeSchemeNode",
    "TypeSchemeVar",
    "TypeVarId",
    "contextual_result_type",
    "format_intrinsic_arity",
    "intrinsic_signature",
]
