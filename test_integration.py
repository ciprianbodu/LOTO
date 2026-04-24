"""
Test de integrare pentru metodele avansate și meta-learner.
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

print("=== TEST INTEGRARE METODE AVANSATE ===")

# Test 1: Import module
try:
    from loto_meta_learner import LotoMetaLearner, ADVANCED_METHODS_AVAILABLE
    from loto_advanced_methods import LotoAdvancedMethods, check_library_availability

    print("[OK] Import reusit")
    print(f"     Module avansate disponibile: {ADVANCED_METHODS_AVAILABLE}")
except Exception as e:
    print(f"[FAIL] Eroare import: {e}")
    raise SystemExit(1) from e

# Test 2: Verificare librării
if ADVANCED_METHODS_AVAILABLE:
    status = check_library_availability()
    print(f"\nLibrarii disponibile: {sum(status.values())}/{len(status)}")
    for lib, avail in status.items():
        icon = "Y" if avail else "N"
        print(f"   [{icon}] {lib}")

# Test 3: CSV minimal + LotoMetaLearner
print("\nTest LotoMetaLearner...")
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
    learner = LotoMetaLearner(tmp_path, game_type="6/49")
    learner.pas_2_meta_learner()
    learner.pas_3_index_ensemble()
    assert learner.ensemble_index is not None
    assert float(np.min(learner.ensemble_index)) >= 0.0
    print(f"   [OK] Meta-learner (ensemble len={len(learner.ensemble_index)})")
finally:
    Path(tmp_path).unlink(missing_ok=True)

# Test 4: LotoAdvancedMethods (enterprise via shim)
print("\nTest LotoAdvancedMethods...")
advanced = LotoAdvancedMethods(max_n=49, draw_count=6)
scores, desc = advanced.neural_network_predictor(demo, [f"n{i}" for i in range(1, 7)])
assert scores.shape == (49,)
print(f"   [OK] neural_network_predictor: {desc} (min={scores.min():.3f}, max={scores.max():.3f})")

print("\n" + "=" * 50)
print("TEST INTEGRARE COMPLETAT CU SUCCES!")
print("=" * 50)
