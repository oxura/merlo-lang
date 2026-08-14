"""Structural parsing for Merlo generic type spellings."""

from __future__ import annotations

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
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*|\d+", name):
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


def parse_type_prefix(source: str, start: int = 0) -> tuple[TypeExpr, int]:
    if not isinstance(source, str):
        raise GenericTypeSyntaxError("type must be text")
    parser = _Parser(source)
    parser.index = start
    expression = parser._expression()
    return expression, parser.index


def iter_type_expressions(
    source: str,
    constructors: frozenset[str] = frozenset({"Option", "Result"}),
) -> tuple[tuple[int, int, TypeExpr], ...]:
    found: list[tuple[int, int, TypeExpr]] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        character = source[index]
        if quote is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character == "#":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if character in "\"'":
            quote = character
            index += 1
            continue
        if character.isalpha() or character == "_":
            start = index
            index += 1
            while index < len(source) and (source[index].isalnum() or source[index] == "_"):
                index += 1
            name = source[start:index]
            if name not in constructors:
                continue
            probe = index
            while probe < len(source) and source[probe].isspace():
                probe += 1
            if probe >= len(source) or source[probe] != "[":
                continue
            expression, end = parse_type_prefix(source, start)
            trailing = end
            while trailing < len(source) and source[trailing].isspace():
                trailing += 1
            if trailing < len(source) and (source[trailing].isalnum() or source[trailing] in "_["):
                raise GenericTypeSyntaxError(f"unexpected type text at offset {trailing}")
            found.append((start, end, expression))
            index = start + len(name)
            continue
        index += 1
    return tuple(found)


def split_structural_commas(payload: str) -> tuple[str, ...]:
    if not payload.strip():
        return ()
    parts: list[str] = []
    depth = 0
    start = 0
    for index, character in enumerate(payload):
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                raise GenericTypeSyntaxError(f"unexpected closing bracket at offset {index}")
        elif character == "," and depth == 0:
            item = payload[start:index].strip()
            if not item:
                raise GenericTypeSyntaxError(f"missing argument at offset {index}")
            parts.append(item)
            start = index + 1
    if depth:
        raise GenericTypeSyntaxError("unclosed generic brackets")
    item = payload[start:].strip()
    if not item:
        raise GenericTypeSyntaxError("missing final argument")
    parts.append(item)
    return tuple(parts)


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
    "GenericTypeSyntaxError", "TypeExpr", "generic_arguments", "generic_parts",
    "iter_type_expressions", "parse_type", "parse_type_prefix", "split_structural_commas",
]
