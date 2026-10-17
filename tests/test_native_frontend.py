from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.project import resolve_dependencies


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def native_lexer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    project = tmp_path_factory.mktemp("native-lexer")
    source = project / "src"
    source.mkdir()
    for name in ("lexer.mlo", "syntax.mlo"):
        shutil.copyfile(ROOT / "selfhost" / "src" / name, source / name)
    (project / "merlo.toml").write_text(
        'manifest = 1\n[project]\nname = "native-lexer"\n'
        'version = "0.1.0"\nedition = "alpha.1"\n',
        encoding="utf-8",
    )
    (source / "main.mlo").write_text(
        '''module main
use lexer

export main(source: Text) -> Text:
    tokens = lexer.lex(source.clone())
    output: TextBuilder = TextBuilder.new()
    expected: UInt64 = 0
    index: UInt64 = 0
    while index < tokens.len():
        token = tokens.get(index)
        if token.length > 0:
            if token.start != expected:
                return "noncontiguous token spans"
            output.append_text(source.as_view().slice_bytes(token.start, token.length).to_text())
            expected = token.start + token.length
        index = index + 1
    if expected != source.len():
        return "truncated token stream"
    rebuilt = output.finish()
    console.write(rebuilt.as_view())
    ""
''',
        encoding="utf-8",
    )
    resolve_dependencies(project)
    build = compile_project(
        project,
        emit_native=True,
        release=True,
        output=project / "lexer-probe",
        require_interface_lock=False,
    )
    assert build.native is not None and build.native.binary_path is not None
    return Path(build.native.binary_path)


@pytest.mark.parametrize(
    "source",
    (
        "",
        "\r\n\n",
        " \t",
        "# no terminal newline",
        "  # comment\r\nmodule probe\n\n",
        "module probe  # declaration\r\n\nmain(input: Text) -> Text:\n    input\n",
        "  \t#\tcomment\n",
        'module p\nmain(input: Text) -> Text:\n    "# not a comment" # tail\n',
        "# café 🌍\r\n  # комментарий",
        "abc @ xyz",
        "   invalid_indent\n",
    ),
)
def test_native_lexer_preserves_every_source_byte(
    native_lexer: Path, source: str,
) -> None:
    payload = source.encode("utf-8")
    completed = subprocess.run(
        [str(native_lexer)], input=payload, capture_output=True, check=False,
        timeout=15, env={"PATH": ""},
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stdout == payload
    assert completed.stderr == b""
