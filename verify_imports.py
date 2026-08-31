"""Verificare imports module-by-module cu progres real-time si timing.

Apelat din START_8000.bat. Imprima fiecare modul cu durata + statut,
flushed line-by-line pentru ca log-ul sa fie util in timp real.

Aplicatia ruleaza exclusiv pe CPU — suportul GPU/neural (torch / TimesFM /
NeuralForecast / pynvml) a fost ELIMINAT complet. Verificam doar stack-ul CPU.

Exit codes:
  0   = toate REQUIRED OK
  20  = cel putin un REQUIRED LIPSA
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
    from ui_shared import check_python_version

    py_ok, py_msg = check_python_version()
    print(f"Python: {sys.version.split()[0]} — {py_msg}")
    if not py_ok:
        print(f"[EROARE] {py_msg}")
        return 21
    print(f"Exec:   {sys.executable}")
    print()

    # Stack CPU: strict ce e necesar pentru engine + UI.
    SCHEMA = [
        ("nicegui",  True, "0-2"),   # UI principal (app_nicegui.py)
        ("pandas",   True, "0-1"),
        ("numpy",    True, "0-1"),
        ("scipy",    True, "1-3"),
        ("psutil",   True, "0-1"),
        ("requests", True, "0-1"),
        ("rich",     True, "0-1"),
        # Metode CPU — optionale: daca lipsesc, bench-ul sare metodele respective,
        # dar aplicatia PORNESTE (engine are fallback determinist).
        ("sklearn",       False, "0-2"),
        ("statsmodels",   False, "0-3"),
        ("statsforecast", False, "0-3"),
    ]
    total = len(SCHEMA)

    print(f"Profil CPU: {total} module (aplicatie exclusiv CPU — fara libs GPU)")
    print()

    missing_required: list[str] = []
    optional_missing: list[str] = []
    ok_count = 0

    for i, (name, required, eta) in enumerate(SCHEMA, 1):
        tag = "REQ" if required else "opt"
        print(f"[{i:2d}/{total}] {name:18s} ({tag}, ETA {eta}s) ... ", end="", flush=True)

        ok, elapsed, err = try_import(name)
        if ok:
            print(f"OK   ({elapsed:5.2f}s)", flush=True)
            ok_count += 1
        else:
            if required:
                missing_required.append(name)
                print(f"LIPSA ({elapsed:5.2f}s) - {err}", flush=True)
            else:
                optional_missing.append(name)
                print(f"skip ({elapsed:5.2f}s) - {err[:60]}", flush=True)

    print()
    print(f"Rezultat: {ok_count}/{total} OK"
          f" | missing required: {len(missing_required)}"
          f" | optional missing: {len(optional_missing)}")

    if missing_required:
        print()
        print(f"[EROARE] Pachete REQUIRED lipsa: {' '.join(missing_required)}")
        print("Solutie: ruleaza ACTUALIZARI.bat apoi reincearca START_8000.bat.")
        return 20

    if optional_missing:
        print(f"[INFO] Pachete optionale lipsa: {' '.join(optional_missing)}")
        print("       (app va merge cu fallback determinist)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
