from __future__ import annotations

import ast
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer import scan_python
from .model import EditCapability
from .world import SoftwareWorld


@dataclass(frozen=True)
class EvolutionCase:
    name: str
    files: dict[str, str]
    operation: str
    target: str
    payload: str
    expected_edits: tuple[tuple[str, str, int], ...]
    expected_obligations: frozenset[str] = frozenset()
    argument_values: tuple[tuple[str, str], ...] = ()
    expected_ready: bool = True
    untouched: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class IdentityCase:
    name: str
    before: dict[str, str]
    after: dict[str, str]
    old_locator: str
    new_locator: str
    expected_status: str
    expected_link: bool


@dataclass(frozen=True)
class BenchmarkReport:
    evolution_cases: int
    identity_cases: int
    edit_precision: float
    edit_recall: float
    obligation_precision: float
    obligation_recall: float
    identity_precision: float
    identity_recall: float
    unintended_edit_count: int
    matched_edits: int
    predicted_edits: int
    expected_edits: int
    matched_obligations: int
    predicted_obligations: int
    expected_obligations: int
    correct_identity_links: int
    predicted_identity_links: int
    expected_identity_links: int
    false_safe_cases: int
    unsafe_cases: int
    transaction_safe_cases: int
    false_safe_rate: float
    transaction_safety: float
    case_results: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evolution_cases": self.evolution_cases,
            "identity_cases": self.identity_cases,
            "edit_precision": round(self.edit_precision, 6),
            "edit_recall": round(self.edit_recall, 6),
            "obligation_precision": round(self.obligation_precision, 6),
            "obligation_recall": round(self.obligation_recall, 6),
            "identity_precision": round(self.identity_precision, 6),
            "identity_recall": round(self.identity_recall, 6),
            "unintended_edit_count": self.unintended_edit_count,
            "false_safe_rate": round(self.false_safe_rate, 6),
            "transaction_safety": round(self.transaction_safety, 6),
            "counts": {
                "matched_edits": self.matched_edits,
                "predicted_edits": self.predicted_edits,
                "expected_edits": self.expected_edits,
                "matched_obligations": self.matched_obligations,
                "predicted_obligations": self.predicted_obligations,
                "expected_obligations": self.expected_obligations,
                "correct_identity_links": self.correct_identity_links,
                "predicted_identity_links": self.predicted_identity_links,
                "expected_identity_links": self.expected_identity_links,
                "false_safe_cases": self.false_safe_cases,
                "unsafe_cases": self.unsafe_cases,
                "transaction_safe_cases": self.transaction_safe_cases,
            },
            "case_results": list(self.case_results),
        }


def run_stage02_bench() -> BenchmarkReport:
    edit_tp = edit_predicted = edit_expected = 0
    obligation_tp = obligation_predicted = obligation_expected = 0
    unintended = false_safe = unsafe_cases = transaction_ok = 0
    case_results: list[dict[str, Any]] = []
    evolution_cases = _evolution_cases()
    for case in evolution_cases:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_files(root, case.files)
            before = {
                relative: (root / relative).read_bytes() for relative in case.files
            }
            world = SoftwareWorld.scan(root)
            target = world.program.entity(case.target)
            plan, capability = _plan_case(world, target.id, case)
            actual_edits = Counter((item.file, item.reason) for item in plan.edits)
            expected_edits = Counter(
                {
                    (file, reason): count
                    for file, reason, count in case.expected_edits
                }
            )
            matched_edits = sum((actual_edits & expected_edits).values())
            edit_tp += matched_edits
            edit_predicted += sum(actual_edits.values())
            edit_expected += sum(expected_edits.values())
            actual_obligations = {
                item.kind for item in plan.obligations if item.blocking
            }
            matched_obligations = actual_obligations & case.expected_obligations
            obligation_tp += len(matched_obligations)
            obligation_predicted += len(actual_obligations)
            obligation_expected += len(case.expected_obligations)
            unintended += sum((actual_edits - expected_edits).values())
            if not case.expected_ready:
                unsafe_cases += 1
                if plan.ready:
                    false_safe += 1
            safe_transaction = False
            if case.expected_ready and plan.ready:
                world.apply(plan, capability)
                _parse_workspace(root)
                safe_transaction = all(
                    text in (root / relative).read_text(encoding="utf-8")
                    for relative, text in case.untouched
                )
            elif not case.expected_ready:
                safe_transaction = all(
                    (root / relative).read_bytes() == content
                    for relative, content in before.items()
                )
            transaction_ok += int(safe_transaction)
            case_results.append(
                {
                    "name": case.name,
                    "ready": plan.ready,
                    "expected_ready": case.expected_ready,
                    "actual_edits": dict(
                        sorted(
                            (f"{file}:{reason}", count)
                            for (file, reason), count in actual_edits.items()
                        )
                    ),
                    "actual_obligations": sorted(actual_obligations),
                    "edit_match": matched_edits == sum(expected_edits.values()),
                    "obligation_match": actual_obligations == case.expected_obligations,
                    "transaction_safe": safe_transaction,
                }
            )

    identity_tp = identity_predicted = identity_expected = 0
    identity_cases = _identity_cases()
    for case in identity_cases:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_files(root, case.before)
            first = SoftwareWorld.scan(root).program
            old = first.entity(case.old_locator)
            _replace_files(root, case.after)
            second = scan_python(root, previous=first)
            new = second.entity(case.new_locator)
            relation = next(
                (
                    item
                    for item in second.identity_relations
                    if item.old_id == old.id
                    and item.new_id == new.id
                    and item.new_locator == case.new_locator
                    and item.status in {"Exact", "Probable"}
                ),
                None,
            )
            predicted_link = relation is not None
            correct = (
                predicted_link == case.expected_link
                and new.identity_status == case.expected_status
            )
            if predicted_link:
                identity_predicted += 1
                identity_tp += int(correct)
            if case.expected_link:
                identity_expected += 1
            case_results.append(
                {
                    "name": case.name,
                    "identity_status": new.identity_status,
                    "expected_status": case.expected_status,
                    "candidate_link": predicted_link,
                    "expected_link": case.expected_link,
                    "identity_inherited": new.id == old.id,
                    "identity_match": correct,
                }
            )

    return BenchmarkReport(
        evolution_cases=len(evolution_cases),
        identity_cases=len(identity_cases),
        edit_precision=_ratio(edit_tp, edit_predicted),
        edit_recall=_ratio(edit_tp, edit_expected),
        obligation_precision=_ratio(obligation_tp, obligation_predicted),
        obligation_recall=_ratio(obligation_tp, obligation_expected),
        identity_precision=_ratio(identity_tp, identity_predicted),
        identity_recall=_ratio(identity_tp, identity_expected),
        unintended_edit_count=unintended,
        matched_edits=edit_tp,
        predicted_edits=edit_predicted,
        expected_edits=edit_expected,
        matched_obligations=obligation_tp,
        predicted_obligations=obligation_predicted,
        expected_obligations=obligation_expected,
        correct_identity_links=identity_tp,
        predicted_identity_links=identity_predicted,
        expected_identity_links=identity_expected,
        false_safe_cases=false_safe,
        unsafe_cases=unsafe_cases,
        transaction_safe_cases=transaction_ok,
        false_safe_rate=_ratio(false_safe, unsafe_cases),
        transaction_safety=_ratio(transaction_ok, len(evolution_cases)),
        case_results=tuple(case_results),
    )


def _plan_case(world: SoftwareWorld, target_id: str, case: EvolutionCase):
    common = {
        "allow_public_api_break": True,
        "allow_new_dependencies": True,
    }
    if case.operation == "rename":
        capability = EditCapability.rename(target_id, **common)
        return world.plan_rename(target_id, case.payload, capability), capability
    if case.operation == "move":
        capability = EditCapability.move(target_id, **common)
        return world.plan_move(target_id, case.payload, capability), capability
    capability = EditCapability.change_signature(target_id, **common)
    return (
        world.plan_change_signature(
            target_id,
            case.payload,
            capability,
            argument_values=dict(case.argument_values),
        ),
        capability,
    )


def stage02_evolution_cases() -> tuple[EvolutionCase, ...]:
    return _evolution_cases()


def _evolution_cases() -> tuple[EvolutionCase, ...]:
    return (
        EvolutionCase(
            "rename/local-call",
            {"m.py": "def target():\n    return 1\n\ndef call():\n    return target()\n"},
            "rename", "m.target", "renamed",
            (("m.py", "definition", 1), ("m.py", "name", 1)),
        ),
        EvolutionCase(
            "rename/aliased-import",
            {
                "api.py": "def target():\n    return 1\n",
                "use.py": "from api import target as stable\nvalue = stable()\n",
            },
            "rename", "api.target", "renamed",
            (("api.py", "definition", 1), ("use.py", "import", 1)),
            untouched=(("use.py", "stable()"),),
        ),
        EvolutionCase(
            "rename/local-import",
            {
                "api.py": "def target():\n    return 1\n",
                "use.py": "def run():\n    from api import target\n    return target()\n",
            },
            "rename", "api.target", "renamed",
            (("api.py", "definition", 1), ("use.py", "import", 1), ("use.py", "name", 1)),
        ),
        EvolutionCase(
            "rename/reexport",
            {
                "pkg/__init__.py": "from .api import target\n",
                "pkg/api.py": "def target():\n    return 1\n",
                "use.py": "from pkg import target\nvalue = target()\n",
            },
            "rename", "pkg.api.target", "renamed",
            (("pkg/api.py", "definition", 1), ("pkg/__init__.py", "import", 1), ("use.py", "import", 1), ("use.py", "name", 1)),
        ),
        EvolutionCase(
            "rename/string-export",
            {"m.py": "__all__ = ['target']\ndef target():\n    return 1\n"},
            "rename", "m.target", "renamed",
            (("m.py", "definition", 1),),
            frozenset({"StringReference"}), expected_ready=False,
        ),
        EvolutionCase(
            "rename/getattr",
            {
                "api.py": "def target():\n    return 1\n",
                "use.py": "import api\nfn = getattr(api, 'target')\n",
            },
            "rename", "api.target", "renamed",
            (("api.py", "definition", 1),),
            frozenset({"DynamicReference"}), expected_ready=False,
        ),
        EvolutionCase(
            "rename/wildcard",
            {
                "api.py": "def target():\n    return 1\n",
                "use.py": "from api import *\nvalue = target()\n",
            },
            "rename", "api.target", "renamed",
            (("api.py", "definition", 1),),
            frozenset({"WildcardReference", "UnknownReference"}), expected_ready=False,
        ),
        EvolutionCase(
            "rename/shadowing",
            {"m.py": "def target():\n    return 1\n\ndef local(target):\n    return target()\n"},
            "rename", "m.target", "renamed",
            (("m.py", "definition", 1),),
            untouched=(("m.py", "def local(target)"),),
        ),
        EvolutionCase(
            "rename/collision",
            {"m.py": "def target():\n    return 1\n\ndef renamed():\n    return 2\n"},
            "rename", "m.target", "renamed",
            (("m.py", "definition", 1),),
            frozenset({"NameCollision"}), expected_ready=False,
        ),
        EvolutionCase(
            "move/simple-alias",
            {
                "pkg/__init__.py": "",
                "pkg/source.py": "def target():\n    return 1\n",
                "pkg/dest.py": "",
                "use.py": "from pkg.source import target as stable\nvalue = stable()\n",
            },
            "move", "pkg.source.target", "pkg.dest",
            (("pkg/source.py", "move_source", 1), ("pkg/dest.py", "move_target", 1), ("use.py", "move_import", 1)),
            untouched=(("use.py", "stable()"),),
        ),
        EvolutionCase(
            "move/collision",
            {
                "pkg/__init__.py": "",
                "pkg/source.py": "def target():\n    return 1\n",
                "pkg/dest.py": "def target():\n    return 2\n",
            },
            "move", "pkg.source.target", "pkg.dest",
            (("pkg/source.py", "move_source", 1), ("pkg/dest.py", "move_target", 1)),
            frozenset({"TargetCollision"}), expected_ready=False,
        ),
        EvolutionCase(
            "signature/optional",
            {"api.py": "def target(a):\n    return a\n", "use.py": "from api import target\nvalue = target(1)\n"},
            "signature", "api.target", "(a, *, model=None)",
            (("api.py", "change_signature", 1),),
        ),
        EvolutionCase(
            "signature/positional",
            {"api.py": "def target(a):\n    return a\n", "use.py": "from api import target\nvalue = target(1)\n"},
            "signature", "api.target", "(a, *, model)",
            (("api.py", "change_signature", 1), ("use.py", "migrate_direct_call", 1)),
            argument_values=(("model", "'fast'"),),
        ),
        EvolutionCase(
            "signature/variadic",
            {"api.py": "def target(a):\n    return a\n", "use.py": "from api import target\ndef run(*args):\n    return target(*args)\n"},
            "signature", "api.target", "(a, *, model)",
            (("api.py", "change_signature", 1),),
            frozenset({"VariadicCallCompatibility"}), (("model", "'fast'"),), False,
        ),
        EvolutionCase(
            "signature/partial",
            {"api.py": "def target(a):\n    return a\n", "use.py": "from functools import partial\nfrom api import target\nfn = partial(target, 1)\n"},
            "signature", "api.target", "(a, *, model)",
            (("api.py", "change_signature", 1),),
            frozenset({"PartialCompatibility"}), (("model", "'fast'"),), False,
        ),
        EvolutionCase(
            "signature/getattr",
            {"api.py": "def target(a):\n    return a\n", "use.py": "import api\nfn = getattr(api, 'target')\n"},
            "signature", "api.target", "(a, *, model)",
            (("api.py", "change_signature", 1),),
            frozenset({"DynamicCallCompatibility"}), (("model", "'fast'"),), False,
        ),
    )


def _identity_cases() -> tuple[IdentityCase, ...]:
    before = {"a.py": "def alpha(x):\n    return x + 1\n"}
    return (
        IdentityCase("identity/body", before, {"a.py": "def alpha(x):\n    return x + 2\n"}, "a.alpha", "a.alpha", "Probable", True),
        IdentityCase("identity/rename", before, {"a.py": "def beta(x):\n    return x + 1\n"}, "a.alpha", "a.beta", "Probable", True),
        IdentityCase("identity/move-body", before, {"b.py": "def alpha(x):\n    return x + 2\n"}, "a.alpha", "b.alpha", "Probable", True),
        IdentityCase("identity/delete-create", before, {"a.py": "def alpha(x):\n    return 'other'\n"}, "a.alpha", "a.alpha", "Ambiguous", False),
        IdentityCase("identity/rename-move", before, {"b.py": "def beta(x):\n    return x + 1\n"}, "a.alpha", "b.beta", "Probable", True),
    )


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _replace_files(root: Path, files: dict[str, str]) -> None:
    for path in root.rglob("*.py"):
        path.unlink()
    _write_files(root, files)


def _parse_workspace(root: Path) -> None:
    for path in root.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
