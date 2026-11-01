"""Audit exhaustiv 2026-11-01 — verificarea prin enumerare brute-force a TUTUROR
designurilor din covering_designs/.

Pentru fiecare design × geometrie eligibilă:
  1. verificare structurală a garanției (fiecare submulțime cerută e acoperită
     de un bilet) — ieftină, CPU;
  2. enumerare exhaustivă a tuturor C(max_num, draw_n) extrageri uniforme
     (covering/exhaustive.py, GPU dacă cupy e disponibil) și comparație cu
     numărătoarea combinatorială exactă (covering/probability.py) — abaterea
     maximă trebuie să fie 0 (ambele sunt exacte, căile sunt independente).

Rulare (stația cu RTX, venv cu cupy):
  /tmp/gpu_bench_venv/Scripts/python.exe scripts/analysis/audit_exhaustive_2026-11-01.py --gpu
Producție (CPU-only):
  .venv/Scripts/python.exe scripts/analysis/audit_exhaustive_2026-11-01.py --cpu --limit 20

Ieșire: scripts/analysis/audit_exhaustive_2026-11-01.json + .md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from itertools import combinations
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from covering.exhaustive import enumerate_hit_histogram, gpu_available, verify_wheel_exact  # noqa: E402
from covering.probability import wheel_hit_probabilities  # noqa: E402

DESIGNS = ROOT / "covering_designs"

# geometrie după joc: (pick, draw_n, max_num)
GAMES = {"649": (6, 6, 49), "540": (5, 6, 40), "joker": (5, 5, 45)}


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


def _geoms_for_pick(pick: int) -> list[tuple[int, int]]:
    """pick 6 → doar 6/49; pick 5 → și 5/40 (draw 6/40) și Joker (draw 5/45)."""
    if pick == 6:
        return [(6, 49)]
    return [(6, 40), (5, 45)]


def collect() -> list[dict]:
    """Toate intrările: (file, kind, v, pick, guarantee, condition, draw_n, max_num, tickets)."""
    entries: list[dict] = []

    def add(path: Path, kind: str, v: int, pick: int, g: int, cond: int | None,
            draw_n: int, max_num: int, tickets):
        entries.append({
            "file": str(path.relative_to(DESIGNS)), "kind": kind, "v": v,
            "pick": pick, "guarantee": g, "condition": cond,
            "draw_n": draw_n, "max_num": max_num, "tickets": [list(t) for t in tickets],
        })

    for p in sorted(DESIGNS.glob("C_*_*.txt")):
        m = re.match(r"C_(\d+)_(\d+)_(\d+)\.txt", p.name)
        v, pick, g = map(int, m.groups())
        rows = _rows(p.read_text(encoding="utf-8-sig"))
        for draw_n, max_num in _geoms_for_pick(pick):
            add(p, "C", v, pick, g, None, draw_n, max_num, rows)

    for p in sorted(DESIGNS.glob("L_*_*.txt")):
        m = re.match(r"L_(\d+)_(\d+)_(\d+)_(\d+)\.txt", p.name)
        v, pick, cond, t = map(int, m.groups())
        rows = _rows(p.read_text(encoding="utf-8-sig"))
        for draw_n, max_num in _geoms_for_pick(pick):
            add(p, "L", v, pick, t, cond, draw_n, max_num, rows)

    for p in sorted((DESIGNS / "budget").glob("H_*_*.txt")):
        m = re.match(r"H_(\d+)_(\d+)_(\d+)\.txt", p.name)
        v, pick, g = map(int, m.groups())
        text = p.read_text(encoding="utf-8-sig")
        # secțiuni "B <n>"
        sections: list[tuple[int, list[tuple[int, ...]]]] = []
        cur_n, cur = 0, []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("B "):
                if cur:
                    sections.append((cur_n, cur))
                cur_n, cur = int(s.split()[1]), []
            elif s and not s.startswith("#"):
                cur.append(tuple(int(x) for x in s.split()))
        if cur:
            sections.append((cur_n, cur))
        for draw_n, max_num in _geoms_for_pick(pick):
            for n, rows in sections:
                add(p, "H", v, pick, g, None, draw_n, max_num, rows)
                entries[-1]["budget"] = n

    for p in sorted((DESIGNS / "refined").glob("*.txt")):
        # RL_<v>_<pick>_<g>_<c>_<draw>_<max> (6 câmpuri) și R_<v>_<pick>_<g>_<draw>_<max> (5)
        m = re.match(r"RL_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\.txt", p.name)
        if m:
            v, pick, g, cond, draw_n, max_num = map(int, m.groups())
            rows = _rows(p.read_text(encoding="utf-8-sig"))
            add(p, "RL", v, pick, g, cond, draw_n, max_num, rows)
            continue
        m = re.match(r"R_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\.txt", p.name)
        if not m:
            continue
        v, pick, g, draw_n, max_num = map(int, m.groups())
        rows = _rows(p.read_text(encoding="utf-8-sig"))
        add(p, "R", v, pick, g, None, draw_n, max_num, rows)

    for p in sorted((DESIGNS / "extended").glob("X_*.txt")):
        m = re.match(r"X_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)\.txt", p.name)
        v, pick, draw_n, max_num, _n = map(int, m.groups())
        rows = _rows(p.read_text(encoding="utf-8-sig"))
        add(p, "X", v, pick, 0, None, draw_n, max_num, rows)

    for p in sorted((DESIGNS / "lr").glob("LR_*.txt")):
        m = re.match(r"LR_(649|540|joker)_cod(\d+)\.txt", p.name)
        game, code = m.group(1), int(m.group(2))
        pick, draw_n, max_num = GAMES[game]
        text = p.read_text(encoding="utf-8-sig")
        n = int(re.search(r"#\s*numere:\s*(\d+)", text).group(1))
        rows = _rows(text)
        add(p, "LR", n, pick, 0, None, draw_n, max_num, rows)
        entries[-1]["code"] = code
        entries[-1]["game"] = game

    return entries


def guarantee_ok(e: dict) -> tuple[bool, str]:
    """Verificare structurală: fiecare submulțime cerută de garanție e acoperită.

    Kindurile H (buget), X (pool extins) și LR (scheme Loteria Română) nu au
    garanție completă prin construcție — pentru ele se raportează doar fracția
    de acoperire, iar verdictul vine exclusiv din enumerare (diff = 0)."""
    v, pick, g, cond = e["v"], e["pick"], e["guarantee"], e["condition"]
    tickets = e["tickets"]
    if e["kind"] in ("X", "LR"):
        return True, "fără garanție declarată (audit doar probabilistic)"
    tsets = [set(t) for t in tickets]
    if cond:  # L/RL: "t dacă p" — fiecare p-submulțime are un bilet cu ≥ t comune
        need = comb(v, cond)
        covered = sum(
            1 for sub in combinations(range(1, v + 1), cond)
            if any(len(set(sub) & ts) >= g for ts in tsets)
        )
    else:  # C/R: fiecare g-submulțime e conținută într-un bilet
        need = comb(v, g)
        covered = sum(
            1 for sub in combinations(range(1, v + 1), g)
            if any(set(sub) <= ts for ts in tsets)
        )
    frac = covered / need if need else 1.0
    if e["kind"] == "H":
        # design cu buget: garanția completă nu e cerută; raportăm doar fracția
        return True, f"buget: {covered}/{need} submulțimi acoperite ({frac:.4f})"
    if covered == need:
        return True, f"toate cele {need} submulțimi acoperite"
    return False, f"doar {covered}/{need} submulțimi acoperite ({frac:.4f})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", action="store_true", help="forțează GPU (cupy) dacă e disponibil")
    ap.add_argument("--cpu", action="store_true", help="forțează CPU chiar dacă cupy e prezent")
    ap.add_argument("--limit", type=int, default=0, help="auditează doar primele N intrări (smoke)")
    ap.add_argument("--kind", default="", help="filtru pe kind (C,L,H,R,RL,X,LR)")
    ap.add_argument("--out-json", default=str(ROOT / "scripts/analysis/audit_exhaustive_2026-11-01.json"))
    ap.add_argument("--out-md", default=str(ROOT / "scripts/analysis/audit_exhaustive_2026-11-01.md"))
    args = ap.parse_args()

    use_gpu = None
    if args.gpu:
        use_gpu = True
    if args.cpu:
        use_gpu = False

    entries = collect()
    if args.kind:
        entries = [e for e in entries if e["kind"] == args.kind]
    if args.limit:
        entries = entries[: args.limit]

    backend = "cupy" if (use_gpu is not False and gpu_available()) else "numpy"
    print(f"backend: {backend} (gpu_available={gpu_available()}, use_gpu={use_gpu})")
    print(f"intrări de auditat: {len(entries)}")

    results = []
    t0 = time.time()
    for i, e in enumerate(entries):
        pool = list(range(1, e["v"] + 1))
        g_ok, g_msg = guarantee_ok(e)
        t1 = time.time()
        ver = verify_wheel_exact(pool, e["tickets"], e["draw_n"], e["max_num"], use_gpu=use_gpu)
        dt = time.time() - t1
        rec = {
            **{k: e[k] for k in ("file", "kind", "v", "pick", "guarantee", "condition",
                                 "draw_n", "max_num")},
            "tickets": len(e["tickets"]),
            "budget": e.get("budget"), "code": e.get("code"), "game": e.get("game"),
            "guarantee_ok": g_ok, "guarantee_msg": g_msg,
            "enum_backend": ver["backend"], "total_draws": ver["total_draws"],
            "max_abs_diff": ver["max_abs_diff"], "enum_ok": ver["ok"],
            "seconds": round(dt, 2),
        }
        results.append(rec)
        flag = "OK " if (g_ok and ver["ok"]) else "FAIL"
        print(f"[{i+1}/{len(entries)}] {flag} {e['file']} kind={e['kind']} v={e['v']} "
              f"draw={e['draw_n']}/{e['max_num']} t={len(e['tickets'])} "
              f"diff={ver['max_abs_diff']:.2e} {dt:.1f}s", flush=True)

    n_ok = sum(1 for r in results if r["guarantee_ok"] and r["enum_ok"])
    payload = {
        "date": "2026-11-01",
        "backend": backend,
        "use_gpu": use_gpu,
        "gpu_available": gpu_available(),
        "entries": len(results),
        "ok": n_ok,
        "failed": len(results) - n_ok,
        "total_seconds": round(time.time() - t0, 1),
        "results": results,
    }
    Path(args.out_json).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Audit exhaustiv 2026-11-01 — enumerare brute-force vs numărătoare combinatorială",
        "",
        f"- Backend: **{backend}** (gpu_available={gpu_available()})",
        f"- Intrări auditate: **{len(results)}**, ok: **{n_ok}**, eșuate: **{len(results)-n_ok}**",
        f"- Timp total: {payload['total_seconds']} s",
        "",
        "Fiecare design a fost verificat două căi independente: (1) structural, fiecare",
        "submulțime cerută de garanție e acoperită de un bilet; (2) enumerând TOATE",
        f"C(max,draw) extrageri uniforme și comparând histograma cu `wheel_hit_probabilities`.",
        "",
        "| fișier | kind | v | draw/max | bilete | garanție | diff max | ok | s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['file']} | {r['kind']} | {r['v']} | {r['draw_n']}/{r['max_num']} "
            f"| {r['tickets']} | {'ok' if r['guarantee_ok'] else 'FAIL'} "
            f"| {r['max_abs_diff']:.2e} | {'DA' if r['enum_ok'] else 'NU'} | {r['seconds']} |"
        )
    lines += ["", "Abaterea maximă 0 = cele două căi (enumerare GPU/CPU și combinatorică) coincid exact.", ""]
    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")
    print(f"\nscris: {args.out_json}\nscris: {args.out_md}")
    print(f"OK {n_ok}/{len(results)}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
