"""Regresii pentru benchmark-ul dedicat Joker Urna 2 (single-pick)."""
from __future__ import annotations

import numpy as np

from loto_enterprise.benchmark import methods, runner


def test_single_pick_fold_emits_exact_top1_rate(monkeypatch):
    """Pentru 1/1, hitul mediu și rata 1+ trebuie să fie identice și exacte."""
    method_name = "test_single_pick_always_one"
    monkeypatch.setitem(
        methods.METHODS, method_name,
        (lambda _draws, max_num: {n: float(n == 1) for n in range(1, max_num + 1)},
         "test", False, "scorer sintetic"),
    )
    game = runner.GameDef(
        key="joker_urna2", label="Joker Urna 2", csv_path="unused.csv",
        cols=["joker"], max_num=20, draw_n=1, pool_extra=0, is_single_pick=True,
    )
    train = np.array([[1], [2], [3], [4], [5]], dtype=np.int64)
    test = np.array([[1], [1], [1]], dtype=np.int64)

    fold, _ = runner._evaluate_fold(method_name, train, test, game, block_size=99)

    assert fold.failed is False
    assert fold.n_eval == 3
    assert fold.avg_hits_topk == 1.0
    assert fold.rate_1plus == 1.0
    assert fold.rates_1plus_per_pool == {"k1": 1.0}
    assert fold.rate_3plus == fold.rate_4plus == 0.0


def test_single_pick_fold_rejects_flat_scores(monkeypatch):
    """Un scorer plat nu poate castiga top-1 doar prin tie-break-ul numeric."""
    method_name = "test_single_pick_flat"
    monkeypatch.setitem(
        methods.METHODS, method_name,
        (lambda _draws, max_num: {n: 0.0 for n in range(1, max_num + 1)},
         "test", False, "scorer plat sintetic"),
    )
    game = runner.GameDef(
        key="joker_urna2", label="Joker Urna 2", csv_path="unused.csv",
        cols=["joker"], max_num=20, draw_n=1, pool_extra=0, is_single_pick=True,
    )
    train = np.array([[1], [2], [3]], dtype=np.int64)
    test = np.array([[20], [20]], dtype=np.int64)

    fold, _ = runner._evaluate_fold(method_name, train, test, game, block_size=99)

    assert fold.failed is True
    assert fold.n_eval == 0
    assert "unusable scores" in fold.error
