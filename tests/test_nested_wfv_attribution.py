"""Count the method actually used when chronological validation falls back."""

from types import SimpleNamespace

import numpy as np
import pytest

from scripts.analysis import nested_wfv_2026_11_18 as nested
import ui_nested_wfv


def test_active_report_credits_frequency_when_the_selected_scorer_failed(monkeypatch):
    key = "loto_6_49"
    fallback = "compact_transition_prior"
    methods = ("frequency", fallback, "random")
    days = np.arange(250, 380)
    days[-1] += 1
    cuts = np.arange(381)
    cuts[379] = 378
    matrix = np.tile(np.arange(1, 7), (len(cuts), 1))
    dates = np.datetime64("2020-01-01") + cuts.astype("timedelta64[D]")
    monkeypatch.setattr(nested, "read_draws", lambda *_: (matrix, dates, cuts, days, 49, "fixture"))
    monkeypatch.setattr(nested, "active_methods", lambda _: methods)

    def outcomes(matrix, cuts, days, universe, size, lookup, methods, caller, fallback_days,
                 *, unavailable, max_run):
        assert max_run == 2
        hits = np.zeros((len(matrix) - days[0], len(methods)), dtype=np.uint8)
        # The calibrated winner falls back for two draws on the first failed date,
        # then one more single-draw date. Its other predictions remain its own.
        hits[:, 1] = 4
        unavailable[-2:, 1] = True
        fallback_days[fallback] = 2
        hits[-3:, 1] = 0
        return hits, hits, hits, np.zeros_like(unavailable, dtype=np.uint8)

    monkeypatch.setattr(nested, "outcome_matrices", outcomes)
    monkeypatch.setattr(nested, "fold_decision", lambda *_: 0)
    report = nested.study_game(key, scheme_code=48, hit_target=4, active_catalog=True)
    arm = report["arms"]["ticket_frozen"]
    assert report["method_draws_effective"] is True
    assert report["fallback_days"][fallback] == 2
    assert arm["selection_count"] == 1
    expected = report["n"] - 3
    assert arm["ticket_4plus"] == expected
    assert arm["method_draws"] == {"frequency": 3, fallback: expected, "random": 0}


@pytest.mark.parametrize("current_counts", [False, True])
def test_selected_policy_omits_legacy_usage_when_fallbacks_occur(monkeypatch, current_counts):
    labels = []

    class Widget:
        def classes(self, _style):
            return self

        def props(self, _props):
            return self

    def label(text):
        labels.append(text)
        return Widget()

    monkeypatch.setattr(ui_nested_wfv, "ui", SimpleNamespace(label=label, table=lambda **_: Widget()))
    key = "loto_6_49"
    report = {
        "key": key, "scheme": None, "n": 3, "n_dates": 2, "start": "2026-01-01", "end": "2026-01-02",
        "catalog_version": ui_nested_wfv.registry.catalog_version_for_game(key),
        "candidate_methods": ["frequency", "compact_transition_prior"],
        "fallback_days": {"compact_transition_prior": 1},
        "scored_days": 102,
        "hit_target": 4, "selection_metric": "ticket_4plus", "max_consecutive_run": 2,
        "best_policy": "ticket_frozen",
        "arms": {
            "ticket_frozen": {
                "primary": 2, "ticket_4plus": 2,
                "method_draws": {"frequency": 1, "compact_transition_prior": 2}
                if current_counts else {"compact_transition_prior": 3},
            },
        },
    }
    if current_counts:
        report["method_draws_effective"] = True
    ui_nested_wfv._nested_results(report)

    assert any("Cea mai bună în perioada testată" in text for text in labels)
    usage = [text for text in labels if text.startswith("Extrageri evaluate cu fiecare metodă")]
    if current_counts:
        assert len(usage) == 1 and usage[0].endswith("frequency (1), compact_transition_prior (2)")
    else:
        assert not usage
