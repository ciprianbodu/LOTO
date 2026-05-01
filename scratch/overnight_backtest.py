"""
Overnight Backtest pentru Adaptive Feedback v2 (regime_reset recalibrat + hard_inversion).

Rulează un backtest de 50 iterații cu setări COMPLETE de producție:
    * smart_reduction = True (Filtru Regresiv Multi-Timeframe activat)
    * ultra_hit_optimization = True (boost pentru hit-uri 4/5)
    * use_feedback = True (adaptive feedback complet — regime_reset + hard_inversion)

Rezultate scrise în:
    scratch/overnight_results.json (date structurate)
    scratch/overnight_results.txt  (raport human-readable)

Backup automat al pool_history.json și restaurare la finalul rulării.

Utilizare:
    .\\.venv_TORINO-ASPIRE5\\Scripts\\python.exe scratch\\overnight_backtest.py

Aproximativ 30-90 minute pe CPU pentru 50 iterații cu smart_reduction=True
(măsoară primele 3 iterații, extrapolează apoi).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

# Setup paths
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

# Logging cu timestamp în fiecare linie
LOG_FILE = ROOT / "scratch" / "overnight_log.txt"
LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# === Config ===
INPUT_CSV = ROOT / "input.csv"
GAME_TYPE = "joker"   # input.csv conține Joker (5 numere din 1-45)
POOL_SIZE = 12
GUARANTEE = 4
LOOKBACK_PERCENT = 30.0
N_ITERATIONS = 50      # 50 backtests
SIMULATION_STEP = 1    # toate

OUT_JSON = ROOT / "scratch" / "overnight_results.json"
OUT_TXT = ROOT / "scratch" / "overnight_results.txt"
POOL_HISTORY = ROOT / "pool_history.json"
ADAPTIVE_STATE = ROOT / "adaptive_state.json"


def backup_state_files() -> dict:
    """Salvează pool_history și adaptive_state (dacă există) ca .backup."""
    backups = {}
    for src in [POOL_HISTORY, ADAPTIVE_STATE]:
        if src.exists():
            dst = src.with_suffix(src.suffix + ".backup_overnight")
            shutil.copy(src, dst)
            backups[str(src)] = str(dst)
            logging.info(f"[BACKUP] {src.name} → {dst.name}")
    return backups


def restore_state_files(backups: dict) -> None:
    for src_str, bak_str in backups.items():
        src = Path(src_str)
        bak = Path(bak_str)
        if bak.exists():
            shutil.copy(bak, src)
            bak.unlink()
            logging.info(f"[RESTORE] {bak.name} → {src.name}")


def baseline_random_hits(game_type: str, pool_size: int) -> float:
    if game_type == "6/49":
        return 6 * pool_size / 49.0
    if game_type == "5/40":
        return 5 * pool_size / 40.0
    if game_type == "joker":
        return 5 * pool_size / 45.0
    return 1.0


def main() -> None:
    logging.info("=" * 80)
    logging.info("OVERNIGHT BACKTEST — Adaptive Feedback v2")
    logging.info("=" * 80)
    logging.info(f"Input: {INPUT_CSV}")
    logging.info(f"Game: {GAME_TYPE} | pool={POOL_SIZE} | guarantee={GUARANTEE}")
    logging.info(f"Iterații: {N_ITERATIONS} | step={SIMULATION_STEP} | lookback={LOOKBACK_PERCENT}%")
    logging.info(f"Mod: PRODUCȚIE (smart_reduction=True, ultra_hit=True, feedback=True)")

    backups = backup_state_files()

    try:
        from loto_enterprise.core.backtesting import LotoBacktester

        backtester = LotoBacktester(str(INPUT_CSV), game_type=GAME_TYPE)
        n_total = len(backtester.draws)
        depth_pct = (N_ITERATIONS / n_total) * 100.0
        logging.info(f"Total extrageri în CSV: {n_total} | depth: {depth_pct:.2f}%")

        t0 = time.time()
        predictions = backtester.run_retroactive_backtest(
            pool_size=POOL_SIZE,
            guarantee=GUARANTEE,
            lookback_percent=LOOKBACK_PERCENT,
            backtest_depth_percent=depth_pct,
            filter_consecutives=True,
            max_variants=0,
            simulation_step=SIMULATION_STEP,
            use_feedback=True,
        )
        elapsed = time.time() - t0

        logging.info(f"\n{'=' * 80}\nREZULTATE\n{'=' * 80}")
        logging.info(f"Timp total: {elapsed/60:.1f} min ({elapsed/len(predictions):.1f}s/iter)")

        if not predictions:
            logging.error("Niciun rezultat — backtest eșuat.")
            return

        pool_hits = [int(p.hits_union) for p in predictions]
        var_hits = [int(p.hits) for p in predictions]
        baseline = baseline_random_hits(GAME_TYPE, POOL_SIZE)

        n = len(pool_hits)
        mean_pool = mean(pool_hits)
        mean_var = mean(var_hits)
        std_pool = stdev(pool_hits) if n > 1 else 0.0
        n_catastrophes = sum(1 for h in pool_hits if h == 0)
        cat_rate = n_catastrophes / n * 100.0
        n_under = sum(1 for h in pool_hits if h == 1)
        under_rate = n_under / n * 100.0

        # Distribuție
        dist = {}
        for h in pool_hits:
            dist[h] = dist.get(h, 0) + 1

        # Streak metrics
        max_streak_zero = 0
        cur_streak = 0
        for h in pool_hits:
            if h == 0:
                cur_streak += 1
                max_streak_zero = max(max_streak_zero, cur_streak)
            else:
                cur_streak = 0

        # Recovery: pool_hits la iterația imediat după 0
        recoveries = []
        for i in range(len(pool_hits) - 1):
            if pool_hits[i] == 0:
                recoveries.append(pool_hits[i + 1])
        avg_recovery = mean(recoveries) if recoveries else None

        report_lines = [
            "=" * 80,
            f"OVERNIGHT BACKTEST — {datetime.now().isoformat(timespec='seconds')}",
            "=" * 80,
            f"Game: {GAME_TYPE} | pool={POOL_SIZE} | iterații={n}",
            f"Mod: smart_reduction=True, ultra_hit=True, adaptive=v2 (reset_recal+hard_inv)",
            f"Timp: {elapsed/60:.1f} min ({elapsed/n:.1f}s/iter)",
            "",
            "--- POOL HITS ---",
            f"Mean:                {mean_pool:.3f}  (random baseline: {baseline:.3f})",
            f"Diff vs random:      {(mean_pool - baseline):+.3f}  ({(mean_pool/baseline - 1)*100:+.1f}%)",
            f"Std:                 {std_pool:.3f}",
            f"Best:                {max(pool_hits)}",
            f"Catastrophes (0):    {n_catastrophes}/{n} = {cat_rate:.1f}%",
            f"Underperf (1):       {n_under}/{n} = {under_rate:.1f}%",
            f"Max streak zero:     {max_streak_zero}",
            f"Avg recovery hits:   {avg_recovery if avg_recovery is not None else 'N/A'}",
            "",
            "--- VARIANT HITS ---",
            f"Mean:                {mean_var:.3f}",
            f"Best:                {max(var_hits)}",
            "",
            "--- DISTRIBUȚIE POOL HITS ---",
        ]
        for h in sorted(dist.keys()):
            pct = dist[h] / n * 100.0
            report_lines.append(f"  {h} hits: {dist[h]:3d} ({pct:5.1f}%)")

        # Comparație directă cu rezultatele anterioare (fast settings)
        report_lines.extend([
            "",
            "--- COMPARAȚIE CU RUN ANTERIOR (fast/30 iter) ---",
            "Anterior fast:       mean=1.20, cat_rate=26.7%, max_streak=4, baseline=1.333",
            f"Acum production:     mean={mean_pool:.3f}, cat_rate={cat_rate:.1f}%, max_streak={max_streak_zero}, baseline={baseline:.3f}",
            "",
            "=" * 80,
        ])

        report = "\n".join(report_lines)
        logging.info("\n" + report)

        # Persistă
        with OUT_TXT.open("w", encoding="utf-8") as f:
            f.write(report)
        logging.info(f"Raport salvat: {OUT_TXT}")

        results_json = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "config": {
                "game_type": GAME_TYPE,
                "pool_size": POOL_SIZE,
                "guarantee": GUARANTEE,
                "n_iterations": n,
                "lookback_percent": LOOKBACK_PERCENT,
                "smart_reduction": True,
                "ultra_hit_optimization": True,
                "adaptive_feedback_version": "v2_reset_recal_hard_inv",
            },
            "elapsed_seconds": elapsed,
            "summary": {
                "mean_pool_hits": mean_pool,
                "mean_variant_hits": mean_var,
                "std_pool_hits": std_pool,
                "best_pool_hits": max(pool_hits),
                "best_variant_hits": max(var_hits),
                "catastrophe_rate_pct": cat_rate,
                "underperf_rate_pct": under_rate,
                "max_streak_zero": max_streak_zero,
                "avg_recovery_hits": avg_recovery,
                "random_baseline": baseline,
                "diff_vs_baseline_pct": (mean_pool / baseline - 1) * 100,
            },
            "distribution": dist,
            "pool_hits_per_iter": pool_hits,
            "variant_hits_per_iter": var_hits,
        }
        with OUT_JSON.open("w", encoding="utf-8") as f:
            json.dump(results_json, f, indent=2)
        logging.info(f"JSON salvat: {OUT_JSON}")

    except Exception as e:
        logging.exception(f"EROARE FATALĂ: {e}")
        raise
    finally:
        restore_state_files(backups)
        logging.info("State files restaurate. End.")


if __name__ == "__main__":
    main()
