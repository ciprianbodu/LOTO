"""Completeness of the actual paid LR recipes used by one calculation."""

from functools import lru_cache
from hashlib import sha256
import json


@lru_cache(maxsize=192)
def _recipe_digest(game, width, objective, budget, price, joker_slots, capacity, zones):
    # Positions and bonus slots survive relabelling. Zone order and capacity
    # fix the physical tickets, including every paid duplicate.
    geometry = (game, width, objective, budget, price, joker_slots, capacity, zones)
    return sha256(json.dumps(geometry, separators=(",", ":")).encode("ascii")).hexdigest()


def search_issues(report):
    search = report.get("search", {})
    issues = [search["fallback"]] if search.get("fallback") else []
    if search.get("stopped") == "cancelled":
        issues.append("cancelled")
    return issues


def recipe_status(plan, width, price, joker_slots, capacity, zones, report):
    issues = search_issues(report)
    return {
        "complete": not issues, "issues": issues,
        "recipe_sha256": [_recipe_digest(plan["game"], width, plan["objective"], plan["budget_bani"],
                                         price, joker_slots, capacity, zones)],
    }


def refinement_complete(record):
    return ((record or {}).get("portfolio_refinement") or {}).get("complete", True)


def merge_refinements(*records):
    complete = True
    issues, recipes = set(), set()
    for record in records:
        evidence = (record or {}).get("portfolio_refinement") or {}
        complete &= evidence.get("complete", True)
        issues.update(evidence.get("issues", ()))
        recipes.update(evidence.get("recipe_sha256", ()))
    return {"complete": complete, "issues": sorted(issues), "recipe_sha256": sorted(recipes)}
