"""Teste pentru verify_imports.py — verificarea de mediu pe care START_8000.bat
o ruleaza la FIECARE pornire si al carei exit code decide daca aplicatia
lanseaza sau nu (verificare globala 2026-09-07)."""
from __future__ import annotations

import sys

import verify_imports as vi


def test_main_reports_clean_error_when_ui_shared_import_fails(monkeypatch, capsys):
    """ui_shared.py importa psutil neconditionat la nivel de modul — psutil e
    chiar unul dintre pachetele REQUIRED verificate mai jos. Daca `from ui_shared
    import check_python_version` explodeaza (ex. psutil lipsa), scriptul nu are
    voie sa moara cu un traceback brut si exit code nedocumentat — trebuie sa
    raporteze curat, cu exit code 20 (acelasi cod ca "pachet REQUIRED lipsa")."""
    monkeypatch.setitem(sys.modules, "ui_shared", None)  # forteaza ImportError
    rc = vi.main()
    assert rc == 20
    out = capsys.readouterr().out
    assert "[EROARE]" in out
    assert "ACTUALIZARI.bat" in out


def test_main_succeeds_normally_when_ui_shared_importable(capsys):
    """Calea fericita ramane neschimbata — nu doar calea de eroare."""
    rc = vi.main()
    out = capsys.readouterr().out
    assert "Python:" in out
    assert "Rezultat:" in out
    # In acest venv de test toate REQUIRED sunt prezente (altfel testele
    # nici n-ar rula) — rc trebuie sa fie 0.
    assert rc == 0


def test_try_import_reports_missing_module_without_raising():
    ok, elapsed, msg = vi.try_import("this_module_does_not_exist_xyz")
    assert ok is False
    assert elapsed >= 0
    assert "ModuleNotFoundError" in msg
