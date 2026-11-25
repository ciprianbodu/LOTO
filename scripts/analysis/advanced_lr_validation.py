"""Compare versioned advanced LR methods on a fixed retrospective daily protocol.

Run from the repository with an explicit history directory and output path.
Only the named output file is written. All contenders forecast prior days only.
"""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_budget import FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_pool_ranking import rank_lr_pool
from loto_enterprise.benchmark.methods import EVALUATION_METHODS, call_method
from loto_enterprise.benchmark.methods_online import HAZARD, PRIOR_DRAWS, RUN_LIMIT, RunLengthPosterior, calibration_losses
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import chronological_history, history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw
from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme
from loto_enterprise.core.lr_ticket_experts import DEFAULT_EXPERTS, FIXED_SHARE_VERSION, TRAINING_DAYS, online_ticket_forecast
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.wf_sig import method_cache_semantics
from scripts.analysis.legacy_methods_2026_11_20 import call_method as archived_call


ARCHIVED = ("bayes_dirichlet_forget", "ridge_pooled_feats", "hedge_experts_mixture")
CONTENDERS = (*DEFAULT_EXPERTS, *ARCHIVED, "random")
NUMERIC_PAIRS = (("bayes_runlength_shrink", "bayes_dirichlet_forget"),
                 ("temporal_stacked_calibration", "ridge_pooled_feats"))
METHOD_REVISIONS = {name: method_cache_semantics(name).removeprefix("|") for name in EVALUATION_METHODS}
THRESHOLDS = (4, 5, 6)
PAIRS = (*NUMERIC_PAIRS, ("online_ticket_experts", "hedge_experts_mixture"),
         *((method, "frequency") for method in (*EVALUATION_METHODS, "online_ticket_experts")))
BOOTSTRAP_SEED = 20261125
BOOTSTRAP_SAMPLES = 10000
UNIFORM_SEED = 1872026
UNIFORM_REPLICATES = 24
UNIFORM_DAYS = 450
UNIFORM_WARMUP = 128


def _input(history, lot):
    raw = (history / Path(lot.csv).name).read_bytes()
    frame = chronological_history(pd.read_csv(BytesIO(raw))).reset_index(drop=True)
    columns = [f"n{i + 1}" for i in range(lot.draw_n)]
    _, valid = valid_draw_matrix(frame, columns, draw_n=lot.draw_n, max_num=lot.max_n)
    frame = frame.loc[valid].reset_index(drop=True)
    matrix, _ = valid_draw_matrix(frame, columns, draw_n=lot.draw_n, max_num=lot.max_n)
    dates = history_dates(frame)
    if dates is None:
        raise ValueError(f"Missing dates for {lot.name}.")
    return frame, matrix, dates, sha256(raw).hexdigest()


def _pool(name, prior, cutoffs, maximum, width):
    call = archived_call if name in ARCHIVED else call_method
    scores, _ = call(name, prior, maximum, history_cutoffs=cutoffs)
    return rank_lr_pool(scores, width), scores


def _sign_flip_p(differences):
    """Exact 2-sided paired randomization over complete dates."""
    differences = tuple(int(n) for n in differences if n)
    observed = abs(sum(differences))
    outcomes = Counter({0: 1})
    for difference in differences:
        shifted = Counter()
        for total, ways in outcomes.items():
            shifted[total - difference] += ways
            shifted[total + difference] += ways
        outcomes = shifted
    return sum(ways for value, ways in outcomes.items() if abs(value) >= observed) / 2 ** len(differences)


def _paired_threshold(rows, candidate, baseline, day_indices, day_sizes, bootstrap):
    a, b = rows[candidate], rows[baseline]
    if not a or a[0] is None:
        return {"status": "not_applicable"}
    differences = np.asarray(a, dtype=np.int8) - np.asarray(b, dtype=np.int8)
    blocks = np.add.reduceat(differences, day_indices)
    discordant = int(np.count_nonzero(blocks))
    rates = 100 * blocks[bootstrap].sum(axis=1) / day_sizes[bootstrap].sum(axis=1)
    return {
        "candidate_hits": sum(a), "baseline_hits": sum(b),
        "wins": int(np.sum(differences > 0)), "losses": int(np.sum(differences < 0)),
        "ties": int(np.sum(differences == 0)), "net": int(differences.sum()),
        "days": len(blocks), "draws": len(differences), "discordant_days": discordant,
        "candidate_minus_baseline_percentage_points": 100 * float(differences.mean()),
        "date_bootstrap_ci95_pp": list(map(float, np.quantile(rates, (0.025, 0.975)))),
        "p_raw": _sign_flip_p(blocks),
        "status": "no_discordance" if not discordant else "rare" if discordant < 5 else "evaluated",
    }


def _adjust_holm(entries):
    entries = sorted(entries, key=lambda row: row["p_raw"])
    previous = 0.0
    for index, row in enumerate(entries):
        previous = min(1.0, max(previous, row["p_raw"] * (len(entries) - index)))
        row["p_holm"] = previous
    return len(entries)


def evaluate_uniform_bocpd():
    rng = np.random.default_rng(UNIFORM_SEED)
    reports = []
    for name, maximum, width in (("6/49", 49, 6), ("5/40", 40, 6), ("joker_urna1", 45, 5), ("joker_urna2", 20, 1)):
        relative = defaultdict(list)
        losses = defaultdict(list)
        fair = np.full(maximum, width / maximum)
        mass_error = 0.0
        for _ in range(UNIFORM_REPLICATES):
            model = RunLengthPosterior(width / maximum, maximum)
            trajectory = defaultdict(list)
            for day in range(UNIFORM_DAYS):
                probabilities = model.predict()
                drawn = rng.choice(maximum, size=width, replace=False) + 1
                if day >= UNIFORM_WARMUP:
                    mass_error = max(mass_error, abs(float(probabilities.sum()) - width))
                    forecast = calibration_losses(probabilities, drawn)
                    uniform = calibration_losses(fair, drawn)
                    for key, value in forecast.items():
                        trajectory[key].append(value - uniform[key])
                        losses[key].append(value)
                model.observe(np.isin(np.arange(1, maximum + 1), drawn).astype(np.int64), 1)
            for key, path in trajectory.items():
                relative[key].append(float(np.mean(path)))
        reports.append({
            "urn": name, "universe": maximum, "draw_size": width, "max_inclusion_mass_error": mass_error,
            "losses": {key: {"bocpd": float(np.mean(values)), "uniform": baseline,
                             "mean_excess": float(np.mean(relative[key])),
                             "replicate_sd": float(np.std(relative[key], ddof=1))}
                       for key, values in losses.items()
                       for baseline in (calibration_losses(fair, np.arange(1, width + 1))[key],)},
        })
    return {"seed": UNIFORM_SEED, "replicates_per_urn": UNIFORM_REPLICATES, "days_per_replicate": UNIFORM_DAYS,
            "warmup_days": UNIFORM_WARMUP, "scored_per_urn": UNIFORM_REPLICATES*(UNIFORM_DAYS - UNIFORM_WARMUP),
            "hazard": HAZARD, "max_run_length": RUN_LIMIT, "prior_pseudodraws": PRIOR_DRAWS, "results": reports}


def _calibration(rows):
    return {method: {key: float(np.mean([item[key] for item in values])) for key in ("brier", "log_loss")}
            for method, values in rows.items()}


def _marginals(metrics, scores, numbers, universe):
    draw_n = len(numbers)
    metrics["uniform"].append(calibration_losses(np.full(universe, draw_n / universe), numbers))
    for name in EVALUATION_METHODS:
        metrics[name].append(calibration_losses(tuple(scores[name].values()), numbers))


def evaluate(game, history, test_days, budget):
    lot = require_lottery(game, "RO")
    frame, matrix, dates, fingerprint = _input(history, lot)
    cutoffs = np.asarray(training_cutoffs(frame), dtype=np.int64)
    origins = np.flatnonzero(cutoffs == np.arange(len(matrix)))
    if len(origins) <= test_days + 384:
        raise ValueError("The selected history does not have enough training dates.")
    first = int(origins[-test_days])
    origins = origins[-test_days:]
    template, = recommend_games(budget, FOUR_PLUS, game)
    scheme = budget_plan_scheme(template)
    width = scheme.n

    bonus, indices, bonus_dates = None, None, None
    if lot.bench_key_urna2:
        bonus, valid = valid_draw_matrix(frame, ["joker"], max_num=20, draw_n=1)
        indices = np.flatnonzero(valid)
        bonus_dates = np.asarray(training_cutoffs(frame.loc[valid]))
    hits = {threshold: defaultdict(list) for threshold in THRESHOLDS}
    prices, calibration, bonus_calibration = (defaultdict(list) for _ in range(3))
    selections = Counter()
    for origin, end in zip(origins, np.r_[origins[1:], len(matrix)], strict=True):
        prior = matrix[:origin]
        day_cutoffs = tuple(cutoffs[:origin])
        jokers, bonus_scores, observed_bonus = [], {}, {}
        if bonus is not None:
            bonus_end = int(np.searchsorted(indices, origin))
            for name in ("frequency", *EVALUATION_METHODS):
                bonus_scores[name], _ = call_method(name, bonus[:bonus_end], 20, history_cutoffs=bonus_dates[:bonus_end])
            jokers = rank_by_score(bonus_scores["frequency"], 5)
            observed_bonus = {i: int(value[0]) for i, value in zip(indices[bonus_end:], bonus[bonus_end:], strict=True)
                              if i < end}
            if len(observed_bonus) != end - origin:
                raise ValueError("The measured Joker date contains an unverified second-urn outcome.")
        plans, scores = {}, {}
        for name in CONTENDERS:
            pool, scores[name] = _pool(name, prior, day_cutoffs, lot.max_n, width)
            plans[name] = restrict_to_pool(deepcopy(template), FOUR_PLUS, pool, jokers)
            plans[name]["forecast_pool"] = pool

        actual = [(tuple(map(int, matrix[i])), observed_bonus.get(i)) for i in range(origin, end)]
        date = dates.iloc[origin].date().isoformat()
        played = online_ticket_forecast(
            lot, template, scheme, frame.iloc[:origin], inverse=False, number_range=None,
            lookback=100, joker_size=5, objective=FOUR_PLUS,
        )
        selections[played["forecast"]["selection"]["method"]] += 1
        plans["online_ticket_experts"] = played
        for name, played in plans.items():
            expanded = expand_plan(played)
            for drawn, joker in actual:
                grade = grade_draw(expanded, drawn, joker, draw_date=date)
                for threshold in THRESHOLDS:
                    hits[threshold][name].append(grade[f"hit{threshold}"])
                prices[name].append(grade["price_bani"])
        for drawn, joker in actual:
            _marginals(calibration, scores, drawn, lot.max_n)
            if bonus is not None:
                _marginals(bonus_calibration, bonus_scores, [joker], 20)

    day_indices = origins - first
    day_sizes = np.diff(np.r_[day_indices, len(matrix) - first])
    bootstrap = np.random.default_rng(BOOTSTRAP_SEED).integers(
        len(origins), size=(BOOTSTRAP_SAMPLES, len(origins)),
    )
    paired = {
        f"{a}/{b}": {
            f"{threshold}+": _paired_threshold(hits[threshold], a, b, day_indices, day_sizes, bootstrap)
            for threshold in THRESHOLDS
        }
        for a, b in PAIRS
    }
    return {"game": game,"history_sha256":fingerprint,"training_draws":first,
            "first_date":dates.iloc[first].date().isoformat(),"last_date":dates.iloc[-1].date().isoformat(),
            "days":len(origins),"draws":len(matrix)-first,"budget_lei":budget,"pool_size":width,
            "paid_variant_hits":{f"{threshold}+": {name: sum(values) if values[0] is not None else None
                                                 for name, values in methods.items()}
                                 for threshold, methods in hits.items()},
            "mean_paid_bani":{name:sum(values)/len(values) for name,values in prices.items()},
            "paired":paired,"inclusion_losses":_calibration(calibration),
            "joker_urna2_inclusion_losses":_calibration(bonus_calibration),
            "fixed_share_days":dict(selections),
            "fixed_share_version":FIXED_SHARE_VERSION, "fixed_share_lookback_days":TRAINING_DAYS}


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument("--history", required=True, type=Path)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--days", type=int, default=104)
    cli.add_argument("--budget", type=int, default=100)
    args = cli.parse_args()
    if args.days < 1:
        cli.error("--days must be positive")
    reports = [evaluate(game, args.history, args.days, args.budget) for game in ("6/49", "5/40", "joker")]
    comparisons = (row for report in reports for thresholds in report["paired"].values()
                   for row in thresholds.values() if "p_raw" in row)
    document = {
        "protocol": "Paired retrospective comparison at the same budget and paid price per game. "
                    "Portfolios see only previous days. The live Fixed Share policy uses up to 104 prior dates "
                    "with complete outcomes. The random scorer is a single reproducible comparator. "
                    "These diagnostics are exploratory and cannot establish predictive advantage.",
        "paired_inference": {
            "date_unit": "All draws on the same date form one paired block.",
            "p_raw": "Two-sided exact sign-flip under symmetric paired date-level differences.",
            "p_holm": "Holm family-wide adjustment over applicable game/method/threshold comparisons.",
            "comparisons": _adjust_holm(comparisons),
            "ci": "Percentile date-cluster bootstrap for candidate minus comparator hit rates in percentage points.",
            "ci_draws": BOOTSTRAP_SAMPLES, "ci_seed": BOOTSTRAP_SEED,
            "rare": "Fewer than 5 dates with different hit totals. Degenerate empirical intervals are possible.",
        },
        "method_revisions": METHOD_REVISIONS, "results": reports,
        "uniform_bocpd": evaluate_uniform_bocpd(),
    }
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
