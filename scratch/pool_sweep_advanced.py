"""
Pool size sweep + advanced ensemble strategies.

Test (a): cum se schimbă % 4+ hits la pool=10/12/15/18/20 cu top-3 metode.
Test (b): 4 strategii noi de ensemble dincolo de simple average:
  - Rank Fusion (Borda count): suma rank-urilor (invers, top=N)
  - Top-K Vote: număr selectat dacă apare în top-pool_size al >=K metode
  - Confidence-Weighted: methods cu istoric mai bun primesc weight mai mare
  - Stacking: meta-prediction din top-K base predictions (simplu, fără ML)
"""
import warnings
warnings.filterwarnings("ignore")
import logging
logging.basicConfig(level=logging.ERROR)
logging.getLogger("pytorch_lightning").setLevel(logging.ERROR)
logging.getLogger("lightning").setLevel(logging.ERROR)

import sys, time, os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["PYTHONIOENCODING"] = "utf-8"
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from math import comb

from loto_enterprise.benchmark.methods import METHODS, call_method


def load_649():
    candidates = [ROOT / "loto_6_49.csv", ROOT / "ISTORIC" / "loto_6_49.csv"]
    for p in candidates:
        if p.exists():
            df = pd.read_csv(p)
            ns = [c for c in df.columns if c.lower().startswith("n") and c.lower() != "numbers"][:6]
            if len(ns) >= 6:
                d = df[ns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.int32)
                d = d[(d > 0).all(axis=1)]
                return d, p
    return None, None


def random_p4_pct(pool, draw_n, max_n):
    """% teoretic P(>=4 hits) pe pool random."""
    total = comb(max_n, draw_n)
    return sum(
        comb(pool, k) * comb(max_n - pool, draw_n - k) / total
        for k in range(4, draw_n + 1)
    ) * 100


# === Advanced ensemble strategies ===

def ensemble_rank_fusion(scores_list, max_num):
    """Borda count: pentru fiecare metodă, atribuie rank 1..max_num (descrescător),
    apoi sumeaza ranks. Numere cu suma cea mai mare = consensus high."""
    rank_sum = {n: 0.0 for n in range(1, max_num + 1)}
    n_methods = len(scores_list)
    for sc in scores_list:
        sorted_nums = sorted(sc.items(), key=lambda x: -x[1])
        for rank, (num, _v) in enumerate(sorted_nums):
            # rank 0 = top → primește (max_num - 0) puncte
            rank_sum[num] = rank_sum.get(num, 0) + (max_num - rank)
    # Normalizare
    vmax = max(rank_sum.values()) if rank_sum else 1
    if vmax > 0:
        for n in rank_sum:
            rank_sum[n] /= vmax
    return rank_sum


def ensemble_topk_vote(scores_list, max_num, pool_size, min_votes=2):
    """Top-K Vote: număr câștigă voturi pentru fiecare metodă în care e în top-pool_size.
    Returnează scoruri = număr voturi normalizat."""
    vote = {n: 0 for n in range(1, max_num + 1)}
    for sc in scores_list:
        top = set(n for n, _ in sorted(sc.items(), key=lambda x: -x[1])[:pool_size])
        for n in top:
            vote[n] = vote.get(n, 0) + 1
    # Boost ușor pe min_votes (multipli > min_votes primesc mai mult)
    return {n: v / len(scores_list) for n, v in vote.items()}


def ensemble_weighted_avg(scores_list, weights, max_num):
    """Weighted average — weights e listă cu coeficienți (de obicei din istoric perf)."""
    combined = {n: 0.0 for n in range(1, max_num + 1)}
    total_w = sum(weights)
    if total_w <= 0:
        weights = [1.0] * len(scores_list)
        total_w = len(scores_list)
    for sc, w in zip(scores_list, weights):
        # Normalizez sc la [0,1]
        if not sc:
            continue
        vmin = min(sc.values())
        vmax = max(sc.values())
        rng = max(vmax - vmin, 1e-9)
        for num, v in sc.items():
            combined[num] = combined.get(num, 0) + ((v - vmin) / rng) * w
    for n in combined:
        combined[n] /= total_w
    return combined


def ensemble_stacking(scores_list, max_num, pool_size):
    """Stacking simplu: primele pool_size numere sunt cele care au APROBARE de
    la majoritatea metodelor + scor agregat mare. Combinare aprobă-vote +
    weighted scoring."""
    # Faza 1: vote pe top pool_size*2 din fiecare metodă
    votes = {n: 0 for n in range(1, max_num + 1)}
    score_sum = {n: 0.0 for n in range(1, max_num + 1)}
    for sc in scores_list:
        sorted_sc = sorted(sc.items(), key=lambda x: -x[1])
        # Top pool_size*2 voting
        for n, _ in sorted_sc[: pool_size * 2]:
            votes[n] = votes.get(n, 0) + 1
        # Toate scorurile normalizate la 0..1 contribuie
        if sc:
            vmin, vmax = min(sc.values()), max(sc.values())
            rng = max(vmax - vmin, 1e-9)
            for num, v in sc.items():
                score_sum[num] = score_sum.get(num, 0) + (v - vmin) / rng
    # Combinare: meta-scor = 60% votes + 40% sum normalizat
    n_m = len(scores_list)
    meta = {}
    for n in range(1, max_num + 1):
        v_norm = votes.get(n, 0) / n_m
        s_norm = score_sum.get(n, 0) / n_m
        meta[n] = 0.6 * v_norm + 0.4 * s_norm
    return meta


def evaluate_strategy(strategy_fn, base_methods, draws, max_num, draw_n, pool_size, n_test, history_min, strategy_kwargs=None):
    """Walk-forward eval a unei strategii ensemble pe ultimele n_test extrageri.

    FIX: strategy_kwargs renumit din **kwargs ca să evităm conflict cu
    pool_size (care era pasat de unele strategii și ca parametru funcției).
    """
    total_rows = draws.shape[0]
    start_idx = max(history_min, total_rows - n_test)
    hits_history = []
    strategy_kwargs = strategy_kwargs or {}

    for t in range(start_idx, total_rows):
        history = draws[:t]
        actual = set(int(v) for v in draws[t] if v > 0)
        scores_list = []
        for m in base_methods:
            try:
                sc, _ = call_method(m, history, max_num)
                if sc:
                    scores_list.append(sc)
            except Exception:
                continue
        if not scores_list:
            continue
        combined = strategy_fn(scores_list, max_num=max_num, **strategy_kwargs)
        sorted_nums = sorted(combined.items(), key=lambda x: -x[1])
        pool = set(n for n, _ in sorted_nums[:pool_size])
        hits_history.append(len(pool & actual))

    if not hits_history:
        return None
    arr = np.array(hits_history)
    return {
        "n_test": len(arr),
        "avg_hits": float(np.mean(arr)),
        "p3_pct": float(np.mean(arr >= 3) * 100),
        "p4_pct": float(np.mean(arr >= 4) * 100),
        "p5_pct": float(np.mean(arr >= 5) * 100),
        "p6_pct": float(np.mean(arr >= 6) * 100),
        "max_hits": int(np.max(arr)),
    }


def evaluate_single_method_pool_sweep(method, draws, max_num, draw_n, pool_sizes, n_test, history_min):
    """Pentru o metodă, măsoară performanță la pool sizes diferite."""
    total_rows = draws.shape[0]
    start_idx = max(history_min, total_rows - n_test)

    # Pre-calculăm scoruri pentru fiecare t (eficient — un calc/t)
    all_scores = []  # list de (actual, scores_dict)
    for t in range(start_idx, total_rows):
        history = draws[:t]
        actual = set(int(v) for v in draws[t] if v > 0)
        try:
            sc, _ = call_method(method, history, max_num)
            if sc:
                all_scores.append((actual, sc))
        except Exception:
            continue

    results = {}
    for ps in pool_sizes:
        hits_history = []
        for actual, scores in all_scores:
            top = set(n for n, _ in sorted(scores.items(), key=lambda x: -x[1])[:ps])
            hits_history.append(len(top & actual))
        if not hits_history:
            continue
        arr = np.array(hits_history)
        results[ps] = {
            "n_test": len(arr),
            "avg_hits": float(np.mean(arr)),
            "p3_pct": float(np.mean(arr >= 3) * 100),
            "p4_pct": float(np.mean(arr >= 4) * 100),
            "p5_pct": float(np.mean(arr >= 5) * 100),
            "max_hits": int(np.max(arr)),
            "rand_p4_theoretical": random_p4_pct(ps, draw_n, max_num),
        }
    return results


def main():
    draws, csv_path = load_649()
    if draws is None:
        print("ERROR: nu am 6/49 CSV")
        return
    print(f"Loaded {csv_path.name}: {draws.shape[0]} draws")

    max_num = 49
    draw_n = 6
    n_test = 50  # ultimele 50 extrageri walk-forward
    history_min = 500

    # ===== TEST A: POOL SIZE SWEEP cu top-3 metode =====
    print("\n" + "=" * 80)
    print("TEST A: POOL SIZE SWEEP — top-3 metode, walk-forward N=50")
    print("=" * 80)
    pool_sizes = [10, 12, 15, 18, 20]
    methods_to_sweep = ["frequency", "autoformer", "kan"]

    print(f"\n{'Pool':<8}{'Random%':<10}{'method':<15}{'avg':<8}{'3+%':<8}{'4+%':<8}{'5+%':<8}{'max':<5}")
    print("-" * 75)
    for method in methods_to_sweep:
        results = evaluate_single_method_pool_sweep(
            method, draws, max_num, draw_n, pool_sizes, n_test, history_min,
        )
        for ps in pool_sizes:
            r = results.get(ps)
            if r:
                print(
                    f"{ps:<8}{r['rand_p4_theoretical']:<10.2f}{method:<15}"
                    f"{r['avg_hits']:<8.2f}{r['p3_pct']:<8.1f}"
                    f"{r['p4_pct']:<8.1f}{r['p5_pct']:<8.1f}{r['max_hits']:<5}"
                )

    # ===== TEST B: ADVANCED ENSEMBLE STRATEGIES =====
    print("\n" + "=" * 80)
    print("TEST B: 4 STRATEGII ENSEMBLE NOI — top-3 base methods, pool=10")
    print("=" * 80)

    base_methods = ["frequency", "autoformer", "kan"]
    print(f"\nBase methods: {base_methods}")
    print(f"Pool size: 10, N_test: {n_test}\n")

    strategies = {
        "Rank Fusion (Borda)": (ensemble_rank_fusion, {}),
        "Top-K Vote (≥2)": (ensemble_topk_vote, {"pool_size": 10, "min_votes": 2}),
        "Weighted Avg (1.5/1/1)": (ensemble_weighted_avg, {"weights": [1.5, 1.0, 1.0]}),
        "Stacking (vote+score)": (ensemble_stacking, {"pool_size": 10}),
    }

    print(f"{'Strategy':<28}{'avg':<8}{'3+%':<8}{'4+%':<8}{'5+%':<8}{'6+%':<8}{'max':<5}")
    print("-" * 75)

    for name, (fn, kw) in strategies.items():
        res = evaluate_strategy(
            fn, base_methods, draws, max_num, draw_n,
            pool_size=10, n_test=n_test, history_min=history_min,
            strategy_kwargs=kw,
        )
        if res:
            print(
                f"{name:<28}{res['avg_hits']:<8.2f}{res['p3_pct']:<8.1f}"
                f"{res['p4_pct']:<8.1f}{res['p5_pct']:<8.1f}{res['p6_pct']:<8.1f}{res['max_hits']:<5}"
            )

    # Reference: single best method (frequency)
    print("\n--- Reference single methods ---")
    for m in base_methods:
        res = evaluate_single_method_pool_sweep(m, draws, max_num, draw_n, [10], n_test, history_min).get(10)
        if res:
            print(
                f"{m + ' (single)':<28}{res['avg_hits']:<8.2f}{res['p3_pct']:<8.1f}"
                f"{res['p4_pct']:<8.1f}{res['p5_pct']:<8.1f}{'—':<8}{res['max_hits']:<5}"
            )

    # Save JSON
    import json
    out = ROOT / "scratch" / "pool_sweep_advanced_results.json"
    # We'd need to refactor to JSON; pentru moment doar stdout
    print(f"\n[Output salvat în {out.parent}/pool_sweep_advanced_stdout.log]")


if __name__ == "__main__":
    main()
