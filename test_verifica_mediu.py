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
    monkeypatch.setattr(vm, "check_scoring_stack", lambda: None)

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
    monkeypatch.setattr(vm, "check_scoring_stack", lambda: None)

    try:
        vm.main()
    except SystemExit as e:
        assert e.code == 0 or e.code is None


def test_safe_version_resolves_real_dependency():
    """_safe_version trebuie sa intoarca o versiune reala pentru pachetele pe
    care le raporteaza sectiunea de stack, nu '?'. (Testul verifica exact
    modulele din SCORING_STACK_PACKAGES — daca lista se schimba, se schimba si
    ce se verifica aici, fara sa mai ramana o asertiune pe un pachet eliminat.)"""
    for mod in vm.SCORING_STACK_PACKAGES:
        assert vm._safe_version(mod) != "?", mod


def test_safe_version_falls_back_to_question_mark_for_unknown_module():
    assert vm._safe_version("nu_exista_asa_ceva_xyz") == "?"


def test_package_lists_drop_removed_ml_dependencies():
    """Setul de metode din 14.09.2026 nu mai importa niciun pachet ML greu.
    verifica_mediu nu are voie sa mai ceara/raporteze ceva ce nu se instaleaza:
    altfel utilizatorul vede [LIPSA] pentru pachete eliminate intentionat si
    crede ca mediul e stricat."""
    removed = {
        "sklearn",
        "scikit-learn",
        "statsmodels",
        "statsforecast",
        "hmmlearn",
        "xgboost",
        "lightgbm",
        "catboost",
        "matplotlib",
    }
    assert removed.isdisjoint(vm.SAFE_UPGRADE_PACKAGES)
    assert removed.isdisjoint(vm.SCORING_STACK_PACKAGES)


def test_scoring_stack_is_the_numeric_base_of_the_registry():
    """Cele 52 de metode se sprijina exclusiv pe numpy + scipy; pandas apare
    pentru istoric/UI. Sectiunea de stack trebuie sa le acopere pe toate trei,
    pentru ca un venv fara ele sa fie diagnosticat, nu doar banuit."""
    assert set(vm.SCORING_STACK_PACKAGES) == {"numpy", "scipy", "pandas"}


def test_safe_upgrade_list_excludes_c_extensions():
    """numpy/scipy/pandas raman in AFARA upgrade-ului automat (wheel-uri
    specifice per versiune de Python) — sunt doar raportate ca versiune."""
    assert {"numpy", "scipy", "pandas"}.isdisjoint(vm.SAFE_UPGRADE_PACKAGES)
