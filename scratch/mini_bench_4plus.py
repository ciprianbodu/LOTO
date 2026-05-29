"""
Mini-bench empiric: rulează fiecare metodă pe walk-forward,
măsoară % 4+ hits real. Scopul: identifică metodă care BATE
semnificativ random baseline.

Run: python scratch/mini_bench_4plus.py
"""
import warnings
warnings.filterwarnings("ignore")
import logging
logging.basicConfig(level=logging.ERROR)
logging.getLogger("pytorch_lightning").setLevel(logging.ERROR)
logging.getLogger("lightning").setLevel(logging.ERROR)

import sys, time, os
# Force UTF-8 stdout pe Windows ca să nu crash-uiască la diacritice
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["PYTHONIOENCODING"] = "utf-8"
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from loto_enterprise.benchmark.methods import METHODS, call_method


def evaluate_method_walkforward(
    method_name: str,
    draws_2d: np.ndarray,
    max_num: int,
    draw_n: int,
    pool_size: int = 10,
    n_test: int = 30,
    history_min: int = 200,
) -> dict:
    """Pentru o metodă: walk-forward pe ultimele n_test extrageri.

    La fiecare pas t:
      - Iau date <= t-1 (NO data leak)
      - Calculez scoruri cu metoda
      - Iau top pool_size numere ca pool
      - Compar pool cu extragerea reală la t
      - Numar hits

    Returnez stats agregate.
    """
    total_rows = draws_2d.shape[0]
    start_idx = max(history_min, total_rows - n_test)
    if start_idx >= total_rows:
        return {
            "method": method_name, "n_test": 0, "avg_hits": 0.0,
            "p4_pct": 0.0, "p5_pct": 0.0, "p6_pct": 0.0,
            "max_hits": 0, "total_time_s": 0.0, "fail": "no_test_data",
        }

    hits_history = []
    total_time = 0.0
    failures = 0

    for t in range(start_idx, total_rows):
        history = draws_2d[:t]
        actual = set(int(v) for v in draws_2d[t] if v > 0)
        try:
            t0 = time.perf_counter()
            scores, _ = call_method(method_name, history, max_num)
            total_time += time.perf_counter() - t0
        except Exception as exc:
            failures += 1
            continue
        if not scores:
            failures += 1
            continue
        # Top pool_size numere după scor
        sorted_nums = sorted(scores.items(), key=lambda x: -x[1])
        pool = [n for n, _ in sorted_nums[:pool_size]]
        hits = len(set(pool) & actual)
        hits_history.append(hits)

    n = len(hits_history)
    if n == 0:
        return {
            "method": method_name, "n_test": 0, "avg_hits": 0.0,
            "p4_pct": 0.0, "p5_pct": 0.0, "p6_pct": 0.0,
            "max_hits": 0, "total_time_s": round(total_time, 1),
            "fail": f"all_failed ({failures} errors)",
        }
    arr = np.array(hits_history)
    return {
        "method": method_name,
        "n_test": n,
        "avg_hits": float(np.mean(arr)),
        "p3_pct": float(np.mean(arr >= 3) * 100),
        "p4_pct": float(np.mean(arr >= 4) * 100),
        "p5_pct": float(np.mean(arr >= 5) * 100),
        "p6_pct": float(np.mean(arr >= 6) * 100),
        "max_hits": int(np.max(arr)),
        "total_time_s": round(total_time, 1),
        "avg_time_per_call_s": round(total_time / max(n, 1), 2),
    }


def main():
    # Detect 6/49 file — verifică toate locurile uzuale + valididează că are 6 coloane
    csv_candidates = [
        ROOT / "loto_6_49.csv",
        ROOT / "ISTORIC" / "loto_6_49.csv",
        ROOT / "input.csv",
    ]
    csv_path = None
    for p in csv_candidates:
        if not p.exists():
            continue
        # Verifică că are >= 6 coloane n*
        try:
            head = pd.read_csv(p, nrows=1)
            n_cols = [c for c in head.columns if c.lower().startswith("n") and c.lower() != "numbers"]
            if len(n_cols) >= 6:
                csv_path = p
                break
        except Exception:
            continue
    if csv_path is None:
        print("ERROR: Nu am găsit fișier CSV 6/49")
        return

    df = pd.read_csv(csv_path)
    n_cols = [c for c in df.columns if c.lower().startswith("n") and c.lower() != "numbers"]
    if len(n_cols) < 6:
        print(f"ERROR: CSV nu are 6 coloane n*, are doar {n_cols}")
        return
    n_cols = n_cols[:6]
    draws = df[n_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.int32)
    # filtrăm rânduri valide (toate 6 > 0)
    valid = (draws > 0).all(axis=1)
    draws = draws[valid]
    print(f"Loaded {csv_path.name}: {draws.shape[0]} draws válide, max={draws.max()}")

    max_num = 49
    draw_n = 6
    pool_size = 10
    n_test = 30  # ultimele 30 extrageri ca walk-forward test
    history_min = 500  # cel puțin 500 extrageri istorice ca training

    # Random baseline teoretic
    from math import comb
    p4_random_theoretical = sum(
        comb(pool_size, k) * comb(max_num - pool_size, draw_n - k) / comb(max_num, draw_n)
        for k in range(4, draw_n + 1)
    ) * 100
    print(f"\nRandom baseline P(>=4 hits) = {p4_random_theoretical:.3f}%")
    print(f"Adică așteptăm ~{p4_random_theoretical * n_test / 100:.2f} evenimente 4+ pe {n_test} extrageri test\n")

    # Doar metodele AVAILABLE (skip unavailable)
    available_methods = [
        name for name, (fn, _f, _t, _n) in METHODS.items()
        if not getattr(fn, "_unavailable_reason", None)
    ]
    # Sortăm: baseline rapide întâi, apoi neural
    fast_first = sorted(available_methods, key=lambda m: METHODS[m][1] != "baseline")
    print(f"Testez {len(fast_first)} metode pe {n_test} extrageri walk-forward...\n")

    results = []
    for i, method in enumerate(fast_first, 1):
        t_start = time.perf_counter()
        print(f"  [{i:2d}/{len(fast_first)}] {method:<25} ... ", end="", flush=True)
        res = evaluate_method_walkforward(
            method, draws, max_num, draw_n,
            pool_size=pool_size, n_test=n_test, history_min=history_min,
        )
        elapsed = time.perf_counter() - t_start
        if res.get("fail"):
            print(f"FAIL ({res['fail']})")
        else:
            print(
                f"avg={res['avg_hits']:.2f} "
                f"4+={res['p4_pct']:5.1f}% "
                f"5+={res['p5_pct']:4.1f}% "
                f"max={res['max_hits']} "
                f"({elapsed:.0f}s)"
            )
        results.append(res)

    # Sumar
    print("\n" + "=" * 100)
    print(f"{'Method':<25}{'avg':<8}{'3+%':<8}{'4+%':<8}{'5+%':<8}{'6+%':<8}{'max':<5}{'time/call':<10}")
    print("-" * 100)
    # Sortează după 4+ %, apoi 5+, apoi avg
    valid_results = [r for r in results if not r.get("fail")]
    valid_results.sort(key=lambda r: (r.get("p4_pct", 0), r.get("p5_pct", 0), r.get("avg_hits", 0)), reverse=True)
    for r in valid_results:
        print(
            f"{r['method']:<25}{r['avg_hits']:<8.2f}{r.get('p3_pct', 0):<8.1f}"
            f"{r['p4_pct']:<8.1f}{r['p5_pct']:<8.1f}{r['p6_pct']:<8.1f}"
            f"{r['max_hits']:<5}{r.get('avg_time_per_call_s', 0):<10.2f}"
        )

    print("\n" + "=" * 100)
    print(f"Random baseline P(>=4) teoretic: {p4_random_theoretical:.3f}%")
    print(f"Random baseline E(4+) pe {n_test} draws: ~{p4_random_theoretical * n_test / 100:.2f} evenimente")
    print("\nMetode care DEPĂȘESC random pe 4+ semnificativ (>=2× random):")
    threshold = p4_random_theoretical * 2
    winners = [r for r in valid_results if r["p4_pct"] >= threshold]
    if winners:
        for r in winners:
            print(f"  ⭐ {r['method']:<25} 4+%={r['p4_pct']:.1f}  ({r['p4_pct']/max(p4_random_theoretical, 0.01):.1f}× random)")
    else:
        print("  (none — niciuna nu bate semnificativ random baseline, conform matematicii iid)")

    # Salvez JSON pentru referință
    import json
    out = ROOT / "scratch" / "mini_bench_4plus_results.json"
    out.write_text(json.dumps({
        "config": {
            "csv": str(csv_path.name),
            "n_draws": int(draws.shape[0]),
            "n_test": n_test,
            "pool_size": pool_size,
            "history_min": history_min,
        },
        "random_baseline_p4_pct": p4_random_theoretical,
        "results": valid_results,
        "failed": [r for r in results if r.get("fail")],
    }, indent=2))
    print(f"\nSalvat: {out}")


if __name__ == "__main__":
    main()
