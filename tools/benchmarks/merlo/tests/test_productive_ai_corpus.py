from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from tools.benchmarks.merlo.productive_ai_corpus import (
    CORPUS_PATH,
    EXECUTION_STATUS,
    REQUIRED_TASK_FIELDS,
    corpus_sha256,
    generate_productive_ai_change_corpus,
    load_productive_ai_change_corpus,
    validate_productive_ai_change_corpus,
)


ROOT = Path(__file__).parents[4]
EXPECTED_APPLICATION_COUNTS = {"ndjson": 6, "csv": 6, "grep": 6}
ENVIRONMENT_PATH = Path(
    "tools/benchmarks/merlo/benchmarks/merlo_productive_ai_environment.json"
)


def test_committed_manifest_is_the_deterministic_template_output() -> None:
    committed = load_productive_ai_change_corpus(ROOT / CORPUS_PATH)
    first = generate_productive_ai_change_corpus(ROOT)
    second = generate_productive_ai_change_corpus(ROOT)

    assert first == second == committed
    assert committed["manifest_sha256"] == corpus_sha256(committed)


def test_manifest_contains_six_unique_not_executed_tasks_per_application() -> None:
    manifest = load_productive_ai_change_corpus(ROOT / CORPUS_PATH)
    tasks = manifest["tasks"]

    assert len(tasks) == 18
    assert len({task["id"] for task in tasks}) == 18
    assert Counter(task["application"] for task in tasks) == EXPECTED_APPLICATION_COUNTS
    assert manifest["trial_execution_status"] == EXECUTION_STATUS
    assert all(task["execution_status"] == EXECUTION_STATUS for task in tasks)


def test_tasks_have_complete_non_overlapping_change_contracts() -> None:
    manifest = load_productive_ai_change_corpus(ROOT / CORPUS_PATH)

    for task in manifest["tasks"]:
        assert REQUIRED_TASK_FIELDS <= task.keys()
        assert task["goal"]
        assert task["acceptance"]
        assert task["allowed_file_scope"]
        assert task["forbidden_file_scope"]
        assert task["interface_impact"]
        assert task["effect_capability_impact"]
        assert not set(task["allowed_file_scope"]) & set(task["forbidden_file_scope"])


def test_source_pins_match_present_sources_or_record_prerequisites() -> None:
    manifest = load_productive_ai_change_corpus(ROOT / CORPUS_PATH)

    for task in manifest["tasks"]:
        revision = task["initial_revision"]
        assert revision["kind"] == "SOURCE_CONTENT_SHA256"
        assert revision["sources"]
        for source in revision["sources"]:
            path = ROOT / source["path"]
            if path.is_file():
                assert source == {
                    "path": source["path"],
                    "kind": "CONTENT_SHA256",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            else:
                assert source == {
                    "path": source["path"],
                    "kind": "PREREQUISITE",
                    "prerequisite": "SOURCE_PATH_MUST_EXIST_BEFORE_EXECUTION",
                }


def test_ai_environment_lock_matches_its_recipe_and_runtime_policy() -> None:
    environment = json.loads((ROOT / ENVIRONMENT_PATH).read_text(encoding="utf-8"))
    build = environment["build"]
    image = environment["image"]

    assert environment["status"] == "LOCKED_BEFORE_EXECUTION"
    assert build["source_date_epoch"] == 0
    assert build["dockerfile_sha256"] == hashlib.sha256(
        (ROOT / "tools/benchmarks/merlo/ai_experiment.Dockerfile").read_bytes()
    ).hexdigest()
    assert build["requirements_sha256"] == hashlib.sha256(
        (
            ROOT / "tools/benchmarks/merlo/ai_experiment_requirements.txt"
        ).read_bytes()
    ).hexdigest()
    assert image["base"].startswith("docker.io/library/python:3.14.1-slim@sha256:")
    assert image["oci_manifest_digest"].startswith("sha256:")
    assert image["oci_archive_sha256"] == (
        "4df511f21344c28e61b0ecb03ef81eb41180d38dbedce7c8f807d0e484ed477c"
    )
    assert environment["runtime_policy"] == {
        "network": "none",
        "root_filesystem": "read_only",
        "workspace_mount": "read_only",
        "temporary_filesystem": "/tmp",
    }


def test_validation_rejects_scope_overlap_digest_tampering_and_execution() -> None:
    manifest = generate_productive_ai_change_corpus(ROOT)
    overlap = copy.deepcopy(manifest)
    overlap["tasks"][0]["forbidden_file_scope"].append(
        overlap["tasks"][0]["allowed_file_scope"][0]
    )
    overlap["manifest_sha256"] = corpus_sha256(overlap)

    with pytest.raises(ValueError, match="scope"):
        validate_productive_ai_change_corpus(overlap)

    tampered = copy.deepcopy(manifest)
    tampered["tasks"][0]["goal"] = "tampered"

    with pytest.raises(ValueError, match="manifest_sha256"):
        validate_productive_ai_change_corpus(tampered)

    executed = copy.deepcopy(manifest)
    executed["tasks"][0]["execution_status"] = "EXECUTED"
    executed["manifest_sha256"] = corpus_sha256(executed)

    with pytest.raises(ValueError, match="execution_status"):
        validate_productive_ai_change_corpus(executed)
