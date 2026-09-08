"""methods_math_extra.py era singurul fisier de scorer FARA try/except la
nivel de functie (methods_graph.py, methods_classical.py, methods_coverage.py,
methods_ml.py, methods_search_649.py il au peste tot) — un exceptie neprinsa
intr-un singur bloc/extragere invalida TOT fold-ul walk-forward (runner.py
prinde exceptia la nivel de fold, nu de bloc), scotand metoda din decizie
prin `incomplete_methods`, in loc sa degradeze grațios la scor plat/0 (pe care
`has_usable_score_variance` il respinge oricum, dar fara sa piarda fold-ul
intreg). Verificare globala 2026-09-07."""

from __future__ import annotations

import numpy as np
import pytest

from loto_enterprise.benchmark import methods_math_extra as mx

_ALL_SCORERS = [
    mx.score_pca_resid_surprise,
    mx.score_mi_lag_bag,
    mx.score_nmf_cooc,
    mx.score_cusum_appearance,
    mx.score_circular_kernel,
]


def _valid_draws(n_rows: int = 30, draw_n: int = 6, max_num: int = 49) -> np.ndarray:
    rng = np.random.default_rng(3)
    return np.array(
        [
            sorted(rng.choice(np.arange(1, max_num + 1), size=draw_n, replace=False))
            for _ in range(n_rows)
        ],
        dtype=np.int64,
    )


@pytest.mark.parametrize("scorer", _ALL_SCORERS)
def test_scorer_degrades_gracefully_on_internal_exception(monkeypatch, scorer):
    """Forteaza o exceptie generica DUPA gardele proprii de tip _safe_draws —
    trebuie prinsa de try/except-ul de nivel functie, nu propagata. Toate cele
    5 functii apeleaza `_normalize` o data la final pe calea normala — mock-ul
    arunca DOAR la primul apel (calculul real), lasand a doua chemare, cea de
    recuperare din except-ul de nivel functie (`_normalize({}, max_num)`), sa
    functioneze normal — la fel cum ar functiona in productie."""
    calls = {"n": 0}
    real_normalize = mx._normalize

    def _normalize_raises_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("eroare simulata")
        return real_normalize(*a, **kw)

    monkeypatch.setattr(mx, "_normalize", _normalize_raises_once)
    draws = _valid_draws()
    scores = scorer(draws, 49)
    assert isinstance(scores, dict)
    assert set(scores.keys()) == set(range(1, 50))
    assert all(v == 0.0 for v in scores.values())  # flat/0, nu crash
    assert calls["n"] == 2  # a incercat calculul real, apoi a recuperat


def test_pca_resid_surprise_degrades_gracefully_when_svd_raises_generic_error(
    monkeypatch,
):
    """SVD-ul are deja un except specific LinAlgError — verificam ca un tip
    DIFERIT de exceptie (nu doar LinAlgError) e tot prins de gardul de nivel
    functie adaugat acum in jurul intregului corp."""

    def _boom(*a, **kw):
        raise RuntimeError("eroare numerica simulata, nu LinAlgError")

    monkeypatch.setattr(np.linalg, "svd", _boom)
    scores = mx.score_pca_resid_surprise(_valid_draws(), 49)
    assert set(scores.keys()) == set(range(1, 50))
    assert all(v == 0.0 for v in scores.values())


@pytest.mark.parametrize("scorer", _ALL_SCORERS)
def test_scorer_still_works_normally_on_valid_input(scorer):
    """Calea fericita ramane neschimbata — fix-ul nu a stricat scorurile reale."""
    scores = scorer(_valid_draws(n_rows=60), 49)
    assert set(scores.keys()) == set(range(1, 50))
    assert all(np.isfinite(v) for v in scores.values())


def test_build_binary_skips_uncastable_value_instead_of_raising():
    draws = np.array([[1, 2, 3, 4, 5, 6]], dtype=object)
    draws[0, 5] = float("nan")
    bm = mx._build_binary(draws, 49)
    assert bm.shape == (49, 1)
    assert bm[:, 0].sum() == 5  # 5 numere valide inregistrate, al 6-lea (NaN) sarit
