"""Verifică + actualizează mediul aplicației LOTO.

Acoperă:
    • Librării standard sigure (nicegui, pandas, numpy, numba, ...)
    • PyTorch + CUDA (GPU detection)
    • Foundation models pentru benchmark: timesfm, chronos, momentfm
    • NeuralForecast (NBEATS / NHITS / TiDE / DLinear / PatchTST / Informer /
      Autoformer / FEDformer / DeepAR / TCN)
    • Telemetrie hardware: rich, nvidia-ml-py (oficial; pynvml e deprecat)
    • Verificare freshness a `best_methods.json` și a istoricului CSV
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as dist_version

# Librarii standard care pot fi actualizate automat in siguranta.
# Am exclus intentionat:
#   - torch / torchvision / torchaudio (GPU build cu128 — risc CPU override)
#   - pandas / numpy / numba (Python C-extensions; wheel-uri specifice per versiune
#     de Python — upgrade nesigur poate cere build din sursa)
#   - timesfm / chronos / momentfm / neuralforecast (modele AI cu deps stricte)
# Acestea ramin pe versiunea curenta din venv.
SAFE_UPGRADE_PACKAGES = [
    "nicegui",
    "psutil",
    "requests",
    "rich",            # bench reporting tables
    "nvidia-ml-py",    # GPU telemetry (oficial NVIDIA; expune modul `pynvml`)
]

# Foundation models pentru benchmark — vrem să știm DACĂ sunt instalate, dar
# NU le upgradăm automat (pot avea constraints de versiune pe torch / transformers).
FOUNDATION_MODELS = {
    "timesfm": "Google TimesFM",
    "chronos": "Amazon Chronos (chronos-forecasting)",
    "momentfm": "CMU MOMENT",
}

# NeuralForecast — un singur pachet, multiple arhitecturi
NEURALFORECAST_MODELS = ["NBEATS", "NHITS", "TiDE", "DLinear", "PatchTST",
                         "Informer", "Autoformer", "FEDformer", "DeepAR", "TCN"]

# Modele opționale (NU sunt instalate by default — vezi requirements.txt)
OPTIONAL_FOUNDATION_MODELS = {
    "uni2ts": "Salesforce MOIRAI (necesită omegaconf<2.4)",
    "lag_llama": "Lag-Llama (git install)",
    "tinytimemixer": "IBM TinyTimeMixer (granite-tsfm)",
    "mamba_ssm": "S-Mamba (CUDA kernel)",
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


def check_pytorch():
    _print_section("PYTORCH + CUDA")
    try:
        import torch
        version = getattr(torch, "__version__", "Necunoscuta")
        print(f"-> [OK] PyTorch v{version}")
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0) if torch.cuda.device_count() > 0 else "NVIDIA GPU"
            cuda_ver = getattr(torch.version, "cuda", "?")
            print(f"-> [EXCELENT] CUDA {cuda_ver} ACTIV pe: {gpu_name}")
            print(f"   (Update-ul PyTorch e BLOCAT — protejam build-ul cu CUDA)")
        else:
            print("-> [ATENTIE] PyTorch ruleaza pe CPU. Pentru NVIDIA GPU:")
            print("   pip install torch --index-url https://download.pytorch.org/whl/cu128")
    except ImportError:
        print("-> [EROARE CRITICA] PyTorch lipseste! Instalare cu CUDA 12.8:")
        print("   pip install torch --index-url https://download.pytorch.org/whl/cu128")


def check_foundation_models():
    _print_section("FOUNDATION MODELS (Zero-Shot)")
    for mod, label in FOUNDATION_MODELS.items():
        try:
            importlib.import_module(mod)
            v = _safe_version(mod)
            print(f"-> [OK] {label}: v{v}")
        except ImportError:
            print(f"-> [LIPSA] {label} — pip install {mod}")
        except Exception as e:
            print(f"-> [EROARE] {label}: {type(e).__name__}: {e}")


def check_neuralforecast():
    _print_section("NEURALFORECAST (DL Architectures)")
    try:
        import neuralforecast
        v = _safe_version("neuralforecast")
        print(f"-> [OK] neuralforecast v{v}")
        import neuralforecast.models as M
        available = [m for m in NEURALFORECAST_MODELS if hasattr(M, m)]
        missing = [m for m in NEURALFORECAST_MODELS if not hasattr(M, m)]
        print(f"   Modele disponibile ({len(available)}/{len(NEURALFORECAST_MODELS)}): {', '.join(available)}")
        if missing:
            print(f"   [LIPSA] {', '.join(missing)} — verifica versiunea pachetului")
    except ImportError:
        print("-> [LIPSA] neuralforecast — `pip install neuralforecast`")
    except Exception as e:
        print(f"-> [EROARE] {type(e).__name__}: {e}")


def check_optional_models():
    _print_section("MODELE OPTIONALE (in afara default install)")
    for mod, label in OPTIONAL_FOUNDATION_MODELS.items():
        try:
            importlib.import_module(mod)
            v = _safe_version(mod)
            print(f"-> [OK] {label}: v{v}")
        except Exception:
            print(f"-> [skip] {label} — install manual daca-l vrei in benchmark")


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
    print(f"Python: {sys.version.split()[0]}")
    print(f"Exec:   {sys.executable}")
    print(f"Venv:   {'DA' if _is_in_venv() else 'NU (GLOBAL — gresit!)'}")

    if not _is_in_venv():
        print("\n[ATENTIE] Ruleaza ACTUALIZARI.bat, nu direct verifica_mediu.py!")
        print("          Cauta venv-ul: .venv_ALF-LUPTATORI\n")

    check_pytorch()
    check_foundation_models()
    check_neuralforecast()
    check_optional_models()
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
