from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from merlo.refactor import preview_fill_hole
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError
from merlo.synthesis import (
    CandidateRank,
    SynthesisCandidate,
    SynthesisRequest,
    build_synthesis_candidate,
)


LLM_SYNTHESIS_PAYLOAD_SCHEMA_VERSION = 1
LLM_SYNTHESIS_PAYLOAD_CONTRACT = "merlo.llm-synthesis-payload.v1"
LLM_PRODUCER_REVISION = "llm/v1"
LLM_MAX_CANDIDATES = 32
LLM_MAX_EXPRESSION_LENGTH = 512
LLM_MAX_TEXT_LENGTH = 512
LLM_MAX_ITEMS = 32
_RESPONSE_FIELDS = frozenset({"provider", "model", "revision", "candidates"})


class LLMProvider(Protocol):
    """An explicitly injected, synchronous provider boundary.

    The provider receives one JSON-compatible semantic payload and must return
    the four-field response envelope documented by ``generate_llm_candidates``.
    This protocol deliberately contains no network or transport methods.
    """

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


Provider = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _request(value: SynthesisRequest | Mapping[str, Any]) -> SynthesisRequest:
    if isinstance(value, SynthesisRequest):
        return value
    if isinstance(value, Mapping):
        return SynthesisRequest.from_dict(value)
    raise WorldError("SynthesisRequestSchemaMismatch")


def _bounded_text(value: Any, code: str, *, limit: int = LLM_MAX_TEXT_LENGTH) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise WorldError(code)
    return value


def _bounded_strings(values: Any, code: str) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > LLM_MAX_ITEMS:
        raise WorldError(code)
    result = []
    for value in values:
        result.append(_bounded_text(value, code, limit=LLM_MAX_TEXT_LENGTH))
    return list(result)
_TRANSFER_PROPERTY_FIELDS = frozenset(
    {
        "is_transferable",
        "is_shareable",
        "is_mutable_shareable",
        "is_resource_transferable",
        "is_thread_safe",
        "is_device_transferable",
        "is_pinned",
        "requires_owner_proof",
    }
)


def _bounded_transfer_properties(value: Any) -> dict[str, dict[str, bool]]:
    if not isinstance(value, Mapping) or len(value) > LLM_MAX_ITEMS:
        raise WorldError("LLMInvalidCapsuleContext")
    result: dict[str, dict[str, bool]] = {}
    for type_name, properties in value.items():
        if (
            type(type_name) is not str
            or not type_name
            or not isinstance(properties, Mapping)
            or set(properties) != _TRANSFER_PROPERTY_FIELDS
            or any(type(item) is not bool for item in properties.values())
        ):
            raise WorldError("LLMInvalidCapsuleContext")
        result[type_name] = {
            name: properties[name]
            for name in sorted(_TRANSFER_PROPERTY_FIELDS)
        }
    return result




def _bounded_hole(hole: Mapping[str, Any]) -> dict[str, Any]:
    """Project one hole to semantic facts, excluding source/path data."""
    required = {"hole_id", "expected_type", "node_id", "context", "callables", "effects", "capabilities"}
    if not isinstance(hole, Mapping) or not required.issubset(hole):
        raise WorldError("LLMInvalidCapsuleHole")
    projected: dict[str, Any] = {
        "hole_id": _bounded_text(hole["hole_id"], "LLMInvalidCapsuleHole"),
        "expected_type": _bounded_text(hole["expected_type"], "LLMInvalidCapsuleHole"),
        "node_id": _bounded_text(hole["node_id"], "LLMInvalidCapsuleHole"),
        "effects": _bounded_strings(hole["effects"], "LLMInvalidCapsuleHole"),
        "capabilities": _bounded_strings(hole["capabilities"], "LLMInvalidCapsuleHole"),
    }
    context = hole["context"]
    if not isinstance(context, (list, tuple)) or len(context) > LLM_MAX_ITEMS:
        raise WorldError("LLMInvalidCapsuleHole")
    context_rows: list[dict[str, str]] = []
    for item in context:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise WorldError("LLMInvalidCapsuleHole")
        name, type_name, ownership = item
        context_rows.append(
            {
                "name": _bounded_text(name, "LLMInvalidCapsuleHole"),
                "type": _bounded_text(type_name, "LLMInvalidCapsuleHole"),
                "ownership": _bounded_text(ownership, "LLMInvalidCapsuleHole"),
            }
        )
    projected["context"] = context_rows
    callables = hole["callables"]
    if not isinstance(callables, (list, tuple)) or len(callables) > LLM_MAX_ITEMS:
        raise WorldError("LLMInvalidCapsuleHole")
    callable_rows: list[dict[str, Any]] = []
    for item in callables:
        if not isinstance(item, Mapping) or set(item) != {"name", "parameters", "return_type", "effects", "capabilities"}:
            raise WorldError("LLMInvalidCapsuleHole")
        parameters = item["parameters"]
        if not isinstance(parameters, (list, tuple)) or len(parameters) > LLM_MAX_ITEMS:
            raise WorldError("LLMInvalidCapsuleHole")
        parameter_rows: list[dict[str, str]] = []
        for parameter in parameters:
            if not isinstance(parameter, (list, tuple)) or len(parameter) != 2:
                raise WorldError("LLMInvalidCapsuleHole")
            parameter_rows.append(
                {
                    "name": _bounded_text(parameter[0], "LLMInvalidCapsuleHole"),
                    "type": _bounded_text(parameter[1], "LLMInvalidCapsuleHole"),
                }
            )
        callable_rows.append(
            {
                "name": _bounded_text(item["name"], "LLMInvalidCapsuleHole"),
                "parameters": parameter_rows,
                "return_type": _bounded_text(item["return_type"], "LLMInvalidCapsuleHole"),
                "effects": _bounded_strings(item["effects"], "LLMInvalidCapsuleHole"),
                "capabilities": _bounded_strings(item["capabilities"], "LLMInvalidCapsuleHole"),
            }
        )
    projected["callables"] = callable_rows
    return projected


def _bounded_capsule(capsule: Any, hole_id: str) -> tuple[dict[str, Any], str]:
    if not isinstance(capsule.world_digest, str) or not capsule.world_digest:
        raise WorldError("LLMCapsuleBindingMismatch")
    matching = tuple(item for item in capsule.holes if isinstance(item, Mapping) and item.get("hole_id") == hole_id)
    if len(matching) != 1:
        raise WorldError("LLMHoleBindingMismatch")
    hole = _bounded_hole(matching[0])
    target = capsule.target.to_dict()
    target = {key: _bounded_text(target[key], "LLMInvalidCapsuleTarget") for key in target if key != "public_boundary"}
    target["public_boundary"] = capsule.target.public_boundary
    capsule_payload: dict[str, Any] = {
        "schema_version": capsule.schema_version,
        "contract": capsule.contract,
        "digest": capsule.digest,
        "world_digest": _bounded_text(capsule.world_digest, "LLMCapsuleBindingMismatch"),
        "target_revision_id": _bounded_text(capsule.target_revision_id, "LLMCapsuleBindingMismatch"),
        "goal": capsule.goal,
        "target": target,
        "signature": _bounded_text(capsule.signature, "LLMInvalidCapsuleText"),
        "dependent_types": _bounded_strings(capsule.dependent_types, "LLMInvalidCapsuleContext"),
        "effects": _bounded_strings(capsule.effects, "LLMInvalidCapsuleContext"),
        "capabilities": _bounded_strings(capsule.capabilities, "LLMInvalidCapsuleContext"),
        "transfer_properties": _bounded_transfer_properties(capsule.transfer_properties),
        "ownership": _bounded_strings(capsule.ownership, "LLMInvalidCapsuleContext"),
        "resources": _bounded_strings(capsule.resources, "LLMInvalidCapsuleContext"),
        "requirements": _bounded_strings(capsule.requirements, "LLMInvalidCapsuleContext"),
        "ensures": _bounded_strings(capsule.ensures, "LLMInvalidCapsuleContext"),
        "invariants": _bounded_strings(capsule.invariants, "LLMInvalidCapsuleContext"),
        "hole": hole,
    }
    return capsule_payload, capsule.digest


def _payload(world: SemanticWorld, request: SynthesisRequest, capsule: Any, hole_id: str) -> tuple[dict[str, Any], str]:
    capsule_payload, capsule_digest = _bounded_capsule(capsule, hole_id)
    if capsule.world_digest != world.digest:
        raise StaleWorldError("StaleWorld: synthesis capsule belongs to another world")
    symbol = world.resolve(request.target)
    if capsule.target_revision_id != symbol["revision_id"]:
        raise StaleWorldError("StaleWorld: synthesis capsule target changed")
    if capsule_payload["hole"]["hole_id"] != hole_id:
        raise WorldError("LLMHoleBindingMismatch")
    request_payload = request.to_dict()
    if (
        type(request.goal) is not str
        or len(request.goal)
        > LLM_MAX_TEXT_LENGTH
    ):
        raise WorldError("LLMInvalidRequest")
    return {
        "schema_version": LLM_SYNTHESIS_PAYLOAD_SCHEMA_VERSION,
        "contract": LLM_SYNTHESIS_PAYLOAD_CONTRACT,
        "request": request_payload,
        "capsule": capsule_payload,
    }, capsule_digest


def _response(value: Any, max_candidates: int) -> tuple[str, str, str, tuple[str, ...]]:
    if not isinstance(value, Mapping) or set(value) != _RESPONSE_FIELDS:
        raise WorldError("LLMResponseSchemaMismatch")
    provider = _bounded_text(value.get("provider"), "LLMResponseSchemaMismatch")
    model = _bounded_text(value.get("model"), "LLMResponseSchemaMismatch")
    revision = _bounded_text(value.get("revision"), "LLMResponseSchemaMismatch")
    expressions = value.get("candidates")
    if type(expressions) is not list or len(expressions) > max_candidates:
        raise WorldError("LLMResponseOverLimit")
    seen: set[str] = set()
    normalized: list[str] = []
    for expression in expressions:
        if (
            type(expression) is not str
            or not expression
            or len(expression) > LLM_MAX_EXPRESSION_LENGTH
            or expression != expression.strip()
            or "```" in expression
            or any(ord(char) < 32 for char in expression)
            or expression in seen
        ):
            raise WorldError("LLMResponseInvalidCandidate")
        seen.add(expression)
        normalized.append(expression)
    if normalized != sorted(normalized):
        raise WorldError("LLMResponseNonCanonical")
    return provider, model, revision, tuple(normalized)


def generate_llm_candidates(
    world: SemanticWorld,
    request: SynthesisRequest | Mapping[str, Any],
    provider: LLMProvider | Provider,
) -> tuple[SynthesisCandidate, ...]:
    """Opt-in provider boundary producing read-only, proposed hole fills."""
    if not isinstance(world, SemanticWorld):
        raise WorldError("SynthesisWorldMismatch")
    if not callable(provider):
        raise WorldError("LLMProviderMismatch")
    active = _request(request)
    if active.operation != "fill_hole":
        raise WorldError("LLMOperationMismatch")
    if set(active.arguments) not in ({"hole_id"}, {"hole_id", "max_candidates"}):
        raise WorldError("LLMInvalidArguments")
    hole_id = active.arguments.get("hole_id")
    if type(hole_id) is not str or not hole_id:
        raise WorldError("LLMInvalidHoleId")
    max_candidates = active.arguments.get("max_candidates", LLM_MAX_CANDIDATES)
    if type(max_candidates) is not int or not 1 <= max_candidates <= LLM_MAX_CANDIDATES:
        raise WorldError("LLMInvalidMaxCandidates")
    world.require_fresh()
    if active.world_digest != world.digest:
        raise StaleWorldError("StaleWorld: synthesis request belongs to another world")
    capsule = world.compile_context(active.target, goal=active.goal)
    payload, capsule_digest = _payload(world, active, capsule, hole_id)
    try:
        response = provider(payload)
    except Exception as exc:
        raise WorldError("LLMProviderFailure") from exc
    provider_name, model, revision, expressions = _response(response, max_candidates)
    result: list[SynthesisCandidate] = []
    for expression in expressions:
        try:
            change = preview_fill_hole(world, active.target, hole_id, expression)
        except StaleWorldError:
            raise
        except WorldError as exc:
            raise WorldError("LLMInvalidExpression") from exc
        result.append(
            build_synthesis_candidate(
                world,
                active,
                change,
                producer="llm",
                producer_revision=LLM_PRODUCER_REVISION,
                rank=CandidateRank(0, len(expression), expression),
                provenance={
                    "provider": provider_name,
                    "model": model,
                    "revision": revision,
                    "expression": expression,
                    "request_digest": active.digest,
                    "capsule_digest": capsule_digest,
                },
            )
        )
    return tuple(result)


__all__ = [
    "LLM_MAX_CANDIDATES",
    "LLM_MAX_EXPRESSION_LENGTH",
    "LLM_PRODUCER_REVISION",
    "LLM_SYNTHESIS_PAYLOAD_CONTRACT",
    "LLM_SYNTHESIS_PAYLOAD_SCHEMA_VERSION",
    "LLMProvider",
    "generate_llm_candidates",
]
