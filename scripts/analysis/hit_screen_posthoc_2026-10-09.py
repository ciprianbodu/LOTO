"""Analize post-hoc, 2026-10-09, dupa ecranul hit_screen_2026-10-09.py.

NU fac parte din protocol si nu pot schimba verdictul lui. Descompun cele doua
rezultate care ies in evidenta, ca sa se vada de unde vin:

1. 6/49, pool 16, 4+, scorul x540_win50 (frecventa numerelor la 5/40 in ultimele
   50 de zile): cat explica faptul ca pool-ul ramane in 1..40, cum se imparte
   pe jumatatile confirmarii, ce da frecventa proprie 6/49 si sensul invers.
2. Joker Urna 1, pool 16, 4+: plafonul retrospectiv (p = 0.003). Pool-ul ales pe
   inceputul istoricului si testat pe rest, la doua puncte de taiere, si
   frecventa cauzala pe perioade de trei ani.
"""

from __future__ import annotations

import importlib.util
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("hit_screen_20261009", HERE / "hit_screen_2026-10-09.py")
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)
ps = hs.ps

K, T = 16, 4


def tail(N: int, d: int) -> float:
    return sum(comb(K, h) * comb(N - K, d - h) for h in range(T, min(K, d) + 1)) / comb(N, d)


def cross_649() -> None:
    d649, d540 = hs.load_game("ro_649"), hs.load_game("ro_540")
    m = ps.masks(d649)
    h, _tie = ps.evaluate(hs.source_state(d540, d649, "win", 50), d649, (K,))[K]
    print("1. 6/49 pool 16 4+, x540_win50 (aleator 7.96%)")
    for part in ("dev", "conf"):
        mk = m[part]
        print(f"   {part}: {(h[mk] >= T).sum()}/{mk.sum()} = {100 * (h[mk] >= T).mean():.2f}%")
        low = (d649.draws[mk] <= 40).sum(1)
        restricted = np.mean([sum(comb(x, j) * comb(40 - x, K - j) for j in range(T, min(x, K) + 1)) / comb(40, K)
                              for x in low])
        print(f"      pool aleator de 16 din 1..40: {100 * restricted:.2f}%")
    conf = np.flatnonzero(m["conf"])
    half = len(conf) // 2
    for name, idx in (("prima jumatate a confirmarii", conf[:half]), ("a doua", conf[half:])):
        print(f"   {name}: {100 * (h[idx] >= T).mean():.2f}% (n={len(idx)})")
    own, _ = ps.evaluate(ps.window(ps.cumsum0(d649.C), 50), d649, (K,))[K]
    print(f"   frecventa proprie 6/49 pe 50 de zile, confirmare: {100 * (own[m['conf']] >= T).mean():.2f}%")
    m5 = ps.masks(d540)
    h5, _ = ps.evaluate(hs.source_state(d649, d540, "win", 50), d540, (K,))[K]
    print(f"   invers, x649_win50 la 5/40, confirmare: {100 * (h5[m5['conf']] >= T).mean():.2f}% (aleator 16.03%)")


def joker_frequency() -> None:
    d = hs.load_game("ro_joker1")
    N, rows = d.N, len(d.draws)
    base = tail(N, d.d)

    def pool_from(dr):
        c = np.bincount(dr.ravel() - 1, minlength=N)
        return np.lexsort((-np.arange(N), -c))[:K]

    def rate(dr, pool):
        return float((np.isin(dr - 1, pool).sum(1) >= T).mean())

    print(f"2. Joker Urna 1 pool 16 4+ (aleator {100 * base:.2f}%)")
    for frac in (0.5, 0.7):
        cut = int(rows * frac)
        pool = pool_from(d.draws[:cut])
        n = rows - cut
        ev = int(round(rate(d.draws[cut:], pool) * n))
        p = binomtest(ev, n, base, alternative="greater").pvalue
        print(f"   pool din primele {int(100 * frac)}%, testat pe rest: {ev}/{n} = {100 * ev / n:.2f}% (p={p:.3f})")
    CS = ps.cumsum0(d.C)
    h, _ = ps.evaluate(CS[:-1], d, (K,))[K]
    m = ps.masks(d)
    for part in ("dev", "conf"):
        print(f"   frecventa cauzala pe tot trecutul, {part}: {100 * (h[m[part]] >= T).mean():.2f}%")
    yrs = pd.DatetimeIndex(d.day_dates[d.day]).year.to_numpy()
    for y0 in range(int(yrs.min()), int(yrs.max()) + 1, 3):
        mk = (yrs >= y0) & (yrs < y0 + 3) & m["all"]
        if mk.sum():
            print(f"      {y0}-{y0 + 2}: {100 * (h[mk] >= T).mean():.2f}% (n={mk.sum()})")


if __name__ == "__main__":
    cross_649()
    joker_frequency()
