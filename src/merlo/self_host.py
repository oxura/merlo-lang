from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from merlo.compiler import compile_project
from merlo.native_c_backend import compile_c_source
from merlo.project import Project, resolve_dependencies


class SelfHostStatus(str, Enum):
    OBSERVED = "OBSERVED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class SelfHostBlocker:
    code: str
    message: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class SelfHostStage:
    number: int
    executable: str
    compiler_digest: str
    source_digest: str
    artifact_digest: str
    semantic_digest: str
    command: tuple[str, ...] = ()
    c_source_digest: str = ""
    c_source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "executable": self.executable,
            "compiler_digest": self.compiler_digest,
            "source_digest": self.source_digest,
            "artifact_digest": self.artifact_digest,
            "semantic_digest": self.semantic_digest,
            "command": list(self.command),
            "c_source_digest": self.c_source_digest,
            "c_source_path": self.c_source_path,
        }


@dataclass(frozen=True)
class SelfHostReport:
    status: SelfHostStatus
    compiler_source_digest: str
    selfhost_source_digest: str
    config_digest: str
    environment_digest: str
    toolchain_digests: tuple[tuple[str, str], ...]
    parsed_sources: tuple[str, ...]
    blockers: tuple[SelfHostBlocker, ...]
    stages: tuple[SelfHostStage, ...] = ()
    semantic_convergence: str = "UNAVAILABLE"
    byte_convergence: str = "UNAVAILABLE"
    canonical_bundle: str = ""
    artifact_root: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "compiler_source_digest": self.compiler_source_digest,
            "selfhost_source_digest": self.selfhost_source_digest,
            "config_digest": self.config_digest,
            "environment_digest": self.environment_digest,
            "toolchain_digests": dict(self.toolchain_digests),
            "parsed_sources": list(self.parsed_sources),
            "blockers": [item.to_dict() for item in self.blockers],
            "stages": [item.to_dict() for item in self.stages],
            "canonical_bundle": self.canonical_bundle,
            "artifact_root": self.artifact_root,
            "convergence": {"semantic": self.semantic_convergence, "bytes": self.byte_convergence},
            "observations": {
                "compiler_source_readable": self.compiler_source_observed,
                "selfhost_source_readable": self.selfhost_source_observed,
                "selfhost_subset_parseable": self.selfhost_subset_parseable,
                "executable_stage_emission": "OBSERVED" if self.executable_stages_observed else "UNAVAILABLE",
            },
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def evidence_digest(self) -> str:
        return _sha256_bytes(self.canonical_json().encode("utf-8"))

    @property
    def executable_stages_observed(self) -> bool:
        return self.status is SelfHostStatus.OBSERVED and len(self.stages) == 3

    @property
    def compiler_source_observed(self) -> bool:
        return self.compiler_source_digest != _sha256_bytes(b"")

    @property
    def selfhost_source_observed(self) -> bool:
        return self.selfhost_source_digest != _sha256_bytes(b"")

    @property
    def selfhost_subset_parseable(self) -> bool:
        return bool(self.parsed_sources)


class SelfHostBlocked(RuntimeError):
    def __init__(self, report: SelfHostReport) -> None:
        self.report = report
        super().__init__("self-hosting is blocked")


class SelfHostStageError(RuntimeError):
    """A concrete failure in one observed bootstrap stage."""

    def __init__(self, stage: str, code: str, detail: str) -> None:
        self.stage = stage
        self.code = code
        self.detail = detail
        super().__init__(f"{stage}:{code}: {detail}")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_files(paths: Iterable[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((item.resolve() for item in paths), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root.resolve()).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _source_files(root: Path) -> tuple[Path, ...]:
    package = root / "src" / "merlo"
    return tuple(path for path in package.rglob("*.py") if path.is_file())


def _selfhost_files(root: Path) -> tuple[Path, ...]:
    source = root / "selfhost" / "src"
    if not source.is_dir():
        return ()
    return tuple(path for path in source.rglob("*.mlo") if path.is_file())


def _config_files(root: Path) -> tuple[Path, ...]:
    return tuple(path for path in (root / "pyproject.toml", root / "selfhost" / "merlo.toml", root / "selfhost" / "merlo.lock") if path.is_file())


def _environment_digest(toolchains: tuple[tuple[str, str], ...]) -> str:
    payload = {"python": sys.version, "implementation": platform.python_implementation(), "machine": platform.machine(), "system": platform.system(), "release": platform.release(), "toolchains": dict(toolchains)}
    return _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _toolchain_digests() -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for name in ("clang", "gcc", "cc"):
        executable = shutil.which(name)
        if executable is None:
            result.append((name, "UNAVAILABLE"))
            continue
        try:
            result.append((name, _sha256_bytes(Path(executable).resolve().read_bytes())))
        except OSError:
            result.append((name, "UNREADABLE"))
    return tuple(result)


def _module_order(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    by_name = {path.stem: path for path in paths}
    dependencies: dict[str, set[str]] = {}
    for name, path in by_name.items():
        text = path.read_text(encoding="utf-8")
        dependencies[name] = {match.group(1).split(".")[-1] for match in re.finditer(r"^use\s+([A-Za-z_][A-Za-z0-9_.]*)", text, re.M) if match.group(1).split(".")[-1] in by_name and match.group(1).split(".")[-1] != name}
    ordered: list[Path] = []
    pending = set(by_name)
    while pending:
        ready = sorted(name for name in pending if not (dependencies[name] & pending))
        if not ready:
            ready = [sorted(pending)[0]]
        for name in ready:
            ordered.append(by_name[name])
            pending.remove(name)
    return tuple(ordered)


def _canonical_bundle(root: Path, destination: Path) -> tuple[Path, tuple[str, ...]]:
    files = _selfhost_files(root)
    required = {"syntax", "lexer", "parser", "validator", "emitter", "main"}
    missing = sorted(required - {path.stem for path in files})
    if missing:
        raise SelfHostStageError("bundle", "MissingModule", ", ".join(missing))
    chunks = ["module selfhost\n\n"]
    parsed: list[str] = []
    for path in _module_order(files):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = text.splitlines()
        body: list[str] = []
        for line in lines:
            if re.match(r"^module\s+", line):
                continue
            use = re.match(r"^(\s*)use\s+([A-Za-z_][A-Za-z0-9_.]*)\s*$", line)
            if use and use.group(2).split(".")[-1] in {item.stem for item in files}:
                continue
            line = re.sub(r"\b(?:syntax|lexer|parser|validator|emitter)\.", "", line)
            body.append(line)
        chunks.append("\n".join(body).strip() + "\n\n")
        parsed.append(str(path.relative_to(root)))
    content = "".join(chunks).encode("utf-8")
    digest = _sha256_bytes(content)
    path = destination / f"compiler-{digest}.mlo"
    path.write_bytes(content)
    return path, tuple(parsed)


def _semantic_digest(source: bytes) -> str:
    text = source.decode("utf-8", errors="strict")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return _sha256_bytes(re.sub(r"\s+", "", text).encode("utf-8"))
def _stage_command(executable: Path) -> tuple[str, ...]:
    limiter = shutil.which("prlimit") if sys.platform.startswith("linux") else None
    if limiter is None:
        return (str(executable),)
    return (
        limiter,
        "--as=1073741824",
        "--cpu=60",
        "--",
        str(executable),
    )




def _run_stage(executable: Path, input_path: Path, output_path: Path, label: str) -> tuple[bytes, tuple[str, ...]]:
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SelfHostStageError(label, "ExecutableMissing", str(executable))
    try:
        input_data = input_path.read_bytes()
    except OSError as exc:
        raise SelfHostStageError(label, "InputUnreadable", str(input_path)) from exc
    command = _stage_command(executable)
    try:
        completed = subprocess.run(
            command,
            input=input_data,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except OSError as exc:
        raise SelfHostStageError(label, "ExecutionFailed", str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise SelfHostStageError(label, "ExecutionTimeout", str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or b"nonzero exit")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise SelfHostStageError(label, "ExecutionFailed", str(detail).strip())
    content = completed.stdout
    if not content:
        raise SelfHostStageError(label, "OutputEmpty", str(output_path))
    try:
        output_path.write_bytes(content)
    except OSError as exc:
        raise SelfHostStageError(label, "OutputWriteFailed", str(output_path)) from exc
    return content, command


def _execute(root: Path) -> SelfHostReport:
    root = root.resolve()
    toolchains = _toolchain_digests()
    files = _selfhost_files(root)
    if not files:
        raise SelfHostStageError("bundle", "SourceUnavailable", str(root / "selfhost" / "src"))
    artifact_root = root / ".merlo" / "self-host"
    artifact_root.mkdir(parents=True, exist_ok=True)
    bundle, parsed = _canonical_bundle(root, artifact_root)
    digest = _sha256_bytes(bundle.read_bytes())
    run_root = artifact_root / digest
    run_root.mkdir(parents=True, exist_ok=True)
    canonical = run_root / "compiler.mlo"
    if canonical != bundle:
        canonical.write_bytes(bundle.read_bytes())
    stage0 = run_root / "stage0"
    (stage0 / "src").mkdir(parents=True, exist_ok=True)
    (stage0 / "src" / "main.mlo").write_bytes(canonical.read_bytes())
    (stage0 / "merlo.toml").write_text('manifest = 1\n\n[project]\nname = "merlo-self-host-stage0"\nversion = "0.1.0"\nedition = "alpha.1"\n', encoding="utf-8")
    resolve_dependencies(Project.load(stage0), write=True)
    stage1_path = run_root / "stage1"
    try:
        compiled = compile_project(stage0, emit_native=True, output=stage1_path, require_interface_lock=False)
    except Exception as exc:
        raise SelfHostStageError("stage0", "CompileFailed", str(exc)) from exc
    if compiled.native is None or compiled.native.binary_path is None:
        raise SelfHostStageError("stage0", "ExecutableMissing", str(stage1_path))
    stage1_executable = Path(compiled.native.binary_path)
    c1, _runtime_command1 = _run_stage(stage1_executable, canonical, run_root / "stage1.c", "stage1")
    try:
        stage1_source = c1.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SelfHostStageError("stage1", "OutputInvalidUtf8", str(exc)) from exc
    stage2_result = compile_c_source(stage1_source, output_dir=run_root, stem="stage2", compiler=compiled.native.compiler)
    if stage2_result.status != "MEASURED" or not stage2_result.binary_path:
        raise SelfHostStageError("stage2", "CompileFailed", stage2_result.stderr)
    stage2_executable = Path(stage2_result.binary_path)
    c2, _runtime_command2 = _run_stage(stage2_executable, canonical, run_root / "stage2.c", "stage2")
    try:
        stage2_source = c2.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SelfHostStageError("stage2", "OutputInvalidUtf8", str(exc)) from exc
    stage3_result = compile_c_source(stage2_source, output_dir=run_root, stem="stage3", compiler=compiled.native.compiler)
    if stage3_result.status != "MEASURED" or not stage3_result.binary_path:
        raise SelfHostStageError("stage3", "CompileFailed", stage3_result.stderr)
    stage3_executable = Path(stage3_result.binary_path)
    c3, _runtime_command3 = _run_stage(stage3_executable, canonical, run_root / "stage3.c", "stage3")
    semantic = tuple(_semantic_digest(item) for item in (c1, c2, c3))
    if len(set(semantic)) != 1:
        raise SelfHostStageError("convergence", "NonConvergent", f"semantic hashes {semantic[0]} != {semantic[1]} != {semantic[2]}")
    stages: list[SelfHostStage] = []
    for number, executable, content, command in ((1, stage1_executable, c1, tuple(compiled.native.command)), (2, stage2_executable, c2, tuple(stage2_result.command)), (3, stage3_executable, c3, tuple(stage3_result.command))):
        raw = executable.read_bytes()
        stages.append(SelfHostStage(number, str(executable), _sha256_bytes(raw), digest, _sha256_bytes(raw), _semantic_digest(content), command, _sha256_bytes(content), str(run_root / f"stage{number}.c")))
    return SelfHostReport(SelfHostStatus.OBSERVED, _digest_files(_source_files(root), root), _digest_files(files, root), _digest_files(_config_files(root), root), _environment_digest(toolchains), toolchains, parsed, (), tuple(stages), "OBSERVED", "OBSERVED" if c1 == c2 == c3 else "DIVERGED", str(canonical), str(run_root))


def assess_self_host(root: str | Path | None = None) -> SelfHostReport:
    return _execute(Path(root or Path(__file__).resolve().parents[2]))


def run_self_host(root: str | Path | None = None) -> SelfHostReport:
    return assess_self_host(root)


__all__ = ["SelfHostBlocked", "SelfHostBlocker", "SelfHostReport", "SelfHostStage", "SelfHostStageError", "SelfHostStatus", "assess_self_host", "run_self_host"]
