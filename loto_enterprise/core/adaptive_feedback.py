"""
Adaptive Feedback Engine — învățare persistentă post-extragere.

Sistemul detectează catastrofe (0 hituri din pool), sub-performanță (1 hit) și
regime mismatch (sub-performanță susținută în fereastră rolling) pentru a
amplifica diferențiat feedback-ul aplicat asupra scorurilor TimesFM la
următoarea predicție live.

State persistat în `adaptive_state.json` (la rădăcina proiectului), keyed pe
`{game_type}_{pool_size}` — consistent cu `pool_history.json`.

Evenimente clasificate:
    * "normal"        — pool a obținut >=2 hituri (peste baseline-ul aleator)
    * "underperf"     — exact 1 hit (sub baseline)
    * "catastrophe"   — 0 hituri (pierdere totală — semnal de regim greșit)
    * "regime_reset"  — fereastră rolling sub baseline aleator pentru >=N
                        extrageri => regimul curent al modelului e nepotrivit;
                        clamp + schimbare ponderi NQI la nivel de engine.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Locația fișierului de stare. Rădăcina proiectului = trei niveluri mai sus față
# de acest modul (loto_enterprise/core/adaptive_feedback.py -> .. -> .. -> root)
_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "adaptive_state.json"

# Limitele globale pentru error_correction_map
_FEEDBACK_MIN = 0.5
_FEEDBACK_MAX = 2.0
_FEEDBACK_DECAY = 0.8  # decay per pas spre 1.0 (memorie scurtă)

# Clamp-uri în mod regime_reset (mai conservator)
_RESET_MIN = 0.7
_RESET_MAX = 1.4

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

# Magnitudini de feedback per eveniment (missed +X, false_positive -Y)
_MAGNITUDES: Dict[str, Tuple[float, float]] = {
    "normal":      (0.10, 0.05),
    "underperf":   (0.15, 0.10),
    "catastrophe": (0.30, 0.20),
}

# Lungimea istoricului păstrat per cheie (rolling buffer)
_HISTORY_MAXLEN = 50


def _baseline_random_hits(game_type: str, pool_size: int) -> float:
    """
    Numărul AȘTEPTAT de hituri pe pool ales la întâmplare.
    E(hits) = draw_n * pool_size / max_n
    """
    params = {
        "6/49":  (6, 49),
        "5/40":  (5, 40),
        "joker": (5, 45),
    }
    draw_n, max_n = params.get(game_type, (6, 49))
    return draw_n * pool_size / max_n


def _state_key(game_type: str, pool_size: int) -> str:
    return f"{game_type}_{pool_size}"


def _empty_entry() -> dict:
    return {
        "last_pool": [],
        "last_pool_date": None,       # ISO timestamp al momentului predicției
        "last_data_rows": 0,          # nr. rânduri din CSV când s-a făcut predicția
        "history": [],                # listă dict {date, pool_hits, actual, event}
        "error_correction_map": {},   # str(num) -> multiplier
        "regime_state": {
            "streak_zero": 0,
            "rolling_avg": None,
            "last_reset": None,
            "active_mode": "normal",  # "normal" | "reset"
            "reset_duration": 0,      # nr. extrageri în care suntem în reset
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
    base["error_correction_map"] = {
        int(k): float(v) for k, v in base.get("error_correction_map", {}).items()
    }
    return base


def save_adaptive_state(game_type: str, pool_size: int, entry: dict) -> None:
    """Salvează starea adaptivă pe disc (read-modify-write atomic-ish)."""
    raw: dict = {}
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            logger.warning(f"[ADAPTIVE] Eroare citire {_STATE_FILE}: {e}. Voi suprascrie.")
            raw = {}

    serializable = {
        "last_pool": [int(n) for n in entry.get("last_pool", [])],
        "last_pool_date": entry.get("last_pool_date"),
        "last_data_rows": int(entry.get("last_data_rows", 0)),
        "history": entry.get("history", [])[-_HISTORY_MAXLEN:],
        "error_correction_map": {
            str(int(k)): round(float(v), 4)
            for k, v in entry.get("error_correction_map", {}).items()
        },
        "regime_state": entry.get("regime_state", {}),
    }
    raw[_state_key(game_type, pool_size)] = serializable

    try:
        from ui_shared import atomic_write_json
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
    history: List[dict],
    game_type: str,
    pool_size: int,
    window: int = _ROLLING_WINDOW,
    threshold: float = _REGIME_UNDERPERF_THRESHOLD,
) -> Tuple[bool, float]:
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
    last_pool: List[int],
    actual_draw: List[int],
    current_map: Dict[int, float],
    history: Optional[List[dict]] = None,
    game_type: str = "6/49",
    pool_size: int = 12,
    streak_zero: int = 0,
    prev_mode: str = "normal",
    reset_duration: int = 0,
) -> Tuple[Dict[int, float], str, Dict[str, object]]:
    """
    Calculează noul `error_correction_map` după ce s-a întâmplat o extragere
    reală.

    Args:
        last_pool: pool-ul prezis pentru extragerea CURENTĂ (numerele jucate)
        actual_draw: numerele care AU IEȘIT efectiv
        current_map: error_correction_map din pasul anterior
        history: istoricul pool_hits din extragerile anterioare (pentru regime)
        game_type, pool_size: pentru calculul baseline-ului
        streak_zero: numărul de catastrofe consecutive (din regime_state)
        prev_mode: modul activ la pasul anterior ("normal" | "reset")
        reset_duration: nr. extrageri consecutive în care am fost în reset

    Returns:
        (new_map, event_type, regime_info)
    """
    pool_set: Set[int] = {int(n) for n in last_pool}
    actual_set: Set[int] = {int(n) for n in actual_draw}
    pool_hits = len(pool_set & actual_set)

    event = classify_event(pool_hits)
    delta_missed, delta_fp = _MAGNITUDES[event]

    new_map = dict(current_map)

    missed = sorted(actual_set - pool_set)
    for m in missed:
        new_map[m] = new_map.get(m, 1.0) + delta_missed

    false_positives = sorted(pool_set - actual_set)
    for fp in false_positives:
        new_map[fp] = new_map.get(fp, 1.0) - delta_fp

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
    should_reset = (
        streak_zero >= _REGIME_STREAK_THRESHOLD or is_mismatch
    )

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

    if active_mode == "reset":
        # Clamp mai strict când suntem în reset — împiedicăm map-ul să se ducă în extreme.
        for k in list(new_map.keys()):
            new_map[k] = max(_RESET_MIN, min(_RESET_MAX, new_map[k]))
    else:
        # Clamp standard + decay spre 1.0 (memorie scurtă, evităm acumularea infinită)
        for k in list(new_map.keys()):
            v = max(_FEEDBACK_MIN, min(_FEEDBACK_MAX, new_map[k]))
            new_map[k] = 1.0 + (v - 1.0) * _FEEDBACK_DECAY

    # Curățare: scoatem multiplicatorii ce s-au întors la ~1.0 (zgomot)
    new_map = {k: v for k, v in new_map.items() if abs(v - 1.0) > 0.005}

    regime_info = {
        "pool_hits": pool_hits,
        "rolling_avg": rolling_avg,
        "baseline": _baseline_random_hits(game_type, pool_size),
        "streak_zero": streak_zero,
        "active_mode": active_mode,
        "reset_duration": new_reset_duration,
        "missed": missed,
        "false_positives": false_positives,
        "delta_missed": delta_missed,
        "delta_fp": delta_fp,
        "is_mismatch": is_mismatch,
        "evaluated_pool": sorted(int(x) for x in pool_set),  # pool-ul pe care s-a calculat feedback (folosit pentru temp_blacklist)
    }

    return new_map, event, regime_info


def update_state_after_draw(
    game_type: str,
    pool_size: int,
    actual_draw: List[int],
    actual_date: Optional[str] = None,
) -> Optional[Tuple[str, Dict[str, object]]]:
    """
    Wrapper convenabil: încarcă state, calculează feedback pentru last_pool vs
    actual_draw, salvează state actualizat. Returnează (event, regime_info) sau
    None dacă nu există un pool anterior de comparat.
    """
    state = load_adaptive_state(game_type, pool_size)
    last_pool = state.get("last_pool", [])
    if not last_pool:
        return None

    rs = state.get("regime_state", {})
    new_map, event, info = compute_post_draw_feedback(
        last_pool=last_pool,
        actual_draw=actual_draw,
        current_map=state.get("error_correction_map", {}),
        history=state.get("history", []),
        game_type=game_type,
        pool_size=pool_size,
        streak_zero=int(rs.get("streak_zero", 0)),
        prev_mode=rs.get("active_mode", "normal"),
        reset_duration=int(rs.get("reset_duration", 0)),
    )

    history = list(state.get("history", []))
    history.append({
        "date": actual_date or datetime.now().isoformat(timespec="seconds"),
        "pool_hits": int(info["pool_hits"]),
        "actual": [int(n) for n in actual_draw],
        "event": event,
    })
    history = history[-_HISTORY_MAXLEN:]

    state["error_correction_map"] = new_map
    state["history"] = history
    state["regime_state"] = {
        "streak_zero": int(info["streak_zero"]),
        "rolling_avg": float(info["rolling_avg"]) if info.get("rolling_avg") is not None else None,
        "last_reset": (
            actual_date or datetime.now().isoformat(timespec="seconds")
            if info["active_mode"] == "reset"
            else state.get("regime_state", {}).get("last_reset")
        ),
        "active_mode": info["active_mode"],
        "reset_duration": int(info.get("reset_duration", 0)),
    }

    save_adaptive_state(game_type, pool_size, state)
    return event, info


def record_predicted_pool(
    game_type: str,
    pool_size: int,
    pool: List[int],
    data_rows: int = 0,
    pool_date: Optional[str] = None,
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


def get_active_mode(game_type: str, pool_size: int) -> str:
    """Returnează modul activ ("normal" | "reset") pentru engine-ul TimesFM."""
    state = load_adaptive_state(game_type, pool_size)
    return state.get("regime_state", {}).get("active_mode", "normal")


def compute_temp_blacklist(
    last_pool: List[int],
    last_event: Optional[str],
    universe_size: int = 49,
    pool_size: int = 12,
    enable_full_inversion: bool = True,
    partial_k: int = 4,
) -> Set[int]:
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

    map_ = state.get("error_correction_map", {})
    sorted_boost = sorted(map_.items(), key=lambda x: -x[1])
    boosts = [(int(n), round(v, 3)) for n, v in sorted_boost if v > 1.05][:8]
    penalties = [(int(n), round(v, 3)) for n, v in sorted_boost[::-1] if v < 0.95][:8]

    return {
        "last_event": last.get("event") if last else None,
        "last_hits": last.get("pool_hits") if last else None,
        "last_date": last.get("date") if last else None,
        "active_mode": state.get("regime_state", {}).get("active_mode", "normal"),
        "streak_zero": state.get("regime_state", {}).get("streak_zero", 0),
        "rolling_avg": state.get("regime_state", {}).get("rolling_avg"),
        "baseline": _baseline_random_hits(game_type, pool_size),
        "boosts": boosts,
        "penalties": penalties,
        "history_size": len(history),
    }
