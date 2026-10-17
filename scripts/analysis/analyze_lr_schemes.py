"""Schemele reduse LR față de variantele aplicației, la același pool și același cost.

Pentru fiecare schemă din ``covering_designs/lr`` cu cel mult 16 numere:

- șansa exactă, pe o extragere uniformă, ca o variantă să prindă 3+, 4+, 5+, 6;
- ce garantează schema: din k numere ieșite dintre cele n, în ce parte a
  cazurilor ajung cel puțin t pe aceeași variantă;
- wheel-ul aplicației pe aceleași n numere, cu exact atâtea variante, pentru
  fiecare garanție 2..bilet-1 (`generate_wheel("hitcover", ...)`, calea
  motorului), și verdictul de dominanță pe pragurile 3..extrase.

Cu ``--wf FIȘIER.pkl`` (cache walk-forward v31) refă și istoricul: la fiecare
pas, schema pusă pe pool-ul de atunci (sortat crescător), față de variantele
validate în WF. Rezultatul: ``scripts/analysis/lr_schemes_2026-10-17.json``.

    python scripts/analysis/analyze_lr_schemes.py [--wf CACHE.pkl ...] [--out FIȘIER]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from covering.lr_schemes import (
    best_hits,
    lr_schemes,
    scheme_guarantees,
    scheme_odds,
)

# Bilet, extrase, univers, lei pe variantă (`ui_results.PRICES`).
GAMES = {"6/49": (6, 6, 49, 8.0), "5/40": (5, 6, 40, 5.0), "joker": (5, 5, 45, 7.0)}


def verdict(lr: dict, app: dict) -> str:
    keys = sorted(lr)
    if all(abs(app[t] - lr[t]) <= 1e-12 for t in keys):
        return "identic"
    ge = all(app[t] >= lr[t] - 1e-12 for t in keys)
    le = all(app[t] <= lr[t] + 1e-12 for t in keys)
    if ge:
        return "aplicatia domina"
    if le:
        return "LR domina"
    return "compromis"


def app_designs(game: str, n: int, budget: int) -> list[dict]:
    from covering.dispatch import generate_wheel
    from covering.probability import wheel_hit_probabilities

    pick, draw_n, max_num, _ = GAMES[game]
    pool = list(range(1, n + 1))
    scores = {p: float(n + 1 - p) for p in pool}
    out = []
    for g in range(2, pick):
        try:
            wheel, _ = generate_wheel(
                "hitcover",
                pool,
                pick,
                g,
                budget,
                scores,
                draw_n=draw_n,
                max_num=max_num,
            )
        except Exception as exc:  # noqa: BLE001 - o geometrie refuzată se raportează
            out.append({"g": g, "error": str(exc)})
            continue
        tickets = [sorted(int(x) for x in t[:pick]) for t in wheel]
        odds = wheel_hit_probabilities(pool, tickets, draw_n, max_num)
        out.append(
            {
                "g": g,
                "variants": len(tickets),
                "P": {t: odds["ticket"][t] for t in range(3, min(pick, draw_n) + 1)},
            }
        )
    return out


def wf_replay(path: Path) -> dict:
    from loto_enterprise.core.py314_io import pickle_load_path
    from loto_enterprise.core.walk_forward_adapter import per_draw_hit_summary

    payload = pickle_load_path(path)
    flat = payload["flat"]
    game = {"6/49": "6/49", "5/40": "5/40", "joker": "joker"}.get(
        str(payload.get("game_type"))
    )
    hits = per_draw_hit_summary(flat)
    steps = {}
    for p in flat:
        ctx = getattr(p, "ticket_context", None) or {}
        if p.draw_index in steps or not ctx.get("hard_core") or not ctx.get("actual"):
            continue
        steps[p.draw_index] = (
            str(getattr(p, "draw_date", "")),
            [int(x) for x in ctx["hard_core"]],
            [int(x) for x in ctx["actual"]],
        )
    out = {
        "file": path.name,
        "game": game,
        "draws": len(steps),
        "wheel_guarantee": payload.get("wheel_guarantee"),
        "wf_variants_per_draw": round(len(flat) / max(1, len(hits)), 2),
        "schemes": [],
    }
    sizes = {len(pool) for _, pool, _ in steps.values()}
    pick, draw_n, _, _ = GAMES[game]
    thresholds = range(3, min(pick, draw_n) + 1)
    out["pool"] = {t: sum(1 for i in steps if hits[i]["pool"] >= t) for t in thresholds}
    out["wf_tickets"] = {
        t: sum(1 for i in steps if hits[i]["best_ticket"] >= t) for t in thresholds
    }
    for n in sorted(sizes):
        for scheme in lr_schemes(game, n):
            best = {
                i: best_hits(scheme, pool, drawn)
                for i, (_, pool, drawn) in steps.items()
                if len(pool) == n
            }
            odds = scheme_odds(scheme)
            top = max(thresholds)
            out["schemes"].append(
                {
                    "code": scheme.code,
                    "n": n,
                    "variants": scheme.size,
                    "draws": len(best),
                    "observed": {
                        t: sum(1 for h in best.values() if h >= t) for t in thresholds
                    },
                    "expected": {t: odds[t] * len(best) for t in thresholds},
                    "top_dates": sorted(
                        steps[i][0] for i, h in best.items() if h >= top - 1
                    ),
                }
            )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wf", type=Path, nargs="*", default=[])
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "scripts" / "analysis" / "lr_schemes_2026-10-17.json",
    )
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    rows = []
    for scheme in lr_schemes():
        if scheme.n > 16:
            continue  # pool-ul aplicației are cel mult 16 numere
        price = GAMES[scheme.game][3]
        lr = scheme_odds(scheme)
        apps = app_designs(scheme.game, scheme.n, scheme.size)
        for a in apps:
            if "P" in a and a["variants"] == scheme.size:
                a["verdict"] = verdict(lr, a["P"])
        rows.append(
            {
                "game": scheme.game,
                "code": scheme.code,
                "n": scheme.n,
                "variants": scheme.size,
                "cost_lei": scheme.size * price,
                "P": lr,
                "guarantees": scheme_guarantees(scheme),
                "app": apps,
            }
        )
        print(
            scheme.game,
            scheme.label,
            scheme.n,
            scheme.size,
            {t: round(100 * p, 4) for t, p in lr.items()},
            [(a["g"], a.get("verdict")) for a in apps],
            flush=True,
        )
    replays = [wf_replay(p) for p in args.wf]
    for r in replays:
        print(json.dumps(r, ensure_ascii=False)[:600])
    args.out.write_text(
        json.dumps(
            {"schemes": rows, "walk_forward": replays}, ensure_ascii=False, indent=1
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
