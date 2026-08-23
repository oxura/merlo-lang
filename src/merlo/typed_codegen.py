"""Ephemeral typed codegen nodes built from validated HIR.

These nodes are not a source artifact and are never serialized.  They preserve
only the expression and statement shape needed by the C emitter after HIR/RIR/
MIR validation has completed.
"""

from __future__ import annotations

import ast as _python_ast
from collections.abc import Iterable, Iterator
from typing import Any


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
        "body",
        "cases",
        "comparators",
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
MatchValue = _define("MatchValue", pattern, ("value",))
MatchSingleton = _define("MatchSingleton", pattern, ("value",))
MatchAs = _define("MatchAs", pattern, ("pattern", "name"))
MatchClass = _define("MatchClass", pattern, ("cls", "patterns", "kwd_attrs", "kwd_patterns"))
match_case = _define("match_case", AST, ("pattern", "guard", "body"))
FunctionDef = _define(
    "FunctionDef",
    stmt,
    ("name", "args", "body", "decorator_list", "returns", "type_comment"),
)
arguments = _define(
    "arguments",
    AST,
    ("posonlyargs", "args", "vararg", "kwonlyargs", "kw_defaults", "kwarg", "defaults"),
)
arg = _define("arg", AST, ("arg", "annotation", "type_comment"))
Module = _define("Module", AST, ("body", "type_ignores"))


_BINARY_SYMBOLS = {
    Add: "+",
    Sub: "-",
    Mult: "*",
    Div: "/",
    FloorDiv: "//",
    Mod: "%",
    BitOr: "|",
    BitAnd: "&",
    BitXor: "^",
    LShift: "<<",
    RShift: ">>",
}
_COMPARE_SYMBOLS = {Eq: "==", NotEq: "!=", Lt: "<", LtE: "<=", Gt: ">", GtE: ">="}
_UNARY_SYMBOLS = {UAdd: "+", USub: "-", Invert: "~", Not: "not "}


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
        for child in iter_child_nodes(current):
            visit(child, current)

    visit(node, None)
    return node


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
    raise TypeError(f"cannot render typed codegen node {type(node).__name__}")


_BINARY_OPERATORS = {name: globals()[name] for name in (
    "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "BitOr", "BitAnd",
    "BitXor", "LShift", "RShift",
)}
_COMPARE_OPERATORS = {name: globals()[name] for name in ("Eq", "NotEq", "Lt", "LtE", "Gt", "GtE")}
_UNARY_OPERATORS = {name: globals()[name] for name in ("UAdd", "USub", "Invert", "Not")}
_BOOLEAN_OPERATORS = {"And": And, "Or": Or}


def _dotted(value: str) -> expr:
    parts = value.split(".")
    result: expr = Name(parts[0], Load())
    for part in parts[1:]:
        result = Attribute(result, part, Load())
    return result


def _annotate(result: AST, source: Any) -> AST:
    span = getattr(source, "source", None)
    if span is not None:
        result.lineno = span.line
        result.col_offset = span.column
        result.end_lineno = span.end_line
        result.end_col_offset = span.end_column
    return result


def _op_node(name: str, table: dict[str, type[AST]], label: str) -> AST:
    try:
        return table[name]()
    except KeyError as exc:
        raise ValueError(f"unsupported typed codegen {label}: {name}") from exc


def _callable_name(expression: str, parameter: str) -> str:
    prefix = f"("  # keep the validation branch explicit below
    del prefix
    open_paren = expression.find("(")
    if open_paren <= 0 or not expression.endswith(")"):
        raise ValueError(f"invalid typed callable expression: {expression}")
    name = expression[:open_paren]
    argument = expression[open_paren + 1 : -1]
    if not name or not all(part.isidentifier() for part in name.split(".")):
        raise ValueError(f"invalid typed callable target: {expression}")
    if argument != parameter:
        raise ValueError(f"typed callable parameter mismatch: {expression}")
    return name

def _implicit_expression(expression: str, parameter: str) -> expr:
    try:
        parsed = _python_ast.parse(expression, mode="eval").body
    except SyntaxError as exc:
        raise ValueError(f"invalid typed implicit expression: {expression}") from exc

    binary_types = {
        _python_ast.Add: Add,
        _python_ast.Sub: Sub,
        _python_ast.Mult: Mult,
        _python_ast.Div: Div,
        _python_ast.FloorDiv: FloorDiv,
        _python_ast.Mod: Mod,
        _python_ast.BitOr: BitOr,
        _python_ast.BitAnd: BitAnd,
        _python_ast.BitXor: BitXor,
        _python_ast.LShift: LShift,
        _python_ast.RShift: RShift,
    }
    compare_types = {
        _python_ast.Eq: Eq,
        _python_ast.NotEq: NotEq,
        _python_ast.Lt: Lt,
        _python_ast.LtE: LtE,
        _python_ast.Gt: Gt,
        _python_ast.GtE: GtE,
    }
    unary_types = {
        _python_ast.UAdd: UAdd,
        _python_ast.USub: USub,
        _python_ast.Invert: Invert,
        _python_ast.Not: Not,
    }

    def convert(node: _python_ast.expr) -> expr:
        if isinstance(node, _python_ast.Name):
            return Name(node.id, Load())
        if isinstance(node, _python_ast.Constant):
            return Constant(node.value, None)
        if isinstance(node, _python_ast.Attribute):
            return Attribute(convert(node.value), node.attr, Load())
        if isinstance(node, _python_ast.BinOp):
            operator = binary_types.get(type(node.op))
            if operator is None:
                raise ValueError(f"unsupported typed implicit binary operator: {expression}")
            return BinOp(convert(node.left), operator(), convert(node.right))
        if isinstance(node, _python_ast.BoolOp):
            operator = {
                _python_ast.And: And,
                _python_ast.Or: Or,
            }.get(type(node.op))
            if operator is None:
                raise ValueError(f"unsupported typed implicit boolean operator: {expression}")
            return BoolOp(operator(), tuple(convert(item) for item in node.values))
        if isinstance(node, _python_ast.UnaryOp):
            operator = unary_types.get(type(node.op))
            if operator is None:
                raise ValueError(f"unsupported typed implicit unary operator: {expression}")
            return UnaryOp(operator(), convert(node.operand))
        if isinstance(node, _python_ast.Compare):
            operators = tuple(compare_types.get(type(item)) for item in node.ops)
            if any(item is None for item in operators):
                raise ValueError(f"unsupported typed implicit comparison: {expression}")
            return Compare(
                convert(node.left),
                tuple(item() for item in operators if item is not None),
                tuple(convert(item) for item in node.comparators),
            )
        if isinstance(node, _python_ast.Call):
            name = _callable_name(_python_ast.unparse(node), parameter)
            return Name(name, Load())
        raise ValueError(f"unsupported typed implicit expression: {expression}")

    return convert(parsed)


def _pattern_binding_names(node: Any, payload_type: str | None) -> tuple[str, ...]:
    if payload_type is None:
        return ()
    declared = {
        str(item.attribute_map["name"])
        for item in node.walk()
        if item.kind in {"LetBinding", "VarBinding"}
        and isinstance(item.attribute_map.get("name"), str)
    }
    names: list[str] = []
    for item in node.walk():
        if item.kind == "Name" and item.type_name == payload_type:
            name = item.attribute_map.get("name")
            if (
                isinstance(name, str)
                and name not in declared
                and name not in names
            ):
                names.append(name)
        callee = item.attribute_map.get("callee")
        method = callee.rsplit(".", 1)[-1] if isinstance(callee, str) else ""
        generic = payload_type.split("[", 1)[0] if "[" in payload_type else ""
        receiver_allowed = (
            (item.kind == "BoxOperation" and generic == "Box" and method == "get")
            or (
                item.kind == "VecOperation"
                and generic == "Vec"
                and method in {"get", "len", "capacity", "view"}
            )
            or (
                item.kind == "MapOperation"
                and generic == "Map"
                and method in {"get", "entries"}
            )
        )
        if receiver_allowed and isinstance(callee, str) and "." in callee:
            receiver = callee.rsplit(".", 1)[0]
            if receiver.isidentifier() and receiver not in names:
                names.append(receiver)
    return tuple(names[-1:])


def _declaration_payload(hir: Any, owner: str, variant: str) -> str | None:
    for declaration in hir.types:
        if declaration.name == owner:
            for item in declaration.variants:
                if item.name == variant:
                    return item.payload_type
    return None


def _case_pattern(hir: Any, subject: Any, case: Any) -> pattern:
    raw = str(case.attribute_map.get("pattern", "_"))
    if case.attribute_map.get("wildcard") or raw == "_":
        return MatchAs(None, None)
    owner: str | None = None
    variant = raw
    if "." in raw:
        owner, variant = raw.rsplit(".", 1)
        cls: expr = Attribute(Name(owner, Load()), variant, Load())
        payload_type = _declaration_payload(hir, owner, variant)
    else:
        cls = Name(variant, Load())
        payload_type = None
        subject_id = getattr(subject, "type_id", None)
        if subject_id is not None:
            try:
                reference = hir.type_context.resolve(subject_id)
            except Exception:
                reference = None
            if reference is not None and reference.constructor in {"Result", "Option"}:
                index = 0 if variant in {"Ok", "Some"} else 1
                if len(reference.arguments) > index:
                    payload_type = hir.type_context.render(reference.arguments[index])
        if payload_type is None:
            subject_type = getattr(subject, "type_name", None)
            if isinstance(subject_type, str):
                payload_type = _declaration_payload(hir, subject_type, variant)
    bindings = _pattern_binding_names(case, payload_type)
    return MatchClass(cls, tuple(MatchAs(None, name) for name in bindings), (), ())


def _convert_expr(hir: Any, node: Any) -> expr:
    kind = node.kind
    attrs = node.attribute_map
    children = node.children
    if kind == "Name":
        return _annotate(Name(str(attrs["name"]), Load()), node)
    if kind == "Literal":
        return _annotate(Constant(attrs.get("value"), None), node)
    if kind == "Binary":
        return _annotate(
            BinOp(
                _convert_expr(hir, children[0]),
                _op_node(str(attrs.get("operator")), _BINARY_OPERATORS, "binary operator"),
                _convert_expr(hir, children[1]),
            ),
            node,
        )
    if kind == "Boolean":
        return _annotate(
            BoolOp(
                _op_node(str(attrs.get("operator")), _BOOLEAN_OPERATORS, "boolean operator"),
                tuple(_convert_expr(hir, item) for item in children),
            ),
            node,
        )
    if kind == "Compare":
        return _annotate(
            Compare(
                _convert_expr(hir, children[0]),
                tuple(
                    _op_node(str(item), _COMPARE_OPERATORS, "comparison operator")
                    for item in attrs.get("operators", ())
                ),
                tuple(_convert_expr(hir, item) for item in children[1:]),
            ),
            node,
        )
    if kind == "Unary":
        return _annotate(
            UnaryOp(
                _op_node(str(attrs.get("operator")), _UNARY_OPERATORS, "unary operator"),
                _convert_expr(hir, children[0]),
            ),
            node,
        )
    if kind in {"FieldAccess", "SetField"}:
        if kind == "SetField":
            return _annotate(_dotted(str(attrs["target"])), node)
        return _annotate(
            Attribute(
                _convert_expr(hir, children[0]),
                str(attrs["field"]),
                Load(),
            ),
            node,
        )
    if kind == "Index":
        return _annotate(
            Subscript(_convert_expr(hir, children[0]), _convert_expr(hir, children[1]), Load()),
            node,
        )
    if kind == "ImplicitCallable":
        expression = str(attrs["expression"])
        result = _implicit_expression(
            expression,
            str(attrs["callable_parameter"]),
        )
        result._merlo_implicit_callable = (
            attrs["callable_id"],
            attrs["callable_parameter"],
            attrs["parameter_type"],
            attrs.get("return_type", attrs.get("callable_return_type", "Bool")),
            expression,
        )
        return _annotate(result, node)
    if kind == "ClosureCreate":
        parameters = tuple(attrs.get("parameters", ()))
        body = attrs.get("closure_body")
        if body is None:
            body_expr: expr = Constant(None, None)
        else:
            body_expr = _convert_expr(hir, body)
        result = Lambda(
            arguments(
                (),
                tuple(arg(name, None, None) for name, _type_name in parameters),
                None,
                (),
                (),
                None,
                (),
            ),
            body_expr,
        )
        result._merlo_closure_metadata = (
            attrs["closure_id"],
            tuple(parameters),
            attrs["return_type"],
            tuple(attrs.get("captures", ())),
            attrs.get("owner"),
        )
        return _annotate(result, node)
    if kind == "ResultPropagation":
        return _annotate(Call(Name("__merlo_try__", Load()), (_convert_expr(hir, children[0]),), ()), node)
    if kind == "CollectionOperation":
        callee = str(attrs["callee"])
        source = _convert_expr(hir, children[0]) if children else _dotted(callee.rsplit(".", 1)[0])
        callable_node = (
            _convert_expr(hir, children[-1])
            if len(children) > 1
            else Name("__merlo_callable", Load())
        )
        metadata = getattr(callable_node, "_merlo_implicit_callable", None)
        if metadata is not None and len(metadata) == 5:
            callable_node._merlo_implicit_callable = (
                metadata[0],
                metadata[1],
                metadata[2],
                attrs.get("callable_return_type", metadata[3]),
                metadata[4],
            )
        return _annotate(
            Call(Attribute(source, callee.rsplit(".", 1)[-1], Load()), (callable_node,), ()),
            node,
        )
    if kind == "ArrayLiteral":
        return _annotate(
            List(tuple(_convert_expr(hir, item) for item in children), Load()),
            node,
        )
    if kind in {
        "RecordConstruct",
        "EnumConstruct",
        "DirectCall",
        "ForeignCall",
        "CallbackCall",
        "BytesTextOperation",
        "VecOperation",
        "BoxOperation",
        "MapOperation",
        "FileOpen",
        "FileRead",
        "FileLines",
        "NumericIntrinsic",
        "ScalarCast",
        "TypedError",
    }:
        callee = attrs.get("callee")
        if callee is None:
            callee = node.type_name or kind
        if (
            kind
            in {
                "BytesTextOperation",
                "VecOperation",
                "BoxOperation",
                "MapOperation",
            }
            and isinstance(callee, str)
            and "." not in callee
            and children
        ):
            function = Attribute(
                _convert_expr(hir, children[0]),
                callee,
                Load(),
            )
            args = tuple(_convert_expr(hir, item) for item in children[1:])
        elif (
            isinstance(callee, str)
            and "." in callee
            and children
            and kind
            in {
                "DirectCall",
                "ForeignCall",
                "BytesTextOperation",
                "VecOperation",
                "BoxOperation",
                "MapOperation",
            }
        ):
            contract_symbol = attrs.get("contract_symbol")
            receiver_type = (
                str(contract_symbol).rsplit(".", 1)[0]
                if isinstance(contract_symbol, str) and "." in contract_symbol
                else None
            )
            consumes_receiver = (
                receiver_type is not None
                and children[0].type_name == receiver_type
            )
            receiver = (
                _convert_expr(hir, children[0])
                if consumes_receiver
                else _dotted(callee.rsplit(".", 1)[0])
            )
            function = Attribute(receiver, callee.rsplit(".", 1)[-1], Load())
            args = tuple(
                _convert_expr(hir, item)
                for item in (children[1:] if consumes_receiver else children)
            )
        else:
            function = _dotted(str(callee))
            args = tuple(_convert_expr(hir, item) for item in children)
        return _annotate(Call(function, args, ()), node)
    if kind == "Expression" and children:
        return _convert_expr(hir, children[0])
    if len(children) == 1:
        return _convert_expr(hir, children[0])
    raise ValueError(f"unsupported typed HIR expression: {kind}")


def _convert_block(hir: Any, node: Any) -> list[stmt]:
    return [_convert_stmt(hir, child) for child in node.children]


def _convert_stmt(hir: Any, node: Any) -> stmt:
    kind = node.kind
    attrs = node.attribute_map
    children = node.children
    if kind in {"LetBinding", "VarBinding"}:
        value = _convert_expr(hir, children[0]) if children else None
        return _annotate(
            AnnAssign(
                Name(str(attrs["name"]), Store()),
                Name(str(node.type_name), Load()),
                value,
                1,
            ),
            node,
        )
    if kind in {"Assign", "SetField"}:
        target = _dotted(str(attrs["target"]))
        value = _convert_expr(hir, children[0])
        return _annotate(Assign((target,), value, None), node)
    if kind == "Expression":
        return _annotate(Expr(_convert_expr(hir, children[0])), node)
    if kind == "Return":
        return _annotate(Return(_convert_expr(hir, children[0]) if children else None), node)
    if kind == "If":
        return _annotate(
            If(
                _convert_expr(hir, children[0]),
                _convert_block(hir, children[1]),
                _convert_block(hir, children[2]) if len(children) > 2 else [],
            ),
            node,
        )
    if kind == "While":
        return _annotate(
            While(_convert_expr(hir, children[0]), _convert_block(hir, children[1]), []),
            node,
        )
    if kind == "For":
        return _annotate(
            For(
                Name(str(attrs["target"]), Store()),
                _convert_expr(hir, children[0]),
                _convert_block(hir, children[1]),
                [],
                None,
            ),
            node,
        )
    if kind == "Match":
        subject = _convert_expr(hir, children[0])
        cases = []
        for case in children[1:]:
            cases.append(
                match_case(
                    _case_pattern(hir, children[0], case),
                    None,
                    _convert_block(hir, case),
                )
            )
        return _annotate(Match(subject, tuple(cases)), node)
    if kind == "DropValue":
        return _annotate(Expr(Call(Name("drop", Load()), (_convert_expr(hir, children[0]),), ())), node)
    if kind == "Break":
        return _annotate(Break(), node)
    if kind == "Continue":
        return _annotate(Continue(), node)
    if kind == "Pass":
        return _annotate(Pass(), node)
    if kind == "UnsafeBlock":
        return _annotate(Expr(_convert_expr(hir, children[0])), node)
    if kind in {"Then", "Else", "LoopBody", "MatchCase"}:
        body = _convert_block(hir, node)
        return body[0] if len(body) == 1 else Expr(Constant(None, None))
    return _annotate(Expr(_convert_expr(hir, node)), node)


def lower_hir_program(hir: Any) -> Module:
    functions: list[FunctionDef] = []
    for function in hir.functions:
        body = [_convert_stmt(hir, node) for node in function.body]
        result = FunctionDef(function.name, None, body, (), None, None)
        result._merlo_ensures = tuple(
            Contract(_convert_expr(hir, contract.condition), contract.kind)
            for contract in function.ensures
        )
        _annotate(result, function)
        functions.append(result)
    return fix_missing_locations(Module(functions, ()))


__all__ = [
    "AST",
    "AnnAssign",
    "Assign",
    "Attribute",
    "BinOp",
    "BoolOp",
    "Break",
    "Call",
    "Compare",
    "Constant",
    "Continue",
    "Contract",
    "Expr",
    "For",
    "FunctionDef",
    "If",
    "Lambda",
    "List",
    "Load",
    "Match",
    "MatchAs",
    "MatchClass",
    "MatchSingleton",
    "MatchValue",
    "Module",
    "Name",
    "Pass",
    "Return",
    "Store",
    "Subscript",
    "Tuple",
    "UnaryOp",
    "While",
    "arg",
    "arguments",
    "fix_missing_locations",
    "lower_hir_program",
    "unparse",
    "walk",
]
