"""Teste pentru fuziunea registry-ului de metode (`methods._load_extra_methods`) —
verificare globala 2026-09-07: o coliziune de NUME intre doua module de extensie
(non-tombstone) trecea complet neobservata, fara niciun log — a doua implementare
disparea tacut din bench, fara nicio urma."""

from __future__ import annotations

import logging

from loto_enterprise.benchmark import methods, methods_classical, methods_ml


def _dummy_fn(draws_2d, max_num):
    return {n: 0.0 for n in range(1, max_num + 1)}


def test_no_collisions_in_the_real_registry():
    """Gardă directă: azi 0 coliziuni intre cele 7 module + baza (111 metode) —
    daca vreun refactor introduce accidental un nume duplicat, testul asta pica."""
    assert len(methods.METHODS) == 111


def test_extension_name_collision_is_logged_not_silent(monkeypatch, caplog):
    fake_methods = {"random": methods.METHODS["random"]}
    monkeypatch.setattr(methods, "METHODS", fake_methods)

    first_tup = (_dummy_fn, "test-family-1", False, "primul")
    second_tup = (
        _dummy_fn,
        "test-family-2",
        False,
        "al doilea (nu trebuie sa castige)",
    )
    monkeypatch.setitem(methods_classical.CLASSICAL_METHODS, "test_dup_name", first_tup)
    monkeypatch.setitem(methods_ml.ML_METHODS, "test_dup_name", second_tup)

    with caplog.at_level(logging.WARNING):
        methods._load_extra_methods()

    # Primul modul incarcat (methods_classical) castiga; al doilea NU
    # suprascrie tacut.
    assert fake_methods["test_dup_name"] == first_tup
    assert any(
        "test_dup_name" in rec.message and "duplicat" in rec.message
        for rec in caplog.records
    )
