"""Deterministic generated fixtures for the Merlo Productive Core."""

from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
CORPUS_PATH = Path("tools/benchmarks/merlo/benchmarks/merlo_productive_corpus.json")
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_MERLO_SOURCE_BYTES = 4 * 1024

FIXED_SEEDS = {
    "ndjson": 101_000_000,
    "csv": 202_000_000,
    "grep": 303_000_000,
    "merlo": 404_000_000,
}

_VALID_FAMILIES = (
    "empty",
    "one-line",
    "large",
    "unicode",
    "map-collision",
    "map-growth",
    "map-duplicate",
)
_INVALID_FAMILIES = {
    "ndjson": (
        "invalid-utf8",
        "early-parse-error",
        "late-parse-error",
        "cli-failure",
    ),
    "csv": (
        "invalid-utf8",
        "early-parse-error",
        "late-parse-error",
        "cli-failure",
    ),
    "grep": ("invalid-utf8", "cli-failure"),
}
_MERLO_FAMILIES = (
    "missing-capability",
    "pure-effect-violation",
    "close-every-exit",
    "stale-line-view",
    "private-module-access",
    "cyclic-import",
    "public-interface-drift",
)

REQUIRED_FAMILIES = frozenset(
    (*_VALID_FAMILIES, *{item for values in _INVALID_FAMILIES.values() for item in values}, *_MERLO_FAMILIES)
)
REQUIRED_COUNTS = {
    ("ndjson", True): 200,
    ("ndjson", False): 120,
    ("csv", True): 200,
    ("csv", False): 120,
    ("grep", True): 200,
    ("grep", False): 120,
    ("merlo", False): 400,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def corpus_sha256(corpus: dict[str, Any]) -> str:
    """Hash canonical JSON for the complete corpus except its digest field."""

    unsigned = dict(corpus)
    unsigned.pop("sha256", None)
    return hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


def serialize_productive_corpus(corpus: dict[str, Any]) -> str:
    """Return the compact canonical on-disk representation."""

    return _canonical_json(corpus) + "\n"


def _balanced_families(families: tuple[str, ...], count: int) -> tuple[str, ...]:
    return tuple(families[index % len(families)] for index in range(count))


def _template_id(kind: str, family: str) -> str:
    return f"{kind}-{family}-v1"


def _case_plan() -> tuple[tuple[str, bool, int, str, str, int], ...]:
    plan: list[tuple[str, bool, int, str, str, int]] = []
    for kind in ("ndjson", "csv", "grep"):
        groups = ((True, 200, _VALID_FAMILIES), (False, 120, _INVALID_FAMILIES[kind]))
        ordinal = 0
        for validity, count, families in groups:
            for index, family in enumerate(_balanced_families(families, count)):
                plan.append(
                    (
                        kind,
                        validity,
                        index,
                        family,
                        _template_id(kind, family),
                        FIXED_SEEDS[kind] + ordinal,
                    )
                )
                ordinal += 1
    for index, family in enumerate(_balanced_families(_MERLO_FAMILIES, 400)):
        plan.append(
            (
                "merlo",
                False,
                index,
                family,
                _template_id("merlo", family),
                FIXED_SEEDS["merlo"] + index,
            )
        )
    return tuple(plan)


def _template_metadata() -> list[dict[str, Any]]:
    templates = {
        (template, family, kind, validity)
        for kind, validity, _, family, template, _ in _case_plan()
    }
    return [
        {"id": template, "family": family, "kind": kind, "validity": validity}
        for template, family, kind, validity in sorted(templates)
    ]


def _fnv1a64(value: str) -> int:
    result = 0xCBF29CE484222325
    for byte in value.encode("utf-8"):
        result ^= byte
        result = (result * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return result


def _collision_keys(seed: int, count: int) -> list[str]:
    bucket = seed & 7
    keys: list[str] = []
    candidate = 0
    while len(keys) < count:
        key = f"collision-{seed}-{candidate}"
        if _fnv1a64(key) & 7 == bucket:
            keys.append(key)
        candidate += 1
    return keys


def _json_record(seed: int, *, service: str | None = None, message: str | None = None) -> str:
    record = {
        "duration_ms": seed % 997,
        "level": ("info", "warn", "error")[seed % 3],
        "message": message or f"generated event {seed}",
        "service": service or f"service-{seed % 23}",
        "timestamp": f"2026-08-{seed % 28 + 1:02d}T00:{seed % 60:02d}:00Z",
    }
    return _canonical_json(record)


def _ndjson_payload(family: str, seed: int) -> tuple[bytes, dict[str, Any]]:
    if family == "empty":
        return b"", {"outcome": "success", "summary": "zero input records"}
    if family == "one-line":
        payload = _json_record(seed).encode("utf-8") + b"\n"
        return payload, {"outcome": "success", "summary": "one valid event record"}
    if family == "unicode":
        line = _json_record(seed, service="münchen-服务", message=f"café Δοκιμή 雪 {seed}")
        return (line + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "one Unicode event with code points preserved",
        }
    if family == "large":
        lines = [
            _json_record(seed + index, message=f"batch-{seed}-" + "x" * 96)
            for index in range(64)
        ]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "64 bounded event records",
        }
    if family == "map-collision":
        lines = [_json_record(seed + index, service=key) for index, key in enumerate(_collision_keys(seed, 8))]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "eight service keys sharing the initial map bucket remain distinct",
        }
    if family == "map-growth":
        lines = [_json_record(seed + index, service=f"growth-{seed}-{index}") for index in range(48)]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "48 distinct service keys survive deterministic map growth",
        }
    if family == "map-duplicate":
        lines = [_json_record(seed + index, service=f"duplicate-{seed}") for index in range(12)]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "12 duplicate service keys aggregate into one insertion-order entry",
        }
    if family == "invalid-utf8":
        return _json_record(seed).encode("utf-8") + b"\n\xff\n", {
            "outcome": "InvalidUtf8",
            "summary": "invalid UTF-8 is rejected without replacement decoding",
        }
    if family == "early-parse-error":
        return b'{"timestamp":\n' + _json_record(seed).encode("utf-8") + b"\n", {
            "outcome": "invalid-record",
            "summary": "malformed first record is reported before a valid record",
        }
    if family == "late-parse-error":
        return _json_record(seed).encode("utf-8") + b'\n{"message":"unterminated', {
            "outcome": "invalid-record",
            "summary": "malformed final record is reported after a valid prefix",
        }
    if family == "cli-failure":
        return _json_record(seed).encode("utf-8") + b"\n", {
            "invocation": ["events.ndjson", "--level"],
            "outcome": "exit-2",
            "summary": "CLI rejects a missing level value before reading input",
        }
    raise AssertionError(f"unknown NDJSON family: {family}")


def _csv_header() -> str:
    return "date,product,region,quantity,unit_price_cents"


def _csv_row(seed: int, *, product: str | None = None, region: str | None = None) -> str:
    return (
        f"2026-08-{seed % 28 + 1:02d},"
        f"{product or f'product-{seed % 29}'},"
        f"{region or f'region-{seed % 17}'},{seed % 9 + 1},{seed % 900 + 100}"
    )


def _csv_payload(family: str, seed: int) -> tuple[bytes, dict[str, Any]]:
    header = _csv_header()
    if family == "empty":
        return b"", {"outcome": "success", "summary": "zero CSV records"}
    if family == "one-line":
        return (header + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "header-only one-line CSV has zero data records",
        }
    if family == "unicode":
        row = _csv_row(seed, product=f'"Crème, 雪-{seed}"', region="süd")
        return (header + "\n" + row + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "quoted Unicode product and region are preserved",
        }
    if family == "large":
        rows = [_csv_row(seed + index, product=f'"bulk {seed}, item {index}"') for index in range(64)]
        return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "64 bounded quoted sales records",
        }
    if family == "map-collision":
        rows = [_csv_row(seed + index, product=key) for index, key in enumerate(_collision_keys(seed, 8))]
        return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "eight product keys sharing the initial map bucket remain distinct",
        }
    if family == "map-growth":
        rows = [_csv_row(seed + index, product=f"growth-{seed}-{index}") for index in range(48)]
        return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "48 distinct product keys survive deterministic map growth",
        }
    if family == "map-duplicate":
        rows = [_csv_row(seed + index, product=f"duplicate-{seed}") for index in range(12)]
        return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "12 duplicate product keys aggregate into one insertion-order entry",
        }
    if family == "invalid-utf8":
        return (header + "\n").encode("utf-8") + b"\xff,broken\n", {
            "outcome": "InvalidUtf8",
            "summary": "invalid UTF-8 is rejected without replacement decoding",
        }
    if family == "early-parse-error":
        payload = header + f'\n2026-08-01,"unterminated-{seed},north,1,100\n'
        return payload.encode("utf-8"), {
            "outcome": "invalid-record",
            "summary": "unterminated quoted field appears in the first data record",
        }
    if family == "late-parse-error":
        payload = header + "\n" + _csv_row(seed) + f'\n2026-08-02,"unterminated-{seed}'
        return payload.encode("utf-8"), {
            "outcome": "invalid-record",
            "summary": "unterminated quoted field appears after a valid data record",
        }
    if family == "cli-failure":
        return (header + "\n").encode("utf-8"), {
            "invocation": ["sales.csv", "--delimiter"],
            "outcome": "exit-2",
            "summary": "CLI rejects a missing delimiter value before reading input",
        }
    raise AssertionError(f"unknown CSV family: {family}")


def _grep_payload(family: str, seed: int) -> tuple[bytes, dict[str, Any]]:
    if family == "empty":
        return b"", {"outcome": "success", "summary": "zero text lines and zero matches"}
    if family == "one-line":
        return f"needle-{seed}\n".encode("utf-8"), {
            "outcome": "success",
            "summary": "one terminated matching line",
        }
    if family == "unicode":
        return f"café Δοκιμή 雪 needle-{seed}\n".encode("utf-8"), {
            "outcome": "success",
            "summary": "Unicode line content is preserved in the match",
        }
    if family == "large":
        lines = [f"{index}:needle-{seed}:" + "x" * 96 for index in range(96)]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "96 bounded matching lines retain stable line numbers",
        }
    if family == "map-collision":
        lines = [f"{key}:needle-{seed}" for key in _collision_keys(seed, 8)]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "eight colliding line labels remain distinct",
        }
    if family == "map-growth":
        lines = [f"growth-{seed}-{index}:needle" for index in range(48)]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "48 distinct line labels survive deterministic map growth",
        }
    if family == "map-duplicate":
        lines = [f"duplicate-{seed}:needle" for _ in range(12)]
        return ("\n".join(lines) + "\n").encode("utf-8"), {
            "outcome": "success",
            "summary": "12 duplicate matching lines retain all source line numbers",
        }
    if family == "invalid-utf8":
        return f"needle-{seed}\n".encode("utf-8") + b"\xff\n", {
            "outcome": "InvalidUtf8",
            "summary": "invalid UTF-8 is rejected without replacement decoding",
        }
    if family == "cli-failure":
        return f"needle-{seed}\n".encode("utf-8"), {
            "invocation": ["input.txt", "--contains"],
            "outcome": "exit-2",
            "summary": "CLI rejects a missing search value before reading input",
        }
    raise AssertionError(f"unknown grep family: {family}")


def _merlo_source(family: str, seed: int) -> tuple[str, dict[str, Any]]:
    suffix = str(seed)
    if family == "missing-capability":
        source = f"""module corpus.missing_capability_{suffix}

enum CorpusError:
    IoFailure: Text

export task load(path: Path) -> Result[Bytes, CorpusError]:
    data = fs.read(path)?
    return Ok(data)
"""
        outcome = "MissingCapability"
        summary = "task performs fs.read without declaring the capability"
    elif family == "pure-effect-violation":
        source = f"""module corpus.pure_effect_{suffix}

export pure fn announce(value: Text) -> Text:
    console.write(value)
    return value
"""
        outcome = "PureEffectViolation"
        summary = "pure function attempts a console effect"
    elif family == "close-every-exit":
        source = f"""module corpus.close_exit_{suffix}

enum CorpusError:
    IoFailure: Text

export task read(path: Path, stop: Bool) -> Result[UInt64, CorpusError]:
    uses fs.open
    reader = fs.open(path)?
    if stop:
        return Ok(0)
    reader.close()
    return Ok(1)
"""
        outcome = "ResourceNotClosed"
        summary = "owned reader is not closed on the early-return path"
    elif family == "stale-line-view":
        source = f"""module corpus.stale_line_{suffix}

enum CorpusError:
    IoFailure: Text

export task inspect(path: Path) -> Result[Text, CorpusError]:
    uses fs.open
    reader = fs.open(path)?
    first = reader.next_line()?
    reader.next_line()?
    result = first.to_text()
    reader.close()
    return Ok(result)
"""
        outcome = "StaleLineView"
        summary = "borrowed line view is used after the reader advances"
    elif family == "private-module-access":
        source = f"""module corpus.private_access_{suffix}
use corpus.internal.secret

export pure fn reveal() -> UInt64:
    return secret.private_value()
"""
        outcome = "PrivateModuleAccess"
        summary = "program imports a private module from outside its package boundary"
    elif family == "cyclic-import":
        source = f"""// file: corpus/cycle_a_{suffix}.mlo
module corpus.cycle_a_{suffix}
use corpus.cycle_b_{suffix}
export pure fn a() -> UInt64:
    return cycle_b_{suffix}.b()
// file: corpus/cycle_b_{suffix}.mlo
module corpus.cycle_b_{suffix}
use corpus.cycle_a_{suffix}
export pure fn b() -> UInt64:
    return cycle_a_{suffix}.a()
"""
        outcome = "CyclicImport"
        summary = "two generated modules import each other"
    elif family == "public-interface-drift":
        source = f"""module corpus.interface_drift_{suffix}

export pure fn total(value: UInt64) -> UInt64:
    return value
export pure fn total(value: Text) -> Text:
    return value
"""
        outcome = "PublicInterfaceDrift"
        summary = "implementation changes the frozen public total signature"
    else:
        raise AssertionError(f"unknown Merlo family: {family}")
    return source, {"outcome": outcome, "summary": summary}


def _make_case(kind: str, validity: bool, index: int, family: str, template: str, seed: int) -> dict[str, Any]:
    label = "valid" if validity else "invalid"
    case: dict[str, Any] = {
        "id": f"{kind}-{label}-{index:03d}",
        "kind": kind,
        "family": family,
        "template": template,
        "seed": seed,
        "validity": validity,
        "provenance": "generated",
    }
    if kind == "merlo":
        source, expected = _merlo_source(family, seed)
        case["merlo_source"] = source
    else:
        builder = {"ndjson": _ndjson_payload, "csv": _csv_payload, "grep": _grep_payload}[kind]
        payload, expected = builder(family, seed)
        case["payload_b64"] = base64.b64encode(payload).decode("ascii")
    case["expected"] = expected
    return case


def generate_productive_corpus() -> dict[str, Any]:
    """Generate the complete Productive Core corpus deterministically."""

    cases = [_make_case(*entry) for entry in _case_plan()]
    corpus: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": "merlo-productive-core",
        "provenance": "generated",
        "digest_scope": "canonical-json-without-sha256",
        "generator": {
            "module": "merlo.productive_corpus",
            "version": SCHEMA_VERSION,
            "seeds": dict(FIXED_SEEDS),
            "families": sorted(REQUIRED_FAMILIES),
            "templates": _template_metadata(),
        },
        "counts": {
            "csv": {"valid": 200, "invalid": 120},
            "grep": {"valid": 200, "invalid": 120},
            "merlo": {"valid": 0, "invalid": 400},
            "ndjson": {"valid": 200, "invalid": 120},
        },
        "limits": {
            "max_payload_bytes": MAX_PAYLOAD_BYTES,
            "max_merlo_source_bytes": MAX_MERLO_SOURCE_BYTES,
        },
        "cases": cases,
    }
    corpus["sha256"] = corpus_sha256(corpus)
    validate_productive_corpus(corpus)
    return corpus


def validate_productive_corpus(corpus: dict[str, Any]) -> None:
    """Reject a stale, malformed, incomplete, or tampered corpus."""

    if not isinstance(corpus, dict):
        raise ValueError("corpus must be a JSON object")
    expected_top_level_fields = {
        "schema_version",
        "name",
        "provenance",
        "digest_scope",
        "generator",
        "counts",
        "limits",
        "cases",
        "sha256",
    }
    if set(corpus) != expected_top_level_fields:
        raise ValueError("corpus top-level fields do not match the schema")
    digest = corpus.get("sha256")
    if not isinstance(digest, str) or digest != corpus_sha256(corpus):
        raise ValueError("corpus sha256 does not match canonical JSON")
    if corpus.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported productive corpus schema version")
    if corpus.get("name") != "merlo-productive-core":
        raise ValueError("unexpected productive corpus name")
    if corpus.get("provenance") != "generated":
        raise ValueError("corpus provenance must be generated")
    if corpus.get("digest_scope") != "canonical-json-without-sha256":
        raise ValueError("unexpected digest scope")
    generator = corpus.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("generator metadata is missing")
    if set(generator) != {"module", "version", "seeds", "families", "templates"}:
        raise ValueError("generator fields do not match the schema")
    if generator.get("module") != "merlo.productive_corpus" or generator.get("version") != SCHEMA_VERSION:
        raise ValueError("generator identity is invalid")
    if generator.get("seeds") != FIXED_SEEDS:
        raise ValueError("fixed generator seeds do not match")
    if generator.get("families") != sorted(REQUIRED_FAMILIES):
        raise ValueError("required family metadata does not match")
    if generator.get("templates") != _template_metadata():
        raise ValueError("template metadata does not match")

    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    plan = _case_plan()
    observed_plan = []
    ids: set[str] = set()
    seeds: set[int] = set()
    families: set[str] = set()
    templates: set[tuple[str, str, str, bool]] = set()
    counts: Counter[tuple[str, bool]] = Counter()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("every case must be an object")
        required = {"id", "kind", "family", "template", "seed", "validity", "provenance", "expected"}
        if not required <= case.keys():
            raise ValueError("case is missing required fields")
        case_id = case["id"]
        seed = case["seed"]
        if not isinstance(case_id, str) or case_id in ids:
            raise ValueError("case ids must be unique strings")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed in seeds:
            raise ValueError("case seeds must be unique integers")
        ids.add(case_id)
        seeds.add(seed)
        kind = case["kind"]
        validity = case["validity"]
        family = case["family"]
        template = case["template"]
        if not all(isinstance(value, str) for value in (kind, family, template)):
            raise ValueError("case kind, family, and template must be strings")
        if not isinstance(validity, bool):
            raise ValueError("case validity must be boolean")
        if case.get("provenance") != "generated":
            raise ValueError("case provenance must be generated")
        expected = case["expected"]
        if (
            not isinstance(expected, dict)
            or not isinstance(expected.get("outcome"), str)
            or not expected["outcome"]
            or not isinstance(expected.get("summary"), str)
            or not expected["summary"]
        ):
            raise ValueError("case expected outcome or semantic summary is missing")
        if kind == "merlo":
            if "payload_b64" in case or not isinstance(case.get("merlo_source"), str):
                raise ValueError("Merlo cases require source and no binary payload")
            if len(case["merlo_source"].encode("utf-8")) > MAX_MERLO_SOURCE_BYTES:
                raise ValueError("Merlo source exceeds the bounded fixture limit")
        else:
            if "merlo_source" in case or not isinstance(case.get("payload_b64"), str):
                raise ValueError("data cases require a base64 payload and no Merlo source")
            try:
                payload = base64.b64decode(case["payload_b64"], validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("payload_b64 is not canonical base64") from error
            if base64.b64encode(payload).decode("ascii") != case["payload_b64"]:
                raise ValueError("payload_b64 is not canonical base64")
            if len(payload) > MAX_PAYLOAD_BYTES:
                raise ValueError("payload exceeds the bounded fixture limit")
            if family == "invalid-utf8":
                try:
                    payload.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    raise ValueError("invalid-utf8 fixture unexpectedly decodes")
        ids_parts = case_id.rsplit("-", 2)
        if len(ids_parts) != 3 or ids_parts[0] != kind or ids_parts[1] != ("valid" if validity else "invalid"):
            raise ValueError("case id does not encode kind and validity")
        index = int(ids_parts[2]) if ids_parts[2].isdigit() else -1
        observed_plan.append((kind, validity, index, family, template, seed))
        families.add(family)
        templates.add((template, family, kind, validity))
        counts[(kind, validity)] += 1
        position = len(observed_plan) - 1
        if position >= len(plan) or case != _make_case(*plan[position]):
            raise ValueError("case does not match its deterministic template")

    if tuple(observed_plan) != plan:
        raise ValueError("case order, templates, families, or fixed seeds do not match the plan")
    if counts != Counter(REQUIRED_COUNTS):
        raise ValueError("case counts do not match the Productive Core contract")
    if families != set(REQUIRED_FAMILIES):
        raise ValueError("required family coverage is incomplete")
    metadata_templates = {
        (item["id"], item["family"], item["kind"], item["validity"])
        for item in generator["templates"]
    }
    if templates != metadata_templates:
        raise ValueError("not every declared template is exercised")
    expected_counts = {
        "csv": {"valid": 200, "invalid": 120},
        "grep": {"valid": 200, "invalid": 120},
        "merlo": {"valid": 0, "invalid": 400},
        "ndjson": {"valid": 200, "invalid": 120},
    }
    if corpus.get("counts") != expected_counts:
        raise ValueError("reported counts do not match")
    if corpus.get("limits") != {
        "max_payload_bytes": MAX_PAYLOAD_BYTES,
        "max_merlo_source_bytes": MAX_MERLO_SOURCE_BYTES,
    }:
        raise ValueError("fixture limits do not match")
    if "external" in _canonical_json(corpus).lower():
        raise ValueError("generated corpus must not claim outside provenance")


def load_productive_corpus(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate a committed Productive Core corpus artifact."""

    if path is None:
        path = Path(__file__).parents[3] / CORPUS_PATH
    corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_productive_corpus(corpus)
    return corpus


def write_productive_corpus(path: str | Path | None = None) -> dict[str, Any]:
    """Generate and atomically replace the canonical corpus artifact."""

    destination = Path(path) if path is not None else Path(__file__).parents[3] / CORPUS_PATH
    corpus = generate_productive_corpus()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(serialize_productive_corpus(corpus), encoding="utf-8")
    temporary.replace(destination)
    return corpus


__all__ = [
    "CORPUS_PATH",
    "FIXED_SEEDS",
    "MAX_MERLO_SOURCE_BYTES",
    "MAX_PAYLOAD_BYTES",
    "REQUIRED_COUNTS",
    "REQUIRED_FAMILIES",
    "SCHEMA_VERSION",
    "corpus_sha256",
    "generate_productive_corpus",
    "load_productive_corpus",
    "serialize_productive_corpus",
    "validate_productive_corpus",
    "write_productive_corpus",
]
