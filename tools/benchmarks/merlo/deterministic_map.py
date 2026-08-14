from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


UINT64_MAX = (1 << 64) - 1
FNV1A64_OFFSET_BASIS = 14695981039346656037
FNV1A64_PRIME = 1099511628211
MAP_MAX_CAPACITY = 1 << 30
_MIN_CAPACITY = 8


def deterministic_text_hash(key: str) -> int:
    if not isinstance(key, str):
        raise TypeError("Map key must be Text")
    value = FNV1A64_OFFSET_BASIS
    for byte in key.encode("utf-8"):
        value ^= byte
        value = (value * FNV1A64_PRIME) & UINT64_MAX
    return value


def map_contract() -> dict[str, object]:
    return {
        "key_type": "Text",
        "value_type": "UInt64",
        "hash": "FNV-1a-64 over UTF-8 bytes",
        "collision": "open addressing with linear probing",
        "growth": "double at 75 percent occupancy",
        "duplicate": "replace value without changing insertion order",
        "iteration": "insertion order",
        "lookup_keys": "borrowed and never copied",
        "stored_keys": "owned exactly once until close",
        "active_views": "growth is rejected",
        "capacity_overflow": "checked",
    }


@dataclass
class _Entry:
    key: str
    value: int
    ordinal: int


class DeterministicMapEntriesView:
    def __init__(self, owner: DeterministicTextUInt64Map) -> None:
        self._owner = owner
        self._closed = False
        owner._active_views += 1

    def __enter__(self) -> DeterministicMapEntriesView:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[tuple[str, int]]:
        entries = self._owner._ordered_entries()
        while True:
            if self._closed:
                raise RuntimeError("MapViewClosed")
            try:
                yield next(entries)
            except StopIteration:
                return

    def close(self) -> None:
        if not self._closed:
            self._owner._active_views -= 1
            self._closed = True


class DeterministicTextUInt64Map:
    def __init__(self, *, initial_capacity: int = _MIN_CAPACITY) -> None:
        if not isinstance(initial_capacity, int) or isinstance(initial_capacity, bool):
            raise TypeError("Map capacity must be UInt64")
        if initial_capacity < 1 or initial_capacity > MAP_MAX_CAPACITY:
            raise OverflowError("MapCapacityOverflow")
        capacity = _MIN_CAPACITY
        while capacity < initial_capacity:
            if capacity > MAP_MAX_CAPACITY // 2:
                raise OverflowError("MapCapacityOverflow")
            capacity *= 2
        self._buckets: list[_Entry | None] = [None] * capacity
        self._size = 0
        self._next_ordinal = 0
        self._active_views = 0
        self._owned_key_count = 0
        self._released_key_count = 0
        self._lookup_key_copies = 0
        self._closed = False

    @property
    def capacity(self) -> int:
        return len(self._buckets)

    @property
    def owned_key_count(self) -> int:
        return self._owned_key_count

    @property
    def released_key_count(self) -> int:
        return self._released_key_count

    @property
    def lookup_key_copies(self) -> int:
        return self._lookup_key_copies

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("MapClosed")

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str):
            raise TypeError("Map key must be Text")

    @staticmethod
    def _validate_uint64(value: int) -> None:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= UINT64_MAX
        ):
            raise OverflowError("MapUInt64Overflow")

    def _bucket(
        self,
        key: str,
        buckets: list[_Entry | None] | None = None,
    ) -> tuple[int, _Entry | None]:
        target = self._buckets if buckets is None else buckets
        index = deterministic_text_hash(key) & (len(target) - 1)
        for _ in range(len(target)):
            entry = target[index]
            if entry is None or entry.key == key:
                return index, entry
            index = (index + 1) & (len(target) - 1)
        raise RuntimeError("MapProbeExhausted")

    def _needs_growth(self) -> bool:
        return self._size >= self.capacity - self.capacity // 4

    def _grow(self) -> None:
        if self._active_views:
            raise RuntimeError("MapReallocationDuringActiveView")
        if self.capacity > MAP_MAX_CAPACITY // 2:
            raise OverflowError("MapCapacityOverflow")
        buckets: list[_Entry | None] = [None] * (self.capacity * 2)
        for entry in self._buckets:
            if entry is not None:
                index, _ = self._bucket(entry.key, buckets)
                buckets[index] = entry
        self._buckets = buckets

    def insert(self, key: str, value: int) -> None:
        self._require_open()
        self._validate_key(key)
        self._validate_uint64(value)
        index, entry = self._bucket(key)
        if entry is not None:
            entry.value = value
            return
        if self._needs_growth():
            self._grow()
            index, _ = self._bucket(key)
        owned_key = key.encode("utf-8").decode("utf-8")
        self._buckets[index] = _Entry(owned_key, value, self._next_ordinal)
        self._next_ordinal += 1
        self._size += 1
        self._owned_key_count += 1

    def increment(self, key: str, amount: int = 1) -> int:
        self._require_open()
        self._validate_key(key)
        self._validate_uint64(amount)
        _, entry = self._bucket(key)
        current = 0 if entry is None else entry.value
        if current > UINT64_MAX - amount:
            raise OverflowError("MapUInt64Overflow")
        updated = current + amount
        if entry is None:
            self.insert(key, updated)
        else:
            entry.value = updated
        return updated

    def get(self, key: str) -> int | None:
        self._require_open()
        self._validate_key(key)
        _, entry = self._bucket(key)
        return None if entry is None else entry.value

    def _ordered_entries(self) -> Iterator[tuple[str, int]]:
        entries = sorted(
            (entry for entry in self._buckets if entry is not None),
            key=lambda entry: entry.ordinal,
        )
        for entry in entries:
            yield entry.key, entry.value

    def entries(self) -> tuple[tuple[str, int], ...]:
        self._require_open()
        return tuple(self._ordered_entries())

    def borrow_entries(self) -> DeterministicMapEntriesView:
        self._require_open()
        return DeterministicMapEntriesView(self)

    def close(self) -> None:
        if self._closed:
            return
        if self._active_views:
            raise RuntimeError("MapDropDuringActiveView")
        self._released_key_count += self._owned_key_count
        self._buckets.clear()
        self._size = 0
        self._next_ordinal = 0
        self._owned_key_count = 0
        self._closed = True

    def __enter__(self) -> DeterministicTextUInt64Map:
        self._require_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "DeterministicMapEntriesView",
    "DeterministicTextUInt64Map",
    "FNV1A64_OFFSET_BASIS",
    "MAP_MAX_CAPACITY",
    "UINT64_MAX",
    "deterministic_text_hash",
    "map_contract",
]
