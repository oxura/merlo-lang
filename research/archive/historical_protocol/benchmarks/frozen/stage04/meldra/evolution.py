from __future__ import annotations

import ast
import hashlib
import keyword
import os
import re
import stat
import tempfile
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from research.archive.historical_protocol.merlo.evidence import create_evidence
from research.archive.historical_protocol.merlo.impact import analyze_impact
from research.archive.historical_protocol.merlo.model import (
    ChangePlan,
    ChangeSignature,
    EditCapability,
    Entity,
    Evidence,
    IdentityHint,
    MoveSymbol,
    Obligation,
    Position,
    ProgramIR,
    Provenance,
    Reference,
    RenameSymbol,
    Resolution,
    SourceEdit,
    Span,
)
from research.archive.historical_protocol.merlo.obligations import (
    build_graph,
    identity_obligations,
    make_obligation,
    uncertain_reference_obligation,
)


class ChangeBlocked(Exception):
    def __init__(self, obligations: Iterable[Obligation]):
        self.obligations = tuple(obligations)
        message = "; ".join(item.message for item in self.obligations)
        super().__init__(message or "semantic change is blocked")


@dataclass(frozen=True)
class _Parameter:
    name: str
    kind: str
    has_default: bool
    semantic_shape: str = ""


@dataclass(frozen=True)
class _ParsedSignature:
    source: str
    parameters: tuple[_Parameter, ...]


_MODULE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_MODULE_EDGE_CACHE_LIMIT = 16
_MODULE_EDGE_CACHE: dict[str, frozenset[tuple[str, str]]] = {}




def plan_rename(
    program: ProgramIR,
    target: str,
    new_name: str,
    capability: EditCapability,
    *,
    goal: str = "Rename a semantic symbol and migrate all known references",
) -> ChangePlan:
    try:
        entity = program.entity(target)
    except KeyError as exc:
        phantom = _unknown_entity(target)
        change = _make_rename(phantom.id, new_name, goal)
        obligation = make_obligation(
            change.id, "UnknownTarget", str(exc).strip("'")
        )
        return ChangePlan(change, phantom, (), (obligation,), ())

    change = _make_rename(entity.id, new_name, goal)
    obligations: list[Obligation] = list(
        identity_obligations(program, change.id, entity.id)
    )
    if "." in entity.qualname:
        obligations.append(
            make_obligation(
                change.id,
                "UnsupportedBinding",
                "RenameSymbol currently accepts module-level symbols only",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if not new_name.isidentifier() or keyword.iskeyword(new_name):
        obligations.append(
            make_obligation(
                change.id,
                "InvalidIdentifier",
                f"{new_name!r} is not a valid Python identifier",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if new_name == entity.name:
        obligations.append(
            make_obligation(
                change.id,
                "NoChange",
                f"{entity.fqname} is already named {new_name!r}",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if _is_semantic_protocol_name(entity.name):
        obligations.append(
            make_obligation(
                change.id,
                "SemanticProtocolName",
                f"{entity.fqname} is invoked by the Python runtime by name",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if _module_has_dynamic_namespace(program, entity.module):
        obligations.append(
            make_obligation(
                change.id,
                "DynamicModuleNamespace",
                (
                    f"{entity.module} defines __getattr__ or __dir__; "
                    "static reference migration is incomplete"
                ),
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    collision = _entity_collision(program, entity.module, new_name, entity.id)
    if collision is not None:
        obligations.append(
            make_obligation(
                change.id,
                "NameCollision",
                f"{collision.fqname} already owns the requested name",
                files=(collision.file, entity.file),
                affected_entities=(entity.id, collision.id),
            )
        )

    exact_references = tuple(
        reference
        for reference in program.references_to(entity.id)
        if not reference.uncertain and reference.rename_on_target
    )
    stable_aliases = tuple(
        reference
        for reference in program.references_to(entity.id)
        if not reference.uncertain and not reference.rename_on_target
    )
    edits = [
        SourceEdit(
            file=entity.file,
            span=entity.definition_span,
            expected=entity.name,
            replacement=new_name,
            reason="definition",
            category="rename",
            affected_entity_ids=(entity.id,),
        )
    ]
    edits.extend(
        SourceEdit(
            file=reference.file,
            span=reference.span,
            expected=reference.expected,
            replacement=new_name,
            reason=reference.kind,
            category="migration",
            affected_entity_ids=tuple(
                identifier
                for identifier in (reference.owner_id, entity.id)
                if identifier is not None
            ),
        )
        for reference in exact_references
    )
    edits = _deduplicate_edits(edits)

    uncertain = program.uncertain_references_to(entity.id)
    obligations.extend(
        _uncertainty_dag(change.id, entity.id, uncertain, "RenameBindingUncertainty")
    )
    if entity.public and "public_api_break" in capability.forbidden_categories:
        obligations.append(
            make_obligation(
                change.id,
                "PublicApiCompatibility",
                "renaming a public symbol may break consumers outside the workspace",
                files=(entity.file,),
                affected_entities=(entity.id,),
                evidence_required=("ExternalConsumerCompatibility",),
                possible_resolutions=(
                    "grant public_api_break explicitly",
                    "introduce a compatibility alias",
                ),
            )
        )
    obligations.extend(
        _capability_obligations(change, entity, edits, capability)
    )
    obligations.extend(_preflight_obligations(program, change.id, edits))
    obligations = list(build_graph(obligations).obligations)

    evidence = _common_evidence(
        program,
        change.id,
        entity,
        edits,
        obligations,
        migrated_references=len(exact_references),
        stable_alias_references=len(stable_aliases),
    )
    impact = analyze_impact(
        program,
        entity.id,
        edits=edits,
        obligations=obligations,
        evidence=evidence,
    )
    hint = IdentityHint(
        entity_id=entity.id,
        kind=entity.kind,
        module=entity.module,
        qualname=_renamed_qualname(entity, new_name),
        caused_by=change.id,
    )
    return ChangePlan(
        change=change,
        target=entity,
        edits=tuple(edits),
        obligations=tuple(obligations),
        evidence=tuple(evidence),
        identity_hints=(hint,),
        impact=impact,
        inverse={
            "operation": "rename_symbol",
            "target_id": entity.id,
            "new_name": entity.name,
        },
    )


def plan_move(
    program: ProgramIR,
    target: str,
    target_module: str,
    capability: EditCapability,
    *,
    goal: str = "Move a semantic symbol and migrate static imports",
) -> ChangePlan:
    try:
        entity = program.entity(target)
    except KeyError as exc:
        phantom = _unknown_entity(target)
        change = _make_move(phantom.id, target_module, goal)
        obligation = make_obligation(
            change.id, "UnknownTarget", str(exc).strip("'")
        )
        return ChangePlan(change, phantom, (), (obligation,), ())

    change = _make_move(entity.id, target_module, goal)
    obligations: list[Obligation] = list(
        identity_obligations(program, change.id, entity.id)
    )
    if "." in entity.qualname:
        obligations.append(
            make_obligation(
                change.id,
                "UnsupportedBinding",
                "MoveSymbol currently accepts module-level symbols only",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if _is_semantic_protocol_name(entity.name):
        obligations.append(
            make_obligation(
                change.id,
                "SemanticProtocolName",
                f"{entity.fqname} is invoked by the Python runtime by name",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if not _MODULE_RE.fullmatch(target_module):
        obligations.append(
            make_obligation(
                change.id,
                "InvalidModule",
                f"{target_module!r} is not a valid module name",
                affected_entities=(entity.id,),
            )
        )
    if target_module == entity.module:
        obligations.append(
            make_obligation(
                change.id,
                "NoChange",
                f"{entity.fqname} is already in {target_module}",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if _module_has_dynamic_namespace(program, entity.module):
        obligations.append(
            make_obligation(
                change.id,
                "DynamicModuleNamespace",
                (
                    f"{entity.module} defines __getattr__ or __dir__; "
                    "static reference migration is incomplete"
                ),
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    collision = _entity_collision(program, target_module, entity.name, entity.id)
    if collision is not None:
        obligations.append(
            make_obligation(
                change.id,
                "TargetCollision",
                f"{collision.fqname} already exists in the target module",
                files=(collision.file, entity.file),
                affected_entities=(entity.id, collision.id),
            )
        )

    root = Path(program.root)
    source_path = root / entity.file
    source_text = source_path.read_text(encoding="utf-8").removeprefix("\ufeff")
    source_span = entity.source_span or entity.definition_span
    definition_text = _extract_span(source_text, source_span)
    target_file = program.file_for_module(target_module)
    if target_file is None:
        candidate = Path(*target_module.split(".")).with_suffix(".py")
        target_file = candidate.as_posix()
        if not (root / target_file).parent.is_dir():
            obligations.append(
                make_obligation(
                    change.id,
                    "TargetPackageMissing",
                    f"parent package for {target_module} does not exist",
                    files=(target_file,),
                    affected_entities=(entity.id,),
                )
            )

    edits: list[SourceEdit] = [
        SourceEdit(
            file=entity.file,
            span=source_span,
            expected=definition_text,
            replacement="",
            reason="move_source",
            category="move",
            affected_entity_ids=(entity.id,),
        )
    ]
    moved_range = source_span
    import_edits: dict[tuple[str, Position, Position], SourceEdit] = {}
    added_edges: set[tuple[str, str]] = set()
    old_module_needs_import = False
    uncertain = program.uncertain_references_to(entity.id)
    obligations.extend(
        _uncertainty_dag(change.id, entity.id, uncertain, "MoveBindingUncertainty")
    )

    exact_refs = tuple(
        reference
        for reference in program.references_to(entity.id)
        if not reference.uncertain
    )
    for reference in exact_refs:
        if reference.file == entity.file and _span_contains(moved_range, reference.span):
            continue
        reference_module = _reference_module(program, reference)
        if reference_module == target_module:
            obligations.append(
                make_obligation(
                    change.id,
                    "MoveDestinationSelfImport",
                    (
                        f"reference in {reference.file} would become an "
                        "import from its own partially initialized module"
                    ),
                    files=(reference.file, entity.file),
                    affected_entities=tuple(
                        identifier
                        for identifier in (reference.owner_id, entity.id)
                        if identifier is not None
                    ),
                )
            )
            continue
        if (
            reference.qualifier_span is not None
            and reference.provenance in {Provenance.IMPORT, Provenance.ALIAS}
        ):
            edit = SourceEdit(
                file=reference.file,
                span=reference.qualifier_span,
                expected=reference.qualifier or "",
                replacement=target_module,
                reason="move_import",
                category="new_dependency",
                affected_entity_ids=tuple(
                    identifier
                    for identifier in (reference.owner_id, entity.id)
                    if identifier is not None
                ),
            )
            import_edits[(edit.file, edit.span.start, edit.span.end)] = edit
            if reference_module and reference_module != target_module:
                added_edges.add((reference_module, target_module))
        elif reference.provenance == Provenance.ATTRIBUTE and reference.qualifier_span:
            conflicts = [
                other
                for other in program.references
                if other.file == reference.file
                and other.qualifier_span == reference.qualifier_span
                and other.target_id not in {None, entity.id}
            ]
            if conflicts:
                obligations.append(
                    make_obligation(
                        change.id,
                        "QualifiedImportMove",
                        (
                            f"module alias in {reference.file} also serves "
                            f"{len(conflicts)} other semantic targets"
                        ),
                        files=(reference.file,),
                        affected_entities=tuple(
                            sorted(
                                {entity.id}
                                | {
                                    item.target_id
                                    for item in conflicts
                                    if item.target_id is not None
                                }
                            )
                        ),
                        possible_resolutions=(
                            "split the module import",
                            "introduce a symbol import for the moved entity",
                        ),
                    )
                )
            else:
                edit = SourceEdit(
                    file=reference.file,
                    span=reference.qualifier_span,
                    expected=reference.qualifier or "",
                    replacement=target_module,
                    reason="move_qualified_import",
                    category="new_dependency",
                    affected_entity_ids=tuple(
                        identifier
                        for identifier in (reference.owner_id, entity.id)
                        if identifier is not None
                    ),
                )
                import_edits[(edit.file, edit.span.start, edit.span.end)] = edit
                if reference_module and reference_module != target_module:
                    added_edges.add((reference_module, target_module))
        elif reference.file == entity.file:
            old_module_needs_import = True
        else:
            obligations.append(
                make_obligation(
                    change.id,
                    "UnqualifiedMoveReference",
                    (
                        f"cannot prove import ownership for reference in "
                        f"{reference.file}:{reference.span.start.line}"
                    ),
                    files=(reference.file,),
                    affected_entities=tuple(
                        identifier
                        for identifier in (reference.owner_id, entity.id)
                        if identifier is not None
                    ),
                )
            )
    edits.extend(import_edits.values())

    dependency_imports: set[str] = set()
    dependency_modules: set[str] = set()
    dependency_bindings: dict[str, str] = {}
    moved_entity_ids = {
        candidate.id
        for candidate in program.entities
        if candidate.file == entity.file
        and candidate.source_span is not None
        and _span_contains(moved_range, candidate.source_span)
    }
    for reference in program.references:
        if not _span_contains(moved_range, reference.span):
            continue
        if reference.target_id in moved_entity_ids:
            continue
        if reference.target_id is None:
            if reference.target_id is None and reference.provenance == Provenance.EXTERNAL_IMPORT:
                statement = _external_import_statement(reference)
                if statement:
                    dependency_imports.add(statement)
                    local_name = str(
                        reference.metadata.get("local_name", reference.expected)
                    )
                    dependency_bindings[local_name] = statement
                    module = str(reference.metadata.get("imported_module", ""))
                    if module:
                        dependency_modules.add(module)
                else:
                    obligations.append(
                        make_obligation(
                            change.id,
                            "MoveDependencyUnknown",
                            (
                                f"unresolved dependency {reference.expected!r} "
                                f"inside {entity.fqname}"
                            ),
                            files=(entity.file, target_file),
                            affected_entities=(entity.id,),
                        )
                    )
            elif reference.target_id is None and reference.resolution == Resolution.UNKNOWN:
                obligations.append(
                    make_obligation(
                        change.id,
                        "MoveDependencyUnknown",
                        (
                            f"unresolved dependency {reference.expected!r} "
                            f"inside {entity.fqname}"
                        ),
                        files=(entity.file, target_file),
                        affected_entities=(entity.id,),
                    )
                )
            continue
        try:
            dependency = program.entity(reference.target_id)
        except KeyError as exc:
            obligations.append(
                make_obligation(
                    change.id,
                    "MoveDependencyAmbiguous",
                    str(exc).strip("'"),
                    files=(entity.file, target_file),
                    affected_entities=(entity.id,),
                )
            )
            continue
        if dependency.module == target_module:
            continue
        dependency_modules.add(dependency.module)
        local_name = str(reference.metadata.get("local_name", reference.expected))
        source_name = str(reference.metadata.get("source_name", dependency.name))
        alias = f" as {local_name}" if local_name != source_name else ""
        dependency_imports.add(
            f"from {dependency.module} import {source_name}{alias}"
        )
        dependency_bindings[local_name] = (
            f"from {dependency.module} import {source_name}{alias}"
        )

    if old_module_needs_import:
        insertion = _import_insertion_edit(
            program,
            entity.file,
            f"from {target_module} import {entity.name}\n",
            reason="move_old_module_bridge",
            affected_entity_ids=(entity.id,),
        )
        edits.append(insertion)

    target_path = root / target_file
    target_exists = target_path.exists()
    target_source = (
        target_path.read_text(encoding="utf-8").removeprefix("\ufeff")
        if target_exists
        else ""
    )
    if target_source and _has_unconditional_import_time_exit(
        target_source, target_file
    ):
        obligations.append(
            make_obligation(
                change.id,
                "MoveDestinationImportHazard",
                (
                    f"{target_module} has an unconditional import-time "
                    "assertion or raise"
                ),
                files=(target_file,),
                affected_entities=(entity.id,),
            )
        )
    if target_source and dependency_bindings:
        existing_imports = {
            line.strip()
            for line in target_source.splitlines()
            if line.lstrip().startswith(("import ", "from "))
        }
        bound_names = _module_bound_names(target_source, target_file)
        collisions = tuple(
            sorted(
                name
                for name, statement in dependency_bindings.items()
                if name in bound_names and statement not in existing_imports
            )
        )
        if collisions:
            obligations.append(
                make_obligation(
                    change.id,
                    "MoveDependencyCollision",
                    (
                        "moved definition dependencies collide with target-module "
                        f"bindings: {', '.join(collisions)}"
                    ),
                    files=(entity.file, target_file),
                    affected_entities=(entity.id,),
                    possible_resolutions=(
                        "move to a module without colliding bindings",
                        "introduce explicit dependency aliases and rewrite the moved body",
                    ),
                )
            )
        dependency_imports = {
            statement
            for statement in dependency_imports
            if statement not in existing_imports
        }
    prefix = ""
    if dependency_imports:
        prefix = "\n".join(sorted(dependency_imports)) + "\n\n"
    definition_payload = prefix + definition_text
    if target_source:
        separator = "\n\n" if not target_source.endswith("\n\n") else ""
        replacement = separator + definition_payload + "\n"
        insertion_position = _end_position(target_source)
    else:
        replacement = definition_payload + "\n"
        insertion_position = Position(1, 0)
    edits.append(
        SourceEdit(
            file=target_file,
            span=Span(insertion_position, insertion_position),
            expected="",
            replacement=replacement,
            reason="move_target",
            category="new_dependency" if dependency_imports else "move",
            affected_entity_ids=(entity.id,),
            allow_create=not target_exists,
        )
    )

    if old_module_needs_import:
        added_edges.add((entity.module, target_module))
    added_edges.update((target_module, module) for module in dependency_modules)
    for source_module, destination_module in added_edges:
        if _would_create_cycle(program, source_module, destination_module, added_edges):
            obligations.append(
                make_obligation(
                    change.id,
                    "CyclicDependency",
                    f"move introduces a cycle through {source_module} -> {destination_module}",
                    files=(entity.file, target_file),
                    affected_entities=(entity.id,),
                    possible_resolutions=(
                        "move the shared dependency to a third module",
                        "introduce a dependency-inversion adapter",
                    ),
                )
            )

    if entity.public and "public_api_break" in capability.forbidden_categories:
        obligations.append(
            make_obligation(
                change.id,
                "PublicApiCompatibility",
                "moving a public symbol changes its import path",
                files=(entity.file, target_file),
                affected_entities=(entity.id,),
                possible_resolutions=(
                    "grant public_api_break explicitly",
                    "keep a re-export compatibility adapter",
                ),
            )
        )
    edits = _deduplicate_edits(edits)
    obligations.extend(_capability_obligations(change, entity, edits, capability))
    obligations.extend(_preflight_obligations(program, change.id, edits))
    obligations = list(build_graph(obligations).obligations)
    evidence = _common_evidence(
        program,
        change.id,
        entity,
        edits,
        obligations,
        migrated_references=len(exact_refs),
        stable_alias_references=0,
    )
    impact = analyze_impact(
        program,
        entity.id,
        edits=edits,
        obligations=obligations,
        evidence=evidence,
    )
    hint = IdentityHint(
        entity_id=entity.id,
        kind=entity.kind,
        module=target_module,
        qualname=entity.qualname,
        caused_by=change.id,
    )
    return ChangePlan(
        change=change,
        target=entity,
        edits=tuple(edits),
        obligations=tuple(obligations),
        evidence=tuple(evidence),
        identity_hints=(hint,),
        impact=impact,
        inverse={
            "operation": "move_symbol",
            "target_id": entity.id,
            "target_module": entity.module,
        },
    )


def plan_change_signature(
    program: ProgramIR,
    target: str,
    new_signature: str,
    capability: EditCapability,
    *,
    argument_values: Mapping[str, str] | None = None,
    goal: str = "Change a function signature and migrate exact callers",
) -> ChangePlan:
    try:
        entity = program.entity(target)
    except KeyError as exc:
        phantom = _unknown_entity(target)
        change = _make_signature(phantom.id, new_signature, goal, argument_values)
        obligation = make_obligation(
            change.id, "UnknownTarget", str(exc).strip("'")
        )
        return ChangePlan(change, phantom, (), (obligation,), ())

    change = _make_signature(entity.id, new_signature, goal, argument_values)
    obligations: list[Obligation] = list(
        identity_obligations(program, change.id, entity.id)
    )
    if entity.kind not in {"function", "async_function"} or entity.signature_span is None:
        obligations.append(
            make_obligation(
                change.id,
                "UnsupportedBinding",
                "ChangeSignature currently accepts Python functions only",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
        old = _ParsedSignature(entity.signature_source or "()", ())
        new = old
    else:
        try:
            old = _parse_signature(entity.signature_source)
            new = _parse_signature(new_signature)
        except ValueError as exc:
            obligations.append(
                make_obligation(
                    change.id,
                    "InvalidSignature",
                    str(exc),
                    files=(entity.file,),
                    affected_entities=(entity.id,),
                )
            )
            old = _ParsedSignature(entity.signature_source or "()", ())
            new = old

    added, compatible = _signature_extension(old, new)
    if not compatible:
        obligations.append(
            make_obligation(
                change.id,
                "UnsupportedSignatureMigration",
                (
                    "automatic migration supports only parameters appended to "
                    "the existing signature"
                ),
                files=(entity.file,),
                affected_entities=(entity.id,),
                possible_resolutions=(
                    "split the change into additive and cleanup phases",
                    "provide a compatibility adapter",
                ),
            )
        )
    if any(parameter.kind == "positional_only" for parameter in added):
        obligations.append(
            make_obligation(
                change.id,
                "PositionalOnlyMigration",
                "new positional-only parameters are not migrated automatically",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )

    values = dict(argument_values or {})
    required = tuple(parameter for parameter in added if not parameter.has_default)
    for name, expression in values.items():
        try:
            ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            obligations.append(
                make_obligation(
                    change.id,
                    "InvalidMigrationExpression",
                    f"migration expression for {name!r} is invalid: {exc.msg}",
                    affected_entities=(entity.id,),
                )
            )
    missing_values = [parameter.name for parameter in required if parameter.name not in values]
    if missing_values:
        obligations.append(
            make_obligation(
                change.id,
                "MissingArgumentMigration",
                f"missing migration values for required parameters: {', '.join(missing_values)}",
                files=(entity.file,),
                affected_entities=(entity.id,),
                possible_resolutions=(
                    "supply --argument NAME=EXPRESSION",
                    "make the new parameter optional",
                ),
            )
        )

    edits: list[SourceEdit] = []
    if entity.signature_span is not None:
        edits.append(
            SourceEdit(
                file=entity.file,
                span=entity.signature_span,
                expected=entity.signature_source,
                replacement=new_signature,
                reason="change_signature",
                category="signature_refinement",
                affected_entity_ids=(entity.id,),
            )
        )

    signature_root: Obligation | None = None
    complex_obligations: list[Obligation] = []
    if required:
        signature_root = make_obligation(
            change.id,
            "SignatureCompatibilityRoot",
            "required parameters expand caller obligations",
            affected_entities=(entity.id,),
            severity="warning",
            possible_resolutions=("resolve every dependent caller obligation",),
        )
        complex_obligations.append(signature_root)

    if required and not missing_values and compatible:
        additions = tuple(
            (parameter.name, values[parameter.name]) for parameter in required
        )
        for edge in program.calls:
            if edge.target_id != entity.id:
                continue
            if edge.span is None:
                continue
            if any(argument.kind in {"starred", "double_star"} for argument in edge.arguments):
                complex_obligations.append(
                    make_obligation(
                        change.id,
                        "VariadicCallCompatibility",
                        f"variadic call at {edge.file}:{edge.line} cannot be migrated exactly",
                        files=(edge.file,),
                        affected_entities=tuple(
                            identifier
                            for identifier in (entity.id, edge.source_id)
                            if identifier is not None
                        ),
                        depends_on=(signature_root.id,) if signature_root else (),
                        evidence_required=("RuntimeCallCompatibility",),
                    )
                )
                continue
            insertion, expected = _call_argument_insertion(
                program, edge, additions
            )
            edits.append(
                SourceEdit(
                    file=edge.file,
                    span=Span(insertion, insertion),
                    expected="",
                    replacement=expected,
                    reason="migrate_direct_call",
                    category="migration",
                    affected_entity_ids=tuple(
                        identifier
                        for identifier in (entity.id, edge.source_id)
                        if identifier is not None
                    ),
                )
            )

        call_reference_ids = {
            edge.reference_id for edge in program.calls if edge.target_id == entity.id
        }
        for reference in program.references_to(entity.id):
            if reference.id in call_reference_ids or reference.usage == "Import":
                continue
            if reference.owner_id == entity.id and reference.file == entity.file:
                continue
            if reference.usage not in {
                "Callback",
                "Partial",
                "StoredValue",
                "Decorator",
                "Value",
            }:
                continue
            kind = {
                "Partial": "PartialCompatibility",
                "Decorator": "DecoratorCompatibility",
                "StoredValue": "StoredFunctionCompatibility",
                "Callback": "CallbackCompatibility",
            }.get(reference.usage, "CallbackCompatibility")
            complex_obligations.append(
                make_obligation(
                    change.id,
                    kind,
                    (
                        f"function value at {reference.file}:"
                        f"{reference.span.start.line} may be called indirectly"
                    ),
                    files=(reference.file,),
                    affected_entities=tuple(
                        identifier
                        for identifier in (entity.id, reference.owner_id)
                        if identifier is not None
                    ),
                    depends_on=(signature_root.id,) if signature_root else (),
                    evidence_required=("IndirectCallerCompatibility",),
                )
            )

    for reference in program.uncertain_references_to(entity.id):
        complex_obligations.append(
            uncertain_reference_obligation(
                change.id,
                reference,
                entity.id,
                kind="DynamicCallCompatibility",
                depends_on=(signature_root.id,) if signature_root else (),
            )
        )
    obligations.extend(complex_obligations)
    if (
        entity.public
        and required
        and "public_api_break" in capability.forbidden_categories
    ):
        obligations.append(
            make_obligation(
                change.id,
                "PublicApiCompatibility",
                "required parameters break unknown external callers",
                files=(entity.file,),
                affected_entities=(entity.id,),
                depends_on=(signature_root.id,) if signature_root else (),
                possible_resolutions=(
                    "make the parameter optional",
                    "grant public_api_break explicitly",
                    "introduce a compatibility wrapper",
                ),
            )
        )

    edits = _deduplicate_edits(edits)
    obligations.extend(_capability_obligations(change, entity, edits, capability))
    obligations.extend(_preflight_obligations(program, change.id, edits))
    obligations = list(build_graph(obligations).obligations)
    evidence = _common_evidence(
        program,
        change.id,
        entity,
        edits,
        obligations,
        migrated_references=sum(1 for edit in edits if edit.reason == "migrate_direct_call"),
        stable_alias_references=0,
    )
    evidence.append(
        create_evidence(
            program,
            change.id,
            "behavior_preservation",
            "Unknown",
            "signature syntax and known callers were checked; behavior equivalence was not proven",
            entity_ids=(entity.id,),
            reference_targets=(entity.id,),
        )
    )
    impact = analyze_impact(
        program,
        entity.id,
        edits=edits,
        obligations=obligations,
        evidence=evidence,
    )
    hint = IdentityHint(
        entity_id=entity.id,
        kind=entity.kind,
        module=entity.module,
        qualname=entity.qualname,
        caused_by=change.id,
    )
    return ChangePlan(
        change=change,
        target=entity,
        edits=tuple(edits),
        obligations=tuple(obligations),
        evidence=tuple(evidence),
        identity_hints=(hint,),
        impact=impact,
        inverse={
            "operation": "change_signature",
            "target_id": entity.id,
            "new_signature": entity.signature_source,
        },
    )


def apply_plan(program: ProgramIR, plan: ChangePlan) -> tuple[str, ...]:
    if not plan.ready:
        raise ChangeBlocked(plan.obligation_graph.blocking)
    issues = _preflight(program, plan.edits)
    if issues:
        raise ChangeBlocked(
            make_obligation(
                plan.change.id,
                kind,
                message,
                files=files,
                affected_entities=(plan.target.id,),
            )
            for kind, message, files in issues
        )
    rendered = _render_edits(program, plan.edits)
    root = Path(program.root)
    originals: dict[str, bytes | None] = {
        relative: (root / relative).read_bytes() if (root / relative).exists() else None
        for relative in rendered
    }
    temporary: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        for relative, content in rendered.items():
            destination = root / relative
            if not destination.parent.is_dir():
                raise FileNotFoundError(destination.parent)
            mode = (
                stat.S_IMODE(destination.stat().st_mode)
                if destination.exists()
                else 0o644
            )
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.meldra-",
                dir=destination.parent,
                delete=False,
            ) as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.chmod(temp_path, mode)
            temporary[relative] = temp_path
        for relative in sorted(rendered):
            destination = root / relative
            os.replace(temporary[relative], destination)
            replaced.append(relative)
            temporary.pop(relative, None)
    except Exception:
        _restore_originals(root, originals, replaced)
        raise
    finally:
        for path in temporary.values():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return tuple(sorted(rendered))


def render_plan(program: ProgramIR, plan: ChangePlan) -> dict[str, str]:
    return _render_edits(program, plan.edits)


def _make_rename(target_id: str, new_name: str, goal: str) -> RenameSymbol:
    return RenameSymbol(
        id=_change_id("rename_symbol", target_id, new_name, goal),
        target_id=target_id,
        new_name=new_name,
        goal=goal,
    )


def _make_move(target_id: str, target_module: str, goal: str) -> MoveSymbol:
    return MoveSymbol(
        id=_change_id("move_symbol", target_id, target_module, goal),
        target_id=target_id,
        target_module=target_module,
        goal=goal,
    )


def _make_signature(
    target_id: str,
    new_signature: str,
    goal: str,
    argument_values: Mapping[str, str] | None,
) -> ChangeSignature:
    values = tuple(sorted((argument_values or {}).items()))
    payload = new_signature + "\0" + repr(values)
    return ChangeSignature(
        id=_change_id("change_signature", target_id, payload, goal),
        target_id=target_id,
        new_signature=new_signature,
        goal=goal,
        argument_values=values,
    )


def _change_id(operation: str, target_id: str, payload: str, goal: str) -> str:
    value = f"{operation}\0{target_id}\0{payload}\0{goal}"
    return "chg_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _unknown_entity(identifier: str) -> Entity:
    return Entity(
        id=identifier,
        kind="unknown",
        module="",
        qualname=identifier,
        name=identifier,
        file="",
        definition_span=Span(Position(1, 0), Position(1, 0)),
        revision_hash="",
        signature="",
        public=False,
    )


def _entity_collision(
    program: ProgramIR, module: str, name: str, excluded_id: str
) -> Entity | None:
    return next(
        (
            other
            for other in program.entities
            if other.id != excluded_id
            and other.module == module
            and "." not in other.qualname
            and other.name == name
        ),
        None,
    )


def _renamed_qualname(entity: Entity, new_name: str) -> str:
    if "." not in entity.qualname:
        return new_name
    parent, _separator, _old = entity.qualname.rpartition(".")
    return f"{parent}.{new_name}"


def _uncertainty_dag(
    change_id: str,
    target_id: str,
    references: Iterable[Reference],
    root_kind: str,
) -> tuple[Obligation, ...]:
    references = tuple(references)
    if not references:
        return ()
    root = make_obligation(
        change_id,
        root_kind,
        f"{len(references)} uncertain relationships affect the change",
        affected_entities=(target_id,),
        severity="warning",
        possible_resolutions=("resolve every dependent binding obligation",),
    )
    children = tuple(
        uncertain_reference_obligation(
            change_id,
            reference,
            target_id,
            depends_on=(root.id,),
        )
        for reference in references
    )
    return (root, *children)


def _capability_obligations(
    change: RenameSymbol | MoveSymbol | ChangeSignature,
    entity: Entity,
    edits: Iterable[SourceEdit],
    capability: EditCapability,
) -> list[Obligation]:
    edits = tuple(edits)
    obligations: list[Obligation] = []
    files = tuple(sorted({edit.file for edit in edits}))
    affected_entities = {
        identifier for edit in edits for identifier in edit.affected_entity_ids
    }
    if change.operation not in capability.operations:
        obligations.append(
            make_obligation(
                change.id,
                "OperationDenied",
                f"EditCapability does not grant {change.operation}",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if entity.id not in capability.target_ids:
        obligations.append(
            make_obligation(
                change.id,
                "TargetDenied",
                f"EditCapability does not grant edits to {entity.id}",
                files=(entity.file,),
                affected_entities=(entity.id,),
            )
        )
    if capability.allowed_files is not None:
        outside = tuple(sorted(set(files) - set(capability.allowed_files)))
        if outside:
            obligations.append(
                make_obligation(
                    change.id,
                    "FileScopeDenied",
                    "generated edits leave the allowed file scope",
                    files=outside,
                    affected_entities=affected_entities,
                )
            )
    if len(files) > capability.max_files:
        obligations.append(
            make_obligation(
                change.id,
                "FileBudgetExceeded",
                f"change touches {len(files)} files; budget is {capability.max_files}",
                files=files,
                affected_entities=affected_entities,
            )
        )
    if len(affected_entities) > capability.max_entities:
        obligations.append(
            make_obligation(
                change.id,
                "EntityBudgetExceeded",
                (
                    f"change reaches {len(affected_entities)} entities; "
                    f"budget is {capability.max_entities}"
                ),
                files=files,
                affected_entities=affected_entities,
            )
        )
    if len(edits) > capability.max_edits:
        obligations.append(
            make_obligation(
                change.id,
                "EditBudgetExceeded",
                f"change creates {len(edits)} edits; budget is {capability.max_edits}",
                files=files,
                affected_entities=affected_entities,
            )
        )
    if not capability.allow_related_entities:
        permitted = set(capability.target_ids) | set(capability.related_entity_ids)
        outside_entities = tuple(sorted(affected_entities - permitted))
        if outside_entities:
            obligations.append(
                make_obligation(
                    change.id,
                    "RelatedEntityScopeDenied",
                    "derived migration edits reach undelegated entities",
                    files=files,
                    affected_entities=outside_entities,
                )
            )
    for category in sorted({edit.category for edit in edits}):
        if category in capability.forbidden_categories:
            obligations.append(
                make_obligation(
                    change.id,
                    "ForbiddenChangeCategory",
                    f"EditCapability forbids category {category}",
                    files=files,
                    affected_entities=affected_entities,
                )
            )
    return obligations


def _common_evidence(
    program: ProgramIR,
    change_id: str,
    entity: Entity,
    edits: Iterable[SourceEdit],
    obligations: Iterable[Obligation],
    *,
    migrated_references: int,
    stable_alias_references: int,
) -> list[Evidence]:
    edits = tuple(edits)
    obligations = tuple(obligations)
    files = tuple(sorted({edit.file for edit in edits if not edit.allow_create}))
    capability_passed = not any(
        item.kind
        in {
            "OperationDenied",
            "TargetDenied",
            "FileScopeDenied",
            "FileBudgetExceeded",
            "EntityBudgetExceeded",
            "EditBudgetExceeded",
            "RelatedEntityScopeDenied",
            "ForbiddenChangeCategory",
        }
        for item in obligations
    )
    preflight_passed = not any(
        item.kind in {"SourceUnavailable", "UntrackedFile", "StaleWorld", "InvalidEdit", "SyntaxInvalid"}
        for item in obligations
    )
    return [
        create_evidence(
            program,
            change_id,
            "edit_scope",
            "StaticallyChecked" if capability_passed else "Unresolved",
            (
                "generated edits fit the supplied EditCapability"
                if capability_passed
                else "generated edits exceed the supplied EditCapability"
            ),
            details={
                "passed": capability_passed,
                "files": list(files),
                "edit_count": len(edits),
            },
            entity_ids=(entity.id,),
            files=files,
        ),
        create_evidence(
            program,
            change_id,
            "binding_migration",
            "StaticallyChecked",
            "all Exact and Derived workspace references selected by the operation are covered",
            details={
                "migrated_references": migrated_references,
                "stable_alias_references": stable_alias_references,
            },
            entity_ids=(entity.id,),
            reference_targets=(entity.id,),
        ),
        create_evidence(
            program,
            change_id,
            "syntax",
            "StaticallyChecked" if preflight_passed else "Unresolved",
            (
                "every changed Python module parses after proposed edits"
                if preflight_passed
                else "post-change syntax could not be established"
            ),
            details={"passed": preflight_passed, "files": list(files)},
            files=files,
        ),
        create_evidence(
            program,
            change_id,
            "external_consumers",
            "Unknown" if entity.public else "Assumed",
            "consumers outside the scanned workspace are not visible",
            details={"public_symbol": entity.public},
            entity_ids=(entity.id,),
        ),
    ]


def _preflight_obligations(
    program: ProgramIR, change_id: str, edits: Iterable[SourceEdit]
) -> list[Obligation]:
    return [
        make_obligation(change_id, kind, message, files=files)
        for kind, message, files in _preflight(program, edits)
    ]


def _preflight(
    program: ProgramIR, edits: Iterable[SourceEdit]
) -> list[tuple[str, str, tuple[str, ...]]]:
    edits = tuple(edits)
    issues: list[tuple[str, str, tuple[str, ...]]] = []
    root = Path(program.root)
    grouped: dict[str, list[SourceEdit]] = defaultdict(list)
    for edit in edits:
        grouped[edit.file].append(edit)
    for relative, file_edits in grouped.items():
        path = root / relative
        if not path.exists():
            if all(edit.allow_create for edit in file_edits):
                continue
            issues.append(("SourceUnavailable", f"cannot read {relative}", (relative,)))
            continue
        raw = path.read_bytes()
        try:
            expected_digest = program.file_digest(relative)
        except KeyError:
            if all(edit.allow_create for edit in file_edits):
                continue
            issues.append(("UntrackedFile", f"{relative} is absent from ProgramIR", (relative,)))
            continue
        if hashlib.sha256(raw).hexdigest() != expected_digest:
            issues.append(("StaleWorld", f"{relative} changed after semantic scan", (relative,)))
    if issues:
        return issues
    try:
        rendered = _render_edits(program, edits)
    except ValueError as exc:
        return [("InvalidEdit", str(exc), tuple(sorted(grouped)))]
    for relative, content in rendered.items():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                ast.parse(content.removeprefix("\ufeff"), filename=relative, type_comments=True)
        except SyntaxError as exc:
            issues.append(
                (
                    "SyntaxInvalid",
                    f"{relative}:{exc.lineno or 1}:{exc.offset or 1}: {exc.msg}",
                    (relative,),
                )
            )
    return issues


def _render_edits(program: ProgramIR, edits: Iterable[SourceEdit]) -> dict[str, str]:
    grouped: dict[str, list[SourceEdit]] = defaultdict(list)
    for edit in edits:
        grouped[edit.file].append(edit)
    rendered: dict[str, str] = {}
    root = Path(program.root)
    for relative, file_edits in grouped.items():
        path = root / relative
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        bom = "\ufeff" if original.startswith("\ufeff") else ""
        source = original.removeprefix("\ufeff")
        line_offsets = _line_offsets(source)
        replacements: list[tuple[int, int, SourceEdit]] = []
        for edit in file_edits:
            start = _offset(line_offsets, edit.span.start, len(source))
            end = _offset(line_offsets, edit.span.end, len(source))
            actual = source[start:end]
            if actual != edit.expected:
                raise ValueError(
                    f"{relative}:{edit.span.start.line}: expected {edit.expected!r}, found {actual!r}"
                )
            replacements.append((start, end, edit))
        replacements.sort(key=lambda item: (item[0], item[1]))
        for previous, current in zip(replacements, replacements[1:]):
            if current[0] < previous[1] or (
                current[0] == previous[0] and current[1] == previous[1]
            ):
                raise ValueError(
                    f"overlapping semantic edits in {relative} at offset {current[0]}"
                )
        result = source
        for start, end, edit in reversed(replacements):
            result = result[:start] + edit.replacement + result[end:]
        rendered[relative] = bom + result
    return rendered


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for index, character in enumerate(source):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _offset(offsets: list[int], position: Position, source_length: int) -> int:
    if position.line < 1 or position.line > len(offsets):
        raise ValueError(f"invalid line {position.line}")
    value = offsets[position.line - 1] + position.column
    if value > source_length:
        raise ValueError(f"position {position.line}:{position.column} leaves source bounds")
    return value


def _extract_span(source: str, span: Span) -> str:
    offsets = _line_offsets(source)
    return source[
        _offset(offsets, span.start, len(source)) : _offset(offsets, span.end, len(source))
    ]


def _deduplicate_edits(edits: Iterable[SourceEdit]) -> list[SourceEdit]:
    unique: dict[tuple[object, ...], SourceEdit] = {}
    for edit in edits:
        key = (edit.file, edit.span.start, edit.span.end)
        existing = unique.get(key)
        if existing is not None and existing != edit:
            raise ValueError(f"conflicting edits for {edit.file}:{edit.span.start.line}")
        unique[key] = edit
    return sorted(
        unique.values(),
        key=lambda item: (item.file, item.span.start.line, item.span.start.column),
    )


def _parse_signature(source: str) -> _ParsedSignature:
    if not source.startswith("(") or not source.endswith(")"):
        raise ValueError("signature must contain only a parenthesized parameter list")
    try:
        module = ast.parse(f"def __meldra{source}:\n    pass\n")
    except SyntaxError as exc:
        raise ValueError(f"invalid signature: {exc.msg}") from exc
    node = module.body[0]
    assert isinstance(node, ast.FunctionDef)
    parameters: list[_Parameter] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults_offset = len(positional) - len(node.args.defaults)
    positional_defaults: list[ast.expr | None] = [
        None
    ] * defaults_offset + list(node.args.defaults)
    for index, argument in enumerate(node.args.posonlyargs):
        default = positional_defaults[index]
        parameters.append(
            _Parameter(
                argument.arg,
                "positional_only",
                default is not None,
                _parameter_shape(argument, default),
            )
        )
    for index, argument in enumerate(
        node.args.args, len(node.args.posonlyargs)
    ):
        default = positional_defaults[index]
        parameters.append(
            _Parameter(
                argument.arg,
                "positional_or_keyword",
                default is not None,
                _parameter_shape(argument, default),
            )
        )
    if node.args.vararg:
        parameters.append(
            _Parameter(
                node.args.vararg.arg,
                "vararg",
                True,
                _parameter_shape(node.args.vararg, None),
            )
        )
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        parameters.append(
            _Parameter(
                argument.arg,
                "keyword_only",
                default is not None,
                _parameter_shape(argument, default),
            )
        )
    if node.args.kwarg:
        parameters.append(
            _Parameter(
                node.args.kwarg.arg,
                "kwarg",
                True,
                _parameter_shape(node.args.kwarg, None),
            )
        )
    return _ParsedSignature(source=source, parameters=tuple(parameters))


def _parameter_shape(argument: ast.arg, default: ast.expr | None) -> str:
    annotation = (
        ast.dump(argument.annotation, include_attributes=False)
        if argument.annotation is not None
        else ""
    )
    default_shape = (
        ast.dump(default, include_attributes=False)
        if default is not None
        else ""
    )
    return f"{annotation}\0{default_shape}"


def _signature_extension(
    old: _ParsedSignature, new: _ParsedSignature
) -> tuple[tuple[_Parameter, ...], bool]:
    old_parameters = old.parameters
    if len(new.parameters) < len(old_parameters):
        return (), False
    for before, after in zip(old_parameters, new.parameters):
        if (
            before.name,
            before.kind,
            before.semantic_shape,
        ) != (
            after.name,
            after.kind,
            after.semantic_shape,
        ):
            return (), False
    return new.parameters[len(old_parameters) :], True


def _call_argument_insertion(
    program: ProgramIR,
    edge: object,
    additions: tuple[tuple[str, str], ...],
) -> tuple[Position, str]:
    span = edge.span
    assert span is not None
    path = Path(program.root) / edge.file
    source = path.read_text(encoding="utf-8").removeprefix("\ufeff")
    offsets = _line_offsets(source)
    call_text = _extract_span(source, span)
    close = Position(span.end.line, span.end.column - 1)
    before_close = call_text[:-1].rstrip()
    rendered = ", ".join(f"{name}={value}" for name, value in additions)
    if "\n" in call_text:
        closing_line = source.splitlines()[close.line - 1]
        indent = closing_line[: len(closing_line) - len(closing_line.lstrip())] + "    "
        separator = "\n" if before_close.endswith(",") else ",\n"
        return close, separator + indent + rendered
    separator = "" if call_text.strip() == "()" else ", "
    return close, separator + rendered


def _external_import_statement(reference: Reference) -> str | None:
    metadata = reference.metadata
    module = str(metadata.get("imported_module", ""))
    if not module:
        return None
    source_name = metadata.get("source_name")
    local_name = metadata.get("local_name")
    if source_name:
        alias = f" as {local_name}" if local_name and local_name != source_name else ""
        return f"from {module} import {source_name}{alias}"
    module_alias = metadata.get("module_alias")
    alias = f" as {module_alias}" if module_alias and module_alias != module else ""
    return f"import {module}{alias}"


def _import_insertion_edit(
    program: ProgramIR,
    file: str,
    statement: str,
    *,
    reason: str,
    affected_entity_ids: tuple[str, ...],
) -> SourceEdit:
    source = (Path(program.root) / file).read_text(encoding="utf-8").removeprefix("\ufeff")
    position = _import_insertion_position(source, file)
    return SourceEdit(
        file=file,
        span=Span(position, position),
        expected="",
        replacement=statement,
        reason=reason,
        category="new_dependency",
        affected_entity_ids=affected_entity_ids,
    )


def _import_insertion_position(source: str, filename: str) -> Position:
    module = ast.parse(source, filename=filename)
    last_line = 0
    body = list(module.body)
    index = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        last_line = body[0].end_lineno or body[0].lineno
        index = 1
    while index < len(body) and isinstance(body[index], (ast.Import, ast.ImportFrom)):
        last_line = body[index].end_lineno or body[index].lineno
        index += 1
    if last_line == 0:
        return Position(1, 0)
    lines = source.splitlines(keepends=True)
    if last_line >= len(lines):
        return _end_position(source)
    return Position(last_line + 1, 0)


def _end_position(source: str) -> Position:
    lines = source.split("\n")
    return Position(len(lines), len(lines[-1]))


def _span_contains(outer: Span, inner: Span) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


class _ModuleBoundNames(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".", 1)[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name != "*"
        )

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)


def _module_bound_names(source: str, filename: str) -> frozenset[str]:
    visitor = _ModuleBoundNames()
    visitor.visit(ast.parse(source, filename=filename, type_comments=True))
    return frozenset(visitor.names)


def _has_unconditional_import_time_exit(source: str, filename: str) -> bool:
    module = ast.parse(source, filename=filename, type_comments=True)
    return any(
        isinstance(statement, (ast.Assert, ast.Raise))
        for statement in module.body
    )


def _is_semantic_protocol_name(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _reference_module(program: ProgramIR, reference: Reference) -> str | None:
    if reference.owner_id:
        try:
            return program.entity(reference.owner_id).module
        except KeyError:
            pass
    return next(
        (
            snapshot.module
            for snapshot in program.files
            if snapshot.path == reference.file
        ),
        None,
    )


def _module_has_dynamic_namespace(
    program: ProgramIR, module: str
) -> bool:
    return any(
        candidate.module == module
        and candidate.qualname in {"__getattr__", "__dir__"}
        for candidate in program.entities
    )


def _module_dependency_edges(
    program: ProgramIR,
) -> frozenset[tuple[str, str]]:
    cached = _MODULE_EDGE_CACHE.get(program.world_revision)
    if cached is not None:
        return cached
    entities = {entity.id: entity for entity in program.entities}
    modules_by_file = {
        snapshot.path: snapshot.module for snapshot in program.files
    }
    edges = set()
    for reference in program.references:
        if reference.target_id is None:
            continue
        target = entities.get(reference.target_id)
        if target is None:
            continue
        owner = entities.get(reference.owner_id) if reference.owner_id else None
        owner_module = (
            owner.module
            if owner is not None
            else modules_by_file.get(reference.file)
        )
        if owner_module and owner_module != target.module:
            edges.add((owner_module, target.module))
    result = frozenset(edges)
    if len(_MODULE_EDGE_CACHE) >= _MODULE_EDGE_CACHE_LIMIT:
        del _MODULE_EDGE_CACHE[next(iter(_MODULE_EDGE_CACHE))]
    _MODULE_EDGE_CACHE[program.world_revision] = result
    return result


def _would_create_cycle(
    program: ProgramIR,
    source_module: str,
    target_module: str,
    added_edges: set[tuple[str, str]],
) -> bool:
    edges = set(_module_dependency_edges(program))
    edges.update(added_edges)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for source, destination in edges:
        adjacency[source].add(destination)
    frontier = [target_module]
    visited: set[str] = set()
    while frontier:
        current = frontier.pop()
        if current == source_module:
            return True
        if current in visited:
            continue
        visited.add(current)
        frontier.extend(adjacency.get(current, ()))
    return False


def _restore_originals(
    root: Path,
    originals: Mapping[str, bytes | None],
    replaced: Iterable[str],
) -> None:
    for relative in replaced:
        path = root / relative
        original = originals[relative]
        if original is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            path.write_bytes(original)
