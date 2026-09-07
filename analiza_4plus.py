"""Analiză 4+ pe procente de backtesting (percentile).

Răspunde la întrebarea: PE CE PROCENT DE BACKTESTING s-au prins cele mai multe
4+ numere ghicite, per joc — și cu ce metodă.

Citește bench_results/folds.csv (scris de runner la FINALUL bench-ului; coloana
`rate_4plus` = fracția extragerilor în care pool-ul a prins ≥4 numere).

Rulare:
    .venv\\Scripts\\python analiza_4plus.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

FOLDS = Path(__file__).resolve().parent / "bench_results" / "folds.csv"


def main() -> int:
    if not FOLDS.exists():
        print(f"Nu există {FOLDS} — rulează un Re-Bench întâi.")
        return 1
    df = pd.read_csv(FOLDS)
    if "rate_4plus" not in df.columns:
        print("folds.csv e vechi (fără coloana rate_4plus) — rulează un Re-Bench cu codul nou.")
        return 1
    # doar rândurile REALE (nu baseline-ul random), care n-au eșuat
    if "is_random" in df.columns:
        df = df[df["is_random"] == False]  # noqa: E712
    if "failed" in df.columns:
        df = df[df["failed"] != True]  # noqa: E712
    if df.empty:
        print("folds.csv nu are rânduri reale utilizabile.")
        return 1

    # Referinta: rata TEORETICA hipergeometrica la pool-ul de baza (K=draw_n),
    # aceeasi conventie ca decision.py (CLAUDE.md §5 pct. 4) — nu o realizare
    # empirica zgomotoasa a randului `random`.
    try:
        from loto_enterprise.benchmark.decision import expected_random_rate
        from loto_enterprise.benchmark.runner import discover_games
        _geo = {g.key: g for g in discover_games()}
    except Exception:  # noqa: BLE001
        _geo = {}

    for game, gdf in df.groupby("game"):
        print(f"\n=== {game} ===")
        g = _geo.get(game)
        baseline = expected_random_rate(g.max_num, g.draw_n, g.draw_n, 4) if g else None
        if baseline is not None:
            print(f"  baseline random (hipergeometric, k={g.draw_n}): 4+: {baseline * 100:.2f}%")
        # Top procente de backtesting după rate_4plus MEDIU (peste toate metodele)
        by_pct = gdf.groupby("percentile")["rate_4plus"].mean().sort_values(ascending=False)
        print("  Procente backtesting (4+ mediu peste metode):")
        for pct, r in by_pct.head(10).items():
            print(f"    {int(pct):>3}%  →  4+: {r * 100:5.2f}%")
        best_pct = int(by_pct.index[0])
        print(f"  ➜ CEL MAI BUN procent (mediu): {best_pct}%  (4+: {by_pct.iloc[0] * 100:.2f}%)")
        # Cea mai bună combinație individuală metodă × percentilă
        best = gdf.loc[gdf["rate_4plus"].idxmax()]
        fam = f" [{best['family']}]" if "family" in gdf.columns and pd.notna(best.get("family")) else ""
        print(f"  ➜ MAXIM absolut: {best['method']}{fam} @ {int(best['percentile'])}%  "
              f"→ 4+: {best['rate_4plus'] * 100:.2f}%")
        # ATENTIE: maximul e ales din N celule (metoda x percentila) fara corectie
        # de testare multipla — poate fi zgomot, nu o metoda validata separat.
        # Vezi decision.py (poarta de consistenta + Wilson) pentru selectia reala
        # de productie; scriptul asta e diagnostic, nu decizie.
        print("  (ATENTIE: maximul de mai sus e cea mai buna din multe celule "
              "metoda×procent, fara corectie de testare multipla — poate fi "
              "zgomot; nu e o metoda validata separat.)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
