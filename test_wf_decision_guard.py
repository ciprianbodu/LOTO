"""Walk-forward never shows another method's validation under the displayed pool.

Audit 2026-10-08: after a Re-Bench or a 3+/4+ switch made after generation, the
live WF scored every step with the NEW decision and stored it under the result
produced by the old one, with no warning.
"""

import pandas as pd
import pytest

import app_nicegui as app
from loto_enterprise.core import walk_forward_adapter as wf


class InlineThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def wf_state(monkeypatch):
    frame = pd.DataFrame({"date": ["24-09-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    data = {
        "hard_core": list(range(1, 17)),
        "pool_size": 16,
        "guarantee": 4,
        "audit": {"bench_winner": {"loto_6_49": {"method": "markov_lag2"}}},
    }
    for key, value in {
        "results": ([("loto_6_49.csv", {"6/49": data})], 1),
        "result_sources": {"loto_6_49.csv": frame},
        "retro": {},
        "retro_meta": {},
        "wf_seq": 0,
        "wf_user_cancel": False,
        "job_start_time": None,
    }.items():
        monkeypatch.setitem(app.STATE, key, value)
    for name in ("_save_report_file", "_finalize_pipeline"):
        monkeypatch.setattr(app, name, lambda: None)
    monkeypatch.setattr(app.threading, "Thread", InlineThread)
    monkeypatch.setattr(app, "_echo_mismatch", lambda *_: False)
    calls = []

    def run_wf(**kwargs):
        calls.append(kwargs)
        return [{"draw_index": 1}], {"partial": False, "n_test_draws": 1, "n_expected": 1}

    monkeypatch.setattr(wf, "run_honest_walk_forward", run_wf)
    return calls


def test_a_moved_decision_skips_the_game_and_says_so(wf_state, monkeypatch):
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: False)
    app._start_walk_forward()
    assert wf_state == []
    assert "loto_6_49.csv_6/49" not in app.STATE["retro"]
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"decision_moved": True}
    assert "decizia de bench s-a schimbat după generare" in app._build_report()


def test_the_same_decision_validates_as_before(wf_state, monkeypatch):
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    app._start_walk_forward()
    assert len(wf_state) == 1
    assert app.STATE["retro"]["loto_6_49.csv_6/49"] == [{"draw_index": 1}]
    assert not app.STATE["retro_meta"]["loto_6_49.csv_6/49"]["decision_changed"]


def test_a_decision_moved_during_the_run_is_flagged(wf_state, monkeypatch):
    answers = iter([True, False])
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: next(answers))
    app._start_walk_forward()
    assert len(wf_state) == 1
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"]["decision_changed"]


def test_a_result_without_the_method_in_its_audit_runs_as_before(wf_state, monkeypatch):
    data = app.STATE["results"][0][0][1]["6/49"]
    monkeypatch.setitem(data, "audit", {})
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: False)
    app._start_walk_forward()
    assert len(wf_state) == 1


def test_a_failed_run_does_not_keep_the_previous_mark(wf_state, monkeypatch):
    """Revizie 2026-10-08: marcajul „decizia s-a schimbat” al rezultatului
    anterior rămânea când pasul curent cădea, deci panoul îl atribuia celui nou."""
    app.STATE["retro_meta"]["loto_6_49.csv_6/49"] = {"decision_moved": True}
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)

    def broken(**_kwargs):
        raise RuntimeError("pas căzut")

    monkeypatch.setattr(wf, "run_honest_walk_forward", broken)
    app._start_walk_forward()
    assert "loto_6_49.csv_6/49" not in app.STATE["retro_meta"]
    assert "decizia de bench s-a schimbat după generare" not in app._build_report()
