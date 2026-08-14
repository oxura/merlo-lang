"""Structural parsing for Merlo generic type spellings."""

from __future__ import annotations

from dataclasses import dataclass
import re


class GenericTypeSyntaxError(ValueError):
    """A type string is malformed or has unbalanced generic delimiters."""


@dataclass(frozen=True)
class TypeExpr:
    name: str
    args: tuple["TypeExpr", ...] = ()

    @property
    def canonical(self) -> str:
        if not self.args:
            return self.name
        return f"{self.name}[{','.join(item.canonical for item in self.args)}]"


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.index = 0

    def parse(self) -> TypeExpr:
        self._skip_space()
        if self.index == len(self.source):
            raise GenericTypeSyntaxError("empty type")
        result = self._expression()
        self._skip_space()
        if self.index != len(self.source):
            raise GenericTypeSyntaxError(f"unexpected type text at offset {self.index}")
        return result

    def _expression(self) -> TypeExpr:
        self._skip_space()
        start = self.index
        while self.index < len(self.source):
            character = self.source[self.index]
            if character.isspace() or character in "[],":
                break
            self.index += 1
        if start == self.index:
            raise GenericTypeSyntaxError(f"expected type name at offset {self.index}")
        name = self.source[start:self.index]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*|\d+|\?", name):
            raise GenericTypeSyntaxError(f"invalid type atom {name!r}")
        self._skip_space()
        if self.index == len(self.source) or self.source[self.index] != "[":
            return TypeExpr(name)
        self.index += 1
        self._skip_space()
        if self.index == len(self.source) or self.source[self.index] == "]":
            raise GenericTypeSyntaxError(f"empty arguments for {name}")
        arguments: list[TypeExpr] = []
        while True:
            arguments.append(self._expression())
            self._skip_space()
            if self.index == len(self.source):
                raise GenericTypeSyntaxError(f"unclosed arguments for {name}")
            character = self.source[self.index]
            if character == "]":
                self.index += 1
                return TypeExpr(name, tuple(arguments))
            if character != ",":
                raise GenericTypeSyntaxError(f"expected ',' or ']' at offset {self.index}")
            self.index += 1
            self._skip_space()
            if self.index == len(self.source) or self.source[self.index] in ",]":
                raise GenericTypeSyntaxError(f"missing argument for {name} at offset {self.index}")

    def _skip_space(self) -> None:
        while self.index < len(self.source) and self.source[self.index].isspace():
            self.index += 1


def validate_type_expr(expression: TypeExpr) -> TypeExpr:
    """Validate fixed constructor arities recursively."""

    arities = {"Option": 1, "Result": 2, "Vec": 1, "Box": 1, "Map": 2}
    expected = arities.get(expression.name)
    if expected is not None and len(expression.args) != expected:
        raise GenericTypeSyntaxError(
            f"{expression.name} expects {expected} arguments, got {len(expression.args)}"
        )
    if expression.name == "Fn" and len(expression.args) < 2:
        raise GenericTypeSyntaxError("Fn expects at least one parameter and a return type")
    for argument in expression.args:
        validate_type_expr(argument)
    return expression


def parse_type(type_name: str) -> TypeExpr:
    if not isinstance(type_name, str):
        raise GenericTypeSyntaxError("type must be text")
    return _Parser(type_name).parse()


def generic_arguments(type_name: str | TypeExpr) -> tuple[str, ...]:
    parsed = type_name if isinstance(type_name, TypeExpr) else parse_type(type_name)
    if not parsed.args:
        raise GenericTypeSyntaxError(f"type {parsed.name} has no arguments")
    return tuple(item.canonical for item in parsed.args)


def generic_parts(type_name: str | None, constructor: str, *, arity: int | None = None) -> tuple[str, ...] | None:
    if not type_name:
        return None
    try:
        parsed = parse_type(type_name)
    except GenericTypeSyntaxError:
        return None
    if parsed.name != constructor or not parsed.args:
        return None
    if arity is not None and len(parsed.args) != arity:
        return None
    return tuple(item.canonical for item in parsed.args)


__all__ = [
    "GenericTypeSyntaxError",
    "TypeExpr",
    "generic_arguments",
    "generic_parts",
    "parse_type",
    "validate_type_expr",
]
