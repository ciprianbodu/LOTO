"""Verificare imports module-by-module cu progres real-time si timing.

Apelat din START_8000.bat. Imprima fiecare modul cu durata + statut,
flushed line-by-line pentru ca log-ul sa fie util in timp real.

Variabile env setate de bat inainte de a apela acest script:
  CUDA_VISIBLE_DEVICES=-1   (pe CPU mode)
  HF_HUB_OFFLINE=1          (forteaza timesfm sa nu loveasca HF Hub la import)
  TRANSFORMERS_OFFLINE=1    (similar pentru transformers)

Exit codes:
  0   = toate REQUIRED OK
  20  = cel putin un REQUIRED LIPSA
"""
from __future__ import annotations

import importlib
import os
import sys
import time


def detect_gpu_mode() -> tuple[str, str]:
    """Citeste .machine_profile (scris de START_8000.bat :DetectGpu)."""
    gpu_type = "CPU_ONLY"
    gpu_name = ""
    try:
        with open(".machine_profile", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k == "GPU_TYPE":
                    gpu_type = v.strip()
                elif k == "GPU_NAME":
                    gpu_name = v.strip()
    except FileNotFoundError:
        pass
    return gpu_type, gpu_name


def setup_offline_env() -> None:
    """Asiguram ca import-urile de modele AI NU lovesc network la load."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # Pe CPU: spune torch sa nu probleze CUDA (rapid import).
    if os.environ.get("CUDA_VISIBLE_DEVICES") is None and detect_gpu_mode()[0] != "NVIDIA":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"


def try_import(name: str, required: bool, eta: str) -> tuple[bool, float, str]:
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
    setup_offline_env()
    gpu_type, gpu_name = detect_gpu_mode()
    is_gpu = (gpu_type == "NVIDIA")

    print(f"Mod hardware: {gpu_type}" + (f" ({gpu_name})" if gpu_name else ""))
    print(f"HF_HUB_OFFLINE={os.environ.get('HF_HUB_OFFLINE', 'unset')}  "
          f"CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES', 'unset')}")
    print()

    # Doua profile distincte: pe CPU NU mai incarcam nimic legat de GPU.
    # Asta evita 30-60s pierdute la cold-start torch/timesfm pe masini fara CUDA,
    # plus elimina riscul de hang pe import timesfm (HF Hub probe etc.).
    CPU_PROFILE = [
        # UI + ce e necesar pentru engine cand merge cu fallback determinist:
        ("nicegui",  True, "0-2"),
        ("pandas",   True, "0-1"),
        ("numpy",    True, "0-1"),
        ("scipy",    True, "1-3"),
        ("numba",    True, "1-5"),   # JIT folosit de loto_engine
        ("psutil",   True, "0-1"),
        ("requests", True, "0-1"),
        ("rich",     True, "0-1"),
    ]

    GPU_PROFILE = CPU_PROFILE + [
        # GPU only: stack-ul greu (torch + neural forecasters)
        ("torch",          True, "2-30"),
        ("timesfm",        True, "5-60"),
        ("transformers",   True, "2-15"),
        ("chronos",        True, "2-15"),
        ("momentfm",       True, "2-15"),
        ("neuralforecast", True, "2-15"),
        ("pynvml",         True, "0-1"),  # GPU telemetry
    ]

    schema = GPU_PROFILE if is_gpu else CPU_PROFILE
    total = len(schema)

    if is_gpu:
        print(f"Profil GPU (full stack): {total} module")
    else:
        print(f"Profil CPU (lean — fara libs GPU): {total} module")
        print("Note: torch/timesfm/chronos/momentfm/neuralforecast/transformers SAREM")
        print("      ca sa nu pierdem 30-60s la cold-start. Engine folosese fallback")
        print("      determinist pe CPU (vezi _fallback_scores_no_tfm in timesfm_engine.py).")
    print()

    missing_required: list[str] = []
    optional_missing: list[str] = []
    ok_count = 0

    for i, (name, required, eta) in enumerate(schema, 1):
        tag = "REQ" if required else "opt"
        print(f"[{i:2d}/{total}] {name:18s} ({tag}, ETA {eta}s) ... ", end="", flush=True)

        ok, elapsed, err = try_import(name, required, eta)
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
