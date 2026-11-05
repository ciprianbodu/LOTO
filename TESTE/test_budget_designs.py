"""Designurile cu buget precalculate (`covering_designs/budget/`): valide,
certificate exact la încărcare, cu amprenta în cheia WF; fără ele nimic nu se
schimbă."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import covering.budget_designs as bd
from covering.anneal_hits import _certify
from covering.common import compute_coverage_pct
from covering.designs import complete_design_size
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities
from loto_enterprise.core import walk_forward_adapter as wf

FILES = sorted(Path("covering_designs/budget").glob("H_*_*_*.txt"))


def _geometry(path: Path) -> tuple[int, int, int]:
    _h, v, pick, g = path.stem.split("_")
    return int(v), int(pick), int(g)


def test_budget_design_files_exist():
    assert FILES, "niciun design cu buget în covering_designs/budget/"
    assert any(_geometry(p) == (16, 6, 4) for p in FILES)


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_every_section_is_wellformed(path: Path):
    """Fiecare secțiune: exact B bilete distincte de `pick` poziții din 1..v,
    toate pozițiile jucate, bugetul între 65 și designul complet (exclusiv)."""
    v, pick, g = _geometry(path)
    designs = bd._designs(v, pick, g)
    assert designs, path
    size = complete_design_size(v, pick, g)
    for budget, (blocks, digest) in designs.items():
        assert 65 <= budget < size, (path, budget)
        assert len(blocks) == budget == len(set(blocks))
        assert all(len(b) == pick == len(set(b)) and 1 <= min(b) and max(b) <= v for b in blocks)
        assert {p for b in blocks for p in b} == set(range(1, v + 1))
        assert re.fullmatch(r"[0-9a-f]{12}", digest)
    assert bd.load_budget_design(v, pick, g, 64) is None
    assert bd.load_budget_design(v, pick, g, size) is None


@pytest.mark.parametrize("v, budget", [(16, 100), (16, 130), (15, 70), (14, 70)])
def test_stored_design_beats_the_in_process_search_on_6_49(v, budget, monkeypatch):
    """Pe calea motorului (6/49, garanția 4) designul precalculat e certificat
    față de căutarea din proces: niciun prag 3..6 nu scade, 4+ crește strict,
    acoperirea garanției nu scade, aceleași 16 numere jucate."""
    if bd.load_budget_design(v, 6, 4, budget) is None:
        pytest.skip("fără design stocat pentru acest buget")
    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    monkeypatch.setattr(bd, "load_budget_design", lambda *a, **k: None)
    before, cov0 = generate_wheel("hitcover", pool, 6, 4, budget, scores, draw_n=6, max_num=49)
    monkeypatch.undo()
    after, cov1 = generate_wheel("hitcover", pool, 6, 4, budget, scores, draw_n=6, max_num=49)
    assert len(after) == len(before) == budget == len({tuple(t) for t in after})
    assert {n for t in after for n in t} == set(pool)
    p0 = wheel_hit_probabilities(pool, before, 6, 49)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, 6, 49)["ticket"]
    assert p1[4] > p0[4] * 1.01
    for t in (3, 4, 5, 6):
        assert p1[t] >= p0[t] - 1e-15
    assert cov1 >= cov0 - 1e-9
    assert _certify(pool, before, after, 4, 6, 49)[0] == ""


def test_the_design_follows_positions_not_labels():
    pool_a = list(range(1, 17))
    pool_b = [3 * n for n in range(1, 17)]
    base_a, _ = generate_wheel("greedy", pool_a, 6, 4, 100, {n: float(17 - n) for n in pool_a})
    base_b = [[pool_b[pool_a.index(n)] for n in t] for t in base_a]
    out_a, audit_a = bd.improve_with_budget_design(pool_a, base_a, 4, 6, 49)
    out_b, audit_b = bd.improve_with_budget_design(pool_b, base_b, 4, 6, 49)
    assert audit_a["applied"] == audit_b["applied"]
    assert [[pool_b[pool_a.index(n)] for n in t] for t in out_a] == out_b


def test_an_uncertified_or_missing_design_leaves_the_wheel_alone(monkeypatch):
    pool = list(range(1, 17))
    scores = {n: float(17 - n) for n in pool}
    base, _ = generate_wheel("greedy", pool, 6, 4, 100, scores)
    monkeypatch.setattr(bd, "load_budget_design", lambda *a, **k: None)
    out, audit = bd.improve_with_budget_design(pool, base, 4, 6, 49)
    assert out == base and audit["reason"] == "no stored design"
    # Un design cu dubluri, cu o poziție nejucată sau care pierde la un prag nu trece.
    monkeypatch.setattr(bd, "load_budget_design", lambda *a, **k: [list(range(1, 7))] * 100)
    out, audit = bd.improve_with_budget_design(pool, base, 4, 6, 49)
    assert out == base and audit["reason"] == "duplicate tickets"
    monkeypatch.setattr(bd, "load_budget_design", lambda *a, **k: [[1, 2, 3, 4, 5, 6]])
    out, audit = bd.improve_with_budget_design(pool, base[:1], 4, 6, 49)
    assert out == base[:1] and audit["reason"] in {"pool number dropped", "no strict gain", "chance falls at a threshold"}


def test_outside_limits_and_empty_input():
    assert bd.improve_with_budget_design(list(range(1, 17)), [], 4, 6, 49)[1]["reason"] == "no tickets"
    pool = list(range(1, 50))
    assert bd.improve_with_budget_design(pool, [list(range(1, 7))], 4, 6, 49)[1]["reason"] == "outside limits"


def test_invalid_sections_are_skipped(tmp_path, monkeypatch):
    text = "\n".join(
        ["# test", "B 2", "1 2 3 4 5 6", "1 2 3 4 5 7", "B 3", "1 2 3 4 5 6", "1 2 3 4 5 6", "7 8 9 1 2 3",
         "B 1", "1 2 3 4 5 9"]
    )
    (tmp_path / "H_9_6_4.txt").write_text(text, encoding="utf-8")
    monkeypatch.setattr(bd, "_BUDGET_DIRS", [tmp_path])
    bd._FILE_CACHE.clear()
    designs = bd._designs(9, 6, 4)
    assert set(designs) == set()  # B 2 nu joacă toate pozițiile, B 3 are dubluri, B 1 nu joacă 7, 8
    (tmp_path / "H_9_6_4.txt").write_text(
        "B 2\n1 2 3 4 5 6\n4 5 6 7 8 9\n", encoding="utf-8"
    )
    bd._FILE_CACHE.clear()
    assert bd.load_budget_design(9, 6, 4, 2) == [[1, 2, 3, 4, 5, 6], [4, 5, 6, 7, 8, 9]]
    assert bd.budget_design_signature(9, 6, 4, 2) and bd.budget_design_signature(9, 6, 4, 3) is None
    # Un caracter nenumeric strică numai secțiunea lui, nu tot fișierul.
    (tmp_path / "H_9_6_4.txt").write_text(
        "B 2\n1 2 3 4 5 6\n4 5 6 7 8 9\nB 3\n1 2 3 4 5 x\n4 5 6 7 8 9\n2 3 4 5 6 7\n", encoding="utf-8"
    )
    bd._FILE_CACHE.clear()
    assert set(bd._designs(9, 6, 4)) == {2}


def test_a_file_replaced_with_the_same_date_is_read_again(tmp_path, monkeypatch):
    """Memoria fișierelor ține cont și de mărime: `cp -p` sau o sincronizare
    care păstrează data nu mai servește designul vechi în procesul pornit."""
    import os

    monkeypatch.setattr(bd, "_BUDGET_DIRS", [tmp_path])
    bd._FILE_CACHE.clear()
    path = tmp_path / "H_9_6_4.txt"
    path.write_text("B 2\n1 2 3 4 5 6\n4 5 6 7 8 9\n", encoding="utf-8")
    stamp = path.stat().st_mtime_ns
    assert bd.load_budget_design(9, 6, 4, 2) == [[1, 2, 3, 4, 5, 6], [4, 5, 6, 7, 8, 9]]
    path.write_text("B 2\n1 2 3 4 5 6\n4 5 6 7 8 9\nB 3\n1 2 3 4 5 6\n4 5 6 7 8 9\n2 3 4 5 6 7\n", encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))
    assert bd.load_budget_design(9, 6, 4, 3) == [[1, 2, 3, 4, 5, 6], [4, 5, 6, 7, 8, 9], [2, 3, 4, 5, 6, 7]]
    bd._FILE_CACHE.clear()


def test_walk_forward_key_carries_the_stored_section_fingerprint(monkeypatch):
    key = wf._wheel_sig(16, "6/49", 4, None, 100)
    stored = bd.budget_design_signature(16, 6, 4, 100)
    assert stored and key.endswith(f"|bc1|bd{stored}")
    monkeypatch.setattr(bd, "budget_design_signature", lambda *a, **k: None)
    assert wf._wheel_sig(16, "6/49", 4, None, 100).endswith("|bc1")


def test_engine_budget_path_uses_the_stored_design():
    """Motorul real (pool 16, garanția 4, 100 de bilete) livrează designul stocat."""
    from loto_engine import LotoEngine

    pool = list(range(1, 17))
    scores = {n: float((n * 17) % 23) for n in pool}
    engine = LotoEngine("6/49")
    engine.hard_core = list(pool)
    engine.hard_core_joker = [7]
    lines, _ = engine.generate_predictions(4, 100, scores)
    main = [line[:6] for line in lines]
    expected, _ = generate_wheel("hitcover", pool, 6, 4, 100, scores, None, 6, 49)
    assert main == expected
    design = bd.load_budget_design(16, 6, 4, 100)
    if design is not None:
        from covering.common import _sorted_pool

        ordered = _sorted_pool(pool, scores)
        mapped = sorted(tuple(sorted(ordered[p - 1] for p in block)) for block in design)
        assert sorted(tuple(sorted(t)) for t in main) == mapped
        assert compute_coverage_pct(main, pool, 4) > 0
