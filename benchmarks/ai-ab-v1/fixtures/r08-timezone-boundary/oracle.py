#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path

# Contract: fixed-offset local-day rollover. Return 1 once UTC hour plus the
# fixed offset reaches 24, otherwise 0. This is intentionally timezone-DB-free.
CASES = [
    ('23|1', 1),
    ('22|1', 0),
    ('12|0', 0),
]


def check(workspace, arm):
    root = Path(workspace) / arm
    source = root / ("main.py" if arm == "python" else "main.mlo")
    results = []
    timed_out = False
    for n, (request, expected) in enumerate(CASES):
        cmd = ["python3", "-B", str(source)] if arm == "python" else ["merlo", "run", str(source)]
        try:
            p = subprocess.run(
                cmd,
                input=json.dumps(request).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            results.append({"case_id": n, "passed": False, "timeout": True, "actual": None, "expected": expected})
            break
        try:
            actual = json.loads(p.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            actual = None
        results.append({"case_id": n, "passed": p.returncode == 0 and actual == expected, "timeout": False, "actual": actual, "expected": expected})
    return {
        "case_id": "r08-timezone-boundary",
        "passed": not timed_out and all(r["passed"] for r in results),
        "defect_case_passed": bool(results) and results[0]["passed"],
        "unaffected_cases_passed": len(results) == len(CASES) and all(r["passed"] for r in results[1:]),
        "timeout": timed_out,
        "cases": results,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--arm", required=True)
    args = p.parse_args()
    print(json.dumps(check(args.workspace, args.arm)))
