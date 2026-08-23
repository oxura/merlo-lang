"""General type descriptors, layout validation, drop plans, and Representation IR v1."""

from __future__ import annotations

from collections import deque
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable

from merlo.structured_hir_v2 import (
    HIRNode,
    HIRTypeDecl,
    SourceSpan,
    StructuredHIRProgram,
)
from merlo.type_arena import (
    FrozenTypeArena,
    TypeArena,
    TypeArenaError,
    TypeContext,
    TypeId,
    TypeRef,
)
from merlo.type_properties import TypePropertyResolver
from merlo.borrow_summary import BorrowSummary


REPRESENTATION_IR_SCHEMA_VERSION = 6
REPRESENTATION_IR_CONTRACT = "merlo.representation-ir.v6"
LAYOUT_ID_SCHEMA_VERSION = 1
LAYOUT_ID_CONTRACT = "merlo.layout-id.v1"
DROP_PLAN_SCHEMA_VERSION = 1
DROP_PLAN_CONTRACT = "merlo.drop-plan.v1"
TARGET_SPEC_SCHEMA_VERSION = 1
TARGET_SPEC_CONTRACT = "merlo.target-spec.v1"
TYPE_ARENA_CONTRACT = "merlo.type-arena.v1"
MAX_U64 = (1 << 64) - 1

_KNOWN_TARGETS = {
    "x86_64-unknown-linux-gnu": ("little", 64),
    "x86_64-unknown-linux-musl": ("little", 64),
    "aarch64-unknown-linux-gnu": ("little", 64),
    "wasm32-unknown-unknown": ("little", 32),
}


@dataclass(frozen=True, order=True)
class TargetSpec:
    """Validated target policy consumed only by the physical layout layer."""

    target_triple: str = "x86_64-unknown-linux-gnu"
    endianness: str = "little"
    pointer_width: int = 64
    address_space: str = "global"
    abi_policy: str = "native"

    def __post_init__(self) -> None:
        expected = _KNOWN_TARGETS.get(self.target_triple)
        if expected is None:
            raise ValueError(f"unknown target specification: {self.target_triple}")
        if (self.endianness, self.pointer_width) != expected:
            raise ValueError("target triple does not match endianness/pointer width")
        if self.address_space not in {"global", "gpu_shared"}:
            raise ValueError(f"unknown target address space: {self.address_space}")
        if self.abi_policy not in {"native", "repr(C)"}:
            raise ValueError(f"unknown ABI policy: {self.abi_policy}")

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": TARGET_SPEC_CONTRACT,
            "schema_version": TARGET_SPEC_SCHEMA_VERSION,
            "target_triple": self.target_triple,
            "endianness": self.endianness,
            "pointer_width": self.pointer_width,
            "address_space": self.address_space,
            "abi_policy": self.abi_policy,
        }

    @classmethod
    def from_dict(cls, value: object) -> "TargetSpec":
        if not isinstance(value, dict) or set(value) != {
            "contract",
            "schema_version",
            "target_triple",
            "endianness",
            "pointer_width",
            "address_space",
            "abi_policy",
        }:
            raise ValueError("target specification schema mismatch")
        if (
            value["contract"] != TARGET_SPEC_CONTRACT
            or value["schema_version"] != TARGET_SPEC_SCHEMA_VERSION
        ):
            raise ValueError("target specification contract mismatch")
        return cls(
            value["target_triple"],
            value["endianness"],
            value["pointer_width"],
            value["address_space"],
            value["abi_policy"],
        )


DEFAULT_TARGET_SPEC = TargetSpec()


def _type_leaf(type_name: str) -> str:
    return type_name.rsplit("__", 1)[-1].rsplit(".", 1)[-1]


class RepresentationCompileError(ValueError):
    """Typed representation/layout/ownership failure."""

@dataclass(frozen=True, order=True)
class LayoutId:
    """Opaque content-addressed identity of one physical representation."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or len(self.value) != 64:
            raise ValueError("LayoutId must be a 64-character hexadecimal digest")
        if any(character not in "0123456789abcdef" for character in self.value):
            raise ValueError("LayoutId must be lowercase hexadecimal")

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, str]:
        return {"contract": LAYOUT_ID_CONTRACT, "value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> "LayoutId":
        if not isinstance(value, dict) or set(value) != {"contract", "value"}:
            raise ValueError("invalid LayoutId")
        if value["contract"] != LAYOUT_ID_CONTRACT:
            raise ValueError("LayoutId contract mismatch")
        if not isinstance(value["value"], str):
            raise ValueError("LayoutId value must be text")
        return cls(value["value"])

@dataclass(frozen=True, order=True)
class DropPlanId:
    """Opaque identity of one type-directed drop plan."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or len(self.value) != 64:
            raise ValueError("DropPlanId must be a 64-character hexadecimal digest")
        if any(character not in "0123456789abcdef" for character in self.value):
            raise ValueError("DropPlanId must be lowercase hexadecimal")

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, str]:
        return {"contract": DROP_PLAN_CONTRACT, "value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> "DropPlanId":
        if not isinstance(value, dict) or set(value) != {"contract", "value"}:
            raise ValueError("invalid DropPlanId")
        if value["contract"] != DROP_PLAN_CONTRACT:
            raise ValueError("DropPlanId contract mismatch")
        if not isinstance(value["value"], str):
            raise ValueError("DropPlanId value must be text")
        return cls(value["value"])


def _identity_json(value: object) -> object:
    if isinstance(value, (TypeId, LayoutId, DropPlanId)):
        return value.to_dict()
    if isinstance(value, (tuple, list)):
        return [_identity_json(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _identity_json(value[key])
            for key in sorted(value)
        }
    return value


def _identity_digest(contract: str, payload: object) -> str:
    encoded = json.dumps(
        {"contract": contract, "payload": _identity_json(payload)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_payload(value: object) -> Any:
    if isinstance(value, (TypeId, LayoutId, DropPlanId)):
        return value.to_dict()
    if isinstance(value, HIRNode):
        return {"contract": "merlo.hir-node.v1", "value": value.to_dict()}
    if isinstance(value, (tuple, list)):
        return [_json_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_payload(value[key])
            for key in sorted(value)
        }
    return value


def _type_id_from_dict(value: object, label: str) -> TypeId:
    try:
        return TypeId.from_dict(value)
    except TypeArenaError as exc:
        raise ValueError(f"invalid {label} TypeId") from exc


def _optional_type_id_from_dict(value: object, label: str) -> TypeId | None:
    return None if value is None else _type_id_from_dict(value, label)
def _hydrate_identity_payload(value: object) -> Any:
    if isinstance(value, dict):
        if (
            set(value) == {"contract", "value"}
            and value["contract"] == "merlo.type-id.v1"
        ):
            return _type_id_from_dict(value, "attribute")
        if (
            set(value) == {"contract", "value"}
            and value["contract"] == "merlo.hir-node.v1"
        ):
            return HIRNode.from_dict(value["value"])
        return {
            str(key): _hydrate_identity_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return tuple(_hydrate_identity_payload(item) for item in value)
    return value


def _source_span_from_dict(value: object) -> SourceSpan:
    if not isinstance(value, dict) or set(value) != {
        "path", "line", "column", "end_line", "end_column"
    }:
        raise ValueError("invalid RIR source span")
    return SourceSpan(
        value["path"],
        value["line"],
        value["column"],
        value["end_line"],
        value["end_column"],
    )


@dataclass(frozen=True)
class TypeDescriptor:
    name: str
    kind: str
    size: int
    alignment: int
    abi_class: str
    copy_class: str
    move_class: str
    drop_class: str
    inline_dependencies: tuple[str, ...]
    indirect_dependencies: tuple[str, ...]
    source_type_identity: str
    fields: tuple[tuple[str, str, int], ...] = ()
    variants: tuple[tuple[str, str | None, int], ...] = ()
    element_type: str | None = None
    payload_type: str | None = None
    key_type: str | None = None
    value_type: str | None = None
    length: int | None = None
    invariants: tuple[tuple[str, int], ...] = ()
    contains_borrow: bool = False
    contains_resource: bool = False
    contained_borrow_types: tuple[str, ...] = ()
    contained_resource_types: tuple[str, ...] = ()
    # Text fields above are diagnostic renderings only. These identities are
    # the semantic and physical authorities for Representation IR v6.
    type_id: TypeId | None = None
    layout_id: LayoutId | None = None
    inline_dependency_ids: tuple[TypeId, ...] = ()
    indirect_dependency_ids: tuple[TypeId, ...] = ()
    field_type_ids: tuple[tuple[str, TypeId], ...] = ()
    variant_type_ids: tuple[tuple[str, TypeId | None, int], ...] = ()
    element_type_id: TypeId | None = None
    payload_type_id: TypeId | None = None
    key_type_id: TypeId | None = None
    value_type_id: TypeId | None = None
    contained_borrow_type_ids: tuple[TypeId, ...] = ()
    contained_resource_type_ids: tuple[TypeId, ...] = ()
    # These fields describe physical storage. They deliberately do not carry
    # semantic TypeId values; child layout identities are the only child
    # authority in the LayoutId hash domain.
    abi_alignment: int | None = None
    preferred_alignment: int | None = None
    packing: str = "natural"
    representation_kind: str | None = None
    variant_tag_encoding: str = "none"
    payload_offsets: tuple[int, ...] = ()
    niche_policy: str = "none"
    field_layout_ids: tuple[tuple[str, LayoutId], ...] = ()
    variant_layout_ids: tuple[tuple[str, LayoutId | None, int], ...] = ()
    element_layout_id: LayoutId | None = None
    payload_layout_id: LayoutId | None = None
    key_layout_id: LayoutId | None = None
    value_layout_id: LayoutId | None = None

    def __post_init__(self) -> None:
        if self.type_id is not None and not isinstance(self.type_id, TypeId):
            raise ValueError("descriptor type_id must be TypeId")
        if self.layout_id is not None and not isinstance(self.layout_id, LayoutId):
            raise ValueError("descriptor layout_id must be LayoutId")
        if self.abi_alignment is None:
            object.__setattr__(self, "abi_alignment", self.alignment)
        if self.preferred_alignment is None:
            object.__setattr__(self, "preferred_alignment", self.alignment)
        if self.representation_kind is None:
            object.__setattr__(self, "representation_kind", self.kind)
        for label, value in (
            ("abi_alignment", self.abi_alignment),
            ("preferred_alignment", self.preferred_alignment),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"descriptor {label} must be positive")
        if any(
            not isinstance(item, LayoutId)
            for _, item in self.field_layout_ids
        ):
            raise ValueError("descriptor field layout identities must be LayoutId")
        if any(
            item is not None and not isinstance(item, LayoutId)
            for _, item, _ in self.variant_layout_ids
        ):
            raise ValueError("descriptor variant layout identities must be LayoutId")

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": type(self).__name__,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "alignment": self.alignment,
            "abi_class": self.abi_class,
            "copy_class": self.copy_class,
            "move_class": self.move_class,
            "drop_class": self.drop_class,
            "inline_dependencies": list(self.inline_dependencies),
            "indirect_dependencies": list(self.indirect_dependencies),
            "source_type_identity": self.source_type_identity,
            "type_id": self.type_id.to_dict() if self.type_id else None,
            "layout_id": self.layout_id.to_dict() if self.layout_id else None,
            "inline_dependency_ids": [
                item.to_dict() for item in self.inline_dependency_ids
            ],
            "indirect_dependency_ids": [
                item.to_dict() for item in self.indirect_dependency_ids
            ],
            "fields": [
                {
                    "name": name,
                    "type": type_name,
                    "offset": offset,
                }
                for name, type_name, offset in self.fields
            ],
            "field_type_ids": [
                {"name": name, "type_id": type_id.to_dict()}
                for name, type_id in self.field_type_ids
            ],
            "variants": [
                {"name": name, "payload_type": payload, "tag": tag}
                for name, payload, tag in self.variants
            ],
            "variant_type_ids": [
                {
                    "name": name,
                    "payload_type_id": (
                        payload.to_dict() if payload else None
                    ),
                    "tag": tag,
                }
                for name, payload, tag in self.variant_type_ids
            ],
            "element_type": self.element_type,
            "element_type_id": (
                self.element_type_id.to_dict()
                if self.element_type_id
                else None
            ),
            "payload_type": self.payload_type,
            "payload_type_id": (
                self.payload_type_id.to_dict()
                if self.payload_type_id
                else None
            ),
            "key_type": self.key_type,
            "key_type_id": self.key_type_id.to_dict() if self.key_type_id else None,
            "value_type": self.value_type,
            "value_type_id": (
                self.value_type_id.to_dict() if self.value_type_id else None
            ),
            "length": self.length,
            "invariants": [
                {"function": function, "line": line}
                for function, line in self.invariants
            ],
            "contains_borrow": self.contains_borrow,
            "contains_resource": self.contains_resource,
            "contained_borrow_types": list(self.contained_borrow_types),
            "contained_borrow_type_ids": [
                item.to_dict() for item in self.contained_borrow_type_ids
            ],
            "contained_resource_types": list(self.contained_resource_types),
            "contained_resource_type_ids": [
                item.to_dict() for item in self.contained_resource_type_ids
            ],
            "abi_alignment": self.abi_alignment,
            "preferred_alignment": self.preferred_alignment,
            "packing": self.packing,
            "representation_kind": self.representation_kind,
            "variant_tag_encoding": self.variant_tag_encoding,
            "payload_offsets": list(self.payload_offsets),
            "niche_policy": self.niche_policy,
            "field_layout_ids": [
                {"name": name, "layout_id": layout_id.to_dict()}
                for name, layout_id in self.field_layout_ids
            ],
            "variant_layout_ids": [
                {
                    "name": name,
                    "layout_id": layout_id.to_dict() if layout_id else None,
                    "tag": tag,
                }
                for name, layout_id, tag in self.variant_layout_ids
            ],
            "element_layout_id": (
                self.element_layout_id.to_dict()
                if self.element_layout_id
                else None
            ),
            "payload_layout_id": (
                self.payload_layout_id.to_dict()
                if self.payload_layout_id
                else None
            ),
            "key_layout_id": (
                self.key_layout_id.to_dict() if self.key_layout_id else None
            ),
            "value_layout_id": (
                self.value_layout_id.to_dict() if self.value_layout_id else None
            ),
        }
    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        arena: FrozenTypeArena | TypeContext,
    ) -> "TypeDescriptor":
        if not isinstance(value, dict):
            raise ValueError("RIR descriptor must be an object")
        expected = {
            "descriptor",
            "name",
            "kind",
            "size",
            "alignment",
            "abi_class",
            "copy_class",
            "move_class",
            "drop_class",
            "inline_dependencies",
            "indirect_dependencies",
            "source_type_identity",
            "type_id",
            "layout_id",
            "inline_dependency_ids",
            "indirect_dependency_ids",
            "fields",
            "field_type_ids",
            "variants",
            "variant_type_ids",
            "element_type",
            "element_type_id",
            "payload_type",
            "payload_type_id",
            "key_type",
            "key_type_id",
            "value_type",
            "value_type_id",
            "length",
            "invariants",
            "contains_borrow",
            "contains_resource",
            "contained_borrow_types",
            "contained_borrow_type_ids",
            "contained_resource_types",
            "contained_resource_type_ids",
            "abi_alignment",
            "preferred_alignment",
            "packing",
            "representation_kind",
            "variant_tag_encoding",
            "payload_offsets",
            "niche_policy",
            "field_layout_ids",
            "variant_layout_ids",
            "element_layout_id",
            "payload_layout_id",
            "key_layout_id",
            "value_layout_id",
        }
        if set(value) != expected:
            raise ValueError("RIR descriptor schema mismatch")
        descriptor_classes = {
            "ScalarDesc": ScalarDesc,
            "RecordDesc": RecordDesc,
            "EnumDesc": EnumDesc,
            "VecDesc": VecDesc,
            "ArrayDesc": ArrayDesc,
            "SliceDesc": SliceDesc,
            "CallbackDesc": CallbackDesc,
            "MapDesc": MapDesc,
            "BoxDesc": BoxDesc,
            "BytesDesc": BytesDesc,
            "TextDesc": TextDesc,
            "BorrowDesc": BorrowDesc,
        }
        descriptor_class = descriptor_classes.get(value["descriptor"])
        if descriptor_class is None:
            raise ValueError("unknown RIR descriptor class")
        fields = tuple(
            (
                item["name"],
                item["type"],
                item["offset"],
            )
            for item in value["fields"]
        )
        field_type_ids = tuple(
            (
                item["name"],
                _type_id_from_dict(item["type_id"], "field"),
            )
            for item in value["field_type_ids"]
        )
        variants = tuple(
            (
                item["name"],
                item["payload_type"],
                item["tag"],
            )
            for item in value["variants"]
        )
        variant_type_ids = tuple(
            (
                item["name"],
                _optional_type_id_from_dict(item["payload_type_id"], "variant"),
                item["tag"],
            )
            for item in value["variant_type_ids"]
        )
        field_layout_ids = tuple(
            (
                item["name"],
                LayoutId.from_dict(item["layout_id"]),
            )
            for item in value["field_layout_ids"]
        )
        variant_layout_ids = tuple(
            (
                item["name"],
                (
                    LayoutId.from_dict(item["layout_id"])
                    if item["layout_id"] is not None
                    else None
                ),
                item["tag"],
            )
            for item in value["variant_layout_ids"]
        )
        if not isinstance(arena, (FrozenTypeArena, TypeContext)):
            raise ValueError("RIR descriptor requires a TypeArena")
        return descriptor_class(
            value["name"],
            value["kind"],
            value["size"],
            value["alignment"],
            value["abi_class"],
            value["copy_class"],
            value["move_class"],
            value["drop_class"],
            tuple(value["inline_dependencies"]),
            tuple(value["indirect_dependencies"]),
            value["source_type_identity"],
            fields=fields,
            variants=variants,
            element_type=value["element_type"],
            payload_type=value["payload_type"],
            key_type=value["key_type"],
            value_type=value["value_type"],
            length=value["length"],
            invariants=tuple(
                (item["function"], item["line"])
                for item in value["invariants"]
            ),
            contains_borrow=value["contains_borrow"],
            contains_resource=value["contains_resource"],
            contained_borrow_types=tuple(value["contained_borrow_types"]),
            contained_resource_types=tuple(value["contained_resource_types"]),
            type_id=_optional_type_id_from_dict(value["type_id"], "descriptor"),
            layout_id=(
                LayoutId.from_dict(value["layout_id"])
                if value["layout_id"] is not None
                else None
            ),
            inline_dependency_ids=tuple(
                _type_id_from_dict(item, "inline dependency")
                for item in value["inline_dependency_ids"]
            ),
            indirect_dependency_ids=tuple(
                _type_id_from_dict(item, "indirect dependency")
                for item in value["indirect_dependency_ids"]
            ),
            field_type_ids=field_type_ids,
            abi_alignment=value["abi_alignment"],
            preferred_alignment=value["preferred_alignment"],
            packing=value["packing"],
            representation_kind=value["representation_kind"],
            variant_tag_encoding=value["variant_tag_encoding"],
            payload_offsets=tuple(value["payload_offsets"]),
            niche_policy=value["niche_policy"],
            field_layout_ids=field_layout_ids,
            variant_layout_ids=variant_layout_ids,
            element_layout_id=(
                LayoutId.from_dict(value["element_layout_id"])
                if value["element_layout_id"] is not None
                else None
            ),
            payload_layout_id=(
                LayoutId.from_dict(value["payload_layout_id"])
                if value["payload_layout_id"] is not None
                else None
            ),
            key_layout_id=(
                LayoutId.from_dict(value["key_layout_id"])
                if value["key_layout_id"] is not None
                else None
            ),
            value_layout_id=(
                LayoutId.from_dict(value["value_layout_id"])
                if value["value_layout_id"] is not None
                else None
            ),
            variant_type_ids=variant_type_ids,
            element_type_id=_optional_type_id_from_dict(
                value["element_type_id"], "element"
            ),
            payload_type_id=_optional_type_id_from_dict(
                value["payload_type_id"], "payload"
            ),
            key_type_id=_optional_type_id_from_dict(value["key_type_id"], "key"),
            value_type_id=_optional_type_id_from_dict(
                value["value_type_id"], "value"
            ),
            contained_borrow_type_ids=tuple(
                _type_id_from_dict(item, "contained borrow")
                for item in value["contained_borrow_type_ids"]
            ),
            contained_resource_type_ids=tuple(
                _type_id_from_dict(item, "contained resource")
                for item in value["contained_resource_type_ids"]
            ),
        )


@dataclass(frozen=True)
class ScalarDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class RecordDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class EnumDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class VecDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class ArrayDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class SliceDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class CallbackDesc(TypeDescriptor):
    pass

@dataclass(frozen=True)
class MapDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class BoxDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class BytesDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class TextDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class BorrowDesc(TypeDescriptor):
    pass


@dataclass(frozen=True)
class StoragePolicy:
    storage: str
    copy: str
    move: str
    drop: str
    partial_initialization: str
    shared_ownership: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage": self.storage,
            "copy": self.copy,
            "move": self.move,
            "drop": self.drop,
            "partial_initialization": self.partial_initialization,
            "shared_ownership": self.shared_ownership,
        }




@dataclass(frozen=True)
class DropPlan:
    type_name: str
    action: str
    children: tuple["DropPlan", ...] = ()
    field_name: str | None = None
    variant_name: str | None = None
    depth_limited_by_program: int | None = None
    type_id: TypeId | None = None
    layout_id: LayoutId | None = None
    drop_plan_id: DropPlanId | None = None

    def __post_init__(self) -> None:
        if self.type_id is not None and not isinstance(self.type_id, TypeId):
            raise ValueError("drop plan type_id must be TypeId")
        if self.layout_id is not None and not isinstance(self.layout_id, LayoutId):
            raise ValueError("drop plan layout_id must be LayoutId")
        if self.drop_plan_id is not None and not isinstance(
            self.drop_plan_id, DropPlanId
        ):
            raise ValueError("drop plan drop_plan_id must be DropPlanId")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "action": self.action,
            "field_name": self.field_name,
            "variant_name": self.variant_name,
            "depth_limited_by_program": self.depth_limited_by_program,
            "type_id": self.type_id.to_dict() if self.type_id else None,
            "layout_id": self.layout_id.to_dict() if self.layout_id else None,
            "drop_plan_id": (
                self.drop_plan_id.to_dict() if self.drop_plan_id else None
            ),
            "children": [item.to_dict() for item in self.children],
        }
    @classmethod
    def from_dict(cls, value: object) -> "DropPlan":
        if not isinstance(value, dict) or set(value) != {
            "type",
            "action",
            "field_name",
            "variant_name",
            "depth_limited_by_program",
            "type_id",
            "layout_id",
            "drop_plan_id",
            "children",
        }:
            raise ValueError("drop plan schema mismatch")
        return cls(
            value["type"],
            value["action"],
            tuple(cls.from_dict(item) for item in value["children"]),
            value["field_name"],
            value["variant_name"],
            value["depth_limited_by_program"],
            _optional_type_id_from_dict(value["type_id"], "drop plan"),
            (
                LayoutId.from_dict(value["layout_id"])
                if value["layout_id"] is not None
                else None
            ),
            (
                DropPlanId.from_dict(value["drop_plan_id"])
                if value["drop_plan_id"] is not None
                else None
            ),
        )

def drop_plan_id_for(plan: DropPlan) -> DropPlanId:
    """Compute a drop identity without consulting diagnostic spellings."""
    if plan.type_id is None or plan.layout_id is None:
        raise ValueError("drop plan identity requires TypeId and LayoutId")
    if any(child.drop_plan_id is None for child in plan.children):
        raise ValueError("drop plan identity requires child identities")
    return DropPlanId(
        _identity_digest(
            DROP_PLAN_CONTRACT,
            {
                "type_id": plan.type_id,
                "layout_id": plan.layout_id,
                "action": plan.action,
                "field_name": plan.field_name,
                "variant_name": plan.variant_name,
                "depth_limited_by_program": plan.depth_limited_by_program,
                "children": tuple(child.drop_plan_id for child in plan.children),
            },
        )
    )


@dataclass(frozen=True)
class RIROperation:
    id: str
    op: str
    type_name: str | None
    source: SourceSpan
    symbol_id: str | None
    revision_id: str
    ownership_provenance: str
    effects: tuple[str, ...]
    attributes: tuple[tuple[str, Any], ...] = ()
    children: tuple["RIROperation", ...] = ()
    type_id: TypeId | None = None

    def __post_init__(self) -> None:
        if self.type_id is not None and not isinstance(self.type_id, TypeId):
            raise ValueError("RIR operation type_id must be TypeId")

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)

    def walk(self) -> Iterable["RIROperation"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "type": self.type_name,
            "type_id": self.type_id.to_dict() if self.type_id else None,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "ownership_provenance": self.ownership_provenance,
            "effects": list(self.effects),
            "attributes": _json_payload(dict(self.attributes)),
            "children": [item.to_dict() for item in self.children],
        }
    @classmethod
    def from_dict(cls, value: object) -> "RIROperation":
        if not isinstance(value, dict) or set(value) != {
            "id",
            "op",
            "type",
            "type_id",
            "source",
            "symbol_id",
            "revision_id",
            "ownership_provenance",
            "effects",
            "attributes",
            "children",
        }:
            raise ValueError("RIR operation schema mismatch")
        attributes = value["attributes"]
        if not isinstance(attributes, dict):
            raise ValueError("RIR operation attributes must be an object")
        return cls(
            value["id"],
            value["op"],
            value["type"],
            _source_span_from_dict(value["source"]),
            value["symbol_id"],
            value["revision_id"],
            value["ownership_provenance"],
            tuple(value["effects"]),
            tuple(
                (
                    key,
                    _hydrate_identity_payload(item),
                )
                for key, item in attributes.items()
            ),
            tuple(cls.from_dict(item) for item in value["children"]),
            _optional_type_id_from_dict(value["type_id"], "operation"),
        )
@dataclass(frozen=True)
class RIRFunction:
    name: str
    symbol_id: str
    revision_id: str
    parameters: tuple[tuple[str, str, str], ...]
    return_type: str
    effects: tuple[str, ...]
    operations: tuple[RIROperation, ...]
    source: SourceSpan
    borrow_summary: BorrowSummary = BorrowSummary()
    parameter_type_ids: tuple[TypeId, ...] = ()
    return_type_id: TypeId | None = None

    def walk(self) -> Iterable[RIROperation]:
        for operation in self.operations:
            yield from operation.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "parameters": [
                {
                    "name": name,
                    "type": type_name,
                    "type_id": (
                        self.parameter_type_ids[index].to_dict()
                        if index < len(self.parameter_type_ids)
                        else None
                    ),
                    "ownership": ownership,
                }
                for index, (name, type_name, ownership) in enumerate(self.parameters)
            ],
            "return_type": self.return_type,
            "return_type_id": (
                self.return_type_id.to_dict() if self.return_type_id else None
            ),
            "effects": list(self.effects),
            "source": self.source.to_dict(),
            "operations": [item.to_dict() for item in self.operations],
            "borrow_summary": self.borrow_summary.to_dict(),
        }
    @classmethod
    def from_dict(cls, value: object) -> "RIRFunction":
        if not isinstance(value, dict) or set(value) != {
            "name",
            "symbol_id",
            "revision_id",
            "parameters",
            "return_type",
            "return_type_id",
            "effects",
            "source",
            "operations",
            "borrow_summary",
        }:
            raise ValueError("RIR function schema mismatch")
        parameters = tuple(
            (
                item["name"],
                item["type"],
                item["ownership"],
            )
            for item in value["parameters"]
        )
        parameter_type_ids = tuple(
            _type_id_from_dict(item["type_id"], "parameter")
            for item in value["parameters"]
        )
        return cls(
            value["name"],
            value["symbol_id"],
            value["revision_id"],
            parameters,
            value["return_type"],
            tuple(value["effects"]),
            tuple(RIROperation.from_dict(item) for item in value["operations"]),
            _source_span_from_dict(value["source"]),
            BorrowSummary.from_dict(value["borrow_summary"]),
            parameter_type_ids,
            _type_id_from_dict(value["return_type_id"], "return"),
        )


@dataclass(frozen=True)
class RepresentationProgram:
    source_hir_digest: str
    source_sha256: str
    entry_function: str
    descriptors: tuple[TypeDescriptor, ...]
    drop_plans: tuple[DropPlan, ...]
    functions: tuple[RIRFunction, ...]
    schema_version: int = REPRESENTATION_IR_SCHEMA_VERSION
    contract: str = REPRESENTATION_IR_CONTRACT
    type_arena: FrozenTypeArena | None = None
    type_arena_digest: str = ""
    type_arena_contract: str = TYPE_ARENA_CONTRACT
    predecessor_digest: str = ""
    target_spec: TargetSpec = DEFAULT_TARGET_SPEC
    target_spec_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != REPRESENTATION_IR_SCHEMA_VERSION:
            raise ValueError("Representation IR schema version drift")
        if self.contract != REPRESENTATION_IR_CONTRACT:
            raise ValueError("Representation IR contract drift")
        if self.type_arena_contract != TYPE_ARENA_CONTRACT:
            raise ValueError("Representation IR TypeArena contract drift")
        if not isinstance(self.type_arena, FrozenTypeArena):
            raise ValueError("Representation IR requires a frozen TypeArena")
        if self.type_arena.allow_unresolved:
            raise ValueError("Representation IR requires a closed TypeArena")
        if self.type_arena.digest != self.type_arena_digest:
            raise ValueError("Representation IR TypeArena digest mismatch")
        if self.predecessor_digest == "":
            object.__setattr__(self, "predecessor_digest", self.source_hir_digest)
        if self.predecessor_digest != self.source_hir_digest:
            raise ValueError("Representation IR predecessor digest mismatch")
        if not isinstance(self.target_spec, TargetSpec):
            raise ValueError("Representation IR requires a validated target spec")
        if self.target_spec_digest == "":
            object.__setattr__(self, "target_spec_digest", self.target_spec.digest)
        if self.target_spec_digest != self.target_spec.digest:
            raise ValueError("Representation IR target specification digest mismatch")
        names = [item.name for item in self.descriptors]
        if len(names) != len(set(names)):
            raise ValueError("duplicate type descriptor")
        function_names = [item.name for item in self.functions]
        if len(function_names) != len(set(function_names)):
            raise ValueError("duplicate Representation IR function")
        if self.entry_function not in set(function_names):
            raise ValueError("missing Representation IR entry function")
        if any(
            item.op
            in {
                "json_parse",
                "json_tokenize",
                "json_token_checksum",
                "json_build_ast",
            }
            for function in self.functions
            for item in function.walk()
        ):
            raise ValueError("domain-level intrinsic escaped into Representation IR")
        _verify_representation_program(self)

    @property
    def digest(self) -> str:
        payload = self.to_dict()
        for function in payload["functions"]:
            for entry in function["borrow_summary"]["entries"]:
                entry["witness_path"] = []
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    def descriptor(self, name: str) -> TypeDescriptor:
        return next(item for item in self.descriptors if item.name == name)

    def function(self, name: str) -> RIRFunction:
        return next(item for item in self.functions if item.name == name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "source_hir_digest": self.source_hir_digest,
            "predecessor_digest": self.predecessor_digest,
            "source_sha256": self.source_sha256,
            "entry_function": self.entry_function,
            "type_arena_contract": self.type_arena_contract,
            "type_arena": self.type_arena.to_dict(),
            "type_arena_digest": self.type_arena_digest,
            "target_spec": self.target_spec.to_dict(),
            "target_spec_digest": self.target_spec_digest,
            "descriptors": [item.to_dict() for item in self.descriptors],
            "drop_plans": [item.to_dict() for item in self.drop_plans],
            "functions": [item.to_dict() for item in self.functions],
            "invariants": {
                "general_descriptors": True,
                "inline_cycles_rejected": True,
                "owning_indirection_cycles_allowed": True,
                "drop_plans_type_directed": True,
                "drop_plan_identity_separate": True,
                "symbol_revision_source_preserved": True,
                "ownership_provenance_preserved": True,
                "domain_intrinsics_absent": True,
                "opaque_type_ids": True,
                "layout_ids": True,
                "target_specific_layouts": True,
                "predecessor_digest_required": True,
            },
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    @classmethod
    def from_dict(cls, value: object) -> "RepresentationProgram":
        expected = {
            "schema_version",
            "contract",
            "source_hir_digest",
            "predecessor_digest",
            "source_sha256",
            "entry_function",
            "type_arena_contract",
            "type_arena",
            "type_arena_digest",
            "target_spec",
            "target_spec_digest",
            "descriptors",
            "drop_plans",
            "functions",
            "invariants",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Representation IR schema mismatch")
        invariants = {
            "general_descriptors": True,
            "inline_cycles_rejected": True,
            "owning_indirection_cycles_allowed": True,
            "drop_plans_type_directed": True,
            "drop_plan_identity_separate": True,
            "symbol_revision_source_preserved": True,
            "ownership_provenance_preserved": True,
            "domain_intrinsics_absent": True,
            "opaque_type_ids": True,
            "layout_ids": True,
            "target_specific_layouts": True,
            "predecessor_digest_required": True,
        }
        if value["schema_version"] != REPRESENTATION_IR_SCHEMA_VERSION:
            raise ValueError("Representation IR schema version drift")
        if value["contract"] != REPRESENTATION_IR_CONTRACT:
            raise ValueError("Representation IR contract drift")
        if value["invariants"] != invariants:
            raise ValueError("Representation IR invariants drift")
        try:
            arena = TypeArena.from_dict(value["type_arena"]).freeze()
        except TypeArenaError as exc:
            raise ValueError("invalid Representation IR TypeArena") from exc
        if arena.digest != value["type_arena_digest"]:
            raise ValueError("Representation IR TypeArena digest mismatch")
        if value["type_arena_contract"] != TYPE_ARENA_CONTRACT:
            raise ValueError("Representation IR TypeArena contract drift")
        target_spec = TargetSpec.from_dict(value["target_spec"])
        if value["target_spec_digest"] != target_spec.digest:
            raise ValueError("Representation IR target specification digest mismatch")
        result = cls(
            value["source_hir_digest"],
            value["source_sha256"],
            value["entry_function"],
            tuple(
                TypeDescriptor.from_dict(item, arena=arena)
                for item in value["descriptors"]
            ),
            tuple(DropPlan.from_dict(item) for item in value["drop_plans"]),
            tuple(RIRFunction.from_dict(item) for item in value["functions"]),
            value["schema_version"],
            value["contract"],
            arena,
            value["type_arena_digest"],
            value["type_arena_contract"],
            value["predecessor_digest"],
            target_spec,
            value["target_spec_digest"],
        )
        if result.to_dict() != value:
            raise ValueError("non-canonical Representation IR artifact")
        return result

    @classmethod
    def from_json(cls, value: str) -> "RepresentationProgram":
        try:
            raw = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Representation IR JSON") from exc
        result = cls.from_dict(raw)
        if result.to_json() != value:
            raise ValueError("non-canonical Representation IR JSON")
        return result


def _verify_identity_value(
    value: object,
    arena: FrozenTypeArena,
    label: str,
) -> None:
    if isinstance(value, TypeId):
        if value not in arena:
            raise ValueError(f"{label} references unknown TypeId")
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _verify_identity_value(item, arena, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _verify_identity_value(item, arena, f"{label}.{key}")


def _verify_representation_program(program: RepresentationProgram) -> None:
    arena = program.type_arena
    assert isinstance(arena, FrozenTypeArena)

    def require_id(value: TypeId | None, label: str) -> TypeId:
        if not isinstance(value, TypeId) or value not in arena:
            raise ValueError(f"{label} must be a known TypeId")
        return value
    def require_layout(value: LayoutId | None, label: str) -> LayoutId:
        if not isinstance(value, LayoutId):
            raise ValueError(f"{label} must be a known LayoutId")
        return value

    descriptors = {item.name: item for item in program.descriptors}
    descriptor_ids: set[TypeId] = set()
    def alias_payload(descriptor: TypeDescriptor) -> dict[str, Any]:
        payload = descriptor.to_dict()
        for key in ("descriptor", "name", "type_id", "layout_id"):
            payload.pop(key, None)
        return payload

    aliases_by_identity: dict[str, list[TypeDescriptor]] = {}
    for descriptor in program.descriptors:
        aliases_by_identity.setdefault(
            descriptor.source_type_identity,
            [],
        ).append(descriptor)
    for source_identity, aliases in sorted(aliases_by_identity.items()):
        if len(aliases) < 2:
            continue
        primary = aliases[0]
        primary_payload = alias_payload(primary)
        for alias in aliases[1:]:
            if alias.layout_id != primary.layout_id or alias_payload(alias) != primary_payload:
                raise ValueError(
                    "IncompatibleDescriptorAlias: "
                    f"{source_identity}: {primary.name} vs {alias.name}"
                )

    for descriptor in program.descriptors:
        type_id = require_id(descriptor.type_id, f"descriptor {descriptor.name}")
        if arena.canonical(type_id) != descriptor.name:
            raise ValueError(
                f"descriptor {descriptor.name} TypeId does not match spelling"
            )
        if descriptor.layout_id is None:
            raise ValueError(f"descriptor {descriptor.name} is missing LayoutId")
        if descriptor.layout_id != layout_id_for_descriptor(
            descriptor,
            program.target_spec,
        ):
            raise ValueError(f"descriptor {descriptor.name} LayoutId mismatch")
        if type_id in descriptor_ids:
            raise ValueError("duplicate descriptor TypeId")
        descriptor_ids.add(type_id)
        if len(descriptor.field_type_ids) != len(descriptor.fields):
            raise ValueError(f"descriptor {descriptor.name} field identities mismatch")
        for (
            (field_name, field_type, _offset),
            (identity_name, field_id),
        ) in zip(descriptor.fields, descriptor.field_type_ids, strict=True):
            if field_name != identity_name or arena.canonical(field_id) != field_type:
                raise ValueError(
                    f"descriptor {descriptor.name} field identity mismatch"
                )
            require_id(field_id, f"descriptor {descriptor.name} field")
        if len(descriptor.field_layout_ids) != len(descriptor.fields):
            raise ValueError(f"descriptor {descriptor.name} field LayoutIds mismatch")
        for (field_name, _field_type, _offset), (layout_name, layout_id) in zip(
            descriptor.fields,
            descriptor.field_layout_ids,
            strict=True,
        ):
            if field_name != layout_name:
                raise ValueError(f"descriptor {descriptor.name} field LayoutId mismatch")
            require_layout(layout_id, f"descriptor {descriptor.name} field layout")
        if len(descriptor.variant_type_ids) != len(descriptor.variants):
            raise ValueError(
                f"descriptor {descriptor.name} variant identities mismatch"
            )
        for (
            (variant_name, payload_name, _tag),
            (identity_name, payload_id, _identity_tag),
        ) in zip(descriptor.variants, descriptor.variant_type_ids, strict=True):
            if variant_name != identity_name:
                raise ValueError(
                    f"descriptor {descriptor.name} variant identity mismatch"
                )
            if payload_id is None:
                if payload_name is not None:
                    raise ValueError(
                        f"descriptor {descriptor.name} null variant identity mismatch"
                    )
            elif arena.canonical(payload_id) != payload_name:
                raise ValueError(
                    f"descriptor {descriptor.name} variant identity mismatch"
                )
            if payload_id is not None:
                require_id(payload_id, f"descriptor {descriptor.name} variant")
        if len(descriptor.variant_layout_ids) != len(descriptor.variants):
            raise ValueError(f"descriptor {descriptor.name} variant LayoutIds mismatch")
        for (
            (variant_name, payload_name, _tag),
            (layout_name, payload_layout_id, _layout_tag),
        ) in zip(descriptor.variants, descriptor.variant_layout_ids, strict=True):
            if variant_name != layout_name:
                raise ValueError(
                    f"descriptor {descriptor.name} variant LayoutId mismatch"
                )
            if payload_name is None:
                if payload_layout_id is not None:
                    raise ValueError(
                        f"descriptor {descriptor.name} null variant LayoutId mismatch"
                    )
            else:
                require_layout(
                    payload_layout_id,
                    f"descriptor {descriptor.name} variant layout",
                )
        for names, ids, label in (
            (
                descriptor.inline_dependencies,
                descriptor.inline_dependency_ids,
                "inline dependency",
            ),
            (
                descriptor.indirect_dependencies,
                descriptor.indirect_dependency_ids,
                "indirect dependency",
            ),
            (
                descriptor.contained_borrow_types,
                descriptor.contained_borrow_type_ids,
                "contained borrow",
            ),
            (
                descriptor.contained_resource_types,
                descriptor.contained_resource_type_ids,
                "contained resource",
            ),
        ):
            if len(names) != len(ids):
                raise ValueError(f"descriptor {descriptor.name} {label} mismatch")
            for name, identity in zip(names, ids, strict=True):
                require_id(identity, f"descriptor {descriptor.name} {label}")
                if arena.canonical(identity) != name:
                    raise ValueError(
                        f"descriptor {descriptor.name} {label} spelling mismatch"
                    )
        for name, identity, label in (
            (descriptor.element_type, descriptor.element_type_id, "element"),
            (descriptor.payload_type, descriptor.payload_type_id, "payload"),
            (descriptor.key_type, descriptor.key_type_id, "key"),
            (descriptor.value_type, descriptor.value_type_id, "value"),
        ):
            if name is None:
                if identity is not None:
                    raise ValueError(
                        f"descriptor {descriptor.name} {label} identity without type"
                    )
            else:
                require_id(identity, f"descriptor {descriptor.name} {label}")
                assert identity is not None
                if arena.canonical(identity) != name:
                    raise ValueError(
                        f"descriptor {descriptor.name} {label} spelling mismatch"
                    )
        if descriptor.kind == "array":
            require_layout(
                descriptor.element_layout_id,
                f"descriptor {descriptor.name} element layout",
            )
        if len(descriptor.payload_offsets) != len(descriptor.variants):
            raise ValueError(f"descriptor {descriptor.name} payload offsets mismatch")

    def verify_plan(plan: DropPlan) -> None:
        type_id = require_id(plan.type_id, f"drop plan {plan.type_name}")
        if arena.canonical(type_id) != plan.type_name:
            raise ValueError("drop plan TypeId does not match spelling")
        descriptor = descriptors.get(plan.type_name)
        if descriptor is None or plan.layout_id != descriptor.layout_id:
            raise ValueError("drop plan LayoutId mismatch")
        for child in plan.children:
            verify_plan(child)
        if plan.drop_plan_id != drop_plan_id_for(plan):
            raise ValueError("drop plan identity mismatch")

    for plan in program.drop_plans:
        verify_plan(plan)

    def verify_operation(operation: RIROperation) -> None:
        if operation.type_name is None:
            if operation.type_id is not None:
                raise ValueError("untyped RIR operation carries a TypeId")
        else:
            type_id = require_id(operation.type_id, f"RIR operation {operation.id}")
            if arena.canonical(type_id) != operation.type_name:
                raise ValueError(
                    f"RIR operation {operation.id} TypeId does not match spelling"
                )
        _verify_identity_value(
            dict(operation.attributes),
            arena,
            f"RIR operation {operation.id} attributes",
        )
        for child in operation.children:
            verify_operation(child)

    for function in program.functions:
        if len(function.parameter_type_ids) != len(function.parameters):
            raise ValueError(f"RIR function {function.name} parameter identities mismatch")
        for (_name, type_name, _ownership), type_id in zip(
            function.parameters,
            function.parameter_type_ids,
            strict=True,
        ):
            require_id(type_id, f"RIR function {function.name} parameter")
            if arena.canonical(type_id) != type_name:
                raise ValueError(
                    f"RIR function {function.name} parameter TypeId mismatch"
                )
        return_id = require_id(
            function.return_type_id,
            f"RIR function {function.name} return",
        )
        if arena.canonical(return_id) != function.return_type:
            raise ValueError(f"RIR function {function.name} return TypeId mismatch")
        for operation in function.operations:
            verify_operation(operation)


def verify_representation_program(program: RepresentationProgram) -> None:
    """Fail-closed verifier for construction and deserialization callers."""

    if not isinstance(program, RepresentationProgram):
        raise TypeError("expected RepresentationProgram")
    _verify_representation_program(program)
@dataclass(frozen=True)
class LayoutValidation:
    accepted: bool
    inline_graph: tuple[tuple[str, tuple[str, ...]], ...]
    minimal_cycle_path: tuple[str, ...] = ()
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "inline_graph": {name: list(edges) for name, edges in self.inline_graph},
            "minimal_cycle_path": list(self.minimal_cycle_path),
            "diagnostic": self.diagnostic,
        }


_SCALARS: dict[str, tuple[int, int]] = {
    "Unit": (0, 1),
    "Bool": (1, 1),
    "Byte": (1, 1),
    "Int8": (1, 1),
    "UInt8": (1, 1),
    "Int16": (2, 2),
    "UInt16": (2, 2),
    "Int32": (4, 4),
    "UInt32": (4, 4),
    "Int64": (8, 8),
    "UInt64": (8, 8),
    "Float32": (4, 4),
    "Float64": (8, 8),
}


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"
def _layout_payload(
    descriptor: TypeDescriptor,
    target_spec: TargetSpec = DEFAULT_TARGET_SPEC,
) -> dict[str, Any]:
    field_layouts = dict(descriptor.field_layout_ids)
    if descriptor.fields and len(field_layouts) != len(descriptor.fields):
        raise ValueError(
            f"descriptor {descriptor.name} is missing child field LayoutIds"
        )
    variant_layouts = {
        name: (layout_id, tag)
        for name, layout_id, tag in descriptor.variant_layout_ids
    }
    if descriptor.variants and len(variant_layouts) != len(descriptor.variants):
        raise ValueError(
            f"descriptor {descriptor.name} is missing child variant LayoutIds"
        )
    return {
        "contract": LAYOUT_ID_CONTRACT,
        "schema_version": LAYOUT_ID_SCHEMA_VERSION,
        "target_spec_contract": TARGET_SPEC_CONTRACT,
        "target_spec_digest": target_spec.digest,
        "target_triple": target_spec.target_triple,
        "endianness": target_spec.endianness,
        "pointer_width": target_spec.pointer_width,
        "address_space": target_spec.address_space,
        "abi_policy": target_spec.abi_policy,
        "abi_alignment": descriptor.abi_alignment,
        "preferred_alignment": descriptor.preferred_alignment,
        "size": descriptor.size,
        "representation_kind": descriptor.representation_kind,
        "packing": descriptor.packing,
        "abi_class": descriptor.abi_class,
        "fields": [
            [offset, field_layouts[name].value]
            for name, _type_name, offset in descriptor.fields
        ],
        "variant_tag_encoding": descriptor.variant_tag_encoding,
        "variants": [
            [
                tag,
                layout_id.value if layout_id is not None else None,
            ]
            for name, _payload, tag in descriptor.variants
            for layout_id, _variant_tag in (variant_layouts[name],)
        ],
        "payload_offsets": list(descriptor.payload_offsets),
        "niche_policy": descriptor.niche_policy,
        "element_layout_id": (
            descriptor.element_layout_id.value
            if descriptor.element_layout_id is not None
            else None
        ),
        "payload_layout_id": (
            descriptor.payload_layout_id.value
            if descriptor.payload_layout_id is not None
            else None
        ),
        "key_layout_id": (
            descriptor.key_layout_id.value
            if descriptor.key_layout_id is not None
            else None
        ),
        "value_layout_id": (
            descriptor.value_layout_id.value
            if descriptor.value_layout_id is not None
            else None
        ),
        "length": descriptor.length,
    }


def layout_id_for_descriptor(
    descriptor: TypeDescriptor,
    target_spec: TargetSpec = DEFAULT_TARGET_SPEC,
) -> LayoutId:
    payload = _layout_payload(descriptor, target_spec)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return LayoutId(hashlib.sha256(encoded).hexdigest())

def _resolve_type_id(
    authority: TypeContext,
    type_name: str,
) -> TypeId:
    try:
        return authority.type_id(type_name)
    except TypeArenaError as exc:
        raise RepresentationCompileError(
            f"unknown structural type identity: {type_name}"
        ) from exc


def _type_ref(
    authority: TypeContext,
    type_id: TypeId,
) -> TypeRef:
    try:
        return authority.resolve(type_id)
    except TypeArenaError as exc:
        raise RepresentationCompileError(
            f"unknown structural type identity: {type_id.value}"
        ) from exc


def _generic(
    type_id: TypeId,
    authority: TypeContext,
) -> TypeRef | None:
    reference = _type_ref(authority, type_id)
    return reference if reference.arguments else None


def _map_types(
    type_id: TypeId,
    authority: TypeContext,
) -> tuple[TypeId, TypeId] | None:
    reference = _type_ref(authority, type_id)
    if reference.constructor != "Map" or len(reference.arguments) != 2:
        return None
    return reference.arguments  # type: ignore[return-value]


def _map_entry_types(
    type_id: TypeId,
    authority: TypeContext,
) -> tuple[TypeId, TypeId] | None:
    reference = _type_ref(authority, type_id)
    if reference.constructor != "MapEntry" or len(reference.arguments) != 2:
        return None
    return reference.arguments  # type: ignore[return-value]


def _array_parts(
    type_id: TypeId,
    authority: TypeContext,
) -> tuple[TypeId, int] | None:
    reference = _type_ref(authority, type_id)
    if reference.constructor != "Array" or len(reference.arguments) != 2:
        return None
    try:
        length = int(authority.render(reference.arguments[1]))
    except (TypeError, ValueError):
        return None
    if length < 0 or length > MAX_U64:
        return None
    return reference.arguments[0], length


def _callback_parts(
    type_id: TypeId,
    authority: TypeContext,
) -> tuple[tuple[TypeId, ...], TypeId] | None:
    reference = _type_ref(authority, type_id)
    if reference.constructor not in {"Fn", "Closure"} or len(reference.arguments) < 2:
        return None
    return reference.arguments[:-1], reference.arguments[-1]


class _LayoutParseError(ValueError):
    def __init__(self, type_name: str, path: tuple[str, ...], reason: str) -> None:
        self.type_name = type_name
        self.path = path
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, order=True)
class _LayoutDependency:
    target: str
    indirect: bool
    path: tuple[str, ...]


@dataclass(frozen=True, order=True)
class _LayoutEdge:
    target: str
    path: tuple[str, ...]


_INDIRECT_LAYOUT_WRAPPERS = frozenset(
    {
        "Box",
        "Vec",
        "Map",
        "Borrow",
        "Slice",
        "Fn",
        "Closure",
    }
)
_LAYOUT_BRANCH_NAMES: dict[str, tuple[str, ...]] = {
    "Option": ("payload",),
    "Result": ("ok", "error"),
    "Array": ("element", "length"),
    "Box": ("payload",),
    "Vec": ("element",),
    "Map": ("key", "value"),
    "Borrow": ("payload",),
    "Slice": ("element",),
}


def _layout_dependencies(
    type_id: TypeId,
    nominal_ids: frozenset[TypeId],
    authority: TypeContext,
    *,
    path: tuple[str, ...] = (),
) -> tuple[_LayoutDependency, ...]:
    """Return nested nominal dependencies from validated TypeRefs only."""

    try:
        _type_ref(authority, type_id)
    except RepresentationCompileError as exc:
        raise _LayoutParseError(
            str(type_id),
            path,
            str(exc),
        ) from exc

    def visit(
        current_id: TypeId,
        current_path: tuple[str, ...],
        indirect: bool,
    ) -> tuple[_LayoutDependency, ...]:
        reference = _type_ref(authority, current_id)
        if current_id in nominal_ids:
            return (
                _LayoutDependency(
                    authority.render(current_id),
                    indirect,
                    current_path,
                ),
            )
        if reference.constructor in {"RawPointer", "Ptr", "MutPointer"}:
            if len(reference.arguments) != 1:
                raise _LayoutParseError(
                    authority.render(current_id),
                    current_path,
                    "pointer requires one pointee identity",
                )
            return visit(
                reference.arguments[0],
                (*current_path, f"{reference.constructor}.pointee"),
                True,
            )
        if not reference.arguments:
            return ()
        arguments = (
            reference.arguments[:1]
            if reference.constructor == "Array"
            else reference.arguments
        )
        labels = _LAYOUT_BRANCH_NAMES.get(reference.constructor, ())
        crossed_indirection = (
            indirect or reference.constructor in _INDIRECT_LAYOUT_WRAPPERS
        )
        dependencies: list[_LayoutDependency] = []
        for index, argument in enumerate(arguments):
            label = (
                labels[index]
                if index < len(labels)
                else (
                    "return"
                    if reference.constructor in {"Fn", "Closure"}
                    and index == len(arguments) - 1
                    else f"argument[{index}]"
                )
            )
            if (
                reference.constructor in {"Fn", "Closure"}
                and index < len(arguments) - 1
            ):
                label = f"parameter[{index}]"
            dependencies.extend(
                visit(
                    argument,
                    (*current_path, f"{reference.constructor}.{label}"),
                    crossed_indirection,
                )
            )
        return tuple(dependencies)

    try:
        return tuple(sorted(set(visit(type_id, path, False))))
    except TypeArenaError as exc:
        raise _LayoutParseError(
            authority.render(type_id) if type_id in authority.arena else str(type_id),
            path,
            str(exc),
        ) from exc


def _minimal_inline_cycle(
    graph: dict[str, tuple[_LayoutEdge, ...]],
) -> tuple[tuple[str, ...], tuple[_LayoutEdge, ...]] | None:
    best: tuple[
        tuple[
            int,
            tuple[str, ...],
            tuple[tuple[str, tuple[str, ...], str], ...],
        ],
        tuple[str, ...],
        tuple[_LayoutEdge, ...],
    ] | None = None
    for start in sorted(graph):
        queue = deque([(start, (start,), ())])
        best_paths: dict[
            str,
            tuple[
                int,
                tuple[str, ...],
                tuple[tuple[str, tuple[str, ...], str], ...],
            ],
        ] = {start: (0, (start,), ())}
        while queue:
            current, nodes, edges = queue.popleft()
            for edge in graph[current]:
                next_edges = (*edges, edge)
                if edge.target == start:
                    cycle_nodes = (*nodes, start)
                    structural = tuple(
                        (nodes[index], item.path, item.target)
                        for index, item in enumerate(next_edges)
                    )
                    key = (len(next_edges), cycle_nodes, structural)
                    candidate = (key, cycle_nodes, next_edges)
                    if best is None or candidate[0] < best[0]:
                        best = candidate
                    continue
                if edge.target in nodes:
                    continue
                path_key = (
                    len(next_edges),
                    (*nodes, edge.target),
                    tuple(
                        (nodes[index], item.path, item.target)
                        for index, item in enumerate(next_edges)
                    ),
                )
                previous = best_paths.get(edge.target)
                if previous is not None and previous <= path_key:
                    continue
                best_paths[edge.target] = path_key
                queue.append((edge.target, (*nodes, edge.target), next_edges))
    if best is None:
        return None
    return best[1], best[2]


def _render_layout_cycle(
    nodes: tuple[str, ...],
    edges: tuple[_LayoutEdge, ...],
) -> str:
    parts = [nodes[0]]
    for edge in edges:
        parts.append(f"--{'/'.join(edge.path)}--> {edge.target}")
    return " ".join(parts)


def validate_recursive_layouts(
    types: Iterable[HIRTypeDecl],
    authority: TypeContext | None = None,
) -> LayoutValidation:
    declarations = {item.name: item for item in types}
    if authority is None:
        return LayoutValidation(
            False,
            (),
            (),
            "LayoutTypeContextRequired: pass the frozen HIR TypeContext",
        )
    nominal_ids = frozenset(item.type_id for item in declarations.values())
    edge_graph: dict[str, set[_LayoutEdge]] = {
        name: set() for name in declarations
    }
    dependencies_by_declaration: dict[str, tuple[_LayoutDependency, ...]] = {}
    for declaration in declarations.values():
        if declaration.kind == "record":
            members = (
                (field.type_id, (f"field[{field.name}]",))
                for field in declaration.fields
            )
        else:
            members = (
                (variant.payload_type_id, (f"variant[{variant.name}]",))
                for variant in declaration.variants
                if variant.payload_type_id is not None
            )
        dependencies: list[_LayoutDependency] = []
        try:
            for type_id, path in members:
                assert type_id is not None
                dependencies.extend(
                    _layout_dependencies(
                        type_id,
                        nominal_ids,
                        authority,
                        path=path,
                    )
                )
        except (TypeArenaError, _LayoutParseError) as exc:
            location = (
                "/".join(exc.path)
                if isinstance(exc, _LayoutParseError)
                else "<root>"
            )
            type_name = (
                exc.type_name
                if isinstance(exc, _LayoutParseError)
                else declaration.name
            )
            reason = exc.reason if isinstance(exc, _LayoutParseError) else str(exc)
            return LayoutValidation(
                False,
                (),
                (),
                f"MalformedLayoutType: {type_name} at {location}: {reason}",
            )
        dependencies_by_declaration[declaration.name] = tuple(dependencies)
    for declaration in declarations.values():
        for dependency in dependencies_by_declaration[declaration.name]:
            if not dependency.indirect:
                edge_graph[declaration.name].add(
                    _LayoutEdge(dependency.target, dependency.path)
                )
    graph = {
        name: tuple(sorted(edges))
        for name, edges in sorted(edge_graph.items())
    }
    ordered_graph = tuple(
        (
            name,
            tuple(sorted({edge.target for edge in edges})),
        )
        for name, edges in graph.items()
    )
    cycle = _minimal_inline_cycle(graph)
    if cycle is not None:
        nodes, edges = cycle
        text = _render_layout_cycle(nodes, edges)
        return LayoutValidation(
            False,
            ordered_graph,
            nodes,
            f"InlineRecursiveLayout: {text}; add Box or Vec indirection",
        )
    return LayoutValidation(True, ordered_graph)


def require_valid_recursive_layouts(
    types: Iterable[HIRTypeDecl],
    authority: TypeContext | None = None,
) -> LayoutValidation:
    validation = validate_recursive_layouts(types, authority)
    if not validation.accepted:
        raise RepresentationCompileError(
            validation.diagnostic or "invalid recursive layout"
        )
    return validation


class _DescriptorBuilder:
    def __init__(
        self,
        hir: StructuredHIRProgram,
        target_spec: TargetSpec = DEFAULT_TARGET_SPEC,
    ) -> None:
        self.hir = hir
        self.authority = hir.type_context
        self.target_spec = target_spec
        self.pointer_size = target_spec.pointer_width // 8
        self.declarations = {item.name: item for item in hir.types}
        self.type_properties = TypePropertyResolver(hir.type_context)
        self.nominal_ids = frozenset(item.type_id for item in hir.types)
        self.descriptors: dict[str, TypeDescriptor] = {}
        for name, (size, alignment) in _SCALARS.items():
            self.descriptors[name] = ScalarDesc(
                name,
                "scalar",
                size,
                alignment,
                "void" if name == "Unit" else "scalar",
                "trivial",
                "copy",
                "trivial",
                (),
                (),
                _stable_id("type", "builtin", name),
            )
        self.descriptors["Bytes"] = BytesDesc(
            "Bytes",
            "bytes",
            self.pointer_size * 3,
            self.pointer_size,
            "aggregate",
            "forbidden",
            "bitwise_then_invalidate",
            "owner_free",
            (),
            (),
            _stable_id("type", "builtin", "Bytes"),
        )
        self.descriptors["BytesView"] = BorrowDesc(
            "BytesView",
            "borrow",
            self.pointer_size * 2,
            self.pointer_size,
            "aggregate",
            "trivial",
            "copy",
            "trivial",
            (),
            ("Bytes",),
            _stable_id("type", "builtin", "BytesView"),
            payload_type="Bytes",
        )
        self.descriptors["TextView"] = BorrowDesc(
            "TextView",
            "borrow",
            self.pointer_size * 2,
            self.pointer_size,
            "aggregate",
            "trivial",
            "copy",
            "trivial",
            (),
            ("Text",),
            _stable_id("type", "builtin", "TextView"),
            payload_type="Text",
        )
        self.descriptors["Text"] = TextDesc(
            "Text",
            "text",
            self.pointer_size * 2,
            self.pointer_size,
            "aggregate",
            "forbidden",
            "bitwise_then_invalidate",
            "owner_free",
            (),
            (),
            _stable_id("type", "builtin", "Text"),
        )
        self.descriptors["TextBuilder"] = RecordDesc(
            "TextBuilder",
            "record",
            self.pointer_size * 4,
            self.pointer_size,
            "aggregate",
            "forbidden",
            "bitwise_then_invalidate",
            "builder_free",
            (),
            (),
            _stable_id("type", "builtin", "TextBuilder"),
        )
        self.descriptors["Path"] = TextDesc(
            "Path",
            "text",
            self.pointer_size * 2,
            self.pointer_size,
            "aggregate",
            "forbidden",
            "bitwise_then_invalidate",
            "owner_free",
            (),
            (),
            _stable_id("type", "builtin", "Path"),
        )
        for name, declaration in sorted(self.declarations.items()):
            if name in self.descriptors:
                raise RepresentationCompileError(
                    f"nominal type conflicts with builtin representation: {name}"
                )
            self.descriptors[name] = (
                RecordDesc(
                    name,
                    "record",
                    0,
                    1,
                    "aggregate",
                    "forbidden",
                    "bitwise_then_invalidate",
                    "fieldwise",
                    (),
                    (),
                    declaration.symbol_id,
                )
                if declaration.kind == "record"
                else EnumDesc(
                    name,
                    "enum",
                    0,
                    1,
                    "aggregate",
                    "forbidden",
                    "bitwise_then_invalidate",
                    "tag_switch",
                    (),
                    (),
                    declaration.symbol_id,
            )
            )

    def build(self) -> tuple[TypeDescriptor, ...]:
        require_valid_recursive_layouts(self.hir.types, self.authority)
        completed: set[str] = set()
        self.completed = completed
        for name in sorted(self.declarations):
            self._finalize_nominal(name, completed)
        referenced = {
            type_name
            for declaration in self.hir.types
            for type_name in (
                [field.type_name for field in declaration.fields]
                + [
                    variant.payload_type
                    for variant in declaration.variants
                    if variant.payload_type
                ]
            )
        }
        referenced.update(
            type_name
            for function in self.hir.functions
            for type_name in (
                [parameter.type_name for parameter in function.parameters]
                + [function.return_type]
                + [node.type_name for node in function.walk() if node.type_name]
            )
            if type_name != "Inferred" and "Inferred]" not in type_name
        )
        for type_name in sorted(referenced):
            self.get(type_name)
        for name, descriptor in tuple(self.descriptors.items()):
            type_id = _resolve_type_id(self.authority, name)
            properties = self.type_properties.resolve(type_id)
            self.descriptors[name] = replace(
                descriptor,
                contains_borrow=properties.contains_borrow,
                contains_resource=(
                    properties.is_resource or properties.contains_resource
                ),
                contained_borrow_types=tuple(
                    self.authority.render(item) for item in properties.borrow_types
                ),
                contained_resource_types=tuple(
                    self.authority.render(item)
                    for item in properties.resource_types
                ),
                contained_borrow_type_ids=tuple(properties.borrow_types),
                contained_resource_type_ids=tuple(properties.resource_types),
            )
        for name, descriptor in tuple(self.descriptors.items()):
            self.descriptors[name] = self._attach_descriptor_identities(descriptor)
        for _ in range(len(self.descriptors) + 1):
            changed = False
            for name in sorted(self.descriptors):
                descriptor = self.descriptors[name]
                try:
                    candidate = self._attach_layout_identity(descriptor)
                except ValueError as exc:
                    if "missing child" in str(exc):
                        continue
                    raise
                if candidate != descriptor:
                    self.descriptors[name] = candidate
                    changed = True
            if not changed:
                break
        missing = [
            descriptor.name
            for descriptor in self.descriptors.values()
            if descriptor.layout_id is None
        ]
        if missing:
            raise RepresentationCompileError(
                "unable to resolve physical child LayoutIds: "
                + ", ".join(sorted(missing))
            )
        return tuple(sorted(self.descriptors.values(), key=lambda item: item.name))

    def _attach_descriptor_identities(
        self,
        descriptor: TypeDescriptor,
    ) -> TypeDescriptor:
        type_id = _resolve_type_id(self.authority, descriptor.name)

        def optional(name: str | None) -> TypeId | None:
            return (
                _resolve_type_id(self.authority, name)
                if name is not None
                else None
            )

        field_type_ids = tuple(
            (name, _resolve_type_id(self.authority, type_name))
            for name, type_name, _offset in descriptor.fields
        )
        variant_type_ids = tuple(
            (name, optional(payload), tag)
            for name, payload, tag in descriptor.variants
        )
        return replace(
            descriptor,
            type_id=type_id,
            inline_dependency_ids=tuple(
                _resolve_type_id(self.authority, name)
                for name in descriptor.inline_dependencies
            ),
            indirect_dependency_ids=tuple(
                _resolve_type_id(self.authority, name)
                for name in descriptor.indirect_dependencies
            ),
            field_type_ids=field_type_ids,
            variant_type_ids=variant_type_ids,
            element_type_id=optional(descriptor.element_type),
            payload_type_id=optional(descriptor.payload_type),
            key_type_id=optional(descriptor.key_type),
            value_type_id=optional(descriptor.value_type),
        )

    def _child_layout(self, type_name: str | None) -> LayoutId | None:
        if type_name is None:
            return None
        child = self.descriptors.get(type_name)
        return child.layout_id if child is not None else None

    def _attach_layout_identity(self, descriptor: TypeDescriptor) -> TypeDescriptor:
        field_layout_ids = tuple(
            (name, layout_id)
            for name, type_name, _offset in descriptor.fields
            if (layout_id := self._child_layout(type_name)) is not None
        )
        if len(field_layout_ids) != len(descriptor.fields):
            raise ValueError(f"descriptor {descriptor.name} missing child fields")
        variant_layout_ids = tuple(
            (name, self._child_layout(payload), tag)
            for name, payload, tag in descriptor.variants
        )
        if any(
            payload is not None and layout_id is None
            for (_name, payload, _tag), (_vname, layout_id, _vtag) in zip(
                descriptor.variants,
                variant_layout_ids,
                strict=True,
            )
        ):
            raise ValueError(f"descriptor {descriptor.name} missing child variants")
        element_layout_id = (
            self._child_layout(descriptor.element_type)
            if descriptor.kind == "array"
            else None
        )
        if descriptor.kind == "array" and element_layout_id is None:
            raise ValueError(f"descriptor {descriptor.name} missing child element")
        payload_offsets = descriptor.payload_offsets
        if descriptor.variants and not payload_offsets:
            payload_offsets = tuple(
                0 if payload is None else _align(4, descriptor.alignment)
                for _name, payload, _tag in descriptor.variants
            )
        with_layouts = replace(
            descriptor,
            field_layout_ids=field_layout_ids,
            variant_layout_ids=variant_layout_ids,
            element_layout_id=element_layout_id,
            payload_offsets=payload_offsets,
        )
        return replace(
            with_layouts,
            layout_id=layout_id_for_descriptor(
                with_layouts,
                self.target_spec,
            ),
        )
    def get(self, type_name: str) -> TypeDescriptor:
        return self._get_id(_resolve_type_id(self.authority, type_name))

    def _get_id(self, type_id: TypeId) -> TypeDescriptor:
        type_name = self.authority.render(type_id)
        if type_name in self.descriptors:
            return self.descriptors[type_name]
        reference = _type_ref(self.authority, type_id)
        constructor = reference.constructor
        arguments = reference.arguments
        if constructor in {"RawPointer", "Ptr", "MutPointer"}:
            if len(arguments) != 1:
                raise RepresentationCompileError(
                    f"invalid pointer representation: {type_name}"
                )
            payload_type = self.authority.render(arguments[0])
            descriptor = BorrowDesc(
                type_name,
                "raw_pointer",
                self.pointer_size,
                self.pointer_size,
                "pointer",
                "trivial",
                "copy",
                "trivial",
                (),
                (),
                _stable_id(
                    "type",
                    "raw_pointer",
                    arguments[0].value,
                    constructor,
                ),
                payload_type=payload_type,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if constructor in {"FileReader", "FileWriter"} and not arguments:
            descriptor = RecordDesc(
                type_name,
                "file_reader" if constructor == "FileReader" else "file_writer",
                64 if constructor == "FileReader" else 16,
                8,
                "aggregate",
                "forbidden",
                "bitwise_then_invalidate",
                "file_close",
                (),
                (),
                _stable_id("type", "builtin", constructor),
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if constructor == "FileLines" and not arguments:
            self.get("FileReader")
            descriptor = BorrowDesc(
                type_name,
                "file_lines",
                self.pointer_size * 2,
                self.pointer_size,
                "aggregate",
                "trivial",
                "copy",
                "trivial",
                (),
                ("FileReader",),
                _stable_id("type", "builtin", "FileLines"),
                payload_type="FileReader",
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        array = _array_parts(type_id, self.authority)
        if array is not None:
            element_id, length = array
            element = self._get_id(element_id)
            element_type = self.authority.render(element_id)
            if element.size and length > MAX_U64 // element.size:
                raise RepresentationCompileError(
                    f"array layout overflow: {type_name}"
                )
            descriptor = ArrayDesc(
                type_name,
                "array",
                element.size * length,
                element.alignment,
                "aggregate",
                element.copy_class,
                "bitwise_then_invalidate"
                if element.copy_class == "forbidden"
                else "copy",
                "array_elements"
                if element.drop_class != "trivial"
                else "trivial",
                (element_type,),
                (),
                _stable_id("type", "array", element_id.value, length),
                element_type=element_type,
                length=length,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        callback = _callback_parts(type_id, self.authority)
        if callback is not None:
            parameter_ids, return_id = callback
            dependency_ids = (*parameter_ids, return_id)
            dependencies = tuple(
                self.authority.render(item) for item in dependency_ids
            )
            for dependency_id in dependency_ids:
                self._get_id(dependency_id)
            descriptor = CallbackDesc(
                type_name,
                "closure",
                self.pointer_size * 4,
                self.pointer_size,
                "aggregate",
                "refcounted",
                "move_then_invalidate",
                "closure_environment",
                (),
                dependencies,
                _stable_id(
                    "type",
                    "callback",
                    [item.value for item in dependency_ids],
                ),
                payload_type=self.authority.render(return_id),
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if constructor == "Shared":
            raise RepresentationCompileError(
                f"SharedOwnershipUnsupported: {type_name}"
            )
        map_types = _map_types(type_id, self.authority)
        if map_types is not None:
            key_id, value_id = map_types
            key_type = self.authority.render(key_id)
            value_type = self.authority.render(value_id)
            if key_type != "Text":
                raise RepresentationCompileError(
                    f"unsupported Map specialization {type_name}; alpha Map "
                    "requires Text keys"
                )
            self._get_id(key_id)
            value = self._get_id(value_id)
            descriptor = MapDesc(
                type_name,
                "map",
                self.pointer_size * 5,
                self.pointer_size,
                "aggregate",
                "forbidden",
                "bitwise_then_invalidate",
                (
                    "map_owned_entries_then_buffers"
                    if value.drop_class != "trivial"
                    else "map_owned_keys_then_buffers"
                ),
                (),
                (key_type, value_type),
                _stable_id(
                    "type",
                    "map",
                    key_id.value,
                    value_id.value,
                ),
                key_type=key_type,
                value_type=value_type,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        map_entry_types = _map_entry_types(type_id, self.authority)
        if map_entry_types is not None:
            key_id, value_id = map_entry_types
            self._get_id(key_id)
            self._get_id(value_id)
            descriptor = BorrowDesc(
                type_name,
                "borrow",
                self.pointer_size * 2,
                self.pointer_size,
                "aggregate",
                "trivial",
                "copy",
                "trivial",
                (),
                (
                    self.authority.render(key_id),
                    self.authority.render(value_id),
                ),
                _stable_id(
                    "type",
                    "map_entry",
                    key_id.value,
                    value_id.value,
                ),
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if constructor == "Result" and len(arguments) == 2:
            ok_id, err_id = arguments
            ok = self._get_id(ok_id)
            err = self._get_id(err_id)
            ok_type = self.authority.render(ok_id)
            err_type = self.authority.render(err_id)
            descriptor = EnumDesc(
                type_name,
                "enum",
                max(ok.size, err.size) + 8,
                max(ok.alignment, err.alignment),
                "aggregate",
                "forbidden",
                "bitwise_then_invalidate",
                "tag_switch",
                tuple(sorted({ok_type, err_type})),
                (),
                _stable_id("type", "result", ok_id.value, err_id.value),
                variants=(("Ok", ok_type, 0), ("Err", err_type, 1)),
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if constructor == "Option" and len(arguments) == 1:
            argument_id = arguments[0]
            argument = self._get_id(argument_id)
            argument_type = self.authority.render(argument_id)
            descriptor = EnumDesc(
                type_name,
                "enum",
                argument.size + 8,
                max(4, argument.alignment),
                "aggregate",
                "forbidden",
                "bitwise_then_invalidate",
                "tag_switch",
                (argument_type,),
                (),
                _stable_id("type", "option", argument_id.value),
                variants=(("NoneValue", None, 0), ("Some", argument_type, 1)),
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if constructor == "Slice" and len(arguments) == 1:
            argument_id = arguments[0]
            self._get_id(argument_id)
            argument_type = self.authority.render(argument_id)
            descriptor = SliceDesc(
                type_name,
                "slice",
                self.pointer_size * 2,
                self.pointer_size,
                "aggregate",
                "trivial",
                "copy",
                "trivial",
                (),
                (argument_type,),
                _stable_id("type", "slice", argument_id.value),
                element_type=argument_type,
                payload_type=argument_type,
            )
            self.descriptors[type_name] = descriptor
            return descriptor
        if len(arguments) == 1:
            argument_id = arguments[0]
            argument = self._get_id(argument_id)
            argument_type = self.authority.render(argument_id)
            if constructor == "Vec":
                descriptor = VecDesc(
                    type_name,
                    "vec",
                    self.pointer_size * 4,
                    self.pointer_size,
                    "aggregate",
                    "forbidden",
                    "bitwise_then_invalidate",
                    "vec_elements_then_buffer",
                    (),
                    (argument_type,),
                    _stable_id("type", "vec", argument_id.value),
                    element_type=argument_type,
                )
            elif constructor == "Box":
                descriptor = BoxDesc(
                    type_name,
                    "box",
                    self.pointer_size,
                    self.pointer_size,
                    "pointer",
                    "forbidden",
                    "pointer_then_invalidate",
                    "payload_then_free",
                    (),
                    (argument_type,),
                    _stable_id("type", "box", argument_id.value),
                    payload_type=argument_type,
                )
            elif constructor == "Borrow":
                descriptor = BorrowDesc(
                    type_name,
                    "borrow",
                    self.pointer_size,
                    self.pointer_size,
                    "pointer",
                    "trivial",
                    "copy",
                    "trivial",
                    (),
                    (argument_type,),
                    _stable_id("type", "borrow", argument_id.value),
                    payload_type=argument_type,
                )
            else:
                raise RepresentationCompileError(
                    f"unsupported representation constructor: {constructor}"
                )
            self.descriptors[type_name] = descriptor
            return descriptor
        if type_name in self.declarations:
            return self.descriptors[type_name]
        aliases = [
            name
            for name in self.declarations
            if _type_leaf(name) == type_name
        ]
        if len(aliases) == 1:
            target_name = aliases[0]
            if target_name not in self.completed:
                self._finalize_nominal(target_name, self.completed)
            alias = replace(
                self.descriptors[target_name],
                name=type_name,
                type_id=None,
                layout_id=None,
            )
            self.descriptors[type_name] = alias
            return alias
        raise RepresentationCompileError(f"unknown representation type: {type_name}")

    def _finalize_nominal(
        self,
        name: str,
        completed: set[str],
    ) -> TypeDescriptor:
        if name in completed:
            return self.descriptors[name]
        declaration = self.declarations[name]
        if declaration.kind == "record":
            members = (
                (field.type_id, (f"field[{field.name}]",))
                for field in declaration.fields
            )
        else:
            members = (
                (
                    variant.payload_type_id,
                    (f"variant[{variant.name}]",),
                )
                for variant in declaration.variants
                if variant.payload_type_id is not None
            )
        dependencies = tuple(
            dependency
            for type_id, path in members
            for dependency in _layout_dependencies(
                type_id,
                self.nominal_ids,
                self.authority,
                path=path,
            )
        )
        for dependency in dependencies:
            if not dependency.indirect:
                self._finalize_nominal(dependency.target, completed)

        if declaration.kind == "record":
            offset = 0
            alignment = 1
            fields = []
            inline = []
            indirect = []
            trivial = True
            for field in declaration.fields:
                field_descriptor = self._get_id(field.type_id)
                alignment = max(alignment, field_descriptor.alignment)
                offset = _align(offset, field_descriptor.alignment)
                fields.append((field.name, field.type_name, offset))
                offset += field_descriptor.size
                for dependency in _layout_dependencies(
                    field.type_id,
                    self.nominal_ids,
                    self.authority,
                    path=(f"field[{field.name}]",),
                ):
                    (indirect if dependency.indirect else inline).append(
                        dependency.target
                    )
                trivial = trivial and field_descriptor.drop_class == "trivial"
            descriptor: TypeDescriptor = RecordDesc(
                name,
                "record",
                _align(offset, alignment),
                alignment,
                "aggregate",
                "trivial" if trivial else "forbidden",
                "copy" if trivial else "bitwise_then_invalidate",
                "trivial" if trivial else "fieldwise",
                tuple(sorted(set(inline))),
                tuple(sorted(set(indirect))),
                declaration.symbol_id,
                fields=tuple(fields),
                invariants=tuple(
                    (
                        invariant.function_name,
                        invariant.source.line,
                    )
                    for invariant in declaration.invariants
                ),
            )
        else:
            maximum_size = 0
            maximum_alignment = 1
            variants = []
            inline = []
            indirect = []
            trivial = True
            for variant in declaration.variants:
                if variant.payload_type_id is not None:
                    payload_descriptor = self._get_id(variant.payload_type_id)
                    maximum_size = max(maximum_size, payload_descriptor.size)
                    maximum_alignment = max(
                        maximum_alignment,
                        payload_descriptor.alignment,
                    )
                    for dependency in _layout_dependencies(
                        variant.payload_type_id,
                        self.nominal_ids,
                        self.authority,
                        path=(f"variant[{variant.name}]",),
                    ):
                        (indirect if dependency.indirect else inline).append(
                            dependency.target
                        )
                    trivial = trivial and payload_descriptor.drop_class == "trivial"
                variants.append(
                    (variant.name, variant.payload_type, variant.tag)
                )
            payload_offset = _align(4, maximum_alignment)
            descriptor = EnumDesc(
                name,
                "enum",
                _align(payload_offset + maximum_size, maximum_alignment),
                maximum_alignment,
                "aggregate",
                "trivial" if trivial else "forbidden",
                "copy" if trivial else "bitwise_then_invalidate",
                "trivial" if trivial else "tag_switch",
                tuple(sorted(set(inline))),
                tuple(sorted(set(indirect))),
                declaration.symbol_id,
                variants=tuple(variants),
            )
        self.descriptors[name] = descriptor
        completed.add(name)
        return descriptor



def _align(value: int, alignment: int) -> int:
    remainder = value % alignment
    return value if remainder == 0 else value + alignment - remainder


def build_type_descriptors(
    hir: StructuredHIRProgram,
    target_spec: TargetSpec = DEFAULT_TARGET_SPEC,
) -> tuple[TypeDescriptor, ...]:
    require_valid_recursive_layouts(hir.types, hir.type_context)
    return _DescriptorBuilder(hir, target_spec).build()


def storage_policy_matrix(
    descriptors: Iterable[TypeDescriptor],
) -> dict[str, StoragePolicy]:
    policies: dict[str, StoragePolicy] = {}
    for descriptor in descriptors:
        if descriptor.kind in {"borrow", "slice"}:
            storage = "borrowed_view"
        elif descriptor.copy_class == "forbidden":
            storage = "unique_owner"
        elif descriptor.abi_class == "aggregate":
            storage = "inline_value"
        else:
            storage = "inline_copy"
        partial_initialization = (
            "initialized_fields"
            if descriptor.kind == "record"
            else "active_variant"
            if descriptor.kind == "enum"
            else "initialized_elements"
            if descriptor.kind in {"array", "vec", "map"}
            else "all_or_nothing"
        )
        policies[descriptor.name] = StoragePolicy(
            storage,
            descriptor.copy_class,
            descriptor.move_class,
            descriptor.drop_class,
            partial_initialization,
        )
    return policies


def build_drop_plans(
    descriptors: Iterable[TypeDescriptor],
    *,
    recursion_limit: int = 128,
) -> tuple[DropPlan, ...]:
    table = {item.name: item for item in descriptors}

    def plan(type_name: str, visiting: tuple[str, ...] = ()) -> DropPlan:
        descriptor = table[type_name]
        if descriptor.drop_class == "trivial":
            return DropPlan(type_name, "trivial")
        if type_name in visiting:
            return DropPlan(
                type_name,
                "recursive_reference",
                depth_limited_by_program=recursion_limit,
            )
        path = visiting + (type_name,)
        if descriptor.kind in {"text", "bytes"}:
            return DropPlan(type_name, "owner_free")
        if descriptor.kind == "record":
            children = tuple(
                replace(plan(field_type, path), field_name=field_name)
                for field_name, field_type, _ in descriptor.fields
                if table[field_type].drop_class != "trivial"
            )
            return DropPlan(type_name, "record_fieldwise", children)
        if descriptor.kind == "enum":
            children = tuple(
                replace(plan(payload, path), variant_name=variant)
                for variant, payload, _ in descriptor.variants
                if payload is not None and table[payload].drop_class != "trivial"
            )
            return DropPlan(type_name, "enum_active_payload", children)
        if descriptor.kind == "array":
            assert descriptor.element_type is not None
            return DropPlan(
                type_name,
                "array_elements",
                (plan(descriptor.element_type, path),),
            )
        if descriptor.kind == "vec":
            assert descriptor.element_type is not None
            return DropPlan(
                type_name,
                "vec_initialized_elements_then_buffer",
                (plan(descriptor.element_type, path),),
            )
        if descriptor.kind == "box":
            assert descriptor.payload_type is not None
            return DropPlan(
                type_name,
                "box_payload_then_free",
                (plan(descriptor.payload_type, path),),
            )
        if descriptor.kind == "map":
            assert descriptor.key_type is not None
            assert descriptor.value_type is not None
            children = [
                replace(plan(descriptor.key_type, path), field_name="key")
            ]
            if table[descriptor.value_type].drop_class != "trivial":
                children.append(
                    replace(
                        plan(descriptor.value_type, path),
                        field_name="value",
                    )
                )
            return DropPlan(
                type_name,
                descriptor.drop_class,
                tuple(children),
            )
        if descriptor.name == "TextBuilder":
            return DropPlan(type_name, "builder_buffer_free")
        return DropPlan(type_name, descriptor.drop_class)

    def attach(plan_value: DropPlan) -> DropPlan:
        descriptor = table[plan_value.type_name]
        attached_children = tuple(attach(child) for child in plan_value.children)
        with_ids = replace(
            plan_value,
            children=attached_children,
            type_id=descriptor.type_id,
            layout_id=descriptor.layout_id,
        )
        payload = {
            "type_id": with_ids.type_id,
            "layout_id": with_ids.layout_id,
            "action": with_ids.action,
            "field_name": with_ids.field_name,
            "variant_name": with_ids.variant_name,
            "depth_limited_by_program": with_ids.depth_limited_by_program,
            "children": tuple(child.drop_plan_id for child in attached_children),
        }
        return replace(
            with_ids,
            drop_plan_id=DropPlanId(
                _identity_digest(DROP_PLAN_CONTRACT, payload)
            ),
        )
    return tuple(
        attach(plan(item.name))
        for item in sorted(table.values(), key=lambda item: item.name)
    )


_HIR_TO_RIR = {
    "TypedHole": "typed_hole",
    "RecordConstruct": "construct_record",
    "FieldAccess": "get_field",
    "SetField": "set_field",
    "EnumConstruct": "construct_enum",
    "EnumTag": "read_enum_tag",
    "Match": "match_enum",
    "VecOperation": "vec_operation",
    "CollectionOperation": "collection_operation",
    "ImplicitCallable": "implicit_callable",
    "BoxOperation": "box_operation",
    "BytesTextOperation": "bytes_text_operation",
    "NumericIntrinsic": "numeric_intrinsic",
    "ScalarCast": "scalar_cast",
    "ArrayLiteral": "array_literal",
    "MapOperation": "map_operation",
    "FileOpen": "file_open_read",
    "FileRead": "file_read",
    "FileLines": "file_lines",
    "ResultPropagation": "try_result",
    "AugAssign": "aug_assign",
    "DirectCall": "call",
    "ForeignCall": "foreign_call",
    "UnsafeBlock": "unsafe_block",
    "Continue": "continue",
    "Break": "break",
    "Pass": "pass",
    "Return": "return",
    "LetBinding": "bind_value",
    "VarBinding": "bind_mutable",
    "TypedError": "typed_error",
    "DropValue": "drop_value",
    "Assign": "store_value",
    "If": "if",
    "While": "while",
    "For": "for",
    "CallbackCall": "callback_call",
    "ClosureCreate": "closure_create",
    "Literal": "const",
    "Name": "load_name",
    "Binary": "binary",
    "Boolean": "boolean",
    "Compare": "compare",
    "Unary": "unary",
    "Expression": "expression",
    "Then": "then",
    "Else": "else",
    "LoopBody": "loop_body",
    "MatchCase": "enum_case",
    "Index": "bounds_checked_index",
}


_HIR_TYPE_ID_ATTRIBUTES = frozenset(
    {
        "expected_type_id",
        "result_type_id",
        "error_type_id",
        "numeric_type_id",
        "target_type_id",
        "source_collection_type_id",
        "element_type_id",
        "map_specialization_id",
        "parameter_type_id",
        "return_type_id",
        "callable_parameter_type_id",
        "callable_return_type_id",
        "source_type_id",
        "parameter_type_ids",
        "capture_type_ids",
    }
)


def _lower_operation(
    node: HIRNode,
    authority: TypeContext,
) -> RIROperation:
    op = _HIR_TO_RIR.get(node.kind)
    if op is None:
        raise RepresentationCompileError(
            f"Structured HIR operation has no Representation IR lowering: {node.kind}"
        )
    attributes = dict(node.attributes)
    if node.kind == "VecOperation":
        method = str(attributes.get("callee", "")).rsplit(".", 1)[-1]
        op = {
            "new": "vec_new",
            "push": "vec_push",
            "get": "vec_get",
            "get_mut": "vec_get_mut",
            "len": "vec_len",
            "capacity": "vec_capacity",
            "view": "vec_view",
        }.get(method, "vec_operation")
    elif node.kind == "BoxOperation":
        method = str(attributes.get("callee", "")).rsplit(".", 1)[-1]
        op = {
            "new": "box_new",
            "get": "box_get",
            "get_mut": "box_get_mut",
        }.get(method, "box_operation")
    elif node.kind == "MapOperation":
        method = attributes.get("map_operation")
        specialization = attributes.get("map_specialization")
        specialization_id = attributes.get("map_specialization_id")
        if not isinstance(specialization_id, TypeId):
            raise RepresentationCompileError(
                f"MapOperation missing structural specialization identity: {specialization}"
            )
        if _map_types(specialization_id, authority) is None:
            raise RepresentationCompileError(
                f"invalid Map specialization identity: {specialization}"
            )
        operations = {
            "new": "map_new",
            "increment": "map_increment",
            "get": "map_get",
            "insert": "map_insert",
            "entries": "map_entries",
        }
        if method not in operations:
            raise RepresentationCompileError(
                f"MapOperation has no lowering: {method}"
            )
        op = operations[method]
    provenance = {
        "owned": "unique_owner",
        "owned_contained_borrow": "unique_owner_with_contained_borrow",
        "borrow": "shared_borrow",
        "contained_borrow": "shared_contained_borrow",
        "borrow_mut": "unique_borrow",
        "value": "plain_value",
    }.get(node.ownership, node.ownership)
    revision = _stable_id("rev", "rir", node.revision_id, op, provenance)
    return RIROperation(
        _stable_id("rirop", node.id, op),
        op,
        node.type_name,
        node.source,
        node.symbol_id,
        revision,
        provenance,
        node.effects,
        tuple(node.attributes),
        tuple(_lower_operation(item, authority) for item in node.children),
        node.type_id,
    )


def lower_structured_hir_to_rir(
    hir: StructuredHIRProgram,
    target_spec: TargetSpec = DEFAULT_TARGET_SPEC,
) -> RepresentationProgram:
    descriptors = build_type_descriptors(hir, target_spec)
    plans = build_drop_plans(descriptors)
    functions = tuple(
        RIRFunction(
            function.name,
            function.symbol_id,
            _stable_id("rev", "rir-function", function.revision_id),
            tuple(
                (item.name, item.type_name, item.ownership)
                for item in function.parameters
            ),
            function.return_type,
            function.effects,
            tuple(_lower_operation(item, hir.type_context) for item in function.body),
            function.source,
            function.borrow_summary,
            tuple(item.type_id for item in function.parameters),
            function.return_type_id,
        )
        for function in hir.functions
    )
    return RepresentationProgram(
        hir.digest,
        hir.source_sha256,
        hir.entry_function,
        descriptors,
        plans,
        functions,
        type_arena=hir.type_context.arena,
        type_arena_digest=hir.type_arena_digest,
        type_arena_contract=TYPE_ARENA_CONTRACT,
        predecessor_digest=hir.digest,
        target_spec=target_spec,
        target_spec_digest=target_spec.digest,
    )


__all__ = [
    "BorrowDesc",
    "BoxDesc",
    "BytesDesc",
    "DropPlan",
    "EnumDesc",
    "LAYOUT_ID_CONTRACT",
    "LAYOUT_ID_SCHEMA_VERSION",
    "LayoutId",
    "LayoutValidation",
    "MAX_U64",
    "RIROperation",
    "MapDesc",
    "RIRFunction",
    "RecordDesc",
    "RepresentationCompileError",
    "RepresentationProgram",
    "REPRESENTATION_IR_CONTRACT",
    "REPRESENTATION_IR_SCHEMA_VERSION",
    "ScalarDesc",
    "TextDesc",
    "StoragePolicy",
    "TypeDescriptor",
    "VecDesc",
    "build_drop_plans",
    "build_type_descriptors",
    "layout_id_for_descriptor",
    "lower_structured_hir_to_rir",
    "storage_policy_matrix",
    "require_valid_recursive_layouts",
    "validate_recursive_layouts",
    "verify_representation_program",
]
