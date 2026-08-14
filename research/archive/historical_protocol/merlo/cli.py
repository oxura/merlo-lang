from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from research.archive.alpha1.merlo.core_bench import run_core_benchmark
from research.archive.historical_protocol.merlo.frontend_evaluator import ReferenceEvaluator
from research.archive.historical_protocol.merlo.frontend_semantics import check_frontend, compile_frontend

EXIT_OK = 0
EXIT_DIAGNOSTIC = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="merlo")
    commands = parser.add_subparsers(dest="command", required=True)
    historical = commands.add_parser("historical")
    historical.add_argument("historical_command", choices=("frontend-check", "frontend-ir", "frontend-run", "frontend-bench", "core-bench"))
    historical.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def _historical_args(command: str, values: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--json", action="store_true")
    if command == "frontend-run":
        parser.add_argument("symbol")
    return parser.parse_args(values)


def main(argv: Sequence[str] | None = None) -> int:
    parsed = build_parser().parse_args(list(argv or ()))
    values = _historical_args(parsed.historical_command, parsed.arguments)
    if parsed.historical_command == "core-bench":
        print(json.dumps(run_core_benchmark().to_dict(), sort_keys=True))
        return EXIT_OK
    root = Path(values.root).resolve()
    sources = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.mlo"))}
    result = check_frontend(sources)
    if parsed.historical_command == "frontend-check":
        print(json.dumps({"ok": result.ok, "diagnostics": [d.to_dict() for d in result.diagnostics]}, sort_keys=True))
        return EXIT_OK if result.ok else EXIT_DIAGNOSTIC
    compilation = compile_frontend(sources)
    if parsed.historical_command == "frontend-ir":
        print(json.dumps(compilation.core_program.to_dict(), sort_keys=True))
        return EXIT_OK
    value = ReferenceEvaluator(compilation).evaluate(values.symbol, {})
    print(json.dumps(value.to_dict(), sort_keys=True))
    return EXIT_OK
