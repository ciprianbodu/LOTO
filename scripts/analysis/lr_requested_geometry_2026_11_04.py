"""Independent exact audit of the requested official LR schemes.

Enumerate every subset of pools 16/15/19, take actual intersections with the
official tickets, and compare every profile entry with subset-DP. Calculate
uniform-draw odds as integer weighted counts and a separate combinatorial
upper bound. Equality proves optimality at that threshold; a gap is inconclusive.
Joker probabilities cover the main urn only. No official block is changed.

Run:
    python scripts/analysis/lr_requested_geometry_2026_11_04.py --output report.json
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
from math import comb
from pathlib import Path
import platform
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from covering.lr_schemes import lr_schemes, scheme_odds
from covering.probability import wheel_hit_profile

CASES = (
    ("6/49", 16, 59, 6, 6, 49),
    ("joker", 15, 12, 5, 5, 45),
    ("5/40", 19, 48, 5, 6, 40),
)


def run_audit():
    started = time.perf_counter()
    results = []
    for game, v, code, pick, draw, maximum in CASES:
        scheme = next(s for s in lr_schemes(game, v) if s.code == code)
        ticket_masks = np.array(
            [sum(1 << (n - 1) for n in row) for row in scheme.variants],
            dtype=np.uint32,
        )
        counts = np.zeros((v + 1, v + 1), dtype=np.int64)
        for start in range(0, 1 << v, 256):
            subsets = np.arange(start, min(start + 256, 1 << v), dtype=np.uint32)
            best = np.bitwise_count(subsets[:, None] & ticket_masks[None, :]).max(axis=1)
            hist = np.bincount(
                np.bitwise_count(subsets).astype(np.int32) * (v + 1) + best,
                minlength=(v + 1) ** 2,
            ).reshape(v + 1, v + 1)
            counts += hist
        counts = counts[:, ::-1].cumsum(axis=1)[:, ::-1]
        assert tuple(map(tuple, counts)) == wheel_hit_profile(
            range(1, v + 1), scheme.variants
        )
        expected = scheme_odds(scheme)
        odds, bounds, numerators, optimal = {}, {}, {}, []
        denominator = comb(maximum, draw)
        for target in range(3, min(draw, pick) + 1):
            numerator = sum(
                int(counts[h, target]) * comb(maximum - v, draw - h)
                for h in range(target, min(v, draw) + 1)
            )
            # There are at most D distinct target-subsets contained in tickets.
            # Each lies in C(v-target,h-target) h-subsets. This union bound
            # overcounts each original pick-ticket at least pick-1 times when
            # target=pick-1 and h=pick, which supplies the correction below.
            dmax = (
                len(ticket_masks)
                if target == pick
                else min(comb(v, target), len(ticket_masks) * comb(pick, target))
            )
            upper = 0
            for h in range(target, min(v, draw) + 1):
                covered = min(comb(v, h), dmax * comb(v - target, h - target))
                if h == pick and target == pick - 1:
                    covered = min(
                        covered, (v - target) * dmax - (pick - 1) * len(ticket_masks)
                    )
                upper += covered * comb(maximum - v, draw - h)
            assert numerator <= upper
            odds[target] = float(Fraction(numerator, denominator))
            bounds[target] = float(Fraction(upper, denominator))
            numerators[target] = {"favorable": numerator, "upper_bound": upper}
            assert odds[target] == expected[target]
            if numerator == upper:
                optimal.append(target)
        block_bytes = "\n".join(" ".join(map(str, row)) for row in scheme.variants).encode()
        results.append({
            "game": game,
            "pool": v,
            "code": code,
            "tickets": len(ticket_masks),
            "canonical_blocks_sha256": hashlib.sha256(block_bytes).hexdigest(),
            "all_subsets_enumerated": 1 << v,
            "draw_n": draw,
            "pick_n": pick,
            "universe": maximum,
            "denominator": denominator,
            "numerators": numerators,
            "odds": odds,
            "upper_bounds": bounds,
            "proven_optimal_thresholds_by_bound": optimal,
            "all_profile_counts_match": True,
            "profile": counts.tolist(),
        })
        print(f"{game} Cod {code}: {odds}; optimal by bound: {optimal}", flush=True)
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scope": (
            "direct enumeration of every pool subset; exact rational odds and "
            "combinatorial upper bounds; Joker main urn only"
        ),
        "subsets_enumerated": sum(r["all_subsets_enumerated"] for r in results),
        "seconds": time.perf_counter() - started,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {report['subsets_enumerated']} subsets in {report['seconds']:.3f}s")


if __name__ == "__main__":
    main()
