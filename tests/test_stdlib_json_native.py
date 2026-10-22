from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from merlo.compiler import compile_project
from merlo.project import resolve_dependencies

SOURCE = '''module main
use std.json

export main(input: Text) -> Text:
    parsed = json.parse(input.as_view())
    match parsed:
        case Result.Ok(value):
            rendered = json.format(value)
            console.write(rendered.as_view())
            return ""
        case Result.Err(error):
            match error:
                case json.JsonError.InvalidEscape:
                    return "INVALID_ESCAPE"
                case json.JsonError.InvalidString:
                    return "INVALID_STRING"
                case json.JsonError.UnexpectedEnd:
                    return "UNEXPECTED_END"
                case _:
                    return "OTHER_ERROR"
'''


@pytest.fixture(scope="module")
def json_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    project = tmp_path_factory.mktemp("json-native")
    source = project / "src"
    source.mkdir()
    (project / "merlo.toml").write_text(
        'manifest = 1\n[project]\nname = "json-native"\n'
        'version = "0.1.0"\nedition = "alpha.1"\n',
        encoding="utf-8",
    )
    (source / "main.mlo").write_text(SOURCE, encoding="utf-8")
    resolve_dependencies(project)
    compilation = compile_project(
        project, emit_native=True, require_interface_lock=False,
        output=project / "json-native",
    )
    assert compilation.native is not None and compilation.native.binary_path is not None
    return Path(compilation.native.binary_path)


def run_json(binary: Path, source: str) -> bytes:
    result = subprocess.run(
        [binary], input=source.encode("utf-8"), capture_output=True,
        check=False, timeout=15, env={"PATH": ""},
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stderr == b""
    return result.stdout


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r'"\ud83d\ude00"', "😀"),
        (r'"\uD800\uDC00"', "\U00010000"),
        (r'"\udbff\udfff"', "\U0010ffff"),
        (r'"\u0000\uD7FF\uE000\uFFFF"', "\x00\ud7ff\ue000\uffff"),
        (r'"\"\\\/\b\f\n\r\t"', '"\\/\b\f\n\r\t'),
        (json.dumps("Кириллица café — 😀", ensure_ascii=False), "Кириллица café — 😀"),
        (
            r'{"\ud83d\ude00":["\u0000",{"x":"\ud834\udd1e"}],"n":1,"b":true,"z":null}',
            {"😀": ["\x00", {"x": "𝄞"}], "n": 1, "b": True, "z": None},
        ),
    ],
)
def test_native_json_preserves_unicode_and_nested_values(
    json_binary: Path, source: str, expected: object,
) -> None:
    assert json.loads(run_json(json_binary, source)) == expected


def test_native_json_formatter_escapes_every_control_character(json_binary: Path) -> None:
    value = "".join(chr(code) for code in range(32))
    rendered = run_json(json_binary, json.dumps(value))
    assert json.loads(rendered) == value
    assert all(byte >= 32 for byte in rendered)


@pytest.mark.parametrize(
    "source",
    [
        r'"\ud800"',
        r'"\udc00"',
        r'"\udc00\ud800"',
        r'"\ud800\ud800"',
        r'"\ud800\u0041"',
        r'"\udbff\ue000"',
        r'"\ud800\u"',
        r'"\ud800\""',
        r'"\ud800\\udc00"',
        r'"\u"',
        r'"\u000"',
        r'"\u00xz"',
        r'"\q"',
    ],
)
def test_native_json_rejects_malformed_unicode_escapes(
    json_binary: Path, source: str,
) -> None:
    assert run_json(json_binary, source) == b"INVALID_ESCAPE"


@pytest.mark.parametrize("control", ["\x00", "\n", "\x1f"])
def test_native_json_rejects_unescaped_control_characters(
    json_binary: Path, control: str,
) -> None:
    assert run_json(json_binary, '"before' + control + 'after"') == b"INVALID_STRING"


def test_native_json_rejects_unterminated_strings(json_binary: Path) -> None:
    assert run_json(json_binary, '"unclosed') == b"UNEXPECTED_END"
