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


def test_curated_plus4_has_mi_lag_bag_not_parity_balance():
    from loto_enterprise.benchmark.curated import load_curated, REQUIRED_METHODS
    from loto_enterprise.benchmark.methods import METHODS

    cur = load_curated()
    must = [
        "frequency", "random", "sum_affinity", "649_wilson_lb",
        "649_katz25_gap75_b", "649_decade_hot", "nmf_cooc", "649_mod7_hot",
        "graph_community_strength", "graph_neighbor_degree", "649_mom_10_40",
        "649_katz75_prime25", "ml_complement_nb", "649_last_neighbors",
        "fourier", "mi_lag_bag",
    ]
    assert all(m in cur for m in must)
    assert all(m in cur for m in REQUIRED_METHODS)
    assert "parity_balance" not in cur
    for gone in ("autocorr", "ml_nearest_centroid", "cover_positional_bands", "ml_logistic"):
        assert gone not in cur
    for added in ("ml_passive_aggressive", "graph_temporal_drift",
                  "graph_spectral_embed", "pca_resid_surprise"):
        assert added in cur
        assert added in METHODS
    assert len(cur) == 20
    assert all(m in METHODS for m in cur)
