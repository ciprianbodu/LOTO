"""Căi runtime per-mașină, în afara checkout-ului sincronizat de OneDrive."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PREFERRED_RUNTIME_ROOT = Path(r"D:\_BUILD\_LOTO")


def _resolve_runtime_root() -> Path:
    """Director runtime comun; override: ``LOTO_RUNTIME_DIR``.

    Pe stația Windows folosim D:\\_BUILD\\_LOTO. În CI sau pe un sistem fără
    acel volum păstrăm fallback-ul compatibil în rădăcina proiectului.
    """
    configured = os.environ.get("LOTO_RUNTIME_DIR", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    if os.name == "nt":
        candidates.append(PREFERRED_RUNTIME_ROOT)
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        except OSError:
            continue
    return PROJECT_ROOT


RUNTIME_ROOT = _resolve_runtime_root()
ENGINE_LOG_FILE = RUNTIME_ROOT / "loto.log"
BENCH_LOG_FILE = RUNTIME_ROOT / "bench_full.log"
STARTUP_LOG_FILE = RUNTIME_ROOT / "startup_8000.log"


def _resolve_wf_cache_dir() -> Path:
    configured = os.environ.get("LOTO_WF_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return RUNTIME_ROOT / ".wf_cache"


WF_CACHE_DIR = _resolve_wf_cache_dir()
