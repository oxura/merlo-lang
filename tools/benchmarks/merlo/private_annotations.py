from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from merlo.surface_ast import SurfaceFunction
from merlo.surface_parser import parse_surface


SCHEMA_VERSION = "merlo.private-annotations.report.v1"
MAX_PRIVATE_BOUNDARY_ANNOTATION_RATE = 1 / 3


def _source_digest(root: Path, paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def measure_private_annotations(root: str | Path) -> dict[str, Any]:
    """Measure explicit parameter and return types on private example functions."""
    base = Path(root).resolve()
    project_roots = tuple(
        sorted(path.parent for path in (base / "examples").glob("*/merlo.toml"))
    )
    source_paths = tuple(
        path
        for project in project_roots
        for path in sorted((project / "src").glob("*.mlo"))
    )
    functions: list[dict[str, Any]] = []
    explicit_annotations = 0
    annotation_slots = 0
    for path in source_paths:
        program = parse_surface(
            path.read_text(encoding="utf-8"),
            path=path.relative_to(base).as_posix(),
        )
        for declaration in program.declarations:
            if not isinstance(declaration, SurfaceFunction) or declaration.exported:
                continue
            parameter_annotations = sum(
                parameter.type_name is not None
                for parameter in declaration.parameters
            )
            slots = len(declaration.parameters) + 1
            annotations = parameter_annotations + (declaration.return_type is not None)
            annotation_slots += slots
            explicit_annotations += annotations
            functions.append(
                {
                    "source": path.relative_to(base).as_posix(),
                    "name": declaration.name,
                    "parameter_count": len(declaration.parameters),
                    "explicit_parameter_annotations": parameter_annotations,
                    "explicit_return_annotation": declaration.return_type is not None,
                    "annotation_slots": slots,
                    "explicit_annotations": annotations,
                }
            )
    rate = explicit_annotations / annotation_slots if annotation_slots else 0.0
    gates = {
        "projects_measured_at_least_15": len(project_roots) >= 15,
        "private_boundary_annotation_rate_at_most_one_third": (
            rate <= MAX_PRIVATE_BOUNDARY_ANNOTATION_RATE
        ),
    }
    return {
        "schema": SCHEMA_VERSION,
        "source_sha256": _source_digest(base, source_paths),
        "project_count": len(project_roots),
        "source_file_count": len(source_paths),
        "private_function_count": len(functions),
        "annotation_slots": annotation_slots,
        "explicit_annotations": explicit_annotations,
        "annotation_rate": rate,
        "gates": gates,
        "passed": all(gates.values()),
        "functions": functions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure private Merlo function boundary annotations."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    arguments = parser.parse_args()
    print(json.dumps(measure_private_annotations(arguments.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
