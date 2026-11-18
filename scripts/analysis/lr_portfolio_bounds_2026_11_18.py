"""Compute exact 4+/5+ bounds for the 33 complete Romanian LR codes at standard physical prices.

Run python -m scripts.analysis.lr_portfolio_bounds_2026_11_18 --output scratch/lr_portfolios.json
from the repository. Option --replay-649 evaluates the two reported equal-cost disjoint examples.
"""
import argparse
import json
from pathlib import Path

from covering.lr_portfolio import compare_portfolios
from covering.lr_schemes import lr_schemes, scheme_tickets
from covering.lr_selection import resolve_lr_scheme


def replay_649():
    from loto_enterprise.benchmark.methods import score_frequency
    from loto_enterprise.core.lotteries import require_lottery
    from loto_enterprise.core.pool_selection import select_pool_from_scores
    from scripts.analysis.nested_wfv_2026_11_18 import MIN_VALIDATION_DAYS, read_draws

    draws, dates, _, days, universe, digest = read_draws(require_lottery("6/49"), False)
    split = MIN_VALIDATION_DAYS + (len(days) - MIN_VALIDATION_DAYS) * 7 // 10
    starts = [*days[split:], len(draws)]
    groups = [(50,), (49, 49), (56,), (57, 57, 57)]
    counters = {codes: {t: 0 for t in range(3, 7)} for codes in groups}
    schemes = {code: resolve_lr_scheme("6/49", code) for codes in groups for code in codes}
    for start, end in zip(starts[:-1], starts[1:], strict=True):
        scores = score_frequency(draws[:start], universe)
        for codes, counts in counters.items():
            used = set()
            tickets = []
            for code in codes:
                scheme = schemes[code]
                pool = select_pool_from_scores(scores, scheme.n, used, max_num=universe, max_consecutive_run=2)
                if len(pool) != scheme.n or used.intersection(pool):
                    raise ValueError("Invalid disjoint pool")
                used.update(pool)
                tickets.extend(map(set, scheme_tickets(scheme, pool)))
            for draw in draws[start:end]:
                winning = set(draw)
                best = max(len(winning & ticket) for ticket in tickets)
                for t in counts:
                    counts[t] += best >= t
    return {"scorer": "frequency", "cap": 2, "n": len(draws) - int(starts[0]), "days": len(starts) - 1,
            "start": str(dates[starts[0]]), "end": str(dates[-1]), "sha256": digest,
            "results": {str(codes): values for codes, values in counters.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replay-649", action="store_true")
    args = parser.parse_args()
    if args.replay_649:
        result = replay_649()
    else:
        result = []
        for game in ("6/49", "5/40", "joker"):
            per_game = [row for scheme in lr_schemes(game) for row in compare_portfolios(game, scheme.code)]
            result.extend(sorted(per_game, key=lambda r: (r["threshold"], r["base"])))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
