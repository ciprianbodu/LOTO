"""Evaluare walk-forward rapidă a valului 2026-11-08 (bayes/hedge) pe ținta 5+.

Nu înlocuiește Re-Bench-ul oficial (poarta cu Holm rămâne acolo); oferă un
prim semnal pe fereastra recentă (ultimele 30% din extrageri, ca în studiul
LR), la poolurile de producție 16/15/19, față de `frequency`, `random` și
baseline-ul hipergeometric exact. Pool = top-N canonic din scoruri (fără
limită de consecutive), ca în motor.
"""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from loto_enterprise.benchmark.methods import call_method  # noqa: E402

GAMES = {
    "loto_6_49": ("loto_6_49.csv", ["n1","n2","n3","n4","n5","n6"], 49, 6, 16),
    "loto_5_40": ("loto_5_40.csv", ["n1","n2","n3","n4","n5","n6"], 40, 6, 19),
    "joker_urna1": ("joker.csv", ["n1","n2","n3","n4","n5"], 45, 5, 15),
}
METHODS = ["frequency", "random", "bayes_dirichlet_forget", "hedge_experts_mixture"]
WARMUP = 200  # ca în studiul LR: scorurile sub 200 de extrageri nu se judecă


def hyper_geom_5plus(v, d, k, target=5):
    """P(cel puțin `target` din cele d extrase se află în poolul de k din v)."""
    top = min(d, k)
    return sum(comb(k, i) * comb(v - k, d - i) for i in range(target, top + 1)) / comb(v, d)


def evaluate(game_key, method):
    csv, cols, max_num, draw_n, pool_k = GAMES[game_key]
    df = pd.read_csv(ROOT / "_ISTORIC" / csv)
    draws = df[cols].to_numpy(dtype=np.int64)
    n = len(draws)
    start = max(WARMUP, int(np.ceil(n * 0.7)))  # ultimele 30%, cu warmup
    hits5 = hits6 = tot = 0
    for t in range(start, n):
        scores, _ = call_method(method, draws[:t], max_num)
        ranked = sorted(scores, key=lambda x: (-scores[x], x))
        pool = set(ranked[:pool_k])
        h = len(pool & set(draws[t]))
        hits5 += h >= 5
        hits6 += h >= 6
        tot += 1
    return hits5, hits6, tot


def main():
    print(f"{'joc':<12} {'metodă':<24} {'5+':>6} {'6+':>4} {'n':>5} {'rată5+':>9} {'baseline':>9}")
    for game in GAMES:
        *_, max_num, draw_n, pool_k = (None, None, *GAMES[game][2:])
        base5 = hyper_geom_5plus(max_num, draw_n, pool_k, 5)
        for m in METHODS:
            h5, h6, tot = evaluate(game, m)
            print(f"{game:<12} {m:<24} {h5:>6} {h6:>4} {tot:>5} {h5/tot:>9.4f} {base5:>9.4f}")


if __name__ == "__main__":
    main()
