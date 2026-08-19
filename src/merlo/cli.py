from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from merlo.alpha_protocol import AlphaProtocol
from merlo.compiler import compile_project
from merlo.evolve_protocol import EvolutionPlan, VerifiedEvolutionProtocol
from merlo.formatter import format_application_source
from merlo.project import Project
from merlo.semantic_world import SemanticWorld
from merlo.synthesize_protocol import synthesize_typed_hole
from merlo.docgen import generate_documentation, write_documentation
from merlo.test_runner import run_project_tests

EXIT_OK = 0
EXIT_DIAGNOSTIC = 1
EXIT_USAGE = 2


_PRODUCTION_COMMANDS = (
    "new", "check", "verify", "obligations", "holes", "explain-hole",
    "build", "run", "test", "fmt", "expand", "explain", "doc", "map",
    "inspect", "refs", "callers", "callees", "deps", "impact", "why",
    "context", "refactor", "evolve", "synthesize", "add",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="merlo", description="Merlo alpha project tooling")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="create a project")
    new.add_argument("path", nargs="?", default=".")
    new.add_argument("--name")
    new.add_argument("--version", default="0.1.0")
    _json_flag(new)

    for name in ("check", "build", "run", "test", "fmt", "expand", "explain", "doc", "map"):
        command = commands.add_parser(name, help=f"{name} a Merlo project")
        command.add_argument("path", nargs="?", default=".")
        _json_flag(command)
        if name == "check":
            command.add_argument("--smt", choices=("z3",))
            command.add_argument(
                "--smt-timeout-ms",
                type=int,
                default=1000,
            )
            command.add_argument(
                "--smt-max-paths",
                type=int,
                default=256,
            )
        elif name == "build":
            command.add_argument("-o", "--output")
            command.add_argument("--release", action="store_true")
            command.add_argument("--target", choices=("native", "wasm"), default="native")
            command.add_argument("--entry")
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

    for name in ("verify", "obligations"):
        command = commands.add_parser(
            name,
            help=(
                "verify project obligations"
                if name == "verify"
                else "list typed verification obligations"
            ),
        )
        command.add_argument("path", nargs="?", default=".")
        command.add_argument("--smt", choices=("z3",))
        command.add_argument("--smt-timeout-ms", type=int, default=1000)
        command.add_argument("--smt-max-paths", type=int, default=256)
        _json_flag(command)

    holes = commands.add_parser("holes", help="list typed holes")
    holes.add_argument("path", nargs="?", default=".")
    _json_flag(holes)

    explain_hole = commands.add_parser(
        "explain-hole",
        help="show the completion context for one typed hole",
    )
    explain_hole.add_argument("hole_id")
    explain_hole.add_argument("path", nargs="?", default=".")
    _json_flag(explain_hole)

    synthesize = commands.add_parser(
        "synthesize",
        help="generate and verify deterministic typed-hole candidates",
    )
    synthesize.add_argument("target")
    synthesize.add_argument("path", nargs="?", default=".")
    synthesize.add_argument("--hole")
    synthesize.add_argument("--goal", default="")
    synthesize.add_argument("--max-candidates", type=int, default=16)
    synthesize.add_argument("--apply", action="store_true")
    synthesize.add_argument("--report-out")
    _json_flag(synthesize)

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

    evolve = commands.add_parser(
        "evolve",
        help="preview or apply a verified semantic evolution",
    )
    evolve_commands = evolve.add_subparsers(
        dest="evolution_operation",
        required=True,
    )
    evolve_rename = evolve_commands.add_parser(
        "rename",
        help="create a verified rename plan",
    )
    evolve_rename.add_argument("target")
    evolve_rename.add_argument("new_name")
    evolve_rename.add_argument("path", nargs="?", default=".")
    evolve_rename.add_argument("--goal", default="")
    evolve_rename.add_argument("--apply", action="store_true")
    evolve_rename.add_argument("--plan-out")
    _json_flag(evolve_rename)
    evolve_apply = evolve_commands.add_parser(
        "apply",
        help="validate and apply an exact serialized evolution plan",
    )
    evolve_apply.add_argument("plan")
    evolve_apply.add_argument("path", nargs="?", default=".")
    _json_flag(evolve_apply)

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


def _compile_verification(args: argparse.Namespace):
    return compile_project(
        _input_path(args.path),
        smt_backend=getattr(args, "smt", None),
        smt_timeout_ms=getattr(args, "smt_timeout_ms", 1000),
        smt_max_paths=getattr(args, "smt_max_paths", 256),
        require_interface_lock=False,
    )


def _obligation_rows(compilation: Any) -> list[dict[str, Any]]:
    verification = {
        item.obligation_id: item.to_dict()
        for item in compilation.verification_metrics.obligations
    }
    bounded = {
        item.obligation_id: item.to_dict()
        for item in compilation.bounded_symbolic.results
    }
    smt = {
        item.obligation_id: item.to_dict()
        for item in compilation.smt.results
    }
    rows = []
    for obligation in compilation.obligations.obligations:
        row = obligation.to_dict()
        row["verification"] = verification[obligation.obligation_id]
        row["bounded_symbolic"] = bounded.get(obligation.obligation_id)
        row["smt"] = smt.get(obligation.obligation_id)
        rows.append(row)
    return rows


def _hole_rows(compilation: Any) -> list[dict[str, Any]]:
    rows = []
    for function in compilation.elaborated.canonical_program.functions:
        for hole in function.holes:
            row = hole.to_payload()
            row["owner"] = function.name
            rows.append(row)
    return sorted(rows, key=lambda item: item["hole_id"])


def _verification_text(metrics: Any, ok: bool) -> str:
    status = "passed" if ok else "incomplete"
    return (
        f"verification {status}\n"
        f"total: {metrics.total_obligations}\n"
        f"automatically closed: {metrics.automatically_closed}\n"
        f"runtime guarded: {metrics.runtime_guarded}\n"
        f"refuted: {metrics.refuted}\n"
        f"unresolved: {metrics.unresolved}\n"
    )


def _main_production(args: argparse.Namespace) -> int:
    name = args.command

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
        compilation = compile_project(
            candidate,
            smt_backend=args.smt,
            smt_timeout_ms=args.smt_timeout_ms,
            smt_max_paths=args.smt_max_paths,
            require_interface_lock=False,
        )
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
    if name in {"verify", "obligations"}:
        compilation = _compile_verification(args)
        metrics = compilation.verification_metrics
        rows = _obligation_rows(compilation)
        ok = metrics.refuted == 0 and metrics.unresolved == 0
        if name == "verify":
            payload = {
                "ok": ok,
                "entry_path": compilation.entry_path,
                "verification": metrics.to_dict(),
                "diagnostics": [],
            }
            _emit(payload, args.json, text=_verification_text(metrics, ok))
            return EXIT_OK if ok else EXIT_DIAGNOSTIC
        text = "no obligations\n" if not rows else "".join(
            f"{row['verification']['state']} {row['category']} "
            f"{row['owner_symbol_id']}: {row['predicate']} "
            f"[{row['obligation_id']}]\n"
            for row in rows
        )
        payload = {
            "ok": True,
            "entry_path": compilation.entry_path,
            "obligation_digest": compilation.obligations.digest,
            "summary": metrics.to_dict(),
            "obligations": rows,
        }
        _emit(payload, args.json, text=text)
        return EXIT_OK
    if name in {"holes", "explain-hole"}:
        compilation = compile_project(
            _input_path(args.path),
            require_interface_lock=False,
        )
        rows = _hole_rows(compilation)
        if name == "explain-hole":
            row = next(
                (item for item in rows if item["hole_id"] == args.hole_id),
                None,
            )
            if row is None:
                raise ValueError(f"UnknownTypedHole: {args.hole_id}")
            context = ", ".join(
                f"{item['name']}: {item['type']}"
                for item in row["context"]
            ) or "none"
            text = (
                f"{row['hole_id']}\n"
                f"owner: {row['owner']}\n"
                f"expected type: {row['expected_type']}\n"
                f"context: {context}\n"
                f"effects: {', '.join(row['effects']) or 'none'}\n"
                f"capabilities: {', '.join(row['capabilities']) or 'none'}\n"
            )
            _emit({"ok": True, "hole": row}, args.json, text=text)
            return EXIT_OK
        text = "no typed holes\n" if not rows else "".join(
            f"{row['hole_id']}: {row['expected_type']} in {row['owner']}\n"
            for row in rows
        )
        _emit(
            {
                "ok": True,
                "entry_path": compilation.entry_path,
                "holes": rows,
            },
            args.json,
            text=text,
        )
        return EXIT_OK
    if name == "build":
        candidate = _input_path(args.path)
        project = Project.discover(candidate)
        root = project.root if project else candidate.parent if candidate.is_file() else candidate
        if args.target == "wasm":
            output = args.output or str(root / ".merlo" / "build" / "app.wasm")
            compilation = compile_project(
                candidate,
                emit_wasm=True,
                wasm_entry=args.entry,
                wasm_output=output,
                require_interface_lock=False,
            )
            if compilation.wasm is None:
                raise RuntimeError("WasmBuildMissing: compiler did not produce a module")
            payload = {
                "ok": True,
                "project": str(root),
                "entry_path": compilation.entry_path,
                "digest": compilation.wasm.artifact_digest,
                "wasm": str(Path(output).resolve()),
                "exports": list(compilation.wasm.exports),
            }
            _emit(payload, args.json, text=f"{Path(output).resolve()}\n")
            return EXIT_OK
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
            value = protocol.call(
                "refactor.move",
                {
                    "target": args.target,
                    "module": args.module,
                    "mode": "apply" if args.apply else "preview",
                },
            )
        else:
            value = protocol.call(
                "refactor.signature",
                {
                    "target": args.target,
                    "signature": args.signature,
                    "mode": "apply" if args.apply else "preview",
                },
            )
        _emit(value, args.json, text=None)
        return (
            EXIT_OK
            if not isinstance(value, dict) or value.get("diagnostic") is None
            else EXIT_DIAGNOSTIC
        )
    if name == "evolve":
        project = _project(args.path)
        protocol = VerifiedEvolutionProtocol(_world(project))
        if args.evolution_operation == "rename":
            plan = protocol.preview_rename(
                args.target,
                args.new_name,
                goal=args.goal,
            )
            if args.plan_out:
                destination = Path(args.plan_out).resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(plan.to_json() + "\n", encoding="utf-8")
            if not args.apply:
                impact = plan.impact
                payload = {
                    "ok": True,
                    "status": "preview",
                    "plan_path": (
                        str(Path(args.plan_out).resolve())
                        if args.plan_out
                        else None
                    ),
                    "plan": plan.to_dict(),
                }
                text = (
                    f"evolution plan {plan.digest}\n"
                    f"direct symbols: {len(impact.directly_changed)}\n"
                    f"affected symbols: {len(impact.transitively_affected)}\n"
                    f"files: {len(impact.files)}\n"
                )
                _emit(payload, args.json, text=text)
                return EXIT_OK
        else:
            plan_path = Path(args.plan).resolve()
            plan = EvolutionPlan.from_json(
                plan_path.read_text(encoding="utf-8"),
                world=protocol.world,
            )
        result = protocol.apply(plan)
        ok = result.status == "committed"
        payload = {
            "ok": ok,
            "status": result.status,
            "result": result.to_dict(),
        }
        text = (
            f"evolution {result.status}\n"
            f"change: {result.change_digest}\n"
            + (
                f"after world: {result.after_world_digest}\n"
                f"preservation: {result.preservation.overall}\n"
                f"evidence: {result.evidence.digest}\n"
                if ok
                else f"diagnostic: {result.diagnostic.message}\n"
            )
        )
        _emit(payload, args.json, text=text)
        return EXIT_OK if ok else EXIT_DIAGNOSTIC
    if name == "synthesize":
        project = _project(args.path)
        world = _world(project)
        symbol = world.resolve(args.target)
        holes = tuple(symbol.get("holes", ()))
        if args.hole is None:
            if len(holes) != 1:
                raise ValueError(
                    "TypedHoleSelectionRequired: target must have exactly "
                    "one hole or --hole must be provided"
                )
            hole_id = holes[0]["hole_id"]
        else:
            matches = tuple(
                item for item in holes if item.get("hole_id") == args.hole
            )
            if len(matches) != 1:
                raise ValueError(f"UnknownTypedHole: {args.hole}")
            hole_id = args.hole
        run = synthesize_typed_hole(
            world,
            symbol["symbol_id"],
            hole_id,
            goal=args.goal,
            max_candidates=args.max_candidates,
        )
        if args.report_out:
            destination = Path(args.report_out).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(run.to_json() + "\n", encoding="utf-8")
        selected = run.selected_candidate
        verified_count = sum(item.verified for item in run.verifications)
        if selected is None:
            payload = {
                "ok": False,
                "status": "no_verified_candidate",
                "report_path": (
                    str(Path(args.report_out).resolve())
                    if args.report_out
                    else None
                ),
                "run": run.to_dict(),
            }
            _emit(
                payload,
                args.json,
                text=(
                    f"no verified synthesis candidate\n"
                    f"generated: {len(run.candidates)}\n"
                    f"verified: {verified_count}\n"
                ),
            )
            return EXIT_DIAGNOSTIC
        expression = str(selected.change_ir.metadata.get("replacement", ""))
        if args.apply:
            application = run.apply(world)
            ok = application["status"] == "committed"
            payload = {
                "ok": ok,
                "status": application["status"],
                "selected_expression": expression,
                "application": application,
                "run": run.to_dict(),
            }
            _emit(
                payload,
                args.json,
                text=(
                    f"synthesis {application['status']}\n"
                    f"selected: {expression}\n"
                    f"candidates: {len(run.candidates)}\n"
                    f"verified: {verified_count}\n"
                ),
            )
            return EXIT_OK if ok else EXIT_DIAGNOSTIC
        payload = {
            "ok": True,
            "status": "selected",
            "selected_expression": expression,
            "report_path": (
                str(Path(args.report_out).resolve())
                if args.report_out
                else None
            ),
            "run": run.to_dict(),
        }
        _emit(
            payload,
            args.json,
            text=(
                f"selected: {expression}\n"
                f"candidates: {len(run.candidates)}\n"
                f"verified: {verified_count}\n"
            ),
        )
        return EXIT_OK
    raise ValueError(f"UnknownCommand: {name}")




def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
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
