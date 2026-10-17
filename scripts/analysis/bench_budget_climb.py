"""Șansele exacte pe bilete cu buget peste 64, înainte și după `budget_climb`.

Pentru fiecare joc, pool și buget: wheel-ul automat fără căutare (`max_num`
omis, ca înainte de 2026-10-08) și cu căutare. Probabilitățile sunt exacte
pentru o extragere uniformă (`wheel_hit_probabilities`), pe pool-ul 1..v fără
scoruri. Bugetele cel puțin cât designul complet sunt sărite: acolo biletele
sunt designul, aceleași cu și fără căutare.

    python scripts/analysis/bench_budget_climb.py [--pools 12,14,16]

Nu scrie stare de aplicație. Fără afirmație predictivă: probabilitatea de hit
a pool-ului nu se schimbă.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from covering.designs import complete_design_size  # noqa: E402
from covering.probability import wheel_hit_probabilities  # noqa: E402
from wheeling_methods import generate_wheel  # noqa: E402

GAMES = {"6/49": (6, 6, 49), "5/40": (5, 6, 40), "joker": (5, 5, 45)}
BUDGETS = (65, 80, 100, 130, 160, 200, 300, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pools", default="12,14,16")
    parser.add_argument("--guarantee", type=int, default=4)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    g = args.guarantee
    print("| joc | pool | buget | 3+ înainte | 3+ după | 4+ înainte | 4+ după | relativ 4+ | 5+ înainte | 5+ după | s |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for game, (pick, draw, max_num) in GAMES.items():
        for v in (int(x) for x in args.pools.split(",")):
            pool = list(range(1, v + 1))
            size = complete_design_size(v, pick, g)
            for budget in BUDGETS:
                if size is not None and budget >= size:
                    continue
                before, _ = generate_wheel("hitcover", pool, pick, g, budget, None, None, draw)
                start = time.perf_counter()
                after, _ = generate_wheel(
                    "hitcover", pool, pick, g, budget, None, None, draw, max_num
                )
                seconds = time.perf_counter() - start
                pb = wheel_hit_probabilities(pool, before, draw, max_num)["ticket"]
                pa = wheel_hit_probabilities(pool, after, draw, max_num)["ticket"]
                rel = (pa[4] / pb[4] - 1) * 100 if pb[4] else 0.0
                print(
                    f"| {game} | {v} | {budget} | {pb[3]:.4%} | {pa[3]:.4%} | {pb[4]:.4%} | "
                    f"{pa[4]:.4%} | {rel:+.1f}% | {pb.get(5, 0):.5%} | {pa.get(5, 0):.5%} | {seconds:.1f} |",
                    flush=True,
                )


if __name__ == "__main__":
    main()
