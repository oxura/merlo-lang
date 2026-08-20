from __future__ import annotations

from pathlib import Path
import re
import subprocess

TARGET = Path("src/merlo/structured_hir_v2.py")
GOOD_BASE = "1adf3460668b55074c0d208e6941593ae1cb25f4"

text = TARGET.read_text(encoding="utf-8")
if text.strip() == "PLACEHOLDER":
    text = subprocess.check_output(
        ["git", "show", f"{GOOD_BASE}:{TARGET.as_posix()}"],
        text=True,
    )

visible_pattern = re.compile(
    r"    @staticmethod\n"
    r"    def _loop_visible_state\(\n"
    r"        before: _OwnershipState,\n"
    r"        candidate: _OwnershipState,\n"
    r"    \) -> _OwnershipState:\n"
    r"        return _OwnershipState\(\n"
    r".*?"
    r"        \)\n"
    r"    @staticmethod\n"
    r"    def _loop_assignment_names",
    re.DOTALL,
)
visible_replacement = '''    @staticmethod
    def _loop_storage_roots(state: _OwnershipState) -> frozenset[PlaceRoot]:
        """Roots whose storage existed at the loop boundary."""
        return frozenset(place.root for place in state.places.values())

    @classmethod
    def _loop_visible_state(
        cls,
        before: _OwnershipState,
        candidate: _OwnershipState,
    ) -> _OwnershipState:
        """Project all state attached to storage that existed before the loop.

        A pre-existing container may acquire its first contained borrow inside
        the loop.  Such a borrow has no key in ``before.borrows`` yet, so the
        storage root, rather than the old dictionary keys, defines visibility.
        """
        tracked_roots = cls._loop_storage_roots(before)
        visible_borrow_names: set[str] = set()
        for name in sorted(set(candidate.borrows) | set(candidate.borrow_places)):
            storage = candidate.borrow_places.get(name) or candidate.places.get(name)
            if (
                name in before.borrows
                or name in before.borrow_places
                or (storage is not None and storage.root in tracked_roots)
            ):
                visible_borrow_names.add(name)
        return _OwnershipState(
            {
                name: candidate.statuses.get(name, "absent")
                for name in before.statuses
            },
            {
                name: candidate.borrows[name]
                for name in sorted(visible_borrow_names)
                if name in candidate.borrows
            },
            {
                name: candidate.places[name]
                for name in before.places
                if name in candidate.places
            },
            False,
            {
                name: candidate.borrow_places[name]
                for name in sorted(visible_borrow_names)
                if name in candidate.borrow_places
            },
        )
    @staticmethod
    def _loop_assignment_names'''
text, count = visible_pattern.subn(visible_replacement, text, count=1)
if count != 1:
    raise SystemExit(f"failed to replace _loop_visible_state: {count}")

backedge_pattern = re.compile(
    r"    def _require_loop_backedge_stable\(\n"
    r"        self,\n"
    r"        before: _OwnershipState,\n"
    r"        candidate: _OwnershipState,\n"
    r"        \*,\n"
    r"        assignment_names: set\[str\] \| frozenset\[str\] = frozenset\(\),\n"
    r"    \) -> None:\n"
    r".*?"
    r"    def _loop_exit_state\(",
    re.DOTALL,
)
backedge_replacement = '''    def _require_loop_backedge_stable(
        self,
        before: _OwnershipState,
        candidate: _OwnershipState,
        *,
        assignment_names: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        """Require every ownership-visible backedge to equal loop entry."""
        before_visible = self._loop_visible_state(before, before)
        candidate_visible = self._loop_visible_state(before, candidate)
        for name in sorted(set(candidate.statuses) - set(before.statuses)):
            if (
                candidate.statuses[name] not in {"dropped", "moved"}
                and name not in assignment_names
            ):
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.statuses):
            if candidate.statuses.get(name, "absent") != before.statuses[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.places):
            if candidate.places.get(name) != before.places[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        if candidate_visible.borrows != before_visible.borrows:
            changed = sorted(
                set(candidate_visible.borrows) ^ set(before_visible.borrows)
                or {
                    name
                    for name in set(candidate_visible.borrows) & set(before_visible.borrows)
                    if candidate_visible.borrows[name] != before_visible.borrows[name]
                }
            )
            self._error(
                "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                changed[0] if changed else None,
            )
        if candidate_visible.borrow_places != before_visible.borrow_places:
            changed = sorted(
                set(candidate_visible.borrow_places) ^ set(before_visible.borrow_places)
                or {
                    name
                    for name in set(candidate_visible.borrow_places)
                    & set(before_visible.borrow_places)
                    if candidate_visible.borrow_places[name]
                    != before_visible.borrow_places[name]
                }
            )
            self._error(
                "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                changed[0] if changed else None,
            )
    def _loop_exit_state('''
text, count = backedge_pattern.subn(backedge_replacement, text, count=1)
if count != 1:
    raise SystemExit(f"failed to replace _require_loop_backedge_stable: {count}")

TARGET.write_text(text, encoding="utf-8")
