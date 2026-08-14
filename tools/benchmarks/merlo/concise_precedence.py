from __future__ import annotations

import ast
import hashlib
import json
import random
import re
from dataclasses import dataclass
from typing import Any


PRECEDENCE_TABLE: tuple[tuple[str, int, str], ...] = (
    ("or", 10, "left"),
    ("and", 20, "left"),
    ("comparison", 30, "chain"),
    ("|", 40, "left"),
    ("^", 50, "left"),
    ("&", 60, "left"),
    ("<< >>", 70, "left"),
    ("+ -", 80, "left"),
    ("* / %", 90, "left"),
    ("unary not + - ~", 100, "right"),
    ("call indexing member", 110, "left"),
    ("atom", 120, "none"),
)

_BIN: dict[type[ast.operator], tuple[str, int]] = {
    ast.BitOr: ("|", 40),
    ast.BitXor: ("^", 50),
    ast.BitAnd: ("&", 60),
    ast.LShift: ("<<", 70),
    ast.RShift: (">>", 70),
    ast.Add: ("+", 80),
    ast.Sub: ("-", 80),
    ast.Mult: ("*", 90),
    ast.Div: ("/", 90),
    ast.FloorDiv: ("//", 90),
    ast.Mod: ("%", 90),
}
_COMPARE: dict[type[ast.cmpop], str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}
_UNARY: dict[type[ast.unaryop], str] = {
    ast.Not: "not ",
    ast.UAdd: "+",
    ast.USub: "-",
    ast.Invert: "~",
}


class PrecedenceError(ValueError):
    pass


@dataclass(frozen=True)
class PrecedenceRoundtrip:
    source: str
    canonical: str
    source_digest: str
    canonical_digest: str

    @property
    def equal(self) -> bool:
        return self.source_digest == self.canonical_digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "canonical": self.canonical,
            "source_digest": self.source_digest,
            "canonical_digest": self.canonical_digest,
            "equal": self.equal,
        }


def _python_expression(source: str) -> str:
    return re.sub(r"\bfalse\b", "False", re.sub(r"\btrue\b", "True", source))


def parse_expression(source: str) -> ast.expr:
    try:
        return ast.parse(_python_expression(source), mode="eval").body
    except SyntaxError as exc:
        raise PrecedenceError(f"expression:{exc.lineno}:{exc.offset}: {exc.msg}") from exc


def semantic_ast_digest(node: ast.AST) -> str:
    payload = ast.dump(node, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parenthesize(text: str, child: int, parent: int, *, equal: bool = False) -> str:
    return f"({text})" if child < parent or (equal and child == parent) else text


def _emit(node: ast.AST) -> tuple[str, int]:
    if isinstance(node, ast.Name):
        return node.id, 120
    if isinstance(node, ast.Constant):
        if node.value is True:
            return "true", 120
        if node.value is False:
            return "false", 120
        if node.value is None:
            return "None", 120
        return repr(node.value), 120
    if isinstance(node, ast.Attribute):
        owner, precedence = _emit(node.value)
        return f"{_parenthesize(owner, precedence, 110)}.{node.attr}", 110
    if isinstance(node, ast.Subscript):
        owner, precedence = _emit(node.value)
        index, _ = _emit(node.slice)
        return f"{_parenthesize(owner, precedence, 110)}[{index}]", 110
    if isinstance(node, ast.Call):
        function, precedence = _emit(node.func)
        arguments = [_emit(item)[0] for item in node.args]
        arguments.extend(
            f"{item.arg}={_emit(item.value)[0]}" if item.arg else f"**{_emit(item.value)[0]}"
            for item in node.keywords
        )
        return f"{_parenthesize(function, precedence, 110)}({', '.join(arguments)})", 110
    if isinstance(node, ast.UnaryOp):
        try:
            operator = _UNARY[type(node.op)]
        except KeyError as exc:
            raise PrecedenceError(f"unsupported unary operator {type(node.op).__name__}") from exc
        operand, precedence = _emit(node.operand)
        return f"{operator}{_parenthesize(operand, precedence, 100)}", 100
    if isinstance(node, ast.BinOp):
        try:
            operator, precedence = _BIN[type(node.op)]
        except KeyError as exc:
            raise PrecedenceError(f"unsupported binary operator {type(node.op).__name__}") from exc
        left, left_precedence = _emit(node.left)
        right, right_precedence = _emit(node.right)
        return (
            f"{_parenthesize(left, left_precedence, precedence)} {operator} "
            f"{_parenthesize(right, right_precedence, precedence, equal=True)}",
            precedence,
        )
    if isinstance(node, ast.BoolOp):
        operator = "and" if isinstance(node.op, ast.And) else "or"
        precedence = 20 if isinstance(node.op, ast.And) else 10
        pieces = []
        for value in node.values:
            text, child_precedence = _emit(value)
            pieces.append(_parenthesize(text, child_precedence, precedence))
        return f" {operator} ".join(pieces), precedence
    if isinstance(node, ast.Compare):
        left, left_precedence = _emit(node.left)
        pieces = [_parenthesize(left, left_precedence, 30)]
        for operation, comparator in zip(node.ops, node.comparators, strict=True):
            try:
                operator = _COMPARE[type(operation)]
            except KeyError as exc:
                raise PrecedenceError(
                    f"unsupported comparison {type(operation).__name__}"
                ) from exc
            right, right_precedence = _emit(comparator)
            pieces.extend((operator, _parenthesize(right, right_precedence, 30)))
        return " ".join(pieces), 30
    raise PrecedenceError(f"unsupported expression {type(node).__name__}")


def canonical_expression(source_or_node: str | ast.expr) -> str:
    node = parse_expression(source_or_node) if isinstance(source_or_node, str) else source_or_node
    return _emit(node)[0]


def roundtrip_expression(source: str) -> PrecedenceRoundtrip:
    parsed = parse_expression(source)
    canonical = canonical_expression(parsed)
    reparsed = parse_expression(canonical)
    result = PrecedenceRoundtrip(
        source,
        canonical,
        semantic_ast_digest(parsed),
        semantic_ast_digest(reparsed),
    )
    if not result.equal:
        raise PrecedenceError(
            f"semantic AST changed: {source!r} -> {canonical!r}"
        )
    return result


def generated_precedence_corpus(count: int = 1024, *, seed: int = 0xC011C15E) -> tuple[str, ...]:
    if count < 1000:
        raise ValueError("precedence corpus must contain at least 1000 expressions")
    randomizer = random.Random(seed)
    atoms = ("a", "b", "c", "d", "items[i]", "f(a)", "record.field", "7", "11")
    arithmetic = ("+", "-", "*", "%", "<<", ">>", "&", "^", "|")
    comparisons = ("==", "!=", "<", "<=", ">", ">=")
    result: list[str] = [
        "checksum ^ (value + i)",
        "a + b * c",
        "(a + b) * c",
        "not ready or valid and enabled",
        "items[i + 1] ^ f(a * b)",
        "a < b and c >= d",
        "-(a + b) * ~c",
    ]
    while len(result) < count:
        left, middle, right = randomizer.sample(atoms, 3)
        first = randomizer.choice(arithmetic)
        second = randomizer.choice(arithmetic)
        comparison = randomizer.choice(comparisons)
        mode = len(result) % 6
        if mode == 0:
            expression = f"{left} {first} ({middle} {second} {right})"
        elif mode == 1:
            expression = f"({left} {first} {middle}) {second} {right}"
        elif mode == 2:
            expression = f"{left} {comparison} {middle} and not ({right} == 0)"
        elif mode == 3:
            expression = f"f({left}, {middle} {first} {right})[{left}]"
        elif mode == 4:
            expression = f"~({left} {first} {middle}) {second} {right}"
        else:
            expression = f"{left} {comparison} {middle} or {middle} {comparison} {right}"
        result.append(expression)
    return tuple(result)


def validate_precedence_corpus(count: int = 1024) -> dict[str, Any]:
    expressions = generated_precedence_corpus(count)
    observations = tuple(roundtrip_expression(item) for item in expressions)
    payload = {
        "table": [list(item) for item in PRECEDENCE_TABLE],
        "count": len(observations),
        "all_semantic_ast_equal": all(item.equal for item in observations),
        "corpus_sha256": hashlib.sha256(
            json.dumps(expressions, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    return payload


__all__ = [
    "PRECEDENCE_TABLE",
    "PrecedenceError",
    "PrecedenceRoundtrip",
    "canonical_expression",
    "generated_precedence_corpus",
    "parse_expression",
    "roundtrip_expression",
    "semantic_ast_digest",
    "validate_precedence_corpus",
]
