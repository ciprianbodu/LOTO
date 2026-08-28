"""Rich console rendering of the benchmark report."""

from __future__ import annotations

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box


def _fmt(v, prec=3):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{prec}f}"
    return str(v)


def render_hardware(console: Console, hw_snap: dict) -> None:
    cpu = hw_snap.get("cpu", {})
    ram = hw_snap.get("ram", {})
    body = [
        f"[bold]CPU[/bold]    : {cpu.get('processor', 'n/a')}",
        f"          {cpu.get('physical_cores', '?')} cores / "
        f"{cpu.get('logical_cores', '?')} threads @ {cpu.get('max_freq_mhz', '?')} MHz",
        f"[bold]RAM[/bold]    : {ram.get('total_gb', '?')} GB total "
        f"({ram.get('used_gb', '?')} used | {ram.get('available_gb', '?')} free)",
    ]
    console.print(Panel("\n".join(body), title="[bold]HARDWARE[/bold]", border_style="cyan"))


def render_methods_table(console: Console, methods: list[str], method_meta_map: dict) -> None:
    t = Table(title="Methods to test", box=box.SIMPLE_HEAD)
    t.add_column("#", justify="right", style="dim")
    t.add_column("Method")
    t.add_column("Family")
    t.add_column("Trained?")
    t.add_column("Status")
    t.add_column("Notes", overflow="fold")
    for i, m in enumerate(methods, 1):
        meta = method_meta_map[m]
        status = "[red]N/A[/red]" if not meta["available"] else "[green]OK[/green]"
        t.add_row(
            str(i), m, meta["family"],
            "yes" if meta["requires_train"] else "no",
            status,
            (meta.get("unavailable_reason") or meta.get("notes") or "")[:80],
        )
    console.print(t)


def render_per_game(console: Console, report: dict) -> None:
    for game_key, data in report["games"].items():
        title = f"[bold magenta]{data['label']}[/bold magenta]  (max={data['max_num']}, draw={data['draw_n']})"
        console.print()
        console.rule(title)

        pool_keys: list[str] = data["pool_keys"]
        per_method = data["per_method"]

        # Table 1: avg_hits per pool size, per method
        t1 = Table(title=f"avg hits per draw at pool top-K  (K = {', '.join(k[1:] for k in pool_keys)})",
                   box=box.MINIMAL_HEAVY_HEAD)
        t1.add_column("Method")
        t1.add_column("Family", style="dim")
        for k in pool_keys:
            t1.add_column(k, justify="right")
        t1.add_column("vs Random*", justify="right")
        # Order: by overall ranking
        ordered = [r["method"] for r in data["overall_ranking"]]
        skipped = [m for m, d in per_method.items() if d.get("skipped") and d.get("available") is False]
        for m in ordered:
            d = per_method[m]
            row = [m, d.get("family", "-")]
            for k in pool_keys:
                stats = d.get("per_pool", {}).get(k)
                if stats:
                    row.append(_fmt(stats["avg_hits_real"], 3))
                else:
                    row.append("-")
            base_k = pool_keys[0]
            random_stat = per_method.get("random", {}).get("per_pool", {}).get(base_k)
            if random_stat and d.get("per_pool", {}).get(base_k):
                lift = d["per_pool"][base_k]["avg_hits_real"] - random_stat["avg_hits_real"]
                color = "green" if lift > 0 else ("yellow" if abs(lift) < 1e-3 else "red")
                row.append(f"[{color}]{lift:+.3f}[/{color}]")
            else:
                row.append("-")
            t1.add_row(*row)
        for m in skipped:
            d = per_method[m]
            t1.add_row(f"[dim]{m}[/dim]", d.get("family", "-") or "-",
                       *(["[dim]skip[/dim]"] * (len(pool_keys) + 1)))
        console.print(t1)

        # Table 2: walk-forward regressive — avg_hits at base K (= draw_n) per percentile.
        # We pull this from the folds.csv-derived per_method... wait, we don't have it
        # here. Build it from the report by reading the underlying csv (passed in).
        # Skipped — handled by render_regressive_table that takes folds.csv.

        # Table 3: hardware footprint per method (CPU/RAM)
        t3 = Table(title="Hardware footprint", box=box.MINIMAL_HEAVY_HEAD)
        t3.add_column("Method")
        t3.add_column("CPU% peak", justify="right")
        t3.add_column("CPU% avg", justify="right")
        t3.add_column("RAM peak (GB)", justify="right")
        t3.add_column("avg t/fold (s)", justify="right")
        for m in ordered:
            d = per_method[m]
            t3.add_row(
                m,
                _fmt(d.get("cpu_pct_peak", 0.0), 1),
                _fmt(d.get("cpu_pct_avg", 0.0), 1),
                _fmt(d.get("ram_gb_peak", 0.0), 2),
                _fmt(d.get("mean_runtime_sec", 0.0), 1),
            )
        console.print(t3)

        # Winners panel — both conditions side-by-side
        winners_nobl = data.get("winners_per_pool", {})
        winners_bl = data.get("winners_per_pool_bl", {})
        winners_best = data.get("winners_per_pool_best", {})
        if winners_nobl or winners_bl:
            t4 = Table(title="Winner per pool size (NO blacklist  vs  WITH blacklist  vs  BEST)",
                       box=box.HEAVY_EDGE, style="bold green")
            t4.add_column("Pool K")
            t4.add_column("Winner NO-BL")
            t4.add_column("avg", justify="right")
            t4.add_column("Winner +BL")
            t4.add_column("avg", justify="right")
            t4.add_column("BEST")
            t4.add_column("use BL?")
            t4.add_column("Δ", justify="right")
            for k in pool_keys:
                wn = winners_nobl.get(k, {})
                wb = winners_bl.get(k, {})
                wbest = winners_best.get(k, {})
                if not wn and not wb:
                    continue
                delta = wbest.get("delta_vs_no_bl") if wbest.get("use_blacklist") else wbest.get("delta_vs_with_bl")
                use_bl = "YES" if wbest.get("use_blacklist") else "no"
                use_color = "[green]YES[/green]" if wbest.get("use_blacklist") else "[yellow]no[/yellow]"
                t4.add_row(
                    k[1:],
                    wn.get("winner", "-"),
                    _fmt(wn.get("avg_hits", 0.0), 3),
                    wb.get("winner", "-"),
                    _fmt(wb.get("avg_hits", 0.0), 3),
                    wbest.get("winner", "-"),
                    use_color,
                    _fmt(delta or 0.0, 3),
                )
            console.print(t4)


def render_regressive_table(console: Console, folds_df: pd.DataFrame, game_key: str, game_label: str, draw_n: int) -> None:
    """Per-percentile breakdown for the base pool K=draw_n (real data only)."""
    base_col = f"k{draw_n}"
    sub = folds_df[(folds_df["game"] == game_key) & (folds_df["is_random"] == False) & (folds_df.get("failed", False) == False)]  # noqa: E712
    if sub.empty or base_col not in sub.columns:
        return
    pcts = sorted(sub["percentile"].unique())
    methods = sorted(sub["method"].unique())

    # Anchor row: random baseline at each percentile
    rnd_sub = folds_df[(folds_df["game"] == game_key) & (folds_df["is_random"] == True) & (folds_df["method"] == "random")]  # noqa: E712

    t = Table(
        title=f"[{game_label}] Regressive walk-forward — avg hits @ K={draw_n} per percentile window",
        box=box.MINIMAL_HEAVY_HEAD,
    )
    t.add_column("Method")
    for p in pcts:
        t.add_column(f"{p}%", justify="right")
    t.add_column("mean", justify="right", style="bold")
    # Compute mean per method
    rows: list[tuple] = []
    for m in methods:
        cells = []
        vals: list[float] = []
        for p in pcts:
            r = sub[(sub["method"] == m) & (sub["percentile"] == p)]
            if r.empty:
                cells.append("-")
            else:
                v = float(r[base_col].mean())
                cells.append(f"{v:.3f}")
                vals.append(v)
        mean = sum(vals) / len(vals) if vals else 0.0
        rows.append((m, cells, mean))
    rows.sort(key=lambda r: r[2], reverse=True)
    for m, cells, mean in rows:
        t.add_row(m, *cells, f"{mean:.3f}")
    console.print(t)
