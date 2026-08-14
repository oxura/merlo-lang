from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize
from dataclasses import dataclass, replace
from typing import Any

from merlo.canonical_ast import CanonicalProgram
from merlo.concise_effects import _validate_declared_task_effects
from merlo.concise_inference import _Inference, _public_type_name
from merlo.concise_syntax import (
    _NUMERIC_TYPES,
    _preprocess_core,
)
from merlo.frontend_model import (
    ConciseApplicationError,
    InferenceDecision,
    SourceOrigin,
    TaskBoundary,
)
from merlo.module_loader import _Module
from merlo.modules import _declaration















@dataclass(frozen=True)
class _CoreAssembly:
    concise_source: str
    canonical_source: str
    canonical_program: CanonicalProgram
    decisions: tuple[InferenceDecision, ...]
    origins: tuple[SourceOrigin, ...]
    exports: tuple[tuple[str, str, str], ...]
    export_symbols: tuple[tuple[str, str, str, str], ...]
    symbol_names: tuple[tuple[str, str, str], ...]
    task_modules: tuple[tuple[str, str, str], ...]



















def _internal_symbol(module: str, name: str, kind: str) -> str:
    """Give every declaration a stable identity independent of assembly order."""
    readable = re.sub(r"[^A-Za-z0-9_]", "_", module)
    digest = hashlib.sha256(f"{module}\0{name}\0{kind}".encode()).hexdigest()[:12]
    prefix = "Merlo_" if kind in {"record", "enum"} else "__merlo_"
    return f"{prefix}{readable}_{digest}__{name}"


def _module_symbols(
    modules: tuple[_Module, ...],
) -> dict[str, dict[str, tuple[str, bool, str]]]:
    symbols: dict[str, dict[str, tuple[str, bool, str]]] = {}
    for module in modules:
        declarations: dict[str, tuple[str, bool, str]] = {}
        for line in module.body.splitlines():
            if line.startswith((" ", "\t")):
                continue
            parsed = _declaration(line.strip())
            if parsed is None:
                continue
            exported, kind, name, _ = parsed
            if name in declarations:
                raise ConciseApplicationError(
                    f"{module.path}: duplicate declaration {name!r}"
                )
            # Keep the entry module's source names for the native entry contract.
            internal = (
                name
                if module is modules[-1]
                else _internal_symbol(module.name, name, kind)
            )
            declarations[name] = (kind, bool(exported), internal)
        symbols[module.name] = declarations
    return symbols


def _function_local_names_at(source: str, line: int) -> set[str]:
    try:
        parsed = ast.parse(_preprocess_core(source))
    except SyntaxError:
        return set()
    function = next(
        (
            node
            for node in parsed.body
            if isinstance(node, ast.FunctionDef)
            and node.lineno <= line <= getattr(node, "end_lineno", node.lineno)
        ),
        None,
    )
    if function is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.FunctionDef):
            names.update(argument.arg for argument in node.args.args)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For)):
            target = getattr(node, "target", None)
            if isinstance(target, ast.Name):
                names.add(target.id)
        elif isinstance(node, ast.match_case):
            for pattern in ast.walk(node.pattern):
                if isinstance(pattern, ast.MatchAs) and pattern.name:
                    names.add(pattern.name)
    return names



def _token_offset(source: str) -> Any:
    offsets: dict[int, int] = {}
    cursor = 0
    for line_number, line in enumerate(source.splitlines(keepends=True), 1):
        offsets[line_number] = cursor
        cursor += len(line)

    def at(position: tuple[int, int]) -> int:
        return offsets.get(position[0], len(source)) + position[1]

    return at


def _rewrite_module_symbols(
    module: _Module,
    modules: tuple[_Module, ...],
    symbols: dict[str, dict[str, tuple[str, bool, str]]],
) -> str:
    """Rewrite symbol references by token span, retaining comments and spacing."""
    current = symbols[module.name]
    aliases: dict[str, list[str]] = {}
    imported: dict[str, list[str]] = {}
    private_imports: dict[str, list[str]] = {}
    for dependency in module.imports:
        if dependency not in symbols:
            raise ConciseApplicationError(f"{module.path}: UnresolvedImport {dependency}")
        for alias in {dependency, dependency.rsplit(".", 1)[-1]}:
            aliases.setdefault(alias, [])
            if dependency not in aliases[alias]:
                aliases[alias].append(dependency)
        for name, (_, exported, _) in symbols[dependency].items():
            target = imported if exported else private_imports
            target.setdefault(name, []).append(dependency)
    for values in aliases.values():
        values.sort()

    source = module.body
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (IndentationError, tokenize.TokenError) as exc:
        raise ConciseApplicationError(f"{module.path}: cannot tokenize module: {exc}") from exc
    ignored = {
        tokenize.ENCODING, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
        tokenize.DEDENT, tokenize.COMMENT, tokenize.ENDMARKER,
    }
    significant = [index for index, item in enumerate(tokens) if item.type not in ignored]
    at = _token_offset(source)
    replacements: list[tuple[int, int, str]] = []
    consumed: set[int] = set()

    def name(index: int) -> str | None:
        item = tokens[index]
        return item.string if item.type == tokenize.NAME else None

    declaration_indices: set[int] = set()
    for line_number, line in enumerate(source.splitlines(), 1):
        if line.startswith((" ", "\t")):
            continue
        parsed = _declaration(line.strip())
        if parsed is None:
            continue
        declaration_name = parsed[2]
        for index, item in enumerate(tokens):
            if (
                item.type == tokenize.NAME
                and item.start[0] == line_number
                and item.string == declaration_name
            ):
                declaration_indices.add(index)
                break
    for index in declaration_indices:
        item = tokens[index]
        candidate = current.get(item.string)
        if candidate is not None and candidate[2] != item.string:
            replacements.append((at(item.start), at(item.end), candidate[2]))
            consumed.add(index)

    def qualified_target(order: int) -> tuple[str, str, int] | None:
        best: tuple[int, str, str, int] | None = None
        for alias, targets in aliases.items():
            parts = alias.split(".")
            need = len(parts) * 2
            if order + need >= len(significant):
                continue
            if any(
                name(significant[order + part * 2]) != value
                or (
                    part < len(parts) - 1
                    and tokens[significant[order + part * 2 + 1]].string != "."
                )
                for part, value in enumerate(parts)
            ):
                continue
            attr_order = order + need
            if tokens[significant[attr_order - 1]].string != ".":
                continue
            attr = name(significant[attr_order])
            if attr is None:
                continue
            if len(targets) > 1:
                raise ConciseApplicationError(
                    f"{module.path}:{tokens[significant[order]].start[0]}: "
                    f"AmbiguousImport {alias}: {', '.join(targets)}"
                )
            candidate = (len(parts), targets[0], attr, attr_order)
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            return None
        _, target_module, public_name, attr_order = best
        target = symbols[target_module].get(public_name)
        if target is None:
            raise ConciseApplicationError(
                f"{module.path}:{tokens[significant[order]].start[0]}: "
                f"UnresolvedImport {target_module}.{public_name}"
            )
        if not target[1]:
            raise ConciseApplicationError(
                f"{module.path}:{tokens[significant[order]].start[0]}: "
                f"PrivateSymbol {target_module}.{public_name}"
            )
        return target[2], target_module, attr_order

    # Longest module alias wins; replace token text individually so trivia survives.
    for order, index in enumerate(significant):
        if index in consumed or name(index) is None:
            continue
        target = qualified_target(order)
        if target is None:
            continue
        internal, _, attr_order = target
        qualified_indices = significant[order : attr_order + 1]
        for offset, token_index in enumerate(qualified_indices):
            item = tokens[token_index]
            replacements.append(
                (
                    at(item.start),
                    at(item.end),
                    internal if offset == 0 else "",
                )
            )
        consumed.update(qualified_indices)

    intrinsic_receivers = {
        "console", "clock", "env", "fs", "network", "process", "random",
        "Text", "Bytes", "TextBuilder", "Vec", "Map", "Box", "Option", "Result",
    }
    for order, index in enumerate(significant):
        if index in consumed or name(index) is None:
            continue
        if order and tokens[significant[order - 1]].string == ".":
            continue
        public_name = name(index)
        assert public_name is not None
        if public_name in _function_local_names_at(
            source, tokens[index].start[0]
        ):
            continue
        current_target = current.get(public_name)
        candidates = imported.get(public_name, [])
        if current_target is None and candidates:
            if len(candidates) > 1:
                raise ConciseApplicationError(
                    f"{module.path}:{tokens[index].start[0]}: "
                    f"AmbiguousImport {public_name}: {', '.join(sorted(candidates))}"
                )
            current_target = symbols[candidates[0]][public_name]
        intrinsic_calls = {
            "Path", "Unit", "Ok", "Err", "Some", "None", "not", "and", "or",
            "drop", "move", "map", "filter", "fold", "len", "release",
            "wrapping_add", "wrapping_sub", "wrapping_mul", "checked_add",
            "checked_sub", "checked_mul", "__merlo_try__", *_NUMERIC_TYPES,
        }
        next_index = significant[order + 1] if order + 1 < len(significant) else None
        is_call = next_index is not None and tokens[next_index].string == "("
        declaration = (
            order >= 1
            and tokens[significant[order - 1]].string in {"fn", "task", "record", "enum"}
        )
        if current_target is not None:
            kind, _, internal = current_target
            is_symbol_value = kind in {"fn", "task"}
            if (
                declaration
                or kind in {"record", "enum", "const"}
                or is_call
                or is_symbol_value
            ):
                if internal != public_name:
                    replacements.append((at(tokens[index].start), at(tokens[index].end), internal))
                consumed.add(index)
        elif public_name in private_imports:
            dependencies = ", ".join(sorted(private_imports[public_name]))
            raise ConciseApplicationError(
                f"{module.path}:{tokens[index].start[0]}: PrivateSymbol "
                f"{dependencies}.{public_name}"
            )
        elif is_call and public_name not in intrinsic_calls:
            raise ConciseApplicationError(
                f"{module.path}:{tokens[index].start[0]}: UnresolvedName {public_name!r}"
            )

    # A lower-case receiver that is neither local nor intrinsic cannot be a runtime object.
    for order, index in enumerate(significant[:-3]):
        if index in consumed or name(index) is None:
            continue
        if order and tokens[significant[order - 1]].string == ".":
            continue
        receiver = name(index)
        dot, attribute, opening = significant[order + 1 : order + 4]
        if (
            receiver
            and receiver[0].islower()
            and tokens[dot].string == "."
            and name(attribute) is not None
            and tokens[opening].string == "("
            and receiver not in intrinsic_receivers
            and receiver
            not in _function_local_names_at(source, tokens[index].start[0])
        ):
            raise ConciseApplicationError(
                f"{module.path}:{tokens[index].start[0]}: "
                f"UnresolvedImport {receiver}.{name(attribute)}"
            )
    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source



































def _assemble_core(
    modules: tuple[_Module, ...],
) -> tuple[_CoreAssembly, tuple[TaskBoundary, ...], str]:
    symbols = _module_symbols(modules)
    exports: list[tuple[str, str, str]] = []
    export_symbols: list[tuple[str, str, str, str]] = []
    symbol_names: list[tuple[str, str, str]] = []
    chunks: list[tuple[str, tuple[str, int], tuple[int, ...]]] = []
    for module in modules:
        for public_name, (kind, exported, internal) in symbols[module.name].items():
            symbol_names.append((module.name, public_name, internal))
            if exported:
                exports.append((module.name, public_name, kind))
                export_symbols.append((module.name, public_name, kind, internal))
        rewritten = _rewrite_module_symbols(module, modules, symbols)
        marker = (str(module.path), module.body_source_lines[0])
        if rewritten.strip():
            chunks.append((rewritten.strip(), marker, module.body_source_lines))
    concise_lines: list[str] = []
    origins: dict[int, tuple[str, int]] = {}
    for source, marker, source_lines in chunks:
        if concise_lines:
            concise_lines.append("")
            origins[len(concise_lines)] = marker
        lines = source.splitlines()
        if len(lines) != len(source_lines):
            raise ConciseApplicationError(f"{marker[0]}: internal source-origin projection mismatch")
        for line, source_line in zip(lines, source_lines, strict=True):
            concise_lines.append(line)
            origins[len(concise_lines)] = (marker[0], source_line)
    concise = "\n".join(concise_lines).strip() + "\n"
    inference = _Inference(concise, path=str(modules[-1].path))
    _validate_declared_task_effects(modules, inference.function_effects, symbols)
    public_names = {
        internal: f"{module}.{public}"
        for module, public, internal in symbol_names
    }
    source_names = {
        internal: f"{module}.{public}"
        for module, public, internal in symbol_names
    }
    decisions = tuple(
        replace(
            decision,
            owner=source_names.get(decision.owner, decision.owner),
            type_name=_public_type_name(decision.type_name, public_names) or "?",
        )
        for decision in inference.decisions(origins)
    )
    canonical_program = inference.canonical_program()
    canonical = (
        canonical_program.projection_source
        or canonical_program.to_source()
    )
    reverse_symbols = {
        internal: (module, public_name, kind, exported)
        for module, declarations in symbols.items()
        for public_name, (kind, exported, internal) in declarations.items()
    }
    entry_module = modules[-1].name
    tasks: list[TaskBoundary] = []
    task_modules: list[tuple[str, str, str]] = []
    for name, effects in inference.function_effects.items():
        if not effects:
            continue
        state = inference.functions[name]
        module_name, public_name, _, public = reverse_symbols.get(
            name, (entry_module, name, "task", False)
        )
        path, line = origins.get(
            state.node.lineno, (str(modules[-1].path), state.node.lineno)
        )
        internal_parameters = tuple(
            (parameter.arg, state.parameters[parameter.arg])
            for parameter in state.node.args.args
        )
        parameters = tuple(
            (name, _public_type_name(type_name, public_names) or "?")
            for name, type_name in internal_parameters
        )
        if module_name == entry_module and public_name == "main" and tuple(
            type_name for _, type_name in internal_parameters
        ) not in {("Path",), ("Text",)}:
            raise ConciseApplicationError(
                f"{path}:{line}: CLI main requires exactly one Path or Text parameter"
            )
        tasks.append(
            TaskBoundary(
                public_name,
                parameters,
                _public_type_name(state.return_type, public_names) or "?",
                effects,
                effects,
                path,
                line,
                public,
            )
        )
        task_modules.append((public_name, path, module_name))
    origin_items = tuple(
        SourceOrigin(line, path, source_line)
        for line, (path, source_line) in sorted(origins.items())
    )
    assembly = _CoreAssembly(
        concise,
        canonical,
        canonical_program,
        decisions,
        origin_items,
        tuple(exports),
        tuple(export_symbols),
        tuple(symbol_names),
        tuple(task_modules),
    )
    return assembly, tuple(tasks), ""







__all__ = [
    "_CoreAssembly",
    "_assemble_core",
    "_module_symbols",
    "_rewrite_module_symbols",
]
