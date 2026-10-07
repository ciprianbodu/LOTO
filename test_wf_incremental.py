"""Walk-forward: pașii vechi se refolosesc la o extragere nouă și se reafișează la pornire.

Un pas WF fără stare vede numai extragerile dinaintea zilei țintei. Când
istoricul nou doar adaugă rânduri la coadă, pașii validării anterioare ale
aceleiași chei sunt identici cu cei pe care i-ar calcula o rulare completă.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest


def history(n: int = 60, seed: int = 20261007) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        [sorted(rng.choice(np.arange(1, 50), 6, replace=False)) for _ in range(n)],
        columns=[f"n{i}" for i in range(1, 7)],
    )
    df.insert(0, "date", pd.date_range("2025-01-01", periods=n).strftime("%d-%m-%Y"))
    return df


@pytest.fixture
def wf(monkeypatch, tmp_path):
    """Cache WF și decizie izolate; pași secvențiali, ca rularea să fie numărabilă."""
    import loto_enterprise.core.backtesting as bt
    import loto_enterprise.core.method_selector as ms
    import loto_enterprise.core.walk_forward_adapter as wfa

    decision = tmp_path / "best_methods.json"
    decision.write_text(json.dumps({"games": {}}), encoding="utf-8")
    monkeypatch.setattr(ms, "_DEFAULT_CONFIG_PATH", decision)
    monkeypatch.setattr(ms, "_CONFIG", None)
    monkeypatch.setattr(ms, "_CONFIG_PATH_USED", None)
    monkeypatch.setattr(ms, "_CONFIG_MTIME", -1.0)
    monkeypatch.setattr(wfa, "CACHE_DIR", tmp_path / "wf")
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 1)
    return wfa


@pytest.fixture
def computed(monkeypatch):
    """Extragerile pe care rularea le calculează efectiv (nu le ia din cache)."""
    from loto_enterprise.core.backtesting import LotoBacktester

    real = LotoBacktester.run_retroactive_backtest
    seen: list[int] = []

    def spy(self, *args, **kwargs):
        preds = real(self, *args, **kwargs)
        seen.extend(int(p.draw_index) for p in preds)
        return preds

    monkeypatch.setattr(LotoBacktester, "run_retroactive_backtest", spy)
    return seen


def _rows(flat):
    return sorted(
        (int(r.draw_index), tuple(r.variant), r.hits, r.hits_union, r.target_draw_date)
        for r in flat
    )


ARGS = dict(game_type="6/49", pool_size=10, backtest_depth_percent=20.0, guarantee=3)


def test_appended_draws_reuse_the_previous_steps_and_match_a_full_run(
    wf, computed, monkeypatch, tmp_path
):
    df = history(60)
    old_flat, old_meta = wf.run_honest_walk_forward(df.iloc[:58], **ARGS)
    assert not old_meta["partial"] and old_meta["n_test_draws"] == 11
    computed.clear()

    flat, meta = wf.run_honest_walk_forward(df, **ARGS)
    # Fereastra nouă: ultimele 12 extrageri (48..59). Din cache vin 48..57.
    assert meta["reused_previous_history"] == 10
    assert sorted(set(computed)) == [58, 59]
    assert meta["n_test_draws"] == meta["n_expected"] == 12 and not meta["partial"]
    assert {int(r.draw_index) for r in flat} == set(range(48, 60))

    # Aceleași variante și aceleași hituri ca o rulare completă, de la zero.
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path / "wf_full")
    computed.clear()
    full, full_meta = wf.run_honest_walk_forward(df, **ARGS)
    assert sorted(set(computed)) == list(range(48, 60))
    assert "reused_previous_history" not in full_meta
    assert _rows(flat) == _rows(full)

    # Al doilea apel pe același istoric: cache exact, nimic calculat.
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path / "wf")
    computed.clear()
    again, again_meta = wf.run_honest_walk_forward(df, **ARGS)
    assert again_meta["from_cache"] and computed == []
    assert _rows(again) == _rows(full)


def test_a_corrected_past_draw_reuses_nothing(wf, computed):
    df = history(60)
    wf.run_honest_walk_forward(df.iloc[:58], **ARGS)
    cols = [f"n{i}" for i in range(1, 7)]
    fixed = df.copy()
    row = [int(v) for v in fixed.loc[30, cols]]
    row[0] = min(set(range(1, 50)) - set(row))
    fixed.loc[30, cols] = sorted(row)
    computed.clear()
    _, meta = wf.run_honest_walk_forward(fixed, **ARGS)
    assert "reused_previous_history" not in meta
    assert sorted(set(computed)) == list(range(48, 60))


def test_an_inserted_older_draw_reuses_nothing(wf, computed):
    """Rândul adăugat la coada CSV-ului, dar cu dată veche: sortarea îl mută în interior."""
    df = history(60)
    wf.run_honest_walk_forward(df, **ARGS)
    late = df.iloc[[5]].copy()
    late["date"] = "15-01-2025"
    late[[f"n{i}" for i in range(1, 7)]] = [[2, 9, 17, 23, 31, 44]]
    computed.clear()
    _, meta = wf.run_honest_walk_forward(pd.concat([df, late], ignore_index=True), **ARGS)
    assert "reused_previous_history" not in meta
    assert len(set(computed)) == 12


def test_an_inserted_draw_is_caught_even_when_target_dates_still_match(wf, computed):
    """Coada ferestrei într-o singură zi: data țintă a pașilor nu se schimbă la
    decalaj, dar extragerile lor da. Prefixul intern (extrageri, cutoff-uri,
    amprentă) respinge refolosirea."""
    df = history(60)
    df.loc[40:, "date"] = "01-06-2025"
    wf.run_honest_walk_forward(df, **ARGS)
    late = df.iloc[[5]].copy()
    late["date"] = "15-01-2025"
    late[[f"n{i}" for i in range(1, 7)]] = [[2, 9, 17, 23, 31, 44]]
    computed.clear()
    _, meta = wf.run_honest_walk_forward(pd.concat([df, late], ignore_index=True), **ARGS)
    assert "reused_previous_history" not in meta
    assert computed


def test_the_stored_row_count_is_the_only_length_tried(wf, monkeypatch):
    """Amprenta include lungimea: cu `history_rows`, celelalte lungimi nu pot
    corespunde, deci nu se mai calculează (un candidat străin costa ~0,3 s)."""
    df = history(60)
    old = wf._csv_hash(df.iloc[:58], "6/49")
    real = wf._csv_hash
    hashed = []
    monkeypatch.setattr(wf, "_csv_hash", lambda d, g: hashed.append(len(d)) or real(d, g))
    assert wf._prefix_rows(df, "6/49", old, 58) == 58 and hashed == [58]
    hashed.clear()
    assert wf._prefix_rows(df, "6/49", "nu-corespunde", 58) is None and hashed == [58]
    hashed.clear()
    assert wf._prefix_rows(df, "6/49", old, 60) is None and hashed == []
    assert wf._prefix_rows(df, "6/49", old) == 58 and hashed == [59, 58]


def test_a_decision_rewritten_during_the_reuse_search_is_flagged(
    wf, computed, monkeypatch, tmp_path
):
    """Pașii refolosiți sunt ai scorerului vechi; dacă decizia se rescrie înainte
    de pașii noi, rezultatul amestecat nu se salvează sub cheia veche."""
    import loto_enterprise.core.method_selector as ms

    df = history(60)
    wf.run_honest_walk_forward(df.iloc[:58], **ARGS)
    real = wf._previous_history_steps

    def rewrite_then_search(*args, **kwargs):
        ms._DEFAULT_CONFIG_PATH.write_text(
            json.dumps({"games": {}, "_meta": {"rewritten": True}}), encoding="utf-8"
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(wf, "_previous_history_steps", rewrite_then_search)
    _, meta = wf.run_honest_walk_forward(df, **ARGS)
    assert meta["reused_previous_history"] == 10 and meta["decision_changed"]
    assert not any(wf._csv_hash(df, "6/49") in f.name for f in (tmp_path / "wf").iterdir())


def test_other_settings_do_not_borrow_steps(wf, computed):
    df = history(60)
    wf.run_honest_walk_forward(df.iloc[:58], **ARGS)
    computed.clear()
    _, meta = wf.run_honest_walk_forward(df, **{**ARGS, "pool_size": 11})
    assert "reused_previous_history" not in meta
    assert len(set(computed)) == 12


def test_cache_only_never_computes_or_writes(wf, computed, tmp_path):
    df = history(60)
    flat, meta = wf.run_honest_walk_forward(df, cache_only=True, **ARGS)
    assert flat == [] and meta["cache_miss"] and computed == []
    assert not list((tmp_path / "wf").glob("*.pkl"))

    full, _ = wf.run_honest_walk_forward(df.iloc[:58], **ARGS)
    files = sorted((tmp_path / "wf").glob("*.pkl"))
    computed.clear()
    # Două extrageri noi: pașii refolosibili nu se arată (le lipsesc tocmai cele
    # mai noi extrageri, iar validarea parțială s-ar citi „cele mai recente”).
    flat, meta = wf.run_honest_walk_forward(df, cache_only=True, **ARGS)
    assert computed == [] and sorted((tmp_path / "wf").glob("*.pkl")) == files
    assert flat == [] and meta["cache_miss"] and not meta["from_cache"]

    # Cache exact: întors ca atare.
    exact, exact_meta = wf.run_honest_walk_forward(df.iloc[:58], cache_only=True, **ARGS)
    assert computed == [] and exact_meta["from_cache"] and not exact_meta["partial"]
    assert _rows(exact) == _rows(full)


def test_recovered_result_shows_the_cached_validation_without_running(
    monkeypatch, tmp_path
):
    """Pornirea reafișează „Istoric hits” din cache, fără calcul, mail sau oprire."""
    import app_nicegui as app

    calls = []

    def fake_wf(**kwargs):
        calls.append(kwargs)
        return ["pas"], {"partial": False, "n_test_draws": 1, "n_expected": 1,
                         "from_cache": True, "pool_size": kwargs["pool_size"]}

    import loto_enterprise.core.walk_forward_adapter as wfa

    monkeypatch.setattr(wfa, "run_honest_walk_forward", fake_wf)
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    finalized = []
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: finalized.append(1))
    data = {"pool_size": 11, "guarantee": 3, "audit": {}}
    for key, value in {
        "results": ([("loto_6_49.csv", {"6/49": data})], 1),
        "result_sources": {"loto_6_49.csv": history(20)},
        "retro": {},
        "retro_meta": {},
    }.items():
        monkeypatch.setitem(app.STATE, key, value)

    assert app._load_cached_walk_forward() == 1
    assert calls and calls[0]["cache_only"] is True and calls[0]["pool_size"] == 11
    assert app.STATE["retro"]["loto_6_49.csv_6/49"] == ["pas"]
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"]["from_cache"] is True
    assert finalized == []

    # Decizia de acum alege altă metodă: validarea din cache nu se arată.
    calls.clear()
    monkeypatch.setitem(app.STATE, "retro", {})
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: False)
    assert app._load_cached_walk_forward() == 0
    assert calls == [] and app.STATE["retro"] == {}


@pytest.fixture
def decision_now(monkeypatch):
    """Metoda pe care decizia de acum o dă fiecărei chei; lipsă = fără decizie."""
    import loto_enterprise.core.method_selector as ms

    chosen: dict[str, str] = {}
    monkeypatch.setattr(ms, "has_decision", lambda key, cfg=None: key in chosen)
    monkeypatch.setattr(
        ms,
        "get_ensemble_for_game",
        lambda key, pool_size=None, config_path=None, max_methods=None: [
            (chosen[key], None, 1.0)
        ],
    )
    return chosen


def _used(**methods):
    return {"pool_size": 11, "audit": {"bench_winner": {
        k: {"method": m} for k, m in methods.items()}}}


def test_the_result_scorer_is_compared_with_the_current_decision(decision_now):
    import app_nicegui as app

    decision_now["loto_6_49"] = "ewma_hl30"
    assert app._result_scorers_match_decision("6/49", _used(loto_6_49="ewma_hl30"))
    # Generat cu ținta 4+ (altă metodă), ținta comutată apoi înapoi pe 3+.
    assert not app._result_scorers_match_decision("6/49", _used(loto_6_49="markov_pairs"))
    assert not app._result_scorers_match_decision("6/49", {"pool_size": 11, "audit": {}})

    # Fără decizie: frecvența, ca în motor.
    assert app._result_scorers_match_decision("5/40", _used(loto_5_40="frequency"))

    # Joker: contează și Urna 2, fiindcă bila ei e pe fiecare variantă.
    decision_now.update(joker_urna1="ewma_hl30", joker_urna2="markov_pairs")
    same = _used(joker_urna1="ewma_hl30", joker_urna2="markov_pairs")
    assert app._result_scorers_match_decision("joker", same)
    other_u2 = _used(joker_urna1="ewma_hl30", joker_urna2="frequency")
    assert not app._result_scorers_match_decision("joker", other_u2)


def test_display_only_recovery_loads_the_cached_validation(monkeypatch):
    import app_nicegui as app

    loads = []
    monkeypatch.setattr(app, "_load_cached_walk_forward", lambda: loads.append(1) or 0)
    monkeypatch.setattr(
        app,
        "get_latest_completed_job",
        lambda: {"id": 5, "result_json": "x", "completed_at": "2026-01-01 10:00:00"},
    )
    monkeypatch.setattr(app, "decode_queue_result", lambda _raw: ([], 0))
    monkeypatch.setattr(app, "_save_report_file", lambda: None)
    for key in ("active_job_id", "results", "result_sources", "results_recovered"):
        monkeypatch.setitem(app.STATE, key, None)
    app._recover_completed_job(allow_finalize=False)
    assert loads == [1]
    assert app.STATE["results"] == ([], 0)


def test_a_job_taken_in_an_earlier_session_comes_back_without_finalizing(
    monkeypatch, tmp_path
):
    """Cazul obișnuit: UI-ul a preluat jobul (WF, mail) și apoi s-a oprit. La
    repornire rezultatul și validarea lui reapar; mail-ul și oprirea, nu."""
    import functools

    import app_nicegui as app
    import job_queue as queue
    from ui_shared import pack_queue_result

    database = str(tmp_path / "station.db")
    jid = queue.submit_job("pipeline", json.dumps({"datasets": []}), db_path=database)
    queue.fetch_pending_job(db_path=database)
    queue.complete_job(jid, pack_queue_result(([], 0)), db_path=database)
    queue.mark_job_finalized(jid, db_path=database)
    stamp = queue.get_job_status(jid, db_path=database)["ui_finalized_at"]
    for name in ("get_latest_completed_job", "get_job_status"):
        monkeypatch.setattr(
            app, name, functools.partial(getattr(queue, name), db_path=database)
        )
    marks = []
    monkeypatch.setattr(app, "mark_job_finalized", marks.append)
    loads = []
    monkeypatch.setattr(app, "_load_cached_walk_forward", lambda: loads.append(1) or 0)
    monkeypatch.setattr(app, "_save_report_file", lambda: None)
    for key in ("active_job_id", "results", "result_sources", "results_recovered",
                "legacy_finalized_job_id"):
        monkeypatch.setitem(app.STATE, key, None)

    for allow_finalize in (True, False, True):
        app._recover_completed_job(allow_finalize=allow_finalize)
        assert app.STATE["active_job_id"] is None
        assert app.STATE["results"] == ([], 0)
        assert f"job #{jid}" in app.STATE["results_recovered"]
    assert loads == [1, 1, 1] and marks == []
    assert queue.get_job_status(jid, db_path=database)["ui_finalized_at"] == stamp
