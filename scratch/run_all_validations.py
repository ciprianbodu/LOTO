"""
Validation Suite Runner — lansează multiple scenarii overnight în secvență.

Scenarii:
    1. Joker 100-iter PRODUCTION (full v2: regime_reset + hard_inversion)
       → prefix `overnight_100j_full`
    2. Joker 50-iter ABLATION (regime_reset only, NO hard_inversion)
       → prefix `overnight_50j_ablation`
    3. (opțional) 6/49 50-iter PRODUCTION dacă există input_649.csv
    4. (opțional) 5/40 50-iter PRODUCTION dacă există input_540.csv

Toate rulează cu pool=12, lookback=30%, smart_reduction=True.

La final scrie un sumar agregat în `scratch/validation_suite_summary.{json,txt}`.

Estimare timp pe CPU laptop:
    33s/iter × 100 (Joker full)  ≈ 55 min
    33s/iter × 50  (Joker abl.)  ≈ 28 min
    + 2× 50 iter cross-game dacă CSV-urile există ≈ 56 min
    Total max: ~140 min (≈ 2h20m)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
SCRATCH = ROOT / "scratch"
PYTHON = sys.executable
SCRIPT = SCRATCH / "overnight_backtest.py"

# (label, prefix, args_list, optional_csv_check)
SCENARIOS = [
    (
        "Joker 100-iter PRODUCTION",
        "overnight_100j_full",
        ["--game", "joker", "--csv", "input.csv", "--iterations", "100",
         "--pool-size", "12", "--output-prefix", "overnight_100j_full"],
        "input.csv",
    ),
    (
        "Joker 50-iter ABLATION (no hard_inv)",
        "overnight_50j_ablation",
        ["--game", "joker", "--csv", "input.csv", "--iterations", "50",
         "--pool-size", "12", "--no-hard-inversion",
         "--output-prefix", "overnight_50j_ablation"],
        "input.csv",
    ),
    (
        "6/49 50-iter PRODUCTION (optional)",
        "overnight_649",
        ["--game", "6_49", "--csv", "input_649.csv", "--iterations", "50",
         "--pool-size", "12", "--output-prefix", "overnight_649"],
        "input_649.csv",
    ),
    (
        "5/40 50-iter PRODUCTION (optional)",
        "overnight_540",
        ["--game", "5_40", "--csv", "input_540.csv", "--iterations", "50",
         "--pool-size", "12", "--output-prefix", "overnight_540"],
        "input_540.csv",
    ),
]


def run_scenario(label: str, prefix: str, args_list: list, csv_required: str) -> dict:
    csv_path = ROOT / csv_required
    if not csv_path.exists():
        print(f"\n[SKIP] {label} — CSV lipsă: {csv_required}\n", flush=True)
        return {"label": label, "prefix": prefix, "status": "skipped",
                "reason": f"missing_csv:{csv_required}"}

    print(f"\n{'#' * 80}")
    print(f"# START: {label}")
    print(f"# CMD: {PYTHON} {SCRIPT} {' '.join(args_list)}")
    print(f"# Time: {datetime.now().isoformat(timespec='seconds')}")
    print(f"{'#' * 80}\n", flush=True)

    t_start = datetime.now()
    proc = subprocess.run(
        [PYTHON, str(SCRIPT), *args_list],
        cwd=str(ROOT),
        env=None,
    )
    t_end = datetime.now()
    elapsed = (t_end - t_start).total_seconds()

    json_out = SCRATCH / f"{prefix}_results.json"
    summary = None
    if json_out.exists():
        try:
            with json_out.open("r", encoding="utf-8") as f:
                data = json.load(f)
            summary = data.get("summary", {})
        except Exception as e:
            print(f"[WARN] Nu pot citi {json_out.name}: {e}")

    status = "ok" if proc.returncode == 0 else f"failed_rc{proc.returncode}"
    print(f"\n[DONE] {label} — {status} — {elapsed/60:.1f} min\n", flush=True)

    return {
        "label": label,
        "prefix": prefix,
        "status": status,
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "started": t_start.isoformat(timespec="seconds"),
        "finished": t_end.isoformat(timespec="seconds"),
        "summary": summary,
    }


def main() -> int:
    print(f"VALIDATION SUITE RUNNER — start {datetime.now().isoformat(timespec='seconds')}")
    print(f"Python: {PYTHON}")
    print(f"Script: {SCRIPT}")
    print(f"Total scenarii: {len(SCENARIOS)}\n")

    results = []
    for label, prefix, args_list, csv_req in SCENARIOS:
        r = run_scenario(label, prefix, args_list, csv_req)
        results.append(r)

    # Sumar agregat
    summary_json = SCRATCH / "validation_suite_summary.json"
    summary_txt = SCRATCH / "validation_suite_summary.txt"

    with summary_json.open("w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "scenarios": results,
        }, f, indent=2)

    lines = [
        "=" * 80,
        f"VALIDATION SUITE SUMMARY — {datetime.now().isoformat(timespec='seconds')}",
        "=" * 80,
        "",
    ]
    for r in results:
        lines.append(f">> {r['label']} [{r['status']}]")
        if r['status'] == 'skipped':
            lines.append(f"   Reason: {r.get('reason')}")
        elif r.get('summary'):
            s = r['summary']
            mean_h = s.get('mean_pool_hits', 0)
            cat = s.get('catastrophe_rate_pct', 0)
            best = s.get('best_pool_hits', 0)
            base = s.get('random_baseline', 0)
            ci_lo = s.get('ci95_low')
            ci_hi = s.get('ci95_high')
            ci = f"[{ci_lo:.3f},{ci_hi:.3f}]" if ci_lo is not None else "N/A"
            lines.append(f"   mean={mean_h:.3f}  CI95={ci}  cat={cat:.1f}%  best={best}  baseline={base:.3f}")
            lines.append(f"   elapsed={r['elapsed_seconds']/60:.1f} min")
        else:
            lines.append(f"   returncode={r.get('returncode')}, elapsed={r.get('elapsed_seconds', 0)/60:.1f} min")
        lines.append("")

    lines.append("=" * 80)
    summary_txt.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"Sumar: {summary_txt}")

    failed = [r for r in results if r['status'].startswith('failed')]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
