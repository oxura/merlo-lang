from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from .alpha_protocol import AlphaProtocol
from .public_benchmark import PublicBenchmarkOutputError, run_public_benchmark
from .bench import run_stage02_bench
from .compiler import compile_project
from .core_bench import run_core_benchmark
from .formatter import format_application_source
from .experiment import run_hypothesis_experiment
from .frontend_bench import run_frontend_benchmark
from .frontend_evaluator import EvaluationError, ReferenceEvaluator
from .frontend_semantics import check_frontend, compile_frontend
from .project import Project
from .semantic_world import SemanticWorld
from .test_runner import run_project_tests

EXIT_OK = 0
EXIT_DIAGNOSTIC = 1
EXIT_USAGE = 2


_PRODUCTION_COMMANDS = (
    "new", "benchmark", "check", "build", "run", "test", "fmt", "expand", "explain",
    "doc", "map", "inspect", "refs", "callers", "callees", "deps", "impact",
    "why", "context", "refactor", "add",
)
_HISTORICAL_COMMANDS = (
    "scan", "ir", "identities", "rename", "move", "signature", "obligations",
    "obligation", "evidence", "bench", "core-bench", "frontend-check",
    "frontend-ir", "frontend-run", "frontend-bench", "experiment", "check",
    "expand", "explain", "build", "run", "impact", "context",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="merlo", description="Merlo alpha project tooling")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="create a project")
    new.add_argument("path", nargs="?", default=".")
    new.add_argument("--name")
    new.add_argument("--version", default="0.1.0")
    _json_flag(new)
    benchmark = commands.add_parser("benchmark", help="run the locked public native benchmark")
    benchmark.add_argument("--output", required=True, metavar="PATH")

    for name in ("check", "build", "run", "test", "fmt", "expand", "explain", "doc", "map"):
        command = commands.add_parser(name, help=f"{name} a Merlo project")
        command.add_argument("path", nargs="?", default=".")
        _json_flag(command)
        if name == "build":
            command.add_argument("-o", "--output")
            command.add_argument("--release", action="store_true")
        elif name == "run":
            command.add_argument("program_arguments", nargs=argparse.REMAINDER)
        elif name == "test":
            command.add_argument("--fail-fast", action="store_true")
        elif name == "fmt":
            command.add_argument("--check", action="store_true")
            command.add_argument("--stdout", action="store_true")
        elif name == "doc":
            command.add_argument("-o", "--output")
        elif name == "map":
            command.add_argument("--projection", choices=("text", "dot", "json"), default="text")

    for name in ("inspect", "refs", "callers", "callees", "deps", "impact", "context"):
        command = commands.add_parser(name, help=f"query semantic world ({name})")
        command.add_argument("target")
        command.add_argument("path", nargs="?", default=".")
        _json_flag(command)
        if name == "callers":
            command.add_argument("--transitive", action="store_true")
        if name == "context":
            command.add_argument("--goal", default="")

    why = commands.add_parser("why", help="explain a diagnostic code")
    why.add_argument("diagnostic")
    why.add_argument("path", nargs="?", default=".")
    _json_flag(why)

    add = commands.add_parser("add", help="add a package dependency")
    add.add_argument("--path", nargs=2, metavar=("NAME", "PATH"))
    add.add_argument("--git", nargs=2, metavar=("NAME", "URL"))
    add.add_argument("--rev")
    add.add_argument("--version")
    add.add_argument("project", nargs="?", default=".")
    _json_flag(add)

    refactor = commands.add_parser("refactor", help="preview or apply a semantic refactor")
    refactor_commands = refactor.add_subparsers(dest="operation", required=True)
    rename = refactor_commands.add_parser("rename")
    rename.add_argument("target")
    rename.add_argument("new_name")
    move = refactor_commands.add_parser("move")
    move.add_argument("target")
    move.add_argument("module")
    signature = refactor_commands.add_parser("signature")
    signature.add_argument("target")
    signature.add_argument("signature")
    for command in (rename, move, signature):
        command.add_argument("path", nargs="?", default=".")
        command.add_argument("--apply", action="store_true")
        _json_flag(command)

    historical = commands.add_parser("historical", help="historical research commands")
    historical.add_argument("historical_command", choices=_HISTORICAL_COMMANDS)
    historical.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")


def _project(path: str | Path) -> Project:
    return Project.load(path)


def _input_path(path: str | Path) -> Path:
    candidate = Path(path).resolve()
    if candidate.is_file() and candidate.suffix != ".mlo":
        raise ValueError(f"SourceExtensionError: expected .mlo source: {candidate}")
    return candidate


def _entry_path(project: Project, path: str | Path) -> Path:
    candidate = Path(path).resolve()
    if candidate.is_file():
        return candidate
    return project.source_dir / "main.mlo"


def _world(project: Project) -> SemanticWorld:
    compilation = compile_project(project.root, require_interface_lock=False)
    world = SemanticWorld.build(
        compilation,
        state_path=project.root / ".merlo" / "world.json",
        lockfile=project.lock_path,
        require_interface_lock=False,
    )
    world.save()
    return world


def _source(project: Project, path: str | Path) -> tuple[Path, str]:
    entry = _entry_path(project, path)
    if not entry.is_file():
        raise ValueError(f"SourceNotFound: {entry}")
    return entry, entry.read_text(encoding="utf-8")


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _emit(payload: Any, as_json: bool, *, text: str | None = None) -> None:
    if as_json:
        _json_print(payload)
    elif text is not None:
        print(text, end="" if text.endswith("\n") else "\n")
    elif isinstance(payload, str):
        print(payload, end="" if payload.endswith("\n") else "\n")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _error_payload(exc: Exception) -> dict[str, Any]:
    code = str(getattr(exc, "code", "")) or type(exc).__name__
    return {"ok": False, "diagnostics": [{"code": code, "message": str(exc)}]}


def _main_production(args: argparse.Namespace) -> int:
    name = args.command
    if name == "benchmark":
        try:
            report = run_public_benchmark(Path.cwd(), output=args.output)
        except PublicBenchmarkOutputError as exc:
            print(f"merlo: benchmark output error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        return EXIT_OK if report.get("status") == "MEASURED" and report.get("passed") is True else EXIT_DIAGNOSTIC

    if name == "new":
        project = Project.create(args.path, name=args.name, version=args.version)
        payload = {"ok": True, "project": str(project.root), "manifest": str(project.manifest_path), "lockfile": str(project.lock_path)}
        _emit(payload, args.json, text=f"created {project.root}\n")
        return EXIT_OK
    if name == "add":
        project = _project(args.project)
        if args.path is not None and args.git is not None:
            raise ValueError("DependencySpecificationError: choose --path or --git")
        if args.path is None and args.git is None:
            raise ValueError("DependencySpecificationError: one of --path or --git is required")
        if args.path is not None:
            dep_name, dep_path = args.path
            dependency = project.add_path(dep_name, dep_path, version=args.version)
        else:
            dep_name, git = args.git
            if not args.rev:
                raise ValueError("DependencySpecificationError: --git requires --rev")
            dependency = project.add_git(dep_name, git, args.rev, version=args.version)
        payload = {"ok": True, "project": str(project.root), "dependency": dependency.to_dict()}
        _emit(payload, args.json, text=f"added {dependency.name}\n")
        return EXIT_OK
    if name == "fmt":
        project = _project(args.path)
        entry, source = _source(project, args.path)
        formatted = format_application_source(source, path=str(entry))
        changed = formatted != source
        if args.check:
            payload = {"ok": not changed, "path": str(entry), "changed": changed}
            _emit(payload, args.json, text=("formatted\n" if changed else "ok\n"))
            return EXIT_OK if not changed else EXIT_DIAGNOSTIC
        if args.stdout:
            _emit({"ok": True, "path": str(entry), "changed": changed, "source": formatted}, args.json, text=formatted)
        else:
            entry.write_text(formatted, encoding="utf-8")
            _emit({"ok": True, "path": str(entry), "changed": changed}, args.json, text=f"formatted {entry}\n")
        return EXIT_OK
    if name in {"expand", "explain"}:
        candidate = _input_path(args.path)
        compilation = compile_project(candidate, require_interface_lock=False)
        entry = Path(compilation.entry_path)
        if name == "expand":
            expanded = compilation.elaborated.canonical_source
            _emit({"ok": True, "path": str(entry), "source": expanded}, args.json, text=expanded)
            return EXIT_OK
        explanation = (
            f"path: {entry}\nsemantic digest: "
            f"{compilation.elaborated.concise_semantic_digest}\n"
            "semantic AST preserved: yes\n"
        )
        _emit({"ok": True, "path": str(entry), "explanation": explanation}, args.json, text=explanation)
        return EXIT_OK
    if name == "check":
        candidate = _input_path(args.path)
        project = Project.discover(candidate)
        compilation = compile_project(candidate, require_interface_lock=False)
        world = SemanticWorld.build(
            compilation,
            state_path=project.root / ".merlo" / "world.json" if project else None,
            lockfile=project.lock_path if project else None,
            require_interface_lock=False,
        )
        world.save()
        root = project.root if project else Path(compilation.entry_path).parent
        payload = {"ok": True, "project": str(root), "entry_path": compilation.entry_path, "compiler": compilation.to_dict(), "world_digest": world.digest, "diagnostics": []}
        _emit(payload, args.json, text=f"ok {root}\n")
        return EXIT_OK
    if name == "build":
        candidate = _input_path(args.path)
        project = Project.discover(candidate)
        root = project.root if project else candidate.parent if candidate.is_file() else candidate
        output = args.output or str(root / ".merlo" / "build" / "app")
        compilation = compile_project(candidate, emit_native=True, release=args.release, output=output, require_interface_lock=False)
        if compilation.native is None:
            raise RuntimeError("NativeBuildMissing: compiler did not produce an executable")
        payload = {"ok": True, "project": str(root), "entry_path": compilation.entry_path, "digest": compilation.digest, "binary": compilation.native.to_dict()}
        _emit(payload, args.json, text=f"{compilation.native.binary_path}\n")
        return EXIT_OK
    if name == "run":
        candidate = _input_path(args.path)
        project = Project.discover(candidate)
        compilation = compile_project(candidate, emit_native=True, require_interface_lock=False)
        if compilation.native is None or compilation.native.binary_path is None:
            raise RuntimeError("NativeBuildMissing: compiler did not produce an executable")
        program_arguments = list(args.program_arguments)
        if "--json" in program_arguments:
            program_arguments.remove("--json")
            args.json = True
        if program_arguments[:1] == ["--"]:
            program_arguments.pop(0)
        entry = Path(compilation.entry_path)
        main_task = next((task for task in compilation.elaborated.tasks if task.name == "main"), None)
        if not program_arguments and main_task is not None and main_task.parameters == (("path", "Path"),):
            program_arguments.append(str(project.root if project else entry.parent))
        completed = subprocess.run([compilation.native.binary_path, *program_arguments], check=False)
        if args.json:
            _json_print({"ok": completed.returncode == 0, "returncode": completed.returncode, "digest": compilation.digest})
        return completed.returncode
    if name == "test":
        report = run_project_tests(_project(args.path))
        _emit(report.to_dict(), args.json, text=f"{report.passed} passed, {report.failed} failed\n")
        return EXIT_OK if report.failed == 0 else EXIT_DIAGNOSTIC
    if name == "doc":
        project = _project(args.path)
        world = _world(project)
        documentation = generate_documentation(world)
        destination = Path(args.output).resolve() if args.output else project.root / "docs" / "API.md"
        write_documentation(world, destination)
        payload = {"ok": True, "path": str(destination), "world_digest": world.digest, "modules": documentation.modules, "public_symbols": documentation.public_symbols}
        _emit(payload, args.json, text=f"{destination}\n")
        return EXIT_OK
    if name == "map":
        world = _world(_project(args.path))
        value = world.map(args.projection)
        if args.json and isinstance(value, str) and args.projection != "json":
            _emit({"ok": True, "projection": args.projection, "map": value}, True)
        else:
            _emit(value if args.projection == "json" else {"ok": True, "projection": args.projection, "map": value}, args.json, text=value if isinstance(value, str) else None)
        return EXIT_OK
    if name in {"inspect", "refs", "callers", "callees", "deps", "impact", "context", "why"}:
        project = _project(args.path)
        protocol = AlphaProtocol(_world(project))
        if name == "inspect":
            value = protocol.inspect(args.target)
        elif name == "refs":
            value = protocol.references(args.target)
        elif name == "callers":
            value = protocol.callers(args.target, transitive=args.transitive)
        elif name == "callees":
            value = protocol.callees(args.target)
        elif name == "deps":
            value = protocol.dependencies(args.target)
        elif name == "impact":
            value = protocol.impact(args.target)
        elif name == "context":
            value = protocol.compile_context(args.target, goal=args.goal).to_dict()
        else:
            value = protocol.diagnostics_explain(args.diagnostic)
        if isinstance(value, tuple):
            value = list(value)
        _emit(value, args.json, text=None)
        return EXIT_OK
    if name == "refactor":
        project = _project(args.path)
        protocol = AlphaProtocol(_world(project))
        if args.operation == "rename":
            value = protocol.call("refactor.rename", {"target": args.target, "new_name": args.new_name, "mode": "apply" if args.apply else "preview"})
        elif args.operation == "move":
            value = protocol.call("refactor.move", {"target": args.target, "module": args.module})
        else:
            value = protocol.call("refactor.signature", {"target": args.target, "signature": args.signature})
        _emit(value, args.json, text=None)
        return EXIT_OK if not isinstance(value, dict) or "diagnostic" not in value else EXIT_DIAGNOSTIC
    raise ValueError(f"UnknownCommand: {name}")


def _historical_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"merlo historical {command}")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--json", action="store_true")
    if command == "frontend-run":
        parser.add_argument("symbol")
    elif command in {"inspect", "impact", "context", "obligation", "evidence"}:
        parser.add_argument("entity")
    elif command in {"rename", "move"}:
        parser.add_argument("entity")
        parser.add_argument("value")
    elif command == "signature":
        parser.add_argument("entity")
        parser.add_argument("value")
    elif command == "obligations":
        parser.add_argument("--change")
        parser.add_argument("--status")
    return parser


def _run_historical(args: argparse.Namespace) -> int:
    command_args = _historical_parser(args.historical_command).parse_args(args.arguments)
    command = args.historical_command
    if command == "bench":
        _json_print(run_stage02_bench().to_dict()); return EXIT_OK
    if command == "core-bench":
        _json_print(run_core_benchmark().to_dict()); return EXIT_OK
    if command == "frontend-bench":
        _json_print(run_frontend_benchmark().to_dict()); return EXIT_OK
    if command == "experiment":
        _json_print(run_hypothesis_experiment().to_dict()); return EXIT_OK
    if command in {"frontend-check", "frontend-ir", "frontend-run"}:
        root = Path(command_args.root).resolve()
        sources = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.mlo"))} if root.is_dir() else {root.name: root.read_text(encoding="utf-8")}
        if command == "frontend-check":
            result = check_frontend(sources)
            _json_print({"ok": result.ok, "diagnostics": [item.to_dict() for item in result.diagnostics]})
            return EXIT_OK if result.ok else EXIT_DIAGNOSTIC
        compilation = compile_frontend(sources)
        if command == "frontend-ir":
            _json_print(compilation.core_program.to_dict())
            return EXIT_OK
        evaluator = ReferenceEvaluator(compilation)
        result = evaluator.evaluate(command_args.symbol, {})
        _json_print(result.to_dict()); return EXIT_OK
    # Historical world commands intentionally retain their original provider.
    from .world import SoftwareWorld
    from .protocol import MerloProtocol
    world = SoftwareWorld.scan(command_args.root, None)
    protocol = MerloProtocol(world)
    if command == "scan":
        world.save()
        _emit(world.summary(), command_args.json)
        return EXIT_OK
    if command == "ir":
        world.save()
        _json_print(world.program.to_dict())
        return EXIT_OK
    if command == "identities":
        world.save()
        _json_print({"world_revision": world.program.world_revision, "relations": [item.to_dict() for item in world.program.identity_relations]})
        return EXIT_OK
    if command in {"inspect", "impact"}:
        value = protocol.inspect(command_args.entity) if command == "inspect" else protocol.impact(command_args.entity)
        world.save()
        _json_print(value)
        return EXIT_OK
    if command == "obligations":
        _json_print({"obligations": list(protocol.obligations(change_id=command_args.change)), "count": len(protocol.obligations(change_id=command_args.change))})
        return EXIT_OK
    if command == "obligation":
        _json_print(protocol.obligation(command_args.entity))
        return EXIT_OK
    if command == "evidence":
        _json_print(protocol.evidence(command_args.entity))
        return EXIT_OK
    if command in {"rename", "move", "signature"}:
        from .model import EditCapability
        target = world.program.entity(command_args.entity)
        operation = {"rename": "rename_symbol", "move": "move_symbol", "signature": "change_signature"}[command]
        capability = EditCapability.for_operation(operation, target.id)
        if command == "rename":
            plan = protocol.preview_rename(command_args.entity, command_args.value, capability)
        elif command == "move":
            plan = protocol.preview_move(command_args.entity, command_args.value, capability)
        else:
            plan = protocol.preview_change_signature(command_args.entity, command_args.value, capability)
        _json_print(plan.to_dict())
        return EXIT_OK if plan.ready else EXIT_DIAGNOSTIC
    if command == "context":
        _json_print(protocol.compile_context(command_args.entity, goal="historical").to_dict())
        return EXIT_OK
    raise ValueError(f"HistoricalCommandUnavailable: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "historical":
            return _run_historical(args)
        return _main_production(args)
    except SystemExit:
        raise
    except Exception as exc:
        as_json = bool(getattr(locals().get("args", None), "json", False))
        payload = _error_payload(exc)
        if as_json:
            _json_print(payload)
        else:
            diagnostic = payload["diagnostics"][0]
            print(f"merlo: {diagnostic['code']}: {diagnostic['message']}", file=sys.stderr)
        return EXIT_DIAGNOSTIC


if __name__ == "__main__":
    raise SystemExit(main())
