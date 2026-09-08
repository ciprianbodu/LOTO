"""Teste de caracterizare pentru calea SECVENTIALA CU STARE (adaptive feedback +
hard inversion) din LotoBacktester.run_retroactive_backtest — inainte de orice
refactorizare (CLAUDE.md §12 P2: "adauga teste de caracterizare inaintea
fiecarei extrageri").

Singurul test existent pentru run_retroactive_backtest (test_wf_partial_cache_
growth.py) exercita DOAR calea stateless (use_feedback=False,
enable_hard_inversion=False) — calea cu stare, care reimplementeaza manual
logica pasului in loc sa refoloseasca `_retroactive_step_stateless`, n-avea
nicio acoperire. Valorile de mai jos sunt "golden": capturate rulind codul
NEATINS pe date sintetice deterministe (seed fix, fara best_methods.json ->
fallback determinist pe `frequency`)."""
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


# (draw_index, hits, hits_union, n_variants) — capturat pe codul dinaintea
# refactorizarii lui run_retroactive_backtest (extragerea logicii comune de pas
# in _retroactive_step_stateless).
_GOLDEN = [
    (32, 1, 1, 10),
    (33, 2, 2, 10),
    (34, 0, 0, 10),
    (35, 2, 2, 10),
    (36, 0, 0, 10),
    (37, 1, 1, 10),
    (38, 2, 2, 10),
    (39, 1, 1, 10),
]


def test_stateful_path_matches_golden_reference(monkeypatch, tmp_path):
    from loto_enterprise.core.backtesting import LotoBacktester

    # Fara best_methods.json -> fallback determinist pe `frequency` (CLAUDE.md
    # §4.2), la fel ca la capturarea valorilor golden. `_DEFAULT_CONFIG_PATH`
    # e o cale ABSOLUTA derivata din locatia modulului, nu relativa la CWD —
    # trebuie redirectionata explicit, un `chdir` n-ar avea niciun efect (si
    # ar rupe accesul relativ la covering_designs/).
    import loto_enterprise.core.method_selector as _ms

    monkeypatch.setattr(_ms, "_DEFAULT_CONFIG_PATH", tmp_path / "nope.json")

    bt = LotoBacktester(_df(), game_type="6/49")
    preds = bt.run_retroactive_backtest(
        pool_size=10,
        guarantee=3,
        lookback_percent=100.0,
        backtest_depth_percent=20.0,
        max_variants=0,
        simulation_step=1,
        use_feedback=True,
        enable_hard_inversion=True,
        smart_reduction=False,
    )
    got = [(p.draw_index, p.hits, p.hits_union, len(p.variants)) for p in preds]
    assert got == _GOLDEN


def test_stateful_path_predictions_are_chronologically_sorted(monkeypatch, tmp_path):
    from loto_enterprise.core.backtesting import LotoBacktester
    import loto_enterprise.core.method_selector as _ms

    monkeypatch.setattr(_ms, "_DEFAULT_CONFIG_PATH", tmp_path / "nope.json")
    bt = LotoBacktester(_df(), game_type="6/49")
    preds = bt.run_retroactive_backtest(
        pool_size=10,
        guarantee=3,
        lookback_percent=100.0,
        backtest_depth_percent=20.0,
        max_variants=0,
        use_feedback=True,
        enable_hard_inversion=True,
    )
    indices = [p.draw_index for p in preds]
    assert indices == sorted(indices)


def test_stateful_path_with_feedback_only_no_inversion_still_runs(monkeypatch, tmp_path):
    """use_feedback=True, enable_hard_inversion=False: tot calea cu stare
    (nu e stateless — vezi `_stateless = (not use_feedback) and (not
    enable_hard_inversion)`), dar fara injectarea temp_blacklist."""
    from loto_enterprise.core.backtesting import LotoBacktester
    import loto_enterprise.core.method_selector as _ms

    monkeypatch.setattr(_ms, "_DEFAULT_CONFIG_PATH", tmp_path / "nope.json")
    bt = LotoBacktester(_df(), game_type="6/49")
    preds = bt.run_retroactive_backtest(
        pool_size=10,
        guarantee=3,
        lookback_percent=100.0,
        backtest_depth_percent=20.0,
        max_variants=0,
        use_feedback=True,
        enable_hard_inversion=False,
    )
    assert len(preds) == 8
    assert all(p.hits >= 0 for p in preds)
