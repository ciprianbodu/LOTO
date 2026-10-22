"""Designuri cu buget pentru biletul de 5 (5/40 și Joker), pool 16, garanția 4 (2026-10-22).

Peste 64 de bilete, motorul (`generate_wheel("hitcover", ..., max_num=...)`:
greedy, schimburi de profil, `budget_climb`, schemele LR) rămâne la 4+ cu până
la 3-4% sub ce se poate la 300..403 de variante. Aici punctul de plecare e
coverul complet C(16,5,4) al motorului (404 variante, 4 sigur), din care se
scoate pe rând biletul care pierde cel mai puțin: întâi pe pragul 4, apoi pe 5,
apoi pe 3, cu ponderea exactă a fiecărei intersecții pool-extragere pe
geometria 5/40 (6 extrase din 40). Fiecare număr rămâne pe cel puțin un bilet.
Tăierea e deterministă; o singură trecere dă toate bugetele 403..65.

O secțiune `B n` intră în fișier numai dacă (`keep_section`):
- trece regula de certificare a producției (`covering.anneal_hits._certify`)
  față de biletele motorului de azi la acel buget, pe AMBELE jocuri, 5/40 (6 din
  40) și Joker (5 din 45): același număr de bilete distincte, numerele jucate
  rămân, niciun prag 3..extrase nu scade, câștig strict, acoperirea garanției nu
  scade;
- câștigă cel puțin 1% relativ la 4+ pe ambele jocuri (pragul de recomandare
  al proiectului);
- bugetul nu e mărimea unei scheme LR de 16 numere (5/40 Cod 37 = 348): acolo
  motorul adoptă schema, iar panoul și `covering_designs/lr/app_same_size.json`
  o compară cu aplicația; o secțiune H ar înlocui-o pentru +0,48%.
Loader-ul (`covering.budget_designs`) recertifică oricum la fiecare apel, pe
jocul cerut.

Scrie `covering_designs/budget/H_16_5_4.txt` și raportul JSON cu cifrele exacte.
Biletele motorului se calculează fără fișierul H existent (altfel s-ar compara
cu el însuși). Rulare din rădăcina proiectului (~30-60 min, un proces):
    python scripts/analysis/gen_budget_designs_pick5.py [--out CALE] [--report CALE]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from math import comb
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import covering.budget_designs as bd  # noqa: E402
from covering.anneal_hits import _certify  # noqa: E402
from covering.dispatch import generate_wheel  # noqa: E402
from covering.probability import wheel_hit_probabilities, wheel_hit_profile  # noqa: E402

V, PICK, G = 16, 5, 4
GAMES = {"5/40": (6, 40), "joker": (5, 45)}  # nume -> (extrase, univers)
PRUNE_GEOMETRY = GAMES["5/40"]
DEFAULT_OUT = ROOT / "covering_designs" / "budget" / "H_16_5_4.txt"
DEFAULT_REPORT = Path(__file__).resolve().parent / "budget_designs_pick5_2026-10-22.json"


def _weights(draw_n: int, max_num: int) -> np.ndarray:
    """Ponderea fiecărei submulțimi a pool-ului ca intersecție exactă cu extragerea."""
    sizes = np.bitwise_count(np.arange(1 << V, dtype=np.int64))
    per_size = [comb(max_num - V, draw_n - s) if 3 <= s <= draw_n else 0 for s in range(V + 1)]
    return np.asarray(per_size, dtype=np.float64)[sizes]


def prune_path(start_masks: list[int], low: int) -> dict[int, list[int]]:
    """{buget: măști} de la len(start) - 1 până la `low`, prin scoaterea greedy a
    biletului cu cea mai mică pierdere (prag 4, apoi 5, apoi 3)."""
    d, m = PRUNE_GEOMETRY
    masks_all = np.arange(1 << V, dtype=np.int64)
    weights = _weights(d, m)
    keep = weights > 0
    masks_all, weights = masks_all[keep], weights[keep]
    scale = {4: 1.0, 5: 1e-3, 3: 1e-4}
    cur = [int(x) for x in start_masks]
    inter = np.stack([np.bitwise_count(masks_all & np.int64(x)).astype(np.int8) for x in cur])
    played = np.zeros(V, dtype=int)
    for x in cur:
        for p in range(V):
            played[p] += (x >> p) & 1
    out: dict[int, list[int]] = {}
    while len(cur) > low:
        loss = np.zeros(len(cur))
        for t, w in scale.items():
            hit = inter >= t
            only = hit.sum(axis=0) == 1
            loss += w * ((hit & only[None, :]).astype(np.float64) @ weights)
        for j, x in enumerate(cur):  # ultimul bilet al unei poziții nu se scoate
            if any(played[p] == 1 for p in range(V) if (x >> p) & 1):
                loss[j] = np.inf
        j = int(np.argmin(loss))
        x = cur.pop(j)
        for p in range(V):
            played[p] -= (x >> p) & 1
        inter = np.delete(inter, j, axis=0)
        out[len(cur)] = list(cur)
    return out


def _tickets(masks: list[int]) -> list[list[int]]:
    return sorted(sorted(p + 1 for p in range(V) if (x >> p) & 1) for x in masks)


def _odds(pool, tickets, draw_n, max_num) -> dict:
    p = wheel_hit_probabilities(pool, tickets, draw_n, max_num)["ticket"]
    return {
        "P": {str(t): p[t] for t in range(3, min(PICK, draw_n) + 1)},
        "cov4": wheel_hit_profile(pool, tickets)[G][G],
    }


MIN_RELATIVE_GAIN = 0.01


def keep_section(row: dict, lr_sizes: set[int]) -> bool:
    """Regula de livrare a unei secțiuni (vezi docstring-ul modulului)."""
    if row["budget"] in lr_sizes:
        return False
    for r in row["games"].values():
        if not r["certified"]:
            return False
        before, after = r["engine"]["P"]["4"], r["design"]["P"]["4"]
        if after < before * (1.0 + MIN_RELATIVE_GAIN):
            return False
    return True


def lr_scheme_sizes() -> set[int]:
    from covering.lr_schemes import lr_schemes

    return {s.size for game in GAMES for s in lr_schemes(game, V) if len(s.variants[0]) == PICK}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--low", type=int, default=65)
    args = ap.parse_args(argv)
    # Jurnalul motorului (greedy, budget_climb) numai în rularea scriptului:
    # la import (teste) starea globală a logging-ului rămâne neatinsă.
    logging.disable(logging.WARNING)

    pool = list(range(1, V + 1))
    scores = {n: float(V + 1 - n) for n in pool}  # poziția 1 = cel mai bine clasat
    lr_sizes = lr_scheme_sizes()
    started = time.perf_counter()
    # Biletele motorului fără fișierul H existent.
    with tempfile.TemporaryDirectory() as empty:
        saved = list(bd._BUDGET_DIRS)
        bd._BUDGET_DIRS[:] = [Path(empty)]
        bd._FILE_CACHE.clear()
        try:
            d, m = PRUNE_GEOMETRY
            full, _ = generate_wheel("hitcover", pool, PICK, G, 404, scores, None, d, m)
            if len(full) != 404:
                raise SystemExit(f"coverul complet are {len(full)} bilete, nu 404")
            path = prune_path([sum(1 << (n - 1) for n in t) for t in full], args.low)
            rows, sections = [], {}
            for budget in range(403, args.low - 1, -1):
                cand = _tickets(path[budget])
                row = {"budget": budget, "games": {}}
                for game, (draw_n, max_num) in GAMES.items():
                    inc, _ = generate_wheel(
                        "hitcover", pool, PICK, G, budget, scores, None, draw_n, max_num
                    )
                    why, gain, _cov = _certify(pool, inc, cand, G, draw_n, max_num)
                    row["games"][game] = {
                        "certified": not why,
                        "reason": why,
                        "engine": _odds(pool, inc, draw_n, max_num),
                        "design": _odds(pool, cand, draw_n, max_num),
                    }
                if keep_section(row, lr_sizes):
                    sections[budget] = cand
                rows.append(row)
                print(
                    f"B{budget}: "
                    + " | ".join(
                        f"{game} {'OK' if r['certified'] else r['reason']} "
                        f"4+ {100 * r['engine']['P']['4']:.4f}% -> {100 * r['design']['P']['4']:.4f}%"
                        for game, r in row["games"].items()
                    ),
                    flush=True,
                )
        finally:
            bd._BUDGET_DIRS[:] = saved
            bd._FILE_CACHE.clear()

    lines = [
        "# Budget designs C(16,5,4), positions 1..16 (1 = best ranked).",
        "# Greedy pruning of the engine's complete cover (404), on the 5/40 geometry.",
        "# Kept only when certified against the engine on 5/40 AND Joker, with at",
        "# least +1% at 4+ on both, and never at an LR scheme size (5/40 Cod 37 = 348)",
        "# (scripts/analysis/gen_budget_designs_pick5.py, 2026-10-22).",
    ]
    for budget in sorted(sections):
        lines.append(f"B {budget}")
        lines.extend(" ".join(str(n) for n in t) for t in sections[budget])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    report = {
        "geometry": {"v": V, "pick": PICK, "guarantee": G},
        "prune_geometry": {"draw_n": PRUNE_GEOMETRY[0], "max_num": PRUNE_GEOMETRY[1]},
        "sections": sorted(sections),
        "rule": {"both_games_certified": True, "min_relative_gain_4plus": MIN_RELATIVE_GAIN,
                 "skipped_lr_sizes": sorted(lr_sizes)},
        "rows": rows,
        "seconds": round(time.perf_counter() - started, 1),
    }
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{len(sections)} sectiuni -> {args.out}; raport {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
