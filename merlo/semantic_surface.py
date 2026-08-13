"""Concise human syntax with a fully explicit machine projection.

This is a falsifiable language experiment, not a second runtime. The surface
omits types and mutability where local constraints determine them uniquely, then
elaborates to the existing typed Merlo native pipeline.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .native_c_backend import CEmitter, NativeBuildResult, compile_c_source
from .native_hir import NativeHIRProgram, compile_native_hir, lower_native_hir_to_performance
from .performance_frontend import PerformanceCompileError
from .performance_mir import PerformanceMIR
from .performance_opt import PassSnapshot, optimize_mir


SEMANTIC_SURFACE_SCHEMA_VERSION = 1
SEMANTIC_SURFACE_CONTRACT = "merlo.semantic-compression-surface.v1"
_UINT = "UInt64"
_BOOL = "Bool"
_SUPPORTED_TYPES = frozenset({_UINT, _BOOL})


class SemanticSurfaceError(ValueError):
    """The concise source cannot be inferred without guessing."""


@dataclass(frozen=True)
class InferredBinding:
    owner: str
    name: str
    kind: str
    type_name: str
    mutable: bool
    source_line: int
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "kind": self.kind,
            "type": self.type_name,
            "mutable": self.mutable,
            "source_line": self.source_line,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ElaboratedSurface:
    source: str
    path: str
    canonical_source: str
    bindings: tuple[InferredBinding, ...]
    entry_function: str
    top_level_script: bool
    schema_version: int = SEMANTIC_SURFACE_SCHEMA_VERSION
    contract: str = SEMANTIC_SURFACE_CONTRACT

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.source.encode()).hexdigest()

    @property
    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_source.encode()).hexdigest()

    @property
    def inferred_annotation_count(self) -> int:
        return sum(item.kind in {"parameter", "return", "local"} for item in self.bindings)

    @property
    def inferred_mutability_count(self) -> int:
        return sum(item.kind == "local" and item.mutable for item in self.bindings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "path": self.path,
            "source_sha256": self.source_sha256,
            "canonical_sha256": self.canonical_sha256,
            "entry_function": self.entry_function,
            "top_level_script": self.top_level_script,
            "bindings": [item.to_dict() for item in self.bindings],
            "inferred_annotation_count": self.inferred_annotation_count,
            "inferred_mutability_count": self.inferred_mutability_count,
            "canonical_source": self.canonical_source,
            "invariants": {
                "no_runtime_dynamic_types": True,
                "no_implicit_numeric_coercions": True,
                "ambiguous_types_rejected": True,
                "canonical_projection_is_ordinary_typed_merlo": True,
                "target_independent_semantics": True,
            },
        }


@dataclass(frozen=True)
class SemanticSurfaceCompilation:
    elaborated: ElaboratedSurface
    hir: NativeHIRProgram
    mir: PerformanceMIR
    optimized_mir: PerformanceMIR
    optimization_snapshots: tuple[PassSnapshot, ...]
    generated_c: str
    @property
    def generated_c_sha256(self) -> str:
        return hashlib.sha256(self.generated_c.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.elaborated.to_dict(),
            "machine_projection": {
                "hir_contract": self.hir.contract,
                "hir_digest": self.hir.digest,
                "symbols": [item.to_dict() for item in self.hir.symbols],
                "references": [item.to_dict() for item in self.hir.references],
                "mir_digest": self.mir.digest,
                "optimized_mir_digest": self.optimized_mir.digest,
                "generated_c_sha256": self.generated_c_sha256,
                "optimization_passes": [
                    item.statistics.to_dict()
                    for item in self.optimization_snapshots
                ],
            },
        }


@dataclass(frozen=True)
class SemanticSurfaceBuild:
    compilation: SemanticSurfaceCompilation
    native: NativeBuildResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "compilation": self.compilation.to_dict(),
            "native": {
                "status": self.native.status,
                "binary_path": self.native.binary_path,
                "binary_sha256": self.native.binary_sha256,
                "compiler": self.native.compiler,
                "compiler_version": self.native.compiler_version,
                "command": list(self.native.command),
                "stderr": self.native.stderr,
            },
        }


@dataclass
class _Function:
    name: str
    node: ast.FunctionDef
    parameter_terms: dict[str, str]
    return_term: str
    local_terms: dict[str, str]
    assignment_counts: dict[str, int]
    evidence: dict[str, set[str]]
    source_lines: dict[str, int]


class _Types:
    def __init__(self, path: str) -> None:
        self.path = path
        self.parent: dict[str, str] = {}
        self.concrete: dict[str, str] = {}

    def variable(self, name: str) -> str:
        self.parent.setdefault(name, name)
        return name

    def typed(self, type_name: str) -> str:
        if type_name not in _SUPPORTED_TYPES:
            raise SemanticSurfaceError(
                f"{self.path}: unsupported inferred-surface type {type_name!r}"
            )
        key = f"$type:{type_name}"
        self.parent.setdefault(key, key)
        self.concrete[key] = type_name
        return key

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def unify(self, left: str, right: str, *, line: int, reason: str) -> None:
        first = self.find(left)
        second = self.find(right)
        if first == second:
            return
        first_type = self.concrete.get(first)
        second_type = self.concrete.get(second)
        if first_type and second_type and first_type != second_type:
            raise SemanticSurfaceError(
                f"{self.path}:{line}: TypeConflict {first_type} vs "
                f"{second_type} ({reason})"
            )
        if first_type and not second_type:
            first, second = second, first
            first_type, second_type = second_type, first_type
        self.parent[first] = second
        resolved = second_type or first_type
        if resolved:
            self.concrete[second] = resolved

    def resolve(self, value: str, *, line: int, name: str) -> str:
        result = self.concrete.get(self.find(value))
        if result is None:
            raise SemanticSurfaceError(
                f"{self.path}:{line}: AmbiguousType {name!r}; add one boundary "
                "annotation or a constraining operation"
            )
        return result


def _preprocess(source: str) -> str:
    output: list[str] = []
    for original in source.splitlines():
        prefix = original[: len(original) - len(original.lstrip())]
        stripped = original.strip()
        line = original
        if stripped.startswith("fn "):
            line = prefix + "def " + stripped[3:]
        range_match = re.match(
            r"^for\s+([A-Za-z_]\w*)\s+in\s+(.+?)\.\.(.+):$", line.strip()
        )
        if range_match:
            name, start, end = range_match.groups()
            line = prefix + f"for {name} in merlo_range({start}, {end}):"
        line = re.sub(r"\btrue\b", "True", line)
        line = re.sub(r"\bfalse\b", "False", line)
        output.append(line)
    return "\n".join(output) + "\n"


def _annotation(node: ast.AST | None, *, path: str, line: int) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id in _SUPPORTED_TYPES:
        return node.id
    raise SemanticSurfaceError(
        f"{path}:{line}: concise surface accepts only Bool and UInt64 "
        "boundary annotations in this experiment"
    )


def _is_argument_input(node: ast.AST) -> int | None:
    if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
        return None
    if node.value.id != "args":
        return None
    index = node.slice
    if isinstance(index, ast.Constant) and isinstance(index.value, int) and index.value >= 0:
        return index.value
    return None


def _prepare_module(
    parsed: ast.Module, *, path: str
) -> tuple[list[ast.FunctionDef], bool]:
    declarations = [item for item in parsed.body if isinstance(item, ast.FunctionDef)]
    executable = [item for item in parsed.body if not isinstance(item, ast.FunctionDef)]
    if not executable:
        if not declarations:
            raise SemanticSurfaceError(f"{path}: empty concise program")
        return declarations, False
    if any(item.name == "main" for item in declarations):
        raise SemanticSurfaceError(
            f"{path}: top-level script cannot also declare fn main"
        )
    inputs: dict[int, tuple[str, ast.Assign]] = {}
    body: list[ast.stmt] = []
    for statement in executable:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            index = _is_argument_input(statement.value)
            if index is not None:
                if index in inputs:
                    raise SemanticSurfaceError(
                        f"{path}:{statement.lineno}: duplicate args[{index}] binding"
                    )
                inputs[index] = (statement.targets[0].id, statement)
                continue
        body.append(statement)
    if not inputs:
        raise SemanticSurfaceError(
            f"{path}: top-level script needs at least one `name = args[index]` "
            "boundary so its native entry contract is explicit"
        )
    if sorted(inputs) != list(range(len(inputs))):
        raise SemanticSurfaceError(f"{path}: argument indexes must be contiguous from zero")
    if not body or not isinstance(body[-1], (ast.Expr, ast.Return)):
        raise SemanticSurfaceError(f"{path}: script must end with a result expression")
    arguments = [
        ast.arg(arg=inputs[index][0], annotation=None, lineno=inputs[index][1].lineno)
        for index in range(len(inputs))
    ]
    main = ast.FunctionDef(
        name="main",
        args=ast.arguments(
            posonlyargs=[], args=arguments, vararg=None,
            kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
        ),
        body=body,
        decorator_list=[],
        returns=None,
        type_comment=None,
        lineno=min(item.lineno for item in executable),
        col_offset=0,
    )
    ast.fix_missing_locations(main)
    return [*declarations, main], True


class _Inferencer:
    def __init__(self, functions: Iterable[ast.FunctionDef], *, path: str) -> None:
        self.path = path
        self.types = _Types(path)
        self.uint = self.types.typed(_UINT)
        self.bool = self.types.typed(_BOOL)
        self.functions: dict[str, _Function] = {}
        for node in functions:
            if node.name in self.functions:
                raise SemanticSurfaceError(
                    f"{path}:{node.lineno}: duplicate function {node.name!r}"
                )
            parameter_terms = {
                parameter.arg: self.types.variable(
                    f"function:{node.name}:parameter:{parameter.arg}"
                )
                for parameter in node.args.args
            }
            function = _Function(
                node.name,
                node,
                parameter_terms,
                self.types.variable(f"function:{node.name}:return"),
                {},
                {},
                {},
                {parameter.arg: parameter.lineno for parameter in node.args.args},
            )
            self.functions[node.name] = function
        for function in self.functions.values():
            self._apply_annotations(function)
        for function in self.functions.values():
            self._statements(function.node.body, function)

    def _note(self, function: _Function, name: str, evidence: str) -> None:
        function.evidence.setdefault(name, set()).add(evidence)

    def _apply_annotations(self, function: _Function) -> None:
        for parameter in function.node.args.args:
            explicit = _annotation(
                parameter.annotation, path=self.path, line=parameter.lineno
            )
            if explicit:
                self.types.unify(
                    function.parameter_terms[parameter.arg],
                    self.types.typed(explicit),
                    line=parameter.lineno,
                    reason="explicit parameter annotation",
                )
                self._note(function, parameter.arg, "explicit_boundary")
        returns = _annotation(
            function.node.returns,
            path=self.path,
            line=function.node.lineno,
        )
        if returns:
            self.types.unify(
                function.return_term,
                self.types.typed(returns),
                line=function.node.lineno,
                reason="explicit return annotation",
            )
            self._note(function, "$return", "explicit_boundary")

    def _local(self, function: _Function, name: str, line: int) -> str:
        if name in function.parameter_terms:
            return function.parameter_terms[name]
        function.source_lines.setdefault(name, line)
        return function.local_terms.setdefault(
            name,
            self.types.variable(f"function:{function.name}:local:{name}"),
        )

    def _name(self, function: _Function, node: ast.Name) -> str:
        if node.id in function.parameter_terms:
            return function.parameter_terms[node.id]
        if node.id in function.local_terms:
            return function.local_terms[node.id]
        raise SemanticSurfaceError(
            f"{self.path}:{node.lineno}: UnresolvedName {node.id!r}"
        )

    def _expression(self, node: ast.AST, function: _Function) -> str:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return self.bool
            if isinstance(node.value, int) and node.value >= 0:
                return self.uint
            raise SemanticSurfaceError(
                f"{self.path}:{node.lineno}: unsupported concise literal {node.value!r}"
            )
        if isinstance(node, ast.Name):
            return self._name(function, node)
        if isinstance(node, ast.BinOp):
            left = self._expression(node.left, function)
            right = self._expression(node.right, function)
            self.types.unify(left, self.uint, line=node.lineno, reason="numeric operator")
            self.types.unify(right, self.uint, line=node.lineno, reason="numeric operator")
            for child in (node.left, node.right):
                if isinstance(child, ast.Name):
                    self._note(function, child.id, "numeric_operator")
            return self.uint
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                term = self._expression(value, function)
                self.types.unify(term, self.bool, line=node.lineno, reason="boolean operator")
            return self.bool
        if isinstance(node, ast.UnaryOp):
            operand = self._expression(node.operand, function)
            expected = self.bool if isinstance(node.op, ast.Not) else self.uint
            self.types.unify(operand, expected, line=node.lineno, reason="unary operator")
            return expected
        if isinstance(node, ast.Compare):
            terms = [
                self._expression(item, function)
                for item in (node.left, *node.comparators)
            ]
            for left, right in zip(terms, terms[1:]):
                self.types.unify(left, right, line=node.lineno, reason="comparison")
            if any(not isinstance(item, (ast.Eq, ast.NotEq)) for item in node.ops):
                for term in terms:
                    self.types.unify(term, self.uint, line=node.lineno, reason="ordered comparison")
            return self.bool
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "merlo_range":
                if len(node.args) != 2:
                    raise SemanticSurfaceError(
                        f"{self.path}:{node.lineno}: range expects two bounds"
                    )
                for argument in node.args:
                    self.types.unify(
                        self._expression(argument, function), self.uint,
                        line=node.lineno, reason="range bound",
                    )
                return self.uint
            target = self.functions.get(node.func.id)
            if target is None:
                raise SemanticSurfaceError(
                    f"{self.path}:{node.lineno}: unknown call {node.func.id!r}"
                )
            if len(node.args) != len(target.parameter_terms):
                raise SemanticSurfaceError(
                    f"{self.path}:{node.lineno}: call {node.func.id!r} expects "
                    f"{len(target.parameter_terms)} arguments"
                )
            for argument, parameter in zip(
                node.args, target.parameter_terms.values(), strict=True
            ):
                self.types.unify(
                    self._expression(argument, function), parameter,
                    line=node.lineno, reason=f"call {target.name}",
                )
            return target.return_term
        raise SemanticSurfaceError(
            f"{self.path}:{getattr(node, 'lineno', 1)}: unsupported concise "
            f"expression {type(node).__name__}"
        )

    def _assign(self, statement: ast.Assign | ast.AnnAssign, function: _Function) -> None:
        target = statement.target if isinstance(statement, ast.AnnAssign) else statement.targets[0]
        if not isinstance(target, ast.Name):
            raise SemanticSurfaceError(
                f"{self.path}:{statement.lineno}: concise assignments require a name"
            )
        value = statement.value
        if value is None:
            raise SemanticSurfaceError(
                f"{self.path}:{statement.lineno}: assignment requires a value"
            )
        term = self._local(function, target.id, statement.lineno)
        function.assignment_counts[target.id] = function.assignment_counts.get(target.id, 0) + 1
        self.types.unify(
            term, self._expression(value, function),
            line=statement.lineno, reason="assignment",
        )
        explicit = _annotation(
            statement.annotation if isinstance(statement, ast.AnnAssign) else None,
            path=self.path,
            line=statement.lineno,
        )
        if explicit:
            self.types.unify(
                term, self.types.typed(explicit),
                line=statement.lineno, reason="explicit local annotation",
            )
            self._note(function, target.id, "explicit_boundary")
        else:
            self._note(function, target.id, "assignment_value")

    def _statements(self, statements: list[ast.stmt], function: _Function) -> None:
        for index, statement in enumerate(statements):
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                self._assign(statement, function)
            elif isinstance(statement, ast.AugAssign):
                if not isinstance(statement.target, ast.Name):
                    raise SemanticSurfaceError(
                        f"{self.path}:{statement.lineno}: augmented assignment requires a name"
                    )
                term = self._local(function, statement.target.id, statement.lineno)
                function.assignment_counts[statement.target.id] = (
                    function.assignment_counts.get(statement.target.id, 0) + 1
                )
                self.types.unify(term, self.uint, line=statement.lineno, reason="numeric mutation")
                self.types.unify(
                    self._expression(statement.value, function), self.uint,
                    line=statement.lineno, reason="numeric mutation",
                )
                self._note(function, statement.target.id, "mutated")
            elif isinstance(statement, ast.For):
                if not isinstance(statement.target, ast.Name):
                    raise SemanticSurfaceError(
                        f"{self.path}:{statement.lineno}: loop target requires a name"
                    )
                iterable = self._expression(statement.iter, function)
                self.types.unify(iterable, self.uint, line=statement.lineno, reason="range iteration")
                target = self._local(function, statement.target.id, statement.lineno)
                self.types.unify(target, self.uint, line=statement.lineno, reason="range index")
                self._note(function, statement.target.id, "range_index")
                self._statements(statement.body, function)
                if statement.orelse:
                    raise SemanticSurfaceError(
                        f"{self.path}:{statement.lineno}: loop else is not in the experiment"
                    )
            elif isinstance(statement, ast.While):
                condition = self._expression(statement.test, function)
                self.types.unify(condition, self.bool, line=statement.lineno, reason="while condition")
                self._statements(statement.body, function)
            elif isinstance(statement, ast.If):
                condition = self._expression(statement.test, function)
                self.types.unify(condition, self.bool, line=statement.lineno, reason="if condition")
                self._statements(statement.body, function)
                self._statements(statement.orelse, function)
            elif isinstance(statement, ast.Return):
                if statement.value is None:
                    raise SemanticSurfaceError(
                        f"{self.path}:{statement.lineno}: Unit is outside this experiment"
                    )
                self.types.unify(
                    function.return_term,
                    self._expression(statement.value, function),
                    line=statement.lineno,
                    reason="return value",
                )
                self._note(function, "$return", "return_value")
            elif isinstance(statement, ast.Expr):
                term = self._expression(statement.value, function)
                if index == len(statements) - 1:
                    self.types.unify(
                        function.return_term, term,
                        line=statement.lineno, reason="final expression",
                    )
                    self._note(function, "$return", "final_expression")
            else:
                raise SemanticSurfaceError(
                    f"{self.path}:{statement.lineno}: unsupported concise statement "
                    f"{type(statement).__name__}"
                )

    def bindings(self) -> tuple[InferredBinding, ...]:
        result: list[InferredBinding] = []
        for function in self.functions.values():
            for name, term in function.parameter_terms.items():
                result.append(
                    InferredBinding(
                        function.name,
                        name,
                        "parameter",
                        self.types.resolve(
                            term,
                            line=function.source_lines[name],
                            name=f"{function.name}.{name}",
                        ),
                        function.assignment_counts.get(name, 0) > 0,
                        function.source_lines[name],
                        tuple(
                            sorted(
                                function.evidence.get(
                                    name, {"call_or_use_constraint"}
                                )
                            )
                        ),
                    )
                )
            result.append(
                InferredBinding(
                    function.name,
                    "$return",
                    "return",
                    self.types.resolve(
                        function.return_term,
                        line=function.node.lineno,
                        name=f"{function.name} return",
                    ),
                    False,
                    function.node.lineno,
                    tuple(
                        sorted(
                            function.evidence.get(
                                "$return", {"body_constraint"}
                            )
                        )
                    ),
                )
            )
            for name, term in function.local_terms.items():
                if name in function.parameter_terms:
                    continue
                count = function.assignment_counts.get(name, 0)
                result.append(
                    InferredBinding(
                        function.name,
                        name,
                        "local",
                        self.types.resolve(
                            term,
                            line=function.source_lines[name],
                            name=f"{function.name}.{name}",
                        ),
                        count > 1,
                        function.source_lines[name],
                        tuple(
                            sorted(
                                function.evidence.get(
                                    name, {"use_constraint"}
                                )
                            )
                        ),
                    )
                )
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item.owner, item.source_line, item.kind, item.name
                ),
            )
        )


def _canonical_function(
    function: ast.FunctionDef,
    binding_map: dict[tuple[str, str, str], InferredBinding],
) -> str:
    node = copy.deepcopy(function)
    mutable_parameters = {
        item.name
        for key, item in binding_map.items()
        if key[0] == node.name
        and item.kind == "parameter"
        and item.mutable
    }
    for parameter in node.args.args:
        original_name = parameter.arg
        item = binding_map[
            (node.name, "parameter", original_name)
        ]
        parameter.annotation = ast.Name(
            id=item.type_name, ctx=ast.Load()
        )
        if original_name in mutable_parameters:
            parameter.arg = f"__input_{original_name}"
    returns = binding_map[(node.name, "return", "$return")]
    node.returns = ast.Name(id=returns.type_name, ctx=ast.Load())
    first_assignments: set[str] = set()
    class AnnotateFirst(ast.NodeTransformer):
        def visit_Assign(self, assignment: ast.Assign) -> ast.AST:
            self.generic_visit(assignment)
            if (
                len(assignment.targets) == 1
                and isinstance(assignment.targets[0], ast.Name)
                and assignment.targets[0].id not in first_assignments
                and (node.name, "local", assignment.targets[0].id) in binding_map
            ):
                name = assignment.targets[0].id
                first_assignments.add(name)
                item = binding_map[(node.name, "local", name)]
                return ast.copy_location(
                    ast.AnnAssign(
                        target=assignment.targets[0],
                        annotation=ast.Name(id=item.type_name, ctx=ast.Load()),
                        value=assignment.value,
                        simple=1,
                    ),
                    assignment,
                )
            return assignment

    node = AnnotateFirst().visit(node)
    ast.fix_missing_locations(node)
    text = ast.unparse(node)
    lines = text.splitlines()
    lines[0] = re.sub(r"^def\s+", "fn ", lines[0])
    mutable = {
        item.name
        for key, item in binding_map.items()
        if key[0] == node.name and item.mutable
    }
    if mutable_parameters:
        rewritten: list[str] = [lines[0]]
        indent = "    "
        for parameter_name in sorted(mutable_parameters):
            binding = binding_map[
                (node.name, "parameter", parameter_name)
            ]
            rewritten.append(
                f"{indent}var {parameter_name}: "
                f"{binding.type_name} = __input_{parameter_name}"
            )
        rewritten.extend(lines[1:])
        lines = rewritten
    declared: set[str] = set()
    for index in range(1, len(lines)):
        match = re.match(r"^(\s+)([A-Za-z_]\w*):\s*(UInt64|Bool)\s*=", lines[index])
        if not match:
            continue
        indent, name, _ = match.groups()
        if name in declared:
            continue
        declared.add(name)
        keyword = "var" if name in mutable else "let"
        lines[index] = indent + keyword + " " + lines[index][len(indent) :]
    canonical = "\n".join(lines)
    canonical = re.sub(r"\bTrue\b", "true", canonical)
    canonical = re.sub(r"\bFalse\b", "false", canonical)
    canonical = re.sub(
        r"for\s+([A-Za-z_]\w*)\s+in\s+merlo_range\((.+?),\s*(.+?)\):",
        r"for \1 in \2..\3:",
        canonical,
    )
    return canonical


def elaborate_semantic_surface(
    source: str,
    *,
    path: str = "main.mlo",
    entry_function: str = "main",
) -> ElaboratedSurface:
    if not source.strip():
        raise SemanticSurfaceError("empty concise source")
    try:
        parsed = ast.parse(_preprocess(source), filename=path)
    except SyntaxError as exc:
        raise SemanticSurfaceError(
            f"{path}:{exc.lineno}:{exc.offset}: {exc.msg}"
        ) from exc
    functions, top_level = _prepare_module(parsed, path=path)
    if entry_function not in {item.name for item in functions}:
        raise SemanticSurfaceError(f"{path}: missing entry function {entry_function!r}")
    inferencer = _Inferencer(functions, path=path)
    bindings = inferencer.bindings()
    binding_map = {
        (item.owner, item.kind, item.name): item
        for item in bindings
    }
    canonical = "\n\n".join(
        _canonical_function(function, binding_map)
        for function in functions
    ) + "\n"
    return ElaboratedSurface(
        source,
        path,
        canonical,
        bindings,
        entry_function,
        top_level,
    )


def compile_semantic_surface(
    source: str,
    *,
    path: str = "main.mlo",
    entry_function: str = "main",
) -> SemanticSurfaceCompilation:
    elaborated = elaborate_semantic_surface(
        source, path=path, entry_function=entry_function
    )
    try:
        hir = compile_native_hir(
            elaborated.canonical_source,
            path=path,
            entry_function=entry_function,
        )
        mir = lower_native_hir_to_performance(hir)
        optimized, snapshots = optimize_mir(mir)
        generated_c = CEmitter(optimized, runtime_arguments=True).emit()
    except (PerformanceCompileError, ValueError) as exc:
        raise SemanticSurfaceError(f"{path}: canonical lowering failed: {exc}") from exc
    return SemanticSurfaceCompilation(
        elaborated,
        hir,
        mir,
        optimized,
        tuple(snapshots),
        generated_c,
    )


def build_semantic_surface(
    source: str,
    *,
    output_dir: str | Path,
    path: str = "main.mlo",
    stem: str = "semantic_surface",
) -> SemanticSurfaceBuild:
    compilation = compile_semantic_surface(source, path=path)
    native = compile_c_source(
        compilation.generated_c,
        output_dir=output_dir,
        stem=stem,
    )
    if native.status != "MEASURED":
        raise SemanticSurfaceError(
            f"{path}: native build failed: {native.stderr}"
        )
    return SemanticSurfaceBuild(compilation, native)


__all__ = [
    "ElaboratedSurface",
    "InferredBinding",
    "SEMANTIC_SURFACE_CONTRACT",
    "SEMANTIC_SURFACE_SCHEMA_VERSION",
    "SemanticSurfaceBuild",
    "SemanticSurfaceCompilation",
    "SemanticSurfaceError",
    "build_semantic_surface",
    "compile_semantic_surface",
    "elaborate_semantic_surface",
]
