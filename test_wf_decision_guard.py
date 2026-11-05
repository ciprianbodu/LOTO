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


def test_partial_log_does_not_assume_contiguous_recent_draws(wf_state, monkeypatch, caplog):
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **_: (
        [{"draw_index": 1}], {"partial": True, "n_test_draws": 1, "n_expected": 3,
                              "newest_missing": True},
    ))
    app._start_walk_forward()
    assert "pot exista goluri între extragerile validate" in caplog.text
    assert "acoperă extragerile RECENTE" not in caplog.text


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
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"wf_error": True}
    assert "Walk-forward: eșuat" in app._build_report()
    assert "decizia de bench s-a schimbat după generare" not in app._build_report()


@pytest.mark.parametrize("fails", [False, True])
def test_old_run_does_not_write_over_new_generation(wf_state, monkeypatch, fails):
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)

    def replaced(**_kwargs):
        app.STATE["wf_seq"] += 1
        app.STATE["retro_meta"]["loto_6_49.csv_6/49"] = {"new": True}
        app.STATE["wf_progress"] = 0.23
        app.STATE["wf_status"] = "validarea nouă"
        app.STATE["wf_deadline"] = 123.0
        _kwargs["progress_cb"](0.8, 8, 10)
        assert _kwargs["should_cancel"]()
        assert app.STATE["wf_progress"] == 0.23
        assert app.STATE["wf_status"] == "validarea nouă"
        assert app.STATE["wf_deadline"] == 123.0
        if fails:
            raise RuntimeError("vechi")
        return [{"draw_index": 1}], {"partial": False}

    monkeypatch.setattr(wf, "run_honest_walk_forward", replaced)
    app._start_walk_forward()
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"new": True}
    assert app.STATE["wf_progress"] == 0.23
    assert app.STATE["wf_status"] == "validarea nouă"
    assert app.STATE["wf_deadline"] == 123.0


def test_cancelled_run_does_not_create_a_failure_mark(wf_state, monkeypatch):
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)

    def cancelled(**_kwargs):
        app.STATE["wf_user_cancel"] = True
        raise RuntimeError("anulat")

    monkeypatch.setattr(wf, "run_honest_walk_forward", cancelled)
    app._start_walk_forward()
    assert "loto_6_49.csv_6/49" not in app.STATE["retro_meta"]
    assert "anulat" in app.STATE["wf_status"]


def test_replacement_between_check_and_publication_cannot_store_old_results(wf_state, monkeypatch):
    calls = 0
    finalized = []
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: finalized.append(True))

    def decision(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            with app.STATE_LOCK:
                app.STATE["wf_seq"] += 1
                app.STATE["retro"] = {"new": ["new result"]}
                app.STATE["retro_meta"] = {"new": {"new": True}}
                app.STATE["wf_progress"] = 0.31
                app.STATE["wf_status"] = "validarea nouă"
        return False

    monkeypatch.setattr(app, "_decision_moved_since_generation", decision)
    app._start_walk_forward()
    assert app.STATE["retro"] == {"new": ["new result"]}
    assert app.STATE["retro_meta"] == {"new": {"new": True}}
    assert app.STATE["wf_progress"] == 0.31
    assert app.STATE["wf_status"] == "validarea nouă"
    assert not finalized


def test_replacement_during_budget_marking_does_not_modify_new_state(wf_state, monkeypatch):
    import time as real_time

    class Clock:
        now = 1_000_000.0
        def time(self):
            return self.now
        def __getattr__(self, name):
            return getattr(real_time, name)

    clock = Clock()
    monkeypatch.setattr(app, "time", clock)
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    frame = app.STATE["result_sources"]["loto_6_49.csv"]
    data = app.STATE["results"][0][0][1]["6/49"]

    class ReplacedName(str):
        def __format__(self, spec):
            with app.STATE_LOCK:
                app.STATE["wf_seq"] += 1
                app.STATE["retro"] = {"new": ["new result"]}
                app.STATE["retro_meta"] = {"new": {"new": True}}
                app.STATE["wf_status"] = "validarea nouă"
            return super().__format__(spec)

    second = ReplacedName("second.csv")
    monkeypatch.setitem(app.STATE, "result_sources", {"loto_6_49.csv": frame, second: frame})
    monkeypatch.setattr(app, "_iter_wf_jobs", lambda *_: [
        ("loto_6_49.csv", "6/49", data), (second, "6/49", data),
    ])
    old_run = wf.run_honest_walk_forward
    def slow(**kwargs):
        clock.now += 10_000.0
        return old_run(**kwargs)
    monkeypatch.setattr(wf, "run_honest_walk_forward", slow)
    app._start_walk_forward()
    assert app.STATE["retro"] == {"new": ["new result"]}
    assert app.STATE["retro_meta"] == {"new": {"new": True}}
    assert app.STATE["wf_status"] == "validarea nouă"


def test_finalization_serializes_with_replacement(wf_state, monkeypatch):
    import _thread
    import threading

    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    started, acquired = threading.Event(), threading.Event()
    observed = []

    def replacement():
        started.set()
        with app.STATE_LOCK:
            acquired.set()

    def save():
        _thread.start_new_thread(replacement, ())
        assert started.wait(2)
        observed.append(acquired.wait(0.1))

    monkeypatch.setattr(app, "_save_report_file", save)
    app._start_walk_forward()
    assert observed == [False]
    assert acquired.wait(2)


def test_cancel_during_report_saving_prevents_shutdown(wf_state, monkeypatch, tmp_path):
    import _thread
    import threading
    import psutil

    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app, "_bench_pid_status", lambda: (None, False))
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / "pid")
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    monkeypatch.setitem(app.STATE, "_shutdown_initiated", False)
    done = threading.Event()
    errors, scheduled = [], []

    def cancel():
        try:
            app.cancel_all()
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    def request_cancel():
        _thread.start_new_thread(cancel, ())
        assert app._WF_CANCEL_REQUESTED.wait(2)

    def save():
        request_cancel()

    def finalize():
        scheduled.append(True)

    monkeypatch.setattr(app, "_save_report_file", save)
    monkeypatch.setattr(app, "_finalize_pipeline", finalize)
    app._start_walk_forward()
    assert done.wait(3) and not errors
    assert not scheduled
    assert not app.STATE["_shutdown_initiated"]


def test_one_failed_game_does_not_hide_a_later_success(wf_state, monkeypatch):
    frame = app.STATE["result_sources"]["loto_6_49.csv"]
    first = app.STATE["results"][0][0][1]["6/49"]
    second = {**first, "audit": {}}
    monkeypatch.setitem(app.STATE, "results", ([
        ("loto_6_49.csv", {"6/49": first}),
        ("loto_5_40.csv", {"5/40": second}),
    ], 2))
    monkeypatch.setitem(app.STATE, "result_sources", {
        "loto_6_49.csv": frame, "loto_5_40.csv": frame,
    })
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)

    def run(**kwargs):
        if kwargs["game_type"] == "6/49":
            raise RuntimeError("pas căzut")
        return [{"draw_index": 1}], {"partial": False, "n_test_draws": 1, "n_expected": 1}

    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    app._start_walk_forward()
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"wf_error": True}
    assert app.STATE["retro"]["loto_5_40.csv_5/40"] == [{"draw_index": 1}]


def test_a_budget_stop_marks_the_games_that_never_started(wf_state, monkeypatch):
    """Audit 2026-10-16: cu bugetul terminat după Joker, 5/40 și 6/49 nu aveau
    nicio intrare: panoul și raportul tăceau, iar starea WF se golea."""
    import time as _time

    class Clock:
        now = 1_000_000.0

        def time(self):
            return self.now

        def __getattr__(self, name):
            return getattr(_time, name)

    clock = Clock()
    monkeypatch.setattr(app, "time", clock)
    frame = app.STATE["result_sources"]["loto_6_49.csv"]
    joker = {
        "hard_core": list(range(1, 17)),
        "pool_size": 16,
        "guarantee": 4,
        "hard_core_joker": [7],
        "audit": {"bench_winner": {"joker_urna1": {"method": "frequency"}}},
    }
    six = app.STATE["results"][0][0][1]
    monkeypatch.setitem(
        app.STATE,
        "results",
        ([("joker.csv", {"joker": joker}), ("loto_6_49.csv", six)], 1),
    )
    monkeypatch.setitem(
        app.STATE, "result_sources", {"joker.csv": frame, "loto_6_49.csv": frame}
    )
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    real = wf.run_honest_walk_forward

    def slow(**kwargs):
        clock.now += 10_000.0  # bugetul de timp se termină în primul joc
        return real(**kwargs)

    monkeypatch.setattr(wf, "run_honest_walk_forward", slow)
    app._start_walk_forward()
    assert len(wf_state) == 1  # numai Joker
    assert app.STATE["retro"]["joker.csv_joker"] == [{"draw_index": 1}]
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"budget_skipped": True}
    assert "loto_6_49.csv_6/49" not in app.STATE["retro"]
    assert "un joc" not in app.STATE["wf_status"]
    assert "1 joc fără validare" in app.STATE["wf_status"]
    assert "neînceput — bugetul de timp" in app._build_report()
