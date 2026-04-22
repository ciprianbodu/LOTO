
import pandas as pd
import numpy as np
from loto_engine import LotoEngine
import os

# Create dummy data
df = pd.DataFrame({
    "date": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"],
    "n1": [1, 10, 20, 30],
    "n2": [2, 11, 21, 31],
    "n3": [3, 12, 22, 32],
    "n4": [4, 13, 23, 33],
    "n5": [5, 14, 24, 34],
    "n6": [6, 15, 25, 35]
})
df.to_csv("test_lookback.csv", index=False)

engine = LotoEngine(game_type="6/49")
engine.load_data("test_lookback.csv")

print("--- ALL HISTORY ---")
lines, p10, p90, g_range, context = engine.run_institutional_pipeline(lookback=0)
print(f"Nucleu Dur: {engine.hard_core}")
print(f"Stats: {engine.hard_core_stats}")

print("\n--- LOOKBACK 2 ---")
engine2 = LotoEngine(game_type="6/49")
engine2.load_data("test_lookback.csv")
lines, p10, p90, g_range, context = engine2.run_institutional_pipeline(lookback=2)
print(f"Nucleu Dur: {engine2.hard_core}")
print(f"Stats: {engine2.hard_core_stats}")

os.remove("test_lookback.csv")
