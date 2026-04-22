
import pandas as pd
import os

files = ["input.csv", "loto_6_49.csv"]
for f in files:
    if os.path.exists(f):
        df = pd.read_csv(f)
        # Assuming numbers are in columns n1..n6 or a "numbers" column
        n_cols = [c for c in df.columns if c.lower().startswith('n')]
        
        found = 0
        for _, row in df.iterrows():
            nums = set()
            if n_cols:
                nums = set(row[n_cols].astype(int))
            elif "numbers" in df.columns:
                nums = set(int(x) for x in str(row["numbers"]).split(",") if x.strip().isdigit())
            
            if {4, 5, 6}.issubset(nums):
                found += 1
                print(f"Found 4,5,6 in {f} at row {row.name}")
        
        if found == 0:
            print(f"4,5,6 NOT found in {f}")
        else:
            print(f"4,5,6 found {found} times in {f}")
