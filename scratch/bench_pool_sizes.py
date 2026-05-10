"""Benchmark reproductibil pe pool sizes.

Rulează walk-forward backtest pe input.csv pentru mai multe pool sizes
(focus pe pool < 15 = sub Ultra-Hit) și captează metrici comparabile.

Uzaj:
    python scratch/bench_pool_sizes.py --pools 9,10,11,12,15 --depth 3 --tag baseline
    python scratch/bench_pool_sizes.py --pools 9,10,11,12,15 --depth 3 --tag exp1

Output: scratch/bench_<tag>.json — pentru comparare A/B.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Worktree path setup
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from loto_enterprise.core.backtesting import LotoBacktester


def run_one_pool(csv_path: str, game_type: str, pool_size: int, guarantee: int,
                 depth_pct: float, lookback_pct: float, sim_step: int,
                 smart_reduction: bool = True,
                 filter_consecutives: bool = True) -> dict:
    """Rulează backtest pentru un singur pool size și întoarce metrici agregate."""
    bt = LotoBacktester(csv_path, game_type=game_type)

    t0 = time.time()
    preds = bt.run_retroactive_backtest(
        pool_size=pool_size,
        guarantee=guarantee,
        lookback_percent=lookback_pct,
        backtest_depth_percent=depth_pct,
        filter_consecutives=filter_consecutives,
        max_variants=0,
        simulation_step=sim_step,
        use_feedback=True,
        enable_hard_inversion=True,
        smart_reduction=smart_reduction,
    )
    elapsed = time.time() - t0

    if not preds:
        return {
            "pool_size": pool_size,
            "guarantee": guarantee,
            "n_simulations": 0,
            "elapsed_s": elapsed,
            "error": "no predictions",
        }

    # Per-iteration metrics
    max_hits = [int(p.hits) for p in preds]            # cel mai bun bilet la fiecare extragere
    union_hits = [int(p.hits_union) for p in preds]    # nucleul (pool) ∩ extragere

    # Distribuții (cu draw_n adaptat per joc)
    draw_n = bt.params["draw_n"]
    max_dist = {h: int(sum(1 for x in max_hits if x == h)) for h in range(draw_n + 1)}
    union_dist = {h: int(sum(1 for x in union_hits if x == h)) for h in range(pool_size + 1)}

    # Variant counts (#bilete generate per simulare)
    variant_counts = [len(p.variants) for p in preds]

    # Per-variant hit distribution: pentru fiecare variantă din wheel, numără hits.
    # Asta e direct corect indiferent de tipul wheel-ului (set-cover sau full).
    # Tracăm câte variante per simulare ating fiecare nivel de hits.
    import math
    # Per draw, pentru fiecare variantă, count |variant ∩ actual_draw|
    variants_at_hits_per_sim = []  # list of dict {hits: count} per sim
    total_4plus_variants = 0
    total_5plus_variants = 0
    total_6plus_variants = 0
    sims_with_4plus_variant = 0
    sims_with_5plus_variant = 0
    sims_with_6plus_variant = 0
    for p in preds:
        actual = set(p.actual_numbers)
        hits_per_var = [len(set(v) & actual) for v in p.variants]
        cnt_4p = sum(1 for h in hits_per_var if h >= 4)
        cnt_5p = sum(1 for h in hits_per_var if h >= 5)
        cnt_6p = sum(1 for h in hits_per_var if h >= 6)
        total_4plus_variants += cnt_4p
        total_5plus_variants += cnt_5p
        total_6plus_variants += cnt_6p
        if cnt_4p > 0:
            sims_with_4plus_variant += 1
        if cnt_5p > 0:
            sims_with_5plus_variant += 1
        if cnt_6p > 0:
            sims_with_6plus_variant += 1
        # dist per sim
        d = {h: int(sum(1 for x in hits_per_var if x == h)) for h in range(draw_n + 1)}
        variants_at_hits_per_sim.append(d)

    n = len(preds)
    return {
        "pool_size": pool_size,
        "guarantee": guarantee,
        "n_simulations": n,
        "elapsed_s": round(elapsed, 1),
        "avg_max_hits": float(np.mean(max_hits)),
        "max_max_hits": int(np.max(max_hits)),
        "median_max_hits": float(np.median(max_hits)),
        "std_max_hits": float(np.std(max_hits)),
        "avg_union_hits": float(np.mean(union_hits)),
        "max_union_hits": int(np.max(union_hits)),
        "median_union_hits": float(np.median(union_hits)),
        "avg_variant_count": float(np.mean(variant_counts)),
        "max_dist": max_dist,
        "union_dist": union_dist,
        # 4+/5+/6+ variant statistics (cele mai importante pentru user)
        "total_variants_4plus": total_4plus_variants,
        "total_variants_5plus": total_5plus_variants,
        "total_variants_6plus": total_6plus_variants,
        "sims_with_4plus_variant": sims_with_4plus_variant,
        "sims_with_5plus_variant": sims_with_5plus_variant,
        "sims_with_6plus_variant": sims_with_6plus_variant,
        "pct_sims_with_4plus": round(100.0 * sims_with_4plus_variant / max(n, 1), 2),
        "pct_sims_with_5plus": round(100.0 * sims_with_5plus_variant / max(n, 1), 2),
        "pct_sims_with_6plus": round(100.0 * sims_with_6plus_variant / max(n, 1), 2),
        "avg_4plus_per_sim": round(total_4plus_variants / max(n, 1), 3),
        # Pentru A/B: raw arrays (permit re-agregare după tag)
        "_raw": {
            "max_hits": max_hits,
            "union_hits": union_hits,
            "variant_counts": variant_counts,
            "draw_indices": [int(p.draw_index) for p in preds],
            "variants_at_hits_per_sim": variants_at_hits_per_sim,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="input.csv")
    ap.add_argument("--game", default="5/40", help="Tip joc (input.csv are 5 numere → 5/40)")
    ap.add_argument("--pools", default="9,10,11,12,15",
                    help="Lista pool sizes separate prin virgulă")
    ap.add_argument("--guarantee", type=int, default=4)
    ap.add_argument("--depth", type=float, default=3.0,
                    help="Procent din istoric (coadă) folosit pentru simulare")
    ap.add_argument("--lookback", type=float, default=100.0,
                    help="Procent istoric folosit pentru analiză la fiecare pas")
    ap.add_argument("--step", type=int, default=1,
                    help="Pas între extrageri simulate (1 = toate)")
    ap.add_argument("--tag", required=True, help="Tag pentru output (baseline, exp1, ...)")
    ap.add_argument("--no-smart-reduction", action="store_true",
                    help="Dezactivează filtrul negativ (regressive blacklist) — pentru ablation")
    ap.add_argument("--no-consecutive-filter", action="store_true",
                    help="Dezactivează filtrul anti-consecutive — pentru ablation")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.WARNING,  # tăcut, doar erorile reale
        format="[%(asctime)s] [%(levelname)s] %(message)s",
    )

    pools = [int(p) for p in args.pools.split(",")]
    out_path = ROOT / "scratch" / f"bench_{args.tag}.json"

    print(f"[BENCH] CSV: {args.csv} | game: {args.game}")
    print(f"[BENCH] Pools: {pools} | guarantee: {args.guarantee} | depth: {args.depth}% | step: {args.step}")
    print(f"[BENCH] Output: {out_path}")
    print()

    results = []
    t_start = time.time()
    for pool_size in pools:
        print(f"[BENCH] >>> Pool {pool_size}, guarantee {args.guarantee} ...", flush=True)
        try:
            r = run_one_pool(
                csv_path=args.csv,
                game_type=args.game,
                pool_size=pool_size,
                guarantee=args.guarantee,
                depth_pct=args.depth,
                lookback_pct=args.lookback,
                sim_step=args.step,
                smart_reduction=not args.no_smart_reduction,
                filter_consecutives=not args.no_consecutive_filter,
            )
        except Exception as exc:
            r = {"pool_size": pool_size, "error": str(exc)}
            print(f"[BENCH]     ERROR: {exc}", flush=True)
        results.append(r)
        if "error" not in r:
            print(f"[BENCH]     n={r['n_simulations']}  avg_max_hits={r['avg_max_hits']:.3f}"
                  f"  avg_union_hits={r['avg_union_hits']:.3f}"
                  f"  variants={r['avg_variant_count']:.0f}"
                  f"  ({r['elapsed_s']:.0f}s)", flush=True)

    total_elapsed = time.time() - t_start

    payload = {
        "tag": args.tag,
        "csv": args.csv,
        "game": args.game,
        "guarantee": args.guarantee,
        "depth_pct": args.depth,
        "lookback_pct": args.lookback,
        "step": args.step,
        "total_elapsed_s": round(total_elapsed, 1),
        "results": results,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Summary table la stdout
    print()
    print("=" * 110)
    print(f"{'Pool':>5} {'Sims':>5} {'AvgMax':>7} {'MaxMax':>7} {'AvgU':>6} "
          f"{'#Var':>5} {'4+sim%':>7} {'5+sim%':>7} {'6+sim%':>7} {'#4+tot':>7} {'Time':>6}")
    print("-" * 110)
    for r in results:
        if "error" in r:
            print(f"{r['pool_size']:>5}  ERROR: {r['error']}")
            continue
        print(f"{r['pool_size']:>5} {r['n_simulations']:>5} "
              f"{r['avg_max_hits']:>7.3f} {r['max_max_hits']:>7d} "
              f"{r['avg_union_hits']:>6.2f} {r['avg_variant_count']:>5.0f} "
              f"{r['pct_sims_with_4plus']:>6.1f}% {r['pct_sims_with_5plus']:>6.1f}% "
              f"{r['pct_sims_with_6plus']:>6.1f}% {r['total_variants_4plus']:>7d} "
              f"{r['elapsed_s']:>5.0f}s")
    print("=" * 110)
    print(f"Total: {total_elapsed:.0f}s | salvat în {out_path}")


if __name__ == "__main__":
    main()
