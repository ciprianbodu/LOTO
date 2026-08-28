"""Citește folds.csv (output bench) şi construieşte tabele per (joc, fereastră, model).

Folosit de UI pentru a afişa matricea walk-forward onest, fără re-compute.
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd


# Path ABSOLUT (rădăcina proiectului = parinte[2] al acestui fișier), nu relativ la CWD —
# altfel matricea apărea „indisponibilă" când CWD-ul procesului ≠ rădăcina proiectului
# (leaderboard-ul/ETA folosesc deja path absolut prin PROJECT_ROOT).
FOLDS_CSV = Path(__file__).resolve().parents[2] / "bench_results" / "folds.csv"


def load_folds() -> pd.DataFrame | None:
    if not FOLDS_CSV.exists():
        return None
    try:
        df = pd.read_csv(FOLDS_CSV)
        if "failed" not in df.columns:
            df["failed"] = False
        return df
    except Exception:
        return None


def matrix_for_game(
    df: pd.DataFrame, game_key: str, pool_size: int
) -> pd.DataFrame:
    """Returnează DataFrame [model × percentile] cu avg_hits pentru pool_size dat.

    Doar fold-uri reale (is_random=False, failed=False).
    Coloane: percentile-uri (10, 20, ..., 100). Linii: metode.
    """
    base_col = f"k{pool_size}"
    if base_col not in df.columns:
        return pd.DataFrame()
    sub = df[
        (df["game"] == game_key)
        & (df["is_random"] == False)  # noqa: E712
        & (df["failed"] == False)     # noqa: E712
    ].copy()
    if sub.empty:
        return pd.DataFrame()

    pivot = sub.pivot_table(
        index="method",
        columns="percentile",
        values=base_col,
        aggfunc="mean",
    )
    # Sort rows by mean across windows (best first)
    pivot["__mean__"] = pivot.mean(axis=1)
    pivot = pivot.sort_values("__mean__", ascending=False)
    return pivot.drop(columns=["__mean__"], errors="ignore")


def random_baseline_for_game(
    df: pd.DataFrame, game_key: str, pool_size: int
) -> pd.Series:
    """Returnează seria Random baseline pentru pool dat — pentru calcul lift."""
    base_col = f"k{pool_size}"
    if base_col not in df.columns:
        return pd.Series(dtype=float)
    rnd = df[
        (df["game"] == game_key)
        & (df["is_random"] == False)  # noqa: E712
        & (df["method"] == "random")
        & (df["failed"] == False)     # noqa: E712
    ]
    if rnd.empty:
        return pd.Series(dtype=float)
    return rnd.groupby("percentile")[base_col].mean()


def lift_matrix(
    df: pd.DataFrame, game_key: str, pool_size: int
) -> pd.DataFrame:
    """Matrice de lift (avg_hits − random baseline) per (model, percentile)."""
    matrix = matrix_for_game(df, game_key, pool_size)
    if matrix.empty:
        return matrix
    baseline = random_baseline_for_game(df, game_key, pool_size)
    if baseline.empty:
        return matrix
    # Aliniere coloane şi scădere
    lift = matrix.copy()
    for col in matrix.columns:
        if col in baseline.index:
            lift[col] = matrix[col] - baseline[col]
    return lift


def summary_per_game(df: pd.DataFrame, game_key: str, pool_size: int) -> dict:
    """Sumar text-friendly pentru un (game, pool)."""
    matrix = matrix_for_game(df, game_key, pool_size)
    if matrix.empty:
        return {"available": False}
    means = matrix.mean(axis=1)
    return {
        "available": True,
        "n_methods": len(matrix),
        "n_windows": len(matrix.columns),
        "best_method": str(means.idxmax()),
        "best_mean": float(means.max()),
        "matrix": matrix,
        "lift": lift_matrix(df, game_key, pool_size),
    }
