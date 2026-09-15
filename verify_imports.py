"""Verificare imports module-by-module cu progres real-time si timing.

Apelat din START_8000.bat. Imprima fiecare modul cu durata + statut,
flushed line-by-line pentru ca log-ul sa fie util in timp real.

Aplicatia ruleaza exclusiv pe CPU — suportul GPU/neural (torch / TimesFM /
NeuralForecast / pynvml) a fost ELIMINAT complet. Verificam doar stack-ul CPU,
iar acesta e in intregime OBLIGATORIU: dupa inlocuirea setului de metode
(14.09.2026) nu mai exista pachete optionale de scoring de sarit.

Exit codes:
  0   = toate REQUIRED OK
  20  = cel putin un pachet REQUIRED lipsa (toate din SCHEMA sunt REQUIRED)
  21  = Python < 3.14
"""

from __future__ import annotations

import importlib
import sys
import time

# Consola Windows e cp1252 by default -> diacriticele (ă, ț) arunca UnicodeEncodeError.
# Reconfiguram stdout/stderr pe UTF-8 cu fallback 'replace' ca sa nu mai crape.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def try_import(name: str) -> tuple[bool, float, str]:
    """Importa un modul, returneaza (ok, elapsed, msg)."""
    t0 = time.perf_counter()
    try:
        importlib.import_module(name)
        elapsed = time.perf_counter() - t0
        return True, elapsed, ""
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return False, elapsed, f"{type(e).__name__}: {str(e)[:80]}"


def main() -> int:
    try:
        from ui_shared import check_python_version
    except Exception as e:
        # ui_shared.py importa psutil neconditionat la nivel de modul — exact
        # unul dintre pachetele REQUIRED verificate in bucla de mai jos. Fara
        # aceasta garda, un psutil lipsa omora scriptul cu un traceback brut
        # SI un exit code nedocumentat (1, implicit Python), inainte ca bucla
        # sa apuce sa raporteze curat "[EROARE] Pachete REQUIRED lipsa: psutil"
        # cu exit code 20 — exact diagnosticul pentru care exista scriptul.
        print(
            f"[EROARE] Nu pot importa ui_shared (verificare versiune Python): "
            f"{type(e).__name__}: {str(e)[:120]}"
        )
        print("Solutie: ruleaza ACTUALIZARI.bat apoi reincearca START_8000.bat.")
        return 20

    py_ok, py_msg = check_python_version()
    print(f"Python: {sys.version.split()[0]} — {py_msg}")
    if not py_ok:
        print(f"[EROARE] {py_msg}")
        return 21
    print(f"Exec:   {sys.executable}")
    print()

    # Stack CPU: strict ce e necesar pentru engine + UI. Toate sunt OBLIGATORII
    # — nu mai exista categorie optionala. Setul de metode din 14.09.2026 se
    # sprijina exclusiv pe numpy + scipy, iar vechile pachete ML (scikit-learn,
    # statsmodels, statsforecast, hmmlearn, xgboost, lightgbm, catboost) au fost
    # scoase din requirements_base.txt: niciun modul nu le mai importa.
    SCHEMA = [
        ("nicegui", "0-2"),  # UI principal (app_nicegui.py)
        ("pandas", "0-1"),
        ("numpy", "0-1"),
        ("scipy", "1-3"),  # 24 din cele 50 de metode noi il importa
        ("psutil", "0-1"),
        ("requests", "0-1"),
        ("rich", "0-1"),
    ]
    total = len(SCHEMA)

    print(f"Profil CPU: {total} module (aplicatie exclusiv CPU — fara libs GPU)")
    print()

    missing_required: list[str] = []
    ok_count = 0

    for i, (name, eta) in enumerate(SCHEMA, 1):
        print(f"[{i:2d}/{total}] {name:18s} (REQ, ETA {eta}s) ... ", end="", flush=True)

        ok, elapsed, err = try_import(name)
        if ok:
            print(f"OK   ({elapsed:5.2f}s)", flush=True)
            ok_count += 1
        else:
            missing_required.append(name)
            print(f"LIPSA ({elapsed:5.2f}s) - {err}", flush=True)

    print()
    print(
        f"Rezultat: {ok_count}/{total} OK | missing required: {len(missing_required)}"
    )

    if missing_required:
        print()
        print(f"[EROARE] Pachete REQUIRED lipsa: {' '.join(missing_required)}")
        print("Solutie: ruleaza ACTUALIZARI.bat apoi reincearca START_8000.bat.")
        return 20

    return 0


if __name__ == "__main__":
    sys.exit(main())
