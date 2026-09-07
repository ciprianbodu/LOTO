"""Regresie pentru bug-ul de fereastra pct=100 pe istoric scurt din
`loto_enterprise.benchmark.runner.run_benchmark` (verificare globala 2026-09-07).

Pentru pct=100, n_train era mereu fixat la 80 prin construcție ("n_train = max(80,
n_train)", unde n_train era deja 0 înainte de linia asta) — garda `if n_train < 80`
verifica deci o valoare care nu putea fi niciodată sub 80, indiferent de câte
extrageri avea de fapt istoricul. Pe un istoric cu n <= 80 extrageri, `n_test`
rezultat (`n - 80`) putea fi 0 sau negativ, iar fold-ul NU era marcat `failed` —
`_evaluate_fold` primea n_test<=0, bucla nu executa nimic, iar rezultatul era o
rată fabricată 0.0 tratată ca fereastră completă la poarta de consistență 60%.
"""

from __future__ import annotations

import pandas as pd
import pytest

from loto_enterprise.benchmark import runner


def _tiny_649_csv(path, n_rows: int) -> None:
    """n_rows extrageri 6/49 valide, deterministe (fara duplicate in acelasi rand)."""
    rows = []
    for i in range(n_rows):
        base = (i * 6) % 44
        nums = sorted(1 + ((base + j) % 49) for j in range(6))
        while len(set(nums)) < 6:
            nums = sorted(set(nums) | {1 + ((nums[-1] + 1) % 49)})
        rows.append({f"n{j + 1}": nums[j] for j in range(6)})
    pd.DataFrame(rows).to_csv(path, index=False)


def test_pct100_window_skipped_on_short_history_not_fabricated(tmp_path):
    """n=50 <= 80: fereastra pct=100 trebuie SĂRITĂ, nu sa produca un rand cu
    n_test<=0 in folds.csv."""
    csv_path = tmp_path / "loto_6_49.csv"
    _tiny_649_csv(csv_path, n_rows=50)
    game = runner.GameDef(
        key="loto_6_49",
        label="Loto 6/49",
        csv_path=str(csv_path),
        cols=["n1", "n2", "n3", "n4", "n5", "n6"],
        max_num=49,
        draw_n=6,
        pool_extra=0,
    )
    out_dir = tmp_path / "bench_out"
    runner.run_benchmark(
        games=[game],
        methods=["frequency"],
        percentiles=[100],
        use_cache=False,
        shuffled_control=False,
        out_dir=str(out_dir),
    )
    folds_path = out_dir / "folds.csv"
    # Cu zero fold-uri, run_benchmark nici nu scrie folds.csv — la fel de valid
    # ca un fisier gol, ambele inseamna "niciun fold produs pentru n_train<80/n_test<1".
    if not folds_path.exists():
        return
    folds = pd.read_csv(folds_path)
    assert len(folds) == 0, (
        f"pct=100 pe n=50 ar trebui sarit complet, nu sa produca randuri: {folds.to_dict('records')}"
    )


def test_pct100_window_runs_on_sufficient_history(tmp_path):
    """n=90 > 80: fereastra pct=100 (n_train=80, n_test=10) trebuie sa produca
    un fold real, nefabricat — confirma ca fix-ul nu a stricat cazul normal."""
    csv_path = tmp_path / "loto_6_49.csv"
    _tiny_649_csv(csv_path, n_rows=90)
    game = runner.GameDef(
        key="loto_6_49",
        label="Loto 6/49",
        csv_path=str(csv_path),
        cols=["n1", "n2", "n3", "n4", "n5", "n6"],
        max_num=49,
        draw_n=6,
        pool_extra=0,
    )
    out_dir = tmp_path / "bench_out"
    runner.run_benchmark(
        games=[game],
        methods=["frequency"],
        percentiles=[100],
        use_cache=False,
        shuffled_control=False,
        out_dir=str(out_dir),
    )
    folds = pd.read_csv(out_dir / "folds.csv")
    assert len(folds) == 1
    row = folds.iloc[0]
    assert row["percentile"] == 100
    assert int(row["n_test"]) == 10
    assert bool(row["failed"]) is False
