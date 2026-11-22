"""Integration contracts for the archived interval scorer and causal benchmark."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from loto_enterprise.benchmark import methods, runner
from loto_enterprise.core import method_selector
from loto_enterprise.core.ranking import rank_by_score
from scripts.analysis import legacy_methods_2026_11_20 as archive


METHOD = "interval_extrema_k16"


@pytest.fixture
def replay_interval_scorer(monkeypatch):
    monkeypatch.setitem(methods.METHODS, METHOD, archive.METHODS[METHOD])


def _draws(n: int) -> np.ndarray:
    rng = np.random.default_rng(2817)
    return np.array(
        [rng.choice(np.arange(1, 50), 6, replace=False) for _ in range(n)],
        dtype=np.int64,
    )


def _game(n: int) -> runner.GameDef:
    # Two draws share every day; indices retain the full prefix coordinate system.
    return runner.GameDef(
        "loto_6_49",
        "6/49",
        "unused.csv",
        [],
        49,
        6,
        history_cutoffs=tuple(i - i % 2 for i in range(n)),
    )


def test_dispatch_passes_chronology_only_to_marked_methods(monkeypatch):
    seen = []

    def contextual(draws, max_num, *, history_cutoffs=None):
        seen.append((draws.copy(), max_num, history_cutoffs))
        return {n: float(n) for n in range(1, max_num + 1)}

    contextual._uses_history_cutoffs = True
    monkeypatch.setitem(
        methods.METHODS, "context_probe", (contextual, "test", False, "")
    )
    draws = _draws(8)
    cutoffs = _game(8).history_cutoffs
    scores, duration = methods.call_method(
        "context_probe", draws, 49, history_cutoffs=cutoffs
    )
    assert seen[0][2] == cutoffs
    np.testing.assert_array_equal(seen[0][0], draws)
    assert seen[0][1] == 49 and duration >= 0
    assert rank_by_score(scores, 3) == [49, 48, 47]
    assert methods.method_meta("context_probe")["uses_history_cutoffs"] is True


def test_dispatch_preserves_legacy_two_argument_contract(monkeypatch):
    seen = []

    def legacy(draws, max_num):
        seen.append((len(draws), max_num))
        return {n: float(n) for n in range(1, max_num + 1)}

    monkeypatch.setitem(methods.METHODS, "legacy_probe", (legacy, "test", False, ""))
    methods.call_method(
        "legacy_probe", _draws(8), 49, history_cutoffs=_game(8).history_cutoffs
    )
    assert seen == [(8, 49)]
    assert not methods.method_meta("legacy_probe").get("uses_history_cutoffs", False)


@pytest.mark.parametrize("with_dates", [True, False])
def test_runner_passes_only_the_observable_cutoff_prefix(monkeypatch, with_dates):
    draws = _draws(16)
    game = _game(16)
    if not with_dates:
        game = replace(game, history_cutoffs=())
    seen = []

    def contextual(past, max_num, *, history_cutoffs=None):
        seen.append((past.copy(), history_cutoffs))
        return {n: float(n) for n in range(1, max_num + 1)}

    contextual._uses_history_cutoffs = True
    monkeypatch.setitem(
        methods.METHODS, "context_probe", (contextual, "test", False, "")
    )
    fold, _ = runner._evaluate_fold(
        "context_probe", draws[:9], draws[9:], game, block_size=1
    )
    assert not fold.failed, fold.error
    assert fold.n_eval == 7
    expected_ends = [8, 10, 12, 14] if with_dates else list(range(9, 16))
    assert [len(past) for past, _ in seen] == expected_ends
    for (past, supplied), end in zip(seen, expected_ends, strict=True):
        np.testing.assert_array_equal(past, draws[:end])
        if with_dates:
            assert supplied == game.history_cutoffs[:end]
        else:
            assert supplied is None or supplied == ()


def test_runner_keeps_legacy_call_method_invocation_unchanged(monkeypatch):
    draws = _draws(16)
    seen = []

    def old_adapter(name, past, max_num):
        seen.append((name, len(past), max_num))
        return {n: float(n) for n in range(1, max_num + 1)}, 0.0

    monkeypatch.setattr(runner, "call_method", old_adapter)
    fold, _ = runner._evaluate_fold(
        "frequency", draws[:13], draws[13:], _game(16), block_size=1
    )
    assert not fold.failed, fold.error
    assert fold.n_eval == 3
    assert seen == [("frequency", 12, 49), ("frequency", 14, 49)]


def test_actual_policy_ignores_same_day_target_and_future(monkeypatch, replay_interval_scorer):
    draws = _draws(224)
    game = _game(len(draws))
    captured = []

    def record_call(name, past, max_num, *, history_cutoffs=None):
        result = methods.call_method(
            name, past, max_num, history_cutoffs=history_cutoffs
        )
        captured.append(result[0])
        return result

    monkeypatch.setattr(runner, "call_method", record_call)
    original, _ = runner._evaluate_fold(
        METHOD, draws[:220], draws[220:], game, block_size=1
    )
    assert not original.failed, original.error
    assert original.n_eval == 4
    original_scores = captured.copy()
    # One prediction per distinct observable prefix, reused for both targets.
    assert len(original_scores) == 2
    assert len(rank_by_score(original_scores[0], 16)) == 16

    # Change both targets on the first test day and every later outcome. Neither
    # prediction for that day may observe the changed draw or its same-day peer.
    changed = draws.copy()
    changed[220:] = 50 - changed[220:]
    captured.clear()
    perturbed, _ = runner._evaluate_fold(
        METHOD, changed[:220], changed[220:], game, block_size=1
    )
    assert not perturbed.failed, perturbed.error
    assert perturbed.n_eval == 4
    assert len(captured) == 2
    assert captured[0] == original_scores[0]


def test_warmup_is_reported_as_partial_evaluation(replay_interval_scorer):
    draws = _draws(204)
    fold, _ = runner._evaluate_fold(
        METHOD, draws[:198], draws[198:], _game(204), block_size=1
    )
    assert not fold.failed, fold.error
    assert fold.n_test == 6
    # The first eligible historical targets are day 200/201. Their outcomes
    # become observable at day 202, so only the final two targets can be scored.
    assert fold.n_eval == 2
    assert fold.blocks == 6


def test_archived_interval_method_is_scoped_to_649():
    scorer, family, *_ = archive.METHODS[METHOD]
    assert scorer._uses_history_cutoffs
    assert "experimental" in family.lower()
    assert METHOD in archive.eligible_methods_for_game("loto_6_49")
    assert all(
        METHOD not in archive.eligible_methods_for_game(game)
        for game in ("loto_5_40", "joker_urna1", "joker_urna2")
    )
    assert METHOD not in methods.list_methods()
    assert not methods.method_meta(METHOD)["available"]


def test_legacy_interval_decision_uses_the_deterministic_fallback(monkeypatch):
    from loto_enterprise.benchmark.methods import score_frequency

    assert method_selector._sanitize_production_name(METHOD, context="test") is None
    monkeypatch.setattr(method_selector, "get_winner_name", lambda *args: METHOD)
    assert method_selector.get_scorer_for_game("loto_6_49", 16) is score_frequency


@pytest.mark.parametrize("rate", [None, float("nan"), 0.1234])
def test_ui_does_not_present_an_archived_method_as_an_active_experiment(rate):
    import ui_bench

    assert ui_bench._experimental_method_caption(METHOD, 16, rate) == ""
    line = ui_bench._bench_rate_line(
        4, single_pick=False, wilson=0.0848, raw_primary=0.3012,
        raw_4=0.0948, rnd_primary=0.2981, rnd_4=0.0796,
    )
    assert "Wilson 4+: 8.48%" in line
    assert "brut 3+: 30.12%" in line
    assert "brut 4+: 9.48%" in line


@pytest.mark.parametrize("with_candidate", [False, True])
def test_ui_filters_archived_interval_and_keeps_random_as_baseline(
    monkeypatch, tmp_path, with_candidate
):
    import pandas as pd

    import app_nicegui as app
    import ui_bench
    import ui_runtime
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui

    state_file = tmp_path / "unused_ui_state.json"
    for module in (app, ui_bench, ui_runtime):
        monkeypatch.setattr(module, "UI_STATE_FILE", state_file)
    monkeypatch.setattr(ui_bench, "_LB_ROWS_MEMO", {})
    monkeypatch.setattr(ui_bench, "_decision_entry", lambda *args: {})
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 4)
    candidates = [(METHOD, 0.99), ("random", 0.08)]
    if with_candidate:
        candidates.append(("frequency", 0.12))
    rows = []
    for percentile in (10, 30, 60, 100):
        for name, rate in candidates:
            rows.append(
                dict(
                    game="loto_6_49",
                    method=name,
                    percentile=percentile,
                    n_test=1000,
                    n_eval=1000,
                    blocks=1000,
                    runtime_sec=0.01,
                    is_random=False,
                    failed=False,
                    k16=2.0,
                    rate_3plus_k16=rate,
                    rate_4plus_k16=rate,
                    tiebreak_k16=0.0,
                )
            )
    with capture_ui() as captured:
        app._render_bench_leaderboard_slice(
            pd.DataFrame(rows), "loto_6_49", 16, "6/49", top_n=20
        )
    assert METHOD not in captured.text()
    assert not state_file.exists()
    if with_candidate:
        assert captured.ranking() == ["frequency"]
        assert "random" in captured.text()
    else:
        assert captured.ranking() == []
        assert "Nicio metodă eligibilă" in captured.text()
