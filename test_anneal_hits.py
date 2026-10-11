"""Recoacerea pe șansa exactă (`covering.anneal_hits`): intră în producție numai
certificată, e deterministă și nu afirmă nimic despre pool."""

from __future__ import annotations

import pytest

import covering.anneal_hits as ah
import covering.dispatch as dispatch
from covering.common import compute_coverage_pct
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities
from loto_enterprise.core import walk_forward_adapter as wf

GEOMETRY = {"6/49": (49, 6, 6), "5/40": (40, 6, 5), "joker": (45, 5, 5)}


def _pool(v):
    pool = list(range(1, v + 1))
    return pool, {n: float(v - i) for i, n in enumerate(pool)}


def _without_anneal(monkeypatch, *args, **kwargs):
    monkeypatch.setattr(dispatch, "improve_ticket_chances", lambda *a, **k: (a[1], {"applied": False}))
    try:
        return generate_wheel(*args, **kwargs)
    finally:
        monkeypatch.setattr(dispatch, "improve_ticket_chances", ah.improve_ticket_chances)


@pytest.mark.parametrize("game, v, g, B", [("6/49", 16, 3, 3), ("joker", 14, 3, 4)])
def test_certified_gain_on_known_geometries(game, v, g, B, monkeypatch):
    """Două geometrii la care recoacerea găsește bilete mai bune la 3+ fără
    scădere la 4+/5+: mai multe hituri pe bilete din același pool, la același cost."""
    max_num, draw_n, pick = GEOMETRY[game]
    pool, scores = _pool(v)
    before, cov0 = _without_anneal(monkeypatch, "hitcover", pool, pick, g, B, scores, draw_n=draw_n, max_num=max_num)
    after, cov1 = generate_wheel("hitcover", pool, pick, g, B, scores, draw_n=draw_n, max_num=max_num)
    assert len(after) == len(before) == B and len({tuple(t) for t in after}) == B
    p0 = wheel_hit_probabilities(pool, before, draw_n, max_num)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, draw_n, max_num)["ticket"]
    assert p1[3] > p0[3] * 1.005
    for t in range(3, min(pick, draw_n) + 1):
        assert p1[t] >= p0[t] - 1e-15
    assert cov1 >= cov0


def test_result_is_deterministic_and_cached():
    pool, scores = _pool(16)
    base, _ = generate_wheel("greedy", pool, 6, 3, 9, scores)
    ah._anneal.cache_clear()
    first, audit1 = ah.improve_ticket_chances(pool, base, 3, 6, 49)
    second, audit2 = ah.improve_ticket_chances(pool, base, 3, 6, 49)
    assert first == second and audit1 == audit2
    assert ah._anneal.cache_info().hits >= 1


def test_the_search_follows_positions_not_labels():
    """Același wheel pe alt pool (alte etichete, aceeași ordine) dă aceleași poziții."""
    pool_a, _ = _pool(12)
    pool_b = [3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47]
    base_a, _ = generate_wheel("greedy", pool_a, 6, 3, 7, {n: float(12 - i) for i, n in enumerate(pool_a)})
    base_b = [[pool_b[pool_a.index(n)] for n in t] for t in base_a]
    out_a, _ = ah.improve_ticket_chances(pool_a, base_a, 3, 6, 49)
    out_b, _ = ah.improve_ticket_chances(pool_b, base_b, 3, 6, 49)
    assert [[pool_b[pool_a.index(n)] for n in t] for t in out_a] == out_b


def test_a_worse_candidate_is_rejected(monkeypatch):
    """Certificarea exactă nu se bazează pe căutare: un candidat care pierde la un
    prag, sau lasă un număr jucat fără bilet, nu trece."""
    pool, scores = _pool(10)
    base, _ = generate_wheel("greedy", pool, 6, 3, 7, scores)
    index = {n: i for i, n in enumerate(pool)}
    masks = tuple(sum(1 << index[n] for n in t) for t in base)
    # Toate biletele pe primele 7 numere: 8, 9, 10 nu mai sunt jucate.
    squeezed = tuple((m & 0b1111111) | (1 << i) for i, m in enumerate(masks))
    monkeypatch.setattr(ah, "_anneal", lambda *a: (squeezed, 1, 1))
    out, audit = ah.improve_ticket_chances(pool, base, 3, 6, 49)
    assert out == base and not audit["applied"]
    assert audit["reason"] in {"pool number dropped", "duplicate tickets", "chance falls at a threshold"}
    monkeypatch.setattr(ah, "_anneal", lambda *a: (a[5], 0, 0))
    out, audit = ah.improve_ticket_chances(pool, base, 3, 6, 49)
    assert out == base and audit["reason"] == "no better design"


def test_outside_limits_leaves_the_wheel_alone():
    pool, scores = _pool(16)
    base, _ = generate_wheel("greedy", pool, 6, 4, 70, scores)
    assert len(base) == 70
    out, audit = ah.improve_ticket_chances(pool, base, 4, 6, 49)
    assert out == base and audit["reason"] == "outside limits"
    assert ah.improve_ticket_chances(pool, [], 3, 6, 49)[1]["reason"] == "no tickets"


def test_engine_path_applies_only_with_max_num_and_a_budget(monkeypatch):
    calls = []

    def spy(*a, **k):
        calls.append(a)
        return a[1], {"applied": False}

    monkeypatch.setattr(dispatch, "improve_ticket_chances", spy)
    pool, scores = _pool(12)
    generate_wheel("hitcover", pool, 6, 3, 7, scores, draw_n=6)  # fără max_num: bilete fizice
    assert calls == []
    generate_wheel("hitcover", pool, 6, 3, 0, scores, draw_n=6, max_num=49)  # fără plafon
    assert calls == []
    generate_wheel("hitcover", pool, 6, 3, 7, scores, draw_n=6, max_num=49)
    assert len(calls) == 1 and calls[0][2:] == (3, 6, 49)


def test_physical_ticket_uses_the_stage_only_below_a_complete_guarantee(monkeypatch):
    from loto_enterprise.core import full_ticket as ft

    calls = []

    def spy(*a, **k):
        calls.append(a)
        return a[1], {"applied": False}

    monkeypatch.setattr(ah, "improve_ticket_chances", spy)
    pool, scores = _pool(16)
    ft._fill_variants(pool, 6, 3, 9, scores, draw_n=6, max_num=49)
    assert len(calls) == 1 and calls[0][2:] == (3, 6, 49)
    calls.clear()
    small, _ = _pool(8)
    ft._fill_variants(small, 6, 3, 9, {n: float(8 - i) for i, n in enumerate(small)}, draw_n=6, max_num=49)
    assert calls == []  # C(8,6,3) are 4 bilete: garanția e completă, locurile rămase vin din g+1
    ft._fill_variants(pool, 6, 3, 9, scores, draw_n=6)
    assert calls == []  # fără max_num, biletul rămâne cel de dinainte
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "greedy")
    ft._fill_variants(pool, 6, 3, 9, scores, draw_n=6, max_num=49)
    assert calls == []  # construcția de referință cerută explicit rămâne neatinsă


def test_wf_key_marks_budgets_the_stage_can_touch():
    assert wf._wheel_sig(16, "6/49", 3, None, 9).endswith("|sa1")
    assert wf._wheel_sig(16, "6/49", 4, None, 64).endswith("|sa1")
    assert "sa1" not in wf._wheel_sig(16, "6/49", 4, None, 65)
    assert "sa1" not in wf._wheel_sig(16, "6/49", 3, None, 0)
    assert "sa1" not in wf._wheel_sig(10, "6/49", 3, None, 12)  # bugetul ia designul complet


def test_coverage_of_the_guarantee_never_falls():
    pool, scores = _pool(13)
    for B in (7, 12):
        before, cov0 = generate_wheel("greedy", pool, 6, 3, B, scores)
        after, audit = ah.improve_ticket_chances(pool, before, 3, 6, 49)
        assert compute_coverage_pct(after, pool, 3) >= compute_coverage_pct(before, pool, 3) - 1e-9
