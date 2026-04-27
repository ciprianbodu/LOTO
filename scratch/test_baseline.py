"""Quick baseline measurement of current hit rates."""
import sys, logging
logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, r"d:\_LIBRARIES\OneDrive\_CODING\_LOTO")

import numpy as np
from loto_enterprise.core.backtesting import LotoBacktester

csv_path = r"d:\_LIBRARIES\OneDrive\_CODING\_LOTO\input.csv"

for game in ["joker"]:
    print(f"\n{'='*60}")
    print(f"  BASELINE TEST: {game}")
    print(f"{'='*60}")
    bt = LotoBacktester(csv_path, game)
    results = bt.run_retroactive_backtest(
        pool_size=12, guarantee=4,
        lookback_percent=0, backtest_depth_percent=2,
        max_variants=0, simulation_step=1, use_feedback=True
    )
    if results:
        union_hits = [r.hits_union for r in results]
        max_hits = [r.hits for r in results]
        print(f"  Simulations: {len(results)}")
        print(f"  Avg Union Hits (pool): {np.mean(union_hits):.2f}")
        print(f"  Avg Best Variant Hits: {np.mean(max_hits):.2f}")
        for threshold in [5, 4, 3]:
            count = sum(1 for h in union_hits if h >= threshold)
            print(f"  {threshold}+ union hits: {count}/{len(union_hits)} ({count/len(union_hits)*100:.1f}%)")
        print(f"  Distribution: {dict(sorted({h: union_hits.count(h) for h in set(union_hits)}.items()))}")
