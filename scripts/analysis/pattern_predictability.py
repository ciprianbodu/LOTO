"""Pasul 2: sunt PREZICIBILE? Autocorelatie + test de permutare."""
import sys, csv, math, random
import numpy as np
sys.path.insert(0,"/home/user/LOTO")
random.seed(0); rng=np.random.default_rng(0)
GAMES={"6/49":("_ISTORIC/loto_6_49.csv",49,6),"5/40":("_ISTORIC/loto_5_40.csv",40,5),
       "joker":("_ISTORIC/joker.csv",45,5)}
def load(path,pick):
    out=[]
    for row in csv.DictReader(open(path,encoding="utf-8")):
        try: n=[int(row[f"n{i}"]) for i in range(1,pick+1)]
        except Exception: continue
        if len(set(n))==pick: out.append(sorted(n))
    return out

FEATS={
 "suma":        lambda d: sum(d),
 "maxim":       lambda d: max(d),
 "minim":       lambda d: min(d),
 "amplitudine": lambda d: max(d)-min(d),
 "cate pare":   lambda d: sum(1 for x in d if x%2==0),
 "cate <=mij":  lambda d: None,  # completat per joc
}
print(f"{'joc':6} {'trasatura':13} {'lag1':>7} {'lag2':>7} {'lag3':>7} {'p(perm,lag1)':>13}")
print("-"*60)
worst=[]
for g,(path,N,pick) in GAMES.items():
    draws=load(path,pick)
    feats=dict(FEATS); feats["cate <=mij"]=lambda d,h=N//2: sum(1 for x in d if x<=h)
    for name,fn in feats.items():
        x=np.array([fn(d) for d in draws], dtype=float)
        x=x-x.mean()
        acs=[]
        for lag in (1,2,3):
            a=x[:-lag]; b=x[lag:]
            r=float((a*b).sum()/math.sqrt((a*a).sum()*(b*b).sum()))
            acs.append(r)
        # test de permutare pe lag1: cat de extrem e |r| daca ordinea e aleatoare?
        obs=abs(acs[0]); cnt=0; NPERM=2000
        for _ in range(NPERM):
            p=rng.permutation(x); a=p[:-1]; b=p[1:]
            rr=abs(float((a*b).sum()/math.sqrt((a*a).sum()*(b*b).sum())))
            cnt += rr>=obs
        pval=(cnt+1)/(NPERM+1)
        worst.append((pval,g,name,acs[0]))
        print(f"{g:6} {name:13} {acs[0]:+7.4f} {acs[1]:+7.4f} {acs[2]:+7.4f} {pval:13.4f}")
worst.sort()
print(f"\nCel mai «semnificativ»: {worst[0][1]} / {worst[0][2]} r={worst[0][3]:+.4f} p={worst[0][0]:.4f}")
print(f"Teste facute: {len(worst)}. Sub Bonferroni prag = {0.05/len(worst):.5f}")
print(f"Cate sub 0.05 fara corectie: {sum(1 for w in worst if w[0]<0.05)} (asteptat din hazard: {0.05*len(worst):.1f})")
