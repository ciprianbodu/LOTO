import os
import sys
import numpy as np
import pandas as pd
from loto_engine import LotoEngine

def run_calibration(engine, test_draws=3, depths_to_test=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100]):
    df = engine.data.copy()
    total_draws = len(df)
    
    results = {}
    
    for depth in depths_to_test:
        hits_4 = 0
        hits_5 = 0
        hits_6 = 0
        pool_hits_list = []
        
        for i in range(total_draws - test_draws, total_draws):
            train_df = df.iloc[:i].copy()
            engine.data = train_df
            engine._build_draw_matrix()
            
            row = df.iloc[i]
            n_cols = sorted([c for c in df.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"], 
                            key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))
            true_draw = [int(row[c]) for c in n_cols if pd.notna(row.get(c))]
            
            try:
                lines, _, _, _, _, audit = engine.run_institutional_pipeline(
                    progress_cb=None,
                    pool_size=9,
                    guarantee=4,
                    max_variants=20,
                    lookback=0,
                    filter_consecutives=True,
                    smart_reduction=True,
                    sim_depth_pct=depth
                )
                
                # Check hits in the hard core pool
                pool = engine.hard_core
                pool_hits = len(set(pool).intersection(set(true_draw)))
                pool_hits_list.append(pool_hits)
                
                # Check hits in the generated lines
                max_line_hits = 0
                for line in lines:
                    line_hits = len(set(line).intersection(set(true_draw)))
                    max_line_hits = max(max_line_hits, line_hits)
                    
                if max_line_hits == 4: hits_4 += 1
                elif max_line_hits == 5: hits_5 += 1
                elif max_line_hits == 6: hits_6 += 1
                
            except Exception as e:
                pass
                
        # Calculam un scor pentru acest prag de adancime:
        score = (hits_6 * 100) + (hits_5 * 20) + (hits_4 * 5) + np.sum(pool_hits_list)
        results[depth] = {
            "score": score,
            "avg_pool_hits": np.mean(pool_hits_list) if pool_hits_list else 0,
            "hits_4": hits_4,
            "hits_5": hits_5,
            "hits_6": hits_6
        }
        
    best_depth = 40
    best_score = -1
    
    for depth, stats in results.items():
        if stats['score'] > best_score:
            best_score = stats['score']
            best_depth = depth
            
    # Restore original data
    engine.data = df
    engine._build_draw_matrix()
    
    return best_depth, results

def test_empiric():
    engine = LotoEngine(game_type="6/49")
    success = engine.load_data("input.csv")
    if not success:
        print("Failed to load data")
        return
        
    test_draws = 4
    depths = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    
    print(f"Testing dynamic Auto-Tuner pe ultimele {test_draws} extrageri...")
    best_depth, results = run_calibration(engine, test_draws=test_draws, depths_to_test=depths)
    
    print("\n\n#####################################################")
    print(" REZULTATE AUTO-TUNER DINAMIC (BACKTESTING)")
    print("#####################################################")
    
    for depth, stats in results.items():
        print(f"--> Adancime {depth}% : Scor = {stats['score']:.1f} | 4-Hits: {stats['hits_4']} | 5-Hits: {stats['hits_5']} | 6-Hits: {stats['hits_6']} | Avg Pool Hits: {stats['avg_pool_hits']:.2f}")
            
    print(f"\n=====================================================")
    print(f" RECOMANDARE DINAMICĂ: Setează 'Adancime Simulare' la {best_depth}%")
    print(f"=====================================================")

if __name__ == "__main__":
    test_empiric()
