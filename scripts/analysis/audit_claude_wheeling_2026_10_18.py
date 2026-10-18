"""Read-only exact audit of Claude's experimental wheel generators.

Run: python scripts/analysis/audit_claude_wheeling_2026_10_18.py --output PATH
Only the explicitly requested JSON report is written. Scorer values below
are synthetic, so this measures ticket geometry and generation time, never
predictive efficacy. Cold timings include first-use search; warm medians
reflect process-local caches and are not end-to-end application timings.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
import logging
from math import comb
from pathlib import Path
import platform
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from covering.anneal_hits import _certify
import covering.budget_designs as bd
from covering.common import _coverage_pct, _sorted_pool
from covering.dispatch import generate_wheel, resolve_wheel_method
from covering.multihit import multi_hit_stats, wheel_multihit
from covering.probability import wheel_hit_probabilities, wheel_hit_profile
from covering.weighted import wheel_weighted

GAMES = (("6/49", 6, 6, 49), ("5/40", 5, 6, 40), ("Joker Urna 1", 5, 5, 45))


def measured(call, repeats):
    started = time.perf_counter()
    result = call()
    cold = (time.perf_counter() - started) * 1000
    times = []
    for _ in range(repeats):
        started = time.perf_counter()
        assert call() == result, "Generator must be deterministic"
        times.append((time.perf_counter() - started) * 1000)
    return result, round(cold, 3), round(statistics.median(times), 3)


def metric(pool, wheel, coverage, g, draw, universe):
    assert coverage == _coverage_pct(wheel, pool, g)
    assert len(wheel) == len({tuple(sorted(t)) for t in wheel})
    return dict(tickets=len(wheel), coverage_pct=coverage,
                uniform_ticket_probability=wheel_hit_probabilities(pool, wheel, draw, universe)["ticket"],
                target_multiplicity=multi_hit_stats(wheel, pool, g))


def comparison(repeats):
    rows = []
    for game, pick, draw, universe in GAMES:
        for budget in (10, 30, 65, 0):
            pool = list(range(1, 17))
            scores = {n: float(17 - n) for n in pool}
            method = resolve_wheel_method(budget, requested="")
            (baseline, coverage), cold, warm = measured(
                lambda: generate_wheel(method, pool, pick, 4, budget, scores, draw_n=draw, max_num=universe), repeats)
            base = metric(pool, baseline, coverage, 4, draw, universe)
            base.update(cold_ms=cold, warm_median_ms=warm)
            candidates = {}
            for name, fn in (("weighted", wheel_weighted), ("multihit", wheel_multihit)):
                (wheel, cov), cold, warm = measured(lambda: fn(pool, pick, 4, budget, scores), repeats)
                stats = metric(pool, wheel, cov, 4, draw, universe)
                stats.update(cold_ms=cold, warm_median_ms=warm)
                stats["delta_probability_points"] = {t: 100 * (stats["uniform_ticket_probability"][t] - base["uniform_ticket_probability"][t]) for t in range(3, min(pick, draw) + 1)}
                if len(wheel) != len(baseline):
                    status = "different ticket count; no equal-cost claim"
                else:
                    reason, _, _ = _certify(pool, baseline, wheel, 4, draw, universe)
                    status = "strict exact dominance certified" if not reason else reason
                stats["equal_cost_certificate"] = status
                candidates[name] = stats
            rows.append(dict(game=game, pool=16, pick=pick, draw_n=draw, max_num=universe, guarantee=4, budget=budget, automatic_method=method, automatic=base, candidates=candidates))
            print(f"{game}: pool 16, g4, budget {budget}: complete", flush=True)
    return rows


def budget_design_audit():
    path = ROOT / "covering_designs/budget/H_11_5_4.txt"
    blocks = bd.load_budget_design(11, 5, 4, 65)
    assert blocks is not None and len(blocks) == 65
    assert len({tuple(t) for t in blocks}) == 65
    assert all(len(t) == len(set(t)) == 5 and set(t) <= set(range(1, 12)) for t in blocks)
    counts = Counter(sub for t in blocks for sub in itertools.combinations(sorted(t), 4))
    # Independent packing certificate: 65 blocks x C(5,4)=325 unique targets.
    assert len(counts) == 65 * comb(5, 4) == 325
    assert set(counts.values()) == {1}
    profile = wheel_hit_profile(list(range(1, 12)), blocks)
    assert profile[3][3] == comb(11, 3)
    assert profile[4][4] == 325
    assert profile[5][4] == comb(11, 5) - 1
    assert profile[6][4] == comb(11, 6)
    assert profile[5][5] == 65
    assert profile[6][5] == 65 * comb(6, 1)
    results = []
    for game, pick, draw, universe in GAMES[1:]:
        pool = list(range(1, 12))
        for label in ("ranked", "flat", "mixed"):
            scores = ({n: float(12 - n) for n in pool} if label == "ranked" else
                      {n: 1. for n in pool} if label == "flat" else
                      {n: float((n * 17) % 23) for n in pool})
            with patch.object(bd, "_BUDGET_DIRS", []):
                baseline, cov = generate_wheel("hitcover", pool, 5, 4, 65, scores, draw_n=draw, max_num=universe)
            ordered = _sorted_pool(pool, scores)
            candidate = [sorted(ordered[n - 1] for n in b) for b in blocks]
            reason, gain, _ = _certify(pool, baseline, candidate, 4, draw, universe)
            actual, actual_cov = generate_wheel("hitcover", pool, 5, 4, 65, scores, draw_n=draw, max_num=universe)
            assert actual_cov >= cov
            assert all(wheel_hit_probabilities(pool, actual, draw, universe)["ticket"][t] >= wheel_hit_probabilities(pool, baseline, draw, universe)["ticket"][t] - 1e-15 for t in range(3, 6))
            results.append(dict(game=game, scores=label, certificate=reason or "strict exact dominance certified", gain=gain,
                                before=metric(pool, baseline, cov, 4, draw, universe),
                                after=metric(pool, actual, actual_cov, 4, draw, universe)))
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                tickets=65, covered_4_subsets=325, total_4_subsets=330, all_four_subsets_unique=True,
                threshold_3_globally_optimal=True, threshold_5_globally_optimal=True,
                primary_target_coverage_globally_optimal=True,
                threshold_4_global_optimality_not_proved=True,
                proof="3+ attains pool bound; primary 4-subset coverage attains the 325-subset counting bound; 4+ covers 461/462 five-subsets and every six-subset; 5+ attains union bound because ticket pairs share at most 3 numbers.",
                comparisons=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    logging.disable(logging.CRITICAL)
    report = dict(date="2026-10-18", python=platform.python_version(), platform=platform.platform(),
                  scores="synthetic descending ranks, not calibrated probabilities", repeats=args.repeats,
                  comparisons=comparison(args.repeats), budget_design=None)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["budget_design"] = budget_design_audit()
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
