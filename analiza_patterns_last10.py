"""Analiză de patternuri pe ultimele 10% din istoricul extragerilor.

Algoritmi PROPRII (frecvență, gap, sumă, paritate, consecutive, carryover,
perechi, Markov, autocorelație) + TESTE DE SEMNIFICAȚIE (chi-square,
Monte-Carlo) ca să distingem un pattern REAL de zgomot statistic.

Loteria e aleatoare → ipoteza nulă e uniformitatea. Raportăm doar abaterile
care depășesc ce ar produce hazardul.
"""
from __future__ import annotations

import sys
from collections import Counter
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats

CSV = sys.argv[1] if len(sys.argv) > 1 else "_ISTORIC/joker.csv"
PCT = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

df = pd.read_csv(CSV)
# Detecție DIN DATE (nu din nume): K = coloane de numere completate, MAXN = max real.
# (Mai robust — ex. fișierul 5/40 are 6 coloane completate, range 1-40.)
main_cols = [c for c in ["n1", "n2", "n3", "n4", "n5", "n6"] if c in df.columns and df[c].notna().any()]
has_joker = "joker" in df.columns
K = len(main_cols)  # numere extrase / extragere (din date)
MAXN = int(df[main_cols].max().max())  # universul real observat

n_total = len(df)
cut = int(round(n_total * (100 - PCT) / 100.0))
recent = df.iloc[cut:].reset_index(drop=True)
n_rec = len(recent)

draws = recent[main_cols].to_numpy(dtype=int)
all_draws = df[main_cols].to_numpy(dtype=int)

print("=" * 70)
print(f"ANALIZĂ PATTERNURI — ultimele {PCT:.0f}% ({n_rec} din {n_total} extrageri)")
print(f"Joc: {K}/{MAXN}" + (" + Joker(1-20)" if has_joker else ""))
print(f"Perioada: {recent.iloc[0,0]} → {recent.iloc[-1,0]}")
print("=" * 70)


def chi2_uniform(counts, n_cat, n_obs):
    exp = n_obs / n_cat
    chi2 = float(((counts - exp) ** 2 / exp).sum())
    dfree = n_cat - 1
    p = float(stats.chi2.sf(chi2, dfree))
    return chi2, dfree, p, exp


# ---------------------------------------------------------------------------
# 1. FRECVENȚĂ + chi-square goodness-of-fit vs uniform
# ---------------------------------------------------------------------------
print("\n[1] FRECVENȚĂ NUMERE (hot/cold) + test uniformitate")
flat = draws.flatten()
cnt = np.zeros(MAXN + 1, dtype=int)
for v in flat:
    if 1 <= v <= MAXN:
        cnt[v] += 1
counts = cnt[1:]
chi2, dfree, p, exp = chi2_uniform(counts, MAXN, len(flat))
print(f"  Așteptat uniform: {exp:.1f} apariții/număr | χ²={chi2:.1f} (df={dfree}) p={p:.3f}")
if p < 0.05:
    print(f"  ⚠️  ABATERE SEMNIFICATIVĂ de la uniform (p<0.05) — merită investigat")
else:
    print(f"  ✅ Consistent cu hazardul (p≥0.05) — fără bias real de frecvență")
order = np.argsort(counts)[::-1]
hot = [(i + 1, counts[i]) for i in order[:8]]
cold = [(i + 1, counts[i]) for i in order[-8:]]
print(f"  HOT : {', '.join(f'{n}({c})' for n,c in hot)}")
print(f"  COLD: {', '.join(f'{n}({c})' for n,c in cold)}")

# ---------------------------------------------------------------------------
# 2. GAP / RECENȚĂ — câte extrageri de la ultima apariție (overdue)
# ---------------------------------------------------------------------------
print("\n[2] GAP / RECENȚĂ (numere 'datorate')")
last_seen = {n: None for n in range(1, MAXN + 1)}
for idx in range(n_rec):
    for v in draws[idx]:
        last_seen[v] = idx
gaps = {n: (n_rec - 1 - ls) if ls is not None else n_rec for n, ls in last_seen.items()}
exp_gap = MAXN / K  # gap mediu teoretic
overdue = sorted(gaps.items(), key=lambda kv: kv[1], reverse=True)[:8]
print(f"  Gap mediu teoretic ≈ {exp_gap:.1f} extrageri")
print(f"  Cele mai 'datorate': {', '.join(f'{n}(gap {g})' for n,g in overdue)}")
print("  ⚠️  NOTĂ: 'overdue' NU crește șansa — extragerile sunt independente (gambler's fallacy)")

# ---------------------------------------------------------------------------
# 3. SUMA celor K numere — distribuție vs teoretic
# ---------------------------------------------------------------------------
print("\n[3] SUMA numerelor/extragere")
sums = draws.sum(axis=1)
theo_mean = K * (1 + MAXN) / 2
print(f"  Observat: medie={sums.mean():.1f} std={sums.std():.1f} | Teoretic medie={theo_mean:.1f}")
print(f"  Interval p10-p90: {np.percentile(sums,10):.0f} – {np.percentile(sums,90):.0f}")
t, pt = stats.ttest_1samp(sums, theo_mean)
print(f"  t-test vs medie teoretică: p={pt:.3f}" + ("  ⚠️ deviază" if pt < 0.05 else "  ✅ normal"))

# ---------------------------------------------------------------------------
# 4. PARITATE & LOW/HIGH
# ---------------------------------------------------------------------------
print("\n[4] PARITATE (par/impar) & LOW/HIGH")
odd = (draws % 2 == 1).sum(axis=1)
mid = MAXN / 2
low = (draws <= mid).sum(axis=1)
print(f"  Impare/extragere: medie={odd.mean():.2f} (așteptat ≈{K/2:.1f})")
print(f"  Low(≤{mid:.0f})/extragere: medie={low.mean():.2f} (așteptat ≈{K/2:.1f})")
odd_dist = Counter(odd.tolist())
print(f"  Distribuție nr. impare: {dict(sorted(odd_dist.items()))}")

# ---------------------------------------------------------------------------
# 5. CONSECUTIVE — câte extrageri au ≥1 pereche consecutivă
# ---------------------------------------------------------------------------
print("\n[5] NUMERE CONSECUTIVE")
has_consec = 0
for row in draws:
    s = sorted(row)
    if any(s[i + 1] - s[i] == 1 for i in range(len(s) - 1)):
        has_consec += 1
# probabilitate teoretică aproximativă de ≥1 pereche consecutivă
sim = 0
rng = np.random.default_rng(42)
for _ in range(20000):
    s = sorted(rng.choice(np.arange(1, MAXN + 1), size=K, replace=False))
    if any(s[i + 1] - s[i] == 1 for i in range(len(s) - 1)):
        sim += 1
print(f"  Observat: {has_consec}/{n_rec} extrageri ({100*has_consec/n_rec:.1f}%) cu ≥1 pereche consecutivă")
print(f"  Așteptat (Monte-Carlo): {100*sim/20000:.1f}%")

# ---------------------------------------------------------------------------
# 6. CARRYOVER — numere care se repetă de la o extragere la următoarea
# ---------------------------------------------------------------------------
print("\n[6] CARRYOVER (repetare extragere-la-extragere)")
carry = [len(set(draws[i]) & set(draws[i - 1])) for i in range(1, n_rec)]
carry_mean = np.mean(carry)
# teoretic: K * K / MAXN
theo_carry = K * K / MAXN
print(f"  Observat: medie={carry_mean:.2f} numere repetate | Teoretic={theo_carry:.2f}")
cc = Counter(carry)
print(f"  Distribuție: {dict(sorted(cc.items()))}")

# ---------------------------------------------------------------------------
# 7. PERECHI co-apariție (top)
# ---------------------------------------------------------------------------
print("\n[7] PERECHI frecvente (co-apariție)")
pair_cnt = Counter()
for row in draws:
    for a, b in combinations(sorted(set(int(x) for x in row)), 2):
        pair_cnt[(a, b)] += 1
exp_pair = n_rec * (K * (K - 1) / 2) / (MAXN * (MAXN - 1) / 2)
top_pairs = pair_cnt.most_common(8)
print(f"  Așteptat/pereche ≈ {exp_pair:.2f} | Top: " +
      ", ".join(f"{a}-{b}({c})" for (a, b), c in top_pairs))

# ---------------------------------------------------------------------------
# 8. MARKOV / AUTOCORELAȚIE — extragerea t prezice t+1?
# ---------------------------------------------------------------------------
print("\n[8] DEPENDENȚĂ TEMPORALĂ (Markov lag-1)")
# Repetă un număr apărut la t la t+1 mai des decât hazardul?
obs_repeat_rate = carry_mean / K  # fracție din numerele de la t care reapar
print(f"  Rată repetare = {obs_repeat_rate:.3f} (hazard = {K/MAXN:.3f})")
z = (carry_mean - theo_carry) / (np.std(carry) / np.sqrt(len(carry)) + 1e-9)
print(f"  z-score carryover vs hazard: {z:+.2f}" +
      ("  ⚠️ dependență" if abs(z) > 2 else "  ✅ independente"))

# ---------------------------------------------------------------------------
# 9. JOKER (dacă există)
# ---------------------------------------------------------------------------
if has_joker:
    print("\n[9] JOKER (1-20)")
    jk = recent["joker"].to_numpy(dtype=int)
    jc = np.zeros(21, dtype=int)
    for v in jk:
        if 1 <= v <= 20:
            jc[v] += 1
    jchi, jdf, jp, jexp = chi2_uniform(jc[1:], 20, len(jk))
    print(f"  χ²={jchi:.1f} (df={jdf}) p={jp:.3f}" +
          ("  ⚠️ bias" if jp < 0.05 else "  ✅ uniform"))
    jorder = np.argsort(jc[1:])[::-1]
    print(f"  HOT joker: {', '.join(f'{i+1}({jc[i+1]})' for i in jorder[:5])}")

# ---------------------------------------------------------------------------
# CONCLUZIE — verdict global
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("VERDICT GLOBAL")
print("=" * 70)
signals = []
if p < 0.05:
    signals.append(f"frecvență numere (p={p:.3f})")
if pt < 0.05:
    signals.append(f"sumă (p={pt:.3f})")
if abs(z) > 2:
    signals.append(f"carryover temporal (z={z:.2f})")
if has_joker and jp < 0.05:
    signals.append(f"joker (p={jp:.3f})")
if signals:
    print("⚠️  Semnale peste prag (necesită Monte-Carlo de confirmare, pot fi fals-pozitive):")
    for s in signals:
        print(f"     • {s}")
    print("   La 9 teste, ~0.4 fals-pozitive sunt așteptate doar din hazard (Bonferroni).")
else:
    print("✅ NICIUN pattern semnificativ. Ultimele {0} extrageri sunt".format(n_rec))
    print("   indistinctibile de o sursă uniform-aleatoare. Orice 'hot/cold/overdue'")
    print("   e ZGOMOT — nu are putere predictivă. (Confirmă filosofia aplicației.)")
print("=" * 70)
