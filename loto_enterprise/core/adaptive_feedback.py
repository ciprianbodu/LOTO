"""
Adaptive Feedback Engine — telemetrie persistentă post-extragere.

Sistemul detectează catastrofe (0 hituri din pool), sub-performanță (1 hit) și
regime mismatch (sub-performanță susținută în fereastră rolling) — evenimente
și mod (`active_mode`) afișate în audit/UI ca istoric, fără să ajusteze scorul
vreunui număr. (Până la eliminarea lui — verificare globală 2026-09-07 — modulul
calcula și un `error_correction_map`, un multiplicator per număr menit să
amplifice/penalizeze scorul la predicția următoare; nu era însă niciodată citit
de pipeline-ul de scoring, deci n-a influențat vreodată vreun pool generat.)

State persistat în `adaptive_state.json` (la rădăcina proiectului), keyed pe
`{game_type}_{pool_size}` — consistent cu `pool_history.json`.

Evenimente clasificate:
    * "normal"        — pool a obținut >=2 hituri (peste baseline-ul aleator)
    * "underperf"     — exact 1 hit (sub baseline)
    * "catastrophe"   — 0 hituri (pierdere totală — semnal de regim greșit)
    * "regime_reset"  — fereastră rolling sub baseline aleator pentru >=N
                        extrageri => regimul curent al modelului e nepotrivit
                        (raportat ca `active_mode = "reset"`, telemetrie).
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Locația fișierului de stare. Rădăcina proiectului = trei niveluri mai sus față
# de acest modul (loto_enterprise/core/adaptive_feedback.py -> .. -> .. -> root)
_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "adaptive_state.json"

# Lungimea ferestrei rolling pentru detecția regime mismatch
_ROLLING_WINDOW = 5

# Pragul de underperformance pentru trigger reset: rolling avg trebuie să fie
# sub `baseline * (1 - threshold)` pentru a declanșa.
# 0.25 înseamnă că rolling avg < 75% din baseline (pentru Joker: < 1.0 hits).
_REGIME_UNDERPERF_THRESHOLD = 0.25

# Numărul minim de catastrofe consecutive pentru auto-trigger reset.
# Anterior era 2. Mărit la 3 pentru a evita reseturi pe varianță naturală
# (P(2 catastrofe consecutive Joker) ≈ 3.8%, P(3) ≈ 0.7%).
_REGIME_STREAK_THRESHOLD = 3

# Numărul maxim de extrageri în care rămânem în mod reset.
# După acest prag revenim automat la normal — evităm să rămânem blocați în
# reset când acesta nu produce îmbunătățiri (descoperit empiric: reset
# performa mai prost decât normal pe ferestre lungi).
_REGIME_MAX_DURATION = 5

# Lungimea istoricului păstrat per cheie (rolling buffer)
_HISTORY_MAXLEN = 50


def _baseline_random_hits(game_type: str, pool_size: int) -> float:
    """
    Numărul AȘTEPTAT de hituri pe pool ales la întâmplare.
    E(hits) = draw_n * pool_size / max_n
    """
    params = {
        "6/49": (6, 49),
        "5/40": (5, 40),
        "joker": (5, 45),
    }
    draw_n, max_n = params.get(game_type, (6, 49))
    return draw_n * pool_size / max_n


def _state_key(game_type: str, pool_size: int) -> str:
    return f"{game_type}_{pool_size}"


def _empty_entry() -> dict:
    return {
        "last_pool": [],
        "last_pool_date": None,  # ISO timestamp al momentului predicției
        "last_data_rows": 0,  # nr. rânduri din CSV când s-a făcut predicția
        "history": [],  # listă dict {date, pool_hits, actual, event}
        "regime_state": {
            "streak_zero": 0,
            "rolling_avg": None,
            "last_reset": None,
            "active_mode": "normal",  # "normal" | "reset"
            "reset_duration": 0,  # nr. extrageri în care suntem în reset
        },
    }


def load_adaptive_state(game_type: str, pool_size: int) -> dict:
    """Încarcă starea adaptivă pentru combinația (game_type, pool_size)."""
    if not _STATE_FILE.exists():
        return _empty_entry()
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"[ADAPTIVE] Nu pot citi {_STATE_FILE}: {e}. Pornesc gol.")
        return _empty_entry()

    entry = raw.get(_state_key(game_type, pool_size))
    if not entry:
        return _empty_entry()

    base = _empty_entry()
    base.update(entry)
    base["regime_state"] = {**base["regime_state"], **entry.get("regime_state", {})}
    return base


def save_adaptive_state(game_type: str, pool_size: int, entry: dict) -> None:
    """Salvează starea adaptivă pe disc. Read-modify-write atomic + lock advisory
    cross-proces (worker vs UI) ca să nu se piardă update-uri pe alte chei."""
    serializable = {
        "last_pool": [int(n) for n in entry.get("last_pool", [])],
        "last_pool_date": entry.get("last_pool_date"),
        "last_data_rows": int(entry.get("last_data_rows", 0)),
        "history": entry.get("history", [])[-_HISTORY_MAXLEN:],
        "regime_state": entry.get("regime_state", {}),
    }
    try:
        from ui_shared import atomic_write_json, file_lock

        with file_lock(_STATE_FILE):
            raw: dict = {}
            if _STATE_FILE.exists():
                try:
                    with open(_STATE_FILE, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                except Exception as e:
                    logger.warning(
                        f"[ADAPTIVE] Eroare citire {_STATE_FILE}: {e}. Voi suprascrie."
                    )
                    raw = {}
            raw[_state_key(game_type, pool_size)] = serializable
            atomic_write_json(_STATE_FILE, raw)  # atomic: tmp+fsync+os.replace
    except Exception as e:
        logger.error(f"[ADAPTIVE] Nu pot scrie {_STATE_FILE}: {e}")


def classify_event(pool_hits: int) -> str:
    """Clasifică un rezultat în normal / underperf / catastrophe."""
    if pool_hits == 0:
        return "catastrophe"
    if pool_hits == 1:
        return "underperf"
    return "normal"


def detect_regime_mismatch(
    history: list[dict],
    game_type: str,
    pool_size: int,
    window: int = _ROLLING_WINDOW,
    threshold: float = _REGIME_UNDERPERF_THRESHOLD,
) -> tuple[bool, float]:
    """
    Detectează regime mismatch — fereastră rolling cu media hiturilor pool
    SEMNIFICATIV sub baseline-ul aleator.

    Triggerează doar când rolling avg < baseline * (1 - threshold). Asta
    elimină false positives din varianța naturală (rolling avg poate fluctua
    sub baseline simplu doar din întâmplare).

    Returnează: (is_mismatch, rolling_avg)
    """
    if len(history) < window:
        return False, _baseline_random_hits(game_type, pool_size)

    recent = history[-window:]
    avg = sum(int(h.get("pool_hits", 0)) for h in recent) / float(window)
    baseline = _baseline_random_hits(game_type, pool_size)
    cutoff = baseline * (1.0 - threshold)
    return (avg < cutoff), avg


def compute_post_draw_feedback(
    last_pool: list[int],
    actual_draw: list[int],
    history: list[dict] | None = None,
    game_type: str = "6/49",
    pool_size: int = 12,
    streak_zero: int = 0,
    prev_mode: str = "normal",
    reset_duration: int = 0,
) -> tuple[str, dict[str, object]]:
    """
    Clasifică rezultatul unei extrageri reale (catastrofă/underperf/normal) și
    actualizează detecția de regim (streak de catastrofe, fereastră rolling).

    Args:
        last_pool: pool-ul prezis pentru extragerea CURENTĂ (numerele jucate)
        actual_draw: numerele care AU IEȘIT efectiv
        history: istoricul pool_hits din extragerile anterioare (pentru regime)
        game_type, pool_size: pentru calculul baseline-ului
        streak_zero: numărul de catastrofe consecutive (din regime_state)
        prev_mode: modul activ la pasul anterior ("normal" | "reset")
        reset_duration: nr. extrageri consecutive în care am fost în reset

    Returns:
        (event_type, regime_info)
    """
    pool_set: set[int] = {int(n) for n in last_pool}
    actual_set: set[int] = {int(n) for n in actual_draw}
    pool_hits = len(pool_set & actual_set)

    event = classify_event(pool_hits)

    missed = sorted(actual_set - pool_set)
    false_positives = sorted(pool_set - actual_set)

    # Streak update
    if event == "catastrophe":
        streak_zero += 1
    else:
        streak_zero = 0

    # Regime detection (semnificativ sub baseline, nu doar sub)
    hist_for_check = list(history or [])
    hist_for_check.append({"pool_hits": pool_hits})
    is_mismatch, rolling_avg = detect_regime_mismatch(
        hist_for_check, game_type, pool_size
    )

    # Trigger reset: streak >= THRESHOLD SAU mismatch detectat.
    # Dar respectăm durata maximă: dacă suntem în reset de mai mult de
    # _REGIME_MAX_DURATION extrageri, ieșim înapoi la normal indiferent.
    new_reset_duration = reset_duration
    should_reset = streak_zero >= _REGIME_STREAK_THRESHOLD or is_mismatch

    if prev_mode == "reset" and reset_duration >= _REGIME_MAX_DURATION:
        # Forțăm ieșirea din reset — am dat o șansă, n-a funcționat.
        active_mode = "normal"
        new_reset_duration = 0
    elif should_reset:
        active_mode = "reset"
        if prev_mode == "reset":
            new_reset_duration = reset_duration + 1
        else:
            new_reset_duration = 1
        if event != "catastrophe":
            event = "regime_reset"
    else:
        active_mode = "normal"
        new_reset_duration = 0

    regime_info = {
        "pool_hits": pool_hits,
        "rolling_avg": rolling_avg,
        "baseline": _baseline_random_hits(game_type, pool_size),
        "streak_zero": streak_zero,
        "active_mode": active_mode,
        "reset_duration": new_reset_duration,
        "missed": missed,
        "false_positives": false_positives,
        "is_mismatch": is_mismatch,
        "evaluated_pool": sorted(
            int(x) for x in pool_set
        ),  # pool-ul pe care s-a calculat feedback (folosit pentru temp_blacklist)
    }

    return event, regime_info


def record_predicted_pool(
    game_type: str,
    pool_size: int,
    pool: list[int],
    data_rows: int = 0,
    pool_date: str | None = None,
) -> None:
    """Marchează pool-ul curent prezis ca fiind cel asupra căruia se va aplica
    feedback la următoarea extragere reală.

    data_rows: numărul de rânduri din CSV la momentul predicției (folosit pentru
    a detecta extrageri noi la rularea următoare).
    """
    state = load_adaptive_state(game_type, pool_size)
    state["last_pool"] = [int(n) for n in pool]
    state["last_pool_date"] = pool_date or datetime.now().isoformat(timespec="seconds")
    state["last_data_rows"] = int(data_rows)
    save_adaptive_state(game_type, pool_size, state)


def compute_temp_blacklist(
    last_pool: list[int],
    last_event: str | None,
    universe_size: int = 49,
    pool_size: int = 12,
    enable_full_inversion: bool = True,
    partial_k: int = 4,
) -> set[int]:
    """
    Hard Inversion Temporară: după o CATASTROFĂ (0 hits), excludem temporar
    numere din pool-ul ratat la următoarea predicție.

    Strategie:
        * Activă DOAR dacă last_event == "catastrophe"
        * Cu enable_full_inversion=True (recomandat): excludem TOT pool-ul
          ratat — forțăm pool-ul nou să fie complet în spațiul complementar.
          Dacă universul (49) - pool_size (12) < pool_size (12), nu putem
          exclude toate (am ramane fara candidati suficienti) → fallback la
          excludere parțială.
        * Cu enable_full_inversion=False: excludem doar primele `partial_k`
          numere (top-K din pool, asumând pool ordonat dupa scor).
        * Această excludere e EFEMERĂ — se aplică UNA singură extragere,
          NU se persistă, NU se acumulează.

    Filozofie: dacă algoritmul a ratat COMPLET, pool-ul lui era prost
    calibrat. Forțăm explorarea spațiului complementar pentru o extragere
    singură — dacă rezolvă, nu mai apare catastrofă; dacă nu, măcar testăm
    ipoteza inversă.

    Args:
        last_pool: pool-ul anterior (cel ratat în catastrofă)
        last_event: evenimentul ultimei evaluări ("catastrophe" | altele)
        universe_size: nr. total de numere din care se extrag (49, 45 etc.)
        pool_size: dimensiunea pool-ului ce urmează a fi prezis
        enable_full_inversion: dacă True excludem TOT pool-ul ratat (când
            spațiul complementar e suficient)
        partial_k: nr. de numere de exclus când nu facem full inversion

    Returns:
        set de numere de exclus la următoarea selecție
    """
    if last_event != "catastrophe" or not last_pool:
        return set()

    pool_unique = sorted(set(int(x) for x in last_pool))
    available_after_full = universe_size - len(pool_unique)

    if enable_full_inversion and available_after_full >= pool_size:
        # Full inversion: excludem tot pool-ul ratat
        return set(pool_unique)
    # Partial fallback: excludem doar primele partial_k
    k = min(partial_k, len(pool_unique))
    return set(pool_unique[:k])


def get_state_summary(game_type: str, pool_size: int) -> dict:
    """
    Sumar pentru UI: ultimele evenimente, ajustări active, mod regim.
    """
    state = load_adaptive_state(game_type, pool_size)
    history = state.get("history", [])
    last = history[-1] if history else None

    return {
        "last_event": last.get("event") if last else None,
        "last_hits": last.get("pool_hits") if last else None,
        "last_date": last.get("date") if last else None,
        "active_mode": state.get("regime_state", {}).get("active_mode", "normal"),
        "streak_zero": state.get("regime_state", {}).get("streak_zero", 0),
        "rolling_avg": state.get("regime_state", {}).get("rolling_avg"),
        "baseline": _baseline_random_hits(game_type, pool_size),
        "history_size": len(history),
    }
