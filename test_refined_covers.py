"""Coverele complete rafinate (`covering_designs/refined/`): valide, complete,
certificate exact la încărcare, cu amprenta în cheia WF; fără ele nimic nu se
schimbă."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import covering.refined_covers as rc
from covering.anneal_hits import _certify
from covering.common import compute_coverage_pct
from covering.designs import complete_design_size
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities
from loto_enterprise.core import walk_forward_adapter as wf

FILES = sorted(Path("covering_designs/refined").glob("R_*_*_*_*_*.txt"))
GAMES = {(6, 49, 6): "6/49", (6, 40, 5): "5/40", (5, 45, 5): "joker"}


def _geometry(path: Path):
    _r, v, pick, g, draw, mx = path.stem.split("_")
    return int(v), int(pick), int(g), int(draw), int(mx)


def _pool(v):
    pool = list(range(1, v + 1))
    return pool, {n: float(v - i) for i, n in enumerate(pool)}


def test_refined_files_exist_for_the_measured_gain():
    assert FILES, "niciun cover rafinat în covering_designs/refined/"
    assert any(_geometry(p) == (13, 6, 3, 6, 49) for p in FILES)


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_every_refined_cover_is_complete_and_the_same_size(path: Path):
    v, pick, g, draw, mx = _geometry(path)
    assert (draw, mx, pick) in GAMES
    blocks = rc.load_refined_cover(v, pick, g, draw, mx)
    assert blocks is not None, path
    assert len(blocks) == complete_design_size(v, pick, g)
    assert len({tuple(b) for b in blocks}) == len(blocks)
    assert {p for b in blocks for p in b} == set(range(1, v + 1))
    assert compute_coverage_pct(blocks, list(range(1, v + 1)), g) == 100.0
    assert re.fullmatch(r"[0-9a-f]{12}", rc.refined_cover_signature(v, pick, g, draw, mx))


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_refined_cover_beats_the_la_jolla_cover_on_the_engine_path(path: Path, monkeypatch):
    """Fără plafon, cu universul jocului: certificat față de coverul La Jolla,
    pragul de peste garanție crește strict, niciun prag nu scade, garanția 100%."""
    v, pick, g, draw, mx = _geometry(path)
    pool, scores = _pool(v)
    monkeypatch.setattr(rc, "load_refined_cover", lambda *a, **k: None)
    before, cov0 = generate_wheel("lajolla", pool, pick, g, 0, scores, draw_n=draw, max_num=mx)
    monkeypatch.undo()
    after, cov1 = generate_wheel("lajolla", pool, pick, g, 0, scores, draw_n=draw, max_num=mx)
    assert cov0 == cov1 == 100.0
    assert len(after) == len(before) == len({tuple(sorted(t)) for t in after})
    assert {n for t in after for n in t} == set(pool)
    p0 = wheel_hit_probabilities(pool, before, draw, mx)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, draw, mx)["ticket"]
    assert p1[g + 1] > p0[g + 1]
    for t in range(3, min(pick, draw) + 1):
        assert p1[t] >= p0[t] - 1e-15
    assert _certify(pool, before, after, g, draw, mx)[0] == ""


def test_the_refined_cover_follows_positions_not_labels():
    pool_a, scores_a = _pool(13)
    pool_b = [3 * n + 1 for n in range(13)]
    scores_b = {pool_b[i]: scores_a[n] for i, n in enumerate(pool_a)}
    out_a, _ = generate_wheel("lajolla", pool_a, 6, 3, 0, scores_a, draw_n=6, max_num=49)
    out_b, _ = generate_wheel("lajolla", pool_b, 6, 3, 0, scores_b, draw_n=6, max_num=49)
    mapped = sorted(tuple(sorted(pool_b[pool_a.index(n)] for n in t)) for t in out_a)
    assert mapped == sorted(tuple(sorted(t)) for t in out_b)


def test_without_max_num_or_with_a_cap_nothing_changes(monkeypatch):
    calls = []
    real = rc.improve_with_refined_cover

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)

    import covering.dispatch as dispatch

    monkeypatch.setattr(dispatch, "improve_with_refined_cover", spy)
    pool, scores = _pool(13)
    generate_wheel("lajolla", pool, 6, 3, 0, scores, draw_n=6)  # fără univers
    generate_wheel("hitcover", pool, 6, 3, 10, scores, draw_n=6, max_num=49)  # cu plafon
    generate_wheel("greedy", pool, 6, 3, 0, scores, draw_n=6, max_num=49)  # referința explicită
    assert calls == []
    generate_wheel("lajolla", pool, 6, 3, 0, scores, draw_n=6, max_num=49)
    assert len(calls) == 1 and calls[0][2:] == (3, 6, 49)


def test_an_uncertified_or_missing_design_leaves_the_wheel_alone(monkeypatch):
    pool, scores = _pool(13)
    base, _ = generate_wheel("lajolla", pool, 6, 3, 0, scores)
    monkeypatch.setattr(rc, "load_refined_cover", lambda *a, **k: None)
    out, audit = rc.improve_with_refined_cover(pool, base, 3, 6, 49)
    assert out == base and audit["reason"] == "no refined cover"
    # Un design care nu mai acoperă garanția sau pierde la un prag nu trece.
    broken = [list(range(1, 7))] + [list(b) for b in base[1:]]
    monkeypatch.setattr(rc, "load_refined_cover", lambda *a, **k: [[pool.index(n) + 1 for n in t] for t in broken])
    out, audit = rc.improve_with_refined_cover(pool, base, 3, 6, 49)
    assert out == base and audit["reason"]
    assert rc.improve_with_refined_cover(pool, [], 3, 6, 49)[1]["reason"] == "no tickets"


def test_invalid_files_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_REFINED_DIRS", [tmp_path])
    rc._FILE_CACHE.clear()
    name = rc.refined_cover_path(7, 6, 3, 6, 49)
    (tmp_path / name).write_text("# test\n1 2 3 4 5 6\n1 2 3 4 5 6\n", encoding="utf-8")
    assert rc.load_refined_cover(7, 6, 3, 6, 49) is None  # bilete repetate, poziția 7 nejucată
    (tmp_path / name).write_text("1 2 3 4 5 6\n2 3 4 5 6 9\n", encoding="utf-8")
    rc._FILE_CACHE.clear()
    assert rc.load_refined_cover(7, 6, 3, 6, 49) is None  # poziție în afara pool-ului
    (tmp_path / name).write_text("1 2 3 4 5 6\n2 3 4 5 6 7\n", encoding="utf-8")
    rc._FILE_CACHE.clear()
    assert rc.load_refined_cover(7, 6, 3, 6, 49) == [[1, 2, 3, 4, 5, 6], [2, 3, 4, 5, 6, 7]]
    assert rc.refined_cover_signature(7, 6, 3, 6, 49)
    rc._FILE_CACHE.clear()


def test_walk_forward_key_carries_the_refined_fingerprint(monkeypatch):
    stored = rc.refined_cover_signature(13, 6, 3, 6, 49)
    assert stored
    key = wf._wheel_sig(13, "6/49", 3, None, 0)
    assert key.startswith("lajolla|") and key.endswith(f"|tg1|rc{stored}")
    size = complete_design_size(13, 6, 3)
    full = wf._wheel_sig(13, "6/49", 3, None, size)
    assert full.endswith(f"|bf1|rc{stored}")
    # Alt joc (alt univers) nu împrumută amprenta; nici o cale cu plafon.
    assert f"rc{stored}" not in wf._wheel_sig(13, "5/40", 3, None, 0)
    assert "|rc" not in wf._wheel_sig(13, "6/49", 3, None, 10)
    monkeypatch.setattr(rc, "refined_cover_signature", lambda *a, **k: None)
    assert wf._wheel_sig(13, "6/49", 3, None, 0).endswith("|tg1")


def test_engine_uncapped_path_delivers_the_refined_cover():
    from loto_engine import LotoEngine

    pool = [2, 5, 9, 11, 17, 20, 23, 28, 31, 36, 40, 44, 47]
    scores = {n: float((n * 17) % 23) + n / 100 for n in pool}
    engine = LotoEngine("6/49")
    engine.hard_core = list(pool)
    engine.hard_core_joker = [7]
    lines, _ = engine.generate_predictions(3, 0, scores)
    main = [line[:6] for line in lines]
    expected, _ = generate_wheel("lajolla", pool, 6, 3, 0, scores, None, 6, 49)
    assert sorted(map(tuple, map(sorted, main))) == sorted(map(tuple, map(sorted, expected)))
    from covering.common import _sorted_pool

    ordered = _sorted_pool(pool, scores)
    design = rc.load_refined_cover(13, 6, 3, 6, 49)
    mapped = sorted(tuple(sorted(ordered[p - 1] for p in block)) for block in design)
    assert sorted(tuple(sorted(t)) for t in main) == mapped
