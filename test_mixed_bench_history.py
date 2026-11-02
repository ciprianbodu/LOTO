"""Un bench mixt alege metoda potrivită pentru fiecare joc."""
from datetime import date, timedelta
from types import SimpleNamespace
import math
import pandas as pd
import pytest
import app_nicegui as app
from loto_enterprise.core import walk_forward_adapter as wf
from scripts.analysis.audit_output import capture_ui
from test_virtual_history_ui_2026_11_01 import virtual_setup


def fake_row(game, virtual):
    n = 6 if game == "6/49" else 5
    return SimpleNamespace(draw_index=10, draw_date="2026-10-10", variant=list(range(1,n+1)),
        hits=4, hits_union=4, variant_count=(math.comb(30,n) if virtual else 42),
        virtual_full_system=virtual, ticket_context={"actual": list(range(1,n+1)), "hard_core":list(range(1,31))})


def test_mixed_pool30_selects_algorithm_and_labels_for_each_game(virtual_setup, monkeypatch):
    for dataset in virtual_setup:
        dataset["tasks"][0].update(pool_size=30, guarantee=5, max_variants=0)
    monkeypatch.setitem(app.STATE, "virtual_wf_depth", 30)
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("mail"))
    calls = []
    def run(**kw):
        calls.append(kw)
        virtual = bool(kw.get("virtual_full_system"))
        return [fake_row(kw["game_type"], virtual)], {"virtual_full_system": virtual, "n_test_draws":1, "n_expected":1, "partial":False}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    assert app._start_virtual_history(automatic=True, simulated=True)
    by_game = {kw["game_type"]: kw for kw in calls}
    assert set(by_game) == {"6/49", "joker", "5/40"}
    assert by_game["6/49"]["compact_results"] is True and not by_game["6/49"].get("virtual_full_system")
    for game in ("joker", "5/40"):
        assert by_game[game]["virtual_full_system"] and not by_game[game].get("compact_results")
    assert all(kw["pool_size"] == 30 and kw["guarantee"] == 5 and kw["max_variants"] == 0 for kw in calls)
    assert [r["simulated"] for r in app.STATE["virtual_wf_results"]] == [True, False, False]
    assert app.STATE["retro"] == {"old": ["actual"]}
    with capture_ui() as view:
        app._render_virtual_history()
    assert "sisteme simulate și virtuale" in view.text()
    assert "una dintre cele simulate" in view.text()
    assert "Exemplul de variantă este construit după extragerea istorică" in view.text()
    tables=[t for t in view.walk() if t["kind"] == "table"]
    details=[t for t in tables if any(c["name"] == "example" for c in t["kwargs"]["columns"])]
    assert len(details) == 3
    example_labels=[next(c["label"] for c in t["kwargs"]["columns"] if c["name"] == "example") for t in details]
    assert "simulată" in example_labels[0]
    assert all("construit după extragere" in label for label in example_labels[1:])
    assert details[0]["kwargs"]["rows"][0]["variants"] == "42"
    assert all(t["kwargs"]["rows"][0]["variants"] == "142.506" for t in details[1:])


def test_mixed_real_virtual_backends_do_not_generate_tickets(virtual_setup, monkeypatch, tmp_path):
    import loto_enterprise.core.backtesting as backtesting
    from loto_engine import LotoEngine
    for dataset in virtual_setup:
        task = dataset["tasks"][0]
        task.update(pool_size=30, guarantee=5, max_variants=0)
        game=task["game_label"]
        draw_n=5 if game == "joker" else 6
        maximum={"6/49":49,"joker":45,"5/40":40}[game]
        rows=[[(i*7+j*5)%maximum+1 for j in range(draw_n)] for i in range(50)]
        frame=pd.DataFrame(rows, columns=[f"n{i}" for i in range(1,draw_n+1)])
        frame["date"]=[(date(2020,1,1)+timedelta(days=i)).strftime("%d-%m-%Y") for i in range(50)]
        if game == "joker": frame["joker"]=[1+i%20 for i in range(50)]
        dataset["df_json"]=frame.to_json(orient="split")
    monkeypatch.setitem(app.STATE,"virtual_wf_depth",30)
    monkeypatch.setattr(wf,"CACHE_DIR",tmp_path)
    monkeypatch.setattr(backtesting,"_WF_PROBE_STEPS",1)
    monkeypatch.setattr(backtesting,"_WF_SERIAL_MAX_MS",1e9)
    monkeypatch.setattr(LotoEngine,"generate_predictions",lambda *_a, **_k: pytest.fail("physical ticket creation"))
    original=wf.run_honest_walk_forward
    def run(**kw):
        if kw["game_type"] == "6/49":
            return [fake_row("6/49",False)], {"n_test_draws":1,"n_expected":1,"partial":False}
        return original(**kw)
    monkeypatch.setattr(wf,"run_honest_walk_forward",run)
    assert app._start_virtual_history(automatic=True,simulated=True)
    records=app.STATE["virtual_wf_results"]
    assert len(records)==3
    assert all("error" not in r for r in records)
    for r in records[1:]:
        assert r["meta"]["n_test_draws"] > 0 and all(x.virtual_full_system and x.variant_count == 142506 for x in r["flat"])


def test_compact_cache_is_written_with_windows_valid_name_and_reused(wf_backend):
    from pathlib import Path
    from test_wf_incremental import history
    kw = dict(df_source=history(50), game_type="6/49", pool_size=8,
              backtest_depth_percent=10, guarantee=3, max_variants=3, compact_results=True)
    first, meta = wf_backend.run_honest_walk_forward(**kw)
    assert first and not meta["partial"]
    path = Path(meta["cache_file"])
    assert path.is_file()
    assert not any(character in path.name for character in '<>:"/\\|?*')
    second, cached = wf_backend.run_honest_walk_forward(**kw)
    assert cached["from_cache"] and len(second) == len(first)


from test_wf_incremental import wf as wf_backend


def test_autopilot_mixed_oversized_starts_history_not_invalid_job(virtual_setup, monkeypatch):
    for ds in virtual_setup:
        ds["tasks"][0].update(pool_size=30, guarantee=5, max_variants=0)
    monkeypatch.setitem(app.SETTINGS, "autopilot_after_bench", True)
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app._bench_freshness_panel, "refresh", lambda: None)
    monkeypatch.setattr(app, "apply_autopilot_and_generate", lambda: pytest.fail("invalid physical job"))
    calls=[]
    monkeypatch.setattr(app, "_start_virtual_history", lambda **kw: calls.append(kw) or True)
    app._on_bench_finished()
    assert calls == [{"automatic": True, "simulated": True, "materialize_full": True}]
    assert "prea mare" in app.STATE["_virtual_physical_notice"]
