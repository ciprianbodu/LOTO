"""Loto 5/40: 6 numere extrase, bilet de 5.

Hiturile (bench, walk-forward, UI) se numara pe toate cele 6 numere extrase;
biletul, garantia si sistemul complet raman pe 5 numere. Tinta deciziei la
5/40 este 4+ (3 numere nu aduc premiu), indiferent de tinta globala. 6/49 si
Joker raman neschimbate.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.benchmark.decision import expected_random_rate
from loto_enterprise.benchmark.hit_target import (
    GAME_DRAW_PICK,
    game_hit_target,
)
from loto_enterprise.benchmark.runner import GameDef, _evaluate_fold, discover_games

ROOT = Path(__file__).resolve().parent


def _hyper_ge(N, n, K, t):
    return sum(
        math.comb(K, k) * math.comb(N - K, n - k) for k in range(t, min(K, n) + 1)
    ) / math.comb(N, n)


def test_540_random_baseline_pool8_uses_six_drawn_numbers():
    # N=40, n=6 extrase, K=8 in pool.
    p4 = expected_random_rate(40, 6, 8, 4)
    p3 = expected_random_rate(40, 6, 8, 3)
    assert p4 == pytest.approx(_hyper_ge(40, 6, 8, 4), abs=1e-15)
    assert p3 == pytest.approx(_hyper_ge(40, 6, 8, 3), abs=1e-15)
    # Valori numerice exacte (C(40,6) = 3838380).
    assert p4 == pytest.approx((70 * 496 + 56 * 32 + 28) / 3838380, abs=1e-15)
    assert p3 == pytest.approx((56 * 4960 + 70 * 496 + 56 * 32 + 28) / 3838380)
    assert p4 == pytest.approx(0.0095, abs=5e-5)
    assert p3 == pytest.approx(0.0819, abs=5e-5)
    # Cu 5 extrase (semantica veche) rata ar fi mai mica.
    assert p4 > expected_random_rate(40, 5, 8, 4)


def test_540_decision_target_is_four_regardless_of_global_target():
    assert game_hit_target("loto_5_40", 3) == 4
    assert game_hit_target("loto_5_40", 4) == 4
    assert game_hit_target("loto_6_49", 3) == 3
    assert game_hit_target("loto_6_49", 4) == 4
    assert game_hit_target("joker_urna1", 3) == 3
    assert GAME_DRAW_PICK["loto_5_40"] == (6, 5)
    assert GAME_DRAW_PICK["loto_6_49"] == (6, 6)
    assert GAME_DRAW_PICK["joker_urna1"] == (5, 5)


def _folds(game, rates):
    rows = []
    for method, rate in rates.items():
        for pct in (10, 30, 60, 100):
            rows.append(
                {
                    "game": game,
                    "method": method,
                    "percentile": pct,
                    "is_random": False,
                    "failed": False,
                    "n_test": 400,
                    "n_eval": 400,
                    "runtime_sec": 1.0,
                    "avg_hits_topk": 1.0,
                    "k5": 1.0,
                    "k8": 1.0,
                    "rate_3plus_k5": rate,
                    "rate_4plus_k5": rate / 5,
                    "rate_3plus_k8": rate,
                    "rate_4plus_k8": rate / 5,
                    "rate_3plus": rate,
                    "rate_4plus": rate / 5,
                }
            )
    return pd.DataFrame(rows)


def test_540_decision_judges_on_4plus_with_six_drawn_baseline(monkeypatch):
    import loto_enterprise.benchmark.decision as decision

    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    df = _folds("loto_5_40", {"frequency": 0.2, "random": 0.08})
    # Un apelant vechi care trimite draw_n=5 primeste tot geometria reala.
    cfg = decision.decide_optimal_config_for_pool(df, "loto_5_40", 8, 5, 40)
    assert cfg["hit_target"] == 4
    assert cfg["target_label"] == "4+"
    assert cfg.get("baseline_rate") == pytest.approx(
        expected_random_rate(40, 6, 8, 4)
    )

    df649 = _folds("loto_6_49", {"frequency": 0.2, "random": 0.08})
    cfg649 = decision.decide_optimal_config_for_pool(df649, "loto_6_49", 8, 6, 49)
    assert cfg649["hit_target"] == 3


def test_540_bench_game_reads_six_columns_with_five_number_base_pool():
    games = {g.key: g for g in discover_games(str(ROOT / "_ISTORIC"))}
    g = games["loto_5_40"]
    assert g.cols == ["n1", "n2", "n3", "n4", "n5", "n6"]
    assert g.draw_n == 6
    assert g.base_k == 5
    assert games["loto_6_49"].base_k == 6 and games["loto_6_49"].draw_n == 6
    assert games["joker_urna1"].base_k == 5 and games["joker_urna1"].draw_n == 5
    assert games["joker_urna2"].base_k == 1


def test_540_bench_counts_the_sixth_drawn_number():
    """Pool top-5 = {1..5}; extragerea are 3 hituri in n1..n5 si al 4-lea in n6."""
    train = np.array(
        [[1, 2, 3, 4, 5, 10 + (i % 30)] for i in range(60)], dtype=np.int64
    )
    test = np.array([[1, 2, 3, 30, 31, 4]], dtype=np.int64)
    game = GameDef(
        "loto_5_40", "5/40", "unused", [], 40, 6, pool_extra=0, pick_n=5
    )
    fold, _snap = _evaluate_fold("frequency", train, test, game, 1)
    assert fold.failed is False
    assert list(fold.hits_per_pool) == ["k5"]
    assert fold.hits_per_pool["k5"] == 4.0
    assert fold.rate_4plus == 1.0
    assert fold.rates_4plus_per_pool["k5"] == 1.0

    # Aceeasi extragere taiata la primele 5 (semantica veche) da doar 3 hituri.
    game5 = GameDef("loto_5_40", "5/40", "unused", [], 40, 5, pool_extra=0)
    fold5, _ = _evaluate_fold("frequency", train[:, :5], test[:, :5], game5, 1)
    assert fold5.hits_per_pool["k5"] == 3.0
    assert fold5.rate_4plus == 0.0


def test_540_engine_reads_six_numbers_but_tickets_stay_five(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTO_POOL_HISTORY_FILE", str(tmp_path / "ph.json"))
    from loto_engine import LotoEngine

    eng = LotoEngine("5/40")
    assert eng.params["draw_n"] == 6 and eng.params["play_n"] == 5
    assert eng.load_data(str(ROOT / "_ISTORIC" / "loto_5_40.csv"))
    assert eng._draw_matrix.shape[1] == 6
    tickets, *_rest = eng.run_institutional_pipeline(
        pool_size=8, guarantee=5, max_variants=0, track_pool_variation=False
    )
    # guarantee 5 = sistem complet pe bilet de 5 (nu clampat la 6, nici la 4).
    assert len(tickets) == math.comb(8, 5)
    assert {len(t) for t in tickets} == {5}
    assert eng.audit["wheel_guarantee_used"] == 5

    # Garantia peste biletul de 5 se clampeaza la 5, nu la 6 extrase.
    eng2 = LotoEngine("5/40")
    assert eng2.load_data(str(ROOT / "_ISTORIC" / "loto_5_40.csv"))
    tickets2, *_ = eng2.run_institutional_pipeline(
        pool_size=8, guarantee=6, max_variants=0, track_pool_variation=False
    )
    assert eng2.audit["wheel_guarantee_used"] == 5
    assert {len(t) for t in tickets2} == {5}


def test_649_and_joker_engine_geometry_unchanged():
    from loto_engine import LotoEngine

    assert LotoEngine("6/49").params["draw_n"] == 6
    assert LotoEngine("6/49").params["play_n"] == 6
    assert LotoEngine("joker").params["draw_n"] == 5
    assert LotoEngine("joker").params["play_n"] == 5


def test_540_walk_forward_history_has_six_numbers_per_draw():
    from loto_enterprise.core.backtesting import LotoBacktester, scored_variant_numbers

    df = pd.read_csv(ROOT / "_ISTORIC" / "loto_5_40.csv").tail(40)
    bt = LotoBacktester(df.reset_index(drop=True), "5/40")
    bt._load_data()
    assert {len(d) for d in bt.draws} == {6}
    # Un bilet de 5 numara hitul prins de al 6-lea numar extras.
    draw = set(bt.draws[-1])
    sixth = int(df.iloc[-1]["n6"])
    ticket = sorted(draw - {sixth})[:3] + [sixth]
    ticket += [n for n in range(1, 41) if n not in draw][:1]
    assert len(set(scored_variant_numbers(ticket, "5/40")) & draw) == 4


def test_ui_540_odds_use_six_drawn_numbers():
    try:
        import ui_results
    except Exception as exc:  # noqa: BLE001 - nicegui/pydantic pe 3.14rc
        pytest.skip(f"ui_results indisponibil: {exc!r}")
    assert ui_results._hypergeo_params("5/40") == (6, 40)
    assert ui_results._hypergeo_params("6/49") == (6, 49)
    assert ui_results._hypergeo_params("joker") == (5, 45)
    assert ui_results._ticket_pick("5/40") == 5
    assert ui_results._random_rate_hypergeo("5/40", 8, 4) == pytest.approx(
        expected_random_rate(40, 6, 8, 4)
    )
