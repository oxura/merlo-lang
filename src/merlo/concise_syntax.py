from __future__ import annotations

import ast
import hashlib
import re

from merlo.frontend_model import ConciseApplicationError
from merlo.structured_hir_v2 import _rewrite_postfix_try
from merlo.type_parser import (
    GenericTypeSyntaxError,
    generic_arguments,
    iter_type_expressions,
    parse_type,
    split_structural_commas,
    validate_type_expr,
)


_FORBIDDEN_FEATURES = (
    "interface ",
    "async ",
    "flow ",
    "machine ",
    "macro ",
    "web ",
    "ui ",
)
_SCALARS = frozenset(
    {"Unit", "Bool", "Byte", "UInt64", "Int64", "Float32", "Float64"}
)
_NUMERIC_TYPES = frozenset({"Byte", "UInt64", "Int64", "Float32", "Float64"})
_TYPE_ALIASES = {"Int": "Int64", "UInt": "UInt64", "Float": "Float64"}
_CONCISE_MAP_TYPE = "Map[Text,UInt64]"
_OWNERS = frozenset({"Text", "Bytes", "TextBuilder"})


def _normalize_type(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    type_name = ast.unparse(node).replace(" ", "")
    for alias, canonical in _TYPE_ALIASES.items():
        type_name = re.sub(rf"\b{alias}\b", canonical, type_name)
    return type_name


def _type_leaf(type_name: str) -> str:
    return type_name.rsplit("__", 1)[-1].rsplit(".", 1)[-1]


def _contains_dynamic_any(source: str) -> bool:
    parsed = ast.parse(_preprocess_core(source))
    return any(
        isinstance(node, ast.Name) and node.id == "Any"
        for node in ast.walk(parsed)
    )


def _one_edit_apart(left: str, right: str) -> bool:
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(
            a != b for a, b in zip(left, right, strict=True)
        ) == 1
    shorter, longer = (
        (left, right) if len(left) < len(right) else (right, left)
    )
    index = 0
    differences = 0
    for character in longer:
        if index < len(shorter) and shorter[index] == character:
            index += 1
        else:
            differences += 1
            if differences > 1:
                return False
    return True


def _generic_arguments(type_name: str) -> tuple[str, ...]:
    if "[" not in type_name:
        return ()
    try:
        return generic_arguments(type_name)
    except GenericTypeSyntaxError as error:
        raise ConciseApplicationError(
            f"malformed generic type {type_name!r}: {error}"
        ) from error


def _map_types(type_name: str | None) -> tuple[str, str] | None:
    if not type_name:
        return None
    try:
        parsed = parse_type(type_name)
    except GenericTypeSyntaxError as error:
        if type_name.startswith("Map"):
            raise ConciseApplicationError(
                f"malformed generic type {type_name!r}: {error}"
            ) from error
        return None
    if parsed.name != "Map" or len(parsed.args) != 2:
        return None
    return parsed.args[0].canonical, parsed.args[1].canonical


def _split_parameters(payload: str, context: str) -> tuple[str, ...]:
    try:
        return split_structural_commas(payload)
    except GenericTypeSyntaxError as error:
        raise ConciseApplicationError(
            f"{context}: malformed generic type: {error}"
        ) from error


def _sum_nominal_name(type_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", type_name)


def _protected_mask(source: str) -> list[bool]:
    protected = [False] * len(source)
    cursor = 0
    length = len(source)
    while cursor < length:
        character = source[cursor]
        if character == "#":
            end = source.find("\n", cursor)
            if end < 0:
                end = length
            protected[cursor:end] = [True] * (end - cursor)
            cursor = end
            continue
        if character in {'"', "'"}:
            delimiter_length = 3 if source.startswith(character * 3, cursor) else 1
            end = cursor + delimiter_length
            while end < length:
                if source[end] == "\\":
                    end += 2
                    continue
                if source.startswith(character * delimiter_length, end):
                    end += delimiter_length
                    break
                end += 1
            end = min(end, length)
            protected[cursor:end] = [True] * (end - cursor)
            cursor = end
            continue
        cursor += 1
    return protected


def _protected_line_views(
    source: str,
) -> list[tuple[str, dict[str, str]]]:
    mask = _protected_mask(source)
    views: list[tuple[str, dict[str, str]]] = []
    offset = 0
    token_number = 0
    for physical in source.splitlines(keepends=True):
        line = physical.rstrip("\r\n")
        replacements: dict[str, str] = {}
        pieces: list[str] = []
        cursor = 0
        while cursor < len(line):
            if not mask[offset + cursor]:
                start = cursor
                cursor += 1
                while cursor < len(line) and not mask[offset + cursor]:
                    cursor += 1
                pieces.append(line[start:cursor])
                continue
            start = cursor
            cursor += 1
            while cursor < len(line) and mask[offset + cursor]:
                cursor += 1
            token = f"\x00{token_number}\x00"
            token_number += 1
            replacements[token] = line[start:cursor]
            pieces.append(token)
        views.append(("".join(pieces), replacements))
        offset += len(physical)
    return views


def _rewrite_code_text(
    source: str,
    replacements: dict[str, str],
) -> str:
    lines: list[str] = []
    for original, protected in _protected_line_views(source):
        line = original
        for before, after in sorted(
            replacements.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            line = line.replace(before, after)
        for token, replacement in protected.items():
            line = line.replace(token, replacement)
        lines.append(line)
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def _rewrite_code_identifiers(
    source: str,
    replacements: dict[str, str],
    *,
    call_only: frozenset[str] = frozenset(),
) -> str:
    """Rewrite exact identifiers in code, preserving strings and comments."""
    protected = _protected_mask(source)
    output: list[str] = []
    cursor = 0
    length = len(source)
    while cursor < length:
        if protected[cursor]:
            output.append(source[cursor])
            cursor += 1
            continue
        character = source[cursor]
        if character.isalpha() or character == "_":
            start = cursor
            cursor += 1
            while cursor < length and (
                source[cursor].isalnum() or source[cursor] == "_"
            ):
                cursor += 1
            identifier = source[start:cursor]
            candidate = identifier
            candidate_end = cursor
            if cursor < length and source[cursor] == ".":
                member_start = cursor + 1
                member_end = member_start
                if member_start < length and (
                    source[member_start].isalpha()
                    or source[member_start] == "_"
                ):
                    member_end += 1
                    while member_end < length and (
                        source[member_end].isalnum()
                        or source[member_end] == "_"
                    ):
                        member_end += 1
                    candidate = source[start:member_end]
                    candidate_end = member_end
            replacement = replacements.get(candidate)
            if replacement is None:
                candidate = identifier
                candidate_end = cursor
                replacement = replacements.get(identifier)
            line_tail = source[candidate_end:].split("\n", 1)[0]
            is_call = line_tail.lstrip().startswith("(")
            if replacement is not None and (
                candidate not in call_only or is_call
            ):
                output.append(replacement)
                cursor = candidate_end
                continue
            output.append(source[start:candidate_end])
            cursor = candidate_end
            continue
        output.append(character)
        cursor += 1
    return "".join(output)


def _rewrite_language_literals(source: str) -> str:
    return _rewrite_code_identifiers(
        source,
        {
            "true": "True",
            "false": "False",
            "Option.None": "Option.NoneValue",
        },
    )


def _preprocess_core(source: str) -> str:
    source = _rewrite_language_literals(source)
    output = []
    for original, protected in _protected_line_views(source):
        indent = original[: len(original) - len(original.lstrip())]
        line = re.sub(r"^(\s*)export\s+", r"\1", original)
        if not indent:
            line = re.sub(r"^(record|enum)\s+", "class ", line)
            if re.fullmatch(r"[A-Z][A-Za-z0-9_]*\s*:", line):
                line = "class " + line
            expression_function = re.fullmatch(
                r"([a-z_][A-Za-z0-9_]*)\((.*)\)\s*"
                r"(?:->\s*(.+?))?\s*=\s*(.+)",
                line,
            )
            block_function = re.fullmatch(
                r"([a-z_][A-Za-z0-9_]*)\((.*)\)\s*"
                r"(?:->\s*(.+))?\s*:",
                line,
            )
            if expression_function is not None:
                name, parameters, return_type, expression = (
                    expression_function.groups()
                )
                suffix = f" -> {return_type}" if return_type else ""
                line = (
                    f"def {name}({parameters}){suffix}: "
                    f"return {expression}"
                )
            elif block_function is not None:
                name, parameters, return_type = block_function.groups()
                suffix = f" -> {return_type}" if return_type else ""
                line = f"def {name}({parameters}){suffix}:"
        line = re.sub(r"^(\s*)fn\s+", r"\1def ", line)
        line = re.sub(r"^(\s*)task\s+", r"\1def ", line)
        line = re.sub(r"^(\s*)const\s+", r"\1", line)
        line = re.sub(r"^(\s*)(?:let|var)\s+", r"\1", line)
        line = re.sub(
            r"(?<![A-Za-z0-9_]])([A-Za-z_][A-Za-z0-9_]*)\?",
            r"Option[\1]",
            line,
        )
        if re.fullmatch(r"\s*uses\s+.+", line):
            line = ""
        print_match = re.fullmatch(r"(\s*)print\s+(.+)", line)
        if print_match is not None:
            line = (
                f"{print_match.group(1)}"
                f"console.write({print_match.group(2)})"
            )
        line = _rewrite_postfix_try(line)
        if re.search(r"\b(?:and|or)\s*$", line):
            line += " " + chr(92)
        for token, replacement in protected.items():
            line = line.replace(token, replacement)
        output.append(line)
    return "\n".join(output) + "\n"


def lower_concise_sum_types(source: str) -> str:
    try:
        occurrences = iter_type_expressions(source)
    except GenericTypeSyntaxError as error:
        raise ConciseApplicationError(
            f"malformed generic type: {error}"
        ) from error
    normalized = {
        source[start:end]: expression.canonical
        for start, end, expression in occurrences
        if expression.name in {"Option", "Result"}
    }
    source = _rewrite_code_text(source, normalized)
    sum_types = sorted(set(normalized.values()))
    if not sum_types:
        return source
    source = _rewrite_code_identifiers(
        source,
        {
            "Result.Ok": "Ok",
            "Result.Err": "Err",
            "Option.Some": "Some",
            "Option.None": "None",
        },
        call_only=frozenset(
            {"Result.Ok", "Result.Err", "Option.Some"}
        ),
    )
    declarations = []
    current_return: str | None = None
    variables: dict[str, str] = {}
    match_sum: tuple[int, str] | None = None
    mapping: dict[str, str] = {}
    used_nominals: dict[str, str] = {}
    for type_name in sum_types:
        nominal = _sum_nominal_name(type_name)
        if nominal in used_nominals and used_nominals[nominal] != type_name:
            nominal = f"{nominal}_{hashlib.sha256(type_name.encode()).hexdigest()[:8]}"
        mapping[type_name] = nominal
        used_nominals[nominal] = type_name
    for type_name in sum_types:
        nominal = mapping[type_name]
        arguments = _generic_arguments(type_name)
        if type_name.startswith("Option[") and len(arguments) == 1:
            declarations.extend(
                (
                    f"enum {nominal}:",
                    "    NoneValue",
                    f"    Some: {arguments[0]}",
                    "",
                )
            )
        elif type_name.startswith("Result[") and len(arguments) == 2:
            declarations.extend(
                (
                    f"enum {nominal}:",
                    f"    Ok: {arguments[0]}",
                    f"    Err: {arguments[1]}",
                    "",
                )
            )
        else:
            raise ConciseApplicationError(
                f"unsupported sum type {type_name!r}"
            )
    output = []
    for original, protected in _protected_line_views(source):
        line = original
        indent = len(line) - len(line.lstrip())
        if match_sum and line.strip() and indent <= match_sum[0]:
            match_sum = None
        function = re.match(
            r"^fn\s+[A-Za-z_]\w*\((.*)\)\s*->\s*(.+):$",
            line,
        )
        if function:
            variables = {}
            current_return = (
                function.group(2).strip().replace(" ", "")
            )
            for parameter in _split_parameters(
                function.group(1), "concise function parameters"
            ):
                if ":" in parameter:
                    name, type_name = parameter.split(":", 1)
                    variables[name.strip()] = (
                        type_name.strip().replace(" ", "")
                    )
        binding = re.match(
            r"^\s*(?:let|var)\s+([A-Za-z_]\w*)\s*:\s*"
            r"([^=]+?)\s*=",
            line,
        )
        binding_type = None
        if binding:
            binding_type = binding.group(2).strip().replace(" ", "")
            variables[binding.group(1)] = binding_type
        match = re.match(
            r"^(\s*)match\s+([A-Za-z_]\w*)\s*:$",
            line,
        )
        if match:
            subject_type = variables.get(match.group(2))
            if subject_type in mapping:
                match_sum = (len(match.group(1)), subject_type)
        active_type = (
            binding_type
            if binding_type in mapping
            else current_return
            if current_return in mapping
            else None
        )
        if match_sum and line.lstrip().startswith("case "):
            active_type = match_sum[1]
        line = _rewrite_code_identifiers(
            line,
            {
                "Result.Ok": "Ok",
                "Result.Err": "Err",
                "Option.Some": "Some",
                "Option.None": "None",
            },
            call_only=frozenset(
                {"Result.Ok", "Result.Err", "Option.Some"}
            ),
        )
        if active_type:
            nominal = mapping[active_type]
            if active_type.startswith("Option["):
                line = _rewrite_code_identifiers(
                    line,
                    {
                        "Some": f"{nominal}.Some",
                        "None": f"{nominal}.NoneValue",
                    },
                    call_only=frozenset({"Some"}),
                )
            else:
                line = _rewrite_code_identifiers(
                    line,
                    {
                        "Ok": f"{nominal}.Ok",
                        "Err": f"{nominal}.Err",
                    },
                    call_only=frozenset({"Ok", "Err"}),
                )
        for type_name in sorted(mapping, key=len, reverse=True):
            line = line.replace(type_name, mapping[type_name])
        for token, replacement in protected.items():
            line = line.replace(token, replacement)
        output.append(line)
    return "\n".join((*declarations, *output)).strip() + "\n"


__all__ = [
    "ConciseApplicationError",
    "GenericTypeSyntaxError",
    "generic_arguments",
    "iter_type_expressions",
    "parse_type",
    "split_structural_commas",
    "validate_type_expr",
    "_FORBIDDEN_FEATURES",
    "_SCALARS",
    "_NUMERIC_TYPES",
    "_TYPE_ALIASES",
    "_CONCISE_MAP_TYPE",
    "_OWNERS",
    "_normalize_type",
    "_type_leaf",
    "_contains_dynamic_any",
    "_one_edit_apart",
    "_generic_arguments",
    "_map_types",
    "_split_parameters",
    "_sum_nominal_name",
    "_protected_mask",
    "_protected_line_views",
    "_rewrite_code_text",
    "_rewrite_code_identifiers",
    "_rewrite_language_literals",
    "_preprocess_core",
    "lower_concise_sum_types",
]
