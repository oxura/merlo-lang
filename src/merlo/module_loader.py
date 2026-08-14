from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from merlo.frontend_model import ConciseApplicationError
from merlo.module_syntax import ModuleSyntaxError, parse_module_prelude
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
    try:
        prelude = parse_module_prelude(source, path=str(path))
    except ModuleSyntaxError as exc:
        raise ConciseApplicationError(str(exc)) from exc
    if prelude.module is None:
        raise ConciseApplicationError(f"{path}:1: expected `module qualified.name`")
    name = prelude.module
    expected = root.joinpath(*name.split(".")).with_suffix(".mlo")
    if external_name is not None:
        if name != external_name:
            raise ConciseApplicationError(
                f"{path}: declares {name!r}, expected standard module {external_name!r}"
            )
    elif path.resolve() != expected.resolve():
        raise ConciseApplicationError(
            f"{path}:{prelude.module_line}: module {name!r} must live at {expected}"
        )
    return _Module(
        name,
        path,
        source,
        prelude.imports,
        prelude.body,
        prelude.body_source_lines,
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
