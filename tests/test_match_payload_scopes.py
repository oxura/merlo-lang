from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.project import resolve_dependencies
from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
from merlo.surface_parser import parse_surface


SOURCE = '''module main

enum Value:
    Flag: Bool
    Textual: Text

keep(value: Value, item: Text) -> Text:
    match value:
        case Flag(item):
            if item:
                console.write("flag:true|")
            else:
                console.write("flag:false|")
        case Textual(item):
            console.write(item.as_view())
            console.write("|")
    item

export main(input: Text) -> Text:
    first = keep(Value.Flag(true), "kept")
    console.write(first.as_view())
    console.write("|")
    second = keep(Value.Textual("payload"), "kept")
    console.write(second.as_view())
    ""
'''


def test_distinct_arm_types_shadow_without_replacing_outer_owner(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / "merlo.toml").write_text(
        'manifest = 1\n[project]\nname = "match-scopes"\n'
        'version = "0.1.0"\nedition = "alpha.1"\n',
        encoding="utf-8",
    )
    (source / "main.mlo").write_text(SOURCE, encoding="utf-8")
    resolve_dependencies(tmp_path)
    compilation = compile_project(
        tmp_path, emit_native=True, require_interface_lock=False,
        output=tmp_path / "match-scopes",
    )
    assert compilation.native is not None and compilation.native.binary_path is not None
    result = subprocess.run(
        [compilation.native.binary_path], input=b"unused", capture_output=True,
        check=False, timeout=15, env={"PATH": ""},
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == b"flag:true|kept|payload|kept"
    assert result.stderr == b""


def test_payload_is_not_visible_after_match() -> None:
    source = '''enum Value:
    Flag: Bool
    Textual: Text

bad(value: Value) -> Text:
    match value:
        case Flag(item):
            true
        case Textual(item):
            "discarded"
    item
'''
    with pytest.raises(SurfaceElaborationError, match="UnresolvedName: bad.item"):
        elaborate_surface(parse_surface(source, path="scope.mlo"))


def test_standalone_inference_uses_separate_payload_identities() -> None:
    source = '''enum Value:
    Flag: Bool
    Textual: Text

render(value: Value) -> Text:
    match value:
        case Flag(item):
            if item:
                return "yes"
            else:
                return "no"
        case Textual(item):
            return item.clone()
'''
    result = elaborate_surface(parse_surface(source, path="scope.mlo"))
    render = result.canonical.function("render")
    assert render.parameters == (("value", "Value"),)
    assert render.return_type == "Text"
    assert render.effects == ()


def test_payload_identity_does_not_depend_on_trivia_positions() -> None:
    source = '''enum Value:
    Flag: Bool
    Textual: Text

render(value: Value) -> Text:
    match value:
        case Flag(item):
            if item:
                return "yes"
            else:
                return "no"
        case Textual(item):
            return item.clone()
'''
    original = elaborate_surface(parse_surface(source, path="scope.mlo"))
    shifted = elaborate_surface(
        parse_surface("\n# leading trivia\n\n" + source, path="scope.mlo")
    )
    assert original.canonical.semantic_hash == shifted.canonical.semantic_hash


def test_returning_arms_do_not_make_a_partial_match_exhaustive() -> None:
    source = '''enum Value:
    Flag: Bool
    Textual: Text

bad(value: Value) -> Text:
    match value:
        case Flag(item):
            return "flag"
'''
    with pytest.raises(SurfaceElaborationError, match="NonExhaustiveMatch"):
        elaborate_surface(parse_surface(source, path="scope.mlo"))
