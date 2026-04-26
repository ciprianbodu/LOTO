import os
import sys
import numpy as np
import pandas as pd
from loto_engine import LotoEngine

import logging

def run_calibration(engine, test_draws=2, depths_to_test=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100], pool_size=12, progress_cb=None):
    # Oprim temporar log-urile INFO, dar lasam un avertisment sa se stie ca incepe
    logging.warning("[CALIBRARE] Incepere procedura de Auto-Tuning. Initializare model...")
    
    old_level = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.WARNING)
    
    df = engine.data.copy()
    total_draws = len(df)
    
    results = {d: {"score": 0, "total_excluded": 0, "total_fatalities": 0} for d in depths_to_test}
    
    total_iters = test_draws * 10 # 10 pasi de regresie per extragere
    current_iter = 0
    
    for i in range(total_draws - test_draws, total_draws):
        # Pregatim engine-ul pentru extragerea de test curenta
        train_df = df.iloc[:i].copy()
        engine.data = train_df
        engine._build_draw_matrix()
        
        row = df.iloc[i]
        n_cols = sorted([c for c in df.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"], 
                        key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))
        true_draw = set([int(row[c]) for c in n_cols if pd.notna(row.get(c))])
        
        # 1. Calculam "feliile" regresive (scorurile) o singura data pentru aceasta extragere
        slice_blacklists = {}
        
        for step in range(100, 9, -10):
            current_iter += 1
            if progress_cb:
                p_val = min(current_iter / total_iters, 0.99)
                progress_cb(p_val, f"🚀 Analiză neurală: Pas {step}% (Extragere {total_draws-i}/{test_draws})")
            
            try:
                num_rows = int(len(train_df) * (step / 100))
                if num_rows < 32: continue
                
                data_slice = train_df.tail(num_rows).copy()
                # dm_slice logic similar to get_regressive_blacklist_v2
                dm_slice = engine._draw_matrix[-num_rows:] if engine._draw_matrix is not None else None
                
                from loto_enterprise.core.timesfm_engine import get_timesfm_scores_v2
                scores = get_timesfm_scores_v2(
                    data_slice, dm_slice, engine.params, 
                    is_joker_drum=(engine.game_type == "joker"),
                    context_len=num_rows,
                    is_regressive_step=True
                )
                
                if scores:
                    vals = list(scores.values())
                    threshold = np.percentile(vals, 25)
                    slice_blacklists[step] = {n for n, s in scores.items() if s <= threshold}
            except Exception as e:
                print(f"Eroare la calcul slice {step}%: {e}")

        # 2. Acum evaluam fiecare adancime rapid prin intersectia feliilor pre-calculate
        for depth in depths_to_test:
            # Blacklist-ul pentru adancimea 'depth' este intersectia tuturor feliilor de la 100 pana la 'depth'
            relevant_steps = [s for s in slice_blacklists.keys() if s >= depth]
            if not relevant_steps:
                continue
                
            current_blacklist = slice_blacklists[relevant_steps[0]]
            for s in relevant_steps[1:]:
                current_blacklist = current_blacklist.intersection(slice_blacklists[s])
            
            excluded_count = len(current_blacklist)
            fatalities = len(current_blacklist.intersection(true_draw))
            
            results[depth]["total_excluded"] += excluded_count
            results[depth]["total_fatalities"] += fatalities

    # 3. Calculam scorurile finale
    for depth in depths_to_test:
        res = results[depth]
        res["score"] = res["total_excluded"] - (res["total_fatalities"] * 50)
        res["avg_excluded"] = res["total_excluded"] / test_draws if test_draws > 0 else 0
        
    best_depth = 40
    best_score = -99999
    
    for depth, stats in results.items():
        if stats['score'] > best_score:
            best_score = stats['score']
            best_depth = depth
            
    # Restore original data
    engine.data = df
    engine._build_draw_matrix()
    
    # Restauram log-urile
    logging.getLogger().setLevel(old_level)
    
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
        print(f"--> Adancime {depth}% : Scor = {stats['score']:.1f} | Excluse Total: {stats['total_excluded']} | Fatalitati: {stats['total_fatalities']} | Medie: {stats['avg_excluded']:.2f}")
            
    print(f"\n=====================================================")
    print(f" RECOMANDARE DINAMICĂ: Setează 'Adancime Simulare' la {best_depth}%")
    print(f"=====================================================")

if __name__ == "__main__":
    test_empiric()
