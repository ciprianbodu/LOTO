"""Gărzi pentru metodele adăugate din testarea externă WF."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark.curated import load_per_game
from loto_enterprise.benchmark.methods import METHODS, call_method
from loto_enterprise.core.ranking import is_consecutive_block, rank_by_score

GAMES = {
    "loto_6_49": (Path("_ISTORIC/loto_6_49.csv"), ("n1", "n2", "n3", "n4", "n5", "n6"), 49, 11),
    "loto_5_40": (Path("_ISTORIC/loto_5_40.csv"), ("n1", "n2", "n3", "n4", "n5"), 40, 11),
    "joker_urna1": (Path("_ISTORIC/joker.csv"), ("n1", "n2", "n3", "n4", "n5"), 45, 11),
    "joker_urna2": (Path("_ISTORIC/joker.csv"), ("joker",), 20, 1),
}

ADDED = (
    # runda 1 (2026-08-25 dimineață)
    "pca_resid_surprise",
    "649_spectral_cooc",
    "cusum_appearance",
    "nmf_cooc",
    "fourier",
    "649_hazard_overdue",
    "pair_affinity",
    # runda 2 (20/joc) — doar cele NOI în active
    "parity_balance",
    "graph_clustering",
    "prime_bias",
    # runda 3 (rebuild TOP 20 din 97 CPU math) — noi în active
    "649_katz15_beta85",
    "graph_eigenvector",
    "649_katz15_gap85",
    "mi_lag_bag",
    "649_ewma_40",
    "graph_eigenvector_recent",
    "neg_binomial",
    "graph_anti_community",
    "649_rrf_graph",
    "649_ewma_10",
    "649_katz25_beta75",
    "graph_harmonic",
    "649_mod10_hot",
    "graph_triangles",
    "graph_rwr_recent",
    "649_mom_20_80",
    "649_mom_15_60",
    "649_ewma_20",
    "graph_fiedler",
    "graph_personalized_pr",
    # 2026-09-01 — selecția top-1 separată pentru Joker Urna 2
    "circular_kernel",
    "649_katz12_gap88",
    "649_cold_rebound",
    "sum_affinity",
    "649_volatility_low",
    "649_consec_penalty",
    "bayes_poisson",
    "649_low_high_bal",
    "649_last_neighbors",
    "649_decade_hot",
    "ml_knn_5",
)


def _load(path: Path, cols: tuple[str, ...]) -> np.ndarray:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append([int(rec[c]) for c in cols])
    assert rows, f"{path} gol"
    return np.asarray(rows, dtype=np.int64)


def test_added_math_methods_finite_not_consecutive_block():
    """Pe jocul (jocurile) din per_game, nu forțat pe 6/49 — degenerarea e per univers."""
    pg = load_per_game()
    hist: dict[str, tuple[np.ndarray, int, int]] = {}
    for gk, (path, cols, max_num, pool_size) in GAMES.items():
        hist[gk] = (_load(path, cols)[:-1], max_num, pool_size)

    for name in ADDED:
        assert name in METHODS, name
        games = [g for g, lst in pg.items() if name in lst]
        if not games:
            games = ["loto_6_49"]
        for gk in games:
            draws, max_num, pool_size = hist[gk]
            scores, _dt = call_method(name, draws, max_num)
            assert scores, (name, gk)
            assert all(np.isfinite(v) for v in scores.values()), (name, gk)
            assert len({round(float(v), 12) for v in scores.values()}) >= 2, (name, gk)
            pool = rank_by_score(scores, pool_size)
            assert len(pool) == pool_size, (name, gk, pool)
            if pool_size > 1:
                assert not is_consecutive_block(pool, min_size=6), (name, gk, sorted(pool))
