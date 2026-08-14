from __future__ import annotations
import argparse
import json
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--arm", choices=("merlo", "python"), required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--protocol", required=True)
    args = parser.parse_args()
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    protocol = json.loads(Path(args.protocol).read_text(encoding="utf-8"))
    budgets = protocol["budgets"]
    fields = (
        "input_tokens", "output_tokens", "iterations", "tool_calls",
        "wall_time_ms",
    )
    checks = {"workspace_exists": Path(args.workspace).is_dir()}
    for key in fields:
        value = evidence.get(key)
        limit = budgets.get(key)
        checks[f"{key}_bounded"] = (
            type(value) is int
            and type(limit) is int
            and 0 <= value <= limit
        )
    field = evidence.get("boundary_field")
    checks["exact_boundary"] = (
        isinstance(field, str)
        and field in fields
        and evidence.get(field) == budgets.get(field)
    )
    terminal_reasons = {
        "input_tokens": "token_budget",
        "output_tokens": "token_budget",
        "iterations": "iteration_budget",
        "tool_calls": "tool_budget",
        "wall_time_ms": "time_budget",
    }
    checks["terminal_reason"] = (
        isinstance(field, str)
        and evidence.get("terminal_reason") == terminal_reasons.get(field)
    )
    print(json.dumps({
        "calibration_id": "c02-budget-boundary",
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
    }, sort_keys=True))
if __name__=='__main__': main()
