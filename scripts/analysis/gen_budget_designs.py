"""Designuri cu buget (65+ bilete) cautate offline prin recoacere in C (2026-10-12).

Pentru fiecare geometrie (azi 6/49: pool 13..16, bilet 6, garantie 4) si fiecare
buget intre 65 si designul complet exclusiv: wheel-ul de productie
(`generate_wheel("hitcover", ..., max_num=...)`, adica greedy + schimburi de
profil + `budget_climb`) e punctul de plecare al recoacerii din
`budget_anneal.c` (sansa EXACTA ca cel putin un bilet sa atinga t, aceeasi
functie ca `covering.probability`). Rezultatul e pastrat numai certificat fata
de productie cu regula din `covering.anneal_hits`: acelasi numar de bilete
distincte, numerele jucate raman jucate, niciun prag 3..draw_n nu scade,
castig strict, acoperirea garantiei nu scade. Mai multe rulari (seed-uri,
mutari) pe acelasi buget: ramane cea mai buna la 4+, apoi la 5+.

Scrie `covering_designs/budget/H_<v>_<pick>_<g>.txt` (sectiuni `B <n>` urmate de
n linii de pozitii 1..v) si `budget_designs_2026-10-12.json` (cifrele). Loader-ul
de productie (`covering.budget_designs`) recertifica designul la fiecare apel.

Rulare din radacina proiectului (gcc necesar; ~1 h pe 3 procese la 3M mutari):
    python scripts/analysis/gen_budget_designs.py [workers] [moves] [seeds, ex. 1,2]
Rularile se pot relua: rezultatele partiale stau in `budget_designs_work/`.
"""

from __future__ import annotations

import json
import logging
import shutil
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
from covering.probability import wheel_hit_probabilities, wheel_hit_profile  # noqa: E402

HERE = Path(__file__).resolve().parent
WORK = HERE / "budget_designs_work"
OUT_DIR = ROOT / "covering_designs" / "budget"
REPORT = HERE / "budget_designs_2026-10-12.json"
GEOMETRIES = [  # (max_num, draw_n, pick, guarantee, pool-uri)
    (49, 6, 6, 4, (13, 14, 15, 16)),
]
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
MOVES = int(sys.argv[2]) if len(sys.argv) > 2 else 3_000_000
SEEDS = [int(s) for s in (sys.argv[3].split(",") if len(sys.argv) > 3 else ["1", "2"])]


def _binary() -> Path:
    exe = WORK / "budget_anneal"
    src = HERE / "budget_anneal.c"
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        WORK.mkdir(parents=True, exist_ok=True)
        subprocess.run(["gcc", "-O2", "-o", str(exe), str(src), "-lm"], check=True)
    return exe


def run(cfg):
    max_num, draw_n, pick, g, v, budget = cfg
    target = WORK / f"{max_num}_{draw_n}_{v}_{pick}_{g}_{budget}.json"
    row = json.loads(target.read_text()) if target.exists() else None
    pool = list(range(1, v + 1))
    if row is None:
        scores = {n: float(v - i) for i, n in enumerate(pool)}
        t0 = time.perf_counter()
        prod, cov = generate_wheel("hitcover", pool, pick, g, budget, scores, draw_n=draw_n, max_num=max_num)
        prod = [sorted(int(n) for n in t) for t in prod]
        pb = wheel_hit_probabilities(pool, prod, draw_n, max_num)["ticket"]
        row = {
            "max_num": max_num, "draw_n": draw_n, "v": v, "pick": pick, "g": g, "B": budget,
            "design": complete_design_size(v, pick, g), "t_prod": time.perf_counter() - t0,
            "cov_prod": cov, "prod": prod, "p_prod": {str(t): pb[t] for t in range(3, min(pick, draw_n) + 1)},
            "cov_g_prod": wheel_hit_profile(pool, prod)[g][g], "runs": [], "best": None,
        }
    prod = row["prod"]
    exe = _binary()
    for seed in SEEDS:
        if any(r["seed"] == seed and r["moves"] == MOVES for r in row["runs"]):
            continue
        start = WORK / f"start_{v}_{budget}_{seed}.txt"
        out = WORK / f"out_{v}_{budget}_{seed}.txt"
        start.write_text("".join(" ".join(str(n) for n in t) + "\n" for t in prod))
        cmd = [str(exe), str(v), str(pick), str(draw_n), str(max_num), str(g), str(MOVES), str(seed),
               "0.5", "0.05", "1000", "100", "1", str(start), str(out)]
        t1 = time.perf_counter()
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        found = [sorted(int(q) for q in line.split()) for line in out.read_text().splitlines() if line.strip()]
        why, _gain, _cov = _certify(pool, prod, found, g, draw_n, max_num)
        pa = wheel_hit_probabilities(pool, found, draw_n, max_num)["ticket"] if not why else None
        row["runs"].append({"seed": seed, "moves": MOVES, "ok": not why, "why": why,
                            "secs": time.perf_counter() - t1,
                            "p": {str(t): pa[t] for t in range(3, min(pick, draw_n) + 1)} if pa else None})
        best = row["best"]
        if not why and (
            best is None
            or pa[g] > best["p"][str(g)] + 1e-15
            or (abs(pa[g] - best["p"][str(g)]) <= 1e-15 and g + 1 <= draw_n and pa[g + 1] > best["p"].get(str(g + 1), 0) + 1e-15)
        ):
            row["best"] = {"seed": seed, "moves": MOVES, "tickets": found,
                           "p": {str(t): pa[t] for t in range(3, min(pick, draw_n) + 1)},
                           "cov_g": wheel_hit_profile(pool, found)[g][g]}
        start.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    target.write_text(json.dumps(row))
    return row


def configs():
    out = []
    for max_num, draw_n, pick, g, pools in GEOMETRIES:
        for v in pools:
            size = complete_design_size(v, pick, g)
            if size is None:
                continue
            out += [(max_num, draw_n, pick, g, v, budget) for budget in range(65, size)]
    return out


def write_files(rows) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_geometry: dict[tuple, list] = {}
    for r in rows:
        if r["best"]:
            by_geometry.setdefault((r["v"], r["pick"], r["g"]), []).append(r)
    for (v, pick, g), items in sorted(by_geometry.items()):
        lines = [
            f"# Designuri cu buget pentru C({v},{pick},{g}): bilete pe pozitii 1..{v}",
            "# (1 = numarul cel mai bine clasat), cautate offline cu budget_anneal.c si",
            "# certificate fata de wheel-ul de productie (scripts/analysis/gen_budget_designs.py).",
            "# Fiecare sectiune `B n` e urmata de n bilete; loader-ul le recertifica la fiecare apel.",
        ]
        for r in sorted(items, key=lambda x: x["B"]):
            lines.append(f"B {r['B']}")
            lines += [" ".join(str(n) for n in t) for t in r["best"]["tickets"]]
        (OUT_DIR / f"H_{v}_{pick}_{g}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    cfgs = configs()
    print(f"{len(cfgs)} configuratii, {MOVES} mutari, seed-uri {SEEDS}, {WORKERS} procese", flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    _binary()
    rows = []
    t_all = time.perf_counter()
    with ProcessPoolExecutor(WORKERS) as ex:
        for r in ex.map(run, cfgs):
            rows.append(r)
            g = r["g"]
            best = r["best"]
            gain = (best["p"][str(g)] / r["p_prod"][str(g)] - 1) * 100 if best else 0.0
            print(
                f"pool {r['v']} B{r['B']:3d}/{r['design']} {g}+ productie {100 * r['p_prod'][str(g)]:.4f}% "
                f"-> {100 * best['p'][str(g)] if best else 0:.4f}% ({gain:+.2f}%)"
                f"{'' if best else ' fara castig certificat'}",
                flush=True,
            )
    write_files(rows)
    summary = []
    for r in rows:
        g = r["g"]
        summary.append({k: r[k] for k in ("max_num", "draw_n", "v", "pick", "g", "B", "design", "cov_prod", "p_prod", "cov_g_prod")}
                       | {"best": ({k: r["best"][k] for k in ("seed", "moves", "p", "cov_g")} if r["best"] else None),
                          "runs": r["runs"]})
    REPORT.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    kept = sum(1 for r in rows if r["best"])
    print(f"{kept}/{len(rows)} designuri scrise in {OUT_DIR}, {time.perf_counter() - t_all:.0f}s", flush=True)
    shutil.rmtree(WORK / "__pycache__", ignore_errors=True)


if __name__ == "__main__":
    main()
