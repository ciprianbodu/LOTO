"""
Test de integrare pentru metodele avansate și meta-learner.
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

print("=== TEST INTEGRARE METODE AVANSATE ===")

# Test 1: Import module (ML modules removed - using mathematical approach only)
try:
    from loto_engine import LotoEngine
    print("[OK] Import LotoEngine reusit (approach matematic)")
    print("     Module ML au fost eliminate - folosire abordare combinatorială")
except Exception as e:
    print(f"[FAIL] Eroare import LotoEngine: {e}")
    raise SystemExit(1) from e

# Test 2: CSV minimal + LotoEngine
print("\nTest LotoEngine...")
rng = np.random.default_rng(7)
rows = []
for _ in range(80):
    nums = sorted(rng.choice(np.arange(1, 50), size=6, replace=False).tolist())
    rows.append({f"n{i+1}": nums[i] for i in range(6)})
demo = pd.DataFrame(rows)
with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as tmp:
    demo.to_csv(tmp.name, index=False)
    tmp_path = tmp.name

try:
    engine = LotoEngine(game_type="6/49")
    engine.load_data(tmp_path)
    
    # Test frequency analysis
    freq = engine.analyze_frequency()
    assert len(freq) == 49
    print(f"   [OK] Frequency analysis (non-zero numbers: {np.count_nonzero(freq)})")
    
    # Test scoring system (blacklist-ul regresiv a fost eliminat complet)
    timesfm_scores = engine._get_timesfm_scores()
    print(f"   [OK] Scoring: Scores={len(timesfm_scores)}")
    
    # Test pipeline
    lines, p10, p90, g_range, context, audit = engine.run_institutional_pipeline(
        pool_size=9, guarantee=4, max_variants=5, smart_reduction=True, sim_depth_pct=100
    )
    assert len(lines) > 0
    print(f"   [OK] Pipeline completat: {len(lines)} variante generate")
    
finally:
    Path(tmp_path).unlink(missing_ok=True)

print("\n" + "=" * 50)
print("TEST INTEGRARE COMPLETAT CU SUCCES!")
print("Abordare matematică + Sistem Inteligent de Reducție")
print("=" * 50)
