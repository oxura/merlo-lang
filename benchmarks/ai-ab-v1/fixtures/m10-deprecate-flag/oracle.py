#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path

TASK_ID = 'm10-deprecate-flag'
CASES = [('M|1|Ada', {'text': 'Ada'}), ('M|0|Grace', {'text': 'Grace'}), ('M|1|Linus', {'text': 'Linus'})]
UNTOUCHED = [('L|0|Legacy', {'text': 'Name: Legacy'})]
INPUT_ENCODING = 'raw'


def invoke(root, arm, request):
    source = root / ("main.py" if arm == "python" else "main.mlo")
    command = ["python3", "-B", str(source)] if arm == "python" else ["merlo", "run", str(source)]
    payload = (
        json.dumps(request, ensure_ascii=False).encode()
        if INPUT_ENCODING == "json"
        else request.encode()
    )
    try:
        process = subprocess.run(
            command,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return {"terminal_reason": "timeout"}
    if process.returncode != 0:
        return {"terminal_reason": "process_error", "returncode": process.returncode}
    try:
        return json.loads(process.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"terminal_reason": "invalid_output"}


def evaluate(root, arm, cases):
    evidence = []
    for case_id, (request, expected) in enumerate(cases):
        actual = invoke(root, arm, request)
        evidence.append({
            "case_id": case_id,
            "expected": expected,
            "actual": actual,
            "outcome": actual == expected,
        })
    return evidence


def check(workspace, arm):
    root = Path(workspace) / arm
    primary = evaluate(root, arm, CASES)
    unaffected = evaluate(root, arm, UNTOUCHED)
    all_cases = primary + unaffected
    passed_count = sum(case["outcome"] for case in all_cases)
    report = {
        "task_id": TASK_ID,
        "cases": primary,
        "case_count": len(all_cases),
        "passed_count": passed_count,
        "failed_count": len(all_cases) - passed_count,
        "task_success": bool(all_cases) and passed_count == len(all_cases),
    }
    if UNTOUCHED:
        report["unaffected_cases"] = unaffected
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--arm", required=True, choices=["merlo", "python"])
    args = parser.parse_args()
    print(json.dumps(check(args.workspace, args.arm), ensure_ascii=False, sort_keys=True))
