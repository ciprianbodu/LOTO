"""Decision algorithm: pick the optimal (scorer, sim_depth, use_blacklist) per
(game, urna, pool_size) — for the auto-pilot.

Selection rules (in priority order):
    1. Method must BEAT the random baseline consistently — at least 60% of
       sim_depth windows must show lift > 0 over the random baseline at the
       same (game, pool, window).
    2. Among qualifying methods, pick the one with HIGHEST mean lift across
       all sim_depth windows (averaged with double-weight on larger windows
       since they exercise more historical data).
    3. For the chosen method, pick the optimal sim_depth window — the one
       that maximises avg_hits AND has at least 30 test draws (statistical
       stability). Prefer larger windows on ties.
    4. Use blacklist if the +BL variant beats the no-BL variant at that
       chosen (method, window).

The output is written to best_methods.json under `auto_pilot_per_pool[gk][kN]`
with full traceability (rationale + supporting numbers).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


CONSISTENCY_THRESHOLD = 0.60  # method must beat random in ≥60% of windows
MIN_TEST_DRAWS_FOR_STABILITY = 30


def _windows_method_beats_random(
    sub_real_method: pd.DataFrame,
    sub_real_random: pd.DataFrame,
    base_col: str,
) -> Tuple[int, int]:
    """Return (n_windows_beat, n_windows_total) for method vs random baseline."""
    if sub_real_method.empty or sub_real_random.empty:
        return 0, 0
    method_by_pct = sub_real_method.groupby("percentile")[base_col].mean()
    random_by_pct = sub_real_random.groupby("percentile")[base_col].mean()
    common_pcts = sorted(set(method_by_pct.index) & set(random_by_pct.index))
    if not common_pcts:
        return 0, 0
    n_beat = 0
    for p in common_pcts:
        if method_by_pct[p] > random_by_pct[p]:
            n_beat += 1
    return n_beat, len(common_pcts)


def _weighted_mean_lift(
    sub_real_method: pd.DataFrame,
    sub_real_random: pd.DataFrame,
    base_col: str,
) -> float:
    """Compute mean lift weighted by sim_depth (larger windows weight more)."""
    if sub_real_method.empty or sub_real_random.empty:
        return 0.0
    method_by_pct = sub_real_method.groupby("percentile")[base_col].mean()
    random_by_pct = sub_real_random.groupby("percentile")[base_col].mean()
    common_pcts = sorted(set(method_by_pct.index) & set(random_by_pct.index))
    if not common_pcts:
        return 0.0
    weighted_sum = 0.0
    weight_total = 0.0
    for p in common_pcts:
        weight = float(p)  # 100% window weighs 10x more than 10% window
        lift = float(method_by_pct[p]) - float(random_by_pct[p])
        weighted_sum += weight * lift
        weight_total += weight
    return weighted_sum / weight_total if weight_total > 0 else 0.0


def decide_optimal_config_for_pool(
    folds_df: pd.DataFrame,
    game_key: str,
    pool_size: int,
    draw_n: int,
) -> Dict:
    """Run the decision algorithm for one (game, pool_size) pair.

    Returns a dict with the chosen scorer, sim_depth, use_blacklist + rationale.
    """
    base_col = f"k{pool_size}"
    base_col_bl = f"k{pool_size}_bl"

    if base_col not in folds_df.columns:
        return {"error": f"column {base_col} missing in folds.csv"}

    sub = folds_df[(folds_df["game"] == game_key) & (folds_df["failed"] != True)]  # noqa: E712
    if sub.empty:
        return {"error": f"no folds for game {game_key}"}

    real_random = sub[(sub["method"] == "random") & (sub["is_random"] == False)]  # noqa: E712
    methods = [m for m in sub["method"].unique() if m != "random"]

    qualifying: List[Tuple[str, float, int, int]] = []
    for m in methods:
        real_m = sub[(sub["method"] == m) & (sub["is_random"] == False)]  # noqa: E712
        if real_m.empty:
            continue
        n_beat, n_total = _windows_method_beats_random(real_m, real_random, base_col)
        if n_total == 0:
            continue
        consistency = n_beat / n_total
        if consistency < CONSISTENCY_THRESHOLD:
            continue
        w_lift = _weighted_mean_lift(real_m, real_random, base_col)
        qualifying.append((m, w_lift, n_beat, n_total))

    if not qualifying:
        # Fallback: pick the method with overall highest avg_hits at base pool
        ranked = []
        for m in methods + ["random"]:
            real_m = sub[(sub["method"] == m) & (sub["is_random"] == False)]  # noqa: E712
            if real_m.empty:
                continue
            ranked.append((m, float(real_m[base_col].mean())))
        ranked.sort(key=lambda kv: kv[1], reverse=True)
        if not ranked:
            return {"error": "no valid folds for any method"}
        scorer = ranked[0][0]
        rationale = (
            f"FALLBACK: no method consistently beat random "
            f"(≥{int(CONSISTENCY_THRESHOLD*100)}% of windows); "
            f"picked highest absolute avg_hits"
        )
    else:
        # Sort by weighted lift descending; tiebreak on consistency
        qualifying.sort(key=lambda r: (r[1], r[2] / max(r[3], 1)), reverse=True)
        scorer = qualifying[0][0]
        rationale = (
            f"{scorer} beat random in {qualifying[0][2]}/{qualifying[0][3]} windows "
            f"(weighted lift = {qualifying[0][1]:+.4f}); "
            f"selected from {len(qualifying)} qualifying methods"
        )

    # Pick the best sim_depth for the chosen scorer
    real_chosen = sub[(sub["method"] == scorer) & (sub["is_random"] == False)]  # noqa: E712
    by_pct = real_chosen.groupby("percentile").agg(
        avg_hits=(base_col, "mean"),
        n_test=("n_test", "mean"),
        runtime=("runtime_sec", "mean"),
    )
    # Filter to windows with enough test data
    stable = by_pct[by_pct["n_test"] >= MIN_TEST_DRAWS_FOR_STABILITY]
    if stable.empty:
        stable = by_pct
    # Sort by avg_hits desc, tiebreak on larger sim_depth
    stable = stable.sort_values(
        by=["avg_hits"], ascending=False
    )
    best_pct = int(stable.index[0])
    best_avg = float(stable.iloc[0]["avg_hits"])

    # Decide use_blacklist at the chosen (method, sim_depth)
    use_bl = False
    bl_avg = None
    if base_col_bl in real_chosen.columns:
        at_pct = real_chosen[real_chosen["percentile"] == best_pct]
        if not at_pct.empty:
            no_bl = float(at_pct[base_col].mean())
            bl_avg = float(at_pct[base_col_bl].mean())
            if bl_avg > no_bl:
                use_bl = True

    return {
        "scorer": scorer,
        "sim_depth_pct": best_pct,
        "use_blacklist": use_bl,
        "avg_hits": best_avg,
        "avg_hits_with_blacklist": bl_avg,
        "rationale": rationale,
        "qualifying_methods": len(qualifying),
        "consistency_threshold": CONSISTENCY_THRESHOLD,
    }


def build_auto_pilot_matrix(
    folds_csv_path: str,
    games_meta: Dict[str, Dict],
) -> Dict[str, Dict[str, Dict]]:
    """Build the full {game_key: {pool_key: config}} matrix from folds.csv.

    Args:
        folds_csv_path: path to bench_results/folds.csv
        games_meta: {game_key: {"draw_n": int, "pool_range": [int]}}

    Returns:
        Nested dict suitable for embedding in best_methods.json.
    """
    df = pd.read_csv(folds_csv_path)
    if "failed" not in df.columns:
        df["failed"] = False
    matrix: Dict[str, Dict[str, Dict]] = {}
    for gk, meta in games_meta.items():
        matrix[gk] = {}
        for k in meta["pool_range"]:
            cfg = decide_optimal_config_for_pool(
                df, gk, pool_size=k, draw_n=meta["draw_n"]
            )
            matrix[gk][f"k{k}"] = cfg
    return matrix


def update_best_methods_with_auto_pilot(
    best_methods_path: str = "best_methods.json",
    folds_csv_path: str = "bench_results/folds.csv",
) -> Dict:
    """Read folds.csv, run decision algo, write `auto_pilot_per_pool` into best_methods.json."""
    bm_path = Path(best_methods_path)
    if not bm_path.exists():
        raise FileNotFoundError(f"{best_methods_path} not found")
    if not Path(folds_csv_path).exists():
        raise FileNotFoundError(f"{folds_csv_path} not found")

    cfg = json.loads(bm_path.read_text(encoding="utf-8"))
    games = cfg.get("games", {})

    games_meta = {}
    for gk, gd in games.items():
        draw_n = int(gd.get("draw_n", 6))
        if gk == "joker_urna2":
            pool_range = [draw_n]
        else:
            # Aligned with runner.py pool_extra=14 → K=draw_n..draw_n+14
            # (2026-05-26: extins de la +6 → +14 ca să acopere pool size sweep
            # K=15/18/20 cerut de user pentru testare 4+ hits stable).
            pool_range = list(range(draw_n, draw_n + 15))  # draw_n .. draw_n+14
        games_meta[gk] = {"draw_n": draw_n, "pool_range": pool_range}

    matrix = build_auto_pilot_matrix(folds_csv_path, games_meta)

    for gk in games:
        games[gk]["auto_pilot_per_pool"] = matrix.get(gk, {})

    bm_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return matrix
