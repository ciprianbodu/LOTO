"""Designuri lotto „g dacă c” rafinate pentru pragurile pe bilet (2026-10-16).

Fără plafon, ramura lotto livrează designul local `covering_designs/L_v_pick_c_g.txt`
(sau greedy-ul pozițional), cu profilul îmbunătățit prin schimburi. Garanția
„g dacă c” nu fixează însă cum se așază biletele, deci P(cel puțin t pe un
bilet), la extrageri uniforme din universul jocului, mai are loc de creștere.

Pentru fiecare joc (6/49, 5/40, Joker Urna 1), pool 10..16 și fiecare pereche
3 <= g < c <= bilet, scriptul:
1. ia wheel-ul de producție pe pool canonic (`generate_wheel("lotto", ...)`,
   poziții 1..v, 1 = numărul cel mai bine clasat);
2. rulează recoacerea `lotto_anneal.c` (aceeași funcție exactă ca
   `covering/anneal_hits.py`, acoperirea urmărită fiind c-subseturile cu >= g
   pe un bilet) cu patru setări; la pool 16 se adaugă patru setări mai lungi;
3. păstrează un rezultat numai certificat: același număr de bilete distincte,
   numerele jucate rămân jucate, garanția „g dacă c” completă, niciun prag
   3..min(bilet, extrase) în scădere, câștig strict la cel puțin unul;
4. dintre cele certificate rămâne cel mai bun pe ordinea g, g+1.., apoi pragurile
   mai mici, scris în `covering_designs/refined/RL_<v>_<pick>_<g>_<c>_<extrase>_<univers>.txt`.

Producția recertifică fișierul la fiecare apel (`covering.refined_covers`,
`improve_with_refined_cover(..., condition=c)`), deci un fișier stricat sau mai
slab decât wheel-ul livrat nu intră. Rezumatul se scrie în
`refined_lotto_2026-10-16.json`.

Rulare din rădăcina proiectului (gcc necesar; ~2 ore pe 4 nuclee):
    python scripts/analysis/gen_refined_lotto.py [mutări] [procese] [pool-uri, ex. 10-16]
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

from covering.common import lotto_coverage_pct  # noqa: E402
from covering.dispatch import generate_wheel  # noqa: E402
from covering.probability import wheel_hit_probabilities, wheel_hit_profile  # noqa: E402
from covering.refined_covers import refined_cover_path  # noqa: E402

HERE = Path(__file__).resolve().parent
WORK = HERE / "refined_covers_work" / "lotto"
REPORT = HERE / "refined_lotto_2026-10-16.json"
OUT_DIR = ROOT / "covering_designs" / "refined"
GAMES = {"6/49": (6, 6, 49), "5/40": (5, 6, 40), "joker": (5, 5, 45)}  # bilet, extrase, univers
MIN_T = 3
# (eticheta, prag principal = g + decalaj, garanție tare, penalizare, mutări țintite, t0, seed)
SETTINGS = [
    ("A", 0, 1, 0.01, 0.5, 0.05, 1),
    ("B", 0, 1, 0.01, 0.5, 0.5, 2),
    ("C", 1, 1, 0.01, 0.5, 0.05, 3),
    ("D", 0, 0, 0.01, 0.5, 0.05, 4),
]
DEEP = [  # pool 16, de două ori mai multe mutări
    ("A3", 0, 1, 0.01, 0.5, 0.05, 11),
    ("B3", 0, 1, 0.01, 0.5, 0.5, 12),
    ("C3", 1, 1, 0.01, 0.5, 0.05, 13),
    ("E3", 0, 1, 0.01, 0.5, 0.2, 15),
]
MOVES = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 2
_lo, _hi = (sys.argv[3] if len(sys.argv) > 3 else "10-16").split("-")
POOLS = range(int(_lo), int(_hi) + 1)


def _binary() -> Path:
    exe = WORK / "lotto_anneal"
    src = HERE / "lotto_anneal.c"
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        WORK.mkdir(parents=True, exist_ok=True)
        subprocess.run(["gcc", "-O2", "-o", str(exe), str(src), "-lm"], check=True)
    return exe


def _read(path: Path) -> list[list[int]]:
    return [sorted(int(x) for x in ln.split()) for ln in path.read_text().splitlines() if ln.strip()]


def _write(path: Path, wheel) -> None:
    path.write_text("".join(" ".join(str(x) for x in sorted(t)) + "\n" for t in wheel))


def _probs(v: int, wheel, draw: int, mx: int) -> dict[int, float]:
    return wheel_hit_probabilities(list(range(1, v + 1)), wheel, draw, mx)["ticket"]


def _certify(v, before, after, g, c, draw, mx, pick) -> str:
    """Motivul respingerii; gol = certificat."""
    pool = list(range(1, v + 1))
    if len({tuple(t) for t in after}) != len(after) or len(after) != len({tuple(t) for t in before}):
        return "alt număr de bilete distincte"
    if any(len(set(t)) != pick for t in after):
        return "bilet invalid"
    if not {n for t in before for n in t} <= {n for t in after for n in t}:
        return "un număr jucat a ieșit"
    if wheel_hit_profile(pool, after)[c][g] < comb(v, c) or lotto_coverage_pct(after, pool, g, c) < 100.0:
        return "garanția lotto incompletă"
    b, a = _probs(v, before, draw, mx), _probs(v, after, draw, mx)
    top = min(pick, draw)
    if any(a[t] < b[t] - 1e-15 for t in range(MIN_T, top + 1)):
        return "scade la un prag"
    if not any(a[t] > b[t] + 1e-15 for t in range(MIN_T, top + 1)):
        return "fără câștig"
    return ""


def jobs() -> list[tuple]:
    out = []
    for game, (pick, draw, _mx) in GAMES.items():
        for v in POOLS:
            for g in range(MIN_T, pick):
                for c in range(g + 1, pick + 1):
                    out.append((game, v, g, c))
    return out


def run_one(job) -> dict:
    game, v, g, c = job
    pick, draw, mx = GAMES[game]
    top = min(pick, draw)
    pool = list(range(1, v + 1))
    scores = {n: float(v + 1 - n) for n in pool}
    wheel, _ = generate_wheel("lotto", pool, pick, g, 0, scores, condition=c, draw_n=draw)
    before = [sorted(int(x) for x in t[:pick]) for t in wheel]
    tag = f"{game.replace('/', '')}_{v}_{g}_{c}"
    WORK.mkdir(parents=True, exist_ok=True)
    start = WORK / f"start_{tag}.txt"
    _write(start, before)
    exe = _binary()
    best, best_key, best_lab = None, None, None
    order = [g, *range(g + 1, top + 1), *range(g - 1, MIN_T - 1, -1)]
    runs = [(s, MOVES) for s in SETTINGS] + ([(s, 2 * MOVES) for s in DEEP] if v == 16 else [])
    for (lab, poff, hard, pen, targ, t0f, seed), moves in runs:
        primary = g + poff
        if primary > top:
            continue
        out = WORK / f"out_{tag}_{lab}.txt"
        subprocess.run(
            [str(exe), str(v), str(pick), str(draw), str(mx), str(g), str(c), str(moves), str(seed),
             str(targ), str(t0f), "1000", str(pen), "1", str(start), str(out), str(primary), str(hard)],
            capture_output=True, text=True, check=True,
        )
        after = _read(out)
        if _certify(v, before, after, g, c, draw, mx, pick):
            continue
        p = _probs(v, after, draw, mx)
        key = tuple(p[t] for t in order if MIN_T <= t <= top)
        if best_key is None or key > best_key:
            best, best_key, best_lab = after, key, lab
    row = {"game": game, "v": v, "g": g, "c": c, "tickets": len(before), "setting": best_lab}
    if best is None:
        row["status"] = "fără design certificat"
        return row
    pb, pa = _probs(v, before, draw, mx), _probs(v, best, draw, mx)
    path = OUT_DIR / refined_cover_path(v, pick, g, draw, mx, condition=c)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Design lotto {g} daca {c}, pool {v}, bilet {pick}, rafinat pentru {draw} extrase din {mx}\n"
        f"# (pozitii 1..{v}, 1 = cel mai bine clasat). Derivat din covering_designs/L_{v}_{pick}_{c}_{g}.txt\n"
        "# prin recoacere in C (scripts/analysis/lotto_anneal.c); aceeasi garantie, acelasi numar de bilete.\n"
    )
    path.write_text(header + "".join(" ".join(str(x) for x in t) + "\n" for t in best))
    row.update(
        status="scris",
        file=str(path.relative_to(ROOT)),
        P_before={t: pb[t] for t in range(MIN_T, top + 1)},
        P_after={t: pa[t] for t in range(MIN_T, top + 1)},
        gain_rel_pct={t: (pa[t] / pb[t] - 1) * 100 for t in range(MIN_T, top + 1) if pb[t] > 0},
    )
    return row


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    _binary()
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        rows = list(ex.map(run_one, jobs()))
    REPORT.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    for r in rows:
        gains = " ".join(f"{t}+ {x:+.2f}%" for t, x in (r.get("gain_rel_pct") or {}).items() if x)
        print(f"{r['game']:5} pool {r['v']:2} {r['g']} daca {r['c']} {r['tickets']:3} bilete: {r['status']} {gains}")
    print("scrise:", sum(r["status"] == "scris" for r in rows), "din", len(rows))
