"""Automatic history after Re-Bench, with no physical full-system allocation."""
from types import SimpleNamespace

import pytest
import app_nicegui as app
from loto_enterprise.core import walk_forward_adapter as wf
from test_virtual_history_ui_2026_11_01 import virtual_setup
from test_recovered_manual_wf_2026_11_01 import recovered


def test_successful_bench_starts_pool30_history_without_physical_generation(virtual_setup, monkeypatch):
    calls = []
    def run(**kw):
        calls.append(kw)
        return [SimpleNamespace(virtual_full_system=True)], {"virtual_full_system": True}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app._bench_freshness_panel, "refresh", lambda: None)
    monkeypatch.setattr(app, "apply_autopilot_and_generate", lambda: pytest.fail("physical generation"))
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("email"))
    monkeypatch.setitem(app.SETTINGS, "autopilot_after_bench", True)
    monkeypatch.setitem(app.STATE, "datasets", [("game", object())])
    monkeypatch.setitem(app.STATE, "analysis_expanded", False)
    app._on_bench_finished()
    assert app.STATE["analysis_expanded"] is True
    assert "prea mare" in app.STATE["_virtual_physical_notice"]
    assert [(c["game_type"], c["guarantee"], c["pool_size"]) for c in calls] == [
        ("6/49", 6, 30), ("joker", 5, 30), ("5/40", 5, 30)]
    assert all(c["virtual_full_system"] for c in calls)
    assert len(app.STATE["virtual_wf_results"]) == 3
    assert app.STATE.get("_autopilot_after_virtual") is None


@pytest.mark.parametrize("exit_code,expect", [(0, True), (1, False), (None, False)])
def test_only_confirmed_bench_success_launches_followup(monkeypatch, exit_code, expect):
    calls = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: calls.append(True))
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_kw: None)
    monkeypatch.setitem(app.STATE, "bench_was_running", True)
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    monkeypatch.setitem(app.STATE, "_bench_process", SimpleNamespace(poll=lambda: exit_code))
    monkeypatch.setitem(app.STATE, "_bench_run_identity", (123, "b" * 32, "0" * 64))
    import bench_outcome
    monkeypatch.setattr(bench_outcome, "peek_exit", lambda *_: exit_code)
    monkeypatch.setattr(bench_outcome, "claim_exit", lambda *_: exit_code)
    monkeypatch.setattr(bench_outcome, "mark_processed", lambda *_: None)
    app._observe_bench_activity(False)
    assert bool(calls) is expect


def test_ordinary_non_full_system_keeps_autopilot(virtual_setup, monkeypatch):
    virtual_setup[0]["tasks"][0]["guarantee"] = 4
    for dataset in virtual_setup:
        dataset["tasks"][0]["pool_size"] = 8
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app._bench_freshness_panel, "refresh", lambda: None)
    monkeypatch.setitem(app.SETTINGS, "autopilot_after_bench", True)
    monkeypatch.setitem(app.STATE, "datasets", [("game", object())])
    calls = []
    monkeypatch.setattr(app, "apply_autopilot_and_generate", lambda: calls.append(True))
    app._on_bench_finished()
    assert calls == [True]


def test_recovered_wf_delays_once_then_starts_after_it_finishes(virtual_setup, monkeypatch):
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app._bench_freshness_panel, "refresh", lambda: None)
    monkeypatch.setattr(app, "_virtual_decision_fingerprint", lambda countries: (3, tuple(countries)))
    monkeypatch.setitem(app.STATE, "datasets", [("game", object())])
    monkeypatch.setitem(app.STATE, "wf_running", True)
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    monkeypatch.setitem(app.STATE, "_autopilot_after_virtual", None)
    app._VIRTUAL_WF_CANCEL_REQUESTED.clear()
    app._on_bench_finished()
    assert app.STATE.get("_bench_history_pending") is not None
    starts = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: starts.append(True))
    app._continue_after_automatic_history(False)
    assert not starts
    app.STATE["wf_running"] = False
    app._continue_after_automatic_history(False)
    app._continue_after_automatic_history(False)
    assert starts == [True]


@pytest.mark.parametrize("change", ["cancel", "decision", "config"])
def test_pending_generation_does_not_run_after_change(virtual_setup, monkeypatch, change):
    cfg = app._build_config_json()
    countries = ("RO",)
    decision = (3, (("RO", "old"),))
    monkeypatch.setitem(app.STATE, "_bench_history_pending", None)
    monkeypatch.setitem(app.STATE, "_autopilot_after_virtual", (cfg, countries, decision))
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    monkeypatch.setattr(app, "_virtual_decision_fingerprint", lambda _: (3, (("RO", "new"),)) if change == "decision" else decision)
    if change == "config":
        monkeypatch.setattr(app, "_build_config_json", lambda: "changed")
    if change == "cancel":
        app._VIRTUAL_WF_CANCEL_REQUESTED.set()
    else:
        app._VIRTUAL_WF_CANCEL_REQUESTED.clear()
    starts = []
    monkeypatch.setattr(app, "apply_autopilot_and_generate", lambda: starts.append(True))
    app._continue_after_automatic_history(False)
    assert not starts
    app._VIRTUAL_WF_CANCEL_REQUESTED.clear()


def test_pending_generation_is_claimed_only_once(virtual_setup, monkeypatch):
    cfg = app._build_config_json()
    countries = ("RO",)
    decision = (3, (("RO", "same"),))
    monkeypatch.setitem(app.STATE, "_bench_history_pending", None)
    monkeypatch.setitem(app.STATE, "_autopilot_after_virtual", (cfg, countries, decision))
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    monkeypatch.setattr(app, "_virtual_decision_fingerprint", lambda _: decision)
    app._VIRTUAL_WF_CANCEL_REQUESTED.clear()
    starts = []
    monkeypatch.setattr(app, "apply_autopilot_and_generate", lambda: starts.append(True))
    app._continue_after_automatic_history(False)
    app._continue_after_automatic_history(False)
    assert starts == [True]


def test_launch_bench_refuses_running_validation(monkeypatch):
    monkeypatch.setitem(app.STATE, "wf_running", True)
    monkeypatch.setitem(app.STATE, "virtual_wf_running", False)
    monkeypatch.setitem(app.STATE, "active_job_id", None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_kw: None)
    monkeypatch.setattr(app.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("bench started during validation"))
    app._launch_bench([], "Re-Bench")


def test_pending_bench_history_prevents_wf_shutdown(recovered, monkeypatch):
    monkeypatch.setattr(app, "_bench_freshness_panel", SimpleNamespace(refresh=lambda: None))
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app, "_build_config_json", lambda: '{"datasets": []}')
    monkeypatch.setattr(app, "_virtual_decision_fingerprint", lambda _: (3, ()))
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setitem(app.STATE, "bench_was_running", False)
    monkeypatch.setitem(app.STATE, "_bench_history_pending", None)
    finalized = []
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: finalized.append(True))
    def run(**_kw):
        # Bench completes while the prior validation is still running.
        app._on_bench_finished()
        assert app.STATE["_bench_history_pending"] is not None
        return [{"draw_index": 1}], {"partial": False, "n_test_draws": 1, "n_expected": 1}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    app._start_walk_forward()
    assert not finalized
    assert app.STATE["_bench_history_pending"] is not None


def test_failed_target_rebuild_does_not_start_history(virtual_setup, monkeypatch):
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: {"6/49": 3})
    monkeypatch.setattr(app, "_rebuild_decision_for_target", lambda _target: None)
    monkeypatch.setattr(app._bench_freshness_panel, "refresh", lambda: None)
    monkeypatch.setattr(app, "_start_virtual_history", lambda **_kw: pytest.fail("old decision was used"))
    monkeypatch.setattr(app, "apply_autopilot_and_generate", lambda: pytest.fail("old decision was used"))
    app._on_bench_finished()
