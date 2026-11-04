"""Descriptive old/new wheel comparison on reused archived draws.

Three examples were fixed from the geometry study before this backtest:
pool 20, guarantee 4, 6/49 cap 250, 5/40 cap 500, Joker Urna 1 cap 500.
No parameter, wheel or scorer is selected using the outcomes below. This is
not an external holdout, a significance test, or evidence of future winnings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from covering import large_pool as large
from loto_enterprise.core.ranking import rank_by_score
from scripts.analysis.hit_evidence_2026_11_04 import load_game

CASES = (
    ("loto_6_49", 49, 6, 6, 250),
    ("loto_5_40", 40, 6, 5, 500),
    ("joker", 45, 5, 5, 500),
)
POOL = 20
GUARANTEE = 4
WARMUP = 250


def canonical_pair(pick, cap):
    """Invoke uncached constructor so the local comparison cannot mix caches."""
    constructor = large._canonical_cover.__wrapped__
    with patch.object(large, "_refine_capped_cover", large._legacy_refine_capped_cover):
        before = constructor(POOL, pick, GUARANTEE, cap)
    after = constructor(POOL, pick, GUARANTEE, cap)
    for wheel in (before, after):
        assert len(wheel) == len(set(wheel)) == cap
        assert all(len(set(ticket)) == pick for ticket in wheel)
        assert set().union(*map(set, wheel)) == set(range(POOL))
    return before, after


def positional_masks(wheel):
    return tuple(sum(1 << position for position in ticket) for ticket in wheel)


def prior_only_rows(matrix, dates, universe, before, after):
    before_masks, after_masks = map(positional_masks, (before, after))
    counts = Counter({number: 0 for number in range(1, universe + 1)})
    rows = []
    start = 0
    while start < len(matrix):
        stop = start + 1
        while stop < len(matrix) and dates[stop] == dates[start]:
            stop += 1
        if start >= WARMUP:
            pool = rank_by_score(dict(counts), POOL)
            for index in range(start, stop):
                actual = set(map(int, matrix[index]))
                mask = sum(1 << i for i, number in enumerate(pool) if number in actual)
                old_hits = max((mask & ticket).bit_count() for ticket in before_masks)
                new_hits = max((mask & ticket).bit_count() for ticket in after_masks)
                # Independent set-intersection check of the first few steps.
                if len(rows) < 3:
                    for wheel, expected in ((before, old_hits), (after, new_hits)):
                        brute = max(
                            len({pool[i] for i in ticket} & actual) for ticket in wheel
                        )
                        assert brute == expected
                rows.append({
                    "date": str(dates[index])[:10],
                    "source_row_index": int(index),
                    "pool": pool,
                    "draw": list(map(int, matrix[index])),
                    "pool_hits": mask.bit_count(),
                    "before_best_ticket_hits": old_hits,
                    "after_best_ticket_hits": new_hits,
                })
        # Even a second draw on this date is unavailable until tomorrow.
        for index in range(start, stop):
            counts.update(map(int, matrix[index]))
        start = stop
    cut = int(len(rows) * 0.7)
    while 0 < cut < len(rows) and rows[cut]["date"] == rows[cut - 1]["date"]:
        cut += 1
    return rows[cut:], len(rows), cut


def analyze():
    started = time.perf_counter()
    result = []
    pairs = {}
    for game, universe, draw_n, pick, cap in CASES:
        if (pick, cap) not in pairs:
            pairs[pick, cap] = canonical_pair(pick, cap)
        before, after = pairs[pick, cap]
        matrix, dates, digest = load_game(ROOT, game, universe, draw_n)
        rows, eligible, earlier = prior_only_rows(
            matrix, dates, universe, before, after
        )
        thresholds = {}
        for target in (3, 4, 5, 6):
            old = np.array([r["before_best_ticket_hits"] >= target for r in rows])
            new = np.array([r["after_best_ticket_hits"] >= target for r in rows])
            thresholds[str(target)] = {
                "before_draws": int(old.sum()),
                "after_draws": int(new.sum()),
                "wins": int(np.sum(new & ~old)),
                "losses": int(np.sum(old & ~new)),
                "net_change_draws": int(new.sum() - old.sum()),
                "threshold_possible_on_ticket": target <= pick,
            }
        record = {
            "game": game,
            "universe": universe,
            "draw_n": draw_n,
            "pick": pick,
            "pool": POOL,
            "guarantee": GUARANTEE,
            "cap": cap,
            "old_new_distinct_ticket_count": [len(before), len(after)],
            "wheel_changed": before != after,
            "history_sha256": digest,
            "old_positions_sha256": hashlib.sha256(
                json.dumps(before).encode("utf-8")
            ).hexdigest(),
            "new_positions_sha256": hashlib.sha256(
                json.dumps(after).encode("utf-8")
            ).hexdigest(),
            "total_source_draws": len(matrix),
            "eligible_after_warmup": eligible,
            "earlier_draws_omitted": earlier,
            "evaluated_draws": len(rows),
            "first_date": rows[0]["date"],
            "last_date": rows[-1]["date"],
            "thresholds": thresholds,
            "rows": rows,
        }
        result.append(record)
        print(game, len(rows), thresholds, flush=True)
    return {
        "protocol": {
            "status": "descriptive_retrospective_reused_archive",
            "selection": "three fixed examples chosen before this backtest",
            "scorer": "cumulative frequency with canonical rank; prior dates only",
            "warmup_draws": WARMUP,
            "period": "recent 30% of eligible draws, split between dates",
            "same_day": "all target-date draws excluded from scoring",
            "pool_consecutive_limit": "off; unfiltered canonical top-20",
            "wheel_comparison": "legacy versus certified; identical pool and cost",
            "interpretation": (
                "Uniform-draw probability dominance does not require more hits "
                "on each finite historical sample. Counts can decrease. No "
                "historical result is used to accept or reject the new wheel."
            ),
            "prizes": "not modeled; Joker second urn is not included",
            "default_pools": "20 is experimental here; defaults remain 16/19/12",
            "statistical_tests": 0,
            "production_changes": 0,
        },
        "cases": result,
        "seconds": time.perf_counter() - started,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_suffix(".json"))
    args = parser.parse_args()
    report = analyze()
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved {args.output}; {report['seconds']:.2f} seconds")


if __name__ == "__main__":
    main()
