"""Poarta de decizie: referință hipergeometrică, ferestre comune, tie-break, determinism."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.benchmark import decision, methods, runner

_SYNTH = ["m_alpha", "m_beta", "m_gamma", "m_incomplete", "m_tiebreak", "m_signal"]


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    for name in _SYNTH:
        if name not in methods.METHODS:
            monkeypatch.setitem(
                methods.METHODS, name,
                (lambda draws, max_num: {}, "test", False, "sintetic (doar teste)"),
            )
    # Fără curare per_game pe jocurile reale folosite sintetic aici.
    from loto_enterprise.benchmark import curated
    monkeypatch.setattr(curated, "load_per_game", lambda: {})


def _row(game, method, pct, n, rate, k_col="k10", k_val=1.0, rate_col="rate_3plus_k10", **extra):
    d = {"game": game, "method": method, "percentile": pct, "n_test": n, "n_eval": n,
         "is_random": False, "failed": False, "runtime_sec": 0.1, k_col: k_val, rate_col: rate}
    d.update(extra)
    return d


def test_expected_random_rate_matches_known_geometries():
    assert decision.expected_random_rate(20, 1, 1, 1) == pytest.approx(0.05)
    # 6/49, pool 6, cel puțin 3: 1 - P(0) - P(1) - P(2)
    from math import comb
    total = comb(49, 6)
    p = sum(comb(6, k) * comb(43, 6 - k) for k in range(3, 7)) / total
    assert decision.expected_random_rate(49, 6, 6, 3) == pytest.approx(p)
    assert decision.expected_random_rate(49, 6, 6, 7) == 0.0
    assert decision.expected_random_rate(49, 6, 49, 3) == pytest.approx(1.0)


def test_gate_uses_hypergeometric_reference_not_noisy_random_row(monkeypatch):
    """O realizare `random` norocoasă nu mai poate descalifica o metodă peste așteptare."""
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    base = decision.expected_random_rate(49, 6, 10, 3)
    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_row("loto_6_49", "random", pct, n, base + 0.05))
        rows.append(_row("loto_6_49", "m_signal", pct, n, base + 0.02))
    cfg = decision.decide_optimal_config_for_pool(pd.DataFrame(rows), "loto_6_49", 10, 6)
    assert cfg["baseline_source"] == "hypergeometric"
    assert cfg["baseline_rate"] == pytest.approx(base)
    assert cfg["scorer"] == "m_signal"
    assert cfg["low_confidence"] is False
    assert cfg["random_empirical_rate"] == pytest.approx(base + 0.05, abs=1e-4)

    # Joc necunoscut, fără geometrie: rămâne referința empirică din folds.csv.
    rows2 = [dict(r, game="synthetic_game") for r in rows]
    cfg2 = decision.decide_optimal_config_for_pool(pd.DataFrame(rows2), "synthetic_game", 10, 6)
    assert cfg2["baseline_source"] == "empirical_random"
    assert cfg2["low_confidence"] is True


def test_method_with_missing_window_is_excluded_and_reported(monkeypatch):
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    base = decision.expected_random_rate(49, 6, 10, 3)
    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_row("loto_6_49", "random", pct, n, base))
        rows.append(_row("loto_6_49", "m_alpha", pct, n, base + 0.01))
        if pct != 60:
            rows.append(_row("loto_6_49", "m_incomplete", pct, n, base + 0.10))
    cfg = decision.decide_optimal_config_for_pool(pd.DataFrame(rows), "loto_6_49", 10, 6)
    assert cfg["scorer"] == "m_alpha"
    assert cfg["expected_windows"] == [10, 30, 60, 100]
    assert cfg["incomplete_methods"] == [{"method": "m_incomplete", "missing_windows": [60]}]


def test_tiebreak_dependent_method_is_excluded_only_when_column_exists(monkeypatch):
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    base = decision.expected_random_rate(49, 6, 10, 3)
    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_row("loto_6_49", "random", pct, n, base, tiebreak_k10=0.0))
        rows.append(_row("loto_6_49", "m_alpha", pct, n, base + 0.01, tiebreak_k10=0.2))
        rows.append(_row("loto_6_49", "m_tiebreak", pct, n, base + 0.10, tiebreak_k10=0.9))
    cfg = decision.decide_optimal_config_for_pool(pd.DataFrame(rows), "loto_6_49", 10, 6)
    assert cfg["tiebreak_gate_applied"] is True
    assert cfg["scorer"] == "m_alpha"
    assert cfg["tiebreak_dependent"] == [{"method": "m_tiebreak", "tiebreak_fraction": 0.9}]

    df_old = pd.DataFrame(rows).drop(columns=["tiebreak_k10"])
    cfg_old = decision.decide_optimal_config_for_pool(df_old, "loto_6_49", 10, 6)
    assert cfg_old["tiebreak_gate_applied"] is False
    assert cfg_old["scorer"] == "m_tiebreak"


def test_exact_ties_are_broken_by_name_regardless_of_row_order(monkeypatch):
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    base = decision.expected_random_rate(49, 6, 10, 3)
    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_row("loto_6_49", "random", pct, n, base))
        rows.append(_row("loto_6_49", "m_gamma", pct, n, base + 0.02))
        rows.append(_row("loto_6_49", "m_beta", pct, n, base + 0.02))
    a = decision.decide_optimal_config_for_pool(pd.DataFrame(rows), "loto_6_49", 10, 6)
    b = decision.decide_optimal_config_for_pool(pd.DataFrame(rows[::-1]), "loto_6_49", 10, 6)
    assert a["scorer"] == b["scorer"] == "m_beta"
    assert [e["method"] for e in a["ensemble"]] == [e["method"] for e in b["ensemble"]]


def test_score_random_is_deterministic_per_history():
    d1 = np.array([[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]], dtype=np.int64)
    d2 = np.array([[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 13]], dtype=np.int64)
    a = methods.score_random(d1, 49)
    assert a == methods.score_random(d1.copy(), 49)
    assert a != methods.score_random(d2, 49)
    assert set(a) == set(range(1, 50))


def test_fold_reports_tiebreak_fraction_per_pool(monkeypatch):
    """Scoruri cu două niveluri: taietura top-K cade mereu în grupul de egalitate."""
    name = "test_two_level_scorer"
    monkeypatch.setitem(
        methods.METHODS, name,
        (lambda _d, max_num: {n: (1.0 if n % 2 == 0 else 0.0) for n in range(1, max_num + 1)},
         "test", False, "sintetic"),
    )
    game = runner.GameDef(
        key="loto_6_49", label="6/49", csv_path="unused.csv",
        cols=[f"n{i}" for i in range(1, 7)], max_num=49, draw_n=6, pool_extra=2,
    )
    rng = np.random.default_rng(1)
    train = np.array([sorted(rng.choice(np.arange(1, 50), 6, replace=False)) for _ in range(20)])
    test = np.array([sorted(rng.choice(np.arange(1, 50), 6, replace=False)) for _ in range(6)])
    fold, _ = runner._evaluate_fold(name, train, test, game, block_size=2)
    assert fold.failed is False
    assert fold.tiebreak_per_pool == {"k6": 1.0, "k7": 1.0, "k8": 1.0}

    # Scoruri strict distincte: niciodată tie la graniță.
    monkeypatch.setitem(
        methods.METHODS, name,
        (lambda _d, max_num: {n: float(n) for n in range(1, max_num + 1)},
         "test", False, "sintetic"),
    )
    fold2, _ = runner._evaluate_fold(name, train, test, game, block_size=2)
    assert fold2.tiebreak_per_pool == {"k6": 0.0, "k7": 0.0, "k8": 0.0}
