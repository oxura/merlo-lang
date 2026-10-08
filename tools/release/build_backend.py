"""Setuptools packaging with stable source-archive metadata and ordering."""
from __future__ import annotations

import os
from pathlib import Path

from setuptools import build_meta as _setuptools

from tools.release.merlo.alpha_release import canonicalize_sdist

build_wheel = _setuptools.build_wheel
build_editable = _setuptools.build_editable
get_requires_for_build_wheel = _setuptools.get_requires_for_build_wheel
get_requires_for_build_sdist = _setuptools.get_requires_for_build_sdist
get_requires_for_build_editable = _setuptools.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _setuptools.prepare_metadata_for_build_editable


def build_sdist(sdist_directory: str, config_settings: dict | None = None) -> str:
    """Keep setuptools' source selection; remove host-specific tar/gzip metadata."""
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    destination = Path(sdist_directory).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    filename = _setuptools.build_sdist(str(destination), config_settings)
    canonicalize_sdist(destination / filename, epoch)
    return filename
