from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from merlo.compiler import compile_project
from merlo.project import resolve_dependencies

ROOT = Path(__file__).parents[1]
SOURCE = '''module main
use lexer
use syntax

classify(source: Text) -> Text:
    length = source.len()
    tokens = lexer.lex(source)
    output = TextBuilder.new()
    expected: UInt64 = 0
    index: UInt64 = 0
    while index < tokens.len():
        token = tokens.get(index)
        if token.length > 0:
            if token.start != expected:
                return "GAP"
            expected = token.start + token.length
        if token.kind == syntax.token_invalid_kind():
            output.append_text("I")
        else:
            if token.kind == syntax.token_string_kind():
                output.append_text("L")
            else:
                if token.kind != syntax.token_eof_kind() and token.kind != syntax.token_newline_kind() and token.kind != syntax.token_indent_kind() and token.kind != syntax.token_dedent_kind() and token.kind != syntax.token_whitespace_kind() and token.kind != syntax.token_comment_kind():
                    output.append_text("O")
        index = index + 1
    if expected != length:
        return "TRUNCATED"
    output.finish()

export main(source: Text) -> Text:
    result = classify(source)
    console.write(result.as_view())
    ""
'''

LEXEME_CASES = (
    ('"plain"', b"L"),
    ("'single'", b"L"),
    (r'"\x41\XFF"', b"L"),
    (r'"\u0061\U0001F600"', b"L"),
    (r'''"\a\b\f\n\r\t\v\\\'\""''', b"L"),
    (r'"\0\07\377\777"', b"L"),
    (r'"\uD7ff\uE000\U0010ffff"', b"L"),
    (r'b"\x00\xff\377"', b"L"),
    (r'B"\X41\0"', b"L"),
    ('b"ASCII"', b"L"),
    (r'r"\q\uD800\x"', b"L"),
    ('u"café"', b"L"),
    (r'R"\""', b"L"),
    (r'rb"\u0000\q"', b"L"),
    (r'Br"\x\400"', b"L"),
    (r'rB"ASCII\""', b"L"),
    ('bR"ASCII"', b"L"),
    ('"café 😀"', b"L"),
    ('"a\tb"', b"L"),
    ('"a\x00b"', b"L"),
    (r'"\uD800"', b"I"),
    (r'"\uDFFF"', b"I"),
    (r'"\U0000D800"', b"I"),
    (r'"\U00110000"', b"I"),
    (r'"\UFFFFFFFF"', b"I"),
    (r'b"\u0000"', b"I"),
    (r'b"\U00000041"', b"I"),
    (r'b"\400"', b"I"),
    (r'b"\777"', b"I"),
    ('b"é"', b"I"),
    ('br"é"', b"I"),
    (r'"\q"', b"I"),
    (r'"\x0"', b"I"),
    (r'"\u123"', b"I"),
    (r'"\U0000000"', b"I"),
    (r'"\uGGGG"', b"I"),
    (r'b"\xG0"', b"I"),
    ('"unclosed', b"I"),
    ('"a\\\nnext', b"IO"),
    ('"\\u12\nnext', b"IO"),
    ('ur"ok"', b"OL"),
    ('rb_name', b"O"),
)


@pytest.fixture(scope="module")
def native_literal_lexer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    project = tmp_path_factory.mktemp("native-literal-lexer")
    source = project / "src"
    source.mkdir()
    for name in ("lexer.mlo", "syntax.mlo"):
        shutil.copyfile(ROOT / "selfhost" / "src" / name, source / name)
    (project / "merlo.toml").write_text(
        'manifest = 1\n[project]\nname = "native-literal-lexer"\n'
        'version = "0.1.0"\nedition = "alpha.1"\n',
        encoding="utf-8",
    )
    (source / "main.mlo").write_text(SOURCE, encoding="utf-8")
    resolve_dependencies(project)
    compilation = compile_project(
        project, emit_native=True, release=True,
        output=project / "literal-lexer", require_interface_lock=False,
    )
    assert compilation.native is not None and compilation.native.binary_path is not None
    return Path(compilation.native.binary_path)


@pytest.mark.parametrize(("source", "expected"), LEXEME_CASES)
def test_native_literal_validation_and_recovery(
    native_literal_lexer: Path, source: str, expected: bytes,
) -> None:
    result = subprocess.run(
        [native_literal_lexer], input=source.encode("utf-8"), capture_output=True,
        check=False, timeout=15, env={"PATH": ""},
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == expected
    assert result.stderr == b""
