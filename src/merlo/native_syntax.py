"""Merlo-owned structured syntax nodes used between Surface, HIR, and backends.

The node vocabulary intentionally resembles the small Python-AST subset used by
the original bootstrap compiler.  The representation itself is Merlo-owned:
production Surface lowering does not construct, compile, or walk CPython AST
objects.  ``parse`` exists only for the legacy direct-source test boundary.
"""

from __future__ import annotations

import ast as _python_ast
from typing import Iterator


class AST:
    _fields: tuple[str, ...] = ()

    def __init__(self, *args: object, **kwargs: object) -> None:
        if len(args) > len(self._fields):
            raise TypeError(
                f"{type(self).__name__} expects at most {len(self._fields)} arguments"
            )
        for name, value in zip(self._fields, args, strict=False):
            setattr(self, name, value)
        for name in self._fields[len(args) :]:
            setattr(self, name, kwargs.pop(name, [] if name in _LIST_FIELDS else None))
        for name, value in kwargs.items():
            setattr(self, name, value)


class expr(AST):
    pass


class stmt(AST):
    pass


class pattern(AST):
    pass


class operator(AST):
    pass


class unaryop(AST):
    pass


class boolop(AST):
    pass


class cmpop(AST):
    pass


class expr_context(AST):
    pass


_LIST_FIELDS = frozenset(
    {
        "args",
        "bases",
        "body",
        "cases",
        "comparators",
        "decorator_list",
        "defaults",
        "elts",
        "keywords",
        "kw_defaults",
        "kwd_attrs",
        "kwd_patterns",
        "kwonlyargs",
        "ops",
        "orelse",
        "patterns",
        "posonlyargs",
        "targets",
        "type_ignores",
        "values",
    }
)


def _define(name: str, base: type[AST], fields: tuple[str, ...] = ()) -> type[AST]:
    result = type(name, (base,), {"_fields": fields})
    globals()[name] = result
    return result


Load = _define("Load", expr_context)
Store = _define("Store", expr_context)
Add = _define("Add", operator)
Sub = _define("Sub", operator)
Mult = _define("Mult", operator)
Div = _define("Div", operator)
FloorDiv = _define("FloorDiv", operator)
Mod = _define("Mod", operator)
BitOr = _define("BitOr", operator)
BitAnd = _define("BitAnd", operator)
BitXor = _define("BitXor", operator)
LShift = _define("LShift", operator)
RShift = _define("RShift", operator)
UAdd = _define("UAdd", unaryop)
USub = _define("USub", unaryop)
Invert = _define("Invert", unaryop)
Not = _define("Not", unaryop)
And = _define("And", boolop)
Or = _define("Or", boolop)
Eq = _define("Eq", cmpop)
NotEq = _define("NotEq", cmpop)
Lt = _define("Lt", cmpop)
LtE = _define("LtE", cmpop)
Gt = _define("Gt", cmpop)
GtE = _define("GtE", cmpop)

Name = _define("Name", expr, ("id", "ctx"))
Constant = _define("Constant", expr, ("value", "kind"))
Attribute = _define("Attribute", expr, ("value", "attr", "ctx"))
Subscript = _define("Subscript", expr, ("value", "slice", "ctx"))
List = _define("List", expr, ("elts", "ctx"))
Tuple = _define("Tuple", expr, ("elts", "ctx"))
UnaryOp = _define("UnaryOp", expr, ("op", "operand"))
BinOp = _define("BinOp", expr, ("left", "op", "right"))
BoolOp = _define("BoolOp", expr, ("op", "values"))
Compare = _define("Compare", expr, ("left", "ops", "comparators"))
Call = _define("Call", expr, ("func", "args", "keywords"))
Lambda = _define("Lambda", expr, ("args", "body"))
Expr = _define("Expr", stmt, ("value",))
Return = _define("Return", stmt, ("value",))
Contract = _define("Contract", stmt, ("condition", "kind"))
Assign = _define("Assign", stmt, ("targets", "value", "type_comment"))
AnnAssign = _define("AnnAssign", stmt, ("target", "annotation", "value", "simple"))
AugAssign = _define("AugAssign", stmt, ("target", "op", "value"))
If = _define("If", stmt, ("test", "body", "orelse"))
While = _define("While", stmt, ("test", "body", "orelse"))
For = _define("For", stmt, ("target", "iter", "body", "orelse", "type_comment"))
Break = _define("Break", stmt)
Continue = _define("Continue", stmt)
Pass = _define("Pass", stmt)
Match = _define("Match", stmt, ("subject", "cases"))
ClassDef = _define("ClassDef", stmt, ("name", "bases", "keywords", "body", "decorator_list"))
FunctionDef = _define(
    "FunctionDef",
    stmt,
    ("name", "args", "body", "decorator_list", "returns", "type_comment"),
)
MatchValue = _define("MatchValue", pattern, ("value",))
MatchSingleton = _define("MatchSingleton", pattern, ("value",))
MatchAs = _define("MatchAs", pattern, ("pattern", "name"))
MatchClass = _define("MatchClass", pattern, ("cls", "patterns", "kwd_attrs", "kwd_patterns"))
match_case = _define("match_case", AST, ("pattern", "guard", "body"))
arg = _define("arg", AST, ("arg", "annotation", "type_comment"))
arguments = _define(
    "arguments",
    AST,
    ("posonlyargs", "args", "vararg", "kwonlyargs", "kw_defaults", "kwarg", "defaults"),
)
keyword = _define("keyword", AST, ("arg", "value"))
Module = _define("Module", AST, ("body", "type_ignores"))


def iter_child_nodes(node: AST) -> Iterator[AST]:
    for field in node._fields:
        value = getattr(node, field, None)
        if isinstance(value, AST):
            yield value
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, AST):
                    yield item


def walk(node: AST) -> Iterator[AST]:
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(tuple(iter_child_nodes(current))))


def fix_missing_locations(node: AST) -> AST:
    def visit(current: AST, parent: AST | None) -> None:
        if parent is not None:
            for name, default in (
                ("lineno", 1),
                ("col_offset", 0),
                ("end_lineno", getattr(parent, "lineno", 1)),
                ("end_col_offset", getattr(parent, "col_offset", 0)),
            ):
                if not hasattr(current, name):
                    setattr(current, name, getattr(parent, name, default))
            if not hasattr(current, "_merlo_path") and hasattr(parent, "_merlo_path"):
                current._merlo_path = parent._merlo_path
        for child in iter_child_nodes(current):
            visit(child, current)

    visit(node, None)
    return node


_BINARY_SYMBOLS = {
    Add: "+", Sub: "-", Mult: "*", Div: "/", FloorDiv: "//", Mod: "%",
    BitOr: "|", BitAnd: "&", BitXor: "^", LShift: "<<", RShift: ">>",
}
_COMPARE_SYMBOLS = {Eq: "==", NotEq: "!=", Lt: "<", LtE: "<=", Gt: ">", GtE: ">="}
_UNARY_SYMBOLS = {UAdd: "+", USub: "-", Invert: "~", Not: "not "}


def unparse(node: AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, Name):
        return node.id
    if isinstance(node, Constant):
        return repr(node.value)
    if isinstance(node, Attribute):
        return f"{unparse(node.value)}.{node.attr}"
    if isinstance(node, Subscript):
        arguments = (
            ", ".join(unparse(item) for item in node.slice.elts)
            if isinstance(node.slice, Tuple)
            else unparse(node.slice)
        )
        return f"{unparse(node.value)}[{arguments}]"
    if isinstance(node, (List, Tuple)):
        opening, closing = ("[", "]") if isinstance(node, List) else ("(", ")")
        content = ", ".join(unparse(item) for item in node.elts)
        if isinstance(node, Tuple) and len(node.elts) == 1:
            content += ","
        return opening + content + closing
    if isinstance(node, UnaryOp):
        return _UNARY_SYMBOLS[type(node.op)] + unparse(node.operand)
    if isinstance(node, BinOp):
        return f"{unparse(node.left)} {_BINARY_SYMBOLS[type(node.op)]} {unparse(node.right)}"
    if isinstance(node, BoolOp):
        symbol = " and " if isinstance(node.op, And) else " or "
        return symbol.join(unparse(item) for item in node.values)
    if isinstance(node, Compare):
        result = unparse(node.left)
        for operation, comparator in zip(node.ops, node.comparators, strict=True):
            result += f" {_COMPARE_SYMBOLS[type(operation)]} {unparse(comparator)}"
        return result
    if isinstance(node, Call):
        return f"{unparse(node.func)}({', '.join(unparse(item) for item in node.args)})"
    if isinstance(node, MatchValue):
        return unparse(node.value)
    if isinstance(node, MatchSingleton):
        return repr(node.value)
    if isinstance(node, MatchAs):
        return node.name or "_"
    if isinstance(node, MatchClass):
        return f"{unparse(node.cls)}({', '.join(unparse(item) for item in node.patterns)})"
    if isinstance(node, Expr):
        return unparse(node.value)
    if isinstance(node, Return):
        return "return" + (f" {unparse(node.value)}" if node.value is not None else "")
    if isinstance(node, Contract):
        return f"{node.kind} {unparse(node.condition)}"
    if isinstance(node, Assign):
        return f"{unparse(node.targets[0])} = {unparse(node.value)}"
    if isinstance(node, AnnAssign):
        value = f" = {unparse(node.value)}" if node.value is not None else ""
        return f"{unparse(node.target)}: {unparse(node.annotation)}{value}"
    if isinstance(node, For):
        return f"for {unparse(node.target)} in {unparse(node.iter)}"
    raise TypeError(f"cannot render Merlo syntax node {type(node).__name__}")


def _from_python(node: object) -> object:
    if isinstance(node, list):
        return [_from_python(item) for item in node]
    if not isinstance(node, _python_ast.AST):
        return node
    node_type = globals().get(type(node).__name__)
    if not isinstance(node_type, type) or not issubclass(node_type, AST):
        raise TypeError(f"legacy syntax node {type(node).__name__} is unsupported")
    values = {
        field: _from_python(getattr(node, field, None))
        for field in getattr(node, "_fields", ())
        if field in node_type._fields
    }
    result = node_type(**values)
    for name in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
        if hasattr(node, name):
            setattr(result, name, getattr(node, name))
    return result


def parse(source: str, *, filename: str = "<unknown>") -> Module:
    """Parse only the legacy canonical-source boundary into Merlo-owned nodes."""
    result = _from_python(_python_ast.parse(source, filename=filename))
    if not isinstance(result, Module):
        raise TypeError("legacy parser did not produce a module")
    for node in walk(result):
        node._merlo_path = filename
    return result


def validate_module(module: Module) -> None:
    if not isinstance(module, Module):
        raise TypeError("expected Merlo native Module")
    for node in walk(module):
        if not isinstance(node, AST):
            raise TypeError(f"invalid native syntax child {type(node).__name__}")


__all__ = [
    "AST", "expr", "stmt", "pattern", "operator", "unaryop", "boolop",
    "cmpop", "expr_context", "fix_missing_locations", "iter_child_nodes",
    "parse", "unparse", "validate_module", "walk",
] + [name for name, value in globals().items() if isinstance(value, type) and issubclass(value, AST)]
