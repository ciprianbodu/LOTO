from __future__ import annotations
import json
import numpy as np
import pandas as pd
import pytest
from loto_engine import LotoEngine
from loto_enterprise.benchmark import runner, methods
from loto_enterprise.benchmark.methods_experimental import score_interval_extrema_k16
from loto_enterprise.core import backtesting, method_selector, wf_sig
from loto_enterprise.core.history import training_cutoffs
from loto_enterprise.core.ranking import rank_by_score

METHOD = "interval_extrema_k16"

def _history():
    rng = np.random.default_rng(2)
    draws = np.array([rng.choice(np.arange(1, 50), 6, replace=False) for _ in range(230)])
    frame = pd.DataFrame(draws, columns=[f"n{i}" for i in range(1,7)])
    frame["date"] = [(pd.Timestamp("2020-01-01") + pd.Timedelta(days=i//2)).strftime("%d-%m-%Y") for i in range(len(frame))]
    frame.loc[10, "n1"] = 0
    return frame

def _choose(monkeypatch, fn=score_interval_extrema_k16):
    monkeypatch.setattr(method_selector, "has_decision", lambda *a,**k: True)
    monkeypatch.setattr(method_selector, "get_ensemble_for_game", lambda *a,**k: [(METHOD, fn, 1.)])
    monkeypatch.setattr(method_selector, "rejected_decision_scorer", lambda *a,**k: None)

def test_bench_engine_and_wf_use_same_day_boundaries(monkeypatch):
    bt = backtesting.LotoBacktester(_history(), game_type="6/49")
    # idx=224 has 224 valid rows before the day of the target.
    idx = 224
    end = bt.df.attrs["training_cutoffs"][idx]
    game = runner.GameDef("loto_6_49", "6/49", "unused", [], 49, 6,
                          history_cutoffs=bt.df.attrs["training_cutoffs"])
    captured = []
    def record(name, past, max_num, *, history_cutoffs=None):
        result = methods.call_method(name, past, max_num, history_cutoffs=history_cutoffs)
        captured.append(result[0]); return result
    monkeypatch.setattr(runner, "call_method", record)
    fold,_ = runner._evaluate_fold(METHOD, np.asarray(bt.draws[:idx]),
                                   np.asarray(bt.draws[idx:idx+1]), game, 1)
    assert not fold.failed, fold.error
    assert end > 200 and len(captured) == 1
    _choose(monkeypatch)
    engine = LotoEngine("6/49"); engine.data = bt.df.iloc[:end].copy(); engine._build_draw_matrix(); engine._winner_pool_hint = 16
    scores = engine._scores_via_bench_winner()
    assert rank_by_score(scores,16) == rank_by_score(captured[0],16)
    wf_captured = []
    def pipeline(self, **kwargs):
        self._winner_pool_hint = 16
        wf_captured.append(self._scores_via_bench_winner())
        self.hard_core = rank_by_score(wf_captured[-1], 16)
        return [], None, None, None, {}, self.audit
    monkeypatch.setattr(LotoEngine, "run_institutional_pipeline", pipeline)
    pred=backtesting._retroactive_step_stateless(bt.df,bt.draws,bt.dates,"6/49",idx,16,4,0,100)
    assert pred is not None
    assert rank_by_score(wf_captured[0],16) == rank_by_score(captured[0],16)

def test_cutoffs_recomputed_after_lookback_and_invalid_draw(monkeypatch):
    seen = []
    def probe(draws, max_num, *, history_cutoffs=None):
        seen.append((draws.copy(),history_cutoffs))
        return {n:float(n) for n in range(1,max_num+1)}
    probe._uses_history_cutoffs = True
    _choose(monkeypatch,probe)
    e=LotoEngine("6/49");e.data=_history();e._build_draw_matrix()
    assert len(e.data)==229
    e.data = e.data.tail(61).copy();e._build_draw_matrix();e._winner_pool_hint=16
    e._scores_via_bench_winner()
    assert len(seen)==1 and len(seen[0][0])==61
    assert seen[0][1] == training_cutoffs(e.data)
    assert seen[0][1][0] == 0 and any(i != c for i,c in enumerate(seen[0][1]))

def test_semantics_is_selective_and_additive():
    assert wf_sig.scorer_semantics({"scorer":"frequency"}) == ""
    assert "|interval-days1" in wf_sig.scorer_semantics({"scorer":METHOD})
    both=wf_sig.scorer_semantics({"scorer":"dmd_forecast", "ensemble":[{"method":METHOD}]})
    assert "|dmd-svd-ties1" in both and "|interval-days1" in both

def test_worker_pipeline_cache_version_is_incremented():
    import worker
    assert worker.PIPELINE_CACHE_VERSION == "v23"
    assert worker._pipeline_cache_key("h").startswith("v23:")


def test_joker_context_filters_invalid_urna2_rows(monkeypatch):
    seen = []
    def probe(draws, max_num, *, history_cutoffs=None):
        seen.append((draws.copy(), history_cutoffs))
        return {n: float(n) for n in range(1, max_num + 1)}
    probe._uses_history_cutoffs = True
    _choose(monkeypatch, probe)
    frame = _history().drop(columns=["n6"])
    frame["joker"] = [1 + i % 20 for i in range(len(frame))]
    frame.loc[15, "joker"] = 21
    e = LotoEngine("joker"); e.data = frame; e._build_draw_matrix(); e._winner_pool_hint = 11
    scores = e._scores_via_bench_winner(is_joker_drum=True)
    assert scores and len(seen) == 1
    expected_frame = e.data.loc[pd.to_numeric(e.data["joker"]).between(1, 20)]
    assert seen[0][1] == training_cutoffs(expected_frame)
    assert len(seen[0][0]) == len(expected_frame)
