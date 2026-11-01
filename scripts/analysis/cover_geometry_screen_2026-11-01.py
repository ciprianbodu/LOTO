"""Ecran de aranjamente geometrice / cover design, 2026-11-01. Protocol fixat
inainte de rulare.

Intrebarea utilizatorului: se pot testa si alte metode experimentale de cover
design / aranjare geometrica a biletelor? Rata pool-ului e hipergeometrica si nu
se poate ridica prin cod; singura parghie e asezarea numerelor pe bilete, la
acelasi pool si acelasi numar de bilete.

Geometrii (aceleasi ca aplicatia): 6/49 (pick 6, draw 6, max 49), Loto 5/40
(pick 5, draw 6, max 40), Joker Urna 1 (pick 5, draw 5, max 45). Pool-ul e
canonic (pozitiile 1..v), ca la designurile din covering_designs.

Configuratii:
  above_complete  v=16, g=4, buget B peste designul complet (6/49: 200/300/404;
                  5/40 si Joker: 500/600, completul fiind 404): obiectiv P(>=5)
                  pe un bilet, cu acoperirea g=4 mentinuta 100%. Intrebare: merita
                  cheltuit mai mult decat designul complet pentru 5+?
  budget_below    v=16, g=3, B in {64, 100}: obiectiv P(>=4) la buget fix sub
                  designul complet.
  complete_refine v in {14, 15, 16}, g=4, fara plafon: obiectiv P(>=5) peste
                  wheel-ul motorului (care include deja coverele rafinate R_*).
  cyclic          v=16, pick 6, g=4: design ciclic (blocuri de baza + shifturi
                  mod 16) vs greedy la acelasi numar de bilete — diagnostic.

Metoda. Baseline = wheel-ul motorului pe pozitii canonice
(dispatch.generate_wheel cu metoda auto rezolvata, max_variants=B, draw_n,
max_num). Pentru above_complete/budget_below baseline-ul de certificare e
greedy la acelasi B (acelasi numar de bilete). Candidati = recoacere simulata
(mutare = inlocuirea unui numar dintr-un bilet cu alt numar din pool), 2 seed-uri
x 3000 de mutari, obiectivul exact (covering.probability.wheel_hit_probabilities).
Fiecare candidat se CERTIFICA cu regula de productie (covering.anneal_hits.
_certify): acelasi numar de bilete distincte, numerele jucate pastrate, niciun
prag 3..draw_n in scadere, castig strict la cel putin un prag, acoperirea
garantiei nedescrescatoare. Ne-certificat = nu se raporteaza ca imbunatatire.

Rezultatele sunt EXACTE (numaratori intregi, extragere uniforma), nu Monte
Carlo. Niciun rezultat nu afirma avantaj predictiv — doar geometria biletelor.
"""

from __future__ import annotations

import json
import time
from itertools import combinations
from math import comb
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT))

from covering.anneal_hits import _certify
from covering.dispatch import generate_wheel, resolve_wheel_method
from covering.probability import wheel_hit_probabilities, wheel_hit_profile

OUT = Path(__file__).resolve().parent / "cover_geometry_screen_2026-11-01.json"

GAMES = {
    "6/49": {"pick": 6, "draw_n": 6, "max_num": 49},
    "5/40": {"pick": 5, "draw_n": 6, "max_num": 40},
    "joker1": {"pick": 5, "draw_n": 5, "max_num": 45},
}

SEEDS = (20261101, 20261102)
MOVES = 3000


def _engine_wheel(game: str, v: int, g: int, B: int):
    cfg = GAMES[game]
    pool = list(range(1, v + 1))
    method = resolve_wheel_method(max_variants=B)
    wheel, cov = generate_wheel(
        method, pool, cfg["pick"], g, max_variants=B,
        draw_n=cfg["draw_n"], max_num=cfg["max_num"],
    )
    return pool, [tuple(t) for t in wheel], cov


def _greedy_wheel(game: str, v: int, g: int, B: int):
    cfg = GAMES[game]
    pool = list(range(1, v + 1))
    wheel, cov = generate_wheel(
        "greedy", pool, cfg["pick"], g, max_variants=B,
        draw_n=cfg["draw_n"], max_num=cfg["max_num"],
    )
    return pool, [tuple(t) for t in wheel], cov


def _objective(pool, tickets, game: str, target: int) -> float:
    cfg = GAMES[game]
    return wheel_hit_probabilities(pool, tickets, cfg["draw_n"], cfg["max_num"])["ticket"][target]


def _coverage_ok(pool, tickets, g: int) -> bool:
    v = len(pool)
    return wheel_hit_profile(pool, tickets)[g][g] == comb(v, g)


def _anneal(pool, start, game: str, target: int, g: int, seed: int, moves: int = MOVES):
    """Recoacere pe P(>=target); pastrata doar daca acoperirea g ramane 100%
    (verificata la fiecare pas acceptat)."""
    cfg = GAMES[game]
    v = len(pool)
    rng = np.random.default_rng(seed)
    tickets = [list(t) for t in start]
    best = [list(t) for t in start]
    cur = _objective(pool, [tuple(t) for t in tickets], game, target)
    best_obj = cur
    temp0 = 0.002
    for it in range(moves):
        temp = temp0 * (1.0 - it / moves) + 1e-6
        i = int(rng.integers(len(tickets)))
        j = int(rng.integers(len(tickets[i])))
        old = tickets[i][j]
        cands = [n for n in pool if n not in tickets[i]]
        if not cands:
            continue
        new = int(cands[rng.integers(len(cands))])
        if new == old:
            continue
        tickets[i][j] = new
        cand = [tuple(t) for t in tickets]
        if not _coverage_ok(pool, cand, g):
            tickets[i][j] = old
            continue
        obj = _objective(pool, cand, game, target)
        if obj >= cur or rng.random() < np.exp((obj - cur) / max(temp, 1e-12)):
            cur = obj
            if obj > best_obj:
                best_obj = obj
                best = [list(t) for t in tickets]
        else:
            tickets[i][j] = old
    return [tuple(t) for t in best], best_obj


def _cyclic_design(v: int, pick: int, g: int, max_blocks: int):
    """Design ciclic: blocuri de baza pe Z_v, toate shifturile mod v; se
    adauga blocuri greedy pana la acoperirea g sau max_blocks blocuri de baza."""
    targets = set(combinations(range(v), g))
    covered: set = set()
    base_blocks: list = []
    for block in combinations(range(v), pick):
        if len(base_blocks) >= max_blocks:
            break
        shifts = {(tuple(sorted((x + s) % v for x in block))) for s in range(v)}
        new_cov = set()
        for sh in shifts:
            for sub in combinations(sh, g):
                if sub in targets and sub not in covered:
                    new_cov.add(sub)
        if new_cov:
            base_blocks.append(block)
            covered |= new_cov
    tickets = set()
    for block in base_blocks:
        for s in range(v):
            tickets.add(tuple(sorted((x + s) % v + 1 for x in block)))  # pozitii 1..v
    return [tuple(t) for t in sorted(tickets)], len(covered), len(targets)


def run_config(game: str, v: int, g: int, B: int, target: int, kind: str) -> dict:
    cfg = GAMES[game]
    if kind == "complete_refine":
        pool, base, cov = _engine_wheel(game, v, g, 0)
    elif kind == "above_complete":
        pool, base, cov = _greedy_wheel(game, v, g, B)
    else:
        pool, base, cov = _greedy_wheel(game, v, g, B)
    rec = {
        "game": game, "v": v, "g": g, "B": B, "target": target, "kind": kind,
        "baseline_tickets": len(base),
        "baseline": wheel_hit_probabilities(pool, base, cfg["draw_n"], cfg["max_num"])["ticket"],
        "baseline_cov_g": wheel_hit_profile(pool, base)[g][g] / comb(v, g),
    }
    best_cand, best_gain = None, None
    for seed in SEEDS:
        cand, _obj = _anneal(pool, base, game, target, g, seed)
        why, gain, cov_after = _certify(pool, base, cand, g, cfg["draw_n"], cfg["max_num"])
        if not why:
            if best_gain is None or sum(gain.values()) > sum(best_gain.values()):
                best_cand, best_gain = cand, gain
    if best_cand is not None:
        rec["certified"] = True
        rec["gain"] = {str(t): best_gain[t] for t in best_gain}
        rec["candidate"] = wheel_hit_probabilities(pool, best_cand, cfg["draw_n"], cfg["max_num"])["ticket"]
        rec["candidate_cov_g"] = wheel_hit_profile(pool, best_cand)[g][g] / comb(v, g)
    else:
        rec["certified"] = False
    # referinta: designul complet (fara plafon) pentru comparatie above_complete
    if kind == "above_complete":
        _p, complete, _c = _engine_wheel(game, v, g, 0)
        rec["complete_tickets"] = len(complete)
        rec["complete"] = wheel_hit_probabilities(pool, complete, cfg["draw_n"], cfg["max_num"])["ticket"]
    return rec


def main() -> None:
    t0 = time.time()
    report: dict = {"protocol": __doc__, "configs": []}
    configs = []
    for game in GAMES:
        # above complete: 5+ cu buget peste complet
        for B in ((200, 300, 404) if game == "6/49" else (500, 600)):
            configs.append((game, 16, 4, B, 5, "above_complete"))
        # buget sub complet, g=3, tinta 4+
        for B in (64, 100):
            configs.append((game, 16, 3, B, 4, "budget_below"))
        # rafinare peste complet la v=14..16
        for v in (14, 15, 16):
            configs.append((game, v, 4, 0, 5, "complete_refine"))
    for cfg in configs:
        rec = run_config(*cfg)
        report["configs"].append(rec)
        b = rec["baseline"]
        line = (f"[{rec['game']}] v={rec['v']} g={rec['g']} B={rec['B']} t={rec['target']} "
                f"base={rec['baseline_tickets']}t "
                + " ".join(f"{t}+:{b[t]*100:.4f}%" for t in sorted(b) if t >= 3))
        if rec.get("certified"):
            c = rec["candidate"]
            line += " CERTIFIED " + " ".join(
                f"{t}+:{c[t]*100:.4f}%" for t in sorted(c) if t >= 3)
        else:
            line += " (fara imbunatatire certificata)"
        print(line, flush=True)
    # diagnostic ciclic 6/49 v=16 g=4
    tickets, cov, tot = _cyclic_design(16, 6, 4, max_blocks=12)
    pool = list(range(1, 17))
    cyc = {
        "game": "6/49", "kind": "cyclic", "tickets": len(tickets),
        "cov_g": cov / tot,
        "probs": wheel_hit_probabilities(pool, tickets, 6, 49)["ticket"],
    }
    _p, greedy, _c = _greedy_wheel("6/49", 16, 4, len(tickets))
    cyc["greedy_same_count"] = wheel_hit_probabilities(pool, greedy, 6, 49)["ticket"]
    report["cyclic"] = cyc
    print(f"[cyclic 6/49 v16 g4] {len(tickets)} t, cov4={cov/tot:.4f}", flush=True)
    report["runtime_s"] = time.time() - t0
    OUT.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(f"[gata] {len(report['configs'])} configuratii, {time.time()-t0:.0f}s -> {OUT.name}", flush=True)


if __name__ == "__main__":
    main()
