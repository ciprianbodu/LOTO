"""Limita de consecutive (`max_consecutive_run`): fără 4-5-6 în pool, tot după scor.

Opțiune de compoziție a utilizatorului, fără avantaj statistic (AGENTS.md §6).
Testele apără CONSECVENȚA: pool-ul rămâne cel mai bun după clasamentul metodei
care respectă limita, iar producția, worker-ul, walk-forward-ul, cache-ul și
biletul complet aplică aceeași regulă, citită din rezultatul afișat.
"""

import functools
import json
import logging
import random
from itertools import combinations

import pandas as pd
import pytest

import job_queue as jq
import worker
from loto_engine import LotoEngine
from loto_enterprise.core import backtesting as bt
from loto_enterprise.core import walk_forward_adapter as wf
from loto_enterprise.core.full_ticket import build_full_ticket
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import limit_consecutive_run, longest_consecutive_run
from ui_shared import decode_queue_result

LINEAR = {n: float(n) for n in range(1, 50)}  # clasament 49, 48, 47, ...


# --- regula: cel mai bun set după rang care respectă limita -------------------
def test_walk_matches_brute_force_on_small_universes():
    """Lexicografic cel mai bun după rang, la cea mai mică limită posibilă."""
    rng = random.Random(3)
    for _ in range(300):
        universe = sorted(rng.sample(range(1, 20), rng.randint(3, 10)))
        ranked = universe[:]
        rng.shuffle(ranked)
        k = rng.randint(1, len(universe))
        limit = rng.choice([1, 2, 3])
        pool, applied, skipped = limit_consecutive_run(ranked, k, limit)
        best = limit
        while not any(
            longest_consecutive_run(c) <= best for c in combinations(universe, k)
        ):
            best += 1
        pos = {n: i for i, n in enumerate(ranked)}
        expected = min(
            (
                sorted(c, key=pos.get)
                for c in combinations(universe, k)
                if longest_consecutive_run(c) <= best
            ),
            key=lambda c: [pos[n] for n in c],
        )
        assert (pool, applied) == (expected, best)
        assert set(skipped).isdisjoint(pool)


def test_top_n_that_already_respects_the_limit_is_kept():
    ranked = [40, 10, 20, 30, 41, 5]
    assert limit_consecutive_run(ranked, 5, 2) == ([40, 10, 20, 30, 41], 2, [])


def test_the_weakest_number_of_the_triple_is_replaced_by_the_next_that_fits():
    """Exemplul utilizatorului: 5, 6, 36, 25, 4, 23, 12 → 4 iese, intră 9."""
    ranked = [5, 6, 36, 25, 4, 23, 12, 9, 2, 13]
    pool, applied, skipped = limit_consecutive_run(ranked, 7, 2)
    assert sorted(pool) == [5, 6, 9, 12, 23, 25, 36]
    assert (applied, skipped) == (2, [4])


def test_limit_is_relaxed_only_when_no_pool_can_respect_it():
    """44..49 cu pool 6: singurul pool posibil are 6 consecutive."""
    assert limit_consecutive_run([49, 48, 47, 46, 45, 44], 6, 2) == (
        [49, 48, 47, 46, 45, 44],
        6,
        [],
    )


def test_completion_check_fills_a_narrow_base_where_a_plain_walk_falls_short():
    """Bază 10..33, pool 16: parcurgerea simplă rămâne des fără numere."""
    rng = random.Random(5)
    for _ in range(40):
        ranked = list(range(10, 34))
        rng.shuffle(ranked)
        pool, applied, _ = limit_consecutive_run(ranked, 16, 2)
        assert len(pool) == 16 and applied == 2
        assert longest_consecutive_run(pool) <= 2


# --- selectorul de pool și auditul ------------------------------------------
def test_selector_off_is_bit_identical_and_writes_no_audit_key():
    audit: dict = {}
    pool = select_pool_from_scores(LINEAR, 12, set(), audit)
    assert pool == list(range(38, 50))
    assert "consecutive_limit" not in audit
    assert len(audit["timesfm_predictions"]) == 25
    assert audit["pool_selection"] == "top_score_pure"


def test_selector_on_replaces_and_records_ranks():
    audit: dict = {}
    pool = select_pool_from_scores(LINEAR, 12, set(), audit, max_consecutive_run=2)
    assert pool == [33, 34, 36, 37, 39, 40, 42, 43, 45, 46, 48, 49]
    cl = audit["consecutive_limit"]
    assert (cl["requested"], cl["applied"], cl["relaxed"]) == (2, 2, False)
    assert cl["removed"] == [[38, 12], [41, 9], [44, 6], [47, 3]]
    assert cl["added"] == [[33, 17], [34, 16], [36, 14], [37, 13]]
    assert audit["pool_selection"] == "top_score_max_run"
    json.dumps(audit["consecutive_limit"])  # raportul și emailul trec prin JSON


def test_every_pool_member_stays_in_the_audited_ranking():
    """`full_ticket` citește clasamentul din audit; un membru sub rangul 25
    trebuie să apară și el, altfel tăierea Joker cade pe „clasament lipsă"."""
    rng = random.Random(9)
    for _ in range(200):
        scores = {n: rng.random() for n in range(1, 50)}
        size = rng.randint(6, 16)
        banned = set(rng.sample(range(1, 50), rng.randint(0, 20)))
        audit: dict = {}
        pool = select_pool_from_scores(
            scores, size, banned, audit, max_consecutive_run=2
        )
        assert set(pool) <= set(audit["timesfm_predictions"])
        assert longest_consecutive_run(pool) <= audit["consecutive_limit"]["applied"]


def test_pool_member_ranked_past_25_is_still_audited():
    """Fiecare pereche luată (2-3, 6-7, ...) blochează vecinii clasați imediat
    după ea (1, 4, ...): al 16-lea număr al pool-ului ajunge pe locul 30."""
    ranked = []
    for a in range(2, 32, 4):
        ranked += [a, a + 1, a - 1, a + 2]
    ranked += [n for n in range(1, 50) if n not in ranked]
    scores = {n: float(100 - i) for i, n in enumerate(ranked)}
    audit: dict = {}
    pool = select_pool_from_scores(scores, 16, set(), audit, max_consecutive_run=2)
    assert max(ranked.index(n) + 1 for n in pool) == 30
    assert set(pool) <= set(audit["timesfm_predictions"])
    assert len(audit["timesfm_predictions"]) == 30


# --- motorul: producție, bază restrânsă, fallback, Urna 2 ---------------------
def _engine(game="6/49", csv="_ISTORIC/loto_6_49.csv"):
    engine = LotoEngine(game)
    engine.load_data(csv)
    engine._build_draw_matrix()
    return engine


def _linear_scores(monkeypatch):
    monkeypatch.setattr(
        LotoEngine, "_get_timesfm_scores", lambda self, *a, **k: dict(LINEAR)
    )


def test_pipeline_default_is_unchanged_and_the_option_applies(monkeypatch):
    _linear_scores(monkeypatch)
    plain = _engine()
    plain.run_institutional_pipeline(
        pool_size=12, guarantee=3, max_variants=5, track_pool_variation=False
    )
    assert sorted(plain.hard_core) == list(range(38, 50))
    assert "consecutive_limit" not in plain.audit

    limited = _engine()
    _lines, _p10, _p90, _g, ctx, audit = limited.run_institutional_pipeline(
        pool_size=12,
        guarantee=3,
        max_variants=5,
        track_pool_variation=False,
        max_consecutive_run=2,
    )
    assert sorted(limited.hard_core) == [33, 34, 36, 37, 39, 40, 42, 43, 45, 46, 48, 49]
    assert audit["consecutive_limit"]["applied"] == 2
    assert ctx["max_consecutive_run"] == {"requested": 2, "applied": 2}


def test_limit_with_a_restricted_base_is_relaxed_not_the_pool(monkeypatch):
    """Intervalul exact cât un bilet: limita urcă, pool-ul și intervalul rămân."""
    _linear_scores(monkeypatch)
    engine = _engine()
    lines, *_ = engine.run_institutional_pipeline(
        pool_size=10,
        guarantee=3,
        max_variants=0,
        track_pool_variation=False,
        restrict_base_min=44,
        restrict_base_max=49,
        max_consecutive_run=2,
    )
    assert sorted(engine.hard_core) == [44, 45, 46, 47, 48, 49]
    cl = engine.audit["consecutive_limit"]
    assert cl["relaxed"] is True and cl["applied"] == 6
    assert all(len(line) == 6 for line in lines)


def test_frequency_fallback_respects_the_limit(monkeypatch):
    """Scorer fără scoruri → frecvență; opțiunea nu se pierde pe drum."""
    monkeypatch.setattr(LotoEngine, "_get_timesfm_scores", lambda self, *a, **k: {})
    engine = _engine()
    engine.run_institutional_pipeline(
        pool_size=16,
        guarantee=3,
        max_variants=5,
        track_pool_variation=False,
        max_consecutive_run=2,
    )
    assert len(engine.hard_core) == 16
    assert longest_consecutive_run(engine.hard_core) <= 2


def test_defensive_fill_respects_the_limit():
    """Scoruri pentru prea puține numere: completarea din frecvență aplică limita."""
    engine = _engine()
    engine.audit = {}
    pool = engine._get_timesfm_pool(
        {n: float(n) for n in (20, 21, 22, 23, 24, 25)},
        pool_size=12,
        blacklist=set(),
        max_consecutive_run=2,
    )
    assert len(pool) == 12
    assert longest_consecutive_run(pool) <= 2


def test_joker_urn_two_is_never_limited():
    base = _engine("joker", "_ISTORIC/joker.csv")
    base.run_institutional_pipeline(
        pool_size=16, guarantee=3, max_variants=5, track_pool_variation=False
    )
    limited = _engine("joker", "_ISTORIC/joker.csv")
    limited.run_institutional_pipeline(
        pool_size=16,
        guarantee=3,
        max_variants=5,
        track_pool_variation=False,
        max_consecutive_run=2,
    )
    assert limited.hard_core_joker == base.hard_core_joker
    assert longest_consecutive_run(limited.hard_core) <= 2


# --- worker: chei fixe, coadă veche fără limită --------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [(None, 0), (-1, 0), ("abc", 0), (2, 2), ("2", 2), (99, 16), (True, 1)],
)
def test_worker_normalises_the_limit(raw, expected):
    task = {} if raw is None else {"max_consecutive_run": raw}
    assert worker._normalize_task(task, draw_n=6)["max_consecutive_run"] == expected


def test_legacy_filter_key_never_turns_the_limit_on():
    norm = worker._normalize_task({"filter_consecutives": True}, draw_n=6)
    assert norm["max_consecutive_run"] == 0


# --- walk-forward: aceeași regulă, cheie separată numai cu limita activă -------
def test_wf_cache_key_changes_only_when_the_limit_is_active():
    assert wf._consecutive_sig(0) == ""
    assert wf._CONSECUTIVE_SEMANTICS in wf._consecutive_sig(2)
    base = wf._decision_sig("6/49", 10, 100.0, 0, 0.5, 4, 4, 0, 0, 0)
    assert wf._decision_sig("6/49", 10, 100.0, 0, 0.5, 4, 4, 0, 0, 0, 0) == base
    assert wf._decision_sig("6/49", 10, 100.0, 0, 0.5, 4, 4, 0, 0, 0, 2) != base


def test_wf_cache_fallback_key_also_separates_the_limit(monkeypatch):
    """Fără decizie bench, cheia „nd" trebuie să separe și ea limita."""
    import loto_enterprise.core.method_selector as ms

    def _broken(*_a, **_k):
        raise RuntimeError("fără decizie")

    monkeypatch.setattr(ms, "recommend_optimal_config", _broken)
    off = wf._decision_sig("6/49", 10, max_consecutive_run=0)
    on = wf._decision_sig("6/49", 10, max_consecutive_run=2)
    assert off.startswith("nd") and on.startswith("nd") and off != on


def test_semantics_markers_stay_in_sync():
    import ui_runtime

    assert ui_runtime._CONSECUTIVE_SEMANTICS == wf._CONSECUTIVE_SEMANTICS


def _tail(n=14):
    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(n).reset_index(drop=True)
    return df, df[[f"n{i}" for i in range(1, 7)]].values.tolist(), df.date.tolist()


def test_wf_step_builds_the_same_pool_as_production(monkeypatch):
    _linear_scores(monkeypatch)
    df, draws, dates = _tail(12)
    step = bt._retroactive_step_stateless(
        df, draws, dates, "6/49",
        sim_idx=10, pool_size=12, guarantee=3, max_variants=2,
        lookback_percent=100.0, max_consecutive_run=2,
    )
    assert step is not None
    assert sorted(step.hard_core) == [33, 34, 36, 37, 39, 40, 42, 43, 45, 46, 48, 49]


def test_wf_worker_step_takes_the_limit_by_name(monkeypatch):
    _linear_scores(monkeypatch)
    df, draws, dates = _tail(12)
    monkeypatch.setattr(
        bt, "_WF_SHARED", {"df": df, "draws": draws, "dates": dates, "game_type": "6/49"}
    )
    step = bt._wf_worker_step(
        {
            "sim_idx": 10,
            "pool_size": 12,
            "guarantee": 3,
            "max_variants": 2,
            "lookback_percent": 100.0,
            "recent_penalty_draws": 0,
            "recent_penalty_factor": 0.5,
            "restrict_base_max": 0,
            "restrict_base_min": 0,
            "wheel_condition": 3,
            "max_consecutive_run": 2,
        }
    )
    assert step is not None and longest_consecutive_run(step.hard_core) <= 2


def test_parallel_walk_forward_carries_the_limit(monkeypatch, caplog):
    """Ramura paralelă reală (procese): fiecare pas respectă limita, iar
    avertismentul de cădere pe calea secvențială lipsește (AGENTS.md §4.4)."""
    df, _draws, _dates = _tail(14)
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 2)
    monkeypatch.setattr(bt, "_WF_SERIAL_MAX_MS", 0.0)
    monkeypatch.setattr(bt, "_WF_PROBE_STEPS", 1)
    backtester = bt.LotoBacktester(df, "6/49")
    backtester._load_data()
    with caplog.at_level(logging.WARNING, logger=bt.logger.name):
        predictions = backtester.run_retroactive_backtest(
            backtest_depth_percent=30.0,
            pool_size=16,
            guarantee=3,
            max_variants=2,
            max_consecutive_run=2,
            use_feedback=False,
            enable_hard_inversion=False,
        )
    assert predictions, "ramura paralelă nu a produs niciun pas"
    for prediction in predictions:
        assert longest_consecutive_run(prediction.hard_core) <= 2
    assert not [
        r for r in caplog.records if "WF rapid indisponibil" in r.getMessage()
    ], "dispatch-ul paralel a căzut pe fallback secvențial"


# --- UI: bifa → task → rezultat → WF, citit din rezultat, nu din sidebar ------
def _app_with_dataset(monkeypatch, rows=40):
    import app_nicegui as app

    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(rows).reset_index(drop=True)
    monkeypatch.setattr(app, "STATE", {**app.STATE, "datasets": [("loto_6_49.csv", df)]})
    return app


def test_checkbox_sends_an_int_and_enters_the_hash_only_when_on(monkeypatch):
    app = _app_with_dataset(monkeypatch)
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", True)
    on = json.loads(app._build_config_json())
    value = on["datasets"][0]["tasks"][0]["max_consecutive_run"]
    assert value == 2 and type(value) is int

    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", False)
    off = json.loads(app._build_config_json())
    assert off["datasets"][0]["tasks"][0]["max_consecutive_run"] == 0
    assert on["input_hash"] != off["input_hash"]

    # Marcajul de semantică schimbă hash-ul numai cu limita activă.
    monkeypatch.setattr(app, "_CONSECUTIVE_SEMANTICS", "test")
    assert json.loads(app._build_config_json())["input_hash"] == off["input_hash"]
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", True)
    assert json.loads(app._build_config_json())["input_hash"] != on["input_hash"]


def test_checkbox_is_on_by_default_and_its_state_survives_a_restart(tmp_path, monkeypatch):
    """Implicit pornită; o bifă scoasă rămâne scoasă după repornire."""
    import ui_runtime

    assert ui_runtime.DEFAULTS["max_consecutive_run_enabled_val"] is True
    state = tmp_path / ".ui_state.json"
    monkeypatch.setattr(ui_runtime, "UI_STATE_FILE", state)
    monkeypatch.setitem(ui_runtime.SETTINGS, "max_consecutive_run_enabled_val", True)
    state.write_text(json.dumps({"max_consecutive_run_enabled_val": False}), "utf-8")
    ui_runtime._load_settings()
    assert ui_runtime.SETTINGS["max_consecutive_run_enabled_val"] is False
    # Stare salvată înainte de opțiune (fără cheie): rămâne implicitul.
    monkeypatch.setitem(ui_runtime.SETTINGS, "max_consecutive_run_enabled_val", True)
    state.write_text(json.dumps({"pool_size_val": 10}), "utf-8")
    ui_runtime._load_settings()
    assert ui_runtime.SETTINGS["max_consecutive_run_enabled_val"] is True


def test_wf_options_come_from_the_result_not_from_the_sidebar(monkeypatch):
    import app_nicegui as app

    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", True)
    # Rezultat vechi, generat fără limită: WF nu are voie să o aplice.
    assert app._wf_generation_options({})["max_consecutive_run"] == 0
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", False)
    assert app._wf_generation_options({"max_consecutive_run": 2})["max_consecutive_run"] == 2
    # Fără ecoul worker-ului: limita CERUTĂ din audit, nu cea relaxată.
    relaxed = {"audit": {"consecutive_limit": {"requested": 2, "applied": 6}}}
    assert app._wf_generation_options(relaxed)["max_consecutive_run"] == 2
    # 0 explicit în rezultat nu e înlocuit de audit.
    explicit = {"max_consecutive_run": 0, **relaxed}
    assert app._wf_generation_options(explicit)["max_consecutive_run"] == 0


def test_run_honest_walk_forward_accepts_every_generated_option():
    """Un nume nepotrivit ar arunca TypeError, prins de UI și doar logat:
    istoricul WF ar dispărea în tăcere pentru toate jocurile."""
    import inspect

    import app_nicegui as app

    params = inspect.signature(wf.run_honest_walk_forward).parameters
    assert set(app._wf_generation_options({})) <= set(params)


def test_text_describes_the_result_and_its_ranks():
    import ui_runtime

    audit = {
        "consecutive_limit": {
            "requested": 2,
            "applied": 2,
            "relaxed": False,
            "removed": [[4, 5]],
            "added": [[9, 8]],
        }
    }
    text = ui_runtime._consecutive_limit_text(audit)
    assert text.startswith("fără 3 numere consecutive în pool")
    assert "4 (locul 5)" in text and "9 (locul 8)" in text
    assert ui_runtime._consecutive_limit_text(audit, details=False) == (
        "fără 3 numere consecutive în pool"
    )
    assert ui_runtime._consecutive_limit_text({}) == ""


def test_wf_history_names_the_limit_it_used():
    src = open("ui_hits.py", encoding="utf-8").read()
    assert "consecutive_limit_text=_consecutive_limit_text(" in src


# --- biletul complet respectă limita rezultatului ------------------------------
_RANKING = {5: 9.0, 6: 8.0, 36: 7.0, 25: 6.0, 23: 5.0, 12: 4.0, 4: 3.5, 9: 3.0, 2: 2.0}


def _ticket_data(**audit_extra):
    audit = {"timesfm_predictions": dict(_RANKING), **audit_extra}
    return {"hard_core": [5, 6, 12, 23, 25, 36], "guarantee": 3, "audit": audit}


def test_ticket_extension_skips_the_number_that_would_form_a_triple():
    limit = {"requested": 2, "applied": 2, "relaxed": False, "removed": [], "added": []}
    t = build_full_ticket("6/49", _ticket_data(consecutive_limit=limit))
    assert t["pool"] == [5, 6, 9, 12, 23, 25, 36]
    assert "am adăugat 9" in t["note"] and "am sărit 4" in t["note"]


def test_ticket_from_a_result_without_the_limit_keeps_the_old_behaviour():
    t = build_full_ticket("6/49", _ticket_data())
    assert t["pool"] == [4, 5, 6, 12, 23, 25, 36]


# --- E2E: sidebar → coadă → worker → rezultat decodat → opțiuni WF -------------
def test_submit_worker_result_and_wf_options_carry_the_limit(tmp_path, monkeypatch):
    """AGENTS.md §4.4: o cheie nouă în config_json cere un test E2E."""
    app = _app_with_dataset(monkeypatch, rows=400)
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", True)
    monkeypatch.setitem(app.SETTINGS, "pool_size_val", 12)
    db = str(tmp_path / "jobs.db")
    jid = jq.submit_job("pipeline", app._build_config_json(), db_path=db)
    job = jq.fetch_pending_job(db_path=db, worker_token=worker.WORKER_TOKEN)
    assert job and job["id"] == jid
    monkeypatch.setattr(worker, "fail_job", functools.partial(jq.fail_job, db_path=db))
    monkeypatch.setattr(
        worker, "update_job_progress", functools.partial(jq.update_job_progress, db_path=db)
    )
    result_json = worker._run_pipeline_job(job)
    assert result_json
    assert jq.complete_job(jid, result_json, db_path=db, worker_token=worker.WORKER_TOKEN)
    rb, _ = decode_queue_result(jq.get_job_status(jid, db_path=db)["result_json"])
    (_fname, outs), = rb
    data = outs["6/49"]
    assert data["max_consecutive_run"] == 2
    assert data["audit"]["consecutive_limit"]["requested"] == 2
    assert longest_consecutive_run(data["hard_core"]) <= 2
    assert app._wf_generation_options(data)["max_consecutive_run"] == 2

    # Un job din coada veche, fără cheie, rulează fără limită.
    old_cfg = json.loads(app._build_config_json())
    for ds in old_cfg["datasets"]:
        for task in ds["tasks"]:
            task.pop("max_consecutive_run")
    jid_old = jq.submit_job("pipeline", json.dumps(old_cfg), db_path=db)
    job_old = jq.fetch_pending_job(db_path=db, worker_token=worker.WORKER_TOKEN)
    rb_old, _ = decode_queue_result(worker._run_pipeline_job(job_old))
    data_old = rb_old[0][1]["6/49"]
    assert job_old["id"] == jid_old
    assert data_old["max_consecutive_run"] == 0
    assert "consecutive_limit" not in data_old["audit"]
