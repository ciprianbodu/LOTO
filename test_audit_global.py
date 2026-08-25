"""Invarianți din verificarea globală — numărări, tombstone, curare, Spearman.

Fără pandas/numba/rich. CSVs din `_ISTORIC/` (versionate).
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark.curated import (
    REQUIRED_METHODS,
    apply_curation,
    load_curated,
    load_per_game,
)
from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION, SAFE_FALLBACK_SCORER
from loto_enterprise.benchmark.disabled import load_disabled
from loto_enterprise.benchmark.methods import METHODS, call_method, list_methods, method_meta
from loto_enterprise.core.method_selector import MAX_MEMBER_CORR, _pair_corr

GAMES = {
    "loto_6_49": (Path("_ISTORIC/loto_6_49.csv"), ("n1", "n2", "n3", "n4", "n5", "n6"), 49),
    "loto_5_40": (Path("_ISTORIC/loto_5_40.csv"), ("n1", "n2", "n3", "n4", "n5"), 40),
    "joker_urna1": (Path("_ISTORIC/joker.csv"), ("n1", "n2", "n3", "n4", "n5"), 45),
}


def _load(path: Path, cols: tuple[str, ...], max_num: int) -> np.ndarray:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            row = [int(rec[c]) for c in cols]
            if all(1 <= v <= max_num for v in row):
                rows.append(row)
    assert rows, path
    return np.asarray(rows, dtype=np.int64)


def test_registry_counts_and_tombstone():
    disabled = set(load_disabled())
    assert len(METHODS) == 111
    assert len(disabled) == 74
    assert "omnius" in disabled
    assert "omnius" not in METHODS
    assert not Path("loto_enterprise/benchmark/methods_omnius.py").exists()
    assert not (set(METHODS) & disabled)


def test_curation_integrity():
    cur = load_curated()
    disabled = set(load_disabled())
    pg = load_per_game()
    assert len(cur) == 43
    assert all(m in METHODS for m in cur)
    assert not (set(cur) & disabled)
    assert all(m in cur for m in REQUIRED_METHODS)
    assert SAFE_FALLBACK_SCORER in cur
    assert "random" in EXCLUDED_FROM_PRODUCTION
    avail = [
        m for m in list_methods()
        if method_meta(m).get("available", True) and m not in disabled
    ]
    kept, info = apply_curation(avail)
    assert set(kept) == set(cur)
    assert not info["missing_required"]
    assert pg.keys() >= GAMES.keys()
    for g, lst in pg.items():
        assert "random" not in lst
        assert all(m in cur for m in lst)
        assert len(lst) == len(set(lst))
    assert "cover_positional_bands" not in cur
    assert "graph_katz_low" not in pg["loto_6_49"]
    assert "graph_katz_low" in pg["loto_5_40"]


def test_per_game_scores_finite_and_not_clones():
    """Pe train = primele 70%: scoruri finite; |Spearman| < 0.95 în per_game."""
    pg = load_per_game()
    for gk, (path, cols, max_num) in GAMES.items():
        draws = _load(path, cols, max_num)
        n_test = max(1, int(round(len(draws) * 0.30)))
        train = draws[: len(draws) - n_test]
        scores: dict[str, dict] = {}
        for m in pg[gk]:
            sc, _dt = call_method(m, train, max_num)
            assert sc, (gk, m)
            assert all(math.isfinite(float(v)) for v in sc.values()), (gk, m)
            scores[m] = sc
        names = list(scores)
        clones = []
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                r = _pair_corr(scores[a], scores[b])
                if r is not None and abs(r) >= MAX_MEMBER_CORR:
                    clones.append((a, b, float(r)))
        assert not clones, clones
