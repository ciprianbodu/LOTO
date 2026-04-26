import os
import sys
import numpy as np
import pandas as pd
from loto_engine import LotoEngine

def evaluate_variant(variant, true_draw):
    return len(set(variant).intersection(set(true_draw)))

def test_params(pool_size, sim_depth_pct, lookback, test_draws=5):
    engine = LotoEngine(game_type="6/49")
    success = engine.load_data("input.csv")
    if not success:
        return -1
        
    df = engine.data.copy()
    total_draws = len(df)
    
    total_hits = 0
    total_variants = 0
    
    # We will test on the last `test_draws`
    for i in range(total_draws - test_draws, total_draws):
        # Data up to i
        train_df = df.iloc[:i].copy()
        engine.data = train_df
        engine._build_draw_matrix()
        
        # True draw at i
        row = df.iloc[i]
        n_cols = sorted([c for c in df.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"], 
                        key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))
        true_draw = [int(row[c]) for c in n_cols if pd.notna(row.get(c))]
        
        try:
            lines, _, _, _, _, _ = engine.run_institutional_pipeline(
                pool_size=pool_size,
                guarantee=4,
                max_variants=10,
                lookback=lookback,
                filter_consecutives=True,
                smart_reduction=True,
                sim_depth_pct=sim_depth_pct
            )
            
            for line in lines:
                hits = evaluate_variant(line, true_draw)
                total_hits += hits
                total_variants += 1
        except Exception as e:
            # Maybe TimesFM is not installed, fallback used
            pass
            
    if total_variants == 0:
        return 0
    return total_hits / total_variants

def main():
    pool_sizes = [9, 12, 15]
    sim_depths = [20, 50, 100]
    lookbacks = [0, 20, 50]
    
    results = []
    
    print("Testing combinations...")
    for p in pool_sizes:
        for s in sim_depths:
            for l in lookbacks:
                avg_hits = test_params(pool_size=p, sim_depth_pct=s, lookback=l, test_draws=3)
                print(f"Pool: {p}, SimDepth: {s}, Lookback: {l} => Avg Hits: {avg_hits:.2f}")
                results.append((avg_hits, p, s, l))
                
    results.sort(reverse=True, key=lambda x: x[0])
    print("\nBest Parameters:")
    for res in results[:3]:
        print(f"Avg Hits: {res[0]:.2f} (Pool: {res[1]}, SimDepth: {res[2]}, Lookback: {res[3]})")

if __name__ == "__main__":
    main()
