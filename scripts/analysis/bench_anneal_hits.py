"""Castigul certificat al recoacerii pe sansa exacta a biletelor (2026-10-11).

Pentru fiecare joc, pool (10, 12, 14, 16), garantie (3, 4) si buget (bugetele
„Biletului complet” pentru 1-3 bilete fizice, apoi 7, 12, 20, 40, 64 de bilete,
sub designul complet): wheel-ul de productie fara etapa de recoacere fata de
cel cu ea, pe acelasi pool canonic si acelasi numar de bilete. Probabilitatile
sunt exacte, pe extrageri uniforme (`covering.probability`); nicio afirmatie
despre pool sau despre predictie. Rezultatul: `anneal_hits_2026-10-11.json`.

Rulare din radacina proiectului:
    python scripts/analysis/bench_anneal_hits.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

import covering.dispatch as dispatch  # noqa: E402
from covering.designs import complete_design_size  # noqa: E402
from covering.dispatch import generate_wheel  # noqa: E402
from covering.probability import wheel_hit_probabilities  # noqa: E402

GAMES = {"6/49": (49, 6, 6), "5/40": (40, 6, 5), "joker": (45, 5, 5)}  # max, extrase, bilet
PER_TICKET = {"6/49": 3, "5/40": 4, "joker": 2}
OUT = Path(__file__).resolve().parent / "anneal_hits_2026-10-11.json"


def run(cfg):
    game, v, g, budget = cfg
    max_num, draw_n, pick = GAMES[game]
    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    real = dispatch.improve_ticket_chances
    dispatch.improve_ticket_chances = lambda *a, **k: (a[1], {"applied": False})
    try:
        before, cov0 = generate_wheel("hitcover", pool, pick, g, budget, scores, draw_n=draw_n, max_num=max_num)
    finally:
        dispatch.improve_ticket_chances = real
    t0 = time.time()
    after, cov1 = generate_wheel("hitcover", pool, pick, g, budget, scores, draw_n=draw_n, max_num=max_num)
    seconds = time.time() - t0
    p0 = wheel_hit_probabilities(pool, before, draw_n, max_num)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, draw_n, max_num)["ticket"]
    target = 4 if (game == "5/40" or g == 4) else 3
    return {
        "game": game, "v": v, "g": g, "B": budget, "target": target,
        "before": {t: p0[t] for t in range(3, draw_n + 1)},
        "after": {t: p1[t] for t in range(3, draw_n + 1)},
        "cov": [cov0, cov1], "applied": after != before, "seconds": seconds,
    }


def configs():
    out = []
    for game in GAMES:
        _max, _draw, pick = GAMES[game]
        per = PER_TICKET[game]
        for v in (10, 12, 14, 16):
            for g in (3, 4):
                if g >= pick:
                    continue
                size = complete_design_size(v, pick, g)
                for budget in sorted({per, 2 * per, 3 * per, 7, 12, 20, 40, 64}):
                    if size is not None and budget >= size:
                        continue
                    out.append((game, v, g, budget))
    return out


def main() -> None:
    cfgs = configs()
    print(f"{len(cfgs)} configuratii", flush=True)
    rows = []
    with ProcessPoolExecutor(4) as ex:
        for r in ex.map(run, cfgs):
            rows.append(r)
            t = r["target"]
            rel = (r["after"][t] - r["before"][t]) / r["before"][t] if r["before"][t] else 0.0
            print(
                f"{r['game']:5s} pool {r['v']:2d} g{r['g']} B{r['B']:2d} {t}+: "
                f"{100 * r['before'][t]:.3f}% -> {100 * r['after'][t]:.3f}% ({100 * rel:+.2f}%) "
                f"aplicat={r['applied']} {r['seconds']:.1f}s",
                flush=True,
            )
    OUT.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    applied = sum(r["applied"] for r in rows)
    gains = [(r["after"][r["target"]] - r["before"][r["target"]]) / r["before"][r["target"]] for r in rows]
    print(f"aplicat {applied}/{len(rows)}, castig la tinta in {sum(g > 1e-9 for g in gains)}, maxim {100 * max(gains):.2f}%")


if __name__ == "__main__":
    main()
