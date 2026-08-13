from __future__ import annotations

import hashlib
import json
from pathlib import Path


EXTERNAL_FIXTURE_MANIFEST = "benchmarks/merlo_productive_external_fixtures.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def verify_productive_external_fixtures(
    root: str | Path = ".",
) -> dict[str, object]:
    root_path = Path(root).resolve()
    manifest_path = root_path / EXTERNAL_FIXTURE_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    claimed_payload_sha256 = str(manifest.get("payload_sha256", ""))
    payload = dict(manifest)
    payload.pop("payload_sha256", None)
    payload_sha256 = hashlib.sha256(_canonical(payload)).hexdigest()
    fixtures = manifest.get("fixtures", [])
    if not isinstance(fixtures, list):
        raise ValueError("external fixture manifest fixtures must be a list")
    counts = {"csv": 0, "ndjson": 0, "text": 0}
    unmeasured = []
    observations = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            unmeasured.append({"reason": "fixture entry is not an object"})
            continue
        relative_path = str(fixture.get("path", ""))
        path = root_path / relative_path
        parts = Path(relative_path).parts
        format_name = parts[-2] if len(parts) >= 2 else ""
        if format_name in counts:
            counts[format_name] += 1
        observed_sha256 = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else "MISSING"
        )
        source_url = str(fixture.get("source_url", ""))
        provenance = str(fixture.get("provenance", ""))
        measured = (
            observed_sha256 == fixture.get("sha256")
            and len(str(fixture.get("source_sha256", ""))) == 64
            and source_url.startswith("https://raw.githubusercontent.com/")
            and provenance
            in {"pinned_external", "derived_from_pinned_external"}
        )
        observation = {
            **fixture,
            "observed_sha256": observed_sha256,
            "measured": measured,
        }
        observations.append(observation)
        if not measured:
            unmeasured.append(observation)
    passed = (
        claimed_payload_sha256 == payload_sha256
        and counts == {"csv": 2, "ndjson": 2, "text": 2}
        and not unmeasured
    )
    return {
        "passed": passed,
        "manifest": EXTERNAL_FIXTURE_MANIFEST,
        "payload_sha256": payload_sha256,
        "claimed_payload_sha256": claimed_payload_sha256,
        "counts": counts,
        "fixtures": observations,
        "unmeasured": unmeasured,
    }


__all__ = [
    "EXTERNAL_FIXTURE_MANIFEST",
    "verify_productive_external_fixtures",
]
