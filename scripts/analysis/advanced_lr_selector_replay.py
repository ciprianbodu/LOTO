"""Compare fixed, daily, and advanced LR policies on the same official tickets."""

from argparse import ArgumentParser
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_budget import FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_pool_ranking import rank_lr_pool
from loto_enterprise.benchmark.lr_budget_scoring import LIVE_BUDGET_REFERENCE
from loto_enterprise.benchmark.methods import EVALUATION_METHODS, call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme, recommend_playable_games
from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw, layout_identity
from loto_enterprise.core.lr_ticket_experts import FIXED_SHARE_VERSION, ONLINE_TICKET_EXPERTS, online_ticket_forecast
from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward
from loto_enterprise.core.ranking import rank_by_score
from scripts.analysis.advanced_lr_validation import METHOD_REVISIONS, THRESHOLDS, _input


GAMES = ("6/49", "5/40", "joker")
DEFAULT_FIRST = "2026-09-13"
DEFAULT_LAST = "2026-10-29"
JOKER_SIZE = 5
POLICIES = ("frozen_rebench", "dynamic_catalog", *EVALUATION_METHODS, ONLINE_TICKET_EXPERTS)


def _date_rows(positions, dates):
    first, final = int(positions[0]), int(positions[-1]) + 1
    bounds = np.asarray(training_cutoffs(pd.DataFrame({"date": dates})))
    origins = list(dict.fromkeys(map(int, bounds[first:final])))
    return zip(origins, [*origins[1:], final], strict=True)


def _selection(method, joker=None):
    return {key: choice for key, choice in (("main", method), ("joker", joker)) if choice is not None}


def _selection_metadata(selection):
    fields = ("method", "source", "fallback", "reason", "selected_method", "validation_days", "training_days", "training_draws")
    return {urn: {key: data[key] for key in fields if key in data} for urn, data in selection.items()}


def _has_fallback(selection):
    reason = selection.get("reason")
    return selection.get("fallback", False) or reason in (
        "insufficient_history", "no_gain_over_random", "unusable_current_scores",
    )


def _daily_entry(day, expected, grades, selection, layout, error=None):
    return {
        "date": day, "draws": expected, "evaluated": len(grades),
        "selection": _selection_metadata(selection), "fallback": any(_has_fallback(value) for value in selection.values()),
        "layout_sha256": layout, "price_bani": grades[0]["price_bani"] if grades else None,
        "paid_variants": grades[0]["paid_variants"] if grades else None,
        "hit_counts": {
            f"{k}+": sum(row[f"hit{k}"] for row in grades) if grades and grades[0][f"hit{k}"] is not None else None
            for k in THRESHOLDS
        },
        "best_variant_hits": [row["max_hits"] for row in grades],
        "draws_with_prize": sum(row["any_prize"] for row in grades), "error": error,
    }


def _grade_plan(day, outcomes, plan, selection):
    if not plan.get("tickets"):
        return _daily_entry(day, len(outcomes), [], selection, None, plan.get("forecast_error"))
    expanded = expand_plan(plan)
    grades = [grade_draw(expanded, drawn, joker, draw_date=day) for drawn, joker in outcomes]
    return _daily_entry(day, len(outcomes), grades, selection, layout_identity(plan))


def _summary(rows):
    scored = [row for row in rows if row["evaluated"]]
    return {
        "days": len(rows), "draws": sum(row["draws"] for row in rows),
        "evaluated": sum(row["evaluated"] for row in rows),
        "infeasible_draws": sum(row["draws"] - row["evaluated"] for row in rows),
        "fallback_days": sum(row["fallback"] for row in rows),
        "fallback_draws": sum(row["draws"] for row in rows if row["fallback"]),
        "main_methods_by_day": dict(Counter(row["selection"].get("main", {}).get("method", "unavailable") for row in rows)),
        "best_variant_hit_distribution": dict(sorted(Counter(
            hits for row in scored for hits in row["best_variant_hits"]
        ).items())),
        "draws_with_prize": sum(row["draws_with_prize"] for row in scored),
        "hit_counts": {
            threshold: sum(row["hit_counts"][threshold] for row in scored)
            if scored and scored[0]["hit_counts"][threshold] is not None else None
            for threshold in (f"{k}+" for k in THRESHOLDS)
        },
        "dates": rows,
    }


def _frozen_day(rows, day, selection, drawn):
    if any((row["numbers"], row["joker_drawn"]) != outcome for row, outcome in zip(rows, drawn, strict=True)):
        raise ValueError("Official outcomes differ from the frozen evaluator's input.")
    grades = [row for row in rows if "price_bani" in row]
    layout = rows[0].get("layout_id")
    if any(row.get("layout_id") != layout for row in rows):
        raise ValueError("A double-draw date received different frozen portfolios.")
    return _daily_entry(day, len(rows), grades, selection, layout, rows[0].get("error"))


def _replay_game(history, game_id, first_day, last_day, budget):
    lot = require_lottery(game_id, "RO")
    full, matrix, dates, fingerprint = _input(history, lot)
    targets = np.flatnonzero((dates >= first_day) & (dates <= last_day))
    if not len(targets) or targets[0] == 0:
        raise ValueError(f"{game_id} lacks the specified dates or training history.")
    origin = int(targets[0])
    end = int(targets[-1]) + 1
    captured = full.iloc[:end]
    dates = dates.iloc[:end]
    matrix = matrix[:end]
    cutoffs = training_cutoffs(captured)
    filename = Path(lot.csv).name
    template, = recommend_games(budget, FOUR_PLUS, game_id)
    scheme = budget_plan_scheme(template)

    # The fixed production evaluator accepts a depth percentage. The offset
    # keeps floor(depth * rows) equal to the exact number of holdout draws.
    report = validate_lr_walk_forward(
        game_id, (filename, captured), budget_lei=budget, objective=FOUR_PLUS, scheme=scheme.code,
        inverse=False, restricted=False, dynamic=True, lookback=100, hit_target=4, joker_size=JOKER_SIZE,
        depth=100 * (len(targets) + 0.25) / len(captured), replay_policy="fixed",
    )
    expected_dates = dates.iloc[targets].dt.strftime("%Y-%m-%d").tolist()
    if [row["date"] for row in report["draws"]] != expected_dates:
        raise ValueError("Frozen evaluator selected different holdout dates.")
    by_urn = report["methods"]["tested"]
    fixed = _selection(by_urn[lot.bench_key], by_urn.get(lot.bench_key_urna2))
    fixed = {key: {**choice, "method": " + ".join(member["method"] for member in choice["members"])}
             for key, choice in fixed.items()}

    bonus = bonus_positions = None
    if lot.geo.second:
        bonus, mask = valid_draw_matrix(captured, ["joker"], draw_n=1, max_num=lot.geo.second.max_n)
        bonus_positions = np.flatnonzero(mask)
        actual_bonus = {i: int(value) for i, value in zip(bonus_positions, bonus[:, 0], strict=True)}
        if any(int(i) not in actual_bonus for i in targets):
            raise ValueError("An evaluated Joker outcome is missing its official second-urn ball.")
    else:
        actual_bonus = {}

    results = {name: [] for name in POLICIES}
    for start, stop in _date_rows(targets, dates):
        prefix = captured.iloc[:start]
        day = dates.iloc[start].date().isoformat()
        drawn = [(matrix[i].tolist(), actual_bonus.get(i)) for i in range(start, stop)]
        results["frozen_rebench"].append(_frozen_day(report["draws"][start - origin:stop - origin], day, fixed, drawn))

        generated, = recommend_playable_games(
            budget, FOUR_PLUS, game_id, {game_id: (filename, prefix)}, pool_codes={game_id: scheme.code},
            dynamic=True, lookback=100, joker_size=JOKER_SIZE, hit_target=4,
        )
        forecast = generated.get("forecast") or {}
        selected = _selection(forecast.get("selection"), forecast.get("joker_selection"))
        results["dynamic_catalog"].append(_grade_plan(day, drawn, generated, selected))

        jokers = ()
        if lot.geo.second:
            bonus_prior = bonus[:np.searchsorted(bonus_positions, start)]
            scores, _ = call_method("frequency", bonus_prior, lot.geo.second.max_n)
            jokers = rank_by_score(scores, JOKER_SIZE)
        for method in EVALUATION_METHODS:
            selected = _selection({"method": method}, {"method": "frequency"} if lot.geo.second else None)
            try:
                main_scores, _ = call_method(method, matrix[:start], lot.max_n, history_cutoffs=cutoffs[:start])
                pool = rank_lr_pool(main_scores, scheme.n)
                paid = restrict_to_pool(deepcopy(template), FOUR_PLUS, pool, jokers)
                paid["forecast_pool"] = pool
                result = _grade_plan(day, drawn, paid, selected)
            except (ArithmeticError, ValueError) as exc:
                result = _daily_entry(day, len(drawn), [], selected, None, str(exc))
            results[method].append(result)

        try:
            switched = online_ticket_forecast(
                lot, template, scheme, prefix, inverse=False, number_range=None, lookback=100,
                joker_size=JOKER_SIZE, objective=FOUR_PLUS,
            )
            policy = switched["forecast"]
            result = _grade_plan(day, drawn, switched, _selection(policy["selection"], policy["joker_selection"]))
        except (ArithmeticError, ValueError) as exc:
            result = _daily_entry(day, len(drawn), [], {}, None, str(exc))
        results[ONLINE_TICKET_EXPERTS].append(result)

    prices = {row["price_bani"] for rows in results.values() for row in rows if row["evaluated"]}
    if len(prices) != 1:
        raise ValueError(f"{game_id}: evaluated portfolios did not have the same cost: {sorted(prices)}")
    return {
        "game": game_id, "history_sha256": fingerprint, "training_draws": origin, "trained_through": report["training_end"],
        "scheme_code": scheme.code, "price_bani": prices.pop(), "dates": list(dict.fromkeys(expected_dates)),
        "draws": len(expected_dates), "frozen_recipes": fixed,
        "policies": {name: _summary(rows) for name, rows in results.items()},
    }


def evaluate_selector_replay(history, first_day=DEFAULT_FIRST, last_day=DEFAULT_LAST, budget=100, workers=3):
    jobs = [(history, game, first_day, last_day, budget) for game in GAMES]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_replay_game, *job) for job in jobs]
        reports = [future.result() for future in futures]
    if any(result["dates"] != reports[0]["dates"] for result in reports):
        raise ValueError("The chosen period does not have the same dates for all the games.")
    project = Path(__file__).resolve().parents[2]
    return {
        "first_date": first_day, "last_date": last_day, "settings": {
            "budget_lei": budget, "objective": FOUR_PLUS, "base": "full", "inverse": False,
            "history_pct": 100, "joker_size": JOKER_SIZE, "bench_target": 4,
        },
        "method_revisions": {**METHOD_REVISIONS, "dynamic_catalog": LIVE_BUDGET_REFERENCE, ONLINE_TICKET_EXPERTS: FIXED_SHARE_VERSION},
        "configuration_sha256": {name: sha256((project / name).read_bytes()).hexdigest()
                                 for name in ("best_methods.json", "curated_methods.json") if (project / name).exists()},
        "protocol": {
            "frozen_rebench": "Live LR walk-forward selects from training dates once, then reranks on each prior-day prefix.",
            "dynamic_catalog": "The normal live LR budget generator runs once per date on the complete earlier prefix. "
                               "Its bounded rolling comparison scores paid variants before the forecast date.",
            "new": "The two numeric scorers rank the main urn, using the independent frequency Joker pool. "
                   "The live Fixed Share portfolio policy is replayed from the complete earlier prefix.",
            "paired": "Exact paid tickets at the same price per game. A date with two official draws uses one forecast "
                      "for both outcomes. A day is counted once in method/fallback summaries.",
        },
        "results": reports,
    }


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument("--history", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--first-day", default=DEFAULT_FIRST)
    cli.add_argument("--last-day", default=DEFAULT_LAST)
    cli.add_argument("--budget", type=int, default=100)
    cli.add_argument("--workers", type=int, default=3)
    args = cli.parse_args()
    report = evaluate_selector_replay(args.history, args.first_day, args.last_day, args.budget, args.workers)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
