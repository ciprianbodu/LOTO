"""Castigul certificat al rearanjarii „Biletului complet” cu garantia completa (2026-10-12).

Configuratiile in care baza garantiei e completa inainte de a umple biletele
(6/49 cu garantia 3 si 4, 5/40 cu garantia 4, Joker Urna 1 cu garantia 3; pool
8..12; 2..10 bilete fizice): variantele livrate fara etapa
`covering.anneal_hits.improve_full_cover_variants` fata de cele cu ea, pe
acelasi pool canonic si acelasi numar de variante. Probabilitatile sunt exacte,
pe extrageri uniforme (`covering.probability`); P(garantie) e la maximul
hipergeometric al pool-ului in ambele cazuri, deci se raporteaza pragul
urmator (g+1). Nicio afirmatie despre pool sau despre predictie.
Rezultatul: `full_cover_anneal_2026-10-12.json`.

Rulare din radacina proiectului:
    python scripts/analysis/bench_full_cover_anneal.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

import covering.anneal_hits as ah  # noqa: E402
from covering.common import compute_coverage_pct  # noqa: E402
from covering.probability import wheel_hit_probabilities  # noqa: E402
from loto_enterprise.core.full_ticket import _fill_variants  # noqa: E402

GAMES = {"6/49": (49, 6, 6), "5/40": (40, 6, 5), "joker": (45, 5, 5)}  # max, extrase, bilet
PER_TICKET = {"6/49": 3, "5/40": 4, "joker": 2}
CELLS = [("6/49", 3), ("6/49", 4), ("5/40", 4), ("joker", 3)]
OUT = Path(__file__).resolve().parent / "full_cover_anneal_2026-10-12.json"


def run(cfg):
    game, g, v, tickets = cfg
    max_num, draw_n, pick = GAMES[game]
    n_var = PER_TICKET[game] * tickets
    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    real = ah.improve_full_cover_variants
    ah.improve_full_cover_variants = lambda *a, **k: (a[1], {"applied": False})
    try:
        before, n_base = _fill_variants(pool, pick, g, n_var, scores, draw_n=draw_n, max_num=max_num)
    finally:
        ah.improve_full_cover_variants = real
    audit: dict = {}
    t0 = time.time()
    after, n_after = _fill_variants(pool, pick, g, n_var, scores, draw_n=draw_n, max_num=max_num, audit=audit)
    seconds = time.time() - t0
    p0 = wheel_hit_probabilities(pool, before, draw_n, max_num)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, draw_n, max_num)["ticket"]
    top = min(pick, draw_n)
    return {
        "game": game, "v": v, "g": g, "tickets": tickets, "n_var": n_var, "n_base": n_base,
        "lever": n_base < len(before), "headline": min(g + 1, top),
        "before": {t: p0[t] for t in range(3, top + 1)},
        "after": {t: p1[t] for t in range(3, top + 1)},
        "cov": [compute_coverage_pct(before, pool, g), compute_coverage_pct(after, pool, g)],
        "cov_next": [compute_coverage_pct(before, pool, g + 1), compute_coverage_pct(after, pool, g + 1)],
        "applied": bool((audit.get("full_cover") or {}).get("applied")),
        "accepted": (audit.get("full_cover") or {}).get("accepted", 0),
        "seconds": seconds,
    }


def configs():
    return [
        (game, g, v, tickets)
        for game, g in CELLS
        for v in (8, 9, 10, 11, 12)
        for tickets in range(2, 11)
        if comb(v, GAMES[game][2]) >= PER_TICKET[game] * tickets
    ]


def main() -> None:
    cfgs = configs()
    print(f"{len(cfgs)} configuratii", flush=True)
    rows = []
    with ProcessPoolExecutor(2) as ex:
        for r in ex.map(run, cfgs):
            rows.append(r)
            if not r["lever"]:
                continue
            t = r["headline"]
            rel = (r["after"][t] - r["before"][t]) / r["before"][t] if r["before"][t] else 0.0
            print(
                f"{r['game']:5s} g{r['g']} pool {r['v']:2d} {r['tickets']:2d} bilete ({r['n_base']}/{r['n_var']}) "
                f"{t}+: {100 * r['before'][t]:.4f}% -> {100 * r['after'][t]:.4f}% ({100 * rel:+.2f}%) "
                f"aplicat={r['applied']} {r['seconds']:.1f}s",
                flush=True,
            )
    OUT.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    lever = [r for r in rows if r["lever"]]
    gains = [
        (r["after"][r["headline"]] - r["before"][r["headline"]]) / r["before"][r["headline"]]
        for r in lever
        if r["before"][r["headline"]]
    ]
    print(
        f"parghia in {len(lever)}/{len(rows)}, aplicat {sum(r['applied'] for r in lever)}, "
        f"castig la g+1 in {sum(x > 1e-9 for x in gains)}, maxim {100 * max(gains):.2f}%"
    )


if __name__ == "__main__":
    main()
