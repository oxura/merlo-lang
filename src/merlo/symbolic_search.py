from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from merlo.frontend.lexer import ExpressionToken, ExpressionLexError, lex_expression
from merlo.refactor import preview_fill_hole
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError
from merlo.synthesis import (
    CandidateRank,
    SynthesisCandidate,
    SynthesisRequest,
    build_synthesis_candidate,
)
from merlo.surface_parser import SurfaceSyntaxError, parse_surface


SYMBOLIC_PRODUCER_REVISION = "symbolic/v1"


def _request(value: SynthesisRequest | Mapping[str, Any]) -> SynthesisRequest:
    if isinstance(value, SynthesisRequest):
        return value
    if isinstance(value, Mapping):
        return SynthesisRequest.from_dict(value)
    raise WorldError("SynthesisRequestSchemaMismatch")


def _significant(tokens: tuple[ExpressionToken, ...]) -> tuple[ExpressionToken, ...]:
    return tuple(token for token in tokens if token.kind != "eof")


def _matching_paren(tokens: tuple[ExpressionToken, ...], index: int) -> int | None:
    if index >= len(tokens) or tokens[index].text != "(":
        return None
    depth = 0
    for position in range(index, len(tokens)):
        text = tokens[position].text
        if text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
            if depth == 0:
                return position
            if depth < 0:
                return None
    return None


def _outer_parens(tokens: tuple[ExpressionToken, ...]) -> tuple[ExpressionToken, ...]:
    while len(tokens) >= 2 and tokens[0].text == ")":
        return tokens
    while len(tokens) >= 2 and tokens[0].text == "(":
        closing = _matching_paren(tokens, 0)
        if closing != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return tokens


def _projection(expression: str) -> tuple[str, int] | None:
    """Return the non-result side of one top-level equality.

    Lexer spans, rather than text splitting, keep equality operators in strings,
    calls, and nested expressions out of the projection boundary.
    """
    try:
        tokens = _significant(lex_expression(expression))
    except ExpressionLexError:
        return None
    tokens = _outer_parens(tokens)
    if not tokens:
        return None
    depth = 0
    equalities: list[int] = []
    for index, token in enumerate(tokens):
        if token.text in {"(", "[", "{"}:
            depth += 1
        elif token.text in {")",
            "]",
            "}",
        }:
            depth -= 1
            if depth < 0:
                return None
        elif token.text == "==" and depth == 0:
            equalities.append(index)
    if depth != 0 or len(equalities) != 1:
        return None
    equality = equalities[0]
    left = _outer_parens(tokens[:equality])
    right = _outer_parens(tokens[equality + 1 :])
    if len(left) == 1 and left[0].kind == "identifier" and left[0].text == "result":
        candidate = right
    elif len(right) == 1 and right[0].kind == "identifier" and right[0].text == "result":
        candidate = left
    else:
        return None
    if not candidate or any(token.kind == "identifier" and token.text == "result" for token in candidate):
        return None
    start = candidate[0].start
    end = candidate[-1].end
    return expression[start:end].strip(), len(candidate)


def _parse_expression(expression: str) -> bool:
    """Use the frontend parser as the syntax gate for a candidate expression."""
    source = (
        "module symbolic_search\n\n"
        "fn probe() -> Bool:\n"
        f"    ensure {expression}\n"
        "    true\n"
    )
    try:
        parse_surface(source, path=str(Path("<symbolic-search>")))
    except (SurfaceSyntaxError, ValueError, ExpressionLexError):
        return False
    return True


def _hole_bindings(hole: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    context: set[str] = set()
    for item in hole.get("context", ()):
        if isinstance(item, Mapping):
            name = item.get("name")
        elif isinstance(item, (list, tuple)) and item and type(item[0]) is str:
            name = item[0]
        else:
            name = None
        if isinstance(name, str) and name:
            context.add(name)
    callables: set[str] = set()
    for item in hole.get("callables", ()):
        if not isinstance(item, Mapping) or type(item.get("name")) is not str:
            continue
        effects = item.get("effects", ())
        capabilities = item.get("capabilities", ())
        if isinstance(effects, (list, tuple)) and isinstance(capabilities, (list, tuple)) and not effects and not capabilities:
            callables.add(item["name"])
    return context, callables, set()


def _type_constructors(world: SemanticWorld, hole: Mapping[str, Any]) -> set[str]:
    names = {"true", "false", "None", "none", "Unit"}
    expected = str(hole.get("expected_type", ""))
    if expected:
        names.add(expected.split("[", 1)[0])
    for symbol in world.data.get("symbols", ()):
        if not isinstance(symbol, Mapping):
            continue
        kind = str(symbol.get("kind", "")).casefold()
        if kind in {"enum", "variant", "enum_variant", "constructor", "type"}:
            for key in ("name", "qualified_name"):
                value = symbol.get(key)
                if isinstance(value, str) and value:
                    names.add(value)
    return names


def _names_allowed(expression: str, world: SemanticWorld, hole: Mapping[str, Any]) -> bool:
    try:
        tokens = _significant(lex_expression(expression))
    except ExpressionLexError:
        return False
    context, callables, _ = _hole_bindings(hole)
    constructors = _type_constructors(world, hole)
    allowed = context | callables | constructors
    identifiers = [token for token in tokens if token.kind == "identifier"]
    if any(token.text == "result" for token in identifiers):
        return False
    # A dotted callable is one name; do not independently admit an effectful
    # prefix such as ``console`` merely because it appears before a dot.
    qualified: set[str] = set()
    for index, token in enumerate(tokens):
        if token.kind != "identifier":
            continue
        parts = [token.text]
        end = index
        while end + 2 < len(tokens) and tokens[end + 1].text == "." and tokens[end + 2].kind == "identifier":
            parts.extend((tokens[end + 1].text, tokens[end + 2].text))
            end += 2
        if len(parts) > 1:
            qualified.add("".join(parts))
    if qualified:
        if any(name not in callables and name not in constructors and name not in context for name in qualified):
            return False
        qualified_parts = {part for name in qualified for part in name.split(".")}
        return all(token.text in qualified_parts or token.text in allowed for token in identifiers)
    return all(token.text in allowed for token in identifiers)


def _target_hole(world: SemanticWorld, target: str, hole_id: str) -> Mapping[str, Any]:
    symbol = world.resolve(target)
    holes = tuple(item for item in symbol.get("holes", ()) if isinstance(item, Mapping))
    matches = tuple(item for item in holes if item.get("hole_id") == hole_id)
    if len(matches) != 1:
        raise WorldError("UnknownTypedHole")
    return matches[0]


def search_symbolic_candidates(
    world: SemanticWorld,
    request: SynthesisRequest | Mapping[str, Any],
) -> tuple[SynthesisCandidate, ...]:
    """Project exact postcondition equalities into proposed fill-hole changes."""
    if not isinstance(world, SemanticWorld):
        raise WorldError("SynthesisWorldMismatch")
    active = _request(request)
    if active.operation != "fill_hole":
        raise WorldError("SynthesisChangeOperationMismatch")
    if set(active.arguments) != {"hole_id"} or type(active.arguments.get("hole_id")) is not str or not active.arguments["hole_id"]:
        raise WorldError("SynthesisInvalidArguments")
    world.require_fresh()
    if active.world_digest != world.digest:
        raise StaleWorldError("StaleWorld: synthesis request belongs to another world")
    hole_id = active.arguments["hole_id"]
    hole = _target_hole(world, active.target, hole_id)
    capsule = world.compile_context(active.target, goal=active.goal)
    capsule_holes = tuple(item for item in capsule.holes if item.get("hole_id") == hole_id)
    if len(capsule_holes) != 1:
        raise WorldError("SynthesisHoleBindingMismatch")
    capsule_hole = capsule_holes[0]
    for key in ("hole_id", "expected_type", "node_id"):
        if str(capsule_hole.get(key, "")) != str(hole.get(key, "")):
            raise WorldError("SynthesisHoleBindingMismatch")

    candidates: dict[str, tuple[int, Mapping[str, Any], str]] = {}
    for ensure_index, ensure_text in enumerate(capsule.ensures):
        projected = _projection(ensure_text)
        if projected is None:
            continue
        expression, token_cost = projected
        if not _parse_expression(expression) or not _names_allowed(expression, world, hole):
            continue
        candidates.setdefault(expression, (token_cost, {"ensure_index": ensure_index, "ensure_text": ensure_text}, expression))

    result: list[SynthesisCandidate] = []
    for expression, (token_cost, source, tie_breaker) in sorted(candidates.items(), key=lambda item: (item[1][0], item[0])):
        change = preview_fill_hole(world, active.target, hole_id, expression)
        provenance = {
            "algorithm": "postcondition_equality_projection",
            "source_contract": source["ensure_text"],
            "ensure_index": source["ensure_index"],
            "ensure_text": source["ensure_text"],
            "expression": expression,
            "token_cost": token_cost,
        }
        result.append(
            build_synthesis_candidate(
                world,
                active,
                change,
                producer="symbolic",
                producer_revision=SYMBOLIC_PRODUCER_REVISION,
                rank=CandidateRank(0, token_cost, tie_breaker),
                provenance=provenance,
            )
        )
    return tuple(result)


__all__ = ["SYMBOLIC_PRODUCER_REVISION", "search_symbolic_candidates"]
