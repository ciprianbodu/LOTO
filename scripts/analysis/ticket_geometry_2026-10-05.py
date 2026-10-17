"""Geometria biletelor la buget fix: probabilitati exacte, 2026-10-05.

Intrebare: la acelasi numar de variante, ce aranjare a numerelor pe variante
da cea mai mare probabilitate ca CEL PUTIN O varianta sa prinda t numere?

Protocol fixat inainte de calcul:

- extragere uniforma (ipoteza nula pe care toate ecranele predictive anterioare
  nu au putut-o respinge); fara istoric, fara scorer;
- productie = `covering.dispatch.generate_wheel` exact ca in aplicatie
  (`resolve_wheel_method(B)`, deci hitcover + schimburi de profil), pool 1..K,
  K = 6..16, garantie 2..4, plafon B variante; se retine cel mai bun (K, g)
  pentru fiecare prag, cu cel mult B bilete distincte;
- "Bilet complet" = `full_ticket._fill_variants` pe pool-ul implicit 10,
  garantia implicita 4;
- dispersie = `covering.spread.spread_variants` (codul livrat in aplicatie) pe
  tot universul jocului: suprapuneri minime prin cautare locala pe
  probabilitatea exacta ca doua variante sa castige impreuna, determinist;
- toate probabilitatile finale sunt exacte: enumerarea tuturor extragerilor
  posibile (13 983 816 la 6/49), nu Monte Carlo; productia se verifica si cu
  `covering.probability.wheel_hit_probabilities`;
- limita superioara pentru orice set de B variante distincte: B * P(o varianta >= t).

Liniaritatea mediei: numarul MEDIU de variante castigatoare este B * P(t) pentru
orice geometrie. Geometria schimba numai cat de des cade cel putin un castig.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from math import comb
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GAMES = {
    "6/49": {"max_num": 49, "draw_n": 6, "pick": 6, "targets": (3, 4, 5), "per_ticket": 3},
    "5/40": {"max_num": 40, "draw_n": 6, "pick": 5, "targets": (3, 4, 5), "per_ticket": 4},
    "joker": {"max_num": 45, "draw_n": 5, "pick": 5, "targets": (3, 4, 5), "per_ticket": 2},
}
PRIMARY = {"6/49": 3, "5/40": 4, "joker": 3}
POOLS = range(6, 17)
GUARANTEES = (2, 3, 4)
DEFAULT_POOL = 10
DEFAULT_GUARANTEE = 4


def _budgets(game: str) -> list[int]:
    per = GAMES[game]["per_ticket"]
    return sorted({1, 2, 7, 10, 20, *(per * n for n in range(1, 11))})


def single_prob(game: str, t: int) -> float:
    g = GAMES[game]
    n, d, p = g["max_num"], g["draw_n"], g["pick"]
    hits = sum(comb(p, h) * comb(n - p, d - h) for h in range(t, min(p, d) + 1))
    return hits / comb(n, d)


_DRAWS: dict[tuple[int, int], np.ndarray] = {}


def all_draw_masks(max_num: int, draw_n: int) -> np.ndarray:
    key = (max_num, draw_n)
    if key in _DRAWS:
        return _DRAWS[key]
    level = [np.array([np.uint64(1) << np.uint64(e)], dtype=np.uint64) for e in range(max_num)]
    for _k in range(1, draw_n):
        nxt = []
        acc: list[np.ndarray] = []
        for e in range(max_num):
            bit = np.uint64(1) << np.uint64(e)
            nxt.append(np.concatenate(acc) | bit if acc else np.zeros(0, dtype=np.uint64))
            acc.append(level[e])
        level = nxt
    out = np.concatenate(level)
    assert len(out) == comb(max_num, draw_n)
    _DRAWS[key] = out
    return out


def exact_ticket_probs(game: str, tickets) -> dict[int, float]:
    """P(cel putin o varianta >= t), t = 1..draw_n, prin enumerare completa."""
    g = GAMES[game]
    draws = all_draw_masks(g["max_num"], g["draw_n"])
    best = np.zeros(len(draws), dtype=np.uint8)
    for ticket in {tuple(sorted(int(x) for x in v)) for v in tickets}:
        mask = np.uint64(sum(1 << (n - 1) for n in ticket))
        np.maximum(best, np.bitwise_count(draws & mask), out=best)
    hist = np.bincount(best, minlength=g["draw_n"] + 1)
    tail = np.cumsum(hist[::-1])[::-1]
    return {t: float(tail[t] / len(draws)) for t in range(1, g["draw_n"] + 1)}


# --------------------------------------------------------------------- spread


def spread_design(game: str, budget: int) -> list[list[int]]:
    """Variantele dispersate livrate in aplicatie, pe tot universul jocului."""
    from covering.spread import spread_variants

    g = GAMES[game]
    return spread_variants(
        range(g["max_num"], 0, -1), budget, g["pick"], g["draw_n"], g["max_num"], PRIMARY[game]
    )


# ----------------------------------------------------------------- production


def _production(args):
    game, pool_size, guarantee, budget = args
    from covering.dispatch import generate_wheel, resolve_wheel_method
    from covering.probability import wheel_hit_probabilities

    g = GAMES[game]
    pool = list(range(1, pool_size + 1))
    if guarantee >= g["pick"] or pool_size < g["pick"]:
        return None
    wheel, cov = generate_wheel(
        resolve_wheel_method(budget),
        pool=pool,
        pick=g["pick"],
        guarantee=guarantee,
        max_variants=budget,
        scores=None,
        draw_n=g["draw_n"],
    )
    distinct = {tuple(sorted(int(x) for x in v)) for v in wheel}
    probs = wheel_hit_probabilities(pool, distinct, g["draw_n"], g["max_num"])["ticket"]
    return {
        "game": game,
        "pool": pool_size,
        "guarantee": guarantee,
        "budget": budget,
        "tickets": len(distinct),
        "coverage": float(cov),
        "probs": {int(t): float(v) for t, v in probs.items()},
        "wheel": [list(v) for v in sorted(distinct)],
    }


def _full_ticket(game: str, tickets: int):
    from loto_enterprise.core.full_ticket import _fill_variants, _min_pool
    from covering.probability import wheel_hit_probabilities

    g = GAMES[game]
    n_var = g["per_ticket"] * tickets
    # Ca `_ticket_pool`: completare pana la variante distincte, plafon = locurile.
    size = min(max(DEFAULT_POOL, _min_pool(g["pick"], n_var)), n_var * g["pick"])
    pool = list(range(1, size + 1))
    variants, n_base = _fill_variants(
        pool, g["pick"], DEFAULT_GUARANTEE, n_var, None, draw_n=g["draw_n"]
    )
    distinct = {tuple(sorted(v)) for v in variants}
    probs = wheel_hit_probabilities(pool, distinct, g["draw_n"], g["max_num"])["ticket"]
    return {
        "pool": size,
        "tickets": len(distinct),
        "probs": {int(t): float(v) for t, v in probs.items()},
        "wheel": [list(v) for v in sorted(distinct)],
    }


def main() -> None:
    from covering.dispatch import generate_wheel, resolve_wheel_method
    from covering.probability import wheel_hit_probabilities
    from covering.spread import ticket_hit_probabilities

    t_start = time.time()
    jobs = [
        (game, k, gu, b)
        for game in GAMES
        for b in _budgets(game)
        for k in POOLS
        for gu in GUARANTEES
    ]
    with ProcessPoolExecutor(max_workers=24) as ex:
        prod = [r for r in ex.map(_production, jobs, chunksize=4) if r is not None]
    print(f"production wheels: {len(prod)} in {time.time() - t_start:.1f}s", flush=True)

    out: dict = {"protocol": __doc__, "games": {}}
    for game, g in GAMES.items():
        singles = {t: single_prob(game, t) for t in range(1, g["draw_n"] + 1)}
        rows = []
        for b in _budgets(game):
            cands = [r for r in prod if r["game"] == game and r["budget"] == b and r["tickets"] <= b]
            best_prod = {}
            for t in g["targets"]:
                top = max(cands, key=lambda r: (r["probs"][t], -r["tickets"], -r["pool"]))
                best_prod[t] = {k: top[k] for k in ("pool", "guarantee", "tickets", "probs")}
            default = next(
                (r for r in cands if r["pool"] == DEFAULT_POOL and r["guarantee"] == DEFAULT_GUARANTEE),
                None,
            )
            ts = time.time()
            design = spread_design(game, b)
            spread = exact_ticket_probs(game, design)
            shipped = ticket_hit_probabilities(design, g["draw_n"], g["max_num"])
            assert all(abs(shipped[t] - spread[t]) < 1e-12 for t in spread), (game, b)
            overlaps = [
                len(set(a) & set(c)) for i, a in enumerate(design) for c in design[i + 1 :]
            ]
            row = {
                "budget": b,
                "union_bound": {t: min(1.0, b * singles[t]) for t in g["targets"]},
                "spread": {t: spread[t] for t in g["targets"]},
                "spread_max_overlap": max(overlaps) if overlaps else 0,
                "spread_numbers": len({x for v in design for x in v}),
                "spread_design": design,
                "best_production": best_prod,
                "default_production": None
                if default is None
                else {k: default[k] for k in ("pool", "guarantee", "tickets", "probs")},
            }
            if b % g["per_ticket"] == 0 and b // g["per_ticket"] <= 10:
                row["full_ticket"] = _full_ticket(game, b // g["per_ticket"])
            # Verificare incrucisata: evaluatorul exact al productiei vs enumerare.
            if default is not None:
                check = exact_ticket_probs(game, default["wheel"])
                assert all(abs(check[t] - default["probs"][t]) < 1e-12 for t in g["targets"]), (game, b)
            rows.append(row)
            print(
                f"[{game}] B={b:>2} spread t{PRIMARY[game]}={100 * spread[PRIMARY[game]]:.3f}% "
                f"best_prod={100 * best_prod[PRIMARY[game]]['probs'][PRIMARY[game]]:.3f}% "
                f"(K={best_prod[PRIMARY[game]]['pool']}, g={best_prod[PRIMARY[game]]['guarantee']}) "
                f"bound={100 * row['union_bound'][PRIMARY[game]]:.3f}% "
                f"maxov={row['spread_max_overlap']} {time.time() - ts:.1f}s",
                flush=True,
            )
        # Productia implicita fara plafon: pool 10, garantie 4, cover complet.
        pool = list(range(1, DEFAULT_POOL + 1))
        wheel, cov = generate_wheel(
            resolve_wheel_method(0), pool=pool, pick=g["pick"],
            guarantee=DEFAULT_GUARANTEE, max_variants=0, scores=None, draw_n=g["draw_n"],
        )
        distinct = {tuple(sorted(int(x) for x in v)) for v in wheel}
        uncapped = wheel_hit_probabilities(pool, distinct, g["draw_n"], g["max_num"])["ticket"]
        design = spread_design(game, len(distinct))
        spread = exact_ticket_probs(game, design)
        out["games"][game] = {
            "single": singles,
            "rows": rows,
            "default_uncapped": {
                "tickets": len(distinct),
                "coverage": float(cov),
                "probs": {int(t): float(v) for t, v in uncapped.items()},
                "spread_same_tickets": {int(t): float(v) for t, v in spread.items()},
            },
        }
        print(
            f"[{game}] uncapped default K=10 g=4: {len(distinct)} tickets "
            f"t{PRIMARY[game]}={100 * uncapped[PRIMARY[game]]:.3f}% vs spread "
            f"{100 * spread[PRIMARY[game]]:.3f}%",
            flush=True,
        )
    dest = ROOT / "scripts" / "analysis" / "ticket_geometry_2026-10-05.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"done in {time.time() - t_start:.1f}s -> {dest.name}")


if __name__ == "__main__":
    main()
