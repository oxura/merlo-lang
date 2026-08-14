from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .model import ChangeSignature, MoveSymbol, RenameSymbol, SemanticChange


@dataclass(frozen=True)
class ChangeDescriptor:
    kind: str
    target_id: str
    payload: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def create(
        cls, kind: str, target_id: str, **payload: Any
    ) -> "ChangeDescriptor":
        return cls(kind, target_id, tuple(sorted(payload.items())))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "payload": dict(self.payload),
        }


def describe_change(change: SemanticChange | ChangeDescriptor) -> ChangeDescriptor:
    if isinstance(change, ChangeDescriptor):
        return change
    if isinstance(change, RenameSymbol):
        return ChangeDescriptor.create(
            "Rename", change.target_id, new_name=change.new_name
        )
    if isinstance(change, MoveSymbol):
        return ChangeDescriptor.create(
            "Move", change.target_id, target_module=change.target_module
        )
    if isinstance(change, ChangeSignature):
        return ChangeDescriptor.create(
            "Refine",
            change.target_id,
            new_signature=change.new_signature,
        )
    raise TypeError(f"unsupported semantic change: {type(change).__name__}")


def changes_conflict(
    left: SemanticChange | ChangeDescriptor,
    right: SemanticChange | ChangeDescriptor,
) -> bool:
    first = describe_change(left)
    second = describe_change(right)
    if first.target_id != second.target_id:
        return False
    destructive = {"Remove"}
    if first.kind in destructive or second.kind in destructive:
        return first != second
    if {first.kind, second.kind} == {"Remove", "Replace"}:
        return True
    if first.kind == second.kind and first.kind in {
        "Rename",
        "Move",
        "Replace",
        "Refine",
        "Redirect",
    }:
        return first.payload != second.payload
    if {first.kind, second.kind} == {"Replace", "Refine"}:
        return True
    return False


def changes_commute(
    left: SemanticChange | ChangeDescriptor,
    right: SemanticChange | ChangeDescriptor,
) -> bool:
    first = describe_change(left)
    second = describe_change(right)
    if changes_conflict(first, second):
        return False
    if first.target_id != second.target_id:
        return True
    return {first.kind, second.kind} == {"Rename", "Move"} or first == second


def compose_changes(
    changes: Iterable[SemanticChange | ChangeDescriptor],
) -> tuple[ChangeDescriptor, ...]:
    descriptors = tuple(describe_change(change) for change in changes)
    for index, left in enumerate(descriptors):
        for right in descriptors[index + 1 :]:
            if changes_conflict(left, right):
                raise ValueError(
                    f"conflicting changes: {left.kind} and {right.kind} "
                    f"on {left.target_id}"
                )
    ordering = {
        "Introduce": 0,
        "Rename": 1,
        "Move": 2,
        "Refine": 3,
        "Replace": 4,
        "Wrap": 5,
        "Redirect": 6,
        "Split": 7,
        "Merge": 8,
        "Remove": 9,
    }
    return tuple(
        sorted(
            descriptors,
            key=lambda item: (
                item.target_id,
                ordering.get(item.kind, 50),
                repr(item.payload),
            ),
        )
    )
