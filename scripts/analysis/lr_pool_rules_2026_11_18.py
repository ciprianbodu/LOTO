"""Compare two implementations of the consecutive limit and the unrestricted pool on future dates.

Fixed scorer: the registry's frequency, with all prior dates and its usual exponential weights.
Four policies at one ticket budget: the production rank-first cap of two, the best score sum under
the same cap, unrestricted top K, and a rule chosen once before the final 30% of eligible dates.
The selection objective is ticket 5+, then 4+, then total best ticket hits. Ties retain the
production rule first, then the capped score-sum rule. Historical and exploratory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark.methods import score_frequency
from loto_enterprise.core.lotteries import GAMES, lottery_by_bench_key
from loto_enterprise.core.pool_selection import apply_consecutive_limit
from loto_enterprise.core.ranking import rank_by_score
from scripts.analysis.nested_wfv_2026_11_18 import MIN_VALIDATION_DAYS, read_draws, scheme_definition

RULES = ("rank_cap2", "sum_cap2", "unrestricted")


def max_sum_pool(scores, ranked, k):
    """Maximize the exact sum of stored scores for K numbers with at most two consecutive.

    Integer scores have a shared power-of-two denominator. A bit per rank breaks ties
    lexicographically in production rank order, including when all scores are equal.
    """
    ratios = {n: float(value).as_integer_ratio() for n, value in scores.items()}
    denominator = max(d for _, d in ratios.values())
    preference = {n: 1 << i for i, n in enumerate(reversed(ranked))}
    states = {(0, 0): (0, 0)}
    for n in range(1, max(scores) + 1):
        following = {}
        if n in scores:
            numerator, divisor = ratios[n]
            weight = numerator * (denominator // divisor)
        for (size, run), best in states.items():
            # Skipping n ends the consecutive run. Keep the best path to each state.
            skipped = (size, 0)
            if skipped not in following or best > following[skipped]:
                following[skipped] = best
            if n in scores and size < k and run < 2:
                taken = (size + 1, run + 1)
                candidate = (best[0] + weight, best[1] | preference[n])
                if taken not in following or candidate > following[taken]:
                    following[taken] = candidate
        states = following
    mask = max(best for (size, _), best in states.items() if size == k)[1]
    return tuple(sorted(n for n in ranked if mask & preference[n]))


def rule_pools(scores, k):
    ranked = rank_by_score(scores, len(scores))
    raw = tuple(sorted(ranked[:k]))
    capped = tuple(sorted(apply_consecutive_limit(ranked, k, 2)))
    optimum = capped if capped == raw else max_sum_pool(scores, ranked, k)
    return capped, optimum, raw


def draw_hits(pool, drawn, lookup):
    mask = sum(1 << index for index, number in enumerate(pool) if number in drawn)
    return mask.bit_count(), lookup(mask)


def choose_rule(previous):
    """Rows are actual draws and columns follow RULES. Ties keep the earlier rule."""
    return max(range(len(RULES)), key=lambda r: (
        int((previous[:, r] >= 5).sum()), int((previous[:, r] >= 4).sum()), int(previous[:, r].sum()),
    ))


def compare_pool_rules(key, *, scheme_code=None):
    lot = lottery_by_bench_key(key)
    second = key == lot.bench_key_urna2
    k, scheme, lookup = scheme_definition(lot, second, scheme_code)
    if second:
        return {"key": key, "k": 1, "equivalent": True}
    matrix, dates, _, days, universe, digest = read_draws(lot, False)
    split = MIN_VALIDATION_DAYS + (len(days) - MIN_VALIDATION_DAYS) * 7 // 10
    origin = int(days[split])
    first = int(days[0])
    shape = (len(matrix) - first, len(RULES))
    pool_hits = np.empty(shape, dtype=np.uint8)
    tickets = np.empty_like(pool_hits)
    differs = np.zeros(len(RULES), dtype=np.int64)
    for start, end in zip(days, (*days[1:], len(matrix)), strict=True):
        pools = rule_pools(score_frequency(matrix[:start], universe), k)
        if start >= origin:
            differs += [(p != pools[0]) * (end - start) for p in pools]
        for i in range(start, end):
            draw = set(map(int, matrix[i]))
            observed = {p: draw_hits(p, draw, lookup) for p in set(pools)}
            for j, pool in enumerate(pools):
                pool_hits[i-first, j], tickets[i-first, j] = observed[pool]

    cut = origin - first
    selected = choose_rule(tickets[:cut])
    baseline = tickets[cut:, 0]
    rows = {}
    for j, rule in enumerate(RULES):
        actual = tickets[cut:, j]
        rows[rule] = {"changed_pools": int(differs[j])}
        for threshold in (4, 5):
            event = actual >= threshold
            reference = baseline >= threshold
            rows[rule].update({
                f"pool_{threshold}plus": int((pool_hits[cut:, j] >= threshold).sum()),
                f"ticket_{threshold}plus": int(event.sum()),
                f"gains_{threshold}plus": int((event & ~reference).sum()),
                f"losses_{threshold}plus": int((reference & ~event).sum()),
            })
    return {"key": key, "k": k, "scheme": scheme, "sha256": digest, "selected": RULES[selected],
            "start": str(dates[origin]), "end": str(dates[-1]), "n": len(baseline),
            "n_dates": len(days) - split, "rules": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = [compare_pool_rules(key) for lot in GAMES for key in lot.bench_keys]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
