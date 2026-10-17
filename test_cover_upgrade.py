"""Smaller complete covers and budget designs invalidate only affected wheels."""

from itertools import combinations
import os
from pathlib import Path

import pytest

from covering import budget_designs, designs, dispatch
from covering.common import compute_coverage_pct
from loto_enterprise.core import walk_forward_adapter as wf


def _replace_file(path: Path, text: str) -> None:
    """Give the new file a distinct stamp even on coarse filesystem clocks."""
    previous = path.stat().st_mtime_ns if path.exists() else None
    path.write_text(text, encoding="utf-8")
    if previous is not None:
        stamp = max(path.stat().st_mtime_ns, previous + 1_000_000_000)
        os.utime(path, ns=(stamp, stamp))


def _block_text(blocks) -> str:
    return "\n".join(" ".join(map(str, block)) for block in blocks) + "\n"


@pytest.mark.parametrize("pick, guarantee, maximum", [(6, 4, 152), (5, 4, 404), (6, 5, 808)])
def test_pool16_complete_covers_retain_guarantee_at_lower_cost(pick, guarantee, maximum):
    blocks = designs._load_lajolla(16, pick, guarantee)
    assert blocks is not None
    assert len(blocks) <= maximum
    assert len(blocks) == len({tuple(sorted(block)) for block in blocks})
    assert compute_coverage_pct(blocks, list(range(1, 17)), guarantee) == 100.0
    assert designs.complete_design_size(16, pick, guarantee) == len(blocks)


def test_smaller_cover_changes_only_affected_keys_and_budget_branch(tmp_path, monkeypatch):
    """An already running process notices that its budget now buys a full cover."""
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    monkeypatch.setattr(designs, "_LAJOLLA_DIRS", [tmp_path])
    path = tmp_path / "C_7_6_3.txt"
    pool = list(range(1, 8))
    # Four different omitted positions suffice: a triple cannot contain all four.
    compact = [[n for n in pool if n != omitted] for omitted in range(1, 5)]
    larger = compact + [[n for n in pool if n != 5]]
    _replace_file(path, _block_text(larger))
    assert designs.complete_design_size(7, 6, 3) == 5
    assert not dispatch.budget_buys_complete_design(7, 6, 3, 4)
    before_full = wf._wheel_sig(7, "6/49", 3)
    before_budget = wf._wheel_sig(7, "6/49", 3, None, 4)
    smaller_budget = wf._wheel_sig(7, "6/49", 3, None, 3)
    other_geometry = wf._wheel_sig(8, "6/49", 3)
    assert before_budget.endswith("|sa1")

    _replace_file(path, _block_text(compact))
    # Do not clear caches: their file stamps must notice this update themselves.
    assert designs.complete_design_size(7, 6, 3) == 4
    assert dispatch.budget_buys_complete_design(7, 6, 3, 4)
    assert wf._wheel_sig(7, "6/49", 3) != before_full
    after_budget = wf._wheel_sig(7, "6/49", 3, None, 4)
    assert after_budget != before_budget
    assert after_budget.startswith("lajolla|") and after_budget.endswith("|bf1")
    assert wf._wheel_sig(7, "6/49", 3, None, 3) == smaller_budget
    assert wf._wheel_sig(8, "6/49", 3) == other_geometry
    scores = {n: float(8 - n) for n in pool}
    full = dispatch.generate_wheel("lajolla", pool, 6, 3, 0, scores, draw_n=6, max_num=49)
    capped = dispatch.generate_wheel("hitcover", pool, 6, 3, 4, scores, draw_n=6, max_num=49)
    assert capped == full
    assert len(capped[0]) == 4 and capped[1] == 100.0


def test_budget_section_update_preserves_other_budget_cache_keys(tmp_path, monkeypatch):
    """Replacing B 65 invalidates B 65, while B 66 and full covers retain their keys."""
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    monkeypatch.setattr(budget_designs, "_BUDGET_DIRS", [tmp_path])
    path = tmp_path / "H_16_6_4.txt"
    blocks = list(combinations(range(1, 17), 6))[:67]
    section65 = blocks[:65]
    section66 = blocks[:66]
    text66 = "B 66\n" + _block_text(section66)
    _replace_file(path, "B 65\n" + _block_text(section65) + text66)
    assert budget_designs.load_budget_design(16, 6, 4, 65) is not None
    sig65 = budget_designs.budget_design_signature(16, 6, 4, 65)
    sig66 = budget_designs.budget_design_signature(16, 6, 4, 66)
    before = {cap: wf._wheel_sig(16, "6/49", 4, None, cap) for cap in (0, 64, 65, 66)}
    assert before[65].endswith(f"|bc1|bd{sig65}")
    assert before[66].endswith(f"|bc1|bd{sig66}")

    section65[-1] = blocks[66]
    _replace_file(path, "B 65\n" + _block_text(section65) + text66)
    assert budget_designs.budget_design_signature(16, 6, 4, 65) != sig65
    assert budget_designs.budget_design_signature(16, 6, 4, 66) == sig66
    assert wf._wheel_sig(16, "6/49", 4, None, 65) != before[65]
    for cap in (0, 64, 66):
        assert wf._wheel_sig(16, "6/49", 4, None, cap) == before[cap]
