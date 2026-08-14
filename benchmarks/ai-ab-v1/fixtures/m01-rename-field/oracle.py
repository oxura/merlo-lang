#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path

CASES = [
    ("op=migrate|id=7|display_name=Ada", {"id": 7, "label": "Ada"}),
    ("op=migrate|id=42|display_name=Grace", {"id": 42, "label": "Grace"}),
    ("op=migrate|id=100|display_name=Lin", {"id": 100, "label": "Lin"}),
]
UNTOUCHED = [
    ("op=legacy|id=9|display_name=Bea", {"id": 9, "display_name": "Bea"}),
]

def invoke(root, arm, request):
    source = root / ("main.py" if arm == "python" else "main.mlo")
    command = ["python3", "-B", str(source)] if arm == "python" else ["merlo", "run", str(source)]
    try:
        process = subprocess.run(
            command,
            input=request.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return {"status": "NOT_EXECUTED", "actual": None}
    if process.returncode != 0:
        return {"status": "ERROR", "actual": None}
    try:
        actual = json.loads(process.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"status": "INVALID_OUTPUT", "actual": None}
    return {"status": "OK", "actual": actual}

def check(workspace, arm):
    root = Path(workspace) / arm
    migration_results = [invoke(root, arm, request) for request, _ in CASES]
    untouched_results = [invoke(root, arm, request) for request, _ in UNTOUCHED]
    migration = [result["status"] == "OK" and result["actual"] == expected for result, (_, expected) in zip(migration_results, CASES)]
    untouched_ok = [result["status"] == "OK" and result["actual"] == expected for result, (_, expected) in zip(untouched_results, UNTOUCHED)]
    return {
        "case_id": "m01-rename-field",
        "passed": all(migration) and all(untouched_ok),
        "migration_passed": migration,
        "unaffected_passed": untouched_ok,
        "statuses": {
            "migration": [result["status"] for result in migration_results],
            "unaffected": [result["status"] for result in untouched_results],
        },
        "cases": len(CASES),
        "unaffected_cases": len(UNTOUCHED),
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--arm", required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.workspace, args.arm)))
