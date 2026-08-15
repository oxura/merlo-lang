from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Iterator, TypeAlias


@dataclass(frozen=True)
class SourceSpan:
    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class SurfaceNode:
    span: SourceSpan

    def walk(self) -> Iterator[SurfaceNode]:
        yield self
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, SurfaceNode):
                yield from value.walk()
            elif isinstance(value, tuple):
                for item in value:
                    if isinstance(item, SurfaceNode):
                        yield from item.walk()


@dataclass(frozen=True)
class SurfaceName(SurfaceNode):
    name: str

    def __init__(self, name: str, span: SourceSpan) -> None:
        object.__setattr__(self, "span", span)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True)
class SurfaceLiteral(SurfaceNode):
    value: object
    kind: str


@dataclass(frozen=True)
class SurfaceList(SurfaceNode):
    items: tuple[SurfaceExpression, ...]


@dataclass(frozen=True)
class SurfaceMember(SurfaceNode):
    receiver: SurfaceExpression
    field: str


@dataclass(frozen=True)
class SurfaceImplicitReceiver(SurfaceNode):
    field: str


@dataclass(frozen=True)
class SurfaceIndex(SurfaceNode):
    receiver: SurfaceExpression
    index: SurfaceExpression


@dataclass(frozen=True)
class SurfaceCallArgument(SurfaceNode):
    value: SurfaceExpression
    name: str | None = None


@dataclass(frozen=True)
class SurfaceCall(SurfaceNode):
    callee: SurfaceExpression
    arguments: tuple[SurfaceCallArgument, ...]


@dataclass(frozen=True)
class SurfaceUnary(SurfaceNode):
    operator: str
    operand: SurfaceExpression


@dataclass(frozen=True)
class SurfaceBinary(SurfaceNode):
    operator: str
    left: SurfaceExpression
    right: SurfaceExpression

    def __init__(
        self,
        operator: str,
        left: SurfaceExpression,
        right: SurfaceExpression,
        span: SourceSpan,
    ) -> None:
        object.__setattr__(self, "span", span)
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "right", right)


@dataclass(frozen=True)
class SurfaceTry(SurfaceNode):
    expression: SurfaceExpression


@dataclass(frozen=True)
class SurfaceLambda(SurfaceNode):
    parameters: tuple[str, ...]
    body: SurfaceExpression


SurfaceExpression: TypeAlias = (
    SurfaceName
    | SurfaceLiteral
    | SurfaceList
    | SurfaceMember
    | SurfaceImplicitReceiver
    | SurfaceIndex
    | SurfaceCall
    | SurfaceUnary
    | SurfaceBinary
    | SurfaceTry
    | SurfaceLambda
)


@dataclass(frozen=True)
class SurfaceParameter(SurfaceNode):
    name: str
    type_name: str | None


@dataclass(frozen=True)
class SurfaceTypeParameter(SurfaceNode):
    name: str
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurfaceField(SurfaceNode):
    name: str
    type_name: str


@dataclass(frozen=True)
class SurfaceBinding(SurfaceNode):
    name: str
    value: SurfaceExpression
    type_name: str | None = None
    explicit_kind: str | None = None


@dataclass(frozen=True)
class SurfaceAnnotation(SurfaceNode):
    name: str
    type_name: str


@dataclass(frozen=True)
class SurfaceAssignment(SurfaceNode):
    target: SurfaceExpression
    value: SurfaceExpression
    operator: str = "="


@dataclass(frozen=True)
class SurfaceExpressionStatement(SurfaceNode):
    expression: SurfaceExpression


@dataclass(frozen=True)
class SurfaceComment(SurfaceNode):
    text: str


@dataclass(frozen=True)
class SurfaceReturn(SurfaceNode):
    expression: SurfaceExpression | None



@dataclass(frozen=True)
class SurfaceContinue(SurfaceNode):
    pass


@dataclass(frozen=True)
class SurfaceBreak(SurfaceNode):
    pass


@dataclass(frozen=True)
class SurfacePass(SurfaceNode):
    pass


@dataclass(frozen=True)
class SurfaceUses(SurfaceNode):
    effects: tuple[str, ...]


@dataclass(frozen=True)
class SurfacePrint(SurfaceNode):
    expression: SurfaceExpression


@dataclass(frozen=True)
class SurfaceFor(SurfaceNode):
    name: str
    iterable: SurfaceExpression
    body: tuple[SurfaceStatement, ...]


@dataclass(frozen=True)
class SurfaceIf(SurfaceNode):
    condition: SurfaceExpression
    body: tuple[SurfaceStatement, ...]
    otherwise: tuple[SurfaceStatement, ...] = ()


@dataclass(frozen=True)
class SurfaceWhile(SurfaceNode):
    condition: SurfaceExpression
    body: tuple[SurfaceStatement, ...]


@dataclass(frozen=True)
class SurfaceCase(SurfaceNode):
    pattern: str
    body: tuple[SurfaceStatement, ...]
    pattern_span: SourceSpan | None = None


@dataclass(frozen=True)
class SurfaceMatch(SurfaceNode):
    expression: SurfaceExpression
    cases: tuple[SurfaceCase, ...]

SurfaceStatement: TypeAlias = (
    SurfaceBinding
    | SurfaceAnnotation
    | SurfaceAssignment
    | SurfaceComment
    | SurfaceExpressionStatement
    | SurfaceReturn
    | SurfaceContinue
    | SurfaceBreak
    | SurfacePass
    | SurfaceUses
    | SurfacePrint
    | SurfaceFor
    | SurfaceIf
    | SurfaceWhile
    | SurfaceMatch
)


@dataclass(frozen=True)
class SurfaceRecord(SurfaceNode):
    name: str
    fields: tuple[SurfaceField, ...]
    exported: bool = False


@dataclass(frozen=True)
class SurfaceEnumVariant(SurfaceNode):
    name: str
    type_name: str | None


@dataclass(frozen=True)
class SurfaceEnum(SurfaceNode):
    name: str
    variants: tuple[SurfaceEnumVariant, ...]
    exported: bool = False


@dataclass(frozen=True)
class SurfaceFunction(SurfaceNode):
    name: str
    parameters: tuple[SurfaceParameter, ...]
    body: SurfaceExpression | tuple[SurfaceStatement, ...]
    body_kind: str
    exported: bool
    declared_kind: str | None = None
    return_type: str | None = None
    type_parameters: tuple[SurfaceTypeParameter, ...] = ()

    def __init__(
        self,
        name: str,
        parameters: tuple[SurfaceParameter, ...],
        body: SurfaceExpression | tuple[SurfaceStatement, ...],
        body_kind: str,
        exported: bool,
        span: SourceSpan,
        declared_kind: str | None = None,
        return_type: str | None = None,
        type_parameters: tuple[SurfaceTypeParameter, ...] = (),
    ) -> None:
        object.__setattr__(self, "span", span)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "body_kind", body_kind)
        object.__setattr__(self, "exported", exported)
        object.__setattr__(self, "declared_kind", declared_kind)
        object.__setattr__(self, "return_type", return_type)
        object.__setattr__(self, "type_parameters", type_parameters)


SurfaceDeclaration: TypeAlias = SurfaceRecord | SurfaceEnum | SurfaceFunction


@dataclass(frozen=True)
class SurfaceProgram(SurfaceNode):
    declarations: tuple[SurfaceDeclaration, ...]
    module: str | None = None
    imports: tuple[str, ...] = ()
    source: str = ""

__all__ = [name for name in globals() if name.startswith("Source") or name.startswith("Surface")]
