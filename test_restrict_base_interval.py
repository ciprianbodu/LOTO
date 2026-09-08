"""Intervalul de bază [min, max]: producție, worker și walk-forward, identic.

Restrângerea e o preferință de compoziție a pool-ului, fără avantaj statistic
(CLAUDE.md §6). Testele de aici nu apără vreun avantaj, ci apără CONSECVENȚA:
ce alege utilizatorul se aplică la fel în producție și în validare, un interval
imposibil nu produce tăcut un pool care nu respectă nimic, iar cache-ul WF nu
servește un rezultat calculat pe alt interval.
"""

import pandas as pd
import pytest

import worker
from loto_engine import LotoEngine
from loto_enterprise.core import walk_forward_adapter as wf


def _engine():
    engine = LotoEngine("6/49")
    engine.load_data("_ISTORIC/loto_6_49.csv")
    engine._build_draw_matrix()
    return engine


@pytest.mark.parametrize(
    "lo,hi,expected_lo,expected_hi",
    [(10, 40, 10, 40), (0, 36, 1, 36), (12, 0, 12, 49), (0, 0, 1, 49)],
)
def test_pool_stays_inside_the_requested_interval(lo, hi, expected_lo, expected_hi):
    """Capătul lăsat pe 0 rămâne liber; celălalt se aplică."""
    engine = _engine()
    engine.run_institutional_pipeline(
        pool_size=10,
        guarantee=3,
        max_variants=5,
        track_pool_variation=False,
        restrict_base_min=lo,
        restrict_base_max=hi,
    )
    assert engine.hard_core, "pool gol"
    assert min(engine.hard_core) >= expected_lo
    assert max(engine.hard_core) <= expected_hi
    audit = engine.audit.get("restrict_base") or {}
    if (expected_lo, expected_hi) == (1, 49):
        assert not audit, "fără restricție nu se scrie audit"
    else:
        assert (audit["min"], audit["max"]) == (expected_lo, expected_hi)


def test_inverted_interval_is_ignored_and_recorded_not_applied_silently():
    """min > max ar goli baza de candidați; motorul refuză și consemnează."""
    engine = _engine()
    engine.run_institutional_pipeline(
        pool_size=10,
        guarantee=3,
        max_variants=5,
        track_pool_variation=False,
        restrict_base_min=40,
        restrict_base_max=10,
    )
    audit = engine.audit["restrict_base"]
    assert audit["ignored"] is True and "inversat" in audit["reason"]
    assert "excluded" not in audit
    # Pool-ul rămâne cel nerestrâns, nu unul tăiat pe jumătate.
    reference = _engine()
    reference.run_institutional_pipeline(
        pool_size=10, guarantee=3, max_variants=5, track_pool_variation=False
    )
    assert sorted(engine.hard_core) == sorted(reference.hard_core)


def test_interval_narrower_than_the_pool_still_produces_a_pool():
    """8 numere disponibile pentru un pool de 10: pool trunchiat, nu excepție."""
    engine = _engine()
    engine.run_institutional_pipeline(
        pool_size=10,
        guarantee=3,
        max_variants=5,
        track_pool_variation=False,
        restrict_base_min=20,
        restrict_base_max=27,
    )
    assert engine.hard_core
    assert set(engine.hard_core) <= set(range(20, 28))


def test_worker_normalises_both_ends_and_forwards_them():
    norm = worker._normalize_task(
        {"restrict_base_min": "10", "restrict_base_max": 99.7}, draw_n=6
    )
    assert norm["restrict_base_min"] == 10
    assert norm["restrict_base_max"] == 49  # plafonat la cel mai mare max_num
    assert worker._normalize_task({}, draw_n=6)["restrict_base_min"] == 0
    assert (
        worker._normalize_task({"restrict_base_min": "x"}, draw_n=6)[
            "restrict_base_min"
        ]
        == 0
    )
    assert (
        worker._normalize_task({"restrict_base_min": -5}, draw_n=6)[
            "restrict_base_min"
        ]
        == 0
    )


def test_wf_cache_key_separates_intervals_and_keeps_old_keys_valid():
    """1..40 și 10..40 nu au voie să împartă același cache."""
    assert wf._restrict_base_sig(0, 0) == ""  # cheile de dinaintea setării rămân valide
    assert wf._restrict_base_sig(40, 0) != wf._restrict_base_sig(40, 10)
    assert wf._restrict_base_sig(40, 10) != wf._restrict_base_sig(40, 12)
    assert wf._restrict_base_sig(0, 10) != ""
    # Capetele pe care motorul le tratează ca inexistente dau ACEEAȘI cheie:
    # min=1 e „fără capăt de jos", max=max_num e „fără capăt de sus". Altfel
    # aceeași rulare primea două chei și se recalcula degeaba.
    assert wf._restrict_base_sig(40, 1) == wf._restrict_base_sig(40, 0)
    assert wf._restrict_base_sig(49, 0, max_num=49) == ""
    assert wf._restrict_base_sig(49, 1, max_num=49) == ""
    assert wf._restrict_base_sig(40, 0, max_num=49) == wf._restrict_base_sig(40, 0)
    assert (
        wf._decision_sig("6/49", 10, 100.0, 0, 0.5, 3, 4, 0, 49, 1)
        == wf._decision_sig("6/49", 10, 100.0, 0, 0.5, 3, 4, 0, 0, 0)
    )
    variants = [(0, 0), (40, 0), (40, 10), (36, 10)]
    assert (
        len({wf._decision_sig("6/49", 10, 100.0, 0, 0.5, 3, 4, 0, hi, lo) for hi, lo in variants})
        == 4
    )


def test_wf_applies_the_same_interval_as_production():
    """Validarea măsoară pool-ul jucat, nu unul nerestrâns."""
    from loto_enterprise.core import backtesting as bt

    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(12).reset_index(drop=True)
    step = bt._retroactive_step_stateless(
        df,
        df[[f"n{i}" for i in range(1, 7)]].values.tolist(),
        df.date.tolist(),
        "6/49",
        sim_idx=10,
        pool_size=10,
        guarantee=3,
        max_variants=2,
        lookback_percent=100.0,
        filter_consecutives=False,
        smart_reduction=False,
        restrict_base_min=10,
        restrict_base_max=40,
    )
    assert step is not None
    for variant in step.variants:
        assert all(10 <= n <= 40 for n in variant)


def test_wf_worker_step_takes_named_settings_so_a_new_one_cannot_shift_a_slot():
    """Calea paralelă primește setările pe nume, nu pe poziție."""
    from loto_enterprise.core import backtesting as bt

    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(12).reset_index(drop=True)
    bt._WF_SHARED = {
        "df": df,
        "draws": df[[f"n{i}" for i in range(1, 7)]].values.tolist(),
        "dates": df.date.tolist(),
        "game_type": "6/49",
    }
    step = bt._wf_worker_step(
        {
            "sim_idx": 10,
            "pool_size": 10,
            "guarantee": 3,
            "max_variants": 2,
            "lookback_percent": 100.0,
            "filter_consecutives": False,
            "smart_reduction": False,
            "recent_penalty_draws": 0,
            "recent_penalty_factor": 0.0,
            "restrict_base_max": 40,
            "restrict_base_min": 10,
            "wheel_condition": 4,
        }
    )
    assert step is not None
    for variant in step.variants:
        assert all(10 <= n <= 40 for n in variant)


def test_interval_narrower_than_a_ticket_is_ignored_not_turned_into_a_short_ticket():
    """47–49 la 6/49 nu poate produce un bilet de 6 numere.

    Fără gardă, blacklist-ul lăsa trei candidați, iar wheeling-ul tratează
    `len(pool) < pick` drept sistem complet cu un singur bilet: pipeline-ul
    raporta `[47, 48, 49]` ca bilet 6/49 cu acoperire 100%, adică un bilet
    nejucabil prezentat drept acoperit integral.
    """
    engine = _engine()
    lines, *_ = engine.run_institutional_pipeline(
        pool_size=10,
        guarantee=3,
        max_variants=0,
        track_pool_variation=False,
        restrict_base_min=47,
        restrict_base_max=49,
    )
    audit = engine.audit["restrict_base"]
    assert audit["ignored"] is True and "prea îngust" in audit["reason"]
    assert all(len(line) == 6 for line in lines)
    assert len(engine.hard_core) == 10

    # Exact cât un bilet: rămâne valid, un singur bilet complet.
    exact = _engine()
    lines_exact, *_ = exact.run_institutional_pipeline(
        pool_size=10,
        guarantee=3,
        max_variants=0,
        track_pool_variation=False,
        restrict_base_min=44,
        restrict_base_max=49,
    )
    assert not (exact.audit.get("restrict_base") or {}).get("ignored")
    assert sorted(exact.hard_core) == [44, 45, 46, 47, 48, 49]
    assert all(len(line) == 6 for line in lines_exact)


def test_parallel_walk_forward_dispatch_reads_the_named_index(monkeypatch, caplog):
    """Ramura PARALELĂ, pe care restul suitei nu o atinge (workers forțat la 1).

    Acolo se construiește harta de future-uri din `task_args`. Cât timp erau
    tupluri, indexul se lua pozițional; după trecerea la dicționar, aceeași
    indexare arunca `KeyError` chiar la construirea hărții, iar handler-ul
    exterior cădea înapoi pe execuția secvențială a tuturor pașilor scumpi.
    """
    import logging

    from loto_enterprise.core import backtesting as bt

    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(14).reset_index(drop=True)
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 2)
    monkeypatch.setattr(bt, "_WF_SERIAL_MAX_MS", 0.0)  # forțează ramura paralelă
    monkeypatch.setattr(bt, "_WF_PROBE_STEPS", 1)

    backtester = bt.LotoBacktester(df, "6/49")
    backtester._load_data()
    with caplog.at_level(logging.WARNING, logger=bt.logger.name):
        predictions = backtester.run_retroactive_backtest(
            backtest_depth_percent=30.0,
            pool_size=10,
            guarantee=3,
            max_variants=2,
            restrict_base_min=10,
            restrict_base_max=40,
            # Calea stateless (cea paralelizabilă) e activă doar fără stare
            # între pași — exact cum o apelează walk-forward-ul din UI.
            use_feedback=False,
            enable_hard_inversion=False,
        )
    assert predictions, "ramura paralelă nu a produs niciun pas"
    for prediction in predictions:
        for variant in prediction.variants:
            assert all(10 <= n <= 40 for n in variant)
    # Fără asta testul ar fi trecut și cu bug-ul: handler-ul exterior prinde
    # excepția din construirea hărții de future-uri și reia TOT secvențial, cu
    # rezultate corecte — paralelizarea dispare în tăcere, nu rezultatul.
    assert not [
        r for r in caplog.records if "WF rapid indisponibil" in r.getMessage()
    ], "dispatch-ul paralel a căzut pe fallback secvențial"


def test_semantics_change_invalidates_only_the_restricted_cache_keys():
    """Regula nouă nu are voie să citească rezultate scrise sub cea veche.

    Sub regula veche, un interval mai îngust decât un bilet PRODUCEA un pool
    trunchiat cu variante mai scurte decât biletul și acoperire 100%. Cheia de
    cache nu se schimbase, deci un pickle scris atunci ar fi fost servit acum.
    Marcajul de semantică intră în cheie DOAR când restricția e activă, ca
    bump-ul să nu arunce cache-urile WF nerestrânse (90 de minute fiecare).
    """
    # Fără restricție: cheia rămâne exact cea dinainte de marcaj.
    assert wf._restrict_base_sig(0, 0) == ""
    assert wf._restrict_base_sig(49, 1, max_num=49) == ""
    # Cu restricție: formatul vechi („|rb40:10") nu mai poate fi produs.
    restricted = wf._restrict_base_sig(40, 10)
    assert restricted != "|rb40:10"
    assert wf._RESTRICT_SEMANTICS in restricted
    assert wf._restrict_base_sig(40, 0) != "|rb40"
    # Intervalele rămân separate între ele.
    assert wf._restrict_base_sig(40, 10) != wf._restrict_base_sig(40, 12)


def test_config_hash_carries_the_semantics_only_when_a_restriction_is_active():
    """Același contract pentru cache-ul de pipeline al worker-ului."""
    import json

    import app_nicegui as app

    app.STATE["datasets"] = [
        ("loto_6_49.csv", pd.read_csv("_ISTORIC/loto_6_49.csv").tail(30))
    ]
    app.SETTINGS["restrict_base_min_val"] = 0
    app.SETTINGS["restrict_base_max_val"] = 0
    free = json.loads(app._build_config_json())["input_hash"]

    app.SETTINGS["restrict_base_max_val"] = 40
    restricted = json.loads(app._build_config_json())["input_hash"]
    assert restricted != free

    # Marcajul trebuie să conteze: aceleași setări, altă semantică → alt hash.
    original = app._RESTRICT_SEMANTICS
    try:
        app._RESTRICT_SEMANTICS = "999"
        assert json.loads(app._build_config_json())["input_hash"] != restricted
        # ...dar fără restricție activă, semantica nu atinge hash-ul.
        app.SETTINGS["restrict_base_max_val"] = 0
        assert json.loads(app._build_config_json())["input_hash"] == free
    finally:
        app._RESTRICT_SEMANTICS = original
        app.SETTINGS["restrict_base_max_val"] = 0
        app.STATE["datasets"] = []


def test_the_two_semantics_markers_stay_in_sync():
    """Aceeași schimbare de regulă invalidează ambele cache-uri sau niciunul."""
    import app_nicegui as app

    assert app._RESTRICT_SEMANTICS == wf._RESTRICT_SEMANTICS
