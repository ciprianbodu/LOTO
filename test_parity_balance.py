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
        load_curated,
        load_per_game,
        REQUIRED_METHODS,
        apply_curation,
    )
    from loto_enterprise.benchmark.methods import METHODS

    cur = load_curated()
    assert len(cur) == 52
    assert all(m in METHODS for m in cur)
    assert all(m in cur for m in REQUIRED_METHODS)
    assert "parity_balance" not in cur
    assert "649_parity_recent" not in cur
    assert "prime_bias" not in cur
    assert "649_mod7_hot" not in cur
    assert "649_mod10_hot" not in cur
    assert "649_decade_hot" not in cur
    assert "649_sum_reversion" not in cur
    assert "649_last_neighbors" not in cur
    # Rebuild TOP per joc din metodele CPU + selecția top-1 pentru Urna 2
    # (2026-09-01). Urna 2 nu se umple artificial până la 20; filtrele de
    # clasă (decade/mod7/parity/prime) au fost scoase ulterior.
    added = {
        "pca_resid_surprise",
        "649_spectral_cooc",
        "cusum_appearance",
        "fourier",
        "pair_affinity",
        "dmd",
        "649_gap_sqrt",
        "graph_clustering",
        "649_katz15_beta85",
        "graph_eigenvector",
        "mi_lag_bag",
        "graph_anti_community",
        "649_rrf_graph",
        "649_mom_20_80",
        "graph_personalized_pr",
        "ml_knn_5",
        "circular_kernel",
        "649_katz12_gap88",
        "bayes_poisson",
    }
    assert added <= set(cur)
    pg = load_per_game()
    expect_n = {
        "loto_6_49": 19,
        "loto_5_40": 19,
        "joker_urna1": 17,
        "joker_urna2": 13,
    }
    expect_extra = {
        "loto_6_49": [
            "649_rank_borda",
            "649_katz25_gap75_b",
            "dmd",
            "pair_affinity",
            "cover_diversity_mmr",
            "cover_adaptive_blend",
        ],
        "loto_5_40": [
            "649_rank_borda",
            "649_rrf_graph",
            "autocorr",
            "graph_anti_community",
            "649_gap_sqrt",
            "weighted_recent",
            "ssa",
        ],
        "joker_urna1": [
            "dmd",
            "frequency",
            "649_mom_20_80",
            "cusum_appearance",
            "modular",
            "cover_complement",
        ],
        "joker_urna2": [
            "circular_kernel",
            "ml_knn_5",
            "autocorr",
            "bayes_poisson",
        ],
    }
    for g, n in expect_n.items():
        assert g in pg
        assert len(pg[g]) == n
        assert all(m in cur for m in pg[g])
        assert "random" not in pg[g]
        for m in expect_extra[g]:
            assert m in pg[g]
        assert "ml_decision_tree" not in pg[g]
        assert "ml_nearest_centroid" not in pg[g]
        assert "parity_balance" not in pg[g]
        assert "649_parity_recent" not in pg[g]
        assert "prime_bias" not in pg[g]
        assert "649_mod7_hot" not in pg[g]
        assert "649_mod10_hot" not in pg[g]
        assert "649_decade_hot" not in pg[g]
        assert "649_sum_reversion" not in pg[g]
        assert "649_last_neighbors" not in pg[g]
    # frequency rămâne fallback structural și a trecut gate-ul extern pe Joker.
    assert "frequency" in pg["joker_urna1"]
    kept, info = apply_curation(list(METHODS))
    assert len(kept) == 52
    assert info["per_game"]["loto_6_49"] == 19
    assert info["per_game"]["loto_5_40"] == 19
    assert info["per_game"]["joker_urna1"] == 17
    assert info["per_game"]["joker_urna2"] == 13


def test_parity_class_filters_are_not_production_scorers():
    from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
    from loto_enterprise.core.method_selector import _production_forbidden

    class_filters = {
        "parity_balance",
        "649_parity_recent",
        "prime_bias",
        "649_mod7_hot",
        "649_mod10_hot",
        "649_decade_hot",
        "649_sum_reversion",
        "649_last_neighbors",
    }
    assert class_filters <= EXCLUDED_FROM_PRODUCTION
    forbidden = _production_forbidden()
    assert class_filters <= forbidden
    assert "random" in forbidden
    assert "frequency" not in forbidden
    assert "modular" not in forbidden
    assert "circular_kernel" not in forbidden


def test_class_filters_collapse_pool_on_649():
    """Garda empirică: aceste scorere umplu o clasă, nu un pool amestecat."""
    from loto_enterprise.benchmark.methods import call_method

    draws = _load(CSV_649, ("n1", "n2", "n3", "n4", "n5", "n6"))
    primes = set()
    for n in range(2, 50):
        if all(n % d for d in range(2, int(n**0.5) + 1)):
            primes.add(n)

    def consec_run(pool):
        s = sorted(pool)
        best = cur = 1
        for a, b in zip(s, s[1:]):
            if b == a + 1:
                cur += 1
                best = max(best, cur)
            else:
                cur = 1
        return best

    scores, _ = call_method("prime_bias", draws, 49)
    pool = rank_by_score(scores, 16)
    nprime = sum(1 for n in pool if n in primes)
    assert nprime in (0, 16), pool

    scores, _ = call_method("649_decade_hot", draws, 49)
    pool = rank_by_score(scores, 11)
    assert consec_run(pool) >= 10, sorted(pool)

    scores, _ = call_method("649_sum_reversion", draws, 49)
    pool = rank_by_score(scores, 11)
    assert consec_run(pool) == 11, sorted(pool)

    scores, _ = call_method("649_mod7_hot", draws, 49)
    nuniq = len({round(v, 8) for v in scores.values()})
    assert nuniq <= 7

    joker = _load(CSV_JOKER, ("joker",))
    scores, _ = call_method("649_last_neighbors", joker, 20)
    nuniq = len({round(v, 8) for v in scores.values()})
    assert nuniq <= 3
    last = int(joker[-1, 0])
    neighbors = {
        b
        for b in range(max(1, last - 3), min(20, last + 3) + 1)
        if b != last
    }
    pool = set(rank_by_score(scores, 1))
    assert pool <= neighbors, (pool, neighbors, last)
