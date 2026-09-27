"""Caracteristicile de operare ale testului preînregistrat, calculate exact.

Fără simulare: programare dinamică pe numărul de reușite după fiecare extragere,
cu aceleași praguri ca `forward_test.Sprt`, cu plafonul de extrageri și cu
testul binomial final pentru un pool nedecis la plafon. Rezultatul e
determinist, deci tabelul din PREREGISTRATION.md se poate reface identic.

Rulare din rădăcina proiectului:
    python scripts/analysis/forward_test/sprt_operating.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.stats import binom

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import forward_test as ft  # noqa: E402

DRAWS_PER_YEAR = 105  # 6/49 România, 2021-2025: 103-107 extrageri pe an


def operating(p: float, p0: float, p1: float, alpha: float, beta: float, cap: int) -> dict:
    """Probabilitățile de decizie ale SPRT-ului trunchiat, pentru rata reală p."""
    t = ft.Sprt(p0, p1, alpha, beta)
    alive = np.zeros(cap + 1)  # alive[k] = P(încă nedecis, k reușite)
    alive[0] = 1.0
    confirm_by = np.zeros(cap + 1)
    reject_by = np.zeros(cap + 1)
    ks = np.arange(cap + 1)
    for n in range(1, cap + 1):
        nxt = alive * (1 - p)
        nxt[1:] += alive[:-1] * p
        llr = ks * t.win + (n - ks) * t.lose
        up = (llr >= t.upper) & (ks <= n)
        down = (llr <= t.lower) & (ks <= n)
        confirm_by[n] = confirm_by[n - 1] + nxt[up].sum()
        reject_by[n] = reject_by[n - 1] + nxt[down].sum()
        nxt[up | down] = 0.0
        alive = nxt
    # Testul final la plafon: binomial exact unilateral, cu același α.
    # Cel mai mic k cu P(X >= k) < α, aceeași regulă ca în forward_test.final_test.
    crit = int(np.nonzero(binom.sf(ks - 1, cap, p0) < alpha)[0][0])
    final_confirm = float(alive[crit:].sum())

    def first_reaching(curve: np.ndarray, level: float) -> int | None:
        idx = np.nonzero(curve >= level)[0]
        return int(idx[0]) if idx.size else None

    decided = confirm_by + reject_by
    return {
        "p": p,
        "confirm_by_cap": float(confirm_by[cap]),
        "reject_by_cap": float(reject_by[cap]),
        "undecided_at_cap": float(alive.sum()),
        "confirm_with_final_test": float(confirm_by[cap]) + final_confirm,
        "final_test_min_hits": crit,
        "median_any_decision": first_reaching(np.append(decided, 1.0)[1:], 0.5),
        "median_to_confirmation": first_reaching(confirm_by, 0.5),
        "median_to_rejection": first_reaching(reject_by, 0.5),
    }


def table(reg: dict) -> dict:
    cap = int(reg["max_forward_draws"])
    out = {}
    for k, v in reg["sprt"].items():
        args = (v["p0"], v["p1"], v["alpha"], v["beta"], cap)
        out[k] = {"H0": operating(v["p0"], *args), "H1": operating(v["p1"], *args)}
    out["type1_bound_total"] = sum(out[k]["H0"]["confirm_with_final_test"] for k in reg["sprt"])
    return out


def _years(n: int | None) -> str:
    return "—" if n is None else f"{n} (~{n / DRAWS_PER_YEAR:.1f} ani)".replace(".", ",")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    reg = json.loads(ft.REGISTRATION.read_text(encoding="utf-8"))
    res = table(reg)
    pct = lambda x: f"{100 * x:.1f}%".replace(".", ",")  # noqa: E731
    for k in reg["sprt"]:
        h0, h1 = res[k]["H0"], res[k]["H1"]
        print(f"Pool {k}")
        print(f"  fără avantaj real: respins până la plafon {pct(h0['reject_by_cap'])}, "
              f"mediana până la respingere {_years(h0['median_to_rejection'])}")
        print(f"  fără avantaj real: confirmare falsă {pct(h0['confirm_by_cap'])} prin SPRT, "
              f"{pct(h0['confirm_with_final_test'])} cu testul final")
        print(f"  avantajul afirmat e real: confirmat până la plafon {pct(h1['confirm_by_cap'])} "
              f"prin SPRT, {pct(h1['confirm_with_final_test'])} cu testul final; "
              f"respins fals {pct(h1['reject_by_cap'])}; nedecis la plafon {pct(h1['undecided_at_cap'])}")
        print(f"  avantajul afirmat e real: jumătate din rulări confirmă până la "
              f"{_years(h1['median_to_confirmation'])}")
        print(f"  testul final confirmă de la {h0['final_test_min_hits']} reușite din "
              f"{reg['max_forward_draws']}")
    print(f"Eroare de tip I totală (ambele pooluri, margine Bonferroni): {pct(res['type1_bound_total'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
