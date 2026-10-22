"""Verifică designurile livrate pentru pool-ul extins (2026-10-22).

Pentru fiecare fișier `covering_designs/extended/X_<v>_<bilet>_<extrase>_<univers>_<variante>.txt`:
pune designul pe un clasament canonic (1..v, scoruri descrescătoare), construiește
coverul complet al motorului pe primele 16 numere (La Jolla, fără plafon) și
rulează exact certificarea din producție (`covering.extended_pool.apply`).
Scrie șansele exacte pe fiecare prag și grupele de 4 acoperite; iese cu 1 dacă
vreun design nu se certifică.

Rulare din rădăcina proiectului:
    python scripts/analysis/extended_pool/verify_extended_designs.py
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from covering.dispatch import generate_wheel  # noqa: E402
from covering.extended_pool import DESIGN_DIR, apply  # noqa: E402

_NAME = re.compile(r"^X_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\.txt$")


def verify(path: Path) -> tuple[bool, str]:
    m = _NAME.match(path.name)
    if not m:
        return False, f"{path.name}: nume neașteptat"
    v, pick, draw_n, max_num, budget = (int(x) for x in m.groups())
    pool = list(range(1, v + 1))
    scores = {n: float(v + 1 - n) for n in pool}
    base = list(range(1, 17))
    incumbent, _cov = generate_wheel(
        "lajolla", base, pick, 4, 0, {n: scores[n] for n in base}, None, draw_n, max_num
    )
    candidate, audit = apply(pool, scores, incumbent, v, pick, draw_n, max_num)
    chances = audit.get("chances") or {}
    groups = audit.get("groups4") or {}
    parts = " ".join(
        f"{t}+ {100 * c['base']:.6f}%->{100 * c['extended']:.6f}%" for t, c in sorted(chances.items())
    )
    ok = candidate is not None and len(candidate) == budget
    status = "OK" if ok else f"RESPINS ({audit.get('reason')})"
    return ok, (
        f"{path.name}: {status} | {parts} | grupe de 4 {groups.get('base')}->"
        f"{groups.get('extended')} din {groups.get('pool_total')}"
    )


def main() -> int:
    logging.disable(logging.WARNING)
    files = sorted(DESIGN_DIR.glob("X_*.txt"))
    if not files:
        print(f"niciun design în {DESIGN_DIR}")
        return 1
    failed = 0
    for path in files:
        ok, line = verify(path)
        failed += not ok
        print(line, flush=True)
    print(f"{len(files) - failed}/{len(files)} certificate")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
