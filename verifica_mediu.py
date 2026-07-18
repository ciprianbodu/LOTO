"""Verifică + actualizează mediul aplicației LOTO (exclusiv CPU).

Acoperă:
    • Librării standard sigure (nicegui, pandas, numpy, numba, ...)
    • Metode statistice / ML CPU (scikit-learn, statsmodels, statsforecast,
      hmmlearn, xgboost, lightgbm, catboost)
    • Verificare freshness a `best_methods.json` și a istoricului CSV

NOTĂ: tot suportul GPU/neural (torch / CUDA / TimesFM / Chronos / MOMENT /
NeuralForecast) a fost ELIMINAT din aplicație — nu se mai verifică nimic GPU.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as dist_version

# Librarii standard care pot fi actualizate automat in siguranta.
# Am exclus intentionat pandas / numpy / numba (Python C-extensions; wheel-uri
# specifice per versiune de Python — upgrade nesigur poate cere build din sursa).
SAFE_UPGRADE_PACKAGES = [
    "nicegui",
    "psutil",
    "requests",
    "rich",            # bench reporting tables
]

# Metode CPU pe care vrem sa stim DACA sunt instalate (nu le upgradam automat).
CPU_METHOD_PACKAGES = {
    "sklearn": "scikit-learn (ML classifiers)",
    "statsmodels": "statsmodels (ARIMA/ETS/Holt-Winters)",
    "statsforecast": "statsforecast (AutoARIMA/AutoETS/Theta/Croston)",
    "hmmlearn": "hmmlearn (HMM)",
    "xgboost": "XGBoost (gradient boosting CPU)",
    "lightgbm": "LightGBM (gradient boosting CPU)",
    "catboost": "CatBoost (gradient boosting CPU)",
}


def _print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def upgrade_pip():
    print("\n--- Actualizare pip ---")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        print("-> [OK] pip actualizat (sau deja la zi).")
    except subprocess.CalledProcessError as e:
        print(f"-> [ATENTIE] Actualizarea pip a esuat ({e}).")
        print(f"   Manual: {sys.executable} -m pip install --upgrade pip")


def check_and_upgrade(packages):
    print("\n--- Verificare + Actualizare Librarii Standard ---")
    try:
        # --prefer-binary: NU build din sursa (evita FAIL pe pandas/numpy fara wheel)
        # --upgrade-strategy only-if-needed: nu cascadeaza upgrade-uri pe deps
        cmd = [
            sys.executable, "-m", "pip", "install",
            "--upgrade", "--prefer-binary",
            "--upgrade-strategy", "only-if-needed",
        ] + packages
        print(f"Comanda: pip install --upgrade --prefer-binary {' '.join(packages)}")
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        print("-> [OK] Librariile standard sunt actualizate si compatibile.")
    except subprocess.CalledProcessError as e:
        print(f"-> [ATENTIE] Update partial esuat ({e}). Pachetele existente raman OK.")
    except Exception as e:
        print(f"-> [ATENTIE] Problema neasteptata: {e}")


def _safe_version(modname: str) -> str:
    try:
        v = dist_version(modname.replace("_", "-"))
    except PackageNotFoundError:
        try:
            v = dist_version(modname)
        except PackageNotFoundError:
            v = "?"
    return v


def check_cpu_methods():
    _print_section("METODE CPU (statistice / ML)")
    for mod, label in CPU_METHOD_PACKAGES.items():
        try:
            importlib.import_module(mod)
            v = _safe_version(mod)
            print(f"-> [OK] {label}: v{v}")
        except ImportError:
            print(f"-> [LIPSA] {label} — pip install {mod.replace('_', '-')}")
        except Exception as e:
            print(f"-> [EROARE] {label}: {type(e).__name__}: {e}")


def check_bench_assets():
    _print_section("ASSETS BENCHMARK")
    from pathlib import Path
    bm = Path("best_methods.json")
    if bm.exists():
        size_kb = bm.stat().st_size / 1024
        print(f"-> [OK] best_methods.json prezent ({size_kb:.1f} KB)")
        try:
            from loto_enterprise.benchmark.freshness import check_freshness, aggregate_recommendation
            reports = check_freshness("best_methods.json")
            rec = aggregate_recommendation(reports)
            print(f"   Freshness overall: {rec}")
            for gk, r in reports.items():
                tag = {"fresh": "[FRESH]", "slight_drift": "[+]", "moderate_drift": "[!!]",
                       "stale": "[STALE]", "missing": "[?]"}.get(r.status, "[?]")
                print(f"   {tag:>10s} {gk}: cached {r.cached_rows} vs curent {r.current_rows} "
                      f"({r.row_delta_pct:+.1f}%)")
            if rec in ("quick_rebench", "full_rebench"):
                print("   Recomandat: ruleaza Re-Bench Full (din UI sau "
                      "'python bench_all_methods.py')")
        except Exception as e:
            print(f"   [WARN] Freshness check failed: {e}")
    else:
        print("-> [LIPSA] best_methods.json — ruleaza `python bench_all_methods.py` macar o data")

    istoric = Path("ISTORIC")
    if istoric.exists():
        csvs = list(istoric.glob("*.csv"))
        print(f"-> [OK] folderul ISTORIC contine {len(csvs)} CSV-uri: "
              f"{[p.name for p in csvs]}")
    else:
        print("-> [LIPSA] folderul ISTORIC — benchmark-ul nu poate rula fara el")


def _is_in_venv() -> bool:
    return (
        hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
        or "venv" in sys.executable.lower()
    )


def main():
    from ui_shared import check_python_version

    py_ok, py_msg = check_python_version()
    print(f"Python: {sys.version.split()[0]} — {py_msg}")
    if not py_ok:
        print(f"\n[EROARE] {py_msg}")
        sys.exit(21)
    print(f"Exec:   {sys.executable}")
    print(f"Venv:   {'DA' if _is_in_venv() else 'NU (GLOBAL — gresit!)'}")

    if not _is_in_venv():
        print("\n[ATENTIE] Ruleaza ACTUALIZARI.bat, nu direct verifica_mediu.py!")
        print("          Cauta venv-ul: .venv\n")

    check_cpu_methods()
    check_bench_assets()

    upgrade_pip()
    check_and_upgrade(SAFE_UPGRADE_PACKAGES)

    print()
    print("=" * 72)
    print("  VERIFICARE FINALIZATA")
    print("=" * 72)
    print("  Daca toate sectiunile au [OK] si freshness e 'fresh' / 'use_cache',")
    print("  poti porni aplicatia cu START_8000.bat fara nicio actiune.")
    print("  Daca freshness recomanda re-bench:")
    print("    Re-Bench Full din UI (buton portocaliu) sau: python bench_all_methods.py")
    print("=" * 72)


if __name__ == "__main__":
    main()
