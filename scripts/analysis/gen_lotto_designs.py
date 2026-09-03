"""Precalculează lotto designs L(v, pick, condition, guarantee) cu buget MARE.

Analogul lui `gen_covering_designs.py` pentru modul „guarantee dacă condition":
orice submulțime de `condition` numere din pool are cel puțin `guarantee`
numere pe un bilet. Coverul e o constantă combinatorică (nu depinde de pool,
scoruri sau dată), deci se calculează o dată, cu solver mai răbdător decât cele
10 s din producție, și se scrie pe disc în format La Jolla (1-based, un bloc pe
linie). `wheeling_methods._load_lotto_design` îl validează la 100% înainte de
folosire; un fișier mai slab decât greedy-ul curent nu se scrie.

Geometriile UI: pool 6..16, pick 5 (5/40, Joker) și 6 (6/49), garanție 3..pick-1,
condiție guarantee+1..pick.

    GEN_BUDGET=60 python scripts/analysis/gen_lotto_designs.py [--only 11,5,4,3]
"""
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)
import wheeling_methods as wm  # noqa: E402

BUDGET = float(os.environ.get("GEN_BUDGET", "60"))
OUT = PROJECT_ROOT / "covering_designs"
OUT.mkdir(exist_ok=True)


def geometries():
    for pick in (5, 6):
        for v in range(6, 17):
            if v < pick:
                continue
            for g in range(3, pick):
                for c in range(g + 1, pick + 1):
                    if c > v:
                        continue
                    yield v, pick, c, g


def main() -> int:
    only = None
    if "--only" in sys.argv:
        only = tuple(int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(","))
    for v, pick, c, g in geometries():
        if only and (v, pick, c, g) != only:
            continue
        name = OUT / wm.lotto_design_path(v, pick, g, c)
        existing = wm._load_lotto_design(v, pick, g, c)
        t0 = time.perf_counter()
        wm._LOTTO_COVER_CACHE.pop((v, pick, g, c), None)
        try:
            cover = wm._lotto_cover_positions(v, pick, g, c, time_limit=BUDGET)
        except ValueError as exc:
            print(f"L({v},{pick},{c},{g}): sar ({exc})", flush=True)
            continue
        cov = wm.lotto_coverage_pct([list(b) for b in cover], list(range(v)), g, c)
        if cov < 100.0:
            print(f"L({v},{pick},{c},{g}): acoperire {cov}% — NU scriu", flush=True)
            continue
        if existing is not None and len(existing) <= len(cover):
            print(f"L({v},{pick},{c},{g}): existent {len(existing)} <= nou {len(cover)} — păstrez", flush=True)
            continue
        name.write_text("".join(" ".join(str(i + 1) for i in b) + "\n" for b in cover), encoding="utf-8")
        print(f"L({v},{pick},{c},{g}): {len(cover)} bilete scrise în {name.name} ({time.perf_counter()-t0:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
