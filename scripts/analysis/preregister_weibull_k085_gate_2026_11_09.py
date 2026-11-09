"""Poarta preînregistrată: `weibull_k0.85`, 6/49, pool 16, garanție 6 (2026-11-09).

PREÎNREGISTRARE. Acest protocol este fixat și comis în Git ÎNAINTE de prima
extragere evaluată. Numai extragerile cu data STRICT după 2026-11-09 intră în
poartă; istoricul până la 2026-11-09 a fost deja folosit la screening
(valul 4) și NU mai poate constitui probă de confirmare.

REGULA (fixă, deterministă, cauzală):
- metodă: traiectoria `traj_weibull_hazard(ind, kappa=0.85)` din
  `scripts/analysis/methods_wave4_bank_2026_11_09.py` — hazard Weibull al
  gap-ului per număr, scorul zilei t calculat strict din zilele < t
  (cauzalitate verificată prin invarianță la prefix în
  `_TESTE/test_wave4_bank.py` și `_TESTE/test_preregister_gate.py`);
- pool: top-16 al scorului zilei, canonic (sortat crescător);
- garanție 6: sistemul complet C(16,6) = 8008 variante — dacă pool-ul
  conține toate cele 6 numere extrase, exact o variantă are 6 (garantat
  combinatorial). Distribuția variantelor pe hituri se calculează EXACT, prin
  formula C(h,j)·C(16−h,6−j), fără enumerare;
- cost contabilizat: 8008 × 8 lei = 64.064 lei/extragere (tarif curent,
  fără taxă pe bilet); premiile în bani NU sunt preînregistrate (depind de
  fondul de câștiguri al fiecărei extrageri) — se contabilizează hiturile.

POARTA (ipoteza nulă: extragerile sunt uniforme ⇒ rata 5+ în pool 16 este
hipergeometrica p0 = 0.0108805779):
- orizont: primele 104 extrageri eligibile (~1 an, joi + duminică);
- criteriu de SUCCES: ≥5 extrageri cu 5+ în pool (P(X≥5 | H0) = 0.00576 ≤
  0.01 = α fixat); la final se raportează și p-ul binomial exact cumulat;
- putere onestă: la rata observată în screening (19/733 = 2,59%) puterea este
  0,134; la 4% este 0,403. Poarta este deci conservatoare: se deschide doar
  la un semnal mai puternic decât cel istoric sau la noroc — acest compromis
  este acceptat și fixat aici;
- ținte secundare (descriptive, fără prag): 4+ (p0 = 0.07960002), 6 în pool
  (p0 = 0.00057266), media hiturilor/extragere (μ0 = 1.95918), distribuția
  variantelor, costul cumulat.

UTILIZARE:
    python scripts/analysis/preregister_weibull_k085_gate_2026_11_09.py update
    python scripts/analysis/preregister_weibull_k085_gate_2026_11_09.py status

`update` e idempotent: evaluează doar zilele noi (> 2026-11-09, neevaluate) și
actualizează starea din `LOTO_RUNTIME_DIR/gate_weibull_k085_2026_11_09_state.json`
(implicit D:\\_BUILD\\_LOTO). `status` doar raportează.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy.stats import binom, hypergeom

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analysis.methods_wave4_bank_2026_11_09 import traj_weibull_hazard

# --- Constantele preînregistrate (NU se modifică după 2026-11-09) ---
REGISTERED_DATE = "2026-11-09"
GAME_CSV = ROOT / "_ISTORIC" / "loto_6_49.csv"
COLS = [f"n{i}" for i in range(1, 7)]
V, K, POOL = 49, 6, 16
KAPPA = 0.85
HORIZON_DRAWS = 104
CRIT_5PLUS = 5
ALPHA = 0.01
VARIANTS = math.comb(POOL, K)          # 8008 — sistem complet (garanție 6)
COST_PER_DRAW = VARIANTS * 8           # lei, tarif curent fără taxă pe bilet
P0_5 = float(hypergeom.sf(4, V, POOL, K))
P0_4 = float(hypergeom.sf(3, V, POOL, K))
P0_6 = float(hypergeom.sf(5, V, POOL, K))
MU0 = K * POOL / V

RUNTIME = Path(os.environ.get("LOTO_RUNTIME_DIR", r"D:\_BUILD\_LOTO"))
STATE_PATH = RUNTIME / "gate_weibull_k085_2026_11_09_state.json"


def load_history() -> tuple[list[str], np.ndarray, list[np.ndarray]]:
    """→ (datele ISO per zi, indicatorul pe zile D×V, extragerile per zi)."""
    df = pd.read_csv(GAME_CSV)
    day = pd.to_datetime(df["date"], dayfirst=True)
    df = df.assign(_day=day).sort_values("_day", kind="stable").reset_index(drop=True)
    dates: list[str] = []
    days: list[np.ndarray] = []
    day_draws: list[np.ndarray] = []
    uniq = df["_day"].to_numpy()
    mat = df[COLS].to_numpy(dtype=int)
    start = 0
    while start < len(df):
        end = start
        while end < len(df) and uniq[end] == uniq[start]:
            end += 1
        rows = np.zeros((end - start, V))
        for r, row in enumerate(mat[start:end]):
            rows[r, np.asarray(row) - 1] = 1.0
        dates.append(pd.Timestamp(uniq[start]).date().isoformat())
        days.append((rows.sum(axis=0) > 0).astype(float))
        day_draws.append(rows)
        start = end
    return dates, np.asarray(days), day_draws


def pool_for_day(day_masks: np.ndarray, t: int) -> list[int]:
    """Top-16 canonic al traiectoriei Weibull κ=0.85 pentru ziua t (cauzal)."""
    traj = traj_weibull_hazard(day_masks, KAPPA)
    idx = np.argpartition(-traj[t], POOL - 1)[:POOL]
    return sorted((int(i) + 1 for i in idx))


def ticket_distribution(pool: list[int], drawn: list[int]) -> dict[int, int]:
    """Distribuția exactă a celor 8008 de variante pe hituri, pentru h hituri
    în pool: C(h,j)·C(16−h,6−j). Garanția 6: h=6 ⇒ exact o variantă cu 6."""
    h = len(set(pool) & set(drawn))
    return {j: math.comb(h, j) * math.comb(POOL - h, K - j) for j in range(0, K + 1)}


def _fresh_state() -> dict:
    return {
        "protocol": {
            "registered_date": REGISTERED_DATE,
            "method": f"weibull_k{KAPPA}",
            "game": "loto_6_49",
            "pool": POOL,
            "guarantee": K,
            "variants_per_draw": VARIANTS,
            "cost_per_draw_lei": COST_PER_DRAW,
            "horizon_draws": HORIZON_DRAWS,
            "crit_5plus": CRIT_5PLUS,
            "alpha": ALPHA,
            "p0": {"4plus": P0_4, "5plus": P0_5, "6": P0_6},
        },
        "evaluated": [],
        "cum": {"n": 0, "hits_sum": 0, "ge4": 0, "ge5": 0, "ge6": 0,
                "tix6": 0, "tix5": 0, "tix4": 0, "tix3": 0, "cost_lei": 0},
    }


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return _fresh_state()


def save_state(state: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
    )


def update() -> None:
    dates, day_masks, day_draws = load_history()
    state = load_state()
    seen = {e["date"] for e in state["evaluated"]}
    new_days = [
        t for t, d in enumerate(dates)
        if d > REGISTERED_DATE and d not in seen and d <= pd.Timestamp.today().date().isoformat()
    ]
    if not new_days:
        print("Nicio extragere nouă de evaluat (poarta numără strict datele "
              f"> {REGISTERED_DATE}). Istoricul are {len(dates)} zile, ultima: {dates[-1]}.")
    traj = traj_weibull_hazard(day_masks, KAPPA) if new_days else None
    for t in new_days:
        idx = np.argpartition(-traj[t], POOL - 1)[:POOL]
        pool = sorted((int(i) + 1 for i in idx))
        for row in day_draws[t]:
            drawn = sorted((int(i) + 1 for i in np.flatnonzero(row > 0)))
            dist = ticket_distribution(pool, drawn)
            h = len(set(pool) & set(drawn))
            state["evaluated"].append({
                "date": dates[t], "pool": pool, "drawn": drawn, "hits": h,
                "tickets": {str(j): dist[j] for j in range(0, K + 1)},
            })
            c = state["cum"]
            c["n"] += 1
            c["hits_sum"] += h
            c["ge4"] += int(h >= 4)
            c["ge5"] += int(h >= 5)
            c["ge6"] += int(h >= 6)
            c["tix6"] += dist[6]
            c["tix5"] += dist[5]
            c["tix4"] += dist[4]
            c["tix3"] += dist[3]
            c["cost_lei"] += COST_PER_DRAW
        print(f"  {dates[t]}: pool={pool} extras={drawn} hits={h} "
              f"(6/5/4/3 variante: {dist[6]}/{dist[5]}/{dist[4]}/{dist[3]})")
    save_state(state)
    status(state)


def status(state: dict | None = None) -> None:
    state = state or load_state()
    c = state["cum"]
    n = c["n"]
    p5 = float(binom.sf(c["ge5"] - 1, n, P0_5)) if c["ge5"] else 1.0
    print(f"\n=== POARTA weibull_k0.85 · 6/49 · pool 16 · garanție 6 ===")
    print(f"extrageri evaluate: {n}/{HORIZON_DRAWS} | cost cumulat: {c['cost_lei']:,} lei")
    print(f"pool hits: 4+={c['ge4']} (așteptat {n * P0_4:.1f}) | "
          f"5+={c['ge5']} (așteptat {n * P0_5:.2f}) | 6={c['ge6']}")
    print(f"medie hituri: {c['hits_sum'] / n if n else 0:.4f} vs μ0={MU0:.4f}")
    print(f"variante: 6={c['tix6']} 5={c['tix5']} 4={c['tix4']} 3={c['tix3']}")
    verdict = ("SUCCES — poarta deschisă" if c["ge5"] >= CRIT_5PLUS
               else "în desfășurare" if n < HORIZON_DRAWS
               else "ÎNCHISĂ — semnal neconfirmat")
    print(f"p binomial cumulat (5+): {p5:.5f} | prag: ≥{CRIT_5PLUS} evenimente "
          f"la {HORIZON_DRAWS} extrageri ⇒ {verdict}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "update":
        update()
    else:
        status()
