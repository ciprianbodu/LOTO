"""Generează covering_designs/MANIFEST.json — manifestul tuturor designurilor:
geometrie (din numele fișierului), sha256, număr de bilete și acoperirea
structurală a garanției (1.0 = fiecare submulțime cerută e acoperită).

Rulare:  .venv/Scripts/python.exe scripts/analysis/gen_design_manifest.py
Ieșire:  covering_designs/MANIFEST.json (comis; test_design_manifest.py
verifică că hash-urile sunt încă proaspete, deci manifestul nu poate rămâne
în urmă față de fișiere).

Tipuri: C (cover clasic), L (lotto „t dacă p"), H (buget, secțiuni „B n"),
R/RL (covere rafinate), X (pool extins, studiu), LR (scheme Loteria Română).
Pentru H/X/LR nu există garanție completă prin construcție — acoperirea e
null (H păstrează doar numărul de bilete per secțiune).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DESIGNS = ROOT / "covering_designs"
OUT = DESIGNS / "MANIFEST.json"


def _rows(text: str) -> list[tuple[int, ...]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        nums = [int(x) for x in line.replace(",", " ").split()]
        if nums:
            out.append(tuple(nums))
    return out


def _cover_frac(tickets: list[tuple[int, ...]], v: int, g: int, cond: int | None) -> float | None:
    """Fracția de submulțimi acoperite; 1.0 = garanție completă. Bitmask-uri."""
    tmasks = [sum(1 << (n - 1) for n in t) for t in tickets]
    if cond:  # „t dacă p”: fiecare p-submulțime are un bilet cu ≥ g comune
        need = 0
        covered = 0
        for sub in combinations(range(v), cond):
            sm = sum(1 << i for i in sub)
            need += 1
            if any(bin(sm & tm).count("1") >= g for tm in tmasks):
                covered += 1
        return covered / need if need else 1.0
    need = 0
    covered = 0
    for sub in combinations(range(v), g):
        sm = sum(1 << i for i in sub)
        need += 1
        if any((sm & tm) == sm for tm in tmasks):
            covered += 1
    return covered / need if need else 1.0


def _entry(path: Path) -> dict:
    rel = path.relative_to(DESIGNS).as_posix()
    text = path.read_text(encoding="utf-8-sig")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    name = path.name
    e: dict = {"file": rel, "sha256": sha}
    m = re.match(r"C_(\d+)_(\d+)_(\d+)\.txt$", name)
    if m:
        v, pick, g = map(int, m.groups())
        rows = _rows(text)
        e.update(kind="C", v=v, pick=pick, guarantee=g, tickets=len(rows),
                 coverage=_cover_frac(rows, v, g, None))
        return e
    m = re.match(r"L_(\d+)_(\d+)_(\d+)_(\d+)\.txt$", name)
    if m:
        v, pick, cond, t = map(int, m.groups())
        rows = _rows(text)
        e.update(kind="L", v=v, pick=pick, guarantee=t, condition=cond, tickets=len(rows),
                 coverage=_cover_frac(rows, v, t, cond))
        return e
    m = re.match(r"H_(\d+)_(\d+)_(\d+)\.txt$", name)
    if m:
        v, pick, g = map(int, m.groups())
        sections: list[dict] = []
        cur_n, cur = 0, []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("B "):
                if cur:
                    sections.append({"budget": cur_n, "tickets": len(cur)})
                cur_n, cur = int(s.split()[1]), []
            elif s and not s.startswith("#"):
                cur.append(tuple(int(x) for x in s.split()))
        if cur:
            sections.append({"budget": cur_n, "tickets": len(cur)})
        e.update(kind="H", v=v, pick=pick, guarantee=g, sections=sections,
                 tickets=sum(s["tickets"] for s in sections), coverage=None)
        return e
    m = re.match(r"RL_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\.txt$", name)
    if m:
        v, pick, g, cond = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        rows = _rows(text)
        e.update(kind="RL", v=v, pick=pick, guarantee=g, condition=cond, tickets=len(rows),
                 coverage=_cover_frac(rows, v, g, cond))
        return e
    m = re.match(r"R_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\.txt$", name)
    if m:
        v, pick, g = int(m.group(1)), int(m.group(2)), int(m.group(3))
        rows = _rows(text)
        e.update(kind="R", v=v, pick=pick, guarantee=g, tickets=len(rows),
                 coverage=_cover_frac(rows, v, g, None))
        return e
    m = re.match(r"X_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\.txt$", name)
    if m:
        v, pick = int(m.group(1)), int(m.group(2))
        rows = _rows(text)
        e.update(kind="X", v=v, pick=pick, tickets=len(rows), coverage=None)
        return e
    m = re.match(r"LR_(649|540|joker)_cod(\d+)\.txt$", name)
    if m:
        rows = _rows(text)
        n = int(re.search(r"#\s*numere:\s*(\d+)", text).group(1))
        e.update(kind="LR", game=m.group(1), code=int(m.group(2)), v=n,
                 tickets=len(rows), coverage=None)
        return e
    e.update(kind="?", tickets=len(_rows(text)), coverage=None)
    return e


def main() -> int:
    files = sorted(p for p in DESIGNS.rglob("*.txt") if p.name != "MANIFEST.json")
    entries = [_entry(p) for p in files]
    from collections import Counter
    kinds = Counter(e["kind"] for e in entries)
    full = [e for e in entries if e["kind"] in ("C", "L", "R", "RL")]
    bad = [e["file"] for e in full if e["coverage"] != 1.0]
    manifest = {
        "generated": "2026-11-01",
        "generator": "scripts/analysis/gen_design_manifest.py",
        "note": ("sha256 peste bytes-ul fișierului; coverage = fracția de submulțimi "
                 "ale garanției acoperite (1.0 = complet). H/X/LR au coverage null "
                 "(fără garanție completă prin construcție). test_design_manifest.py "
                 "verifică prospețimea hash-urilor."),
        "counts": dict(sorted(kinds.items())),
        "total_files": len(entries),
        "entries": entries,
    }
    OUT.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"manifest: {len(entries)} fișiere, counts={dict(sorted(kinds.items()))}")
    print(f"covere complete (C/L/R/RL): {len(full)}, cu coverage != 1.0: {bad}")
    print(f"scris: {OUT}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
