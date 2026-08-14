from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.archive.historical_protocol.merlo.model import EditCapability
from research.archive.historical_protocol.merlo.world import SoftwareWorld


@dataclass(frozen=True)
class LongHorizonReport:
    steps: int
    entity_id: str
    final_locator: str
    revisions: tuple[str, ...]
    world_revisions: tuple[str, ...]
    evolution_log_entries: int
    valid_evidence: int
    stale_evidence: int
    failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "entity_id": self.entity_id,
            "final_locator": self.final_locator,
            "distinct_entity_revisions": len(set(self.revisions)),
            "distinct_world_revisions": len(set(self.world_revisions)),
            "evolution_log_entries": self.evolution_log_entries,
            "valid_evidence": self.valid_evidence,
            "stale_evidence": self.stale_evidence,
            "failures": list(self.failures),
        }


def run_long_horizon(root: str | Path) -> LongHorizonReport:
    root = Path(root)
    _seed(root)
    state = root / ".merlo" / "world.json"
    world = SoftwareWorld.scan(root, state)
    target = world.program.entity("app.pricing.compute_price")
    entity_id = target.id
    revisions = [target.revision_hash]
    world_revisions = [world.program.world_revision]
    failures: list[str] = []
    completed = 0

    steps: tuple[tuple[str, str, dict[str, str]], ...] = (
        ("rename", "calculate_price", {}),
        ("move", "app.billing", {}),
        ("signature", "(amount, rate=1, *, model=None)", {}),
        ("rename", "price_for_model", {}),
        (
            "signature",
            "(amount, rate=1, *, model=None, region)",
            {"region": "'KG'"},
        ),
        ("move", "app.pricing", {}),
        ("rename", "quote_price", {}),
        (
            "signature",
            "(amount, rate=1, *, model=None, region, trace_id=None)",
            {},
        ),
        ("move", "app.billing", {}),
        ("rename", "final_price", {}),
    )
    for index, (operation, payload, arguments) in enumerate(steps, 1):
        current = world.program.entity(entity_id)
        if operation == "rename":
            capability = EditCapability.rename(
                entity_id, allow_public_api_break=True
            )
            plan = world.plan_rename(entity_id, payload, capability)
        elif operation == "move":
            capability = EditCapability.move(
                entity_id,
                allow_public_api_break=True,
                allow_new_dependencies=True,
            )
            plan = world.plan_move(entity_id, payload, capability)
        else:
            capability = EditCapability.change_signature(
                entity_id, allow_public_api_break=True
            )
            plan = world.plan_change_signature(
                entity_id,
                payload,
                capability,
                argument_values=arguments,
            )
        if not plan.ready:
            failures.append(
                f"step {index} {operation}: "
                + ", ".join(item.kind for item in plan.obligation_graph.blocking)
            )
            break
        world.apply(plan, capability)
        _parse_all(root)
        updated = world.program.entity(entity_id)
        if updated.identity_status != "Exact":
            failures.append(
                f"step {index}: identity became {updated.identity_status}"
            )
            break
        if updated.revision_hash == current.revision_hash:
            failures.append(f"step {index}: semantic revision did not change")
            break
        world = SoftwareWorld.scan(root, state)
        if world.program.entity(entity_id).id != entity_id:
            failures.append(f"step {index}: identity did not survive world reload")
            break
        revisions.append(world.program.entity(entity_id).revision_hash)
        world_revisions.append(world.program.world_revision)
        completed += 1

    final = world.program.entity(entity_id)
    return LongHorizonReport(
        steps=completed,
        entity_id=entity_id,
        final_locator=final.fqname,
        revisions=tuple(revisions),
        world_revisions=tuple(world_revisions),
        evolution_log_entries=len(world.evolution_log),
        valid_evidence=sum(1 for item in world.evidence.values() if item.status == "valid"),
        stale_evidence=sum(1 for item in world.evidence.values() if item.status == "stale"),
        failures=tuple(failures),
    )


def _seed(root: Path) -> None:
    package = root / "app"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pricing.py").write_text(
        "def compute_price(amount, rate=1):\n    return amount * rate\n",
        encoding="utf-8",
    )
    (package / "billing.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        (
            "from app.pricing import compute_price\n\n"
            "def quote(amount):\n"
            "    return compute_price(amount)\n"
        ),
        encoding="utf-8",
    )


def _parse_all(root: Path) -> None:
    for path in root.rglob("*.py"):
        if ".merlo" in path.parts:
            continue
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
