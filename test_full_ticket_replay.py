"""„Bilet complet” refăcut pe pașii walk-forward, din pool și dispersat."""

from __future__ import annotations

import logging
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import ui_hits
from covering.spread import _excess_of, spread_variants
from loto_enterprise.core import backtesting as bt
from loto_enterprise.core import walk_forward_adapter as wf
from loto_enterprise.core.full_ticket import build_full_ticket
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.py314_io import pickle_load_path, pickle_store_path_atomic
from loto_enterprise.core.ticket_replay import replay_full_tickets, step_contexts
from scripts.analysis.audit_output import capture_ui

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
    contexts = step_contexts(flat)
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


@pytest.mark.parametrize("game", ["6/49", "joker", "5/40"])
@pytest.mark.parametrize("tickets", [1, 3])
def test_replay_is_the_full_ticket_button_on_each_step(game, tickets, tmp_path, monkeypatch):
    flat, _meta = _wf(game, tmp_path, monkeypatch)
    res = replay_full_tickets(flat, game, tickets, 3)
    assert res["missing"] == res["unavailable"] == 0
    assert res["best"].keys() == step_contexts(flat).keys()
    for di, ctx in step_contexts(flat).items():
        data = {
            "hard_core": ctx["hard_core"], "guarantee": 3, "audit": ctx["audit"],
            "hard_core_joker": ctx["hard_core_joker"],
        }
        for mode, spread in (("pool", False), ("spread", True)):
            t = build_full_ticket(game, data, tickets, spread=spread)
            urn1 = [v[:5] if game == "joker" else v for v in t["variants"]]
            assert res["best"][di][mode] == max(len(set(v) & set(ctx["actual"])) for v in urn1)
            assert res["best"][di][f"{mode}_variants"] == len(t["variants"])


@pytest.mark.parametrize("game", ["6/49", "joker", "5/40"])
def test_uniform_rates_are_the_dialog_chances_of_the_latest_step(game, tmp_path, monkeypatch):
    flat, _meta = _wf(game, tmp_path, monkeypatch)
    res = replay_full_tickets(flat, game, 3, 3)
    last = max(res["best"])
    assert res["uniform_draw"] == last
    ctx = step_contexts(flat)[last]
    data = {
        "hard_core": ctx["hard_core"], "guarantee": 3, "audit": ctx["audit"],
        "hard_core_joker": ctx["hard_core_joker"],
    }
    for mode, spread in (("pool", False), ("spread", True)):
        shown = build_full_ticket(game, data, 3, spread=spread)["chances"]["shown"]
        assert shown
        for threshold, p in shown.items():
            assert res["uniform"][mode][threshold] == pytest.approx(p, abs=1e-15)


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
    assert step_contexts(kept).keys() == step_contexts(flat).keys()
    assert {(r.draw_index, tuple(r.variant)): (r.hits, r.hits_union) for r in kept} == hits
    missing = wf.steps_without_ticket_context(kept)
    assert missing
    assert replay_full_tickets(kept, "6/49", 1, 3)["missing"] == len(missing)

    # Rularea următoare reface pașii: același rezultat WF, acum cu context.
    redone, meta_redone = _wf("6/49", tmp_path, monkeypatch)
    assert not meta_redone["from_cache"]
    assert {(r.draw_index, tuple(r.variant)): (r.hits, r.hits_union) for r in redone} == hits
    assert not wf.steps_without_ticket_context(redone)
    _again, meta_again = _wf("6/49", tmp_path, monkeypatch)
    assert meta_again["from_cache"]


def test_replay_reports_missing_and_unbuildable_steps(tmp_path, monkeypatch):
    flat, _meta = _wf("6/49", tmp_path, monkeypatch)
    steps = sorted(step_contexts(flat))
    for r in flat:
        if r.draw_index == steps[0]:
            r.ticket_context = None
        elif r.draw_index == steps[1]:
            r.ticket_context = {}
    res = replay_full_tickets(flat, "6/49", 1, 3)
    assert res["n_draws"] == len(steps)
    assert res["missing"] == 1 and res["unavailable"] == 1
    assert len(res["best"]) == len(steps) - 2


def _contexts(n, seed=5):
    rng = np.random.default_rng(seed)
    out = []
    for di in range(n):
        vals = rng.random(49)
        scores = {k + 1: float(vals[k]) for k in range(49)}
        audit: dict = {}
        pool = select_pool_from_scores(scores, 10, set(), audit, max_num=49, max_consecutive_run=2)
        actual = sorted(int(x) for x in rng.choice(np.arange(1, 50), 6, replace=False))
        out.append((di, {"hard_core": pool, "hard_core_joker": [], "audit": audit, "actual": actual}))
    return out


class _Row:
    def __init__(self, di, ctx):
        self.draw_index, self.ticket_context = di, ctx


def test_parallel_replay_equals_the_in_process_one(caplog):
    flat = [_Row(di, ctx) for di, ctx in _contexts(70)]
    serial = replay_full_tickets(flat, "6/49", 1, 3, workers=1)
    with caplog.at_level(logging.WARNING):
        parallel = replay_full_tickets(flat, "6/49", 1, 3, workers=2)
    assert not [r for r in caplog.records if "reluare paralela indisponibila" in r.getMessage()]
    assert parallel == serial
    assert len(serial["best"]) == 70


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


@pytest.mark.parametrize("max_run", [1, 2, 3])
def test_run_excess_matches_brute_force(max_run):
    rng = np.random.default_rng(max_run)
    for _ in range(300):
        nums = {int(x) for x in rng.choice(np.arange(1, 30), int(rng.integers(0, 12)), replace=False)}
        ordered = sorted(nums)
        runs, length = [], 0
        for i, n in enumerate(ordered):
            length = length + 1 if i and n == ordered[i - 1] + 1 else 1
            if i + 1 == len(ordered) or ordered[i + 1] != n + 1:
                runs.append(length)
        assert _excess_of(nums, max_run) == sum(max(0, r - max_run) for r in runs)


def test_spread_with_run_limit_still_respects_it_on_contiguous_universes():
    for lo in range(1, 30, 7):
        uni = list(range(lo, lo + 18))
        variants = spread_variants(uni, 3, 6, 6, 49, 3, max_run=2)
        for v in variants:
            assert not any(a + 1 in v and a + 2 in v for a in v)
        assert all(len(set(a) & set(b)) == 0 for a, b in combinations(variants, 2))


def _replay(best, uniform=None):
    return {"tickets": 1, "n_draws": len(best) + 2, "missing": 2, "unavailable": 0,
            "errors": {}, "best": best, "uniform": uniform}


def test_history_rows_count_draws_with_a_winning_variant(monkeypatch):
    best = {
        1: {"pool": 3, "spread": 2, "pool_variants": 3, "spread_variants": 3},
        2: {"pool": 2, "spread": 4, "pool_variants": 3, "spread_variants": 3},
        3: {"pool": 5, "spread": 3, "pool_variants": 3, "spread_variants": 3},
        4: {"pool": 1, "spread": 1, "pool_variants": 3, "spread_variants": 3},
    }
    uniform = {"pool": {3: 0.0547, 4: 0.0029, 5: 0.00005}, "spread": {3: 0.0559, 4: 0.003, 5: 0.00006}}
    monkeypatch.setattr(ui_hits, "_full_ticket_replay", lambda *a: _replay(best, uniform))
    monkeypatch.setitem(ui_hits.SETTINGS, "full_ticket_count_val", 1)
    with capture_ui() as ui:
        ui_hits._render_full_ticket_replay([], "6/49", 3)
    table = next(n for n in ui.walk() if n["kind"] == "table")
    pool, spread = table["kwargs"]["rows"]
    assert (pool["p3"], pool["p4"], pool["p5"]) == ("2 (50.00%)", "1 (25.00%)", "1 (25.00%)")
    assert (spread["p3"], spread["p4"], spread["p5"]) == ("2 (50.00%)", "1 (25.00%)", "0 (0.00%)")
    assert pool["var"] == spread["var"] == "3 variante"
    assert pool["rnd"] == "5.5% / 0.29% / 0.005%"
    assert spread["rnd"] == "5.6% / 0.30% / 0.006%"
    text = ui.text()
    assert "un bilet pe extragere" in text
    assert "Lipsesc 2 din 6: 2 extrageri din cache-ul vechi" in text
    assert any("extragere uniformă" in c.get("label", "") for c in table["kwargs"]["columns"])
    assert "nu e o validare externă" in text


def test_history_says_why_rows_are_missing_on_an_old_cache(monkeypatch):
    monkeypatch.setattr(ui_hits, "_full_ticket_replay", lambda *a: _replay({}))
    with capture_ui() as ui:
        ui_hits._render_full_ticket_replay([], "6/49", 3)
    assert not [n for n in ui.walk() if n["kind"] == "table"]
    assert "următoarea validare walk-forward" in ui.text()


def test_history_shows_progress_while_computing(monkeypatch):
    monkeypatch.setattr(ui_hits, "_full_ticket_replay", lambda *a: None)
    with capture_ui() as ui:
        ui_hits._render_full_ticket_replay([], "joker", 3)
    assert "⏳" in ui.text()
    assert any(n["kind"] == "timer" for n in ui.walk())


def test_background_replay_is_memoised_on_the_displayed_history(monkeypatch):
    calls = []

    def fake(flat, game, tickets, guarantee, workers=1):
        calls.append(tickets)
        return {"best": {}, "tickets": tickets}

    import loto_enterprise.core.ticket_replay as tr

    monkeypatch.setattr(tr, "replay_full_tickets", fake)
    monkeypatch.setattr(ui_hits, "_REPLAY_DONE", {})
    monkeypatch.setattr(ui_hits, "_REPLAY_PENDING", {})
    flat = [_Row(0, {})]
    assert ui_hits._full_ticket_replay(flat, "6/49", 2, 3) is None
    for _ in range(200):
        if not ui_hits._REPLAY_THREAD["running"]:
            break
        import time

        time.sleep(0.02)
    assert ui_hits._full_ticket_replay(flat, "6/49", 2, 3) == {"best": {}, "tickets": 2}
    assert ui_hits._full_ticket_replay(list(flat), "6/49", 2, 3) is None  # altă listă
    assert calls[0] == 2


def test_analysis_menu_passes_the_result_game_and_guarantee():
    src = open("ui_hits.py", encoding="utf-8").read()
    assert '"guarantee": data.get("guarantee")' in src
    assert "full_ticket={" in src


def test_replay_does_not_emit_wheel_progress_logs(caplog):
    flat = [_Row(di, ctx) for di, ctx in _contexts(5)]
    with caplog.at_level(logging.INFO):
        replay_full_tickets(flat, "6/49", 1, 3, workers=1)
    assert not [r for r in caplog.records if "[WHEEL]" in r.getMessage()]


def test_ui_entrypoint_does_not_start_under_child_processes():
    src = Path("app_nicegui.py").read_text(encoding="utf-8")
    assert 'if __name__ in {"__main__", "__mp_main__"}:' not in src
    assert 'if __name__ == "__main__":' in src
