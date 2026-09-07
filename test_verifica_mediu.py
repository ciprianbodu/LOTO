"""Teste pentru verifica_mediu.py — main() ieșea mereu cu exit 0, chiar și
cu `_ISTORIC/` lipsa; ACTUALIZARI.bat citea asta ca "mediu OK"."""

from __future__ import annotations

import verifica_mediu as vm


def test_check_bench_assets_returns_false_when_istoric_missing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    ok = vm.check_bench_assets()
    assert ok is False


def test_check_bench_assets_returns_true_when_istoric_present(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "_ISTORIC").mkdir()
    ok = vm.check_bench_assets()
    assert ok is True


def test_main_exits_nonzero_when_istoric_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vm, "upgrade_pip", lambda: None)
    monkeypatch.setattr(vm, "check_and_upgrade", lambda pkgs: None)
    monkeypatch.setattr(vm, "check_cpu_methods", lambda: None)

    try:
        vm.main()
        raised = False
        code = 0
    except SystemExit as e:
        raised = True
        code = e.code
    assert raised is True
    assert code != 0


def test_main_does_not_exit_when_istoric_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "_ISTORIC").mkdir()
    monkeypatch.setattr(vm, "upgrade_pip", lambda: None)
    monkeypatch.setattr(vm, "check_and_upgrade", lambda pkgs: None)
    monkeypatch.setattr(vm, "check_cpu_methods", lambda: None)

    try:
        vm.main()
    except SystemExit as e:
        assert e.code == 0 or e.code is None


def test_safe_version_resolves_sklearn_to_scikit_learn_dist_name():
    """sklearn (import) != scikit-learn (nume de distributie pip) — inainte
    ambele incercari cadeau pe 'sklearn' si intorceau mereu '?'."""
    v = vm._safe_version("sklearn")
    assert v != "?"


def test_safe_version_falls_back_to_question_mark_for_unknown_module():
    assert vm._safe_version("nu_exista_asa_ceva_xyz") == "?"
