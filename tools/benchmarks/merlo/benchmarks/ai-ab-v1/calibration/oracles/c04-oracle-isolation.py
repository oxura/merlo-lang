from __future__ import annotations
import argparse
import json
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument('--workspace', required=True); parser.add_argument('--arm', required=True); parser.add_argument('--evidence', required=True)
    args = parser.parse_args(); workspace = Path(args.workspace); evidence = Path(args.evidence)
    forbidden = {'oracle.py', 'calibration.json', 'tasks.json'}
    relative_paths = [p.relative_to(workspace).parts for p in workspace.rglob('*')] if workspace.is_dir() else []
    leaked = [parts for parts in relative_paths if any(part in forbidden for part in parts)]
    checks = {'workspace_exists': workspace.is_dir(), 'trusted_files_hidden_recursive': not leaked, 'evidence_present': evidence.is_file()}
    print(json.dumps({'calibration_id': 'c04-oracle-isolation', 'checks': checks, 'status': 'PASS' if all(checks.values()) else 'FAIL'}, sort_keys=True))

if __name__ == '__main__': main()
