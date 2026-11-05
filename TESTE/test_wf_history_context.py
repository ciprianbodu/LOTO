"""Historical pool context remains available for LR replay and prize validation."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

from loto_enterprise.core import backtesting as bt
from loto_enterprise.core import walk_forward_adapter as wf
from loto_enterprise.core.py314_io import pickle_load_path, pickle_store_path_atomic

CSV = {
    "6/49": "_ISTORIC/loto_6_49.csv",
    "joker": "_ISTORIC/joker.csv",
    "5/40": "_ISTORIC/loto_5_40.csv",
}
COLS = {
    "6/49": [f"n{i}" for i in range(1, 7)],
    "joker": [f"n{i}" for i in range(1, 6)],
    "5/40": [f"n{i}" for i in range(1, 7)],
}


def _contexts_by_draw(flat):
    return {row.draw_index: row.ticket_context for row in flat}


def _tail(game, n=60):
    return pd.read_csv(CSV[game]).tail(n).reset_index(drop=True)



def _wf(game, tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    opts = dict(backtest_depth_percent=15.0, guarantee=3, max_consecutive_run=2)
    opts.update(kw)
    return wf.run_honest_walk_forward(_tail(game), game, 10, **opts)



@pytest.mark.parametrize("game", ["6/49", "joker", "5/40"])
def test_every_wf_step_keeps_the_ticket_context(game, tmp_path, monkeypatch):
    flat, meta = _wf(game, tmp_path, monkeypatch)
    df = bt.LotoBacktester(_tail(game), game).df
    contexts = _contexts_by_draw(flat)
    assert len(contexts) == meta["n_test_draws"] > 0
    for di, ctx in contexts.items():
        assert ctx["actual"] == sorted(int(x) for x in df.iloc[di][COLS[game]])
        ranking = list(ctx["audit"]["timesfm_predictions"])
        assert set(ctx["hard_core"]) <= set(ranking)
        assert len(ctx["actual"]) == (6 if game != "joker" else 5)
        assert ctx["audit"]["consecutive_limit"]["requested"] == 2
        assert len(ctx["hard_core_joker"]) == (1 if game == "joker" else 0)
        # Un singur obiect pe extragere, nu o copie pe fiecare variantă.
        rows = [r for r in flat if r.draw_index == di]
        assert all(r.ticket_context is rows[0].ticket_context for r in rows)



def _strip_context(cache_file):
    cache_file = Path(cache_file)
    payload = pickle_load_path(cache_file)
    for row in payload["flat"]:
        del row.__dict__["ticket_context"]  # ca o intrare scrisă înaintea câmpului
    pickle_store_path_atomic(cache_file, payload)
    return payload



def test_old_cache_keeps_its_entries_until_the_steps_are_redone(tmp_path, monkeypatch):
    flat, meta = _wf("6/49", tmp_path, monkeypatch)
    old = _strip_context(meta["cache_file"])
    hits = {(r.draw_index, tuple(r.variant)): (r.hits, r.hits_union) for r in old["flat"]}

    # Oprit după sondă (un pas): pașii nerefăcuți își păstrează intrările vechi.
    monkeypatch.setattr(bt, "_WF_PROBE_STEPS", 1)
    kept, meta_kept = _wf("6/49", tmp_path, monkeypatch, should_cancel=lambda: True)
    assert not meta_kept["partial"]
    assert _contexts_by_draw(kept).keys() == _contexts_by_draw(flat).keys()
    assert {(r.draw_index, tuple(r.variant)): (r.hits, r.hits_union) for r in kept} == hits
    missing = wf.steps_without_ticket_context(kept)
    assert missing

    # Rularea următoare reface pașii: același rezultat WF, acum cu context.
    redone, meta_redone = _wf("6/49", tmp_path, monkeypatch)
    assert not meta_redone["from_cache"]
    assert {(r.draw_index, tuple(r.variant)): (r.hits, r.hits_union) for r in redone} == hits
    assert not wf.steps_without_ticket_context(redone)
    _again, meta_again = _wf("6/49", tmp_path, monkeypatch)
    assert meta_again["from_cache"]



def test_wf_parallel_branch_carries_the_ticket_context(monkeypatch, caplog):
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 2)
    monkeypatch.setattr(bt, "_WF_SERIAL_MAX_MS", 0.0)
    monkeypatch.setattr(bt, "_WF_PROBE_STEPS", 1)
    b = bt.LotoBacktester(_tail("6/49", 40), "6/49")
    with caplog.at_level(logging.WARNING, logger=bt.logger.name):
        preds = b.run_retroactive_backtest(
            backtest_depth_percent=15.0, pool_size=10, guarantee=3, max_variants=2,
            use_feedback=False, enable_hard_inversion=False, max_consecutive_run=2,
        )
    assert not [r for r in caplog.records if "WF rapid indisponibil" in r.getMessage()]
    assert len(preds) > 1
    for p in preds:
        assert p.ticket_context["hard_core"] == list(p.hard_core)
        assert p.ticket_context["actual"] == sorted(p.actual_numbers)



def test_ui_entrypoint_does_not_start_under_child_processes():
    src = Path("app_nicegui.py").read_text(encoding="utf-8")
    assert 'if __name__ in {"__main__", "__mp_main__"}:' not in src
    assert 'if __name__ == "__main__":' in src
