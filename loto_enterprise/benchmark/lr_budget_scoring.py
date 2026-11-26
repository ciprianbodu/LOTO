"""Compare eligible Re-Bench recipes using the actual paid zones of the selected budget."""

from collections import Counter, deque
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json

import numpy as np

from covering.lr_pool_budget import positional_recipe, restrict_to_pool
from covering.lr_pool_ranking import rank_lr_pool
from loto_enterprise.benchmark.lr_provenance import dynamic_formulas
from loto_enterprise.benchmark.methods import EVALUATION_METHODS, call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.dynamic_winner import CANDIDATES
from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw, layout_identity
from loto_enterprise.core.ranking import rank_by_score


# Predeclared selection sample. The independent outer walk-forward starts after selection.
PORTFOLIO_DAYS = 60


def _recipes(urn):
    incumbent = urn["decision"]["members"]
    recipes = {" + ".join(m["method"] for m in incumbent): incumbent}
    for row in urn["rows"]:
        name = row["method"]
        recipes.setdefault(name, [{"method": name, "weight": 1.0}])
    return recipes


def evaluate_budget_recipes(layout, scheme, lot, frame, matrix, dates, game, urns, *, restricted,
                            inverse, dynamic, lookback, joker_size, check, recipe=None):
    from loto_enterprise.benchmark.lr_hypotheses import _training
    from loto_enterprise.core.lr_budget_forecast import _profile_scores

    for urn in urns:
        if urn.get("error"):
            return {"error": f"{urn['name']}: {urn['error']}", "n_eval": 0}
    if dates is None or any(not u.get("decision") or not u.get("rows") for u in urns):
        return {"error": "Istoricul nu permite compararea portofoliilor pe zile complete.", "n_eval": 0}
    bounds = game.history_cutoffs
    origins = tuple(dict.fromkeys(b for b in bounds if b >= 80))[-PORTFOLIO_DAYS:]
    if not origins:
        return {"error": "Nu există o zi de test după prefixul minim de 80 de extrageri."}
    limits = (10, 40) if restricted else None
    base = range(10, min(40, lot.max_n) + 1) if restricted else range(1, lot.max_n + 1)
    outside = set(range(1, lot.max_n + 1)).difference(base)
    slots = joker_size if lot.geo.second else 0
    if recipe is None or not recipe.matches(layout, scheme.n, slots):
        recipe = positional_recipe(layout, scheme.n, slots, solver=restrict_to_pool)
    references = replay_budget_portfolios(
        layout, lot, scheme, matrix, frame, origins, inverse=False, dynamic=dynamic,
        lookback=lookback, joker_size=joker_size, number_range=limits, check=check, recipe=recipe,
    ) if inverse else {}
    main_recipes = _recipes(urns[0])
    bonus_recipes = {"": []}
    bonus_values = {}
    if lot.geo.second:
        bonus_matrix, valid = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=lot.geo.second.max_n)
        bonus_positions = np.flatnonzero(valid)
        bonus_bounds = training_cutoffs(frame.loc[valid])
        bonus_recipes = _recipes(urns[1])
        bonus_values = dict(zip(bonus_positions, map(int, bonus_matrix[:, 0]), strict=True))

    objective = "any_prize" if layout["objective"] == "any_prize" else "hit4"
    recipes = {}
    daily_evaluations = []
    for idx, origin in enumerate(origins):
        check()
        end = origins[idx + 1] if idx + 1 < len(origins) else len(matrix)
        reference = references.get(origin, {})
        if "error" in reference:
            for m in main_recipes:
                for b in bonus_recipes:
                    recipes.setdefault((m, b), Counter())["infeasible"] += end - origin
            continue
        blocked = outside.union(reference.get("pool", ()))
        past, cuts = _training(matrix, bounds, origin, lookback)
        main = {}
        for name, members in main_recipes.items():
            try:
                scores, _ = _profile_scores(({"members": members}, "members"), past, cuts,
                                           lot.max_n, blocked, scheme.n)
                main[name] = sorted(rank_lr_pool(scores, scheme.n))
            except (ValueError, ArithmeticError):
                main[name] = None
        bonus = {"": ()}
        if lot.geo.second:
            offset = int(np.searchsorted(bonus_positions, origin))
            bonus = dict.fromkeys(bonus_recipes)
            if offset:
                bp, bc = _training(bonus_matrix, bonus_bounds, offset, lookback)
                excluded = reference.get("jokers", ())
                for name, members in bonus_recipes.items():
                    try:
                        scores, _ = _profile_scores(({"members": members}, "members"), bp, bc,
                                                   lot.geo.second.max_n, excluded, joker_size)
                        bonus[name] = rank_by_score(scores, joker_size)
                    except (ValueError, ArithmeticError):
                        continue
        outcomes = [(matrix[i], bonus_values.get(i)) for i in range(origin, end)]
        day = dates.iloc[origin].date().isoformat()
        evaluated = measure_paid_portfolios(layout, main, bonus, outcomes, day, objective, recipe=recipe)
        daily_evaluations.append(evaluated)
        for pair, tally in evaluated.items():
            recipes.setdefault(pair, Counter()).update(tally)

    incumbent = next(iter(main_recipes)), next(iter(bonus_recipes))
    def eligible(pair):
        return (all(name not in (*EVALUATION_METHODS, "random")
                    for name in (*[m["method"] for m in main_recipes[pair[0]]],
                                 *[m["method"] for m in bonus_recipes[pair[1]]]))
                and recipes[pair]["objective_evaluated"] > 0
                and _same_portfolio_sample(daily_evaluations, pair, incumbent)
                and all(recipes[pair][k] >= recipes[incumbent][k] for k in ("hit4","hit5","hit6")))
    # Ties retain the original policy and terminal price. Failures remain in the denominator.
    winner = max((key for key in recipes if eligible(key)),
                 key=lambda k: (recipes[k][objective], k == incumbent), default=incumbent)
    if not recipes[winner]["objective_evaluated"]:
        return {"error": "Obiectivul comun nu a putut fi evaluat pe biletele disponibile.",
                "n_eval": recipes[winner]["n_eval"], "portfolio_refinement": recipe.portfolio_refinement}
    for u, name, choices in zip(urns, winner, (main_recipes, bonus_recipes)):
        u["decision"].update(
            members=choices[name], portfolio_objective=objective, evaluated=recipes[winner]["objective_evaluated"],
            reference_members=[], reference_policy=LIVE_BUDGET_REFERENCE if inverse else None,
        )
        u["method"] = name
        u["protocol"] += (f" Metoda aplicată se alege după {objective} "
                          f"pe întregul portofoliu, în ultimele {len(origins)} zile de selecție.")
    if inverse:
        for urn in urns:
            urn["protocol"] += " Excluderea se recalculează din portofoliul direct disponibil înaintea fiecărei zile."
    return {"layout": layout_identity(layout), "objective": objective, "days": len(origins),
            "portfolio_refinement": recipe.portfolio_refinement,
            "first_date": dates.iloc[origins[0]].date().isoformat(), "draws": len(matrix) - origins[0],
            "pool_choice": list(incumbent), "ticket_choice": list(winner), "changed": winner != incumbent,
            "candidates": [
                {"main": m, "joker": b or None, **dict(c), "hit6": c["hit6"] if lot.pick_n == 6 else None}
                for (m, b), c in recipes.items()
            ]}


LIVE_BUDGET_REFERENCE = "paid-budget-v1"
DYNAMIC_PORTFOLIO_DAYS = 32
MIN_PORTFOLIO_DAYS = 12
_MIN_TRAINING_ROWS = 80
_HIT_KEYS = ("hit4", "hit5", "hit6")


def measure_paid_portfolios(layout, main, bonus, outcomes, day, objective, *, pairs=None, recipe=None):
    """Score the same observed draws for all playable portfolios on that date."""
    if pairs is None:
        pairs = [(m, b) for m in main for b in bonus]
    results = {}
    grades = {}
    shared = {}
    for pair in pairs:
        pool, jokers = main[pair[0]], bonus[pair[1]]
        if not pool or jokers is None:
            results[pair] = Counter(infeasible=len(outcomes))
            continue
        positions = tuple(pool), tuple(jokers)
        if positions not in shared:
            plan = (recipe.place(pool, jokers) if recipe is not None else
                    restrict_to_pool(deepcopy(layout), layout["objective"], pool, jokers))
            if plan["tickets"]:
                plan["forecast_pool"] = pool
                expanded = expand_plan(plan)
                shared[positions] = [grade_draw(expanded, numbers, ball, draw_date=day) for numbers, ball in outcomes]
            else:
                shared[positions] = None
        if shared[positions] is None:
            results[pair] = Counter(infeasible=len(outcomes))
        else:
            grades[pair] = shared[positions]

    if not grades:
        return results
    known_prizes = tuple(all(grade["any_prize"] is not None for grade in group) for group in zip(*grades.values(), strict=True))
    for pair, rows in grades.items():
        tally = Counter(n_eval=len(rows))
        for grade, prize_known in zip(rows, known_prizes, strict=True):
            for key in _HIT_KEYS:
                tally[key] += int(grade[key] is True)
            for key in ("pool_hits", "max_hits", "paid_variants", "distinct_variants", "duplicates"):
                tally[key] += grade[key]
            tally["cost_bani"] += grade["price_bani"]
            if prize_known:
                tally["any_prize"] += int(grade["any_prize"] is True)
            else:
                tally["prize_unknown"] += 1
            if grade["winning_variants"] is None:
                tally["winning_variants_unknown"] += 1
            else:
                tally["winning_variants"] += grade["winning_variants"]
            if objective == "hit4" or prize_known:
                tally["objective_evaluated"] += 1
            else:
                tally["objective_unknown"] += 1
        tally["objective_days"] = int(bool(tally["objective_evaluated"]))
        results[pair] = tally
    return results


@lru_cache(maxsize=384)
def _live_recipe_scores(semantics, methods, maximum, draw_n, payload, cutoffs):
    window = np.frombuffer(payload, dtype=np.int32).reshape(-1, draw_n)
    scores = {}
    for name in methods:
        try:
            values, _ = call_method(name, window, maximum, history_cutoffs=cutoffs)
            if len(values) == maximum and all(np.isfinite(value) for value in values.values()):
                scores[name] = values
        except (ValueError, ArithmeticError):
            continue
    return scores


def _score_before(matrix, cutoffs, origin, lookback, maximum, methods, semantics):
    used = origin if lookback in (0, 100) else max(1, int(origin * (lookback / 100)))
    start = origin - used
    relative = tuple(max(0, bound - start) for bound in cutoffs[start:origin])
    return _live_recipe_scores(semantics, methods, maximum, matrix.shape[1], matrix[start:origin].tobytes(), relative)


def _rank_candidates(scores, methods, eligible, size, rank):
    pools = {}
    for method in methods:
        try:
            values = scores[method]
            pools[method] = rank({n: values[n] for n in eligible if n in values}, size)
        except (KeyError, ValueError, ArithmeticError):
            pools[method] = None
    return pools


def _same_portfolio_sample(days, pair, baseline):
    # Known-observation identities were intersected across recipes within each
    # day by the grader. Equal per-date coverage now also rules out exchanging
    # infeasible dates while keeping the same overall denominator.
    for observation in days:
        candidate, control = observation.get(pair, {}), observation.get(baseline, {})
        if any(candidate.get(k, 0) != control.get(k, 0) for k in ("n_eval", "objective_evaluated", "cost_bani")):
            return False
    return True


class _PortfolioWindow:
    def __init__(self, pairs, horizon, baseline, objective):
        self.tallies = {pair: Counter() for pair in pairs}
        self.pending = deque()
        self.horizon = horizon
        self.baseline = baseline
        self.objective = objective
        self.random_pair = next(pair for pair in pairs if pair[0] == "random")

    def update(self, day):
        self.pending.append(day)
        for pair, tally in day.items():
            self.tallies[pair].update(tally)
        if len(self.pending) > self.horizon:
            expired = self.pending.popleft()
            for pair, tally in expired.items():
                self.tallies[pair].subtract(tally)

    def select(self):
        base = self.tallies[self.baseline]
        control = self.tallies[self.random_pair]
        if base["objective_days"] < MIN_PORTFOLIO_DAYS:
            return self.baseline, "insufficient_paid_history"
        if not _same_portfolio_sample(self.pending, self.random_pair, self.baseline):
            return self.baseline, "paid_control_unavailable"
        eligible = [
            pair for pair, stats in self.tallies.items() if pair != self.random_pair
            and all(stats[k] >= base[k] for k in _HIT_KEYS)
            and _same_portfolio_sample(self.pending, pair, self.baseline)
        ]
        best = max(eligible, key=lambda pair: self.tallies[pair][self.objective])
        if self.tallies[best][self.objective] <= control[self.objective]:
            return self.baseline, "no_paid_gain_over_random"
        return best, "paid_winner"

    def evidence(self, pair, reason):
        stats = self.tallies[pair]
        return {
            "source": "paid_budget", "reason": reason, "fallback": reason != "paid_winner",
            "portfolio_objective": self.objective, "validation_days": stats["objective_days"],
            "validation_draws": stats["objective_evaluated"], "unknown_draws": stats["objective_unknown"],
            "portfolio_hits": stats[self.objective],
            "random_portfolio_hits": self.tallies[self.random_pair][self.objective],
        }


def paid_snapshot_identities(recipe, scheme, matrix, dates, bonus, origins, *, inverse, dynamic,
                             lookback, joker_size, number_range, semantics=None):
    """Bind each decision to its policy and its strictly prior observations in one pass."""
    requested = sorted(set(origins))
    if not requested:
        return {}
    horizon = DYNAMIC_PORTFOLIO_DAYS if dynamic else PORTFOLIO_DAYS
    policy = ("paid-replay-prefix-v1", LIVE_BUDGET_REFERENCE, semantics or dynamic_formulas(),
              CANDIDATES, horizon, MIN_PORTFOLIO_DAYS, _MIN_TRAINING_ROWS, scheme.code,
              recipe.source_layout, recipe.identity, inverse, dynamic, float(lookback), joker_size, number_range)
    digest = sha256(json.dumps(policy, separators=(",", ":")).encode("ascii"))
    end = requested[-1]
    # A fixed-width little-endian row binds official ball order, day and optional
    # Joker. Zero represents the unavailable bonus as in paid portfolio grading.
    rows = np.zeros((end, matrix.shape[1] + 2), dtype="<i4")
    rows[:, 0] = dates.iloc[:end].to_numpy(dtype="datetime64[D]").astype(np.int32)
    rows[:, 1:-1] = matrix[:end]
    if bonus is not None:
        rows[:, -1] = bonus[:end]
    previous = 0
    fingerprints = {}
    for origin in requested:
        digest.update(rows[previous:origin])
        fingerprints[origin] = digest.hexdigest()
        previous = origin
    return fingerprints


def replay_budget_portfolios(layout, lot, scheme, matrix, frame, origins, *, inverse, dynamic,
                             lookback, joker_size, number_range, check=None, recipe=None):
    """Rank each requested origin on paid historical variants, before observing its date.

    The no-profile selector recalibrates across a bounded sample. Every inverse
    calibration date excludes the direct selector's actual causal forecast.
    """
    wanted = set(origins)
    if not wanted:
        return {}
    dates = history_dates(frame)
    if dates is None:
        return {origin: {"error": "Istoricul nu are date pentru comparația pe bilete."} for origin in wanted}
    matrix = np.ascontiguousarray(matrix, dtype=np.int32)
    bounds = training_cutoffs(frame)
    steps = [*dict.fromkeys(bounds), len(matrix)]
    if not wanted.issubset(steps) or 0 in wanted:
        raise ValueError("Originea trebuie să fie după cel puțin o zi completă.")
    end_position = steps.index(max(wanted))
    first_position = steps.index(min(wanted))
    horizon = DYNAMIC_PORTFOLIO_DAYS if dynamic else PORTFOLIO_DAYS
    start_position = max(1, first_position - horizon * (2 if inverse else 1))
    semantics = dynamic_formulas()
    main_methods = (*CANDIDATES, "random")
    bonus_methods = (*CANDIDATES, "random") if lot.geo.second else ("",)
    pairs = [(m, b) for m in CANDIDATES for b in bonus_methods if b != "random"]
    pairs.append(("random", "random" if lot.geo.second else ""))
    baseline = ("frequency", "frequency" if lot.geo.second else "")
    objective = "any_prize" if layout["objective"] == "any_prize" else "hit4"
    direct = _PortfolioWindow(pairs, horizon, baseline, objective)
    alternative = _PortfolioWindow(pairs, horizon, baseline, objective)
    lo, hi = number_range or (1, lot.max_n)
    base = set(range(lo, min(hi, lot.max_n) + 1))
    bonus_base = set()
    bonus_values = {}
    if lot.geo.second:
        maximum = lot.geo.second.max_n
        if type(joker_size) is not int or not 1 <= joker_size <= maximum:
            raise ValueError("Dimensiunea pool-ului Joker pentru Urna 2 este nevalidă.")
        bonus_base = set(range(1, maximum + 1))
        bonus_matrix, valid = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=maximum)
        bonus_positions = np.flatnonzero(valid)
        bonus_matrix = np.ascontiguousarray(bonus_matrix, dtype=np.int32)
        bonus_bounds = training_cutoffs(frame.loc[valid])
        bonus_values = dict(zip(bonus_positions, map(int, bonus_matrix[:, 0]), strict=True))

    slots = joker_size if lot.geo.second else 0
    if recipe is None or not recipe.matches(layout, scheme.n, slots):
        recipe = positional_recipe(layout, scheme.n, slots, solver=restrict_to_pool)
    aligned = None
    if lot.geo.second:
        aligned = np.zeros(len(matrix), dtype=np.int32)
        aligned[bonus_positions] = bonus_matrix[:, 0]
    identities = paid_snapshot_identities(
        recipe, scheme, matrix, dates, aligned, wanted, inverse=inverse, dynamic=dynamic,
        lookback=lookback, joker_size=joker_size, number_range=number_range, semantics=semantics,
    )
    results = {}
    for position in range(start_position, end_position + 1):
        if check is not None:
            check()
        origin = steps[position]
        main_scores = _score_before(matrix, bounds, origin, lookback, lot.max_n, main_methods, semantics)
        bonus_scores = {}
        if lot.geo.second:
            bonus_origin = int(np.searchsorted(bonus_positions, origin))
            if bonus_origin:
                bonus_scores = _score_before(bonus_matrix, bonus_bounds, bonus_origin, lookback, maximum, bonus_methods, semantics)

        def candidates(excluded=(), excluded_bonus=()):
            available = base.difference(excluded)
            main = _rank_candidates(main_scores, main_methods, available, scheme.n, rank_lr_pool)
            bonus = _rank_candidates(
                bonus_scores, bonus_methods, bonus_base.difference(excluded_bonus), joker_size, rank_by_score,
            ) if lot.geo.second else {"": ()}
            if lot.geo.second:
                bonus = {m: p if p and len(p) == joker_size else None for m, p in bonus.items()}
            return main, bonus

        day_main, day_bonus = candidates()
        direct_pair, direct_reason = direct.select()
        if not day_main[direct_pair[0]] or day_bonus[direct_pair[1]] is None:
            direct_pair, direct_reason = baseline, "unusable_paid_winner"
        normal, normal_bonus = day_main[direct_pair[0]], day_bonus[direct_pair[1]]
        if inverse and normal and normal_bonus is not None:
            test_main, test_bonus = candidates(normal, normal_bonus)
        else:
            test_main, test_bonus = day_main, day_bonus
        active = alternative if inverse else direct
        pair, reason = active.select() if inverse else (direct_pair, direct_reason)
        if not test_main[pair[0]] or test_bonus[pair[1]] is None:
            pair, reason = baseline, "unusable_paid_winner"
        pool, jokers = test_main[pair[0]], test_bonus[pair[1]]
        if origin in wanted:
            if not normal or normal_bonus is None or not pool or jokers is None:
                results[origin] = {"error": "Pool-ul eligibil sau istoricul unei urne nu permite portofoliul LR."}
            else:
                evidence = active.evidence(pair, reason)
                results[origin] = {
                    "pool": sorted(pool), "jokers": tuple(jokers), "excluded_pool": sorted(normal) if inverse else [],
                    "excluded_jokers": tuple(sorted(normal_bonus)) if inverse else (),
                    "eligible_count": len(base) - (len(normal) if inverse else 0),
                    "eligible_joker_count": len(bonus_base) - (len(normal_bonus) if inverse else 0),
                    "selection": {**evidence, "method": pair[0]},
                    "joker_selection": {**evidence, "method": pair[1]} if lot.geo.second else None,
                }
                if inverse:
                    direct_evidence = direct.evidence(direct_pair, direct_reason)
                    results[origin]["direct_selection"] = {**direct_evidence, "method": direct_pair[0]}
                    results[origin]["direct_joker_selection"] = (
                        {**direct_evidence, "method": direct_pair[1]} if lot.geo.second else None
                    )
            results[origin]["portfolio_refinement"] = recipe.portfolio_refinement
            results[origin]["paid_snapshot_sha256"] = identities[origin]
        if position == end_position or origin < _MIN_TRAINING_ROWS:
            continue
        current_day = dates.iloc[origin].date().isoformat()
        outcomes = [(matrix[i], bonus_values.get(i)) for i in range(origin, steps[position + 1])]
        direct.update(measure_paid_portfolios(layout, day_main, day_bonus, outcomes, current_day,
                                                 objective, pairs=pairs, recipe=recipe))
        if inverse:
            comparison = measure_paid_portfolios(
                layout, test_main, test_bonus, outcomes, current_day, objective, pairs=pairs, recipe=recipe,
            ) if normal and normal_bonus is not None else {}
            alternative.update(comparison)
    return results
