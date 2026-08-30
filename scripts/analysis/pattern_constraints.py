"""Caut TIPARE de tip constrângere pe extrageri reale.

Întrebarea nu e „cât de des se întâmplă X" (aia e combinatorică pură, calculabilă),
ci „știind ce s-a întâmplat în ultimele K extrageri, pot prezice dacă URMĂTOAREA
respectă X?". Doar a doua ar reduce legitim baza de numere.
"""
import sys, csv, math, itertools
from math import comb
sys.path.insert(0,"/home/user/LOTO")

GAMES = {
    "6/49": ("_ISTORIC/loto_6_49.csv", 49, 6),
    "5/40": ("_ISTORIC/loto_5_40.csv", 40, 5),
    "joker": ("_ISTORIC/joker.csv", 45, 5),
}

def load(path, pick):
    out=[]
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                nums=[int(row[f"n{i}"]) for i in range(1, pick+1)]
            except (KeyError, TypeError, ValueError):
                continue
            if len(set(nums))==pick: out.append(sorted(nums))
    return out

def hyper_p(pred_count, N, pick):
    """P(toate cele `pick` numere dintr-o submultime de dimensiune pred_count)."""
    return comb(pred_count, pick)/comb(N, pick) if pred_count>=pick else 0.0

def constraints(N, pick):
    C={}
    for thr in (25, 30, 35, 40, 45):
        if thr < N:
            C[f"toate <= {thr}"] = (lambda d,t=thr: max(d)<=t, sum(1 for x in range(1,N+1) if x<=thr))
    for thr in (5, 10, 15):
        C[f"toate > {thr}"] = (lambda d,t=thr: min(d)>t, sum(1 for x in range(1,N+1) if x>thr))
    C["toate pare"]  = (lambda d: all(x%2==0 for x in d), N//2)
    C["toate impare"]= (lambda d: all(x%2==1 for x in d), (N+1)//2)
    return C

print(f"{'joc':6} {'constrangere':16} {'obs%':>7} {'teoretic%':>10} {'|z|':>6} {'baza':>5} "
      f"{'P(6din baza)':>13}")
print("-"*72)
rows=[]
for g,(path,N,pick) in GAMES.items():
    draws=load(path,pick)
    n=len(draws)
    for name,(fn,base) in constraints(N,pick).items():
        obs=sum(1 for d in draws if fn(d))
        p_th=hyper_p(base,N,pick)
        if p_th<=0: continue
        exp=n*p_th
        z=(obs-exp)/math.sqrt(max(exp*(1-p_th),1e-9))
        rows.append((g,name,obs,n,obs/n,p_th,z,base))
        print(f"{g:6} {name:16} {obs/n*100:6.2f}% {p_th*100:9.2f}% {abs(z):6.2f} {base:5} "
              f"{p_th*100:12.3f}%")
print(f"\n(n: 6/49={len(load(*[GAMES['6/49'][0]],6))}, "
      f"5/40={len(load(GAMES['5/40'][0],5))}, joker={len(load(GAMES['joker'][0],5))})")
