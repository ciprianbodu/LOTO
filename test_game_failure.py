"""Pass 2 auto-invert nu mai aruncă Pool 1 dacă a reușit."""
from __future__ import annotations

from job_queue import build_game_failure_output


def test_pass2_failure_keeps_phase1_pool():
    phase1 = {
        "hard_core": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "variants": [[1, 2, 3, 4, 5, 6]],
        "pool_size": 10,
        "audit": {"wheel_guarantee_used": 3},
        "context": {"coverage_pct": 100.0, "max_variants": 0},
    }
    out = build_game_failure_output(RuntimeError("csv boom"), phase1_data=phase1)
    assert out["hard_core"] == phase1["hard_core"]
    assert out["variants"] == phase1["variants"]
    assert out.get("error") is None
    assert out["audit"]["auto_invert_pass2_failed"] is True
    assert "csv boom" in out["audit"]["pass2_error"]
    assert out["auto_invert"] is False


def test_pass1_failure_is_empty_stub():
    out = build_game_failure_output(RuntimeError("scorer down"))
    assert out["hard_core"] == []
    assert out["variants"] == []
    assert out["error"]
    assert out["audit"]["pipeline_error"]
