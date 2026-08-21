from __future__ import annotations

from dataclasses import dataclass, replace

from merlo.canonical_ast import CanonicalProgram
from merlo.concise_effects import _validate_declared_task_effects
from merlo.frontend_model import (
    ConciseApplicationError,
    InferenceDecision,
    SourceOrigin,
    TaskBoundary,
)
from merlo.module_loader import _Module
from merlo.surface_ast import SourceSpan, SurfaceProgram
from merlo.surface_binding import (
    bind_module,
    module_symbols,
    parse_modules,
)
from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
from merlo.type_parser import TypeExpr, parse_type
from merlo.type_arena import TypeContextBuilder
















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



















def _public_type_name(type_name: str | None, public_names: dict[str, str]) -> str | None:
    if type_name is None:
        return None

    def project(expression: TypeExpr) -> TypeExpr:
        return TypeExpr(
            public_names.get(expression.name, expression.name),
            tuple(project(argument) for argument in expression.args),
        )

    return project(parse_type(type_name)).canonical


def _surface_program(
    programs: tuple[SurfaceProgram, ...],
    *,
    entry_path: str,
    source: str,
) -> SurfaceProgram:
    declarations = tuple(
        declaration
        for program in programs
        for declaration in program.declarations
    )
    lines = source.splitlines()
    end_line = len(lines) or 1
    end_column = len(lines[-1]) + 1 if lines else 1
    return SurfaceProgram(
        SourceSpan(entry_path, 1, 1, end_line, end_column),
        declarations,
        None,
        (),
        source,
    )


def _assemble_core(
    modules: tuple[_Module, ...],
) -> tuple[_CoreAssembly, tuple[TaskBoundary, ...], str]:
    parsed_programs = parse_modules(modules)
    symbols = module_symbols(modules, parsed_programs)
    exports: list[tuple[str, str, str]] = []
    export_symbols: list[tuple[str, str, str, str]] = []
    symbol_names: list[tuple[str, str, str]] = []
    chunks: list[tuple[str, tuple[str, int], tuple[int, ...]]] = []
    bound_programs: list[SurfaceProgram] = []
    for module, parsed in zip(modules, parsed_programs, strict=True):
        for public_name, (kind, exported, internal) in symbols[module.name].items():
            symbol_names.append((module.name, public_name, internal))
            if exported:
                exports.append((module.name, public_name, kind))
                export_symbols.append((module.name, public_name, kind, internal))
        bound_programs.append(bind_module(module, parsed, symbols))
        if module.body.strip():
            marker = (
                str(module.path),
                module.body_source_lines[0] if module.body_source_lines else 1,
            )
            chunks.append((module.body.strip(), marker, module.body_source_lines))

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
    concise = "\n".join(concise_lines).strip() + "\n"
    program = _surface_program(
        tuple(bound_programs),
        entry_path=str(modules[-1].path),
        source=concise,
    )
    type_context_builder = TypeContextBuilder(allow_unresolved=False)
    try:
        elaborated = elaborate_surface(
            program,
            type_context_builder=type_context_builder,
        )
    except SurfaceElaborationError as exc:
        raise ConciseApplicationError(str(exc)) from exc
    canonical_program = elaborated.canonical
    canonical_program = replace(
        canonical_program,
        records=tuple(
            replace(record, exported=False)
            for record in canonical_program.records
        ),
        functions=tuple(
            replace(function, exported=False)
            for function in canonical_program.functions
        ),
        enums=tuple(
            replace(enum, exported=False)
            for enum in canonical_program.enums
        ),
    )
    canonical = canonical_program.to_source()
    canonical_program = replace(
        canonical_program,
        projection_source=canonical,
    )

    public_names = {
        internal: f"{module}.{public}"
        for module, public, internal in symbol_names
    }
    source_names = dict(public_names)
    functions = {function.name: function for function in canonical_program.functions}
    inferred_effects = {
        function.name: function.effects for function in canonical_program.functions
    }
    _validate_declared_task_effects(program, inferred_effects)
    decisions: list[InferenceDecision] = []
    for decision in elaborated.decisions:
        function = functions.get(decision.owner)
        path = function.span.path if function is not None else str(modules[-1].path)
        line = function.span.start_line if function is not None else 1
        decisions.append(
            InferenceDecision(
                source_names.get(decision.owner, decision.owner),
                decision.name,
                decision.kind,
                _public_type_name(decision.type_name, public_names) or "?",
                decision.mutable,
                path,
                line,
                decision.evidence,
            )
        )

    reverse_symbols = {
        internal: (module, public_name, kind, exported)
        for module, declarations in symbols.items()
        for public_name, (kind, exported, internal) in declarations.items()
    }
    entry_module = modules[-1].name
    tasks: list[TaskBoundary] = []
    task_modules: list[tuple[str, str, str]] = []
    for function in canonical_program.functions:
        if not function.effects:
            continue
        module_name, public_name, _, public = reverse_symbols.get(
            function.name, (entry_module, function.name, function.kind, function.exported)
        )
        path, line = function.span.path, function.span.start_line
        parameters = tuple(
            (name, _public_type_name(type_name, public_names) or "?")
            for name, type_name in function.parameters
        )
        if module_name == entry_module and public_name == "main" and tuple(
            type_name for _, type_name in function.parameters
        ) not in {("Path",), ("Text",)}:
            raise ConciseApplicationError(
                f"{path}:{line}: CLI main requires exactly one Path or Text parameter"
            )
        tasks.append(
            TaskBoundary(
                public_name,
                parameters,
                _public_type_name(function.return_type, public_names) or "?",
                function.effects,
                function.capabilities,
                tuple(
                    item.expression
                    for item in function.requirements
                ),
                tuple(item.expression for item in function.ensures),
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
        tuple(decisions),
        origin_items,
        tuple(exports),
        tuple(export_symbols),
        tuple(symbol_names),
        tuple(task_modules),
    )
    return assembly, tuple(tasks), ""







__all__ = ["_CoreAssembly", "_assemble_core"]
