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
    assert len(cur) == 44
    assert "draw_shape_reversion" in cur
    assert all(m in METHODS for m in cur)
    assert all(m in cur for m in REQUIRED_METHODS)
    # Rebuild TOP 20/joc din 97 metode CPU math (2026-08-25 r3).
    added = {
        "pca_resid_surprise", "649_spectral_cooc", "cusum_appearance",
        "nmf_cooc", "fourier", "649_hazard_overdue", "pair_affinity",
        "parity_balance", "graph_clustering", "prime_bias",
        "649_katz15_beta85", "graph_eigenvector", "mi_lag_bag",
        "neg_binomial", "graph_anti_community", "649_rrf_graph",
        "649_mom_20_80", "graph_personalized_pr",
    }
    assert added <= set(cur)
    pg = load_per_game()
    expect_n = {"loto_6_49": 20, "loto_5_40": 20, "joker_urna1": 20}
    expect_extra = {
        "loto_6_49": [
            "pair_affinity", "649_katz15_beta85", "649_rank_borda",
            "pca_resid_surprise", "mi_lag_bag", "neg_binomial",
        ],
        "loto_5_40": [
            "graph_anti_community", "autocorr", "649_rrf_graph",
            "frequency", "graph_rwr_recent", "parity_balance",
        ],
        "joker_urna1": [
            "649_mom_20_80", "649_katz15_beta85", "graph_anti_community",
            "cusum_appearance", "prime_bias", "nmf_cooc",
        ],
    }
    rejected = {
        "circular_kernel", "649_sum_reversion", "649_mom_10_40",
        "bayes_poisson", "649_beta_mean", "649_wilson_lb",
        "649_last_neighbors", "649_decade_hot",
        "ml_decision_tree", "ml_nearest_centroid", "ml_passive_aggressive",
        "ml_lda",
    }
    for g, n in expect_n.items():
        assert g in pg
        assert len(pg[g]) == n
        assert all(m in cur for m in pg[g])
        assert "random" not in pg[g]
        for m in expect_extra[g]:
            assert m in pg[g]
        assert rejected.isdisjoint(pg[g])
    # frequency e REQUIRED în active; pe Joker e clonă Katz → nu e în per_game
    assert "frequency" not in pg["joker_urna1"]
    kept, info = apply_curation(list(METHODS))
    assert len(kept) == 44
    assert info["per_game"]["loto_6_49"] == 20
    assert info["per_game"]["loto_5_40"] == 20
    assert info["per_game"]["joker_urna1"] == 20
