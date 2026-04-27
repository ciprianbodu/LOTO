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
        return None
        
    df = engine.data.copy()
    total_draws = len(df)
    
    hit_counts = {3: 0, 4: 0, 5: 0, 6: 0}
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
                max_variants=20, # increased variants
                lookback=lookback,
                filter_consecutives=True,
                smart_reduction=True,
                sim_depth_pct=sim_depth_pct
            )
            
            for line in lines:
                hits = evaluate_variant(line, true_draw)
                if hits >= 3:
                    hit_counts[min(hits, 6)] += 1
                total_variants += 1
        except Exception as e:
            # print(f"Error: {e}")
            pass
            
    if total_variants == 0:
        return hit_counts, 0

    return hit_counts, total_variants

def main():
    pool_sizes = [15, 18, 21]
    sim_depths = [50]
    lookbacks = [0, 20]
    
    test_draws = 10
    results = []
    
    print(f"Testing combinations on last {test_draws} draws...")
    for p in pool_sizes:
        for s in sim_depths:
            for l in lookbacks:
                res = test_params(pool_size=p, sim_depth_pct=s, lookback=l, test_draws=test_draws)
                if res:
                    hit_counts, total_v = res
                    h4 = hit_counts[4]
                    h5 = hit_counts[5]
                    score = h4 * 10 + h5 * 100 + hit_counts[3]
                    print(f"Pool: {p}, SimDepth: {s}, Lookback: {l} => Hits: {hit_counts}, Variants: {total_v}")
                    results.append((score, hit_counts, p, s, l))
                
    results.sort(reverse=True, key=lambda x: x[0])
    print("\nBest Parameters (weighted score 3*1 + 4*10 + 5*100):")
    for res in results[:3]:
        print(f"Score: {res[0]} (Hits: {res[1]}) (Pool: {res[2]}, SimDepth: {res[3]}, Lookback: {res[4]})")

if __name__ == "__main__":
    main()
