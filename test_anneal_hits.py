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


# --------------------------------------------------------------------------- #
# „Bilet complet” cu garanția completă înainte de umplere (2026-10-12):
# recoacere pe toate variantele, acoperirea 100% ținută, pragul următor.
# --------------------------------------------------------------------------- #


def _filled(game, v, g, n_var, monkeypatch):
    """Variantele biletului fizic fără rearanjarea pe toate variantele."""
    from loto_enterprise.core import full_ticket as ft

    max_num, draw_n, pick = GEOMETRY[game]
    pool, scores = _pool(v)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ah, "improve_full_cover_variants", lambda *a, **k: (a[1], {"applied": False}))
        chosen, n_base = ft._fill_variants(pool, pick, g, n_var, scores, draw_n=draw_n, max_num=max_num)
    return pool, scores, [list(t) for t in chosen], n_base


@pytest.mark.parametrize(
    "game, v, g, n_var, min_rel",
    [("6/49", 10, 3, 15, 1.02), ("joker", 9, 3, 18, 1.02), ("6/49", 10, 4, 30, 1.02)],
)
def test_full_cover_variants_raise_the_next_threshold(game, v, g, n_var, min_rel, monkeypatch):
    """Garanția e la maximul hipergeometric și rămâne acolo; pragul următor
    crește certificat, fără scădere la vreun prag și cu acoperirea 100%."""
    from covering.common import _sorted_pool

    max_num, draw_n, pick = GEOMETRY[game]
    pool, scores, chosen, n_base = _filled(game, v, g, n_var, monkeypatch)
    assert n_base < n_var and compute_coverage_pct(chosen, pool, g) == 100.0
    out, audit = ah.improve_full_cover_variants(_sorted_pool(pool, scores), chosen, g, draw_n, max_num)
    assert audit["applied"], audit
    assert len(out) == len(chosen) == len({tuple(t) for t in out})
    assert {n for t in out for n in t} == set(pool)
    assert compute_coverage_pct(out, pool, g) == 100.0
    p0 = wheel_hit_probabilities(pool, chosen, draw_n, max_num)["ticket"]
    p1 = wheel_hit_probabilities(pool, out, draw_n, max_num)["ticket"]
    assert abs(p1[g] - p0[g]) < 1e-15
    assert p1[g + 1] > p0[g + 1] * min_rel
    for t in range(3, min(pick, draw_n) + 1):
        assert p1[t] >= p0[t] - 1e-15


def test_full_cover_stage_refuses_an_incomplete_guarantee_or_no_threshold_above():
    pool, scores = _pool(16)
    base, _ = generate_wheel("greedy", pool, 6, 3, 9, scores)
    out, audit = ah.improve_full_cover_variants(pool, base, 3, 6, 49)
    assert out == base and audit["reason"] == "guarantee incomplete"
    small, scores = _pool(8)
    system, _ = generate_wheel("greedy", small, 6, 6, 0, scores)  # sistemul complet
    out, audit = ah.improve_full_cover_variants(small, system, 6, 6, 49)
    assert out == system and audit["reason"] == "no threshold above the guarantee"
    joker, scores = _pool(7)
    system, _ = generate_wheel("greedy", joker, 5, 5, 0, scores)  # 21 de variante
    out, audit = ah.improve_full_cover_variants(joker, system, 5, 5, 45)
    assert out == system and audit["reason"] == "no threshold above the guarantee"
    # Joker cu garanția 4: pragul 5 există, dar la același număr de variante
    # distincte P(5 din 5) nu se mișcă, deci nu există design mai bun.
    joker, scores = _pool(9)
    cover, _ = generate_wheel("greedy", joker, 5, 4, 0, scores)
    out, audit = ah.improve_full_cover_variants(joker, cover, 4, 5, 45)
    assert out == cover and audit["reason"] == "no better design"
    assert ah.improve_full_cover_variants(pool, [], 3, 6, 49)[1]["reason"] == "no tickets"


def test_full_cover_search_follows_positions_and_is_cached(monkeypatch):
    pool_a, scores, chosen, _ = _filled("6/49", 10, 3, 15, monkeypatch)
    pool_b = [2, 5, 9, 14, 20, 27, 33, 38, 44, 49]
    chosen_b = [[pool_b[pool_a.index(n)] for n in t] for t in chosen]
    ah._anneal_full_cover.cache_clear()
    out_a, audit_a = ah.improve_full_cover_variants(pool_a, chosen, 3, 6, 49)
    out_b, audit_b = ah.improve_full_cover_variants(pool_b, chosen_b, 3, 6, 49)
    assert audit_a["applied"] and audit_a["gain"] == audit_b["gain"]
    assert [[pool_b[pool_a.index(n)] for n in t] for t in out_a] == out_b
    assert ah._anneal_full_cover.cache_info().misses == 1
    again, _ = ah.improve_full_cover_variants(pool_a, chosen, 3, 6, 49)
    assert again == out_a and ah._anneal_full_cover.cache_info().hits >= 1


@pytest.mark.parametrize(
    "game, v, g, n_var", [("6/49", 11, 3, 30), ("6/49", 9, 4, 18), ("joker", 8, 3, 12), ("5/40", 8, 4, 24)]
)
def test_full_cover_search_returns_only_designs_that_never_fall(game, v, g, n_var, monkeypatch):
    """Căutarea ține minte cel mai bun design CERTIFICAT: ce întoarce nu scade la
    niciun prag și nici la acoperire față de biletele de plecare."""
    max_num, draw_n, pick = GEOMETRY[game]
    pool, scores, chosen, _ = _filled(game, v, g, n_var, monkeypatch)
    index = {n: i for i, n in enumerate(pool)}
    start = tuple(sum(1 << index[n] for n in t) for t in chosen)
    found, _acc, _moves = ah._anneal_full_cover(v, pick, draw_n, max_num, g, start)
    candidate = [sorted(pool[p] for p in range(v) if (m >> p) & 1) for m in found]
    why, _gain, cov = ah._certify(pool, chosen, candidate, g, draw_n, max_num)
    assert why in {"", "no strict gain"}, why
    assert compute_coverage_pct(candidate, pool, g) == 100.0


def test_physical_ticket_runs_the_full_cover_stage_only_when_the_base_is_complete(monkeypatch):
    from loto_enterprise.core import full_ticket as ft
    from loto_enterprise.core.full_ticket import build_full_ticket

    calls = []
    real = ah.improve_full_cover_variants

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(ah, "improve_full_cover_variants", spy)
    pool, scores = _pool(10)
    audit = {}
    ft._canonical_full_cover.cache_clear()
    chosen, n_base = ft._fill_variants(pool, 6, 3, 15, scores, draw_n=6, max_num=49, audit=audit)
    # Variantele pasului și, o dată pe geometrie, designul canonic (2026-10-16).
    assert len(calls) == 2 and all(c[2:] == (3, 6, 49) for c in calls)
    assert audit["full_cover"]["applied"] and n_base == len(chosen) == 15
    calls.clear()
    ft._fill_variants(pool, 6, 3, 15, scores, draw_n=6, max_num=49)
    assert len(calls) == 1  # designul canonic e ținut minte
    calls.clear()
    big, scores16 = _pool(16)
    ft._fill_variants(big, 6, 3, 9, scores16, draw_n=6, max_num=49)
    assert calls == []  # baza nu e completă în 9 variante: calea recoacerii pe țintă
    ft._fill_variants(pool, 6, 3, 15, scores, draw_n=6)
    assert calls == []  # fără max_num, biletul rămâne cel de dinainte
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "greedy")
    ft._fill_variants(pool, 6, 3, 15, scores, draw_n=6, max_num=49)
    assert calls == []  # construcția de referință cerută explicit rămâne neatinsă
    monkeypatch.delenv("LOTO_WHEEL_METHOD")
    data = {"hard_core": pool, "guarantee": 3, "audit": {"timesfm_predictions": scores}}
    t = build_full_ticket("6/49", data, 5)
    assert t["refined"] and t["guarantee_variants"] == 15 == len(t["variants"])
    assert t["coverage"] == 100.0 and t["upper_coverage"][0] == 4
    assert t["chances"]["shown"][4] > 0.0104  # pool 10, 5 bilete: 0,9885% -> 1,0465%
