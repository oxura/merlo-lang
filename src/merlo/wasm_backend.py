"""Small dependency-free WebAssembly backend for pure scalar MIR."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class WasmCompileError(ValueError):
    def __init__(self, message: str, code: str = "WasmUnsupported") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class WasmDiagnostic:
    code: str
    message: str
    function: str | None = None
    operation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "function": self.function, "operation": self.operation}


@dataclass(frozen=True)
class WasmArtifact:
    wasm: bytes
    source_digest: str
    compiler_digest: str
    artifact_digest: str
    exports: tuple[str, ...]
    diagnostics: tuple[WasmDiagnostic, ...] = ()
    schema: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.wasm, bytes) or not self.wasm.startswith(b"\x00asm\x01\x00\x00\x00"):
            raise WasmCompileError("invalid wasm module", "WasmArtifactInvalid")
        for name, value in (
            ("source_digest", self.source_digest),
            ("compiler_digest", self.compiler_digest),
            ("artifact_digest", self.artifact_digest),
        ):
            if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise WasmCompileError(name, "WasmArtifactInvalid")
        if hashlib.sha256(self.wasm).hexdigest() != self.artifact_digest:
            raise WasmCompileError("artifact digest mismatch", "WasmArtifactTampered")
        if not isinstance(self.exports, tuple) or any(not isinstance(item, str) or not item for item in self.exports):
            raise WasmCompileError("invalid exports", "WasmArtifactInvalid")

    @property
    def bytes(self) -> bytes:
        return self.wasm

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "source_digest": self.source_digest, "compiler_digest": self.compiler_digest, "artifact_digest": self.artifact_digest, "exports": list(self.exports), "diagnostics": [d.to_dict() for d in self.diagnostics], "size": len(self.wasm)}

    @property
    def digest(self) -> str:
        return self.artifact_digest

    @property
    def module(self) -> bytes:
        return self.wasm
_SUPPORTED = {"const", "literal", "add", "sub", "mul", "checked_uint64_add", "checked_uint64_sub", "checked_uint64_mul", "load_local", "return", "identity"}
_EFFECTFUL = {"call", "allocate", "open_file_reader", "borrow_lines", "primitive_call", "store_field", "store_local", "drop_value", "result_branch", "load_field"}


def _u32(value: int) -> bytes:
    out = bytearray()
    value = int(value)
    while True:
        byte = value & 0x7F
        value >>= 7
        if value: out.append(byte | 0x80)
        else:
            out.append(byte); return bytes(out)


def _s64(value: int) -> bytes:
    out = bytearray(); value = int(value)
    more = True
    while more:
        byte = value & 0x7F; value >>= 7
        more = not ((value == 0 and not (byte & 0x40)) or (value == -1 and (byte & 0x40)))
        out.append(byte | (0x80 if more else 0))
    return bytes(out)


def _vec(items: Sequence[bytes]) -> bytes:
    return _u32(len(items)) + b"".join(items)


def _section(number: int, payload: bytes) -> bytes:
    return bytes([number]) + _u32(len(payload)) + payload


def _name(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _u32(len(encoded)) + encoded


def _type_name(type_name: str | None) -> int:
    if type_name in {"Bool", "Byte", "UInt32", "Int32", "u32", "i32"}:
        return 0x7F
    if type_name in {"UInt64", "Int64", "u64", "i64"}:
        return 0x7E
    raise WasmCompileError(f"unsupported scalar type {type_name}", "WasmUnsupportedType")


def _constant(value: Any, type_name: str) -> bytes:
    if type(value) is not int:
        raise WasmCompileError("integer constant required", "WasmInvalidMIR")
    if type_name in {"Bool"}:
        if value not in {0, 1}:
            raise WasmCompileError("Bool constant must be 0 or 1", "WasmConstantOutOfRange")
        return b"\x41" + _s64(value)
    if type_name in {"Byte", "UInt32", "u32"}:
        if not 0 <= value < 2**32:
            raise WasmCompileError("UInt32 constant out of range", "WasmConstantOutOfRange")
        signed = value if value < 2**31 else value - 2**32
        return b"\x41" + _s64(signed)
    if type_name in {"Int32", "i32"}:
        if not -(2**31) <= value < 2**31:
            raise WasmCompileError("Int32 constant out of range", "WasmConstantOutOfRange")
        return b"\x41" + _s64(value)
    if type_name in {"UInt64", "u64"}:
        if not 0 <= value < 2**64:
            raise WasmCompileError("UInt64 constant out of range", "WasmConstantOutOfRange")
        signed = value if value < 2**63 else value - 2**64
        return b"\x42" + _s64(signed)
    if type_name in {"Int64", "i64"}:
        if not -(2**63) <= value < 2**63:
            raise WasmCompileError("Int64 constant out of range", "WasmConstantOutOfRange")
        return b"\x42" + _s64(value)
    raise WasmCompileError(f"unsupported scalar type {type_name}", "WasmUnsupportedType")


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping): return obj.get(name, default)
    return getattr(obj, name, default)


def _functions(mir: Any) -> list[Any]:
    funcs = _field(mir, "functions")
    if funcs is None and isinstance(mir, Mapping): funcs = mir.get("function")
    if funcs is None: funcs = [mir]
    if isinstance(funcs, Mapping): funcs = [funcs]
    return list(funcs)

def _blocks(function: Any) -> list[Any]:
    return list(_field(function, "blocks", ()) or ())
def _instructions(function: Any) -> list[Any]:
    result: list[Any] = []
    for block in _blocks(function):
        result.extend(_field(block, "instructions", ()) or ())
    # Friendly input forms: functions can carry direct instructions/operations.
    if not result:
        result.extend(_field(function, "instructions", _field(function, "operations", ())) or ())
    return result


def _attrs(instruction: Any) -> dict[str, Any]:
    raw = _field(instruction, "attributes", {})
    if isinstance(raw, Mapping): return dict(raw)
    return dict(raw or ())


class WasmBackend:
    compiler_identity = "merlo-wasm-backend.scalar.v1"

    def __init__(self, *, compiler_digest: str | None = None) -> None:
        self.compiler_digest = compiler_digest or hashlib.sha256(self.compiler_identity.encode()).hexdigest()

    def compile(self, mir: Any, *, source: str | bytes | None = None, entry: str | None = None) -> WasmArtifact:
        functions = _functions(mir)
        if not functions: raise WasmCompileError("MIR has no functions", "WasmInvalidMIR")
        requested_entry = entry or _field(mir, "entry_function")
        chosen = (
            next((function for function in functions if _field(function, "name") == requested_entry), None)
            if requested_entry is not None
            else functions[0]
        )
        if chosen is None:
            raise WasmCompileError(f"unknown entry {requested_entry}", "WasmUnknownEntry")
        name = str(_field(chosen, "name", "main"))
        effects = tuple(str(e) for e in (_field(chosen, "effects", ()) or ()))
        if effects:
            raise WasmCompileError(f"function {name} declares effects: {', '.join(effects)}", "WasmEffectful")
        instructions = _instructions(chosen)
        checked = {"checked_uint64_add", "checked_uint64_sub", "checked_uint64_mul"}
        for instruction in instructions:
            op = str(_field(instruction, "op", ""))
            if op in checked:
                raise WasmCompileError(f"checked operation {op} is not lowered safely", "WasmCheckedArithmeticUnsupported")
            if op in _EFFECTFUL or (_field(instruction, "effects", ()) or ()):
                raise WasmCompileError(f"unsupported effectful operation {op}", "WasmEffectful")
            if op not in _SUPPORTED:
                raise WasmCompileError(f"unsupported operation {op}", "WasmUnsupportedOp")
        params = _field(chosen, "parameters", ()) or ()
        return_type_name = str(_field(chosen, "return_type", "UInt64"))
        result_type = _type_name(return_type_name)
        param_types = tuple(
            _type_name(
                str(
                    parameter[1]
                    if isinstance(parameter, (tuple, list)) and len(parameter) > 1
                    else _field(parameter, "type_name", _field(parameter, "type", "UInt64"))
                )
            )
            for parameter in params
        )
        if any(item != result_type for item in param_types):
            raise WasmCompileError("mixed scalar widths are unsupported", "WasmUnsupportedTypeMix")
        for instruction in instructions:
            instruction_type = _field(instruction, "type_name", _field(instruction, "type"))
            if instruction_type is not None and _type_name(str(instruction_type)) != result_type:
                raise WasmCompileError("mixed scalar widths are unsupported", "WasmUnsupportedTypeMix")
        body = self._function_body(instructions, params, result_type, return_type_name, chosen)
        wasm = self._module(name, param_types, result_type, body)
        source_bytes = source.encode() if isinstance(source, str) else (source or self._canonical_mir(mir))
        source_digest = hashlib.sha256(source_bytes).hexdigest()
        artifact_digest = hashlib.sha256(wasm).hexdigest()
        return WasmArtifact(wasm, source_digest, self.compiler_digest, artifact_digest, (name,))

    emit = compile

    @staticmethod
    def _canonical_mir(mir: Any) -> bytes:
        if hasattr(mir, "to_json"): value = mir.to_json()
        elif hasattr(mir, "to_dict"): value = json.dumps(mir.to_dict(), sort_keys=True, separators=(",", ":"))
        else: value = json.dumps(mir, sort_keys=True, default=str, separators=(",", ":"))
        return value.encode()

    def _function_body(self, instructions: list[Any], params: Any, result_type: int, result_type_name: str, function: Any) -> bytes:
        param_names = [str(p[0] if isinstance(p, (tuple, list)) else _field(p, "name", p)) for p in params]
        locals_by_name = {name: index for index, name in enumerate(param_names)}
        result_slots: dict[str, int] = {}
        next_slot = len(param_names)
        code = bytearray()
        def ref(value: Any) -> None:
            key = str(value)
            if key in result_slots: code.extend(b"\x20" + _u32(result_slots[key]))
            elif key in locals_by_name: code.extend(b"\x20" + _u32(locals_by_name[key]))
            else: raise WasmCompileError(f"unknown scalar value {key}", "WasmInvalidMIR")
        for instruction in instructions:
            op = str(_field(instruction, "op", ""))
            rid = _field(instruction, "result")
            if rid is None:
                rid = _field(instruction, "id")
            operands = tuple(_field(instruction, "operands", ()) or ()); attrs = _attrs(instruction)
            if op in {"return"}:
                if operands: ref(operands[0])
                elif _field(instruction, "value") is not None: ref(_field(instruction, "value"))
                else: code.extend((b"\x41" if result_type == 0x7F else b"\x42") + (b"\x00" if result_type == 0x7F else b"\x00"))
                code.append(0x0F); continue
            if op in {"load_local", "identity"} and operands:
                ref(operands[0])
            elif op in {"const", "literal"}:
                value = attrs.get("value", attrs.get("constant", _field(instruction, "value", 0)))
                code.extend(_constant(value, result_type_name))
            elif op in {"add", "sub", "mul"}:
                if len(operands) != 2:
                    raise WasmCompileError(f"{op} needs two operands", "WasmInvalidMIR")
                ref(operands[0])
                ref(operands[1])
                code.append(
                    {
                        "add": 0x6A if result_type == 0x7F else 0x7C,
                        "sub": 0x6B if result_type == 0x7F else 0x7D,
                        "mul": 0x6C if result_type == 0x7F else 0x7E,
                    }[op]
                )
            else:
                raise WasmCompileError(f"unsupported operation {op}", "WasmUnsupportedOp")
            if rid is not None:
                result_slots[str(rid)] = next_slot; code.extend(b"\x21" + _u32(next_slot)); next_slot += 1
        # General MIR stores returns on block terminators rather than instructions.
        terminated = code and code[-1] == 0x0F
        for terminator in [_field(block, "terminator") for block in _blocks(function)]:
            kind = str(_field(terminator, "kind", "")) if terminator is not None else ""
            if kind == "return":
                value = _field(terminator, "value")
                if value is None:
                    code.extend(b"\x41\x00" if result_type == 0x7F else b"\x42\x00")
                else:
                    ref(value)
                code.append(0x0F)
                terminated = True
                break
            if kind not in {"", "unreachable"}:
                raise WasmCompileError(f"control-flow terminator {kind} is outside scalar subset", "WasmUnsupportedOp")
        if not terminated:
            if result_slots:
                code.extend(b"\x20" + _u32(max(result_slots.values())))
            else:
                code.extend(b"\x41\x00" if result_type == 0x7F else b"\x42\x00")
            code.append(0x0F)
        code.append(0x0B)
        locals_count = max(0, next_slot - len(param_names))
        local_decl = _vec([_u32(locals_count) + bytes([result_type])]) if locals_count else b"\x00"
        return local_decl + bytes(code)

    @staticmethod
    def _module(name: str, param_types: tuple[int, ...], result_type: int, body: bytes) -> bytes:
        type_entry = b"\x60" + _u32(len(param_types)) + bytes(param_types) + b"\x01" + bytes([result_type])
        sections = [
            _section(1, _vec([type_entry])),
            _section(3, _vec([_u32(0)])),
            _section(7, _vec([_name(name) + b"\x00" + _u32(0)])),
            _section(10, _vec([_u32(len(body)) + body])),
        ]
        return b"\x00asm\x01\x00\x00\x00" + b"".join(sections)


compile_wasm = WasmBackend().compile
__all__ = ["WasmBackend", "WasmArtifact", "WasmDiagnostic", "WasmCompileError", "compile_wasm"]
