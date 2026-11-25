"""The stored decision is usable only with the rules, tickets and data it evaluated."""

import os
import shutil

import numpy as np
import pandas as pd

import ui_lr_budget
import ui_lr_rebench
from covering import lr_schemes
from covering.lr_budget import FOUR_PLUS, recommend_games
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark import lr_hypotheses as bench, methods
from loto_enterprise.core import lr_budget_forecast as forecast


def test_saved_selector_rechecks_executed_formula_roster_layout_and_history(monkeypatch):
    names = ["frequency", "random"]
    monkeypatch.setattr(bench, "_methods", lambda game: tuple(names))
    rng = np.random.default_rng(485)
    history = pd.DataFrame([rng.choice(range(1, 50), 6, replace=False) for _ in range(100)],
                           columns=[f"n{i}" for i in range(1, 7)])
    history["date"] = pd.date_range("2024-01-01", periods=len(history)).strftime("%d-%m-%Y")
    settings = dict(scheme=49, dynamic=True, inverse=False, restricted=True, lookback=100,
                    hit_target=4, joker_size=1, budget_lei=171, objective=FOUR_PLUS)
    report = bench.benchmark_lr_hypotheses("6/49", history, **settings)
    entry = {"game": "6/49", "source": "six.csv", "settings": settings, "result": report}
    profile = ui_lr_rebench._compact_profile(entry, "2026-11-25T12:00:00Z")
    assert profile and report["portfolio"] and "error" not in report["portfolio"]

    def play(budget=171):
        plan, = forecast.recommend_playable_games(
            budget, FOUR_PLUS, "6/49", {"6/49": ("six.csv", history)}, pool_codes={},
            number_range=(10, 40), joker_size=1, hit_target=4, lr_rebench_entries=[profile])
        assert plan["tickets"]
        return plan

    baseline = play()
    assert baseline["forecast"]["selection"]["source"] == "lr_rebench"
    assert baseline == play()
    signed = report["semantics"].copy()
    original, *meta = methods.METHODS["frequency"]
    def changed(draws_2d, max_num):
        weights = original(draws_2d, max_num)
        return {number: 1.0 - value for number, value in weights.items()}
    changed.__name__, changed.__qualname__, changed.__module__ = original.__name__, original.__qualname__, original.__module__
    with monkeypatch.context() as m:
        m.setitem(methods.METHODS, "frequency", (changed, *meta))
        assert play()["forecast"]["selection"].get("source") != "lr_rebench"
    names.append("compact_transition_prior")
    assert play()["forecast"]["selection"].get("source") != "lr_rebench"
    names.pop()
    assert play(172)["forecast"]["selection"].get("source") != "lr_rebench"
    before = ui_lr_budget._budget_input_snapshot(lambda _: {"histories": {"6/49": ("six.csv", history)}}, "6/49")[0]
    history.loc[0, "n1"] = next(n for n in range(1, 50) if n not in history.iloc[0].values)
    after = ui_lr_budget._budget_input_snapshot(lambda _: {"histories": {"6/49": ("six.csv", history)}}, "6/49")[0]
    assert before != after
    assert play()["forecast"]["selection"].get("source") != "lr_rebench"
    assert "Extragerile disponibile s-au schimbat" in ui_lr_rebench._report_compatibility(entry, lambda _: ("six.csv", history))
    assert entry["result"]["semantics"] == signed


def test_cached_range_report_matches_full_replay_after_earlier_draw_correction():
    from loto_enterprise.core import lr_range_analysis as analysis
    rng = np.random.default_rng(84)
    frame = pd.DataFrame([rng.choice(range(1, 50), 6, replace=False) for _ in range(100)],
                         columns=[f"n{i}" for i in range(1, 7)])
    frame["date"] = pd.date_range("2024-01-01", periods=len(frame)).strftime("%d-%m-%Y")
    def calculate():
        return analysis.compare_budget_ranges(171, FOUR_PLUS, "6/49", {"6/49": ("six.csv", frame)})
    analysis._replay.cache_clear()
    before = calculate()
    assert calculate() == before and analysis._replay.cache_info().hits == 1
    frame.loc[0, "n1"] = next(n for n in range(1, 50) if n not in frame.iloc[0].values)
    corrected = calculate()
    assert analysis._replay.cache_info().misses == 2
    analysis._replay.cache_clear()
    assert calculate() == corrected


def test_same_code_layout_change_invalidates_hot_catalog(tmp_path, monkeypatch):
    copied = tmp_path / "lr"
    shutil.copytree(lr_schemes.LR_DIR, copied)
    monkeypatch.setattr(lr_schemes, "LR_DIR", copied)
    lot = forecast.require_lottery("6/49", "RO")
    def signature():
        layout, = recommend_games(171, FOUR_PLUS, "6/49")
        scheme = resolve_lr_scheme("6/49", 49)
        return forecast.lr_semantics(lot, scheme, 4, layout=layout), scheme.variants
    before, old_rows = signature()
    assert signature() == (before, old_rows)
    source = copied / "LR_649_cod49.txt"
    data, original_time = source.read_bytes(), source.stat()
    assert b"1 2 3 5 7 8" in data
    source.write_bytes(data.replace(b"1 2 3 5 7 8", b"1 2 3 5 7 9", 1))
    os.utime(source, ns=(original_time.st_atime_ns, original_time.st_mtime_ns))
    after, new_rows = signature()
    assert len(old_rows) == len(new_rows) and old_rows != new_rows and before["scheme"] != after["scheme"]
    assert signature() == (after, new_rows)
    alternate = copied / "LR_649_cod48.txt"
    rows = alternate.read_text().splitlines()
    i = next(i for i, row in enumerate(rows) if row and row[0].isdigit())
    rows[i], rows[i+1] = rows[i+1], rows[i]
    alternate.write_text("\n".join(rows) + "\n")
    assert signature()[0]["scheme"] != after["scheme"]


def test_global_bench_is_bound_to_the_available_prefix_per_urn(monkeypatch):
    from loto_enterprise.benchmark import freshness
    from loto_enterprise.core import method_selector
    from loto_enterprise.core.lr_budget_forecast import _forecast_engine
    from loto_enterprise.core.lotteries import require_lottery
    import pandas as pd

    history = pd.DataFrame({
        "date": ["01-10-2026", "04-10-2026", "08-10-2026"],
        **{f"n{i}": [i, i + 5, i + 10] for i in range(1, 6)}, "joker": [1, 2, 3],
    })
    keys = ("joker_urna1", "joker_urna2")
    config = {"games": {key: {"overall_winner": "markov_lag2"} for key in keys},
              "_meta": {"engine_signature": freshness.compute_engine_signature(country="RO"),
                        "csv_signatures": {}}}
    for key in keys:
        fingerprint, rows = freshness._frame_signature(history.iloc[:2], freshness.GAMES_COLS_MAP[key])
        config["_meta"]["csv_signatures"][key] = {"hash": fingerprint, "rows": rows}

    lot = require_lottery("joker", "RO")
    load = method_selector._load_config
    monkeypatch.setattr(method_selector, "_load_config", lambda path=None: load(path) if isinstance(path, dict) else config)
    current = _forecast_engine(lot, ("test.csv", history), 70)
    assert set(current._lr_decision_snapshot) == set(keys)
    assert load(current._lr_decision_snapshot[keys[1]]) is config
    earlier = _forecast_engine(lot, ("test.csv", history.iloc[:1]), 100)
    assert not earlier._lr_decision_snapshot
    score = earlier._get_timesfm_scores(is_joker_drum=True)
    assert score == earlier._frequency_fallback_scores(is_joker_drum=True)
    assert earlier.audit["bench_winner"][keys[1]]["fallback"]

    corrected = history.copy()
    corrected.loc[0, "joker"] = 9
    assert set(_forecast_engine(lot, ("test.csv", corrected), 100)._lr_decision_snapshot) == {keys[0]}
    config["_meta"]["engine_signature"] = {}
    assert not _forecast_engine(lot, ("test.csv", history), 100)._lr_decision_snapshot
