from __future__ import annotations

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.type_parser import parse_type


class TypeConstraints:
    """Union-find type constraints for one surface elaboration."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.concrete: dict[str, str] = {}

    def variable(self, name: str) -> str:
        self.parent.setdefault(name, name)
        return name

    def find(self, term: str) -> str:
        self.parent.setdefault(term, term)
        while self.parent[term] != term:
            self.parent[term] = self.parent[self.parent[term]]
            term = self.parent[term]
        return term

    def typed(self, type_name: str) -> str:
        try:
            parsed = parse_type(type_name)
        except ValueError as error:
            raise SurfaceElaborationError(f"MalformedType: {type_name}") from error
        stack = [parsed]
        while stack:
            current = stack.pop()
            if current.name == "Any":
                raise SurfaceElaborationError("DynamicAnyForbidden")
            stack.extend(current.args)
        canonical = parsed.canonical
        if canonical.startswith("Map[") and canonical != "Map[Text,UInt64]":
            raise SurfaceElaborationError(f"UnsupportedMapType: {canonical}")
        term = self.variable(f"type:{canonical}")
        self.concrete[self.find(term)] = canonical
        return term

    def unify(self, left: str, right: str, *, context: str) -> str:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return left_root
        left_type = self.concrete.get(left_root)
        right_type = self.concrete.get(right_root)
        if left_type and right_type and left_type != right_type:
            raise SurfaceElaborationError(
                f"TypeConflict: {context}: {left_type} vs {right_type}"
            )
        self.parent[right_root] = left_root
        selected = left_type or right_type
        if selected:
            self.concrete[left_root] = selected
        self.concrete.pop(right_root, None)
        return left_root

    def resolve(self, term: str, *, name: str) -> str:
        value = self.concrete.get(self.find(term))
        if value is None:
            raise SurfaceElaborationError(f"AmbiguousType: {name}")
        return value


__all__ = ["TypeConstraints"]
