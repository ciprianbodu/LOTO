"""Poarta preînregistrată nr. 2: `frequency`, Joker Urna 1, pool 15, țintă 4+ (2026-11-14).

PREÎNREGISTRARE. Acest protocol este fixat și comis în Git ÎNAINTE de prima
extragere evaluată. Numai extragerile cu data STRICT după 2026-11-14 intră în
poartă; istoricul până la 2026-11-14 (2204 extrageri) a fost deja folosit la
screening (scanarea fără Holm + testul disjunct din 2026-11-14) și NU mai
poate constitui probă de confirmare.

MOTIVAȚIA (screening retrospectiv, doar ipoteză): la pool 15 cauzal,
`frequency` a avut 103 vs 79,3 evenimente 4+ pe istoricul complet și a fost
pozitiv în AMBELE jumătăți disjuncte (48/39,6 și 55/39,6, Fisher p=0,0047
brut, nesupraviețuitor corecției multiple). Poarta testează prospectiv dacă
semnalul există.

REGULA (fixă, deterministă, cauzală):
- metodă: `score_frequency` — frecvența cu ponderi exponențiale de recență,
  weights = exp(linspace(-2, 0, n)) (float32, acumulare float64 prin
  bincount), scorul zilei t calculat strict din extragerile < t. Copie
  ÎNGHEȚATĂ mai jos (`_score_frequency_frozen`), deliberat NEIMPORTATĂ din
  aplicație: modificări ulterioare ale motorului NU pot schimba poarta;
- pool: top-15 canonic după scor; la egalitate exactă de scor, numărul MAI
  MARE câștigă (regula canonică rank_by_score / runner._top_k). Fără limită
  de consecutive, fără filtre — top-N pur;
- se evaluează numai cele 5 numere din Urna 1 (bila Joker NU intră în pool);
- sistemul complet C(15,5) = 3003 variante: dacă pool-ul conține toate cele
  5 numere, exact o variantă are 5 (garantat combinatorial). Distribuția
  variantelor pe hituri: C(h,j)·C(15−h,5−j), exactă, fără enumerare;
- cost contabilizat: 3003 × 7 lei = 21.021 lei/extragere (tarif curent,
  fără taxă pe bilet); premiile în bani NU sunt preînregistrate — se
  contabilizează hiturile.

POARTA (ipoteza nulă: extragerile sunt uniforme ⇒ rata 4+ în pool 15 este
hipergeometrica p0 = 0.0359751800):
- orizont: primele 104 extrageri eligibile (~1 an, joi + duminică);
- criteriu de SUCCES: ≥8 extrageri cu 4+ în pool (P(X≥8 | H0) = 0.03435 ≤
  0.05 = α fixat, nivel SCREENING — mai larg decât α=0.01 al porții
  weibull, declarat explicit aici); la final se raportează și p-ul binomial
  exact cumulat;
- putere onestă: la rata observată în screening (103/2204 = 4,673%) puterea
  este 0,114; la 6% este 0,286; la 7% este 0,444; la 8% este 0,598. Poarta
  este deci CONSERVATOARE: la magnitudinea retrospectivă a semnalului se
  deschide în ~1 din 9 luni de testare — compromis acceptat și fixat aici.
  Un rezultat negativ NU respinge ipoteza la magnitudinea observată
  retrospectiv, doar ratele ≥7% ar fi fost respinse cu încredere rezonabilă;
- ținte secundare (descriptive, fără prag): 5 în pool (p0 = 0.0024579316),
  media hiturilor/extragere (μ0 = 1.66667), distribuția variantelor, costul
  cumulat.

UTILIZARE:
    python scripts/analysis/preregister_frequency_joker4_gate_2026_11_14.py update
    python scripts/analysis/preregister_frequency_joker4_gate_2026_11_14.py status

`update` e idempotent: evaluează doar zilele noi (> 2026-11-14, neevaluate)
și actualizează starea din
`LOTO_RUNTIME_DIR/gate_frequency_joker4_2026_11_14_state.json`
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

# --- Constantele preînregistrate (NU se modifică după 2026-11-14) ---
REGISTERED_DATE = "2026-11-14"
GAME_CSV = ROOT / "_ISTORIC" / "joker.csv"
COLS = [f"n{i}" for i in range(1, 6)]     # numai Urna 1; bila Joker nu intră în pool
V, K, POOL = 45, 5, 15
HORIZON_DRAWS = 104
CRIT_4PLUS = 8
ALPHA = 0.05
VARIANTS = math.comb(POOL, K)             # 3003 — sistem complet (garanție 5)
COST_PER_DRAW = VARIANTS * 7              # lei, tarif curent fără taxă pe bilet
P0_4 = float(hypergeom.sf(3, V, POOL, K))
P0_5 = float(hypergeom.sf(4, V, POOL, K))
MU0 = K * POOL / V

RUNTIME = Path(os.environ.get("LOTO_RUNTIME_DIR", r"D:\_BUILD\_LOTO"))
STATE_PATH = RUNTIME / "gate_frequency_joker4_2026_11_14_state.json"


def _score_frequency_frozen(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """Copie înghețată a `score_frequency` (exp-decay recency-weighted).

    Returnează vectorul de scoruri brute indexat 0..max_num (scorul numărului
    n pe poziția n). Normalizarea din aplicație este monotonă și nu schimbă
    clasamentul, deci este omisă aici; pipeline-ul de tipuri (float32 →
    float64) este identic cu cel al motorului.
    """
    n = draws_2d.shape[0]
    if n == 0:
        return np.ones(max_num + 1, dtype=np.float64)
    weights = np.exp(np.linspace(-2.0, 0.0, n)).astype(np.float32)
    values = np.asarray(draws_2d).astype(np.int64).ravel()
    repeated_weights = np.repeat(weights.astype(np.float64), draws_2d.shape[1])
    valid = (values >= 1) & (values <= max_num)
    return np.bincount(
        values[valid], weights=repeated_weights[valid], minlength=max_num + 1
    )


def load_history() -> tuple[list[str], np.ndarray]:
    """→ (datele ISO per extragere, matricea N×5 cronologic crescătoare)."""
    df = pd.read_csv(GAME_CSV)
    day = pd.to_datetime(df["date"], dayfirst=True)
    df = df.assign(_day=day).sort_values("_day", kind="stable").reset_index(drop=True)
    dates = [pd.Timestamp(d).date().isoformat() for d in df["_day"]]
    return dates, df[COLS].to_numpy(dtype=int)


def pool_for_day(draws_2d: np.ndarray) -> list[int]:
    """Top-15 canonic al frecvenței ponderate, cauzal (doar extragerile date).

    La egalitate exactă de scor câștigă numărul mai mare (regula canonică).
    """
    scores = _score_frequency_frozen(draws_2d, V)
    ranked = sorted(range(1, V + 1), key=lambda n: (-scores[n], -n))
    return sorted(ranked[:POOL])


def ticket_distribution(pool: list[int], drawn: list[int]) -> dict[int, int]:
    """Distribuția exactă a celor 3003 variante pe hituri: C(h,j)·C(15−h,5−j).
    Garanția 5: h=5 ⇒ exact o variantă cu 5."""
    h = len(set(pool) & set(drawn))
    return {j: math.comb(h, j) * math.comb(POOL - h, K - j) for j in range(0, K + 1)}


def _fresh_state() -> dict:
    return {
        "protocol": {
            "registered_date": REGISTERED_DATE,
            "method": "frequency",
            "game": "joker_urna1",
            "pool": POOL,
            "guarantee": K,
            "variants_per_draw": VARIANTS,
            "cost_per_draw_lei": COST_PER_DRAW,
            "horizon_draws": HORIZON_DRAWS,
            "crit_4plus": CRIT_4PLUS,
            "alpha": ALPHA,
            "p0": {"4plus": P0_4, "5": P0_5},
        },
        "evaluated": [],
        "cum": {"n": 0, "hits_sum": 0, "ge4": 0, "ge5": 0,
                "tix5": 0, "tix4": 0, "tix3": 0, "cost_lei": 0},
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
    dates, draws = load_history()
    state = load_state()
    seen = {e["date"] for e in state["evaluated"]}
    today = pd.Timestamp.today().date().isoformat()
    new_idx = [
        t for t, d in enumerate(dates)
        if d > REGISTERED_DATE and d not in seen and d <= today
    ]
    if not new_idx:
        print("Nicio extragere nouă de evaluat (poarta numără strict datele "
              f"> {REGISTERED_DATE}). Istoricul are {len(dates)} extrageri, "
              f"ultima: {dates[-1]}.")
    for t in new_idx:
        pool = pool_for_day(draws[:t])
        drawn = sorted(int(x) for x in draws[t])
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
        c["tix5"] += dist[5]
        c["tix4"] += dist[4]
        c["tix3"] += dist[3]
        c["cost_lei"] += COST_PER_DRAW
        print(f"  {dates[t]}: pool={pool} extras={drawn} hits={h} "
              f"(5/4/3 variante: {dist[5]}/{dist[4]}/{dist[3]})")
    save_state(state)
    status(state)


def status(state: dict | None = None) -> None:
    state = state or load_state()
    c = state["cum"]
    n = c["n"]
    p4 = float(binom.sf(c["ge4"] - 1, n, P0_4)) if c["ge4"] else 1.0
    print(f"\n=== POARTA frequency · Joker Urna 1 · pool 15 · țintă 4+ ===")
    print(f"extrageri evaluate: {n}/{HORIZON_DRAWS} | cost cumulat: {c['cost_lei']:,} lei")
    print(f"pool hits: 4+={c['ge4']} (așteptat {n * P0_4:.2f}) | "
          f"5={c['ge5']} (așteptat {n * P0_5:.2f})")
    print(f"medie hituri: {c['hits_sum'] / n if n else 0:.4f} vs μ0={MU0:.4f}")
    print(f"variante: 5={c['tix5']} 4={c['tix4']} 3={c['tix3']}")
    verdict = ("SUCCES — poarta deschisă" if c["ge4"] >= CRIT_4PLUS
               else "în desfășurare" if n < HORIZON_DRAWS
               else "ÎNCHISĂ — semnal neconfirmat")
    print(f"p binomial cumulat (4+): {p4:.5f} | prag: ≥{CRIT_4PLUS} evenimente "
          f"la {HORIZON_DRAWS} extrageri ⇒ {verdict}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "update":
        update()
    else:
        status()
