"""Gărzi pentru metodele adăugate din testarea externă WF."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark.methods import METHODS, call_method
from loto_enterprise.core.ranking import is_consecutive_block, rank_by_score

CSV_649 = Path("_ISTORIC/loto_6_49.csv")

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
)


def _load_649():
    rows = []
    with CSV_649.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append([int(rec[c]) for c in ("n1", "n2", "n3", "n4", "n5", "n6")])
    return np.asarray(rows, dtype=np.int64)


def test_added_math_methods_finite_not_consecutive_block():
    draws = _load_649()
    history = draws[:-1]
    for name in ADDED:
        assert name in METHODS, name
        scores, _dt = call_method(name, history, 49)
        assert scores, name
        assert all(np.isfinite(v) for v in scores.values()), name
        pool = rank_by_score(scores, 11)
        assert len(pool) == 11, (name, pool)
        assert not is_consecutive_block(pool, min_size=6), (name, sorted(pool))
