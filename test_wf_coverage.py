"""Acoperirea wheel-ului dusă până în walk-forward.

`hits_union` numără hituri de POOL. Asta e egal cu „hit pe cel puțin un bilet"
DOAR cât timp wheel-ul acoperă 100% din țintele de garanție; sub 100% cifra de
pool e un PLAFON (oglinda bug-ului vechi, în care uniunea biletelor SUB-număra).
Testele de aici verifică lanțul care face diferența vizibilă: context → pas →
înregistrare flat → sumar → meta.
"""
from __future__ import annotations

from dataclasses import fields

import pandas as pd
import pytest

from loto_enterprise.core.backtesting import RetroactivePrediction, coverage_from_context
from loto_enterprise.core.walk_forward_adapter import (
    WalkForwardResult,
    _backfill_new_fields,
    build_retrospective_pool_hits_flat,
    expand_predictions_to_flat,
    per_draw_hit_summary,
    wheel_coverage_summary,
)


def _rec(draw_index: int, cov=None) -> WalkForwardResult:
    return WalkForwardResult(draw_index=draw_index, draw_date="01-01-2026",
                             variant=[1, 2, 3, 4, 5, 6], hits=2, hits_union=3,
                             wheel_coverage=cov)


# --------------------------------------------------------------------------- #
# coverage_from_context
# --------------------------------------------------------------------------- #
def test_coverage_from_context_reads_pipeline_context():
    assert coverage_from_context({"coverage_pct": 100.0}) == 100.0
    assert coverage_from_context({"coverage_pct": "62.5"}) == 62.5


def test_coverage_from_context_is_none_not_zero_when_missing():
    """None = NECUNOSCUT. Un 0.0 ar fi raportat drept „wheel complet stricat"."""
    for bad in ({}, None, {"coverage_pct": None}, {"coverage_pct": "n/a"}, 42):
        assert coverage_from_context(bad) is None


# --------------------------------------------------------------------------- #
# wheel_coverage_summary
# --------------------------------------------------------------------------- #
def test_summary_dedups_per_draw_not_per_ticket():
    """Acoperirea e proprietate a PASULUI; un pas cu 50 de bilete nu cântărește 50."""
    flat = [_rec(1, 50.0) for _ in range(50)] + [_rec(2, 100.0)]
    cov = wheel_coverage_summary(flat)
    assert cov["n_draws"] == 2 and cov["known"] == 2
    assert cov["below_100"] == 1 and cov["min"] == 50.0


def test_summary_reports_unknown_separately_from_100():
    flat = [_rec(1), _rec(2), _rec(3, 100.0)]
    cov = wheel_coverage_summary(flat)
    assert cov["unknown"] == 2 and cov["known"] == 1
    assert cov["below_100"] == 0, "necunoscut NU se numără ca incomplet"
    assert cov["min"] == 100.0, "min se calculează doar pe ce se știe"


def test_summary_on_empty_flat():
    assert wheel_coverage_summary([])["n_draws"] == 0
    assert wheel_coverage_summary(None)["min"] is None


def test_per_draw_hits_do_not_weight_larger_wheels_more_heavily():
    """O extragere cu 50 bilete contează o dată, nu de 50 de ori."""
    many_tickets = [_rec(1, 100.0) for _ in range(50)]
    many_tickets[0].hits = 4
    one_ticket = _rec(2, 100.0)
    one_ticket.hits = 1
    one_ticket.hits_union = 2

    per = per_draw_hit_summary(many_tickets + [one_ticket])

    assert len(per) == 2
    assert per[1] == {"pool": 3, "best_ticket": 4}
    assert per[2] == {"pool": 2, "best_ticket": 1}


# --------------------------------------------------------------------------- #
# Compatibilitate cu cache-ul WF scris ÎNAINTE de câmp (fără bump CACHE_VERSION)
# --------------------------------------------------------------------------- #
def _old_record() -> WalkForwardResult:
    """Instanță ca după un unpickle vechi: `__dict__` fără câmpul nou.

    Unpickle-ul restaurează `__dict__` fără să treacă prin `__init__`.
    """
    obj = object.__new__(WalkForwardResult)
    obj.__dict__.update(draw_index=7, draw_date="01-01-2026", variant=[1, 2],
                        hits=1, hits_union=2, target_draw_date="01-01-2026")
    return obj


def test_old_cached_record_reads_as_unknown_not_crash():
    old = _old_record()
    assert "wheel_coverage" not in vars(old)
    assert old.wheel_coverage is None  # cade pe atributul de CLASĂ
    cov = wheel_coverage_summary([old])
    assert cov["unknown"] == 1 and cov["known"] == 0


def test_backfill_leaves_simple_default_field_readable():
    """Pe `wheel_coverage` (default simplu) backfill-ul e no-op — și e în regulă.

    Citirea cade pe atributul de CLASĂ, deci `hasattr` e deja True. Ce nu trebuie
    să facă: să strice câmpurile vechi sau să inventeze un 100%.
    """
    old = _old_record()
    _backfill_new_fields([old])
    assert all(hasattr(old, f.name) for f in fields(WalkForwardResult))
    assert old.wheel_coverage is None and old.hits_union == 2
    _backfill_new_fields([old])  # idempotent
    assert old.draw_index == 7


def test_backfill_fills_fields_without_class_attribute():
    """Cazul pentru care există funcția: câmp cu `default_factory` (fără atribut
    de clasă) adăugat după scrierea cache-ului → altfel AttributeError."""
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _Rec:
        a: int
        tags: list = dc_field(default_factory=list)

    stale = object.__new__(_Rec)
    stale.__dict__.update(a=1)  # ca după un unpickle scris înainte de `tags`
    with pytest.raises(AttributeError):
        stale.tags
    _backfill_new_fields([stale])
    assert stale.tags == [] and "tags" in vars(stale)


def test_backfill_ignores_non_dataclasses():
    _backfill_new_fields([object(), None])  # nu trebuie să crape


# --------------------------------------------------------------------------- #
# Propagare pas → flat → retrospectiv
# --------------------------------------------------------------------------- #
def test_expand_predictions_propagates_coverage():
    pred = RetroactivePrediction(
        simulation_date="01-01-2026", target_draw_date="02-01-2026",
        variants=[[1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 7]],
        predicted_numbers={1, 2, 3, 4, 5, 6, 7},
        actual_numbers=[1, 2, 3, 8, 9, 10],
        hits=3, pool_size=7, guarantee=4, game_type="6/49",
        draw_index=5, hits_union=3, wheel_coverage=62.5,
    )
    flat = expand_predictions_to_flat([pred], "6/49")
    assert len(flat) == 2
    assert all(r.wheel_coverage == 62.5 for r in flat)
    assert wheel_coverage_summary(flat)["below_100"] == 1


def _df(n: int = 30) -> pd.DataFrame:
    cols = ["n1", "n2", "n3", "n4", "n5", "n6"]
    return pd.DataFrame([
        {"date": f"{(i % 28) + 1:02d}-01-2026",
         **{c: ((i * 7) % 40) + j + 1 for j, c in enumerate(cols)}}
        for i in range(n)
    ])


def test_retrospective_carries_production_coverage():
    """Pool 2 nu regenerează wheel-ul → acoperirea e cea a wheel-ului de producție."""
    df = _df()
    ref = [_rec(10), _rec(11)]
    flat, meta = build_retrospective_pool_hits_flat(
        ref, df, "6/49", [1, 2, 3, 4, 5, 6, 7], [[1, 2, 3, 4, 5, 6]],
        wheel_coverage=88.0,
    )
    assert flat and all(r.wheel_coverage == 88.0 for r in flat)
    assert meta["wheel_coverage"]["below_100"] == meta["wheel_coverage"]["known"] > 0


def test_retrospective_without_coverage_is_unknown():
    flat, meta = build_retrospective_pool_hits_flat(
        [_rec(10)], _df(), "6/49", [1, 2, 3, 4, 5, 6, 7], [[1, 2, 3, 4, 5, 6]],
    )
    assert all(r.wheel_coverage is None for r in flat)
    assert meta["wheel_coverage"]["unknown"] == meta["wheel_coverage"]["n_draws"]
