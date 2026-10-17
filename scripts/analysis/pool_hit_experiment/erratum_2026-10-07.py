"""Erata din 7 octombrie 2026: efectul corecturii 5/40 asupra celulei ro_540_k11.

Evalueaza `ro_540` de doua ori cu acelasi cod: pe randurile preinregistrate
(ERRATA aplicata, ca `run`) si pe fisierul corectat (ERRATA ignorata), apoi
calculeaza celula ca `run`. Celelalte celule nu citesc fisierul. Nu scrie
nimic; aproximativ 4 minute pe doua procese.

    python erratum_2026-10-07.py
"""

from __future__ import annotations

import importlib.util
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "pool_hit_experiment", HERE / "pool_hit_experiment.py"
)
phe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(phe)

NAME, K, THR = "ro_540", 11, 4


def _evaluate(registered: bool) -> dict:
    reg = json.loads(phe.REG_PATH.read_text(encoding="utf-8"))
    errata = phe.ERRATA
    phe.ERRATA = errata if registered else {}
    try:
        return phe._evaluate_dataset(NAME, reg["datasets"][NAME]["rows"])
    finally:
        phe.ERRATA = errata


def cell(ev: dict) -> dict:
    """Pasii din `run` pentru ro_540_k11: metoda C3 pe dezvoltare, apoi testul."""
    from scipy.stats import binomtest

    _, max_num, draw_n, _, _ = phe.DATASETS[NAME]
    dev = np.asarray(ev["targets"]) < int(phe.DEV_FRACTION * ev["n_rows"])
    H = phe.hits_matrix(ev, K)[dev]
    rate, mean = (H >= THR).mean(axis=0), H.mean(axis=0)
    order = sorted(
        range(len(ev["methods"])), key=lambda m: (-rate[m], -mean[m], ev["methods"][m])
    )
    dev_best = ev["methods"][order[0]]
    hits = phe.candidate_hits(ev, K, dev_best)
    n, p0 = int((~dev).sum()), phe.baseline(max_num, draw_n, K, THR)
    out = {
        "dev_best": dev_best,
        "n": n,
        "baseline": p0,
        "fallbacks": sum(ev["fallback_counts"].values()),
    }
    for c in phe.CANDIDATES:
        x = int((hits[c][~dev] >= THR).sum())
        out[c] = {
            "x": x,
            "p_raw": binomtest(x, n, p0, alternative="greater").pvalue,
            "dev_x": int((hits[c][dev] >= THR).sum()),
            "dev_n": int(dev.sum()),
        }
    return out


def main() -> None:
    with ProcessPoolExecutor(max_workers=2) as ex:
        registered, corrected = ex.map(_evaluate, (True, False))
    changed = sum(
        not np.array_equal(a, b)
        for a, b in zip(registered["ranks"], corrected["ranks"])
    )
    report = {
        "targets_with_changed_rankings": changed,
        "registered": cell(registered),
        "corrected": cell(corrected),
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
