"""Persistență locală fără a relansa un walk-forward la pornire."""
import copy
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

import app_nicegui as app
import rebench_history_store as store
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
from loto_enterprise.core import walk_forward_adapter as wf
from TESTE.test_virtual_history_ui_2026_11_01 import virtual_setup
from TESTE.test_wf_incremental import wf as wf_backend, history


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_FILE", tmp_path / "history.sqlite3")
    return store.STORE_FILE


def sample():
    row = WalkForwardResult(draw_index=5, draw_date="22-10-2026", variant=[4, 5, 7, 8, 17, 29], hits=5, hits_union=5,
        ticket_context={"actual": [4,5,7,8,17,29], "hard_core": list(range(1, 17)), "audit": {"timesfm_predictions": {1: np.float64(0.25)}}},
        variant_count=8008, variant_prize_groups={"version":1, "total_variants":8008, "groups":[{"variant":[4,5,7,8,17,29], "count":8008}]})
    return [{"name":"Loto 6/49", "game":"6/49", "game_id":"6/49", "source":"history.csv", "source_sha256":"a"*64,
             "pool":16, "guarantee":6, "depth":30, "flat":[row], "meta":{"partial":True}, "simulated":True,
             "max_variants":0, "wheel_condition":0, "task":{"pool_size":16}}]


def test_missing_store_is_not_created_on_load(local_store, monkeypatch):
    monkeypatch.setattr(store.sqlite3, "connect", lambda *_a, **_kw: pytest.fail("SQLite opened"))
    assert store.load() is None
    assert not local_store.exists()


def test_snapshot_roundtrip_preserves_groups_types_and_uses_read_only_connection(local_store, monkeypatch):
    run = "a" * 32
    records = sample()
    store.mark_launch(run)
    assert store.save(run, records, "Parțial", complete=False, provenance={"decision": (4, (("RO", "abc"),))})
    real_connect = sqlite3.connect
    calls = []
    def connect(path, *args, **kwargs):
        calls.append((path, kwargs))
        assert kwargs.get("uri") is True and "mode=ro" in path
        return real_connect(path, *args, **kwargs)
    monkeypatch.setattr(store.sqlite3, "connect", connect)
    result, previous = store.load()
    assert calls and previous is False and result["complete"] is False
    actual = result["records"][0]["flat"][0]
    assert actual.variant_prize_groups == records[0]["flat"][0].variant_prize_groups
    assert actual.ticket_context["audit"]["timesfm_predictions"] == {1: 0.25}
    assert result["provenance"]["decision"] == (4, (("RO", "abc"),))


def test_old_run_cannot_overwrite_new_run_and_previous_is_labeled(local_store):
    old, new = "a" * 32, "b" * 32
    store.mark_launch(old, 1)
    assert store.save(old, sample(), "old", complete=True, provenance={})
    store.mark_launch(new, 2)
    assert store.load()[1] is True
    assert not store.save(old, sample(), "old late", complete=True, provenance={})
    assert store.save(new, sample(), "new", complete=True, provenance={})
    assert store.load()[0]["status"] == "new" and store.load()[1] is False
    assert not store.save(old, sample(), "old even later", complete=True, provenance={})
    # Windows: niciun handle SQLite nu rămâne deschis după lansare/citire/scriere.
    moved = local_store.with_name("moved.sqlite3")
    local_store.rename(moved)
    moved.rename(local_store)


def test_invalid_new_snapshot_never_overwrites_previous(local_store):
    old, new = "a" * 32, "b" * 32
    store.mark_launch(old, 1)
    store.save(old, sample(), "old", complete=True, provenance={})
    store.mark_launch(new, 2)
    bad = sample()
    bad[0]["flat"][0].ticket_context["audit"] = {(1, 2): 3}
    with pytest.raises(TypeError):
        store.save(new, bad, "new", complete=True, provenance={})
    payload, previous = store.load()
    assert payload["status"] == "old" and previous is True


def test_corrupt_store_is_rejected_without_wf_or_network(local_store, monkeypatch):
    run = "a" * 32
    store.mark_launch(run)
    store.save(run, sample(), "old", complete=True, provenance={})
    with sqlite3.connect(str(local_store)) as db:
        db.execute("UPDATE history SET payload='{}'")
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **_: pytest.fail("WF started"))
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("mail"))
    assert not app._restore_rebench_history()
    assert "indisponibil" in app.STATE["virtual_wf_status"]


def test_invalid_payload_type_is_reported_without_recalculation(local_store, monkeypatch):
    run = "a" * 32
    store.mark_launch(run)
    store.save(run, sample(), "old", complete=True, provenance={})
    with sqlite3.connect(str(local_store)) as db:
        db.execute("UPDATE history SET payload=?", (sqlite3.Binary(b"invalid"),))
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **_: pytest.fail("WF started"))
    assert not app._restore_rebench_history()
    assert "indisponibil" in app.STATE["virtual_wf_status"]


def test_startup_restores_only_saved_data_without_calculation(local_store, monkeypatch):
    run = "a" * 32
    store.mark_launch(run)
    store.save(run, sample(), "Istoric salvat", complete=False, provenance={"decision":"known"})
    store.mark_launch("b" * 32)
    for key in ("virtual_wf_results", "virtual_wf_previous", "virtual_wf_status", "virtual_wf_provenance", "virtual_wf_store_error", "analysis_expanded", "virtual_wf_dirty"):
        monkeypatch.setitem(app.STATE, key, None)
    for name in ("init_job_queue", "_load_settings", "_discard_finished_bench_at_startup", "_migrate_legacy_finalized_marker", "_recover_completed_job"):
        monkeypatch.setattr(app, name, lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app, "is_fresh_ui_start", lambda: False)
    monkeypatch.setattr(app, "get_active_job", lambda: None)
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **_: pytest.fail("WF started"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("mail"))
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown"))
    app._startup()
    assert app.STATE["virtual_wf_results"][0]["pool"] == 16
    assert app.STATE["virtual_wf_previous"] is True
    assert app.STATE["virtual_wf_status"] == "Istoric salvat"


def test_failed_run_without_steps_keeps_previous_snapshot_and_result(local_store, virtual_setup, monkeypatch):
    old, new = "a" * 32, "b" * 32
    store.mark_launch(old, 1)
    store.save(old, sample(), "old", complete=True, provenance={})
    previous = sample()
    monkeypatch.setitem(app.STATE, "virtual_wf_results", previous)
    monkeypatch.setitem(app.STATE, "_bench_seen_run", new)
    store.mark_launch(new, 2)
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **_: (_ for _ in ()).throw(ValueError("date invalide")))
    assert app._start_virtual_history(automatic=True, simulated=True, materialize_full=True)
    assert app.STATE["virtual_wf_results"] is previous
    assert app.STATE["virtual_wf_previous"] is True
    assert "date invalide" in app.STATE["virtual_wf_status"]
    assert store.load()[0]["status"] == "old" and store.load()[1] is True


def test_partial_failure_keeps_other_games_and_marks_incomplete(local_store, virtual_setup, monkeypatch):
    old, new = "a" * 32, "b" * 32
    store.mark_launch(old, 1)
    store.save(old, sample(), "old", complete=True, provenance={})
    monkeypatch.setitem(app.STATE, "_bench_seen_run", new)
    store.mark_launch(new, 2)
    virtual_setup[:] = virtual_setup[:2]
    virtual_setup[0]["tasks"][0].update(pool_size=16, guarantee=6)
    virtual_setup[1]["tasks"][0].update(pool_size=12, guarantee=5)
    real = sample()[0]["flat"][0]
    def run(**kw):
        if kw["game_type"] == "joker":
            raise ValueError("Joker indisponibil")
        return [real], {"virtual_full_system":False, "n_test_draws":1, "n_expected":1, "partial":False}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    assert app._start_virtual_history(automatic=True, simulated=True, materialize_full=True)
    saved, previous = store.load()
    assert not previous and saved["complete"] is False
    assert len(saved["records"]) == 2 and saved["records"][1]["error"] == "Joker indisponibil"
    assert saved["records"][0]["flat"][0].variant_prize_groups == real.variant_prize_groups
    assert "parțial" in saved["status"]


def test_cancelled_after_one_game_saves_partial_without_claiming_completion(local_store, virtual_setup, monkeypatch):
    run_id = "d" * 32
    store.mark_launch(run_id)
    monkeypatch.setitem(app.STATE, "_bench_seen_run", run_id)
    virtual_setup[:] = virtual_setup[:2]
    virtual_setup[0]["tasks"][0].update(pool_size=16, guarantee=6)
    row = sample()[0]["flat"][0]
    def run(**_kw):
        app._VIRTUAL_WF_CANCEL_REQUESTED.set()
        return [row], {"virtual_full_system":False, "n_test_draws":1, "n_expected":2, "partial":True}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    try:
        assert app._start_virtual_history(automatic=True, simulated=True, materialize_full=True)
        saved, _ = store.load()
        assert saved["complete"] is False and len(saved["records"]) == 1
        assert "anulat" in saved["status"] and "parțial" in saved["status"]
    finally:
        app._VIRTUAL_WF_CANCEL_REQUESTED.clear()


def test_failed_local_save_does_not_block_calculation(local_store, virtual_setup, monkeypatch):
    run_id = "e" * 32
    store.mark_launch(run_id)
    monkeypatch.setitem(app.STATE, "_bench_seen_run", run_id)
    virtual_setup[:] = virtual_setup[:1]
    virtual_setup[0]["tasks"][0].update(pool_size=16, guarantee=6)
    row = sample()[0]["flat"][0]
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **_: ([row], {"virtual_full_system":False, "n_test_draws":1, "n_expected":1, "partial":False}))
    monkeypatch.setattr(store, "save", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disc plin")))
    assert app._start_virtual_history(automatic=True, simulated=True, materialize_full=True)
    assert app.STATE["virtual_wf_results"][0]["flat"] == [row]
    assert not app.STATE["virtual_wf_running"]
    assert "disc plin" in app.STATE["virtual_wf_store_error"]


@pytest.mark.parametrize(("game", "pool", "guarantee", "count", "maximum"), [
    ("6/49", 16, 6, 8008, 49), ("joker", 12, 5, 792, 45), ("5/40", 19, 5, 11628, 40),
])
def test_real_wf_snapshot_roundtrip_with_prize_groups(local_store, wf_backend, game, pool, guarantee, count, maximum):
    source = history(50)
    rng = np.random.default_rng(42)
    source[[f"n{i}" for i in range(1, 7)]] = [sorted(rng.choice(np.arange(1, maximum + 1), 6, replace=False)) for _ in range(50)]
    if game == "joker":
        source["joker"] = [int(x) for x in rng.integers(1, 21, size=50)]
    rows, meta = wf_backend.run_honest_walk_forward(source, game_type=game, pool_size=pool,
        backtest_depth_percent=2, guarantee=guarantee, max_variants=0, compact_results=True)
    assert meta["n_test_draws"] == 1 and rows[0].variant_count == count and rows[0].variant_prize_groups
    record = {"name":game, "game":game, "game_id":game, "source":"history.csv", "pool":pool,
              "guarantee":guarantee, "depth":30, "flat":rows, "meta":meta, "simulated":True}
    run = "c" * 32
    store.mark_launch(run)
    assert store.save(run, [record], "real", complete=True, provenance={"source_hash":"d"*64})
    restored = store.load()[0]["records"][0]["flat"][0]
    assert restored.variant_prize_groups == rows[0].variant_prize_groups
    assert restored.ticket_context == rows[0].ticket_context
    assert restored.variant_count == count
