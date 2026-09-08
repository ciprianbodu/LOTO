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
