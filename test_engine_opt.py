import pandas as pd
from loto_engine import LotoEngine
import time

def test_engine():
    engine = LotoEngine(game_type="5/40")
    success = engine.load_data("_ISTORIC/loto_5_40.csv")  # sursa reală 5/40
    if not success:
        print("Nu pot încărca _ISTORIC/loto_5_40.csv (lipsește?)")
        return
        
    print("Running Institutional Pipeline...")
    start_time = time.time()
    
    # We use pool_size 12, max_variants 0 to see what it generates
    lines, p10, p90, g_range, context, audit = engine.run_institutional_pipeline(
        pool_size=12, 
        guarantee=3, 
        max_variants=50, 
        lookback=0, 
        filter_consecutives=True, 
        smart_reduction=True, 
        ultra_hit_optimization=True
    )
    
    print(f"Elapsed time: {time.time() - start_time:.2f}s")
    print(f"Hard Core (Nucleul Dur): {engine.hard_core}")
    
    # Check post-hoc validation stats
    if 'post_hoc_validation' in audit:
        v = audit['post_hoc_validation']
        print("\nPost-Hoc Validation Stats:")
        print(f"Iterations: {v.get('iterations')}")
        print(f"Original Score: {v.get('original_score')}, Final Score: {v.get('final_score')}")
        print(f"Original Big Hits (4+,5+,6+): {v.get('original_big_hits')}, Final: {v.get('final_big_hits')}")
        print(f"Original Total Matches: {v.get('original_total')}, Final: {v.get('final_total')}")
    else:
        print("\nPost-Hoc Validation did not trigger or make changes.")

if __name__ == "__main__":
    test_engine()