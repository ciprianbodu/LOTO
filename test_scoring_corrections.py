"""Corecții de scoring: SES, Theta, Urna 2 și fallback-ul plat."""

from __future__ import annotations

import numpy as np
import pandas as pd

from loto_engine import LotoEngine
from loto_enterprise.benchmark.methods_wave2 import _ses_series, score_theta_drift
from loto_enterprise.benchmark.runner import GameDef, _evaluate_fold, load_draws
from loto_enterprise.core.history import chronological_history


def _ses_loop(x: np.ndarray, alpha: float) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float64)
    out[0] = x[0]
    decay = 1.0 - alpha
    for t in range(1, len(x)):
        out[t] = alpha * x[t] + decay * out[t - 1]
    return out


def test_ses_initial_state_propagates_past_the_first_step():
    rng = np.random.default_rng(7)
    x = rng.random((48, 3))
    assert np.allclose(_ses_series(x, 0.2), _ses_loop(x, 0.2))


def test_theta_drift_is_the_linear_forecast_not_the_mean_rate():
    from loto_enterprise.benchmark.methods_common import indicator, vector_to_scores

    n = 100
    pattern = np.zeros((n, 3))
    pattern[10:30, 0] = 1.0
    pattern[70:, 0] = 1.0
    pattern[:50, 1] = 1.0
    pattern[::2, 2] = 1.0
    draws = np.zeros((n, 3), dtype=np.int64)
    for i in range(n):
        present = [j + 1 for j in range(3) if pattern[i, j] == 1.0]
        draws[i, : len(present)] = present
    rate_win = 20
    ind = indicator(draws, 4)
    x = ind[-(80 + rate_win) :]
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, axis=0)])
    rate = (cs[rate_win:] - cs[:-rate_win]) / float(rate_win)
    k = rate.shape[0]
    t = np.arange(k, dtype=np.float64)
    tc = t - t.mean()
    slope = (tc[:, None] * (rate - rate.mean(axis=0))).sum(axis=0) / float(
        (tc**2).sum()
    )
    linear = rate.mean(axis=0) + slope * (k - t.mean())
    ses = _ses_series(rate, 0.2)[-1]
    expected = vector_to_scores(0.5 * linear + 0.5 * ses, 4)
    got = score_theta_drift(draws, max_num=4, window=80, rate_win=rate_win)
    assert got == expected
    old_drift = (np.cumsum(x, axis=0)[-1] - x[0]) / max(x.shape[0] - 1, 1)
    old = vector_to_scores(0.5 * old_drift + 0.5 * _ses_series(x, 0.2)[-1], 4)
    assert got != old
    assert got[1] > got[2]


def test_single_pick_cooccurrence_fails_before_the_block_loop():
    game = GameDef(
        "joker_urna2",
        "Joker — Urna 2 (1/20)",
        "unused",
        ["joker"],
        20,
        1,
        is_single_pick=True,
    )
    history = np.arange(1, 21, dtype=np.int64).reshape(-1, 1)
    test = np.array([[1], [2], [3]], dtype=np.int64)
    fold, _snap = _evaluate_fold("cooc_last3", history, test, game, 1)
    assert fold.failed
    assert "unusable scores" in fold.error
    assert fold.blocks == 0


def test_cooc_last3_still_scores_a_multi_number_draw():
    rng = np.random.default_rng(3)
    rows = [
        rng.choice(np.arange(1, 50), size=6, replace=False) for _ in range(40)
    ]
    draws = np.vstack(rows)
    game = GameDef("loto_6_49", "6/49", "unused", [], 49, 6, pool_extra=0)
    fold, _snap = _evaluate_fold("cooc_last3", draws[:30], draws[30:], game, 1)
    assert fold.failed is False
    assert fold.n_eval == 10


def test_urna2_bench_uses_the_same_rows_as_the_engine(tmp_path):
    rows = []
    for i in range(8):
        nums = [1, 2, 3, 4, 5 + (i % 3)]
        rows.append(
            {
                "date": f"{i + 1:02d}-01-2024",
                "n1": nums[0],
                "n2": nums[1],
                "n3": nums[2],
                "n4": nums[3],
                "n5": nums[4],
                "joker": 7 if i != 4 else 0,
            }
        )
    rows[2]["n1"] = 99
    frame = pd.DataFrame(rows)
    path = tmp_path / "joker.csv"
    frame.to_csv(path, index=False)
    game = GameDef(
        "joker_urna2",
        "Joker — Urna 2 (1/20)",
        str(path),
        ["joker"],
        20,
        1,
        is_single_pick=True,
    )
    draws = load_draws(game)
    eng = LotoEngine("joker")
    eng.data = chronological_history(frame)
    eng._build_draw_matrix()
    jokers = eng._valid_joker_values()
    assert draws[:, 0].tolist() == jokers.tolist()
    assert 0 not in draws[:, 0]
    assert len(draws) == 6


def test_frequency_fallback_rejects_an_empty_history():
    eng = LotoEngine("6/49")
    eng._draw_matrix = np.zeros((0, 6), dtype=np.int64)
    assert eng._frequency_fallback_scores() == {}


def test_walk_forward_summary_names_a_conditional_cover():
    from ui_results import _conditional_cover_note, _wf_summary

    note = _conditional_cover_note(
        {"audit": {"wheel_guarantee_used": 4, "wheel_condition_used": 5}}
    )
    assert "4 dacă 5" in note
    assert "condițională" in note
    assert _conditional_cover_note({"guarantee": 4, "wheel_condition": 4}) == ""

    class _Row:
        def __init__(self):
            self.draw_index = 1
            self.hits = 2
            self.hits_union = 3
            self.wheel_coverage = 100.0

    text = _wf_summary(
        [_Row()],
        {"audit": {"wheel_guarantee_used": 4, "wheel_condition_used": 5}},
    )
    assert text is not None
    assert "condițională 100%" in text
    assert "acoperire wheel: 100%" not in text
