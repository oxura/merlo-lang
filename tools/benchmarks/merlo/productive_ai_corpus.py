"""Deterministic, unexecuted AI change-task manifest for Productive Core."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 1
CORPUS_PATH = Path("tools/benchmarks/merlo/benchmarks/merlo_productive_ai_change_corpus.json")
EXECUTION_STATUS = "NOT_EXECUTED"
REQUIRED_APPLICATION_COUNTS = {"ndjson": 6, "csv": 6, "grep": 6}
REQUIRED_TASK_FIELDS = frozenset(
    {
        "id",
        "application",
        "goal",
        "initial_revision",
        "acceptance",
        "acceptance_outputs",
        "allowed_file_scope",
        "forbidden_file_scope",
        "interface_impact",
        "effect_capability_impact",
        "execution_status",
    }
)
_SOURCE_PATHS = (
    "tools/benchmarks/merlo/productive_applications.py",
    "tools/benchmarks/merlo/tests/test_productive_applications.py",
)


@dataclass(frozen=True)
class _TaskTemplate:
    identifier: str
    application: str
    goal: str
    acceptance: str
    acceptance_outputs: tuple[str, ...]
    allowed_file_scope: tuple[str, ...]
    forbidden_file_scope: tuple[str, ...]
    interface_impact: str
    effect_capability_impact: str


_TASKS = (
    _TaskTemplate(
        "ndjson-filtered-duration-report",
        "ndjson",
        "Preserve filtered NDJSON duration aggregation while adding its regression coverage.",
        "An error-level failed-message query with a 20 ms minimum duration reports only matching valid records.",
        (
            "total=6\\nvalid=4\\ninvalid=2\\nmatching=2\\n",
            "duration_sum_ms=80\\nduration_average_ms=40\\nduration_max_ms=50\\n",
            "level error=2\\nservice api=1\\nservice db=1\\n",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_ndjson_analyzer_filters_groups_and_reports_invalid_lines",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; NdjsonOptions and NdjsonResult remain the interface.",
        "Preserves filtering, grouping, and checked duration-report capability without executing a trial.",
    ),
    _TaskTemplate(
        "ndjson-invalid-utf8",
        "ndjson",
        "Reject malformed NDJSON byte streams before line-level analysis.",
        "A stream containing an invalid UTF-8 byte raises the typed InvalidUtf8 application error.",
        ("ProductiveApplicationError: InvalidUtf8",),
        (
            "tools/benchmarks/merlo/productive_applications.py::_text_lines",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_ndjson_analyzer_rejects_invalid_utf8",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; analyze_ndjson keeps its existing error contract.",
        "Preserves deterministic rejection of non-text input; no file mutation or trial execution is allowed.",
    ),
    _TaskTemplate(
        "ndjson-cli-success",
        "ndjson",
        "Keep the NDJSON CLI adapter aligned with analyzer output for a valid filtered invocation.",
        "The valid command exits successfully, writes its report to stdout, and leaves stderr empty.",
        ("exit_code=0", "stdout contains matching=1\\n", "stderr="),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_ndjson_cli",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_productive_cli_entrypoints_execute_each_application",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_csv_cli",
            "tools/benchmarks/merlo/productive_applications.py::run_grep_cli",
            "tools/benchmarks/merlo/productive_cli.py::parse_productive_cli",
        ),
        "No public API change; run_ndjson_cli continues to return ProductiveCliRun.",
        "Preserves the command-to-report bridge and success/error stream separation without a trial run.",
    ),
    _TaskTemplate(
        "ndjson-optional-duration",
        "ndjson",
        "Maintain optional duration handling so valid matching records without duration do not corrupt duration metrics.",
        "A record with no duration remains valid and contributes no duration sample to aggregate duration output.",
        ("valid record accepted", "duration metrics use only present duration_ms values"),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson::duration handling",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_ndjson_analyzer_filters_groups_and_reports_invalid_lines",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; the optional duration_ms field remains optional.",
        "Preserves partial-record analytics without introducing an implicit zero-duration capability.",
    ),
    _TaskTemplate(
        "ndjson-uint64-boundary",
        "ndjson",
        "Protect NDJSON counters and duration accumulation from unsigned-64 overflow.",
        "A duration or counter addition beyond UInt64 is rejected through the typed application error path.",
        ("ProductiveApplicationError: CountOverflow or DurationOverflow",),
        (
            "tools/benchmarks/merlo/productive_applications.py::_checked_add",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::ndjson uint64 overflow regression",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::_checked_multiply",
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
        ),
        "No public API change; overflow remains represented by ProductiveApplicationError.",
        "Preserves bounded arithmetic and prevents wraparound; no benchmark or agent execution is permitted.",
    ),
    _TaskTemplate(
        "ndjson-insertion-order-groups",
        "ndjson",
        "Retain first-seen ordering for NDJSON level and service report groups.",
        "The report emits error before later groups and api before db for the established fixture order.",
        ("level error=2\\n", "service api=1\\nservice db=1\\n"),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson::group ordering",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_ndjson_analyzer_filters_groups_and_reports_invalid_lines",
        ),
        (
            "tools/benchmarks/merlo/deterministic_map.py",
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
        ),
        "No public API change; tuple-shaped group results retain their established order.",
        "Preserves deterministic observable report ordering without changing map implementation or running a trial.",
    ),
    _TaskTemplate(
        "csv-quoted-field-revenue",
        "csv",
        "Preserve CSV quoting support while aggregating revenue by product and region.",
        "Quoted product names containing delimiters contribute exact checked revenue to both groupings.",
        (
            "total=4\\nvalid=2\\ninvalid=2\\n",
            "quantity=5\\nrevenue_cents=850\\n",
            "revenue_by_product includes Widget, large=250 and Gadget=600",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_csv_aggregator_supports_quotes_and_checked_revenue",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; CsvOptions and CsvResult remain the interface.",
        "Preserves CSV parsing and deterministic revenue grouping without issuing a trial command.",
    ),
    _TaskTemplate(
        "csv-revenue-overflow",
        "csv",
        "Reject a CSV row whose quantity-times-unit-price revenue exceeds UInt64.",
        "The overflow fixture raises RevenueOverflow instead of wrapping or emitting a partial report.",
        ("ProductiveApplicationError: RevenueOverflow",),
        (
            "tools/benchmarks/merlo/productive_applications.py::_checked_multiply",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_csv_aggregator_detects_uint64_overflow",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::_checked_add",
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
        ),
        "No public API change; aggregate_csv continues to signal arithmetic failure with ProductiveApplicationError.",
        "Preserves checked revenue arithmetic and rejects unsafe capability expansion to wrapped totals.",
    ),
    _TaskTemplate(
        "csv-cli-success",
        "csv",
        "Keep the CSV CLI adapter aligned with aggregate_csv for a valid comma-delimited source.",
        "The command exits with zero, emits the checked revenue report, and emits no stderr text.",
        ("exit_code=0", "stdout contains revenue_cents=250\\n", "stderr="),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_csv_cli",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_productive_cli_entrypoints_execute_each_application",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_ndjson_cli",
            "tools/benchmarks/merlo/productive_applications.py::run_grep_cli",
            "tools/benchmarks/merlo/productive_cli.py::parse_productive_cli",
        ),
        "No public API change; run_csv_cli continues to return ProductiveCliRun.",
        "Preserves CSV command output routing without trial execution or changes to other applications.",
    ),
    _TaskTemplate(
        "csv-invalid-row-accounting",
        "csv",
        "Count structurally malformed and non-numeric CSV rows as invalid while retaining valid-row totals.",
        "The mixed fixture reports two valid rows, two invalid rows, quantity five, and revenue 850 cents.",
        ("valid=2\\n", "invalid=2\\n", "quantity=5\\n", "revenue_cents=850\\n"),
        (
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv::row validation",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_csv_aggregator_supports_quotes_and_checked_revenue",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; CsvResult valid and invalid counters remain the interface.",
        "Preserves partial-input aggregation without granting malformed rows a revenue effect.",
    ),
    _TaskTemplate(
        "csv-group-order",
        "csv",
        "Preserve first-seen deterministic ordering for CSV product and region revenue groups.",
        "The established fixture reports Widget, large before Gadget and north before south.",
        (
            "revenue_by_product=((Widget, large,250),(Gadget,600))",
            "revenue_by_region=((north,250),(south,600))",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv::group ordering",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_csv_aggregator_supports_quotes_and_checked_revenue",
        ),
        (
            "tools/benchmarks/merlo/deterministic_map.py",
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
        ),
        "No public API change; ordered tuple group results remain unchanged.",
        "Preserves observable insertion order without altering the deterministic map implementation or executing a trial.",
    ),
    _TaskTemplate(
        "csv-delimiter-validation",
        "csv",
        "Reject delimiters that cannot represent exactly one UTF-8 byte.",
        "An invalid delimiter fails before parsing instead of producing an ambiguous aggregation result.",
        ("ProductiveApplicationError: InvalidDelimiter",),
        (
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv::delimiter validation",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::csv invalid delimiter regression",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::search_text",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; CsvOptions.delimiter remains the sole delimiter parameter.",
        "Preserves byte-delimited CSV capability and prevents unsupported multi-byte delimiter behavior.",
    ),
    _TaskTemplate(
        "grep-ascii-ignore-case",
        "grep",
        "Keep grep-style matching ASCII-case-insensitive without applying Unicode case folding.",
        "The alpha query matches Alpha, ALPHABET, and the unterminated last alpha line only.",
        ("1:Alpha\\n3:ALPHABET\\n4:last alpha\\n", "matching_lines=3", "total_lines=4"),
        (
            "tools/benchmarks/merlo/productive_applications.py::_ascii_lower",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_grep_search_exact_ascii_ignore_case_and_unterminated_line",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; GrepOptions.ignore_case keeps ASCII-only semantics.",
        "Preserves exact text-search capability without silently expanding it to Unicode case folding or running a trial.",
    ),
    _TaskTemplate(
        "grep-count-only",
        "grep",
        "Preserve count-only grep output as one decimal count followed by one newline.",
        "The alpha ignore-case fixture emits exactly 3 followed by a newline and no matched-line text.",
        ("3\\n",),
        (
            "tools/benchmarks/merlo/productive_applications.py::search_text::count_only output",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_grep_search_exact_ascii_ignore_case_and_unterminated_line",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; GrepResult.output continues to carry count-only text.",
        "Preserves the compact count mode without granting it line-output effects or executing a trial.",
    ),
    _TaskTemplate(
        "grep-invalid-utf8",
        "grep",
        "Reject invalid UTF-8 text sources before line matching.",
        "A source containing an invalid byte raises InvalidUtf8 rather than returning partial match output.",
        ("ProductiveApplicationError: InvalidUtf8",),
        (
            "tools/benchmarks/merlo/productive_applications.py::_text_lines",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_grep_search_rejects_invalid_utf8",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; search_text retains the existing typed error contract.",
        "Preserves all-or-nothing text input validation and forbids partial-output trial behavior.",
    ),
    _TaskTemplate(
        "grep-cli-success",
        "grep",
        "Keep the grep CLI adapter aligned with case-insensitive matching output.",
        "The valid command exits zero, emits exactly the first matching line, and leaves stderr empty.",
        ("exit_code=0", "stdout=1:Alpha\\n", "stderr="),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_grep_cli",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_productive_cli_entrypoints_execute_each_application",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_ndjson_cli",
            "tools/benchmarks/merlo/productive_applications.py::run_csv_cli",
            "tools/benchmarks/merlo/productive_cli.py::parse_productive_cli",
        ),
        "No public API change; run_grep_cli continues to return ProductiveCliRun.",
        "Preserves grep command output routing while prohibiting any agent or command trial execution.",
    ),
    _TaskTemplate(
        "grep-cli-argument-error",
        "grep",
        "Keep grep CLI argument failures typed and isolated from stdout.",
        "A missing --contains value exits with code two, writes no stdout, and reports family=missing on stderr.",
        ("exit_code=2", "stdout=", "stderr contains family=missing"),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_grep_cli::error mapping",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::test_productive_cli_entrypoints_return_typed_argument_errors",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::run_ndjson_cli",
            "tools/benchmarks/merlo/productive_applications.py::run_csv_cli",
            "tools/benchmarks/merlo/productive_cli.py::parse_productive_cli::grammar",
        ),
        "No public API change; ProductiveCliRun continues to represent argument errors.",
        "Preserves deterministic error-channel behavior without modifying the shared parser or executing a trial.",
    ),
    _TaskTemplate(
        "grep-unmatched-output",
        "grep",
        "Preserve empty matched-line output when a nonempty text source has no exact matches.",
        "A no-match search returns zero matching lines and an empty normal-mode output string.",
        ("matching_lines=0", "output="),
        (
            "tools/benchmarks/merlo/productive_applications.py::search_text::no-match branch",
            "tools/benchmarks/merlo/tests/test_productive_applications.py::grep no-match regression",
        ),
        (
            "tools/benchmarks/merlo/productive_applications.py::analyze_ndjson",
            "tools/benchmarks/merlo/productive_applications.py::aggregate_csv",
            "tools/benchmarks/merlo/productive_cli.py",
        ),
        "No public API change; GrepResult keeps zero counts and text output as its interface.",
        "Preserves a non-error no-match capability without adding implicit diagnostics or running a trial.",
    ),
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def corpus_sha256(corpus: dict[str, object]) -> str:
    """Hash canonical manifest content, excluding its self-referential digest."""

    unsigned = {key: value for key, value in corpus.items() if key != "manifest_sha256"}
    return hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


def serialize_productive_ai_change_corpus(corpus: dict[str, object]) -> str:
    """Return the compact canonical representation stored in the repository."""

    return _canonical_json(corpus) + "\n"


def _source_pin(root: Path, source_path: str) -> dict[str, str]:
    path = root / source_path
    if path.is_file():
        return {
            "path": source_path,
            "kind": "CONTENT_SHA256",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "path": source_path,
        "kind": "PREREQUISITE",
        "prerequisite": "SOURCE_PATH_MUST_EXIST_BEFORE_EXECUTION",
    }


def _task_manifest(template: _TaskTemplate, root: Path) -> dict[str, object]:
    return {
        "id": template.identifier,
        "application": template.application,
        "goal": template.goal,
        "initial_revision": {
            "kind": "SOURCE_CONTENT_SHA256",
            "sources": [_source_pin(root, source_path) for source_path in _SOURCE_PATHS],
        },
        "acceptance": template.acceptance,
        "acceptance_outputs": list(template.acceptance_outputs),
        "allowed_file_scope": list(template.allowed_file_scope),
        "forbidden_file_scope": list(template.forbidden_file_scope),
        "interface_impact": template.interface_impact,
        "effect_capability_impact": template.effect_capability_impact,
        "execution_status": EXECUTION_STATUS,
    }


def generate_productive_ai_change_corpus(root: str | Path = ".") -> dict[str, object]:
    """Generate the complete 18-task AI change manifest without executing tasks."""

    root_path = Path(root)
    corpus: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "trial_execution_status": EXECUTION_STATUS,
        "tasks": [_task_manifest(template, root_path) for template in _TASKS],
    }
    corpus["manifest_sha256"] = corpus_sha256(corpus)
    return corpus


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return value


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be nonempty text")
    return value


def _text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a nonempty list")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} must contain nonempty text")
    return value


def _validate_source(source: object) -> None:
    value = _object(source, "source")
    path = _nonempty_text(value.get("path"), "source path")
    kind = _nonempty_text(value.get("kind"), "source kind")
    if kind == "CONTENT_SHA256":
        if set(value) != {"path", "kind", "sha256"}:
            raise ValueError(f"content source pin has unexpected fields: {path}")
        digest = _nonempty_text(value.get("sha256"), "source sha256")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"invalid source sha256: {path}")
        return
    if kind == "PREREQUISITE":
        if value != {
            "path": path,
            "kind": "PREREQUISITE",
            "prerequisite": "SOURCE_PATH_MUST_EXIST_BEFORE_EXECUTION",
        }:
            raise ValueError(f"invalid source prerequisite: {path}")
        return
    raise ValueError(f"unknown source pin kind: {kind}")


def _validate_task(task: object) -> str:
    value = _object(task, "task")
    missing = REQUIRED_TASK_FIELDS - value.keys()
    if missing:
        raise ValueError("task missing fields: " + ", ".join(sorted(missing)))
    identifier = _nonempty_text(value.get("id"), "task id")
    application = _nonempty_text(value.get("application"), "task application")
    if application not in REQUIRED_APPLICATION_COUNTS:
        raise ValueError(f"unknown application: {application}")
    _nonempty_text(value.get("goal"), "task goal")
    _nonempty_text(value.get("acceptance"), "task acceptance")
    _text_list(value.get("acceptance_outputs"), "task acceptance_outputs")
    allowed = _text_list(value.get("allowed_file_scope"), "task allowed_file_scope")
    forbidden = _text_list(value.get("forbidden_file_scope"), "task forbidden_file_scope")
    if set(allowed) & set(forbidden):
        raise ValueError(f"task scope overlap: {identifier}")
    _nonempty_text(value.get("interface_impact"), "task interface_impact")
    _nonempty_text(value.get("effect_capability_impact"), "task effect_capability_impact")
    if value.get("execution_status") != EXECUTION_STATUS:
        raise ValueError(f"task execution_status must be {EXECUTION_STATUS}: {identifier}")

    revision = _object(value.get("initial_revision"), "task initial_revision")
    if revision.get("kind") != "SOURCE_CONTENT_SHA256":
        raise ValueError(f"task initial revision kind: {identifier}")
    sources = revision.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"task sources must be nonempty: {identifier}")
    for source in sources:
        _validate_source(source)
    return application


def validate_productive_ai_change_corpus(corpus: dict[str, object]) -> None:
    """Reject malformed, incomplete, tampered, or executed manifests."""

    if corpus.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    if corpus.get("trial_execution_status") != EXECUTION_STATUS:
        raise ValueError(f"trial_execution_status must be {EXECUTION_STATUS}")
    digest = _nonempty_text(corpus.get("manifest_sha256"), "manifest_sha256")
    if digest != corpus_sha256(corpus):
        raise ValueError("manifest_sha256 does not match manifest content")
    tasks = corpus.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    if len(tasks) != sum(REQUIRED_APPLICATION_COUNTS.values()):
        raise ValueError("incorrect task count")

    identifiers: set[str] = set()
    counts = {application: 0 for application in REQUIRED_APPLICATION_COUNTS}
    for task in tasks:
        value = _object(task, "task")
        identifier = _nonempty_text(value.get("id"), "task id")
        if identifier in identifiers:
            raise ValueError(f"duplicate task id: {identifier}")
        identifiers.add(identifier)
        counts[_validate_task(value)] += 1
    if counts != REQUIRED_APPLICATION_COUNTS:
        raise ValueError("incorrect application task counts")


def load_productive_ai_change_corpus(path: str | Path | None = None) -> dict[str, object]:
    """Load and validate the committed canonical AI change manifest."""

    target = CORPUS_PATH if path is None else Path(path)
    corpus = _object(json.loads(target.read_text(encoding="utf-8")), "manifest")
    validate_productive_ai_change_corpus(corpus)
    return corpus


def write_productive_ai_change_corpus(
    path: str | Path | None = None,
    root: str | Path = ".",
) -> dict[str, object]:
    """Generate and replace the canonical manifest without executing a task."""

    target = CORPUS_PATH if path is None else Path(path)
    corpus = generate_productive_ai_change_corpus(root)
    validate_productive_ai_change_corpus(corpus)
    target.write_text(serialize_productive_ai_change_corpus(corpus), encoding="utf-8")
    return corpus


__all__ = [
    "CORPUS_PATH",
    "EXECUTION_STATUS",
    "REQUIRED_TASK_FIELDS",
    "corpus_sha256",
    "generate_productive_ai_change_corpus",
    "load_productive_ai_change_corpus",
    "serialize_productive_ai_change_corpus",
    "validate_productive_ai_change_corpus",
    "write_productive_ai_change_corpus",
]
