"""Contracte temporale pentru auditul selecției, fără rulare de scoreri reali."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from scripts.analysis import audit_selection_replay as replay


def _series(method, hits):
    return replay.PredictionSeries(method, list(hits), list(hits), [False] * len(hits), [], 0.0)


def _game(key="loto_6_49"):
    return replay.GameDef(key, key, "unused.csv", [f"n{i}" for i in range(1, 7)], 49, 6)


def test_prediction_and_selection_exclude_all_targets_on_same_day(monkeypatch):
    seen = []

    def scorer(_method, history, maximum):
        seen.append(history.copy())
        return {number: float(number) for number in range(1, maximum + 1)}, 0.0

    monkeypatch.setattr(replay, "call_method", scorer)
    draws = np.arange(48).reshape(8, 6) % 49 + 1
    cutoffs = (0, 1, 2, 3, 4, 5, 5, 7)
    result = replay.evaluate_method("fake", draws, cutoffs, [5, 6, 7], 49, 16, 0, 2)
    assert [len(history) for history in seen] == [5, 7]
    assert np.array_equal(seen[0], draws[:5])
    assert np.array_equal(seen[1], draws[:7])
    assert replay.available_prediction_offsets([4, 5, 6, 7], cutoffs[6]) == [0]
    assert len(result.raw_hits) == len(result.limited_hits) == 3
    assert not result.errors


@pytest.mark.parametrize("target", [3, 4])
def test_holdout_outcomes_cannot_change_earlier_selection(target):
    indices = list(range(100, 280))
    series = {
        "frequency": _series("frequency", [0] * 180),
        "ridge_pooled_feats": _series("ridge_pooled_feats", [4] * 120 + [0] * 60),
        "random": _series("random", [2] * 180),
    }
    before, available = replay.select_from_past(series, indices, 220, _game(), 16, target)
    altered = deepcopy(series)
    altered["frequency"].raw_hits[120:] = [6] * 60
    altered["ridge_pooled_feats"].raw_hits[120:] = [6] * 60
    after, after_available = replay.select_from_past(altered, indices, 220, _game(), 16, target)
    assert before == after
    assert available == after_available == list(range(120))
    assert before["scorer"] == "ridge_pooled_feats"


def test_global_target_restored_when_temporal_selection_raises(monkeypatch):
    original = replay.decision.BENCH_HIT_TARGET
    received = []

    def fail(*_args):
        received.append(replay.decision.BENCH_HIT_TARGET)
        raise RuntimeError("selection failed")

    monkeypatch.setattr(replay.decision, "decide_optimal_config_for_pool", fail)
    with pytest.raises(RuntimeError, match="selection failed"):
        replay.select_from_past({"frequency": _series("frequency", [3] * 30)}, list(range(30)), 30, _game(), 16, 4)
    assert received == [4]
    assert replay.decision.BENCH_HIT_TARGET == original


def test_failed_prediction_is_missing_and_cannot_improve_selection_denominator(monkeypatch):
    monkeypatch.setattr(replay, "call_method", lambda *_args: ({1: 1.0, 2: 1.0}, 0.0))
    result = replay.evaluate_method("flat", np.ones((8, 6), dtype=int), tuple(range(8)), [5, 6, 7], 49, 16, 0, 2)
    assert result.raw_hits == result.limited_hits == [-1, -1, -1]
    assert len(result.errors) == 3
    partly_evaluated = _series("frequency", [4, -1, 4])
    frame = replay.selection_frame({"frequency": partly_evaluated}, [0, 1, 2], "loto_6_49", 16)
    largest = frame.loc[frame["percentile"].eq(100)].iloc[0]
    assert largest["n_test"] == 3
    assert largest["n_eval"] == 2
    assert largest["rate_4plus_k16"] == 1.0
    summary = replay.hit_summary([4, -1, 4], 49, 6, 16)
    assert summary["requested"] == 3
    assert summary["evaluated"] == 2
    assert summary["missing"] == 1


def test_block_selection_never_sees_outcomes_on_first_target_day(monkeypatch):
    indices = list(range(100, 280))
    cutoffs = list(range(280))
    cutoffs[220] = 219
    dates = [str(index) for index in range(280)]
    captured = []

    def choose(series, _indices, cutoff, _game, _pool_size, _target):
        available = replay.available_prediction_offsets(_indices, cutoff)
        captured.append((cutoff, available))
        return {"scorer": "frequency"}, available

    monkeypatch.setattr(replay, "select_from_past", choose)
    result = replay.replay_scenario({"frequency": _series("frequency", [3] * 180)}, indices,
                                    tuple(cutoffs), dates, _game(), 16, 120, 3, 20)
    assert [cutoff for cutoff, _available in captured] == [219, 240, 260]
    assert len(captured[0][1]) == 119
    assert result["selections"][0]["last_selection_outcome_index"] == 218
    assert len(result["trace"]) == 60
    assert all(trace["selection_cutoff"] <= trace["training_cutoff"] for trace in result["trace"])


def test_chronology_and_cutoffs_follow_shared_valid_draw_contract(tmp_path):
    path = tmp_path / "loto_6_49.csv"
    pd.DataFrame({
        "date": ["03-01-2026", "01-01-2026", "02-01-2026", "02-01-2026"],
        **{f"n{i}": [i, i, i, i] for i in range(1, 7)},
    }).to_csv(path, index=False)
    game = _game()
    game.csv_path = str(path)
    matrix, cutoffs, dates, source_hash = replay.history_for_game(game)
    assert len(matrix) == 4
    assert dates == ["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-03"]
    assert cutoffs == (0, 1, 1, 3)
    assert len(source_hash) == 64


def test_five_hits_remain_diagnostic_while_540_selection_target_is_four():
    indices = list(range(100, 280))
    series = {
        "frequency": _series("frequency", [0] * 180),
        "ridge_pooled_feats": _series("ridge_pooled_feats", [4] * 180),
        "random": _series("random", [2] * 180),
    }
    config, _available = replay.select_from_past(series, indices, 220, _game("loto_5_40"), 16, 3)
    assert config["hit_target"] == 4
    summary = replay.hit_summary([3, 4, 5, 6], 40, 6, 16)
    assert summary["hits_4plus"] == 3
    assert summary["hits_5plus"] == 2


def test_urna2_uses_top1_and_preserves_global_target():
    indices = list(range(100, 280))
    series = {
        "frequency": _series("frequency", [0] * 180),
        "ridge_pooled_feats": _series("ridge_pooled_feats", [1] * 180),
        "random": _series("random", [0] * 180),
    }
    game = replay.GameDef("joker_urna2", "Urna 2", "unused.csv", ["joker"], 20, 1,
                          pool_extra=0, is_single_pick=True)
    original = replay.decision.BENCH_HIT_TARGET
    config, available = replay.select_from_past(series, indices, 220, game, 1, 1)
    assert config["hit_target"] == 1
    assert config["baseline_rate"] == 0.05
    assert config["scorer"] == "ridge_pooled_feats"
    assert len(available) == 120
    assert replay.decision.BENCH_HIT_TARGET == original
    summary = replay.hit_summary([0, 1, 0, 1], 20, 1, 1)
    assert summary["hits_1plus"] == 2
    assert summary["rate_1plus"] == 0.5
    assert summary["baseline_1plus"] == 0.05
    assert summary["hits_3plus"] == 0


def test_urna2_chronology_first_rejects_invalid_main_draw(tmp_path):
    path = tmp_path / "joker.csv"
    frame = pd.DataFrame({
        "date": ["03-01-2026", "01-01-2026", "02-01-2026"],
        **{f"n{i}": [i, i, i] for i in range(1, 6)},
        "joker": [3, 1, 2],
    })
    frame.loc[2, "n2"] = 1
    frame.to_csv(path, index=False)
    game = replay.GameDef("joker_urna2", "Urna 2", str(path), ["joker"], 20, 1,
                          pool_extra=0, is_single_pick=True)
    draws, cutoffs, dates, _source_hash = replay.history_for_game(game)
    assert draws.tolist() == [[1], [3]]
    assert dates == ["2026-01-01", "2026-01-03"]
    assert cutoffs == (0, 1)
