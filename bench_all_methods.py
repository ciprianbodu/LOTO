"""LOTO time-series benchmark laboratory (CLI, 100% pure-laboratorul).

Specul:
    • auto-detect ISTORIC/ (sau _LOTO/istoric/) cu CSV-uri per joc
    • teste pe FIECARE joc: 6/49, 5/40, Joker (Urna 1 = 5/45, Urna 2 = 1/20)
    • toate cele 21 modele din lista (cele instalate rulează; restul sunt
      raportate ca N/A cu motiv)
    • walk-forward regresiv pe 10, 20, 30, ..., 90, 100 % din istoric
    • Top-K hits pentru pool-uri DRAW_SIZE .. DRAW_SIZE + 6 (Urna 1)
      sau DRAW_SIZE (Urna 2)
    • telemetrie: CPU% peak/avg, RAM peak, GPU% peak/avg, VRAM peak
    • output în consolă cu tabele `rich`
    • la final scrie best_methods.json (per-pool winner per joc)

Forțare CUDA: toate modelele neuronale rulează pe `torch.device("cuda")`
(spec); modelele NF aruncă RuntimeError dacă CUDA lipsește.

Usage
-----
    python bench_all_methods.py                               # Full (73 metode: CPU + top-3 GPU)
    python bench_all_methods.py --percentiles 10,30,50,70,100
    python bench_all_methods.py --methods random,frequency,timesfm,chronos
    python bench_all_methods.py --block-size 50               # walk-forward più fine
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from loto_enterprise.benchmark.hardware import snapshot as hw_snapshot
from loto_enterprise.benchmark.methods import METHODS, list_methods, method_meta
from loto_enterprise.benchmark.reporting import (
    render_hardware,
    render_methods_table,
    render_per_game,
    render_regressive_table,
)
from loto_enterprise.benchmark.runner import discover_games, run_benchmark


# Ordered exactly as the spec lists them (A → E):
# A. Fundaționale Zero-Shot
# B. DL Liniare/MLP
# C. DL Transformers
# D. DL Probabilistice/Recurente/SSM
# E. Random baseline + classical for context
# Lista bench: TOATE metodele SIMPLE (CPU, instant) + DOAR top-3 GPU dovedite.
#
# Decizie pe DATE REALE (folds.csv, 288 fold-uri): pe loterie (aleatoare), metodele
# simple CPU (recency/frequency) au batut TOATE retelele neurale GPU, iar diferentele
# sunt zgomot statistic. Modelele GPU grele (informer/fedformer) au iesit chiar SUB
# random, consumand 90%+ GPU. Deci pastram toate CPU (cost ~zero) + cele 3 GPU care au
# avut cel mai bun lift masurat (patchtst +0.009, dlinear +0.007, autoformer +0.005).
# TOP 5 GPU dupa lift masurat (folds.csv): patchtst +0.009, dlinear +0.007,
# autoformer +0.005, moment +0.005, tcn +0.003. Restul (informer/fedformer = sub
# random; ~80 retele grele) scoase: timp imens, rezultat <= random.
_GPU_KEEP = {"patchtst", "dlinear", "autoformer", "moment", "tcn"}


def _is_gpu_method(m: str) -> bool:
    fam = method_meta(m).get("family", "")
    return (m.startswith("torch_") or m.startswith("ens_torch") or m.endswith("_gpu")
            or fam.startswith("nf-") or fam.startswith("foundation") or fam == "ssm")


ALL_SPEC_METHODS = [
    m for m in list_methods()
    if method_meta(m).get("available", True)
    and (not _is_gpu_method(m) or m in _GPU_KEEP)
]

QUICK_METHODS = ["random", "frequency", "recency", "dlinear"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark regresiv multi-model pentru predicție LOTO"
    )
    parser.add_argument("--istoric", default=None,
                        help="Folder cu CSV-uri istorice (default: auto-detect)")
    parser.add_argument("--out", default="bench_results")
    parser.add_argument(
        "--methods", default=None,
        help="Comma-sep list; default = toate 21 metode din spec",
    )
    parser.add_argument(
        "--percentiles", default="10,20,30,40,50,60,70,80,90,100",
        help="Ferestre walk-forward, default 10..100 cu pas 10",
    )
    parser.add_argument("--block-size", type=int, default=99999,
                        help="Walk-forward re-score block; default = score-once-per-fold. "
                             "block_size=1 = true per-step walk-forward (foarte lent).")
    parser.add_argument("--quick", action="store_true",
                        help="Quick: baseline + 1 NF")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--no-rich", action="store_true",
                        help="Plain-text output în loc de rich tables")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignora cache-ul disk (.bench_cache/). Folosit cand: "
                             "(a) utilizatorul forteaza rebench, (b) hardware s-a "
                             "schimbat (CPU->GPU) si rezultatele cache-uite sunt stale.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    # Logăm SIMULTAN la consolă ȘI în bench_full.log (mode='w' = fresh la fiecare
    # rulare). Astfel logul există indiferent cum e pornit bench-ul (manual sau din
    # UI, pe orice OS) — esențial ca să vedem unde/de ce se oprește un full rebench
    # (ex. ultima linie 'START compute' înainte de un crash GPU/OOM necapturabil).
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bench_full.log", encoding="utf-8", mode="w"),
        ],
    )

    methods = (
        QUICK_METHODS if args.quick
        else (args.methods.split(",") if args.methods else ALL_SPEC_METHODS)
    )
    methods = [m.strip() for m in methods if m.strip()]
    unknown = [m for m in methods if m not in METHODS]
    if unknown:
        print(f"Unknown methods: {unknown}", file=sys.stderr)
        print(f"Available     : {list_methods()}", file=sys.stderr)
        return 2

    pcts = [int(x) for x in args.percentiles.split(",") if x.strip()]
    console = Console(force_terminal=not args.no_rich)

    hw = hw_snapshot()
    render_hardware(console, hw)
    console.print()

    meta_map = {m: method_meta(m) for m in methods}
    render_methods_table(console, methods, meta_map)
    console.print()

    games = discover_games(args.istoric)
    console.rule("[bold]Jocuri detectate[/bold]")
    for g in games:
        console.print(
            f"  • [bold]{g.label}[/bold]  "
            f"CSV=[cyan]{g.csv_path}[/cyan]  max_num={g.max_num} draw_n={g.draw_n}  "
            f"pool_range = {g.draw_n}..{g.draw_n + g.pool_extra}"
            + (" [italic dim](single-pick → pool = draw_n)[/italic dim]" if g.is_single_pick else "")
        )
    console.print()

    # Progress bar over folds
    available_methods = [m for m in methods if meta_map[m]["available"]]
    total_est = len(games) * len(available_methods) * len(pcts) * 2

    console.rule(f"[bold]Sweep start — est. {total_est} folds[/bold]")

    progress = Progress(
        TextColumn("[bold blue]bench[/bold blue]"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("•"),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )
    task_id = None
    # Counter independent (rich Progress overwrite-uieste in-place pe TTY, dar
    # nu emite linii utile la --no-rich pe stream redirected → app.py UI nu poate
    # extrage progresul din log. Emitem aici o linie distinctiva "[done/total]"
    # per fold pentru ca polling-ul din app.py:1629 sa o poata parsa.)
    _folds_done = {"n": 0}

    def _cb(idx, total, fr, game):
        nonlocal task_id
        if task_id is None:
            task_id = progress.add_task("", total=total)
        progress.update(
            task_id,
            advance=1,
            description=f"[{game.label}] {fr.method} pct={fr.percentile}% "
                        f"{'RND' if fr.is_random else 'REAL'}  hits@k{game.draw_n}={fr.avg_hits_topk:.3f}",
        )
        # Linie de progres parsabila de UI (app.py cauta regex r"\[(\d+)/(\d+)\]").
        # Scriem direct la stdout (flush imediat) ca sa apara in bench_full.log
        # in timp real, nu doar la finalul rularii.
        _folds_done["n"] += 1
        try:
            sys.stdout.write(
                f"[{_folds_done['n']}/{total}] {game.label} | {fr.method} "
                f"| pct={fr.percentile}% | {'RND' if fr.is_random else 'REAL'} "
                f"| hits@k{game.draw_n}={fr.avg_hits_topk:.3f}\n"
            )
            sys.stdout.flush()
        except Exception:
            pass

    with progress:
        report = run_benchmark(
            games=games,
            methods=methods,
            percentiles=pcts,
            block_size=args.block_size,
            random_seed=args.seed,
            out_dir=args.out,
            progress_cb=_cb,
            use_cache=not args.no_cache,
        )

    # ─── Console reports ────────────────────────────────────────────────────
    console.print()
    console.rule("[bold green]RAPOARTE PER JOC[/bold green]")

    # Render regressive table from folds.csv
    import pandas as pd
    folds_df = pd.read_csv(Path(args.out) / "folds.csv")

    render_per_game(console, report)
    console.print()
    console.rule("[bold green]REGRESIV — hits @ K=draw_n pe FIECARE fereastră istorică[/bold green]")
    for game in games:
        render_regressive_table(console, folds_df, game.key, game.label, game.draw_n)

    # ─── best_methods.json ─────────────────────────────────────────────────
    # Schema v3: per pool we record BOTH "no blacklist" and "with blacklist"
    # winners + a "best" hint that production engine reads to decide.
    best = {
        "_meta": {
            "hardware": hw,
            "block_size": args.block_size,
            "percentiles": pcts,
            "methods_tested": methods,
            "n_folds_total": report.get("n_folds_total", 0),
            "blacklist_rule": "numere absente din ultimele 50-200 extrageri (semnal independent de scorer)",
        },
        "games": {
            gk: {
                "label": gd["label"],
                "draw_n": gd["draw_n"],
                # Backward-compat (used by older method_selector callers):
                "overall_winner": gd["overall_winner"],
                "winners_per_pool": {
                    k: w.get("winner") for k, w in gd.get("winners_per_pool", {}).items()
                },
                "winner_details": gd.get("winners_per_pool", {}),
                # NEW (v3): with-blacklist winners
                "overall_winner_bl": gd.get("overall_winner_bl"),
                "winners_per_pool_bl": {
                    k: w.get("winner") for k, w in gd.get("winners_per_pool_bl", {}).items()
                },
                "winner_details_bl": gd.get("winners_per_pool_bl", {}),
                # NEW (v3): best of (no-bl, with-bl) — what production should use
                "winners_per_pool_best": gd.get("winners_per_pool_best", {}),
            }
            for gk, gd in report["games"].items()
        },
    }
    out_path = Path(args.out)
    out_path.mkdir(exist_ok=True, parents=True)
    from ui_shared import atomic_write_json
    atomic_write_json("best_methods.json", best)  # atomic: tmp+fsync+os.replace

    # Stamp CSV signatures so freshness detection knows when cache is stale
    try:
        from loto_enterprise.benchmark.freshness import write_signatures_to_best_methods
        write_signatures_to_best_methods()
    except Exception as _e:
        logging.warning(f"[freshness] failed to stamp signatures: {_e}")

    # Build per-(game, pool) auto-pilot matrix from folds.csv
    try:
        from loto_enterprise.benchmark.decision import update_best_methods_with_auto_pilot
        update_best_methods_with_auto_pilot()
    except Exception as _e:
        logging.warning(f"[auto-pilot] failed to build decision matrix: {_e}")

    # ─── Final summary panel ────────────────────────────────────────────────
    console.print()
    from rich.panel import Panel
    lines = ["[bold]INTEGRARE FINALĂ — câștigător per pool per joc (NO-BL  |  +BL  |  BEST)[/bold]\n"]
    for gk, gd in report["games"].items():
        lines.append(f"\n[bold magenta]{gd['label']}[/bold magenta]")
        wpp = gd.get("winners_per_pool", {})
        wpp_bl = gd.get("winners_per_pool_bl", {})
        wpp_best = gd.get("winners_per_pool_best", {})
        if not wpp:
            lines.append("  (niciun rezultat valid)")
            continue
        for k in gd["pool_keys"]:
            w = wpp.get(k, {})
            wb = wpp_bl.get(k, {})
            wbest = wpp_best.get(k, {})
            if not w.get("winner"):
                continue
            fam = report["method_meta"].get(w["winner"], {}).get("family", "-")
            use_bl_label = (
                f"[green]+BL[/green] (Δ+{wbest.get('delta_vs_no_bl',0):.3f})"
                if wbest.get("use_blacklist") else
                f"[yellow]no-BL[/yellow] (Δ+{wbest.get('delta_vs_with_bl',0):.3f})"
            )
            lines.append(
                f"  K={k[1:]:>2s}  no-BL: [cyan]{w['winner']:<12s}[/cyan] {w['avg_hits']:.3f}  │  "
                f"+BL: [cyan]{wb.get('winner','-'):<12s}[/cyan] {wb.get('avg_hits',0):.3f}  │  "
                f"BEST: [bold green]{wbest.get('winner','-')}[/bold green] {use_bl_label}"
            )
        lines.append(
            f"  [dim]overall: no-BL={gd.get('overall_winner')}  |  +BL={gd.get('overall_winner_bl')}[/dim]"
        )
    console.print(Panel("\n".join(lines), title="[bold]best_methods.json[/bold]", border_style="green"))

    console.print()
    console.print(f"[dim]Saved:[/dim]")
    console.print(f"  • [cyan]{out_path / 'folds.csv'}[/cyan]")
    console.print(f"  • [cyan]{out_path / 'report.json'}[/cyan]")
    console.print(f"  • [cyan]best_methods.json[/cyan]  (consumed by method_selector)")
    console.print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
