"""Quick re-benchmark — top-N methods × few windows × few pools.

Triggered when CSV has changed enough that cached best_methods.json may be
stale, but a full 50-min sweep is overkill. Re-tests only the FINALISTS from
the prior full sweep (the methods that won at least one pool) on 3 windows
(30%, 60%, 90%) for the top 3 most-used pool sizes (or all if no usage data).

Target: ~3-5 min on RTX 5060 Ti for all 4 games.

Updates best_methods.json `auto_pilot_per_pool` entries IN PLACE if the new
winner differs from the cached one. Leaves the full ranking intact.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def get_finalists_per_game(best_methods_path: str = "best_methods.json") -> Dict[str, Set[str]]:
    """Return {game_key: {finalist_method_names}} based on cached winners."""
    p = Path(best_methods_path)
    if not p.exists():
        return {}
    cfg = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[str, Set[str]] = {}
    for gk, gd in cfg.get("games", {}).items():
        winners: Set[str] = set()
        # winners_per_pool_best (v3)
        for k, w in gd.get("winners_per_pool_best", {}).items():
            if isinstance(w, dict) and w.get("winner"):
                winners.add(w["winner"])
        # auto_pilot_per_pool
        for k, c in gd.get("auto_pilot_per_pool", {}).items():
            if isinstance(c, dict) and c.get("scorer"):
                winners.add(c["scorer"])
        # Always include a sanity baseline
        winners.update({"random", "frequency"})
        out[gk] = winners
    return out


def get_finalists_global(best_methods_path: str = "best_methods.json") -> List[str]:
    """Return the union of finalists across all games (for a single sweep)."""
    per_game = get_finalists_per_game(best_methods_path)
    all_methods: Set[str] = set()
    for s in per_game.values():
        all_methods |= s
    # Add a few canonical baselines that should always be re-tested
    all_methods.update({"random", "frequency", "recency"})
    return sorted(all_methods)


def quick_rebench_cli_args(
    best_methods_path: str = "best_methods.json",
    percentiles: str = "30,60,90",
    block_size: int = 99999,
) -> List[str]:
    """Compose CLI args for bench_all_methods.py to do a fast re-test."""
    finalists = get_finalists_global(best_methods_path)
    if not finalists:
        finalists = ["random", "frequency", "recency", "dlinear", "fedformer", "deepar"]
    return [
        "--methods", ",".join(finalists),
        "--percentiles", percentiles,
        "--block-size", str(block_size),
        "--no-rich",
    ]


def merge_quick_into_full(
    quick_best_methods_path: str = "best_methods.json",
    backup: bool = True,
) -> None:
    """No-op placeholder: the quick rebench writes directly to best_methods.json
    via bench_all_methods.py. Future hook for diffing."""
    if backup:
        p = Path(quick_best_methods_path)
        if p.exists():
            bak = p.with_suffix(".json.prev")
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
