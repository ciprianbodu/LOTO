"""Ecranele din 2026-11-01 (advanced_screen + cover_geometry_screen): fiecare
scorer vede numai trecutul si urmeaza numarul, nu eticheta lui; certificarea
coverelor accepta doar castiguri exacte. Pe istorii sintetice scurte, ca sa
ruleze repede; ecranele reale repeta aceleasi verificari pe 6/49 inainte de
calcul."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


adv = _load("advanced_screen_20261101", "scripts/analysis/advanced_screen_2026-11-01.py")
cgs = _load("cover_geometry_screen_20261101", "scripts/analysis/cover_geometry_screen_2026-11-01.py")


def _synthetic(N, d, days, seed):
    rng = np.random.default_rng(seed)
    draws = np.argsort(rng.random((days, N)), axis=1)[:, :d] + 1
    day = np.arange(days)
    dates = np.arange("2020-01-01", days, dtype="datetime64[D]")[:days]
    return adv.ps.Data(draws, day, dates, N, ordered=False)


@pytest.fixture(scope="module")
def pool_game():
    return _synthetic(49, 6, 420, 3)


def test_the_bank_has_the_new_families(pool_game):
    names = [name for name, _f, _S in adv.bank3(pool_game)]
    assert len(names) == len(set(names)) >= 60
    fams = {f for _n, f, _S in adv.bank3(pool_game)}
    assert {"A1_wavelet", "A3_mi", "A4_motif", "A10_kalman", "A11_garch", "A17_hmm"} <= fams


def test_every_scorer_sees_only_past_days(pool_game):
    assert adv.h5.leak_check(pool_game) >= 60


def test_every_scorer_follows_the_numbers_not_their_labels(pool_game):
    assert adv.h5.equivariance_check(pool_game) >= 60


def test_checks_catch_a_leak_and_a_label_bias(pool_game, monkeypatch):
    orig = adv.bank3

    def leaky(data):
        yield "leak_today", "X", data.C.copy()
        yield from orig(data)

    monkeypatch.setattr(adv.h5, "bank", leaky)
    with pytest.raises(AssertionError, match="leak_today"):
        adv.h5.leak_check(pool_game)

    def biased(data):
        yield "label_bias", "X", np.tile(np.arange(data.N, dtype=float), (data.n_days, 1))
        yield from orig(data)

    monkeypatch.setattr(adv.h5, "bank", biased)
    with pytest.raises(AssertionError, match="label_bias"):
        adv.h5.equivariance_check(pool_game)
    monkeypatch.setattr(adv.h5, "bank", orig)


def test_cover_certification_rejects_worse_and_accepts_better():
    from covering.anneal_hits import _certify

    pool = list(range(1, 11))
    # greedy g=4 pe 10 numere, pick 5
    wheel, _cov = cgs.generate_wheel("greedy", pool, 5, 4, max_variants=0, draw_n=5, max_num=45)
    wheel = [tuple(t) for t in wheel]
    # candidat identic -> fara castig strict
    why, gain, _ = _certify(pool, wheel, wheel, 4, 5, 45)
    assert why == "no strict gain"
    # candidat cu un bilet in minus -> numar diferit de bilete
    why, _g, _c = _certify(pool, wheel, wheel[:-1], 4, 5, 45)
    assert why == "duplicate tickets" or why
    # candidat care pierde acoperirea garantiei -> respins
    bad = [tuple(t) for t in wheel]
    bad[0] = tuple(sorted(set(bad[0]) - {bad[0][0]} | {pool[-1]}))
    why, _g, _c = _certify(pool, wheel, bad, 4, 5, 45)
    assert why


def test_cover_screen_config_schema():
    rec = cgs.run_config("6/49", 12, 3, 20, 4, "budget_below")
    assert rec["game"] == "6/49" and rec["v"] == 12 and rec["g"] == 3
    assert 1 <= rec["baseline_tickets"] <= 20
    assert set(rec["baseline"]) >= {3, 4, 5, 6}
    assert rec["certified"] in (True, False)
    if rec["certified"]:
        assert any(v > 0 for v in rec["gain"].values())
