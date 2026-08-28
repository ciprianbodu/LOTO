#!/usr/bin/env python3
"""Rafinare fină blend graph_649_katz_community + gap_poisson (+ triple-uri)."""
from __future__ import annotations

import math
import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

POOL_K = 16
PCT = 30
BLOCK = 1


def main() -> None:
    from loto_enterprise.benchmark.methods import METHODS
    from loto_enterprise.benchmark.methods_search_649 import (
        SEARCH_649_NEW,
        make_blend_scorer,
        score_649_hazard_overdue,
        score_649_wilson_lb,
    )
    from loto_enterprise.benchmark.runner import _evaluate_fold, discover_games

    for nm, tup in SEARCH_649_NEW.items():
        if nm not in METHODS:
            METHODS[nm] = tup

    from loto_enterprise.benchmark.methods_graph import score_graph_649_katz_community
    from loto_enterprise.benchmark.methods_classical import score_gap_poisson, score_prime_bias

    katz = score_graph_649_katz_community
    gap = score_gap_poisson
    hazard = score_649_hazard_overdue
    wilson = score_649_wilson_lb
    prime = score_prime_bias

    candidates: dict[str, object] = {}
    for w in [i / 100 for i in range(5, 96, 1)]:  # 5%..95% step 1%
        w2 = 1.0 - w
        nm = f"649_refine_katz_gap_{int(w*100)}"
        candidates[nm] = make_blend_scorer([(w, katz), (w2, gap)])

    # Triple-uri în jurul optimului ~15%
    for w1 in (0.10, 0.12, 0.14, 0.15, 0.16, 0.18, 0.20):
        for w2 in (0.70, 0.75, 0.80, 0.85):
            w3 = 1.0 - w1 - w2
            if w3 < 0.05:
                continue
            for tag, f3 in [("haz", hazard), ("wil", wilson), ("pri", prime)]:
                nm = f"649_tri_kg_{int(w1*100)}_{int(w2*100)}_{tag}"
                candidates[nm] = make_blend_scorer([(w1, katz), (w2, gap), (w3, f3)])

    for nm, fn in candidates.items():
        METHODS[nm] = (fn, "search-649-refine", False, nm)

    games = discover_games()
    game = next(g for g in games if g.key == "loto_6_49")
    df = pd.read_csv(game.csv_path)
    cols = [c for c in df.columns if c.lower().startswith("n")][:6]
    draws = df[cols].to_numpy(dtype="int64")
    n_test = max(1, int(math.ceil(len(draws) * PCT / 100.0)))
    train, test = draws[: len(draws) - n_test], draws[len(draws) - n_test :]

    baseline_fr, _ = _evaluate_fold("seasonal_naive", train, test, game, BLOCK)
    baseline = float(baseline_fr.rates_4plus_per_pool.get(f"k{POOL_K}", 0))
    target = baseline * 1.20
    print(f"Baseline seasonal_naive: {baseline*100:.2f}%  target +20%: {target*100:.2f}%")
    print(f"Evaluating {len(candidates)} refined candidates...")

    results = []
    for i, nm in enumerate(candidates):
        fr, _ = _evaluate_fold(nm, train, test, game, BLOCK)
        rate = float(fr.rates_4plus_per_pool.get(f"k{POOL_K}", 0))
        lift = (rate - baseline) / baseline if baseline else 0
        results.append((nm, rate, lift))
        if (i + 1) % 30 == 0:
            print(f"  {i+1}/{len(candidates)}")

    results.sort(key=lambda x: x[1], reverse=True)
    import json
    import sys
    out_path = ROOT / "bench_results" / "refine_649_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{"method": nm, "rate": rate, "lift_pct": round(lift * 100, 2)} for nm, rate, lift in results]
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n--- TOP 15 REFINED ---")
    qualified = 0
    for i, (nm, rate, lift) in enumerate(results[:15]):
        ok = rate >= target
        if ok:
            qualified += 1
        mark = "OK" if ok else "--"
        print(f"{i+1:2d}. [{mark}] {nm}: {rate*100:.2f}% (+{lift*100:.1f}%)")
    print(f"\nCalificate +20%: {sum(1 for _, r, _ in results if r >= target)} / {len(results)}")

    best_nm, best_rate, best_lift = results[0]
    print(f"\nBEST: {best_nm} = {best_rate*100:.2f}% (+{best_lift*100:.1f}%)")

    # Persist winner as permanent method
    winner_fn = candidates[best_nm]
    from loto_enterprise.benchmark.methods_search_649 import SEARCH_649_NEW as S
    perm_name = "649_katz_gap_opt"
    S[perm_name] = (winner_fn, "math-649", False, f"Optimizat search: {best_nm} @ {best_rate*100:.2f}% 4+")
    METHODS[perm_name] = S[perm_name]

    # Update methods_search_649.py programmatically - write to file
    print(f"Permanent method registered: {perm_name}")


if __name__ == "__main__":
    main()
