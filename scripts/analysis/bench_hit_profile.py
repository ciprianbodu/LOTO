"""Exact before/after ticket-hit probabilities of the profile swaps (2026-10-02).

Read-only. For every configuration it builds the production wheel twice, with
``covering.profile_swap.improve_hit_profile`` disabled (before) and enabled
(after), and prints exact P(at least one ticket with >= 3/4/5 hits) for one
uniformly random draw. Pure geometry: pool = 1..v, no scores, no history.
No predictive claim: the pool hit probability is identical in both columns.

    python scripts/analysis/bench_hit_profile.py [--quick]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import covering.dispatch as dispatch  # noqa: E402
import covering.profile_swap as profile_swap  # noqa: E402
from covering.probability import wheel_hit_probabilities  # noqa: E402
from loto_enterprise.core.full_ticket import _fill_variants  # noqa: E402

GAMES = {"6/49": (6, 6, 49), "5/40": (5, 6, 40), "joker": (5, 5, 45)}
_REAL = profile_swap.improve_hit_profile


def _identity(pool, tickets, *args, **kwargs):
    return [sorted(t) for t in tickets], {"applied": False}


def _set(enabled: bool) -> None:
    fn = _REAL if enabled else _identity
    dispatch.improve_hit_profile = fn
    profile_swap.improve_hit_profile = fn


def _build(kind, pool, pick, g, c, budget, draw_n, enabled):
    _set(enabled)
    profile_swap._improve.cache_clear()
    start = time.perf_counter()
    if kind == "fill":
        wheel, _ = _fill_variants(pool, pick, g, budget, None)
    else:
        method = dispatch.resolve_wheel_method(budget, "")
        wheel, _ = dispatch.generate_wheel(
            method, pool, pick, g, budget, None,
            condition=c if c != g else None, draw_n=draw_n,
        )
    return wheel, time.perf_counter() - start


def configs(quick: bool):
    pools = (8, 12, 16) if quick else range(6, 17)
    for game, (pick, draw_n, max_num) in GAMES.items():
        for v in pools:
            if v <= pick:
                continue
            for g in range(3, pick):
                yield "classic", game, v, g, g, 0
            for g in (3, 4):
                for c in range(g + 1, min(pick, draw_n) + 1):
                    yield "lotto", game, v, g, c, 0
            for g in (3, 4):
                if g < pick:
                    for b in (7, 20, 64, 100):
                        yield "budget", game, v, g, g, b
            per = {"6/49": 3, "5/40": 4, "joker": 2}[game]
            for tickets in (1, 3, 10):
                yield "fill", game, v, 3, 3, per * tickets


def main() -> None:
    quick = "--quick" in sys.argv
    print("| area | game | pool | g | c | budget | tickets | t | before | after | rel | sec |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    worst = 0.0
    for kind, game, v, g, c, budget in configs(quick):
        pick, draw_n, max_num = GAMES[game]
        pool = list(range(1, v + 1))
        before, _ = _build(kind, pool, pick, g, c, budget, draw_n, False)
        after, sec = _build(kind, pool, pick, g, c, budget, draw_n, True)
        worst = max(worst, sec)
        assert len({tuple(t) for t in after}) == len({tuple(t) for t in before})
        if not before:
            continue
        p0 = wheel_hit_probabilities(pool, before, draw_n, max_num)["ticket"]
        p1 = wheel_hit_probabilities(pool, after, draw_n, max_num)["ticket"]
        for t in (3, 4, 5):
            if t > draw_n or p1[t] == p0[t]:
                continue
            rel = (p1[t] / p0[t] - 1) * 100 if p0[t] else float("inf")
            print(
                f"| {kind} | {game} | {v} | {g} | {c} | {budget} | {len(after)} "
                f"| {t}+ | {p0[t]:.6f} | {p1[t]:.6f} | {rel:+.2f}% | {sec:.2f} |",
                flush=True,
            )
    _set(True)
    print(f"\nworst generation time with swaps: {worst:.2f} s")


if __name__ == "__main__":
    main()
