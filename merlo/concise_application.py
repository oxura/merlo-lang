from __future__ import annotations

import ast
import copy
import hashlib
import json
import re

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from .structured_hir_v2 import _rewrite_postfix_try
from .intrinsics import (
    INTRINSIC_SIGNATURES,
    contextual_result_type,
    format_intrinsic_arity,
    intrinsic_signature,
)
from .modules import STDLIB_MODULES, _declaration
from .version import VERSIONS
from .runtime_contract import ALPHA_EFFECTS, CLOSED_EFFECTS
from .canonical_ast import (
    CanonicalBinding,
    CanonicalEnum,
    CanonicalFunction,
    CanonicalProgram,
    CanonicalRecord,
    CanonicalReturn,
)
from .surface_ast import SourceSpan as SurfaceSourceSpan
from .type_parser import (
    GenericTypeSyntaxError,
    generic_arguments,
    iter_type_expressions,
    parse_type,
    split_structural_commas,
    validate_type_expr,
)

CONCISE_APPLICATION_SCHEMA_VERSION = VERSIONS.frontend
CONCISE_APPLICATION_CONTRACT = "merlo.concise-application.v2"
CONCISE_SURFACE_VERSION = VERSIONS.language
_ALLOWED_EFFECTS = ALPHA_EFFECTS
_FORBIDDEN_FEATURES = (
    "interface ",
    "async ",
    "flow ",
    "machine ",
    "macro ",
    "web ",
    "ui ",
)
_SCALARS = frozenset(
    {"Unit", "Bool", "Byte", "UInt64", "Int64", "Float32", "Float64"}
)
_NUMERIC_TYPES = frozenset({"Byte", "UInt64", "Int64", "Float32", "Float64"})
_TYPE_ALIASES = {"Int": "Int64", "UInt": "UInt64", "Float": "Float64"}
_CONCISE_MAP_TYPE = "Map[Text,UInt64]"
_OWNERS = frozenset({"Text", "Bytes", "TextBuilder"})


class ConciseApplicationError(ValueError):
    """A concise program cannot be elaborated without guessing."""


@dataclass(frozen=True)
class SourceOrigin:
    canonical_line: int
    path: str
    source_line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_line": self.canonical_line,
            "path": self.path,
            "source_line": self.source_line,
        }


@dataclass(frozen=True)
class InferenceDecision:
    owner: str
    name: str
    kind: str
    type_name: str
    mutable: bool
    path: str
    line: int
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "kind": self.kind,
            "type": self.type_name,
            "mutable": self.mutable,
            "path": self.path,
            "line": self.line,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class TaskBoundary:
    name: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    path: str
    line: int
    public: bool

    @property
    def revision_id(self) -> str:
        return _digest(
            {
                "name": self.name,
                "parameters": self.parameters,
                "return_type": self.return_type,
                "effects": self.effects,
                "capabilities": self.capabilities,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "path": self.path,
            "line": self.line,
            "public": self.public,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class PublicInterface:
    module: str
    name: str
    kind: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str | None
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]

    @property
    def revision_id(self) -> str:
        return _digest(
            {
                "module": self.module,
                "name": self.name,
                "kind": self.kind,
                "parameters": self.parameters,
                "return_type": self.return_type,
                "effects": self.effects,
                "capabilities": self.capabilities,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "name": self.name,
            "kind": self.kind,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class ConciseApplicationElaboration:
    entry_path: str
    modules: tuple[str, ...]
    source_sha256: str
    canonical_source: str
    canonical_program: CanonicalProgram
    machine_source: str
    concise_semantic_digest: str
    canonical_semantic_digest: str
    decisions: tuple[InferenceDecision, ...]
    tasks: tuple[TaskBoundary, ...]
    interfaces: tuple[PublicInterface, ...]
    origins: tuple[SourceOrigin, ...]
    interface_lock_path: str
    interface_lock_valid: bool
    canonical_reference_equal: bool

    @property
    def semantic_ast_equal(self) -> bool:
        return self.concise_semantic_digest == self.canonical_semantic_digest

    @property
    def interface_revision(self) -> str:
        return _digest([item.to_dict() for item in self.interfaces])

    @property
    def effects(self) -> tuple[str, ...]:
        return tuple(sorted({effect for task in self.tasks for effect in task.effects}))

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(
            sorted({capability for task in self.tasks for capability in task.capabilities})
        )

    @property
    def ambiguous_points(self) -> tuple[str, ...]:
        return ()

    @property
    def argument_parsing(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "task": task.name,
                "name": name,
                "type": type_name,
                "checked": True,
                "failure": "typed AppError",
            }
            for task in self.tasks
            for name, type_name in task.parameters
        )

    @property
    def ownership_transfers(self) -> tuple[str, ...]:
        transfers = []
        if "fs.read" in self.effects:
            transfers.append("fs.read returns owned Bytes")
        if any(item.type_name in _OWNERS or item.type_name.startswith("Vec[") for item in self.decisions):
            transfers.append("owned locals move into constructors and are dropped on every exit")
        return tuple(transfers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONCISE_APPLICATION_SCHEMA_VERSION,
            "contract": CONCISE_APPLICATION_CONTRACT,
            "entry_path": self.entry_path,
            "modules": list(self.modules),
            "source_sha256": self.source_sha256,
            "canonical_sha256": hashlib.sha256(self.canonical_source.encode()).hexdigest(),
            "machine_sha256": hashlib.sha256(self.machine_source.encode()).hexdigest(),
            "semantic_ast": {
                "concise_digest": self.concise_semantic_digest,
                "canonical_digest": self.canonical_semantic_digest,
                "equal": self.semantic_ast_equal,
            },
            "decisions": [item.to_dict() for item in self.decisions],
            "tasks": [item.to_dict() for item in self.tasks],
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "implicit_argument_parsing": list(self.argument_parsing),
            "ownership_transfers": list(self.ownership_transfers),
            "ambiguous_points": list(self.ambiguous_points),
            "interfaces": [item.to_dict() for item in self.interfaces],
            "interface_revision": self.interface_revision,
            "interface_lock_path": self.interface_lock_path,
            "interface_lock_valid": self.interface_lock_valid,
            "canonical_reference_equal": self.canonical_reference_equal,
            "origins": [item.to_dict() for item in self.origins],
            "invariants": {
                "no_any": not _contains_dynamic_any(self.canonical_source),
                "ambiguity_rejected": True,
                "effects_explicit": bool(self.tasks) and all(item.effects for item in self.tasks),
                "capabilities_closed": set(self.capabilities) <= _ALLOWED_EFFECTS,
                "ordinary_lifetime_annotations": 0,
                "manual_memory_operations": 0,
            },
        }






@dataclass(frozen=True)
class _Module:
    name: str
    path: Path
    source: str
    imports: tuple[str, ...]
    body: str
    body_source_lines: tuple[int, ...]


@dataclass(frozen=True)
class _Record:
    name: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Enum:
    name: str
    variants: tuple[tuple[str, str | None], ...]


@dataclass
class _FunctionState:
    name: str
    node: ast.FunctionDef
    parameters: dict[str, str]
    return_type: str
    locals: dict[str, str]
    evidence: dict[str, set[str]]
    first_lines: dict[str, int]
    assignment_counts: dict[str, int]


@dataclass(frozen=True)
class _CoreAssembly:
    concise_source: str
    canonical_source: str
    canonical_program: CanonicalProgram
    decisions: tuple[InferenceDecision, ...]
    origins: tuple[SourceOrigin, ...]
    exports: tuple[tuple[str, str, str], ...]


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

def _normalize_type(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    type_name = ast.unparse(node).replace(" ", "")
    for alias, canonical in _TYPE_ALIASES.items():
        type_name = re.sub(rf"\b{alias}\b", canonical, type_name)
    return type_name


def _contains_dynamic_any(source: str) -> bool:
    parsed = ast.parse(_preprocess_core(source))
    return any(
        isinstance(node, ast.Name) and node.id == "Any"
        for node in ast.walk(parsed)
    )


def _one_edit_apart(left: str, right: str) -> bool:
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(
            a != b for a, b in zip(left, right, strict=True)
        ) == 1
    shorter, longer = (
        (left, right) if len(left) < len(right) else (right, left)
    )
    index = 0
    differences = 0
    for character in longer:
        if index < len(shorter) and shorter[index] == character:
            index += 1
        else:
            differences += 1
            if differences > 1:
                return False
    return True


def _project_root(entry: Path) -> Path:
    resolved = entry.resolve()
    for parent in (resolved.parent, *resolved.parents):
        candidate = parent / "app" / "main.mlo"
        if candidate == resolved:
            return parent
    return resolved.parent


def _read_module(
    path: Path,
    root: Path,
    *,
    external_name: str | None = None,
) -> _Module:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConciseApplicationError(f"{path}: cannot read module: {exc}") from exc
    lines = source.splitlines()
    if not lines:
        raise ConciseApplicationError(f"{path}: empty module")
    match = re.fullmatch(r"module\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", lines[0].strip())
    if match is None:
        raise ConciseApplicationError(f"{path}:1: expected `module qualified.name`")
    name = match.group(1)
    expected = root.joinpath(*name.split(".")).with_suffix(".mlo")
    if external_name is not None:
        if name != external_name:
            raise ConciseApplicationError(f"{path}: declares {name!r}, expected standard module {external_name!r}")
    elif path.resolve() != expected.resolve():
        raise ConciseApplicationError(
            f"{path}:1: module {name!r} must live at {expected}"
        )
    imports = []
    body_pairs: list[tuple[str, int]] = []
    header = True
    for line_number, line in enumerate(lines[1:], 2):
        stripped = line.strip()
        if header and not stripped:
            body_pairs.append(("", line_number))
            continue
        import_match = re.fullmatch(
            r"use\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", stripped
        )
        if header and import_match:
            imports.append(import_match.group(1))
            continue
        header = False
        if stripped.startswith("use "):
            raise ConciseApplicationError(
                f"{path}:{line_number}: imports must precede declarations"
            )
        body_pairs.append((line, line_number))
    while body_pairs and not body_pairs[0][0].strip():
        body_pairs.pop(0)
    while body_pairs and not body_pairs[-1][0].strip():
        body_pairs.pop()
    return _Module(
        name,
        path,
        source,
        tuple(imports),
        "\n".join(line for line, _ in body_pairs) + "\n",
        tuple(line_number for _, line_number in body_pairs),
    )
def _generic_arguments(type_name: str) -> tuple[str, ...]:
    if "[" not in type_name:
        return ()
    try:
        return generic_arguments(type_name)
    except GenericTypeSyntaxError as error:
        raise ConciseApplicationError(
            f"malformed generic type {type_name!r}: {error}"
        ) from error


def _map_types(type_name: str | None) -> tuple[str, str] | None:
    if not type_name:
        return None
    try:
        parsed = parse_type(type_name)
    except GenericTypeSyntaxError as error:

        if type_name.startswith("Map"):
            raise ConciseApplicationError(
                f"malformed generic type {type_name!r}: {error}"
            ) from error
        return None
    if parsed.name != "Map" or len(parsed.args) != 2:
        return None
    return parsed.args[0].canonical, parsed.args[1].canonical
def _split_parameters(payload: str, context: str) -> tuple[str, ...]:
    try:
        return split_structural_commas(payload)
    except GenericTypeSyntaxError as error:
        raise ConciseApplicationError(
            f"{context}: malformed generic type: {error}"
        ) from error


def _sum_nominal_name(type_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", type_name)


def lower_concise_sum_types(source: str) -> str:
    try:
        occurrences = iter_type_expressions(source)
    except GenericTypeSyntaxError as error:
        raise ConciseApplicationError(
            f"malformed generic type: {error}"
        ) from error
    normalized = {
        source[start:end]: expression.canonical
        for start, end, expression in occurrences
        if expression.name in {"Option", "Result"}
    }
    source = _rewrite_code_text(source, normalized)
    sum_types = sorted(set(normalized.values()))
    if not sum_types:
        return source
    source = _rewrite_code_identifiers(
        source,
        {
            "Result.Ok": "Ok",
            "Result.Err": "Err",
            "Option.Some": "Some",
            "Option.None": "None",
        },
        call_only=frozenset(
            {"Result.Ok", "Result.Err", "Option.Some"}
        ),
    )
    declarations = []
    mapping: dict[str, str] = {}
    used_nominals: dict[str, str] = {}
    for type_name in sum_types:
        nominal = _sum_nominal_name(type_name)
        if nominal in used_nominals and used_nominals[nominal] != type_name:
            nominal = f"{nominal}_{hashlib.sha256(type_name.encode()).hexdigest()[:8]}"
        mapping[type_name] = nominal
        used_nominals[nominal] = type_name
    for type_name in sum_types:
        nominal = mapping[type_name]
        arguments = _generic_arguments(type_name)
        if type_name.startswith("Option[") and len(arguments) == 1:
            declarations.extend(
                (
                    f"enum {nominal}:",
                    "    NoneValue",
                    f"    Some: {arguments[0]}",
                    "",
                )
            )
        elif type_name.startswith("Result[") and len(arguments) == 2:
            declarations.extend(
                (
                    f"enum {nominal}:",
                    f"    Ok: {arguments[0]}",
                    f"    Err: {arguments[1]}",
                    "",
                )
            )
        else:
            raise ConciseApplicationError(
                f"unsupported sum type {type_name!r}"
            )
    output = []
    current_return: str | None = None
    variables: dict[str, str] = {}
    match_sum: tuple[int, str] | None = None
    for original, protected in _protected_line_views(source):
        line = original
        indent = len(line) - len(line.lstrip())
        if match_sum and line.strip() and indent <= match_sum[0]:
            match_sum = None
        function = re.match(
            r"^fn\s+[A-Za-z_]\w*\((.*)\)\s*->\s*(.+):$",
            line,
        )
        if function:
            variables = {}
            current_return = (
                function.group(2).strip().replace(" ", "")
            )
            for parameter in _split_parameters(
                function.group(1), "concise function parameters"
            ):
                if ":" in parameter:
                    name, type_name = parameter.split(":", 1)
                    variables[name.strip()] = (
                        type_name.strip().replace(" ", "")
                    )
        binding = re.match(
            r"^\s*(?:let|var)\s+([A-Za-z_]\w*)\s*:\s*"
            r"([^=]+?)\s*=",
            line,
        )
        binding_type = None
        if binding:
            binding_type = binding.group(2).strip().replace(" ", "")
            variables[binding.group(1)] = binding_type
        match = re.match(
            r"^(\s*)match\s+([A-Za-z_]\w*)\s*:$",
            line,
        )
        if match:
            subject_type = variables.get(match.group(2))
            if subject_type in mapping:
                match_sum = (len(match.group(1)), subject_type)
        active_type = (
            binding_type
            if binding_type in mapping
            else current_return
            if current_return in mapping
            else None
        )
        if match_sum and line.lstrip().startswith("case "):
            active_type = match_sum[1]
        line = _rewrite_code_identifiers(
            line,
            {
                "Result.Ok": "Ok",
                "Result.Err": "Err",
                "Option.Some": "Some",
                "Option.None": "None",
            },
            call_only=frozenset(
                {"Result.Ok", "Result.Err", "Option.Some"}
            ),
        )
        if active_type:
            nominal = mapping[active_type]
            if active_type.startswith("Option["):
                line = _rewrite_code_identifiers(
                    line,
                    {
                        "Some": f"{nominal}.Some",
                        "None": f"{nominal}.NoneValue",
                    },
                    call_only=frozenset({"Some"}),
                )
            else:
                line = _rewrite_code_identifiers(
                    line,
                    {
                        "Ok": f"{nominal}.Ok",
                        "Err": f"{nominal}.Err",
                    },
                    call_only=frozenset({"Ok", "Err"}),
                )
        for type_name in sorted(mapping, key=len, reverse=True):
            line = line.replace(type_name, mapping[type_name])
        for token, replacement in protected.items():
            line = line.replace(token, replacement)
        output.append(line)
    return "\n".join((*declarations, *output)).strip() + "\n"



def _load_modules(entry: Path) -> tuple[_Module, ...]:
    root = _project_root(entry)
    visiting: list[str] = []
    loaded: dict[str, _Module] = {}
    ordered: list[_Module] = []

    def visit(name: str, path: Path) -> None:
        if name in visiting:
            cycle = " -> ".join((*visiting, name))
            raise ConciseApplicationError(f"{path}: cyclic module dependency: {cycle}")
        if name in loaded:
            return
        standard_path = STDLIB_MODULES.get(name)
        if not path.exists() and standard_path is not None:
            module = _read_module(standard_path, root, external_name=name)
        else:
            module = _read_module(path, root)
        if module.name != name:
            raise ConciseApplicationError(
                f"{path}: imported as {name!r}, declares {module.name!r}"
            )
        visiting.append(name)
        for dependency in module.imports:
            visit(dependency, root.joinpath(*dependency.split(".")).with_suffix(".mlo"))
        visiting.pop()
        loaded[name] = module
        ordered.append(module)

    entry_module = _read_module(entry.resolve(), root)
    visit(entry_module.name, entry.resolve())
    return tuple(ordered)

_EFFECT_CALL_PATTERNS: dict[str, tuple[str, ...]] = {}
for _intrinsic in INTRINSIC_SIGNATURES.values():
    _EFFECT_CALL_PATTERNS.setdefault(_intrinsic.effect, ())
    _EFFECT_CALL_PATTERNS[_intrinsic.effect] += (
        rf"\b{re.escape(_intrinsic.name)}\s*\(",
    )


def _direct_effects(source: str) -> set[str]:
    body = "\n".join(
        line for line in source.splitlines()
        if not re.fullmatch(r"\s*uses\s+.+", line.strip())
    )
    effects = {
        effect
        for effect, patterns in _EFFECT_CALL_PATTERNS.items()
        if any(re.search(pattern, body) for pattern in patterns)
    }
    if re.search(r"\.lines\s*\(", body):
        effects.add("fs.read")
    return effects


def _resolve_task_effects(
    modules: tuple[_Module, ...],
    tasks: tuple[TaskBoundary, ...],
) -> tuple[TaskBoundary, ...]:
    bodies: dict[str, str] = {}
    for module in modules:
        lines = module.body.splitlines()
        index = 0
        while index < len(lines):
            match = re.fullmatch(
                r"(?:export\s+)?task\s+([A-Za-z_]\w*)\((.*)\)\s*->\s*(.+):",
                lines[index].strip(),
            )
            if match is None:
                index += 1
                continue
            name = match.group(1)
            index += 1
            body: list[str] = []
            while index < len(lines) and (
                not lines[index].strip() or lines[index].startswith((" ", "\t"))
            ):
                body.append(lines[index])
                index += 1
            bodies[name] = "\n".join(body)
    by_name = {task.name: task for task in tasks}
    direct = {name: _direct_effects(body) for name, body in bodies.items()}
    calls = {
        name: {
            candidate
            for candidate in by_name
            if candidate != name and re.search(rf"\b{re.escape(candidate)}\s*\(", body)
        }
        for name, body in bodies.items()
    }
    resolved: dict[str, frozenset[str]] = {}

    def visit(name: str, visiting: frozenset[str] = frozenset()) -> frozenset[str]:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            raise ConciseApplicationError(f"EffectCycle:{name}")
        result = set(direct.get(name, set()))
        for callee in calls.get(name, set()):
            result.update(visit(callee, visiting | {name}))
        if "fs.close" in bodies.get(name, ""):
            result.update(
                effect for effect in by_name[name].effects
                if effect in {"fs.read", "fs.write"}
            )
        resolved[name] = frozenset(result)
        return resolved[name]
    output: list[TaskBoundary] = []
    for task in tasks:
        actual = tuple(sorted(visit(task.name)))
        declared = set(task.effects)
        missing = set(actual) - declared
        extra = declared - set(actual)
        if missing or extra:
            detail = f"missing={sorted(missing)} extra={sorted(extra)}"
            raise ConciseApplicationError(
                f"{task.path}:{task.line}: EffectDeclarationMismatch {task.name}: {detail}"
            )
        output.append(
            TaskBoundary(
                task.name, task.parameters, task.return_type, actual, actual,
                task.path, task.line, task.public,
            )
        )
    return tuple(output)





def _extract_task(
    module: _Module,
    effectful_dependencies: Iterable[TaskBoundary] = (),
) -> tuple[
    str,
    tuple[TaskBoundary, ...],
    str,
    tuple[int, ...],
    tuple[int, ...],
]:
    lines = module.body.splitlines()
    source_lines = module.body_source_lines
    output: list[str] = []
    output_origins: list[int] = []
    tasks: list[TaskBoundary] = []
    canonical_tasks: list[str] = []
    task_origins: list[int] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        source_line = source_lines[index]
        host_error = re.fullmatch(r"export\s+enum\s+AppError\s*:", line.strip())
        if host_error and module.name.endswith(".main"):
            canonical_tasks.append(re.sub(r"^export\s+", "", line))
            task_origins.append(source_line)
            index += 1
            while index < len(lines) and (
                not lines[index].strip()
                or lines[index].startswith((" ", "\t"))
            ):
                canonical_tasks.append(lines[index])
                task_origins.append(source_lines[index])
                index += 1
            continue
        match = re.fullmatch(
            r"(export\s+)?task\s+([A-Za-z_]\w*)\((.*)\)\s*->\s*(.+):",
            line.strip(),
        )
        if match is None:
            output.append(line)
            output_origins.append(source_line)
            index += 1
            continue
        public = bool(match.group(1))
        name = match.group(2)
        raw_parameters = match.group(3).strip()
        return_type = match.group(4).strip().replace(" ", "")
        parameters: list[tuple[str, str]] = []
        if raw_parameters:
            try:
                parameter_items = split_structural_commas(raw_parameters)
            except GenericTypeSyntaxError as error:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: malformed parameter types: {error}"
                ) from error
            for item in parameter_items:
                if ":" not in item:
                    raise ConciseApplicationError(
                        f"{module.path}:{source_line}: public task parameters require annotations"
                    )
                parameter_name, type_name = item.split(":", 1)
                parameters.append((parameter_name.strip(), type_name.strip()))
        body: list[tuple[str, int]] = []
        index += 1
        while index < len(lines) and (
            not lines[index].strip()
            or lines[index].startswith((" ", "\t"))
        ):
            body.append((lines[index], source_lines[index]))
            index += 1
        uses = ()
        for body_line, _ in body:
            uses_match = re.fullmatch(r"\s*uses\s+(.+)", body_line)
            if uses_match:
                uses = tuple(
                    sorted(item.strip() for item in uses_match.group(1).split(","))
                )
                break
        if not uses:
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: MissingEffectDeclaration {name}; "
                "task must declare `uses`"
            )
        unknown = set(uses) - _ALLOWED_EFFECTS
        if unknown:
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: UnsupportedEffect {sorted(unknown)}"
            )
        joined = "\n".join(body_line for body_line, _ in body)
        uses_set = set(uses)
        actual = _direct_effects(joined)
        for dependency in effectful_dependencies:
            if re.search(rf"\b{re.escape(dependency.name)}\s*\(", joined):
                actual.update(dependency.effects)
        if not actual <= uses_set:
            missing = tuple(sorted(actual - uses_set))
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: MissingCapability {name}: "
                f"declare {missing} in the task uses list"
            )
        if name == "main" and tuple(type_name for _, type_name in parameters) != ("Path",):
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: CLI main requires exactly one Path parameter"
            )

        canonical_tasks.append(re.sub(r"^export\s+", "", line))
        task_origins.append(source_line)
        for body_line, body_source_line in body:
            if re.fullmatch(r"\s*uses\s+.+", body_line.strip()):
                continue
            canonical_tasks.append(body_line)
            task_origins.append(body_source_line)
        tasks.append(
            TaskBoundary(
                name,
                tuple(parameters),
                return_type,
                uses,
                uses,
                str(module.path),
                source_line,
                public,
            )
        )
    while output and not output[-1].strip():
        output.pop()
        output_origins.pop()
    while canonical_tasks and not canonical_tasks[-1].strip():
        canonical_tasks.pop()
        task_origins.pop()
    return (
        "\n".join(output) + "\n",
        tuple(tasks),
        "\n".join(canonical_tasks) + "\n",
        tuple(output_origins),
        tuple(task_origins),
    )

def _protected_mask(source: str) -> list[bool]:
    protected = [False] * len(source)
    cursor = 0
    length = len(source)
    while cursor < length:
        character = source[cursor]
        if character == "#":
            end = source.find("\n", cursor)
            if end < 0:
                end = length
            protected[cursor:end] = [True] * (end - cursor)
            cursor = end
            continue
        if character in {'"', "'"}:
            delimiter_length = 3 if source.startswith(character * 3, cursor) else 1
            end = cursor + delimiter_length
            while end < length:
                if source[end] == "\\":
                    end += 2
                    continue
                if source.startswith(character * delimiter_length, end):
                    end += delimiter_length
                    break
                end += 1
            end = min(end, length)
            protected[cursor:end] = [True] * (end - cursor)
            cursor = end
            continue
        cursor += 1
    return protected


def _protected_line_views(
    source: str,
) -> list[tuple[str, dict[str, str]]]:
    mask = _protected_mask(source)
    views: list[tuple[str, dict[str, str]]] = []
    offset = 0
    token_number = 0
    for physical in source.splitlines(keepends=True):
        line = physical.rstrip("\r\n")
        replacements: dict[str, str] = {}
        pieces: list[str] = []
        cursor = 0
        while cursor < len(line):
            if not mask[offset + cursor]:
                start = cursor
                cursor += 1
                while cursor < len(line) and not mask[offset + cursor]:
                    cursor += 1
                pieces.append(line[start:cursor])
                continue
            start = cursor
            cursor += 1
            while cursor < len(line) and mask[offset + cursor]:
                cursor += 1
            token = f"\x00{token_number}\x00"
            token_number += 1
            replacements[token] = line[start:cursor]
            pieces.append(token)
        views.append(("".join(pieces), replacements))
        offset += len(physical)
    return views

def _rewrite_code_text(
    source: str,
    replacements: dict[str, str],
) -> str:
    lines: list[str] = []
    for original, protected in _protected_line_views(source):
        line = original
        for before, after in sorted(
            replacements.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            line = line.replace(before, after)
        for token, replacement in protected.items():
            line = line.replace(token, replacement)
        lines.append(line)
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def _rewrite_code_identifiers(
    source: str,
    replacements: dict[str, str],
    *,
    call_only: frozenset[str] = frozenset(),
) -> str:
    """Rewrite exact identifiers in code, preserving strings and comments."""
    protected = _protected_mask(source)
    output: list[str] = []
    cursor = 0
    length = len(source)
    while cursor < length:
        if protected[cursor]:
            output.append(source[cursor])
            cursor += 1
            continue
        character = source[cursor]
        if character.isalpha() or character == "_":
            start = cursor
            cursor += 1
            while cursor < length and (
                source[cursor].isalnum() or source[cursor] == "_"
            ):
                cursor += 1
            identifier = source[start:cursor]
            candidate = identifier
            candidate_end = cursor
            if cursor < length and source[cursor] == ".":
                member_start = cursor + 1
                member_end = member_start
                if member_start < length and (
                    source[member_start].isalpha()
                    or source[member_start] == "_"
                ):
                    member_end += 1
                    while member_end < length and (
                        source[member_end].isalnum()
                        or source[member_end] == "_"
                    ):
                        member_end += 1
                    candidate = source[start:member_end]
                    candidate_end = member_end
            replacement = replacements.get(candidate)
            if replacement is None:
                candidate = identifier
                candidate_end = cursor
                replacement = replacements.get(identifier)
            line_tail = source[candidate_end:].split("\n", 1)[0]
            is_call = line_tail.lstrip().startswith("(")
            if replacement is not None and (
                candidate not in call_only or is_call
            ):
                output.append(replacement)
                cursor = candidate_end
                continue
            output.append(source[start:candidate_end])
            cursor = candidate_end
            continue
        output.append(character)
        cursor += 1
    return "".join(output)


def _rewrite_language_literals(source: str) -> str:
    return _rewrite_code_identifiers(
        source,
        {
            "true": "True",
            "false": "False",
            "Option.None": "Option.NoneValue",
        },
    )


def _preprocess_core(source: str) -> str:
    source = _rewrite_language_literals(source)
    output = []
    for original, protected in _protected_line_views(source):
        indent = original[: len(original) - len(original.lstrip())]
        line = re.sub(r"^(\s*)export\s+", r"\1", original)
        stripped = line.strip()
        if not indent:
            line = re.sub(r"^(record|enum)\s+", "class ", line)
            if re.fullmatch(r"[A-Z][A-Za-z0-9_]*\s*:", line):
                line = "class " + line
            expression_function = re.fullmatch(
                r"([a-z_][A-Za-z0-9_]*)\((.*)\)\s*"
                r"(?:->\s*(.+?))?\s*=\s*(.+)",
                line,
            )
            block_function = re.fullmatch(
                r"([a-z_][A-Za-z0-9_]*)\((.*)\)\s*"
                r"(?:->\s*(.+))?\s*:",
                line,
            )
            if expression_function is not None:
                name, parameters, return_type, expression = (
                    expression_function.groups()
                )
                suffix = f" -> {return_type}" if return_type else ""
                line = (
                    f"def {name}({parameters}){suffix}: "
                    f"return {expression}"
                )
            elif block_function is not None:
                name, parameters, return_type = block_function.groups()
                suffix = f" -> {return_type}" if return_type else ""
                line = f"def {name}({parameters}){suffix}:"
        line = re.sub(r"^(\s*)fn\s+", r"\1def ", line)
        line = re.sub(r"^(\s*)task\s+", r"\1def ", line)
        line = re.sub(r"^(\s*)const\s+", r"\1", line)
        line = re.sub(r"^(\s*)(?:let|var)\s+", r"\1", line)
        line = re.sub(
            r"(?<![A-Za-z0-9_]])([A-Za-z_][A-Za-z0-9_]*)\?",
            r"Option[\1]",
            line,
        )
        if re.fullmatch(r"\s*uses\s+.+", line):
            line = ""
        print_match = re.fullmatch(r"(\s*)print\s+(.+)", line)
        if print_match is not None:
            line = (
                f"{print_match.group(1)}"
                f"console.write({print_match.group(2)})"
            )
        line = _rewrite_postfix_try(line)
        if re.search(r"\b(?:and|or)\s*$", line):
            line += " " + chr(92)
        for token, replacement in protected.items():
            line = line.replace(token, replacement)
        output.append(line)
    return "\n".join(output) + "\n"


def _type_declarations(
    module: ast.Module,
    declaration_kinds: dict[str, str],
) -> tuple[dict[str, _Record], dict[str, _Enum]]:
    records: dict[str, _Record] = {}
    enums: dict[str, _Enum] = {}
    for declaration in (
        item for item in module.body
        if isinstance(item, ast.ClassDef)
    ):
        fields = []
        variants = []
        if declaration_kinds.get(declaration.name) == "record":
            for item in declaration.body:
                if not (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                ):
                    raise ConciseApplicationError(
                        f"concise: record {declaration.name} "
                        "requires typed fields"
                    )
                fields.append(
                    (
                        item.target.id,
                        _normalize_type(item.annotation) or "Unit",
                    )
                )
            records[declaration.name] = _Record(
                declaration.name,
                tuple(fields),
            )
        else:
            for item in declaration.body:
                if (
                    isinstance(item, ast.Expr)
                    and isinstance(item.value, ast.Name)
                ):
                    variants.append((item.value.id, None))
                elif (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                ):
                    variants.append(
                        (
                            item.target.id,
                            _normalize_type(item.annotation),
                        )
                    )
                else:
                    raise ConciseApplicationError(
                        f"concise: enum {declaration.name} "
                        "has unsupported variant syntax"
                    )
            enums[declaration.name] = _Enum(
                declaration.name,
                tuple(variants),
            )
    return records, enums


def _first_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class _Inference:
    def __init__(
        self,
        source: str,
        *,
        path: str,
        effectful_functions: frozenset[str] = frozenset(),
    ) -> None:
        self.source = source
        self.path = path
        self.external_effectful_functions = effectful_functions
        try:
            self.module = ast.parse(_preprocess_core(source), filename=path)
        except SyntaxError as exc:
            raise ConciseApplicationError(
                f"{path}:{exc.lineno}:{exc.offset}: {exc.msg}"
            ) from exc
        self.tail_return_lines: set[int] = set()
        for node in (
            item
            for item in self.module.body
            if isinstance(item, ast.FunctionDef)
        ):
            if node.body and isinstance(node.body[-1], ast.Expr):
                tail = node.body[-1]
                node.body[-1] = ast.copy_location(
                    ast.Return(value=tail.value),
                    tail,
                )
                self.tail_return_lines.add(tail.lineno)
        self.declaration_kinds: dict[str, str] = {}
        for match in re.finditer(
            r"(?m)^\s*(?:export\s+)?(?:(record|enum)\s+)?"
            r"([A-Z][A-Za-z0-9_]*)\s*:",
            source,
        ):
            kind, name = match.groups()
            self.declaration_kinds[name] = kind or "record"
        self.declared_function_kinds = {
            name: kind
            for kind, name in re.findall(
                r"(?m)^\s*(?:export\s+)?(fn|task)\s+"
                r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                source,
            )
        }
        self.records, self.enums = _type_declarations(
            self.module,
            self.declaration_kinds,
        )
        self.constants = {
            item.target.id: _normalize_type(item.annotation) or "UInt64"
            for item in self.module.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
        }
        self.functions: dict[str, _FunctionState] = {}
        for node in (
            item
            for item in self.module.body
            if isinstance(item, ast.FunctionDef)
        ):
            parameters = {}
            for parameter in node.args.args:
                type_name = _normalize_type(parameter.annotation)
                if type_name is not None:
                    parameters[parameter.arg] = type_name
            return_type = _normalize_type(node.returns)
            if return_type is None:
                return_type = "?"
            self.functions[node.name] = _FunctionState(
                node.name,
                node,
                parameters,
                return_type,
                {},
                {},
                {},
                {},
            )
        self.function_effects = self._infer_effects()
        self.effectful_functions = frozenset(
            name for name, effects in self.function_effects.items() if effects
        ) | effectful_functions
        self._validate_declarations()
        for state in self.functions.values():
            self._count_assignments(state.node.body, state)
        for _ in range(max(16, len(self.functions) * 2)):
            before = self._snapshot()
            for state in self.functions.values():
                self._statements(state.node.body, state)
            if before == self._snapshot():
                break
        self._finish()

    def _infer_effects(self) -> dict[str, tuple[str, ...]]:
        direct = {
            name: _direct_effects(ast.unparse(state.node))
            for name, state in self.functions.items()
        }
        main = self.functions.get("main")
        if (
            main is not None
            and self.declared_function_kinds.get("main") == "task"
            and tuple(main.parameters.values()) == ("Text",)
            and main.return_type == "Text"
        ):
            direct["main"].update(("console.read", "console.write"))
        calls = {
            name: {
                call.func.id
                for call in ast.walk(state.node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in self.functions
            }
            for name, state in self.functions.items()
        }
        resolved = {name: set(effects) for name, effects in direct.items()}
        for _ in range(max(1, len(self.functions))):
            changed = False
            for name, targets in calls.items():
                effects = set(resolved[name])
                for target in targets:
                    effects.update(resolved[target])
                if effects != resolved[name]:
                    resolved[name] = effects
                    changed = True
            if not changed:
                break
        return {
            name: tuple(sorted(effects))
            for name, effects in resolved.items()
        }

    def _snapshot(self) -> tuple[Any, ...]:
        return tuple(
            (name, tuple(sorted(state.parameters.items())), state.return_type, tuple(sorted(state.locals.items())))
            for name, state in sorted(self.functions.items())
        )

    def _validate_declarations(self) -> None:
        known = set(self.records) | set(self.enums) | set(_SCALARS) | {
            "Text", "Bytes", "BytesView", "TextView", "TextBuilder",
            "Path", "FileReader", "FileLines", "Vec", "Map", "Box",
            "Borrow", "Option", "Result", "Array", "Slice",
        }
        if any(
            isinstance(node, ast.Name) and node.id == "Any"
            for node in ast.walk(self.module)
        ):
            raise ConciseApplicationError(f"{self.path}: DynamicAnyForbidden")
        for feature in _FORBIDDEN_FEATURES:
            keyword = feature.strip()
            if re.search(
                rf"(?m)^\s*{re.escape(keyword)}\b",
                self.source,
                re.IGNORECASE,
            ):
                raise ConciseApplicationError(
                    f"{self.path}: DeferredFeatureForbidden {keyword}"
                )
        try:
            map_occurrences = iter_type_expressions(
                self.source, frozenset({"Map"})
            )
        except GenericTypeSyntaxError as error:
            raise ConciseApplicationError(
                f"{self.path}: malformed generic type: {error}"
            ) from error
        for start, end, _ in map_occurrences:
            type_name = self.source[start:end]
            map_types = _map_types(type_name)
            if (
                map_types is None
                or map_types[0] != "Text"
                or map_types[1] not in _SCALARS
            ):
                raise ConciseApplicationError(
                    f"{self.path}: UnsupportedMapType {type_name}; alpha Map "
                    "requires Text keys and scalar values"
                )
        for state in self.functions.values():
            for type_name in (*state.parameters.values(), state.return_type):
                try:
                    parsed_type = validate_type_expr(parse_type(type_name))
                except GenericTypeSyntaxError as error:
                    raise ConciseApplicationError(
                        f"{self.path}:{state.node.lineno}: malformed generic type "
                        f"{type_name!r}: {error}"
                    ) from error
                base = parsed_type.name
                if type_name != "?" and base not in known and type_name not in known:
                    raise ConciseApplicationError(
                        f"{self.path}:{state.node.lineno}: unknown type {type_name!r}"
                    )
            if (
                self.declared_function_kinds.get(state.name) == "fn"
                and self.function_effects.get(state.name)
            ):
                raise ConciseApplicationError(
                    f"{self.path}:{state.node.lineno}: EffectInPureFunction "
                    f"{state.name} cannot use "
                    f"{', '.join(self.function_effects[state.name])}"
                )
        self._validate_recursive_boundaries()
        return None

    def _validate_recursive_boundaries(self) -> None:
        graph: dict[str, set[str]] = {
            name: set() for name in self.functions
        }
        for name, state in self.functions.items():
            graph[name] = {
                call.func.id
                for call in ast.walk(state.node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in self.functions
            }
        index = 0
        indexes: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        stack: list[str] = []
        stacked: set[str] = set()
        groups: list[tuple[str, ...]] = []

        def visit(name: str) -> None:
            nonlocal index
            indexes[name] = index
            lowlinks[name] = index
            index += 1
            stack.append(name)
            stacked.add(name)
            for target in graph[name]:
                if target not in indexes:
                    visit(target)
                    lowlinks[name] = min(
                        lowlinks[name], lowlinks[target]
                    )
                elif target in stacked:
                    lowlinks[name] = min(
                        lowlinks[name], indexes[target]
                    )
            if lowlinks[name] == indexes[name]:
                group = []
                while True:
                    target = stack.pop()
                    stacked.remove(target)
                    group.append(target)
                    if target == name:
                        break
                groups.append(tuple(group))

        for name in graph:
            if name not in indexes:
                visit(name)
        for group in groups:
            recursive = len(group) > 1 or group[0] in graph[group[0]]
            if not recursive:
                continue
            constrained = any(
                self.functions[name].return_type != "?"
                or bool(self.functions[name].parameters)
                for name in group
            )
            if not constrained:
                state = self.functions[group[0]]
                raise ConciseApplicationError(
                    f"{self.path}:{state.node.lineno}: "
                    "RecursiveBoundaryAnnotationRequired "
                    f"{sorted(group)}"
                )
        return None

    def _note(self, state: _FunctionState, name: str, evidence: str) -> None:
        state.evidence.setdefault(name, set()).add(evidence)

    def _set(self, state: _FunctionState, name: str, type_name: str | None, line: int, evidence: str) -> bool:
        if type_name is None or type_name == "?":
            return False
        target = state.parameters if name in {item.arg for item in state.node.args.args} else state.locals
        prior = target.get(name)
        if prior is not None and prior != type_name:
            raise ConciseApplicationError(
                f"{self.path}:{line}: TypeConflict {name}: {prior} vs {type_name} ({evidence})"
            )
        target[name] = type_name
        self._note(state, name, evidence)
        return prior is None

    def _lookup(self, state: _FunctionState, name: str) -> str | None:
        return state.locals.get(name) or state.parameters.get(name) or self.constants.get(name)

    def _record_field(self, owner: str | None, name: str) -> str | None:
        record = self.records.get(owner or "")
        if record is None:
            return None
        return dict(record.fields).get(name)

    def _enum_payload(self, enum_name: str, variant: str) -> str | None:
        enum = self.enums.get(enum_name)
        if enum is None:
            return None
        return dict(enum.variants).get(variant)

    def _expression(self, node: ast.AST, state: _FunctionState, expected: str | None = None) -> str | None:
        if isinstance(node, ast.Constant):
            actual = (
                "Bool" if isinstance(node.value, bool)
                else "UInt64" if isinstance(node.value, int) and node.value >= 0
                else "Int64" if isinstance(node.value, int)
                else "Float64" if isinstance(node.value, float)
                else "Text" if isinstance(node.value, str)
                else None
            )
            if expected and actual and expected != actual:
                if (
                    expected == "Int64"
                    and actual == "UInt64"
                    and isinstance(node.value, int)
                ):
                    return expected
                if (actual, expected) in {("Text", "TextView"), ("Bytes", "BytesView")}:
                    return expected
                raise ConciseApplicationError(
                    f"{self.path}:{node.lineno}: TypeConflict literal {actual} vs {expected}"
                )
            return expected or actual
        if isinstance(node, ast.Name) and node.id == "Unit":
            return "Unit"
        if isinstance(node, ast.Name):
            actual = self._lookup(state, node.id)
            if actual is None and node.id in self.enums:
                actual = node.id
            declared = (
                node.id in state.first_lines
                or node.id in {item.arg for item in state.node.args.args}
                or node.id in self.enums
            )
            if actual is None and not declared:
                raise ConciseApplicationError(
                    f"{self.path}:{node.lineno}: "
                    f"UnresolvedName {node.id!r}"
                )
            if expected and node.id not in self.enums:
                if (actual, expected) in {("Text", "TextView"), ("Bytes", "BytesView")}:
                    return expected
                self._set(
                    state,
                    node.id,
                    expected,
                    node.lineno,
                    "use_constraint",
                )
                return expected
            return actual
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in self.enums:
                if node.attr not in dict(self.enums[node.value.id].variants):
                    raise ConciseApplicationError(
                        f"{self.path}:{node.lineno}: unknown enum variant {node.value.id}.{node.attr}"
                    )
                return node.value.id
            owner = self._expression(node.value, state)
            actual = self._record_field(owner, node.attr)
            if expected and actual and expected != actual:
                raise ConciseApplicationError(
                    f"{self.path}:{node.lineno}: TypeConflict field {actual} vs {expected}"
                )
            return actual or expected
        if isinstance(node, ast.BinOp):
            numeric = (
                expected
                or self._expression(node.left, state)
                or self._expression(node.right, state)
                or "UInt64"
            )
            if numeric not in _NUMERIC_TYPES:
                raise ConciseApplicationError(
                    f"{self.path}:{node.lineno}: numeric operator requires numeric operands, got {numeric}"
                )
            self._expression(node.left, state, numeric)
            self._expression(node.right, state, numeric)
            return numeric
        if isinstance(node, ast.BoolOp):
            for item in node.values:
                self._expression(item, state, "Bool")
            return "Bool"
        if isinstance(node, ast.UnaryOp):
            if (
                isinstance(node.op, ast.USub)
                and isinstance(node.operand, ast.Constant)
                and isinstance(node.operand.value, int)
                and not isinstance(node.operand.value, bool)
            ):
                if expected and expected not in {"Int64", "Float32", "Float64"}:
                    raise ConciseApplicationError(
                        f"{self.path}:{node.lineno}: TypeConflict negative literal Int64 vs {expected}"
                    )
                return expected or "Int64"
            required = "Bool" if isinstance(node.op, ast.Not) else expected
            actual = self._expression(node.operand, state, required)
            if not isinstance(node.op, ast.Not) and actual not in _NUMERIC_TYPES:
                return expected
            return "Bool" if isinstance(node.op, ast.Not) else actual
        if isinstance(node, ast.Compare):
            items = (node.left, *node.comparators)
            known = next((self._expression(item, state) for item in items if self._expression(item, state)), None)
            for item in items:
                self._expression(item, state, known)
            return "Bool"
        if isinstance(node, ast.Call):
            return self._call(node, state, expected)
        if isinstance(node, ast.Subscript):
            owner = self._expression(node.value, state)
            self._expression(node.slice, state, "UInt64")
            if owner and "[" in owner:
                arguments = _generic_arguments(owner)
                return arguments[0] if arguments else expected
            return expected
        return expected

    def _call(self, node: ast.Call, state: _FunctionState, expected: str | None) -> str | None:
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name == "__merlo_try__" and len(node.args) == 1:
                result_type = self._expression(node.args[0], state)
                if result_type and result_type.startswith("Result["):
                    return _generic_arguments(result_type)[0]
                return expected
            if name in _NUMERIC_TYPES:
                if len(node.args) != 1:
                    raise ConciseApplicationError(
                        f"{self.path}:{node.lineno}: {name} cast expects one argument"
                    )
                actual = self._expression(node.args[0], state)
                if actual == "Bool" and name != "Bool":
                    raise ConciseApplicationError(
                        f"{self.path}:{node.lineno}: BoolNumericCastForbidden"
                    )
                return name
            if name not in self.functions and name in {
                "wrapping_add",
                "wrapping_sub",
                "wrapping_mul",
                "checked_add",
                "checked_sub",
                "checked_mul",
            }:
                actual_types = {
                    actual
                    for argument in node.args
                    if (actual := self._expression(argument, state)) is not None
                }
                if (
                    len(node.args) != 2
                    or len(actual_types) != 1
                    or not actual_types <= {"Byte", "Int64", "UInt64"}
                ):
                    raise ConciseApplicationError(
                        f"{self.path}:{node.lineno}: {name} expects two matching "
                        "Byte, Int64, or UInt64 arguments"
                    )
                return next(iter(actual_types))
            if name in self.functions:
                target = self.functions[name]
                for argument, parameter in zip(node.args, target.node.args.args, strict=False):
                    parameter_type = target.parameters.get(parameter.arg)
                    actual = self._expression(argument, state, parameter_type)
                    if actual:
                        self._set(target, parameter.arg, actual, node.lineno, f"call_from_{state.name}")
                if expected and target.return_type == "?":
                    target.return_type = expected
                return expected or (None if target.return_type == "?" else target.return_type)
            if name == "Path":
                if len(node.args) != 1:
                    raise ConciseApplicationError(
                        f"{self.path}:{node.lineno}: Path constructor expects one Text argument"
                    )
                self._expression(node.args[0], state, "Text")
                return "Path"
            if name in self.records:
                record = self.records[name]
                for argument, (_, field_type) in zip(node.args, record.fields, strict=False):
                    self._expression(argument, state, field_type)
                return name
            if name == "Unit":
                return "Unit"
            if name in {"Ok", "Err", "Some", "None"}:
                for argument in node.args:
                    self._expression(argument, state)
                return expected
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver_text = ast.unparse(node.func.value)
            callee = ast.unparse(node.func)
            signature = intrinsic_signature(callee)
            if signature is not None:
                if len(node.args) != signature.arity:
                    raise ConciseApplicationError(
                        f"{self.path}:{node.lineno}: {format_intrinsic_arity(signature, len(node.args))}"
                    )
                for argument, parameter_type in zip(
                    node.args,
                    signature.parameters,
                    strict=True,
                ):
                    self._expression(argument, state, parameter_type)
                contextual_expected = (
                    expected
                    or (
                        state.return_type
                        if state.return_type.startswith("Result[")
                        else None
                    )
                )
                if (
                    callee == "network.tcp_connect"
                    and contextual_expected
                    and contextual_expected.startswith("Result[TcpStream,")
                ):
                    return contextual_expected
                return contextual_result_type(
                    signature.result_type,
                    contextual_expected,
                )
            if receiver_text in {
                "console", "fs", "env", "clock", "random", "network", "tcp",
                "process",
            }:
                raise ConciseApplicationError(
                    f"{self.path}:{node.lineno}: UnknownIntrinsic: {callee}"
                )
            if receiver_text == "Text" and method == "from_bytes":
                return "Text"
            if receiver_text == "TextBuilder" and method == "new":
                return "TextBuilder"
            if receiver_text == "Vec" and method == "new":
                return expected if expected and expected.startswith("Vec[") else None
            if receiver_text == "Map" and method == "new":
                return (
                    expected
                    if _map_types(expected) is not None
                    else _CONCISE_MAP_TYPE
                )
            if receiver_text == "Box" and method == "new":
                payload = self._expression(node.args[0], state) if node.args else None
                return expected or (f"Box[{payload}]" if payload else None)
            receiver_type = self._expression(node.func.value, state)
            if receiver_type == "Path" and method == "to_text":
                return "Text"
            if receiver_type == "FileReader" and method == "lines":
                return "FileLines"
            if receiver_type == "FileReader" and method == "close":
                return "Result[Unit,AppError]"
            if isinstance(node.func.value, ast.Name) and node.func.value.id in self.enums:
                enum_name = node.func.value.id
                payload = self._enum_payload(enum_name, method)
                if payload is not None and node.args:
                    self._expression(node.args[0], state, payload)
                return enum_name
            if receiver_type == "Text" and method == "clone":
                return "Text"
            if receiver_type == "Text" and method == "as_view":
                return "TextView"
            if receiver_type == "TextView":
                if method == "parse_uint64":
                    return "Result[UInt64,UInt64]"
                if method in {"is_ascii", "is_digits", "contains", "contains_ascii_case_insensitive"}:
                    return "Bool"
                if method == "slice_bytes":
                    return "TextView"
                if method == "to_text":
                    return "Text"
            if method in {"len", "capacity", "byte", "tag"}:
                for argument in node.args:
                    self._expression(argument, state, "UInt64")
                return "UInt64"
            if method == "finish":
                return "Text"
            if method == "view":
                if receiver_type == "Bytes":
                    return "BytesView"
                if receiver_type == "Text":
                    return "TextView"
                if receiver_type and receiver_type.startswith("Vec["):
                    return f"Borrow[{receiver_type}]"
            map_types = _map_types(receiver_type)
            if map_types is not None:
                key_type, value_type = map_types
                if method in {"increment", "insert"}:
                    if node.args:
                        self._expression(node.args[0], state, key_type)
                    if method == "insert" and len(node.args) > 1:
                        self._expression(node.args[1], state, value_type)
                    if method == "increment" and value_type != "UInt64":
                        raise ConciseApplicationError(
                            f"{self.path}:{node.lineno}: Map.increment requires UInt64 values"
                        )
                    return "UInt64" if method == "increment" else "Unit"
                if method == "get":
                    if node.args:
                        self._expression(node.args[0], state, key_type)
                    return value_type
                if method == "entries":
                    return f"Borrow[{receiver_type}]"
            if method in {"get", "get_mut"} and receiver_type and "[" in receiver_type:
                arguments = _generic_arguments(receiver_type)
                if arguments:
                    return arguments[0] if len(arguments) == 1 else ",".join(arguments)
            if method == "push" and node.args:
                element = self._expression(node.args[0], state)
                if isinstance(node.func.value, ast.Name) and element:
                    self._set(
                        state,
                        node.func.value.id,
                        f"Vec[{element}]",
                        node.lineno,
                        "vec_push_element",
                    )
                return "Unit"
            if method in {"append_byte", "append_scalar"}:
                for argument in node.args:
                    self._expression(argument, state, "UInt64")
                return "Unit"
        for argument in node.args:
            self._expression(argument, state)
        return expected

    def _count_assignments(self, statements: Iterable[ast.stmt], state: _FunctionState) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                target = statement.target if isinstance(statement, ast.AnnAssign) else statement.targets[0]
                if isinstance(target, ast.Name):
                    state.assignment_counts[target.id] = state.assignment_counts.get(target.id, 0) + 1
                    state.first_lines.setdefault(target.id, statement.lineno)
            elif isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
                state.assignment_counts[statement.target.id] = state.assignment_counts.get(statement.target.id, 0) + 1
            for child in (
                statement.body if isinstance(statement, (ast.If, ast.While, ast.For)) else ()
            ):
                self._count_assignments((child,), state)
            if isinstance(statement, ast.If):
                self._count_assignments(statement.orelse, state)
            if isinstance(statement, ast.Match):
                for case in statement.cases:
                    self._count_assignments(case.body, state)

    def _pattern(
        self,
        pattern: ast.pattern,
        subject_type: str | None,
        state: _FunctionState,
    ) -> None:
        if isinstance(pattern, ast.MatchClass):
            class_name = ast.unparse(pattern.cls)
            payload = None
            variant_name = class_name.rsplit(".", 1)[-1]
            if (
                subject_type
                and subject_type.startswith("Option[")
                and variant_name == "Some"
            ):
                payload = _generic_arguments(subject_type)[0]
            elif (
                subject_type
                and subject_type.startswith("Result[")
                and variant_name in {"Ok", "Err"}
            ):
                arguments = _generic_arguments(subject_type)
                payload = arguments[0 if variant_name == "Ok" else 1]
            else:
                enum_name = class_name.split(".", 1)[0]
                variant = class_name.split(".")[-1]
                payload = self._enum_payload(enum_name, variant)
            for child in pattern.patterns:
                if (
                    isinstance(child, ast.MatchAs)
                    and child.name
                    and payload
                ):
                    self._set(
                        state,
                        child.name,
                        payload,
                        pattern.lineno,
                        "enum_payload",
                    )
        elif (
            isinstance(pattern, ast.MatchAs)
            and pattern.name
            and pattern.name != "_"
            and subject_type
        ):
            self._set(
                state,
                pattern.name,
                subject_type,
                pattern.lineno,
                "match_binding",
            )

    def _statements(self, statements: Iterable[ast.stmt], state: _FunctionState) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                target = statement.target if isinstance(statement, ast.AnnAssign) else statement.targets[0]
                value = statement.value
                explicit = _normalize_type(statement.annotation) if isinstance(statement, ast.AnnAssign) else None
                expected = explicit
                if isinstance(target, ast.Name):
                    expected = expected or self._lookup(state, target.id)
                elif isinstance(target, ast.Attribute):
                    expected = self._expression(target, state)
                if (
                    isinstance(target, ast.Name)
                    and self._lookup(state, target.id) is None
                    and not isinstance(statement, ast.AnnAssign)
                ):
                    raw = self.source.splitlines()[statement.lineno - 1].lstrip()
                    declared = raw.startswith(("let ", "var "))
                    referenced = {
                        item.id
                        for item in ast.walk(value)
                        if isinstance(item, ast.Name)
                        and self._lookup(state, item.id) is not None
                    }
                    used = any(
                        isinstance(item, ast.Name)
                        and isinstance(item.ctx, ast.Load)
                        and item.id == target.id
                        for item in ast.walk(state.node)
                    )
                    if not declared and not used and any(
                        _one_edit_apart(target.id, name)
                        for name in referenced
                    ):
                        raise ConciseApplicationError(
                            f"{self.path}:{statement.lineno}: PossibleTypoSymbol "
                            f"{target.id!r}; use `let` to declare a distinct binding"
                        )
                actual = self._expression(value, state, expected) if value is not None else expected
                if isinstance(target, ast.Name):
                    self._set(state, target.id, actual, statement.lineno, "assignment_value")
            elif isinstance(statement, ast.AugAssign):
                if isinstance(statement.target, ast.Name):
                    current = self._lookup(state, statement.target.id)
                    if current is None:
                        raise ConciseApplicationError(
                            f"{self.path}:{statement.lineno}: UnresolvedMutation {statement.target.id}"
                        )
                    self._expression(statement.value, state, current)
            elif isinstance(statement, ast.Expr):
                self._expression(statement.value, state)
            elif isinstance(statement, ast.Return):
                if statement.value is None:
                    actual = "Unit"
                else:
                    actual = self._expression(
                        statement.value,
                        state,
                        None if state.return_type == "?" else state.return_type,
                    )
                if state.return_type == "?" and actual:
                    state.return_type = actual
                elif actual and state.return_type != actual:
                    raise ConciseApplicationError(
                        f"{self.path}:{statement.lineno}: ReturnTypeConflict {state.return_type} vs {actual}"
                    )
            elif isinstance(statement, ast.If):
                self._expression(statement.test, state, "Bool")
                self._statements(statement.body, state)
                self._statements(statement.orelse, state)
            elif isinstance(statement, ast.While):
                self._expression(statement.test, state, "Bool")
                self._statements(statement.body, state)
            elif isinstance(statement, ast.For):
                iterable_type = self._expression(statement.iter, state)
                target_type = "TextView" if iterable_type == "FileLines" else "UInt64"
                self._set(
                    state,
                    statement.target.id,
                    target_type,
                    statement.lineno,
                    "stream_line_borrow" if iterable_type == "FileLines" else "range_index",
                )
                self._statements(statement.body, state)
            elif isinstance(statement, ast.Match):
                subject = self._expression(statement.subject, state)
                for case in statement.cases:
                    self._pattern(case.pattern, subject, state)
                    if case.guard is not None:
                        self._expression(case.guard, state, "Bool")
                    self._statements(case.body, state)

    def _control_flow_locals(self, state: _FunctionState) -> set[str]:
        locals_: set[str] = set(state.locals)
        for node in ast.walk(state.node):
            target: ast.AST | None = None
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = (
                    node.target
                    if isinstance(node, ast.AnnAssign)
                    else node.targets[0]
                )
            elif isinstance(node, ast.AugAssign):
                target = node.target
            elif isinstance(node, (ast.For, ast.comprehension)):
                target = node.target
            if isinstance(target, ast.Name):
                locals_.add(target.id)
            if isinstance(node, ast.MatchAs) and node.name not in {None, "_"}:
                locals_.add(node.name)
        return locals_

    def _flow_expression(
        self,
        node: ast.AST,
        state: _FunctionState,
        assigned: set[str],
        locals_: set[str],
        initializer_names: frozenset[str] = frozenset(),
        bound: set[str] | None = None,
    ) -> None:
        active = locals_ if bound is None else bound
        known_callables = (
            set(self.functions)
            | set(self.records)
            | set(self.enums)
            | set(_NUMERIC_TYPES)
            | {
                "None",
                "Ok",
                "Err",
                "Some",
                "Path",
                "Unit",
                "Text",
                "Bytes",
                "TextBuilder",
                "Vec",
                "Map",
                "Box",
                "Option",
                "Result",
                "Array",
                "Slice",
                "Borrow",
                "wrapping_add",
                "wrapping_sub",
                "wrapping_mul",
                "checked_add",
                "checked_sub",
                "checked_mul",
            }
        )

        def visit(current: ast.AST) -> None:
            if (
                isinstance(current, ast.Name)
                and isinstance(current.ctx, ast.Load)
                and current.id in active
                and current.id not in assigned
            ):
                raise ConciseApplicationError(
                    f"{self.path}:{current.lineno}: "
                    f"UnresolvedName {current.id!r}; local is not definitely assigned"
                )
            if isinstance(current, ast.Call):
                if isinstance(current.func, ast.Name):
                    local_shadow = (
                        current.func.id in active
                        and (
                            current.func.id not in initializer_names
                            or (
                                bound is not None
                                and current.func.id in bound
                            )
                        )
                    )
                    if current.func.id not in known_callables or local_shadow:
                        visit(current.func)
                else:
                    visit(current.func)
                for argument in current.args:
                    visit(argument)
                for keyword in current.keywords:
                    visit(keyword.value)
                return
            for child in ast.iter_child_nodes(current):
                visit(child)

        visit(node)

    def _flow_pattern(
        self,
        pattern: ast.pattern,
        assigned: set[str],
    ) -> None:
        for child in ast.walk(pattern):
            if isinstance(child, ast.MatchAs) and child.name not in {None, "_"}:
                assigned.add(child.name)
            elif isinstance(child, ast.MatchStar) and child.name not in {None, "_"}:
                assigned.add(child.name)

    @staticmethod
    def _pattern_names(pattern: ast.pattern) -> set[str]:
        return {
            child.name
            for child in ast.walk(pattern)
            if isinstance(child, (ast.MatchAs, ast.MatchStar))
            and child.name not in {None, "_"}
        }
    def _match_is_exhaustive(
        self,
        node: ast.Match,
        state: _FunctionState,
    ) -> bool:
        if any(
            case.guard is None
            and isinstance(case.pattern, ast.MatchAs)
            and case.pattern.pattern is None
            and case.pattern.name is None
            for case in node.cases
        ):
            return True
        subject_type = self._expression(node.subject, state)
        declaration = self.enums.get(subject_type or "")
        if subject_type and subject_type.startswith("Option["):
            expected = {"None", "Some"}
        elif subject_type and subject_type.startswith("Result["):
            expected = {"Ok", "Err"}
        elif declaration is not None:
            expected = {name for name, _ in declaration.variants}
        else:
            return False
        covered: set[str] = set()
        wildcard = False
        for case in node.cases:
            if case.guard is not None:
                continue
            pattern = case.pattern
            if (
                isinstance(pattern, ast.MatchAs)
                and pattern.pattern is None
                and pattern.name is None
            ):
                wildcard = True
                continue
            if isinstance(pattern, ast.MatchSingleton):
                if pattern.value is None:
                    covered.add("None")
            elif isinstance(pattern, ast.MatchValue):
                value = pattern.value
                if isinstance(value, ast.Attribute):
                    covered.add("None" if value.attr == "NoneValue" else value.attr)
            elif isinstance(pattern, ast.MatchClass):
                if isinstance(pattern.cls, ast.Attribute):
                    covered.add(pattern.cls.attr)
                elif isinstance(pattern.cls, ast.Name):
                    covered.add(pattern.cls.id)
        return wildcard or not expected - covered

    def _has_reachable_break(self, statements: Iterable[ast.stmt]) -> bool:
        def visit(node: ast.AST) -> bool:
            if isinstance(node, ast.Break):
                return True
            if isinstance(node, (ast.While, ast.For)):
                return False
            return any(visit(child) for child in ast.iter_child_nodes(node))

        return any(visit(statement) for statement in statements)

    def _flow_statements(
        self,
        statements: Iterable[ast.stmt],
        state: _FunctionState,
        assigned: set[str],
        locals_: set[str],
        bound: set[str],
    ) -> tuple[set[str], set[str], bool]:
        current = set(assigned)
        current_bound = set(bound)
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                target = (
                    statement.target
                    if isinstance(statement, ast.AnnAssign)
                    else statement.targets[0]
                )
                initializer_names = (
                    frozenset({target.id})
                    if value is not None and isinstance(target, ast.Name)
                    else frozenset()
                )
                if value is not None:
                    self._flow_expression(
                        value,
                        state,
                        current,
                        locals_,
                        initializer_names,
                        current_bound,
                    )
                if isinstance(target, ast.Name):
                    current_bound.add(target.id)
                    if value is not None:
                        current.add(target.id)
                else:
                    self._flow_expression(
                        target,
                        state,
                        current,
                        locals_,
                        bound=current_bound,
                    )
            elif isinstance(statement, ast.AugAssign):
                if (
                    isinstance(statement.target, ast.Name)
                    and statement.target.id in locals_
                    and statement.target.id not in current
                ):
                    raise ConciseApplicationError(
                        f"{self.path}:{statement.target.lineno}: "
                        f"UnresolvedName {statement.target.id!r}; "
                        "local is not definitely assigned"
                    )
                if not isinstance(statement.target, ast.Name):
                    self._flow_expression(
                        statement.target,
                        state,
                        current,
                        locals_,
                        bound=current_bound,
                    )
                self._flow_expression(
                    statement.value,
                    state,
                    current,
                    locals_,
                    bound=current_bound,
                )
            elif isinstance(statement, ast.Expr):
                self._flow_expression(
                    statement.value,
                    state,
                    current,
                    locals_,
                    bound=current_bound,
                )
            elif isinstance(statement, ast.Return):
                if statement.value is not None:
                    self._flow_expression(
                        statement.value,
                        state,
                        current,
                        locals_,
                        bound=current_bound,
                    )
                return current, current_bound, False
            elif isinstance(statement, ast.If):
                self._flow_expression(
                    statement.test,
                    state,
                    current,
                    locals_,
                    bound=current_bound,
                )
                literal = (
                    isinstance(statement.test, ast.Constant)
                    and isinstance(statement.test.value, bool)
                )
                if literal:
                    selected = (
                        statement.body
                        if statement.test.value
                        else statement.orelse
                    )
                    selected_after, selected_bound, selected_falls = (
                        self._flow_statements(
                            selected,
                            state,
                            current,
                            locals_,
                            current_bound,
                        )
                    )
                    current_bound.update(selected_bound)
                    if not selected_falls:
                        return set(), current_bound, False
                    current = selected_after
                    continue
                body_after, body_bound, body_falls = self._flow_statements(
                    statement.body,
                    state,
                    current,
                    locals_,
                    current_bound,
                )
                if statement.orelse:
                    else_after, else_bound, else_falls = self._flow_statements(
                        statement.orelse,
                        state,
                        current,
                        locals_,
                        current_bound,
                    )
                else:
                    else_after, else_bound, else_falls = (
                        set(current),
                        set(current_bound),
                        True,
                    )
                current_bound.update(body_bound)
                current_bound.update(else_bound)
                reachable = [
                    branch
                    for branch, falls in (
                        (body_after, body_falls),
                        (else_after, else_falls),
                    )
                    if falls
                ]
                if not reachable:
                    return set(), current_bound, False
                current = set.intersection(*reachable)
            elif isinstance(statement, ast.While):
                self._flow_expression(
                    statement.test,
                    state,
                    current,
                    locals_,
                    bound=current_bound,
                )
                body_after, body_bound, _ = self._flow_statements(
                    statement.body,
                    state,
                    current,
                    locals_,
                    current_bound,
                )
                current_bound.update(body_bound)
                if (
                    isinstance(statement.test, ast.Constant)
                    and statement.test.value is True
                    and not self._has_reachable_break(statement.body)
                ):
                    return body_after, current_bound, False
                current = set(current)
            elif isinstance(statement, ast.For):
                self._flow_expression(
                    statement.iter,
                    state,
                    current,
                    locals_,
                    bound=current_bound,
                )
                body_input = set(current)
                body_bound = set(current_bound)
                if isinstance(statement.target, ast.Name):
                    body_input.add(statement.target.id)
                    body_bound.add(statement.target.id)
                else:
                    self._flow_expression(
                        statement.target,
                        state,
                        body_input,
                        locals_,
                        bound=body_bound,
                    )
                _, body_bound, _ = self._flow_statements(
                    statement.body,
                    state,
                    body_input,
                    locals_,
                    body_bound,
                )
                current_bound.update(body_bound)
                current = set(current)
            elif isinstance(statement, ast.Match):
                self._flow_expression(
                    statement.subject,
                    state,
                    current,
                    locals_,
                    bound=current_bound,
                )
                reachable = (
                    []
                    if self._match_is_exhaustive(statement, state)
                    else [set(current)]
                )
                match_bound = set(current_bound)
                for case in statement.cases:
                    case_input = set(current)
                    case_bound = set(current_bound)
                    pattern_names = self._pattern_names(case.pattern)
                    self._flow_pattern(case.pattern, case_input)
                    case_bound.update(pattern_names)
                    if case.guard is not None:
                        self._flow_expression(
                            case.guard,
                            state,
                            case_input,
                            locals_,
                            bound=case_bound,
                        )
                    case_after, case_bound, case_falls = self._flow_statements(
                        case.body,
                        state,
                        case_input,
                        locals_,
                        case_bound,
                    )
                    match_bound.update(case_bound)
                    if case_falls:
                        reachable.append(case_after)
                current_bound = match_bound
                if not reachable:
                    return set(), current_bound, False
                current = set.intersection(*reachable)
        return current, current_bound, True

    def _validate_control_flow(self, state: _FunctionState) -> None:
        locals_ = self._control_flow_locals(state)
        assigned = {
            parameter.arg
            for parameter in state.node.args.args
        }
        _, _, falls_through = self._flow_statements(
            state.node.body,
            state,
            assigned,
            locals_,
            set(assigned),
        )
        if state.return_type not in {"Unit", "?"} and falls_through:
            raise ConciseApplicationError(
                f"{self.path}:{state.node.lineno}: "
                f"MissingReturn {state.name} may fall through without "
                "returning a value"
            )

    def _finish(self) -> None:
        for state in self.functions.values():
            self._validate_exhaustive_matches(state)
            self._validate_control_flow(state)
            missing_parameters = [
                item.arg for item in state.node.args.args if item.arg not in state.parameters
            ]
            if missing_parameters or state.return_type == "?":
                missing = missing_parameters or ["$return"]
                public = bool(
                    re.search(
                        rf"(?m)^\s*export\s+fn\s+"
                        rf"{re.escape(state.name)}\b",
                        self.source,
                    )
                )
                diagnostic = (
                    "PublicBoundaryAnnotationRequired"
                    if public
                    else "AmbiguousType"
                )
                raise ConciseApplicationError(
                    f"{self.path}:{state.node.lineno}: "
                    f"{diagnostic} {state.name}.{missing[0]}; "
                    "add one constraining boundary annotation"
                )
            for name, line in state.first_lines.items():
                if name not in state.locals and name not in state.parameters:
                    raise ConciseApplicationError(
                        f"{self.path}:{line}: AmbiguousType {state.name}.{name}"
                    )

    def _validate_exhaustive_matches(
        self,
        state: _FunctionState,
    ) -> None:
        for node in ast.walk(state.node):
            if not isinstance(node, ast.Match):
                continue
            subject_type = self._expression(node.subject, state)
            declaration = self.enums.get(subject_type or "")
            if subject_type and subject_type.startswith("Option["):
                expected = {"None", "Some"}
            elif subject_type and subject_type.startswith("Result["):
                expected = {"Ok", "Err"}
            elif declaration is not None:
                expected = {
                    name for name, _ in declaration.variants
                }
            else:
                continue
            covered: set[str] = set()
            wildcard = False
            for case in node.cases:
                pattern = case.pattern
                if (
                    isinstance(pattern, ast.MatchAs)
                    and pattern.pattern is None
                    and pattern.name is None
                ):
                    wildcard = True
                    continue
                if isinstance(pattern, ast.MatchSingleton):
                    if pattern.value is None:
                        covered.add("None")
                elif isinstance(pattern, ast.MatchValue):
                    value = pattern.value
                    if isinstance(value, ast.Attribute):
                        covered.add("None" if value.attr == "NoneValue" else value.attr)
                elif isinstance(pattern, ast.MatchClass):
                    if isinstance(pattern.cls, ast.Attribute):
                        covered.add(pattern.cls.attr)
                    elif isinstance(pattern.cls, ast.Name):
                        covered.add(pattern.cls.id)
            missing = sorted(expected - covered)
            if missing and not wildcard:
                raise ConciseApplicationError(
                    f"{self.path}:{node.lineno}: "
                    f"NonExhaustiveMatch {subject_type}; missing {missing}"
                )

    def decisions(self, origins: dict[int, tuple[str, int]]) -> tuple[InferenceDecision, ...]:
        decisions = []
        for state in self.functions.values():
            for parameter in state.node.args.args:
                path, line = origins.get(parameter.lineno, (self.path, parameter.lineno))
                decisions.append(
                    InferenceDecision(
                        state.name,
                        parameter.arg,
                        "parameter",
                        state.parameters[parameter.arg],
                        False,
                        path,
                        line,
                        tuple(sorted(state.evidence.get(parameter.arg, {"explicit_boundary"}))),
                    )
                )
            path, line = origins.get(state.node.lineno, (self.path, state.node.lineno))
            decisions.append(
                InferenceDecision(
                    state.name,
                    "$return",
                    "return",
                    state.return_type,
                    False,
                    path,
                    line,
                    tuple(sorted(state.evidence.get("$return", {"explicit_boundary"}))),
                )
            )
            for name, type_name in state.locals.items():
                source_line = state.first_lines.get(name, state.node.lineno)
                path, line = origins.get(source_line, (self.path, source_line))
                decisions.append(
                    InferenceDecision(
                        state.name,
                        name,
                        "local",
                        type_name,
                        state.assignment_counts.get(name, 0) > 1,
                        path,
                        line,
                        tuple(sorted(state.evidence.get(name, {"use_constraint"}))),
                    )
                )
        return tuple(sorted(decisions, key=lambda item: (item.owner, item.line, item.kind, item.name)))

    @staticmethod
    def _span(node: ast.AST, path: str) -> SurfaceSourceSpan:
        return SurfaceSourceSpan(
            path,
            int(getattr(node, "lineno", 1)),
            int(getattr(node, "col_offset", 0)) + 1,
            int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
            int(
                getattr(
                    node,
                    "end_col_offset",
                    getattr(node, "col_offset", 0),
                )
            )
            + 1,
        )

    @staticmethod
    def _type_node(type_name: str) -> ast.expr:
        return ast.parse(type_name, mode="eval").body

    def typed_module(
        self,
    ) -> tuple[ast.Module, dict[int, str]]:
        module = copy.deepcopy(self.module)
        binding_kinds: dict[int, str] = {}

        def statements(
            values: list[ast.stmt],
            state: _FunctionState,
        ) -> list[ast.stmt]:
            output: list[ast.stmt] = []
            for statement in values:
                if isinstance(statement, (ast.If, ast.While, ast.For)):
                    statement.body = statements(statement.body, state)
                    statement.orelse = statements(statement.orelse, state)
                elif isinstance(statement, ast.Match):
                    for case in statement.cases:
                        case.body = statements(case.body, state)
                if (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and state.first_lines.get(
                        statement.targets[0].id
                    )
                    == statement.lineno
                ):
                    name = statement.targets[0].id
                    mutable = state.assignment_counts.get(name, 0) > 1
                    binding_kinds[statement.lineno] = (
                        "var" if mutable else "let"
                    )
                    output.append(
                        ast.copy_location(
                            ast.AnnAssign(
                                target=statement.targets[0],
                                annotation=self._type_node(
                                    state.locals[name]
                                ),
                                value=statement.value,
                                simple=1,
                            ),
                            statement,
                        )
                    )
                else:
                    if (
                        isinstance(statement, ast.AnnAssign)
                        and isinstance(statement.target, ast.Name)
                    ):
                        mutable = (
                            state.assignment_counts.get(
                                statement.target.id,
                                0,
                            )
                            > 1
                        )
                        binding_kinds[statement.lineno] = (
                            "var" if mutable else "let"
                        )
                    output.append(statement)
            return output

        for function in (
            item
            for item in module.body
            if isinstance(item, ast.FunctionDef)
        ):
            state = self.functions[function.name]
            for parameter in function.args.args:
                parameter.annotation = self._type_node(
                    state.parameters[parameter.arg]
                )
            function.returns = self._type_node(state.return_type)
            function.body = statements(function.body, state)
        ast.fix_missing_locations(module)
        return module, binding_kinds

    def canonical(self) -> str:
        first_by_line: dict[int, tuple[str, str, bool]] = {}
        for state in self.functions.values():
            for name, line in state.first_lines.items():
                if name in state.locals:
                    first_by_line[line] = (
                        name,
                        state.locals[name],
                        state.assignment_counts.get(name, 0) > 1,
                    )
        output: list[str] = []
        current: _FunctionState | None = None
        for line_number, original in enumerate(
            self.source.splitlines(),
            1,
        ):
            indent = original[: len(original) - len(original.lstrip())]
            line = re.sub(r"^(\s*)export\s+", r"\1", original)
            stripped = line.strip()
            parsed = (
                _declaration(stripped)
                if not indent and stripped
                else None
            )
            if parsed is not None and parsed[2] in self.functions:
                _, _, name, _ = parsed
                current = self.functions[name]
                parameters = ", ".join(
                    f"{item.arg}: {current.parameters[item.arg]}"
                    for item in current.node.args.args
                )
                keyword = (
                    "task"
                    if self.function_effects.get(current.name)
                    else "fn"
                )
                output.append(
                    f"{keyword} {current.name}({parameters}) -> "
                    f"{current.return_type}:"
                )
                effects = self.function_effects.get(current.name, ())
                if effects:
                    output.append(
                        f"    uses {', '.join(effects)}"
                    )
                expression = re.fullmatch(
                    r"(?:(?:fn|task)\s+)?"
                    r"[a-z_][A-Za-z0-9_]*\(.*\)\s*"
                    r"(?:->\s*.+?)?\s*=\s*(.+)",
                    re.sub(r"^export\s+", "", original.strip()),
                )
                if expression is not None:
                    output.append(
                        f"    return {expression.group(1)}"
                    )
                    current = None
                continue
            if parsed is not None and parsed[1] == "record":
                if not re.match(r"(?:record)\s+", stripped):
                    line = f"record {line}"
            if re.fullmatch(r"\s*uses\s+.+", line):
                continue
            if current is not None:
                print_match = re.fullmatch(r"(\s*)print\s+(.+)", line)
                if print_match is not None:
                    line = (
                        f"{print_match.group(1)}console.write("
                        f"{print_match.group(2)})"
                    )
                declared = re.match(
                    r"^(\s*)(let|var)\s+([A-Za-z_][A-Za-z0-9_]*)"
                    r"(?:\s*:\s*[^=]+)?\s*=\s*(.*)$",
                    line,
                )
                if declared is not None:
                    indentation, keyword, name, expression = (
                        declared.groups()
                    )
                    type_name = current.locals.get(name)
                    if type_name is None:
                        raise ConciseApplicationError(
                            f"{self.path}:{line_number}: AmbiguousType "
                            f"{current.name}.{name}"
                        )
                    line = (
                        f"{indentation}{keyword} {name}: {type_name} = "
                        f"{expression}"
                    )
                decision = (
                    None
                    if declared is not None
                    else first_by_line.get(line_number)
                )
                if decision:
                    name, type_name, mutable = decision
                    assignment = re.match(
                        rf"^(\s*)(?:(?:let|var)\s+)?"
                        rf"{re.escape(name)}"
                        r"(?:\s*:\s*[^=]+)?\s*=\s*(.*)$",
                        line,
                    )
                    if assignment is not None:
                        keyword = "var" if mutable else "let"
                        line = (
                            f"{assignment.group(1)}{keyword} {name}: "
                            f"{type_name} = {assignment.group(2)}"
                        )
                if (
                    line_number in self.tail_return_lines
                    and line.strip()
                    and not line.lstrip().startswith("return")
                ):
                    line = (
                        f"{line[:len(line) - len(line.lstrip())]}"
                        f"return {line.lstrip()}"
                    )
            if line and not line[0].isspace():
                current = None
            output.append(line)
        return "\n".join(output).strip() + "\n"

    def canonical_program(self) -> CanonicalProgram:
        canonical_source = self.canonical()
        function_lines: dict[str, tuple[str, ...]] = {}
        active: str | None = None
        body: list[str] = []
        for line in canonical_source.splitlines():
            function = re.match(
                r"^(?:fn|task)\s+([A-Za-z_][A-Za-z0-9_]*)\(",
                line,
            )
            if function is not None:
                if active is not None:
                    function_lines[active] = tuple(body)
                active = function.group(1)
                body = []
                continue
            if active is not None:
                if line and not line.startswith(" "):
                    function_lines[active] = tuple(body)
                    active = None
                    body = []
                elif line.strip() and not line.strip().startswith("uses "):
                    body.append(line[4:] if line.startswith("    ") else line)
        if active is not None:
            function_lines[active] = tuple(body)

        native_module, binding_kinds = self.typed_module()
        exported = {
            parsed[2]
            for line in self.source.splitlines()
            if not line.startswith((" ", "\t"))
            if (parsed := _declaration(line.strip())) is not None
            and parsed[0]
        }
        records = tuple(
            CanonicalRecord(
                name,
                value.fields,
                self._span(
                    next(
                        item
                        for item in self.module.body
                        if isinstance(item, ast.ClassDef)
                        and item.name == name
                    ),
                    self.path,
                ),
                name in exported,
            )
            for name, value in self.records.items()
        )
        enums = tuple(
            CanonicalEnum(
                name,
                value.variants,
                self._span(
                    next(
                        item
                        for item in self.module.body
                        if isinstance(item, ast.ClassDef)
                        and item.name == name
                    ),
                    self.path,
                ),
                name in exported,
            )
            for name, value in self.enums.items()
        )
        functions = []
        for name, state in self.functions.items():
            statements: list[CanonicalBinding | CanonicalReturn] = []
            for statement in state.node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and statement.targets[0].id in state.locals
                ):
                    local = statement.targets[0].id
                    statements.append(
                        CanonicalBinding(
                            local,
                            state.locals[local],
                            state.assignment_counts.get(local, 0) > 1,
                            ast.unparse(statement.value),
                            self._span(statement, self.path),
                        )
                    )
                elif isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target,
                    ast.Name,
                ):
                    local = statement.target.id
                    if statement.value is not None:
                        statements.append(
                            CanonicalBinding(
                                local,
                                state.locals[local],
                                state.assignment_counts.get(local, 0) > 1,
                                ast.unparse(statement.value),
                                self._span(statement, self.path),
                            )
                        )
                elif isinstance(statement, ast.Return):
                    statements.append(
                        CanonicalReturn(
                            (
                                ast.unparse(statement.value)
                                if statement.value is not None
                                else None
                            ),
                            self._span(statement, self.path),
                            (
                                "tail_expression"
                                if statement.lineno
                                in self.tail_return_lines
                                else None
                            ),
                        )
                    )
            result_parts = _generic_arguments(state.return_type)
            errors = (
                (result_parts[1],)
                if state.return_type.startswith("Result[")
                and len(result_parts) == 2
                else ()
            )
            effects = self.function_effects.get(name, ())
            functions.append(
                CanonicalFunction(
                    name,
                    tuple(
                        (
                            parameter.arg,
                            state.parameters[parameter.arg],
                        )
                        for parameter in state.node.args.args
                    ),
                    state.return_type,
                    "task" if effects else "fn",
                    effects,
                    effects,
                    errors,
                    tuple(statements),
                    self._span(state.node, self.path),
                    name in exported,
                    None,
                    (),
                    (),
                    function_lines.get(name, ()),
                )
            )
        return CanonicalProgram(
            records,
            tuple(functions),
            enums,
            native_module,
            tuple(sorted(self.declaration_kinds.items())),
            tuple(sorted(binding_kinds.items())),
            canonical_source,
        )


def _strip_semantic_annotations(source: str) -> str:
    parsed = ast.parse(_preprocess_core(source))

    class Normalize(ast.NodeTransformer):
        def visit_arg(self, node: ast.arg) -> ast.arg:
            node.annotation = None
            return node

        def visit_FunctionDef(
            self,
            node: ast.FunctionDef,
        ) -> ast.FunctionDef:
            self.generic_visit(node)
            node.returns = None
            if node.body and isinstance(node.body[-1], ast.Expr):
                tail = node.body[-1]
                node.body[-1] = ast.copy_location(
                    ast.Return(value=tail.value),
                    tail,
                )
            return node
        
        def visit_Pass(self, node: ast.Pass) -> None:
            return None

        def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
            self.generic_visit(node)
            if node.value is None:
                return node
            return ast.copy_location(
                ast.Assign(targets=[node.target], value=node.value), node
            )

    normalized = Normalize().visit(parsed)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)

def _project_module_calls(
    source: str,
    modules: tuple[_Module, ...],
) -> str:
    """Flatten qualified application calls to the assembled namespace."""
    aliases: dict[str, dict[str, str]] = {}
    for module in modules:
        functions = {
            parsed[2]
            for line in module.body.splitlines()
            if not line.startswith((" ", "\t"))
            if (parsed := _declaration(line.strip())) is not None
        }
        if not functions:
            continue
        for alias in (module.name, module.name.rsplit(".", 1)[-1]):
            aliases[alias] = {
                name: name
                for name in functions
                if all(
                    existing.get(name) == name
                    for existing in aliases.values()
                    if name in existing
                )
            }
    projected = source
    for alias, functions in aliases.items():
        for function in functions:
            projected = re.sub(
                rf"\b{re.escape(alias)}\.{re.escape(function)}\s*\(",
                f"{function}(",
                projected,
            )
    return projected


def _validate_declared_task_effects(
    modules: tuple[_Module, ...],
    inferred_effects: dict[str, tuple[str, ...]],
) -> None:
    for module in modules:
        lines = module.body.splitlines()
        index = 0
        while index < len(lines):
            match = re.fullmatch(
                r"(?:export\s+)?task\s+([A-Za-z_]\w*)\(.*\)\s*->\s*.+:",
                lines[index].strip(),
            )
            if match is None:
                index += 1
                continue
            name = match.group(1)
            source_line = module.body_source_lines[index]
            index += 1
            uses: tuple[str, ...] = ()
            while index < len(lines) and (
                not lines[index].strip()
                or lines[index].startswith((" ", "\t"))
            ):
                if uses_match := re.fullmatch(r"\s*uses\s+(.+)", lines[index]):
                    uses = tuple(
                        sorted(
                            item.strip()
                            for item in uses_match.group(1).split(",")
                        )
                    )
                index += 1
            if not uses:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: "
                    f"MissingEffectDeclaration {name}; task must declare `uses`"
                )
            unknown = set(uses) - _ALLOWED_EFFECTS
            if unknown:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: "
                    f"UnsupportedEffect {sorted(unknown)}"
                )
            missing = set(inferred_effects.get(name, ())) - set(uses)
            if missing:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: MissingCapability {name}: "
                    f"declare {tuple(sorted(missing))} in the task uses list"
                )


def _assemble_core(
    modules: tuple[_Module, ...],
) -> tuple[_CoreAssembly, tuple[TaskBoundary, ...], str]:
    chunks: list[tuple[str, tuple[str, int], tuple[int, ...]]] = []
    exports: list[tuple[str, str, str]] = []
    for module in modules:
        for line in module.body.splitlines():
            if line.startswith((" ", "\t")):
                continue
            parsed = _declaration(line.strip())
            if parsed is None:
                continue
            exported, kind, name, _ = parsed
            if exported:
                exports.append((module.name, name, kind))
        marker = (str(module.path), module.body_source_lines[0])
        if module.body.strip():
            chunks.append(
                (module.body.strip(), marker, module.body_source_lines)
            )
    concise_lines: list[str] = []
    origins: dict[int, tuple[str, int]] = {}
    for source, marker, source_lines in chunks:
        if concise_lines:
            concise_lines.append("")
            origins[len(concise_lines)] = marker
        lines = source.splitlines()
        if len(lines) != len(source_lines):
            raise ConciseApplicationError(
                f"{marker[0]}: internal source-origin projection mismatch"
            )
        for line, source_line in zip(lines, source_lines, strict=True):
            concise_lines.append(line)
            origins[len(concise_lines)] = (marker[0], source_line)
    concise = _project_module_calls(
        "\n".join(concise_lines).strip() + "\n",
        modules,
    )
    inference = _Inference(
        concise,
        path=str(modules[-1].path),
    )
    _validate_declared_task_effects(modules, inference.function_effects)
    canonical_program = inference.canonical_program()
    canonical = canonical_program.to_source()
    public_functions = {
        name for _, name, kind in exports if kind in {"fn", "task"}
    }
    tasks = []
    for name, effects in inference.function_effects.items():
        if not effects:
            continue
        state = inference.functions[name]
        path, line = origins.get(
            state.node.lineno,
            (str(modules[-1].path), state.node.lineno),
        )
        parameters = tuple(
            (parameter.arg, state.parameters[parameter.arg])
            for parameter in state.node.args.args
        )
        if name == "main" and tuple(
            type_name for _, type_name in parameters
        ) not in {("Path",), ("Text",)}:
            raise ConciseApplicationError(
                f"{path}:{line}: CLI main requires exactly one Path or Text parameter"
            )
        tasks.append(
            TaskBoundary(
                name,
                parameters,
                state.return_type,
                effects,
                effects,
                path,
                line,
                name in public_functions,
            )
        )
    origin_items = tuple(
        SourceOrigin(line, path, source_line)
        for line, (path, source_line) in sorted(origins.items())
    )
    assembly = _CoreAssembly(
        concise,
        canonical,
        canonical_program,
        inference.decisions(origins),
        origin_items,
        tuple(exports),
    )
    return assembly, tuple(tasks), ""


def _interfaces(
    assembly: _CoreAssembly,
    tasks: tuple[TaskBoundary, ...],
) -> tuple[PublicInterface, ...]:
    parsed = ast.parse(_preprocess_core(assembly.canonical_source))
    functions = {
        item.name: item for item in parsed.body if isinstance(item, ast.FunctionDef)
    }
    result = []
    task_names = {task.name for task in tasks}
    for module, name, kind in assembly.exports:
        if name in task_names:
            continue
        if kind == "fn":
            node = functions[name]
            result.append(
                PublicInterface(
                    module,
                    name,
                    "fn",
                    tuple(
                        (item.arg, _normalize_type(item.annotation) or "?")
                        for item in node.args.args
                    ),
                    _normalize_type(node.returns),
                    (),
                    (),
                )
            )
        else:
            result.append(PublicInterface(module, name, kind, (), None, (), ()))
    for task in tasks:
        if not task.public:
            continue
        task_path = Path(task.path)
        try:
            first_line = task_path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, UnicodeError, IndexError) as exc:
            raise ConciseApplicationError(
                f"{task_path}: cannot read task module: {exc}"
            ) from exc
        match = re.fullmatch(
            r"module\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
            first_line.strip(),
        )
        if match is None:
            raise ConciseApplicationError(f"{task_path}:1: expected module declaration")
        result.append(
            PublicInterface(
                match.group(1),
                task.name,
                "task",
                task.parameters,
                task.return_type,
                task.effects,
                task.capabilities,
            )
        )
    return tuple(sorted(result, key=lambda item: (item.module, item.kind, item.name)))


def _interface_lock(
    root: Path,
    interfaces: tuple[PublicInterface, ...],
) -> tuple[Path, bool]:
    path = root / ".merlo-interface.json"
    actual = {
        "schema_version": 1,
        "interfaces": [item.to_dict() for item in interfaces],
    }
    if not path.exists():
        return path, False
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConciseApplicationError(
            f"{path}: invalid interface lock: {exc}"
        ) from exc
    return path, expected == actual


def elaborate_concise_core(
    source: str,
    *,
    path: str = "main.mlo",
) -> dict[str, Any]:
    from .surface_elaborator import SurfaceElaborationError, elaborate_surface
    from .surface_parser import SurfaceSyntaxError, parse_surface

    try:
        surface = parse_surface(source, path=path)
        elaborated = elaborate_surface(surface)
    except (SurfaceSyntaxError, SurfaceElaborationError) as exc:
        raise ConciseApplicationError(f"{path}: {exc}") from exc
    canonical = elaborated.canonical.to_source()
    semantic_digest = elaborated.canonical.semantic_hash
    decisions = [
        {
            "owner": item.owner,
            "name": item.name,
            "kind": item.kind,
            "type": item.type_name,
            "mutable": item.mutable,
            "evidence": list(item.evidence),
            "path": path,
            "line": next(
                (
                    declaration.span.start_line
                    for declaration in surface.declarations
                    if getattr(declaration, "name", None) == item.owner
                ),
                1,
            ),
        }
        for item in elaborated.decisions
    ]
    return {
        "canonical_program": elaborated.canonical,
        "canonical_source": canonical,
        "machine_source": lower_concise_sum_types(canonical),
        "decisions": decisions,
        "concise_semantic_digest": semantic_digest,
        "canonical_semantic_digest": semantic_digest,
        "semantic_ast_equal": True,
        "semantic_node_count": sum(
            1
            for declaration in surface.declarations
            for _ in declaration.walk()
        ),
    }


def _machine_source(canonical_core: str) -> tuple[str, bool]:
    executable_core = re.sub(r"(?m)^task(\s+)", r"fn\1", canonical_core)
    return lower_concise_sum_types(executable_core), False


def elaborate_concise_application(
    entry: str | Path,
    *,
    require_interface_lock: bool = True,
) -> ConciseApplicationElaboration:
    entry_path = Path(entry).resolve()
    modules = _load_modules(entry_path)
    assembly, tasks, canonical_tasks = _assemble_core(modules)
    if not tasks:
        raise ConciseApplicationError(f"{entry_path}: application requires an effectful task boundary")
    if len([item for item in tasks if item.name == "main"]) != 1:
        raise ConciseApplicationError(f"{entry_path}: application requires exactly one task main")
    interfaces = _interfaces(assembly, tasks)
    lock_path, lock_valid = _interface_lock(_project_root(entry_path), interfaces)
    if require_interface_lock and not lock_valid:
        raise ConciseApplicationError(
            f"{entry_path}: PublicInterfaceRevisionMismatch; expected lock {lock_path}"
        )
    machine, reference_equal = _machine_source(assembly.canonical_source)
    canonical = assembly.canonical_source
    concise_digest = _strip_semantic_annotations(assembly.concise_source)
    canonical_digest = _strip_semantic_annotations(assembly.canonical_source)
    if concise_digest != canonical_digest:
        raise ConciseApplicationError(
            f"{entry_path}: concise/canonical semantic AST mismatch"
        )
    if _contains_dynamic_any(canonical):
        raise ConciseApplicationError(f"{entry_path}: DynamicAnyForbidden")
    source_payload = "\0".join(item.source for item in modules)
    return ConciseApplicationElaboration(
        str(entry_path),
        tuple(item.name for item in modules),
        hashlib.sha256(source_payload.encode()).hexdigest(),
        canonical,
        assembly.canonical_program,
        machine,
        concise_digest,
        canonical_digest,
        assembly.decisions,
        tasks,
        interfaces,
        assembly.origins,
        str(lock_path),
        lock_valid,
        reference_equal,
    )






def explain_concise_application(entry: str | Path) -> str:
    elaborated = elaborate_concise_application(entry)
    lines = [
        f"modules: {', '.join(elaborated.modules)}",
        f"semantic digest: {elaborated.concise_semantic_digest}",
        "semantic AST preserved: yes",
        "inferred bindings:",
    ]
    for item in elaborated.decisions:
        name = "return" if item.name == "$return" else item.name
        lines.append(
            f"  {item.owner}.{name}: {item.type_name}; kind={item.kind}; "
            f"mutability={'mutable' if item.mutable else 'immutable'}; "
            f"evidence={','.join(item.evidence)}; origin={item.path}:{item.line}"
        )
    lines.extend(
        (
            f"effects: {', '.join(elaborated.effects) or 'none'}",
            f"capabilities: {', '.join(elaborated.capabilities) or 'none'}",
            "implicit argument parsing:",
        )
    )
    if elaborated.argument_parsing:
        for item in elaborated.argument_parsing:
            lines.append(
                f"  {item['name']}: {item['type']} checked -> {item['failure']}"
            )
    else:
        lines.append("  none")
    lines.append("ownership transfers:")
    if elaborated.ownership_transfers:
        lines.extend(f"  {item}" for item in elaborated.ownership_transfers)
    else:
        lines.append("  trivial values only")
    lines.append("public interfaces:")
    for interface in elaborated.interfaces:
        parameters = ", ".join(
            f"{name}: {type_name}" for name, type_name in interface.parameters
        )
        signature = f"{interface.kind} {interface.module}.{interface.name}({parameters})"
        if interface.return_type is not None:
            signature += f" -> {interface.return_type}"
        lines.append(
            f"  {signature}; effects={','.join(interface.effects) or 'none'}; "
            f"capabilities={','.join(interface.capabilities) or 'none'}; "
            f"revision={interface.revision_id}"
        )
    lines.append("ambiguous points: none")
    lines.append(f"interface revision: {elaborated.interface_revision}")
    semantic_nodes = sum(
        1
        for _ in ast.walk(ast.parse(_preprocess_core(elaborated.canonical_source)))
    )
    lines.append(
        f"costs: modules={len(elaborated.modules)} semantic_nodes={semantic_nodes} "
        f"inference_decisions={len(elaborated.decisions)} "
        f"task_boundaries={len(elaborated.tasks)} "
        f"public_interfaces={len(elaborated.interfaces)}"
    )
    return "\n".join(lines) + "\n"


def write_interface_lock(entry: str | Path) -> Path:
    elaborated = elaborate_concise_application(
        entry, require_interface_lock=False
    )
    path = Path(elaborated.interface_lock_path)
    payload = {
        "schema_version": 1,
        "interfaces": [item.to_dict() for item in elaborated.interfaces],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path




__all__ = [
    "CONCISE_APPLICATION_CONTRACT",
    "CONCISE_APPLICATION_SCHEMA_VERSION",
    "ConciseApplicationElaboration",
    "ConciseApplicationError",
    "InferenceDecision",
    "PublicInterface",
    "SourceOrigin",
    "TaskBoundary",
    "elaborate_concise_application",
    "elaborate_concise_core",
    "explain_concise_application",
    "lower_concise_sum_types",
    "write_interface_lock",
]
