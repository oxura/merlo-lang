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
                if token.kind == syntax.token_number_kind():
                    output.append_text("N")
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

NUMERIC_CASES = (
    ("0", b"N"),
    ("18446744073709551615", b"N"),
    ("18446744073709551616", b"N"),
    ("1_000", b"N"),
    ("0xFF", b"N"),
    ("0X_fF", b"N"),
    ("0b101_011", b"N"),
    ("0B_1", b"N"),
    ("0o755", b"N"),
    ("0O_7", b"N"),
    ("1.25", b"N"),
    ("1.", b"N"),
    (".5", b"N"),
    ("1e3", b"N"),
    ("1E-3", b"N"),
    ("1e+3", b"N"),
    ("0.5e+9", b"N"),
    ("1_e_2", b"N"),
    ("1_._2", b"N"),
    ("1e+_2", b"N"),
    ("1._e2", b"N"),
    ("00_1", b"N"),
    ("08", b"N"),
    ("1e9999", b"N"),
    ("0x", b"I"),
    ("0x_", b"I"),
    ("0x__1", b"I"),
    ("0x1_", b"I"),
    ("0x1G", b"I"),
    ("0b2", b"I"),
    ("0o8", b"I"),
    ("0b10__01", b"I"),
    ("1__2", b"I"),
    ("1_", b"I"),
    ("1e", b"I"),
    ("1e+", b"I"),
    ("1e_", b"I"),
    (".1.2", b"I"),
    ("1.2_", b"I"),
    ("1e2__3", b"I"),
    ("1._", b"I"),
    ("1e-_", b"I"),
    ("12name", b"NO"),
    ("1..2", b"NN"),
    ("1.2.3", b"NN"),
    ("1.2foo", b"NO"),
    ("1e2name", b"NO"),
    ("0x10.foo", b"NOO"),
    ("0x10.2", b"NN"),
    ("0x1+2", b"NON"),
    ("1 + 2", b"NON"),
    ("1 # tail\r\n2", b"NN"),
    ("-1", b"ON"),
    (".", b"O"),
    ("_1", b"O"),
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


@pytest.mark.parametrize(("source", "expected"), LEXEME_CASES + NUMERIC_CASES)
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
