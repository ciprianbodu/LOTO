"""Curare REVERSIBILĂ a setului de metode rulate de benchmark.

Sursa: curated_methods.json (rădăcina proiectului).

Diferența față de `disabled.py` (blacklist):
    • `disabled_methods.json` e MERGE-ONLY și PERMANENT — o metodă intrată acolo
      nu se mai rulează niciodată și nu se scoate.
    • `curated_methods.json` e o SELECȚIE ACTIVĂ, complet reversibilă: ștergi
      fișierul (sau golești lista `active`) și bench-ul revine instantaneu la
      toate metodele available minus blacklist. Nimic nu se pierde.

Criteriul de selecție NU e clasamentul (vezi CLAUDE.md — tăierea pe performanță
e zgomot: overlap top-15 între jumătățile de date = 13-20%, iar baseline-ul
`random` câștigă 4 din 45 de celule joc×pool). Criteriul e ACOPERIREA DE SEMNAL
DISTINCT: păstrăm metode ale căror scoruri sunt necorelate între ele
(|Spearman| < 0.95, sub pragul `method_selector.MAX_MEMBER_CORR`) și eliminăm
clonele. Efect: mai puține metode, bench mai rapid, iar ensemble-ul chiar rămâne
cu membri distincți (nu mai colapsează la runtime prin decorelare).

Cele două filtre se COMPUN: curated ∩ (available minus disabled).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parents[2] / "curated_methods.json"

# Metode fără care mecanica deciziei se rupe — le verificăm, nu le impunem tăcut.
#   • `random`   = baseline STRUCTURAL: decision._windows_method_beats_random()
#     întoarce (0, 0) dacă rândul `random` lipsește din folds.csv → n_total == 0 →
#     `continue` pe TOATE metodele → `qualifying` gol → low_confidence pe toate
#     jocurile. Fără el gate-ul de consistență nu funcționează deloc.
#     (Rămâne interzis ca scorer de producție: decision.EXCLUDED_FROM_PRODUCTION.)
#   • `frequency` = decision.SAFE_FALLBACK_SCORER, folosit pe ramura fără metode
#     calificate. Baseline DETERMINIST, deci permis în producție (regula de aur 7).
REQUIRED_METHODS = ("random", "frequency")


def curated_path() -> Path:
    """Calea fișierului de curare (util pt UI: mesaj de dezactivare)."""
    return _PATH


def load_curated() -> list[str]:
    """Lista de metode curate, în ordinea din fișier (dedup, ordine păstrată).

    Întoarce [] dacă fișierul lipsește, e invalid, sau `active` e gol/absent —
    adică „fără curare", comportamentul implicit (toate metodele).
    """
    try:
        if not _PATH.exists():
            return []
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[curated] citire %s eșuată: %s — rulez TOATE metodele.", _PATH, exc)
        return []
    if not isinstance(data, dict):
        logger.warning("[curated] %s nu conține un obiect JSON — rulez TOATE metodele.", _PATH)
        return []
    raw = data.get("active") or []
    if not isinstance(raw, (list, tuple)):
        logger.warning("[curated] cheia 'active' nu e listă — rulez TOATE metodele.")
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def is_curation_active() -> bool:
    """True dacă există o listă `active` nevidă (deci bench-ul rulează un subset)."""
    return bool(load_curated())


def curated_meta() -> dict:
    """Blocul `_meta` din fișier (criteriu, dată, notă) — pt afișare în UI."""
    try:
        if _PATH.exists():
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("_meta"), dict):
                return dict(data["_meta"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[curated] _meta necitibil: %s", exc)
    return {}


def apply_curation(candidates: Iterable[str]) -> tuple[list[str], dict]:
    """Filtrează `candidates` (available minus blacklist) prin lista curată.

    Întoarce (metode, info). `info` e telemetrie pt log/UI:
        active            – curarea e pornită sau nu
        n_before/n_after  – câte metode erau candidate / câte rămân
        missing           – nume din curated care NU sunt candidate valide
                            (inexistente în registry, unavailable sau blacklistate)
        missing_required  – `random`/`frequency` absente → decizia se rupe

    Ordinea rezultatului e cea din curated_methods.json (nu cea din registry),
    ca lista rulată să fie exact ce a cerut utilizatorul, citibilă în log.
    Dacă curarea e inactivă, `candidates` se întoarce neatins.
    """
    cand = list(candidates)
    curated = load_curated()
    info = {
        "active": bool(curated),
        "n_before": len(cand),
        "n_after": len(cand),
        "n_curated": len(curated),
        "missing": [],
        "missing_required": [],
    }
    if not curated:
        return cand, info

    cand_set = set(cand)
    kept = [m for m in curated if m in cand_set]
    info["missing"] = [m for m in curated if m not in cand_set]
    info["n_after"] = len(kept)
    info["missing_required"] = [m for m in REQUIRED_METHODS if m not in kept]

    if not kept:
        # Curare care nu lasă nimic = greșeală de configurare, nu intenție.
        # Mai bine rulăm tot decât să rămână bench-ul fără nicio metodă.
        logger.error(
            "[curated] lista 'active' din %s nu conține NICIO metodă validă "
            "(%s) — ignor curarea și rulez toate cele %d metode.",
            _PATH, info["missing"], len(cand),
        )
        info["active"] = False
        info["n_after"] = len(cand)
        return cand, info

    return kept, info


def log_curation(info: dict) -> None:
    """Emite în log starea curării. De apelat DUPĂ logging.basicConfig()."""
    if not info.get("active"):
        logger.info(
            "[curated] fără curare (curated_methods.json absent/gol) — rulez toate "
            "cele %d metode active.", info.get("n_after", 0),
        )
        return
    logger.info(
        "[curated] curated: %d din %d metode (criteriu: acoperire de semnal distinct, "
        "NU clasament). Anulare: șterge sau golește %s + re-bench.",
        info.get("n_after", 0), info.get("n_before", 0), _PATH,
    )
    if info.get("missing"):
        logger.warning(
            "[curated] %d nume din 'active' nu-s candidate valide (inexistente, "
            "unavailable sau blacklistate) și au fost sărite: %s",
            len(info["missing"]), info["missing"],
        )
    if info.get("missing_required"):
        logger.warning(
            "[curated] LIPSESC metode structurale %s — fără 'random' gate-ul de "
            "consistență din decision.py nu funcționează (low_confidence pe toate "
            "jocurile), fără 'frequency' dispare SAFE_FALLBACK_SCORER.",
            info["missing_required"],
        )
