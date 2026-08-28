"""Căutare ESCALADATĂ de pattern cu 3+ hits, per joc, fereastră 10%→100%.

Pentru fiecare joc și fiecare fereastră (ultimele 10%, 20%, …, 100%):
  - walk-forward ONEST: la fiecare extragere t din fereastră, prezic top-K
    numere „hot" (cele mai frecvente din TOT trecutul < t) și măsor câte
    nimeresc din extragerea reală t.
  - compar rata de 3+ hits a pool-ului HOT cu rata ALEATOARE (hipergeometric)
    + z-test pe media hit-urilor.
  - „Pattern găsit" = HOT bate ALEATORUL semnificativ (p < prag Bonferroni)
    ȘI rata 3+ e practic mai mare.

Dacă la 10% nu există pattern, lărgesc fereastra cu 10% ș.a.m.d. până la 100%.
K = draw_n (6/49→6, 5/40→5 [Categoria I = primele 5], joker→5).
"""
from __future__ import annotations

import sys
import numpy as np
import pandas as pd
from scipy import stats

DATADIR = sys.argv[1] if len(sys.argv) > 1 else "_ISTORIC"

GAMES = [
    ("6/49", "loto_6_49.csv", 49, 6),
    ("5/40", "loto_5_40.csv", 40, 5),   # 5/40: primele 5 extrase = Categoria I
    ("joker", "joker.csv", 45, 5),
]
WINDOWS = list(range(10, 101, 10))
ALPHA = 0.05
N_TESTS = len(GAMES) * len(WINDOWS)          # 30 teste → corecție Bonferroni
ALPHA_BONF = ALPHA / N_TESTS


def hyper_mean_std(N, K):
    """Hypergeometric: K extrase din N, K 'succese' (numerele extrase)."""
    mean = K * K / N
    var = K * (K / N) * ((N - K) / N) * ((N - K) / (N - 1))
    return mean, np.sqrt(var)


def hyper_p3plus(N, K):
    """P(≥3 hits) pt un bilet aleator de K numere vs K extrase din N."""
    rv = stats.hypergeom(N, K, K)
    return float(rv.sf(2))  # P(X >= 3)


def walkforward_hot(arr, N, K, start_idx):
    """De la start_idx încolo: pool = top-K hot din extragerile < t. Întoarce hits."""
    freq = np.zeros(N + 1)
    for i in range(start_idx):
        for v in arr[i]:
            if 1 <= v <= N:
                freq[v] += 1
    hits = []
    for t in range(start_idx, len(arr)):
        topK = set(int(x) for x in (np.argsort(freq[1:])[::-1][:K] + 1))
        actual = set(int(x) for x in arr[t])
        hits.append(len(topK & actual))
        for v in arr[t]:
            if 1 <= v <= N:
                freq[v] += 1
    return np.array(hits)


print("=" * 74)
print("CĂUTARE ESCALADATĂ PATTERN 3+ HITS (walk-forward onest, hot vs aleator)")
print(f"Corecție Bonferroni: {N_TESTS} teste → prag p < {ALPHA_BONF:.4f}")
print("=" * 74)

for game, fname, N, K in GAMES:
    path = f"{DATADIR}/{fname}"
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        print(f"\n### {game}: lipsește {path} — sar peste")
        continue
    cols = [c for c in ["n1", "n2", "n3", "n4", "n5", "n6"] if c in df.columns][:K]
    arr = df[cols].to_numpy(dtype=int)
    n_total = len(arr)
    mu0, sd0 = hyper_mean_std(N, K)
    p3_rand = hyper_p3plus(N, K)

    print(f"\n### {game}  (K={K}/{N}, {n_total} extrageri)")
    print(f"    Aleator: medie hits/bilet={mu0:.3f} | rată 3+ = {100*p3_rand:.2f}%")
    found = False
    for w in WINDOWS:
        start = int(round(n_total * (100 - w) / 100.0))
        start = max(start, 60)  # min antrenament
        hits = walkforward_hot(arr, N, K, start)
        n = len(hits)
        if n < 20:
            continue
        mean_h = hits.mean()
        rate3 = float((hits >= 3).mean())
        # z-test: media hot vs media aleatoare (SE = sd0/sqrt(n))
        z = (mean_h - mu0) / (sd0 / np.sqrt(n))
        p = float(stats.norm.sf(z))  # one-sided (hot > aleator?)
        beats = (p < ALPHA_BONF) and (rate3 > p3_rand)
        flag = "  ⚠️ PATTERN" if beats else ""
        print(f"    ultimele {w:3d}% ({n:4d} extr.): hot medie={mean_h:.3f} "
              f"(aleator {mu0:.3f}) | 3+ hot={100*rate3:.2f}% (aleator {100*p3_rand:.2f}%) "
              f"| z={z:+.2f} p={p:.3f}{flag}")
        if beats:
            found = True
            print(f"    → PATTERN GĂSIT la fereastra {w}% — hot bate aleatorul semnificativ.")
            break
    if not found:
        print(f"    → NICIUN pattern 3+ peste prag până la 100%. "
              f"Hot ≈ aleator pe toate ferestrele (loterie aleatoare).")

print("\n" + "=" * 74)
