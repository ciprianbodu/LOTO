"""Pasul 3: chiar daca ai reduce baza, ce se intampla cu rata de 3+?"""
import sys, csv, random
sys.path.insert(0,"/home/user/LOTO")
random.seed(11)
draws=[]
for row in csv.DictReader(open("_ISTORIC/loto_6_49.csv",encoding="utf-8")):
    try: n=[int(row[f"n{i}"]) for i in range(1,7)]
    except Exception: continue
    if len(set(n))==6: draws.append(set(n))
K=10; TRIALS=400
def rate(base):
    tot=hit=0
    for _ in range(TRIALS):
        pool=set(random.sample(base,K))
        for d in draws:
            tot+=1
            if len(pool & d)>=3: hit+=1
    return hit/tot
full=list(range(1,50))
for label, base in [("univers complet (1-49)", full),
                    ("baza redusa: <=40", [x for x in full if x<=40]),
                    ("baza redusa: <=35", [x for x in full if x<=35]),
                    ("baza redusa: doar pare", [x for x in full if x%2==0]),
                    ("baza redusa: >10", [x for x in full if x>10])]:
    print(f"{label:26} baza={len(base):2}  rata 3+ pe pool de {K} = {rate(base)*100:.2f}%")
print(f"\nteoretic hipergeometric P(>=3 | pool 10, 6/49) = 9.03%  — depinde DOAR de K")
