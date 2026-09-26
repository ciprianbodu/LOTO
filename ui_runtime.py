"""Shared UI runtime: SETTINGS, STATE, persist, game helpers."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from ui_shared import PROJECT_ROOT, atomic_write_json

logger = logging.getLogger("app_nicegui")

# --------------------------------------------------------------------------- #
# Constante / căi de stare pe disc (compatibile cu app.py & worker)
# --------------------------------------------------------------------------- #
UI_STATE_FILE = PROJECT_ROOT / ".ui_state.json"
BENCH_PID_FILE = PROJECT_ROOT / ".bench_pid"
REPORT_FILE = PROJECT_ROOT / "raport_complet.txt"

# Buget de timp TOTAL pentru walk-forward (toate jocurile). Peste buget, validarea
# se oprește PARȚIAL și pipeline-ul continuă (mail + shutdown).
WF_TOTAL_BUDGET_S = 90 * 60
# Adâncime walk-forward: ultimele X% din istoric simulate onest (fără lookahead).
WF_DEPTH_PERCENT = 30.0


def _effective_lookback_pct(from_data: dict | None = None) -> float:
    """Lookback pentru WF: preferă valoarea din rezultatul generat (job),
    altfel slider-ul UI. 0 = tot istoricul (100%)."""
    raw = None
    if isinstance(from_data, dict) and "lookback" in from_data:
        raw = from_data.get("lookback")
    if raw is None:
        raw = SETTINGS.get("lookback_val") or 0
    try:
        v = int(raw or 0)
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        return 100.0
    return float(max(1, min(100, v)))


def _restrict_base_text(audit: dict | None) -> str:
    """Descrierea restrângerii de bază aplicate, sau "" dacă nu s-a aplicat.

    Un singur loc pentru toate cele trei suprafețe (panou, raport, notă de bench),
    ca intervalul să nu apară altfel în raport decât pe ecran. Întoarce "" și
    pentru intervalul inversat, pe care motorul îl ignoră: acolo nu s-a exclus
    niciun număr, deci nu e nimic de raportat ca restrângere.
    """
    rb = (audit or {}).get("restrict_base") or {}
    if rb.get("ignored") or not rb.get("excluded"):
        return ""
    lo = int(rb.get("min") or 1)
    hi = int(rb.get("max") or 0)
    if not hi:
        return ""
    if lo > 1 and hi:
        return f"baza restrânsă la {lo}–{hi}"
    return f"baza restrânsă la ≤{hi}"


# Ținut sincron cu `walk_forward_adapter._RESTRICT_SEMANTICS`: aceeași schimbare
# de regulă invalidează și cache-ul WF, și cache-ul de pipeline al worker-ului.
_RESTRICT_SEMANTICS = "2"

# Același contract pentru limita de consecutive: sincron cu
# `walk_forward_adapter._CONSECUTIVE_SEMANTICS`, în cheie numai cu limita activă.
_CONSECUTIVE_SEMANTICS = "1"
# Valoarea trimisă când bifa e pusă: cel mult 2 consecutive, deci fără 4-5-6.
_MAX_CONSECUTIVE_RUN_ON = 2


def _consecutive_limit_text(audit: dict | None, details: bool = True) -> str:
    """Limita de consecutive aplicată rezultatului, sau "" dacă a fost oprită.

    Sursa unică pentru panou, raport, nota de bench și istoricul WF. Citește
    auditul rezultatului afișat, nu bifa din sidebar: un rezultat generat fără
    limită rămâne descris ca atare. Numerele din clasament apar cu locul lor în
    clasamentul metodei, nu cu frecvența din paranteza pool-ului. `details=False`
    dă numai regula, pentru istoricul WF, unde înlocuirile se decid la fiecare pas.
    """
    cl = (audit or {}).get("consecutive_limit") or {}
    requested = int(cl.get("requested") or 0)
    if requested <= 0:
        return ""
    text = f"fără {requested + 1} numere consecutive în pool"
    if not details:
        return text
    removed = [(int(n), int(r)) for n, r in cl.get("removed") or []]
    added = [(int(n), int(r)) for n, r in cl.get("added") or []]
    if removed:
        text += (
            "; au ieșit "
            + ", ".join(f"{n} (locul {r})" for n, r in removed)
            + ", au intrat "
            + ", ".join(f"{n} (locul {r})" for n, r in added)
            + " în clasamentul metodei"
        )
    else:
        text += "; nu a fost nevoie de nicio înlocuire"
    applied = int(cl.get("applied") or requested)
    if cl.get("relaxed"):
        text += (
            f"; limita a urcat la {applied} consecutive: baza restrânsă e prea "
            "îngustă pentru pool"
        )
    return text


def _wf_generation_options(data: dict) -> dict:
    """Validează configurația rezultatului, inclusiv factorul legitim 0.

    Totul vine din rezultatul afișat, nu din sidebar. Limita de consecutive:
    ecoul worker-ului, apoi limita cerută din audit, apoi 0 (rezultat vechi,
    generat fără limită). Se trimite limita CERUTĂ, nu cea relaxată: WF decide
    relaxarea la fiecare pas, ca producția.
    """
    audit = data.get("audit") or {}
    context = data.get("context") or {}
    factor = data.get("recent_penalty_factor", 0.5)
    # `is not None`, NU `or`: acelasi tipar de bug pe care factorul de mai sus
    # tocmai l-a reparat (0 e valoare LEGITIMA, nu absenta) s-ar reintroduce
    # aici daca `wheel_guarantee_used`/`wheel_condition_used` ar deveni vreodata
    # 0 (azi imposibil - worker.py plafoneaza garantia la minim 3 - dar nimic nu
    # garanteaza asta pentru totdeauna).
    guarantee = audit.get("wheel_guarantee_used")
    if guarantee is None:
        guarantee = data.get("guarantee")
    wheel_condition = audit.get("wheel_condition_used")
    if wheel_condition is None:
        wheel_condition = data.get("wheel_condition")
    max_run = data.get("max_consecutive_run")
    if max_run is None:
        max_run = (audit.get("consecutive_limit") or {}).get("requested")
    return {
        "recent_penalty_draws": int(data.get("recent_penalty_draws") or 0),
        "recent_penalty_factor": 0.5 if factor is None else float(factor),
        "guarantee": guarantee,
        "wheel_condition": wheel_condition,
        "max_variants": int(data.get("max_variants", context.get("max_variants")) or 0),
        "restrict_base_max": int(data.get("restrict_base_max") or 0),
        "restrict_base_min": int(data.get("restrict_base_min") or 0),
        "max_consecutive_run": int(max_run or 0),
    }


# Praguri separate PER JOC: 6/49, 5/40 și Joker Urna 1 au universuri diferite
# (49/40/45), deci un singur interval global (ex. „10-40") nu are sens pentru
# toate trei deodată — pentru 5/40 chiar depășește universul. Cheia SETTINGS e
# sufixată cu identificatorul jocului; `game_label` e forma întoarsă de
# `_game_label_for` ("6/49", "5/40", "joker"), aceeași folosită la construirea
# task-urilor din `_build_config_json`.
_RESTRICT_BASE_GAMES = (
    ("6/49", "649", 49),
    ("5/40", "540", 40),
    ("joker", "joker", 45),
)
_RESTRICT_BASE_SUFFIX = {label: suffix for label, suffix, _max_num in _RESTRICT_BASE_GAMES}


def _active_restrict_base(game_label: str) -> tuple[int, int]:
    """Interval de bază activ pentru UN joc: (0, 0) dacă bifa e oprită sau
    jocul nu are o pereche proprie de praguri, indiferent de ce e tastat în
    câmpuri. Sursa unică pentru `_build_config_json` (hash + task per joc) —
    oprirea bifei trebuie să anuleze valorile peste tot, nu doar la ultima
    citire."""
    if not SETTINGS.get("restrict_base_enabled_val"):
        return 0, 0
    suffix = _RESTRICT_BASE_SUFFIX.get(game_label)
    if suffix is None:
        return 0, 0
    return (
        _int_setting(f"restrict_base_min_{suffix}_val"),
        _int_setting(f"restrict_base_max_{suffix}_val"),
    )


def _active_max_consecutive_run() -> int:
    """Limita de consecutive pentru următoarea generare: 2 cu bifa pusă, 0 fără.

    Întreg, nu bool: `int(True)` ar da 1, adică „fără nicio pereche", mult mai
    strict decât a cerut utilizatorul. Aceeași valoare pentru toate jocurile;
    motorul o aplică numai pool-ului principal (Urna 2 Joker are un număr).
    """
    return _MAX_CONSECUTIVE_RUN_ON if SETTINGS.get("max_consecutive_run_enabled_val") else 0


def _clamped_bench_target(value=None) -> int:
    raw = SETTINGS.get("bench_hit_target", 3) if value is None else value
    try:
        from loto_enterprise.benchmark.hit_target import clamp_bench_hit_target

        return clamp_bench_hit_target(raw)
    except Exception:  # noqa: BLE001
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = 3
        return n if n in (3, 4) else 3


UI_PERSIST_KEYS = [
    "pool_size_val",
    "guarantee_val",
    "max_variants_val",
    "lookback_val",
    "wheel_condition_val",
    "recent_penalty_draws_val",
    "recent_penalty_factor_val",
    "restrict_base_enabled_val",
    *(f"restrict_base_min_{suffix}_val" for _l, suffix, _m in _RESTRICT_BASE_GAMES),
    *(f"restrict_base_max_{suffix}_val" for _l, suffix, _m in _RESTRICT_BASE_GAMES),
    # Fără ea în listă, bifa debifată revine pornită la fiecare repornire:
    # `_load_settings` citește numai cheile de aici, iar implicitul e True.
    "max_consecutive_run_enabled_val",
    "shutdown_on_complete",
    "sim_depth_val",
    "autopilot_after_bench",
    "mail_on_complete",
    "last_finalized_job_id",
    "wf_budget_min",
    "bench_hit_target",
]
DEFAULTS = {
    "pool_size_val": 10,
    "guarantee_val": 4,
    "max_variants_val": 0,
    # Lotto design „garanție dacă condiție": 0 = cover clasic (condiție = garanție).
    "wheel_condition_val": 0,
    # Penalizare numere extrase în ultimele N extrageri (0 = oprit), scor × factor^aparitii.
    # Implicit OPRITĂ: schimbă pool-ul fără nicio acțiune din partea utilizatorului
    # dacă e pornită din start — utilizatorul decide explicit din UI dacă o vrea.
    "recent_penalty_draws_val": 0,
    "recent_penalty_factor_val": 0.5,
    # Restrângere bază — praguri SEPARATE per joc (6/49, 5/40, Joker Urna 1), toate
    # 0 implicit (fără restricție). Preferință OPȚIONALĂ a utilizatorului, FĂRĂ
    # avantaj statistic demonstrat — probabilitatea de hit a unui pool de
    # dimensiune fixă e identică matematic (hipergeometric) indiferent de care
    # numere îl compun. Vezi scripts/analysis/pattern_base_reduction.py și
    # docstring-ul loto_engine.run_institutional_pipeline. Nu prezenta niciodată
    # acest câmp drept optimizare — e doar compoziție, la fel ca recent_penalty_draws.
    **{
        f"restrict_base_{bound}_{suffix}_val": 0
        for _label, suffix, _max_num in _RESTRICT_BASE_GAMES
        for bound in ("min", "max")
    },
    # Bifă separată de valorile de mai sus: oprită implicit, indiferent de ce e
    # tastat în câmpuri — un capăt lăsat din greșeală nediscutat nu ajunge
    # niciodată în producție cât bifa e oprită (vezi `_active_restrict_base`).
    "restrict_base_enabled_val": False,
    # Fără 3 numere consecutive în pool: PORNITĂ implicit, la cererea explicită a
    # utilizatorului (2026-09-26), spre deosebire de penalizare și restrângere,
    # oprite fiindcă ar schimba pool-ul fără nicio acțiune. Tot compoziție, FĂRĂ
    # avantaj statistic demonstrat (hipergeometric, ca mai sus); bifa o oprește.
    "max_consecutive_run_enabled_val": True,
    "lookback_val": 0,
    "shutdown_on_complete": False,
    "sim_depth_val": 40,
    "autopilot_after_bench": True,
    "mail_on_complete": False,
    # NU e o bifă de UI: ultimul job dus prin finalize (mail/shutdown). Împiedică
    # re-procesarea aceluiași job la fiecare repornire (altfel = shutdown repetat).
    "last_finalized_job_id": 0,
    # Buget walk-forward (minute). Plafon de siguranță (anulare automată), NU timp real de rulare:
    # cu WF paralel (~80% CPU) validarea completă la 30% depth durează minute, nu ore.
    # 90 min e larg; la rulări zilnice poți coborî la 15–30 dacă vrei.
    "wf_budget_min": 90,
    "bench_hit_target": 3,
}

# --------------------------------------------------------------------------- #
# Stare server-side (single-user local app → globală e suficient)
# --------------------------------------------------------------------------- #
SETTINGS: dict = dict(DEFAULTS)


def _float_setting(key: str, default: float | None = None) -> float:
    """SETTINGS[key] ca float, robust la câmp golit (None) — ca `_int_setting`."""
    try:
        v = SETTINGS.get(key)
        if v is None:
            v = DEFAULTS.get(key) if default is None else default
        return float(v)
    except (TypeError, ValueError):
        return float(DEFAULTS.get(key, 0.0) if default is None else default)


def _int_setting(key: str, default: int | None = None) -> int:
    """SETTINGS[key] ca int, robust la câmp GOLIT în UI (ui.number → None).

    _bind_save scrie None direct în SETTINGS; fără gardă, int(None) crăpa
    submit-ul (Generează eșua tăcut) și randarea clasamentului. Fallback:
    DEFAULTS[key] (sau `default` explicit)."""
    try:
        v = SETTINGS.get(key)
        if v is None:
            raise TypeError(key)
        return int(v)
    except (TypeError, ValueError):
        return int(default if default is not None else DEFAULTS[key])


STATE: dict = {
    "datasets": [],  # list[(fname, DataFrame)]
    "active_job_id": None,
    "job_start_time": None,
    "job_elapsed": None,  # durata FIXĂ a ultimei generări (sec); setată la COMPLETED
    "wf_elapsed": None,  # durata FIXĂ generare+walk-forward (sec); setată la finalul WF
    "results": None,  # (results_bundle, count)
    "results_recovered": None,  # etichetă „job #N · dată" dacă rezultatele-s recuperate (vechi)
    "retro": {},  # {f"{fname}_{game}": flat_walk_forward}
    "retro_meta": {},  # {aceeași cheie: {partial, n_test_draws, n_expected, from_cache}}
    "wf_status": "",  # text status walk-forward
    "wf_progress": 0.0,  # fracție 0..1 progres walk-forward (bară)
    "wf_start": None,  # timestamp pornire WF (ETA în UI)
    "pure_bench": False,
    "show_all": {},  # {f"{fname}_{game}": bool} — toggle wheel complet
    "bench_was_running": False,
    "bench_cancelled": False,  # True după Anulează → _tick NU mai pornește Auto-Pilot
    "_log_cache": None,  # conținut loguri pre-citit în thread (ne-blocant pt UI)
}

# R3: lock pentru mutații compuse pe STATE din thread-uri (walk-forward)
# vs thread-ul principal UI. (Operațiile simple pe dict sunt atomice prin GIL;
# lock-ul protejează secvențele multi-pas / iterările.)
STATE_LOCK = threading.RLock()

GK_MATRIX = {  # etichetă afișată → cheia jocului din bench (best_methods.json / folds.csv)
    "Loto 6/49": "loto_6_49",
    "Loto 5/40": "loto_5_40",
    "Joker Urna 1": "joker_urna1",
    "Joker Urna 2": "joker_urna2",
}


def _load_settings() -> None:
    if UI_STATE_FILE.exists():
        try:
            data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
            for k in UI_PERSIST_KEYS:
                if k in data:
                    SETTINGS[k] = data[k]
        except Exception as exc:  # noqa: BLE001
            logger.warning("load settings: %s", exc)
    # Pool max 16 — clamp pentru valori salvate de versiuni vechi.
    try:
        if int(SETTINGS.get("pool_size_val", 10)) > 16:
            SETTINGS["pool_size_val"] = 16
    except (TypeError, ValueError):
        SETTINGS["pool_size_val"] = 10

    # Inițializează variabila din modulul decision și os.environ din setările salvate
    try:
        import loto_enterprise.benchmark.decision as decision

        target = _clamped_bench_target()
        SETTINGS["bench_hit_target"] = target
        decision.BENCH_HIT_TARGET = target
        os.environ["LOTO_BENCH_TARGET"] = str(target)
    except Exception as exc:
        logger.warning("init bench_hit_target check: %s", exc)


def _save_settings() -> None:
    try:
        atomic_write_json(UI_STATE_FILE, SETTINGS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("save settings: %s", exc)


def _game_label_for(fname: str) -> str:
    low = fname.lower()
    if "5_40" in low or "5/40" in low:
        return "5/40"
    if "joker" in low:
        return "joker"
    return "6/49"


# Ordinea de AFIȘARE a jocurilor în UI / rapoarte: 6/49 primul, Joker al doilea, 5/40 al treilea.
# (Independentă de ordinea în care s-au încărcat fișierele/dataset-urile.)
_GAME_DISPLAY_ORDER = {"6/49": 0, "joker": 1, "5/40": 2}
# Ordinea walk-forward: jocuri rapide / adesea din cache ÎNTÂI, 6/49 (cel mai lent) ULTIM
# → primește restul bugetului global, nu doar prima felie (1/N).
_WF_GAME_ORDER = {"joker": 0, "5/40": 1, "6/49": 2}


def _ordered_game_items(outs):
    """Items din `outs` ordonate pentru afișare: 6/49, Joker, 5/40."""
    return sorted(
        outs.items(),
        key=lambda kv: _GAME_DISPLAY_ORDER.get(_game_label_for(str(kv[0])), 99),
    )


def _primary_pool_data(data: dict) -> dict:
    """Pool unic; payload-urile vechi cu două faze folosesc faza normală."""
    if isinstance(data, dict) and isinstance(data.get("phase1"), dict):
        return data["phase1"]
    return data


def _iter_wf_jobs(results_bundle):
    """(fname, game_label, data) pentru singurul pool de producție.

    Ordinea e pe JOC (Joker → 5/40 → 6/49), NU pe ordinea fișierelor încărcate.
    Altfel upload 6/49 + 5/40 + joker rula 6/49 ca 1/3 și îi mânca bugetul;
    6/49 trebuie ULTIM ca să primească timpul rămas.
    """
    jobs: list[tuple] = []
    for fname, outs in results_bundle:
        for g_label, data in outs.items():
            jobs.append((fname, g_label, _primary_pool_data(data)))
    jobs.sort(key=lambda j: _WF_GAME_ORDER.get(_game_label_for(str(j[1])), 99))
    yield from jobs


def _count_wf_jobs(results_bundle) -> int:
    return sum(1 for _ in _iter_wf_jobs(results_bundle))


def _fmt_dur(sec) -> str:
    """Durată granulară în h/m/s: '1h 23m 4s' / '3m 12s' / '45s'."""
    try:
        s = int(round(float(sec)))
    except (TypeError, ValueError):
        return "?"
    if s < 0:
        s = 0
    h, rem = divmod(s, 3600)
    m, sec_ = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec_}s"
    if m:
        return f"{m}m {sec_}s"
    return f"{sec_}s"

