"""Native-subset coverage and executable kernels from five real Python projects."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from research.archive.alpha1.merlo.native_differential import run_differential


EXTERNAL_CORPUS_SCHEMA_VERSION = 1
EXTERNAL_SEED = 0xE7060001
_MASK64 = (1 << 64) - 1
_PROJECTS = (
    ("click", "cli-library"),
    ("pluggy", "plugin-architecture"),
    ("flask", "backend-framework"),
    ("boltons", "utility-library"),
    ("itsdangerous", "security-signing"),
)
_ALLOWED_KERNEL_NODES = (
    ast.Name,
    ast.Constant,
    ast.BinOp,
    ast.Add,
    ast.Mult,
    ast.BitAnd,
    ast.BitOr,
    ast.BitXor,
    ast.LShift,
    ast.RShift,
    ast.Mod,
    ast.Load,
)


def _rank(project: str, locator: str) -> str:
    return hashlib.sha256(f"{EXTERNAL_SEED}:{project}:{locator}".encode()).hexdigest()


def _revision(root: Path) -> str | None:
    completed = subprocess.run(
        ("git", "-C", str(root), "rev-parse", "HEAD"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _blockers(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    result = set()
    if isinstance(node, ast.AsyncFunctionDef):
        result.add("async")
    if node.decorator_list:
        result.add("decorators")
    for child in ast.walk(node):
        if isinstance(child, (ast.Try, ast.Raise)):
            result.add("exceptions")
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            result.add("context_managers")
        elif isinstance(child, (ast.Yield, ast.YieldFrom)):
            result.add("generators")
        elif isinstance(child, (ast.Await, ast.AsyncFor)):
            result.add("async")
        elif isinstance(child, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            result.add("dynamic_expressions")
        elif isinstance(child, ast.Call):
            result.add("calls")
        elif isinstance(child, (ast.Attribute, ast.Subscript)):
            result.add("object_or_container_access")
        elif isinstance(child, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
            result.add("containers")
        elif isinstance(child, ast.Constant) and isinstance(child.value, (str, bytes)):
            result.add("text_or_bytes")
    return result


def _kernel(
    project: str,
    relative: str,
    node: ast.FunctionDef,
) -> dict[str, Any] | None:
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
        return None
    expression = node.body[0].value
    if not isinstance(expression, ast.BinOp):
        return None
    nodes = list(ast.walk(expression))
    if not all(isinstance(item, _ALLOWED_KERNEL_NODES) for item in nodes):
        return None
    if any(
        isinstance(item, ast.Constant)
        and (not isinstance(item.value, int) or isinstance(item.value, bool) or item.value < 0)
        for item in nodes
    ):
        return None
    parameter_names = {item.arg for item in node.args.args}
    used_names = sorted({item.id for item in nodes if isinstance(item, ast.Name)})
    if not used_names or not set(used_names) <= parameter_names:
        return None
    locator = f"{relative}:{node.lineno}:{node.name}"
    return {
        "project": project,
        "locator": locator,
        "rank": _rank(project, locator),
        "expression": ast.unparse(expression),
        "parameters": used_names,
    }


def _execute_kernel(kernel: dict[str, Any], destination: Path) -> dict[str, Any]:
    arguments = tuple(7 + index for index, _ in enumerate(kernel["parameters"]))
    source = (
        "fn main("
        + ", ".join(f"{name}: UInt64" for name in kernel["parameters"])
        + ") -> UInt64:\n    "
        + kernel["expression"]
        + "\n"
    )
    expression = ast.parse(kernel["expression"], mode="eval")
    expected = eval(
        compile(expression, kernel["locator"], "eval"),
        {"__builtins__": {}},
        dict(zip(kernel["parameters"], arguments, strict=True)),
    ) & _MASK64
    result = run_differential(
        source,
        arguments,
        artifact_dir=destination,
    )
    return {
        **kernel,
        "arguments": list(arguments),
        "expected": expected,
        "source": source,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "ok": result.ok and dict(result.observations)["native"].return_value == expected,
        "differential": result.to_dict(),
    }


def run_external_corpus(
    *,
    corpus_root: str | Path = "/tmp/meldra-external-corpus",
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/stage06p_external_corpus",
    kernels_per_project: int = 10,
) -> dict[str, Any]:
    root = Path(corpus_root)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    projects = []
    selected_kernels = []
    for project, category in _PROJECTS:
        project_root = root / project
        if not project_root.is_dir():
            projects.append(
                {
                    "project": project,
                    "category": category,
                    "status": "UNMEASURED_PROJECT_UNAVAILABLE",
                }
            )
            continue
        files = sorted(
            path
            for path in project_root.rglob("*.py")
            if not any(part in {".git", ".venv", "venv", "build", "dist"} for part in path.parts)
        )
        functions = 0
        parse_failures = []
        blocker_counts: Counter[str] = Counter()
        candidates = []
        digest = hashlib.sha256()
        for path in files:
            relative = str(path.relative_to(project_root))
            try:
                payload = path.read_bytes()
                module = ast.parse(payload, filename=relative)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                parse_failures.append(
                    {"path": relative, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            digest.update(relative.encode())
            digest.update(hashlib.sha256(payload).digest())
            for node in ast.walk(module):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                functions += 1
                blocker_counts.update(_blockers(node))
                if isinstance(node, ast.FunctionDef):
                    candidate = _kernel(project, relative, node)
                    if candidate is not None:
                        candidates.append(candidate)
        chosen = sorted(candidates, key=lambda item: (item["rank"], item["locator"]))[
            :kernels_per_project
        ]
        selected_kernels.extend(chosen)
        projects.append(
            {
                "project": project,
                "category": category,
                "status": "SCANNED",
                "revision": _revision(project_root),
                "python_files": len(files),
                "functions": functions,
                "parse_failures": parse_failures,
                "blocker_counts": dict(sorted(blocker_counts.items())),
                "arithmetic_kernel_candidates": len(candidates),
                "selected_kernels": len(chosen),
                "tree_digest": digest.hexdigest(),
            }
        )

    executions = []
    for index, kernel in enumerate(selected_kernels):
        executions.append(
            _execute_kernel(
                kernel,
                destination / "kernels" / f"kernel_{index:03d}",
            )
        )
    report = {
        "schema_version": EXTERNAL_CORPUS_SCHEMA_VERSION,
        "kind": "MeldraStage06PExternalCorpus",
        "selection": {
            "seed": EXTERNAL_SEED,
            "projects": [name for name, _ in _PROJECTS],
            "categories": [category for _, category in _PROJECTS],
            "kernel_rule": "single-return nonnegative integer expression over parameters using arithmetic/bitwise operators",
            "kernels_per_project_max": kernels_per_project,
        },
        "projects": projects,
        "project_count": len(projects),
        "scanned_project_count": sum(item["status"] == "SCANNED" for item in projects),
        "total_files": sum(item.get("python_files", 0) for item in projects),
        "total_functions": sum(item.get("functions", 0) for item in projects),
        "selected_kernel_count": len(selected_kernels),
        "executed_kernels": executions,
        "kernel_failures": [item for item in executions if not item["ok"]],
        "limitations": [
            "This is whole-project syntax/feature coverage plus executable pure-kernel evidence, not a claim that Meldra compiles Python projects.",
            "The kernel rule is fixed and deterministic but finds no arithmetic kernel in some dynamic projects.",
            "Project dependencies and project test suites are outside this compiler-subset check.",
        ],
    }
    (destination / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = ["EXTERNAL_CORPUS_SCHEMA_VERSION", "run_external_corpus"]
