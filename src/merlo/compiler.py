from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from merlo.bounded_symbolic import BoundedSymbolicReport, verify_bounded
from merlo.frontend_model import (
    ConciseApplicationElaboration,
    ConciseApplicationError,
)
from merlo.concise_services import elaborate_concise_application
from merlo.modules import ModuleError, ModuleGraph
from merlo.native_c_backend import NativeBuildResult, compile_c_source
from merlo.project import Project
from merlo.obligation_ir import (
    ObligationProgram,
    build_obligation_ir,
    extend_obligations,
)
from merlo.range_analysis import RangeAnalysisResult, analyze_constant_ranges
from merlo.representation_c_backend import GeneratedC, emit_general_c
from merlo.representation_ir import RepresentationProgram, lower_structured_hir_to_rir
from merlo.representation_mir import (
    GeneralPerformanceMIR,
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import (
    StructuredHIRProgram,
    compile_canonical_hir,
)
from merlo.version import VERSIONS, CompilerVersions


@dataclass(frozen=True)
class StageArtifact:
    name: str
    contract: str
    version: int | str
    digest: str
    parent_digest: str | None
    content: str

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "contract": self.contract,
            "version": self.version,
            "digest": self.digest,
            "parent_digest": self.parent_digest,
        }
        if include_content:
            result["content"] = self.content
        return result


@dataclass(frozen=True)
class ProjectCompilation:
    entry_path: str
    versions: CompilerVersions
    elaborated: ConciseApplicationElaboration
    module_graph: ModuleGraph
    hir: StructuredHIRProgram
    obligations: ObligationProgram
    range_analysis: RangeAnalysisResult
    bounded_symbolic: BoundedSymbolicReport
    representation: RepresentationProgram
    mir: GeneralPerformanceMIR
    optimized_mir: GeneralPerformanceMIR
    generated: GeneratedC
    generated_c: str
    artifacts: Mapping[str, StageArtifact]
    native: NativeBuildResult | None = None
    @property
    def diagnostic_source_map(self) -> tuple[dict[str, Any], ...]:
        result = []
        for function in self.hir.functions:
            for node in function.walk():
                result.append(
                    {
                        "node_id": node.id,
                        "canonical": node.source.to_dict(),
                        "concise": node.source.to_dict(),
                    }
                )
        return tuple(result)

    @property
    def generated_c_sha256(self) -> str:
        return _digest(self.generated_c)


    @property
    def digest(self) -> str:
        return self.artifacts["c11"].digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_path": self.entry_path,
            "versions": self.versions.to_dict(),
            "digest": self.digest,
            "artifacts": {
                name: artifact.to_dict()
                for name, artifact in self.artifacts.items()
            },
            "obligations": {
                "digest": self.obligations.digest,
                "count": len(self.obligations.obligations),
                "unresolved": len(self.obligations.unresolved),
            },
            "range_analysis": {
                "digest": self.range_analysis.digest,
                "fact_count": len(self.range_analysis.facts),
                "unreachable_branch_count": len(
                    self.range_analysis.unreachable_branch_ids
                ),
            },
            "bounded_symbolic": {
                "digest": self.bounded_symbolic.digest,
                "result_count": len(self.bounded_symbolic.results),
                "proven_count": sum(
                    item.status.value == "proven"
                    for item in self.bounded_symbolic.results
                ),
            },
            "native": self.native.to_dict() if self.native is not None else None,
        }


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _artifact(
    name: str,
    contract: str,
    version: int | str,
    content: str,
    parent: StageArtifact | None,
) -> StageArtifact:
    return StageArtifact(
        name=name,
        contract=contract,
        version=version,
        digest=_digest(content),
        parent_digest=parent.digest if parent is not None else None,
        content=content,
    )


def _entry_path(path: str | Path) -> Path:
    candidate = Path(path).resolve()
    if candidate.is_file():
        return candidate
    if not candidate.is_dir():
        raise ConciseApplicationError(f"{candidate}: project or source does not exist")
    manifest = candidate / "merlo.toml"
    if manifest.exists():
        entry = candidate / "src" / "main.mlo"
    else:
        entry = candidate / "main.mlo"
    if not entry.is_file():
        raise ConciseApplicationError(f"{candidate}: missing Merlo entry source {entry}")
    return entry
def _validate_project_lock(path: str | Path) -> None:
    candidate = Path(path).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    manifest_root = candidate
    while manifest_root != manifest_root.parent and not (manifest_root / "merlo.toml").is_file():
        manifest_root = manifest_root.parent
    if (manifest_root / "merlo.toml").is_file():
        try:
            Project.load(manifest_root).lock()
        except ValueError as exc:
            raise ConciseApplicationError(f"{manifest_root}: {exc}") from exc






def compile_project(
    path: str | Path,
    *,
    emit_native: bool = False,
    release: bool = False,
    output: str | Path | None = None,
    require_interface_lock: bool = True,
) -> ProjectCompilation:
    _validate_project_lock(path)
    entry = _entry_path(path)

    try:
        module_graph = ModuleGraph.load(entry)
    except ModuleError as exc:
        raise ConciseApplicationError(str(exc)) from exc
    elaborated = elaborate_concise_application(
        entry,
        require_interface_lock=require_interface_lock,
        module_graph=module_graph,
    )
    try:
        hir = compile_canonical_hir(
            elaborated.canonical_program,
            entry_function="main",
        )
        range_analysis = analyze_constant_ranges(hir)
        obligations = extend_obligations(
            build_obligation_ir(hir),
            range_analysis.obligations,
        )
        bounded_symbolic = verify_bounded(hir, obligations)
        representation = lower_structured_hir_to_rir(hir)
        mir = lower_rir_to_performance_mir(hir, representation)
        optimized = optimize_general_mir(mir)
        generated = emit_general_c(hir, representation, optimized)
        generated_c = generated.source
    except (TypeError, ValueError) as exc:
        raise ConciseApplicationError(
            f"{entry}: production lowering failed: {exc}"
        ) from exc

    module_artifact = _artifact(
        "modules",
        "merlo.module-graph.v1",
        1,
        module_graph.to_json(),
        None,
    )
    concise = _artifact(
        "concise",
        "merlo.concise-source",
        VERSIONS.frontend,
        "\0".join(
            (
                elaborated.source_sha256,
                elaborated.concise_semantic_digest,
            )
        ),
        module_artifact,
    )
    canonical = _artifact(
        "canonical",
        "merlo.canonical-typed",
        VERSIONS.canonical,
        elaborated.canonical_source,
        concise,
    )
    hir_artifact = _artifact(
        "hir",
        hir.contract,
        hir.schema_version,
        hir.to_json(),
        canonical,
    )
    range_artifact = _artifact(
        "ranges",
        range_analysis.contract,
        range_analysis.schema_version,
        range_analysis.to_json(),
        hir_artifact,
    )
    obligation_artifact = _artifact(
        "obligations",
        obligations.contract,
        obligations.schema_version,
        obligations.to_json(),
        hir_artifact,
    )
    symbolic_artifact = _artifact(
        "bounded-symbolic",
        bounded_symbolic.contract,
        bounded_symbolic.schema_version,
        bounded_symbolic.to_json(),
        obligation_artifact,
    )
    rir_artifact = _artifact(
        "rir",
        representation.contract,
        representation.schema_version,
        representation.to_json(),
        hir_artifact,
    )
    mir_artifact = _artifact(
        "mir",
        mir.contract,
        mir.schema_version,
        mir.to_json(),
        rir_artifact,
    )
    optimized_artifact = _artifact(
        "optimized_mir",
        optimized.contract,
        optimized.schema_version,
        optimized.to_json(),
        mir_artifact,
    )
    c_artifact = _artifact(
        "c11",
        "merlo.c11.runtime-abi",
        VERSIONS.runtime_abi,
        generated_c,
        optimized_artifact,
    )
    artifacts = {
        artifact.name: artifact
        for artifact in (
            module_artifact,
            concise,
            canonical,
            hir_artifact,
            obligation_artifact,
            range_artifact,
            symbolic_artifact,
            rir_artifact,
            mir_artifact,
            optimized_artifact,
            c_artifact,
        )
    }

    native: NativeBuildResult | None = None
    if emit_native:
        holes = tuple(
            node
            for function in hir.functions
            for node in function.walk()
            if node.kind == "TypedHole"
        )
        if holes:
            identifiers = ", ".join(
                str(node.attribute_map.get("hole_id"))
                for node in holes
            )
            raise ConciseApplicationError(
                f"{entry}: TypedHoleNotExecutable: "
                f"{identifiers}"
            )
    if emit_native:
        destination = (
            Path(output).resolve()
            if output is not None
            else Path(".merlo/build").resolve() / c_artifact.digest[:16] / "app"
        )
        compiler = shutil.which("clang") or shutil.which("gcc")
        native = compile_c_source(
            generated_c,
            output_dir=destination.parent,
            stem=destination.name,
            compiler=compiler,
        )
        if native.status != "MEASURED" or native.binary_path is None:
            raise ConciseApplicationError(
                f"{entry}: native build failed: {native.stderr}"
            )

    return ProjectCompilation(
        entry_path=str(entry),
        versions=VERSIONS,
        elaborated=elaborated,
        module_graph=module_graph,
        hir=hir,
        obligations=obligations,
        range_analysis=range_analysis,
        bounded_symbolic=bounded_symbolic,
        representation=representation,
        mir=mir,
        optimized_mir=optimized,
        generated=generated,
        generated_c=generated_c,
        artifacts=artifacts,
        native=native,
    )


__all__ = [
    "CompilerVersions",
    "ProjectCompilation",
    "StageArtifact",
    "VERSIONS",
    "compile_project",
]
