"""A10 (definit 2026-10-09, dupa rularea de proba, inainte de calcul): bias fizic comun.
Pentru perechile de jocuri romanesti, pe numerele comune: corelatia Pearson intre
numaratorile pe numar ale celor doua jocuri (a) pe tot istoricul comun si (b) media
corelatiilor pe blocuri consecutive de 100 de zile comune. Unilateral (corelatie
pozitiva = bias comun). Nul: 2000 de istorii sintetice iid pentru ambele jocuri."""
import importlib.util, json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hs", HERE / "hit_screen_2026-10-09.py")
hs = importlib.util.module_from_spec(spec); spec.loader.exec_module(hs)
M, BLOCK = 2000, 100

def stats(IbA, IbB, ia, ib, m):
    a, b = IbA[ia, :m], IbB[ib, :m]
    full = np.corrcoef(a.sum(0), b.sum(0))[0, 1]
    cs = []
    for s in range(0, len(ia) - BLOCK + 1, BLOCK):
        x, y = a[s:s+BLOCK].sum(0), b[s:s+BLOCK].sum(0)
        if x.std() > 0 and y.std() > 0:
            cs.append(np.corrcoef(x, y)[0, 1])
    return float(full), float(np.mean(cs)), len(cs)

out = {}
rng = np.random.default_rng(20261009)
for ga, gb in (("ro_649", "ro_540"), ("ro_649", "ro_joker1"), ("ro_540", "ro_joker1")):
    A, B = hs.load_game(ga), hs.load_game(gb)
    m = min(A.N, B.N)
    _c, ia, ib = np.intersect1d(A.day_dates, B.day_dates, return_indices=True)
    full, blk, nb = stats(A.Ib, B.Ib, ia, ib, m)
    sims = []
    for _ in range(M):
        sa = hs.day_indicator(hs.fast_draws(len(A.draws), A.N, A.d, rng), A.day, A.n_days, A.N)
        sb = hs.day_indicator(hs.fast_draws(len(B.draws), B.N, B.d, rng), B.day, B.n_days, B.N)
        sims.append(stats(sa, sb, ia, ib, m)[:2])
    sims = np.array(sims)
    out[f"{ga}~{gb}"] = {
        "common_days": int(len(ia)), "numbers": m, "blocks": nb,
        "corr_full": full, "p_full": float((1 + (sims[:, 0] >= full).sum()) / (M + 1)),
        "corr_blocks_mean": blk, "p_blocks": float((1 + (sims[:, 1] >= blk).sum()) / (M + 1)),
        "null_blocks_mean": float(sims[:, 1].mean()), "null_blocks_sd": float(sims[:, 1].std()),
    }
    print(f"{ga}~{gb}", json.dumps(out[f'{ga}~{gb}']), flush=True)
(HERE / "shared_bias_2026-10-09.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
