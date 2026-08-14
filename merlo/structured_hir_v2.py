"""Structured Typed HIR v2 for Merlo's general representation milestone.

The HIR is deliberately a tree. Control-flow graphs, allocation primitives, drop
flags, and pointer arithmetic belong to lower layers.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from .intrinsics import format_intrinsic_arity, intrinsic_signature
from .intrinsics import contextual_result_type


STRUCTURED_HIR_SCHEMA_VERSION = 2
STRUCTURED_HIR_CONTRACT = "merlo.structured-typed-hir.v2"
_OWNING_PREFIXES = ("Vec[", "Box[", "Map[", "Result[")
_OWNING_TYPES = {"Text", "Bytes", "TextBuilder", "Json", "FileReader", "Path"}
_SCALAR_TYPES = frozenset(
    {
        "Bool",
        "Byte",
        "Int8",
        "UInt8",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
        "Float32",
        "Float64",
    }
)
_INTEGER_TYPES = frozenset({"Byte", "Int8", "UInt8", "Int16", "UInt16", "Int32", "UInt32", "Int64", "UInt64"})
_TYPE_ALIASES = {"Int": "Int64", "UInt": "UInt64", "Float": "Float64"}


class StructuredHIRCompileError(ValueError):
    """Typed source/HIR construction failure."""


@dataclass(frozen=True)
class SourceSpan:
    path: str
    line: int
    column: int
    end_line: int
    end_column: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class HIRField:
    name: str
    type_name: str
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRVariant:
    name: str
    payload_type: str | None
    tag: int
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload_type": self.payload_type,
            "tag": self.tag,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRTypeDecl:
    name: str
    kind: str
    source: SourceSpan
    symbol_id: str
    revision_id: str
    fields: tuple[HIRField, ...] = ()
    variants: tuple[HIRVariant, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "fields": [item.to_dict() for item in self.fields],
            "variants": [item.to_dict() for item in self.variants],
        }


@dataclass(frozen=True)
class HIRParameter:
    name: str
    type_name: str
    ownership: str
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "ownership": self.ownership,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRNode:
    id: str
    kind: str
    source: SourceSpan
    scope_id: str
    type_name: str | None
    ownership: str
    effects: tuple[str, ...]
    symbol_id: str | None
    revision_id: str
    attributes: tuple[tuple[str, Any], ...] = ()
    children: tuple["HIRNode", ...] = ()

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)

    def walk(self) -> Iterable["HIRNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source.to_dict(),
            "scope_id": self.scope_id,
            "type": self.type_name,
            "ownership": self.ownership,
            "effects": list(self.effects),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "attributes": dict(self.attributes),
            "children": [item.to_dict() for item in self.children],
        }


@dataclass(frozen=True)
class HIRFunction:
    name: str
    parameters: tuple[HIRParameter, ...]
    return_type: str
    effects: tuple[str, ...]
    body: tuple[HIRNode, ...]
    source: SourceSpan
    scope_id: str
    symbol_id: str
    revision_id: str

    def walk(self) -> Iterable[HIRNode]:
        for node in self.body:
            yield from node.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "body": [item.to_dict() for item in self.body],
            "source": self.source.to_dict(),
            "scope_id": self.scope_id,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class StructuredHIRProgram:
    source: str
    path: str
    source_sha256: str
    types: tuple[HIRTypeDecl, ...]
    functions: tuple[HIRFunction, ...]
    entry_function: str
    schema_version: int = STRUCTURED_HIR_SCHEMA_VERSION
    contract: str = STRUCTURED_HIR_CONTRACT
    native_module: ast.Module | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.schema_version != STRUCTURED_HIR_SCHEMA_VERSION:
            raise ValueError("Structured HIR schema version drift")
        type_names = [item.name for item in self.types]
        function_names = [item.name for item in self.functions]
        if len(type_names) != len(set(type_names)):
            raise ValueError("duplicate Structured HIR type")
        if len(function_names) != len(set(function_names)):
            raise ValueError("duplicate Structured HIR function")
        if self.entry_function not in set(function_names):
            raise ValueError("missing Structured HIR entry function")
        node_ids = [node.id for function in self.functions for node in function.walk()]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate Structured HIR node id")
        forbidden = {"BasicBlock", "Goto", "Malloc", "Free", "DropFlag", "RawPointer"}
        actual = {node.kind for function in self.functions for node in function.walk()}
        if actual & forbidden:
            raise ValueError("CFG or raw-memory detail escaped into Structured HIR")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def type_decl(self, name: str) -> HIRTypeDecl:
        return next(item for item in self.types if item.name == name)

    def function(self, name: str) -> HIRFunction:
        return next(item for item in self.functions if item.name == name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "path": self.path,
            "source_sha256": self.source_sha256,
            "entry_function": self.entry_function,
            "types": [item.to_dict() for item in self.types],
            "functions": [item.to_dict() for item in self.functions],
            "invariants": {
                "structured_program_tree": True,
                "cfg_absent": True,
                "raw_memory_absent": True,
                "stable_symbol_ids": True,
                "stable_revision_ids": True,
                "source_scopes": True,
                "source_mappings": True,
                "ownership_modes": True,
                "effect_sets": True,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class _Preprocessed:
    source: str
    declaration_kinds: dict[str, str]
    binding_kinds: dict[int, str]


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"


def _span(path: str, node: ast.AST) -> SourceSpan:
    return SourceSpan(
        path,
        int(getattr(node, "lineno", 1)),
        int(getattr(node, "col_offset", 0)) + 1,
        int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0))) + 1,
    )


def _type_name(node: ast.AST | None) -> str:
    if node is None:
        return "Unit"
    type_name = ast.unparse(node).replace(" ", "")
    for alias, canonical in _TYPE_ALIASES.items():
        type_name = re.sub(rf"\b{alias}\b", canonical, type_name)
    try:
        return validate_type_expr(parse_type(type_name)).canonical
    except ValueError as error:
        raise StructuredHIRCompileError(f"MalformedType: {type_name}") from error

_DEFAULT_MAP = "Map[Text,UInt64]"


def _map_types(type_name: str | None) -> tuple[str, str] | None:
    parts = generic_parts(type_name, "Map", arity=2)
    return parts if parts is not None else None  # type: ignore[return-value]


def _sum_variants(type_name: str | None) -> dict[str, str | None] | None:
    option = generic_parts(type_name, "Option", arity=1)
    if option is not None:
        return {"NoneValue": None, "Some": option[0]}
    result = generic_parts(type_name, "Result", arity=2)
    if result is not None:
        return {"Ok": result[0], "Err": result[1]}
    return None


def _callback_parts(type_name: str) -> tuple[tuple[str, ...], str] | None:
    parts = generic_parts(type_name, "Fn")
    if parts is None or len(parts) < 2:
        return None
    return parts[:-1], parts[-1]


def _function_callback_type(function: ast.FunctionDef) -> str:
    parameters = [_type_name(item.annotation) for item in function.args.args]
    return "Fn[" + ",".join((*parameters, _type_name(function.returns))) + "]"


def _validate_map_specializations(module: ast.Module, path: str) -> None:
    def validate(annotation: ast.AST) -> None:
        if isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name) and annotation.value.id == "Map":
                specialization = _type_name(annotation)
                map_types = _map_types(specialization)
                if (
                    map_types is None
                    or map_types[0] != "Text"
                    or map_types[1] not in _SCALAR_TYPES
                ):
                    raise StructuredHIRCompileError(
                        f"{path}:{getattr(annotation, 'lineno', 1)}: unsupported Map "
                        f"specialization {specialization}; alpha Map requires "
                        "Text keys and scalar values"
                    )
                validate(annotation.slice)
                return
            for child in ast.iter_child_nodes(annotation):
                validate(child)
            return
        if isinstance(annotation, ast.Name):
            if annotation.id == "Any":
                raise StructuredHIRCompileError(
                    f"{path}:{getattr(annotation, 'lineno', 1)}: DynamicAnyForbidden"
                )
            if annotation.id == "Map":
                raise StructuredHIRCompileError(
                    f"{path}:{getattr(annotation, 'lineno', 1)}: unsupported Map; "
                    "alpha Map requires Text keys and scalar values"
                )
            return
        for child in ast.iter_child_nodes(annotation):
            validate(child)

    for node in ast.walk(module):
        annotation: ast.AST | None = None
        if isinstance(node, ast.AnnAssign):
            annotation = node.annotation
        elif isinstance(node, ast.arg):
            annotation = node.annotation
        elif isinstance(node, ast.FunctionDef):
            annotation = node.returns
        if annotation is not None:
            validate(annotation)


def _is_owned(type_name: str | None) -> bool:
    return bool(type_name) and (
        type_name in _OWNING_TYPES or type_name.startswith(_OWNING_PREFIXES)
    )


def _is_borrowed(type_name: str | None) -> bool:
    return bool(type_name) and (
        type_name in {"BytesView", "TextView", "FileLines"}
        or type_name.startswith(("Slice[", "Borrow["))
    )


def _rewrite_postfix_try(line: str) -> str:
    """Preserve postfix ``?`` as an explicit marker before Python parsing."""
    cursor = 0
    quote: str | None = None
    while cursor < len(line):
        character = line[cursor]
        if quote is not None:
            if character == "\\":
                cursor += 2
                continue
            if character == quote:
                quote = None
            cursor += 1
            continue
        if character in {'"', "'"}:
            quote = character
            cursor += 1
            continue
        if character != "?" or (cursor > 0 and line[cursor - 1] == "?") or (
            cursor + 1 < len(line) and line[cursor + 1] == "?"
        ):
            cursor += 1
            continue
        end = cursor
        start = end - 1
        while start >= 0 and line[start].isspace():
            start -= 1
        if start < 0:
            return line
        if line[start] == ")":
            depth = 1
            start -= 1
            while start >= 0 and depth:
                if line[start] == ")":
                    depth += 1
                elif line[start] == "(":
                    depth -= 1
                start -= 1
            if depth:
                return line
            start += 1
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_."):
                start -= 1
        elif line[start].isalnum() or line[start] == "_":
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_."):
                start -= 1
        else:
            return line
        expression = line[start:end].rstrip()
        line = f"{line[:start]}__merlo_try__({expression}){line[end + 1:]}"
        cursor = start + len("__merlo_try__(") + len(expression) + 1
    return line


def _preprocess(source: str) -> _Preprocessed:
    declaration_kinds: dict[str, str] = {}
    binding_kinds: dict[int, str] = {}
    output = []
    for line_number, line in enumerate(source.splitlines(), 1):
        declaration = re.match(r"^(\s*)(record|enum)\s+([A-Za-z_]\w*)\s*:\s*$", line)
        if declaration:
            indent, kind, name = declaration.groups()
            declaration_kinds[name] = kind
            output.append(f"{indent}class {name}:")
            continue
        constant = re.match(r"^(\s*)const\s+", line)
        if constant:
            line = re.sub(r"^(\s*)const\s+", r"\1", line)
        function = re.match(r"^(\s*)(fn|task)\s+", line)
        if function:
            line = re.sub(r"^(\s*)(?:fn|task)\s+", r"\1def ", line)
        binding = re.match(r"^(\s*)(let|var)\s+", line)
        if binding:
            binding_kinds[line_number] = binding.group(2)
            line = re.sub(r"^(\s*)(?:let|var)\s+", r"\1", line)
        if re.fullmatch(r"\s*uses\s+.+", line):
            line = ""
        line = re.sub(r"\bOption\.None\b", "Option.NoneValue", line)
        line = _rewrite_postfix_try(line)
        line = re.sub(r"\btrue\b", "True", line)
        line = re.sub(r"\bfalse\b", "False", line)
        if re.search(r"\b(?:and|or)\s*$", line):
            line += " " + chr(92)
        output.append(line)
    return _Preprocessed("\n".join(output) + "\n", declaration_kinds, binding_kinds)

def _preprocess_ffi_surface(source: str) -> str:
    """Erase declarations that are parsed by :mod:`merlo.ffi` before Python AST parsing."""
    output: list[str] = []
    extern_block = False
    for line in source.splitlines():
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        if re.match(r'^extern\s*(?:"C"|C)\s*[{:]?\s*$', stripped):
            extern_block = True
            output.append(f"{indent}# extern C")
            continue
        if extern_block:
            if stripped in {"}", "};"}:
                extern_block = False
            output.append(f"{indent}# extern declaration")
            continue
        if re.match(r'^extern\s*(?:"C"|C)\s*(?:fn\s+)?[A-Za-z_]\w*\s*\(', stripped):
            output.append(f"{indent}# extern declaration")
            continue
        if re.match(r"^(?:@repr\(C\)\s+|repr\(C\)\s+)(record|enum)\s+", stripped):
            output.append(re.sub(r"^(?P<i>\s*)(?:@repr\(C\)\s+|repr\(C\)\s+)", r"\g<i>", line))
            continue
        if re.match(r"^\s*unsafe\s*:\s*$", line):
            output.append(re.sub(r"unsafe\s*:", "if __merlo_unsafe_scope__:", line))
            continue
        output.append(line)
    return "\n".join(output) + ("\n" if source.endswith("\n") else "")


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
                    "increment",
                }
            ):
                assigned.add(root.id)
    return assigned


@dataclass
class _OwnershipState:
    statuses: dict[str, str]
    borrows: dict[str, tuple[str, str]]
    terminal: bool = False

    def clone(self) -> "_OwnershipState":
        return _OwnershipState(dict(self.statuses), dict(self.borrows), self.terminal)

class _OwnershipChecker:
    """Conservative source ownership analysis used to gate HIR construction."""

    def __init__(
        self,
        path: str,
        types: dict[str, HIRTypeDecl],
        functions: dict[str, ast.FunctionDef],
    ) -> None:
        self.path = path
        self.types = types
        self.functions = functions
        self.current: ast.FunctionDef | None = None
        self.env: dict[str, str] = {}
        self.parameters: set[str] = set()

    def _error(self, name: str, variable: str | None = None) -> None:
        suffix = f": {variable}" if variable else ""
        raise StructuredHIRCompileError(f"{name}{suffix}")

    def _owner(self, type_name: str | None) -> bool:
        return _is_owned(type_name) or bool(type_name and type_name in self.types)

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
                    return _type_name(self.functions[name].returns)
                if name in self.types:
                    return name
                if name in {"Text", "Bytes", "Path", "TextBuilder"}:
                    return name
                if name == "drop":
                    return "Unit"
            if isinstance(node.func, ast.Attribute):
                receiver = self._expr_type(node.func.value)
                method = node.func.attr
                receiver_text = ast.unparse(node.func.value)
                if receiver_text == "Text" and method == "from_bytes":
                    return "Text"
                if receiver_text == "TextBuilder" and method == "new":
                    return "TextBuilder"
                if receiver_text == "Vec" and method == "new":
                    return expected or "Vec[Inferred]"
                if receiver_text == "Map" and method == "new":
                    return expected or _DEFAULT_MAP
                if receiver_text == "Box" and method == "new":
                    return expected or "Box[Inferred]"
                if method == "as_view" and receiver == "Text":
                    return "TextView"
                if method == "view" and receiver == "Bytes":
                    return "BytesView"
                if method == "view" and receiver and receiver.startswith("Vec["):
                    return f"Borrow[{receiver}]"
                if method in {"get", "get_mut"} and receiver:
                    vec_parts = generic_parts(receiver, "Vec", arity=1)
                    if vec_parts is not None:
                        return vec_parts[0]
                if method == "get" and (map_types := _map_types(receiver)) is not None:
                    return map_types[1]
                if method == "entries" and receiver and receiver.startswith("Map["):
                    return f"Borrow[{receiver}]"
                if method == "to_text" and receiver == "Path":
                    return "Text"
                if method == "finish" and receiver == "TextBuilder":
                    return "Text"
        if isinstance(node, ast.Subscript):
            owner = self._expr_type(node.value)
            vec_parts = generic_parts(owner, "Vec", arity=1)
            if vec_parts is not None:
                return vec_parts[0]
        return expected

    @staticmethod
    def _root_name(node: ast.AST | None) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    @staticmethod
    def _borrow_source(node: ast.AST | None) -> ast.AST | None:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.value
        return node

    def _check_name(self, name: str, state: _OwnershipState) -> None:
        status = state.statuses.get(name)
        if status == "moved":
            self._error("UseAfterMove", name)
        if status == "dropped":
            self._error("UseAfterDrop", name)
    def _consume(self, name: str, state: _OwnershipState) -> None:
        self._check_name(name, state)
        if name in state.statuses:
            state.statuses[name] = "moved"


    def _check_mutation(self, name: str, state: _OwnershipState) -> None:
        self._check_name(name, state)
        if any(owner == name for owner, _ in state.borrows.values()):
            self._error("MutationDuringBorrow", name)

    def _borrow_result(self, expression: ast.AST, result_type: str | None, state: _OwnershipState) -> None:
        if not _is_borrowed(result_type):
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
            name = node.func.id if isinstance(node.func, ast.Name) else ast.unparse(node.func)
            if isinstance(node.func, ast.Name) and node.func.id == "drop":
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
                    self._error("InvalidDrop")
                target = node.args[0].id
                if target in state.borrows:
                    del state.borrows[target]
                    return "Unit"
                if state.statuses.get(target) == "dropped":
                    self._error("DuplicateDrop", target)
                self._check_name(target, state)
                state.statuses[target] = "dropped"
                return "Unit"
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            receiver_type = self._expr_type(receiver)
            method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            receiver_root = self._root_name(receiver)
            if receiver_root is not None:
                self._check_name(receiver_root, state)
            if receiver_root and method in {
                "push", "get_mut", "insert", "increment",
                "append_byte", "append_scalar", "append_text", "append_uint64",
            }:
                self._check_mutation(receiver_root, state)
            if receiver_root and method in {"view", "get", "get_mut", "entries", "as_view"}:
                self._check_name(receiver_root, state)
            if receiver is not None:
                self._check_expr(receiver, state)
            argument_types = [
                self._check_expr(argument, state)
                for argument in node.args
            ]
            signature = intrinsic_signature(name)
            if signature is not None:
                for argument, parameter_ownership in zip(
                    node.args, signature.parameter_ownership, strict=True
                ):
                    root = self._root_name(argument)
                    if root is None:
                        continue
                    if parameter_ownership == "borrow_mut":
                        self._check_mutation(root, state)
                    elif parameter_ownership in {"owned", "consuming"}:
                        self._consume(root, state)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                callee = self.functions[node.func.id]
                for argument, parameter in zip(node.args, callee.args.args):
                    parameter_type = _type_name(parameter.annotation)
                    returned = any(
                        isinstance(item, ast.Return)
                        and isinstance(item.value, ast.Name)
                        and item.value.id == parameter.arg
                        for item in ast.walk(callee)
                    )
                    if self._owner(parameter_type) and returned and isinstance(argument, ast.Name):
                        if isinstance(argument, ast.Name):
                            self._consume(argument.id, state)
            elif receiver_root and method == "push" and node.args:
                vec_parts = generic_parts(receiver_type, "Vec", arity=1)
                element = vec_parts[0] if vec_parts is not None else None
                if self._owner(element) and isinstance(node.args[0], ast.Name):
                    self._consume(node.args[0].id, state)
            elif receiver_text := (
                ast.unparse(receiver) if receiver is not None else ""
            ):
                if receiver_text == "Box" and method == "new" and node.args:
                    argument = node.args[0]
                    if isinstance(argument, ast.Name) and self._owner(self._expr_type(argument)):
                        self._consume(argument.id, state)
            result_type = self._expr_type(node, expected)
            self._borrow_result(node.func.value if receiver is not None else node, result_type, state)
            return result_type
    def _merge(self, before: _OwnershipState, branches: tuple[_OwnershipState, ...]) -> _OwnershipState:
        live = tuple(branch for branch in branches if not branch.terminal)
        if not live:
            return _OwnershipState(dict(before.statuses), dict(before.borrows), True)
        for name in sorted(before.statuses):
            statuses = {
                branch.statuses.get(name, before.statuses.get(name))
                for branch in live
            }
            if len(statuses) > 1:
                self._error("OwnershipAmbiguity", name)
        borrows = [branch.borrows for branch in live]
        if borrows and any(item != borrows[0] for item in borrows[1:]):
            self._error("OwnershipAmbiguity")
        return _OwnershipState(dict(live[0].statuses), dict(live[0].borrows), False)

    def _check_statements(self, statements: list[ast.stmt], state: _OwnershipState) -> _OwnershipState:
        for node in statements:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                type_name = _type_name(node.annotation)
                if node.value is not None:
                    value_type = self._check_expr(node.value, state, expected=type_name)
                    if self._owner(type_name) and isinstance(node.value, ast.Name):
                        self._consume(node.value.id, state)
                    if _is_borrowed(value_type or type_name):
                        borrow_source = (
                            node.value.func.value
                            if isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Attribute)
                            else node.value
                        )
                        root = self._root_name(borrow_source)
                        if root is not None:
                            state.borrows[node.target.id] = (root, "shared")
                self.env[node.target.id] = type_name
                if self._owner(type_name):
                    state.statuses[node.target.id] = "available"
                continue
            if isinstance(node, ast.Assign):
                value_type = self._check_expr(node.value, state)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        target_type = self.env.get(target.id)
                        if target_type is None and self._owner(value_type):
                            self._error("UnsafeOwnershipInference", target.id)
                        if target_type and self._owner(target_type):
                            if isinstance(node.value, ast.Name):
                                self._consume(node.value.id, state)
                            state.statuses[target.id] = "available"
                continue
            if isinstance(node, ast.Expr):
                self._check_expr(node.value, state)
                if (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "__merlo_try__"
                ):
                    state.terminal = True
                    break
                continue
            if isinstance(node, ast.Return):
                result_type = _type_name(self.current.returns if self.current else None)
                root = self._root_name(self._borrow_source(node.value))
                escape_root = (
                    state.borrows[root][0]
                    if root is not None and root in state.borrows
                    else root
                )
                if (
                    _is_borrowed(result_type)
                    and escape_root is not None
                    and escape_root not in self.parameters
                ):
                    self._error("EscapedView", escape_root)
                value_type = self._check_expr(node.value, state, expected=result_type)
                if self._owner(result_type) and isinstance(node.value, ast.Name):
                    self._consume(node.value.id, state)
                self._borrow_result(node.value, value_type or result_type, state)
                state.terminal = True
                break
            if isinstance(node, ast.If):
                self._check_expr(node.test, state)
                then_state = self._check_statements(node.body, state.clone())
                else_state = self._check_statements(node.orelse, state.clone())
                state = self._merge(state, (then_state, else_state))
                continue
            if isinstance(node, ast.While):
                self._check_expr(node.test, state)
                loop_state = self._check_statements(node.body, state.clone())
                self._merge(state, (state, loop_state))
                continue
            if isinstance(node, ast.For):
                self._check_expr(node.iter, state)
                loop_state = state.clone()
                before_statuses = set(loop_state.statuses)
                before_borrows = set(loop_state.borrows)
                if isinstance(node.target, ast.Name):
                    self.env[node.target.id] = "Inferred"
                loop_state = self._check_statements(node.body, loop_state)
                for name in set(loop_state.statuses) - before_statuses:
                    loop_state.statuses.pop(name, None)
                for name in set(loop_state.borrows) - before_borrows:
                    loop_state.borrows.pop(name, None)
                state = self._merge(state, (state, loop_state))
                continue
            if isinstance(node, ast.Match):
                self._check_expr(node.subject, state)
                branches = []
                before_statuses = set(state.statuses)
                before_borrows = set(state.borrows)
                for case in node.cases:
                    branch = state.clone()
                    self._check_statements(case.body, branch)
                    for name in set(branch.statuses) - before_statuses:
                        branch.statuses.pop(name, None)
                    for name in set(branch.borrows) - before_borrows:
                        branch.borrows.pop(name, None)
                    branches.append(branch)
                if branches:
                    state = self._merge(state, tuple(branches))
                continue
        return state

    def check(self) -> None:
        for function in self.functions.values():
            self.current = function
            self.env = {
                argument.arg: _type_name(argument.annotation)
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
            )
            self._check_statements(function.body, state)


class _HIRBuilder:
    def __init__(
        self,
        path: str,
        source: str,
        preprocessed: _Preprocessed,
        types: dict[str, HIRTypeDecl],
        functions: dict[str, ast.FunctionDef],
        ffi_program: FFIProgram | None = None,
    ) -> None:
        self.path = path
        self.source = source
        self.preprocessed = preprocessed
        self.types = types
        self.functions = functions
        self.ffi_program = ffi_program or FFIProgram()
        self.extern_functions = {item.name: item for item in self.ffi_program.extern_functions}
        self.function_symbols = {
            name: _stable_id("shirs", path, "function", name) for name in functions
        }
        self.mutable_parameters = self._mutable_parameter_table(functions)
        self.local_types: dict[str, str] = {}
        self.current_function = ""
        self.ordinal = 0

    @staticmethod
    def _mutable_parameter_table(
        functions: dict[str, ast.FunctionDef],
    ) -> dict[str, set[str]]:
        mutable = {
            name: _assigned_parameter_names(function)
            for name, function in functions.items()
        }
        parameter_names = {
            name: {parameter.arg for parameter in function.args.args}
            for name, function in functions.items()
        }
        changed = True
        while changed:
            changed = False
            for caller_name, caller in functions.items():
                for call in (
                    node
                    for node in ast.walk(caller)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in functions
                ):
                    callee = functions[call.func.id]
                    for argument, parameter in zip(
                        call.args,
                        callee.args.args,
                    ):
                        if (
                            parameter.arg in mutable[call.func.id]
                            and isinstance(argument, ast.Name)
                            and argument.id in parameter_names[caller_name]
                            and argument.id not in mutable[caller_name]
                        ):
                            mutable[caller_name].add(argument.id)
                            changed = True
        return mutable

    def _scope(self, suffix: str = "body") -> str:
        return _stable_id("scope", self.path, self.current_function, suffix)

    def _new_node(
        self,
        node: ast.AST,
        kind: str,
        *,
        type_name: str | None = None,
        ownership: str = "value",
        effects: Iterable[str] = (),
        symbol_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        children: Iterable[HIRNode] = (),
        scope_id: str | None = None,
    ) -> HIRNode:
        self.ordinal += 1
        source = _span(self.path, node)
        attrs = tuple(sorted((attributes or {}).items()))
        child_tuple = tuple(children)
        revision = _stable_id(
            "rev",
            kind,
            type_name,
            ownership,
            tuple(sorted(set(effects))),
            attrs,
            tuple(item.revision_id for item in child_tuple),
        )
        return HIRNode(
            _stable_id(
                "shirn",
                self.path,
                self.current_function,
                source.to_dict(),
                kind,
                self.ordinal,
            ),
            kind,
            source,
            scope_id or self._scope(),
            type_name,
            ownership,
            tuple(sorted(set(effects))),
            symbol_id,
            revision,
            attrs,
            child_tuple,
        )

    def expression(
        self,
        node: ast.AST,
        *,
        expected: str | None = None,
    ) -> HIRNode:
        if isinstance(node, ast.Name):
            type_name = self.local_types.get(node.id)
            symbol = _stable_id(
                "shirs",
                self.path,
                self.current_function,
                "local",
                node.id,
            )
            if node.id in self.functions and type_name is None:
                type_name = _function_callback_type(self.functions[node.id])
                symbol = self.function_symbols[node.id]
            return self._new_node(
                node,
                "Name",
                type_name=type_name,
                ownership="borrow" if _is_owned(type_name) else "value",
                symbol_id=symbol,
                attributes={"name": node.id},
            )
        if isinstance(node, ast.Constant):
            type_name = (
                "Bool"
                if isinstance(node.value, bool)
                else "UInt64"
                if isinstance(node.value, int)
                else "Float64"
                if isinstance(node.value, float)
                else "Text"
                if isinstance(node.value, str)
                else "Unit"
            )
            return self._new_node(
                node,
                "Literal",
                type_name=type_name,
                attributes={"value": node.value},
            )
        if isinstance(node, ast.Attribute):
            owner = self.expression(node.value)
            type_name = self._attribute_type(owner.type_name, node.attr)
            return self._new_node(
                node,
                "FieldAccess",
                type_name=type_name,
                ownership="borrow" if _is_owned(type_name) else "value",
                attributes={"field": node.attr},
                children=(owner,),
            )
        if isinstance(node, ast.Call):
            arguments = tuple(self.expression(item) for item in node.args)
            return self._call(node, arguments, expected=expected)
        if isinstance(node, ast.BinOp):
            children = (self.expression(node.left), self.expression(node.right))
            numeric = {
                item.type_name
                for item in children
                if item.type_name is not None
            }
            if "Bool" in numeric or not numeric <= _SCALAR_TYPES - {"Bool"}:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: NumericOperandsRequired "
                    f"{tuple(item.type_name for item in children)}"
                )
            non_literals = {
                item.type_name
                for item in children
                if item.kind != "Literal" and item.type_name is not None
            }
            type_name = next(
                iter(non_literals),
                next(iter(numeric), "UInt64"),
            )
            return self._new_node(
                node,
                "Binary",
                type_name=type_name,
                attributes={"operator": type(node.op).__name__, "overflow": "checked"},
                children=children,
            )
        if isinstance(node, ast.BoolOp):
            return self._new_node(
                node,
                "Boolean",
                type_name="Bool",
                attributes={"operator": type(node.op).__name__},
                children=tuple(self.expression(item) for item in node.values),
            )
        if isinstance(node, ast.Compare):
            children = (self.expression(node.left),) + tuple(
                self.expression(item) for item in node.comparators
            )
            comparable = {
                item.type_name
                for item in children
                if item.type_name not in {None, "Inferred"}
            }
            if len(comparable) > 1:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: ComparableOperandsRequired "
                    f"{tuple(item.type_name for item in children)}"
                )
            return self._new_node(
                node,
                "Compare",
                type_name="Bool",
                attributes={"operators": [type(item).__name__ for item in node.ops]},
                children=children,
            )
        if isinstance(node, ast.UnaryOp):
            child = self.expression(node.operand)
            return self._new_node(
                node,
                "Unary",
                type_name="Bool" if isinstance(node.op, ast.Not) else child.type_name,
                attributes={"operator": type(node.op).__name__},
                children=(child,),
            )
        if isinstance(node, (ast.List, ast.Tuple)):
            children = tuple(self.expression(item) for item in node.elts)
            element_types = {item.type_name for item in children}
            if len(element_types) > 1:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: HeterogeneousArray "
                    f"{sorted(str(item) for item in element_types)}"
                )
            element_type = next(iter(element_types), "Unit")
            return self._new_node(
                node,
                "ArrayLiteral",
                type_name=f"Array[{element_type},{len(children)}]",
                ownership="owned" if _is_owned(element_type) else "value",
                attributes={"length": len(children)},
                children=children,
            )
        if isinstance(node, ast.Subscript):
            owner = self.expression(node.value)
            return self._new_node(
                node,
                "Index",
                ownership="borrow",
                effects=("bounds_check",),
                children=(owner, self.expression(node.slice)),
            )
        if isinstance(node, ast.Lambda):
            raise StructuredHIRCompileError(
                f"{self.path}:{node.lineno}: CapturingClosureUnsupported; "
                "use a named non-capturing fn"
            )
        raise StructuredHIRCompileError(
            f"{self.path}:{getattr(node, 'lineno', 1)}: unsupported expression {type(node).__name__}"
        )

    def _attribute_type(self, owner: str | None, field_name: str) -> str | None:
        if owner in self.types:
            declaration = self.types[owner]
            if declaration.kind == "record":
                for field in declaration.fields:
                    if field.name == field_name:
                        return field.type_name
        return None

    def _result_parts(self, type_name: str) -> tuple[str, str] | None:
        parts = generic_parts(type_name, "Result", arity=2)
        if parts is not None:
            return parts
        declaration = self.types.get(type_name)
        if declaration is None or declaration.kind != "enum":
            return None
        variants = {variant.name: variant.payload_type for variant in declaration.variants}
        ok_type = variants.get("Ok")
        error_type = variants.get("Err")
        if ok_type is None or error_type is None:
            return None
        return ok_type, error_type

    def _call(
        self,
        node: ast.Call,
        arguments: tuple[HIRNode, ...],
        *,
        expected: str | None = None,
    ) -> HIRNode:
        name = ast.unparse(node.func)
        if isinstance(node.func, ast.Name) and node.func.id == "__merlo_try__":
            if len(arguments) != 1:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: postfix propagation expects one Result expression"
                )
            result_type = arguments[0].type_name or ""
            result_parts = self._result_parts(result_type)
            if result_parts is None:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: postfix propagation requires Result, not {result_type or 'unknown'}"
                )
            ok_type, error_type = result_parts
            function_return = _type_name(self.functions[self.current_function].returns)
            function_parts = self._result_parts(function_return)
            if function_parts is None:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: postfix propagation requires a Result-returning function"
                )
            expected_error = function_parts[1]
            if error_type != expected_error:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: propagated error {error_type} does not match {expected_error}"
                )
            return self._new_node(
                node,
                "ResultPropagation",
                type_name=ok_type,
                ownership="owned" if _is_owned(ok_type) else arguments[0].ownership,
                effects=arguments[0].effects + ("result_branch", "may_return_error"),
                attributes={"result_type": result_type, "error_type": error_type},
                children=arguments,
            )
        effects: set[str] = set()
        type_name: str | None = None
        ownership = "value"
        kind = "DirectCall"
        symbol_id = None
        call_attributes: dict[str, Any] = {"callee": name}
        operation_children = arguments
        if isinstance(node.func, ast.Name):
            name = node.func.id
            symbol_id = self.function_symbols.get(name)
            if name == "drop":
                if len(arguments) != 1:
                    raise StructuredHIRCompileError(f"{self.path}:{node.lineno}: drop expects one value")
                kind = "DropValue"
                type_name = "Unit"
                call_attributes["drop_target"] = (
                    arguments[0].attribute_map.get("name")
                    if arguments[0].kind == "Name"
                    else None
                )
            elif name == "Path":
                if len(arguments) != 1 or arguments[0].type_name != "Text":
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: Path constructor expects Text"
                    )
                kind = "DirectCall"
                type_name = "Path"
                ownership = "owned"
            elif name in self.types and self.types[name].kind == "record":
                kind = "RecordConstruct"
                type_name = name
                ownership = "owned" if any(_is_owned(field.type_name) for field in self.types[name].fields) else "value"
            elif name not in self.functions and name in {
                "wrapping_add",
                "wrapping_sub",
                "wrapping_mul",
                "checked_add",
                "checked_sub",
                "checked_mul",
            }:
                numeric_types = {
                    argument.type_name
                    for argument in arguments
                    if argument.type_name is not None
                }
                if (
                    len(arguments) != 2
                    or len(numeric_types) != 1
                    or not numeric_types <= _INTEGER_TYPES
                ):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {name} expects two matching "
                        "Byte, Int64, or UInt64 arguments"
                    )
                kind = "NumericIntrinsic"
                type_name = next(iter(numeric_types))
                call_attributes["numeric_type"] = type_name
                call_attributes["overflow"] = (
                    "wrapping" if name.startswith("wrapping_") else "checked"
                )
            elif name in _SCALAR_TYPES:
                if len(arguments) != 1:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {name} cast expects one argument"
                    )
                if arguments[0].type_name == "Bool" and name != "Bool":
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: BoolNumericCastForbidden"
                    )
                kind = "ScalarCast"
                type_name = name
                call_attributes["target_type"] = name
            elif name in self.local_types and (
                callback := _callback_parts(self.local_types[name])
            ) is not None:
                parameter_types, return_type = callback
                if len(arguments) != len(parameter_types):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: callback {name} expects "
                        f"{len(parameter_types)} arguments"
                    )
                kind = "CallbackCall"
                type_name = return_type
                symbol_id = _stable_id(
                    "shirs", self.path, self.current_function, "parameter", name
                )
            elif name in self.extern_functions:
                foreign = self.extern_functions[name]
                kind = "ForeignCall"
                type_name = foreign.return_type
                effects.update(foreign.effects)
                call_attributes.update(
                    {
                        "abi": foreign.abi,
                        "foreign": True,
                        "error_type": foreign.error_type,
                        "pointer_policies": [
                            item.policy.to_dict()
                            for item in foreign.parameters
                            if item.policy is not None
                        ],
                    }
                )
                ownership = "owned" if _is_owned(type_name) else "value"
                symbol_id = _stable_id("shirs", self.path, "extern", name)
            elif name in self.functions:
                type_name = _type_name(self.functions[name].returns)
                effects.update(self._function_effect_hint(self.functions[name]))
                ownership = (
                    "owned"
                    if _is_owned(type_name)
                    else "borrow"
                    if _is_borrowed(type_name)
                    else "value"
                )
                call_attributes["move_arguments"] = tuple(
                    index
                    for index, parameter in enumerate(self.functions[name].args.args)
                    if (
                        _is_owned(_type_name(parameter.annotation))
                        or _type_name(parameter.annotation) in self.types
                    )
                    and any(
                        isinstance(item, ast.Return)
                        and isinstance(item.value, ast.Name)
                        and item.value.id == parameter.arg
                        for item in ast.walk(self.functions[name])
                    )
                )
            if name == "set_error":
                kind = "TypedError"
            elif (
                name not in self.functions
                and name not in self.types
                and name not in self.extern_functions
                and name not in _SCALAR_TYPES
                and name not in {
                    "drop",
                    "wrapping_add",
                    "wrapping_sub",
                    "wrapping_mul",
                    "checked_add",
                    "checked_sub",
                    "checked_mul",
                    "Ok",
                    "Err",
                    "Some",
                    "NoneValue",
                    "Unit",
                    "console",
                    "fs",
                    "env",
                    "clock",
                    "random",
                    "network",
                    "tcp",
                    "process",
                }
                and not (
                    name in self.local_types
                    and _callback_parts(self.local_types[name]) is not None
                )
                and name != "Path"
            ):
                raise StructuredHIRCompileError(f"UnresolvedName: {name}")
        if isinstance(node.func, ast.Attribute):
            receiver_text = ast.unparse(node.func.value)
            receiver_type = self.local_types.get(receiver_text)
            method = node.func.attr
            callee = f"{receiver_text}.{method}"
            signature = intrinsic_signature(callee)
            if signature is not None:
                if len(arguments) != signature.arity:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {format_intrinsic_arity(signature, len(arguments))}"
                    )
                for index, (argument, parameter_type) in enumerate(
                    zip(arguments, signature.parameters, strict=True), 1
                ):
                    actual = argument.type_name
                    if actual != parameter_type and not (
                        (actual, parameter_type) in {("Text", "TextView"), ("Bytes", "BytesView")}
                    ):
                        raise StructuredHIRCompileError(
                            f"{self.path}:{node.lineno}: IntrinsicTypeMismatch: {callee} "
                            f"argument {index} expects {parameter_type}, got {actual}"
                        )
                kind = "FileOpen" if callee in {"fs.open_read", "fs.open_write"} else "DirectCall"
                type_name = contextual_result_type(signature.result_type, expected)
                if expected and expected.startswith("Result["):
                    expected_parts = self._result_parts(expected)
                    result_parts = self._result_parts(signature.result_type)
                    if (
                        expected_parts is None
                        or result_parts is None
                        or expected_parts[0] != result_parts[0]
                    ):
                        raise StructuredHIRCompileError(
                            f"{self.path}:{node.lineno}: {callee} returns "
                            f"{result_parts[0] if result_parts else signature.result_type}, "
                            f"not {expected_parts[0] if expected_parts else expected}"
                        )
                ownership = signature.result_ownership
                effects.add(signature.effect)
                if signature.result_type.startswith("Result["):
                    effects.add("may_fail")
                operation_children = arguments
                call_attributes["host_operation"] = callee
                if type_name.startswith("Result["):
                    call_attributes["error_type"] = type_name.split(",", 1)[1].rstrip("]")
                if callee.startswith("fs."):
                    call_attributes["resource"] = (
                        "FileReader"
                        if method in {"open_read", "open_write"}
                        else "Text"
                        if method == "read_text"
                        else "Bytes"
                    )
            elif receiver_text in {
                "console", "fs", "env", "clock", "random", "network", "process"
            }:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: UnknownIntrinsic: {callee}"
                )
            elif receiver_type == "FileReader" and method == "lines":
                kind = "FileLines"
                type_name = "FileLines"
                ownership = "borrow"
                effects.add("borrow")
                operation_children = (self.expression(node.func.value),) + arguments
                call_attributes.update({"resource": "FileLines", "borrowed_from": receiver_text})
            elif receiver_text == "Map" or _map_types(receiver_type) is not None:
                kind = "MapOperation"
                static_call = receiver_text == "Map"
                specialization = (
                    expected
                    if static_call and _map_types(expected) is not None
                    else _DEFAULT_MAP
                    if static_call
                    else receiver_type
                )
                map_types = _map_types(specialization)
                if map_types is None:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: Map operation requires "
                        "a concrete specialization"
                    )
                key_type, value_type = map_types
                arities = {
                    "new": {0},
                    "increment": {1, 2},
                    "get": {1},
                    "insert": {2},
                    "entries": {0},
                }
                if method not in arities or len(arguments) not in arities[method]:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: unsupported Map operation "
                        f"{method}/{len(arguments)}"
                    )
                if static_call != (method == "new"):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: unsupported Map operation {name}"
                    )
                if method == "increment" and value_type != "UInt64":
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: Map.increment requires UInt64 values"
                    )
                if not static_call:
                    operation_children = (self.expression(node.func.value),) + arguments
                    expected_types = (
                        (key_type, value_type)
                        if method == "insert"
                        else (key_type, "UInt64")
                        if method == "increment" and len(arguments) == 2
                        else (key_type,)
                    )
                    for argument, expected_type in zip(arguments, expected_types):
                        if argument.type_name not in {None, "Inferred", expected_type}:
                            raise StructuredHIRCompileError(
                                f"{self.path}:{node.lineno}: Map.{method} argument must be "
                                f"{expected_type}, not {argument.type_name}"
                            )
                call_attributes.update(
                    {"map_operation": method, "map_specialization": specialization}
                )
                if method == "new":
                    type_name = specialization
                    ownership = "owned"
                    effects.update(("allocate", "may_fail"))
                elif method == "increment":
                    type_name = "UInt64"
                    effects.update(("allocate", "copy", "may_fail"))
                elif method == "insert":
                    type_name = "Unit"
                    effects.update(("allocate", "copy", "may_fail"))
                elif method == "get":
                    type_name = value_type
                else:
                    type_name = f"Borrow[{specialization}]"
                    ownership = "borrow"
            elif receiver_text == "Box" or (receiver_type or "").startswith("Box["):
                kind = "BoxOperation"
                effects.update({"allocate", "may_fail"} if method == "new" else set())
                ownership = "owned" if method == "new" else "borrow"
                if method == "new":
                    type_name = "Box[Inferred]"
                elif receiver_type and method in {"get", "get_mut"}:
                    box_parts = generic_parts(receiver_type, "Box", arity=1)
                    if box_parts is not None:
                        type_name = box_parts[0]
            elif (
                receiver_text in {"Text", "TextBuilder"}
                or receiver_type in {
                    "Text",
                    "TextBuilder",
                    "Bytes",
                    "BytesView",
                    "TextView",
                }
                or method in {"append_byte", "append_scalar", "finish", "byte"}
            ):
                kind = "BytesTextOperation"
                if name == "Text.from_bytes":
                    type_name = "Text"
                    ownership = "owned"
                    effects.update(("allocate", "copy", "may_fail"))
                elif name == "TextBuilder.new":
                    type_name = "TextBuilder"
                    ownership = "owned"
                    effects.update(("allocate", "may_fail"))
                elif receiver_type == "Bytes" and method == "view":
                    type_name = "BytesView"
                    ownership = "borrow"
                elif receiver_type == "Text" and method == "as_view":
                    type_name = "TextView"
                    ownership = "borrow"
                elif receiver_type == "Text" and method == "clone":
                    type_name = "Text"
                    ownership = "owned"
                    effects.update(("allocate", "copy", "may_fail"))
                elif method == "finish":
                    type_name = "Text"
                    ownership = "owned"
                elif method == "byte":
                    type_name = "UInt64"
                    effects.add("bounds_check")
                elif method == "len":
                    type_name = "UInt64"
            elif (
                receiver_text == "Vec"
                or receiver_text.startswith("Vec[")
                or (receiver_type or "").startswith("Vec[")
                or method in {"push", "capacity", "get_mut", "view"}
            ):
                kind = "VecOperation"
                effects.update({"allocate", "may_fail"} if method in {"new", "push"} else {"bounds_check"} if method in {"get", "get_mut"} else set())
                if method == "new":
                    type_name = "Vec[Inferred]"
                    ownership = "owned"
                elif method in {"len", "capacity"}:
                    type_name = "UInt64"
                elif method == "view":
                    type_name = "Borrow[Inferred]"
                    ownership = "borrow"
                elif receiver_type and method in {"get", "get_mut"}:
                    vec_parts = generic_parts(receiver_type, "Vec", arity=1)
                    if vec_parts is not None:
                        type_name = vec_parts[0]
                    ownership = "borrow_mut" if method == "get_mut" else "borrow"
            elif receiver_text in self.types and self.types[receiver_text].kind == "enum":
                kind = "EnumConstruct"
                type_name = receiver_text
                ownership = "owned" if any(_is_owned(item.payload_type) for item in self.types[receiver_text].variants) else "value"
                variant = next((item for item in self.types[receiver_text].variants if item.name == method), None)
                if variant is None:
                    raise StructuredHIRCompileError(f"unknown enum variant {name}")
            elif method == "tag":
                kind = "EnumTag"
                type_name = "UInt64"
        return self._new_node(
            node,
            kind,
            type_name=type_name,
            ownership=ownership,
            effects=effects,
            symbol_id=symbol_id,
            attributes=call_attributes,
            children=operation_children,
        )

    def _function_effect_hint(
        self,
        function: ast.FunctionDef,
        visiting: frozenset[str] = frozenset(),
    ) -> set[str]:
        if function.name in visiting:
            return set()
        visiting = visiting | {function.name}
        effects: set[str] = set()
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if (
                ".push" in name
                or name in {"Vec.new", "TextBuilder.new", "Text.from_bytes", "Map.new"}
                or name.endswith(".insert")
                or name.endswith(".increment")
            ):
                effects.update(("allocate", "may_fail"))
            signature = intrinsic_signature(name)
            if signature is not None:
                effects.add(signature.effect)
                if signature.result_type.startswith("Result["):
                    effects.add("may_fail")
            elif isinstance(node.func, ast.Name) and node.func.id in self.functions:
                effects.update(
                    self._function_effect_hint(self.functions[node.func.id], visiting)
                )
            if name == "set_error":
                effects.add("typed_error")
        return effects

    def statement(self, node: ast.stmt, *, scope_suffix: str = "body") -> HIRNode:
        scope_id = self._scope(scope_suffix)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            type_name = _type_name(node.annotation)
            self.local_types[node.target.id] = type_name
            value = (
                self.expression(node.value, expected=type_name)
                if node.value is not None
                else ()
            )
            binding = self.preprocessed.binding_kinds.get(node.lineno, "let")
            return self._new_node(
                node,
                "VarBinding" if binding == "var" else "LetBinding",
                type_name=type_name,
                ownership="owned" if _is_owned(type_name) else "value",
                symbol_id=_stable_id("shirs", self.path, self.current_function, "local", node.target.id),
                attributes={"name": node.target.id, "mutable": binding == "var"},
                children=(value,) if isinstance(value, HIRNode) else (),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            value = self.expression(node.value)
            kind = "SetField" if isinstance(target, ast.Attribute) else "Assign"
            return self._new_node(
                node,
                kind,
                type_name=value.type_name,
                attributes={"target": ast.unparse(target)},
                children=(value,),
                scope_id=scope_id,
            )
        if isinstance(node, ast.AugAssign):
            target = self.expression(node.target)
            value = self.expression(node.value)
            return self._new_node(
                node,
                "AugAssign",
                type_name=target.type_name or value.type_name,
                attributes={"target": ast.unparse(node.target), "operator": type(node.op).__name__},
                children=(target, value),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Expr):
            child = self.expression(node.value)
            return self._new_node(
                node,
                "Expression",
                effects=child.effects,
                children=(child,),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Break):
            return self._new_node(node, "Break", scope_id=scope_id)
        if isinstance(node, ast.Return):
            child = self.expression(node.value) if node.value is not None else None
            return_type = child.type_name if child else "Unit"
            ownership = (
                "owned"
                if _is_owned(return_type)
                else "borrow"
                if _is_borrowed(return_type)
                else child.ownership
                if child
                else "value"
            )
            return self._new_node(
                node,
                "Return",
                type_name=return_type,
                ownership=ownership,
                children=(child,) if child else (),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Continue):
            return self._new_node(node, "Continue", scope_id=scope_id)
        if isinstance(node, ast.Pass):
            return self._new_node(node, "Pass", scope_id=scope_id)
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "__merlo_unsafe_scope__"
        ):
            body = tuple(
                self.statement(item, scope_suffix=f"unsafe@{node.lineno}")
                for item in node.body
            )
            effects = tuple(
                sorted({effect for item in body for nested in item.walk() for effect in nested.effects})
            )
            return self._new_node(
                node,
                "UnsafeBlock",
                effects=effects,
                attributes={"non_propagating": True},
                children=body,
                scope_id=scope_id,
            )
        if isinstance(node, ast.If):
            test = self.expression(node.test)
            body = tuple(self.statement(item, scope_suffix=f"if@{node.lineno}.then") for item in node.body)
            other = tuple(self.statement(item, scope_suffix=f"if@{node.lineno}.else") for item in node.orelse)
            then_node = self._new_node(node, "Then", children=body, scope_id=self._scope(f"if@{node.lineno}.then"))
            else_node = self._new_node(node, "Else", children=other, scope_id=self._scope(f"if@{node.lineno}.else"))
            return self._new_node(node, "If", type_name="Unit", children=(test, then_node, else_node), scope_id=scope_id)
        if isinstance(node, ast.While):
            test = self.expression(node.test)
            body = tuple(self.statement(item, scope_suffix=f"while@{node.lineno}") for item in node.body)
            loop_body = self._new_node(node, "LoopBody", children=body, scope_id=self._scope(f"while@{node.lineno}"))
            return self._new_node(node, "While", children=(test, loop_body), scope_id=scope_id)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            iterable = self.expression(node.iter)
            self.local_types[node.target.id] = "TextView" if iterable.type_name == "FileLines" else "Inferred"
            body = tuple(self.statement(item, scope_suffix=f"for@{node.lineno}") for item in node.body)
            loop_body = self._new_node(node, "LoopBody", children=body, scope_id=self._scope(f"for@{node.lineno}"))
            return self._new_node(
                node,
                "For",
                attributes={"target": node.target.id},
                children=(iterable, loop_body),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Match):
            subject = self.expression(node.subject)
            cases = tuple(
                self._match_case(item, node, index, subject.type_name)
                for index, item in enumerate(node.cases)
            )
            self._validate_match(node, subject.type_name, node.cases)
            return self._new_node(node, "Match", children=(subject,) + cases, scope_id=scope_id)
        raise StructuredHIRCompileError(f"{self.path}:{node.lineno}: unsupported statement {type(node).__name__}")

    def _match_case(
        self,
        case: ast.match_case,
        owner: ast.Match,
        index: int,
        subject_type: str | None,
    ) -> HIRNode:
        pattern_text = ast.unparse(case.pattern)
        bindings: dict[str, str] = {}
        if isinstance(case.pattern, ast.MatchClass):
            variant_name = (
                case.pattern.cls.attr
                if isinstance(case.pattern.cls, ast.Attribute)
                else case.pattern.cls.id
                if isinstance(case.pattern.cls, ast.Name)
                else ""
            )
            declaration = self.types.get(subject_type or "")
            variants = (
                {
                    variant.name: variant.payload_type
                    for variant in declaration.variants
                }
                if declaration is not None and declaration.kind == "enum"
                else _sum_variants(subject_type)
            )
            payload_type = variants.get(variant_name) if variants is not None else None
            if payload_type is not None:
                for pattern in case.pattern.patterns:
                    if isinstance(pattern, ast.MatchAs) and pattern.name:
                        bindings[pattern.name] = payload_type
        elif isinstance(case.pattern, ast.MatchAs) and case.pattern.name and subject_type:
            bindings[case.pattern.name] = subject_type
        previous = {
            name: self.local_types.get(name)
            for name in bindings
        }
        self.local_types.update(bindings)
        body = tuple(
            self.statement(item, scope_suffix=f"match@{owner.lineno}.case{index}")
            for item in case.body
        )
        for name, type_name in previous.items():
            if type_name is None:
                self.local_types.pop(name, None)
            else:
                self.local_types[name] = type_name
        return self._new_node(
            case.pattern,
            "MatchCase",
            attributes={"pattern": pattern_text, "wildcard": isinstance(case.pattern, ast.MatchAs) and case.pattern.name is None},
            children=body,
            scope_id=self._scope(f"match@{owner.lineno}.case{index}"),
        )

    def _validate_match(self, node: ast.Match, subject_type: str | None, cases: list[ast.match_case]) -> None:
        if any(isinstance(item.pattern, ast.MatchAs) and item.pattern.name is None for item in cases):
            return
        declaration = self.types.get(subject_type or "")
        variants = (
            {
                item.name: item.payload_type
                for item in declaration.variants
            }
            if declaration is not None and declaration.kind == "enum"
            else _sum_variants(subject_type)
        )
        if variants is None:
            return
        enum_name = subject_type
        expected = set(variants)
        seen = set()
        for item in cases:
            pattern = item.pattern
            if isinstance(pattern, ast.MatchSingleton) and pattern.value is None:
                seen.add("NoneValue")
            elif isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Attribute):
                seen.add(pattern.value.attr)
            elif isinstance(pattern, ast.MatchClass):
                if isinstance(pattern.cls, ast.Attribute):
                    seen.add(pattern.cls.attr)
                elif isinstance(pattern.cls, ast.Name):
                    seen.add("NoneValue" if pattern.cls.id == "None" else pattern.cls.id)
        missing = sorted(expected - seen)
        if missing:
            raise StructuredHIRCompileError(
                f"{self.path}:{node.lineno}: NonExhaustiveMatch {enum_name}: {missing}"
            )

    def function(self, node: ast.FunctionDef) -> HIRFunction:
        self.current_function = node.name
        self.ordinal = 0
        self.local_types = {
            argument.arg: _type_name(argument.annotation) for argument in node.args.args
        }
        assigned = self.mutable_parameters[node.name]
        returned_parameters = {
            item.value.id
            for item in ast.walk(node)
            if isinstance(item, ast.Return)
            and isinstance(item.value, ast.Name)
        }
        parameters = []
        for argument in node.args.args:
            type_name = _type_name(argument.annotation)
            source = _span(self.path, argument)
            owns_value = _is_owned(type_name) or type_name in self.types
            ownership = (
                "owned"
                if owns_value and argument.arg in returned_parameters
                else "borrow_mut"
                if argument.arg in assigned
                else "borrow"
                if owns_value or _is_borrowed(type_name)
                else "value"
            )
            symbol_id = _stable_id("shirs", self.path, node.name, "parameter", argument.arg)
            parameters.append(
                HIRParameter(
                    argument.arg,
                    type_name,
                    ownership,
                    source,
                    symbol_id,
                    _stable_id("rev", node.name, argument.arg, type_name, ownership),
                )
            )
        body = tuple(self.statement(item) for item in node.body)
        effects = tuple(sorted({effect for item in body for nested in item.walk() for effect in nested.effects}))
        source = _span(self.path, node)
        symbol_id = self.function_symbols[node.name]
        return_type = _type_name(node.returns)
        revision_id = _stable_id(
            "rev",
            node.name,
            [(item.name, item.type_name, item.ownership) for item in parameters],
            return_type,
            effects,
            [item.revision_id for item in body],
        )
        return HIRFunction(
            node.name,
            tuple(parameters),
            return_type,
            effects,
            body,
            source,
            self._scope(),
            symbol_id,
            revision_id,
        )


def _parse_type_declarations(
    path: str,
    module: ast.Module,
    kinds: dict[str, str],
) -> dict[str, HIRTypeDecl]:
    result: dict[str, HIRTypeDecl] = {}
    for node in (item for item in module.body if isinstance(item, ast.ClassDef)):
        kind = kinds.get(node.name)
        if kind not in {"record", "enum"}:
            raise StructuredHIRCompileError(f"{path}:{node.lineno}: unknown type declaration")
        source = _span(path, node)
        type_symbol = _stable_id("shirs", path, kind, node.name)
        fields: list[HIRField] = []
        variants: list[HIRVariant] = []
        for ordinal, statement in enumerate(node.body):
            if kind == "record":
                if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                    raise StructuredHIRCompileError(f"{path}:{statement.lineno}: record fields require types")
                name = statement.target.id
                type_name = _type_name(statement.annotation)
                item_source = _span(path, statement)
                symbol = _stable_id("shirs", path, node.name, "field", name)
                fields.append(HIRField(name, type_name, item_source, symbol, _stable_id("rev", node.name, name, type_name)))
            else:
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Name):
                    name = statement.value.id
                    payload = None
                elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    name = statement.target.id
                    payload = _type_name(statement.annotation)
                else:
                    raise StructuredHIRCompileError(f"{path}:{statement.lineno}: invalid enum variant")
                item_source = _span(path, statement)
                symbol = _stable_id("shirs", path, node.name, "variant", name)
                variants.append(HIRVariant(name, payload, ordinal, item_source, symbol, _stable_id("rev", node.name, name, payload, ordinal)))
        revision = _stable_id(
            "rev",
            kind,
            node.name,
            [(item.name, item.type_name) for item in fields],
            [(item.name, item.payload_type, item.tag) for item in variants],
        )
        result[node.name] = HIRTypeDecl(node.name, kind, source, type_symbol, revision, tuple(fields), tuple(variants))
    return result




def compile_structured_hir(
    source: str,
    *,
    path: str = "main.mlo",
    entry_function: str = "main",
) -> StructuredHIRProgram:
    if not source.strip():
        raise StructuredHIRCompileError("empty Structured HIR source")
    try:
        ffi_program = validate_ffi(source, path=path)
    except FFICompileError as exc:
        raise StructuredHIRCompileError(str(exc)) from exc
    preprocessed = _preprocess(_preprocess_ffi_surface(source))
    try:
        module = ast.parse(preprocessed.source, filename=path)
    except SyntaxError as exc:
        raise StructuredHIRCompileError(f"{path}:{exc.lineno}:{exc.offset}: {exc.msg}") from exc
    _validate_map_specializations(module, path)
    types = _parse_type_declarations(path, module, preprocessed.declaration_kinds)
    function_nodes = {
        item.name: item for item in module.body if isinstance(item, ast.FunctionDef)
    }
    unsupported = [
        type(item).__name__
        for item in module.body
        if not isinstance(item, (ast.ClassDef, ast.FunctionDef))
    ]
    if unsupported:
        raise StructuredHIRCompileError(f"unsupported top-level declarations: {unsupported}")
    if entry_function not in function_nodes:
        raise StructuredHIRCompileError(f"missing entry function: {entry_function}")
    _OwnershipChecker(path, types, function_nodes).check()
    builder = _HIRBuilder(path, source, preprocessed, types, function_nodes, ffi_program)
    functions = tuple(builder.function(item) for item in function_nodes.values())
    return StructuredHIRProgram(
        source,
        path,
        hashlib.sha256(source.encode()).hexdigest(),
        tuple(types.values()),
        functions,
        entry_function,
    )


def compile_canonical_hir(
    program: CanonicalProgram,
    *,
    entry_function: str = "main",
) -> StructuredHIRProgram:
    """Lower an in-memory canonical tree through the production HIR builder."""
    if program.native_module is None:
        return compile_structured_hir(
            program.to_source(),
            entry_function=entry_function,
        )
    source = program.to_source()
    path = next(
        (
            function.span.path
            for function in program.functions
            if function.name == entry_function
        ),
        "main.mlo",
    )
    module = copy.deepcopy(program.native_module)
    _validate_map_specializations(module, path)
    preprocessed = _Preprocessed(
        source,
        dict(program.native_declaration_kinds),
        dict(program.native_binding_kinds),
    )
    types = _parse_type_declarations(
        path,
        module,
        preprocessed.declaration_kinds,
    )
    function_nodes = {
        item.name: item
        for item in module.body
        if isinstance(item, ast.FunctionDef)
    }
    unsupported = [
        type(item).__name__
        for item in module.body
        if not isinstance(item, (ast.ClassDef, ast.FunctionDef))
    ]
    if unsupported:
        raise StructuredHIRCompileError(
            f"unsupported top-level declarations: {unsupported}"
        )
    if entry_function not in function_nodes:
        raise StructuredHIRCompileError(
            f"missing entry function: {entry_function}"
        )
    _OwnershipChecker(path, types, function_nodes).check()
    builder = _HIRBuilder(
        path,
        source,
        preprocessed,
        types,
        function_nodes,
    )
    functions = tuple(
        builder.function(item) for item in function_nodes.values()
    )
    return StructuredHIRProgram(
        source,
        path,
        program.semantic_hash,
        tuple(types.values()),
        functions,
        entry_function,
        native_module=module,
    )


def compile_structured_hir_file(path: str | Path) -> StructuredHIRProgram:
    source_path = Path(path)
    return compile_structured_hir(source_path.read_text(encoding="utf-8"), path=str(source_path))


__all__ = [
    "HIRField",
    "HIRFunction",
    "HIRNode",
    "HIRParameter",
    "HIRTypeDecl",
    "HIRVariant",
    "SourceSpan",
    "StructuredHIRCompileError",
    "StructuredHIRProgram",
    "STRUCTURED_HIR_CONTRACT",
    "STRUCTURED_HIR_SCHEMA_VERSION",
    "compile_structured_hir",
    "compile_canonical_hir",
    "compile_structured_hir_file",
]
