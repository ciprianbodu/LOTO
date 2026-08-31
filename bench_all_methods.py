"""LOTO time-series benchmark laboratory (CLI, 100% pure-laboratorul).

Specul:
    • auto-detect ISTORIC/ (sau _LOTO/istoric/) cu CSV-uri per joc
    • teste pe FIECARE joc: 6/49, 5/40, Joker (Urna 1 = 5/45, Urna 2 = 1/20)
    • toate metodele din registry (cele instalate rulează; restul N/A cu motiv)
    • walk-forward regresiv pe 10, 20, 30, ..., 90, 100 % din istoric
    • Top-K hits pentru pool-uri DRAW_SIZE .. DRAW_SIZE + 6 (Urna 1)
      sau DRAW_SIZE (Urna 2)
    • telemetrie: CPU% peak/avg, RAM peak
    • output în consolă cu tabele `rich`
    • la final scrie best_methods.json (per-pool winner per joc)

Suport GPU/neural (torch/TimesFM/foundation) eliminat complet — benchmark
exclusiv CPU (numpy/sklearn/statsmodels).

Usage
-----
    python bench_all_methods.py                               # Full (toate metodele available)
    python bench_all_methods.py --percentiles 10,30,50,70,100
    python bench_all_methods.py --methods random,frequency
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


# Lista bench = TOATE metodele disponibile din registry (exclusiv CPU).
# Metodele din disabled_methods.json au fost ELIMINATE din cod (tombstone).
# Pe loterie, diferențele dintre scorere sunt majoritar zgomot statistic.
try:
    from loto_enterprise.benchmark.disabled import load_disabled as _load_disabled
    _DISABLED_METHODS = _load_disabled()
except Exception:  # noqa: BLE001
    _DISABLED_METHODS = set()

_AVAILABLE_METHODS = [
    m for m in list_methods()
    if method_meta(m).get("available", True) and m not in _DISABLED_METHODS
]

# Al DOILEA filtru, REVERSIBIL: curated_methods.json (rădăcina repo). Dacă fișierul
# există și are `active` nevidă, bench-ul rulează DOAR acel subset (criteriu:
# acoperire de semnal distinct, nu clasament). Absent/gol → comportamentul de
# dinainte (toate metodele available minus blacklist). Cele două filtre se compun:
# curated ∩ (available minus disabled). Log-ul se emite în main(), după
# logging.basicConfig() (aici, la import, handler-ele încă nu există).
try:
    from loto_enterprise.benchmark.curated import (
        apply_curation as _apply_curation,
        log_curation as _log_curation,
    )
    ALL_SPEC_METHODS, CURATION_INFO = _apply_curation(_AVAILABLE_METHODS)
except Exception as _exc:  # noqa: BLE001
    ALL_SPEC_METHODS = list(_AVAILABLE_METHODS)
    CURATION_INFO = {"active": False, "n_before": len(_AVAILABLE_METHODS),
                     "n_after": len(_AVAILABLE_METHODS), "error": str(_exc)}

    def _log_curation(info):  # noqa: D103 — no-op dacă modulul lipsește
        return None

QUICK_METHODS = ["random", "frequency"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark regresiv multi-model pentru predicție LOTO"
    )
    parser.add_argument("--istoric", default=None,
                        help="Folder cu CSV-uri istorice (default: auto-detect)")
    parser.add_argument("--out", default="bench_results")
    parser.add_argument(
        "--methods", default=None,
        help="Comma-sep list; default = setul curat (curated_methods.json) peste registry minus blacklist — renumără cu bench_all_methods.ALL_SPEC_METHODS",
    )
    parser.add_argument(
        "--percentiles", default="10,20,30,40,50,60,70,80,90,100",
        help="Ferestre walk-forward, default 10..100 cu pas 10",
    )
    parser.add_argument("--block-size", type=int, default=99999,
                        help="Walk-forward re-score block; default = score-once-per-fold. "
                             "block_size=1 = true per-step walk-forward (foarte lent).")
    parser.add_argument("--quick", action="store_true",
                        help="Quick: random + frequency")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--no-rich", action="store_true",
                        help="Plain-text output în loc de rich tables")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignora cache-ul disk (.bench_cache/). Folosit cand "
                             "utilizatorul forteaza rebench.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-decision", action="store_true",
                        help="Nu scrie best_methods.json (folosit la bench paralel pe faze: "
                             "fiecare faza scrie doar folds.csv in --out propriu; decizia "
                             "se ia separat dupa combinarea folds-urilor).")
    parser.add_argument("--no-shuffled-control", action="store_true",
                        help="Nu rula folds-urile de control pe extrageri AMESTECATE "
                             "(is_random=True). Injumatateste bench-ul (masurat: 50.1%% "
                             "din runtime) si NU afecteaza decizia de productie "
                             "(decision.py filtreaza is_random==False). Pierzi doar "
                             "lift_vs_shuffle din report.json si tie-break-ul secundar "
                             "al winners_per_pool (cale legacy).")
    parser.add_argument("--force-decision", action="store_true",
                        help="Rescrie best_methods.json CHIAR ȘI cu set redus de metode "
                             "(--quick/--methods). Implicit decizia e sărită la seturi "
                             "reduse: un folds.csv cu 2 metode ar înlocui tăcut decizia "
                             "de producție cu low_confidence/frequency peste tot.")
    args = parser.parse_args()

    # Benchmark exclusiv CPU → un singur log.
    _log_name = "bench_full.log"
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(_log_name, encoding="utf-8", mode="w"),
        ],
    )

    # Starea curării în log (acum că handler-ele există). --methods/--quick sunt
    # override-uri EXPLICITE ale utilizatorului → ocolesc curarea, deliberat.
    _explicit = bool(args.quick or args.methods)
    if _explicit and CURATION_INFO.get("active"):
        logging.info("[curated] curare IGNORATĂ: lista de metode e dată explicit "
                     "(--methods/--quick).")
    else:
        _log_curation(CURATION_INFO)

    methods = (
        QUICK_METHODS if args.quick
        else (args.methods.split(",") if args.methods else ALL_SPEC_METHODS)
    )
    methods = [m.strip() for m in methods if m.strip()]
    # Aliasurile legacy (METHOD_ALIASES, ex. ml_catboost_cpu) sunt acceptate de tot
    # stack-ul (method_meta/call_method) — rezolvă-le ÎNAINTE de verificarea unknown,
    # altfel `--methods ml_catboost_cpu` era respins deși ar fi rulat corect.
    from loto_enterprise.benchmark.methods import resolve_method_name as _resolve
    methods = [_resolve(m) for m in methods]
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
    if CURATION_INFO.get("active") and not _explicit:
        console.print(
            f"[bold yellow]🎯 Curare activă:[/bold yellow] {CURATION_INFO['n_after']} metode "
            f"din {CURATION_INFO['n_before']} "
            f"[dim](criteriu: acoperire de semnal, nu clasament — "
            f"anulare: șterge/golește curated_methods.json + re-bench)[/dim]"
        )
    console.print()

    games = discover_games(args.istoric)
    # joker_urna2 trage 1 SINGUR număr (single-pick) → backtesting-ul și regula 4+ n-au
    # sens (nu poți ghici „4+" cu 1 număr; rate_4plus mereu 0). Îl scoatem complet din
    # bench. La generare, engine-ul folosește frequency pentru bila Joker
    # (scorer determinist; nicio metodă nu bate șansa pe 1 număr).
    _n_before = len(games)
    games = [g for g in games if not g.is_single_pick]
    if len(games) < _n_before:
        console.print(f"[dim]Sar {_n_before - len(games)} joc single-pick (ex. joker_urna2) "
                      f"— backtesting irelevant pe 1 număr.[/dim]")
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
            shuffled_control=not args.no_shuffled_control,
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
            # Telemetrie de curare: ca să se vadă în best_methods.json că bench-ul
            # a rulat un SUBSET (și de ce clasamentul are mai puține rânduri).
            "curated": dict(CURATION_INFO),
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

    _skip_decision = bool(args.no_decision)
    if not _skip_decision and _explicit and not args.force_decision:
        # Gardă anti-footgun: un run cu set REDUS (--quick / --methods a,b) scrie un
        # folds.csv redus (OVERWRITE) — dacă decizia rulează pe el, best_methods.json
        # de producție e înlocuit tăcut (ex. --quick → low_confidence/frequency peste
        # tot). UI-ul a scos Quick exact din motivul ăsta; CLI-ul cere acum opt-in.
        _skip_decision = True
        logging.warning("[bench] set redus de metode (--quick/--methods): NU rescriu "
                        "best_methods.json. Forțează explicit cu --force-decision.")
        console.print("[bold yellow]⚠ Set redus de metode — best_methods.json rămâne "
                      "NEATINS (adaugă --force-decision ca să-l rescrii).[/bold yellow]")
    if _skip_decision:
        # Bench paralel pe faze: scriem DOAR folds.csv in --out (deja scris de runner);
        # decizia (best_methods.json) se ia separat dupa combinarea folds-urilor.
        logging.info("[bench] sar scrierea best_methods.json (no-decision/set redus).")
    else:
        from ui_shared import atomic_write_json
        atomic_write_json("best_methods.json", best)  # atomic: tmp+fsync+os.replace

        # Stamp CSV signatures so freshness detection knows when cache is stale
        try:
            from loto_enterprise.benchmark.freshness import write_signatures_to_best_methods
            write_signatures_to_best_methods()
        except Exception as _e:
            logging.warning(f"[freshness] failed to stamp signatures: {_e}")

        # Build per-(game, pool) auto-pilot matrix from folds.csv.
        # ATENTIE: folds-ul rularii CURENTE, din --out (nu default-ul
        # "bench_results/folds.csv"). Fara asta, `--out alt_folder` producea un
        # best_methods.json HIBRID: winners din rularea noua, dar
        # auto_pilot_per_pool (partea pe care o citeste PRODUCTIA) recalculat pe
        # un folds.csv vechi/strain — inclusiv peste un bump de CACHE_VERSION.
        _folds_now = str(out_path / "folds.csv")
        try:
            from loto_enterprise.benchmark.decision import update_best_methods_with_auto_pilot
            update_best_methods_with_auto_pilot(folds_csv_path=_folds_now)
            logging.info("[auto-pilot] decizie construita din %s", _folds_now)
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
    # Titlul + lista „Saved" trebuie să spună ADEVĂRUL despre ce s-a scris:
    # cu `--quick` / `--methods` (fără `--force-decision`) sau cu `--no-decision`
    # garda de mai sus SARE scrierea, dar panoul raporta oricum
    # „Saved: • best_methods.json", adică exact fișierul rămas neatins.
    _panel_title = ("[bold]câștigător per pool (NU s-a scris best_methods.json)[/bold]"
                    if _skip_decision else "[bold]best_methods.json[/bold]")
    console.print(Panel("\n".join(lines), title=_panel_title,
                        border_style="yellow" if _skip_decision else "green"))

    console.print()
    console.print(f"[dim]Saved:[/dim]")
    console.print(f"  • [cyan]{out_path / 'folds.csv'}[/cyan]")
    console.print(f"  • [cyan]{out_path / 'report.json'}[/cyan]")
    if _skip_decision:
        console.print("  • [yellow]best_methods.json NU a fost rescris[/yellow] "
                      "(set redus de metode / --no-decision; forțează cu --force-decision)")
    else:
        console.print(f"  • [cyan]best_methods.json[/cyan]  (consumed by method_selector)")
    console.print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
