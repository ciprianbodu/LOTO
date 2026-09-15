"""Registry-ul de metode (`methods._load_extra_methods`): dimensiune, coliziuni, tombstone."""

from __future__ import annotations

import logging

from loto_enterprise.benchmark import methods, methods_recency, methods_relational


def _dummy_fn(draws_2d, max_num):
    return {n: 0.0 for n in range(1, max_num + 1)}


def test_registry_has_two_baselines_plus_fifty_methods():
    """2 baseline-uri structurale (random, frequency) + 30 + 20 metode (14.09.2026)."""
    assert len(methods.METHODS) == 52
    assert "random" in methods.METHODS and "frequency" in methods.METHODS
    assert len(methods.list_methods()) == 52
    assert methods.METHOD_ALIASES == {}


def test_old_method_names_are_gone():
    """Cele 109 metode vechi nu mai există în registry (și nici lista de tombstone)."""
    for old in ("autocorr", "ml_knn_5", "649_decade_hot", "parity_balance", "dmd", "omnius"):
        assert old not in methods.METHODS


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
