"""Deterministic CoreIR binding and change experiments for the Meldra Core Lab.

This module deliberately operates on structured data.  It is not a parser, a
runtime, or a promise about a future Meldra language.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping


CORE_SCHEMA_VERSION = 1
CORE_SCHEMA_ID = "https://meldra.dev/schema/core-ir-v1.json"
CORE_SCHEMA_SHA256 = "5bf9700ce20c3f6d906e3be2982412cec00a7dc21f692055489cc87787040ac4"
_DECLARATION_KINDS = frozenset(
    {"value", "function", "task", "interface", "capability"}
)


class CoreError(ValueError):
    """The CoreIR is structurally or semantically invalid."""


class CoreBindingError(CoreError):
    """A CoreIR name cannot be bound exactly and unambiguously."""


class CapabilityViolation(CoreError):
    """A change attempts to use a capability which was not materialized."""

    def __init__(self, symbol_id: str, capabilities: Iterable[str]) -> None:
        self.symbol_id = symbol_id
        self.capabilities = tuple(sorted(set(capabilities)))
        joined = ", ".join(self.capabilities)
        super().__init__(f"capability escalation for {symbol_id}: {joined}")


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError) as exc:
        raise CoreError(f"CoreIR must contain JSON values: {exc}") from exc


def _copy_json(value: Any) -> Any:
    return json.loads(_canonical(value))


def _digest(prefix: str, value: Any) -> str:
    data = _canonical(value).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(data).hexdigest()}"


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoreError(f"{label} must be a non-empty string")
    return value


def _texts(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise CoreError(f"{label} must be a list")
    result = tuple(_required_text(item, label) for item in value)
    if len(set(result)) != len(result):
        raise CoreError(f"duplicate entry in {label}")
    return result


def _change_texts(value: Iterable[str] | str, label: str) -> tuple[str, ...]:
    values: Iterable[str] = (value,) if isinstance(value, str) else value
    try:
        normalized = {_required_text(item, label) for item in values}
    except TypeError as exc:
        raise CoreError(f"{label} must be iterable") from exc
    return tuple(sorted(normalized))


def _field(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


@dataclass(frozen=True)
class CoreProgram:
    """A canonical, immutable JSON CoreIR document."""

    _json: str = field(repr=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CoreProgram":
        if not isinstance(value, Mapping):
            raise CoreError("CoreProgram must be an object")
        schema_version = value.get("schema_version", CORE_SCHEMA_VERSION)
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != CORE_SCHEMA_VERSION
        ):
            raise CoreError(f"unsupported CoreIR schema version: {schema_version!r}")
        packages = value.get("packages")
        if not isinstance(packages, list):
            raise CoreError("CoreProgram.packages must be a list")
        document = dict(value)
        document["schema_version"] = CORE_SCHEMA_VERSION
        return cls(_canonical(document))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    def to_json(self) -> str:
        return self._json


@dataclass(frozen=True)
class CoreSymbol:
    id: str
    revision_id: str
    package_id: str
    package_name: str
    module: str
    name: str
    kind: str
    exported: bool
    contract_json: str = field(repr=False)
    implementation_json: str = field(repr=False)
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    @property
    def symbol_id(self) -> str:
        return self.id

    @property
    def signature(self) -> Any:
        return json.loads(self.contract_json)

    @property
    def typed_contract(self) -> Any:
        return self.signature

    @property
    def implementation(self) -> Any:
        return json.loads(self.implementation_json)

    @property
    def locator(self) -> str:
        return f"{self.package_name}.{self.module}.{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision_id": self.revision_id,
            "package_id": self.package_id,
            "package_name": self.package_name,
            "module": self.module,
            "name": self.name,
            "kind": self.kind,
            "exported": self.exported,
            "typed_contract": self.typed_contract,
            "implementation": self.implementation,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class CoreReference:
    id: str
    owner_id: str
    spelling: str
    usage: str
    status: str
    target_id: str | None = None
    foreign_target: str | None = None
    ordinal: int = 0

    @property
    def resolution(self) -> str:
        return self.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "spelling": self.spelling,
            "usage": self.usage,
            "status": self.status,
            "target_id": self.target_id,
            "foreign_target": self.foreign_target,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class CorePackage:
    id: str
    name: str
    interface_revision: str
    implementation_revision: str
    modules: tuple[str, ...]
    symbol_ids: tuple[str, ...]
    exported_symbol_ids: tuple[str, ...]

    @property
    def interface_revision_id(self) -> str:
        return self.interface_revision

    @property
    def implementation_revision_id(self) -> str:
        return self.implementation_revision

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "interface_revision": self.interface_revision,
            "implementation_revision": self.implementation_revision,
            "modules": list(self.modules),
            "symbol_ids": list(self.symbol_ids),
            "exported_symbol_ids": list(self.exported_symbol_ids),
        }


@dataclass(frozen=True)
class CoreWorld:
    symbols: tuple[CoreSymbol, ...]
    references: tuple[CoreReference, ...]
    packages: tuple[CorePackage, ...]
    _program: CoreProgram = field(repr=False, compare=False)

    def symbol(self, symbol_id: str) -> CoreSymbol:
        matches = [item for item in self.symbols if item.id == symbol_id]
        if not matches:
            raise KeyError(symbol_id)
        return matches[0]

    def package(self, id_or_name: str) -> CorePackage:
        matches = [
            item
            for item in self.packages
            if item.id == id_or_name or item.name == id_or_name
        ]
        if not matches:
            raise KeyError(id_or_name)
        if len(matches) != 1:
            raise CoreError(f"ambiguous package lookup: {id_or_name}")
        return matches[0]

    def context_for(self, symbol_id: str) -> dict[str, Any]:
        target = self.symbol(symbol_id)
        inbound = tuple(
            item for item in self.references if item.target_id == symbol_id
        )
        outbound = tuple(
            item for item in self.references if item.owner_id == symbol_id
        )
        related_ids = {item.owner_id for item in inbound}
        related_ids.update(
            item.target_id for item in outbound if item.target_id is not None
        )
        return {
            "symbol": target.to_dict(),
            "package": self.package(target.package_id).to_dict(),
            "inbound_references": [item.to_dict() for item in inbound],
            "outbound_references": [item.to_dict() for item in outbound],
            "related_symbols": [
                self.symbol(item).to_dict() for item in sorted(related_ids)
            ],
        }

    @property
    def exact_reference_count(self) -> int:
        return sum(item.status == "Exact" for item in self.references)

    @property
    def foreign_reference_count(self) -> int:
        return sum(item.status == "Foreign" for item in self.references)

    @property
    def unknown_reference_count(self) -> int:
        return sum(item.status == "Unknown" for item in self.references)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CORE_SCHEMA_VERSION,
            "symbols": [item.to_dict() for item in self.symbols],
            "references": [item.to_dict() for item in self.references],
            "packages": [item.to_dict() for item in self.packages],
            "binding_counts": {
                "exact": self.exact_reference_count,
                "foreign": self.foreign_reference_count,
                "unknown": self.unknown_reference_count,
            },
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True)
class CoreChange:
    kind: str
    symbol_id: str
    new_name: str | None = None
    target_module: str | None = None
    target_package: str | None = None
    value_json: str | None = field(default=None, repr=False)
    effects: tuple[str, ...] | None = None
    capabilities: tuple[str, ...] | None = None
    materialized_capabilities: tuple[str, ...] = ()

    @property
    def value(self) -> Any:
        return json.loads(self.value_json) if self.value_json is not None else None

    @classmethod
    def rename(cls, symbol_id: str, new_name: str) -> "CoreChange":
        return cls("Rename", symbol_id, new_name=_required_text(new_name, "new_name"))

    @classmethod
    def move(
        cls,
        symbol_id: str,
        target_module: str,
        target_package: str | None = None,
    ) -> "CoreChange":
        return cls(
            "Move",
            symbol_id,
            target_module=_required_text(target_module, "target_module"),
            target_package=target_package,
        )

    @classmethod
    def change_signature(
        cls,
        symbol_id: str,
        signature: Any,
        *,
        effects: Iterable[str] | None = None,
        capabilities: Iterable[str] | None = None,
        materialized_capabilities: Iterable[str] = (),
    ) -> "CoreChange":
        return cls(
            "ChangeSignature",
            symbol_id,
            value_json=_canonical(signature),
            effects=(
                _change_texts(effects, "effects") if effects is not None else None
            ),
            capabilities=(
                _change_texts(capabilities, "capabilities")
                if capabilities is not None
                else None
            ),
            materialized_capabilities=_change_texts(
                materialized_capabilities, "materialized_capabilities"
            ),
        )

    @classmethod
    def change_implementation(
        cls,
        symbol_id: str,
        implementation: Any,
        *,
        effects: Iterable[str] | None = None,
        capabilities: Iterable[str] | None = None,
        materialized_capabilities: Iterable[str] = (),
    ) -> "CoreChange":
        return cls(
            "ChangeImplementation",
            symbol_id,
            value_json=_canonical(implementation),
            effects=(
                _change_texts(effects, "effects") if effects is not None else None
            ),
            capabilities=(
                _change_texts(capabilities, "capabilities")
                if capabilities is not None
                else None
            ),
            materialized_capabilities=_change_texts(
                materialized_capabilities, "materialized_capabilities"
            ),
        )

    @classmethod
    def restrict_effect(
        cls, symbol_id: str, effects: Iterable[str] | str
    ) -> "CoreChange":
        return cls(
            "RestrictEffect",
            symbol_id,
            effects=_change_texts(effects, "effects"),
        )


@dataclass(frozen=True)
class CoreChangeResult:
    world: CoreWorld
    affected_symbols: tuple[str, ...] = ()
    affected_packages: tuple[str, ...] = ()
    interface_changed_packages: tuple[str, ...] = ()
    blocked: bool = False
    capability_violation: CapabilityViolation | None = None

    @property
    def applied(self) -> bool:
        return not self.blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "world": self.world.to_dict(),
            "affected_symbols": list(self.affected_symbols),
            "affected_packages": list(self.affected_packages),
            "interface_changed_packages": list(self.interface_changed_packages),
            "blocked": self.blocked,
            "capability_violation": (
                {
                    "symbol_id": self.capability_violation.symbol_id,
                    "capabilities": list(self.capability_violation.capabilities),
                    "message": str(self.capability_violation),
                }
                if self.capability_violation
                else None
            ),
        }


@dataclass(frozen=True)
class _RawDeclaration:
    symbol: CoreSymbol
    package_index: int
    module_index: int
    declaration_index: int
    declaration: dict[str, Any]
    reference_values: tuple[Any, ...]


def _contract_for(declaration: Mapping[str, Any], kind: str) -> Any:
    if "typed_contract" in declaration:
        contract = declaration["typed_contract"]
    elif "signature" in declaration:
        contract = declaration["signature"]
    elif "type" in declaration:
        contract = declaration["type"]
    elif kind == "interface" and "members" in declaration:
        contract = {"members": declaration["members"]}
    elif kind == "capability":
        contract = {"effects": sorted(_texts(declaration.get("effects", []), "effects"))}
    else:
        contract = None
    if contract is None:
        raise CoreError(
            f"declaration {declaration.get('name', '<unnamed>')} has no typed contract"
        )
    return _copy_json(contract)


def _implementation_for(declaration: Mapping[str, Any]) -> Any:
    return _copy_json(
        _field(declaration, "implementation", "body", "value", default=None)
    )


def _contract_type_names(contract: Any) -> tuple[str, ...]:
    builtins = {
        "Any",
        "Bool",
        "Bytes",
        "Float",
        "Int",
        "List",
        "Map",
        "Never",
        "Option",
        "Result",
        "String",
        "Unit",
    }
    names: set[str] = set()
    type_keys = {
        "args",
        "extends",
        "implements",
        "input",
        "items",
        "key",
        "output",
        "returns",
        "type",
        "value_type",
    }

    def visit(value: Any, type_position: bool = False) -> None:
        if isinstance(value, str):
            if type_position and value not in builtins:
                names.add(value)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, type_position)
            return
        if not isinstance(value, Mapping):
            return
        explicit_ref = value.get("ref")
        if isinstance(explicit_ref, str) and explicit_ref not in builtins:
            names.add(explicit_ref)
        if type_position and "args" in value and isinstance(value.get("name"), str):
            constructor = value["name"]
            if constructor not in builtins:
                names.add(constructor)
        for key, item in value.items():
            if key == "members" and isinstance(item, Mapping):
                for member_type in item.values():
                    visit(member_type, True)
            elif key in type_keys:
                visit(item, True)
            elif key != "ref":
                visit(item, False)

    visit(contract)
    return tuple(sorted(names))


def _references_for(declaration: Mapping[str, Any]) -> tuple[Any, ...]:
    refs = _field(declaration, "references", "refs", default=[])
    if not isinstance(refs, list):
        raise CoreError("declaration references must be a list")
    type_refs = declaration.get("type_refs", [])
    if not isinstance(type_refs, list):
        raise CoreError("declaration type_refs must be a list")
    normalized: list[Any] = list(refs)
    explicit_type_names: set[str] = {
        name
        for item in refs
        if isinstance(item, Mapping) and item.get("usage") == "Type"
        for name in [_field(item, "name", "ref")]
        if isinstance(name, str)
    }
    for item in type_refs:
        if isinstance(item, str):
            explicit_type_names.add(item)
            normalized.append({"name": item, "usage": "Type"})
        elif isinstance(item, Mapping):
            copied = dict(item)
            copied.setdefault("usage", "Type")
            name = _field(copied, "name", "ref")
            if isinstance(name, str):
                explicit_type_names.add(name)
            normalized.append(copied)
        else:
            raise CoreError("type_refs entries must be strings or objects")
    kind = _required_text(declaration.get("kind"), "declaration kind")
    if kind != "capability":
        contract = _contract_for(declaration, kind)
        for name in _contract_type_names(contract):
            if name not in explicit_type_names:
                normalized.append({"name": name, "usage": "Type"})
    return tuple(normalized)


def _normalize_import(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CoreError("imports must contain objects")
    item = dict(value)
    foreign = item.get("foreign")
    source_name = _field(item, "name", "symbol", "export")
    if foreign is not None:
        foreign_name = _required_text(foreign, "foreign")
        if source_name is None:
            source_name = foreign_name.rsplit(".", 1)[-1]
        alias = _field(item, "as", "alias", default=source_name)
        return {
            "foreign": foreign_name,
            "name": _required_text(source_name, "import name"),
            "alias": _required_text(alias, "import alias"),
        }
    package = _field(item, "package", "from")
    module = item.get("module")
    if package is None and isinstance(module, str) and "." in module:
        package, module = module.split(".", 1)
    return {
        "package": _required_text(package, "import package"),
        "module": _required_text(module, "import module"),
        "name": _required_text(source_name, "import name"),
        "alias": _required_text(
            _field(item, "as", "alias", default=source_name), "import alias"
        ),
    }


def compile_core(program: CoreProgram | Mapping[str, Any]) -> CoreWorld:
    """Bind a structured CoreIR program or fail before producing a world."""

    if not isinstance(program, CoreProgram):
        program = CoreProgram.from_dict(program)
    document = program.to_dict()
    package_values = document["packages"]
    package_names: set[str] = set()
    package_ids: set[str] = set()
    module_keys: set[tuple[str, str]] = set()
    symbol_ids: set[str] = set()
    raw: list[_RawDeclaration] = []
    module_data: dict[tuple[str, str], dict[str, Any]] = {}
    module_symbols: dict[tuple[str, str], dict[str, CoreSymbol]] = {}
    exported: dict[tuple[str, str, str], CoreSymbol] = {}

    for package_index, package_value in enumerate(package_values):
        if not isinstance(package_value, Mapping):
            raise CoreError("packages must contain objects")
        package_name = _required_text(package_value.get("name"), "package name")
        package_id = _required_text(
            package_value.get("id", _digest("pkg", package_name)), "package id"
        )
        if package_name in package_names:
            raise CoreError(f"duplicate package name: {package_name}")
        if package_id in package_ids:
            raise CoreError(f"duplicate package id: {package_id}")
        package_names.add(package_name)
        package_ids.add(package_id)
        modules = package_value.get("modules")
        if not isinstance(modules, list):
            raise CoreError(f"package {package_name} modules must be a list")
        for module_index, module_value in enumerate(modules):
            if not isinstance(module_value, Mapping):
                raise CoreError("modules must contain objects")
            module_name = _required_text(module_value.get("name"), "module name")
            module_key = (package_id, module_name)
            if module_key in module_keys:
                raise CoreError(f"duplicate module: {package_name}.{module_name}")
            module_keys.add(module_key)
            imports = module_value.get("imports", [])
            if not isinstance(imports, list):
                raise CoreError("module imports must be a list")
            normalized_imports = [_normalize_import(item) for item in imports]
            aliases = [item["alias"] for item in normalized_imports]
            if len(set(aliases)) != len(aliases):
                raise CoreBindingError(
                    f"ambiguous import aliases in {package_name}.{module_name}"
                )
            exports_value = module_value.get("exports", [])
            if not isinstance(exports_value, list):
                raise CoreError("module exports must be a list")
            export_names: list[str] = []
            for item in exports_value:
                if isinstance(item, str):
                    export_names.append(_required_text(item, "export"))
                elif isinstance(item, Mapping):
                    export_names.append(
                        _required_text(_field(item, "name", "symbol"), "export")
                    )
                else:
                    raise CoreError("exports must contain strings or objects")
            if len(set(export_names)) != len(export_names):
                raise CoreError(f"duplicate export in {package_name}.{module_name}")
            declarations = _field(module_value, "declarations", "symbols")
            if not isinstance(declarations, list):
                raise CoreError("module declarations must be a list")
            names: set[str] = set()
            by_name: dict[str, CoreSymbol] = {}
            pending: list[tuple[dict[str, Any], CoreSymbol, tuple[Any, ...], int]] = []
            for declaration_index, declaration_value in enumerate(declarations):
                if not isinstance(declaration_value, Mapping):
                    raise CoreError("declarations must contain objects")
                declaration = dict(declaration_value)
                name = _required_text(declaration.get("name"), "declaration name")
                kind = _required_text(declaration.get("kind"), "declaration kind")
                if kind not in _DECLARATION_KINDS:
                    raise CoreError(f"invalid declaration kind: {kind}")
                if name in names:
                    raise CoreError(
                        f"duplicate declaration name: {package_name}.{module_name}.{name}"
                    )
                if name in aliases:
                    raise CoreBindingError(
                        f"ambiguous local/import binding: {package_name}.{module_name}.{name}"
                    )
                names.add(name)
                symbol_id = _required_text(
                    declaration.get(
                        "id",
                        _digest(
                            "sym",
                            {
                                "package": package_id,
                                "module": module_name,
                                "name": name,
                                "kind": kind,
                            },
                        ),
                    ),
                    "symbol id",
                )
                if symbol_id in symbol_ids:
                    raise CoreError(f"duplicate symbol id: {symbol_id}")
                symbol_ids.add(symbol_id)
                contract = _contract_for(declaration, kind)
                implementation = _implementation_for(declaration)
                effects = tuple(sorted(_texts(declaration.get("effects", []), "effects")))
                capabilities = tuple(
                    sorted(_texts(declaration.get("capabilities", []), "capabilities"))
                )
                if kind not in {"task", "capability"} and effects:
                    raise CoreError(
                        f"effects are only valid on task or capability declarations: {name}"
                    )
                explicitly_exported = bool(declaration.get("export", False))
                is_exported = name in export_names or explicitly_exported
                semantic_state = {
                    "package_id": package_id,
                    "module": module_name,
                    "name": name,
                    "kind": kind,
                    "exported": is_exported,
                    "typed_contract": contract,
                    "implementation": implementation,
                    "effects": list(effects),
                    "capabilities": list(capabilities),
                }
                symbol = CoreSymbol(
                    id=symbol_id,
                    revision_id=_digest("rev", semantic_state),
                    package_id=package_id,
                    package_name=package_name,
                    module=module_name,
                    name=name,
                    kind=kind,
                    exported=is_exported,
                    contract_json=_canonical(contract),
                    implementation_json=_canonical(implementation),
                    effects=effects,
                    capabilities=capabilities,
                )
                by_name[name] = symbol
                pending.append(
                    (declaration, symbol, _references_for(declaration), declaration_index)
                )
            missing_exports = sorted(set(export_names) - names)
            if missing_exports:
                raise CoreError(
                    f"invalid exports in {package_name}.{module_name}: "
                    + ", ".join(missing_exports)
                )
            module_data[module_key] = {
                "package_name": package_name,
                "package_id": package_id,
                "module_name": module_name,
                "imports": normalized_imports,
                "exports": tuple(sorted(name for name, sym in by_name.items() if sym.exported)),
            }
            module_symbols[module_key] = by_name
            for name, symbol in by_name.items():
                if symbol.exported:
                    exported[(package_name, module_name, name)] = symbol
                    exported[(package_id, module_name, name)] = symbol
            for declaration, symbol, refs, declaration_index in pending:
                raw.append(
                    _RawDeclaration(
                        symbol,
                        package_index,
                        module_index,
                        declaration_index,
                        declaration,
                        refs,
                    )
                )

    symbols_by_id = {item.symbol.id: item.symbol for item in raw}
    references: list[CoreReference] = []
    for item in raw:
        symbol = item.symbol
        module_key = (symbol.package_id, symbol.module)
        local = module_symbols[module_key]
        imports = module_data[module_key]["imports"]
        imported: dict[str, tuple[str, CoreSymbol | str]] = {}
        for import_item in imports:
            alias = import_item["alias"]
            if "foreign" in import_item:
                imported[alias] = (
                    "Foreign",
                    f"{import_item['foreign']}::{import_item['name']}",
                )
                continue
            export_key = (
                import_item["package"],
                import_item["module"],
                import_item["name"],
            )
            target = exported.get(export_key)
            if target is None:
                package_exists = (
                    import_item["package"] in package_names
                    or import_item["package"] in package_ids
                )
                reason = "hidden or missing export" if package_exists else "unknown package"
                raise CoreBindingError(
                    f"{reason}: {import_item['package']}.{import_item['module']}."
                    f"{import_item['name']}"
                )
            imported[alias] = ("Exact", target)
        for reference_index, reference_value in enumerate(item.reference_values):
            usage = "Value"
            target_id: str | None = None
            foreign_target: str | None = None
            if isinstance(reference_value, str):
                spelling = _required_text(reference_value, "reference")
                reference_object: Mapping[str, Any] = {}
            elif isinstance(reference_value, Mapping):
                reference_object = reference_value
                usage = _required_text(
                    reference_object.get("usage", "Value"), "reference usage"
                )
                direct_foreign = reference_object.get("foreign")
                if direct_foreign is not None:
                    spelling = _required_text(
                        reference_object.get("name", direct_foreign),
                        "foreign reference name",
                    )
                    foreign_target = _required_text(
                        direct_foreign, "foreign reference"
                    )
                    status = "Foreign"
                    reference_id = _digest(
                        "ref",
                        {
                            "owner": symbol.id,
                            "index": reference_index,
                            "target": None,
                            "foreign": foreign_target,
                            "usage": usage,
                        },
                    )
                    references.append(
                        CoreReference(
                            reference_id,
                            symbol.id,
                            spelling,
                            usage,
                            status,
                            foreign_target=foreign_target,
                            ordinal=reference_index,
                        )
                    )
                    continue
                direct_target = reference_object.get("target_id")
                if direct_target is not None:
                    target_id = _required_text(direct_target, "reference target_id")
                    target = symbols_by_id.get(target_id)
                    if target is None:
                        raise CoreBindingError(f"unresolved symbol id: {target_id}")
                    spelling = _required_text(
                        reference_object.get("name", target.name), "reference name"
                    )
                    visible = (
                        target.package_id == symbol.package_id
                        and target.module == symbol.module
                    ) or any(
                        status == "Exact"
                        and isinstance(candidate, CoreSymbol)
                        and candidate.id == target.id
                        for status, candidate in imported.values()
                    )
                    if not visible:
                        raise CoreBindingError(
                            f"hidden direct reference {target.id} in {symbol.locator}"
                        )
                    if usage == "Type" and target.kind != "interface":
                        raise CoreBindingError(
                            f"type reference {spelling!r} does not target an interface"
                        )
                    status = "Exact"
                    reference_id = _digest(
                        "ref",
                        {
                            "owner": symbol.id,
                            "index": reference_index,
                            "target": target_id,
                            "foreign": None,
                            "usage": usage,
                        },
                    )
                    references.append(
                        CoreReference(
                            reference_id,
                            symbol.id,
                            spelling,
                            usage,
                            status,
                            target_id=target_id,
                            ordinal=reference_index,
                        )
                    )
                    continue
                spelling = _required_text(
                    _field(reference_object, "name", "ref"), "reference name"
                )
            else:
                raise CoreError("references must contain strings or objects")
            candidates: list[tuple[str, CoreSymbol | str]] = []
            if spelling in local:
                candidates.append(("Exact", local[spelling]))
            if spelling in imported:
                candidates.append(imported[spelling])
            if len(candidates) != 1:
                reason = "ambiguous" if candidates else "unresolved"
                raise CoreBindingError(
                    f"{reason} reference {spelling!r} in {symbol.locator}"
                )
            status, resolved = candidates[0]
            if status == "Exact":
                assert isinstance(resolved, CoreSymbol)
                target_id = resolved.id
                if usage == "Type" and resolved.kind != "interface":
                    raise CoreBindingError(
                        f"type reference {spelling!r} does not target an interface"
                    )
            else:
                assert isinstance(resolved, str)
                foreign_target = resolved
            reference_id = _digest(
                "ref",
                {
                    "owner": symbol.id,
                    "index": reference_index,
                    "target": target_id,
                    "foreign": foreign_target,
                    "usage": usage,
                },
            )
            references.append(
                CoreReference(
                    reference_id,
                    symbol.id,
                    spelling,
                    usage,
                    status,
                    target_id=target_id,
                    foreign_target=foreign_target,
                    ordinal=reference_index,
                )
            )

    # Task capabilities are themselves ordinary exact bindings, but their
    # semantic permission must also cover every declared effect.
    capabilities_by_module: dict[tuple[str, str], dict[str, CoreSymbol]] = {}
    for raw_item in raw:
        sym = raw_item.symbol
        if sym.kind == "capability":
            capabilities_by_module.setdefault((sym.package_id, sym.module), {})[
                sym.name
            ] = sym
    for raw_item in raw:
        sym = raw_item.symbol
        if sym.kind != "task":
            continue
        available: dict[str, CoreSymbol] = dict(
            capabilities_by_module.get((sym.package_id, sym.module), {})
        )
        for import_item in module_data[(sym.package_id, sym.module)]["imports"]:
            if "foreign" in import_item:
                continue
            target = exported.get(
                (
                    import_item["package"],
                    import_item["module"],
                    import_item["name"],
                )
            )
            if target is not None and target.kind == "capability":
                available[import_item["alias"]] = target
        covered: set[str] = set()
        for capability_name in sym.capabilities:
            capability = available.get(capability_name)
            if capability is None:
                raise CoreError(
                    f"task {sym.locator} requires missing capability {capability_name}"
                )
            contract = capability.typed_contract
            declared_effects = (
                contract.get("effects", []) if isinstance(contract, dict) else []
            )
            covered.update(str(effect) for effect in declared_effects)
        missing = sorted(set(sym.effects) - covered)
        if missing:
            raise CoreError(
                f"task {sym.locator} has effects without capabilities: "
                + ", ".join(missing)
            )

    core_references = tuple(sorted(references, key=lambda item: item.id))
    revised_symbols: list[CoreSymbol] = []
    for raw_item in raw:
        symbol = raw_item.symbol
        outbound = [
            {
                "status": reference.status,
                "target_id": reference.target_id,
                "foreign_target": reference.foreign_target,
                "usage": reference.usage,
            }
            for reference in core_references
            if reference.owner_id == symbol.id
        ]
        revision_state = {
            "package_id": symbol.package_id,
            "module": symbol.module,
            "name": symbol.name,
            "kind": symbol.kind,
            "exported": symbol.exported,
            "typed_contract": symbol.typed_contract,
            "implementation": symbol.implementation,
            "effects": list(symbol.effects),
            "capabilities": list(symbol.capabilities),
            "references": outbound,
        }
        revised_symbols.append(
            replace(symbol, revision_id=_digest("rev", revision_state))
        )
    core_symbols = tuple(sorted(revised_symbols, key=lambda item: item.id))
    revised_by_id = {item.id: item for item in core_symbols}

    def public_symbol_state(item: CoreSymbol) -> dict[str, Any]:
        type_dependencies: list[dict[str, str]] = []
        for reference in core_references:
            if (
                reference.owner_id != item.id
                or reference.usage != "Type"
                or reference.target_id is None
            ):
                continue
            dependency = revised_by_id[reference.target_id]
            dependency_contract = {
                "package_id": dependency.package_id,
                "module": dependency.module,
                "name": dependency.name,
                "kind": dependency.kind,
                "typed_contract": dependency.typed_contract,
                "effects": list(dependency.effects),
                "capabilities": list(dependency.capabilities),
            }
            type_dependencies.append(
                {
                    "target_id": dependency.id,
                    "contract_revision": _digest(
                        "contract", dependency_contract
                    ),
                }
            )
        return {
            "module": item.module,
            "name": item.name,
            "kind": item.kind,
            "typed_contract": item.typed_contract,
            "effects": list(item.effects),
            "capabilities": list(item.capabilities),
            "type_dependencies": type_dependencies,
        }

    packages: list[CorePackage] = []
    for package_value in package_values:
        package_name = package_value["name"]
        package_id = package_value.get("id", _digest("pkg", package_name))
        package_symbols = tuple(
            sorted(
                (item for item in core_symbols if item.package_id == package_id),
                key=lambda item: item.id,
            )
        )
        public_contract = [
            public_symbol_state(item)
            for item in sorted(
                (symbol for symbol in package_symbols if symbol.exported),
                key=lambda symbol: (symbol.module, symbol.name, symbol.id),
            )
        ]
        implementation_state = {
            "symbols": [
                item.to_dict()
                for item in sorted(
                    package_symbols,
                    key=lambda symbol: (symbol.module, symbol.name, symbol.id),
                )
            ],
            "references": [
                reference.to_dict()
                for reference in core_references
                if revised_by_id[reference.owner_id].package_id == package_id
            ],
        }
        modules = tuple(
            sorted(
                key[1] for key in module_keys if key[0] == package_id
            )
        )
        packages.append(
            CorePackage(
                id=package_id,
                name=package_name,
                interface_revision=_digest("iface", public_contract),
                implementation_revision=_digest("impl", implementation_state),
                modules=modules,
                symbol_ids=tuple(item.id for item in package_symbols),
                exported_symbol_ids=tuple(
                    item.id for item in package_symbols if item.exported
                ),
            )
        )
    return CoreWorld(
        core_symbols,
        core_references,
        tuple(sorted(packages, key=lambda item: item.id)),
        program,
    )


def _find_declaration(
    document: dict[str, Any], symbol: CoreSymbol
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for package in document["packages"]:
        package_id = package.get("id", _digest("pkg", package["name"]))
        if package_id != symbol.package_id:
            continue
        for module in package["modules"]:
            if module["name"] != symbol.module:
                continue
            for declaration in _field(module, "declarations", "symbols", default=[]):
                candidate_id = declaration.get(
                    "id",
                    _digest(
                        "sym",
                        {
                            "package": package_id,
                            "module": module["name"],
                            "name": declaration["name"],
                            "kind": declaration["kind"],
                        },
                    ),
                )
                if candidate_id == symbol.id:
                    declaration["id"] = symbol.id
                    return package, module, declaration
    raise CoreError(f"CoreIR declaration not found for {symbol.id}")


def _materialize_exact_references(document: dict[str, Any], world: CoreWorld) -> None:
    refs_by_owner: dict[str, list[CoreReference]] = {}
    for reference in world.references:
        refs_by_owner.setdefault(reference.owner_id, []).append(reference)
    for owner_id, refs in refs_by_owner.items():
        owner = world.symbol(owner_id)
        _, _, declaration = _find_declaration(document, owner)
        rewritten: list[Any] = []
        for reference in sorted(refs, key=lambda item: (item.ordinal, item.id)):
            if reference.status == "Exact":
                rewritten.append(
                    {
                        "target_id": reference.target_id,
                        "name": reference.spelling,
                        "usage": reference.usage,
                    }
                )
            else:
                rewritten.append(
                    {
                        "foreign": reference.foreign_target,
                        "name": reference.spelling,
                        "usage": reference.usage,
                    }
                )
        declaration["references"] = rewritten
        declaration.pop("refs", None)
        declaration.pop("type_refs", None)


def _set_contract(declaration: dict[str, Any], value: Any) -> None:
    if "typed_contract" in declaration:
        declaration["typed_contract"] = value
    elif "signature" in declaration:
        declaration["signature"] = value
    elif "type" in declaration:
        declaration["type"] = value
    elif "members" in declaration and isinstance(value, Mapping) and "members" in value:
        declaration["members"] = value["members"]
    else:
        declaration["typed_contract"] = value


def _dependent_symbols(world: CoreWorld, roots: set[str]) -> set[str]:
    affected = set(roots)
    changed = True
    while changed:
        changed = False
        for reference in world.references:
            if reference.target_id in affected and reference.owner_id not in affected:
                affected.add(reference.owner_id)
                changed = True
    return affected


def _rewrite_imports_for_change(
    document: dict[str, Any],
    target: CoreSymbol,
    *,
    package: str,
    module: str,
    name: str,
) -> None:
    for package_value in document["packages"]:
        for module_value in package_value["modules"]:
            imports = module_value.get("imports", [])
            for index, import_value in enumerate(imports):
                normalized = _normalize_import(import_value)
                if "foreign" in normalized:
                    continue
                if (
                    normalized["package"] not in {target.package_id, target.package_name}
                    or normalized["module"] != target.module
                    or normalized["name"] != target.name
                ):
                    continue
                imports[index] = {
                    "package": package,
                    "module": module,
                    "name": name,
                    "alias": normalized["alias"],
                }


def _ensure_move_bindings(
    document: dict[str, Any],
    world: CoreWorld,
    target: CoreSymbol,
    destination_package: Mapping[str, Any],
    destination_module: Mapping[str, Any],
) -> None:
    destination_package_id = destination_package.get(
        "id", _digest("pkg", destination_package["name"])
    )
    modules: dict[tuple[str, str], dict[str, Any]] = {}
    for package_value in document["packages"]:
        package_id = package_value.get(
            "id", _digest("pkg", package_value["name"])
        )
        for module_value in package_value["modules"]:
            modules[(package_id, module_value["name"])] = module_value

    for reference in world.references:
        if reference.status != "Exact" or reference.target_id is None:
            continue
        owner = world.symbol(reference.owner_id)
        dependency = world.symbol(reference.target_id)
        owner_package_id = (
            destination_package_id if owner.id == target.id else owner.package_id
        )
        owner_module_name = (
            destination_module["name"] if owner.id == target.id else owner.module
        )
        dependency_package_id = (
            destination_package_id
            if dependency.id == target.id
            else dependency.package_id
        )
        dependency_package_name = (
            destination_package["name"]
            if dependency.id == target.id
            else dependency.package_name
        )
        dependency_module_name = (
            destination_module["name"]
            if dependency.id == target.id
            else dependency.module
        )
        owner_module = modules[(owner_package_id, owner_module_name)]
        same_scope = (
            owner_package_id == dependency_package_id
            and owner_module_name == dependency_module_name
        )
        matching_indexes: list[int] = []
        for index, import_value in enumerate(owner_module.get("imports", [])):
            normalized = _normalize_import(import_value)
            if "foreign" in normalized:
                continue
            package_matches = normalized["package"] in {
                dependency.package_id,
                dependency.package_name,
                dependency_package_id,
                dependency_package_name,
            }
            module_matches = normalized["module"] in {
                dependency.module,
                dependency_module_name,
            }
            if (
                package_matches
                and module_matches
                and normalized["name"] == dependency.name
            ):
                matching_indexes.append(index)
        if same_scope:
            if matching_indexes:
                owner_module["imports"] = [
                    import_value
                    for index, import_value in enumerate(
                        owner_module.get("imports", [])
                    )
                    if index not in set(matching_indexes)
                ]
            continue
        if not dependency.exported:
            raise CoreBindingError(
                f"move would expose hidden dependency {dependency.locator}"
            )
        if matching_indexes:
            continue
        aliases = {
            _normalize_import(import_value)["alias"]
            for import_value in owner_module.get("imports", [])
        }
        if reference.spelling in aliases:
            raise CoreBindingError(
                f"move creates ambiguous import alias {reference.spelling!r}"
            )
        owner_module.setdefault("imports", []).append(
            {
                "package": dependency_package_name,
                "module": dependency_module_name,
                "name": dependency.name,
                "alias": reference.spelling,
            }
        )


def apply_core_change(world: CoreWorld, change: CoreChange) -> CoreChangeResult:
    """Apply one semantic change transactionally.

    Capability escalation returns the unchanged world with ``blocked=True``;
    all other invalid changes raise and likewise cannot expose a partial world.
    """

    if not isinstance(world, CoreWorld):
        raise TypeError("world must be a CoreWorld")
    if not isinstance(change, CoreChange):
        raise TypeError("change must be a CoreChange")
    target = world.symbol(change.symbol_id)
    document = world._program.to_dict()
    _materialize_exact_references(document, world)
    source_package, source_module, declaration = _find_declaration(document, target)
    old_package_revisions = {
        item.id: (item.interface_revision, item.implementation_revision)
        for item in world.packages
    }
    old_effects = set(target.effects)
    old_capabilities = set(target.capabilities)
    proposed_effects = set(change.effects if change.effects is not None else target.effects)
    proposed_capabilities = set(
        change.capabilities
        if change.capabilities is not None
        else target.capabilities
    )
    if change.kind == "RestrictEffect" and not proposed_effects.issubset(
        old_effects
    ):
        raise CoreError("RestrictEffect cannot add effects")
    escalation = (proposed_effects - old_effects) | (
        proposed_capabilities - old_capabilities
    )


    missing_materialization = escalation - set(change.materialized_capabilities)
    if missing_materialization:
        violation = CapabilityViolation(target.id, missing_materialization)
        return CoreChangeResult(
            world=world,
            affected_symbols=(),
            affected_packages=(),
            interface_changed_packages=(),
            blocked=True,
            capability_violation=violation,
        )

    if change.kind == "Rename":
        assert change.new_name is not None
        declaration["name"] = change.new_name
        exports = source_module.setdefault("exports", [])
        for index, export in enumerate(exports):
            if export == target.name:
                exports[index] = change.new_name
            elif isinstance(export, dict) and _field(export, "name", "symbol") == target.name:
                replaced = dict(export)
                if "name" in replaced:
                    replaced["name"] = change.new_name
                else:
                    replaced["symbol"] = change.new_name
                exports[index] = replaced
        _rewrite_imports_for_change(
            document,
            target,
            package=target.package_name,
            module=target.module,
            name=change.new_name,
        )
    elif change.kind == "Move":
        assert change.target_module is not None
        target_package_name = change.target_package or target.package_id
        destination_package = None
        for package in document["packages"]:
            package_id = package.get("id", _digest("pkg", package["name"]))
            if target_package_name in {package_id, package["name"]}:
                destination_package = package
                break
        if destination_package is None:
            raise CoreError(f"move destination package not found: {target_package_name}")
        destination_module = next(
            (
                module
                for module in destination_package["modules"]
                if module["name"] == change.target_module
            ),
            None,
        )
        if destination_module is None:
            raise CoreError(f"move destination module not found: {change.target_module}")
        source_declarations = _field(source_module, "declarations", "symbols")
        destination_key = (
            "declarations" if "declarations" in destination_module else "symbols"
        )
        source_declarations.remove(declaration)
        destination_module[destination_key].append(declaration)
        if target.exported:
            source_exports = source_module.setdefault("exports", [])
            source_module["exports"] = [
                export
                for export in source_exports
                if not (
                    export == target.name
                    or (
                        isinstance(export, Mapping)
                        and _field(export, "name", "symbol") == target.name
                    )
                )
            ]
            destination_module.setdefault("exports", []).append(target.name)
        _rewrite_imports_for_change(
            document,
            target,
            package=destination_package["name"],
            module=change.target_module,
            name=target.name,
        )
        _ensure_move_bindings(
            document,
            world,
            target,
            destination_package,
            destination_module,
        )
    elif change.kind == "ChangeSignature":
        _set_contract(declaration, change.value)
        if change.effects is not None:
            declaration["effects"] = list(change.effects)
        if change.capabilities is not None:
            declaration["capabilities"] = list(change.capabilities)
    elif change.kind == "ChangeImplementation":
        implementation_key = (
            "implementation"
            if "implementation" in declaration
            else "body" if "body" in declaration else "value"
        )
        declaration[implementation_key] = change.value
        if change.effects is not None:
            declaration["effects"] = list(change.effects)
        if change.capabilities is not None:
            declaration["capabilities"] = list(change.capabilities)
    elif change.kind == "RestrictEffect":
        if target.kind != "task":
            raise CoreError("RestrictEffect is only valid for task declarations")
        assert change.effects is not None
        if not set(change.effects).issubset(old_effects):
            raise CoreError("RestrictEffect cannot add effects")
        declaration["effects"] = list(change.effects)
    else:
        raise CoreError(f"unknown CoreChange kind: {change.kind}")

    new_world = compile_core(CoreProgram.from_dict(document))
    new_target = new_world.symbol(target.id)
    target_interface_changed = (
        target.exported
        and (
            target.name != new_target.name
            or target.module != new_target.module
            or target.package_id != new_target.package_id
            or target.kind != new_target.kind
            or target.contract_json != new_target.contract_json
            or target.effects != new_target.effects
            or target.capabilities != new_target.capabilities
        )
    )
    affected_symbol_ids = (
        _dependent_symbols(new_world, {target.id})
        if target_interface_changed
        else {target.id}
    )
    changed_packages = {
        package.id
        for package in new_world.packages
        if old_package_revisions.get(package.id)
        != (package.interface_revision, package.implementation_revision)
    }
    interface_changed = {
        package.id
        for package in new_world.packages
        if old_package_revisions.get(package.id, (None, None))[0]
        != package.interface_revision
    }
    affected_packages = changed_packages | {
        new_world.symbol(symbol_id).package_id for symbol_id in affected_symbol_ids
    }
    return CoreChangeResult(
        world=new_world,
        affected_symbols=tuple(sorted(affected_symbol_ids)),
        affected_packages=tuple(sorted(affected_packages)),
        interface_changed_packages=tuple(sorted(interface_changed)),
    )


__all__ = [
    "CORE_SCHEMA_VERSION",
    "CORE_SCHEMA_ID",
    "CORE_SCHEMA_SHA256",
    "CapabilityViolation",
    "CoreBindingError",
    "CoreChange",
    "CoreChangeResult",
    "CoreError",
    "CorePackage",
    "CoreProgram",
    "CoreReference",
    "CoreSymbol",
    "CoreWorld",
    "apply_core_change",
    "compile_core",
]
