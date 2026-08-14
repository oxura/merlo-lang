from __future__ import annotations

import re
from dataclasses import dataclass


_QUALIFIED_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


@dataclass(frozen=True)
class ModulePrelude:
    """The structural module/use header and its declaration body."""

    module: str | None
    imports: tuple[str, ...]
    body: str
    body_source_lines: tuple[int, ...]
    module_line: int | None


class ModuleSyntaxError(ValueError):
    def __init__(self, path: str, line: int, code: str, message: str) -> None:
        self.path = path
        self.line = line
        self.code = code
        self.message = message
        super().__init__(f"{path}:{line}: {code}: {message}")


def _directive_line(text: str, keyword: str) -> bool:
    return text == keyword or text.startswith(keyword + " ") or text.startswith(keyword + "\t")


def _name(text: str) -> str | None:
    candidate = text.strip()
    return candidate if _QUALIFIED_NAME.fullmatch(candidate) else None


def parse_module_prelude(
    source: str,
    *,
    path: str = "<source>",
    require_module: bool = True,
) -> ModulePrelude:
    """Parse one module declaration and its contiguous use prelude.

    ``require_module=False`` is used by the standalone surface parser, whose
    expression/declaration snippets historically had no module header.  Once a
    module/use directive appears, the same strict header rules apply.
    """

    lines = source.splitlines()
    first_nonblank = next(
        (index for index, line in enumerate(lines) if line.strip()),
        None,
    )
    if first_nonblank is None:
        if require_module:
            raise ModuleSyntaxError(path, 1, "EmptyModule", "empty module")
        return ModulePrelude(None, (), "", (), None)

    first = lines[first_nonblank].strip()
    if not _directive_line(first, "module"):
        if _directive_line(first, "use") and not require_module:
            imports: list[str] = []
            seen_imports: set[str] = set()
            body_start: int | None = None
            for index in range(first_nonblank, len(lines)):
                stripped = lines[index].strip()
                if not stripped:
                    continue
                line_number = index + 1
                if _directive_line(stripped, "module"):
                    raise ModuleSyntaxError(
                        path,
                        line_number,
                        "ExpectedModule",
                        "expected `module qualified.name` as the first directive",
                    )
                if _directive_line(stripped, "use"):
                    imported = _name(stripped[len("use") :].strip())
                    if imported is None:
                        raise ModuleSyntaxError(
                            path,
                            line_number,
                            "MalformedUse",
                            "expected `use qualified.name`",
                        )
                    if body_start is not None:
                        raise ModuleSyntaxError(
                            path,
                            line_number,
                            "LateUse",
                            "imports must precede declarations",
                        )
                    if imported in seen_imports:
                        raise ModuleSyntaxError(
                            path,
                            line_number,
                            "DuplicateUse",
                            f"duplicate import {imported!r}",
                        )
                    seen_imports.add(imported)
                    imports.append(imported)
                    continue
                if body_start is None:
                    body_start = index
            body_lines = (
                []
                if body_start is None
                else list(enumerate(lines[body_start:], body_start + 1))
            )
            return _body(None, tuple(imports), body_lines, None)
        if _directive_line(first, "use"):
            raise ModuleSyntaxError(
                path,
                first_nonblank + 1,
                "ExpectedModule",
                "expected `module qualified.name` as the first directive",
            )
        if require_module:
            raise ModuleSyntaxError(
                path,
                first_nonblank + 1,
                "ExpectedModule",
                "expected `module qualified.name`",
            )
        body_lines = list(enumerate(lines, 1))
        return _body(None, (), body_lines, None)

    declaration_text = first[len("module") :].strip()
    module = _name(declaration_text)
    if module is None:
        raise ModuleSyntaxError(
            path,
            first_nonblank + 1,
            "MalformedModule",
            "expected `module qualified.name`",
        )

    imports: list[str] = []
    seen_imports: set[str] = set()
    body_start: int | None = None
    for index in range(first_nonblank + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        line_number = index + 1
        if _directive_line(stripped, "module"):
            if body_start is None:
                raise ModuleSyntaxError(
                    path,
                    line_number,
                    "DuplicateModule",
                    "duplicate module declaration; exactly one module declaration is allowed",
                )
            raise ModuleSyntaxError(
                path,
                line_number,
                "DuplicateModule",
                "duplicate module declaration",
            )
        if _directive_line(stripped, "use"):
            imported = _name(stripped[len("use") :].strip())
            if imported is None:
                raise ModuleSyntaxError(
                    path,
                    line_number,
                    "MalformedUse",
                    "expected `use qualified.name`",
                )
            if body_start is not None:
                raise ModuleSyntaxError(
                    path,
                    line_number,
                    "LateUse",
                    "imports must precede declarations",
                )
            if imported in seen_imports:
                raise ModuleSyntaxError(
                    path,
                    line_number,
                    "DuplicateUse",
                    f"duplicate import {imported!r}",
                )
            seen_imports.add(imported)
            imports.append(imported)
            continue
        if body_start is None:
            body_start = index

    if body_start is None:
        body_lines: list[tuple[int, str]] = []
    else:
        body_lines = list(enumerate(lines[body_start:], body_start + 1))
    return _body(module, tuple(imports), body_lines, first_nonblank + 1)


def _body(
    module: str | None,
    imports: tuple[str, ...],
    body_lines: list[tuple[int, str]],
    module_line: int | None,
) -> ModulePrelude:
    while body_lines and not body_lines[0][1].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1][1].strip():
        body_lines.pop()
    return ModulePrelude(
        module,
        imports,
        "\n".join(line for _, line in body_lines) + "\n",
        tuple(number for number, _ in body_lines),
        module_line,
    )


__all__ = ["ModulePrelude", "ModuleSyntaxError", "parse_module_prelude"]
