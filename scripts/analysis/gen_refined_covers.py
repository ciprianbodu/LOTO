"""Covere complete rafinate pentru pragurile de peste garanție (2026-10-16).

Pe calea fără plafon, coverul complet intră în producție așa cum e în fișierul
`covering_designs/C_v_k_t.txt`. Cu garanția completă, P(cel puțin garanția pe
un bilet) e deja maximul pool-ului; pragul următor (g+1) depinde însă de cum
sunt așezate biletele. Pentru fiecare geometrie de joc (6/49, 5/40, Joker
Urna 1), recoacerea din `budget_anneal.c`, pornită din wheel-ul de producție pe
pool canonic, urmărește pragul g+1 (al 16-lea argument) cu acoperirea
penalizată; se păstrează numai un design complet, certificat față de
producție cu regula `covering.anneal_hits._certify`: același număr de bilete
distincte, numerele jucate rămân jucate, niciun prag 3..extrase nu scade,
câștig strict, acoperirea garanției rămâne 100%. Mai multe rulări pe aceeași
geometrie: rămâne cea mai bună la g+1, apoi la pragurile mai mari.

Scrie `covering_designs/refined/R_<v>_<pick>_<g>_<extrase>_<univers>.txt`
(poziții 1..v, 1 = numărul cel mai bine clasat) și
`refined_covers_2026-10-16.json`. Fișierele La Jolla din `covering_designs/`
rămân neatinse; producția recertifică designul rafinat la fiecare apel
(`covering.refined_covers`).

Rulare din rădăcina proiectului (gcc necesar):
    python scripts/analysis/gen_refined_covers.py [mutări] [procese] [pool-uri, ex. 9-13]
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

from covering.anneal_hits import _certify  # noqa: E402
from covering.designs import complete_design_size  # noqa: E402
from covering.dispatch import generate_wheel  # noqa: E402
from covering.probability import wheel_hit_probabilities  # noqa: E402
from covering.refined_covers import refined_cover_path  # noqa: E402

HERE = Path(__file__).resolve().parent
WORK = HERE / "refined_covers_work"
OUT_DIR = ROOT / "covering_designs" / "refined"
REPORT = HERE / "refined_covers_2026-10-16.json"
GAMES = {"6/49": (49, 6, 6), "5/40": (40, 6, 5), "joker": (45, 5, 5)}  # univers, extrase, bilet
# (penalizare, fracția mutărilor țintite, temperatura inițială ca fracție din P(g+1), seed)
SETTINGS = [(0.01, 0.5, 0.05, 1), (0.05, 0.3, 0.05, 2), (0.01, 0.5, 0.05, 3), (0.002, 0.5, 0.5, 4),
            (0.02, 0.5, 0.1, 5), (0.05, 0.3, 0.05, 6)]
MOVES = int(sys.argv[1]) if len(sys.argv) > 1 else 3_000_000
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 2
POOLS = range(*(lambda a, b: (int(a), int(b) + 1))(*(sys.argv[3] if len(sys.argv) > 3 else "6-16").split("-")))


def _binary() -> Path:
    exe = WORK / "budget_anneal"
    src = HERE / "budget_anneal.c"
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        WORK.mkdir(parents=True, exist_ok=True)
        subprocess.run(["gcc", "-O2", "-o", str(exe), str(src), "-lm"], check=True)
    return exe


def geometries():
    out = []
    for game, (mx, draw, pick) in GAMES.items():
        top = min(pick, draw)
        for g in (3, 4, 5):
            primary = g + 1
            if g >= pick or primary > top or primary == pick == draw:
                continue  # nimic peste garanție, sau numai marele premiu (invariant)
            for v in POOLS:
                if v <= pick:
                    continue
                size = complete_design_size(v, pick, g)
                if size is not None and size >= 2:
                    out.append((game, v, g))
    return out


def _key(p: dict, primary: int, top: int) -> tuple:
    return tuple(p[t] for t in [primary, *range(primary + 1, top + 1), *range(primary - 1, 2, -1)])


def run(cfg):
    game, v, g = cfg
    mx, draw, pick = GAMES[game]
    top = min(pick, draw)
    primary = g + 1
    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    prod, _cov = generate_wheel("lajolla", pool, pick, g, 0, scores, draw_n=draw, max_num=mx)
    prod = [sorted(int(n) for n in t) for t in prod]
    pb = wheel_hit_probabilities(pool, prod, draw, mx)["ticket"]
    tag = f"{game.replace('/', '')}_{v}_{g}"
    start = WORK / f"start_{tag}.txt"
    start.write_text("".join(" ".join(map(str, t)) + "\n" for t in prod))
    exe = _binary()
    best, runs = None, []
    for pen, targ, t0f, seed in SETTINGS:
        out = WORK / f"out_{tag}_{seed}.txt"
        t0 = time.perf_counter()
        subprocess.run([str(exe), str(v), str(pick), str(draw), str(mx), str(g), str(MOVES), str(seed),
                        str(targ), str(t0f), "1000", str(pen), "1", str(start), str(out), str(primary)],
                       capture_output=True, text=True, check=True)
        found = [sorted(int(q) for q in line.split()) for line in out.read_text().splitlines() if line.strip()]
        why, _gain, _c = _certify(pool, prod, found, g, draw, mx)
        pa = wheel_hit_probabilities(pool, found, draw, mx)["ticket"] if not why else None
        runs.append({"seed": seed, "setting": [pen, targ, t0f], "why": why, "secs": time.perf_counter() - t0,
                     "p": {str(t): pa[t] for t in range(3, top + 1)} if pa else None})
        if pa and (best is None or _key(pa, primary, top) > _key(best["p_num"], primary, top)):
            best = {"seed": seed, "tickets": found, "p_num": {t: pa[t] for t in range(3, top + 1)}}
        out.unlink(missing_ok=True)
    start.unlink(missing_ok=True)
    row = {"game": game, "max_num": mx, "draw_n": draw, "pick": pick, "v": v, "g": g, "primary": primary,
           "size": len(prod), "moves": MOVES, "p_prod": {str(t): pb[t] for t in range(3, top + 1)}, "runs": runs,
           "best": None}
    if best:
        row["best"] = {"seed": best["seed"], "tickets": best["tickets"],
                       "p": {str(t): best["p_num"][t] for t in range(3, top + 1)},
                       "gain_primary_pct": (best["p_num"][primary] / pb[primary] - 1) * 100}
    return row


def write_file(row) -> Path:
    path = OUT_DIR / refined_cover_path(row["v"], row["pick"], row["g"], row["draw_n"], row["max_num"])
    lines = [
        f"# Cover complet C({row['v']},{row['pick']},{row['g']}) rafinat pentru {row['primary']}+ pe "
        f"{row['draw_n']} extrase din {row['max_num']} (pozitii 1..{row['v']}, 1 = cel mai bine clasat).",
        "# Derivat din designul La Jolla din covering_designs/ (CC BY 4.0, vezi README), rearanjat",
        "# prin scripts/analysis/gen_refined_covers.py; aceeasi garantie, acelasi numar de bilete.",
    ]
    lines += [" ".join(str(n) for n in t) for t in row["best"]["tickets"]]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    cfgs = geometries()
    print(f"{len(cfgs)} geometrii, {MOVES} mutari x {len(SETTINGS)} rulari, {WORKERS} procese", flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    _binary()
    rows = []
    with ProcessPoolExecutor(WORKERS) as ex:
        for r in ex.map(run, cfgs):
            rows.append(r)
            p = str(r["primary"])
            gain = r["best"]["gain_primary_pct"] if r["best"] else 0.0
            print(f"{r['game']:5s} pool {r['v']:2d} g{r['g']} ({r['size']:3d} bilete) {p}+ "
                  f"{100 * r['p_prod'][p]:.5f}% -> {100 * r['best']['p'][p] if r['best'] else 0:.5f}% ({gain:+.2f}%)",
                  flush=True)
    for r in rows:
        if r["best"]:
            write_file(r)
    REPORT.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"{sum(1 for r in rows if r['best'])}/{len(rows)} covere rafinate scrise in {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
