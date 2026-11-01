"""Comparație La Jolla vs ILP vs greedy pe ACEEAȘI geometrie, prin
(coverage, ticket_count, runtime) — AGENTS.md §12 P1 „acoperire și cost".

Geometrii: pick 5/6 × garanție 3/4(/5) × pool 10/12/14/16, plus cazurile mari
(pool 16 g4/g5). Scoruri sintetice deterministe (descrescătoare), ca greedy-ul
să fie reproductibil. Rulare cu și fără plafon (max_variants=64).

Rulare:  .venv/Scripts/python.exe scripts/analysis/compare_wheel_methods_2026-11-01.py
Ieșire:  scripts/analysis/wheel_methods_comparison_2026-11-01.{json,md}
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from covering.common import compute_coverage_pct
from covering.dispatch import WHEEL_METHODS, _greedy_fallback

METHODS = ("lajolla", "ilp", "greedy")
GEOMS = (
    [(v, pick, g) for v in (10, 12, 14, 16) for pick in (5, 6) for g in (3, 4)]
    + [(16, 6, 5), (16, 5, 5), (16, 6, 6)]
)
CAPS = (0, 64)


def run_one(method: str, v: int, pick: int, g: int, cap: int):
    pool = list(range(1, v + 1))
    scores = {n: float(v + 1 - n) for n in pool}  # determinist, descrescător
    fn = WHEEL_METHODS.get(method) or _greedy_fallback  # "greedy" = fallback-ul canonic
    t0 = time.perf_counter()
    try:
        wheel, cov = fn(pool, pick, g, cap, scores)
        err = None
    except Exception as exc:  # noqa: BLE001
        wheel, cov, err = [], 0.0, f"{type(exc).__name__}: {exc}"
    dt = time.perf_counter() - t0
    if err is None:
        cov = compute_coverage_pct(wheel, pool, g)
    return {
        "method": method, "v": v, "pick": pick, "guarantee": g, "cap": cap,
        "tickets": len(wheel), "coverage_pct": round(cov, 4),
        "seconds": round(dt, 3), "error": err,
    }


def main() -> int:
    rows = []
    for v, pick, g in GEOMS:
        for cap in CAPS:
            for method in METHODS:
                r = run_one(method, v, pick, g, cap)
                rows.append(r)
                print(f"v={v} pick={pick} g={g} cap={cap} {method}: "
                      f"tickets={r['tickets']} cov={r['coverage_pct']} "
                      f"{r['seconds']}s {r['error'] or ''}", flush=True)

    # sumar: pe fiecare geometrie, cine câștigă la acoperire și cost
    summary = []
    for v, pick, g in GEOMS:
        for cap in CAPS:
            sub = [r for r in rows if (r["v"], r["pick"], r["guarantee"], r["cap"]) == (v, pick, g, cap)]
            best_cov = max(r["coverage_pct"] for r in sub if not r["error"])
            winners = [r["method"] for r in sub if not r["error"] and r["coverage_pct"] == best_cov]
            min_t = min(r["tickets"] for r in sub if not r["error"] and r["coverage_pct"] == best_cov)
            best = [r["method"] for r in sub if not r["error"] and r["coverage_pct"] == best_cov and r["tickets"] == min_t]
            summary.append({
                "v": v, "pick": pick, "guarantee": g, "cap": cap,
                "best_coverage_pct": best_cov, "min_tickets_at_best": min_t,
                "winners": best,
                "per_method": {r["method"]: {"tickets": r["tickets"], "cov": r["coverage_pct"], "s": r["seconds"]} for r in sub},
            })

    payload = {"date": "2026-11-01", "methods": list(METHODS), "caps": list(CAPS),
               "rows": rows, "summary": summary}
    out_json = ROOT / "scripts/analysis/wheel_methods_comparison_2026-11-01.json"
    out_md = ROOT / "scripts/analysis/wheel_methods_comparison_2026-11-01.md"
    out_json.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Comparație La Jolla vs ILP vs greedy — 2026-11-01",
        "",
        "Aceeași geometrie, aceleași scoruri sintetice deterministe. Metrice: acoperire, număr de bilete, timp.",
        "",
        "| v | pick | g | cap | metodă | bilete | acoperire % | timp s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['v']} | {r['pick']} | {r['guarantee']} | {r['cap']} | {r['method']} "
                     f"| {r['tickets']} | {r['coverage_pct']} | {r['seconds']} |")
    lines += ["", "## Câștigători per geometrie (acoperire max, apoi cele mai puține bilete)", "",
              "| v | pick | g | cap | acoperire max % | min bilete | câștigători |", "|---|---|---|---|---|---|---|"]
    for s in summary:
        lines.append(f"| {s['v']} | {s['pick']} | {s['guarantee']} | {s['cap']} | {s['best_coverage_pct']} "
                     f"| {s['min_tickets_at_best']} | {', '.join(s['winners'])} |")
    lines += ["", "Notă: fără plafon (cap=0) producția preferă La Jolla (designurile validate); greedy-ul cu scoruri",
              " îl înlocuiește când are mai puține bilete sau profil dominant (§7). ILP e peste 16 numere oprit",
              "(`_ILP_MAX_POOL`) și la 16+ poate dura mult — aici e măsurat doar ca referință.", ""]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nscris: {out_json.name}, {out_md.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
