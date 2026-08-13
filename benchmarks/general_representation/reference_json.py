from __future__ import annotations

import sys

from merlo.general_json_oracle import evaluate_python_oracle


def main() -> int:
    repeat = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if repeat <= 0:
        return 64
    payload = sys.stdin.buffer.read()
    result = None
    for _ in range(repeat):
        result = evaluate_python_oracle(payload)
    assert result is not None
    if not result.ok:
        print(f"ERROR family={result.error_family} offset={result.error_offset}")
        return 2
    print(
        f"OK checksum={result.checksum} nodes={result.nodes} "
        f"arrays={result.arrays} objects={result.objects} fields={result.fields}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
