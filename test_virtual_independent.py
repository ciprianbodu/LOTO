"""Independent parity checks for virtual full-system walk-forward."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.core import walk_forward_adapter as wf
from loto_enterprise.core.backtesting import LotoBacktester, _retroactive_step_stateless
from loto_enterprise.core.prize_categories import resolve_variant_category


def history(game):
    draw_n = 5 if game == "joker" else 6
    maximum = {"6/49": 49, "5/40": 40, "joker": 45}[game]
    rng = np.random.default_rng(725)
    draws = [rng.choice(np.arange(1, maximum + 1), draw_n, replace=False) for _ in range(50)]
    df = pd.DataFrame(draws, columns=[f"n{i}" for i in range(1, draw_n + 1)])
    df["date"] = [(pd.Timestamp("2020-01-01") + pd.Timedelta(days=int(i // 2))).strftime("%d-%m-%Y") for i in range(len(df))]
    if game == "joker":
        df["joker"] = [1 + i % 20 for i in range(len(df))]
    return df


OPTIONS = [
    {},
    {"lookback_percent": 60.0},
    {"recent_penalty_draws": 5, "recent_penalty_factor": 0},
    {"max_consecutive_run": 2},
    {"restrict_base_min": 10, "restrict_base_max": 19},
    {"restrict_base_min": 15, "restrict_base_max": 22, "max_consecutive_run": 2,
     "recent_penalty_draws": 4, "recent_penalty_factor": 0.35},
]


@pytest.mark.parametrize("game", ["6/49", "5/40", "joker"])
@pytest.mark.parametrize("options", OPTIONS)
def test_virtual_matches_real_wheel_with_selection_options(game, options):
    bt = LotoBacktester(history(game), game_type=game)
    pick = 6 if game == "6/49" else 5
    options = dict(options)
    lookback = options.pop("lookback_percent", 100.0)
    args = (bt.df, bt.draws, bt.dates, game, len(bt.draws) - 1, 10, pick, 0, lookback)
    real = _retroactive_step_stateless(*args, **options)
    virtual = _retroactive_step_stateless(*args, virtual_full_system=True, **options)
    assert virtual.hard_core == real.hard_core
    assert (virtual.hits, virtual.hits_union, virtual.joker_hit) == (real.hits, real.hits_union, real.joker_hit)
    assert virtual.variant_count == len(real.variants)
    assert len(virtual.variants) == 1 and virtual.variants[0] in real.variants
    assert virtual.ticket_context == real.ticket_context


def test_virtual_and_real_cache_are_distinct_and_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    kwargs = dict(df_source=history("6/49"), game_type="6/49", pool_size=10,
                  backtest_depth_percent=2, guarantee=6, max_variants=0, use_cache=True)
    real, rm = wf.run_honest_walk_forward(**kwargs)
    virtual, vm = wf.run_honest_walk_forward(**kwargs, virtual_full_system=True)
    assert rm["cache_file"] != vm["cache_file"]
    assert len(real) == 210 and len(virtual) == 1
    assert virtual[0].virtual_full_system and virtual[0].variant_count == 210
    real2, rm2 = wf.run_honest_walk_forward(**kwargs)
    virtual2, vm2 = wf.run_honest_walk_forward(**kwargs, virtual_full_system=True)
    assert rm2["from_cache"] and vm2["from_cache"]
    assert len(real2) == 210 and len(virtual2) == 1
    assert virtual2[0].virtual_full_system and virtual2[0].variant_count == 210
    assert len(list(tmp_path.glob("*.pkl"))) == 2


def test_virtual_witness_540_does_not_claim_highest_prize_category():
    official = [30, 1, 2, 3, 4, 5]
    witness = resolve_variant_category("5/40", [1, 2, 3, 4, 5], official)
    another = resolve_variant_category("5/40", [1, 2, 3, 4, 30], official)
    assert witness.category == "II" and another.category == "I"
