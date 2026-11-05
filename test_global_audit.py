"""Regression tests for chronology, cache identity, and score parity."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from loto_engine import LotoEngine
from loto_enterprise.benchmark import runner
from loto_enterprise.benchmark.methods import _normalize, score_frequency
from loto_enterprise.core.backtesting import LotoBacktester, _retroactive_step_stateless
from loto_enterprise.core.history import chronological_history, training_cutoffs
from loto_enterprise.core.walk_forward_adapter import _csv_hash


def history(n=12):
    rng = np.random.default_rng(310)
    df = pd.DataFrame(
        [rng.choice(np.arange(1, 50), 6, replace=False) for _ in range(n)],
        columns=[f"n{i}" for i in range(1, 7)],
    )
    df["date"] = pd.date_range("2025-01-01", periods=n).strftime("%d-%m-%Y")
    return df


def test_headers_and_chronology_agree_across_all_loaders(tmp_path):
    expected = history()
    dirty = expected.iloc[::-1].rename(columns=lambda c: f" {c.upper()} ")
    dirty["notes"] = "metadata must not become a number column"
    path = tmp_path / "loto_6_49.csv"
    dirty.to_csv(path, index=False)
    engine = LotoEngine("6/49")
    assert engine.load_data(str(path))
    bt = LotoBacktester(dirty, "6/49")
    game = runner.GameDef(
        "loto_6_49", "6/49", str(path), [f"n{i}" for i in range(1, 7)], 49, 6
    )
    assert runner.load_draws(game).tolist() == bt.draws == engine._draw_matrix.tolist()
    assert bt.draws == expected[[f"n{i}" for i in range(1, 7)]].values.tolist()
    assert bt.dates == expected.date.tolist()


def test_ambiguous_headers_and_invalid_dates_are_rejected():
    df = history()
    df[" N1 "] = df.n1
    with pytest.raises(ValueError, match="ambigue"):
        chronological_history(df)
    df = history()
    df.loc[4, "date"] = "31-02-2025"
    with pytest.raises(ValueError, match="date de extragere"):
        chronological_history(df)


def test_backtest_does_not_observe_other_draws_on_target_day(monkeypatch):
    df = history()
    df.loc[9, "date"] = df.loc[8, "date"]
    bt = LotoBacktester(df, "6/49")
    seen = []

    def pipeline(self, **kwargs):
        seen.append(self.data.copy())
        self.hard_core = [1, 2, 3, 4, 5, 6]
        return [self.hard_core], 0, 0, [], {"coverage_pct": 100}, {}

    monkeypatch.setattr(LotoEngine, "run_institutional_pipeline", pipeline)
    prediction = _retroactive_step_stateless(
        bt.df, bt.draws, bt.dates, "6/49", 9, 6, 3, 0, 100, False, False
    )
    assert len(seen[0]) == 8
    assert prediction.simulation_date == df.loc[7, "date"]


def test_benchmark_same_day_boundary_crossing_train_split(monkeypatch):
    df = history()
    df.loc[9, "date"] = df.loc[8, "date"]
    draws = df[[f"n{i}" for i in range(1, 7)]].to_numpy()
    game = runner.GameDef(
        "loto_6_49", "6/49", "unused", [], 49, 6, history_cutoffs=training_cutoffs(df)
    )
    lengths = []

    def scorer(name, past, max_num):
        lengths.append(len(past))
        return {n: float(n) for n in range(1, max_num + 1)}, 0

    monkeypatch.setattr(runner, "call_method", scorer)
    fold, _ = runner._evaluate_fold("frequency", draws[:9], draws[9:], game, 1)
    assert not fold.failed and fold.n_eval == 3
    assert lengths == [8, 10, 11]
    lengths.clear()
    runner._evaluate_fold(
        "frequency", draws[:9], draws[9:], replace(game, history_cutoffs=()), 1
    )
    assert lengths == [9, 10, 11]


@pytest.mark.parametrize("block_size,n_test", [(0, 2), (-1, 2), (1, 0)])
def test_invalid_fold_cannot_hang_or_look_successful(block_size, n_test):
    draws = history()[[f"n{i}" for i in range(1, 7)]].to_numpy()
    game = runner.GameDef("loto_6_49", "6/49", "unused", [], 49, 6)
    fold, _ = runner._evaluate_fold(
        "frequency", draws, draws[:n_test], game, block_size
    )
    assert fold.failed


@pytest.mark.parametrize("max_num,pick", [(49, 6), (40, 5), (45, 5), (20, 1)])
def test_vectorized_frequency_is_bit_identical_to_reference(max_num, pick):
    rng = np.random.default_rng(392)
    for length in (1, 2, 80, 2185):
        draws = np.array(
            [
                rng.choice(np.arange(1, max_num + 1), pick, replace=False)
                for _ in range(length)
            ]
        )
        weights = np.exp(np.linspace(-2.0, 0.0, length)).astype(np.float32)
        raw = np.zeros(max_num + 1, dtype=np.float64)
        for weight, row in zip(weights, draws):
            for value in row:
                raw[value] += weight
        expected = _normalize(
            {i: float(raw[i]) for i in range(1, max_num + 1)}, max_num
        )
        assert score_frequency(draws, max_num) == expected


@pytest.mark.parametrize(
    "game,max_num,pick", [("6/49", 49, 6), ("5/40", 40, 5), ("joker", 45, 5)]
)
def test_frequency_fallback_matches_bench_exactly(game, max_num, pick):
    rng = np.random.default_rng(128)
    draws = np.array(
        [rng.choice(np.arange(1, max_num + 1), pick, replace=False) for _ in range(150)]
    )
    engine = LotoEngine(game)
    engine._draw_matrix = draws
    assert engine._frequency_fallback_scores() == score_frequency(draws, max_num)


def test_wf_hash_covers_dates_and_nonstandard_headers():
    df = history()
    variant = df.copy()
    variant.loc[9, "date"] = variant.loc[8, "date"]
    assert _csv_hash(df, "6/49") != _csv_hash(variant, "6/49")
    renamed = df.rename(columns=lambda c: f" {c.upper()} ")
    assert _csv_hash(renamed, "6/49") == _csv_hash(df, "6/49")
    renamed.loc[0, " N1 "] = 22
    assert _csv_hash(renamed, "6/49") != _csv_hash(df, "6/49")


def test_full_depth_cache_can_complete_without_impossible_early_steps(
    tmp_path, monkeypatch
):
    from loto_enterprise.core import walk_forward_adapter as wf
    from loto_enterprise.core import backtesting as bt

    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 1)
    df = history(12)
    flat, meta = wf.run_honest_walk_forward(df, "6/49", 6, 100, guarantee=3)
    assert meta["n_expected"] == meta["n_test_draws"] == 7
    assert not meta["partial"]
    assert {row.draw_index for row in flat} == set(range(5, 12))
    _, cached = wf.run_honest_walk_forward(df, "6/49", 6, 100, guarantee=3)
    assert cached["from_cache"]


def test_fractional_depth_and_lookback_use_distinct_cache_keys(tmp_path, monkeypatch):
    from loto_enterprise.core import walk_forward_adapter as wf

    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    df = history(12)
    _, first = wf.run_honest_walk_forward(df, "6/49", 6, 9.1, guarantee=3)
    _, second = wf.run_honest_walk_forward(df, "6/49", 6, 9.2, guarantee=3)
    assert first["cache_file"] != second["cache_file"]
    assert wf._decision_sig("6/49", 11, 30.1) != wf._decision_sig("6/49", 11, 30.2)


def test_short_pool_cannot_be_reported_as_playable_ticket():
    from wheeling_methods import WHEEL_METHODS, generate_wheel, compute_coverage_pct
    from loto_engine import generate_combinatorial_wheel

    assert generate_combinatorial_wheel([1, 2, 3], 6, 3) == ([], 0.0)
    for name, method in WHEEL_METHODS.items():
        assert method([1, 2, 3], 6, 3) == ([], 0.0), name
    assert generate_wheel("lotto", [1, 2, 3], 6, 3, condition=4) == ([], 0.0)
    assert generate_wheel("lajolla", [1, 2, 3], 6, 4, max_variants=7) == ([], 0.0)
    assert compute_coverage_pct([], [1, 2, 3], 4) == 0.0


def test_freshness_hash_tracks_corrected_dates(tmp_path):
    from loto_enterprise.benchmark.freshness import _content_hash

    path = tmp_path / "history.csv"
    df = history()
    df.to_csv(path, index=False)
    before = _content_hash(path, [f"n{i}" for i in range(1, 7)])
    df.loc[8, "date"] = df.loc[7, "date"]
    df.to_csv(path, index=False)
    assert _content_hash(path, [f"n{i}" for i in range(1, 7)]) != before


def test_worker_cache_covers_decisions_and_all_wheel_overrides(tmp_path, monkeypatch):
    import worker

    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    config = {"datasets": []}
    default = worker._pipeline_cache_key("input", config)
    (tmp_path / "best_methods.json").write_text('{"games": {}}', encoding="utf-8")
    decision_changed = worker._pipeline_cache_key("input", config)
    assert decision_changed != default
    keys = set()
    for name in ("greedy", "lajolla", "ilp", "maxcover"):
        monkeypatch.setenv("LOTO_WHEEL_METHOD", name)
        keys.add(worker._pipeline_cache_key("input", config))
    assert len(keys) == 4


def test_failed_reload_does_not_leave_old_draws_usable(tmp_path):
    engine = LotoEngine("6/49")
    engine.data = history()
    engine._build_draw_matrix()
    assert not engine.load_data(str(tmp_path / "absent.csv"))
    assert engine.data is None and engine._draw_matrix is None
