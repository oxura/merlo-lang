from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field as dataclass_field
from types import MappingProxyType
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from merlo.semantic_world import SemanticWorld

SEMANTIC_CAPSULE_SCHEMA_VERSION = 1
SEMANTIC_CAPSULE_CONTRACT = "merlo.semantic-capsule.v1"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(
                    value.items(),
                    key=lambda pair: str(pair[0]),
                )
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(
            sorted(
                (_freeze(item) for item in value),
                key=repr,
            )
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            "SemanticCapsuleNonFiniteNumber"
        )
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_thaw(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SemanticTarget:
    symbol_id: str
    qualified_name: str
    module: str
    name: str
    kind: str
    revision_id: str
    interface_revision_id: str
    implementation_revision_id: str
    public_boundary: bool

    def __post_init__(self) -> None:
        for name in (
            "symbol_id",
            "qualified_name",
            "module",
            "name",
            "kind",
            "revision_id",
            "interface_revision_id",
            "implementation_revision_id",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(
                self,
                name,
            ):
                raise ValueError(f"InvalidSemanticTarget:{name}")
        if not isinstance(self.public_boundary, bool):
            raise ValueError("InvalidSemanticTarget:public_boundary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id, "qualified_name": self.qualified_name,
            "module": self.module, "name": self.name, "revision_id": self.revision_id,
            "kind": self.kind,
            "interface_revision_id": self.interface_revision_id,
            "implementation_revision_id": self.implementation_revision_id,
            "public_boundary": self.public_boundary,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticTarget":
        expected = {
            "symbol_id",
            "qualified_name",
            "module",
            "name",
            "kind",
            "revision_id",
            "interface_revision_id",
            "implementation_revision_id",
            "public_boundary",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("SemanticTargetSchemaMismatch")
        return cls(**{key: value[key] for key in expected})


_CAPSULE_FIELDS = {
    "schema_version", "contract", "digest", "world_digest", "target_revision_id", "goal", "target", "source", "signature",
    "dependent_types", "callers", "callees", "dependencies", "effects", "capabilities", "ownership", "resources",
    "requirements", "ensures", "invariants", "holes", "obligations", "tests", "verification",
}


def _sorted_strings(
    values: Any,
    field: str,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            f"InvalidSemanticCapsule:{field}"
        )
    items = tuple(values)
    if any(
        not isinstance(item, str) or not item
        for item in items
    ):
        raise ValueError(
            f"InvalidSemanticCapsule:{field}"
        )
    if items != tuple(sorted(set(items))):
        raise ValueError(
            f"{field.title()}NotCanonical"
        )
    return items


def _filtered_report(report: Any, obligation_ids: frozenset[str]) -> dict[str, Any]:
    raw = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    result: dict[str, Any] = {
        "schema_version": raw.get("schema_version"), "contract": raw.get("contract"),
        "digest": report.digest if hasattr(report, "digest") else _digest(raw),
    }
    for key in ("hir_digest", "obligation_digest", "backend", "backend_version", "max_cases", "max_values_per_parameter", "timeout_ms", "max_paths", "case_cap"):
        if key in raw:
            result[key] = raw[key]
    for key, value in raw.items():
        if key in result or key in {"schema_version", "contract"}:
            continue
        if isinstance(value, list):
            rows = [row for row in value if isinstance(row, Mapping) and row.get("obligation_id") in obligation_ids]
            if rows:
                result[key] = rows
        elif key == "parameter_bounds":
            result[key] = value
    return result


@dataclass(frozen=True)
class SemanticCapsule:
    world_digest: str
    target_revision_id: str
    goal: str
    target: SemanticTarget
    source: str
    signature: str
    dependent_types: tuple[str, ...] = ()
    callers: tuple[str, ...] = ()
    callees: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    ownership: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    ensures: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    holes: tuple[Mapping[str, Any], ...] = ()
    obligations: tuple[Mapping[str, Any], ...] = ()
    tests: tuple[str, ...] = ()
    verification: Mapping[str, Any] = dataclass_field(
        default_factory=lambda: MappingProxyType({})
    )
    schema_version: int = SEMANTIC_CAPSULE_SCHEMA_VERSION
    contract: str = SEMANTIC_CAPSULE_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_CAPSULE_SCHEMA_VERSION:
            raise ValueError("SemanticCapsuleSchemaVersionMismatch")
        if self.contract != SEMANTIC_CAPSULE_CONTRACT:
            raise ValueError("SemanticCapsuleContractMismatch")
        if not isinstance(self.target, SemanticTarget):
            object.__setattr__(
                self,
                "target",
                SemanticTarget.from_dict(self.target),
            )
        if not isinstance(self.world_digest, str) or not self.world_digest:
            raise ValueError("InvalidSemanticCapsuleWorldDigest")
        if not isinstance(self.target_revision_id, str) or not self.target_revision_id:
            raise ValueError("InvalidSemanticCapsuleTargetRevision")
        if self.target_revision_id != self.target.revision_id:
            raise ValueError("SemanticCapsuleTargetRevisionMismatch")
        if not all(isinstance(getattr(self, field), str) for field in ("goal", "source", "signature")):
            raise ValueError("InvalidSemanticCapsuleText")
        for field in ("dependent_types", "callers", "callees", "dependencies", "effects", "capabilities", "ownership", "resources", "requirements", "ensures", "invariants", "tests"):
            object.__setattr__(self, field, _sorted_strings(getattr(self, field), field))
        for field in ("holes", "obligations"):
            values = getattr(self, field)
            if not isinstance(values, (list, tuple)) or any(
                not isinstance(item, Mapping) for item in values
            ):
                raise ValueError(f"InvalidSemanticCapsule:{field}")
            rows = tuple(_freeze(item) for item in values)
            ids = tuple(
                str(
                    item.get(
                        "obligation_id",
                        item.get("hole_id", ""),
                    )
                )
                for item in rows
            )
            if any(not identifier for identifier in ids):
                raise ValueError(
                    f"Invalid{field.title()}Identity"
                )
            if ids != tuple(sorted(ids)):
                raise ValueError(f"{field.title()}NotCanonical")
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate{field.title()}")
            object.__setattr__(self, field, rows)
        if not isinstance(self.verification, Mapping):
            raise ValueError("InvalidSemanticCapsule:verification")
        object.__setattr__(
            self,
            "verification",
            _freeze(self.verification),
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "contract": self.contract,
            "world_digest": self.world_digest, "target_revision_id": self.target_revision_id,
            "goal": self.goal, "target": self.target.to_dict(), "source": self.source,
            "signature": self.signature, "dependent_types": list(self.dependent_types),
            "callers": list(self.callers), "callees": list(self.callees), "dependencies": list(self.dependencies),
            "effects": list(self.effects), "capabilities": list(self.capabilities), "ownership": list(self.ownership),
            "resources": list(self.resources), "requirements": list(self.requirements), "ensures": list(self.ensures),
            "invariants": list(self.invariants), "holes": _thaw(self.holes), "obligations": _thaw(self.obligations),
            "tests": list(self.tests), "verification": _thaw(self.verification),
        }

    @property
    def digest(self) -> str:
        return _digest(self._payload())

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        payload["digest"] = self.digest
        return payload

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticCapsule":
        if not isinstance(value, Mapping) or set(value) != _CAPSULE_FIELDS:
            raise ValueError("SemanticCapsuleSchemaMismatch")
        if value.get("schema_version") != SEMANTIC_CAPSULE_SCHEMA_VERSION:
            raise ValueError("SemanticCapsuleSchemaVersionMismatch")
        if value.get("contract") != SEMANTIC_CAPSULE_CONTRACT:
            raise ValueError("SemanticCapsuleContractMismatch")
        supplied = value.get("digest")
        payload = dict(value)
        payload.pop("digest", None)
        if not isinstance(supplied, str) or supplied != _digest(payload):
            raise ValueError("SemanticCapsuleDigestMismatch")
        target = SemanticTarget.from_dict(value["target"])
        return cls(
            world_digest=value["world_digest"], target_revision_id=value["target_revision_id"], goal=value["goal"], target=target,
            source=value["source"], signature=value["signature"], dependent_types=tuple(value["dependent_types"]), callers=tuple(value["callers"]),
            callees=tuple(value["callees"]), dependencies=tuple(value["dependencies"]), effects=tuple(value["effects"]), capabilities=tuple(value["capabilities"]),
            ownership=tuple(value["ownership"]), resources=tuple(value["resources"]), requirements=tuple(value["requirements"]), ensures=tuple(value["ensures"]),
            invariants=tuple(value["invariants"]), holes=tuple(value["holes"]), obligations=tuple(value["obligations"]), tests=tuple(value["tests"]),
            verification=value["verification"], schema_version=value["schema_version"], contract=value["contract"],
        )

    @classmethod
    def from_json(cls, payload: str) -> "SemanticCapsule":
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("InvalidSemanticCapsuleJSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("InvalidSemanticCapsuleJSON")
        return cls.from_dict(value)


def extract_semantic_capsule(world: "SemanticWorld", target: str, *, goal: str = "") -> SemanticCapsule:
    symbol = world.resolve(target)
    impact = world.impact(symbol["symbol_id"])
    obligation_ids = frozenset(symbol.get("obligations", ()))
    obligations = tuple(_freeze(item) for item in sorted((item for item in world.data.get("obligations", ()) if item.get("obligation_id") in obligation_ids), key=lambda item: item.get("obligation_id", "")))
    verification = {name: _filtered_report(world.data[name], obligation_ids) for name in ("range_analysis", "bounded_symbolic", "smt", "property_evidence", "verification_metrics") if name in world.data}
    target_data = SemanticTarget(
        symbol_id=symbol["symbol_id"], qualified_name=symbol["qualified_name"], module=symbol["module"], name=symbol["name"], revision_id=symbol["revision_id"],
        kind=symbol["kind"],
        interface_revision_id=symbol["interface_revision_id"], implementation_revision_id=symbol["implementation_revision_id"], public_boundary=bool(symbol["exported"]),
    )
    tests = tuple(item["path"] for item in impact["tests"]) if symbol["exported"] else ()
    return SemanticCapsule(
        world_digest=world.digest, target_revision_id=symbol["revision_id"], goal=goal, target=target_data, source=world.source(symbol["symbol_id"]), signature=symbol["signature"],
        dependent_types=tuple(symbol.get("types", ())), callers=tuple(item["symbol_id"] for item in impact["callers"]), callees=tuple(item["symbol_id"] for item in impact["callees"]), dependencies=tuple(item["symbol_id"] for item in impact["dependencies"]),
        effects=tuple(symbol.get("effects", ())), capabilities=tuple(symbol.get("capabilities", ())), ownership=tuple(symbol.get("ownership", ())), resources=tuple(symbol.get("resources", ())),
        requirements=tuple(symbol.get("requirements", ())), ensures=tuple(symbol.get("ensures", ())), invariants=tuple(symbol.get("invariants", ())), holes=tuple(symbol.get("holes", ())), obligations=obligations, tests=tests, verification=verification,
    )


__all__ = ["SEMANTIC_CAPSULE_CONTRACT", "SEMANTIC_CAPSULE_SCHEMA_VERSION", "SemanticCapsule", "SemanticTarget", "extract_semantic_capsule"]
