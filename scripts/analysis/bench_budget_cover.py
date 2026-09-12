"""Fixed-budget covering experiment; no production decisions or caches written.

Protocol fixed before evaluation: frequency scorer over the expanding prefix;
last 30% of valid chronological draws, three consecutive reporting windows;
all draws on the target date are excluded from scoring (unknown intra-day order);
pool 11, budgets 7/10, targets 3/4. Same pool and actual ticket count for every
method. Holdout is new to this experiment, NOT unseen by the wider project.
Joker measures urn 1 only; 5/40 uses n1..n5, matching the application.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import logging
import math
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import binomtest, hypergeom

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from budget_cover import wheel_maxcover
from loto_enterprise.benchmark.methods import score_frequency
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.ranking import rank_by_score
from ui_shared import atomic_write_json
from wheeling_methods import compute_coverage_pct, generate_wheel

GAMES = {
    "6/49": ("loto_6_49.csv", 49, 6),
    "5/40": ("loto_5_40.csv", 40, 5),
    "Joker urn 1": ("joker.csv", 45, 5),
}


def historical_steps(draws, universe, pool_size, start, dates=None):
    """Target draw is deliberately absent from the scoring call."""
    for index in range(start, len(draws)):
        prefix_end = index
        if dates is not None:
            while prefix_end > 0 and dates[prefix_end - 1] == dates[index]:
                prefix_end -= 1
        scores = score_frequency(draws[:prefix_end], universe)
        yield index, rank_by_score(scores, pool_size), scores


def ticket_hits(wheel, draw):
    winning = set(map(int, draw))
    hits = [len(set(ticket) & winning) for ticket in wheel]
    return max(hits, default=0), sum(h >= 3 for h in hits), sum(h >= 4 for h in hits)


def wilson(successes, n):
    return list(binomtest(int(successes), int(n)).proportion_ci(method="wilson"))


def paired_test(candidate, baseline):
    a, b = np.asarray(candidate, dtype=bool), np.asarray(baseline, dtype=bool)
    wins, losses = int(np.sum(a & ~b)), int(np.sum(b & ~a))
    return dict(
        wins=wins,
        losses=losses,
        p=binomtest(wins, wins + losses).pvalue if wins + losses else 1.0,
    )


def holm(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    result, previous = [1.0] * len(values), 0.0
    for rank, i in enumerate(order):
        previous = max(previous, min(1.0, values[i] * (len(values) - rank)))
        result[i] = previous
    return result


def exact_uniform_rate(wheel, v, pick, universe, target):
    """Enumerate pool intersections; all outside-pool completions counted exactly."""
    masks = [sum(1 << n for n in ticket) for ticket in wheel]
    favorable = 0
    for p in range(target, min(pick, v) + 1):
        outside = pick - p
        if outside > universe - v:
            continue
        covered = 0
        for subset in itertools.combinations(range(v), p):
            mask = sum(1 << n for n in subset)
            covered += any((mask & ticket).bit_count() >= target for ticket in masks)
        favorable += covered * math.comb(universe - v, outside)
    return favorable / math.comb(universe, pick)


def geometry_table():
    rows = []
    for v, pick, g, budget in itertools.product((11, 16), (5, 6), (3, 4), (7, 10)):
        pool = list(range(v))
        scores = {n: float((n * 17) % 23) for n in pool}
        for method in ("greedy", "lajolla", "maxcover"):
            start = time.perf_counter()
            wheel, coverage = (
                wheel_maxcover(pool, pick, g, budget, scores)
                if method == "maxcover"
                else generate_wheel(method, pool, pick, g, budget, scores)
            )
            elapsed = time.perf_counter() - start
            rows.append(
                dict(
                    pool=v,
                    pick=pick,
                    target=g,
                    budget=budget,
                    method=method,
                    universe=49 if pick == 6 else 45,
                    tickets=len(wheel),
                    coverage=coverage,
                    seconds=elapsed,
                    uniform_rate=exact_uniform_rate(
                        wheel, v, pick, 49 if pick == 6 else 45, g
                    ),
                )
            )
    return rows


def run(pool_size=11):
    result = {
        "protocol": __doc__,
        "pool_size": pool_size,
        "seed": 20260912,
        "sources": {},
        "geometry": geometry_table(),
        "rows": [],
        "paired": [],
    }
    for game, (filename, universe, pick) in GAMES.items():
        path = ROOT / "_ISTORIC" / filename
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="raise")
        df = df.sort_values("date", kind="stable").reset_index(drop=True)
        draws, valid = valid_draw_matrix(
            df, [f"n{i}" for i in range(1, pick + 1)], draw_n=pick, max_num=universe
        )
        dates = df.loc[valid, "date"].reset_index(drop=True)
        start = max(100, int(len(draws) * 0.70))
        if start >= len(draws):
            raise ValueError("Insufficient history")
        result["sources"][game] = dict(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            valid=len(draws),
            rejected=int((~valid).sum()),
            evaluated=len(draws) - start,
            same_day_rows=int(dates.duplicated(keep=False).sum()),
            first=str(dates.iloc[start].date()),
            last=str(dates.iloc[-1].date()),
        )
        steps = list(historical_steps(draws, universe, pool_size, start, dates))
        blocks = list(itertools.combinations(range(pool_size), pick))
        for g, budget in itertools.product((3, 4), (7, 10)):
            rng = np.random.default_rng(20260912)
            metrics = {
                name: [] for name in ("greedy", "lajolla", "random_tickets", "maxcover")
            }
            for index, pool, scores in steps:
                base, bc = generate_wheel("greedy", pool, pick, g, budget, scores)
                n = len(base)
                for name in metrics:
                    t0 = time.perf_counter()
                    if name == "greedy":
                        wheel, coverage = generate_wheel(
                            name, pool, pick, g, budget, scores
                        )
                    elif name == "random_tickets":
                        chosen = rng.choice(len(blocks), size=n, replace=False)
                        wheel = [[pool[i] for i in blocks[j]] for j in chosen]
                        coverage = compute_coverage_pct(wheel, pool, g)
                    elif name == "maxcover":
                        wheel, coverage = wheel_maxcover(pool, pick, g, n, scores)
                    else:
                        wheel, coverage = generate_wheel(name, pool, pick, g, n, scores)
                    duration = time.perf_counter() - t0
                    if len(wheel) != n:
                        raise AssertionError("Unequal actual budgets")
                    best, count3, count4 = ticket_hits(wheel, draws[index])
                    metrics[name].append(
                        (
                            best,
                            count3,
                            count4,
                            n,
                            coverage,
                            duration,
                            len(set(pool) & set(draws[index])),
                        )
                    )
            for name, values in metrics.items():
                a = np.asarray(values)
                row = dict(
                    game=game,
                    pool=pool_size,
                    target=g,
                    budget=budget,
                    method=name,
                    n=len(a),
                    mean_tickets=float(a[:, 3].mean()),
                    coverage=float(a[:, 4].mean()),
                    median_ms=float(np.median(a[:, 5]) * 1000),
                )
                for target in (3, 4):
                    success = a[:, 0] >= target
                    row[f"rate{target}"] = float(success.mean())
                    row[f"ci{target}"] = wilson(success.sum(), len(success))
                    row[f"windows{target}"] = [
                        float(x.mean()) for x in np.array_split(success, 3)
                    ]
                    row[f"winning_per_100_tickets_{target}"] = float(
                        100 * a[:, target - 2].sum() / a[:, 3].sum()
                    )
                    row[f"pool_rate{target}"] = float((a[:, 6] >= target).mean())
                    row[f"uniform_pool_rate{target}"] = float(
                        hypergeom.sf(target - 1, universe, pool_size, pick)
                    )
                    row[f"uniform_single_ticket_rate{target}"] = float(
                        hypergeom.sf(target - 1, universe, pick, pick)
                    )
                result["rows"].append(row)
            for target in (3, 4):
                candidate = np.asarray(metrics["maxcover"])[:, 0] >= target
                baseline = np.asarray(metrics["greedy"])[:, 0] >= target
                result["paired"].append(
                    dict(
                        game=game,
                        pool=pool_size,
                        target=g,
                        budget=budget,
                        outcome=target,
                        delta_pp=float(100 * (candidate.mean() - baseline.mean())),
                        **paired_test(candidate, baseline),
                    )
                )
            print(
                f"{game}: pool={pool_size} g={g} budget={budget} ({len(steps)} draws)",
                flush=True,
            )
    adjusted = holm([row["p"] for row in result["paired"]])
    for row, p in zip(result["paired"], adjusted):
        row["p_holm"] = p
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pool", default=11, type=int, choices=range(6, 17))
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    atomic_write_json(args.output, run(args.pool))
