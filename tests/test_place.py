from __future__ import annotations

import json

import pytest

from merlo.place import (
    IndexClass,
    OverlapRelation,
    PLACE_CONTRACT,
    PLACE_SCHEMA_VERSION,
    Place,
    PlaceError,
    PlaceRoot,
    PlaceStep,
    UnsupportedProjectionError,
    overlap_relation,
)


ROOT = PlaceRoot.param("symbol:parameter:0")
LOCAL_ROOT = PlaceRoot.local("symbol:local:0")


def test_place_v1_roundtrip_is_canonical_and_versioned() -> None:
    place = Place.from_root(ROOT).append(
        PlaceStep.field("field:User:name"),
        PlaceStep.index(IndexClass.constant(7)),
        PlaceStep.dereference(),
    )

    payload = place.to_dict()
    assert payload["contract"] == PLACE_CONTRACT
    assert payload["schema_version"] == PLACE_SCHEMA_VERSION
    assert Place.from_dict(payload) == place
    assert Place.from_json(place.to_json()) == place
    assert json.loads(place.to_json()) == payload
    assert place.semantic_key == (ROOT, place.steps)


def test_place_roots_keep_local_and_parameter_bindings_distinct() -> None:
    assert LOCAL_ROOT.is_local
    assert not LOCAL_ROOT.is_param
    assert Place.from_root(LOCAL_ROOT).overlap(Place.from_root(ROOT)) is OverlapRelation.DISJOINT
    assert PlaceRoot.from_dict(LOCAL_ROOT.to_dict()) == LOCAL_ROOT


def test_place_identity_uses_symbol_and_structural_ids_not_source_names() -> None:
    left = Place(PlaceRoot.param("symbol:left"), (PlaceStep.field("field:left"),))
    right = Place(PlaceRoot.param("symbol:right"), (PlaceStep.field("field:left"),))
    other_field = Place(PlaceRoot.param("symbol:left"), (PlaceStep.field("field:right"),))
    assert overlap_relation(left, right) is OverlapRelation.DISJOINT
    assert overlap_relation(left, other_field) is OverlapRelation.DISJOINT


def test_overlap_relation_table_for_prefixes_fields_and_variants() -> None:
    root = PlaceRoot.self_root("symbol:record:User")
    whole = Place(root)
    name = whole.project(PlaceStep.field("field:name"))
    nested = name.project(PlaceStep.field("field:bytes"))
    age = whole.project(PlaceStep.field("field:age"))
    active = whole.project(PlaceStep.variant_payload("variant:Active"))
    inactive = whole.project(PlaceStep.variant_payload("variant:Inactive"))

    assert whole.overlap(name) is OverlapRelation.ANCESTOR
    assert nested.overlap(name) is OverlapRelation.DESCENDANT
    assert name.overlap(name) is OverlapRelation.EQUAL
    assert name.overlap(age) is OverlapRelation.DISJOINT
    assert active.overlap(inactive) is OverlapRelation.DISJOINT


def test_dynamic_indexes_are_conservatively_may_overlap() -> None:
    root = PlaceRoot.param("symbol:parameter:array")
    dynamic = Place(root).project(PlaceStep.index(IndexClass.dynamic()))
    constant = Place(root).project(PlaceStep.index(IndexClass.constant(0)))
    other_dynamic = Place(root).project(PlaceStep.index(IndexClass.dynamic()))

    assert dynamic.overlap(constant) is OverlapRelation.MAY_OVERLAP
    assert dynamic.overlap(other_dynamic) is OverlapRelation.MAY_OVERLAP



def test_unsupported_projection_and_malformed_contract_fail_closed() -> None:
    with pytest.raises(UnsupportedProjectionError):
        PlaceStep("OpaqueProjection", "source-name")
    with pytest.raises(UnsupportedProjectionError):
        PlaceStep.from_dict({"kind": "OpaqueProjection"})
    with pytest.raises(PlaceError, match="contract or schema"):
        Place.from_dict(
            {
                "contract": "merlo.place.v0",
                "schema_version": 0,
                "root": ROOT.to_dict(),
                "steps": [],
            }
        )
    with pytest.raises(PlaceError):
        Place.from_root(ROOT).project("field:name")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, 1 << 64, True])
def test_constant_index_requires_uint64(value: object) -> None:
    with pytest.raises(PlaceError):
        IndexClass.constant(value)  # type: ignore[arg-type]


def test_place_deserialization_rejects_extra_shape_data() -> None:
    payload = Place(ROOT).to_dict()
    payload["unexpected"] = True
    with pytest.raises(PlaceError):
        Place.from_dict(payload)
