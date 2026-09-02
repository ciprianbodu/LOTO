"""Cache-ul WF parțial trebuie să CREASCĂ: pașii deja validați se sar."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _df(n: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = []
    for i in range(n):
        nums = sorted(rng.choice(np.arange(1, 50), size=6, replace=False).tolist())
        row = {"date": f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"}
        row.update({f"n{j + 1}": v for j, v in enumerate(nums)})
        rows.append(row)
    return pd.DataFrame(rows)


def test_skip_indices_only_runs_the_missing_steps():
    from loto_enterprise.core.backtesting import LotoBacktester

    bt = LotoBacktester(_df(), game_type="6/49")
    n = len(bt.draws)
    depth = 25.0
    n_sim = max(1, int(n * depth / 100.0))
    all_idx = list(range(n - n_sim, n))
    skip = set(all_idx[::2])
    preds = bt.run_retroactive_backtest(
        pool_size=10, guarantee=3, lookback_percent=100.0,
        backtest_depth_percent=depth, max_variants=0, simulation_step=1,
        use_feedback=False, enable_hard_inversion=False, smart_reduction=False,
        skip_indices=skip,
    )
    got = {int(p.draw_index) for p in preds}
    assert got.isdisjoint(skip)
    assert got == set(all_idx) - skip

    # Toate sărite → nimic de rulat, fără excepție.
    assert bt.run_retroactive_backtest(
        pool_size=10, guarantee=3, lookback_percent=100.0,
        backtest_depth_percent=depth, max_variants=0,
        use_feedback=False, enable_hard_inversion=False,
        skip_indices=set(all_idx),
    ) == []


def test_generate_wheel_rejects_guarantee_above_pick():
    from wheeling_methods import generate_wheel
    from loto_engine import generate_combinatorial_wheel

    with pytest.raises(ValueError):
        generate_wheel("greedy", list(range(1, 13)), 5, 6)
    with pytest.raises(ValueError):
        generate_combinatorial_wheel(list(range(1, 13)), pick=5, guarantee=6)
