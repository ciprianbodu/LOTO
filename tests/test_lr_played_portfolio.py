"""Cross-game replay of bought variants, chronology and official same-day ordering."""

from copy import deepcopy

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from loto_enterprise.core.lr_played_portfolio import evaluation_contract, expand_plan, grade_draw
from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme, recommend_playable_games
from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward
import numpy as np
import pandas as pd


def test_grade_counts_duplicate_cost_and_respects_540_sixth():
    plan, = recommend_games(100, ANY_PRIZE, "5/40")
    scheme = budget_plan_scheme(plan)
    pool = tuple(range(1, scheme.n + 1))
    played = restrict_to_pool(plan, ANY_PRIZE, pool)
    actual = expand_plan(played)
    first = actual.variants[0][0]
    sixth = next(n for n in range(1, 41) if n not in first)
    draw = (*first, sixth)
    grade = grade_draw(actual, draw)
    assert grade["hit5"] and grade["hit6"] is None and grade["prize_counts"]["I"] >= 1
    swapped = (*first[:4], sixth, first[-1])
    assert grade_draw(actual, swapped)["max_hits"] == 5
    doubled = deepcopy(played)
    doubled["tickets"] *= 2
    doubled["price_bani"] *= 2
    doubled["budget_bani"] *= 2
    twice = grade_draw(expand_plan(doubled), draw)
    assert twice["distinct_variants"] == grade["distinct_variants"]
    assert twice["duplicates"] == 2 * grade["paid_variants"] - grade["distinct_variants"]
    assert twice["winning_variants"] == 2 * grade["winning_variants"]
    assert twice["price_bani"] == 2 * grade["price_bani"]


def test_daily_policy_replays_real_generator_and_ignores_later_draws():
    rng = np.random.default_rng(82)
    frame = pd.DataFrame([rng.choice(range(1, 50), 6, replace=False) for i in range(23)],
                         columns=["n1", "n2", "n3", "n4", "n5", "n6"])
    frame.insert(0, "date", pd.date_range("2025-01-01", periods=23).strftime("%d-%m-%Y"))
    frame.loc[21, "date"] = frame.loc[20, "date"]
    plan, = recommend_games(100, FOUR_PLUS, "6/49")
    code = budget_plan_scheme(plan).code
    options = dict(budget_lei=100, objective=FOUR_PLUS, scheme=code, inverse=True, restricted=True,
                   dynamic=True, lookback=100, hit_target=4, joker_size=3, depth=12,
                   replay_policy="daily")
    report = validate_lr_walk_forward("6/49", ("sample.csv", frame), **options)
    first = report["draws"][0]
    assert first["training_rows"] == 20
    assert first["tickets"] == report["draws"][1]["tickets"]
    changed = frame.copy()
    changed.loc[22, ["n1", "n2", "n3", "n4", "n5", "n6"]] = [1, 2, 3, 4, 5, 6]
    rerun = validate_lr_walk_forward("6/49", ("sample.csv", changed), **options)
    assert first["tickets"] == rerun["draws"][0]["tickets"]
    generated, = recommend_playable_games(
        100, FOUR_PLUS, "6/49", {"6/49": ("sample.csv", frame.iloc[:20])},
        inverse=True, dynamic=True, number_range=(10, 40), pool_codes={"6/49": code}, hit_target=4)
    assert first["tickets"] == generated["tickets"]
    assert generated["evaluation"]["layout"] == first["layout_id"]


def test_joker_paid_scoring_requires_one_bonus_in_the_prefix_and_reports_hit6_unavailable():
    import numpy as np
    import pandas as pd

    from covering.lr_budget import FOUR_PLUS, recommend_games
    from loto_enterprise.benchmark.lr_budget_scoring import evaluate_budget_recipes
    from loto_enterprise.benchmark.lr_hypotheses import GameDef
    from loto_enterprise.core.lotteries import require_lottery
    from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme

    lot = require_lottery("joker", "RO")
    layout, = recommend_games(100, FOUR_PLUS, lot.game_id)
    scheme = budget_plan_scheme(layout)
    matrix = np.tile(np.arange(1, 6, dtype=np.int32), (82, 1))
    dates = pd.Series(pd.date_range("2026-01-01", periods=len(matrix)))
    frame = pd.DataFrame({"date": dates, "joker": [None] * 80 + [3, 3]})
    game = GameDef(lot.bench_key, lot.name, "", [], lot.max_n, lot.draw_n,
                   pool_extra=scheme.n - lot.pick_n, pick_n=lot.pick_n,
                   history_cutoffs=tuple(range(len(matrix))))
    urns = [{
        "decision": {"members": [{"method": "frequency", "weight": 1.0}]},
        "rows": [{"method": "frequency"}], "target": target, "protocol": "",
    } for target in (4, 1)]
    report = evaluate_budget_recipes(
        layout, scheme, lot, frame, matrix, dates, game, urns, restricted=False,
        inverse=False, dynamic=False, lookback=100, joker_size=2, check=lambda **kwargs: None,
    )
    candidate, = report["candidates"]
    assert report["draws"] == 2
    assert candidate["infeasible"] == candidate["n_eval"] == 1
    assert candidate["cost_bani"] == layout["price_bani"]
    assert candidate["paid_variants"] == layout["variants"]
    assert candidate["hit6"] is None


def test_budget_reports_why_inverse_pool_is_impossible():
    from loto_enterprise.benchmark.lr_budget_scoring import evaluate_budget_recipes

    urns = [{"name": "Urna principală", "error": "Rămân 15 numere eligibile. Sunt necesare 16."}]
    result = evaluate_budget_recipes({}, None, None, None, None, None, None, urns,
                                     restricted=True, inverse=True, dynamic=False,
                                     lookback=100, joker_size=3, check=lambda: None)
    assert result == {"error": "Urna principală: Rămân 15 numere eligibile. Sunt necesare 16.", "n_eval": 0}
