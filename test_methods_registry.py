"""Registry-ul de metode (`methods._load_extra_methods`): dimensiune, coliziuni, module picate."""

from __future__ import annotations

import builtins
import logging
from pathlib import Path

import pandas as pd
import pytest

from loto_enterprise.benchmark import methods, methods_recency, methods_relational
from loto_enterprise.benchmark.decision import (
    EXCLUDED_FROM_PRODUCTION,
    KNOWN_GAME_MAX_NUM,
    decide_optimal_config_for_pool,
)


def _dummy_fn(draws_2d, max_num):
    return {n: 0.0 for n in range(1, max_num + 1)}


def test_registry_has_two_baselines_plus_fifty_methods():
    """2 baseline-uri structurale (random, frequency) + 30 + 20 metode (14.09.2026)."""
    assert len(methods.METHODS) == 52
    assert "random" in methods.METHODS and "frequency" in methods.METHODS
    assert len(methods.list_methods()) == 52


def test_renamed_method_still_resolves_from_saved_decisions():
    """`alternating_parity` → `season_period2`: o decizie salvată sub numele vechi
    nu are voie să cadă pe `frequency` după redenumire."""
    from loto_enterprise.core.method_selector import _sanitize_production_name

    assert "alternating_parity" not in methods.METHODS
    assert "season_period2" in methods.METHODS
    assert methods.resolve_method_name("alternating_parity") == "season_period2"
    assert (
        _sanitize_production_name("alternating_parity", context="test")
        == "season_period2"
    )


def test_old_method_names_are_gone():
    """Cele 109 metode vechi nu mai există în registry (și nici lista de tombstone)."""
    for old in ("autocorr", "ml_knn_5", "649_decade_hot", "parity_balance", "dmd", "omnius"):
        assert old not in methods.METHODS


def test_spatial_filters_stay_in_registry_but_excluded_from_production():
    """Filtrele de apartenență rămân măsurabile în bench, nu scorer de producție."""
    for name in (
        "neighbor_adjacent",
        "repeat_last_draw",
        "rwr_last_draw",
        "haar_multiscale",
    ):
        assert name in methods.METHODS
        assert name in EXCLUDED_FROM_PRODUCTION
    assert "random" in EXCLUDED_FROM_PRODUCTION
    assert "frequency" not in EXCLUDED_FROM_PRODUCTION


def test_folds_decision_never_picks_excluded_last_draw_filters():
    """Pe folds.csv curent, decizia nu poate numi un filtru last-draw."""
    folds = Path("bench_results/folds.csv")
    if not folds.exists():
        pytest.skip("folds.csv absent")
    df = pd.read_csv(folds)
    specs = (
        ("loto_6_49", 12, 6),
        ("loto_5_40", 11, 5),
        ("joker_urna1", 10, 5),
        ("joker_urna2", 1, 1),
    )
    for game, k, draw_n in specs:
        cfg = decide_optimal_config_for_pool(
            df, game, k, draw_n, max_num=KNOWN_GAME_MAX_NUM[game]
        )
        assert cfg.get("scorer") not in EXCLUDED_FROM_PRODUCTION
        assert cfg.get("scorer") != "rwr_last_draw"


def test_extension_name_collision_is_logged_not_silent(monkeypatch, caplog):
    fake_methods = {"random": methods.METHODS["random"]}
    monkeypatch.setattr(methods, "METHODS", fake_methods)

    first_tup = (_dummy_fn, "test-family-1", False, "primul")
    second_tup = (_dummy_fn, "test-family-2", False, "al doilea (nu trebuie sa castige)")
    monkeypatch.setitem(methods_recency.RECENCY_METHODS, "test_dup_name", first_tup)
    monkeypatch.setitem(methods_relational.RELATIONAL_METHODS, "test_dup_name", second_tup)

    with caplog.at_level(logging.WARNING):
        methods._load_extra_methods()

    assert fake_methods["test_dup_name"] == first_tup
    assert any(
        "test_dup_name" in rec.message and "duplicat" in rec.message
        for rec in caplog.records
    )


def test_module_load_failure_is_recorded_and_logged_as_error(monkeypatch, caplog):
    """Un modul de metode care nu se incarca: numele + eroarea in METHOD_LOAD_ERRORS, log ERROR.

    Regresia reala: fara scipy, `methods_wave2` (si `methods_learning`) dispar,
    registry-ul scade de la 52 la 28, iar productia cade pe `frequency` fiindca
    `_sanitize_production_name` nu mai gaseste castigatorul. Cauza trebuie sa fie
    VIZIBILA, nu un simplu warning inghitit.
    """
    fake_methods = {"random": methods.METHODS["random"]}
    monkeypatch.setattr(methods, "METHODS", fake_methods)
    # Dict propriu, ca esecul simulat sa nu se scurga in alte teste.
    monkeypatch.setattr(methods, "METHOD_LOAD_ERRORS", {})

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name.endswith(".methods_wave2"):
            raise ModuleNotFoundError("No module named 'scipy'")
        return real_import(name, *args, **kwargs)

    # `_load_extra_methods` foloseste `__import__` din builtins; numele se
    # rezolva intai in globals-ul modulului, deci patch-ul de aici il umbreste.
    monkeypatch.setattr(methods, "__import__", _fake_import, raising=False)

    with caplog.at_level(logging.ERROR):
        methods._load_extra_methods()

    assert "methods_wave2" in methods.METHOD_LOAD_ERRORS
    assert "scipy" in methods.METHOD_LOAD_ERRORS["methods_wave2"]
    # Modulul picat chiar lipseste din registry, nu doar din log.
    assert "alternating_parity" not in fake_methods
    # ...iar celelalte module s-au incarcat normal (esecul e izolat).
    assert "neighbor_adjacent" in fake_methods
    assert any(
        rec.levelno >= logging.ERROR and "methods_wave2" in rec.getMessage()
        for rec in caplog.records
    ), "modulul picat trebuie logat la nivel ERROR, nu WARNING"


def test_method_load_errors_are_reset_on_reload(monkeypatch):
    """Reapelarea nu acumuleaza: erorile vechi dispar daca incarcarea reuseste."""
    monkeypatch.setattr(methods, "METHODS", dict(methods.METHODS))
    monkeypatch.setattr(
        methods, "METHOD_LOAD_ERRORS", {"modul_inexistent": "eroare veche"}
    )

    methods._load_extra_methods()

    assert "modul_inexistent" not in methods.METHOD_LOAD_ERRORS


def test_unknown_name_message_names_the_failed_module(monkeypatch, caplog):
    """Fallback-ul de productie numeste modulul picat, nu doar 'metoda necunoscuta'."""
    from loto_enterprise.core import method_selector

    monkeypatch.setattr(methods, "METHODS", {"frequency": methods.METHODS["frequency"]})
    monkeypatch.setattr(
        methods,
        "METHOD_LOAD_ERRORS",
        {"methods_wave2": "ModuleNotFoundError: No module named 'scipy'"},
    )

    with caplog.at_level(logging.WARNING):
        assert (
            method_selector._sanitize_production_name(
                "alternating_parity", context="winner"
            )
            is None
        )

    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "methods_wave2" in text and "scipy" in text


def test_module_docstrings_state_the_real_method_counts():
    """Cifrele din docstring-uri se verifica programatic, nu din ochi."""
    import re

    from loto_enterprise.benchmark import methods_learning, methods_wave2

    sizes = {
        "methods_recency": len(methods_recency.RECENCY_METHODS),
        "methods_relational": len(methods_relational.RELATIONAL_METHODS),
        "methods_learning": len(methods_learning.LEARNING_METHODS),
        "methods_wave2": len(methods_wave2.WAVE2_METHODS),
    }
    assert sum(sizes.values()) + 2 == len(methods.METHODS)

    for modname, size in sizes.items():
        found = re.search(rf"{modname}\.py\s+(\d+) metode", methods.__doc__)
        assert found, f"{modname} nu apare in docstring-ul registry-ului"
        assert int(found.group(1)) == size, modname

    assert f"({sizes['methods_relational']} metode)" in methods_relational.__doc__
    assert f"({sizes['methods_wave2']})" in methods_wave2.__doc__.splitlines()[0]
