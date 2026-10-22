"""Upper bounds (repo formula and tightened) for v = 16..20 at the user's budgets,
plus the pool's hypergeometric rate. Writes bounds_table.json."""
import json, sys
from math import comb
sys.path.insert(0, '.')
import lib_exact as L
rows = []
for game, budgets in (("6/49", (100, 112, 152, 200)), ("5/40", (348, 404)), ("joker", (404,))):
    pick, d, N = L.GAMES[game]
    tot = comb(N, d)
    for b in budgets:
        for v in range(16, 21):
            pool = L.pool_numerators(v, d, N)
            r = {"game": game, "budget": b, "v": v}
            for t in range(3, min(pick, d) + 1):
                rep = L.ub_repo(v, b, pick, t, d, N)
                num, terms = L.ub_tight(v, b, pick, t, d, N, return_terms=True)
                r[f"pool{t}"] = pool[t] / tot
                r[f"ub_repo{t}"] = rep
                r[f"ub_tight{t}"] = num / tot
                r[f"ub_tight_num{t}"] = num
                r[f"D{t}cap"] = L.derived_cover_cap(v, b, pick, t)
                r[f"terms{t}"] = terms
            rows.append(r)
            print(f"{game:5} b={b:3} v={v}: " + "  ".join(
                f"t{t}: pool {r[f'pool{t}']*100:8.4f} ubR {r[f'ub_repo{t}']*100:8.4f} ubT {r[f'ub_tight{t}']*100:8.4f}"
                for t in range(3, min(pick, d) + 1)))
json.dump(rows, open("bounds_table.json", "w"), indent=0)
