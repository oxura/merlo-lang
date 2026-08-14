"""Immutable canonical contracts for host intrinsics.

Every frontend and runtime adapter uses this table for intrinsic identity,
argument validation, result adaptation, effects, and ownership metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


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
    _signature("console.write", ("TextView",), "Unit", "console.write", parameter_ownership=("borrow",)),
    _signature("fs.open_read", ("Path",), "Result[FileReader,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read", ("Path",), "Result[Bytes,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read_text", ("Path",), "Result[Text,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read_chunk", ("FileReader", "UInt64"), "Result[Bytes,FileError]", "fs.read", parameter_ownership=("borrow_mut", "value"), result_ownership="owned"),
    _signature("fs.open_write", ("Path",), "Result[FileReader,FileError]", "fs.write", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.write", ("Path", "BytesView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow", "borrow")),
    _signature("fs.write_text", ("Path", "TextView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow", "borrow")),
    _signature("fs.write_chunk", ("FileReader", "BytesView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow_mut", "borrow")),
    _signature("fs.close", ("FileReader",), "Result[Unit,FileError]", "fs.write", parameter_ownership=("consuming",)),
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
)
INTRINSIC_SIGNATURES: Mapping[str, IntrinsicSignature] = MappingProxyType(
    {row.name: row for row in _INTRINSIC_ROWS}
)


def intrinsic_signature(name: str) -> IntrinsicSignature | None:
    return INTRINSIC_SIGNATURES.get(name)


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
    "INTRINSIC_EFFECTS",
    "INTRINSIC_SIGNATURES",
    "IntrinsicSignature",
    "contextual_result_type",
    "format_intrinsic_arity",
    "intrinsic_signature",
]
