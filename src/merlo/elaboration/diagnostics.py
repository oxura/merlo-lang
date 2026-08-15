from __future__ import annotations


class SurfaceElaborationError(ValueError):
    pass


def edit_distance_one(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(
            first != second
            for first, second in zip(left, right, strict=True)
        ) == 1
    shorter, longer = (
        (left, right) if len(left) < len(right) else (right, left)
    )
    return any(
        longer[:index] + longer[index + 1:] == shorter
        for index in range(len(longer))
    )


__all__ = ["SurfaceElaborationError", "edit_distance_one"]
