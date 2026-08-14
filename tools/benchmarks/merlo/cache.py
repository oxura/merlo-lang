from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from merlo.version import VERSIONS

CACHE_KINDS = (
    "source",
    "canonical",
    "hir",
    "rir",
    "mir",
    "c11",
    "object",
    "binary",
    "semantic_world",
    "test_result",
)


class CacheError(ValueError):
    code = "CacheError"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code or self.code
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True)
class CacheEntry:
    kind: str
    key: str
    path: Path
    size: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "path": str(self.path),
            "size": self.size,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TimingSample:
    metric: str
    seconds: float
    cache_state: str = "cold"
    changed_scope: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "seconds": self.seconds,
            "cache_state": self.cache_state,
            "changed_scope": self.changed_scope,
        }


@dataclass(frozen=True)
class TimingReport:
    samples: tuple[TimingSample, ...]

    @property
    def metrics(self) -> tuple[str, ...]:
        return tuple(sample.metric for sample in self.samples)

    def to_dict(self) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for sample in self.samples:
            grouped.setdefault(sample.metric, []).append(sample.to_dict())
        return {metric: grouped[metric] for metric in sorted(grouped)}


class ContentCache:
    """Independent content-addressed stores for compiler and tooling outputs.

    Metadata records source/function/module dependencies so an edit can remove
    only entries that are affected; unrelated package and artifact entries are
    retained. The cache never shells out, downloads, or consults a registry.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._index: dict[str, dict[str, Any]] = self._read_index()
        self._timings: list[TimingSample] = []

    def _read_index(self) -> dict[str, dict[str, Any]]:
        if not self._index_path.is_file():
            return {}
        try:
            value = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CacheError(str(self._index_path), code="InvalidCacheIndex") from exc
        if not isinstance(value, dict):
            raise CacheError(str(self._index_path), code="InvalidCacheIndex")
        return {str(key): dict(item) for key, item in value.items() if isinstance(item, Mapping)}

    def _write_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._index, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _payload_bytes(payload: bytes | bytearray | str | Mapping[str, Any]) -> bytes:
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, bytearray):
            return bytes(payload)
        if isinstance(payload, str):
            return payload.encode("utf-8")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    def key(
        self,
        kind: str,
        payload: bytes | bytearray | str | Mapping[str, Any],
        *,
        compiler: Mapping[str, Any] | None = None,
        dependencies: Mapping[str, Any] | Iterable[str] | None = None,
        target: str = "native",
    ) -> str:
        if kind not in CACHE_KINDS:
            raise CacheError(f"unknown cache kind {kind}", code="UnknownCacheKind")
        if dependencies is None:
            dependency_data: Any = ()
        elif isinstance(dependencies, Mapping):
            dependency_data = dict(sorted(dependencies.items()))
        else:
            dependency_data = tuple(sorted(str(item) for item in dependencies))
        envelope = {
            "kind": kind,
            "payload_sha256": hashlib.sha256(self._payload_bytes(payload)).hexdigest(),
            "compiler": dict(compiler or VERSIONS.to_dict()),
            "dependencies": dependency_data,
            "target": target,
        }
        return hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    cache_key = key

    def put(
        self,
        kind: str,
        payload: bytes | bytearray | str | Mapping[str, Any],
        *,
        key: str | None = None,
        source_hashes: Iterable[str] = (),
        modules: Iterable[str] = (),
        functions: Iterable[str] = (),
        depends_on: Iterable[str] = (),
        compiler: Mapping[str, Any] | None = None,
        dependencies: Mapping[str, Any] | Iterable[str] | None = None,
        target: str = "native",
    ) -> CacheEntry:
        content = self._payload_bytes(payload)
        entry_key = key or self.key(
            kind,
            content,
            compiler=compiler,
            dependencies=dependencies,
            target=target,
        )
        path = self.root / kind / entry_key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_bytes(content)
        metadata = {
            "kind": kind,
            "source_hashes": sorted(set(source_hashes)),
            "modules": sorted(set(modules)),
            "functions": sorted(set(functions)),
            "depends_on": sorted(set(depends_on)),
            "target": target,
        }
        self._index[f"{kind}:{entry_key}"] = {
            "path": str(path.relative_to(self.root)),
            "size": len(content),
            "metadata": metadata,
        }
        self._write_index()
        return CacheEntry(kind, entry_key, path, len(content), metadata)

    def get(self, kind: str, key: str) -> bytes | None:
        record = self._index.get(f"{kind}:{key}")
        path = self.root / str(record["path"]) if record else self.root / kind / key
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError:
            return None

    def entry(self, kind: str, key: str) -> CacheEntry | None:
        record = self._index.get(f"{kind}:{key}")
        if record is None:
            return None
        path = self.root / str(record["path"])
        if not path.is_file():
            return None
        return CacheEntry(
            kind,
            key,
            path,
            int(record.get("size", path.stat().st_size)),
            dict(record.get("metadata", {})),
        )

    def contains(self, kind: str, key: str) -> bool:
        return self.get(kind, key) is not None

    def invalidate(self, kind: str, keys: Iterable[str]) -> tuple[str, ...]:
        removed: list[str] = []
        for key in sorted(set(keys)):
            index_key = f"{kind}:{key}"
            record = self._index.pop(index_key, None)
            if record is None:
                continue
            path = self.root / str(record["path"])
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            removed.append(key)
        if removed:
            self._write_index()
        return tuple(removed)

    def _matching(self, predicate: Callable[[str, Mapping[str, Any]], bool]) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        for index_key, record in self._index.items():
            kind, _, key = index_key.partition(":")
            metadata = record.get("metadata", {})
            if isinstance(metadata, Mapping) and predicate(kind, metadata):
                result.append((kind, key))
        return tuple(sorted(result))

    def invalidate_source(self, source_hash: str) -> tuple[str, ...]:
        matches = self._matching(
            lambda _kind, metadata: source_hash in metadata.get("source_hashes", ())
        )
        removed: list[str] = []
        for kind, key in matches:
            removed.extend(self.invalidate(kind, (key,)))
        return tuple(removed)

    def invalidate_function(self, function_id: str) -> tuple[str, ...]:
        matches = self._matching(
            lambda _kind, metadata: function_id in metadata.get("functions", ())
            or function_id in metadata.get("depends_on", ())
        )
        removed: list[str] = []
        for kind, key in matches:
            removed.extend(self.invalidate(kind, (key,)))
        return tuple(removed)

    def invalidate_module(self, module: str, *, dependents: Iterable[str] = ()) -> tuple[str, ...]:
        affected = {module, *dependents}
        matches = self._matching(
            lambda _kind, metadata: bool(affected.intersection(metadata.get("modules", ())))
            or bool(affected.intersection(metadata.get("depends_on", ())))
        )
        removed: list[str] = []
        for kind, key in matches:
            removed.extend(self.invalidate(kind, (key,)))
        return tuple(removed)

    def invalidate_semantic_world(self, *, source_hashes: Iterable[str] = (), modules: Iterable[str] = ()) -> tuple[str, ...]:
        source_set = set(source_hashes)
        module_set = set(modules)
        matches = self._matching(
            lambda kind, metadata: kind == "semantic_world"
            and (bool(source_set.intersection(metadata.get("source_hashes", ())))
                 or bool(module_set.intersection(metadata.get("modules", ()))))
        )
        removed: list[str] = []
        for kind, key in matches:
            removed.extend(self.invalidate(kind, (key,)))
        return tuple(removed)

    def record_timing(
        self,
        metric: str,
        seconds: float,
        *,
        cache_state: str = "cold",
        changed_scope: str = "none",
    ) -> TimingSample:
        if metric not in {"cold", "warm", "single_function_edit", "module_edit", "link", "semantic_map"}:
            raise CacheError(metric, code="UnknownTimingMetric")
        sample = TimingSample(metric, float(seconds), cache_state, changed_scope)
        self._timings.append(sample)
        return sample

    def measure(
        self,
        metric: str,
        operation: Callable[[], Any],
        *,
        cache_state: str = "cold",
        changed_scope: str = "none",
    ) -> tuple[Any, TimingSample]:
        started = time.perf_counter()
        result = operation()
        sample = self.record_timing(
            metric,
            time.perf_counter() - started,
            cache_state=cache_state,
            changed_scope=changed_scope,
        )
        return result, sample

    def timing_report(self) -> TimingReport:
        return TimingReport(tuple(self._timings))

    report_timings = timing_report

    def clear(self) -> None:
        for kind in CACHE_KINDS:
            shutil.rmtree(self.root / kind, ignore_errors=True)
        self._index.clear()
        self._write_index()


__all__ = [
    "CACHE_KINDS",
    "CacheEntry",
    "CacheError",
    "ContentCache",
    "TimingReport",
    "TimingSample",
]
