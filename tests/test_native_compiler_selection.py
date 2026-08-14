from __future__ import annotations

import os
import sys
from pathlib import Path

from merlo import native_c_backend


def test_override_resolves_real_executable(monkeypatch) -> None:
    executable = os.environ.get("PYTHON", sys.executable)
    monkeypatch.setenv("MERLO_C_COMPILER", executable)

    resolved = native_c_backend.find_c_compiler()

    assert resolved == native_c_backend.shutil.which(executable)
    assert resolved is not None
    assert Path(resolved).is_file()


def test_explicit_preference_overrides_environment(monkeypatch) -> None:
    monkeypatch.setenv("MERLO_C_COMPILER", "clang-from-env")
    monkeypatch.setattr(
        native_c_backend.shutil,
        "which",
        lambda candidate: f"/usr/bin/{candidate}",
    )

    assert native_c_backend.find_c_compiler("gcc") == "/usr/bin/gcc"


def test_failed_override_returns_controlled_result_with_version(tmp_path, monkeypatch) -> None:
    compiler = tmp_path / "nonstandard-cc"
    compiler.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'nonstandard-cc 9.1' >&2\nexit 1\n",
        encoding="utf-8",
    )
    compiler.chmod(0o755)
    monkeypatch.setenv("MERLO_C_COMPILER", str(compiler))

    result = native_c_backend.compile_c_source(
        "int main(void) { return 0; }\n",
        output_dir=tmp_path / "build",
    )

    assert result.status == "FAILED"
    assert result.compiler == str(compiler)
    assert result.compiler_version == "nonstandard-cc 9.1"
    assert result.binary_path is None
    assert result.stderr == "nonstandard-cc 9.1\n"
