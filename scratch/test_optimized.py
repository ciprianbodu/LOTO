# -*- coding: utf-8 -*-
"""Test optimized engine v3 -- quick single-run validation against last 10 draws."""
import sys, logging, time, os
os.environ["PYTHONIOENCODING"] = "utf-8"
logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, r"d:\_LIBRARIES\OneDrive\_CODING\_LOTO")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except: pass

import numpy as np
import pandas as pd
from loto_engine import LotoEngine

csv_path = r"d:\_LIBRARIES\OneDrive\_CODING\_LOTO\input.csv"
df_full = pd.read_csv(csv_path)
total = len(df_full)

TEST_DRAWS = 10

n_cols = sorted([c for c in df_full.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))

print(f"Total extrageri: {total}")
print(f"Coloane numere: {n_cols}")
print(f"Testam pe ultimele {TEST_DRAWS} extrageri (walk-forward)")
print("=" * 70)

results = []
for test_idx in range(total - TEST_DRAWS, total):
    train_df = df_full.iloc[:test_idx].copy()
    actual_row = df_full.iloc[test_idx]
    actual_nums = set(int(actual_row[c]) for c in n_cols[:5] if pd.notna(actual_row.get(c)))
    actual_joker = int(actual_row["joker"]) if "joker" in df_full.columns and pd.notna(actual_row.get("joker")) else None
    
    engine = LotoEngine("joker")
    engine.data = train_df
    engine._build_draw_matrix()
    
    t0 = time.time()
    try:
        lines, p10, p90, g_range, context, audit = engine.run_institutional_pipeline(
            progress_cb=None, pool_size=12, guarantee=4, max_variants=0,
            lookback=0, filter_consecutives=False, smart_reduction=False, sim_depth_pct=10
        )
    except Exception as e:
        print(f"  Eroare la extragerea {test_idx}: {e}")
        continue
    elapsed = time.time() - t0
    
    pool = set(engine.hard_core)
    pool_hits = pool & actual_nums
    
    best_hits = 0
    for v in lines:
        h = len(set(v[:5]) & actual_nums)
        best_hits = max(best_hits, h)
    
    joker_hit = actual_joker in getattr(engine, 'hard_core_joker', []) if actual_joker else False
    
    date_str = str(actual_row.get("date", f"#{test_idx}")).split()[0]
    results.append({
        "date": date_str,
        "actual": sorted(actual_nums),
        "pool": sorted(pool),
        "pool_hits": len(pool_hits),
        "hit_nums": sorted(pool_hits),
        "best_var_hits": best_hits,
        "joker_hit": joker_hit,
        "elapsed": elapsed
    })
    
    marker = "[5+]" if len(pool_hits) >= 5 else ("[4+]" if len(pool_hits) >= 4 else ("[3+]" if len(pool_hits) >= 3 else "[  ]"))
    print(f"  {marker} {date_str}: Pool={len(pool_hits)}/5 ({sorted(pool_hits)}) | BestVar={best_hits} | Joker={'Y' if joker_hit else 'N'} | {elapsed:.1f}s")

print("\n" + "=" * 70)
print("  SUMAR OPTIMIZAT v3")
print("=" * 70)
if results:
    union_hits = [r["pool_hits"] for r in results]
    var_hits = [r["best_var_hits"] for r in results]
    print(f"  Total teste: {len(results)}")
    print(f"  Avg Union Hits (pool of 12): {np.mean(union_hits):.2f}")
    print(f"  Avg Best Variant Hits: {np.mean(var_hits):.2f}")
    for t in [5, 4, 3, 2]:
        c = sum(1 for h in union_hits if h >= t)
        print(f"  {t}+ pool hits: {c}/{len(union_hits)} ({c/len(union_hits)*100:.1f}%)")
    print(f"  Distributie: {dict(sorted({h: union_hits.count(h) for h in set(union_hits)}.items()))}")
    joker_count = sum(1 for r in results if r["joker_hit"])
    print(f"  Joker hits: {joker_count}/{len(results)} ({joker_count/len(results)*100:.1f}%)")
