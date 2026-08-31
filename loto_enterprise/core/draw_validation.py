"""Validarea canonică a unei extrageri pentru engine, WF și benchmark.

O extragere utilizabilă are exact ``draw_n`` numere întregi, distincte, în
intervalul jocului. Aplicarea aceleiași reguli peste tot e importantă: altfel
bench-ul poate alege un scorer pe alt set de date decât cel folosit de engine
sau walk-forward.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def valid_draw_matrix(
    df: pd.DataFrame,
    columns: Sequence[str],
    *,
    draw_n: int,
    max_num: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Returnează matricea validă și masca rândurilor acceptate.

    Valorile non-numerice, zecimale, în afara intervalului sau duplicate nu se
    transformă tăcut în extrageri valide. Matricea are mereu forma
    ``(n_valid, draw_n)``, inclusiv când nu există niciun rând acceptat.
    """
    cols = list(columns)
    if len(cols) != int(draw_n):
        raise ValueError(
            f"sunt necesare exact {draw_n} coloane de numere, am primit {len(cols)}"
        )
    missing = [col for col in cols if col not in df.columns]
    if missing:
        raise ValueError(f"lipsesc coloanele de numere: {missing}")

    raw = df.loc[:, cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
    finite = np.isfinite(raw)
    whole = finite & (raw == np.floor(raw))
    integers = np.where(whole, raw, 0.0).astype(np.int64)
    in_range = (integers >= 1) & (integers <= int(max_num))
    no_duplicates = np.all(np.diff(np.sort(integers, axis=1), axis=1) != 0, axis=1)
    valid = np.all(whole & in_range, axis=1) & no_duplicates
    return integers[valid], valid
