"""Verify frozen Meldra-era evidence after the package-only Merlo rename."""

from __future__ import annotations

import hashlib
from pathlib import Path


_LEGACY_PACKAGE_PREFIX = "meldra/"
_FROZEN_ARCHIVE = Path("benchmarks/frozen/stage04")
_ACTIVE_PACKAGE_PREFIX = "merlo/"
_BRAND_NORMALIZATION = (
    (b".mlo", b".meldra"),
    (b"MERLO", b"MELDRA"),
    (b"Merlo", b"Meldra"),
    (b"merlo", b"meldra"),
)

_INDEPENDENT_CORPUS_IMPORT = b"from .legacy_evidence import frozen_sha256\n"
_INDEPENDENT_CORPUS_CURRENT = (
    b'        "harness_sha256": frozen_sha256(\n'
    b'            root_path, "meldra/independent_corpus.py"\n'
    b"        ),\n"
)
_INDEPENDENT_CORPUS_FROZEN = (
    b'        "harness_sha256": _sha256_bytes(\n'
    b'            (root_path / "meldra" / "independent_corpus.py").read_bytes()\n'
    b"        ),\n"
)

_INDEPENDENT_CORPUS_RELOCATED_DEFAULT = (
    b'def verify_independent_corpus_lock(root: str | Path = Path(__file__).resolve().parents[1])'
)
_INDEPENDENT_CORPUS_FROZEN_DEFAULT = (
    b'def verify_independent_corpus_lock(root: str | Path = ".")'
)


def resolve_frozen_path(root: str | Path, recorded_path: str) -> Path:
    root_path = Path(root)
    archived_path = root_path / _FROZEN_ARCHIVE / recorded_path
    if archived_path.is_file():
        return archived_path
    if recorded_path.startswith(_LEGACY_PACKAGE_PREFIX):
        recorded_path = _ACTIVE_PACKAGE_PREFIX + recorded_path[len(_LEGACY_PACKAGE_PREFIX) :]
    return root_path / recorded_path


def frozen_sha256(root: str | Path, recorded_path: str) -> str:
    path = resolve_frozen_path(root, recorded_path)
    payload = path.read_bytes()
    if recorded_path == "meldra/independent_corpus.py":
        payload = payload.replace(_INDEPENDENT_CORPUS_IMPORT, b"")
        payload = payload.replace(
            _INDEPENDENT_CORPUS_CURRENT, _INDEPENDENT_CORPUS_FROZEN
        )
        payload = payload.replace(
            _INDEPENDENT_CORPUS_RELOCATED_DEFAULT, _INDEPENDENT_CORPUS_FROZEN_DEFAULT
        )
    if recorded_path.startswith(_LEGACY_PACKAGE_PREFIX) or recorded_path.startswith("tests/"):
        for current, historical in _BRAND_NORMALIZATION:
            payload = payload.replace(current, historical)
    return hashlib.sha256(payload).hexdigest()


__all__ = ["frozen_sha256", "resolve_frozen_path"]
