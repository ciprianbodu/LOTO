"""Rafinarea locala a coverului cu plafon la pool > 19 (2026-10-31).

`covering.large_pool._refine_capped_cover` schimba cate un bilet pentru mai
multe grupe de g si g+1 acoperite la acelasi numar de bilete, pastrand
biletele distincte si pozitiile jucate. Determinista, fara scoruri/date, fara
apel la optimizatorii exponentiali (pool <= 20). Cheile WF/worker trec la
`lp2` peste 19.
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

from covering import large_pool as large
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities


def _covered(wheel, t):
    return {sub for ticket in wheel for sub in itertools.combinations(sorted(ticket), t)}


def _greedy_only(v, pick, g, cap, monkeypatch):
    """Coverul fara rafinare; monkeypatch-ul se anuleaza inainte de intoarcere."""
    monkeypatch.setattr(large, "_refine_capped_cover", lambda *a: a[-1])
    large._canonical_cover.cache_clear()
    try:
        return large._canonical_cover(v, pick, g, cap)
    finally:
        large._canonical_cover.cache_clear()
        monkeypatch.undo()


@pytest.mark.parametrize(
    "v,pick,g,cap",
    [(20, 6, 4, 152), (24, 6, 4, 152), (28, 6, 4, 404), (49, 6, 4, 152), (20, 5, 4, 404), (45, 5, 4, 404)],
)
def test_refinement_preserves_invariants_and_never_lowers_coverage(v, pick, g, cap, monkeypatch):
    base = _greedy_only(v, pick, g, cap, monkeypatch)
    refined = large._canonical_cover(v, pick, g, cap)
    assert len(refined) == cap == len(base)
    assert len(set(refined)) == cap
    played_base = set().union(*(set(t) for t in base))
    played_ref = set().union(*(set(t) for t in refined))
    assert played_base == played_ref
    # un swap poate inlocui grupe vechi cu mai multe noi: invariantul e pe
    # numarul de grupe acoperite (acoperirea raportata), nu pe superset
    n_g_base, n_g_ref = len(_covered(base, g)), len(_covered(refined, g))
    n_g1_base, n_g1_ref = len(_covered(base, g + 1)), len(_covered(refined, g + 1))
    assert n_g_ref >= n_g_base
    assert n_g1_ref >= n_g1_base
    # cand greedy-ul nu a atins impachetarea maxima, rafinarea castiga strict
    # (cazul cu castig strict e acoperit si de testul cu sanse exacte la pool 20)
    packing_max = min(cap * math.comb(pick, g), math.comb(v, g))
    if n_g_base < packing_max:
        assert (n_g_ref, n_g1_ref) > (n_g_base, n_g1_base)


def test_refinement_is_deterministic_and_leaves_global_rng_alone():
    large._canonical_cover.cache_clear()
    random.seed(1234)
    state = random.getstate()
    a = large._canonical_cover(24, 6, 4, 152)
    b = large._canonical_cover(24, 6, 4, 152)
    assert a == b
    assert random.getstate() == state


def test_refined_cover_raises_exact_hit_chances_at_pool_20(monkeypatch):
    """Pool 20 mai are sanse exacte in aplicatie: 4+ si 5+ pe bilet nu scad,
    acoperirea raportata creste."""
    v, pick, g, cap = 20, 6, 4, 152
    refined = large._canonical_cover(v, pick, g, cap)
    base = _greedy_only(v, pick, g, cap, monkeypatch)
    pool = list(range(1, v + 1))
    to_nums = lambda wheel: [sorted(p + 1 for p in t) for t in wheel]
    pb = wheel_hit_probabilities(pool, to_nums(base), 6, 49)["ticket"]
    pa = wheel_hit_probabilities(pool, to_nums(refined), 6, 49)["ticket"]
    for t in (3, 4, 5, 6):
        assert pa[t] >= pb[t] - 1e-15
    assert pa[4] > pb[4] + 1e-15 or pa[5] > pb[5] + 1e-15
    assert len(_covered(refined, 4)) > len(_covered(base, 4))


def test_full_cover_full_system_and_huge_caps_are_not_refined(monkeypatch):
    called = []

    def spy(v, pick, g, cap, tickets):
        called.append((v, pick, g, cap))
        return tuple(tickets)

    monkeypatch.setattr(large, "_refine_capped_cover", spy)
    large._canonical_cover.cache_clear()
    # garantie == pick: sistem complet, fara prag peste garantie
    large._canonical_cover(49, 6, 6, 10)
    # fara plafon: cover complet, nu se rafineaza
    large._canonical_cover(21, 6, 4, 0)
    # plafon peste limita de rafinare: se sare (rapid)
    large._canonical_cover(49, 6, 4, large._REFINE_MAX_CAP + 1)
    assert called == []


def test_generate_wheel_large_pool_reports_at_least_the_greedy_coverage(monkeypatch):
    pool = list(range(1, 50))
    scores = {n: -float(n) for n in pool}
    wheel, coverage = generate_wheel("hitcover", pool, 6, 4, 152, scores, max_num=49)
    base = _greedy_only(49, 6, 4, 152, monkeypatch)
    assert len(wheel) == 152
    exact = len(_covered(wheel, 4)) / math.comb(49, 4) * 100
    assert coverage == (min(99.99, round(exact, 2)) if exact < 100 else 100.0)
    assert len(_covered(wheel, 4)) >= len(_covered(base, 4))


def test_wheel_signature_marks_the_refined_large_pool_namespace():
    from loto_enterprise.core import walk_forward_adapter as wf

    assert wf._wheel_sig(49, "6/49", 4, None, 20).endswith("|lp2")
    assert "|lp2" not in wf._wheel_sig(19, "5/40", 4, None, 20)


def test_conditional_large_pool_cover_is_not_refined(monkeypatch):
    """Designul lotto „g dacă c” peste 19 păstrează construcția greedy: rafinarea
    optimizează acoperirea clasică, iar counts[c][g] nu e monotonie în ea."""
    called = []

    def spy(v, pick, g, cap, tickets):
        called.append(1)
        return tuple(tickets)

    monkeypatch.setattr(large, "_refine_capped_cover", spy)
    large._canonical_cover.cache_clear()
    large._canonical_cover(24, 6, 4, 152, 5)  # condition 5 > guarantee 4
    assert called == []
    large._canonical_cover(24, 6, 4, 152, 4)  # condition == guarantee: clasic
    assert called == [1]
