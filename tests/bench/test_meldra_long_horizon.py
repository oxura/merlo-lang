from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from merlo.long_horizon import run_long_horizon


def test_ten_step_semantic_evolution_stays_consistent(tmp_path: Path):
    report = run_long_horizon(tmp_path)

    assert report.failures == ()
    assert report.steps == 10
    assert report.final_locator == "app.billing.final_price"
    assert len(set(report.revisions)) == 11
    assert len(set(report.world_revisions)) == 11
    assert report.evolution_log_entries == 10
    assert report.valid_evidence > 0
    assert report.stale_evidence > 0

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.service import quote; assert quote(10) == 10",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
