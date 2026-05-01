"""Quick validation of new logic — bypasses sync lag."""
from __future__ import annotations
import time
import numpy as np
import pandas as pd
from itertools import combinations
from collections import Counter

CSV = "/sessions/charming-trusting-cerf/mnt/_LOTO/input.csv"

def emp_decades(dm, max_num, lookback=400):
    nd = (max_num + 9) // 10
    if dm is None or dm.size == 0: return [1.0/nd]*nd
    n = min(lookback, dm.shape[0])
    rec = dm[-n:]
    counts = np.zeros(nd, dtype=np.float64); total = 0
    for row in rec:
        for v in row:
            iv = int(v)
            if 1 <= iv <= max_num:
                idx = (iv-1)//10
                if idx < nd:
                    counts[idx] += 1; total += 1
    return (counts/total).tolist() if total else [1.0/nd]*nd

def emp_parity(dm, lookback=400):
    if dm is None or dm.size==0: return 0.5
    n = min(lookback, dm.shape[0])
    rec = dm[-n:]
    even = total = 0
    for row in rec:
        for v in row:
            iv = int(v)
            if iv > 0:
                if iv % 2 == 0: even += 1
                total += 1
    return even/total if total else 0.5

def trip_old(dm, num_map, lookback=200):
    if dm is None or dm.size==0: return {n: 0.0 for n in num_map}
    n_d = min(lookback, dm.shape[0])
    rec = dm[-n_d:]
    sets = [set(row) for row in rec]
    sc = {n: 0.0 for n in num_map}
    top = num_map[:30] if len(num_map) > 30 else num_map
    for a,b,c in combinations(top, 3):
        ts = {a,b,c}
        cnt = sum(1 for s in sets if ts.issubset(s))
        if cnt >= 2:
            w = cnt/n_d
            sc[a] += w; sc[b] += w; sc[c] += w
    m = max(sc.values()) if sc else 1.0
    if m > 0: sc = {k: v/m for k,v in sc.items()}
    return sc

def trip_new(dm, num_map, lookback=200):
    if dm is None or dm.size==0: return {n: 0.0 for n in num_map}
    n_d = min(lookback, dm.shape[0])
    rec = dm[-n_d:]
    nset = set(num_map)
    counts = Counter()
    for row in rec:
        nums = sorted(set(int(v) for v in row if int(v)>0) & nset)
        if len(nums) < 3: continue
        for t in combinations(nums, 3):
            counts[t] += 1
    sc = {n: 0.0 for n in num_map}
    for t, c in counts.items():
        if c >= 2:
            w = c/max(n_d,1)
            for a in t: sc[a] += w
    m = max(sc.values()) if sc else 1.0
    if m > 0: sc = {k: v/m for k,v in sc.items()}
    return sc

def quad(dm, num_map, lookback=350):
    if dm is None or dm.size==0: return {n: 0.0 for n in num_map}
    dn = dm.shape[1] if dm.ndim>=2 else 6
    if dn < 4: return {n: 0.0 for n in num_map}
    n_d = min(lookback, dm.shape[0])
    rec = dm[-n_d:]
    nset = set(num_map)
    counts = Counter()
    for row in rec:
        nums = sorted(set(int(v) for v in row if int(v)>0) & nset)
        if len(nums) < 4: continue
        for q in combinations(nums, 4):
            counts[q] += 1
    sc = {n: 0.0 for n in num_map}
    for q, c in counts.items():
        w = (c**1.5)/max(n_d,1)
        for a in q: sc[a] += w
    m = max(sc.values()) if sc else 0.0
    if m > 0: sc = {k: v/m for k,v in sc.items()}
    return sc

def proxy_scores(dm, max_num, recent_window=50):
    """Vectorized."""
    if dm is None or dm.size==0: return {n: 0.0 for n in range(1, max_num+1)}
    R = dm.shape[0]
    pres = np.zeros((R, max_num), dtype=np.float32)
    for col in range(dm.shape[1]):
        v = dm[:, col]
        ok = (v>=1) & (v<=max_num)
        idx = np.where(ok)[0]
        pres[idx, v[idx]-1] = 1.0
    hf = pres.mean(axis=0)
    rf = pres[-min(recent_window,R):].mean(axis=0)
    vrf = pres[-min(15,R):].mean(axis=0)
    last = np.full(max_num, float(R), dtype=np.float32)
    for ni in range(max_num):
        nz = np.where(pres[:, ni] > 0.5)[0]
        if len(nz): last[ni] = R-1-nz[-1]
    gap = 1.0/(1.0 + last/10.0)
    raw = 0.4*rf + 0.25*vrf + 0.2*hf + 0.15*gap
    vmin, vmax = float(raw.min()), float(raw.max())
    rng = max(vmax-vmin, 1e-6)
    norm = (raw-vmin)/rng
    return {n+1: float(norm[n]) for n in range(max_num)}

def pool_old(scores, ps, bl, max_num):
    valid = {n: s for n, s in scores.items() if n not in bl}
    sorted_n = sorted(valid.items(), key=lambda x: -x[1])
    third = max_num/3.0
    zones = {"lo":[], "mi":[], "hi":[]}
    for n,s in sorted_n:
        if n <= third: zones["lo"].append((n,s))
        elif n <= 2*third: zones["mi"].append((n,s))
        else: zones["hi"].append((n,s))
    mpz = max(1, ps//4) if ps>=4 else 0
    pool, used = [], set()
    for z in ["lo","mi","hi"]:
        for n,s in zones[z][:mpz]:
            if n not in used and len(pool)<ps:
                pool.append(n); used.add(n)
    for n,s in sorted_n:
        if len(pool)>=ps: break
        if n not in used: pool.append(n); used.add(n)
    return sorted(pool[:ps])

def pool_new(scores, ps, bl, max_num, dm):
    valid = {n: s for n,s in scores.items() if n not in bl}
    sorted_n = sorted(valid.items(), key=lambda x: -x[1])
    decs = emp_decades(dm, max_num, 400)
    pe = emp_parity(dm, 400)
    nd = len(decs)
    rq = [p*ps for p in decs]
    tq = [int(round(q)) for q in rq]
    for i,p in enumerate(decs):
        if p>=0.08 and tq[i]<1: tq[i]=1
    diff = sum(tq) - ps
    while diff != 0:
        if diff > 0:
            i = int(np.argmax(tq)); tq[i] = max(0,tq[i]-1); diff -= 1
        else:
            sc = [decs[i]*ps - tq[i] for i in range(nd)]
            i = int(np.argmax(sc)); tq[i] += 1; diff += 1
    pool, used = [], set()
    df = [0]*nd
    for n,_ in sorted_n:
        if len(pool)>=ps: break
        if n in used: continue
        di = (int(n)-1)//10
        if di>=nd: continue
        if df[di]<tq[di]:
            pool.append(n); used.add(n); df[di]+=1
    if len(pool)<ps:
        rem = [(n,s) for n,s in sorted_n if n not in used]
        ec = sum(1 for n in pool if n%2==0)
        for n,_ in rem:
            if len(pool)>=ps: break
            tot = max(len(pool),1)
            ce = ec/tot
            ie = (n%2==0)
            if abs(ce-pe)>0.10:
                nm = ce<pe
                if nm and not ie: continue
                if (not nm) and ie: continue
            pool.append(n); used.add(n)
            if ie: ec+=1
    if len(pool)<ps:
        for n,_ in sorted_n:
            if len(pool)>=ps: break
            if n not in used: pool.append(n); used.add(n)
    return sorted(pool[:ps])

def walk(dm, max_num, ps, n_eval, selector_name, use_boost=False):
    R = dm.shape[0]
    start = max(80, R-n_eval)
    hits = []
    for i in range(start, R):
        h = dm[:i]
        sc = proxy_scores(h, max_num)
        if use_boost:
            top40 = [n for n,_ in sorted(sc.items(), key=lambda x: -x[1])[:40]]
            tb = trip_new(h, top40, 220)
            qb = quad(h, top40, 320)
            for n in sc:
                sc[n] *= (0.70 + 0.30*tb.get(n,0.0) + 0.18*qb.get(n,0.0))
        if selector_name == "old":
            pool = pool_old(sc, ps, set(), max_num)
        else:
            pool = pool_new(sc, ps, set(), max_num, h)
        actual = set(int(v) for v in dm[i] if v>0)
        hits.append(len(set(pool) & actual))
    return hits

print("="*70)
print("VALIDARE OPTIMIZARI MOTOR LOTO — pe input.csv")
print("="*70)
df = pd.read_csv(CSV)
print(f"Date: {len(df)} extrageri ({df['date'].iloc[0]} -> {df['date'].iloc[-1]})")
dm = df[["n1","n2","n3","n4","n5"]].to_numpy(dtype=np.int32)
max_num = 45
print(f"draw_matrix: {dm.shape}, max_num={max_num} (Joker urna 1)")

print("\n--- Distributie empirica decade ---")
decs = emp_decades(dm, max_num, 400)
for i,p in enumerate(decs):
    a, b = i*10+1, min((i+1)*10, max_num)
    print(f"  {a:2d}-{b:2d}: {p:.3%}")
print(f"\nParitate empirica (procent pare): {emp_parity(dm,400):.3%}")

print("\n--- Triplet OLD vs NEW timing pe top-40 ---")
nm = list(range(1,41))
t0 = time.perf_counter(); _ = trip_old(dm, nm, 250); old_ms = (time.perf_counter()-t0)*1000
t0 = time.perf_counter(); _ = trip_new(dm, nm, 250); new_ms = (time.perf_counter()-t0)*1000
print(f"  OLD (top-30 limit, C(30,3)=4060): {old_ms:.1f} ms")
print(f"  NEW (top-40 full, observed):     {new_ms:.1f} ms")

print("\n--- Walk-forward backtest pool selection ---")
ps, ne = 15, 100
print(f"  pool_size={ps}, n_eval={ne}")
hold = walk(dm, max_num, ps, ne, "old")
hnde = walk(dm, max_num, ps, ne, "new")
hful = walk(dm, max_num, ps, ne, "new", use_boost=True)
def stat(h, label):
    h = np.array(h)
    print(f"  [{label}] n={len(h)}  avg={h.mean():.3f}  median={np.median(h):.1f}  max={h.max()}")
    print(f"          hits>=2: {(h>=2).sum()}  hits>=3: {(h>=3).sum()}  hits>=4: {(h>=4).sum()}  hits>=5: {(h>=5).sum()}")
    print(f"          dist: {dict(sorted(Counter(h.tolist()).items()))}")
stat(hold, "OLD zone")
stat(hnde, "NEW decade")
stat(hful, "NEW + triplet+quad")

print("\n--- DELTA ---")
old_arr = np.array(hold); nde_arr = np.array(hnde); ful_arr = np.array(hful)
print(f"  Delta avg (decade vs old):       {nde_arr.mean()-old_arr.mean():+.3f}  ({(nde_arr.mean()-old_arr.mean())/max(old_arr.mean(),1e-6)*100:+.2f}%)")
print(f"  Delta avg (decade+boost vs old): {ful_arr.mean()-old_arr.mean():+.3f}  ({(ful_arr.mean()-old_arr.mean())/max(old_arr.mean(),1e-6)*100:+.2f}%)")
print(f"  Delta hits>=3 (decade): {(nde_arr>=3).sum()-(old_arr>=3).sum():+d}  full: {(ful_arr>=3).sum()-(old_arr>=3).sum():+d}")
print(f"  Delta hits>=4 (decade): {(nde_arr>=4).sum()-(old_arr>=4).sum():+d}  full: {(ful_arr>=4).sum()-(old_arr>=4).sum():+d}")
print(f"  Delta hits>=5 (decade): {(nde_arr>=5).sum()-(old_arr>=5).sum():+d}  full: {(ful_arr>=5).sum()-(old_arr>=5).sum():+d}")
print("="*70)
