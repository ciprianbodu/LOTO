"""Compare eligible Re-Bench recipes using the actual paid zones of the selected budget."""

from collections import Counter
from copy import deepcopy

import numpy as np

from covering.lr_pool_budget import restrict_to_pool
from covering.lr_pool_ranking import rank_lr_pool
from loto_enterprise.benchmark.methods import EVALUATION_METHODS
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import training_cutoffs
from loto_enterprise.core.lr_budget_forecast import _profile_scores
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
                            inverse, dynamic, lookback, joker_size, check):
    from loto_enterprise.benchmark.lr_hypotheses import _blocked_by_date, _training

    if dates is None or any(not u.get("decision") or not u.get("rows") for u in urns):
        return {"error": "Istoricul nu permite compararea portofoliilor pe zile complete."}
    bounds = game.history_cutoffs
    origins = tuple(dict.fromkeys(b for b in bounds if b >= 80))[-PORTFOLIO_DAYS:]
    if not origins:
        return {"error": "Nu există o zi de test după prefixul minim de 80 de extrageri."}
    base = tuple(range(10, min(40, lot.max_n) + 1) if restricted else range(1, lot.max_n + 1))
    main_block, _, refs = _blocked_by_date(matrix, bounds, game, scheme, origins, base,
                                          scheme.n if inverse else 0, dynamic, lookback,
                                          urns[0]["target"], check, calendar=True)
    main_recipes = _recipes(urns[0])
    bonus_recipes = {"": []}
    bonus_refs = []
    if lot.geo.second:
        bonus_matrix, valid = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=lot.geo.second.max_n)
        bonus_positions = np.flatnonzero(valid)
        bonus_bounds = training_cutoffs(frame.loc[valid])
        bonus_recipes = _recipes(urns[1])
        bonus_refs = urns[1]["decision"].get("reference_members", ())
        if inverse and not bonus_refs:
            return {"error": "Lipsește metoda de referință pentru urna a doua."}

    objective = "any_prize" if layout["objective"] == "any_prize" else "hit4"
    recipes = {}
    for idx, origin in enumerate(origins):
        check()
        end = origins[idx + 1] if idx + 1 < len(origins) else len(matrix)
        past, cuts = _training(matrix, bounds, origin, lookback)
        main = {}
        for name, members in main_recipes.items():
            try:
                scores, _ = _profile_scores(({"members": members}, "members"), past, cuts,
                                           lot.max_n, main_block[origin], scheme.n)
                main[name] = sorted(rank_lr_pool(scores, scheme.n))
            except (ValueError, ArithmeticError):
                main[name] = None
        bonus = {"": ()}
        if lot.geo.second:
            offset = int(np.searchsorted(bonus_positions, origin))
            bonus = dict.fromkeys(bonus_recipes)
            if offset:
                bp, bc = _training(bonus_matrix, bonus_bounds, offset, lookback)
                excluded = ()
                if inverse:
                    ref_scores, _ = _profile_scores(({"members": bonus_refs}, "members"), bp, bc,
                                                    lot.geo.second.max_n, (), joker_size)
                    excluded = rank_by_score(ref_scores, joker_size)
                for name, members in bonus_recipes.items():
                    try:
                        scores, _ = _profile_scores(({"members": members}, "members"), bp, bc,
                                                   lot.geo.second.max_n, excluded, joker_size)
                        bonus[name] = rank_by_score(scores, joker_size)
                    except (ValueError, ArithmeticError):
                        continue
        for m, pool in main.items():
            for b, jokers in bonus.items():
                key = m, b
                counter = recipes.setdefault(key, Counter())
                if not pool or jokers is None:
                    counter["infeasible"] += end - origin
                    continue
                plan = restrict_to_pool(deepcopy(layout), layout["objective"], pool, jokers)
                plan["forecast_pool"] = pool
                if not plan["tickets"]:
                    counter["infeasible"] += end - origin
                    continue
                expanded = expand_plan(plan)
                for i in range(origin, end):
                    day = dates.iloc[i].date().isoformat()
                    bonus_idx = int(np.searchsorted(bonus_positions, i)) if lot.geo.second else 0
                    if lot.geo.second and (bonus_idx >= len(bonus_positions) or bonus_positions[bonus_idx] != i):
                        counter["infeasible"] += 1
                        continue
                    drawn_joker = int(bonus_matrix[bonus_idx, 0]) if lot.geo.second else None
                    grade = grade_draw(expanded, matrix[i], drawn_joker, draw_date=day)
                    counter.update({k: int(grade[k] or 0) for k in (
                        "any_prize", "hit4", "hit5", "hit6", "pool_hits", "max_hits", "winning_variants")})
                    counter["paid_variants"] += grade["paid_variants"]
                    counter["distinct_variants"] += grade["distinct_variants"]
                    counter["duplicates"] += grade["duplicates"]
                    counter["cost_bani"] += grade["price_bani"]
                    counter["n_eval"] += 1

    incumbent = next(iter(main_recipes)), next(iter(bonus_recipes))
    def eligible(pair):
        return (all(name not in (*EVALUATION_METHODS, "random")
                    for name in (*[m["method"] for m in main_recipes[pair[0]]],
                                 *[m["method"] for m in bonus_recipes[pair[1]]]))
                and recipes[pair]["n_eval"] > 0
                and recipes[pair]["n_eval"] >= recipes[incumbent]["n_eval"]
                and all(recipes[pair][k] >= recipes[incumbent][k] for k in ("hit4","hit5","hit6")))
    # Ties retain the original policy and terminal price. Failures remain in the denominator.
    winner = max((key for key in recipes if eligible(key)),
                 key=lambda k: (recipes[k][objective], k == incumbent), default=incumbent)
    if not recipes[winner]["n_eval"]:
        return {"error": "Niciun candidat nu a produs bilete evaluabile.", "n_eval": 0}
    for u, name, choices, reference in zip(urns, winner, (main_recipes, bonus_recipes), (refs, bonus_refs)):
        u["decision"].update(members=choices[name], portfolio_objective=objective,
                             evaluated=recipes[winner]["n_eval"], reference_members=list(reference))
        u["method"] = name
        u["protocol"] += (f" Metoda aplicată se alege după {objective} "
                          f"pe întregul portofoliu, în ultimele {len(origins)} zile de selecție.")
    return {"layout": layout_identity(layout), "objective": objective, "days": len(origins),
            "first_date": dates.iloc[origins[0]].date().isoformat(), "draws": len(matrix) - origins[0],
            "pool_choice": list(incumbent), "ticket_choice": list(winner), "changed": winner != incumbent,
            "candidates": [
                {"main": m, "joker": b or None, **dict(c), "hit6": c["hit6"] if lot.pick_n == 6 else None}
                for (m, b), c in recipes.items()
            ]}
