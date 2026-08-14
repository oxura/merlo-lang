from __future__ import annotations
import argparse
import json
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--arm", choices=("merlo", "python"), required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    trace = evidence.get("tool_trace")
    entries = trace if isinstance(trace, list) else []
    expected = ["shell", "read", "search", "edit", "test"]
    source = "main.mlo" if args.arm == "merlo" else "main.py"
    checks = {
        "workspace_exists": workspace.is_dir(),
        "exact_common_tools": (
            [item.get("tool") for item in entries if isinstance(item, dict)]
            == expected
            and all(
                isinstance(item, dict) and item.get("success") is True
                for item in entries
            )
        ),
        "arm_source_touched": any(
            isinstance(item, dict) and item.get("path") == source
            for item in entries
        ),
    }
    print(json.dumps({
        "calibration_id": "c01-tool-smoke",
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
    }, sort_keys=True))
if __name__=='__main__': main()
