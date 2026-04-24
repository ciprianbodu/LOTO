
import logging
import sys
from pathlib import Path
import pandas as pd

# Adăugăm rădăcina proiectului în path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loto_enterprise.core.backtesting import LotoBacktester

def run_baseline():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    csv_path = "input.csv"
    game_type = "joker" # Detectat din coloane: n1..n5 + joker
    
    print(f"--- START BASELINE BACKTEST ({game_type}) ---")
    
    backtester = LotoBacktester(csv_path, game_type)
    
    # Rulăm un backtest retroactiv pe ultimele 2% din date (pentru viteză în demo)
    # Folosim parametrii default
    results = backtester.run_retroactive_backtest(
        pool_size=12,
        guarantee=4,
        lookback_percent=20.0,
        backtest_depth_percent=2.0, # ~50 extrageri din 2500
        filter_consecutives=True,
        max_variants=10, # Limitam variantele pentru viteza
        simulation_step=5 # Sărim peste unele extrageri pentru a termina mai repede
    )
    
    if not results:
        print("Nu s-au generat rezultate.")
        return

    total_hits = sum(r.hits for r in results)
    avg_hits = total_hits / len(results)
    
    print("\n" + "="*40)
    print(f"REZULTATE BASELINE (TimesFM v2)")
    print(f"Simulări efectuate: {len(results)}")
    print(f"Media Hits (Max per variantă): {avg_hits:.2f}")
    
    # Afișăm câteva rezultate detaliate
    for i, r in enumerate(results[:5]):
        print(f"Sim {i+1} ({r.target_draw_date}): {r.hits} hits | Pool: {sorted(list(r.predicted_numbers))[:10]}...")

if __name__ == "__main__":
    run_baseline()
