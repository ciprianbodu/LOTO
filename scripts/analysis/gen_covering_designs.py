"""Precalculează coverele C(v, pick, guarantee) o singură dată, cu buget MARE.

De ce: `_ilp_cover_positions` rulează cu `time_limit=15s` și NU demonstrează
niciodată optimalitatea în bugetul ăla — arde tot ceasul și întoarce cea mai bună
soluție găsită. Deci calitatea depinde de încărcarea mașinii în acele 15 secunde:
măsurat, 5/40 pool 12 / g3 a dat 33 de bilete într-o rulare și 30 în alta, pe
ACELAȘI cod și aceleași date. Un cover e o constantă matematică — nu depinde nici
de pool, nici de scoruri, nici de dată. Îl calculăm o dată, îl scriem pe disc, și
de-atunci încolo e și determinist, și mai bun.

Scrie în formatul La Jolla (1-based, un bloc pe linie), deci `_load_lajolla` îl
citește fără cod nou și îl VALIDEAZĂ la 100% acoperire înainte de folosire.
"""
import itertools, os, sys, time
from math import comb
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
import wheeling_methods as wm

BUDGET = float(os.environ.get("GEN_BUDGET", "90"))
OUT = _ROOT / "covering_designs"
OUT.mkdir(parents=True, exist_ok=True)

def coverage(blocks, v, g):
    need = set(itertools.combinations(range(v), g))
    for b in blocks:
        for s in itertools.combinations(sorted(b), g):
            need.discard(s)
    return 100.0 * (1 - len(need)/max(1, comb(v,g)))

rows=[]
# v de la pick (nu pick+1): C(6,6,g) e un singur bilet — fără el, 6/49
# pool 6 cădea pe ILP (nedeterminist), exact gaura pe care o închide setul.
geos=[(v,pick,g) for pick in (5,6) for g in ([3,4] if pick==5 else [3,4,5])
      for v in range(pick, 17)]
print(f"{len(geos)} geometrii, buget {BUDGET}s fiecare\n", flush=True)
for i,(v,pick,g) in enumerate(geos,1):
    pool = list(range(1, v+1))
    # referinta: greedy-ul de azi (ce s-ar folosi fara design)
    t=time.time(); gw,_ = wm._greedy_fallback(pool, pick, g, 0, None); t_greedy=time.time()-t
    n_greedy=len(gw)
    # ILP cu buget mare
    wm._ILP_COVER_CACHE.clear()
    t=time.time(); cover = wm._ilp_cover_positions(v, pick, g, time_limit=BUDGET); t_ilp=time.time()-t
    if cover is not None and coverage(cover, v, g) >= 100.0 and len(cover) < n_greedy:
        blocks = [sorted(x+1 for x in b) for b in cover]
        best, src = blocks, "ILP"
    else:
        blocks = [sorted(b) for b in gw]
        best, src = blocks, "greedy"
    assert coverage([[x-1 for x in b] for b in best], v, g) >= 100.0, f"C({v},{pick},{g}) NU e 100%"
    path = OUT / f"C_{v}_{pick}_{g}.txt"
    existed = path.exists()
    if existed:
        prev = [l.split() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(prev) and len(prev) <= len(best):
            print(f"[{i:2}/{len(geos)}] C({v},{pick},{g}): PASTREZ fisierul existent "
                  f"({len(prev)} <= {len(best)})", flush=True)
            rows.append((v,pick,g,n_greedy,len(prev),"existent")); continue
    with open(path,"w",encoding="utf-8") as fh:
        fh.write("\n".join(" ".join(str(x) for x in b) for b in best) + "\n")
    print(f"[{i:2}/{len(geos)}] C({v},{pick},{g}): greedy={n_greedy:4}  ->  {len(best):4} ({src}) "
          f"{'*** -' + str(n_greedy-len(best)) if len(best)<n_greedy else ''}  [{t_ilp:.0f}s]", flush=True)
    rows.append((v,pick,g,n_greedy,len(best),src))

tot_g=sum(r[3] for r in rows); tot_b=sum(r[4] for r in rows)
print(f"\nTOTAL bilete: greedy {tot_g} -> designuri {tot_b}  ({100*(tot_g-tot_b)/tot_g:.1f}% mai putine)")
