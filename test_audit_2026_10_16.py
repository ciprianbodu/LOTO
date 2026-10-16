"""Constatările mărunte din auditul 2026-10-16, fiecare cu testul care pica înainte."""

from __future__ import annotations

import covering.dispatch as dispatch
from covering.common import _order_by_scores, _sorted_pool
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities
from loto_enterprise.core.full_ticket import _pool_scores, build_full_ticket

POOL = list(range(1, 17))
SCORES = {n: 1.0 + ((n * 7) % 16) / 10.0 for n in POOL}


def _sum(ticket) -> float:
    return sum(SCORES[n] for n in ticket)


# --------------------------------------------------------------------------- #
# Motorul livrează biletele în ordinea scorului, după orice etapă.
# --------------------------------------------------------------------------- #


def test_engine_wheel_is_ordered_by_score_after_every_stage():
    # 6/49 pool 16, garanția 4, 100 de bilete: designul cu buget își întorcea
    # biletele în ordinea proprie, iar „Top 10 bilete simple” nu erau primele 10.
    wheel, cov = generate_wheel("hitcover", POOL, 6, 4, 100, SCORES, draw_n=6, max_num=49)
    raw, raw_cov = dispatch._generate_wheel(
        "hitcover", POOL, 6, 4, 100, SCORES, draw_n=6, max_num=49
    )
    assert cov == raw_cov
    assert sorted(map(tuple, wheel)) == sorted(tuple(sorted(t)) for t in raw)
    assert wheel == _order_by_scores(raw, SCORES)
    sums = [_sum(t) for t in wheel]
    assert sums == sorted(sums, reverse=True)
    assert [_sum(sorted(t)) for t in raw] != sums  # înainte: altă ordine


def test_physical_ticket_path_keeps_its_own_order():
    # Fără `max_num` (biletele fizice), baza garanției rămâne în primele variante.
    assert generate_wheel("hitcover", POOL, 6, 4, 10, SCORES) == dispatch._generate_wheel(
        "hitcover", POOL, 6, 4, 10, SCORES
    )


# --------------------------------------------------------------------------- #
# Cheia worker-ului semnează designul pool-ului jucat.
# --------------------------------------------------------------------------- #


def test_worker_key_signs_the_design_of_the_restricted_pool(monkeypatch):
    import loto_enterprise.core.walk_forward_adapter as wf
    import worker

    seen = []
    real = wf._wheel_sig

    def spy(pool_size, *args, **kwargs):
        seen.append(int(pool_size))
        return real(pool_size, *args, **kwargs)

    monkeypatch.setattr(wf, "_wheel_sig", spy)
    task = {"game_label": "6/49", "pool_size": 16, "guarantee": 4, "max_variants": 0}
    worker._pipeline_cache_key("in", {"datasets": [{"tasks": [dict(task)]}]})
    worker._pipeline_cache_key(
        "in",
        {"datasets": [{"tasks": [dict(task, restrict_base_min=1, restrict_base_max=14)]}]},
    )
    # Interval 1..14: motorul joacă C(14,6,4), deci cheia trebuie să-l semneze.
    assert seen == [16, 14]


# --------------------------------------------------------------------------- #
# „Bilet complet”: scorurile rotunjite egale nu inversează clasamentul.
# --------------------------------------------------------------------------- #


def test_rounded_ties_keep_the_audit_order_in_the_design():
    # Joker, cusum_burst: 18 (locul 9) și 38 (locul 10) aveau după rotunjire
    # același scor, iar `_sorted_pool` punea 38 înaintea lui 18.
    order = [14, 1, 45, 31, 10, 43, 30, 25, 18, 38, 5, 37, 22, 15, 17, 24]
    exact = {n: 1.0 - i * 0.01 for i, n in enumerate(order)}
    rounded = dict(exact)
    rounded[38] = rounded[18]
    rounded[37] = rounded[5]
    data = {"hard_core": order, "guarantee": 4, "audit": {"timesfm_predictions": rounded}}
    scores = _pool_scores(data)
    assert list(scores) == order
    assert _sorted_pool(order, scores) == order
    values = list(scores.values())
    assert all(a > b for a, b in zip(values, values[1:]))
    assert scores[18] == rounded[18] and scores[38] < scores[18]

    ref = dict(data, audit={"timesfm_predictions": exact})
    tied = build_full_ticket("joker", data, 10, with_chances=False)
    clean = build_full_ticket("joker", ref, 10, with_chances=False)
    assert tied["variants"] == clean["variants"]
    shown = [v[:5] for v in tied["variants"]]
    assert sum(18 in v for v in shown) >= sum(38 in v for v in shown)
    p = wheel_hit_probabilities(order, shown, 5, 45)["ticket"]
    assert p[3] > 0


# --------------------------------------------------------------------------- #
# „Bilet complet” cu garanția completă: designul canonic e al doilea candidat.
# --------------------------------------------------------------------------- #


def test_full_cover_keeps_the_better_of_the_step_and_canonical_designs(monkeypatch):
    import random

    import loto_enterprise.core.full_ticket as ft

    gains = 0
    for seed in range(6):
        rng = random.Random(seed)
        nums = rng.sample(range(1, 50), 25)
        scores = {x: 1.0 - k * 0.013 + rng.random() * 1e-3 for k, x in enumerate(nums)}
        scores = dict(sorted(scores.items(), key=lambda kv: -kv[1]))
        data = {"hard_core": list(scores)[:10], "guarantee": 3, "audit": {"timesfm_predictions": scores}}
        both = build_full_ticket("6/49", data, 6, with_chances=False)
        with monkeypatch.context() as m:
            m.setattr(ft, "_full_cover_from_canonical", lambda r, chosen, *a, **k: (chosen, {"applied": False}))
            step = build_full_ticket("6/49", data, 6, with_chances=False)
        assert both["refined"] and step["refined"] and both["coverage"] == 100.0
        pool = both["pool"]
        pa = wheel_hit_probabilities(pool, [v[:6] for v in both["variants"]], 6, 49)["ticket"]
        pb = wheel_hit_probabilities(pool, [v[:6] for v in step["variants"]], 6, 49)["ticket"]
        # Ambele sunt certificate față de variantele pasului; rămâne cel mai bun
        # la pragul de peste garanție.
        assert pa[3] == pb[3] and pa[4] >= pb[4] - 1e-15
        gains += pa[4] > pb[4] + 1e-15
    assert gains >= 1  # pe cel puțin un pas designul canonic e mai bun
