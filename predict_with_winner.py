"""Predict the next-draw pool using the benchmark-winning method per game/pool.

Reads best_methods.json (produced by bench_all_methods.py), picks per-pool
winners through method_selector, and prints the top-K pool for the CSV.

Usage:
    python predict_with_winner.py                          # all games, pool = draw_n
    python predict_with_winner.py --game loto_6_49 --pool 12
    python predict_with_winner.py --game joker_urna2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from rich.console import Console
from rich.table import Table
from rich import box

from loto_enterprise.core.method_selector import (
    get_scorer_for_game,
    get_winner_name,
    summary_line,
    all_per_pool_winners,
)
from loto_enterprise.benchmark.hardware import snapshot, format_snapshot
from loto_enterprise.benchmark.runner import discover_games, load_draws


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--istoric", default=None)
    parser.add_argument(
        "--game",
        choices=["loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2", "all"],
        default="all",
    )
    parser.add_argument("--pool", type=int, default=0,
                        help="Pool size K to predict; 0 = use draw_n")
    args = parser.parse_args()

    console = Console()
    games = discover_games(args.istoric)
    if args.game != "all":
        games = [g for g in games if g.key == args.game]
    if not games:
        console.print(f"[red]Unknown game: {args.game}[/red]")
        return 2

    console.print(format_snapshot(snapshot()))
    console.print()

    if not Path("best_methods.json").exists():
        console.print("[yellow][!] best_methods.json missing — run `python bench_all_methods.py` first.[/yellow]")
        console.print("[yellow]    Falling back to frequency baseline.[/yellow]")
        console.print()

    for g in games:
        draws = load_draws(g)
        k = args.pool if args.pool > 0 else g.draw_n
        scorer = get_scorer_for_game(g.key, pool_size=k)
        winner = get_winner_name(g.key, pool_size=k)
        scores = scorer(draws, g.max_num)
        if not scores:
            console.print(f"[{g.label}] [red]scorer returned empty[/red]")
            continue
        top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        nums = sorted(n for n, _ in top)

        # Table per game
        t = Table(title=f"{g.label} — top-{k} via {winner}", box=box.MINIMAL_HEAVY_HEAD)
        t.add_column("rank", justify="right", style="dim")
        t.add_column("number", justify="right", style="bold")
        t.add_column("score", justify="right")
        for i, (n, s) in enumerate(top, 1):
            t.add_row(str(i), str(n), f"{s:.3f}")
        console.print(t)
        console.print(f"  → [bold green]Predicted pool[/bold green]: {nums}")

        all_winners = all_per_pool_winners(g.key)
        if all_winners:
            line = ", ".join(f"k{kk}={mm}" for kk, mm in sorted(all_winners.items()))
            console.print(f"  [dim]All per-pool winners: {line}[/dim]")
        console.print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
