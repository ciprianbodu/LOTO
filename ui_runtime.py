"""Shared UI runtime: SETTINGS, STATE, persist, game helpers."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from ui_shared import PROJECT_ROOT, atomic_write_json
from covering.lr_budget import DEFAULT_BUDGET_LEI
from loto_enterprise.benchmark import DEFAULT_REPLAY_PERCENT, VALIDATION_PERCENTILES
from loto_enterprise.benchmark.hit_target import (
    BENCH_TARGET_REVISION, DEFAULT_BENCH_HIT_TARGET, clamp_bench_hit_target,
    saved_bench_hit_target,
)
from loto_enterprise.core import lotteries as _LOT

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
# Fereastra implicită pentru telemetria generării manuale. Auto-Pilot poate
# folosi fereastra din decizia de bench a jocului.
BENCH_TELEMETRY_DEPTH = 40
# Adâncime walk-forward: ultimele X% din istoric simulate onest (fără lookahead).
WF_DEPTH_PERCENT = float(DEFAULT_REPLAY_PERCENT)


def _effective_lookback_pct(from_data: dict | None = None) -> float:
    """Lookback pentru WF: preferă valoarea din rezultatul generat (job),
    altfel slider-ul UI. 0 = tot istoricul (100%)."""
    raw = None
    if isinstance(from_data, dict) and "lookback" in from_data:
        raw = from_data.get("lookback")
    if raw is None:
        raw = _int_setting("lookback_val")
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
    applied = int(cl.get("applied") or requested)
    if cl.get("relaxed"):
        # Baza restrânsă nu a lăsat destule numere: pool-ul poate avea secvențe
        # mai lungi, deci textul nu are voie să promită „fără 3 consecutive".
        text = (
            f"cererea „fără {requested + 1} numere consecutive” nu a încăput în "
            f"baza restrânsă; pool-ul are până la {applied} consecutive"
        )
    else:
        text = f"fără {requested + 1} numere consecutive în pool"
    if not details:
        return text
    removed = [(int(n), int(r)) for n, r in cl.get("removed") or []]
    added = [(int(n), int(r)) for n, r in cl.get("added") or []]
    # „Scos din pool", nu „a ieșit": într-o aplicație de loterie „a ieșit"
    # înseamnă „a fost extras". Locul e cel din clasamentul metodei.
    if removed:
        text += (
            "; scos din pool: "
            + ", ".join(f"{n} (locul {r})" for n, r in removed)
            + "; pus în loc: "
            + ", ".join(f"{n} (locul {r})" for n, r in added)
            + " în clasamentul metodei"
        )
    elif not cl.get("relaxed"):
        text += "; nu a fost nevoie de nicio înlocuire"
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
    opts = {
        "recent_penalty_draws": int(data.get("recent_penalty_draws") or 0),
        "recent_penalty_factor": 0.5 if factor is None else float(factor),
        "guarantee": guarantee,
        "wheel_condition": wheel_condition,
        "max_variants": int(data.get("max_variants", context.get("max_variants")) or 0),
        "restrict_base_max": int(data.get("restrict_base_max") or 0),
        "restrict_base_min": int(data.get("restrict_base_min") or 0),
        "max_consecutive_run": int(max_run or 0),
    }
    lr_code = _result_lr_scheme_code(data)
    if lr_code is not None:
        opts["lr_scheme_code"] = lr_code
        if _result_uses_dynamic_method(data):
            opts["dynamic_method"] = True
    # Identitatea jocului STRĂIN vine din ecoul rezultatului (worker v6), ca WF
    # să citească decizia țării lui. România (sau un rezultat fără ecou): dict-ul
    # de dinainte, fără chei noi.
    country = data.get("country")
    if country and str(country).upper() != "RO":
        opts["game_key"] = data.get("bench_key")
        opts["country"] = country
    return opts


_RO_UI_SUFFIXES = {"6/49": "649", "5/40": "540", "joker": "joker"}

# Pool-ul pe joc: câmpuri separate pentru România, cu limitele jocului
# (`Lottery.max_pool`: 49 / 40 / 45). La pornire revenim la schemele oficiale
# jucate de utilizator: 6/49 Cod 48 (9 numere), 5/40 Cod 16 (8 numere),
# Joker Cod 24 (10 numere). Aceste valori nu sunt maximele catalogului LR;
# `Lottery.default_pool` rămâne separat, fiind folosit și pentru notele de catalog.
# Jocurile altor țări folosesc câmpul comun `pool_size_val`.
# Minimul UI (2026-11-11): cel mai mic cod LR oficial (`Lottery.min_pool`:
# 9 la 6/49 = Cod 48; 7 la 5/40 = Cod 15; 7 la Joker = Cod 45).
_POOL_KEYS = {game: f"pool_size_{suffix}_val" for game, suffix in _RO_UI_SUFFIXES.items()}
_POOL_DEFAULTS = {"6/49": 9, "5/40": 8, "joker": 10}
_LR_CODE_KEYS = {game: f"lr_scheme_{suffix}_val" for game, suffix in _RO_UI_SUFFIXES.items()}
_LR_CODE_DEFAULTS = {"6/49": 48, "5/40": 16, "joker": 24}


def _pool_size_for(game) -> int:
    """Pool-ul următoarei generări pentru `game` (etichetă, fișier sau cheie de bench).

    România: numai dimensiuni din catalogul LR; sliderul alege codul exact,
    inclusiv când mai multe coduri folosesc aceeași dimensiune. O valoare
    veche, păstrată numai ca număr, se apropie de o dimensiune LR disponibilă.
    Alte țări: câmpul numeric comun rămâne plafonat la limitele jocului."""
    lot = _game_spec_for(game)
    key = _POOL_KEYS.get(lot.game_id) if lot.is_romanian else None
    raw = _int_setting(key) if key else _int_setting("pool_size_val")
    if lot.is_romanian:
        from covering.lr_selection import lr_pool_sizes

        allowed = lr_pool_sizes(lot.geometry)
        if not allowed:
            raise ValueError(f"Nu există scheme reduse oficiale pentru {lot.name}.")
        return min(allowed, key=lambda n: (abs(n - raw), n))
    return max(int(lot.min_pool), min(int(lot.max_pool), int(raw)))


def _result_lr_scheme_code(data: dict):
    code = data.get("lr_scheme_code")
    if code is None:
        code = ((data.get("audit") or {}).get("lr_scheme") or {}).get("code")
    return code


def _result_uses_dynamic_method(data: dict | None) -> bool:
    if not data or _result_lr_scheme_code(data) is None:
        return False
    if "dynamic_method" in data:
        return data["dynamic_method"] is True
    audit = data.get("audit") or {}
    return bool(audit.get("dynamic_selection")) or any(
        info.get("dynamic_selection") for info in (audit.get("bench_winner") or {}).values() if info
    )


def _lr_scheme_for(game, pool_size=None):
    """Keep the explicit slider code; legacy pool-only settings use cheapest."""
    from covering.lr_selection import resolve_lr_scheme, select_lr_scheme

    lot = _game_spec_for(game)
    if not lot.is_romanian:
        return None
    size = _pool_size_for(game) if pool_size is None else int(pool_size)
    code = SETTINGS.get(_LR_CODE_KEYS[lot.game_id])
    if code is not None:
        try:
            return resolve_lr_scheme(lot.geometry, code, size)
        except (TypeError, ValueError):
            pass
    return select_lr_scheme(lot.geometry, size, policy="cost")


def _lr_pct(p: float) -> str:
    """Procent cu două zecimale; sub 0,1% două cifre semnificative."""
    value = 100.0 * float(p)
    return f"{value:.2f}%" if value >= 0.1 or value == 0 else f"{value:.2g}%"


def _lr_guarantee_text(scheme) -> str:
    """Ce prinde schema după câte numere ies dintre cele jucate, din profilul exact."""
    from covering.lr_schemes import scheme_guarantees

    return _lr_guarantees_text(scheme_guarantees(scheme))


def _lr_guarantees_text(guarantees: dict) -> str:
    """Profil exact păstrat în rezultat; acceptă și cheile JSON ale istoricului."""
    parts = []
    for k, raw_row in sorted(guarantees.items(), key=lambda item: int(item[0])):
        row = {int(t): float(v) for t, v in raw_row.items()}
        top = max(row)
        sure = max((t for t, v in row.items() if v >= 1.0 - 1e-12), default=None)
        bits = [f"{sure} sigur"] if sure is not None else []
        bits += [
            f"{t}{'' if t == top else '+'} în {_lr_pct(v)}"
            for t, v in row.items()
            if (sure is None or t > sure) and v > 0
        ]
        parts.append(f"{k} {'ieșite ' if not parts else ''}→ " + (", ".join(bits) or "sub 3"))
    return "; ".join(parts)


def _lr_selection_lines(game) -> tuple[str, str]:
    lot = _game_spec_for(game)
    scheme = _lr_scheme_for(game)
    price = float(lot.price)
    summary = (f"Cod {scheme.code} · {scheme.n} numere · {scheme.size} variante · "
               f"{scheme.size * price:g} lei în variante; 0,50 lei/bilet, tarif standard.")
    return summary, "Garanții condiționate: " + _lr_guarantee_text(scheme)


def _lr_selection_change_text(game, previous_code, next_code) -> str:
    from covering.lr_selection import resolve_lr_scheme, lr_scheme_transition

    if previous_code == next_code:
        return ""
    geometry = _game_spec_for(game).geometry
    change = lr_scheme_transition(resolve_lr_scheme(geometry, previous_code), resolve_lr_scheme(geometry, next_code))
    lost = []
    for threshold, values in sorted(change["lost_odds"].items()):
        label = "3+ (orice premiu la 6/49)" if geometry == "6/49" and threshold == 3 else f"{threshold}+"
        lost.append(f"{label}: {100 * values['before']:.6f}% → {100 * values['after']:.6f}%")
    lost += [f"cu {entry['pool_hits']} în pool, {entry['ticket_hits']} sigur → {100 * entry['new_probability']:.2f}%"
             for entry in change["lost_sure_guarantees"]]
    joker_labels = {"any_prize": "orice premiu Joker",
                    "exact_two_plus_joker": "exact 2 numere + J (categoria VII)"}
    joker_losses = change.get("lost_joker_odds", {})
    lost += [f"{joker_labels[key]}: {100 * values['before']:.6f}% → {100 * values['after']:.6f}%"
             for key, values in joker_losses.items()]
    if not lost:
        return ""
    note = (
        " Mai multe variante sau un pool mai mare nu asigură șanse mai mari la fiecare categorie. "
        "Calculele presupun aceeași bilă Joker pe toate variantele."
        if joker_losses else " Mai multe variante sau un pool mai mare nu asigură șanse mai mari la fiecare prag."
    )
    return (f"Față de Cod {previous_code}, Cod {next_code} reduce șansa pe cel puțin o variantă: "
            + " · ".join(lost) + "." + note)


def _large_pool_notes() -> list[str]:
    """Costul unei alegeri mari."""
    if _selected_country() != _LOT.RO:
        return []
    from math import comb
    from covering.large_pool import validate_large_pool_request

    notes = []
    budget = max(0, _int_setting("max_variants_val"))
    guarantee = _int_setting("guarantee_val")
    for lot in _selected_games():
        pool = _pool_size_for(lot.game_id)
        if pool <= lot.default_pool:
            continue
        text = (
            f"{lot.name}: pool de {pool} numere. Variantele sunt generate în afara "
            "catalogului LR; acoperirea efectivă și garanția se verifică pentru "
            "plafonul ales. "
        )
        guard_message = ""
        if pool > 19:
            normalized_guarantee = max(3, min(lot.pick_n, guarantee))
            condition = _int_setting("wheel_condition_val")
            condition = (
                normalized_guarantee if condition <= 0
                else max(normalized_guarantee, min(lot.pick_n, condition))
            )
            try:
                validate_large_pool_request(
                    pool, lot.pick_n, normalized_guarantee, budget, condition
                )
            except ValueError as exc:
                guard_message = str(exc).replace(",", ".")
        if guard_message:
            text += guard_message
        elif budget:
            text += f"Plafonul ales este de {budget:,} variante".replace(",", ".")
            if lot.price is not None:
                cost = (
                    f"{budget * lot.price:,.2f}".replace(",", "_")
                    .replace(".", ",").replace("_", ".")
                )
                text += f" (cel mult {cost} lei, fără taxe pe bilet)"
            text += "."
        elif guarantee >= lot.pick_n:
            variants = comb(pool, lot.pick_n)
            formatted = f"{variants:,}".replace(",", ".")
            text += (
                f"Garanția cerută înseamnă sistem complet: {formatted} variante, "
                "fără plafon."
            )
        else:
            text += (
                "Nu este ales un plafon de variante. Un pool mai mare poate cere "
                "mai multe variante și un cost mai mare."
            )
        notes.append(text)
    return notes


def _clamped_bench_target(value=None) -> int:
    raw = SETTINGS.get("bench_hit_target", DEFAULT_BENCH_HIT_TARGET) if value is None else value
    return clamp_bench_hit_target(raw)


UI_PERSIST_KEYS = [
    "lr_budget_lei",
    "bench_target_revision",
    "dynamic_method_enabled_val",
    "joker_pool_size_val",
    "pool_size_val",
    *_POOL_KEYS.values(),
    *_LR_CODE_KEYS.values(),
    "guarantee_val",
    "max_variants_val",
    "lookback_val",
    "wheel_condition_val",
    # Țara și jocurile ei (pasul 1).
    "country_val",
    "games_val",
    "shutdown_on_complete",
    "autopilot_after_bench",
    "mail_on_complete",
    "wf_depth_pct",
    "bench_hit_target",
]
DEFAULTS = {
    "lr_budget_lei": DEFAULT_BUDGET_LEI,
    "pool_size_val": 10,
    "joker_pool_size_val": 5,
    **{_POOL_KEYS[label]: size for label, size in _POOL_DEFAULTS.items()},
    **{_LR_CODE_KEYS[label]: code for label, code in _LR_CODE_DEFAULTS.items()},
    "guarantee_val": 4,
    "max_variants_val": 0,
    # Lotto design „garanție dacă condiție": 0 = cover clasic (condiție = garanție).
    "wheel_condition_val": 0,
    # Țara aleasă (pasul 1) și jocurile ei; [] = toate jocurile țării.
    "country_val": _LOT.RO,
    "games_val": [],
    "lookback_val": 0,
    "shutdown_on_complete": False,
    "autopilot_after_bench": True,
    "mail_on_complete": False,
    "wf_depth_pct": DEFAULT_REPLAY_PERCENT,
    "bench_hit_target": DEFAULT_BENCH_HIT_TARGET,
    "bench_target_revision": BENCH_TARGET_REVISION,
    "dynamic_method_enabled_val": True,
}

# --------------------------------------------------------------------------- #
# Stare server-side (single-user local app → globală e suficient)
# --------------------------------------------------------------------------- #
SETTINGS: dict = dict(DEFAULTS)


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
    except (TypeError, ValueError, OverflowError):
        return int(default if default is not None else DEFAULTS[key])


STATE: dict = {
    "datasets": [],  # list[(fname, DataFrame)]
    "dataset_game": {},  # {fname: game_id} — legătură explicită (lipsă = ghicit RO)
    "active_job_id": None,
    "job_start_time": None,
    "job_elapsed": None,  # durata FIXĂ a ultimei generări (sec); setată la COMPLETED
    "wf_elapsed": None,  # durata FIXĂ generare+walk-forward (sec); setată la finalul WF
    "results": None,  # (results_bundle, count)
    "result_sources": None,  # {fname: submitted DataFrame}; None = legacy/live source
    "results_recovered": None,  # etichetă „job #N · dată" dacă rezultatele-s recuperate (vechi)
    "retro": {},  # {f"{fname}_{game}": flat_walk_forward}
    "retro_meta": {},  # {aceeași cheie: {partial, n_test_draws, n_expected, from_cache}}
    "wf_status": "",  # text status walk-forward
    "wf_progress": 0.0,  # fracție 0..1 progres walk-forward (bară)
    "wf_start": None,  # timestamp pornire WF (ETA în UI)
    "pure_bench": False,
    "bench_was_running": False,
    "bench_cancelled": False,  # True după Anulează → _tick NU mai pornește Auto-Pilot
    "_log_cache": None,  # conținut loguri pre-citit în thread (ne-blocant pt UI)
}

# R3: lock pentru mutații compuse pe STATE din thread-uri (walk-forward)
# vs thread-ul principal UI. (Operațiile simple pe dict sunt atomice prin GIL;
# lock-ul protejează secvențele multi-pas / iterările.)
STATE_LOCK = threading.RLock()


def _result_source(fname: str):
    """Source used by the displayed result, independent of subsequent uploads."""
    with STATE_LOCK:
        sources = STATE.get("result_sources")
        if sources is not None:
            return sources.get(fname)
        return next((df for name, df in STATE.get("datasets", []) if name == fname), None)

GK_MATRIX = {  # etichetă afișată → cheia jocului din bench (best_methods.json / folds.csv)
    "Loto 6/49": "loto_6_49",
    "Loto 5/40": "loto_5_40",
    "Joker Urna 1": "joker_urna1",
    "Joker Urna 2": "joker_urna2",
}


def _load_settings() -> None:
    data = {}
    migrated_target = False
    if UI_STATE_FILE.exists():
        try:
            data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
            for k in UI_PERSIST_KEYS:
                if k in data:
                    SETTINGS[k] = data[k]
        except Exception as exc:  # noqa: BLE001
            logger.warning("load settings: %s", exc)
    depth = SETTINGS.get("wf_depth_pct")
    if type(depth) is not int or depth not in VALIDATION_PERCENTILES:
        SETTINGS["wf_depth_pct"] = DEFAULT_REPLAY_PERCENT
    if isinstance(data, dict):
        SETTINGS["bench_hit_target"], migrated_target = saved_bench_hit_target(data)
        if migrated_target:
            SETTINGS["bench_target_revision"] = BENCH_TARGET_REVISION
    # Câmpul comun rămâne 6..16 pentru jocurile altor țări.
    try:
        SETTINGS["pool_size_val"] = max(
            6, min(16, int(SETTINGS.get("pool_size_val", 10)))
        )
    except (TypeError, ValueError, OverflowError):
        SETTINGS["pool_size_val"] = 10
    # Numai la pornirea procesului, cele trei pooluri revin la valorile cerute.
    # Câmpurile rămân editabile pentru toată sesiunea și pentru toate paginile.
    for _label, _key in _POOL_KEYS.items():
        SETTINGS[_key] = _POOL_DEFAULTS[_label]
        SETTINGS[_LR_CODE_KEYS[_label]] = _LR_CODE_DEFAULTS[_label]

    _sanitize_country_settings()

    # Inițializează variabila din modulul decision și os.environ din setările salvate
    try:
        import loto_enterprise.benchmark.decision as decision

        target = _clamped_bench_target()
        SETTINGS["bench_hit_target"] = target
        decision.BENCH_HIT_TARGET = target
        os.environ["LOTO_BENCH_TARGET"] = str(target)
    except Exception as exc:
        logger.warning("init bench_hit_target check: %s", exc)
    if migrated_target:
        # Migrarea modifica numai tinta; nu rescrie poolurile ori alte valori
        # salvate folosind normalizarile temporare ale sesiunii.
        updated = dict(data)
        updated.update(bench_hit_target=SETTINGS["bench_hit_target"], bench_target_revision=BENCH_TARGET_REVISION)
        try:
            atomic_write_json(UI_STATE_FILE, updated)
        except Exception as exc:  # noqa: BLE001
            logger.warning("save bench target migration: %s", exc)


def _legacy_finalized_job_id() -> int | None:
    """`last_finalized_job_id` scris de versiunile vechi; None = cheia lipsește.

    Jobul preluat de UI se marchează acum în baza stației (`ui_finalized_at`)."""
    try:
        data = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "last_finalized_job_id" not in data:
        return None
    try:
        return int(data["last_finalized_job_id"] or 0)
    except (TypeError, ValueError):
        return 0


def _save_settings() -> None:
    try:
        atomic_write_json(UI_STATE_FILE, SETTINGS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("save settings: %s", exc)


def _sanitize_country_settings() -> None:
    """Țară necunoscută → România; jocuri care nu sunt ale țării → scoase."""
    try:
        cc = _LOT.normalize_country(SETTINGS.get("country_val") or _LOT.RO)
    except _LOT.UnknownLotteryError:
        cc = _LOT.RO
    SETTINGS["country_val"] = cc
    own = {g.game_id for g in _LOT.games_for_country(cc)}
    raw = SETTINGS.get("games_val") or []
    if not isinstance(raw, (list, tuple)):
        raw = []
    SETTINGS["games_val"] = [str(g) for g in raw if str(g) in own]


def _selected_country() -> str:
    try:
        return _LOT.normalize_country(SETTINGS.get("country_val") or _LOT.RO)
    except _LOT.UnknownLotteryError:
        return _LOT.RO


def _selected_games() -> tuple:
    """Jocurile (Lottery) alese pentru țara curentă; nimic ales = toate."""
    games = _LOT.games_for_country(_selected_country())
    chosen = {str(g) for g in (SETTINGS.get("games_val") or [])}
    picked = tuple(g for g in games if g.game_id in chosen)
    return picked or games


def _country_label(country=None) -> str:
    cc = country or _selected_country()
    try:
        return _LOT.country_name(cc)
    except _LOT.UnknownLotteryError:
        return str(cc)


def _legacy_ro_label(name: str) -> str:
    """Ghicitul vechi, NUMAI pentru România (fișiere fără legătură explicită)."""
    low = str(name).lower()
    if "5_40" in low or "5/40" in low:
        return "5/40"
    if "joker" in low:
        return "joker"
    return "6/49"


def _dataset_binding(fname) -> str | None:
    """Id-ul jocului legat explicit de fișier (STATE['dataset_game']), dacă există."""
    try:
        return (STATE.get("dataset_game") or {}).get(str(fname))
    except Exception:  # noqa: BLE001
        return None


def _game_spec_for(key, data=None):
    """Jocul (Lottery) al unei chei de rezultat / fișier / chei de bench.

    Ordinea: ecoul worker-ului din rezultat (`data['game_id']`), id-ul din
    registru, cheia de bench, legătura explicită a fișierului, apoi ghicitul
    vechi românesc. Un rezultat fără ecou este România."""
    if isinstance(data, dict):
        gid = data.get("game_id")
        if gid is not None:
            lot = _LOT.lottery_by_id(gid)
            if lot is not None:
                return lot
    k = str(key)
    lot = _LOT.lottery_by_id(k) or _LOT.lottery_by_bench_key(k)
    if lot is not None:
        return lot
    bound = _dataset_binding(k)
    if bound is not None:
        lot = _LOT.lottery_by_id(bound)
        if lot is not None:
            return lot
    return _LOT.LOTTERIES[_legacy_ro_label(k)]


def _game_label_for(fname: str) -> str:
    """GEOMETRIA jocului ("6/49", "5/40", "joker", "6/45", "5/50").

    Pentru România geometria coincide cu eticheta internă, deci apelanții vechi
    primesc exact ce primeau. Un id străin sau un fișier legat explicit de un
    joc străin întoarce geometria lui, niciodată identitatea românească."""
    k = str(fname)
    lot = _LOT.lottery_by_id(k)
    if lot is None:
        bound = _dataset_binding(k)
        lot = _LOT.lottery_by_id(bound) if bound is not None else None
    if lot is not None:
        return lot.geometry
    return _legacy_ro_label(k)


def _game_title(game, data=None) -> str:
    """Țara și jocul pentru titluri, rapoarte și mail: „România · Loto 6/49”."""
    return _game_spec_for(game, data).display


def _fmt_price(lot) -> str:
    """„8 Lei/variantă”; tarif neverificat → „tarif necunoscut”."""
    if lot is None or lot.price is None:
        return "tarif necunoscut"
    return f"{lot.price:g} {lot.currency}/variantă"


_TRAINING_ONLY_NOTE = "doar antrenament: nu se joacă online din România"


def _training_only_note(lot) -> str:
    """Nota „doar antrenament”, sau nota de joc (`play_note`) a unui joc care
    se joacă altfel decât online din România; gol pentru jocurile online."""
    if lot is None:
        return ""
    if getattr(lot, "play_note", None):
        return lot.play_note
    return _TRAINING_ONLY_NOTE if lot.training_only else ""


def _next_draw_date_for(weekdays=(3, 6), today=None) -> str:
    """Următoarea zi de extragere (astăzi inclusiv) pentru zilele date."""
    from datetime import date as _date

    base = today or _date.today()
    o = base.toordinal()
    for i in range(0, 8):
        d = base.fromordinal(o + i)
        if d.weekday() in tuple(weekdays):
            return d.strftime("%d-%m-%Y")
    return base.strftime("%d-%m-%Y")


_WEEKDAY_RO = ("Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică")


def _weekdays_text(weekdays) -> str:
    return "/".join(_WEEKDAY_RO[int(d)] for d in weekdays)


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

