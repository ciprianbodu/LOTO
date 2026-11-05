"""Istoricul automat simulează sistemele cu buget și nu dublează extragerile."""
from types import SimpleNamespace
import pytest
import app_nicegui as app
from loto_enterprise.core import walk_forward_adapter as wf
from scripts.analysis.audit_output import capture_ui
from test_virtual_history_ui_2026_11_01 import virtual_setup
from test_wf_incremental import wf as wf_backend, history


def row(index, hits, pool_hits, variant, count=None):
    return SimpleNamespace(draw_index=index, draw_date=f"2026-10-{index:02d}", variant=variant,
        hits=hits, hits_union=pool_hits, variant_count=count, virtual_full_system=False,
        ticket_context={"actual": list(range(1, pool_hits + 1)) + list(range(30, 36 - pool_hits)), "hard_core": list(range(1, 11))})


def test_simulated_history_uses_settings_and_aggregates_variants_per_draw(virtual_setup, monkeypatch):
    virtual_setup[:] = virtual_setup[:1]
    task = virtual_setup[0]["tasks"][0]
    task.update(pool_size=10, guarantee=4, max_variants=3)
    monkeypatch.setitem(app.STATE, "virtual_wf_depth", 30)
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("mail"))
    calls = []
    def run(**kw):
        calls.append(kw)
        return [row(10, 2, 5, [1,2,7,8,9,10]), row(10, 5, 5, [1,2,3,4,5,8]),
                row(11, 3, 3, [1,2,3,7,8,9])], {"n_test_draws": 2, "n_expected": 2, "partial": False}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    assert app._start_virtual_history(automatic=True, simulated=True)
    assert calls[0]["pool_size"] == 10 and calls[0]["guarantee"] == 4 and calls[0]["max_variants"] == 3
    assert calls[0]["compact_results"] is True and not calls[0].get("virtual_full_system")
    assert app.STATE["retro"] == {"old": ["actual"]}
    with capture_ui() as view:
        app._render_virtual_history()
    assert "nu stabilește că au fost jucate" in view.text()
    assert "schema redusă LR" in view.text()
    tables = [node for node in view.walk() if node["kind"] == "table"]
    detail = next(t for t in tables if any(c["name"] == "example" for c in t["kwargs"]["columns"]))
    summary = next(t for t in tables if any(c["name"] == "threshold" for c in t["kwargs"]["columns"]))
    assert [r["draw_index"] for r in detail["kwargs"]["rows"]] == [10]
    first = next(r for r in detail["kwargs"]["rows"] if r["draw_index"] == 10)
    from covering.lr_schemes import best_hits, lr_schemes
    scheme = lr_schemes("6/49", 10)[0]
    assert first["best_hits"] == best_hits(scheme, range(1, 11), [1,2,3,4,5,30])
    assert first["variants"] == str(scheme.size) and scheme.label in first["system"]
    fifth = next(r for r in summary["kwargs"]["rows"] if r["threshold"] == "5+")
    assert fifth["pool"] == "1 (50.00%)"
    found = sum(best_hits(scheme, range(1, 11), numbers) >= 5 for numbers in ([1,2,3,4,5,30], [1,2,3,30,31,32]))
    assert fifth["ticket"] == f"{found} ({found / 2 * 100:.2f}%)"


def test_simulated_resource_skip_displays_detail(virtual_setup, monkeypatch):
    virtual_setup[:] = virtual_setup[:1]
    virtual_setup[0]["tasks"][0].update(guarantee=4, max_variants=50)
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **kw: ([], {
        "large_pool_wf_skipped": True, "skip_detail": "Limita explicită a fost depășită.", "partial": True}))
    app._start_virtual_history(automatic=True, simulated=True)
    with capture_ui() as view:
        app._render_virtual_history()
    assert "Limita explicită a fost depășită" in view.text()


def test_bench_without_autopilot_starts_reduced_simulation(virtual_setup, monkeypatch):
    virtual_setup[0]["tasks"][0].update(guarantee=4, max_variants=100)
    monkeypatch.setitem(app.SETTINGS, "autopilot_after_bench", False)
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app._bench_freshness_panel, "refresh", lambda: None)
    calls = []
    monkeypatch.setattr(app, "_start_virtual_history", lambda **kw: calls.append(kw) or True)
    app._on_bench_finished()
    assert calls == [{"automatic": True, "simulated": True, "materialize_full": True}]


def test_compact_simulation_uses_separate_cache_and_same_draw_maxima(wf_backend, tmp_path):
    kw = dict(df_source=history(50), game_type="6/49", pool_size=8,
              backtest_depth_percent=10, guarantee=3, max_variants=3)
    full, full_meta = wf_backend.run_honest_walk_forward(**kw)
    compact, compact_meta = wf_backend.run_honest_walk_forward(**kw, compact_results=True)
    assert full and compact
    assert full_meta["cache_file"] != compact_meta["cache_file"]
    by_draw = {}
    for item in full:
        by_draw.setdefault(item.draw_index, []).append(item)
    assert len(compact) == len(by_draw)
    for item in compact:
        items = by_draw[item.draw_index]
        assert item.hits == max(x.hits for x in items)
        assert item.variant_count == len(items)
        assert item.hits_union == items[0].hits_union



def test_real_large_pool_guard_still_applies_to_compact_simulation(wf_backend):
    flat, meta = wf_backend.run_honest_walk_forward(df_source=history(50), game_type="6/49",
        pool_size=30, guarantee=4, max_variants=100_001, compact_results=True)
    assert not flat
    assert meta["large_pool_wf_skipped"] and meta["skip_reason"] == "resource_limit"
    assert "plafon" in meta["skip_detail"].lower()
