import pytest

from merlo.deterministic_map import (
    MAP_MAX_CAPACITY,
    UINT64_MAX,
    DeterministicTextUInt64Map,
    deterministic_text_hash,
)



def _keys_in_bucket(bucket: int, count: int) -> tuple[str, ...]:
    keys: list[str] = []
    candidate = 0
    while len(keys) < count:
        key = f"collision-{candidate}"
        if deterministic_text_hash(key) & 7 == bucket:
            keys.append(key)
        candidate += 1
    return tuple(keys)


def test_growth_preserves_colliding_entries_and_insertion_order() -> None:
    keys = _keys_in_bucket(3, 20)
    counts = DeterministicTextUInt64Map(initial_capacity=8)

    for value, key in enumerate(keys):
        counts.insert(key, value)

    assert counts.capacity == 32
    assert counts.entries() == tuple((key, value) for value, key in enumerate(keys))
    assert tuple(counts.get(key) for key in keys) == tuple(range(20))


def test_collision_update_changes_only_the_matching_entry_and_keeps_ordinal() -> None:
    first, second, third = _keys_in_bucket(5, 3)
    counts = DeterministicTextUInt64Map(initial_capacity=8)
    counts.insert(first, 11)
    counts.insert(second, 22)
    counts.insert(third, 33)

    counts.insert(second, 99)

    assert counts.entries() == ((first, 11), (second, 99), (third, 33))
    assert counts.owned_key_count == 3


def test_duplicate_keys_are_not_owned_or_released_twice() -> None:
    counts = DeterministicTextUInt64Map()
    first_key = "owned-key-with-a-non-interned-value"
    equal_key = (" " + first_key).strip()
    assert equal_key == first_key
    assert equal_key is not first_key

    counts.insert(first_key, 1)
    assert counts.entries()[0][0] is not first_key
    counts.insert(equal_key, 2)
    counts.insert("second-owned-key-with-a-non-interned-value", 3)

    assert counts.owned_key_count == 2
    counts.close()
    assert counts.owned_key_count == 0
    assert counts.released_key_count == 2

    counts.close()
    assert counts.released_key_count == 2


def test_growth_rejection_during_view_is_atomic() -> None:
    counts = DeterministicTextUInt64Map(initial_capacity=8)
    for index in range(6):
        counts.insert(f"key-{index}", index)
    before = counts.entries()
    view = counts.borrow_entries()

    with pytest.raises(RuntimeError, match="MapReallocationDuringActiveView"):
        counts.insert("growth-trigger", 99)

    assert counts.capacity == 8
    assert counts.entries() == before
    assert counts.get("growth-trigger") is None
    assert counts.owned_key_count == 6

    view.close()
    counts.insert("growth-trigger", 99)
    assert counts.capacity == 16
    assert counts.entries() == before + (("growth-trigger", 99),)


def test_closing_view_invalidates_an_iterator_already_in_progress() -> None:
    counts = DeterministicTextUInt64Map()
    counts.insert("first", 1)
    counts.insert("second", 2)
    view = counts.borrow_entries()
    iterator = iter(view)
    assert next(iterator) == ("first", 1)

    view.close()

    with pytest.raises(RuntimeError, match="MapViewClosed"):
        next(iterator)
    counts.close()
    assert counts.released_key_count == 2


def test_lookup_and_existing_increment_borrow_the_lookup_key() -> None:
    counts = DeterministicTextUInt64Map()
    stored_key = "borrowed-lookup-key-with-a-non-interned-value"
    lookup_key = (" " + stored_key).strip()
    assert lookup_key == stored_key
    assert lookup_key is not stored_key
    counts.insert(stored_key, 40)

    assert counts.get(lookup_key) == 40
    assert counts.increment(lookup_key, 2) == 42
    assert counts.lookup_key_copies == 0
    assert counts.owned_key_count == 1


def test_uint64_increment_and_capacity_failures_leave_state_unchanged() -> None:
    counts = DeterministicTextUInt64Map(initial_capacity=9)
    counts.insert("maximum", UINT64_MAX)

    with pytest.raises(OverflowError, match="MapUInt64Overflow"):
        counts.increment("maximum")

    assert counts.capacity == 16
    assert counts.entries() == (("maximum", UINT64_MAX),)
    with pytest.raises(TypeError, match="Map key must be Text"):
        counts.increment(1)  # type: ignore[arg-type]
    with pytest.raises(OverflowError, match="MapCapacityOverflow"):
        DeterministicTextUInt64Map(initial_capacity=MAP_MAX_CAPACITY + 1)
