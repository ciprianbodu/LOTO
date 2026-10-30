"""Integrarea pool-ului maxim: selecție, wheel, audit și Joker reale."""

import math
from itertools import combinations

import pandas as pd
import pytest

from loto_engine import LotoEngine


def _engine(game, monkeypatch):
    engine = LotoEngine(game)
    engine.use_bench_winner = False
    filename = {"6/49": "loto_6_49.csv", "5/40": "loto_5_40.csv", "joker": "joker.csv"}[game]
    engine.data = pd.read_csv(f"_ISTORIC/{filename}").tail(24).reset_index(drop=True)
    engine._build_draw_matrix()
    monkeypatch.setattr(
        engine, "_get_timesfm_scores",
        lambda **_kw: {n: float(n) for n in range(1, engine.params["max_n"] + 1)},
    )
    return engine


@pytest.mark.parametrize("game,v,pick", [("6/49", 49, 6), ("5/40", 40, 5), ("joker", 45, 5)])
def test_full_universe_is_retained_through_the_pipeline(game, v, pick, monkeypatch):
    engine = _engine(game, monkeypatch)
    lines, _, _, _, context, audit = engine.run_institutional_pipeline(
        pool_size=v, guarantee=4, max_variants=112,
        max_consecutive_run=2, track_pool_variation=False,
    )
    assert engine.hard_core == list(range(1, v + 1))
    assert len(lines) == 112
    assert {n for line in lines for n in line[:pick]} == set(engine.hard_core)
    assert all(len(set(line[:pick])) == pick for line in lines)
    covered = {s for line in lines for s in combinations(sorted(line[:pick]), 4)}
    assert context["coverage_pct"] == round(100 * len(covered) / math.comb(v, 4), 2)
    assert audit["consecutive_limit"]["applied"] == v
    assert context["max_consecutive_run"] == {"requested": 2, "applied": v}
    assert audit["large_pool"]["pool_size"] == v
    assert audit["pool_numbers_not_on_tickets"] == []
    assert audit["hit_forecast"]["random_baseline"]["P(4+)%"] == 100.0
    if game == "joker":
        assert all(len(line) == 6 and line[5] == engine.hard_core_joker[0] for line in lines)


def test_huge_full_system_fails_with_a_budget_instruction(monkeypatch):
    engine = _engine("6/49", monkeypatch)
    with pytest.raises(ValueError, match="plafon pozitiv"):
        engine.run_institutional_pipeline(
            pool_size=49, guarantee=6, max_variants=0, track_pool_variation=False,
        )
    assert len(engine.hard_core) == 49


def test_budget_one_reports_pool_positions_not_actually_played(monkeypatch):
    engine = _engine("6/49", monkeypatch)
    lines, _, _, _, _, audit = engine.run_institutional_pipeline(
        pool_size=49, guarantee=4, max_variants=1, track_pool_variation=False,
    )
    assert len(lines) == 1 and len(audit["pool_numbers_not_on_tickets"]) == 43
    assert set(audit["pool_numbers_not_on_tickets"]).isdisjoint(lines[0])


def test_reused_engine_clears_large_pool_audit_for_a_legacy_pool(monkeypatch):
    engine = _engine("6/49", monkeypatch)
    engine.run_institutional_pipeline(pool_size=49, guarantee=4, max_variants=1, track_pool_variation=False)
    engine.run_institutional_pipeline(pool_size=6, guarantee=4, max_variants=1, track_pool_variation=False)
    assert len(engine.hard_core) == 6 and "large_pool" not in engine.audit
