from __future__ import annotations

import pytest

from merlo.intrinsics import (
    INSTANCE_METHOD_SIGNATURES,
    INTRINSIC_SIGNATURES,
    InstanceMethodSignature,
    operation_footprint,
)
from merlo.operation_footprint import (
    OPERATION_FOOTPRINT_CONTRACT,
    OPERATION_FOOTPRINT_SCHEMA_VERSION,
    OperationFootprint,
    PlacePattern,
    footprint_attributes,
)


def test_place_patterns_are_small_and_canonical() -> None:
    assert PlacePattern("receiver").render() == "receiver"
    assert PlacePattern("receiver", ("*",)).render() == "receiver[*]"
    assert PlacePattern("receiver", ("deref",)).render() == "receiver.deref"
    state = PlacePattern("parameter", ("0", "state"))
    assert state.render() == "parameter[0].state"
    assert PlacePattern.parse(state.render()) == state

    with pytest.raises(ValueError):
        PlacePattern("receiver", ("state",))
    with pytest.raises(ValueError):
        PlacePattern.parse("receiver[*].deref")
    with pytest.raises(ValueError):
        PlacePattern.parse("parameter[00].state")


def test_footprint_round_trip_is_strict_and_deterministic() -> None:
    footprint = operation_footprint("Map[Text,UInt64].insert")
    assert footprint is not None
    payload = footprint.to_dict()
    assert payload["schema_version"] == OPERATION_FOOTPRINT_SCHEMA_VERSION
    assert payload["contract"] == OPERATION_FOOTPRINT_CONTRACT
    assert OperationFootprint.from_dict(payload) == footprint
    assert OperationFootprint.from_dict(dict(payload)) is not footprint

    with pytest.raises(ValueError):
        OperationFootprint.from_dict({**payload, "extra": True})
    with pytest.raises(ValueError):
        OperationFootprint.from_dict({**payload, "atomicity": "sometimes"})

    with pytest.raises(ValueError):
        OperationFootprint.from_dict({**payload, "device_compatibility": "cpu"})

def test_required_catalog_semantics_and_unknowns() -> None:
    vec_get = operation_footprint("Vec[Text].get")
    assert vec_get is not None
    assert tuple(pattern.render() for pattern in vec_get.read_places) == ("receiver[*]",)
    assert tuple(pattern.render() for pattern in vec_get.borrow_places) == ("receiver[*]",)

    vec_push = operation_footprint("Vec[Text].push")
    assert vec_push is not None
    assert vec_push.may_relocate and vec_push.allocates
    assert tuple(pattern.render() for pattern in vec_push.invalidated_borrows) == ("receiver[*]",)

    map_insert = operation_footprint("Map[Text,UInt64].insert")
    assert map_insert is not None
    assert map_insert.read_places == map_insert.write_places == (PlacePattern("receiver"),)
    assert map_insert.atomicity == "operation"

    box_get = operation_footprint("Box[Text].get")
    assert box_get is not None
    assert box_get.borrow_places == (PlacePattern("receiver", ("deref",)),)

    read_chunk = operation_footprint("fs.read_chunk")
    assert read_chunk is not None
    assert read_chunk.read_places == read_chunk.write_places == (PlacePattern("parameter", ("0", "state")),)
    assert read_chunk.blocking and read_chunk.allocates
    assert operation_footprint("missing.operation") is None


    close_read = operation_footprint("fs.close_read")
    assert close_read is not None and close_read.frees
    assert close_read.device_compatibility == ("cpu",)

def test_signature_footprints_come_from_catalog() -> None:
    assert INTRINSIC_SIGNATURES["fs.read_chunk"].footprint is operation_footprint("fs.read_chunk")
    assert INSTANCE_METHOD_SIGNATURES[("Vec[T]", "push")].footprint is operation_footprint("Vec.push")
    custom = InstanceMethodSignature("NotAType", "method", (), "Unit")
    assert custom.footprint is None


def test_footprint_attributes_are_optional_and_stable() -> None:
    assert footprint_attributes(None) == {}
    footprint = operation_footprint("Vec.set")
    assert footprint is not None
    assert footprint_attributes(footprint) == {"operation_footprint": footprint.to_dict()}
