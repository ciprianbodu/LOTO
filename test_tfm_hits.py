import os
import sys

# Asigurăm că LotoEngine are acces la tot ce îi trebuie
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from loto_engine import LotoEngine

def main():
    engine = LotoEngine(game_type="6/49")
    success = engine.load_data("_ISTORIC/loto_6_49.csv")  # sursa reală 6/49
    if not success:
        print("Eroare la încărcare _ISTORIC/loto_6_49.csv (lipsește?)")
        return
        
    print(f"Date incarcate: {len(engine.data)} extrageri.")
    
    lines, p10, p90, g_range, context, audit = engine.run_institutional_pipeline(
        progress_cb=None,
        pool_size=9,
        guarantee=4,
        max_variants=16,
        lookback=20, # Folosim ultimele 20% ca in feedback "508 extrageri"
        filter_consecutives=True,
        smart_reduction=True,
        sim_depth_pct=10
    )
    
    print("\n--- REZULTATE ---")
    print(f"Linii generate: {len(lines)}")
    print(f"Audit TimesFM: {audit.get('timesfm_version', 'N/A')}")
    if 'timesfm_predictions' in audit:
        print("Top predictii TimesFM:", audit['timesfm_predictions'])

if __name__ == "__main__":
    main()
