from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

def digest_map(root: Path) -> dict[str, dict[str, object]]:
    result = {}
    for path in sorted(p for p in root.rglob('*') if p.is_file() or p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = path.readlink().as_posix(); result[rel] = {'kind': 'symlink', 'content_sha256': hashlib.sha256(target.encode()).hexdigest(), 'executable': False, 'symlink_target': target}
        else:
            result[rel] = {'kind': 'file', 'content_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'executable': bool(path.stat().st_mode & 0o111), 'symlink_target': None}
    return result

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument('--workspace', required=True); parser.add_argument('--arm', required=True); parser.add_argument('--evidence', required=True); parser.add_argument('--pre-workspace', required=True)
    args = parser.parse_args(); workspace = Path(args.workspace); pre_root = Path(args.pre_workspace); evidence_path = Path(args.evidence)
    evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}; before = evidence.get('pre_digest_map'); after = evidence.get('post_digest_map'); actual_pre = digest_map(pre_root) if pre_root.is_dir() else {}; actual_post = digest_map(workspace) if workspace.is_dir() else {}
    checks = {'maps_present': isinstance(before, dict) and isinstance(after, dict), 'pre_map_matches_workspace': before == actual_pre, 'post_map_matches_workspace': after == actual_post, 'delta_is_derived': isinstance(evidence.get('changed_paths'), list) and sorted(evidence['changed_paths']) == sorted(k for k in set(before or {}) | set(after or {}) if (before or {}).get(k) != (after or {}).get(k))}
    print(json.dumps({'calibration_id': 'c03-digest-map', 'checks': checks, 'status': 'PASS' if all(checks.values()) else 'FAIL'}, sort_keys=True))

if __name__ == '__main__': main()
