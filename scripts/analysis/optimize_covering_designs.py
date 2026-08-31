"""Optimizează (micșorează) coverurile din `covering_designs/` prin local search
ruin-and-recreate, pornind de la designul EXISTENT (nu de la zero).

De ce: `gen_covering_designs.py` alege min(ILP@90s, greedy) per geometrie. Pe
geometriile mari (pick=6, guarantee=5, v mare) ILP-ul cu buget 90s nu se apropie
de optim, deci fișierul e practic greedy. Limita Schönheim (rigurosă matematic,
verificată aici pe cazuri cu sisteme Steiner cunoscute: C(13,3,2)=26, C(14,4,3)=91
— egalitate exactă) arată un gol de pana la 35% pe cele mai mari geometrii.

Algoritm per geometrie:
  1. Elimină bilete redundante din designul curent (target-elee lor sunt
     acoperite și de alte bilete) — determinist, fără pierdere de acoperire.
  2. Ruin-and-recreate: scoate un procent din biletele cu cea mai puțină
     acoperire UNICĂ, apoi reface acoperirea cu pași greedy (blocul care
     acoperă cele mai multe ținte încă neacoperite). Ține rezultatul doar
     dacă e STRICT mai mic și verificat independent la 100% acoperire.

Nu schimbă niciun fișier direct — scrie candidați în `covering_designs/` DOAR
prin `--apply`, și doar dacă noul cover e mai mic și validat.
"""
from __future__ import annotations

import argparse
import itertools
import random
import sys
import time
from math import ceil, comb
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIR = ROOT / "covering_designs"


def _schonheim(v: int, k: int, t: int) -> int:
    def rec(vv: int, kk: int, tt: int) -> int:
        if tt == 1:
            return ceil(vv / kk)
        return ceil(vv / kk * rec(vv - 1, kk - 1, tt - 1))
    return rec(v, k, t)


def load_design(path: Path) -> list[tuple[int, ...]]:
    return [
        tuple(sorted(int(x) for x in ln.split()))
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def write_design(path: Path, blocks: list[tuple[int, ...]]) -> None:
    text = "\n".join(" ".join(str(x) for x in b) for b in blocks) + "\n"
    path.write_text(text, encoding="utf-8")


def coverage_ok(blocks: list[tuple[int, ...]], v: int, t: int) -> bool:
    """Recalcul INDEPENDENT (nu reutilizează structurile din optimizator)."""
    need = set(itertools.combinations(range(1, v + 1), t))
    for b in blocks:
        for s in itertools.combinations(sorted(b), t):
            need.discard(s)
    return not need


def block_targets(block: tuple[int, ...], tidx: dict, t: int) -> list[int]:
    return [tidx[s] for s in itertools.combinations(sorted(block), t)]


def remove_redundant(
    cur: list[tuple[int, ...]],
    cur_bt: list[list[int]],
    cover_count: np.ndarray,
) -> None:
    changed = True
    while changed:
        changed = False
        # întâi biletele cu cele mai puține ținte (mai ieftin de verificat/scos)
        order = sorted(range(len(cur)), key=lambda i: len(cur_bt[i]))
        for i in order:
            if cur_bt[i] and all(cover_count[x] > 1 for x in cur_bt[i]):
                for x in cur_bt[i]:
                    cover_count[x] -= 1
                cur.pop(i)
                cur_bt.pop(i)
                changed = True
                break


def optimize(
    v: int, k: int, t: int, design: list[tuple[int, ...]], budget_s: float,
    seed: int = 42,
) -> list[tuple[int, ...]]:
    pool = list(range(1, v + 1))
    targets = list(itertools.combinations(pool, t))
    tidx = {tt: i for i, tt in enumerate(targets)}
    nt = len(targets)

    all_blocks = list(itertools.combinations(pool, k))
    all_bt = [block_targets(b, tidx, t) for b in all_blocks]

    cur = [tuple(b) for b in design]
    cur_bt = [block_targets(b, tidx, t) for b in cur]
    cover_count = np.zeros(nt, dtype=np.int32)
    for bt in cur_bt:
        for x in bt:
            cover_count[x] += 1

    remove_redundant(cur, cur_bt, cover_count)
    best = [tuple(b) for b in cur]
    best_n = len(best)

    rng = random.Random(seed)
    start = time.time()
    iters = 0
    improved = 0
    while time.time() - start < budget_s:
        iters += 1
        n_remove = max(1, int(0.08 * len(cur)))
        uniq_count = [sum(1 for x in bt if cover_count[x] == 1) for bt in cur_bt]
        order = sorted(range(len(cur)), key=lambda i: uniq_count[i])
        # amestecăm puțin printre cele "slabe" ca să nu rămânem blocați într-un ciclu
        weak = order[: max(n_remove * 3, n_remove)]
        rng.shuffle(weak)
        remove_idx = set(weak[:n_remove])

        new_cur = [cur[i] for i in range(len(cur)) if i not in remove_idx]
        new_bt = [cur_bt[i] for i in range(len(cur)) if i not in remove_idx]
        new_cc = cover_count.copy()
        for i in remove_idx:
            for x in cur_bt[i]:
                new_cc[x] -= 1

        uncovered = set(np.where(new_cc == 0)[0].tolist())
        # refill greedy: blocul care acoperă cele mai multe ținte NEACOPERITE
        guard = 0
        while uncovered and guard < 200:
            guard += 1
            best_i, best_cov = -1, 0
            # sondaj pe un subset dacă universul de blocuri e mare, ca să rămânem rapizi
            candidates = range(len(all_blocks))
            if len(all_blocks) > 4000:
                candidates = rng.sample(range(len(all_blocks)), 4000)
            for i in candidates:
                c = sum(1 for x in all_bt[i] if x in uncovered)
                if c > best_cov:
                    best_cov, best_i = c, i
            if best_i < 0 or best_cov == 0:
                break
            b = all_blocks[best_i]
            bt = all_bt[best_i]
            new_cur.append(b)
            new_bt.append(bt)
            for x in bt:
                if x in uncovered:
                    uncovered.discard(x)
                new_cc[x] += 1

        if uncovered:
            continue  # refill incomplet (n-ar trebui, dar nu riscăm o acoperire falsă)

        remove_redundant(new_cur, new_bt, new_cc)
        if len(new_cur) < best_n:
            # verificare INDEPENDENTĂ înainte să acceptăm ca "best"
            if coverage_ok(new_cur, v, t):
                best = [tuple(b) for b in new_cur]
                best_n = len(best)
                improved += 1
                cur, cur_bt, cover_count = new_cur, new_bt, new_cc
                continue
        # continuăm exploarea din starea nouă doar dacă nu-i mult mai proastă
        if len(new_cur) <= best_n + max(2, int(0.03 * best_n)):
            cur, cur_bt, cover_count = new_cur, new_bt, new_cc

    print(f"  C({v},{k},{t}): {len(design)} -> {best_n} bilete "
          f"({iters} iterații ruin-recreate, {improved} îmbunătățiri, {budget_s:.0f}s)",
          flush=True)
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="scrie fișierele îmbunătățite")
    ap.add_argument("--budget", type=float, default=60.0, help="secunde per geometrie")
    ap.add_argument("--only", type=str, default="", help="ex. 16_6_5,15_6_5")
    ap.add_argument("--min-gap-pct", type=float, default=5.0,
                     help="sări geometriile cu gol < X%% față de Schönheim (deja aproape de limită)")
    args = ap.parse_args()

    only = {tuple(int(x) for x in s.split("_")) for s in args.only.split(",") if s}

    paths = sorted(DESIGN_DIR.glob("C_*_*_*.txt"))
    rows = []
    for p in paths:
        v, k, t = (int(x) for x in p.stem.replace("C_", "").split("_"))
        if only and (v, k, t) not in only:
            continue
        n = sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())
        rows.append((n, v, k, t, p))
    rows.sort(reverse=True)  # cele mai mari (probabil cel mai mult gol) primele

    total_before = total_after = 0
    skipped = 0
    for n, v, k, t, p in rows:
        if v == k:
            continue  # C(v,v,t) = 1 bilet, deja optim (limita triviala)
        lb = _schonheim(v, k, t)
        gap_pct = 100.0 * (n - lb) / max(n, 1)
        if n <= lb or gap_pct < args.min_gap_pct:
            skipped += 1
            print(f"  skip C({v},{k},{t}): {n} bilete, Schönheim {lb}, "
                  f"gol {gap_pct:.1f}% < {args.min_gap_pct:g}%", flush=True)
            continue
        design = load_design(p)
        assert coverage_ok(design, v, t), f"{p.name}: designul EXISTENT nu e 100%% — stop"
        improved = optimize(v, k, t, design, args.budget)
        total_before += len(design)
        total_after += len(improved)
        if len(improved) < len(design):
            assert coverage_ok(improved, v, t), f"{p.name}: candidatul nou NU e 100%% — refuz"
            if args.apply:
                write_design(p, improved)
                print(f"    scris {p.name}: {len(design)} -> {len(improved)}", flush=True)
    print(f"\nTOTAL: {total_before} -> {total_after} "
          f"({'scris' if args.apply else 'DRY-RUN, nescris'}); "
          f"sărite {skipped} (gol < {args.min_gap_pct:g}% vs Schönheim)")


if __name__ == "__main__":
    main()
