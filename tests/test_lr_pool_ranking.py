"""Strict LR pools preserve rank whenever a full admissible selection exists."""

from itertools import combinations
from pathlib import Path
from random import Random

import pandas as pd
import pytest

from covering.lr_pool_ranking import lr_pool_capacity, rank_lr_pool, select_ranked_lr_pool
from loto_engine import LotoEngine
from loto_enterprise.core.backtesting import LotoBacktester
from loto_enterprise.core.walk_forward_adapter import run_honest_walk_forward


def _no_triple(numbers):
    selected = set(numbers)
    return not any({n + 1, n + 2} <= selected for n in selected)


@pytest.mark.parametrize(
    ("ranked", "replacement", "skipped"),
    [
        ([13, 20, 21, 26, 27, 28, 30, 32, 33, 40, 34, 35], 35, 28),
        ([4, 5, 9, 12, 23, 24, 25, 32, 36, 43, 6, 44], 44, 25),
    ],
)
def test_weakest_rank_in_the_reported_triples_yields_to_next_feasible_number(ranked, replacement, skipped):
    expected = [number for number in ranked[:10] if number != skipped] + [replacement]
    pool = select_ranked_lr_pool(ranked, 10)
    assert pool == expected
    assert _no_triple(pool)


def test_score_ties_keep_canonical_descending_ball_order_and_allow_pairs():
    assert rank_lr_pool(dict.fromkeys(range(1, 9), 1.0), 5) == [8, 7, 5, 4, 2]
    assert select_ranked_lr_pool([40, 10, 20, 30, 41, 5], 5) == [40, 10, 20, 30, 41]


def test_best_legal_rank_is_skipped_only_when_it_blocks_completion():
    # Keeping the first ball 3 would make any full pool of four impossible.
    assert select_ranked_lr_pool([3, 1, 2, 4, 5], 4) == [1, 2, 4, 5]
    assert select_ranked_lr_pool([3, 1, 2, 4, 5], 3) == [3, 1, 4]


def test_capacity_uses_actual_labels_and_never_relaxes_or_shortens():
    assert lr_pool_capacity(range(5, 41)) == 24
    assert lr_pool_capacity(range(1, 50)) == 33
    assert lr_pool_capacity([10, 11, 12, 14, 15, 16, 17, 20]) == 6
    with pytest.raises(ValueError, match="cel mult 6 din cele 8.*necesită 7"):
        select_ranked_lr_pool([10, 11, 12, 14, 15, 16, 17, 20], 7)
    with pytest.raises(ValueError, match="cel mult 2 din cele 2.*necesită 3"):
        rank_lr_pool({1: 10, 2: float("nan"), 3: 5}, 3)
    assert select_ranked_lr_pool([], 0) == []


def test_dense_and_gapped_windows_match_exhaustive_rank_order():
    rng = Random(171)
    for _ in range(35):
        ranked = rng.sample(range(22), 10)
        k = rng.randint(4, 8)
        feasible = [list(t) for t in combinations(ranked, k) if _no_triple(t)]
        if feasible:
            assert select_ranked_lr_pool(ranked, k) == feasible[0]
        else:
            with pytest.raises(ValueError):
                select_ranked_lr_pool(ranked, k)


def test_main_engine_and_ordinary_walk_forward_refuse_an_unplayable_lr_interval():
    frame = pd.read_csv(Path(__file__).resolve().parents[1] / "_ISTORIC" / "loto_6_49.csv").tail(40)
    engine = LotoEngine("6/49")
    engine.data = frame
    settings = {
        "pool_size": 9, "lr_scheme_code": 48, "restrict_base_min": 10, "restrict_base_max": 20,
        "max_consecutive_run": 2,
    }
    calls = (
        lambda: engine.run_institutional_pipeline(track_pool_variation=False, **settings),
        lambda: LotoBacktester(frame, "6/49").run_retroactive_backtest(**settings),
        lambda: run_honest_walk_forward(frame, "6/49", use_cache=False, **settings),
    )
    for call in calls:
        with pytest.raises(ValueError, match="10–20.*cel mult 8 numere fără trei consecutive.*necesită 9"):
            call()


def test_default_main_lr_job_and_ordinary_replay_keep_the_rule_with_enough_capacity():
    frame = pd.read_csv(Path(__file__).resolve().parents[1] / "_ISTORIC" / "loto_6_49.csv").tail(40)
    engine = LotoEngine("6/49")
    engine.data = frame
    settings = {"pool_size": 9, "lr_scheme_code": 48, "restrict_base_min": 10, "restrict_base_max": 40}
    lines, *_, _, audit = engine.run_institutional_pipeline(track_pool_variation=False, **settings)
    assert len(lines) == 12
    assert audit["consecutive_limit"]["applied"] == 2
    assert len(engine.hard_core) == 9 and _no_triple(engine.hard_core)

    rows, metadata = run_honest_walk_forward(
        frame, "6/49", backtest_depth_percent=5, use_cache=False, **settings,
    )
    assert rows and metadata["max_consecutive_run"] == 2
    for row in rows:
        pool = row.ticket_context["hard_core"]
        assert len(pool) == 9 and _no_triple(pool)
        assert all(10 <= number <= 40 for number in pool)


@pytest.mark.parametrize(
    ("options", "generic_limit", "lr_limit"),
    [
        ({}, 0, 2),
        ({"max_consecutive_run": 0}, 0, 2),
        ({"max_consecutive_run": 4}, 4, 2),
        ({"max_consecutive_run": 1}, 1, 1),
    ],
)
def test_worker_applies_the_lr_limit_to_old_queue_options(options, generic_limit, lr_limit):
    from loto_enterprise.core.lotteries import require_lottery
    from worker import _task_norm

    lot = require_lottery("6/49", "RO")
    task = {"pool_size": 9, **options}
    assert _task_norm(task, lot)["max_consecutive_run"] == generic_limit
    assert _task_norm({**task, "lr_scheme_code": 48}, lot)["max_consecutive_run"] == lr_limit


def test_worker_refuses_an_infeasible_lr_base_during_normalization():
    from loto_enterprise.core.lotteries import require_lottery
    from worker import _task_norm

    lot = require_lottery("6/49", "RO")
    task = {"pool_size": 9, "lr_scheme_code": 48, "restrict_base_min": 10, "restrict_base_max": 20}
    with pytest.raises(ValueError, match="cel mult 8 numere fără trei consecutive"):
        _task_norm(task, lot)
