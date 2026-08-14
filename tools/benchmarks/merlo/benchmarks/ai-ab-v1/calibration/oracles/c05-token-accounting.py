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
    input_tokens = evidence.get("input_tokens")
    output_tokens = evidence.get("output_tokens")
    total_tokens = evidence.get("total_tokens")
    checks["total_identity"] = (
        type(input_tokens) is int
        and type(output_tokens) is int
        and type(total_tokens) is int
        and total_tokens == input_tokens + output_tokens
    )
    print(json.dumps({
        "calibration_id": "c05-token-accounting",
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
    }, sort_keys=True))
if __name__=='__main__': main()
