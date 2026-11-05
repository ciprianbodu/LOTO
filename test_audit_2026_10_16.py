"""Constatările mărunte din auditul 2026-10-16, fiecare cu testul care pica înainte."""

from __future__ import annotations

import covering.dispatch as dispatch
from covering.common import _order_by_scores
from covering.dispatch import generate_wheel

POOL = list(range(1, 17))
SCORES = {n: 1.0 + ((n * 7) % 16) / 10.0 for n in POOL}


def _sum(ticket) -> float:
    return sum(SCORES[n] for n in ticket)


# --------------------------------------------------------------------------- #
# Motorul livrează biletele în ordinea scorului, după orice etapă.
# --------------------------------------------------------------------------- #


def test_engine_wheel_is_ordered_by_score_after_every_stage():
    # 6/49 pool 16, garanția 4, 100 de bilete: designul cu buget își întorcea
    # biletele în ordinea proprie în locul ordinii scorului.
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


def test_wheel_without_universe_keeps_its_construction_order():
    # Fără `max_num`, constructorul păstrează ordinea proprie a variantelor.
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
