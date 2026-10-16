"""Pârghiile de hituri din auditul 2026-10-16, fiecare certificată la același cost.

- garanția 5 sau 6 cu peste 64 de bilete ia designul cu buget al garanției 4;
- garanția egală cu biletul, cu plafon, ia biletele unei garanții mai mici;
- ramura lotto „g dacă c” primește universul jocului: fără plafon designul
  rafinat, cu plafon wheel-ul clasic certificat.
Toate: același număr de bilete distincte, niciun prag 3..extrase în scădere.
"""

from __future__ import annotations

from math import comb
from pathlib import Path

import pytest

import covering.budget_designs as bd
import covering.dispatch as dispatch
import covering.refined_covers as rc
from covering.common import lotto_coverage_pct
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities, wheel_hit_profile
from loto_enterprise.core import walk_forward_adapter as wf

POOL = list(range(1, 17))
SCORES = {n: float(17 - n) for n in POOL}
GAMES = {"6/49": (49, 6, 6), "5/40": (40, 6, 5), "joker": (45, 5, 5)}


def _p(tickets, draw_n, max_num, pool=POOL):
    return wheel_hit_probabilities(pool, [sorted(t) for t in tickets], draw_n, max_num)["ticket"]


def _dominates(q, p, top):
    return all(q[t] >= p[t] - 1e-15 for t in range(3, top + 1)) and any(
        q[t] > p[t] + 1e-15 for t in range(3, top + 1)
    )


# --------------------------------------------------------------------------- #
# Garanția 5 și 6 peste 64 de bilete: designul cu buget al garanției 4.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("guarantee", [5, 6])
@pytest.mark.parametrize("budget", [65, 100, 120])
def test_high_guarantee_budget_takes_the_certified_g4_design(guarantee, budget, monkeypatch):
    stored = bd._designs(16, 6, 4).get(budget)
    assert stored
    after, _ = generate_wheel("hitcover", POOL, 6, guarantee, budget, SCORES, draw_n=6, max_num=49)
    monkeypatch.setattr(bd, "_stored", lambda *a, **k: None)
    monkeypatch.setattr(dispatch, "improve_with_lower_guarantees", lambda *a, **k: (a[1], {"applied": False}))
    before, _ = generate_wheel("hitcover", POOL, 6, guarantee, budget, SCORES, draw_n=6, max_num=49)
    assert len({tuple(sorted(t)) for t in after}) == budget == len(before)
    assert wheel_hit_profile(POOL, after)[guarantee][guarantee] >= wheel_hit_profile(POOL, before)[guarantee][guarantee]
    p0, p1 = _p(before, 6, 49), _p(after, 6, 49)
    assert _dominates(p1, p0, 6)
    assert p1[4] > p0[4] * 1.01
    assert sorted(map(tuple, map(sorted, after))) == sorted(tuple(sorted(b)) for b in stored[0])


def test_high_guarantee_walk_forward_key_carries_the_design_fingerprint():
    stored = bd.budget_design_signature(16, 6, 4, 100)
    assert stored and bd.budget_design_signature(16, 6, 5, 100) == stored
    assert f"|bd{stored}" in wf._wheel_sig(16, "6/49", 5, None, 100)
    assert f"|bd{stored}" in wf._wheel_sig(16, "6/49", 6, None, 100)
    assert "|bd" not in wf._wheel_sig(16, "6/49", 3, None, 30)


# --------------------------------------------------------------------------- #
# Garanția egală cu biletul, cu plafon.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("game,n", [("6/49", 30), ("6/49", 64), ("5/40", 50), ("5/40", 100), ("joker", 50), ("joker", 80)])
def test_guarantee_equal_to_pick_is_not_dominated_by_a_lower_guarantee(game, n):
    mx, dn, pick = GAMES[game]
    prod, _ = generate_wheel("hitcover", POOL, pick, pick, n, SCORES, draw_n=dn, max_num=mx)
    p, top = _p(prod, dn, mx), min(pick, dn)
    played = {x for t in prod for x in t}
    assert len({tuple(sorted(t)) for t in prod}) == n and played == set(POOL)
    for lower in range(3, pick):
        cand, _ = generate_wheel("hitcover", POOL, pick, lower, n, SCORES, draw_n=dn, max_num=mx)
        if len({tuple(sorted(t)) for t in cand}) != n or not played <= {x for t in cand for x in t}:
            continue
        assert not _dominates(_p(cand, dn, mx), p, top), f"g{lower} domină g{pick} la N={n}"


def test_guarantee_equal_to_pick_gains_against_the_old_construction(monkeypatch):
    after, _ = generate_wheel("hitcover", POOL, 5, 5, 100, SCORES, draw_n=6, max_num=40)
    monkeypatch.setattr(dispatch, "improve_with_lower_guarantees", lambda *a, **k: (a[1], {"applied": False}))
    before, _ = generate_wheel("hitcover", POOL, 5, 5, 100, SCORES, draw_n=6, max_num=40)
    p0, p1 = _p(before, 6, 40), _p(after, 6, 40)
    assert _dominates(p1, p0, 5) and p1[3] > p0[3] * 1.2  # 5/40 pool 16, 100 de bilete: 33,0% -> 45,6%


def test_more_tickets_no_longer_lower_the_chances_at_guarantee_six():
    a, _ = generate_wheel("hitcover", POOL, 6, 6, 64, SCORES, draw_n=6, max_num=49)
    b, _ = generate_wheel("hitcover", POOL, 6, 6, 65, SCORES, draw_n=6, max_num=49)
    pa, pb = _p(a, 6, 49), _p(b, 6, 49)
    assert all(pb[t] >= pa[t] for t in range(3, 7))


def test_physical_ticket_at_guarantee_equal_to_pick_uses_the_lower_guarantee():
    import covering.guarantee_portfolio as gp
    from loto_enterprise.core.full_ticket import build_full_ticket

    data = {"hard_core": POOL, "guarantee": 6, "audit": {"timesfm_predictions": SCORES}}
    after = build_full_ticket("6/49", data, 10, with_chances=False)
    real = gp.improve_with_lower_guarantees
    gp.improve_with_lower_guarantees = lambda *a, **k: (a[1], {"applied": False})
    try:
        before = build_full_ticket("6/49", data, 10, with_chances=False)
    finally:
        gp.improve_with_lower_guarantees = real
    p0 = _p([v[:6] for v in before["variants"]], 6, 49)
    p1 = _p([v[:6] for v in after["variants"]], 6, 49)
    assert len(after["variants"]) == len(before["variants"]) == 30
    assert _dominates(p1, p0, 6) and p1[3] > p0[3] * 1.05  # 24,10% -> 27,00%


def test_guarantee_portfolio_walk_forward_key():
    assert "|sa1|lg1:" in wf._wheel_sig(16, "6/49", 6, None, 30)
    assert "|sa1|lg1:" in wf._wheel_sig(16, "5/40", 6, None, 30)  # garanția tăiată la 5
    assert "|lg1:" in wf._wheel_sig(16, "6/49", 6, None, 100)
    assert "lg1" not in wf._wheel_sig(16, "6/49", 4, None, 30)
    assert "lg1" not in wf._wheel_sig(16, "6/49", 5, None, 30)


def test_guarantee_portfolio_key_signs_the_lower_guarantee_designs(monkeypatch):
    """Revizie 2026-10-16: `lg1` era constant, deși biletele vin din fișierele C,
    R și H ale garanțiilor mai mici (5/40 pool 12, g5, 113 bilete = C(12,5,4))."""
    before = wf._wheel_sig(12, "5/40", 5, None, 113)
    assert "|lg1:" in before
    monkeypatch.setattr(rc, "refined_cover_signature", lambda *a, **k: "altul")
    assert wf._wheel_sig(12, "5/40", 5, None, 113) != before
    monkeypatch.undo()
    with_h = wf._wheel_sig(16, "6/49", 6, None, 100)
    monkeypatch.setattr(bd, "budget_design_signature", lambda *a, **k: None)
    assert wf._wheel_sig(16, "6/49", 6, None, 100) != with_h


def test_capped_lotto_key_signs_the_classic_wheel_it_can_adopt(monkeypatch):
    """Revizie 2026-10-16: cu plafon, ramura lotto poate livra wheel-ul clasic,
    care la 65+ bilete citește secțiunea H; cheia `lc1` era constantă."""
    key = wf._wheel_sig(16, "6/49", 5, 6, 100)
    assert "|lc1:" in key
    monkeypatch.setattr(bd, "budget_design_signature", lambda *a, **k: None)
    assert wf._wheel_sig(16, "6/49", 5, 6, 100) != key


def test_lotto_cap_holding_the_whole_design_gets_the_refined_design():
    """Revizie 2026-10-16: un plafon cât designul întreg (6/49 pool 16, 3 dacă 5:
    10 bilete) livra designul nerafinat, cu șanse mai mici decât fără plafon."""
    free, free_cov = generate_wheel("lotto", POOL, 6, 3, 0, SCORES, condition=5, draw_n=6, max_num=49)
    for cap in (10, 20, 64):
        capped, cov = generate_wheel("lotto", POOL, 6, 3, cap, SCORES, condition=5, draw_n=6, max_num=49)
        assert sorted(map(tuple, capped)) == sorted(map(tuple, free)) and cov == free_cov == 100.0
    assert _p(free, 6, 49)[3] > 0.145


# --------------------------------------------------------------------------- #
# Ramura lotto „g dacă c”.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("g,c,budget", [(5, 6, 64), (4, 5, 20), (4, 6, 10)])
def test_capped_lotto_is_not_dominated_by_the_classic_engine_path(g, c, budget):
    lotto, _ = generate_wheel("lotto", POOL, 6, g, budget, SCORES, condition=c, draw_n=6, max_num=49)
    classic, _ = generate_wheel("hitcover", POOL, 6, g, budget, SCORES, draw_n=6, max_num=49)
    lotto = [sorted(t[:6]) for t in lotto]
    classic = [sorted(t[:6]) for t in classic]
    assert len({tuple(t) for t in lotto}) == budget
    p_l, p_c = _p(lotto, 6, 49), _p(classic, 6, 49)
    cov_l = wheel_hit_profile(POOL, lotto)[c][g]
    cov_c = wheel_hit_profile(POOL, classic)[c][g]
    assert not (_dominates(p_c, p_l, 6) and cov_c >= cov_l)


def test_capped_lotto_never_loses_against_the_truncated_design():
    for game, g, c, budget in [("6/49", 5, 6, 64), ("5/40", 4, 5, 64), ("joker", 3, 4, 20)]:
        mx, dn, pick = GAMES[game]
        old, old_cov = generate_wheel("lotto", POOL, pick, g, budget, SCORES, condition=c, draw_n=dn)
        new, new_cov = generate_wheel("lotto", POOL, pick, g, budget, SCORES, condition=c, draw_n=dn, max_num=mx)
        p0, p1 = _p(old, dn, mx), _p(new, dn, mx)
        assert len(new) == len(old)
        assert all(p1[t] >= p0[t] - 1e-15 for t in range(3, min(pick, dn) + 1))
        assert new_cov >= old_cov


LOTTO_FILES = sorted(Path("covering_designs/refined").glob("RL_*.txt"))


def _lotto_geometry(path: Path):
    _r, v, pick, g, c, draw, mx = path.stem.split("_")
    return int(v), int(pick), int(g), int(c), int(draw), int(mx)


def test_refined_lotto_files_exist():
    assert len(LOTTO_FILES) >= 60
    assert any(_lotto_geometry(p) == (16, 6, 3, 5, 6, 49) for p in LOTTO_FILES)


@pytest.mark.parametrize("path", LOTTO_FILES, ids=[p.name for p in LOTTO_FILES])
def test_every_refined_lotto_design_keeps_the_guarantee_and_beats_production(path, monkeypatch):
    v, pick, g, c, draw, mx = _lotto_geometry(path)
    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    blocks = rc.load_refined_cover(v, pick, g, draw, mx, condition=c)
    assert blocks is not None
    assert wheel_hit_profile(pool, blocks)[c][g] == comb(v, c)
    after, cov = generate_wheel("lotto", pool, pick, g, 0, scores, condition=c, draw_n=draw, max_num=mx)
    monkeypatch.setattr(rc, "load_refined_cover", lambda *a, **k: None)
    before, _ = generate_wheel("lotto", pool, pick, g, 0, scores, condition=c, draw_n=draw, max_num=mx)
    assert cov == lotto_coverage_pct(after, pool, g, c) == 100.0
    assert len(after) == len(before) == len({tuple(sorted(t)) for t in after})
    assert _dominates(_p(after, draw, mx, pool), _p(before, draw, mx, pool), min(pick, draw))


def test_refined_lotto_walk_forward_key_and_engine_path():
    from loto_engine import LotoEngine

    stored = rc.refined_cover_signature(16, 6, 3, 6, 49, condition=5)
    assert stored
    assert wf._wheel_sig(16, "6/49", 3, 5, 0).endswith(f"|rl{stored}")
    on_540 = rc.refined_cover_signature(16, 5, 3, 6, 40, condition=5)
    assert on_540 and on_540 != stored
    assert wf._wheel_sig(16, "5/40", 3, 5, 0).endswith(f"|rl{on_540}")
    assert f"rl{stored}" not in wf._wheel_sig(16, "5/40", 3, 5, 0)
    engine = LotoEngine("6/49")
    engine.hard_core = list(POOL)
    lines, cov = engine.generate_predictions(3, 0, SCORES, condition=5)
    expected, _ = generate_wheel("lotto", POOL, 6, 3, 0, SCORES, condition=5, draw_n=6, max_num=49)
    assert [t[:6] for t in lines] == [list(t) for t in expected] and cov == 100.0
    p = _p([t[:6] for t in lines], 6, 49)
    assert p[3] > 0.145  # 12,94% -> 14,58% la 10 bilete
