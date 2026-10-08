from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_archive_preserves_payload_and_ignores_host_mtimes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "src" / "archive_probe"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "archive-probe"\nversion = "1.0"\n'
        '[tool.setuptools]\npackage-dir = {"" = "src"}\n'
        '[tool.setuptools.package-data]\narchive_probe = ["payload.bin"]\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text('VALUE = "Мерло"\n', encoding="utf-8")
    payload = b"\x00\xff\x01archive payload\x00"
    (package / "payload.bin").write_bytes(payload)
    environment = dict(os.environ, PYTHONPATH=str(ROOT), SOURCE_DATE_EPOCH="946684800")
    archives = []
    for index in range(2):
        output = tmp_path / f"build-{index}"
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; from tools.release.build_backend import build_sdist; "
             "build_sdist(sys.argv[1])", str(output)],
            cwd=project, env=environment, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        archives.append(next(output.glob("*.tar.gz")))
        os.utime(package / "__init__.py", (1800000000, 1800000000))
        os.utime(package / "payload.bin", (1800000001, 1800000001))
    assert hashlib.sha256(archives[0].read_bytes()).digest() == hashlib.sha256(archives[1].read_bytes()).digest()
    with tarfile.open(archives[1]) as archive:
        source = archive.extractfile("archive_probe-1.0/src/archive_probe/__init__.py")
        data = archive.extractfile("archive_probe-1.0/src/archive_probe/payload.bin")
        assert source is not None and source.read() == 'VALUE = "Мерло"\n'.encode()
        assert data is not None and data.read() == payload
