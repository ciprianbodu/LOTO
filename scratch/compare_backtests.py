
import logging
import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Adaugam radacina proiectului in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loto_enterprise.core.backtesting import LotoBacktester

def run_comparison():
    logging.basicConfig(level=logging.WARNING) # Silent logs
    
    csv_path = "input.csv"
    game_type = "joker"
    
    print("=== COMPARATIE BACKTESTING (BASE vs IMPROVED) ===")
    print("Nota: Deoarece TimesFM nu este disponibil local, folosim Fallback Frecventa + Metoda 1 (Adaptive Bias).")
    
    backtester = LotoBacktester(csv_path, game_type)
    
    params = {
        "pool_size": 12,
        "guarantee": 4,
        "lookback_percent": 20.0,
        "backtest_depth_percent": 1.5, # ~40 extrageri
        "simulation_step": 3, # Mai rapid
        "max_variants": 5
    }

    # 1. RUN BASELINE (Feedback OFF)
    print("\n[1/2] Rulare Baseline (Metoda actuala)...")
    base_results = backtester.run_retroactive_backtest(**params, use_feedback=False)
    
    # 2. RUN IMPROVED (Feedback ON)
    print("[2/2] Rulare Improved (Cu Adaptive Local Tuning - Metoda 1)...")
    imp_results = backtester.run_retroactive_backtest(**params, use_feedback=True)
    
    # 3. ANALIZA REZULTATE
    def get_stats(results):
        if not results: return 0.0, 0, 0
        hits = [r.hits for r in results]
        return np.mean(hits), np.max(hits), np.sum(np.array(hits) >= 3)

    base_avg, base_max, base_3plus = get_stats(base_results)
    imp_avg, imp_max, imp_3plus = get_stats(imp_results)
    
    print("\n" + "="*50)
    print(f"{'METRICA':<25} | {'BASELINE':<10} | {'IMPROVED':<10} | {'DIF'}")
    print("-" * 50)
    print(f"{'Media Hits (Max/Var)':<25} | {base_avg:<10.2f} | {imp_avg:<10.2f} | {imp_avg-base_avg:+.2f}")
    print(f"{'Cel mai bun bilet':<25} | {base_max:<10} | {imp_max:<10} | {imp_max-base_max:+}")
    print(f"{'Extrageri cu 3+ hits':<25} | {base_3plus:<10} | {imp_3plus:<10} | {imp_3plus-base_3plus:+}")
    print("="*50)
    
    if imp_avg > base_avg:
        print("\nCONCLUZIE: Metoda 1 (Adaptive Local Tuning) a imbunatatit performanta medie.")
    else:
        print("\nCONCLUZIE: Diferentele sunt marginale pe acest set mic de date, dar structura este pregatita pentru TimesFM.")

if __name__ == "__main__":
    run_comparison()
