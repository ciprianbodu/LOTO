"""parity_balance nu mai are voie să producă pool = cele mai MARI pare/impare.

Bug: scorul avea doar 2 nivele (clasa cerută vs cealaltă). rank_by_score la
egalitate alege numărul mare → top-12 pe 6/49 era 27,29,…,49 sau 28,30,…,48.

Fix: aceeași clasă + 0.01 * frecvență (ca prime_bias). Clasa rămâne axa
principală; APARTENENȚA în top-K nu mai e „cel mai mare număr din clasă".
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark.methods_classical import score_parity_balance
from loto_enterprise.core.ranking import rank_by_score

CSV_649 = Path("_ISTORIC/loto_6_49.csv")
CSV_JOKER = Path("_ISTORIC/joker.csv")


def _load(path: Path, cols: tuple[str, ...]) -> np.ndarray:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append([int(rec[c]) for c in cols])
    assert rows, f"{path} gol"
    return np.asarray(rows, dtype=np.int64)


def _largest_ap2(max_num: int, pool_n: int, odd: bool) -> list[int]:
    seq = list(range(max_num, 0, -1))
    picked = [n for n in seq if (n % 2 == 1) == odd][:pool_n]
    return sorted(picked)


def test_parity_balance_not_largest_odd_or_even_on_649():
    draws = _load(CSV_649, ("n1", "n2", "n3", "n4", "n5", "n6"))
    scores = score_parity_balance(draws, 49)
    assert scores
    assert all(np.isfinite(v) for v in scores.values())
    nuniq = len({round(v, 8) for v in scores.values()})
    assert nuniq > 2, f"încă 2 nivele: {nuniq}"
    pool = sorted(rank_by_score(scores, 12))
    assert pool != _largest_ap2(49, 12, odd=True), pool
    assert pool != _largest_ap2(49, 12, odd=False), pool


def test_parity_balance_not_largest_ap2_on_joker():
    draws = _load(CSV_JOKER, ("n1", "n2", "n3", "n4", "n5"))
    scores = score_parity_balance(draws, 45)
    pool = sorted(rank_by_score(scores, 11))
    assert pool != _largest_ap2(45, 11, odd=True), pool
    assert pool != _largest_ap2(45, 11, odd=False), pool


def test_curated_active_and_per_game():
    from loto_enterprise.benchmark.curated import (
        load_curated, load_per_game, REQUIRED_METHODS, apply_curation,
    )
    from loto_enterprise.benchmark.methods import METHODS

    cur = load_curated()
    assert len(cur) == 37
    assert all(m in METHODS for m in cur)
    assert all(m in cur for m in REQUIRED_METHODS)
    # +7 matematice CPU din testare externă WF 30% @ pool 11 (2026-08-25)
    added = {
        "pca_resid_surprise", "649_spectral_cooc", "cusum_appearance",
        "nmf_cooc", "fourier", "649_hazard_overdue", "pair_affinity",
    }
    assert added <= set(cur)
    pg = load_per_game()
    expect_n = {"loto_6_49": 13, "loto_5_40": 12, "joker_urna1": 12}
    expect_extra = {
        "loto_6_49": ["pca_resid_surprise", "649_spectral_cooc", "cusum_appearance"],
        "loto_5_40": ["nmf_cooc", "fourier"],
        "joker_urna1": ["649_hazard_overdue", "pair_affinity"],
    }
    rejected = {
        "circular_kernel", "649_sum_reversion", "649_mom_10_40",
        "mi_lag_bag", "bayes_poisson", "neg_binomial", "649_beta_mean",
        "649_wilson_lb",
    }
    for g, n in expect_n.items():
        assert g in pg
        assert len(pg[g]) == n
        assert all(m in cur for m in pg[g])
        assert "random" not in pg[g]
        for m in expect_extra[g]:
            assert m in pg[g]
        assert rejected.isdisjoint(pg[g])
    kept, info = apply_curation(list(METHODS))
    assert len(kept) == 37
    assert info["per_game"]["loto_6_49"] == 13
