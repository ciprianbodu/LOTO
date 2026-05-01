"""
Overnight Backtest harness — parametrizabil pentru orice joc/configurație.

Suportă:
    * Joc țintă (--game joker/6_49/5_40) cu CSV custom (--csv path)
    * Iterații variabile (--iterations 50/100)
    * Ablation mode (--no-hard-inversion) — dezactivează doar Hard Inversion
      pentru a izola contribuția regime_reset
    * Prefix output personalizat (--output-prefix overnight_100j)

Rezultate scrise în:
    scratch/{prefix}_results.json (date structurate)
    scratch/{prefix}_results.txt  (raport human-readable)
    scratch/{prefix}_log.txt      (log timestamped — ignorat de git)

Backup automat al pool_history.json și adaptive_state.json + restaurare la final.

Exemple:
    # 100-iter Joker production (full v2)
    python scratch/overnight_backtest.py --iterations 100 --output-prefix overnight_100j

    # 50-iter Joker ablation (regime_reset only, no hard_inversion)
    python scratch/overnight_backtest.py --no-hard-inversion --output-prefix overnight_ablation_j

    # 50-iter 6/49 (necesită input_649.csv)
    python scratch/overnight_backtest.py --game 6_49 --csv input_649.csv --pool-size 12 \
        --output-prefix overnight_649

Estimare timp: ~33s/iter pe Joker CPU (full producție).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Overnight backtest harness pentru Adaptive Feedback v2")
    p.add_argument("--game", default="joker", choices=["joker", "6_49", "5_40"],
                   help="Tip joc (default: joker)")
    p.add_argument("--csv", default="input.csv",
                   help="Fișier CSV cu istoric (default: input.csv din rădăcină)")
    p.add_argument("--iterations", type=int, default=50,
                   help="Număr iterații retroactive (default: 50)")
    p.add_argument("--pool-size", type=int, default=12,
                   help="Dimensiune pool (default: 12)")
    p.add_argument("--guarantee", type=int, default=4,
                   help="Garanție set cover (default: 4)")
    p.add_argument("--lookback-percent", type=float, default=30.0,
                   help="Lookback % din istoric per iter (default: 30.0)")
    p.add_argument("--no-hard-inversion", action="store_true",
                   help="Dezactivează Hard Inversion (ablation: doar regime_reset activ)")
    p.add_argument("--no-smart-reduction", action="store_true",
                   help="Dezactivează Filtrul Regresiv Multi-Timeframe (fast mode)")
    p.add_argument("--output-prefix", default="overnight",
                   help="Prefix pentru fișierele scratch/{prefix}_results.{json,txt,log}.txt")
    return p.parse_args()


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(exist_ok=True)
    for h in list(logging.root.handlers):
        logging.root.removeHandler(h)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def baseline_random_hits(game_type: str, pool_size: int) -> float:
    if game_type == "6_49":
        return 6 * pool_size / 49.0
    if game_type == "5_40":
        return 5 * pool_size / 40.0
    if game_type == "joker":
        return 5 * pool_size / 45.0
    return 1.0


def backup_state_files(files: list) -> dict:
    backups: dict = {}
    for src in files:
        if src.exists():
            dst = src.with_suffix(src.suffix + ".backup_overnight")
            shutil.copy(src, dst)
            backups[str(src)] = str(dst)
            logging.info(f"[BACKUP] {src.name} -> {dst.name}")
    return backups


def restore_state_files(backups: dict) -> None:
    for src_str, bak_str in backups.items():
        src = Path(src_str)
        bak = Path(bak_str)
        if bak.exists():
            shutil.copy(bak, src)
            bak.unlink()
            logging.info(f"[RESTORE] {bak.name} -> {src.name}")


def main() -> int:
    args = parse_args()

    csv_path = ROOT / args.csv
    if not csv_path.exists():
        # Permitem și calea absolută
        csv_path = Path(args.csv).resolve()
    if not csv_path.exists():
        print(f"EROARE: CSV nu există: {args.csv}", file=sys.stderr)
        return 2

    # Mapare game_type pentru engine ('6_49' -> '6/49', '5_40' -> '5/40')
    game_map = {"joker": "joker", "6_49": "6/49", "5_40": "5/40"}
    game_engine_label = game_map[args.game]

    out_dir = ROOT / "scratch"
    out_dir.mkdir(exist_ok=True)
    out_json = out_dir / f"{args.output_prefix}_results.json"
    out_txt = out_dir / f"{args.output_prefix}_results.txt"
    log_path = out_dir / f"{args.output_prefix}_log.txt"

    setup_logging(log_path)

    pool_history = ROOT / "pool_history.json"
    adaptive_state = ROOT / "adaptive_state.json"

    smart_reduction = not args.no_smart_reduction
    enable_hard_inv = not args.no_hard_inversion
    mode_label = "ABLATION (regime_reset only)" if not enable_hard_inv else "FULL v2 (reset+hard_inv)"

    logging.info("=" * 80)
    logging.info(f"OVERNIGHT BACKTEST — Adaptive Feedback v2 — {mode_label}")
    logging.info("=" * 80)
    logging.info(f"CSV:        {csv_path}")
    logging.info(f"Game:       {game_engine_label} | pool={args.pool_size} | guarantee={args.guarantee}")
    logging.info(f"Iterații:   {args.iterations} | lookback={args.lookback_percent}%")
    logging.info(f"Smart red:  {smart_reduction}  Hard inv: {enable_hard_inv}")
    logging.info(f"Output:     {args.output_prefix}_results.{{json,txt}}, log={log_path.name}")

    backups = backup_state_files([pool_history, adaptive_state])

    try:
        from loto_enterprise.core.backtesting import LotoBacktester

        backtester = LotoBacktester(str(csv_path), game_type=game_engine_label)
        n_total = len(backtester.draws)
        if n_total < args.iterations + 5:
            logging.error(f"CSV are doar {n_total} extrageri — insuficient pentru {args.iterations} iter.")
            return 3
        depth_pct = (args.iterations / n_total) * 100.0
        logging.info(f"Total extrageri: {n_total} | depth: {depth_pct:.2f}%")

        t0 = time.time()
        predictions = backtester.run_retroactive_backtest(
            pool_size=args.pool_size,
            guarantee=args.guarantee,
            lookback_percent=args.lookback_percent,
            backtest_depth_percent=depth_pct,
            filter_consecutives=True,
            max_variants=0,
            simulation_step=1,
            use_feedback=True,
            enable_hard_inversion=enable_hard_inv,
        )
        elapsed = time.time() - t0

        if not predictions:
            logging.error("Niciun rezultat — backtest eșuat.")
            return 4

        pool_hits = [int(p.hits_union) for p in predictions]
        var_hits = [int(p.hits) for p in predictions]
        baseline = baseline_random_hits(args.game, args.pool_size)

        n = len(pool_hits)
        mean_pool = mean(pool_hits)
        mean_var = mean(var_hits)
        std_pool = stdev(pool_hits) if n > 1 else 0.0
        n_cat = sum(1 for h in pool_hits if h == 0)
        cat_rate = n_cat / n * 100.0
        n_under = sum(1 for h in pool_hits if h == 1)
        under_rate = n_under / n * 100.0

        dist: dict = {}
        for h in pool_hits:
            dist[h] = dist.get(h, 0) + 1

        max_streak_zero = 0
        cur = 0
        for h in pool_hits:
            if h == 0:
                cur += 1
                max_streak_zero = max(max_streak_zero, cur)
            else:
                cur = 0

        recoveries = [pool_hits[i + 1] for i in range(len(pool_hits) - 1) if pool_hits[i] == 0]
        avg_recovery = mean(recoveries) if recoveries else None

        # Confidence interval pentru mean (95%, normal approx)
        if n > 1:
            sem = std_pool / (n ** 0.5)
            ci_low = mean_pool - 1.96 * sem
            ci_high = mean_pool + 1.96 * sem
            ci_str = f"[{ci_low:.3f}, {ci_high:.3f}]"
        else:
            ci_str = "N/A"

        report = [
            "=" * 80,
            f"OVERNIGHT BACKTEST — {datetime.now().isoformat(timespec='seconds')}",
            "=" * 80,
            f"Mode:       {mode_label}",
            f"Game:       {game_engine_label} | pool={args.pool_size} | iterații={n}",
            f"Smart red:  {smart_reduction}  Hard inv: {enable_hard_inv}",
            f"Timp:       {elapsed/60:.1f} min ({elapsed/n:.1f}s/iter)",
            "",
            "--- POOL HITS ---",
            f"Mean:                {mean_pool:.3f}  (random baseline: {baseline:.3f})",
            f"95% CI:              {ci_str}",
            f"Diff vs random:      {(mean_pool - baseline):+.3f}  ({(mean_pool/baseline - 1)*100:+.1f}%)",
            f"Std:                 {std_pool:.3f}",
            f"Best:                {max(pool_hits)}",
            f"Catastrophes (0):    {n_cat}/{n} = {cat_rate:.1f}%",
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
            report.append(f"  {h} hits: {dist[h]:3d} ({pct:5.1f}%)")
        report.append("")
        report.append("=" * 80)

        report_str = "\n".join(report)
        logging.info("\n" + report_str)

        with out_txt.open("w", encoding="utf-8") as f:
            f.write(report_str)
        logging.info(f"Raport salvat: {out_txt}")

        results_json = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "config": {
                "game_type": game_engine_label,
                "csv": str(csv_path),
                "pool_size": args.pool_size,
                "guarantee": args.guarantee,
                "n_iterations": n,
                "lookback_percent": args.lookback_percent,
                "smart_reduction": smart_reduction,
                "enable_hard_inversion": enable_hard_inv,
                "mode": mode_label,
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
                "ci95_low": ci_low if n > 1 else None,
                "ci95_high": ci_high if n > 1 else None,
            },
            "distribution": dist,
            "pool_hits_per_iter": pool_hits,
            "variant_hits_per_iter": var_hits,
        }
        with out_json.open("w", encoding="utf-8") as f:
            json.dump(results_json, f, indent=2)
        logging.info(f"JSON salvat: {out_json}")

        return 0

    except Exception as e:
        logging.exception(f"EROARE FATALĂ: {e}")
        return 1
    finally:
        restore_state_files(backups)
        logging.info("State files restaurate. End.")


if __name__ == "__main__":
    sys.exit(main())
