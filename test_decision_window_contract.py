"""Ferestrele din martorul random sunt contractul tuturor metricilor deciziei."""

from __future__ import annotations

import pandas as pd
import pytest

from loto_enterprise.benchmark import curated, decision, methods


WINDOWS = ((10, 100), (30, 300), (60, 600), (100, 1000))
STRAY = (15, 25, 35, 45, 55, 65)


@pytest.fixture(autouse=True)
def _synthetic_methods(monkeypatch):
    for name in ("contract_alpha", "contract_beta"):
        monkeypatch.setitem(
            methods.METHODS, name,
            (lambda draws, max_num: {}, "test", False, "doar teste"),
        )
    monkeypatch.setattr(curated, "load_per_game", lambda: {})
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)


def _row(method, pct, n, rate, *, game="loto_6_49", k=10, target=3, **extra):
    row = {
        "game": game, "method": method, "percentile": pct,
        "n_test": n, "n_eval": n, "blocks": n,
        "is_random": False, "failed": False, "runtime_sec": 0.1,
        f"k{k}": 1.0, f"rate_{target}plus_k{k}": rate, f"tiebreak_k{k}": 0.0,
    }
    row.update(extra)
    return row


def _matrix(alpha, beta, **geometry):
    return [
        _row(method, pct, n, rate, **geometry)
        for pct, n in WINDOWS
        for method, rate in (("random", 0.09), ("contract_alpha", alpha), ("contract_beta", beta))
    ]


def _decide(rows, game="loto_6_49", k=10, draw_n=6):
    return decision.decide_optimal_config_for_pool(pd.DataFrame(rows), game, k, draw_n)


def test_foreign_windows_cannot_qualify_a_method_that_loses_every_common_window():
    base = decision.expected_random_rate(49, 6, 10, 3)
    rows = _matrix(base + 0.01, base - 0.02)
    original = _decide(rows)
    extra = [_row("contract_beta", p, 10000, 0.9) for p in STRAY]
    after = _decide(rows + extra)
    assert original["scorer"] == "contract_alpha"
    assert original["qualifying_methods"] == 1
    assert after == original


def test_foreign_losses_cannot_remove_a_qualified_method():
    rows = _matrix(0.15, 0.13)
    original = _decide(rows)
    extra = [_row("contract_alpha", p, 10000, 0.0) for p in STRAY]
    assert original["scorer"] == "contract_alpha"
    assert original["low_confidence"] is False
    assert _decide(rows + extra) == original


def test_foreign_tied_windows_cannot_disqualify_a_method():
    rows = _matrix(0.15, 0.13)
    original = _decide(rows)
    extra = [_row("contract_alpha", p, 10000, 0.9, tiebreak_k10=1.0) for p in STRAY]
    assert _decide(rows + extra) == original


def test_foreign_untied_windows_cannot_rescue_a_tiebreak_dependent_method():
    rows = _matrix(0.4, 0.13)
    for row in rows:
        if row["method"] == "contract_alpha":
            row["tiebreak_k10"] = 0.8
    original = _decide(rows)
    extra = [_row("contract_alpha", p, 10000, 0.9) for p in STRAY]
    assert original["scorer"] == "contract_beta"
    assert original["tiebreak_dependent"] == [
        {"method": "contract_alpha", "tiebreak_fraction": 0.8}
    ]
    assert _decide(rows + extra) == original


def test_partial_foreign_window_does_not_make_common_windows_incomplete():
    rows = _matrix(0.15, 0.13)
    extra = _row("contract_alpha", 50, 10000, 0.9, n_eval=10)
    assert _decide(rows + [extra]) == _decide(rows)


def test_selected_sim_depth_ignores_foreign_windows():
    rows = _matrix(0.15, 0.13)
    rows += [_row("contract_alpha", 50, 10000, 0.9)]
    cfg = _decide(rows)
    assert cfg["scorer"] == "contract_alpha"
    assert cfg["sim_depth_pct"] in {p for p, _n in WINDOWS}
    assert cfg["expected_windows"] == [10, 30, 60, 100]


def test_fallback_winner_and_all_its_metrics_ignore_foreign_windows():
    rows = _matrix(0.04, 0.02)
    original = _decide(rows)
    extra = [_row("contract_beta", p, 10000, 0.08) for p in STRAY]
    assert original["low_confidence"] is True
    assert original["scorer"] == "contract_alpha"
    assert _decide(rows + extra) == original


@pytest.mark.parametrize("low_confidence", [False, True])
def test_all_gate_metrics_receive_only_common_windows(monkeypatch, low_confidence):
    rows = _matrix(0.04, 0.02) if low_confidence else _matrix(0.15, 0.13)
    rows += [_row("contract_beta", p, 10000, 0.08) for p in STRAY]
    seen = set()
    for name in (
        "_windows_method_beats_random", "_weighted_mean_lift",
        "pooled_wilson_distinct", "pooled_mean", "tiebreak_fraction",
    ):
        original = getattr(decision, name)

        def checked(frame, *args, _name=name, _original=original, **kwargs):
            if not frame.empty:
                assert set(frame["percentile"]) <= {p for p, _n in WINDOWS}
                seen.add(_name)
            return _original(frame, *args, **kwargs)

        monkeypatch.setattr(decision, name, checked)
    cfg = _decide(rows)
    assert cfg["low_confidence"] is low_confidence
    assert {"_windows_method_beats_random", "pooled_wilson_distinct", "tiebreak_fraction"} <= seen
    assert ("pooled_mean" if low_confidence else "_weighted_mean_lift") in seen


@pytest.mark.parametrize(
    ("game", "draw_n", "k", "target"),
    [("loto_5_40", 6, 10, 4), ("joker_urna2", 1, 1, 1), ("bg_toto2", 6, 10, 3)],
)
def test_window_contract_also_applies_to_other_geometries(game, draw_n, k, target):
    geo = {"game": game, "k": k, "target": target}
    rows = _matrix(0.15, 0.13, **geo)
    extra = [_row("contract_beta", p, 10000, 0.9, **geo) for p in STRAY]
    assert _decide(rows + extra, game, k, draw_n) == _decide(rows, game, k, draw_n)


def test_random_anchors_windows_even_when_per_game_excludes_it(monkeypatch):
    monkeypatch.setattr(curated, "load_per_game", lambda: {"loto_6_49": ["contract_alpha"]})
    rows = _matrix(0.15, 0.25)
    extra = [_row("contract_beta", p, 10000, 0.9) for p in STRAY]
    original = _decide(rows)
    assert original["scorer"] == "contract_alpha"
    assert original["expected_windows"] == [10, 30, 60, 100]
    assert _decide(rows + extra) == original


def test_empirical_random_baseline_uses_same_window_contract():
    geo = {"game": "contract_unknown"}
    rows = _matrix(0.15, 0.13, **geo)
    extra = [_row("contract_beta", p, 10000, 0.9, **geo) for p in STRAY]
    original = _decide(rows, "contract_unknown")
    assert original["baseline_source"] == "empirical_random"
    assert _decide(rows + extra, "contract_unknown") == original


def test_common_window_helper_uses_union_when_random_has_too_few_windows():
    rows = [_row("random", 10, 100, 0.09), _row("random", 30, 300, 0.09)]
    rows += [_row("contract_alpha", p, n, 0.15) for p, n in WINDOWS]
    assert decision.common_window_percentiles(pd.DataFrame(rows), "rate_3plus_k10") == {10, 30, 60, 100}


def test_common_window_helpers_preserve_legacy_no_contract_frames():
    frame = pd.DataFrame([{"method": "contract_alpha", "k10": 1.0}])
    assert decision.common_window_percentiles(frame, "rate_3plus_k10") == set()
    assert decision.filter_window_percentiles(frame, set()) is frame


def test_foreign_only_method_is_reported_incomplete():
    rows = [r for r in _matrix(0.15, 0.13) if r["method"] != "contract_beta"]
    rows.append(_row("contract_beta", 50, 10000, 0.9))
    cfg = _decide(rows)
    assert cfg["scorer"] == "contract_alpha"
    assert {"method": "contract_beta", "missing_windows": [10, 30, 60, 100]} in cfg["incomplete_methods"]


def test_row_order_does_not_change_window_contract_or_decision():
    rows = _matrix(0.15, 0.13)
    rows += [_row("contract_beta", p, 10000, 0.9) for p in STRAY]
    original = _decide(rows)
    assert _decide(list(reversed(rows))) == original
