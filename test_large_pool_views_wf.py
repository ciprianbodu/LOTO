"""Full-universe pools keep their identity without inventing bench/WF results."""

import pandas as pd
import pytest

import ui_bench
import ui_hits
import ui_results
from loto_enterprise.core import method_selector as selector
from loto_enterprise.core import walk_forward_adapter as wf
from loto_enterprise.core.backtesting import LotoBacktester
from loto_enterprise.core.full_ticket import _ticket_pool
from scripts.analysis.audit_output import capture_ui


def history(game="6/49", n=30):
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%d-%m-%Y"),
        **{f"n{i}": [i] * n for i in range(1, 6 if game == "joker" else 7)},
    })
    if game == "joker":
        frame["joker"] = 7
    return frame


@pytest.mark.parametrize("game,pool", [
    ("6/49", 49), ("5/40", 40), ("joker", 45),
    ("6/49", 17), ("6/49", 19), ("joker", 17), ("joker", 19),
])
def test_uncapped_large_wf_is_explicitly_skipped_before_cache_or_simulation(monkeypatch, game, pool):
    def forbidden(*args, **kwargs):
        raise AssertionError("Skipped replay must not build/read/write its cache or simulate")

    monkeypatch.setattr(wf, "_decision_sig", forbidden)
    monkeypatch.setattr(LotoBacktester, "run_retroactive_backtest", forbidden)
    flat, meta = wf.run_honest_walk_forward(
        history(game), game, pool, 100, use_cache=False, guarantee=4,
    )
    assert flat == []
    assert meta["large_pool_wf_skipped"]
    assert meta["skip_reason"] == "variant_cap_required"
    assert meta["n_test_draws"] == meta["n_predictions"] == 0
    assert meta["n_expected"] == 25
    assert meta["pool_size"] == pool
    assert "cache_file" not in meta
    with capture_ui() as captured:
        ui_hits._render_hits_4plus([], game, meta)
    assert "Stabilește un plafon" in captured.text()
    assert "procente istorice" in captured.text()
    assert "%" not in captured.text()


def test_capped_large_wf_rows_are_bounded_without_fabricating_samples(monkeypatch):
    monkeypatch.setattr(wf, "_LARGE_POOL_MAX_ROWS", 49)
    flat, meta = wf.run_honest_walk_forward(
        history(), "6/49", 49, 100, use_cache=False, guarantee=4, max_variants=2,
    )
    assert flat == []
    assert meta["skip_reason"] == "row_limit"
    assert meta["estimated_rows"] == 50
    assert meta["row_limit"] == 49
    assert "Redu plafonul" in ui_hits._large_pool_wf_note(meta)


def test_allowed_capped_wf_receives_full_pool_and_only_one_backtester(monkeypatch, tmp_path):
    calls = []

    def simulate(self, **options):
        calls.append(options)
        return []

    monkeypatch.setattr(LotoBacktester, "run_retroactive_backtest", simulate)
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(wf, "_decision_sig", lambda *a, **kw: "test")
    flat, meta = wf.run_honest_walk_forward(
        history(), "6/49", 49, 100, use_cache=False, guarantee=4, max_variants=2,
    )
    assert flat == []
    assert calls[0]["pool_size"] == 49
    assert calls[0]["max_variants"] == 2
    assert "large_pool_wf_skipped" not in meta
    assert "_pool49_" in meta["cache_file"]


def test_existing_540_pool19_uncapped_replay_keeps_its_behaviour(monkeypatch, tmp_path):
    calls = []

    def simulate(self, **options):
        calls.append(options)
        return []

    monkeypatch.setattr(LotoBacktester, "run_retroactive_backtest", simulate)
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(wf, "_decision_sig", lambda *a, **kw: "test")
    _, meta = wf.run_honest_walk_forward(
        history("5/40"), "5/40", 19, 100, use_cache=False, guarantee=4,
    )
    assert calls[0]["pool_size"] == 19
    assert calls[0]["max_variants"] == 0
    assert "large_pool_wf_skipped" not in meta
    assert "_pool19_" in meta["cache_file"]


def test_wf_pool_is_capped_at_its_actual_universe():
    _, meta = wf.run_honest_walk_forward(history(), "6/49", 99, 100, guarantee=4)
    assert meta["pool_size"] == 49


def test_large_wheel_key_has_its_own_namespace_only_above_19():
    assert wf._wheel_sig(49, "6/49", 4, None, 20) == "constructive_cover|g4|c4|cap20|rt1|lp1"
    assert "|lp1" not in wf._wheel_sig(19, "5/40", 4, None, 20)
    assert "|lp1" not in wf._wheel_sig(16, "6/49", 4, None, 20)


@pytest.mark.parametrize("game,pool", [
    ("loto_6_49", 17), ("loto_6_49", 19), ("loto_6_49", 49),
    ("joker_urna1", 17), ("joker_urna1", 19), ("joker_urna1", 45),
])
def test_unmeasured_leaderboard_does_not_relabel_base_pool_statistics(game, pool):
    frame = pd.DataFrame([{
        "game": game, "method": "frequency", "k16": 2.0,
        "avg_hits_topk": 0.6, "rate_3plus_k16": 0.2,
    }])
    with capture_ui() as captured:
        ui_bench._render_bench_leaderboard_slice(frame, game, pool, game)
    text = captured.text()
    assert f"pool {pool}" in text and "ultimul pool evaluat: 16" in text
    assert "nu descriu pool-ul cerut" in text
    assert "Wilson" not in text


@pytest.mark.parametrize("pool", [17, 19, 49])
def test_decision_note_identifies_the_pool_that_was_actually_benchmarked(monkeypatch, pool):
    entry = {"scorer": "frequency", "low_confidence": True}
    cfg = {"games": {"loto_6_49": {"auto_pilot_per_pool": {"k16": entry}}}}
    monkeypatch.setattr(selector, "_load_config", lambda *args: cfg)
    chosen = ui_bench._decision_entry("loto_6_49", pool)
    assert chosen["pool_substituted"] == {"requested": pool, "used": 16}
    monkeypatch.setattr(ui_results, "_decision_entry", lambda *args: chosen)
    notes = ui_results._decision_notes({"loto_6_49": {"method": "frequency", "pool_hint": pool}}, "loto_6_49", pool)
    assert "pool 16" in notes[0]
    assert "pool-ul de referință 16" in notes[1]


def test_540_pool19_retains_its_existing_decision_entry(monkeypatch):
    entry = {"scorer": "frequency", "low_confidence": True}
    cfg = {"games": {"loto_5_40": {"auto_pilot_per_pool": {"k19": entry}}}}
    monkeypatch.setattr(selector, "_load_config", lambda *args: cfg)
    assert ui_bench._decision_entry("loto_5_40", 19) == entry
    assert not ui_bench._extended_bench_pool("loto_5_40", 19)
    assert ui_bench._extended_bench_pool("loto_5_40", 20)


@pytest.mark.parametrize("game,pool,pick", [("6/49", 49, 6), ("5/40", 40, 5), ("joker", 45, 5)])
def test_full_urn_pool_odds_are_exact_without_subset_ticket_enumeration(monkeypatch, game, pool, pick):
    import covering.probability as probability

    def forbidden(*args):
        raise AssertionError("Large pools must not enumerate 2**pool masks")

    monkeypatch.setattr(probability, "wheel_hit_probabilities", forbidden)
    lines = ui_results._wheel_probability_lines(game, {
        "hard_core": list(range(1, pool + 1)), "variants": [list(range(1, pick + 1))],
    })
    assert "5+ numere în pool: 100.000%" in lines[0]
    assert "numai pool-ul" in lines[1]


def test_large_invalid_variant_does_not_get_theoretical_odds():
    lines = ui_results._wheel_probability_lines("6/49", {
        "hard_core": list(range(1, 50)), "variants": [[1, 1, 2, 3, 4, 5]],
    })
    assert "invalide" in lines[0]


def test_full_urn_is_not_misreported_as_scorer_degeneracy():
    assert ui_results._consecutive_pool_warning(list(range(1, 50)), universe_size=49) is None
    assert ui_results._consecutive_pool_warning(list(range(1, 26)), universe_size=49)


def test_large_route_is_explicitly_constructive_without_lr_or_minimum_claim():
    note = ui_results._large_pool_route_note("6/49", {"hard_core": list(range(1, 50))})
    assert "cover constructiv" in note
    assert "fără cod LR" in note
    assert "nu este demonstrat minim" in note
    assert ui_results._large_pool_route_note("6/49", {"hard_core": list(range(1, 17))}) is None


@pytest.mark.parametrize("scores", [None, {n: 1 / n for n in range(1, 26)}])
def test_full_ticket_remains_bounded_even_with_missing_ranking_tail(scores):
    kept, note = _ticket_pool(list(range(1, 50)), scores, 30, 6, 0, 10)
    assert len(kept) == 16
    assert "cel mult 16" in note
    if scores:
        assert kept == list(range(1, 17))
    else:
        assert "determinist" in note
