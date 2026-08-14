from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from merlo.frontend_model import ConciseApplicationError
from merlo.modules import STDLIB_MODULES


@dataclass(frozen=True)
class _Module:
    name: str
    path: Path
    source: str
    imports: tuple[str, ...]
    body: str
    body_source_lines: tuple[int, ...]


def _project_root(entry: Path) -> Path:
    resolved = entry.resolve()
    for parent in (resolved.parent, *resolved.parents):
        candidate = parent / "app" / "main.mlo"
        if candidate == resolved:
            return parent
    return resolved.parent


def _read_module(
    path: Path,
    root: Path,
    *,
    external_name: str | None = None,
) -> _Module:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConciseApplicationError(f"{path}: cannot read module: {exc}") from exc
    lines = source.splitlines()
    if not lines:
        raise ConciseApplicationError(f"{path}: empty module")
    match = re.fullmatch(r"module\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", lines[0].strip())
    if match is None:
        raise ConciseApplicationError(f"{path}:1: expected `module qualified.name`")
    name = match.group(1)
    expected = root.joinpath(*name.split(".")).with_suffix(".mlo")
    if external_name is not None:
        if name != external_name:
            raise ConciseApplicationError(
                f"{path}: declares {name!r}, expected standard module {external_name!r}"
            )
    elif path.resolve() != expected.resolve():
        raise ConciseApplicationError(f"{path}:1: module {name!r} must live at {expected}")
    imports = []
    body_pairs: list[tuple[str, int]] = []
    header = True
    for line_number, line in enumerate(lines[1:], 2):
        stripped = line.strip()
        if header and not stripped:
            body_pairs.append(("", line_number))
            continue
        import_match = re.fullmatch(r"use\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", stripped)
        if header and import_match:
            imports.append(import_match.group(1))
            continue
        header = False
        if stripped.startswith("use "):
            raise ConciseApplicationError(
                f"{path}:{line_number}: imports must precede declarations"
            )
        body_pairs.append((line, line_number))
    while body_pairs and not body_pairs[0][0].strip():
        body_pairs.pop(0)
    while body_pairs and not body_pairs[-1][0].strip():
        body_pairs.pop()
    return _Module(
        name,
        path,
        source,
        tuple(imports),
        "\n".join(line for line, _ in body_pairs) + "\n",
        tuple(line_number for _, line_number in body_pairs),
    )


def _load_modules(entry: Path) -> tuple[_Module, ...]:
    root = _project_root(entry)
    visiting: list[str] = []
    loaded: dict[str, _Module] = {}
    ordered: list[_Module] = []

    def visit(name: str, path: Path) -> None:
        if name in visiting:
            cycle = " -> ".join((*visiting, name))
            raise ConciseApplicationError(f"{path}: cyclic module dependency: {cycle}")
        if name in loaded:
            return
        standard_path = STDLIB_MODULES.get(name)
        if not path.exists() and standard_path is not None:
            module = _read_module(standard_path, root, external_name=name)
        else:
            module = _read_module(path, root)
        if module.name != name:
            raise ConciseApplicationError(
                f"{path}: imported as {name!r}, declares {module.name!r}"
            )
        visiting.append(name)
        for dependency in module.imports:
            visit(dependency, root.joinpath(*dependency.split(".")).with_suffix(".mlo"))
        visiting.pop()
        loaded[name] = module
        ordered.append(module)

    entry_module = _read_module(entry.resolve(), root)
    visit(entry_module.name, entry.resolve())
    return tuple(ordered)


__all__ = ["_Module", "_load_modules", "_project_root", "_read_module"]
