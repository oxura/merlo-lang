from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from merlo import surface_ast as surface
from merlo.frontend_model import ConciseApplicationError
from merlo.intrinsics import BUILTIN_FUNCTIONS, BUILTIN_RECEIVERS
from merlo.module_loader import _Module
from merlo.surface_parser import SurfaceSyntaxError, parse_surface
from merlo.type_parser import TypeExpr, parse_type

SymbolTable = dict[str, dict[str, tuple[str, bool, str]]]

def internal_symbol(module: str, name: str, kind: str) -> str:
    """Give every declaration a stable identity independent of assembly order."""
    readable = re.sub(r"[^A-Za-z0-9_]", "_", module)
    digest = hashlib.sha256(f"{module}\0{name}\0{kind}".encode()).hexdigest()[:12]
    prefix = "Merlo_" if kind in {"record", "enum"} else "__merlo_"
    return f"{prefix}{readable}_{digest}__{name}"


def parse_modules(modules: tuple[_Module, ...]) -> tuple[surface.SurfaceProgram, ...]:
    programs = []
    for module in modules:
        try:
            programs.append(
                parse_surface(
                    module.body,
                    path=str(module.path),
                    line_offset=(module.body_source_lines[0] - 1 if module.body_source_lines else 0),
                )
            )
        except SurfaceSyntaxError as exc:
            raise ConciseApplicationError(str(exc)) from exc
    return tuple(programs)


def declaration_kind(declaration: surface.SurfaceDeclaration) -> str:
    if isinstance(declaration, surface.SurfaceRecord):
        return "record"
    if isinstance(declaration, surface.SurfaceEnum):
        return "enum"
    return declaration.declared_kind or "fn"


def module_symbols(
    modules: tuple[_Module, ...],
    programs: tuple[surface.SurfaceProgram, ...] | None = None,
) -> SymbolTable:
    parsed_programs = programs if programs is not None else parse_modules(modules)
    symbols: SymbolTable = {}
    for module, program in zip(modules, parsed_programs, strict=True):
        declarations: dict[str, tuple[str, bool, str]] = {}
        for declaration in program.declarations:
            name = declaration.name
            kind = declaration_kind(declaration)
            if name in declarations:
                raise ConciseApplicationError(
                    f"{module.path}:{declaration.span.start_line}: duplicate declaration {name!r}"
                )
            internal = name if module is modules[-1] else internal_symbol(module.name, name, kind)
            declarations[name] = (kind, declaration.exported, internal)
        symbols[module.name] = declarations
    return symbols


def _expression_parts(expression: surface.SurfaceExpression) -> tuple[str, ...] | None:
    if isinstance(expression, surface.SurfaceName):
        return (expression.name,)
    if isinstance(expression, surface.SurfaceMember):
        receiver = _expression_parts(expression.receiver)
        if receiver is not None:
            return (*receiver, expression.field)
    return None


def _payload_name(pattern: str) -> str | None:
    opening = pattern.find("(")
    if opening < 0 or not pattern.endswith(")"):
        return None
    payload = pattern[opening + 1 : -1].strip()
    return payload if payload.isidentifier() and payload != "_" else None


def _function_locals(function: surface.SurfaceFunction) -> frozenset[str]:
    names = {parameter.name for parameter in function.parameters}
    nodes = function.body if isinstance(function.body, tuple) else (function.body,)
    for root in nodes:
        for node in root.walk():
            if isinstance(node, (surface.SurfaceBinding, surface.SurfaceAnnotation, surface.SurfaceFor)):
                names.add(node.name)
            elif isinstance(node, surface.SurfaceCase):
                payload = _payload_name(node.pattern)
                if payload is not None:
                    names.add(payload)
    return frozenset(names)


def bind_module(
    module: _Module,
    program: surface.SurfaceProgram,
    symbols: SymbolTable,
    *,
    reject_unknown_calls: bool = True,
) -> surface.SurfaceProgram:
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
    for targets in aliases.values():
        targets.sort()

    def fail(span: surface.SourceSpan, message: str) -> None:
        raise ConciseApplicationError(f"{span.path}:{span.start_line}: {message}")

    def qualified(name: str, span: surface.SourceSpan) -> str | None:
        if "." not in name:
            return None
        owner, public_name = name.rsplit(".", 1)
        targets = aliases.get(owner)
        if targets is None:
            return None
        if len(targets) != 1:
            fail(span, f"AmbiguousImport {owner}: {', '.join(targets)}")
        target_module = targets[0]
        target = symbols[target_module].get(public_name)
        if target is None:
            fail(span, f"UnresolvedImport {target_module}.{public_name}")
        if not target[1]:
            fail(
                span,
                f"PrivateSymbol: {target_module}.{public_name} is not exported",
            )
        return target[2]

    def unqualified(
        name: str,
        span: surface.SourceSpan,
        locals_: frozenset[str],
        *,
        call: bool = False,
    ) -> str:
        if name in locals_:
            return name
        target = current.get(name)
        candidates = imported.get(name, ())
        if target is None and candidates:
            if len(candidates) != 1:
                fail(span, f"AmbiguousImport {name}: {', '.join(sorted(candidates))}")
            target = symbols[candidates[0]][name]
        if target is not None:
            return target[2]
        if name in private_imports:
            dependencies = ", ".join(sorted(private_imports[name]))
            fail(
                span,
                f"PrivateSymbol: {dependencies}.{name} is not exported",
            )
        if reject_unknown_calls and call and name not in BUILTIN_FUNCTIONS:
            fail(span, f"UnresolvedName {name!r}")
        return name

    def type_name(
        name: str | None,
        span: surface.SourceSpan,
        type_parameters: frozenset[str] = frozenset(),
    ) -> str | None:
        if name is None:
            return None

        def visit(expression: TypeExpr) -> TypeExpr:
            if expression.name in type_parameters:
                renamed = expression.name
            else:
                renamed = qualified(expression.name, span)
                if renamed is None:
                    renamed = unqualified(expression.name, span, frozenset())
            return TypeExpr(renamed, tuple(visit(argument) for argument in expression.args))

        return visit(parse_type(name)).canonical

    def expression(
        node: surface.SurfaceExpression,
        locals_: frozenset[str],
        *,
        call: bool = False,
    ) -> surface.SurfaceExpression:
        if isinstance(node, surface.SurfaceName):
            return surface.SurfaceName(
                unqualified(node.name, node.span, locals_, call=call),
                node.span,
            )
        if isinstance(node, surface.SurfaceLiteral):
            return node
        if isinstance(node, surface.SurfaceList):
            return replace(node, items=tuple(expression(item, locals_) for item in node.items))
        if isinstance(node, surface.SurfaceLambda):
            parameters = frozenset(node.parameters)
            return replace(
                node,
                body=expression(node.body, locals_ | parameters),
            )
        if isinstance(node, surface.SurfaceMember):
            parts = _expression_parts(node)
            if parts is not None:
                renamed = qualified(".".join(parts), node.span)
                if renamed is not None:
                    return surface.SurfaceName(renamed, node.span)
            receiver = expression(node.receiver, locals_)
            if (
                call
                and reject_unknown_calls
                and isinstance(receiver, surface.SurfaceName)
                and receiver.name
                and receiver.name[0].islower()
                and receiver.name not in locals_
                and receiver.name not in BUILTIN_RECEIVERS
                and receiver.name not in current
            ):
                fail(node.span, f"UnresolvedImport {receiver.name}.{node.field}")
            return replace(node, receiver=receiver)
        if isinstance(node, surface.SurfaceImplicitReceiver):
            return node
        if isinstance(node, surface.SurfaceIndex):
            return replace(
                node,
                receiver=expression(node.receiver, locals_),
                index=expression(node.index, locals_),
            )
        if isinstance(node, surface.SurfaceCall):
            return replace(
                node,
                callee=expression(node.callee, locals_, call=True),
                arguments=tuple(
                    replace(argument, value=expression(argument.value, locals_))
                    for argument in node.arguments
                ),
            )
        if isinstance(node, surface.SurfaceUnary):
            return replace(node, operand=expression(node.operand, locals_))
        if isinstance(node, surface.SurfaceBinary):
            return replace(
                node,
                left=expression(node.left, locals_),
                right=expression(node.right, locals_),
            )
        if isinstance(node, surface.SurfaceTry):
            return replace(node, expression=expression(node.expression, locals_))
        raise AssertionError(type(node).__name__)

    def pattern(value: str, span: surface.SourceSpan) -> str:
        opening = value.find("(")
        head = value if opening < 0 else value[:opening]
        suffix = "" if opening < 0 else value[opening:]
        if "." not in head:
            return value
        owner, variant = head.rsplit(".", 1)
        renamed = qualified(owner, span)
        if renamed is None:
            renamed = unqualified(owner, span, frozenset())
        return f"{renamed}.{variant}{suffix}"

    def statements(
        nodes: tuple[surface.SurfaceStatement, ...],
        locals_: frozenset[str],
        type_parameters: frozenset[str] = frozenset(),
    ) -> tuple[surface.SurfaceStatement, ...]:
        result: list[surface.SurfaceStatement] = []
        for node in nodes:
            if isinstance(node, surface.SurfaceBinding):
                result.append(
                    replace(
                        node,
                        value=expression(node.value, locals_),
                        type_name=type_name(
                            node.type_name,
                            node.span,
                            type_parameters,
                        ),
                    )
                )
            elif isinstance(node, surface.SurfaceAnnotation):
                result.append(
                    replace(
                        node,
                        type_name=type_name(
                            node.type_name,
                            node.span,
                            type_parameters,
                        ),
                    )
                )
            elif isinstance(node, surface.SurfaceAssignment):
                result.append(
                    replace(
                        node,
                        target=expression(node.target, locals_),
                        value=expression(node.value, locals_),
                    )
                )
            elif isinstance(node, surface.SurfaceExpressionStatement):
                result.append(replace(node, expression=expression(node.expression, locals_)))
            elif isinstance(node, surface.SurfaceReturn):
                result.append(
                    replace(
                        node,
                        expression=(
                            expression(node.expression, locals_)
                            if node.expression is not None
                            else None
                        ),
                    )
                )
            elif isinstance(node, surface.SurfacePrint):
                result.append(replace(node, expression=expression(node.expression, locals_)))
            elif isinstance(node, surface.SurfaceFor):
                result.append(
                    replace(
                        node,
                        iterable=expression(node.iterable, locals_),
                        body=statements(node.body, locals_, type_parameters),
                    )
                )
            elif isinstance(node, surface.SurfaceIf):
                result.append(
                    replace(
                        node,
                        condition=expression(node.condition, locals_),
                        body=statements(node.body, locals_, type_parameters),
                        otherwise=statements(
                            node.otherwise,
                            locals_,
                            type_parameters,
                        ),
                    )
                )
            elif isinstance(node, surface.SurfaceWhile):
                result.append(
                    replace(
                        node,
                        condition=expression(node.condition, locals_),
                        body=statements(node.body, locals_, type_parameters),
                    )
                )
            elif isinstance(node, surface.SurfaceMatch):
                result.append(
                    replace(
                        node,
                        expression=expression(node.expression, locals_),
                        cases=tuple(
                            replace(
                                case,
                                pattern=pattern(case.pattern, case.pattern_span or case.span),
                                body=statements(
                                    case.body,
                                    locals_,
                                    type_parameters,
                                ),
                            )
                            for case in node.cases
                        ),
                    )
                )
            else:
                result.append(node)
        return tuple(result)

    declarations: list[surface.SurfaceDeclaration] = []
    for declaration in program.declarations:
        internal = current[declaration.name][2]
        if isinstance(declaration, surface.SurfaceRecord):
            declarations.append(
                replace(
                    declaration,
                    name=internal,
                    fields=tuple(
                        replace(field, type_name=type_name(field.type_name, field.span))
                        for field in declaration.fields
                    ),
                )
            )
        elif isinstance(declaration, surface.SurfaceEnum):
            declarations.append(
                replace(
                    declaration,
                    name=internal,
                    variants=tuple(
                        replace(variant, type_name=type_name(variant.type_name, variant.span))
                        for variant in declaration.variants
                    ),
                )
            )
        else:
            locals_ = _function_locals(declaration)
            generic_names = frozenset(
                parameter.name for parameter in declaration.type_parameters
            )
            body = (
                statements(declaration.body, locals_, generic_names)
                if isinstance(declaration.body, tuple)
                else expression(declaration.body, locals_)
            )
            declarations.append(
                replace(
                    declaration,
                    name=internal,
                    parameters=tuple(
                        replace(
                            parameter,
                            type_name=type_name(
                                parameter.type_name,
                                parameter.span,
                                generic_names,
                            ),
                        )
                        for parameter in declaration.parameters
                    ),
                    return_type=type_name(
                        declaration.return_type,
                        declaration.span,
                        generic_names,
                    ),
                    body=body,
                )
            )
    return replace(program, declarations=tuple(declarations))


__all__ = [
    "SymbolTable",
    "bind_module",
    "declaration_kind",
    "internal_symbol",
    "module_symbols",
    "parse_modules",
]
