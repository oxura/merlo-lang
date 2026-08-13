from __future__ import annotations

from merlo.core_semantics import CoreChange, apply_core_change
from merlo.frontend_semantics import check_frontend, compile_frontend


MODEL = """package shop.model
export UserId, User, Status, label
newtype UserId = Int
record User:
    id: UserId
    score: Int
    name: Text
    status: Status
enum Status:
    Active
    Disabled
fn label(status: Status) -> Text:
    match status:
        Active: "active"
        Disabled: "disabled"
"""

PAYMENTS = """package shop.payments
export Payments, Receipt
newtype Receipt = Int
capability Payments:
    charge(amount: Int) -> Receipt uses payments.charge
"""

CHECKOUT = """package shop.checkout
use shop.model::{User, UserId, Status, label}
use shop.payments::{Payments, Receipt}
export Order, total, checkout
record Order:
    id: UserId
    total: Int
    receipt: Receipt
fn total(user: User) -> Int:
    if user.status == Status.Active:
        user.score + 1
    else:
        0
task checkout(user: User, payments: cap Payments) -> Receipt:
    uses payments.charge
    let amount = total(user)
    payments.charge(amount)
"""


def _sources(checkout: str = CHECKOUT):
    return {
        "model.meldra": MODEL,
        "payments.meldra": PAYMENTS,
        "checkout.meldra": checkout,
    }


def _codes(source_map):
    return [item.code for item in check_frontend(source_map).diagnostics]


def test_closed_binder_type_checker_effects_and_lowering_form_one_exact_world():
    compilation = compile_frontend(_sources())

    assert compilation.hir.unknown_internal_reference_count == 0
    assert compilation.hir.exact_reference_count >= 20
    assert compilation.world.unknown_reference_count == 0
    assert compilation.world.exact_reference_count > 0
    assert compilation.core_program.to_dict()["schema_version"] == 1
    assert len(compilation.world.packages) == 1
    package = compilation.world.package("shop")
    assert package.interface_revision_id.startswith("iface_")
    assert package.implementation_revision_id.startswith("impl_")

    user = compilation.hir.symbol("shop.model.User")
    field = compilation.hir.symbol("shop.model.User$name")
    total = compilation.hir.symbol("shop.checkout.total")
    checkout = compilation.hir.symbol("shop.checkout.checkout")
    assert len({user.syntax_node_id, user.binding_id, user.symbol_id, user.revision_id}) == 4
    assert field.parent_symbol_id == user.symbol_id
    assert total.effects == ()
    assert checkout.effects == ("payments.charge",)
    assert checkout.capabilities == ("Payments",)
    assert all(item.status == "Exact" for item in compilation.hir.references)


def test_formatting_only_edit_preserves_semantic_ids_and_coreir_bytes():
    compact = {
        "main.meldra": "package p.main\nexport f\nfn f(value: Int) -> Int:\n  value + 1\n"
    }
    formatted = {
        "main.meldra": "package p.main\n\n# formatting only\nexport f\nfn f(value: Int) -> Int:\n    value + 1  # same semantics\n"
    }

    first = compile_frontend(compact)
    second = compile_frontend(formatted)

    assert first.csts[0].source_sha256 != second.csts[0].source_sha256
    assert first.hir.symbol("p.main.f").symbol_id == second.hir.symbol("p.main.f").symbol_id
    assert first.hir.symbol("p.main.f").revision_id == second.hir.symbol("p.main.f").revision_id
    assert first.core_program.to_json() == second.core_program.to_json()
    assert first.hir.package_revisions == second.hir.package_revisions


def test_external_semantic_edit_gets_new_symbol_but_changeir_preserves_provenance():
    first = compile_frontend(
        {"main.meldra": "package p.main\nexport f\nfn f(value: Int) -> Int:\n    value + 1\n"}
    )
    external = compile_frontend(
        {"main.meldra": "package p.main\nexport f\nfn f(value: Int) -> Int:\n    value + 2\n"}
    )
    original = first.hir.symbol("p.main.f")
    changed = external.hir.symbol("p.main.f")

    assert changed.symbol_id != original.symbol_id
    explicit = apply_core_change(
        first.world,
        CoreChange.change_implementation(
            original.symbol_id,
            {"kind": "explicit", "return": "value + 2"},
        ),
    )
    assert explicit.applied
    assert explicit.world.symbol(original.symbol_id).revision_id != original.revision_id


def test_private_body_edit_changes_only_implementation_revision():
    source = """package p.lib
export public
fn private(value: Int) -> Int:
    value + 1
fn public(value: Int) -> Int:
    private(value)
"""
    compilation = compile_frontend({"lib.meldra": source})
    package = compilation.world.package("p")
    private = compilation.hir.symbol("p.lib.private")

    changed = apply_core_change(
        compilation.world,
        CoreChange.change_implementation(
            private.symbol_id,
            {"kind": "binary", "operator": "+", "constant": 2},
        ),
    )
    changed_package = changed.world.package("p")

    assert changed.applied
    assert changed.affected_symbols == (private.symbol_id,)
    assert changed.interface_changed_packages == ()
    assert changed_package.interface_revision_id == package.interface_revision_id
    assert changed_package.implementation_revision_id != package.implementation_revision_id


def test_public_contract_change_invalidates_exact_consumers_and_interface():
    compilation = compile_frontend(_sources())
    total = compilation.hir.symbol("shop.checkout.total")
    old_package = compilation.world.package("shop")

    changed = apply_core_change(
        compilation.world,
        CoreChange.change_signature(
            total.symbol_id,
            {
                "args": [
                    {"name": "user", "type": "User"},
                    {"name": "scale", "type": "Int"},
                ],
                "returns": "Int",
            },
        ),
    )

    assert changed.applied
    assert total.symbol_id in changed.affected_symbols
    assert compilation.hir.symbol("shop.checkout.checkout").symbol_id in changed.affected_symbols
    assert changed.interface_changed_packages == (old_package.id,)
    assert changed.world.package("shop").interface_revision_id != old_package.interface_revision_id


def test_unknown_and_hidden_bindings_never_enter_successful_hir():
    unknown = "package p.main\nexport f\nfn f(value: Int) -> Int:\n    missing(value)\n"
    private_import = {
        "lib.meldra": "package p.lib\nfn hidden(value: Int) -> Int:\n    value\n",
        "main.meldra": "package p.main\nuse p.lib::{hidden}\nexport f\nfn f(value: Int) -> Int:\n    hidden(value)\n",
    }

    result = check_frontend({"main.meldra": unknown})
    assert result.compilation is None
    assert "UnknownBinding" in [item.code for item in result.diagnostics]
    assert "PrivateImport" in _codes(private_import)


def test_nominal_type_and_call_errors_are_typed_diagnostics():
    cases = {
        "UnknownType": "package p.main\nexport f\nfn f(value: Missing) -> Int:\n    1\n",
        "ArgumentTypeMismatch": "package p.main\nexport f\nfn id(value: Int) -> Int:\n    value\nfn f() -> Int:\n    id(\"wrong\")\n",
        "ArityMismatch": "package p.main\nexport f\nfn id(value: Int) -> Int:\n    value\nfn f() -> Int:\n    id()\n",
        "UnknownField": "package p.main\nexport User, f\nrecord User:\n    name: Text\nfn f(user: User) -> Text:\n    user.missing\n",
        "ConditionNotBool": "package p.main\nexport f\nfn f(value: Int) -> Int:\n    if value:\n        1\n    else:\n        2\n",
        "ReturnTypeMismatch": "package p.main\nexport f\nfn f() -> Int:\n    \"wrong\"\n",
    }

    for expected, source in cases.items():
        assert expected in _codes({"main.meldra": source})


def test_match_requires_known_unique_exhaustive_variants():
    missing = MODEL.replace('        Disabled: "disabled"\n', "")
    duplicate = MODEL.replace(
        '        Disabled: "disabled"\n',
        '        Active: "again"\n        Disabled: "disabled"\n',
    )
    unknown = MODEL.replace(
        '        Disabled: "disabled"\n',
        '        Missing: "missing"\n        Disabled: "disabled"\n',
    )

    assert "NonExhaustiveMatch" in _codes({"model.meldra": missing})
    assert "DuplicateMatchArm" in _codes({"model.meldra": duplicate})
    assert "UnknownVariant" in _codes({"model.meldra": unknown})


def test_effect_and_capability_failures_are_blocked_before_coreir():
    pure_effect = {
        "payments.meldra": PAYMENTS,
        "main.meldra": """package shop.main
use shop.payments::{Payments, Receipt}
export bad
fn bad(amount: Int, payments: cap Payments) -> Receipt:
    uses payments.charge
    payments.charge(amount)
""",
    }
    missing_capability = CHECKOUT.replace(
        "task checkout(user: User, payments: cap Payments) -> Receipt:",
        "task checkout(user: User) -> Receipt:",
    ).replace("payments.charge(amount)", "total(user)")
    undeclared_effect = CHECKOUT.replace("    uses payments.charge\n", "")

    assert "EffectInPureFunction" in _codes(pure_effect)
    assert "CapabilityEscalation" in _codes(_sources(missing_capability))
    assert "EffectNotDeclared" in _codes(_sources(undeclared_effect))


def test_cross_package_cycles_are_rejected():
    sources = {
        "a.meldra": """package alpha
module main
use beta.main::{b}
export a
fn a(value: Int) -> Int:
    b(value)
""",
        "b.meldra": """package beta
module main
use alpha.main::{a}
export b
fn b(value: Int) -> Int:
    a(value)
""",
    }

    assert "PackageCycle" in _codes(sources)
