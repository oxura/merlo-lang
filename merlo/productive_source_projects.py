from __future__ import annotations

import hashlib
import re
from pathlib import Path


PRODUCTIVE_PROJECTS = {
    "ndjson": (
        "merlo/programs/productive_ndjson/app/main.mlo",
        "merlo/programs/productive_ndjson/app/report.mlo",
    ),
    "csv": (
        "merlo/programs/productive_csv/app/main.mlo",
        "merlo/programs/productive_csv/app/sales.mlo",
    ),
    "grep": (
        "merlo/programs/productive_grep/app/main.mlo",
        "merlo/programs/productive_grep/app/search.mlo",
    ),
}


def validate_productive_source_projects(
    root: str | Path = ".",
) -> dict[str, object]:
    root_path = Path(root).resolve()
    applications = []
    for name in ("ndjson", "csv", "grep"):
        paths = PRODUCTIVE_PROJECTS[name]
        sources = [
            (root_path / path).read_text(encoding="utf-8")
            for path in paths
        ]
        joined = "\n".join(sources)
        opaque_helpers = sorted(
            set(
                re.findall(
                    r"\b(?:ndjson|csv|grep)_(?:parse|analyze|aggregate|search)\b",
                    joined,
                )
            )
        )
        applications.append(
            {
                "name": name,
                "modules": list(paths),
                "module_count": len(paths),
                "source_sha256": hashlib.sha256(joined.encode()).hexdigest(),
                "dynamic_any": len(re.findall(r"\bAny\b", joined)),
                "manual_resource_operations": len(
                    re.findall(r"\b(?:malloc|free|fclose)\s*\(", joined)
                ),
                "domain_opaque_c_helpers": opaque_helpers,
                "reuses_general_json_parser": (
                    name == "ndjson" and "use app.json" in joined
                ),
            }
        )
    return {
        "applications": applications,
        "passed": (
            all(item["module_count"] >= 2 for item in applications)
            and all(item["dynamic_any"] == 0 for item in applications)
            and all(item["manual_resource_operations"] == 0 for item in applications)
            and all(not item["domain_opaque_c_helpers"] for item in applications)
            and applications[0]["reuses_general_json_parser"] is True
        ),
    }


__all__ = [
    "PRODUCTIVE_PROJECTS",
    "validate_productive_source_projects",
]
