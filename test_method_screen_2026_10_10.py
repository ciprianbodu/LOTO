"""Ecranul de metode din 2026-10-10: fiecare scorer vede numai trecutul si
urmeaza numarul, nu eticheta lui. Pe o istorie sintetica scurta, ca sa ruleze
repede; ecranul real repeta aceleasi verificari pe 6/49 inainte de calcul."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "method_screen_20261010", ROOT / "scripts" / "analysis" / "method_screen_2026-10-10.py"
)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def _synthetic(N, d, days, seed):
    rng = np.random.default_rng(seed)
    draws = np.argsort(rng.random((days, N)), axis=1)[:, :d] + 1
    day = np.arange(days)
    dates = np.arange("2020-01-01", days, dtype="datetime64[D]")[:days]
    return ms.ps.Data(draws, day, dates, N, ordered=False)


@pytest.fixture(scope="module")
def pool_game():
    return _synthetic(49, 6, 420, 3)


def test_the_bank_has_about_a_thousand_scorers(pool_game):
    names = [name for name, _f, _S in ms.bank(pool_game)]
    assert len(names) == len(set(names)) >= 1000
    single = _synthetic(20, 1, 420, 4)
    assert len([n for n, _f, _S in ms.bank(single)]) >= 800


def test_every_scorer_sees_only_past_days(pool_game):
    assert ms.leak_check(pool_game) >= 1000


def test_every_scorer_follows_the_numbers_not_their_labels(pool_game):
    assert ms.equivariance_check(pool_game) >= 1000


def test_checks_catch_a_leak_and_a_label_bias(pool_game, monkeypatch):
    orig = ms.bank

    def leaky(data):
        yield "leak_today", "X", data.C.copy()
        yield from orig(data)

    monkeypatch.setattr(ms, "bank", leaky)
    with pytest.raises(AssertionError, match="leak_today"):
        ms.leak_check(pool_game)

    def biased(data):
        yield "label_bias", "X", np.tile(np.arange(data.N, dtype=float), (data.n_days, 1))
        yield from orig(data)

    monkeypatch.setattr(ms, "bank", biased)
    with pytest.raises(AssertionError, match="label_bias"):
        ms.equivariance_check(pool_game)


def test_mcnemar_counts_only_the_draws_where_the_two_differ():
    new = np.array([1, 1, 0, 0, 1, 0], dtype=bool)
    lead = np.array([1, 0, 1, 0, 0, 0], dtype=bool)
    b, c, p = ms.mcnemar_greater(new, lead)
    assert (b, c) == (2, 1)
    assert p == pytest.approx(0.5)  # P(X >= 2), X ~ Bin(3, 1/2)
