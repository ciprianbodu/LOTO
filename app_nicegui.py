"""Front-end NiceGUI pentru Loto Enterprise Wheeling.

Înlocuiește app.py (Streamlit). Motivul migrării: modelul Streamlit re-rula tot
scriptul și — ca să detecteze terminarea bench-ului din background — injecta un
reload COMPLET de pagină (window.location.reload), care ștergea session_state și
golea uploader-ul → bug-uri recurente ("Încărcați un CSV!", bife pierdute etc.).

NiceGUI ține starea pe server și actualizează componentele prin websocket cu
`ui.timer` — fără reload, deci starea nu se mai pierde NICIODATĂ.

Backend-ul (job_queue.py SQLite, worker.py subprocess, loto_engine, tot
loto_enterprise/) e reutilizat NEATINS. Contractul config_json/result e identic
cu cel din app.py, deci worker-ul nu știe ce UI l-a chemat.

Rulare:  python app_nicegui.py   (sau: python -m app_nicegui)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path

from runtime_version import require_python_version

require_python_version()

import pandas as pd
from nicegui import app, ui

from job_queue import (
    cancel_pending_running_jobs,
    cancel_unstarted_job,
    claim_job_finalization_action,
    finish_job_finalization_action,
    get_job_finalization_actions,
    get_job_finalization_details,
    get_active_job,
    get_job_status,
    get_latest_completed_job,
    init_job_queue,
    is_fresh_ui_start,
    is_unstarted_job,
    mark_job_finalized,
    submit_job,
)
from runtime_paths import BENCH_LOG_FILE
from ui_shared import (
    PROJECT_ROOT,
    atomic_write_json,
    atomic_write_text,
    clear_logs,
    decode_queue_result,
    ensure_worker_running,
    is_worker_running,
    load_mail_config,
    mail_pool_alternative_lines,
    read_logs_filtered,
    read_tail_lines,
    render_html_safe,
    send_email,
)

logging.basicConfig(
    level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s"
)
logger = logging.getLogger("app_nicegui")

# Semnalizat imediat, inclusiv când finalizarea ține STATE_LOCK la I/O.
_WF_CANCEL_REQUESTED = threading.Event()
_VIRTUAL_WF_CANCEL_REQUESTED = threading.Event()
_VIRTUAL_WF_DECISION_CHANGED = threading.Event()

from ui_runtime import *  # noqa: F403
from ui_results import *  # noqa: F403
from ui_bench import *  # noqa: F403
from ui_hits import *  # noqa: F403
from ui_history_upload import (
    MAX_BATCH_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    UPLOAD_LIMITS,
    LimitHistoryUploadBody,
    read_history_csv,
    validate_upload_batch,
)
from ui_job_status import JobStatusProbe
from ui_history_windows import history_windows_panel
from ui_nested_wfv import nested_wfv_panel
from covering.lr_budget import FOUR_PLUS, SINGLE_SCHEME_MODE, budget_in_lei
from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN, LR_RESTRICTED_RANGE
from ui_lr_budget import (
    LR_RESTRICTED_BASE, LR_RESTRICTED_LABEL, initial_budget_amount, lr_base_choice, lr_budget_panel,
    lr_budget_slider, lr_recommendation_progress, on_budget_click,
)
from ui_lr_rebench import (
    cancel_lr_rebench, lr_rebench_notice, lr_rebench_panel, lr_rebench_progress, lr_rebench_running,
    reusable_lr_profiles, run_lr_rebench,
)
from lr_walk_forward_run import (
    cancel_lr_walk_forward, lr_walk_forward_notice, lr_walk_forward_progress, lr_walk_forward_running, run_lr_walk_forward,
)
from ui_lr_walk_forward import lr_walk_forward_panel
from ui_lr_portfolio import lr_portfolio_panel
from loto_enterprise.benchmark.chronological_validation import PROGRESS_MARKER, VALIDATION_CODES_ENV
from loto_enterprise.core import lotteries as _LOT
from loto_enterprise.core.ro_text import count, lei_text


def _get_build_timestamp() -> str:
    """Returnează data și ora ultimei compilări / modificări de cod."""
    ts = 0.0
    try:
        ts = max(ts, os.path.getmtime(__file__))
    except Exception:
        pass
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=str(PROJECT_ROOT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            ts = max(ts, float(res.stdout.strip()))
    except Exception:
        pass
    if ts > 0:
        return _dt.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")
    return ""


BUILD_TIMESTAMP = _get_build_timestamp()
APP_TITLE = (
    f"Loto Enterprise Wheeling - {BUILD_TIMESTAMP}"
    if BUILD_TIMESTAMP
    else "Loto Enterprise Wheeling"
)


def _budget_objective():
    return FOUR_PLUS


def _build_config_json(sim_depth_per_game: dict | None = None) -> str:
    sim_depth_per_game = sim_depth_per_game or {}
    h = hashlib.sha256()
    for k in (
        "pool_size_val",
        *_POOL_KEYS.values(),
        "guarantee_val",
        "max_variants_val",
        "lookback_val",
        "bench_hit_target",
        "wheel_condition_val",
    ):
        h.update(str(SETTINGS.get(k, DEFAULTS.get(k))).encode("utf-8"))
    _selected = {g.game_id for g in _selected_games()}
    h.update(f"|bench_telemetry={BENCH_TELEMETRY_DEPTH}|".encode("ascii"))
    h.update(
        str(sorted(sim_depth_per_game.items())).encode("utf-8")
    )  # adâncime per joc → cache key
    # `pure_bench` NU intră în hash: taskul emite `"pure_bench_mode": True`
    # NECONDIȚIONAT (mai jos), iar `loto_engine` scrie `audit["pure_bench_mode"] = True`
    # indiferent de argument — deci „pure" vs „normal" produce EXACT aceleași bilete.
    # Cât era în hash, cele două variante primeau chei de cache diferite pentru un
    # rezultat identic: cache ratat degeaba dacă `use_cache` ar fi True.
    datasets_cfg = []
    for fname, df in STATE["datasets"]:
        # Numai fișierele țării alese și ale jocurilor bifate intră în job.
        _spec = _game_spec_for(fname)
        if _spec.game_id not in _selected:
            continue
        # România: eticheta de dinainte ("6/49"...); străin: id-ul din registru.
        g_label = _spec.game_id
        df_json = df.to_json(orient="split")
        game_depth = sim_depth_per_game.get(g_label)
        sd = BENCH_TELEMETRY_DEPTH if game_depth is None else int(game_depth)
        task = {
            "game_label": g_label,
            # Pool-ul jocului: câmpul lui (România) sau cel comun (alte țări).
            "pool_size": _pool_size_for(_spec.game_id),
            "guarantee": _int_setting("guarantee_val"),
            "max_variants": _int_setting("max_variants_val"),
            "wheel_condition": _int_setting("wheel_condition_val"),
            "recent_penalty_draws": 0,
            "recent_penalty_factor": 0.5,
            "restrict_base_max": 0,
            "restrict_base_min": 0,
            "max_consecutive_run": LR_MAX_CONSECUTIVE_RUN if _spec.is_romanian else 0,
            "lookback": _int_setting("lookback_val"),
            "sim_depth_pct": sd,  # TELEMETRIE de bench, nu taie istoricul (vezi AGENTS.md)
            "pure_bench_mode": True,
            "bench_hit_target": _clamped_bench_target(),
        }
        if _spec.is_romanian:
            from covering.lr_selection import lr_scheme_signature

            scheme = _lr_scheme_for(g_label)
            task.update(lr_scheme_code=scheme.code, guarantee=4, wheel_condition=0,
                        max_variants=scheme.size)
            h.update(f"|lr_max_run={LR_MAX_CONSECUTIVE_RUN}|".encode("ascii"))
            if _spec.bench_key_urna2:
                joker_pool_size = min(20, max(1, _int_setting("joker_pool_size_val")))
                if joker_pool_size > 1:
                    task["joker_pool_size"] = joker_pool_size
                    h.update(f"joker_pool_size={joker_pool_size}".encode("utf-8"))
            if SETTINGS.get("dynamic_method_enabled_val", True):
                task["dynamic_method"] = True
                h.update(b"dynamic_method=1")
            h.update(lr_scheme_signature(g_label, scheme.code, scheme.n).encode("utf-8"))
        if not _spec.is_romanian:
            task["country"] = _spec.country
            h.update(f"country={_spec.country}|game_id={_spec.game_id}".encode("utf-8"))
        dataset = {"fname": fname, "df_json": df_json, "tasks": [task]}
        if _spec.is_romanian:
            amount = initial_budget_amount(SETTINGS.get("lr_budget_lei"))
            objective = _budget_objective()
            dataset["lr_mail_settings"] = {
                "lr_budget_lei": amount, "lr_budget_objective": objective, "lr_budget_mode": SINGLE_SCHEME_MODE,
            }
            h.update(f"|lr_budget={amount}:{objective}:{SINGLE_SCHEME_MODE}|".encode("ascii"))
        datasets_cfg.append(dataset)
        h.update(fname.encode("utf-8"))
        h.update(hashlib.sha256(df_json.encode("utf-8")).hexdigest().encode("ascii"))
    config = {"use_cache": False, "datasets": datasets_cfg}
    depth = _int_setting("wf_depth_pct")
    if depth != DEFAULT_REPLAY_PERCENT:
        config["wf_depth_pct"] = depth
        h.update(f"wf_depth_pct={depth}".encode("ascii"))
    return json.dumps({"input_hash": h.hexdigest(), **config})


def _queue_operation(operation, completed, failed):
    """Use the caller's client for UI feedback, and a worker thread for SQLite."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            value = operation()
        except Exception as exc:
            failed(exc)
        else:
            completed(value)
        return
    from nicegui import background_tasks
    try:
        client = ui.context.client
    except RuntimeError:
        client = None

    async def dispatch():
        try:
            value = await asyncio.to_thread(operation)
        except Exception as exc:
            callback, arg = failed, exc
        else:
            callback, arg = completed, value
        if client:
            with client:
                callback(arg)
        else:
            callback(arg)
    background_tasks.create(dispatch())


def submit_generation(
    pure: bool = False, sim_depth_per_game: dict | None = None
) -> None:
    if STATE.get("virtual_wf_running"):
        ui.notify("Analiza virtuală este în curs; așteaptă sau anulează.", type="warning")
        return
    if not STATE["datasets"]:
        ui.notify("Încărcați cel puțin un fișier CSV!", type="negative")
        return
    selected = _selected_games()
    available = {_game_spec_for(fn).game_id for fn, _ in STATE["datasets"]}
    if not any(game.game_id in available for game in selected):
        ui.notify(
            f"Niciun istoric încărcat pentru {_country_label()} (jocurile bifate). "
            f"Folosește «📂 Încarcă istoricul {_country_label()}».",
            type="negative",
        )
        return
    if STATE.get("_queue_boot_pending"):
        ui.notify("Se restabilește legătura cu istoricul rulărilor. Reîncearcă după afișarea stării.", type="warning")
        return
    if STATE["active_job_id"] or STATE.get("_submission_pending") or STATE.get("_cancellation_pending"):
        ui.notify("Există deja o cerere de generare sau de anulare în curs.", type="warning")
        return
    epoch = STATE.get("_cancellation_epoch", 0)
    STATE["_submission_pending"] = True

    def failed(exc):
        STATE["_submission_pending"] = False
        logger.error("Nu am putut înregistra cererea de generare: %s", exc)
        ui.notify("Cererea de generare nu a putut fi salvată. Rezultatul anterior rămâne disponibil.", type="negative")

    try:
        cfg = _build_config_json(sim_depth_per_game)
    except Exception as exc:
        failed(exc)
        return

    def submitted(job_id):
        STATE["_submission_pending"] = False
        if STATE.get("_cancellation_epoch", 0) != epoch:
            _queue_operation(lambda: cancel_pending_running_jobs("Oprit de utilizator", job_ids=[job_id]),
                             lambda _: None, lambda exc: logger.error("Anulare job #%s: %s", job_id, exc))
            return
        with STATE_LOCK:
            # Un walk-forward încă în rulare aparține rezultatului vechi: îl facem
            # „stale” (nu mai scrie în `retro`, nu rescrie raportul, nu oprește PC-ul).
            STATE["wf_seq"] = int(STATE.get("wf_seq") or 0) + 1
            STATE["wf_running"] = False
            STATE["pure_bench"] = pure
            STATE["results"] = None
            STATE["result_sources"] = None
            STATE["retro"] = {}
            STATE["retro_meta"] = {}
            STATE["wf_status"] = ""
            STATE["active_job_id"] = job_id
            STATE["_result_job_id"] = None
            STATE["_job_notice"] = None
            STATE["job_start_time"] = time.time()
            STATE["job_elapsed"] = None
            STATE["wf_elapsed"] = None
        ensure_worker_running()
        missing = [game.name for game in selected if game.game_id not in available]
        if missing:
            ui.notify("Jocuri omise din generare pentru că lipsește istoricul: " + ", ".join(missing),
                      type="warning", timeout=8000)
        ui.notify(f"Job #{job_id} trimis.", type="positive")
        _refresh_status()

    _queue_operation(lambda: int(submit_job("pipeline", cfg)), submitted, failed)


def _autopilot_evidence_note(config: dict) -> str:
    if config.get("low_confidence"):
        return " · low_confidence"
    evidence = config.get("multiplicity") or {}
    if evidence.get("proven") is False:
        correction = " (Holm)" if evidence.get("correction") in (None, "", "holm") else ""
        return f" · avantaj nedemonstrat{correction}"
    return ""


def apply_autopilot_and_generate() -> None:
    """Arată selecția aplicabilă pe joc și pornește generarea cu setările curente."""
    # best_methods.json folosește CHEIA jocului (loto_6_49 ...), nu eticheta scurtă
    # (6/49) întoarsă de _game_label_for → altfel lookup-ul eșua mereu → fallback.
    _LABEL_TO_KEY = _LABEL_TO_FOLDS_GAME
    per_game: dict = {}  # {game_label: sim_depth_pct} — telemetrie per joc, nu filtru
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config

        recs = []
        _selected = {g.game_id for g in _selected_games()}
        for fname, _ in STATE["datasets"]:
            _spec = _game_spec_for(fname)
            if _spec.game_id not in _selected:
                continue
            if _spec.is_romanian:
                label = _game_label_for(fname)
                gk = _LABEL_TO_KEY.get(label, "loto_6_49")
                pool_size = _pool_size_for(_spec.game_id)
                if SETTINGS.get("dynamic_method_enabled_val", True):
                    recs.append(
                        f"{gk} · selecție dinamică după 4+ pe o variantă LR. "
                        f"Pool de {pool_size} numere."
                    )
                    cfg = None
                else:
                    cfg = recommend_optimal_config(gk, pool_size)
            else:
                # Decizia țării ei; fără bench → frequency (fallback), spus explicit.
                label = _spec.game_id
                gk = _spec.bench_key
                from loto_enterprise.core.method_selector import decision_path_for

                cfg = recommend_optimal_config(
                    gk,
                    _pool_size_for(_spec.game_id),
                    config_path=str(decision_path_for(_spec.country)),
                )
                if not cfg or cfg.get("fallback"):
                    recs.append(
                        f"{_spec.display}: fără decizie bench pentru "
                        f"{_spec.country_name} — frequency"
                    )
                    continue
            if cfg and not cfg.get("fallback"):
                sd = int(cfg.get("sim_depth_pct", BENCH_TELEMETRY_DEPTH))
                per_game[label] = sd
                low = _autopilot_evidence_note(cfg)
                # Substituirea nearest-k: bench-ul n-a evaluat pool-ul cerut, deci
                # scorer-ul (și cifrele lui) vin de la ALT pool. Până acum se vedea
                # doar ca linie INFO în loto.log, iar notificarea îl prezenta ca și
                # cum ar fi fost măsurat la pool-ul de pe slider.
                _sub = cfg.get("pool_substituted") or {}
                sub = (
                    f" · ⚠ măsurat la pool {_sub['used']}, nu {_sub['requested']}"
                    if _sub
                    else ""
                )
                recs.append(f"{gk}: {cfg.get('scorer')}{low}{sub}")
            # Numărul Joker are decizia lui (Urna 2, top-1), cu nota ei.
            if _spec.is_romanian and _spec.geometry == "joker":
                gk2 = _spec.bench_key_urna2 or f"{gk}_urna2"
                cfg2 = recommend_optimal_config(gk2, 1)
                if cfg2 and not cfg2.get("fallback"):
                    recs.append(f"{gk2}: {cfg2.get('scorer')}{_autopilot_evidence_note(cfg2)}")
        if recs:
            ui.notify(
                "Auto-Pilot · " + " | ".join(recs),
                type="info",
            )
        else:
            ui.notify(
                "Fără decizie bench încă — rulează un Re-Bench întâi. Folosesc setările curente.",
                type="warning",
            )
    except Exception as exc:  # noqa: BLE001
        ui.notify(
            f"Auto-Pilot indisponibil ({exc}); folosesc setările curente.",
            type="warning",
        )
    submit_generation(pure=False, sim_depth_per_game=per_game)


# --------------------------------------------------------------------------- #
# Bench (subprocess) + status
# --------------------------------------------------------------------------- #
def _bench_process_identity(proc) -> bool | None:
    """Acceptă numai un interpret Python care execută scriptul acestui proiect.

    Argumentele de după alt script, `-c` sau `-m` nu identifică programul
    executat. La date inaccesibile nu se autorizează oprirea procesului.
    """
    try:
        import psutil
        if proc.status() == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
            return False
        name = str(proc.name() or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not re.fullmatch(r"python(?:w|3(?:\.\d+)?)?(?:\.exe)?", name):
            return False
        args = list(proc.cmdline() or [])
        if len(args) < 2:
            return False
        exe = str(args[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not re.fullmatch(r"python(?:w|3(?:\.\d+)?)?(?:\.exe)?", exe):
            return False
        index = 1
        # Numai opțiuni fără valori și fără -c/-m. Opțiunile necunoscute
        # se resping, nu se interpretează prin presupuneri.
        while index < len(args) and str(args[index]).startswith("-"):
            arg = str(args[index])
            if arg == "--":
                index += 1
                break
            if not re.fullmatch(r"-[bBdEhiIOPqRsSuvVx]+", arg):
                return False
            index += 1
        if index >= len(args):
            return False
        script = Path(str(args[index]))
        if not script.is_absolute():
            script = Path(proc.cwd()) / script
        return os.path.normcase(str(script.resolve())) == os.path.normcase(
            str((Path(PROJECT_ROOT) / "bench_all_methods.py").resolve())
        )
    except Exception as exc:  # noqa: BLE001 — proces dispărut sau inaccesibil
        import psutil
        if isinstance(exc, (psutil.NoSuchProcess, psutil.ZombieProcess)):
            return False
        return None


def _is_project_bench_process(proc) -> bool:
    """Oprirea cere identitate pozitivă, nu numai lipsa dovezii contrare."""
    return _bench_process_identity(proc) is True


def _bench_pid_status() -> tuple[int | None, bool]:
    """PID-ul și dacă identitatea a putut fi confirmată pozitiv.

    `psutil.pid_exists()` singur nu ajunge: nimic nu șterge `.bench_pid` la
    terminarea normală a bench-ului, deci fișierul supraviețuiește cu un PID mort,
    iar Windows reciclează PID-urile. Un proces străin care nimerea acel PID
    bloca la nesfârșit „Un bench rulează deja.", ascundea panoul de rezultate, iar
    la ieșirea lui `_tick` declanșa `_on_bench_finished()` → o generare Auto-Pilot
    NECERUTĂ (și, cu shutdown-ul bifat, o oprire a PC-ului). Aceeași verificare
    apără și `cancel_all()`: fără ea, un `kill_pid_tree(pid)` pe un PID reciclat
    ar omorî tot arborele procesului străin, nu doar l-ar raporta greșit ca „bench".

    Verificăm identitatea, nu doar existența: `create_time` față de timestamp-ul
    scris la lansare (`pid|ts`) și `bench_all_methods.py` în linia de comandă.
    Fișierul stale e șters; la AccessDenied rămâne, pentru a bloca un al doilea
    bench. Oprirea verifică separat identitatea pozitivă.
    """
    if not BENCH_PID_FILE.exists():
        return None, False
    parts = []
    try:
        import psutil

        parts = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")
        pid = int(parts[0])
        started = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        if (len(parts) >= 4 and re.fullmatch(r"[0-9a-f]{32}", parts[2])
                and re.fullmatch(r"[0-9a-f]{64}", parts[3])
                and STATE.get("_bench_seen_run") != parts[2]):
            from bench_outcome import is_claimed
            if is_claimed(parts[2]):
                return None, False
            STATE["_bench_run_identity"] = (pid, parts[2], parts[3])
            if "ctx1" in parts[4:]:
                STATE["_bench_context_required"] = parts[2]
            STATE["bench_was_running"] = True
            if parts[-1] == "cancelled":
                STATE["bench_cancelled"] = True
        proc = psutil.Process(pid)  # NoSuchProcess → stale
        if proc.status() == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
            raise psutil.NoSuchProcess(pid)
        # Identitate: fereastră generoasă (ceasul de pornire poate diferi cu
        # câteva secunde de `int(time.time())` scris de noi).
        if started is not None and abs(proc.create_time() - started) > 60:
            raise psutil.NoSuchProcess(pid)
        identity = _bench_process_identity(proc)
        if identity is False:
            raise psutil.NoSuchProcess(pid)
        return pid, identity is True
    except psutil.AccessDenied:
        # Stare necunoscută: păstrăm markerul și blocăm un al doilea bench;
        # cancel_all verifică separat identitatea pozitivă înainte de kill.
        return pid, False
    except Exception:  # noqa: BLE001
        # PID mort / reciclat / fișier corupt → curățăm ca să nu blocheze UI-ul.
        try:
            # Păstrăm identitatea unei rulări cunoscute până la claim, inclusiv
            # dacă UI se repornește din nou înainte să încarce sursele originale.
            if not (len(parts) >= 4 and re.fullmatch(r"[0-9a-f]{32}", parts[2])):
                BENCH_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return None, False


def _verified_bench_pid() -> int | None:
    # PID necunoscut = ocupat pentru starea UI; nu autorizează oprirea.
    return _bench_pid_status()[0]


def _bench_cancel_marker() -> bool:
    try:
        return BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")[-1] == "cancelled"
    except (OSError, UnicodeError):
        return False


def _mark_bench_cancelled() -> bool:
    """Păstrează prefixul pid|timestamp și adaugă anularea peste repornire."""
    try:
        if not BENCH_PID_FILE.exists():
            return True
        raw = BENCH_PID_FILE.read_text(encoding="utf-8").strip()
        if not raw.split("|")[0].isdigit():
            return False
        if raw.split("|")[-1] != "cancelled":
            atomic_write_text(BENCH_PID_FILE, raw + "|cancelled")
        return True
    except (OSError, UnicodeError) as exc:
        logger.warning("[BENCH] marcajul anulării nu a putut fi salvat: %s", exc)
        return False


def _bench_survivor_identity(pid: int, started: float | None) -> bool | None:
    """Confirmă același bench; None păstrează garda când accesul e refuzat."""
    import psutil
    try:
        proc = psutil.Process(pid)
        if started is not None and proc.create_time() != started:
            return False
        return _bench_process_identity(proc)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except (psutil.Error, OSError):
        return None


def _bench_running() -> bool:
    if _verified_bench_pid() is not None:
        if _bench_cancel_marker():
            STATE["bench_cancelled"] = True
        return True
    pending = STATE.get("_bench_survivors") or []
    alive = [(pid, started) for pid, started in pending
             if _bench_survivor_identity(pid, started) is not False]
    STATE["_bench_survivors"] = alive
    return bool(alive)


def _launch_bench(args: list[str], label: str, *, lr_workflow=False) -> subprocess.Popen | None:
    if STATE.get("virtual_wf_running") or STATE.get("wf_running") or STATE.get("active_job_id"):
        ui.notify("O generare sau validare este în curs; așteaptă sau anulează.", type="warning")
        return
    if _bench_running():
        ui.notify("Un bench rulează deja.", type="warning")
        return
    py = sys.executable
    cmd = [py, "bench_all_methods.py"] + args
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # Bench exclusiv CPU (GPU eliminat complet din aplicație).
    env = dict(os.environ)
    env["LOTO_BENCH_TARGET"] = str(_clamped_bench_target())
    run_id = uuid.uuid4().hex
    env["LOTO_BENCH_RUN_ID"] = run_id
    env["LOTO_BENCH_UI_PID"] = str(os.getpid())
    try:
        from bench_outcome import save_context
        config = _bench_launch_history_config()
        if lr_workflow:
            captured = json.loads(config)
            captured["after_general"] = "lr_recommendation"
            config = json.dumps(captured)
        pools = {g.game_id: _pool_size_for(g.game_id) for g in _LOT.GAMES}
        lr_codes = json.loads(config).get("rebench_lr_codes", {})
        env[VALIDATION_CODES_ENV] = json.dumps({
            key: lr_codes.get(lot.game_id) if key == lot.bench_key else None
            for lot in _LOT.games_for_country(_selected_country())
            for key in lot.bench_keys
        })
        config_digest = save_context(run_id, config, pools)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BENCH] Identitatea surselor nu poate fi fixată: %s", exc)
        ui.notify("Nu pot salva setările Re-Bench; încearcă din nou.", type="negative")
        return
    try:
        # bench_all_methods.py își scrie SINGUR bench_full.log (FileHandler) → nu
        # mai redirectăm stdout aici (altfel doi writeri pe același fișier). Logul
        # există acum și pe Windows, vizibil în consola DEBUG.
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT), creationflags=flags, env=env
        )
        atomic_write_text(BENCH_PID_FILE, f"{proc.pid}|{int(time.time())}|{run_id}|{config_digest}|ctx1")
        STATE["_bench_run_identity"] = (proc.pid, run_id, config_digest)
        STATE["_bench_context_required"] = run_id
        STATE["_bench_general_lr_id"] = run_id if lr_workflow else None
        try:
            from rebench_history_store import mark_launch
            mark_launch(run_id)
            STATE["virtual_wf_store_error"] = ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WF] Nu pot marca lansarea în istoricul local: %s", exc)
            STATE["virtual_wf_store_error"] = "Istoricul nou nu poate fi salvat local; vezi jurnalul."
        # Un istoric afișat înainte de acest Re-Bench este al rulării precedente.
        STATE["virtual_wf_previous"] = bool(STATE.get("virtual_wf_results"))
        STATE["virtual_wf_dirty"] = True
        # Numai o pornire nouă cerută de utilizator ridică anularea anterioară.
        STATE["bench_cancelled"] = False
        STATE.pop("_bench_waiting_sources", None)
        _VIRTUAL_WF_CANCEL_REQUESTED.clear()
        STATE["_bench_process"] = proc
        STATE["bench_was_running"] = True
        ui.notify(f"{label} pornit (PID {proc.pid}).", type="positive")
    except Exception as exc:  # noqa: BLE001
        try:
            from bench_outcome import clear_context
            clear_context(run_id)
        except OSError:
            pass
        proc = None
        ui.notify(f"Nu pot porni bench-ul: {exc}", type="negative")
    _refresh_status()
    return proc


_PCTS = ",".join(map(str, VALIDATION_PERCENTILES))


def _log_notify(message, type: str = "info", **_kw) -> None:  # noqa: A002
    """`ui.notify` fără pagină (pornirea UI-ului): mesajul ajunge în log."""
    log = logger.info if type in ("info", "positive") else logger.warning
    log("[decizie] %s", message)


def _rebuild_decision_for_target(target: int, notify=None) -> bool:
    """Recalculează decizia de producție pentru ținta selectată (3+ până la 6+).

    Refuză un folds.csv care nu acoperă Re-Bench-ul deciziei (bench oprit,
    `--quick`, flush parțial): decizia rămâne cea veche, cu avertisment.
    `notify` înlocuiește `ui.notify` acolo unde nu există pagină (pornirea).
    """
    notify = notify or ui.notify
    try:
        import loto_enterprise.benchmark.decision as decision

        _cc = _selected_country()
        if _cc != _LOT.RO:
            # Altă țară: decizia și folds-ul ei, nu fișierele României.
            _cname = _LOT.country_name(_cc)
            _dp = _LOT.decision_path_for(_cc, PROJECT_ROOT)
            _fp = _LOT.bench_out_dir_for(_cc, PROJECT_ROOT) / "folds.csv"
            if not (_fp.exists() and _dp.exists()):
                notify(
                    f"{_cname} nu are încă bench — rulează un Re-Bench "
                    "ca ținta să fie aplicată.",
                    type="warning",
                )
                return
            decision.update_best_methods_with_auto_pilot(
                str(_dp), str(_fp), require_complete=True
            )
            _mismatch = False
            try:
                from loto_enterprise.core.method_selector import (
                    recommend_optimal_config,
                )

                for _g in _LOT.games_for_country(_cc):
                    for _gk in _g.bench_keys:
                        if _gk == _g.bench_key_urna2:
                            continue
                        _c = recommend_optimal_config(
                            _gk,
                            _pool_size_for(_g.game_id),
                            config_path=str(_dp),
                        )
                        if _c.get("rate_col_mismatch"):
                            _mismatch = True
            except Exception:  # noqa: BLE001
                pass
            if _mismatch:
                notify(
                    f"Decizie {_cname} actualizată, DAR folds nu au coloane "
                    f"{target}+ — ținta nu este validată (rate_col_mismatch). "
                    "Rulează Re-Bench.",
                    type="warning",
                )
            else:
                notify(
                    f"Decizia Auto-Pilot {_cname} a fost actualizată pentru "
                    f"{target}+ hits!",
                    type="info",
                )
            _refresh_status()
            results_panel.refresh()
            return not _mismatch
        if (PROJECT_ROOT / "bench_results" / "folds.csv").exists():
            decision.update_best_methods_with_auto_pilot(require_complete=True)
            _mismatch = False
            try:
                from loto_enterprise.core.method_selector import (
                    recommend_optimal_config,
                )

                for _gk in ("loto_6_49", "loto_5_40", "joker_urna1"):
                    _c = recommend_optimal_config(_gk, _pool_size_for(_gk))
                    if _c.get("rate_col_mismatch"):
                        _mismatch = True
                        break
            except Exception:  # noqa: BLE001
                pass
            if _mismatch:
                notify(
                    f"Decizie actualizată, DAR folds nu au coloane {target}+ "
                    f"— ținta nu este validată (rate_col_mismatch). Rulează Re-Bench.",
                    type="warning",
                )
            else:
                notify(
                    f"Decizia Auto-Pilot a fost actualizată pentru {target}+ hits!",
                    type="info",
                )
            _refresh_status()
            results_panel.refresh()
            return not _mismatch
    except FileNotFoundError:
        # best_methods.json e gitignored: pe un clone proaspăt (sau după
        # ștergere) nu există, iar `update_best_methods_with_auto_pilot`
        # aruncă ÎNAINTE de `ui.notify`/`_refresh_status`, deci utilizatorul
        # schimba ținta și nu vedea absolut nimic — nici succes, nici
        # eroare — deși decizia NU fusese recalculată.
        notify(
            "Nu există încă best_methods.json — rulează un Re-Bench "
            "ca ținta să fie aplicată.",
            type="warning",
        )
    except Exception as exc:  # noqa: BLE001
        from loto_enterprise.benchmark.decision import IncompleteFoldsError

        if isinstance(exc, IncompleteFoldsError):
            logger.warning("Decizie nerecalculată: %s", exc)
            notify(
                "folds.csv nu acoperă ultimul Re-Bench complet sau lipsesc ratele "
                "țintei selectate: decizia de producție rămâne neschimbată. Rulează "
                f"un Re-Bench complet ca ținta {target}+ să fie aplicată.",
                type="warning",
            )
        else:
            logger.warning("Eroare la schimbarea țintei de hituri: %s", exc)
            notify(f"Nu am putut recalcula decizia: {exc}", type="negative")
    finally:
        _bench_freshness_panel.refresh()
    return False


def _bench_launch_history_config() -> str:
    """Capture even the LR selections for games whose CSV is loaded after launch."""
    config = json.loads(_build_config_json())
    config["rebench_lr_codes"] = {
        game.game_id: _lr_scheme_for(game.game_id).code
        for game in _LOT.games_for_country(_LOT.RO)
    }
    return json.dumps(config)


def _bench_history_config(context: tuple[str, dict[str, int]]) -> str:
    """Sursele inițiale și poolurile de la lansare, cu jocurile adăugate ulterior."""
    original, pools = context
    current_text = _build_config_json()
    current = json.loads(current_text)
    initial = json.loads(original)
    selected = {g.game_id for g in _selected_games()}
    original_by_game = {}
    original_lr_codes = dict(initial.get("rebench_lr_codes", {}))
    original_dynamic = {}
    for dataset in initial.get("datasets", []):
        for task in dataset.get("tasks", []):
            original_by_game.setdefault(task.get("game_label"), []).append(dataset)
            original_dynamic[task["game_label"]] = task.get("lr_scheme_code") is not None and task.get("dynamic_method") is True
            if task.get("lr_scheme_code") is not None:
                gid = task["game_label"]
                if original_lr_codes.setdefault(gid, task["lr_scheme_code"]) != task["lr_scheme_code"]:
                    raise ValueError(f"{gid}: coduri LR diferite în configurația de la lansare.")
    merged = []
    for dataset in current.get("datasets", []):
        tasks = dataset.get("tasks", [])
        gid = tasks[0].get("game_label") if tasks else None
        if gid in original_by_game:
            continue
        merged.append({**dataset, "tasks": [dict(task) for task in tasks]})
    for gid, datasets in original_by_game.items():
        if gid in selected:
            # Sursa, poolul și codul LR provin de la lansare.
            template = next((d["tasks"][0] for d in current.get("datasets", [])
                             if d.get("tasks") and d["tasks"][0].get("game_label") == gid), None)
            for dataset in datasets:
                ds = dict(dataset)
                ds["tasks"] = [dict(template or task) for task in dataset.get("tasks", [])]
                if "lr_mail_settings" not in ds and len(dataset.get("tasks", [])) == 1:
                    # Previously queued jobs stored the mail budget inside the worker task.
                    original_task = dataset["tasks"][0]
                    legacy = {key: original_task[key] for key in ("lr_budget_lei", "lr_budget_objective") if key in original_task}
                    if legacy:
                        ds["lr_mail_settings"] = legacy
                for task in ds["tasks"]:
                    task.pop("lr_budget_lei", None)
                    task.pop("lr_budget_objective", None)
                    task["bench_hit_target"] = _clamped_bench_target()
                merged.append(ds)
    for dataset in merged:
        for task in dataset.get("tasks", []):
            gid = task.get("game_label")
            if gid in pools:
                task["pool_size"] = pools[gid]
            if gid in original_lr_codes or task.get("lr_scheme_code") is not None:
                from covering.lr_selection import resolve_lr_scheme, select_lr_scheme

                if gid in original_dynamic:
                    if original_dynamic[gid]:
                        task["dynamic_method"] = True
                    else:
                        task.pop("dynamic_method", None)
                if gid in original_lr_codes:
                    scheme = resolve_lr_scheme(gid, original_lr_codes[gid], task["pool_size"])
                else:
                    # Legacy receipts for later-added games persisted only the pool.
                    scheme = select_lr_scheme(gid, task["pool_size"], policy="cost")
                task.update(lr_scheme_code=scheme.code, max_variants=scheme.size,
                            guarantee=4, wheel_condition=0)
    depth = initial.get("wf_depth_pct", DEFAULT_REPLAY_PERCENT)
    if merged == current.get("datasets") and depth == current.get("wf_depth_pct", DEFAULT_REPLAY_PERCENT):
        return current_text
    current["datasets"] = merged
    current.pop("wf_depth_pct", None)
    if depth != DEFAULT_REPLAY_PERCENT:
        current["wf_depth_pct"] = depth
    # Include the launch depth even if the selection changed during Re-Bench.
    payload = [merged, depth] if depth != DEFAULT_REPLAY_PERCENT else merged
    current["input_hash"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return json.dumps(current)


def _on_bench_finished(*, context=None, config_json=None, lr_workflow=False):
    """Actualizează starea bench-ului și pornește Auto-Pilot dacă e bifat."""
    if STATE.pop("bench_target_pending", False) or _decision_target_mismatch():
        # Ținta s-a schimbat cât rula bench-ul, care și-a scris decizia cu
        # ținta de la pornire. Acum folds.csv e complet. Fără marcaj (UI-ul a
        # fost repornit cât rula bench-ul), decizia scrisă pe altă țintă decât
        # cea selectată e singurul semn.
        if not _rebuild_decision_for_target(_clamped_bench_target()):
            logger.warning("[BENCH] Decizia nu a putut fi refăcută; istoricul automat nu pornește.")
            return
    _bench_freshness_panel.refresh()
    if _decision_target_mismatch():
        logger.warning("[BENCH] Decizia nu corespunde țintei selectate; istoricul automat și Auto-Pilot nu pornesc.")
        ui.notify("Decizia nu corespunde țintei selectate; istoricul automat nu a pornit.", type="warning")
        return
    # Completează jocurile selectate, inclusiv înaintea unei amânări.
    _load_missing_bench_histories()
    try:
        cfg_text = config_json if config_json is not None else (_bench_history_config(context) if context is not None else _build_config_json())
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("[BENCH] Nu pot pregăti istoricul automat: %s", exc)
        return
    if STATE.get("wf_running") or STATE.get("active_job_id"):
        # Un WF început înainte de bench nu poate fi suprascris. Așteptăm
        # finalizarea lui și verificăm din nou datele și decizia.
        try:
            cfg = json.loads(cfg_text)
            countries = _history_decision_keys((task for ds in cfg.get("datasets", []) for task in ds.get("tasks", [])), simulated=True)
            STATE["_bench_history_pending"] = (cfg_text, countries, _virtual_decision_fingerprint(countries))
            STATE["_bench_history_snapshot"] = context is not None or config_json is not None
            STATE["_bench_history_lr_workflow"] = lr_workflow
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[BENCH] Istoricul automat nu poate fi planificat: %s", exc)
        return
    autopilot = not lr_workflow and bool(SETTINGS.get("autopilot_after_bench") and STATE.get("datasets"))
    if autopilot and (context is not None or config_json is not None) and cfg_text != _build_config_json():
        # Nu genera bilete pe alte setări decât cele validate.
        autopilot = False
        ui.notify("Setările s-au schimbat; istoricul folosește poolul de la Re-Bench, fără generare automată.", type="warning")
    # Sistemul complet virtual se evaluează chiar și atunci când sistemul fizic
    # este prea mare pentru generator; o decizie de bench nouă este obligatorie.
    try:
        cfg = json.loads(cfg_text)
        tasks = [task for ds in cfg.get("datasets", []) for task in ds.get("tasks", [])]
        full = bool(tasks) and all(
            task.get("lr_scheme_code") is None and min(max(3, int(task["guarantee"])),
                _LOT.require_lottery(task["game_label"], task.get("country", _LOT.RO)).pick_n)
            == _LOT.require_lottery(task["game_label"], task.get("country", _LOT.RO)).pick_n
            and int(task.get("max_variants") or 0) == 0 for task in tasks
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BENCH] Nu pot pregăti istoricul automat: %s", exc)
        return
    from covering.large_pool import LEGACY_MAX_POOL, validate_large_pool_request
    from loto_enterprise.core.walk_forward_adapter import _effective_pool_size, _wf_geometry
    physical_errors = []
    for task in tasks:
        spec = _LOT.require_lottery(task["game_label"], task.get("country", _LOT.RO))
        try:
            pool = _effective_pool_size(int(task["pool_size"]), spec.geometry,
                                        task.get("restrict_base_max", 0), task.get("restrict_base_min", 0))
            guarantee, condition, cap = _wf_geometry(pool, spec.geometry, task["guarantee"],
                                                       task.get("wheel_condition"), task.get("max_variants"))
            if pool > LEGACY_MAX_POOL:
                validate_large_pool_request(pool, spec.pick_n, guarantee, cap, condition)
        except (ValueError, TypeError, OverflowError) as exc:
            physical_errors.append(f"{spec.name}: {exc}")
    STATE["_virtual_physical_notice"] = (
        "Sistemul este prea mare sau invalid pentru generare: " + " | ".join(physical_errors) if physical_errors else ""
    )
    if physical_errors and autopilot:
        ui.notify(STATE["_virtual_physical_notice"], type="warning")
    official_lr = any(task.get("lr_scheme_code") is not None for task in tasks)
    if (full or official_lr) and not STATE.get("active_job_id"):
        # Istoricul schemei LR sau al sistemului complet precedă generarea.
        # Cererile prea mari pentru generator pot folosi analiza virtuală.
        extra = {"config_json": cfg_text} if context is not None or config_json is not None else {}
        if _start_virtual_history(auto_generate_config=cfg_text if autopilot and not physical_errors else None,
                                  automatic=True, simulated=True, materialize_full=True, **extra):
            return True
        if physical_errors or official_lr:
            return True
    if not full and (not autopilot or physical_errors) and not STATE.get("active_job_id"):
        extra = {"config_json": cfg_text} if context is not None or config_json is not None else {}
        _start_virtual_history(automatic=True, simulated=True, materialize_full=True, **extra)
        return True
    if autopilot and not STATE.get("active_job_id"):
        ui.notify("✅ Re-Bench terminat → pornesc Auto-Pilot automat.", type="positive")
        apply_autopilot_and_generate()
    return True


def _clear_bench_pid_if_matches(identity) -> None:
    """Retire the receipt; do not delete a PID file another UI can replace."""
    try:
        from bench_outcome import mark_processed, clear_context
        mark_processed(identity[1])
        clear_context(identity[1])
    except OSError as exc:
        logger.warning("[BENCH] Nu pot marca rezultatul ca preluat: %s", exc)


def _resume_bench_waiting_sources() -> None:
    identity = STATE.get("_bench_waiting_sources")
    if not identity or STATE.get("bench_cancelled") or _VIRTUAL_WF_CANCEL_REQUESTED.is_set():
        return
    pid, run_id, config_digest = identity
    try:
        from bench_outcome import peek_exit, claim_exit
        code = peek_exit(run_id, pid)
        if code is None:
            STATE.pop("_bench_waiting_sources", None)
            _clear_bench_pid_if_matches(identity)
            STATE["virtual_wf_status"] = "Re-Bench s-a încheiat prea demult sau rezultatul nu mai poate fi confirmat; rulează din nou."
            STATE["virtual_wf_dirty"] = True
            return
        if config_digest != hashlib.sha256(_build_config_json().encode("utf-8")).hexdigest():
            return
        if code == 0 and claim_exit(run_id, pid) == 0:
            STATE.pop("_bench_waiting_sources", None)
            _clear_bench_pid_if_matches(identity)
            _on_bench_finished()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BENCH] Așteptarea surselor: %s", exc)


def _observe_bench_activity(bench_now: bool) -> None:
    """Detectează terminarea fără a revoca anularea unui proces încă viu."""
    if STATE.get("bench_was_running") and not bench_now:
        STATE["bench_was_running"] = False
        process = STATE.pop("_bench_process", None)
        identity = STATE.pop("_bench_run_identity", None)
        rc = None
        failure_reason = None
        lr_workflow = False
        if identity is not None:
            pid, run_id, config_digest = identity
            if process is not None and process.poll() is None:
                # The receipt can be written just before the Windows launcher
                # exits. Wait for its exit code without consuming the receipt.
                STATE["_bench_process"] = process
                STATE["_bench_run_identity"] = identity
                STATE["bench_was_running"] = True
                return
            STATE["_bench_seen_run"] = run_id
            try:
                from bench_outcome import claim_exit, peek_exit, load_context
                code = peek_exit(run_id, pid)
                context = load_context(run_id, config_digest)
                lr_workflow = bool(context and json.loads(context[0]).get("after_general") == "lr_recommendation")
                if lr_workflow and process is None:
                    # Another UI owns the continuation. A dead owner releases its kernel lock.
                    try:
                        with _general_lr_lock():
                            pass
                    except TimeoutError:
                        STATE.pop("_bench_seen_run", None)
                        return
                try:
                    marker = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")
                    requires_context = (len(marker) >= 5 and marker[2] == run_id and "ctx1" in marker[4:])
                    if requires_context and marker[-1] == "cancelled":
                        STATE["bench_cancelled"] = True
                except (OSError, UnicodeError):
                    requires_context = False
                requires_context = requires_context or STATE.get("_bench_context_required") == run_id
                if requires_context and context is None:
                    logger.warning("[BENCH] Setările de la lansare lipsesc sau sunt modificate; istoricul nu pornește.")
                    failure_reason = "Setările Re-Bench nu mai sunt disponibile; rulează din nou."
                    STATE["virtual_wf_status"] = failure_reason
                    STATE["virtual_wf_dirty"] = True
                    code = None
                if process is not None and process.poll() != 0:
                    code = None
                if process is None and code == 0 and not STATE.get("bench_cancelled") and context is None:
                    current = hashlib.sha256(_build_config_json().encode("utf-8")).hexdigest()
                    if config_digest != current:
                        STATE["_bench_waiting_sources"] = identity
                        STATE["virtual_wf_status"] = "Re-Bench este gata; încarcă istoricele și setările folosite la pornire pentru calculul automat."
                        STATE["analysis_expanded"] = True
                        STATE["virtual_wf_dirty"] = True
                        return
                if code is not None:
                    rc = claim_exit(run_id, pid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BENCH] Verificarea rezultatului a eșuat: %s", exc)
            _clear_bench_pid_if_matches(identity)
        if not STATE.get("bench_cancelled") and rc == 0:
            if lr_workflow:
                if _on_bench_finished(context=context, lr_workflow=True):
                    STATE["_general_lr_completed"] = run_id
            elif context is None:
                _on_bench_finished()
            else:
                _on_bench_finished(context=context)
        elif not STATE.get("bench_cancelled"):
            logger.warning("[BENCH] Încheierea cu succes nu a putut fi confirmată (cod %s); nu pornesc istoricul sau Auto-Pilot.", rc)
            STATE["virtual_wf_status"] = failure_reason or "Re-Bench nu a confirmat finalizarea cu succes sau datele originale nu corespund; istoricul automat nu a pornit. Vezi jurnalul."
            STATE["analysis_expanded"] = True
            STATE["virtual_wf_dirty"] = True
            ui.notify(STATE["virtual_wf_status"], type="warning")
    elif bench_now:
        STATE["bench_was_running"] = True


def _continue_after_automatic_history(bench_now: bool) -> None:
    if not (bench_now or STATE.get("active_job_id") or STATE.get("wf_running") or STATE.get("virtual_wf_running")):
        _resume_bench_waiting_sources()
    # Continuarea după analiza virtuală rulează numai pe event-loop și
    # numai pe aceleași setări și date care au pornit analiza.
    with STATE_LOCK:
        waiting = STATE.get("_bench_history_pending")
        if waiting and not (bench_now or STATE.get("active_job_id") or STATE.get("wf_running") or STATE.get("virtual_wf_running")):
            STATE.pop("_bench_history_pending", None)
            is_snapshot = STATE.pop("_bench_history_snapshot", False)
            lr_workflow = STATE.pop("_bench_history_lr_workflow", False)
        else:
            waiting = None
            is_snapshot = False
        pending = STATE.pop("_autopilot_after_virtual", None)
    if waiting is not None:
        config, countries, decision = waiting
        try:
            unchanged = (is_snapshot or config == _build_config_json()) and decision == _virtual_decision_fingerprint(countries)
        except Exception:  # noqa: BLE001
            unchanged = False
        if unchanged and not STATE.get("bench_cancelled") and not _VIRTUAL_WF_CANCEL_REQUESTED.is_set():
            if lr_workflow:
                _on_bench_finished(config_json=config, lr_workflow=True)
            elif is_snapshot:
                _on_bench_finished(config_json=config)
            else:
                _on_bench_finished()
        else:
            ui.notify("Istoricul automat nu a pornit: anulare, setări sau decizie schimbate.", type="warning")
    if pending is not None:
        config, countries, decision = pending
        try:
            unchanged = config == _build_config_json() and decision == _virtual_decision_fingerprint(countries)
        except Exception:  # noqa: BLE001
            unchanged = False
        if unchanged and not _VIRTUAL_WF_CANCEL_REQUESTED.is_set() and not STATE.get("bench_cancelled") and not (bench_now or STATE.get("active_job_id") or STATE.get("wf_running") or STATE.get("virtual_wf_running")):
            apply_autopilot_and_generate()
        else:
            ui.notify("Auto-Pilot nu a pornit: anulare, setări sau decizie schimbate.", type="warning")


def _bench_status_text() -> str:
    if STATE.get("bench_cancelled"):
        return "Anulare solicitată; oprirea bench-ului nu este confirmată. Auto-Pilot nu va porni la final."
    if STATE.get("_bench_general_lr_id"):
        return "Re-Bench general în curs. După finalizare se evaluează selecția LR pentru bilet."
    return "Testez toate metodele (CPU, pe toate nucleele). Auto-Pilot pornește la final."


def _reconcile_decision_target() -> None:
    """La pornire: decizia salvată pe altă țintă decât cea selectată se recalculează.

    Un bench pornit din consolă cu altă țintă, o schimbare de țintă refuzată
    (folds.csv incomplet) sau UI-ul oprit cât rula un bench lăsau decizia pe
    3+ cu 4+ selectat, fără niciun semn. Cu un bench în curs, recalcularea vine
    la finalul lui (`_on_bench_finished`); pe un folds.csv incomplet decizia
    rămâne, iar panoul de prospețime o spune până la un Re-Bench complet.
    """
    stale = _decision_target_mismatch()
    if not stale:
        return
    target = _clamped_bench_target()
    logger.warning(
        "[STARTUP] decizia salvată e pe altă țintă decât %s+ (%s).",
        target,
        ", ".join(f"{gk}={t}+" for gk, t in stale.items()),
    )
    if _bench_running():
        STATE["bench_target_pending"] = True
        return
    _rebuild_decision_for_target(target, notify=_log_notify)


def _after_server_start(fn) -> None:
    """`fn` pe bucla UI imediat după pornire, nu în lifespan.

    Recalcularea deciziei durează ~10 s; în `on_startup` ținea portul închis,
    iar START_8000 deschide browserul după 12 s fixe. Așa cererile așteaptă,
    nu sunt refuzate."""
    app.timer(0.1, fn, once=True)


def _reconcile_decision_target_logged() -> None:
    try:
        _reconcile_decision_target()
    except Exception as exc:  # noqa: BLE001
        logger.warning("verificare țintă decizie la pornire: %s", exc)


def _istoric_has_data() -> bool:
    """True dacă există măcar un CSV în _ISTORIC/ (sursa pe care o citește bench-ul)."""
    try:
        from loto_enterprise.benchmark.runner import _list_istoric_dirs

        for d in _list_istoric_dirs():
            if any(d.glob("*.csv")):
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("istoric check: %s", exc)
    return False


def _general_lr_lock():
    from process_lock import file_lock
    return file_lock(str(BENCH_PID_FILE) + ".lr-workflow", timeout=0)


async def _run_general_before_lr() -> bool:
    """Wait for the successful receipt of this launch; another UI cannot consume it."""
    import asyncio

    try:
        with _general_lr_lock():
            epoch = STATE.get("_cancellation_epoch", 0)
            process = run_rebench(lr_workflow=True)
            if process is None:
                return False
            identity = STATE["_bench_run_identity"]
            while process.poll() is None:
                if STATE.get("bench_cancelled") or epoch != STATE.get("_cancellation_epoch", 0):
                    return False
                await asyncio.sleep(.5)
            if STATE.get("_bench_process") is process:
                _observe_bench_activity(False)
            return (STATE.get("_general_lr_completed") == identity[1]
                    and epoch == STATE.get("_cancellation_epoch", 0) and not STATE.get("bench_cancelled"))
    except TimeoutError:
        ui.notify("Un Re-Bench general pentru selecția LR este deja în curs.", type="warning")
        return False


def run_rebench(*, lr_workflow=False) -> subprocess.Popen | None:
    """Re-Bench UNIC, aliniat cu walk-forward-ul de producție.

    ``block-size=1`` recalculează scorul înaintea FIECĂREI extrageri testate,
    exact ca validarea afișată după generare. Varianta rapidă istorică
    (score-once-per-fold, block=99999) putea alege un câștigător care trecea
    poarta din bench, dar pierdea față de random în walk-forward-ul real.

    Controlul pe istoricul amestecat este sărit: dubla timpul și nu participă
    la decizia de producție (``decision.py`` folosește doar ``is_random=False``).
    Baseline-ul scorerului ``random`` rămâne prezent și obligatoriu.
    """
    if lr_rebench_running() or _bench_running():
        ui.notify("Un bench rulează deja.", type="warning")
        return
    if _selected_country() != _LOT.RO:
        _missing = [
            g.display
            for g in _LOT.games_for_country(_selected_country())
            if not (PROJECT_ROOT / g.csv).is_file()
        ]
        if _missing:
            ui.notify(
                "Lipsește istoricul pentru: " + ", ".join(_missing),
                type="negative",
                timeout=8000,
            )
            return
    elif not _istoric_has_data():
        ui.notify(
            "Nu există date în _ISTORIC/ — adaugă CSV-urile cu extragerile "
            "(loto_6_49.csv, loto_5_40.csv, joker.csv) înainte de Re-Bench.",
            type="negative",
            timeout=8000,
        )
        return
    _load_missing_bench_histories()
    if not STATE["datasets"]:
        ui.notify(
            "⚠️ Niciun CSV încărcat în UI — bench-ul va rula, dar Auto-Pilot-ul "
            "de după NU va putea genera pool-uri. Încarcă fișierele la pasul 1.",
            type="warning",
            timeout=8000,
        )
    # Un singur bench, fără --methods (= curarea per-game din producție), scrie
    # best_methods.json. Dacă curarea este inactivă, CLI-ul revine la TOATE.
    # Re-score per extragere: selecția și avertismentul de onestitate măsoară
    # aceeași procedură, nu un pool înghețat la începutul întregului fold.
    _cc = _selected_country()
    _args = [
        "--no-rich",
        "--percentiles",
        _PCTS,
        "--block-size",
        "1",
        "--no-shuffled-control",
    ]
    if _cc != _LOT.RO:
        # Altă țară: registrul ei, ieșire separată (bench_results/countries/<CC>/,
        # decisions/<CC>/best_methods.json). România: argv-ul de dinainte.
        _args += ["--country", _cc]
    STATE["bench_country"] = _cc
    return _launch_bench(
        _args,
        "Re-Bench walk-forward real (metodele fiecărui joc)"
        if _cc == _LOT.RO
        else f"Re-Bench {_country_label(_cc)}",
        **({"lr_workflow": True} if lr_workflow else {}),
    )


def _fmt_eta_seconds(total: float) -> str:
    if total < 60:
        return f"~{int(total)} sec"
    if total < 3600:
        return f"~{int(total / 60)} min"
    return f"~{total / 3600:.1f} h"


def _avg_fold_runtime(fp, game_key: str | None = None) -> float | None:
    """Media runtime_sec a foldurilor reale din folds.csv (opțional pe un joc)."""
    if not fp.exists():
        return None
    df = pd.read_csv(fp)
    if df.empty or "runtime_sec" not in df.columns:
        return None
    if game_key is not None and "game" in df.columns:
        df = df[df["game"].astype(str) == game_key]
        if df.empty:
            return None
    mask = (df.get("failed", False) == False) & (df["runtime_sec"] > 0.05)  # noqa: E712
    real = df[mask] if mask.any() else df
    return float(real["runtime_sec"].mean())


def _bench_workers() -> int:
    """Procesele în care rulează bench-ul pe această stație (același calcul ca runner-ul)."""
    try:
        from loto_enterprise.benchmark.runner import bench_worker_count

        return bench_worker_count()
    except Exception:  # noqa: BLE001
        return 1


def _workers_text(n: int) -> str:
    """„pe 4 procese în paralel”, „pe 21 de procese în paralel”, „într-un singur proces”."""
    return "într-un singur proces" if int(n) <= 1 else f"pe {count(n, 'procese')} în paralel"


def _estimate_bench_eta(target_folds: int, overhead: float = 1.25) -> str:
    """ETA bench pe baza ULTIMEI rulări (bench_results/folds.csv): avg runtime_sec
    al folds-urilor reale × nr. folds × overhead, împărțit la procesele în care
    rulează în paralel (`runtime_sec` e timpul unui fold într-un proces).
    Fallback la estimarea implicită dacă nu există bench anterior."""
    default = "~50 min" if target_folds >= 1000 else "~5 min"
    fp = PROJECT_ROOT / "bench_results" / "folds.csv"
    if not fp.exists():
        return default
    try:
        avg = _avg_fold_runtime(fp)
        if avg is None:
            return default
        return _fmt_eta_seconds(avg * target_folds * overhead / _bench_workers())
    except Exception:  # noqa: BLE001
        return default


def _csv_rows(path) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except OSError:
        return 0


def _new_draws_text(n: int) -> str:
    """„1 extragere nouă”, „5 extrageri noi”, „20 de extrageri noi”."""
    return "1 extragere nouă" if int(n) == 1 else f"{count(n, 'extrageri')} noi"


def _bench_target_rule_text(country: str) -> str:
    """Descrie ținta efectivă pe joc, fără a schimba selectorul global."""
    from loto_enterprise.benchmark.hit_target import game_hit_target

    target = _clamped_bench_target()
    if _LOT.normalize_country(country) == _LOT.RO:
        if target >= 5:
            return (
                f"6/49: {game_hit_target('loto_6_49', target)}+ în pool; "
                f"Joker Urna 1: {game_hit_target('joker_urna1', target)}+ în pool; "
                f"5/40: {game_hit_target('loto_5_40', target)}+ în pool. "
                "6+ este urmărit separat la 6/49 și 5/40; o variantă 5/40 are cinci numere. "
                "Joker Urna 2: top-1, potrivire exactă."
            )
        return (
            f"6/49 și Joker Urna 1: {target}+. Loto 5/40: minimum 4+ (mereu 4+ "
            "cu selectorul 3/4). Joker Urna 2: top-1, potrivire exactă."
        )
    return "Ținte per joc: " + "; ".join(
        f"{game.name}: {game_hit_target(game.bench_key, target)}+"
        for game in _LOT.games_for_country(country)
    ) + "."


def _bench_intro_text(country: str) -> str:
    """Text recalculabil la schimbarea țintei, fără citiri de fișiere."""
    cc = _LOT.normalize_country(country)
    if cc == _LOT.RO:
        return (
            "Un singur bench compară în paralel metodele fiecărui joc și alege metoda fixă "
            "pentru fiecare dimensiune de pool. Cu selecția dinamică LR activă, "
            "metoda urnei principale se realege din extragerile anterioare. "
            f"{_bench_target_rule_text(cc)} "
            "Vezi clasamentul complet la 🏆 Clasament bench."
        )
    name = _LOT.country_name(cc)
    names = ", ".join(g.name for g in _LOT.games_for_country(cc))
    return (
        f"{name} ({names}): un singur bench testează metodele fiecărui joc "
        "(exclusiv CPU), pe toate nucleele (în paralel). În fiecare joc, "
        "metodele concurează în ACELAȘI clasament → UN câștigător "
        "→ UN Auto-Pilot → UN walk-forward. "
        f"{_bench_target_rule_text(cc)} Rezultatele stau separat de România "
        f"(bench_results/countries/{cc}/, decisions/{cc}/best_methods.json)."
    )


def _default_bench_methods_by_game(game_keys):
    from loto_enterprise.benchmark.curated import apply_curation, resolve_methods_per_game
    from loto_enterprise.benchmark.methods import list_methods, method_meta
    from loto_enterprise.benchmark.runner import _methods_for_game

    keys = tuple(game_keys)
    available = [name for name in list_methods() if method_meta(name).get("available", True)]
    kept, info = apply_curation(available)
    configured = resolve_methods_per_game(kept, keys) if info.get("active") else None
    return {key: _methods_for_game(kept, configured, key) for key in keys}


def _country_bench_texts(country: str) -> dict:
    """Cele patru texte ale panoului de bench pentru o țară STRĂINĂ.

    (1) introducerea cu țara și jocurile ei; (2) ETA-ul din ultima rulare a
    țării sau, fără ea, o estimare scalată cu lungimea istoricului (rescorare
    per extragere ~ n²), spusă ca atare; (3) matricea de curare a țării;
    (4) prospețimea CSV-urilor ei față de fișierul ei de decizie."""
    cc = _LOT.normalize_country(country)
    name = _LOT.country_name(cc)
    games = _LOT.games_for_country(cc)
    out: dict = {"intro": _bench_intro_text(cc)}
    keys = [key for game in games for key in game.bench_keys]
    n_urna2 = sum(1 for g in games if g.bench_key_urna2)
    try:
        matrix = _default_bench_methods_by_game(keys)
    except Exception:  # noqa: BLE001
        matrix = dict.fromkeys(keys, ())
    counts = [sum(method != "random" for method in methods) for methods in matrix.values()]
    n_methods = "/".join(map(str, dict.fromkeys(counts)))
    n_main = len(games)
    out["curation"] = (
        f"{name}: {n_methods} metode × {n_main} "
        + ("joc" if n_main == 1 else "jocuri")
        + (f" + Urna 2 ({n_urna2})" if n_urna2 else "")
        + ", plus baseline-ul random."
    )
    n_windows = len([p for p in _PCTS.split(",") if p.strip()])
    target = sum(map(len, matrix.values())) * max(1, n_windows)
    fp = _LOT.bench_out_dir_for(cc, PROJECT_ROOT) / "folds.csv"
    eta = None
    try:
        avg = _avg_fold_runtime(fp)
        if avg is not None:
            workers = _bench_workers()
            eta = (
                f"⏱ ETA estimat: {_fmt_eta_seconds(avg * target * 1.25 / workers)} "
                f"pentru {count(target, 'folduri', 'un fold')} {_workers_text(workers)} "
                f"— din durata medie a unui fold la ultima rulare {name}."
            )
    except Exception:  # noqa: BLE001
        eta = None
    if eta is None:
        try:
            ref = _avg_fold_runtime(
                PROJECT_ROOT / "bench_results" / "folds.csv", "loto_6_49"
            )
            n_ref = _csv_rows(PROJECT_ROOT / "_ISTORIC" / "loto_6_49.csv")
            n_here = max(_csv_rows(PROJECT_ROOT / g.csv) for g in games)
            if ref and n_ref:
                scaled = ref * (n_here / n_ref) ** 2
                workers = _bench_workers()
                eta = (
                    f"⏱ ETA estimat: {_fmt_eta_seconds(scaled * target * 1.25 / workers)} "
                    f"pentru {count(target, 'folduri', 'un fold')} {_workers_text(workers)} "
                    f"— estimare fără o rulare anterioară {name}, "
                    f"scalată cu lungimea istoricului ({n_here} extrageri față de "
                    f"{n_ref} la România · Loto 6/49)."
                )
        except Exception:  # noqa: BLE001
            eta = None
    out["eta"] = eta or (
        f"⏱ ETA: fără o rulare anterioară {name} și fără referință românească — "
        "nu se poate estima."
    )
    try:
        from loto_enterprise.benchmark.freshness import (
            aggregate_recommendation,
            check_freshness,
            country_freshness_inputs,
        )

        dec, csv_map, cols_map = country_freshness_inputs(cc)
        if not Path(dec).exists():
            out["freshness"] = (
                f"{name} nu are încă bench; până atunci generarea folosește frequency."
            )
        else:
            reports = check_freshness(dec, csv_map, cols_map, country=cc)
            rec = aggregate_recommendation(reports)
            delta = sum(
                max(0, int(r.current_rows or 0) - int(r.cached_rows or 0))
                for k, r in reports.items()
                if not str(k).endswith("_urna2")
            )
            if rec in ("quick_rebench", "full_rebench") and delta > 0:
                out["freshness"] = (
                    f"🆕 {name}: +{_new_draws_text(delta)} de la ultimul bench → "
                    "Re-Bench recalculează complet."
                )
            elif rec in ("quick_rebench", "full_rebench"):
                out["freshness"] = (
                    f"⚠️ {name}: benchmarkul nu mai corespunde datelor sau "
                    "metodelor curente. Rulează Re-Bench pentru actualizare."
                )
            else:
                out["freshness"] = f"✅ {name}: date neschimbate de la ultimul bench."
    except Exception as exc:  # noqa: BLE001
        out["freshness"] = f"{name}: prospețimea bench-ului nu poate fi verificată ({exc})."
    return out


def _load_registry_histories(games) -> tuple[list, list]:
    """Încarcă CSV-urile din registru pentru jocurile date (acțiune explicită).

    Validare: coloanele n1..n<draw_n>, fiecare rând o extragere validă
    (`valid_draw_matrix`) și date ZZ-LL-AAAA stricte. Leagă fișierul de joc în
    STATE['dataset_game'], ca identitatea să nu mai fie ghicită din nume.
    Întoarce (încărcate [(nume, id, n)], erori [text])."""
    from loto_enterprise.core.draw_validation import valid_draw_matrix

    loaded, errors = [], []
    for g in games:
        path = PROJECT_ROOT / g.csv
        name = Path(g.csv).name
        try:
            df = pd.read_csv(path)
            cols = [f"n{i}" for i in range(1, g.draw_n + 1)]
            _draws, mask = valid_draw_matrix(df, cols, draw_n=g.draw_n, max_num=g.max_n)
            if int(len(df)) == 0 or not bool(mask.all()):
                raise ValueError(
                    f"{int((~mask).sum())} rânduri invalide din {len(df)}"
                )
            if "date" in df.columns:
                pd.to_datetime(df["date"], format="%d-%m-%Y", errors="raise")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{g.display}: {exc}")
            continue
        with STATE_LOCK:
            STATE["datasets"] = [(f, d) for f, d in STATE["datasets"] if f != name] + [
                (name, df)
            ]
            STATE.setdefault("dataset_game", {})[name] = g.game_id
            STATE.setdefault("dataset_loaded_at", {})[name] = _dt.now().strftime(
                "%d-%m-%Y %H:%M"
            )
        loaded.append((name, g.game_id, len(df)))
    return loaded, errors


def _load_missing_bench_histories(games=None) -> None:
    """Completează jocurile selectate, fără să reîncarce sursele existente."""
    with STATE_LOCK:
        current = list(STATE.get("datasets") or [])
        present = {_game_spec_for(name).game_id for name, _ in current}
        names = {str(name).casefold() for name, _ in current}
    games = _selected_games() if games is None else games
    missing = [game for game in games if game.game_id not in present]
    loadable, errors = [], []
    for game in missing:
        name = Path(game.csv).name
        if name.casefold() in names:
            errors.append(f"{game.display}: numele {name} este deja folosit de altă sursă")
        else:
            loadable.append(game)
    if loadable:
        _loaded, load_errors = _load_registry_histories(loadable)
        errors.extend(load_errors)
    for error in errors:
        ui.notify(f"Istoric indisponibil pentru Re-Bench: {error}", type="warning", timeout=8000)


def _autoload_histories(games) -> tuple[list, list]:
    """Încarcă automat CSV-urile jocurilor date care lipsesc din STATE sau s-au
    schimbat pe disc de la ultima încărcare (ex. după ACTUALIZARI.bat)."""
    seen = STATE.setdefault("dataset_mtime", {})
    loaded_names = {f for f, _ in STATE["datasets"]}
    todo, stamps = [], {}
    for g in games:
        name = Path(g.csv).name
        try:
            mt = (PROJECT_ROOT / g.csv).stat().st_mtime_ns
        except OSError:
            continue
        if name not in loaded_names or seen.get(name) != mt:
            todo.append(g)
            stamps[name] = mt
    if not todo:
        return [], []
    loaded, errors = _load_registry_histories(todo)
    for name, _gid, _n in loaded:
        seen[name] = stamps[name]
    return loaded, errors


def _target_bench_folds() -> int:
    """Count folds on the methods and games that a normal Romanian Re-Bench will run."""
    try:
        from loto_enterprise.benchmark.runner import discover_games

        methods = _default_bench_methods_by_game(game.key for game in discover_games())
        total_methods = sum(map(len, methods.values()))
        n_windows = len([p for p in _PCTS.split(",") if p.strip()])
        return total_methods * max(1, n_windows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("target folds Re-Bench: %s", exc)
        return 0


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


def _bench_progress_from(log_path, start_ts=None) -> tuple[float, str] | None:
    """(fracție, text live) dintr-un log de bench specific. None dacă logul lipsește.

    Log: `[N/M] [game/method/pct%/REAL|RND/CPU] ...`
    Text (plain, fără HTML): `% (N/M teste) · rămas ~X · acum: joc · metodă · fereastră pct% · REAL/RND`.
    `pct%` = fereastra istorică a fold-ului (ex. ultimele 60% din CSV), NU progresul bench.
    """
    if not log_path.exists():
        return None
    cur = tot = 0
    last_now = ""
    try:
        import re

        # Bench-ul poate produce zeci de MB, pe OneDrive. Citirea integrală la
        # fiecare tick de 1s bloca inutil I/O + event-loop-ul. Ultimul progres
        # este suficient și se află în coada logului.
        txt = "".join(read_tail_lines(log_path, 3000, block=512 * 1024))
        if PROGRESS_MARKER in txt:
            return 1.0, txt.rsplit(PROGRESS_MARKER, 1)[1].splitlines()[0].strip()
        # Ultima linie de progres: [1048/2568] [loto_5_40/ml_svm_rbf/60%/REAL/CPU]
        matches = re.findall(r"\[(\d+)/(\d+)\]\s*\[([^\]]+)\]", txt)
        if matches:
            cur, tot = int(matches[-1][0]), int(matches[-1][1])
            seg = [s.strip() for s in matches[-1][2].split("/") if s.strip()]
            # game / method / window% / REAL|RND [/CPU]
            if len(seg) >= 3:
                game, method, window = seg[0], seg[1], seg[2]
                kind = seg[3] if len(seg) >= 4 else ""
                # „60% backtest" e greșit — e fereastra pe istoric, nu % din bench.
                parts = [game, method, f"fereastră {window}"]
                if kind in ("REAL", "RND"):
                    parts.append(kind)
                last_now = " · ".join(parts)
    except Exception:  # noqa: BLE001
        pass
    if tot <= 0:
        return 0.03, "pornește... (estimez după primele teste)"
    frac = max(0.0, min(1.0, cur / tot))

    elapsed = max(0.0, time.time() - start_ts) if start_ts else 0.0
    eta = (
        (tot - cur) * (elapsed / cur)
        if (elapsed > 0 and cur > 0 and tot > cur)
        else None
    )

    text = f"{int(frac * 100)}% ({cur}/{tot} teste)"
    if eta is not None:
        text += f"  ·  rămas ~{_fmt_dur(eta)}"
    elif cur >= tot:
        text += "  ·  ✅ gata"
    if last_now:
        text += f"  ·  acum: {last_now}"
    return frac, text


_HW_CACHE = {"html": "", "ts": 0.0, "running": False}


def _hw_telemetry_refresh() -> None:
    """Citește CPU/RAM ÎN FUNDAL (thread) și cache-uiește HTML-ul. Apelat de un
    thread separat — NU pe event-loop-ul UI (psutil e blocant → ar pica
    WebSocket-ul 'connection lost'). GPU eliminat complet."""
    cpu = ram = ""
    try:
        import psutil

        ncores = psutil.cpu_count(logical=True) or 1
        # interval=0.3 → citire instantanee REALĂ (blochează 0.3s, dar suntem în thread
        # de fundal, nu pe event-loop). interval=None dădea mereu 0% la prima citire.
        pct = psutil.cpu_percent(interval=0.3)
        active = round(pct / 100.0 * ncores)
        cpu = f"{pct:.0f}% (~{active}/{ncores} nuclee)"
        vm = psutil.virtual_memory()
        ram = f"{vm.used / (1024**3):.1f}/{vm.total / (1024**3):.0f} GB ({vm.percent:.0f}%)"
    except Exception:  # noqa: BLE001
        pass
    parts = []
    if cpu:
        parts.append(render_html_safe(t"<span style='color:#38bdf8'>CPU {cpu}</span>"))
    if ram:
        parts.append(render_html_safe(t"<span style='color:#60a5fa'>RAM {ram}</span>"))
    _HW_CACHE["html"] = (
        (
            render_html_safe(
                t"<div style='margin-top:6px;font-size:.82em;font-family:monospace;opacity:.9'>📊 "
            )
            + " &nbsp;·&nbsp; ".join(parts)
            + render_html_safe(t"</div>")
        )
        if parts
        else ""
    )


def _hw_telemetry_html() -> str:
    """Întoarce INSTANT HTML-ul cache-uit (ne-blocant). Pornește un thread de refresh
    la fundal dacă datele-s vechi (>2.5s) — astfel event-loop-ul UI nu se blochează."""
    import threading, time as _t

    if not _HW_CACHE["running"] and (_t.time() - _HW_CACHE["ts"]) > 2.5:
        _HW_CACHE["running"] = True
        _HW_CACHE["ts"] = _t.time()

        def _bg():
            try:
                _hw_telemetry_refresh()
            finally:
                _HW_CACHE["running"] = False

        threading.Thread(target=_bg, daemon=True).start()
    return _HW_CACHE["html"]


def cancel_all() -> None:
    cancel_lr_walk_forward()
    cancel_lr_rebench()
    # Publicarea și finalizarea WF se serializează cu anularea, înainte de I/O.
    cancelled_identity = STATE.get("_bench_run_identity")
    try:
        marker_parts = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")
        owns_marker = bool(cancelled_identity and len(marker_parts) >= 5
                           and marker_parts[2] == cancelled_identity[1] and "ctx1" in marker_parts[4:])
    except (OSError, UnicodeError, IndexError):
        owns_marker = False
    _WF_CANCEL_REQUESTED.set()
    _VIRTUAL_WF_CANCEL_REQUESTED.set()
    with STATE_LOCK:
        STATE["wf_user_cancel"] = True
        STATE["bench_cancelled"] = True
        STATE.pop("_autopilot_after_virtual", None)
        STATE.pop("_bench_history_pending", None)
    cancel_marker_saved = _mark_bench_cancelled()
    STATE["_cancellation_epoch"] = int(STATE.get("_cancellation_epoch", 0)) + 1
    STATE["_cancellation_pending"] = True

    def cancel_finished(_):
        STATE["_cancellation_pending"] = False

    def cancel_failed(exc):
        cancel_finished(None)
        logger.warning("cancel jobs: %s", exc)
        ui.notify("Coada este indisponibilă. Anularea joburilor nu a fost confirmată.", type="warning")

    _queue_operation(lambda: cancel_pending_running_jobs("Oprit de utilizator"), cancel_finished, cancel_failed)
    # Kill bench (din .bench_pid) + fallback orice bench_all_methods.py din proiect
    from cleanup_old_processes import kill_pid_tree
    import psutil

    keep_bench_pid_file = False
    survivor_candidates = list(STATE.get("_bench_survivors") or [])
    try:
        # PID VERIFICAT (aceeași identitate ca _bench_running: create_time +
        # cmdline), nu doar `pid_exists`: un PID stale reciclat de Windows către
        # un proces străin ar face ca tree-kill-ul să omoare tot arborele lui,
        # nu doar procesul greșit izolat cum era înainte de tree-kill.
        pid, verified = _bench_pid_status()
        if pid is not None:
            # Kill poate eșua fără excepție; markerul rămâne până când starea
            # procesului a fost verificată din nou după încercare.
            keep_bench_pid_file = True
            # Tree-kill, nu doar terminate() pe părinte: runner.py paralelizează
            # foldurile pe un ProcessPoolExecutor — uciderea DOAR a procesului
            # principal lasă lucrătorii orfani, arzând CPU după „Anulează TOT".
            if not verified:
                keep_bench_pid_file = True
            else:
                proc = psutil.Process(pid)
                identity = _bench_process_identity(proc)
                if identity is True:
                    kill_pid_tree(pid, expected_create_time=proc.create_time())
                elif identity is None:
                    keep_bench_pid_file = True
    except psutil.AccessDenied:
        keep_bench_pid_file = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("kill bench pid: %s", exc)
    try:
        # Plasa de siguranță cerea AMBELE substring-uri în linia de comandă:
        # „bench_all_methods.py" ȘI PROJECT_ROOT. Nu se potrivea niciodată:
        # `_launch_bench` pornea scriptul cu cale RELATIVĂ (cwd=PROJECT_ROOT), iar
        # `sys.executable` e venv-ul din D:\_BUILD\_LOTO, deliberat în AFARA
        # repo-ului — deci PROJECT_ROOT nu apărea nicăieri în cmdline. Cu
        # `.bench_pid` lipsă sau reciclat, „Anulează TOT" raporta „Proces anulat."
        # în timp ce bench-ul mergea mai departe și rescria folds.csv.
        # Acum: potrivim pe numele scriptului și confirmăm prin CWD-ul procesului.
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            if _is_project_bench_process(p):
                started = None
                try:
                    started = p.create_time()
                    kill_pid_tree(p.pid, expected_create_time=started)
                    if _bench_survivor_identity(p.pid, started) is not False:
                        survivor_candidates.append((p.pid, started))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[BENCH] oprirea PID %s nu a fost confirmată: %s", p.pid, exc)
                    survivor_candidates.append((p.pid, started))
    except Exception as exc:  # noqa: BLE001
        logger.warning("kill bench fallback: %s", exc)
    if keep_bench_pid_file:
        keep_bench_pid_file = _bench_pid_status()[0] is not None
    survivors = []
    for candidate in dict.fromkeys(survivor_candidates):
        if _bench_survivor_identity(*candidate) is not False:
            survivors.append(candidate)
    STATE["_bench_survivors"] = survivors
    # Reconstituim markerul numai când identitatea și momentul sunt confirmate;
    # garda în memorie rămâne și dacă scrierea pe disc nu reușește.
    if not keep_bench_pid_file:
        for survivor_pid, survivor_started in survivors:
            if survivor_started is not None and _bench_survivor_identity(survivor_pid, survivor_started) is True:
                try:
                    atomic_write_text(BENCH_PID_FILE, f"{survivor_pid}|{int(survivor_started)}|cancelled")
                    keep_bench_pid_file = True
                except OSError as exc:
                    logger.warning("[BENCH] markerul nu a putut fi refăcut: %s", exc)
                break
    if not keep_bench_pid_file:
        if owns_marker and not survivors and cancel_marker_saved and _bench_survivor_identity(cancelled_identity[0], None) is False:
            _clear_bench_pid_if_matches(cancelled_identity)
        try:
            BENCH_PID_FILE.unlink()
        except OSError:
            pass
    else:
        logger.warning("[BENCH] markerul a fost păstrat: oprirea procesului nu a fost confirmată.")
    STATE["active_job_id"] = None
    # IMPORTANT: marcăm că bench-ul NU mai e "în rulare" ca _tick să NU interpreteze
    # disparitia procesului ca "bench terminat → Auto-Pilot". Altfel Anuleaza pornea
    # generarea automat.
    STATE["bench_was_running"] = False
    STATE["bench_cancelled"] = True
    # Oprește și WALK-FORWARD-ul (thread separat) și blochează _finalize_pipeline:
    # altfel „Anulează TOT" lăsa WF să ruleze până la buget (până la 90 min), iar
    # finally-ul lui declanșa mail/shutdown — PC-ul se putea ÎNCHIDE după un cancel.
    if keep_bench_pid_file or survivors or not cancel_marker_saved:
        ui.notify("Anularea bench-ului nu a fost confirmată; un proces poate rula în continuare.", type="warning")
    else:
        ui.notify("Proces anulat.", type="warning")
    _refresh_status()


# --------------------------------------------------------------------------- #
# Walk-forward backtest (în thread de fundal, ca să nu blocheze UI-ul)
# --------------------------------------------------------------------------- #


def _echo_mismatch(g_label, data) -> bool:
    """True dacă o cheie străină nu e confirmată de ecoul worker-ului."""
    lot = _LOT.lottery_by_id(str(g_label))
    if lot is None or lot.is_romanian:
        return False
    return str((data or {}).get("game_id") or "") != lot.game_id


def _result_sources_from_job(job: dict) -> dict | None:
    """Decode the persisted input snapshot without changing the queue contract."""
    raw = job.get("config_json")
    if not raw:
        return None  # compatibility with old/in-memory results lacking job metadata
    sources = {}
    try:
        config = json.loads(raw)
        for dataset in config.get("datasets", []):
            fname = str(dataset["fname"])
            if fname in sources:
                raise ValueError(f"Duplicate dataset name: {fname}")
            # Identic workerului: datele DD-MM rămân string-uri până la
            # normalizarea cronologică; inferența pandas este month-first.
            frame = pd.read_json(
                io.StringIO(dataset["df_json"]), orient="split", dtype=False
            )
            identities = {
                (str(task.get("game_label") or ""), str(task.get("country") or _LOT.RO))
                for task in dataset.get("tasks", [])
            }
            if len(identities) == 1:
                game_id, country = identities.pop()
                spec = _LOT.require_lottery(game_id, country)
                frame.attrs.update(game_id=spec.game_id, country=spec.country)
                if spec.is_romanian and len(dataset["tasks"]) == 1:
                    task = dataset["tasks"][0]
                    frame.attrs["lr_mail_settings"] = {
                        key: task[key] for key in (
                            "lr_budget_lei", "lr_budget_objective", "lr_budget_mode", "bench_hit_target",
                            "lookback", "dynamic_method", "joker_pool_size",
                        ) if key in task
                    }
                    mail = dataset.get("lr_mail_settings", {})
                    frame.attrs["lr_mail_settings"].update(
                        {key: mail[key] for key in ("lr_budget_lei", "lr_budget_objective", "lr_budget_mode") if key in mail}
                    )
            sources[fname] = frame
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        logger.warning("[RESULT] Original job source unavailable: %s", exc)
        return {}  # never validate a broken snapshot against a replacement upload
    return sources


def _wf_run_kwargs(g_label: str, data: dict) -> dict:
    """Parametrii walk-forward ai rezultatului afișat (aceeași cheie de cache)."""
    return {
        # GEOMETRIA (străin: din ecoul rezultatului); România: eticheta.
        "game_type": _game_spec_for(g_label, data).geometry,
        "pool_size": int(data.get("pool_size_requested") or data.get("pool_size") or 10),
        "backtest_depth_percent": data.get("wf_depth_pct", DEFAULT_REPLAY_PERCENT),
        "lookback_percent": _effective_lookback_pct(data),
        **_wf_generation_options(data),
    }


def _store_wf_result(rk: str, flat, meta: dict) -> None:
    """Pașii WF și meta lor pentru „Istoric hits” și raport."""
    with STATE_LOCK:
        STATE["retro"][rk] = flat
        STATE.setdefault("retro_meta", {})[rk] = {
            "partial": bool(meta.get("partial")),
            "newest_missing": bool(meta.get("newest_missing")),
            "decision_changed": bool(meta.get("decision_changed")),
            "n_test_draws": meta.get("n_test_draws"),
            "n_expected": meta.get("n_expected"),
            "backtest_depth_percent": meta.get("backtest_depth_percent"),
            "from_cache": bool(meta.get("from_cache")),
            # pool-ul REAL cu care a rulat WF (pt afișare corectă în istoric)
            "pool_size": meta.get("pool_size"),
            "wheel_guarantee": meta.get("wheel_guarantee"),
            "wheel_condition": meta.get("wheel_condition"),
            "max_variants": meta.get("max_variants"),
            "max_consecutive_run": meta.get("max_consecutive_run"),
            # Identitatea și profilul oficial sunt ale rulării, nu ale sliderului curent.
            "lr_scheme_code": meta.get("lr_scheme_code"),
            "lr_scheme": deepcopy(meta.get("lr_scheme")),
        }
        for key in (
            "large_pool_wf_skipped", "skip_reason", "skip_detail", "estimated_rows", "row_limit",
            "dynamic_method", "selection_validation",
        ):
            if key in meta:
                STATE["retro_meta"][rk][key] = meta[key]


def _result_scorers_match_decision(g_label: str, data: dict) -> bool:
    """Metodele care au produs rezultatul sunt cele ale deciziei de acum.

    Pașii WF și cheia cache-ului citesc decizia CURENTĂ. După un Re-Bench sau o
    schimbare a țintei 3+/4+, cache-ul găsit sub decizia de acum validează altă
    metodă decât cea care a produs pool-ul afișat. Se compară cu auditul
    rezultatului (`bench_winner`), pe aceeași cale ca motorul; la Joker și Urna 2."""
    if _result_uses_dynamic_method(data):
        from loto_enterprise.core.dynamic_winner import CANDIDATES, DYNAMIC_VERSION

        audit = data.get("audit") or {}
        info = (audit.get("bench_winner") or {}).get(_game_spec_for(g_label, data).bench_key) or {}
        selection = audit.get("dynamic_selection") or info.get("dynamic_selection") or {}
        if (
            selection.get("version") != DYNAMIC_VERSION
            or selection.get("method") not in CANDIDATES
            or ("candidate_methods" in selection and selection["candidate_methods"] != list(CANDIDATES))
        ):
            return False
    for _key, methods_now, info in _decision_methods_now(g_label, data):
        if info.get("method") != methods_now[0]:
            return False
        if info.get("ensemble"):
            members = tuple(member.get("method") for member in info["ensemble"] if isinstance(member, dict))
            if members != methods_now:
                return False
    return True


def _decision_methods_now(g_label: str, data: dict):
    """(cheie, metodele deciziei de acum, auditul rezultatului) pentru fiecare cheie
    de bench a jocului (Joker: și Urna 2), pe aceeași cale ca motorul."""
    from loto_enterprise.benchmark.decision import ENSEMBLE_MAX_METHODS
    from loto_enterprise.core.method_selector import (
        decision_path_for,
        get_ensemble_for_game,
        has_decision,
    )

    lot = _game_spec_for(g_label, data)
    cfg = None if lot.is_romanian else str(decision_path_for(lot.country))
    keys = [(lot.bench_key, _wf_run_kwargs(g_label, data)["pool_size"])]
    if lot.geometry == "joker":
        keys.append((lot.bench_key_urna2 or f"{lot.bench_key}_urna2", 1))
    used = (data.get("audit") or {}).get("bench_winner") or {}
    for key, hint in keys:
        if key == lot.bench_key and _result_uses_dynamic_method(data):
            continue  # The main scorer is chosen again from prior draws at each WF step.
        methods_now = ("frequency",)
        if has_decision(key, cfg):
            ens = get_ensemble_for_game(
                key, pool_size=hint, config_path=cfg, max_methods=ENSEMBLE_MAX_METHODS
            )
            if ens:
                methods_now = tuple(name for name, _fn, _weight in ens)
        yield key, methods_now, used.get(key) or {}


def _generation_used_engine_reserve(g_label: str, data: dict) -> str:
    """Scorerul deciziei care a eșuat la generare, când pool-ul afișat vine din
    rezerva `frequency` a motorului și decizia de acum îl numește tot pe el.

    Decizia nu s-a mutat, dar WF-ul rămâne sărit: pașii ar fi scorați de metoda
    deciziei, nu de `frequency`, care a produs pool-ul (AGENTS.md §8). `attempted`
    absent = rezultat fără numele încercat. Gol dacă vreo cheie nepotrivită nu e
    rezerva motorului (decizie mutată cu adevărat)."""
    names: list[str] = []
    for _key, methods_now, info in _decision_methods_now(g_label, data):
        now = methods_now[0]
        if info.get("method") == now:
            continue
        if not (
            info.get("fallback")
            and not info.get("no_decision")
            and not info.get("target_mismatch")
            and info.get("method") == "frequency"
            and info.get("attempted") in (None, now)
        ):
            return ""
        if now not in names:
            names.append(now)
    return ", ".join(names)


def _wf_skip_mark(g_label: str, data: dict) -> dict:
    """Marcajul (`retro_meta`) unui joc al cărui WF e sărit pentru că alt scorer
    decât cel care a produs pool-ul afișat i-ar scora pașii: motivul adevărat."""
    try:
        scorer = _generation_used_engine_reserve(g_label, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WF] %s: nu pot citi rezerva de la generare: %s", g_label, exc)
        scorer = ""
    if scorer:
        return {"scorer_fallback": True, "scorer": scorer}
    return {"decision_moved": True}


def _decision_moved_since_generation(g_label: str, data: dict) -> bool:
    """Decizia de acum alege altă metodă decât cea care a produs pool-ul afișat.

    Numai când auditul rezultatului numește metoda (`bench_winner`); un rezultat
    vechi fără ea nu se poate verifica și rulează ca înainte. WF-ul citește
    decizia CURENTĂ, deci după un Re-Bench sau o schimbare a țintei 3+/4+ ar
    măsura altă metodă sub pool-ul generat."""
    used = (data.get("audit") or {}).get("bench_winner") or {}
    if not _result_uses_dynamic_method(data) and not any(
        (info or {}).get("method") or (info or {}).get("ensemble") for info in used.values()
    ):
        return False
    try:
        return not _result_scorers_match_decision(g_label, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WF] %s: nu pot compara decizia cu rezultatul: %s", g_label, exc)
        return False


def _load_cached_walk_forward() -> int:
    """Validarea WF a rezultatului recuperat, numai din cache-ul de pe disc.

    Fără niciun pas calculat, fără scriere, fără mail sau oprire: reafișează
    „Istoric hits” după repornire. Numai cache-ul exact al istoricului
    rezultatului și numai dacă decizia de acum alege aceeași metodă care a
    produs rezultatul. Întoarce câte jocuri au primit validarea."""
    with STATE_LOCK:
        results = STATE.get("results")
        sources = STATE.get("result_sources")
        ds_by_name = dict(sources) if sources is not None else dict(STATE.get("datasets", []))
    if not (isinstance(results, tuple) and len(results) == 2) or not ds_by_name:
        return 0
    from loto_enterprise.core.walk_forward_adapter import run_honest_walk_forward

    loaded = 0
    for fname, g_label, data in _iter_wf_jobs(results[0]):
        df_source = ds_by_name.get(fname)
        if df_source is None or _echo_mismatch(g_label, data):
            continue
        try:
            if not _result_scorers_match_decision(g_label, data):
                logger.info(
                    "[WF] %s: decizia de acum nu alege metoda care a produs "
                    "rezultatul — validarea din cache ar fi a altei metode; nu o afișez.",
                    g_label,
                )
                if _decision_moved_since_generation(g_label, data):
                    # Același aviz ca la calea live, nu o secțiune care lipsește.
                    with STATE_LOCK:
                        STATE.setdefault("retro_meta", {})[f"{fname}_{g_label}"] = (
                            _wf_skip_mark(g_label, data)
                        )
                continue
            flat, meta = run_honest_walk_forward(
                df_source=df_source,
                use_cache=True,
                cache_only=True,
                **_wf_run_kwargs(g_label, data),
            )
        except Exception as exc:  # noqa: BLE001 — reafișarea nu blochează pornirea
            logger.warning("[WF] validare din cache indisponibilă pentru %s: %s", g_label, exc)
            continue
        if flat or meta.get("large_pool_wf_skipped"):
            _store_wf_result(f"{fname}_{g_label}", flat, meta)
            if flat:
                loaded += 1
    if loaded:
        logger.info("[WF] validare reîncărcată din cache pentru %d joc(uri).", loaded)
    return loaded


def _start_walk_forward(*, post_completion_actions: bool = True) -> None:
    with STATE_LOCK:
        results = STATE.get("results")
        sources = STATE.get("result_sources")
        ds_by_name = dict(sources) if sources is not None else dict(STATE.get("datasets", []))
        if isinstance(results, tuple) and len(results) == 2:
            _WF_CANCEL_REQUESTED.clear()
            completing_job = STATE.get("_result_job_id") if post_completion_actions else None
            STATE["wf_user_cancel"] = False
            my_seq = int(STATE.get("wf_seq") or 0) + 1
            STATE["wf_seq"] = my_seq
            STATE["wf_progress"] = 0.0
            wf_t0 = time.time()
            wf_deadline = wf_t0 + WF_TOTAL_BUDGET_S
            STATE["wf_start"] = wf_t0
            STATE["wf_deadline"] = wf_deadline
            STATE["wf_status"] = "📊 Pornesc walk-forward backtest (paralel ~80% CPU)..."
            STATE["wf_running"] = True
    if not (isinstance(results, tuple) and len(results) == 2):
        if post_completion_actions:
            _finalize_pipeline()  # ramura normală a unei generări
        return
    results_bundle, _ = results
    # Walk-forward pentru singurul pool de producție.
    _pfx = ""  # bench unic → fără prefix de secțiune

    # Secvență de rulare: dacă între timp pornește ALT walk-forward (generare nouă
    # cât rula validarea veche), thread-ul vechi devine stale — se oprește și NU mai
    # scrie retro/status/finalize peste rularea nouă.
    def _worker_wf() -> None:
        def _wf_cancel_all():
            # Anulare explicită din UI sau rulare înlocuită (alt WF pornit) →
            # oprire imediată; altfel buget global depășit → oprire parțială.
            if (
                STATE.get("wf_user_cancel")
                or _WF_CANCEL_REQUESTED.is_set()
                or STATE.get("wf_seq") != my_seq
            ):
                return True
            return time.time() > wf_deadline

        try:
            from loto_enterprise.core.walk_forward_adapter import (
                run_honest_walk_forward,
            )

            if not ds_by_name:
                # Tipic la RECUPERARE după restart: CSV-urile nu-s reîncărcate (se încarcă
                # manual). Walk-forward se va sări (df_source None) → fără stats de validare,
                # dar mail-ul (= doar numerele) și shutdown-ul rulează normal.
                logger.warning(
                    "[WF] datasets goale (probabil recuperare după restart) → "
                    "walk-forward sărit; mail/shutdown continuă fără stats de validare."
                )
            jobs = list(_iter_wf_jobs(results_bundle))
            total = len(jobs)
            done = 0
            budget_skipped = 0
            for i_job, (fname, g_label, data) in enumerate(jobs):
                df_source = ds_by_name.get(fname)
                if df_source is None:
                    continue
                if _echo_mismatch(g_label, data):
                    logger.warning(
                        "[WF] %s: rezultat fără ecoul jocului (worker vechi / "
                        "nepotrivire țară) — walk-forward sărit.",
                        g_label,
                    )
                    continue
                done += 1
                rk = f"{_pfx}{fname}_{g_label}"
                with STATE_LOCK:
                    if STATE.get("wf_seq") != my_seq:
                        break
                    # Marcajul unui rezultat anterior nu rămâne pe cel nou.
                    STATE.setdefault("retro_meta", {}).pop(rk, None)
                if _decision_moved_since_generation(g_label, data):
                    # Pașii ar fi scorați de altă metodă decât cea care a produs
                    # pool-ul afișat; nu se rulează și nu se afișează drept ai lui.
                    _skip = _wf_skip_mark(g_label, data)
                    logger.warning(
                        "[WF] %s: %s — walk-forward sărit.",
                        g_label,
                        f"pool din rezerva frequency (scorerul deciziei {_skip['scorer']} "
                        "a eșuat la generare)"
                        if _skip.get("scorer_fallback")
                        else "decizia de bench s-a schimbat după generare",
                    )
                    with STATE_LOCK:
                        if STATE.get("wf_seq") != my_seq:
                            break
                        STATE["retro"].pop(rk, None)
                        STATE.setdefault("retro_meta", {})[rk] = _skip
                    continue
                base = (done - 1) / max(1, total)
                with STATE_LOCK:
                    if STATE.get("wf_seq") != my_seq:
                        break
                    STATE["wf_status"] = f"📊 Walk-forward {done}/{total}: {g_label}..."
                    STATE["wf_progress"] = base

                def _wf_cb(
                    frac,
                    n_done=0,
                    n_total=0,
                    _b=base,
                    _t=total,
                    _d=done,
                    _tot=total,
                    _g=g_label,
                ):
                    frac = max(0.0, min(1.0, float(frac)))
                    with STATE_LOCK:
                        if STATE.get("wf_seq") != my_seq:
                            return
                        STATE["wf_progress"] = min(1.0, _b + frac / _t)
                        if n_total > 0:
                            STATE["wf_status"] = (
                                f"📊 Walk-forward {_d}/{_tot}: {_g} — "
                                f"pas {n_done}/{n_total} ({int(frac * 100)}%)"
                            )
                        else:
                            STATE["wf_status"] = (
                                f"📊 Walk-forward {_d}/{_tot}: {_g} — {int(frac * 100)}%"
                            )

                # Buget PER JOC (felie adaptivă din timpul global rămas): un joc
                # lent nu mai înfometează jocurile următoare (bug văzut: joker a
                # consumat tot bugetul → 5/40 parțial, 6/49 aproape zero). Minim
                # 60s/joc, împărțind timpul rămas la începutul fiecărui joc.
                _games_left = max(1, total - done + 1)
                _game_t0 = time.time()
                _game_deadline = _game_t0 + max(60.0, (wf_deadline - _game_t0) / _games_left)

                def _wf_should_cancel(_deadline=_game_deadline):
                    return _wf_cancel_all() or time.time() > _deadline

                try:
                    flat, meta = run_honest_walk_forward(
                        df_source=df_source,
                        use_cache=True,
                        progress_cb=_wf_cb,
                        should_cancel=_wf_should_cancel,
                        # Semnal SEPARAT de should_cancel: doar înlocuire (alt WF pornit
                        # pe aceeași cheie), NU buget/anulare — acelea vor cache-ul scris
                        # (asta e scopul acoperirii parțiale). Fără el, o rulare veche
                        # care termină DUPĂ una nouă i-ar suprascrie cache-ul mai complet.
                        should_skip_cache_write=lambda: STATE.get("wf_seq") != my_seq,
                        **_wf_run_kwargs(g_label, data),
                    )
                    if STATE.get("wf_seq") != my_seq:
                        # A pornit alt walk-forward: nu-i suprascriem retro/status.
                        logger.info(
                            "[WF] rulare înlocuită de una nouă — mă opresc fără scriere."
                        )
                        break
                    if meta.get("partial"):
                        logger.warning(
                            "[WF] %s validat PARȚIAL: %s/%s extrageri "
                            "(pot exista goluri între extragerile validate).",
                            g_label,
                            meta.get("n_test_draws"),
                            meta.get("n_expected"),
                        )
                    if not meta.get("decision_changed") and _decision_moved_since_generation(
                        g_label, data
                    ):
                        # Decizia s-a schimbat chiar înaintea pornirii WF-ului acestui joc.
                        meta = {**meta, "decision_changed": True}
                    with STATE_LOCK:
                        if STATE.get("wf_seq") != my_seq:
                            break
                        _store_wf_result(rk, flat, meta)
                except Exception as exc:  # noqa: BLE001
                    logger.error("walk-forward %s: %s", g_label, exc)
                    with STATE_LOCK:
                        if (
                            STATE.get("wf_seq") == my_seq
                            and not STATE.get("wf_user_cancel")
                            and not _WF_CANCEL_REQUESTED.is_set()
                        ):
                            STATE["retro"].pop(rk, None)
                            STATE.setdefault("retro_meta", {})[rk] = {"wf_error": True}
                with STATE_LOCK:
                    if STATE.get("wf_seq") != my_seq:
                        break
                    STATE["wf_progress"] = done / max(1, total)
                if _wf_cancel_all():
                    logger.warning(
                        "[WF] oprire walk-forward (anulare/buget) după %d/%d jocuri.",
                        done,
                        total,
                    )
                    # Secvența și marcajele se verifică/scriu atomic pentru
                    # fiecare joc, ca să nu atingă rezultatul unei generări noi.
                    for fn_left, g_left, _d_left in jobs[i_job + 1 :]:
                        if ds_by_name.get(fn_left) is None:
                            continue
                        rk_left = f"{_pfx}{fn_left}_{g_left}"
                        with STATE_LOCK:
                            if (
                                STATE.get("wf_seq") != my_seq
                                or STATE.get("wf_user_cancel")
                                or _WF_CANCEL_REQUESTED.is_set()
                            ):
                                break
                            budget_skipped += 1
                            STATE["retro"].pop(rk_left, None)
                            STATE.setdefault("retro_meta", {})[rk_left] = {
                                "budget_skipped": True
                            }
                    break
            with STATE_LOCK:
                if (
                    STATE.get("wf_seq") == my_seq
                    and not STATE.get("wf_user_cancel")
                    and not _WF_CANCEL_REQUESTED.is_set()
                ):
                    STATE["wf_status"] = (
                        f"⏱ Walk-forward oprit de bugetul de timp: "
                        f"{count(budget_skipped, 'jocuri', 'joc')} fără validare. "
                        "La generarea următoare pașii "
                        "deja validați se iau din cache."
                        if budget_skipped
                        else ""
                    )
                    STATE["wf_progress"] = 1.0
        except Exception as exc:  # noqa: BLE001
            with STATE_LOCK:
                if STATE.get("wf_seq") == my_seq:
                    STATE["wf_status"] = f"Walk-forward eșuat: {exc}"
        finally:
            # Serializăm decizia de finalizare cu generarea nouă și anularea:
            # altfel o rulare veche putea salva raportul sau programa oprirea PC-ului.
            skipped_shutdown = True
            with STATE_LOCK:
                if STATE.get("wf_seq") == my_seq:
                    if STATE.get("job_start_time") and STATE.get("wf_elapsed") is None:
                        STATE["wf_elapsed"] = time.time() - STATE["job_start_time"]
                    _save_report_file()  # raportul include statisticile walk-forward
                    STATE["results_dirty"] = True  # refresh pe event-loop
                    # Cancel poate fi cerut cât salvarea ține lacătul. Evenimentul
                    # se setează înainte ca handlerul să aștepte STATE_LOCK.
                    if STATE.get("wf_user_cancel") or _WF_CANCEL_REQUESTED.is_set():
                        STATE["wf_status"] = "Walk-forward anulat (fără mail/oprire PC)."
                        logger.info("[WF] finalizare sărită: anulare explicită din UI.")
                    elif post_completion_actions:
                        # Un bench pornit extern în timpul validării nu trebuie
                        # întrerupt de oprirea PC-ului din generarea anterioară.
                        try:
                            bench_busy = bool(STATE.get("_bench_history_pending") or STATE.get("bench_was_running") or _bench_running())
                        except Exception:  # noqa: BLE001
                            bench_busy = True
                        if bench_busy:
                            STATE["wf_status"] = "Finalizarea automată a fost oprită: Re-Bench este în curs."
                            logger.warning("[WF] Finalizare omisă: Re-Bench este în curs sau starea lui nu poate fi verificată.")
                        else:
                            skipped_shutdown = False
                            _finalize_pipeline()
                    STATE["results_dirty"] = True
                    STATE["wf_running"] = False
            if skipped_shutdown and completing_job is not None:
                _complete_job_stage(completing_job, "shutdown")

    threading.Thread(target=_worker_wf, daemon=True).start()


def _start_recovered_walk_forward() -> None:
    """Calculează numai la cerere istoricul rezultatului recuperat, pe sursele lui."""
    with STATE_LOCK:
        results = STATE.get("results")
        sources = STATE.get("result_sources")
        if STATE.get("wf_running") or STATE.get("virtual_wf_running") or STATE.get("active_job_id"):
            ui.notify("Există deja o generare sau o validare în curs.", type="warning")
            return
        if not STATE.get("results_recovered") or not (isinstance(results, tuple) and len(results) == 2):
            ui.notify("Rezultatul recuperat nu este disponibil.", type="warning")
            return
        jobs = list(_iter_wf_jobs(results[0]))
        if not jobs or not isinstance(sources, dict) or any(sources.get(fname) is None for fname, _, _ in jobs):
            ui.notify(
                "Istoricele folosite la generarea recuperată nu sunt disponibile. Generează din nou.",
                type="warning",
            )
            return
        _start_walk_forward(post_completion_actions=False)
    ui.notify("Calculez istoricul pentru rezultatul recuperat.", type="positive")
    _refresh_status()
    results_panel.refresh()


def _romanian_guarantee_text() -> str:
    """Pragul pe joc folosește aceleași limite ca normalizarea workerului."""
    requested = max(3, min(6, _int_setting("guarantee_val")))
    parts = []
    for game_id, label in (("6/49", "6/49"), ("5/40", "5/40"), ("joker", "Joker")):
        pick = _LOT.require_lottery(game_id, _LOT.RO).pick_n
        effective = min(requested, pick)
        suffix = f" (plafonată la {pick})" if effective < requested else ""
        parts.append(f"{label}: {effective}{suffix}")
    return "Garanția cerută pe joc: " + " · ".join(parts) + ". Depinde de acoperirea variantelor."


def _history_decision_keys(tasks, *, simulated: bool) -> tuple:
    """Deciziile de care depinde simularea: țările cu metode fixe și urna secundară Joker."""
    keys = []
    for task in tasks:
        if simulated and task.get("lr_scheme_code") is not None and task.get("dynamic_method") is True:
            if task["game_label"] == "joker":
                keys.append("joker_urna2")
        else:
            keys.append(task.get("country", _LOT.RO))
    return tuple(keys)


def _virtual_decision_fingerprint(countries) -> tuple:
    """Ținta curentă și conținutul fiecărei decizii, inclusiv modificări externe."""
    from loto_enterprise.core.method_selector import decision_path_for

    fingerprints = []
    for country in sorted(set(countries)):
        if country == "joker_urna2":
            from loto_enterprise.core.method_selector import recommend_optimal_config

            choice = recommend_optimal_config("joker_urna2", 1)
            selection = [choice.get(key) for key in ("scorer", "hit_target", "ensemble")]
            fingerprints.append((country, json.dumps(selection, sort_keys=True)))
            continue
        try:
            content = Path(decision_path_for(country)).read_bytes()
        except FileNotFoundError:
            digest = None
        else:
            digest = hashlib.sha256(content).hexdigest()
        fingerprints.append((country, digest))
    target = _clamped_bench_target() if any(k != "joker_urna2" for k in countries) else None
    return target, tuple(fingerprints)


def _start_virtual_history(*, auto_generate_config: str | None = None, automatic: bool = False, simulated: bool = False,
                           materialize_full: bool = False, config_json: str | None = None) -> bool:
    """Analizează sistemul complet teoretic pe setările curente."""
    with STATE_LOCK:
        if STATE.get("wf_running") or STATE.get("virtual_wf_running") or STATE.get("active_job_id"):
            ui.notify("Există deja o generare sau o validare în curs.", type="warning")
            return
        if STATE.get("bench_was_running") and not STATE.get("bench_cancelled"):
            ui.notify("Se finalizează Re-Bench; așteaptă actualizarea înainte de analiza virtuală.", type="warning")
            return
        try:
            if _bench_running():
                ui.notify("Re-Bench este în curs; așteaptă încheierea înainte de analiza virtuală.", type="warning")
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WF VIRTUAL] nu pot verifica Re-Bench: %s", exc)
            ui.notify("Nu pot verifica starea Re-Bench; încearcă din nou.", type="warning")
            return
        try:
            config = json.loads(config_json if config_json is not None else _build_config_json())
            jobs = []
            source_hashes = {}
            for dataset in config.get("datasets", []):
                frame = pd.read_json(io.StringIO(dataset["df_json"]), orient="split", dtype=False)
                for task in dataset.get("tasks", []):
                    spec = _LOT.require_lottery(task["game_label"], task.get("country", _LOT.RO))
                    source_hashes[(dataset["fname"], spec.game_id)] = hashlib.sha256(dataset["df_json"].encode("utf-8")).hexdigest()
                    guarantee = max(3, min(spec.pick_n, int(task["guarantee"])))
                    if not simulated and task.get("lr_scheme_code") is not None:
                        guarantee = spec.pick_n  # explicit complete-system reference, outside the LR bet
                    if not simulated and task.get("lr_scheme_code") is None and (guarantee != spec.pick_n or int(task.get("max_variants") or 0) != 0):
                        raise ValueError("Analiza virtuală cere sistem complet și plafon 0 pentru fiecare joc selectat.")
                    jobs.append((dataset["fname"], spec, frame, dict(task), guarantee))
            if not jobs:
                raise ValueError("Încarcă istoricul jocurilor selectate.")
            depth = config.get("wf_depth_pct", DEFAULT_REPLAY_PERCENT)
            if type(depth) is not int or depth not in VALIDATION_PERCENTILES:
                raise ValueError(f"Fereastră de validare necunoscută: {depth!r}.")
            budget = WF_TOTAL_BUDGET_S
            countries = _history_decision_keys((task for _, _, _, task, _ in jobs), simulated=simulated)
            initial_decision = _virtual_decision_fingerprint(countries)
            if initial_decision[0] is not None and any(
                int(task.get("bench_hit_target", initial_decision[0])) != initial_decision[0]
                for _, _, _, task, _ in jobs if _history_decision_keys([task], simulated=simulated) not in ((), ("joker_urna2",))
            ):
                raise ValueError("Ținta de bench s-a schimbat; pornește analiza din nou.")
        except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
            ui.notify(str(exc), type="warning")
            return
        _VIRTUAL_WF_CANCEL_REQUESTED.clear()
        _VIRTUAL_WF_DECISION_CHANGED.clear()
        STATE["virtual_wf_running"] = True
        if automatic:
            STATE["analysis_expanded"] = True
        # Păstrăm istoricul anterior până există efectiv pași ai noii rulări.
        STATE["virtual_wf_previous"] = bool(STATE.get("virtual_wf_results"))
        STATE["_virtual_history_simulated"] = simulated
        if not automatic:
            STATE["_virtual_physical_notice"] = ""
        STATE["virtual_wf_status"] = "Pornesc simularea istorică…" if simulated else "Pornesc analiza virtuală…"
        STATE["virtual_wf_dirty"] = True
        run_id = STATE.get("_bench_seen_run") if automatic else None
        provenance = {"decision": initial_decision, "input_hash": config.get("input_hash"),
                      "sources": source_hashes and {f"{k[0]}|{k[1]}": v for k, v in source_hashes.items()}}

    def worker() -> None:
        deadline = time.monotonic() + budget
        fatal_error = False
        decision_changed = False
        published = False
        records = []
        failure_notes = []

        def persist(status, *, complete=False):
            if not run_id:
                return
            try:
                from rebench_history_store import save
                saved = save(run_id, records, status, complete=complete, provenance=provenance)
                if not saved:
                    with STATE_LOCK:
                        STATE["virtual_wf_previous"] = True
                        STATE["virtual_wf_store_error"] = "O rulare Re-Bench mai nouă a fost lansată; istoricul acesta este precedent."
                        STATE["virtual_wf_dirty"] = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WF] Istoricul nu a putut fi păstrat local: %s", exc)
                with STATE_LOCK:
                    STATE["virtual_wf_store_error"] = f"Istoricul nu a putut fi salvat local: {exc}"
                    STATE["virtual_wf_dirty"] = True

        def decision_moved():
            if initial_decision[0] is not None and _VIRTUAL_WF_DECISION_CHANGED.is_set():
                return True
            try:
                return _virtual_decision_fingerprint(countries) != initial_decision
            except (OSError, ValueError):
                return True

        try:
            from loto_enterprise.core.walk_forward_adapter import run_honest_walk_forward

            for index, (fname, spec, frame, task, guarantee) in enumerate(jobs):
                # Sistemele complete se pot evalua virtual, fără a materializa
                # sute de mii de combinații; restul simulează variantele reale.
                complete = task.get("lr_scheme_code") is None and guarantee == spec.pick_n and int(task.get("max_variants") or 0) == 0
                job_simulated = simulated and (materialize_full or not complete)
                if complete and materialize_full:
                    from covering.large_pool import LEGACY_MAX_POOL, validate_large_pool_request
                    from loto_enterprise.core.walk_forward_adapter import _effective_pool_size, _wf_geometry
                    try:
                        pool = _effective_pool_size(int(task["pool_size"]), spec.geometry,
                                                    task.get("restrict_base_max", 0), task.get("restrict_base_min", 0))
                        _guarantee, condition, cap = _wf_geometry(pool, spec.geometry, guarantee,
                                                                 task.get("wheel_condition"), task.get("max_variants"))
                        if pool > LEGACY_MAX_POOL:
                            validate_large_pool_request(pool, spec.pick_n, _guarantee, cap, condition)
                    except (ValueError, TypeError, OverflowError):
                        job_simulated = False
                if _VIRTUAL_WF_CANCEL_REQUESTED.is_set() or time.monotonic() >= deadline:
                    break
                if decision_moved():
                    decision_changed = True
                    break
                game_deadline = min(deadline, time.monotonic() + max(60.0, (deadline - time.monotonic()) / (len(jobs) - index)))
                with STATE_LOCK:
                    STATE["virtual_wf_status"] = f"{'Simulare istorică' if job_simulated else 'Analiză virtuală'} {index + 1}/{len(jobs)}: {spec.name}…"
                    STATE["virtual_wf_dirty"] = True
                kw = {
                    "df_source": frame, "game_type": spec.geometry,
                    "pool_size": int(task["pool_size"]), "backtest_depth_percent": depth,
                    "lookback_percent": _effective_lookback_pct({"lookback": task.get("lookback")}),
                    "use_cache": True, "guarantee": guarantee, "wheel_condition": task.get("wheel_condition"),
                    "max_variants": int(task.get("max_variants") or 0) if job_simulated else 0, "recent_penalty_draws": task.get("recent_penalty_draws", 0),
                    "recent_penalty_factor": task.get("recent_penalty_factor", 0.5),
                    "restrict_base_max": task.get("restrict_base_max", 0),
                    "restrict_base_min": task.get("restrict_base_min", 0),
                    "max_consecutive_run": task.get("max_consecutive_run", 0),
                    **({"compact_results": True} if job_simulated else {"virtual_full_system": True}),
                    "should_cancel": lambda: _VIRTUAL_WF_CANCEL_REQUESTED.is_set() or time.monotonic() >= game_deadline or decision_moved(),
                    "should_skip_cache_write": decision_moved,
                }
                if not spec.is_romanian:
                    kw.update(game_key=spec.bench_key, country=spec.country)
                if job_simulated and task.get("lr_scheme_code") is not None:
                    kw["lr_scheme_code"] = task["lr_scheme_code"]
                    if task.get("dynamic_method") is True:
                        kw["dynamic_method"] = True
                meta = {}
                try:
                    flat, meta = run_honest_walk_forward(**kw)
                    if not job_simulated and (not meta.get("virtual_full_system") or any(not getattr(row, "virtual_full_system", False) for row in flat)):
                        raise ValueError("Rezultatul nu este identificat ca sistem virtual.")
                    if job_simulated and (meta.get("virtual_full_system") or any(getattr(row, "virtual_full_system", False) for row in flat)):
                        raise ValueError("Sistemul virtual nu poate fi afișat ca simulare de variante.")
                    record = {"name": spec.name, "game": spec.geometry, "game_id": spec.game_id,
                              "source": fname, "pool": task["pool_size"],
                              "source_sha256": source_hashes[(fname, spec.game_id)], "task": dict(task),
                              "guarantee": guarantee, "depth": depth, "flat": flat, "meta": meta, "simulated": job_simulated,
                              "lr_reference_only": not job_simulated,
                              "max_variants": int(task.get("max_variants") or 0), "wheel_condition": task.get("wheel_condition")}
                except Exception as exc:  # noqa: BLE001
                    fatal_error = True
                    logger.error("[WF VIRTUAL] %s: %s", spec.name, exc)
                    record = {"name": spec.name, "game": spec.geometry, "game_id": spec.game_id,
                              "source": fname, "pool": task["pool_size"], "guarantee": guarantee,
                              "depth": depth, "error": str(exc), "simulated": job_simulated,
                              "task": dict(task), "lr_reference_only": not job_simulated}
                if decision_moved() or (not record.get("error") and meta.get("decision_changed")):
                    decision_changed = True
                    break
                if record.get("error"):
                    failure_notes.append(f"{spec.name}: {record['error']}")
                elif meta.get("large_pool_wf_skipped"):
                    fatal_error = True
                    failure_notes.append(f"{spec.name}: {meta.get('skip_detail') or 'limita de resurse a fost depășită'}")
                records.append(record)
                if any(r.get("flat") for r in records):
                    published = True
                    with STATE_LOCK:
                        STATE["virtual_wf_results"] = list(records)
                        STATE["virtual_wf_previous"] = False
                        STATE["virtual_wf_dirty"] = True
                    persist("Istoric Re-Bench parțial; calculul era în curs.")
        except Exception as exc:  # noqa: BLE001
            fatal_error = True
            logger.error("[WF VIRTUAL] analiza a eșuat: %s", exc)
        finally:
            over_budget = time.monotonic() >= deadline
            cancelled = _VIRTUAL_WF_CANCEL_REQUESTED.is_set()
            complete = (len(records) == len(jobs) and not fatal_error and not decision_changed
                        and not cancelled and not over_budget
                        and all(r.get("flat") and not r.get("error") and not r.get("meta", {}).get("partial")
                                and not r.get("meta", {}).get("large_pool_wf_skipped") for r in records))
            with STATE_LOCK:
                STATE["virtual_wf_running"] = False
                # Fără un istoric anterior, arătăm eșecurile pe filele jocurilor;
                # nu înlocuim un istoric salvat când nu există pași noi.
                if not published and records and not STATE.get("virtual_wf_results"):
                    STATE["virtual_wf_results"] = list(records)
                    STATE["virtual_wf_previous"] = False
                if automatic and not cancelled:
                    STATE["analysis_expanded"] = True
                analysis_label = "Analiza istorică" if simulated else "Analiza virtuală"
                STATE["virtual_wf_status"] = (
                    f"{analysis_label} a fost anulată." if cancelled
                    else f"{analysis_label} s-a oprit: decizia de bench sau ținta s-a schimbat. Reia analiza." if decision_changed
                    else f"{analysis_label} a eșuat; vezi jurnalul." if fatal_error
                    else f"{analysis_label} s-a oprit la limita de timp." if over_budget
                    else f"{analysis_label} s-a încheiat." if complete
                    else f"{analysis_label} s-a încheiat cu rezultate parțiale." if published
                    else f"{analysis_label} s-a încheiat fără extrageri validate."
                )
                if published and not complete and (cancelled or decision_changed or fatal_error or over_budget):
                    STATE["virtual_wf_status"] += " Istoricul afișat este parțial."
                if failure_notes:
                    STATE["virtual_wf_status"] += " " + " | ".join(failure_notes)
                STATE["virtual_wf_dirty"] = True
                final_status = STATE["virtual_wf_status"]
                if (auto_generate_config is not None and not fatal_error and not decision_changed
                        and not _VIRTUAL_WF_CANCEL_REQUESTED.is_set()):
                    STATE["_autopilot_after_virtual"] = (auto_generate_config, countries, initial_decision)
            if published:
                persist(final_status, complete=complete)

    threading.Thread(target=worker, daemon=True).start()
    ui.notify("Calculez istoricul sistemelor selectate." if simulated else "Calculez istoricul sistemului complet virtual.", type="positive")
    results_panel.refresh()
    return True


def _history_lr_identity(record, context):
    """Read the launch and draw identities from the saved history, including older compact rows."""
    meta = record.get("meta") or {}
    run_scheme = meta.get("lr_scheme") or {}
    draw_scheme = (context.get("audit") or {}).get("lr_scheme") or context.get("lr_scheme") or {}
    codes = [value for value in (
        (record.get("task") or {}).get("lr_scheme_code"), meta.get("lr_scheme_code"),
        run_scheme.get("code"), context.get("lr_scheme_code"), draw_scheme.get("code"),
    ) if value is not None]
    signatures = [value for value in (
        run_scheme.get("catalog_signature"), draw_scheme.get("catalog_signature"),
    ) if value is not None]
    identity = {"lr_scheme_code": codes[0] if codes else None,
                "lr_catalog_signature": signatures[0] if signatures else None}
    if len({str(code) for code in codes}) > 1 or any(value != signatures[0] for value in signatures[1:]):
        identity["history_system_issue"] = "schema LR păstrată pentru extragere diferă de schema rulării"
    return identity


def _render_virtual_history(records=None, *, show_status: bool = True) -> None:
    records = list(STATE.get("virtual_wf_results") or []) if records is None else list(records)
    status = STATE.get("virtual_wf_status") if show_status else None
    if not records and not status:
        return
    modes = {bool(record.get("simulated")) for record in records}
    mixed = len(modes) > 1
    simulated = (modes == {True}) if records else bool(STATE.get("_virtual_history_simulated"))
    all_lr = bool(records) and simulated and all(
        not r.get("lr_reference_only") and any(
            source.get("lr_scheme_code") is not None
            for source in (r.get("task") or {}, r.get("meta") or {})
        ) for r in records
    )
    heading = ("📊 Istoricul variantelor LR" if all_lr else
               "📊 Istoricul sistemelor simulate și virtuale" if mixed else
               "📊 Istoricul variantelor simulate" if simulated else
               "📊 Istoric hits — sistem complet virtual")
    with ui.card().classes("w-full"):
        with ui.expansion(heading, value=True).classes("w-full"):
            if STATE.get("virtual_wf_previous"):
                ui.label("Istoric din rularea precedentă. Clasamentul poate folosi un Re-Bench mai nou.").classes("text-caption text-warning")
            ui.label(
                "Pentru rulările cu metodă fixă, Re-Bench poate include unele dintre extragerile evaluate."
            ).classes("text-caption text-warning")
            if status:
                ui.label(status).classes("text-caption")
            if STATE.get("virtual_wf_store_error"):
                ui.label(STATE["virtual_wf_store_error"]).classes("text-caption text-warning")
            if STATE.get("_virtual_physical_notice"):
                ui.label(STATE["_virtual_physical_notice"]).classes("text-caption text-warning")
            for record in records:
                simulated = bool(record.get("simulated"))
                spec = _analysis_record_spec(record) or _game_spec_for(record["game"])
                meta = record.get("meta") or {}
                lr_meta = dict(meta)
                if lr_meta.get("lr_scheme_code") is None and (record.get("task") or {}).get("lr_scheme_code") is not None:
                    lr_meta.update(lr_scheme_code=record["task"]["lr_scheme_code"], pool_size=record["pool"])
                lr_lines = _lr_history_lines(record["game"], lr_meta) if simulated and not record.get("lr_reference_only") else []
                system_note = ("schemă LR" if lr_lines else "referință sistem complet virtual" if record.get("lr_reference_only")
                               else f"garanție inițială {record['guarantee']}")
                ui.label(f"{record['name']} · pool cerut {record['pool']} · {system_note} · fereastră cerută {record['depth']}%").classes("text-bold")
                for line in lr_lines:
                    ui.label(line).classes("text-caption")
                if lr_lines and (record.get("task") or {}).get("dynamic_method") is True:
                    ui.label("Metoda LR a fost realeasă din zilele anterioare fiecărei extrageri.").classes("text-caption")
                ui.label(
                    "Istoricul nu stabilește că au fost jucate. "
                    + ("Codul LR păstrat în rulare este reconstituit pe pool-ul de atunci. " if lr_lines else
                       "Referința completă este o ipoteză fără schemă LR. " if record.get("lr_reference_only") else
                       "Schema LR disponibilă este reconstituită pe pool-ul de atunci; în lipsa unui cod se păstrează sistemul simulat sau virtual. ") +
                    "Premiul și costul corespund sistemului indicat pe rând. Exemplul de variantă este "
                    "construit după extragerea istorică, din variantele sistemului evaluat."
                ).classes("text-caption text-warning")
                if simulated and not lr_lines and not record.get("error"):
                    cap = int(record.get("max_variants") or 0)
                    condition = int(record.get("meta", {}).get("wheel_condition") or record.get("wheel_condition") or record["guarantee"])
                    ui.label(f"Simularea inițială: condiție {condition}; " + (f"plafon {cap} variante pe extragere." if cap else "fără plafon de variante.")).classes("text-caption")
                if record.get("error"):
                    ui.label(f"Analiză necalculată: {record['error']}").classes("text-negative")
                    continue
                meta = record["meta"]
                if meta.get("large_pool_wf_skipped"):
                    ui.label(f"Analiză necalculată: {meta.get('skip_detail') or 'limita de resurse a fost depășită'}").classes("text-warning")
                    continue
                ui.label(
                    f"Extrageri validate: {meta.get('n_test_draws', 0)} din {meta.get('n_expected', 0)} evaluabile. "
                    + ("Validare parțială; pot exista goluri." if meta.get("partial") else "Validare completă pentru fereastra cerută.")
                ).classes("text-caption")
                if meta.get("decision_changed"):
                    ui.label(
                        "Decizia de bench s-a schimbat în timpul validării; pașii pot folosi metode diferite."
                    ).classes("text-caption text-warning")
                dates = sorted(
                    date for entry in record["flat"]
                    if (date := _parse_draw_date(entry.draw_date)) is not None
                )
                if dates:
                    ui.label(
                        f"Prima extragere validată: {dates[0].strftime('%d-%m-%Y')}; "
                        f"ultima: {dates[-1].strftime('%d-%m-%Y')}."
                    ).classes("text-caption")
                    loaded = next((df for name, df in STATE.get("datasets", ())
                                   if name == record.get("source")), None)
                    latest = _parse_draw_date(_csv_last_date(loaded))
                    if latest is not None and latest > dates[-1]:
                        ui.label(
                            f"CSV-ul încărcat ajunge la {latest:%d-%m-%Y}. "
                            "Această validare se oprește înainte de ultima extragere."
                        ).classes("text-caption text-warning")
                # Datele compacte au un rând pe extragere; cache-urile complete
                # pot avea mai multe variante. Numitorul rămâne numărul extragerilor.
                per_draw = {}
                for entry in record["flat"]:
                    key = int(entry.draw_index)
                    bucket = per_draw.setdefault(key, [])
                    bucket.append(entry)
                flat = [max(group, key=lambda entry: int(entry.hits)) for group in per_draw.values()]
                from ui_prizes import history_played_system
                from ui_hits import _wf_marked_numbers, _WF_MARKED_CELL
                played = {}
                for entry in flat:
                    context = entry.ticket_context or {}
                    jokers = context.get("hard_core_joker") or []
                    played[int(entry.draw_index)] = history_played_system(record["game"], {
                        "country": meta.get("country", "RO"),
                        "label": str(entry.draw_date or ""), "drawn": list(context.get("actual") or []),
                        "pool_numbers": list(context.get("hard_core") or []),
                        "best_variant_raw": list(entry.variant or []), "best_variant_joker_hit": getattr(entry, "joker_hit", None),
                        "chosen_joker": jokers[0] if jokers else None,
                        "best_ticket": int(entry.hits),
                        "generated_variants": [list(getattr(e, "variant", None) or []) for e in per_draw[int(entry.draw_index)]],
                        "expected_variant_count": getattr(entry, "variant_count", None),
                        **_history_lr_identity(record, context),
                        "variant_prize_groups": getattr(entry, "variant_prize_groups", None),
                        "virtual_full_system": bool(getattr(entry, "virtual_full_system", False)),
                        "lr_reference_only": bool(record.get("lr_reference_only")),
                    })
                has_lr = any(d.get("lr_scheme_game") is not None for d in played.values())
                if not has_lr:
                    ui.label(
                        ("Variante simulate pentru fiecare extragere pe baza datelor anterioare; "
                         "nu stabilește că au fost jucate. Premiile afișate, când sunt disponibile, "
                         "sunt estimări brute pe baza valorilor oficiale. Varianta afișată este "
                         "una dintre cele simulate, aleasă după verificarea extragerii." if simulated else
                         "Analiza teoretică nu generează bilete și nu stabilește că au fost jucate; nu se atribuie premii. "
                         "Exemplul de variantă este construit după extragerea istorică din numerele nimerite; "
                         "nu este o variantă anticipată.")
                    ).classes("text-caption text-warning")
                if flat:
                    verified = [data for data in played.values() if not data.get("history_system_issue")]
                    if len(verified) < len(flat):
                        ui.label(f"Sistem verificabil pentru {len(verified)} din {len(flat)} extrageri; "
                                 "procentele pe variante folosesc numai aceste extrageri.").classes("text-caption text-warning")
                    thresholds = range(4, spec.draw_n + 1)
                    def _rate(n, total):
                        return f"{n} ({n / total * 100:.2f}%)" if total else "—"
                    summary = []
                    for threshold in thresholds:
                        summary.append({
                            "threshold": f"{threshold}+",
                            "pool": _rate(sum(int(p.hits_union) >= threshold for p in flat), len(flat)),
                            "ticket": (
                                _rate(sum(int(data["best_ticket"]) >= threshold for data in verified), len(verified))
                                if threshold <= spec.pick_n else "—"
                            ),
                        })
                    ui.label(_history_hit_note(spec)).classes("text-caption")
                    if spec.geometry == "joker":
                        known = [entry.joker_hit for entry in flat if getattr(entry, "joker_hit", None) is not None]
                        if known:
                            ui.label(f"Urna 2: bila Joker nimerită la {sum(known)} din {len(known)} "
                                     "extrageri cu rezultat cunoscut.").classes("text-caption")
                    ui.table(columns=[
                        {"name": "threshold", "label": "Prag cumulativ", "field": "threshold"},
                        {"name": "pool", "label": "Pool-ul de atunci", "field": "pool"},
                        {"name": "ticket", "label": "Pe cel puțin o variantă LR" if has_lr else "Pe cel puțin o variantă simulată" if simulated else "Pe o variantă teoretică", "field": "ticket"},
                    ], rows=summary, row_key="threshold").classes("w-full").props("dense wrap-cells")
                rows = []
                pool_records = {}
                for entry in sorted(flat, key=lambda x: int(x.draw_index), reverse=True):
                    # The prize table displays pool hits 4+, while summaries
                    # above keep every validated draw in their denominator.
                    if int(entry.hits_union) < 4:
                        continue
                    context = entry.ticket_context or {}
                    data = played[int(entry.draw_index)]
                    variant = data["best_variant_raw"]
                    issue = data.get("history_system_issue")
                    try:
                        pool_text, pool_marks = _wf_marked_numbers(data["pool_numbers"], data["drawn"])
                    except (TypeError, ValueError, OverflowError):
                        pool_text, pool_marks = "—", []
                    pool_size = len({mark["v"] for mark in pool_marks})
                    if record["game"] == "joker" and len(variant) == 6:
                        example = " ".join(map(str, variant[:5])) + f" · Joker {variant[5]}"
                    else:
                        example = " ".join(map(str, variant))
                    pool_records[int(entry.draw_index)] = data
                    rows.append({
                        "_draw_index": int(entry.draw_index), "pool_prize": "se încarcă premiile oficiale",
                        "draw_index": int(entry.draw_index),
                        "date": str(entry.draw_date or f"#{entry.draw_index}"),
                        "drawn": " ".join(map(str, context.get("actual") or [])) or "—",
                        "pool": pool_text, "pool_marks": pool_marks, "pool_size": pool_size or "—",
                        "system": data["system_label"],
                        "pool_hits": int(entry.hits_union), "best_hits": "—" if issue else data["best_ticket"],
                        "example": "—" if issue else (example or "—"),
                        **({"joker_hit": {True: "Da", False: "Nu"}.get(getattr(entry, "joker_hit", None), "—")}
                           if spec.geometry == "joker" else {}),
                        "variants": "—" if issue else f"{int(data['expected_variant_count'] or len(per_draw[int(entry.draw_index)])):,}".replace(",", "."),
                    })
                if rows:
                    ui.label("Detalii pentru extragerile cu cel puțin 4 numere nimerite în pool.").classes("text-caption")
                    romanian = str(meta.get("country") or "RO").upper() == "RO" and record["game"] in ("6/49", "5/40", "joker")
                    holder = {}
                    def _pool_page(event, holder=holder):
                        if holder.get("binding") is not None:
                            holder["binding"].set_pagination(event.value)
                    ui.label(
                        ("Premiul brut estimat și costul folosesc exact codul LR păstrat în rulare. " if lr_lines else
                         "Referința completă nu atribuie premiile unei scheme LR. " if record.get("lr_reference_only") else
                         "Premiul brut estimat și costul folosesc schema redusă LR disponibilă pentru pool-ul de atunci; "
                         "la mai multe coduri se alege cel cu cele mai puține variante. În lipsa unei scheme LR, se folosesc variantele simulate. ") +
                        "Codul și numărul de variante sunt afișate pe fiecare rând. "
                        "Sumele sunt estimări la tariful actual și premiile unitare publicate, fără taxa pe bilet; "
                        "reconstituirea nu dovedește că variantele au fost jucate."
                    ).classes("text-caption text-grey") if romanian else None
                    table = ui.table(columns=[
                        {"name": "date", "label": "Extragere", "field": "date"},
                        {"name": "drawn", "label": "Numere extrase", "field": "drawn"},
                        {"name": "pool_size", "label": "Numere în pool", "field": "pool_size"},
                        {"name": "pool", "label": "Pool-ul de atunci", "field": "pool"},
                        {"name": "pool_hits", "label": "În pool", "field": "pool_hits"},
                        {"name": "system", "label": "Sistem evaluat", "field": "system"},
                        {"name": "best_hits", "label": "Maxim pe variantele evaluate" if has_lr else "Maxim pe variantele simulate" if simulated else "Maxim teoretic pe variantă", "field": "best_hits"},
                        *([{"name": "joker_hit", "label": "Urna 2 nimerită", "field": "joker_hit"}]
                          if spec.geometry == "joker" else []),
                        {"name": "example", "label": "Variantă simulată, aleasă după verificare" if simulated else "Exemplu construit după extragere", "field": "example"},
                        {"name": "variants", "label": "Variante în sistemul evaluat" if has_lr else "Variante simulate" if simulated else "Combinații posibile calculate matematic", "field": "variants"},
                        *([{"name": "pool_prize", "label": "Premiu brut estimat · sistem evaluat", "field": "pool_prize"},
                           {"name": "pool_cost", "label": "Cost schemă / variante · tarif actual", "field": "pool_cost"}] if romanian else []),
                    ], rows=rows, row_key="draw_index", pagination=15,
                        **({"on_pagination_change": _pool_page} if romanian else {})).classes("w-full").props("dense wrap-cells")
                    table.add_slot("body-cell-pool", _WF_MARKED_CELL)
                    if romanian:
                        from ui_prizes import PoolPrizeBinding
                        from ui_hits import _WF_PRIZE_CELL
                        price = PRICES.get(record["game"])
                        for row in rows:
                            data = pool_records[row["_draw_index"]]
                            n = data["expected_variant_count"]
                            if n is None:
                                n = len(data["generated_variants"])
                            if data["virtual_full_system"] or data.get("history_system_issue"):
                                n = None
                            row["pool_cost"] = f"{_thousands(n)} × {price:g} = {_thousands(n * price)} lei" if n is not None and n > 0 and price else "—"
                        table.rows = rows
                        table.add_slot("body-cell-pool_prize", _WF_PRIZE_CELL)
                        binding = PoolPrizeBinding(record["game"], table, rows, pool_records)
                        holder["binding"] = binding
                        binding.request()
                        ui.button("Reîncearcă premiile indisponibile", on_click=lambda b=binding: b.request(retry_missing=True)).props("flat dense no-caps").classes("text-caption")
                        ui.timer(0.5, binding.refresh)
                else:
                    ui.label("Nu există extrageri cu 4+ în pool pentru această cerere." if flat else "Nu există extrageri validate pentru această cerere.").classes("text-caption")


def _walk_forward_depth_selector(label):
    options = {pct: f"Ultimele {pct}%" for pct in VALIDATION_PERCENTILES}
    options[100] = "Întreg istoricul evaluabil (100%)"
    selector = ui.select(options, value=_int_setting("wf_depth_pct"), label=label).classes("w-full")
    selector.bind_value(SETTINGS, "wf_depth_pct")
    selector.on_value_change(lambda: _save_settings())


def _render_virtual_controls() -> None:
    _walk_forward_depth_selector("Istoric de validare pentru următoarea rulare")
    if _selected_country() == _LOT.RO:
        ui.link("Validare walk-forward pentru selecția LR", "#lr-walk-forward")
    ui.label(
        "Istoricul general se calculează automat după Re-Bench general, pentru setările și istoricele acelei rulări. "
        "Bugetul walk-forward poate opri calculul înainte de toate extragerile."
    ).classes("text-caption text-grey")


# --------------------------------------------------------------------------- #
# UI — randare
# --------------------------------------------------------------------------- #
# Mesajul de FAILED vine din `result_json`; îl trunchiem ca să nu inunde panoul.
_FAIL_MSG_MAX_CHARS = 800
_UNSTARTED_WORKER_WAIT_S = 45.0


_JOB_PROBE = JobStatusProbe()
_COMPLETION_LOCK = threading.Lock()
_COMPLETION_STAGES = {}


def _complete_job_stage(job_id, stage):
    if job_id is None:
        return
    with _COMPLETION_LOCK:
        stages = _COMPLETION_STAGES.setdefault(job_id, set())
        if stage is not None:
            stages.add(stage)
        ready = {"smtp", "shutdown"} <= stages
    if ready and (_mark_job_finalized(job_id) or (get_job_status(job_id) or {}).get("ui_finalized_at")):
        with _COMPLETION_LOCK:
            _COMPLETION_STAGES.pop(job_id, None)


_PENDING_ACTIONS = {}
_CLAIM_DELAYS = (1.0, 3.0)


def _retry_job_claims():
    """Called from a background timer and on polling. Only one caller takes a retry."""
    with _COMPLETION_LOCK:
        ready = [(key, attempt, operation) for key, (due, attempt, operation) in _PENDING_ACTIONS.items()
                 if time.monotonic() >= due]
        for key, _, _ in ready:
            del _PENDING_ACTIONS[key]
    for (job_id, action), attempt, operation in ready:
        try:
            if _perform_job_action(job_id, action, operation, attempt=attempt):
                _complete_job_stage(job_id, action)
        except Exception as exc:
            logger.error("[JOB] confirmarea acțiunii %s pentru #%s: %s", action, job_id, exc)


def _perform_job_action(job_id, action, operation, *, attempt=0):
    """Retry a transient claim failure. A claimed external action is never replayed."""
    try:
        claimed = claim_job_finalization_action(job_id, action)
    except (sqlite3.OperationalError, OSError) as exc:
        if attempt < len(_CLAIM_DELAYS):
            delay = _CLAIM_DELAYS[attempt]
            with _COMPLETION_LOCK:
                _PENDING_ACTIONS[job_id, action] = time.monotonic() + delay, attempt + 1, operation
            wake = threading.Timer(delay, _retry_job_claims)
            wake.daemon = True
            wake.start()
            logger.warning("[JOB] revendicarea %s pentru #%s se reia în %.1f s: %s", action, job_id, delay, exc)
            return False
        message = f"Job #{job_id}: acțiunea {action} nu a pornit. Revendicarea a eșuat după {attempt+1} încercări: {exc}"
        logger.error(message)
        if STATE.get("_result_job_id") == job_id:
            STATE.update(_job_notice=message, results_dirty=True)
        return True
    if not claimed:
        return True
    state, detail = "uncertain", None
    try:
        result = operation()
        state, detail = result if isinstance(result, tuple) else (result or "done", None)
    except Exception as exc:
        detail = str(exc)
        logger.exception("Job #%s: %s", job_id, action)
    finish_job_finalization_action(job_id, action, state, detail)
    return True


def _job_action_snapshot(job_id):
    actions = get_job_finalization_actions(job_id)
    details = get_job_finalization_details(job_id) if any(s in ("failed", "uncertain") for s in actions.values()) else {}
    return {"actions": actions, "details": details}


def _complete_live_generation(job_id, row):
    payload = decode_queue_result(str(row.get("result_json") or "{}"))
    if not (isinstance(payload, tuple) and len(payload) == 2):
        _mark_job_finalized(job_id)
        with STATE_LOCK:
            if STATE.get("active_job_id") == job_id:
                STATE["active_job_id"] = None
                STATE["_job_notice"] = f"Job #{job_id}: rezultat ilizibil. Verifică loto.log și regenerează."
                STATE["results_dirty"] = True
        return
    sources = _result_sources_from_job(row)
    with STATE_LOCK:
        if STATE.get("active_job_id") != job_id:
            return
        if STATE.get("job_start_time") and STATE.get("job_elapsed") is None:
            STATE["job_elapsed"] = time.time() - STATE["job_start_time"]
        generation_epoch = STATE.get("_cancellation_epoch", 0)
        STATE.update(results=payload, result_sources=sources, results_recovered=None,
                     active_job_id=None, results_dirty=True, _result_job_id=job_id, _job_notice=None)
        try:
            snapshot = _mail_snapshot_inputs(payload, sources)
        except Exception as exc:
            logger.error("[MAIL] datele mesajului pentru jobul #%s: %s", job_id, exc)
            snapshot = None
    try:
        _save_report_file()
    except Exception as exc:
        logger.error("[JOB] salvare raport #%s: %s", job_id, exc)

    if snapshot is not None and snapshot["enabled"]:
        try:
            _freeze_mail_ratings(snapshot)
        except Exception as exc:
            logger.error("[MAIL] rating indisponibil pentru jobul #%s: %s", job_id, exc)
            snapshot = None

    def send_snapshot():
        try:
            if snapshot is None or snapshot["enabled"]:
                def send():
                    if snapshot is None:
                        return "failed"
                    return _maybe_send_results_email(snapshot)
                if not _perform_job_action(job_id, "smtp", send):
                    return
            _complete_job_stage(job_id, "smtp")
        except Exception as exc:
            logger.error("[MAIL] starea jobului #%s: %s", job_id, exc)
            if STATE.get("_result_job_id") == job_id:
                STATE["_job_notice"] = "Confirmarea trimiterii e-mailului este indisponibilă. Verifică loto.log."
                STATE["results_dirty"] = True

    if snapshot is not None and snapshot["enabled"]:
        threading.Thread(target=send_snapshot, name="mail-results", daemon=True).start()
    else:
        send_snapshot()
    with STATE_LOCK:
        if (STATE.get("_result_job_id") == job_id and not STATE.get("active_job_id")
                and STATE.get("_cancellation_epoch", 0) == generation_epoch):
            _start_walk_forward()
            return
    _complete_job_stage(job_id, "shutdown")


def _read_ui_job(job_id):
    """Read the queue and publish any terminal transition on a background thread."""
    _retry_job_claims()
    if STATE.get("active_job_id") != job_id:
        if job_id in _COMPLETION_STAGES:
            _complete_job_stage(job_id, None)
        return {"job": None, **_job_action_snapshot(job_id)}
    row = get_job_status(job_id)
    if STATE.get("active_job_id") == job_id and is_unstarted_job(row):
        start = STATE.get("job_start_time")
        if start is not None:
            ensure_worker_running()
        if start is None or (time.time() - start > _UNSTARTED_WORKER_WAIT_S and not is_worker_running()):
            reason = f"Worker-ul nu a preluat jobul #{job_id}."
            if cancel_unstarted_job(job_id, reason):
                with STATE_LOCK:
                    if STATE.get("active_job_id") == job_id:
                        STATE.update(active_job_id=None, job_start_time=None, _job_notice=reason, results_dirty=True)
            # The worker may have claimed or completed the job since the first read.
            row = get_job_status(job_id)

    if STATE.get("active_job_id") == job_id:
        if row is not None and row.get("status") == "COMPLETED":
            _complete_live_generation(job_id, row)
        elif row is None or row.get("status") in ("FAILED", "CANCELLED"):
            message = (str(row.get("result_json") or row.get("log_tail") or "Vezi loto.log.")[-_FAIL_MSG_MAX_CHARS:]
                       if row else "Jobul nu mai există în coadă.")
            with STATE_LOCK:
                if STATE.get("active_job_id") == job_id:
                    STATE.update(active_job_id=None, _job_notice=f"Job #{job_id}: {message}", results_dirty=True)
        elif not is_unstarted_job(row):
            ensure_worker_running()
    actions = _job_action_snapshot(job_id) if row and row.get("status") == "COMPLETED" else {}
    return {"job": row, **actions}


@ui.refreshable
def status_panel() -> None:
    job_id = STATE.get("active_job_id")
    bench_on = STATE.get("_status_bench_running", False)
    observed_id = job_id or STATE.get("_result_job_id")
    snapshot, unavailable = _JOB_PROBE.view(observed_id)
    boot_waiting = (STATE.get("_queue_boot_pending") and
                    time.monotonic() - STATE.get("_queue_boot_start", time.monotonic()) > 1.5)
    if unavailable or boot_waiting or STATE.get("_queue_boot_unavailable"):
        ui.label("Starea cozii este temporar indisponibilă. Afișez ultimele date primite.").classes("text-warning")
    if notice := STATE.get("_job_notice"):
        ui.label(notice).classes("text-negative")
    actions = (snapshot or {}).get("actions") or {}
    for action, label in (("smtp", "E-mail"), ("shutdown", "Oprire PC")):
        diagnostic = ((snapshot or {}).get("details") or {}).get(action)
        if diagnostic:
            ui.label(f"{label}: {diagnostic}").classes("text-caption")
        if actions.get(action) in ("claimed", "uncertain"):
            ui.label(f"{label}: rezultat încă neconfirmat. Acțiunea nu se repetă automat.").classes("text-warning")
        elif actions.get(action) == "failed":
            ui.label(f"{label}: acțiunea a eșuat.").classes("text-negative")

    if job_id:
        stt = (snapshot or {}).get("job")
        if not stt:
            ui.label(f"Se citește starea jobului #{job_id}…").classes("text-caption")
            return
        pct = int(stt.get("progress_pct") or 0)
        if is_unstarted_job(stt):
            t0 = STATE.get("job_start_time")
            waited = max(0.0, time.time() - float(t0)) if t0 else 0.0
            current = "aștept worker-ul..." if waited <= _UNSTARTED_WORKER_WAIT_S else "worker-ul termină jobul anterior…"
            elapsed_txt = f" · scurs {_fmt_dur(waited)}"
            ui.label(f"⏳ Job în rulare (#{job_id}) — {pct}%{elapsed_txt}").classes(
                "text-bold"
            )
            ui.linear_progress(value=0, show_value=False).props("instant-feedback")
            ui.label(f"➡️ {current}").classes("text-caption text-info")
            return
        with ui.card().classes("w-full"):
            tail = str(stt.get("log_tail") or "").strip()
            lines = tail.splitlines() if tail else []
            current = lines[-1] if lines else "se inițializează..."
            elapsed_txt = ""
            if STATE.get("job_start_time"):
                elapsed_txt = (
                    f" · scurs {_fmt_dur(time.time() - STATE['job_start_time'])}"
                )
            # Worker mort (kill -9 / crash) lăsa jobul RUNNING la infinit:
            # reatașarea de la startup cheamă ensure o dată; _tick nu o refăcea.
            ui.label(f"⏳ Job în rulare (#{job_id}) — {pct}%{elapsed_txt}").classes(
                "text-bold"
            )
            ui.linear_progress(value=pct / 100.0, show_value=False).props(
                "instant-feedback"
            )
            ui.label(f"➡️ {current}").classes("text-caption text-info")
            if len(lines) > 1:
                with ui.expansion(
                    f"Pași detaliați ({len(lines)})", value=False
                ).classes("w-full"):
                    ui.code("\n".join(lines[-15:]), language="text").classes(
                        "w-full max-h-48 overflow-auto text-xs"
                    )
        return

    if bench_on:
        _start = None
        try:
            _p = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")
            _start = float(_p[1]) if len(_p) > 1 else None
        except Exception:  # noqa: BLE001
            pass
        rc = _bench_progress_from(BENCH_LOG_FILE, _start)
        with ui.card().classes("w-full"):
            if rc:
                ui.html(
                    render_html_safe(
                        t"🔬 <b style='color:#38bdf8'>RE-BENCH</b> — {rc[1]}"
                    )
                )
                ui.linear_progress(value=rc[0], show_value=False).props(
                    "instant-feedback"
                ).classes("w-full")
            ui.label(_bench_status_text()).classes("text-caption")
            ui.html(_hw_telemetry_html())  # consum live CPU/RAM
        # Clasament PARȚIAL live: metodele apar pe măsură ce termină.
        _render_bench_live_leaderboard(_start, progress=(rc[0] if rc else None))
        return

    _shutdown_banner()
    if isinstance(STATE.get("results"), tuple):
        rec = STATE.get("results_recovered")
        if rec:
            # Rezultate recuperate dintr-o sesiune anterioară (job vechi, neprelucrat la
            # momentul lui) → avertizăm CLAR: nu sunt din rularea curentă.
            ui.label(
                f"⚠️ Rezultate RECUPERATE dintr-o sesiune anterioară ({rec}) — "
                "verifică data extragerii înainte să joci; re-rulează pentru numere noi."
            ).classes("text-warning text-bold")
        else:
            ui.label("✅ Ultima generare e gata (vezi mai jos).").classes(
                "text-positive"
            )
    else:
        ui.label(
            "Generator disponibil."
        ).classes("text-caption")


SOUND_JS = (
    "try{const c=new (window.AudioContext||window.webkitAudioContext)();"
    "const o=c.createOscillator();const g=c.createGain();o.connect(g);g.connect(c.destination);"
    "o.type='sine';o.frequency.value=880;g.gain.value=0.08;o.start();"
    "o.stop(c.currentTime+0.35);}catch(e){}"
)


def _next_draw_date(weekdays=(3, 6), last_draw=None) -> str:
    """Următoarea extragere (Loteria Română: 6/49, 5/40, Joker — JOI și DUMINICĂ;
    alt joc: zilele lui din registru), de azi inclusiv.

    `last_draw` = ziua ultimei extrageri din istoricul rezultatului: căutarea
    pornește STRICT după ea. Generat joi seara, după ce extragerea de joi a
    intrat în CSV, pool-ul e pentru duminică, nu pentru extragerea de azi, deja
    trasă. Fără dată (istoric fără date, mail de test): de azi, ca înainte."""
    from datetime import timedelta

    start = _dt.now().date()
    if last_draw is not None:
        start = max(start, last_draw + timedelta(days=1))
    return _next_draw_date_for(weekdays, start)


def _result_last_draw_day(fname: str):
    """Ziua ultimei extrageri valide din snapshot-ul rezultatului, sau None."""
    from loto_enterprise.core.history import history_dates

    info = _last_csv_draw(fname)
    if not info or not info[0]:
        return None
    try:
        return history_dates(pd.DataFrame({"date": [info[0]]})).iloc[0].date()
    except (TypeError, ValueError, OverflowError):
        return None


def _results_games() -> list:
    """(fișier, Lottery) pentru jocurile rezultatului afișat, din ecoul worker-ului."""
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return []
    return [
        (fn, _game_spec_for(g, _primary_pool_data(d)))
        for fn, outs in results[0]
        for g, d in outs.items()
    ]


def _results_specs() -> list:
    """Jocurile (Lottery) din rezultatul afișat, din ecoul worker-ului."""
    return [sp for _fn, sp in _results_games()]


def _ro_next_draw_date() -> str:
    """Extragerea românească pentru care sunt numerele rezultatului.

    Cele trei jocuri se trag în aceeași seară: o extragere aflată în oricare
    istoric al rezultatului a avut deja loc."""
    days = [d for d in (_result_last_draw_day(fn) for fn, _sp in _results_games()) if d]
    return _next_draw_date(last_draw=max(days) if days else None)


def _mail_method_context(spec, data: dict, *, urna2: bool = False) -> tuple:
    """Stable audit identity for the scorer and benchmark pool used by the job."""
    key = (spec.bench_key_urna2 or f"{spec.bench_key}_urna2") if urna2 else spec.bench_key
    info = ((data.get("audit") or {}).get("bench_winner") or {}).get(key) or {}
    pool = (
        1 if urna2 else info.get("pool_hint") or data.get("pool_size_requested")
        or data.get("pool_size") or len(data.get("hard_core") or []) or None
    )
    pool = int(pool) if pool else None
    method = info.get("method") or (info.get("ensemble") or [{}])[0].get("method")
    return key, pool, info, method


def _mail_snapshot_inputs(results=None, result_sources=None) -> dict:
    """Copy only email inputs while the caller owns STATE_LOCK.

    Large variant lists and walk-forward results are deliberately omitted.
    Frames are copied once per filename; no later upload or in-place change
    can replace the history belonging to this completed job.
    """
    results = STATE.get("results") if results is None else results
    snapshot = {
        "generated_at": _dt.now(),
        "enabled": bool(SETTINGS.get("mail_on_complete")),
        "valid": isinstance(results, tuple) and len(results) == 2,
        "games": [], "sources": {}, "decisions": {}, "exact_ratings": {},
    }
    if not snapshot["valid"]:
        return snapshot
    fields = (
        "hard_core", "hard_core_joker", "joker_pool", "joker_pool_size", "pool_size",
        "pool_size_requested", "lr_scheme_code", "dynamic_method", "lookback", "total_draws",
    )
    audit_fields = (
        "bench_winner", "recent_penalty", "lookback_pct", "restrict_base", "consecutive_limit",
        "hash", "dynamic_selection", "lr_scheme",
    )
    for fname, outs in results[0]:
        if fname not in snapshot["sources"]:
            source = result_sources.get(fname) if result_sources is not None else _result_source(fname)
            snapshot["sources"][fname] = source.copy(deep=True) if isinstance(source, pd.DataFrame) else None
        for game, raw_data in outs.items():
            primary = _primary_pool_data(raw_data)
            spec = _game_spec_for(game, primary)
            data = {key: deepcopy(primary[key]) for key in fields if key in primary}
            context = primary.get("context") or {}
            data["context"] = {key: deepcopy(context[key]) for key in ("last_3", "lr_scheme_code") if key in context}
            variants = primary.get("variants")
            if isinstance(variants, (list, tuple)):
                data["generated_variant_count"] = len(variants)
                if spec.game_id == "joker":
                    _, missing, invalid = _joker_variant_balls(variants, spec.pick_n)
                    data["incomplete_joker_count"] = missing + invalid
            data.update(game_id=spec.game_id, country=spec.country)
            audit = primary.get("audit") or {}
            data["audit"] = {key: deepcopy(audit[key]) for key in audit_fields if key in audit}
            code = _result_lr_scheme_code(primary)
            if code is not None:
                data["lr_scheme_code"] = deepcopy(code)
                if isinstance(variants, (list, tuple)) and _generated_system_identity(game, primary)[0] != "lr":
                    data["lr_layout_mismatch"] = True
            record = {"fname": fname, "game": game, "spec": spec, "data": data}
            if spec.is_romanian:
                frame = snapshot["sources"][fname]
                job = frame.attrs.get("lr_mail_settings", {}) if frame is not None else {}
                record["lr_mail_settings"] = {
                    "budget_lei": initial_budget_amount(job.get("lr_budget_lei", SETTINGS.get("lr_budget_lei"))),
                    "objective": job.get("lr_budget_objective", _budget_objective()),
                    **({"budget_mode": job["lr_budget_mode"]} if "lr_budget_mode" in job else {}),
                    "hit_target": job.get("bench_hit_target", _clamped_bench_target()),
                    "dynamic": job.get("dynamic_method", data.get("dynamic_method", False)),
                    "lookback": job.get("lookback", data.get("lookback", 0)),
                    "joker_size": job.get("joker_pool_size", data.get("joker_pool_size", 1)),
                }
            snapshot["games"].append(record)
    return snapshot


def _prepare_mail_alternatives(snapshot: dict) -> None:
    """Calculate from the completed job's detached inputs, outside the UI lock."""
    from loto_enterprise.core.lr_mail_forecast import prepare_inverse_mail_pools

    for record in snapshot["games"]:
        settings = record.get("lr_mail_settings")
        if settings is None or "pool_alternatives" in record:
            continue
        try:
            record["pool_alternatives"] = prepare_inverse_mail_pools(
                record, snapshot["sources"].get(record["fname"]), **settings,
            )
        except Exception:
            logger.exception("[MAIL] Pool invers indisponibil pentru %s", record["game"])
            record["pool_alternatives"] = {"inverse_reduced": {
                "number_range": LR_RESTRICTED_RANGE, "error": "Calculul pool-ului invers a eșuat.",
            }}


def _freeze_mail_ratings(snapshot: dict) -> dict:
    """Detach the relevant decisions and scalar ratings outside STATE_LOCK."""
    for record in snapshot["games"]:
        spec, data = record["spec"], record["data"]
        for urna2 in (False, True) if data.get("hard_core_joker") else (False,):
            key, pool, info, method = _mail_method_context(spec, data, urna2=urna2)
            if info.get("dynamic_selection"):
                continue
            identity = (key, pool)
            if identity not in snapshot["decisions"]:
                snapshot["decisions"][identity] = deepcopy(_decision_entry(key, pool)) if pool else {}
            entry = snapshot["decisions"][identity]
            rating_identity = (key, pool, method)
            if rating_identity in snapshot["exact_ratings"]:
                continue
            exact = None
            if pool and method and not (info.get("fallback") or info.get("no_decision")):
                try:
                    exact = _mail_exact_rating(key, pool, method, entry)
                except Exception as exc:  # noqa: BLE001 - numbers remain usable without a rating
                    logger.warning("[MAIL] rating indisponibil pentru %s k%s: %s", key, pool, exc)
            snapshot["exact_ratings"][rating_identity] = deepcopy(exact)
    return snapshot


def _capture_mail_snapshot() -> dict:
    """Capture the current result for previews/direct callers, without sending."""
    with STATE_LOCK:
        snapshot = _mail_snapshot_inputs()
    return _freeze_mail_ratings(snapshot)


def _mail_draw_info(snapshot: dict, record: dict):
    return _last_draw_from_source(snapshot["sources"].get(record["fname"]), record["spec"])


def _mail_draw_date(snapshot: dict, record: dict | None = None) -> str:
    """Target day from the captured timestamp and captured source boundaries."""
    from datetime import timedelta
    from loto_enterprise.core.history import history_dates

    records = [record] if record is not None else snapshot["games"]
    start = snapshot["generated_at"].date()
    for item in records:
        info = _mail_draw_info(snapshot, item)
        if info and info[0]:
            try:
                last = history_dates(pd.DataFrame({"date": [info[0]]})).iloc[0].date()
                start = max(start, last + timedelta(days=1))
            except (TypeError, ValueError, OverflowError):
                pass
    weekdays = record["spec"].draw_weekdays if record is not None else (3, 6)
    return _next_draw_date_for(weekdays, start)


def _mail_subject_date(snapshot: dict | None = None) -> str:
    snapshot = _capture_mail_snapshot() if snapshot is None else snapshot
    games = snapshot["games"]
    if not games or all(item["spec"].is_romanian for item in games):
        return _mail_draw_date(snapshot)
    dates = [_mail_draw_date(snapshot, item) for item in games]
    return min(dates, key=lambda day: _dt.strptime(day, "%d-%m-%Y"))


def _build_mail_body(snapshot: dict | None = None) -> str:
    """Compose one completed job's concise email from detached inputs only."""
    snapshot = _capture_mail_snapshot() if snapshot is None else snapshot
    if not snapshot["valid"]:
        return "Nu există rezultate de generare."
    _prepare_mail_alternatives(snapshot)

    def _nums(seq):
        return " ".join(str(int(x)) for x in sorted(seq)) if seq else "—"

    games = snapshot["games"]
    all_ro = all(item["spec"].is_romanian for item in games)
    lines = [
        f"📅 Extragere (următoarea, Joi/Duminică): {_mail_draw_date(snapshot)}"
        if all_ro else "📅 Următoarea extragere: pe fiecare joc, mai jos",
        f"(generat: {snapshot['generated_at'].strftime('%d-%m-%Y %H:%M')})", "",
    ]
    for record in sorted(games, key=lambda item: _GAME_DISPLAY_ORDER.get(item["spec"].geometry, 99)):
        fname, game, spec, primary = (record[key] for key in ("fname", "game", "spec", "data"))
        bonus_pool = primary.get("joker_pool") if spec.game_id == "joker" and spec.is_romanian else None
        joker = sorted(int(x) for x in (bonus_pool or primary.get("hard_core_joker") or []))
        lines.append(f"=== {spec.display} ===")
        if not all_ro:
            lines.append(f"extragere: {_mail_draw_date(snapshot, record)} ({_weekdays_text(spec.draw_weekdays)})")
            if note := _training_only_note(spec):
                lines.append(f"({note})")
        info = _mail_draw_info(snapshot, record)
        if info:
            day, numbers, ball = info
            draw = " ".join(str(x) for x in numbers) + (f" + joker {ball}" if ball is not None else "")
            lines.append(f"ultima extragere CSV: {day or '?'} → {draw}")
        pool_line = "POOL:   " + _nums(primary.get("hard_core") or []) + (f"  | joker: {_nums(joker)}" if joker else "")
        alternatives = record.get("pool_alternatives")
        if spec.is_romanian and isinstance(alternatives, dict) and "inverse_reduced" in alternatives:
            pool_line += "  (pool normal)"
        lines.append(pool_line)
        mismatch = primary.get("lr_layout_mismatch")
        played_lr = primary.get("lr_scheme_code") is not None and not mismatch
        generated_count = primary.get("generated_variant_count")
        if spec.is_romanian and not played_lr and generated_count is not None:
            price = spec.price if spec.price is not None else PRICES.get(spec.geometry)
            cost = (f" (≈ {_thousands(generated_count * price)} lei în variante; taxa pe bilet se adaugă separat)"
                    if price is not None else " (tarif necunoscut)")
            lines.append(f"VARIANTE GENERATE: {_thousands(generated_count)}{cost}")
            if incomplete := primary.get("incomplete_joker_count"):
                lines.append(f"Variante fără bilă Joker validă: {incomplete}. Costul presupune completarea câmpului Joker.")
        if mismatch:
            lines.append(f"Codul LR {primary['lr_scheme_code']} din rezultat nu corespunde variantelor generate.")
        schemes = [] if mismatch else _lr_schemes_for(spec.game_id, primary)
        if schemes:
            price = spec.price if spec.price is not None else PRICES.get(spec.geometry, 8.0)
            prefix = "SCHEMĂ LR: " if played_lr else "REFERINȚĂ LR: "
            lines.append(prefix + " sau ".join(
                f"{scheme.label} ({scheme.size} var. ≈ {_thousands(scheme.size * price)} lei în variante; taxa pe bilet se adaugă separat)"
                for scheme in schemes
            ))
        lines.extend(_mail_method_lines(spec, primary, snapshot=snapshot))
        if joker:
            lines.extend(_mail_method_lines(spec, primary, urna2=True, snapshot=snapshot))
        lines.append(_mail_best_draw_line(spec, snapshot["sources"].get(fname), primary.get("hard_core")))
        if spec.is_romanian and alternatives:
            lines.extend(mail_pool_alternative_lines(alternatives, primary, has_joker=bool(spec.bench_key_urna2)))
        lines.append("")
    return "\n".join(lines).strip()

def _mail_best_draw_line(spec, df, pool) -> str:
    """Cea mai bună extragere din istoric pentru pool-ul de azi și ultima ei dată.

    Mailul pleacă înainte de walk-forward, deci linia se calculează pe CSV-ul
    încărcat: câte numere din pool au ieșit la fiecare extragere. E o privire
    retrospectivă (pool-ul e ales cu tot istoricul), nu o validare."""
    import numpy as np

    from loto_enterprise.core.draw_validation import valid_draw_matrix
    from loto_enterprise.core.history import chronological_history

    unavailable = "CEL MAI BUN REZULTAT: istoric indisponibil pentru acest joc"
    pool = {int(x) for x in (pool or [])}
    cols = [f"n{i}" for i in range(1, int(spec.draw_n) + 1)]
    if df is None or not pool or any(c not in df.columns for c in cols):
        return unavailable
    # Aceeași cronologie și aceleași extrageri valide ca ultima extragere din UI:
    # un CSV încărcat în ordine descrescătoare dădea altfel „ultima oară" o dată veche.
    try:
        df = chronological_history(df)
        matrix, valid = valid_draw_matrix(
            df, cols, draw_n=int(spec.draw_n), max_num=int(spec.max_n)
        )
    except (TypeError, ValueError, OverflowError):
        return unavailable
    df = df.loc[valid].reset_index(drop=True)
    if not len(matrix):
        return unavailable
    hits = pd.Series(np.isin(matrix, sorted(pool)).sum(axis=1), index=df.index)
    best = int(hits.max())
    idx = hits[hits == best].index
    last = idx[-1]
    when = str(df.at[last, "date"]) if "date" in df.columns else f"rândul {last + 1}"
    # Cât ar avea un pool oarecare de aceeași mărime: pool-ul de azi e ales pe
    # aceleași extrageri, deci numărătoarea singură ar părea un palmares.
    from math import comb

    k, n, m = len(pool), int(spec.draw_n), int(spec.max_n)
    chance = len(df) * comb(k, best) * comb(m - k, n - best) / comb(m, n)
    # Sub 1, cifre semnificative: „~0.0” s-ar citi „imposibil la întâmplare”.
    chance_txt = f"{chance:.1f}" if chance >= 0.95 else f"{chance:.2g}"
    times = "o dată" if len(idx) == 1 else f"de {count(len(idx), 'ori')}"
    return (
        f"CEL MAI BUN REZULTAT pe istoric ({count(len(df), 'extrageri', 'extragere')}, "
        f"pool-ul de azi): {best} numere din pool, ultima oară la {when}; {times} în "
        f"total, față de ~{chance_txt} pentru un pool oarecare de {k} numere. Privire "
        "înapoi, nu validare: pool-ul e ales pe aceleași extrageri."
    )


def _mail_method_lines(spec, data: dict, *, urna2: bool = False, snapshot: dict | None = None) -> list[str]:
    """Actual scorer and exact benchmark rating, never parsed from rounded prose."""
    key, pool, info, method = _mail_method_context(spec, data, urna2=urna2)
    tag = " JOKER" if urna2 else ""
    where = "pe Urna 2, top-1" if urna2 else f"la pool {pool}"
    if not method:
        return [f"METODĂ{tag}: necunoscută (rezultat fără audit de metodă)"]
    if info.get("dynamic_selection"):
        return _dynamic_selection_lines(info["dynamic_selection"])
    if info.get("no_decision") or info.get("fallback"):
        why = (
            f"metoda din decizie, {info['attempted']}, nu a putut fi folosită"
            if info.get("attempted") and not info.get("no_decision")
            else f"fără bench pentru {spec.display}"
        )
        return [f"METODĂ{tag}: {method} ({why}; rezervă implicită, fără rating)"]
    if snapshot is None:
        entry = deepcopy(_decision_entry(key, pool)) if pool else {}
        exact = _mail_exact_rating(key, pool, method, entry) if pool else None
    else:
        entry = snapshot["decisions"].get((key, pool), {})
        exact = snapshot["exact_ratings"].get((key, pool, method))
    head = f"METODĂ{tag}: {method} (câștigătoarea bench-ului {where})"
    now = _decision_entry_method(entry, key) if entry else method
    if now != method:
        return [head, f"RATING{tag}: indisponibil — decizia de bench s-a schimbat după generare "
                f"(acum alege {now or 'altă metodă'} {where}); ratingul ei nu "
                "descrie metoda care a produs aceste numere"]
    lines = [head, f"RATING{tag}: indisponibil — benchmarkul exact nu poate fi verificat pentru această metodă și acest pool"]
    if exact:
        target = int(exact["target"])
        rate, wilson = _rate_pct(exact["rate"], target), _rate_pct(exact["wilson"], target)
        baseline = _rate_pct(exact["baseline"], target)
        label = "top-1" if urna2 else f"{exact['label']} în pool"
        line = f"RATING{tag}: rată {label} {rate} față de {baseline} la întâmplare"
        line += (f"; scor Wilson (z=1) {wilson}; rata observată peste media "
                 f"aleatoare în {exact['beat']}/{exact['windows']} ferestre suprapuse (comparație descriptivă)")
        if _decision_low_confidence(entry) is True:
            line += "; încredere redusă: nicio metodă nu a trecut poarta de consistență"
        if not urna2:
            line += ". Rating calculat pe top-K brut, înainte de regulile de generare a poolului."
        lines[1] = line
    note = _multiplicity_note(entry)
    if note and _decision_low_confidence(entry) is not True:
        lines.append(note)
    if urna2:
        penalty = (data.get("audit") or {}).get("recent_penalty") or {}
        if int(penalty.get("draws") or 0) > 0 and penalty.get("penalized_urna2"):
            lines.append("Numărul Joker include penalizarea recentă; ratingul măsoară scorerul fără ea.")
    elif transform := _bench_transform_note(data):
        lines.append(transform)
    return lines

def _send_test_email() -> None:
    """Buton (declanșat de utilizator): trimite un mail de test ca să confirmi configul."""
    cfg = load_mail_config(PROJECT_ROOT)
    if not cfg:
        ui.notify(
            "📧 Lipsesc credențialele în mail_config.json (smtp_user/smtp_pass).",
            type="warning",
        )
        return
    body = (
        "Test e-mail Loto Enterprise — configurarea funcționează ✅\n"
        f"Următoarea extragere: {_next_draw_date()}\n"
        "După fiecare generare vei primi data extragerii, pool-ul fiecărui joc, "
        "metoda și ratingul ei."
    )
    try:
        send_email(cfg, "🎰 Loto — mail de test", body)
        ui.notify(
            f"📧 Mail de test trimis la {cfg['mail_to']}. Verifică inbox-ul.",
            type="positive",
        )
        logger.info("[MAIL] test trimis la %s", cfg["mail_to"])
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"📧 Test eșuat: {exc}", type="negative")
        logger.error("[MAIL] test eșuat: %s", exc)


def _maybe_send_results_email(snapshot: dict | None = None) -> str | tuple[str, str]:
    """Trimite rezultatele pe mail la finalul pipeline-ului, dacă e bifat
    'mail_on_complete' ȘI SMTP-ul e configurat (mail_config.json / env). Best-effort:
    orice eroare e logată, NU oprește restul (shutdown etc.)."""
    snapshot = _capture_mail_snapshot() if snapshot is None else snapshot
    if not snapshot["enabled"]:
        return "failed"
    games = snapshot.get("games") or []
    if not snapshot.get("valid") or not games or any(
        not (record.get("data") or {}).get("hard_core") for record in games
    ):
        logger.warning("[MAIL] rezultat gol sau invalid — nu trimit mesaj de rezultate.")
        return "failed"
    cfg = load_mail_config(PROJECT_ROOT)
    if not cfg:
        logger.warning(
            "[MAIL] cerut, dar SMTP neconfigurat (mail_config.json / env) — sar peste."
        )
        try:
            ui.notify(
                "📧 Mail cerut, dar lipsesc credențialele (vezi mail_config.json).",
                type="warning",
            )
        except Exception:  # noqa: BLE001
            pass
        return "failed"
    try:
        body = _build_mail_body(snapshot)
        subject = f"🎰 Loto — numere pentru extragerea {_mail_subject_date(snapshot)}"
    except Exception as exc:  # noqa: BLE001
        logger.error("[MAIL] build body: %s", exc)
        return "failed"
    try:
        send_email(cfg, subject, body)  # doar esențialul (data + pool), fără atașament
        logger.info("[MAIL] rezultate trimise la %s", cfg["mail_to"])
        try:
            ui.notify(
                f"📧 Rezultate trimise pe mail ({cfg['mail_to']}).", type="positive"
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.error("[MAIL] trimitere eșuată: %s", exc)
        try:
            ui.notify(f"📧 Mail eșuat: {exc}", type="negative")
        except Exception:  # noqa: BLE001
            pass
        return "uncertain", str(exc)
    return "done"


def _finalize_pipeline() -> None:
    """La finalul walk-forward-ului (sau ramura fără rezultate): oprește PC-ul (dacă
    e cerut). Mailul NU se trimite aici — pleacă imediat după generare (vezi
    status_panel, ramura COMPLETED), fiindcă nu are conținut dependent de WF
    (`_build_mail_body` = doar numerele generate) și n-are rost să aștepte minute/ore
    de validare retroactivă doar ca notificare să ajungă mai târziu."""
    logger.info(
        "[FINALIZE] post-walk-forward: shutdown_on_complete=%s",
        SETTINGS.get("shutdown_on_complete"),
    )
    job_id = STATE.get("_result_job_id")
    if job_id is None:
        _maybe_shutdown()
        return
    sequence = STATE.get("wf_seq")

    def finish():
        def eligible():
            return (STATE.get("_result_job_id") == job_id and STATE.get("wf_seq") == sequence
                    and not STATE.get("wf_user_cancel") and not _WF_CANCEL_REQUESTED.is_set())
        try:
            if SETTINGS.get("shutdown_on_complete") or (PROJECT_ROOT / ".shutdown_pending.flag").exists():
                if not _perform_job_action(job_id, "shutdown", lambda: _maybe_shutdown(eligible)):
                    return
            _complete_job_stage(job_id, "shutdown")
        except Exception as exc:
            logger.error("[SHUTDOWN] starea jobului #%s: %s", job_id, exc)
            if STATE.get("_result_job_id") == job_id:
                STATE["_job_notice"] = "Confirmarea opririi este indisponibilă. Verifică loto.log."
                STATE["results_dirty"] = True

    threading.Thread(target=finish, name="finalize-generation", daemon=True).start()


def _maybe_shutdown(eligible=None):
    """Mark shutdown as scheduled only after Windows confirms the command."""
    flag = PROJECT_ROOT / ".shutdown_pending.flag"
    want = bool(SETTINGS.get("shutdown_on_complete")) or flag.exists()
    if not want or os.name != "nt":
        return "failed", "Oprirea nu este activată pentru această stație."
    with STATE_LOCK:
        if eligible is not None and not eligible():
            return "failed", "Rularea a fost anulată sau înlocuită."
        if STATE.get("_shutdown_initiated") or STATE.get("_shutdown_launching"):
            return "failed", "Există deja o cerere de oprire."
        try:
            proc = subprocess.Popen(["shutdown", "/s", "/t", "60", "/f", "/c",
                                     "Loto Enterprise: shutdown automat după job complete"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:
            return "failed", f"Comanda de oprire nu a pornit: {exc}"
        started = time.time()
        STATE["_shutdown_launching"] = True
    try:
        output, _ = proc.communicate(timeout=10)
        if proc.returncode != 0:
            message = f"Windows a respins comanda de oprire (cod {proc.returncode}): {(output or '').strip()[:500]}"
            logger.error("[SHUTDOWN] %s", message)
            return "failed", message
        with STATE_LOCK:
            STATE["_shutdown_initiated"] = True
            STATE["_shutdown_at"] = started
        try:
            flag.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[SHUTDOWN] marcajul local nu poate fi șters: %s", exc)
        return "done"
    except subprocess.TimeoutExpired:
        return "uncertain", "Windows nu a confirmat comanda de oprire în 10 secunde."
    finally:
        STATE["_shutdown_launching"] = False


def _cancel_shutdown() -> None:
    if os.name == "nt":
        try:
            subprocess.Popen(["shutdown", "/a"])
        except Exception as exc:  # noqa: BLE001
            logger.error("[SHUTDOWN] anulare eșuată: %s", exc)
    STATE["_shutdown_initiated"] = False
    ui.notify("Oprire anulată.", type="positive")
    status_panel.refresh()


def _shutdown_banner() -> None:
    if not STATE.get("_shutdown_initiated"):
        return
    with ui.card().classes("w-full bg-red-900"):
        ui.label("🔌 Oprire PC programată (60s). Poți anula:").classes("text-bold")
        ui.button("❌ ANULEAZĂ OPRIREA", on_click=_cancel_shutdown).props(
            "color=negative"
        )


def _read_bench_log_tail(n: int = 50) -> str:
    """Ultimele n linii din bench_full.log (procesul de bench, separat de worker)."""
    if not BENCH_LOG_FILE.exists():
        return ""
    try:
        # Coada, nu tot fișierul: `logs_panel` se re-randează la fiecare tick cât
        # timp rulează un bench, iar bench_full.log crește pe toată durata lui.
        # `read_tail_lines` întoarce exact aceleași linii (verificat pe fișier
        # gol / fără newline final / 18 MB).
        lines = read_tail_lines(str(BENCH_LOG_FILE), n)
        return "".join(lines).strip()
    except Exception as exc:  # noqa: BLE001
        return f"(eroare citire bench_full.log: {exc})"


def _clear_all_logs() -> None:
    """Curăță atât loto.log (engine/worker) cât și bench_full.log (benchmark)."""
    clear_logs()  # rescrie loto.log cu un header
    try:
        if BENCH_LOG_FILE.exists():
            BENCH_LOG_FILE.write_text("", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("clear bench log: %s", exc)
    logs_panel.refresh()
    ui.notify("Loguri curățate (loto.log + bench_full.log).", type="positive")


@ui.refreshable
def logs_panel() -> None:
    # Toolbar: curăță AMBELE loguri (loto.log + bench_full.log) + refresh manual.
    with ui.row().classes("w-full items-center gap-2 mb-1"):
        ui.button("🗑️ Curăță logurile", on_click=_clear_all_logs).props(
            "outline dense no-caps color=negative"
        ).classes("text-xs")
        ui.button("🔄 Reîmprospătează", on_click=logs_panel.refresh).props(
            "outline dense no-caps"
        ).classes("text-xs")
    # ── Engine / Worker (loto.log) ── include faza POST-BENCH: selectia metodei
    # castigatoare din best_methods.json, scoringul, POST-HOC si walk-forward.
    ui.label(
        "⚙️ Engine / Worker — loto.log (include ce se întâmplă DUPĂ bench)"
    ).classes("text-xs text-bold text-cyan-400")
    # citim din cache (populat de thread-ul _tick) ca să nu blocăm event-loop-ul UI
    _logtxt = STATE.get("_log_cache")
    if _logtxt is None:
        try:
            _logtxt = read_logs_filtered(120)
        except Exception:  # noqa: BLE001
            _logtxt = "(loguri indisponibile)"
    ui.code(_logtxt, language="text").classes("w-full max-h-72 overflow-auto text-xs")

    # ── Bench (bench_full.log) ── proces separat; afisat doar daca exista log.
    bench_tail = _read_bench_log_tail(50)
    if bench_tail:
        ui.label(
            "📊 Bench — bench_full.log (benchmark metode + best_methods.json)"
        ).classes("text-xs text-bold text-amber-400 mt-2")
        ui.code(bench_tail, language="text").classes(
            "w-full max-h-56 overflow-auto text-xs"
        )

def _render_results_bundle(results_bundle, res_prefix: str = "") -> None:
    # 1) Meniu global analiză — sus, închis implicit.
    _render_analysis_menu(results_bundle, res_prefix)

    # 2) Pool-urile per joc — DOAR pool + bilete de jucat (fără clasament/walk-forward).
    #    Sortăm fișierele după joc (6/49, Joker, 5/40), nu după ordinea de încărcare.
    ordered_bundle = sorted(
        results_bundle,
        key=lambda fo: min(
            (_GAME_DISPLAY_ORDER.get(_game_label_for(str(g)), 99) for g in fo[1]),
            default=99,
        ),
    )
    for fname, outs in ordered_bundle:
        with ui.card().classes("w-full"):
            ui.label(f"📄 {fname}").classes("text-subtitle1 text-bold")
            for game, raw_data in _ordered_game_items(outs):
                data = _primary_pool_data(raw_data)
                with ui.expansion(f"🎯 {_game_title(game, data)}", value=True).classes(
                    "w-full"
                ):
                    _render_pool_body(fname, game, data)


def _new_draws_summary():
    """Câte extrageri noi s-au adăugat de la ultimul bench (per joc + total), din
    semnăturile CSV stampilate în best_methods.json de modulul `freshness`
    (`write_signatures_to_best_methods`, apelat la finalul fiecărui bench).

    Întoarce None dacă freshness e indisponibil. `any_bench` = există măcar o
    semnătură de la un bench anterior (altfel primul bench e oricum complet)."""
    try:
        from loto_enterprise.benchmark.freshness import (
            check_freshness,
            aggregate_recommendation,
        )

        reports = check_freshness()
    except Exception:  # noqa: BLE001
        return None
    per, total, any_bench = {}, 0, False
    for gk, r in reports.items():
        if gk == "joker_urna2":  # alias pe aceeași sursă ca joker_urna1 — nu dubla
            continue
        if getattr(r, "status", "") == "missing":
            continue
        cached = int(getattr(r, "cached_rows", 0) or 0)
        cur = int(getattr(r, "current_rows", 0) or 0)
        if cached > 0:
            any_bench = True
        d = cur - cached
        if d > 0:
            per[gk] = d
            total += d
    try:
        rec = aggregate_recommendation(reports)
    except Exception:  # noqa: BLE001
        rec = ""
    return {"total": total, "per": per, "rec": rec, "any_bench": any_bench}


@ui.refreshable
def _bench_freshness_panel(country: str) -> None:
    """Actualizează numai mesajele bench, fără a recrea rezultatele sau controalele."""
    from loto_enterprise.core.method_selector import _load_config

    next_windows = ", ".join(f"{value}%" for value in VALIDATION_PERCENTILES)
    ui.label(f"Următorul Re-Bench validează metodele pe {next_windows} din istoric. "
             "Fereastra de 100% începe după primele 80 de extrageri.").classes("text-caption")
    decision_path = _LOT.decision_path_for(country, PROJECT_ROOT)
    meta = _load_config(str(decision_path)).get("_meta")
    saved = meta.get("percentiles") if isinstance(meta, dict) else None
    windows_changed = False
    if isinstance(saved, list) and saved and all(type(p) is int and 1 <= p <= 100 for p in saved):
        ui.label("Ferestrele deciziei salvate: " + ", ".join(f"{p}%" for p in saved) + ".").classes("text-caption")
        windows_changed = set(saved) != set(VALIDATION_PERCENTILES)
        if windows_changed:
            missing = [f"{p}%" for p in VALIDATION_PERCENTILES if p not in saved]
            detail = " Următorul Re-Bench evaluează și " + ", ".join(missing) + "." if missing else ""
            ui.label("Noua combinație de ferestre nu a fost evaluată în decizia salvată."
                     + detail).classes("text-caption text-warning")
    stale_target = _decision_target_text(country)
    if stale_target:
        # Persistent: dispare numai când decizia ajunge pe ținta selectată.
        ui.label(stale_target).classes("text-caption text-negative")
    if _LOT.normalize_country(country) != _LOT.RO:
        text = _country_bench_texts(country)["freshness"]
        if (stale_target or windows_changed) and text.startswith("✅"):
            return  # „la zi” ar contrazice avertismentul despre țintă
        color = "text-positive" if text.startswith("✅") else "text-warning"
        ui.label(text).classes("text-caption " + color)
        return
    fresh = _new_draws_summary()
    if fresh is None:
        ui.label("⚠️ Starea benchmarkului nu poate fi verificată.").classes(
            "text-caption text-warning"
        )
    elif not fresh["any_bench"]:
        ui.label("Nu există încă un benchmark verificabil. Rulează Re-Bench.").classes(
            "text-caption text-warning"
        )
    elif fresh["total"] > 0:
        labels = {v: k for k, v in GK_MATRIX.items()}
        parts = ", ".join(f"{labels.get(gk, gk)} +{d}" for gk, d in fresh["per"].items())
        color = "text-negative" if fresh["rec"] == "full_rebench" else "text-warning"
        total = fresh["total"]
        ui.label(f"🆕 +{_new_draws_text(total)} de la ultimul bench ({parts}).").classes(
            "text-caption " + color
        )
        ui.label(
            "⚠️ Datele noi invalidează cache-ul → Re-Bench recalculează complet. "
            "Generarea poate folosi istoricul nou cu metoda aleasă anterior."
        ).classes("text-caption " + color)
    elif fresh["rec"] in ("quick_rebench", "full_rebench"):
        ui.label(
            "⚠️ Benchmarkul nu mai corespunde datelor sau metodelor curente. "
            "Rulează Re-Bench pentru actualizare."
        ).classes("text-caption text-warning")
    elif stale_target or windows_changed:
        pass  # Avertismentul de mai sus identifică ținta sau ferestrele care diferă.
    elif _target_data_ready():
        ui.label(
            "✅ Benchmark la zi pentru istoricul și metodele curente. "
            "Re-Bench poate reutiliza rezultatele din cache."
        ).classes("text-caption text-positive")
    else:
        ui.label(
            f"⚠️ Lipsesc rezultatele pentru ținta curentă (≥{_clamped_bench_target()}). "
            "Rulează Re-Bench pentru a le calcula."
        ).classes("text-caption text-warning")


# Panoul de istoric adaptiv folosea două nume NEDEFINITE (NameError la orice
# apel). Fișierul canonic e cel scris de core.adaptive_feedback (rădăcina repo);
# pool-urile acceptate de UI urmează registrul: tot universul jocurilor
# românești, 6..16 pentru jocurile altor țări.
ADAPTIVE_STATE_FILE = PROJECT_ROOT / "adaptive_state.json"
SUPPORTED_POOLS = range(6, _LOT.GAMES_BY_ID["6/49"].max_pool + 1)
SUPPORTED_POOLS_540 = range(6, _LOT.GAMES_BY_ID["5/40"].max_pool + 1)
SUPPORTED_POOLS_JOKER = range(6, _LOT.GAMES_BY_ID["joker"].max_pool + 1)


def _adaptive_pool_range(state_key):
    stem = str(state_key).rsplit("_", 1)[0]
    lot = _LOT.lottery_by_id(stem)
    if lot is None:
        lot = next(
            (
                g for g in _LOT.GAMES
                if g.history_key_prefix and stem.startswith(g.history_key_prefix)
            ),
            None,
        )
    return range(6, (lot.max_pool if lot is not None else 16) + 1)


def _clean_stale_adaptive(stale_keys) -> None:
    # NU e @ui.refreshable: e o ACȚIUNE de mutare (buton), nu funcție de randare.
    try:
        from ui_shared import file_lock

        with file_lock(ADAPTIVE_STATE_FILE):  # nu ne batem cu worker-ul pe RMW
            raw = json.loads(ADAPTIVE_STATE_FILE.read_text(encoding="utf-8"))
            for k in stale_keys:
                raw.pop(k, None)
            atomic_write_json(ADAPTIVE_STATE_FILE, raw)
        ui.notify(f"Șters {len(stale_keys)} configurări stale.", type="positive")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Eroare la curățare: {exc}", type="negative")
    adaptive_history_panel.refresh()


@ui.refreshable
def adaptive_history_panel() -> None:
    if not ADAPTIVE_STATE_FILE.exists():
        ui.label(
            "Fără istoric adaptiv încă (se creează după prima generare cu feedback)."
        ).classes("text-caption")
        return
    try:
        raw = json.loads(ADAPTIVE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = {}
    if not raw:
        ui.label("Fără istoric adaptiv încă.").classes("text-caption")
        return

    stale = []
    for k in raw:
        try:
            _allowed = _adaptive_pool_range(k)
            if int(str(k).split("_")[-1]) not in _allowed:
                stale.append(k)
        except (ValueError, IndexError):
            pass

    ui.label(
        "Stare persistentă Adaptive Feedback v2 — telemetrie evenimente "
        "(catastrofă/underperf/normal), regime resets, hard inversions."
    ).classes("text-caption")
    if stale:
        with ui.row().classes("items-center gap-3"):
            ui.label(
                f"⚠️ {len(stale)} configurări STALE (maxim 49 la 6/49, 40 la 5/40, "
                f"45 la Joker, 16 la celelalte jocuri): "
                f"{', '.join(stale)}"
            ).classes("text-warning text-caption")
            ui.button(
                "🗑️ Curăță stale", on_click=lambda s=stale: _clean_stale_adaptive(s)
            ).props("flat dense color=negative")

    icons = {
        "catastrophe": "🔥",
        "underperf": "⚠️",
        "normal": "✅",
        "regime_reset": "🚨",
    }
    for key in sorted(raw):
        entry = raw[key] or {}
        hist = entry.get("history", []) or []
        rs = entry.get("regime_state", {}) or {}
        mode = rs.get("active_mode", "normal")
        streak = int(rs.get("streak_zero", 0) or 0)
        events = [str(h.get("event", "?")) for h in hist]
        hits = [int(h.get("pool_hits", 0) or 0) for h in hist]
        n = len(events)
        n_cat = events.count("catastrophe")
        mean_h = (sum(hits) / n) if n else 0.0
        max_h = max(hits) if hits else 0
        badge = "RESET" if mode == "reset" else "NORMAL"
        title = f"{key}  [{badge}]" + ("  [STALE]" if key in stale else "")
        with ui.expansion(title, value=False).classes("w-full"):
            with ui.row().classes("gap-6"):
                cat_txt = f"{n_cat} ({n_cat / n * 100:.0f}%)" if n else "0"
                for lbl, val in [
                    ("Total extrageri", n),
                    ("Mean hits", f"{mean_h:.2f}"),
                    ("Best", max_h),
                    ("Catastrofe", cat_txt),
                    ("Streak zero", streak),
                ]:
                    with ui.column().classes("items-center gap-0"):
                        ui.label(lbl).classes("text-caption")
                        ui.label(str(val)).classes("text-subtitle1")
            if entry.get("last_pool_date"):
                ui.label(f"Ultima predicție: {entry['last_pool_date']}").classes(
                    "text-caption"
                )
            if hits:
                ui.echart(
                    {
                        "tooltip": {"trigger": "axis"},
                        "xAxis": {
                            "type": "category",
                            "data": list(range(1, len(hits) + 1)),
                        },
                        "yAxis": {"type": "value"},
                        "series": [
                            {
                                "type": "line",
                                "data": hits,
                                "smooth": True,
                                "areaStyle": {},
                            }
                        ],
                        "grid": {"left": 30, "right": 10, "top": 10, "bottom": 20},
                    }
                ).classes("w-full").style("height:140px")
                recent = hist[-min(15, len(hist)) :]
                seq = " ".join(
                    f"{icons.get(str(h.get('event', '?')), '•')}{int(h.get('pool_hits', 0) or 0)}"
                    for h in recent
                )
                ui.label(f"Ultimele {len(recent)}: {seq}").classes("text-caption")

    total_learned = sum(len(e.get("history", []) or []) for e in raw.values())
    n_reset = sum(
        1
        for e in raw.values()
        if (e.get("regime_state") or {}).get("active_mode") == "reset"
    )
    ui.label(
        f"📈 Global: {total_learned} extrageri învățate · {n_reset} configurări în mod RESET."
    ).classes("text-caption text-bold")


def _refresh_status() -> None:
    status_panel.refresh()
    logs_panel.refresh()


# --------------------------------------------------------------------------- #
# Pagina principală
# --------------------------------------------------------------------------- #
def _render_lr_pool_input(game: str, title: str, *, budget_entry=None, on_manual_budget=None):
    """One stop per official code, including codes sharing a pool size."""
    from covering.lr_schemes import lr_schemes

    schemes = sorted(lr_schemes(game), key=lambda scheme: (scheme.n, scheme.size, scheme.code))
    selected = _lr_scheme_for(game)
    codes = [scheme.code for scheme in schemes]
    key = _LR_CODE_KEYS[game]
    SETTINGS[key] = selected.code
    SETTINGS[_POOL_KEYS[game]] = selected.n
    previous = {"code": selected.code}
    ui.label(title).classes("text-bold mt-2")
    control = ui.slider(min=0, max=len(schemes) - 1, step=1,
                        value=codes.index(selected.code)).classes("w-full")
    control.props(f'label-always markers aria-label="{title}"')

    def describe(scheme):
        control.props(f'label-value={scheme.n} aria-valuetext="Cod {scheme.code}, {scheme.n} numere, {scheme.size} variante"')

    describe(selected)
    control.bind_value(SETTINGS, key,
                       forward=lambda index: schemes[max(0, min(len(schemes) - 1, int(index or 0)))].code,
                       backward=lambda code: codes.index(code) if code in codes else codes.index(_lr_scheme_for(game).code))
    ui.label().bind_text_from(SETTINGS, key, lambda _: _lr_selection_lines(game)[0]).classes("text-caption text-info")
    ui.label().bind_text_from(SETTINGS, key, lambda _: _lr_selection_lines(game)[1]).classes("text-caption text-grey")
    transition = ui.label("").classes("text-caption text-warning")

    def changed(event):
        index = max(0, min(len(schemes) - 1, int(event.value or 0)))
        scheme = schemes[index]
        SETTINGS[key] = scheme.code
        SETTINGS[_POOL_KEYS[game]] = scheme.n
        describe(scheme)
        transition.set_text(_lr_selection_change_text(game, previous["code"], scheme.code))
        previous["code"] = scheme.code
        _save_settings()

    control.on_value_change(changed)
    if on_manual_budget is not None:
        from covering.lr_budget import scheme_price_bani

        lot = _game_spec_for(game)
        prices = [scheme_price_bani(lot, scheme) for scheme in schemes]

        def commit_budget(event):
            index = event.args
            if type(index) is int and 0 <= index < len(prices):
                control.set_value(index)
                on_manual_budget(budget_in_lei(prices[index]))

        # Quasar emits change on a user commit, not when a budget updates the bound slider.
        # Update the input in that gesture too: a rapid Generate click reads its DOM value.
        control.on("change", commit_budget, js_handler=f"""index => {{
            const bani = {json.dumps(prices)}[index];
            if (!Number.isInteger(bani)) return;
            const input = document.querySelector('input[id="{budget_entry.html_id}"]');
            if (input) input.value = (bani / 100).toFixed(2);
            emit(index);
        }}""")
    return control


def _apply_budget_plan(plans):
    """Use the budget's official scoring schemes for the next generator and Re-Bench job."""
    from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme

    updated = {
        "lr_budget_lei": budget_in_lei(plans[0]["budget_bani"]),
        "lr_budget_objective": FOUR_PLUS,
    } if plans else {}
    for plan in plans:
        if plan["tickets"]:
            scheme = budget_plan_scheme(plan)
            game = plan["game"]
            updated[_POOL_KEYS[game]] = scheme.n
            updated[_LR_CODE_KEYS[game]] = scheme.code
    if any(SETTINGS.get(key) != value for key, value in updated.items()):
        SETTINGS.update(updated)
        _save_settings()


def _lr_rebench_jobs(choices, amount, *, sync_pool=False):
    from covering.lr_budget import recommend_games
    from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme

    selected = choices["lr_budget_game"]
    plans = recommend_games(amount, FOUR_PLUS, None if selected == "all" else selected, single_scheme=True)
    if sync_pool:
        # The budget preview may still be running when the user clicks Re-Bench.
        _apply_budget_plan(plans)
    games = [_LOT.require_lottery(plan["game"], _LOT.RO) for plan in plans]
    _load_missing_bench_histories(games)
    with STATE_LOCK:
        sources = {_game_spec_for(name).game_id: (name, frame) for name, frame in STATE["datasets"]}
        jobs = []
        for plan in plans:
            game = plan["game"]
            source = sources.get(game)
            inverse = choices["lr_inverse_val"]
            # Legacy callers supplied individual base overrides. In the current
            # panel manual mode takes the checkbox as the explicit choice.
            individual_bases = {} if "lr_auto_options_val" in choices else choices.get("base_choices", {})
            base = lr_base_choice(individual_bases, game, inverse, choices["lr_restricted_val"])
            options = {
                "scheme": budget_plan_scheme(plan).code,
                "budget_lei": amount, "objective": FOUR_PLUS, "budget_mode": plan["budget_mode"],
                "inverse": inverse, "restricted": base == LR_RESTRICTED_BASE,
                "dynamic": bool(SETTINGS["dynamic_method_enabled_val"]),
                "lookback": _effective_lookback_pct(), "hit_target": 4,
                "joker_size": _int_setting("joker_pool_size_val"),
            }
            if choices.get("lr_auto_options_val"):
                options["auto_options"] = {
                    "depth": _int_setting("wf_depth_pct"), "replay_policy": choices.get("replay_policy", "fixed"),
                }
            elif base == "auto":
                options["auto_budget"] = (amount, FOUR_PLUS)
                if choices.get("lr_fixed_share_val"):
                    from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS
                    options["auto_range_policy"] = ONLINE_TICKET_EXPERTS
            if choices.get("lr_auto_options_val") is False:
                options["manual_fixed_share"] = bool(choices.get("lr_fixed_share_val"))
            jobs.append((game, (source[0], source[1].copy(deep=True)) if source else None, options))
    return jobs


async def _run_selected_rebench(choices, amount, on_scoped, *, generate_ticket=None,
                                selection_current=None, general=False, reuse_saved=False):
    if _selected_country() != _LOT.RO:
        run_rebench()
        return
    if STATE.get("_lr_ticket_flow_active") or lr_rebench_running() or _bench_running():
        ui.notify("Un Re-Bench sau recomandarea aferentă este deja în curs.", type="warning")
        return
    if lr_walk_forward_running():
        ui.notify("Validarea selecției LR este deja în curs.", type="info")
        return

    selection = deepcopy(choices)
    if generate_ticket is None:
        jobs = _lr_rebench_jobs(selection, amount)
        on_scoped()
        return await run_lr_rebench(jobs)

    import asyncio
    from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS

    depth = _int_setting("wf_depth_pct")
    epoch = STATE.get("_cancellation_epoch", 0)
    STATE.update(_lr_ticket_flow_active=True, _lr_ticket_flow_stage="rebench",
                 _lr_ticket_flow_status="Se pregătește evaluarea pentru biletul ales.")

    def current():
        return STATE.get("_cancellation_epoch", 0) == epoch and (selection_current is None or selection_current())

    def stopped():
        STATE["_lr_ticket_flow_status"] = "Secvența s-a oprit după anulare sau schimbarea selecției."

    try:
        on_scoped()
        jobs = _lr_rebench_jobs(selection, amount, sync_pool=True)
        if general:
            STATE["_lr_ticket_flow_stage"] = "general"
            STATE["_lr_ticket_flow_status"] = "Se evaluează catalogul general înainte de Re-Bench pentru bilet."
            if not await _run_general_before_lr():
                STATE["_lr_ticket_flow_status"] = "Re-Bench general nu a confirmat finalizarea. Secvența s-a oprit."
                return
            if not current():
                stopped()
                return

        STATE["_lr_ticket_flow_stage"] = "rebench"
        profiles = {}
        pending = jobs
        if reuse_saved and not general:
            STATE["_lr_ticket_flow_status"] = "Se verifică ultimul Re-Bench salvat."
            profiles = await asyncio.to_thread(reusable_lr_profiles, jobs)
            if not current():
                stopped()
                return
            pending = [job for job in jobs if job[0] not in profiles]
        reused = ", ".join(profiles)
        if pending:
            STATE["_lr_ticket_flow_status"] = "Se evaluează metodele pentru biletul ales."
            fresh = {}
            saved = await run_lr_rebench(pending, require_decisions=True, completed_profiles=fresh, allow_partial=True)
            if saved:
                profiles.update(fresh)
            elif lr_rebench_notice() or lr_rebench_running():
                STATE["_lr_ticket_flow_status"] = lr_rebench_notice() or "Un alt Re-Bench a început. Reia recomandarea."
                return
        if not current():
            stopped()
            return
        if not profiles:
            STATE["_lr_ticket_flow_status"] = lr_rebench_notice() or (
                "Re-Bench nu a salvat o decizie completă pentru selecție. Verifică raportul."
            )
            return

        jobs_with_decision = []
        for game, history, options in jobs:
            if game not in profiles:
                continue
            if "auto_options" in options:
                profile, = profiles[game]
                decision = profile["settings"]["auto_options_result"]
                options = {k: v for k, v in options.items() if k != "auto_options"}
                options.update(inverse=decision["inverse"], restricted=decision["restricted"],
                               auto_options_result=decision)
                if decision["fixed_share"]:
                    options["selection_policy"] = ONLINE_TICKET_EXPERTS
            jobs_with_decision.append((game, history, options))
        validation_results = {}
        STATE["_lr_ticket_flow_stage"] = "validation"
        reuse_notice = f" Re-Bench salvat reutilizat pentru {reused}." if reused else ""
        STATE["_lr_ticket_flow_status"] = "Se testează biletele istorice prin walk-forward." + reuse_notice
        validated = await _run_selected_lr_walk_forward(
            selection, amount, jobs=jobs_with_decision, depth=depth, completed_results=validation_results,
        )
        if not current():
            stopped()
            return
        if not validated:
            STATE["_lr_ticket_flow_status"] = lr_walk_forward_notice() or (
                "Validarea walk-forward nu a produs un rezultat utilizabil. Vezi raportul înainte de a genera bilete."
            )
            return

        ticket_jobs = [job for job in jobs_with_decision if job[0] in validation_results]
        if not ticket_jobs:
            STATE["_lr_ticket_flow_status"] = "Niciun joc selectat nu are extrageri evaluate pentru obiectivul ales."
            return
        accepted = {game: profiles[game] for game, _, _ in ticket_jobs}
        omitted = [game for game, _, _ in jobs if game not in accepted]
        detail = (" Jocuri omise: " + ", ".join(omitted) + ". Vezi rapoartele Re-Bench și walk-forward."
                  if omitted else "")
        STATE["_lr_ticket_flow_stage"] = "ticket"
        STATE["_lr_ticket_flow_status"] = "Validare walk-forward finalizată. Se generează recomandarea." + detail
        if selection.get("lr_fixed_share_val") and not selection.get("lr_auto_options_val"):
            ticket_jobs = [(game, history, {**options, "selection_policy": ONLINE_TICKET_EXPERTS})
                           for game, history, options in ticket_jobs]
        generated = await generate_ticket(ticket_jobs, current, accepted, validation_results)
        if not current():
            stopped()
            return
        if not generated:
            STATE["_lr_ticket_flow_status"] = "Recomandarea nu a fost înregistrată integral. Verifică rezultatul biletelor." + detail
            return
        STATE["_lr_ticket_flow_stage"] = "done"
        STATE["_lr_ticket_flow_status"] = "Recomandarea este înregistrată. Rezultatul walk-forward apare lângă bilet." + reuse_notice + detail
    except ValueError as exc:
        STATE["_lr_ticket_flow_status"] = f"Secvența s-a oprit. {exc}"
    except Exception:
        logger.exception("[LR] Secvența Re-Bench, recomandare și validare a fost întreruptă")
        STATE["_lr_ticket_flow_status"] = "Calculul s-a întrerupt. Verifică raportul ultimei etape."
    finally:
        STATE["_lr_ticket_flow_active"] = False


async def _run_selected_lr_walk_forward(choices, amount, *, jobs=None, depth=None, completed_results=None):
    if lr_walk_forward_running():
        ui.notify("Validarea walk-forward pentru selecția LR este deja în curs.", type="info")
        return
    from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS

    if depth is None:
        depth = _int_setting("wf_depth_pct")
    if jobs is None:
        jobs = _lr_rebench_jobs(choices, amount)
    validation = []
    for game, history, captured in jobs:
        options = {**captured, "depth": depth}
        options.pop("manual_fixed_share", None)
        options.pop("auto_range_policy", None)
        options.pop("auto_options_result", None)
        if choices.get("lr_fixed_share_val") and not choices.get("lr_auto_options_val"):
            options["selection_policy"] = ONLINE_TICKET_EXPERTS
        if choices.get("replay_policy") == "daily":
            options["replay_policy"] = "daily"
        if options.pop("auto_budget", None) is not None:
            # A historical validation must select its base on its own training prefix.
            options["restricted"] = None
        validation.append((game, history, options))
    if completed_results is None:
        return await run_lr_walk_forward(validation)
    return await run_lr_walk_forward(validation, completed_results=completed_results)


def _lr_scope_text(choices):
    game = choices["lr_budget_game"]
    name = "Toate jocurile" if game == "all" else _LOT.require_lottery(game, _LOT.RO).name
    if choices.get("lr_auto_options_val"):
        return f"{name} · Auto · alegere separată pentru fiecare joc"
    method = "Metoda inversă" if choices["lr_inverse_val"] else "Metoda normală"
    if "lr_auto_options_val" in choices:
        base = LR_RESTRICTED_LABEL if choices["lr_restricted_val"] else "Baza completă"
        return f"{name} · {method} · {base}"
    games = _LOT.games_for_country(_LOT.RO) if game == "all" else [_LOT.require_lottery(game, _LOT.RO)]
    bases = {}
    for lot in games:
        value = lr_base_choice(choices["base_choices"], lot.game_id, choices["lr_inverse_val"], choices["lr_restricted_val"])
        bases[lot.name] = {"auto": "Bază automată", LR_RESTRICTED_BASE: LR_RESTRICTED_LABEL}.get(value, value)
    base = next(iter(bases.values())) if len(set(bases.values())) == 1 else ", ".join(
        f"{title} ({value})" for title, value in bases.items()
    )
    return f"{name} · {method} · {base}"


def _align_budget_sources(histories, generated):
    """Use the same loaded draw history for Re-Bench and the next recommendation."""
    originals = STATE.get("result_sources") or {}
    for game, (name, _) in tuple(generated.items()):
        original = originals.get(name)
        current = histories.get(game)
        if current is None:
            if original is not None:
                histories[game] = (name, original)
        elif current[0] != name or original is None or not original.equals(current[1]):
            del generated[game]


def _generation_games_selector(country, refresh):
    games = _LOT.games_for_country(country)

    def on_games(event):
        if not event.value:
            # An empty saved preference means every game in the current country.
            event.sender.set_value([game.game_id for game in games])
            return
        SETTINGS["games_val"] = list(event.value)
        _save_settings()
        _, errors = _autoload_histories(_selected_games())
        for error in errors:
            ui.notify(f"Istoric respins: {error}", type="negative", timeout=8000)
        refresh()

    return ui.select(
        {g.game_id: g.name + (" (doar antrenament)" if g.training_only else "") for g in games},
        value=[g.game_id for g in _selected_games()],
        multiple=True, label="Jocuri", on_change=on_games,
    ).classes("w-full")


@ui.page("/", title=APP_TITLE)
def main_page() -> None:
    ui.dark_mode().enable()
    refresh_budget = None
    generate_budget_ticket = None

    def register_budget_generator(generate):
        nonlocal generate_budget_ticket
        generate_budget_ticket = generate

    budget_result = STATE.get("results")
    last_completed = STATE.get("_result_job_id")
    lr_choices = {
        "lr_budget_game": SETTINGS.get("lr_budget_game", "all"),
        "lr_budget_objective": _budget_objective(),
        "lr_inverse_val": SETTINGS.get("lr_inverse_val") is True,
        "lr_restricted_val": SETTINGS.get("lr_restricted_val") is True,
        "lr_fixed_share_val": SETTINGS.get("lr_fixed_share_val") is True,
        "lr_auto_options_val": SETTINGS.get("lr_auto_options_val") is True,
        "base_choices": {},
    }
    if lr_choices["lr_budget_game"] not in ("all", *(g.game_id for g in _LOT.games_for_country(_LOT.RO))):
        lr_choices["lr_budget_game"] = "all"

    selection_revision = 0
    active_recommendation = None

    def change_lr_choices(**changed):
        nonlocal selection_revision
        if any(lr_choices.get(key) != value for key, value in changed.items()):
            selection_revision += 1
            if active_recommendation is not None:
                cancel_lr_rebench()
                cancel_lr_walk_forward()
        lr_choices.update(changed)
        show_lr_scope()
        if any(SETTINGS.get(key) != value for key, value in changed.items()):
            SETTINGS.update(changed)
            _save_settings()

    def focus_control(control):
        ui.run_javascript(f"""requestAnimationFrame(() => {{
            const target = document.getElementById("{control.html_id}");
            target?.scrollIntoView({{block: "nearest"}});
            target?.focus({{preventScroll: true}});
        }})""")

    def budget_forecast_inputs(game_id):
        games = {g.game_id for g in _LOT.games_for_country("RO")}
        if game_id is not None:
            games.intersection_update((game_id,))
        histories, generated = {}, {}
        with STATE_LOCK:
            results = STATE.get("results")
            if isinstance(results, tuple) and len(results) == 2:
                for name, label, data in _iter_wf_jobs(results[0]):
                    lot = _game_spec_for(label, data)
                    if lot.game_id not in games or not data.get("hard_core"):
                        continue
                    fields = (
                        "hard_core", "joker_pool", "hard_core_joker", "context", "total_draws",
                        "lr_scheme_code", "lookback", "dynamic_method",
                    )
                    snapshot = {key: deepcopy(data[key]) for key in fields if key in data}
                    audit = data.get("audit") or {}
                    snapshot["audit"] = {
                        key: deepcopy(audit[key])
                        for key in ("bench_winner", "dynamic_selection", "lr_scheme", "hash") if key in audit
                    }
                    generated[lot.game_id] = (name, snapshot)
            for name, history in STATE["datasets"]:
                lot = _game_spec_for(name)
                if lot.game_id in games:
                    histories[lot.game_id] = (name, history)
            _align_budget_sources(histories, generated)
            return {
                "histories": {game: (name, history.copy(deep=True)) for game, (name, history) in histories.items()},
                "generated": generated,
                "pool_codes": {game: _lr_scheme_for(game).code for game in games},
                "hit_target": 4,
                "lookback": _effective_lookback_pct(),
                "dynamic": bool(SETTINGS["dynamic_method_enabled_val"]),
                "joker_size": _int_setting("joker_pool_size_val"),
            }

    # Chevron-ul expansion-urilor: vârful în JOS când e DESCHIS (arată spre conținut),
    # în sus când e închis — invers față de implicitul Quasar. Inversăm rotația global
    # pentru toate expansion-urile (q-expansion-item) printr-o singură regulă.
    # Pe telefon, fiecare card și expansion imbricat lua câte 32 px din lățime:
    # tabelele din „Istoric hits” rămâneau cu 230 px din 390.
    ui.add_css("""
        .q-expansion-item__toggle-icon { transform: rotate(180deg) !important; }
        .q-expansion-item__toggle-icon--rotated { transform: rotate(0deg) !important; }
        @media (max-width: 1023.98px) {
            .loto-header { min-height: 56px; padding: 8px 12px 8px 64px; }
            .q-page-sticky.loto-settings-menu { top: 0 !important; transform: none !important; z-index: 2100; }
        }
        @media (max-width: 599.98px) {
            .loto-main, .loto-main .nicegui-card, .loto-main .nicegui-expansion-content {
                padding: 4px !important;
            }
            .loto-main .q-table--dense th, .loto-main .q-table--dense td {
                padding-left: 4px !important;
                padding-right: 4px !important;
            }
        }
    """)

    with ui.header().classes("loto-header items-center justify-between"):
        ui.label("Loto Enterprise Wheeling").classes("text-h6").tooltip(APP_TITLE)
        if BUILD_TIMESTAMP:
            ui.label(f"Versiune · {BUILD_TIMESTAMP}").classes("gt-sm text-caption")

    # ---- Sidebar (drawer stânga) ----
    with ui.left_drawer(fixed=False).props("width=360 bordered").classes("p-3") as drawer:
        _cc = _selected_country()
        _cc_name = _country_label(_cc)
        budget_slider = budget_entry = None
        if _cc == _LOT.RO:
            from covering.lr_budget import recommend_games

            budget_slider, budget_entry = lr_budget_slider(SETTINGS.get("lr_budget_lei"), with_entry=True)
            def use_scheme_budget(amount):
                nonlocal selection_revision
                if budget_slider.value != amount:
                    selection_revision += 1
                    if active_recommendation is not None:
                        cancel_lr_rebench()
                        cancel_lr_walk_forward()
                budget_entry.set_value(amount)
                budget_slider.set_value(amount)
                if SETTINGS.get("lr_budget_lei") != amount:
                    SETTINGS["lr_budget_lei"] = amount
                    _save_settings()

            budget_objective = _budget_objective()
            budget_game = lr_choices["lr_budget_game"]
            _apply_budget_plan(recommend_games(budget_slider.value, budget_objective,
                                              None if budget_game == "all" else budget_game, single_scheme=True))
            def return_to_recommendation():
                drawer.hide()
                focus_control(budget_access)

            ui.button("Înapoi la recomandare", icon="arrow_back", on_click=return_to_recommendation).props(
                "flat no-caps"
            ).classes("lt-md")
            ui.separator()
        ui.label("Jocuri și generare").classes("text-bold")
        # Istoricul țării selectate (implicit România) se încarcă singur, la
        # fiecare deschidere a paginii dacă fișierul s-a schimbat pe disc.
        _auto_loaded, _auto_errors = _autoload_histories(_selected_games())
        for _err in _auto_errors:
            ui.notify(f"Istoric respins: {_err}", type="negative", timeout=8000)

        def _on_country(e) -> None:
            try:
                SETTINGS["country_val"] = _LOT.normalize_country(e.value)
            except _LOT.UnknownLotteryError:
                SETTINGS["country_val"] = _LOT.RO
            SETTINGS["games_val"] = []
            _save_settings()
            # Tot ce depinde de țară (jocuri, praguri, texte de bench) se
            # redesenează; rezultatul afișat rămâne neatins (e în STATE).
            ui.navigate.reload()

        ui.select(
            {cc: _country_label(cc) for cc in _LOT.countries()},
            value=_cc,
            label="🌍 Țara",
            on_change=_on_country,
        ).classes("w-full")

        def refresh_game_settings() -> None:
            datasets_label.refresh()
            _render_large_pool_warning()

        _generation_games_selector(_cc, refresh_game_settings)
        if any(g.training_only for g in _LOT.games_for_country(_cc)):
            ui.label(
                "Jocurile marcate „doar antrenament” nu se joacă online din România. Re-Bench, validarea walk-forward "
                "și analizele sunt disponibile."
            ).classes("text-caption text-grey")

        _BTN = "w-full"
        _BTN_STYLE = "white-space:normal;line-height:1.2;min-height:40px"
        if _cc != _LOT.RO:
            ui.button(
                "Generează recomandarea", icon="confirmation_number", on_click=lambda: submit_generation(pure=False),
            ).props("color=positive no-caps").classes(_BTN).style(_BTN_STYLE)
        ui.separator()

        async def _load_country_history() -> None:
            loaded, errors = _load_registry_histories(_selected_games())
            for err in errors:
                ui.notify(f"Istoric respins: {err}", type="negative", timeout=8000)
            if loaded:
                ui.notify(
                    "Încărcat: "
                    + ", ".join(f"{n} ({k} extrageri)" for n, _g, k in loaded),
                    type="positive",
                )
            datasets_label.refresh()
            _bench_freshness_panel.refresh()
            if refresh_budget is not None:
                await refresh_budget()

        ui.button(
            f"📂 Încarcă istoricul {_cc_name}", on_click=_load_country_history
        ).props("outline no-caps").classes("w-full")

        # Țară străină: fișierul încărcat manual se leagă de un joc ales aici
        # (identitatea nu se ghicește din nume).
        _upload_game = {"id": _selected_games()[0].game_id}
        if _cc != _LOT.RO:
            ui.select(
                {g.game_id: g.name for g in _LOT.games_for_country(_cc)},
                value=_upload_game["id"],
                label="Fișierul încărcat manual este pentru",
                on_change=lambda e: _upload_game.update(id=str(e.value)),
            ).classes("w-full")

        async def _on_upload(e) -> None:
            try:
                validate_upload_batch(e.files)
            except (OSError, ValueError) as exc:
                ui.notify(str(exc), type="negative")
                return

            selected_game = _upload_game["id"]
            for file in e.files:
                name = file.name
                game_id = _legacy_ro_label(name) if _cc == _LOT.RO else selected_game
                try:
                    game = _LOT.require_lottery(game_id, _cc)
                    frame = read_history_csv(await file.read(), game)
                except (csv.Error, OSError, ValueError) as exc:
                    ui.notify(f"{name}: {exc}", type="negative")
                    continue
                with STATE_LOCK:
                    bindings = STATE.setdefault("dataset_game", {})
                    if _cc == _LOT.RO:
                        bindings.pop(name, None)
                    else:
                        bindings[name] = game_id
                    STATE["datasets"] = [
                        (f, d) for f, d in STATE["datasets"] if f != name
                    ] + [(name, frame)]
                ui.notify(f"Încărcat {name} ({len(frame)} extrageri).", type="positive")
            datasets_label.refresh()
            if refresh_budget is not None:
                await refresh_budget()

        def _upload_rejected() -> None:
            ui.notify(f"Fișier sau lot respins. {UPLOAD_LIMITS}", type="negative")

        ui.upload(
            on_multi_upload=_on_upload,
            on_rejected=_upload_rejected,
            multiple=True,
            auto_upload=True,
            max_file_size=MAX_FILE_BYTES,
            max_total_size=MAX_BATCH_BYTES,
            max_files=MAX_FILES,
            label=UPLOAD_LIMITS,
        ).props("accept=.csv").classes("w-full")

        @ui.refreshable
        def datasets_label() -> None:
            if STATE["datasets"]:
                _sel = {g.game_id for g in _selected_games()}
                _at = STATE.get("dataset_loaded_at") or {}
                for fn, df in STATE["datasets"]:
                    if _game_spec_for(fn).game_id not in _sel:
                        continue
                    _last = _csv_last_date(df)
                    _last_html = (
                        render_html_safe(
                            t", ultima extragere <span style='color:#38bdf8;font-weight:600'>{_last}</span>"
                        )
                        if _last
                        else ""
                    )
                    _at_html = (
                        render_html_safe(t"; încărcat {_at[fn]}") if fn in _at else ""
                    )
                    ui.html(
                        render_html_safe(t"✅ {fn}: {len(df)} extrageri")
                        + _last_html
                        + _at_html
                    ).classes("text-caption text-positive")
                _ignored = [
                    fn
                    for fn, _ in STATE["datasets"]
                    if _game_spec_for(fn).game_id not in _sel
                ]
                if _ignored:
                    ui.label(
                        f"Încărcate, dar ignorate pentru {_country_label()}: "
                        + ", ".join(_ignored)
                    ).classes("text-caption text-grey")
                with ui.expansion("📅 Istoric CSV", value=False).classes("w-full"):
                    for fn, df in STATE["datasets"]:
                        _last = _csv_last_date(df)
                        ui.label(
                            f"{fn}: {len(df)} extrageri × {len(df.columns)} coloane"
                            + (f" (ultima: {_last})" if _last else "")
                        ).classes("text-caption")
            else:
                ui.label("Niciun CSV încărcat.").classes("text-caption text-warning")

        datasets_label()

        ui.separator()
        ui.label("Setări de calcul").classes("text-bold")

        def _bind_save(widget, key):
            widget.bind_value(SETTINGS, key)
            widget.on_value_change(lambda: _save_settings())
            return widget

        # România: fiecare cod LR oficial are o poziție proprie a sliderului.
        # Alte țări: câmpul numeric comun de dinainte.
        _pool_inputs = []
        if _cc == _LOT.RO:
            dynamic_input = _bind_save(
                ui.checkbox("Alege dinamic metoda pentru pool-ul schemei LR"), "dynamic_method_enabled_val",
            )
            ui.label(
                "Recomandarea folosește metoda din Re-Bench când jocul, schema, istoricul și selecția coincid. "
                "Până la acel calcul, selecția dinamică alege metoda după extragerile anterioare "
                "cu cel puțin 4 numere pe aceeași variantă LR. Debifarea activează selecția fixă."
            ).classes("text-caption text-grey")
            for _plabel, _ptitle in (
                ("6/49", "Pool Loto 6/49"),
                ("5/40", "Pool Loto 5/40"),
                ("joker", "Pool Joker (Urna 1)"),
            ):
                _pool_inputs.append(_render_lr_pool_input(
                    _plabel, _ptitle, budget_entry=budget_entry, on_manual_budget=use_scheme_budget,
                ))
            joker_pool_key = "joker_pool_size_val"
            SETTINGS[joker_pool_key] = min(20, max(1, _int_setting(joker_pool_key)))
            ui.label("Pool Joker (Urna 2)").classes("text-bold mt-2")
            joker_pool_slider = ui.slider(
                min=1, max=20, step=1, value=SETTINGS[joker_pool_key]
            ).props('label-always markers aria-label="Pool Joker (Urna 2)"').classes("w-full")
            _pool_inputs.append(_bind_save(joker_pool_slider, joker_pool_key))
            ui.label(
                "Numere afișate în ordinea scorului Urnei 2. Prima bilă se folosește pe toate "
                "variantele schemei LR."
            ).classes("text-caption text-grey")
            ui.label(
                "Glisoarele de cod LR aleg scheme oficiale complete. Codurile cu "
                "același număr de numere au poziții distincte, în ordinea costului. "
                "Suma orientativă selectează pentru fiecare joc codul LR cel mai apropiat ca preț total, inclusiv taxa. "
                "Când alegi manual un cod LR, bugetul preia prețul acestuia cu taxa inclusă. "
                "Un cod mai scump sau un pool mai mare nu garantează șanse mai mari la toate pragurile."
            ).classes("text-caption text-grey")
        else:
            _pool_inputs.append(
                _bind_save(
                    ui.number("Dimensiune Pool (Nucleu Dur)", min=6, max=16, step=1).classes(
                        "w-full"
                    ),
                    "pool_size_val",
                )
            )
        _guarantee_input = _condition_input = _variant_limit_input = None
        if _cc != _LOT.RO:
            _guarantee_input = _bind_save(
                ui.number("Garanție minimă (Set Cover)", min=3, max=6, step=1).classes(
                    "w-full"
                ),
                "guarantee_val",
            )
            ui.label(
                "6 este sistem complet pentru 6/49. La 5/40 garanția este plafonată "
                "la 5 numere pe bilet, din cele 6 extrase. Joker are 5 numere extrase "
                "în Urna 1, deci garanția maximă este 5."
            ).classes("text-caption text-grey")
            _condition_input = _bind_save(
                ui.number(
                    "Garanția se aplică dacă în pool cad (0 = câte cere garanția)",
                    min=0,
                    max=6,
                    step=1,
                ).classes("w-full"),
                "wheel_condition_val",
            )
            ui.label(
                "Lotto design „t dacă p”: de exemplu garanție 3 cu 4 = un bilet cu 3 numere garantat "
                "doar când cad 4 numere din pool. Economia de bilete depinde de dimensiunea pool-ului."
            ).classes("text-caption text-grey")
            _variant_limit_input = _bind_save(
                ui.number(
                    "Limită maximă variante (0=nelimitat)", min=0, max=10000, step=10
                ).classes("w-full"),
                "max_variants_val",
            )
            ui.label(
                "La buget limitat, aplicația caută automat o acoperire mai bună "
                "fără să mărească numărul de variante. 0 cere acoperire completă, "
                "în limitele de calcul afișate."
            ).classes("text-caption text-grey")
        _large_pool_warning = ui.column().classes("w-full gap-1")

        def _render_large_pool_warning():
            _large_pool_warning.clear()
            with _large_pool_warning:
                for note in (_large_pool_notes() if _cc != _LOT.RO else []):
                    ui.label(note).classes("text-caption text-warning")

        _render_large_pool_warning()
        for _budget_input in (
            *_pool_inputs, _guarantee_input, _condition_input,
            _variant_limit_input,
        ):
            if _budget_input is not None:
                _budget_input.on_value_change(lambda: _render_large_pool_warning())
        lookback_input = _bind_save(
            ui.number(
                "Analizează doar ultimele X% extrageri", min=0, max=100, step=5
            ).classes("w-full"),
            "lookback_val",
        )
        ui.label(
            "0 folosește tot istoricul. În «Analiză & Clasament» alegi cât istoric se validează."
        ).classes("text-caption text-grey")

        def _on_target_change(e):
            target = _clamped_bench_target(e.value)
            if STATE.get("virtual_wf_running") and target != _clamped_bench_target():
                _VIRTUAL_WF_DECISION_CHANGED.set()
            SETTINGS["bench_hit_target"] = target
            _save_settings()
            try:
                import loto_enterprise.benchmark.decision as decision

                decision.BENCH_HIT_TARGET = target
                os.environ["LOTO_BENCH_TARGET"] = str(target)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ținta de bench nu a putut fi aplicată: %s", exc)
            if _bench_running():
                # Bench-ul își scrie decizia cu ținta de la pornire, iar folds.csv
                # e parțial până la final. Recalcularea vine după bench.
                STATE["bench_target_pending"] = True
                ui.notify(
                    f"Re-Bench în curs: decizia se recalculează pentru {target}+ "
                    "după ce se termină.",
                    type="warning",
                )
                _bench_freshness_panel.refresh()
                return
            _rebuild_decision_for_target(target)

        ui.select(
            {3: "3+ în pool", 4: "4+ în pool (implicit)", 5: "5+ în pool", 6: "6 în pool"},
            value=_clamped_bench_target(),
            label="🎯 Ținta Re-Bench pentru pool",
            on_change=_on_target_change,
        ).props("name=bench_hit_target").classes("w-full")
        ui.label(_bench_target_rule_text(_cc)).bind_text_from(
            SETTINGS, "bench_hit_target", lambda _: _bench_target_rule_text(_cc)
        ).classes("text-caption text-grey")
        ui.label(
            "Re-Bench general evaluează pragul selectat pentru fiecare dimensiune de pool, "
            "cu prioritate de la 7 numere în sus."
            + (" Recomandarea de bilete LR folosește întotdeauna 4+ pe aceeași variantă. "
               "O extragere cu 5 sau 6 potriviri pe o variantă se numără o singură dată la 4+."
               if _cc == _LOT.RO else "")
        ).classes("text-caption text-grey")
        ui.label(
            "Validarea walk-forward folosește istoricul și fereastra din cererea de generare."
        ).classes("text-caption text-grey")
        _bind_save(
            ui.checkbox("🔌 Oprește PC-ul automat la final"), "shutdown_on_complete"
        )
        _bind_save(
            ui.checkbox("📧 Trimite rezultatele pe mail după fiecare generare"),
            "mail_on_complete",
        )
        ui.button("📧 Trimite mail de test", on_click=_send_test_email).props(
            "outline no-caps size=sm"
        ).classes("text-caption")

        ui.separator()
        ui.label("Re-Bench și întreținere").classes("text-bold")

        async def start_selected_rebench(*, general=False, force=False):
            nonlocal active_recommendation
            if budget_slider is None:
                run_rebench()
                return

            def scope():
                return (selection_revision, deepcopy(lr_choices), budget_slider.value, _effective_lookback_pct(),
                        _clamped_bench_target() if general else 4, _int_setting("joker_pool_size_val"),
                        _int_setting("wf_depth_pct"), bool(SETTINGS["dynamic_method_enabled_val"]))

            original = scope()
            request = object()

            def show_scoped():
                nonlocal active_recommendation
                active_recommendation = request
                drawer.hide()
                focus_control(budget_access)

            try:
                await _run_selected_rebench(
                    lr_choices, budget_slider.value, show_scoped, generate_ticket=generate_budget_ticket,
                    selection_current=lambda: budget_slider.is_deleted or (_selected_country() == _LOT.RO and scope() == original),
                    general=general, reuse_saved=not (force or general),
                )
            finally:
                if active_recommendation is request:
                    active_recommendation = None

        bench_button = ui.button("🔬 RE-BENCH").props("color=orange no-caps").classes(_BTN).style(_BTN_STYLE)
        on_budget_click(bench_button, lambda: start_selected_rebench(force=True), budget_slider, budget_entry)
        if _cc == _LOT.RO:
            scope_label = ui.label().classes("text-caption text-info")

            def show_lr_scope():
                scope_label.set_text(f"Re-Bench · {_lr_scope_text(lr_choices)}")

            show_lr_scope()
            ui.label("După Re-Bench, walk-forward verifică biletele istorice. Apoi primești recomandarea și rezultatul validării.").classes("text-caption")
        bench_options = ui.expansion("Re-Bench general").classes("w-full") if _cc == _LOT.RO else ui.column()
        with bench_options:
            if _cc == _LOT.RO:
                general_button = ui.button("Pornește Re-Bench general").props("outline no-caps").classes("w-full")
                on_budget_click(general_button, lambda: start_selected_rebench(general=True), budget_slider, budget_entry)
            ui.label(_bench_intro_text(_cc)).bind_text_from(
                SETTINGS, "bench_hit_target", lambda _: _bench_intro_text(_cc)
            ).classes("text-caption")
            if _cc != _LOT.RO:
                _ctx = _country_bench_texts(_cc)
                ui.label(_ctx["eta"]).classes("text-caption text-grey")
                ui.label(f"🎯 {_ctx['curation']}").classes("text-caption text-info")
            if _cc == _LOT.RO:
                _eta_folds = _target_bench_folds()
                if _eta_folds:
                    ui.label(
                        f"⏱ ETA estimat: {_estimate_bench_eta(_eta_folds)} pentru "
                        f"{count(_eta_folds, 'folduri', 'un fold')} "
                        f"{_workers_text(_bench_workers())} — din durata medie a unui fold "
                        "la ultima rulare; după o schimbare de matrice estimarea e aproximativă."
                    ).classes("text-caption text-grey")
                # Curare REVERSIBILĂ a setului de metode (curated_methods.json). Dacă e
                # activă, bench-ul rulează un SUBSET — spunem clar câte și cum se anulează.
                _cur = _curation_banner_info()
                if _cur is not None:
                    _pg = _cur.get("per_game") or {}
                    _main_pg_txt = "/".join(
                        str(int(_pg[g]))
                        for g in ("loto_6_49", "loto_5_40", "joker_urna1")
                        if g in _pg
                    )
                    _urna2_n = _pg.get("joker_urna2")
                    if _main_pg_txt and _urna2_n is not None:
                        _pg_bit = (
                            f"matrice Re-Bench = {_main_pg_txt} jocuri principale "
                            f"+ {int(_urna2_n)} Urna 2, plus baseline-urile structurale"
                        )
                    elif _main_pg_txt:
                        _pg_bit = f"matrice Re-Bench = {_main_pg_txt} per joc, plus baseline-urile structurale"
                    elif _urna2_n is not None:
                        _pg_bit = f"matrice Re-Bench = {int(_urna2_n)} Urna 2, plus baseline-urile structurale"
                    else:
                        _pg_bit = "matrice Re-Bench = tot setul activ"
                    _n_after = _cur["n_after"]
                    ui.html(
                        render_html_safe(
                            t"🎯 <b>Curare activă: {_n_after} metode din {_cur['n_before']}</b> "
                            t"(uniune eligibilă; {_pg_bit})."
                        )
                    ).classes("text-caption text-info")
                    ui.label(
                        "Dezactivare (revine la toate metodele): șterge sau golește lista "
                        f"'active' din {_cur['path']}, apoi rulează un Re-Bench. "
                        "Nimic nu se pierde — nu e blacklist."
                    ).classes("text-caption text-grey")
                    if _cur["missing_required"]:
                        ui.label(
                            "⚠️ Lipsesc din curare metode structurale "
                            f"({', '.join(_cur['missing_required'])}) — decizia bench poate "
                            "cădea pe low_confidence. Adaugă-le în curated_methods.json."
                        ).classes("text-caption text-negative")
            _bench_freshness_panel(_cc)
            if _cc != _LOT.RO:
                _bind_save(
                    ui.checkbox("Generează recomandarea după Re-Bench general"),
                    "autopilot_after_bench",
                )

        ui.separator()
        ui.button("🔴 Anulează TOT Procesul", on_click=cancel_all).props(
            "color=negative outline no-caps"
        ).classes("w-full").style(_BTN_STYLE)
        ui.button(
            "🗑️ Șterge Log", on_click=lambda: (clear_logs(), logs_panel.refresh())
        ).props("outline no-caps").classes("w-full").style(_BTN_STYLE)

    # Sub 1024 px sertarul iese din ecran și toate comenzile odată cu el; butonul
    # de meniu rămâne accesibil lângă titlu.
    with ui.page_sticky(position="top-left", x_offset=8, y_offset=8).classes("lt-md") as settings_menu:
        settings_menu.classes("loto-settings-menu")
        ui.button(icon="menu", on_click=drawer.toggle).props(
            "round color=primary aria-label=Setări"
        ).tooltip("Setări și comenzi")

    # ---- Zona principală ----
    with ui.column().classes("w-full p-4 gap-2 loto-main"):
        status_panel()
        if budget_slider is not None:
            def show_budget():
                drawer.show()
                focus_control(budget_slider)

            budget_access = ui.button(icon="tune", on_click=show_budget).props("outline no-caps color=positive")
            budget_access.bind_text_from(
                budget_slider, "value", lambda amount: f"Buget orientativ · {lei_text(amount)}"
            )
            lr_recommendation_progress(STATE, lr_rebench_progress, lr_walk_forward_progress)
            refresh_budget = lr_budget_panel(
                budget_slider, budget_forecast_inputs, dynamic_input, lookback_input, *_pool_inputs,
                on_budget_plan=_apply_budget_plan, initial_objective=budget_objective,
                on_generation_ready=register_budget_generator, budget_entry=budget_entry,
                on_request_recommendation=start_selected_rebench,
                initial_inverse=lr_choices["lr_inverse_val"], initial_restricted=lr_choices["lr_restricted_val"],
                initial_game=lr_choices["lr_budget_game"],
                initial_fixed_share=lr_choices["lr_fixed_share_val"],
                initial_auto_options=lr_choices["lr_auto_options_val"],
                on_auto_options_change=lambda checked: change_lr_choices(lr_auto_options_val=bool(checked)),
                on_fixed_share_change=lambda checked: change_lr_choices(lr_fixed_share_val=bool(checked)),
                on_method_change=lambda inverse, restricted: change_lr_choices(
                    lr_inverse_val=inverse, lr_restricted_val=restricted,
                ),
                on_game_change=lambda game: change_lr_choices(lr_budget_game=game),
                base_choices=lr_choices["base_choices"], on_base_change=show_lr_scope,
            )
            lr_rebench_panel(lambda game: budget_forecast_inputs(game)["histories"].get(game))
            lr_walk_forward_panel(
                lambda: f"{_lr_scope_text(lr_choices)} · {lei_text(budget_slider.value)}"
                + (" · Fixed Share pe bilete" if lr_choices["lr_fixed_share_val"] and not lr_choices["lr_auto_options_val"] else ""),
                on_replay_change=lambda value: lr_choices.update(replay_policy=value),
                depth_control=lambda: _walk_forward_depth_selector("Istoric pentru validarea walk-forward LR"),
            )
        results_panel()
        history_windows_panel()
        nested_wfv_panel(_cc)
        if budget_slider is not None:
            lr_portfolio_panel(_lr_scheme_for("6/49").code)
        with ui.expansion("🛠 Consolă DEBUG / Loguri (live)", value=False).classes(
            "w-full"
        ):
            logs_panel()

    # ---- Polling fără reload. Munca BLOCANTĂ (citiri loguri OneDrive, psutil, pid-uri)
    # rulează în io_bound (thread), ca event-loop-ul UI să NU se blocheze → fără
    # 'connection lost'. Doar refresh-ul UI (rapid, din cache STATE) e pe loop. ----
    async def _tick() -> None:
        nonlocal budget_result, last_completed
        from nicegui import run as _nrun

        job_to_observe = STATE.get("active_job_id") or STATE.get("_result_job_id")
        if job_to_observe:
            _JOB_PROBE.request(job_to_observe, _read_ui_job, interval=1.0 if STATE.get("active_job_id") else 3.0)

        def _blocking_probe():
            """Rulat în THREAD: pid bench + citiri loguri (lente OneDrive) → cache STATE."""
            try:
                bn = _bench_running()
            except Exception:  # noqa: BLE001
                bn = False
            try:
                STATE["_log_cache"] = read_logs_filtered(120)
            except Exception:  # noqa: BLE001
                pass
            return bn

        try:
            bench_now = await _nrun.io_bound(_blocking_probe)
        except Exception:  # noqa: BLE001
            bench_now = False

        STATE["_status_bench_running"] = bench_now
        _active = bool(
            STATE.get("active_job_id") or bench_now or STATE.get("wf_running") or STATE.get("virtual_wf_running")
        )
        virtual_dirty = STATE.pop("virtual_wf_dirty", False)
        if STATE.pop("results_dirty", False) or virtual_dirty:
            # Cerut din thread-ul WF: refresh-ul se execută AICI, pe event-loop.
            try:
                results_panel.refresh()
                status_panel.refresh()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[tick] refresh după walk-forward eșuat: %s", exc)
        # Re-Bench terminat → Auto-Pilot automat
        bench_was_running = STATE.get("bench_was_running")
        _observe_bench_activity(bench_now)
        _continue_after_automatic_history(bench_now)
        # Refresh UI (rapid, din STATE) — doar când e activ
        if _active:
            logs_panel.refresh()
            status_panel.refresh()
        if not _active and (job_to_observe or STATE.get("_queue_boot_pending")):
            status_panel.refresh()
        current_result = STATE.get("results")
        completed_id = STATE.get("_result_job_id")
        if completed_id and last_completed != completed_id:
            last_completed = completed_id
            if not STATE.get("results_recovered"):
                ui.run_javascript(SOUND_JS)
        bench_finished = bench_was_running and not STATE.get("bench_was_running")
        if refresh_budget is not None and (current_result is not budget_result or bench_finished):
            budget_result = current_result
            await refresh_budget()
        if STATE.get("wf_running"):
            # DOAR progresul WF — NU tot bundle-ul, ca expansion-urile deschise
            # (🏆 Clasament bench etc.) să NU se închidă la fiecare poll de 1s.
            wf_progress_panel.refresh()

    ui.timer(1.0, _tick)


# --------------------------------------------------------------------------- #
# Recuperare la pornire a unui job terminat cât UI-ul era jos
# --------------------------------------------------------------------------- #
# Fereastra în care un job COMPLETED proaspăt mai DECLANȘEAZĂ mail + shutdown la
# pornirea UI-ului. Peste ea: DOAR afișăm rezultatul (fără shutdown-surpriză la o
# repornire mult ulterioară, fără mail cu numere vechi). Ținut SCURT intenționat:
# dacă UI-ul revine în câteva minute e aproape sigur o repornire automată (cazul
# „finalize ratat"); mai târziu = probabil utilizator prezent, care poate re-rula.
# Shutdown-ul rămâne oricum anulabil 60s prin banner.
RECOVERY_FINALIZE_WINDOW_S = 10 * 60


def _completed_age_seconds(job: dict) -> float | None:
    """Secunde de la finalizarea jobului. completed_at e UTC (CURRENT_TIMESTAMP).
    None dacă lipsește/necitibil (joburi de dinainte de migrarea coloanei) SAU dacă
    ceasul a sărit înapoi semnificativ (vechime negativă mare) — în acel caz NU
    riscăm să clasificăm un job vechi drept „proaspăt" (= shutdown-surpriză)."""
    ts = job.get("completed_at")
    if not ts:
        return None
    try:
        t = _dt.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")  # naiv = UTC
        now_utc = _dt.now(_tz.utc).replace(
            tzinfo=None
        )  # naiv UTC (fără deprecation utcnow)
        delta = (now_utc - t).total_seconds()
        if delta < -120:
            # Ceas dat înapoi (corecție NTP, resume VM, baterie BIOS) → suspect, NU proaspăt.
            logger.warning(
                "[RECOVERY] completed_at în viitor cu %.0fs (ceas?) → tratez ca vechi.",
                -delta,
            )
            return None
        return max(0.0, delta)  # micile negative (sub-secundă) → 0
    except Exception:  # noqa: BLE001
        return None


def _completed_local_text(job: dict) -> str:
    """Ora finalizării jobului în ora locală a stației, ZZ-LL-AAAA HH:MM.

    `completed_at` e UTC (CURRENT_TIMESTAMP), fără fus; afișat ca atare, un job
    terminat la 07:38 în România apărea la 04:38, iar după miezul nopții cu ziua
    precedentă. Șir gol dacă lipsește sau nu se poate citi."""
    ts = job.get("completed_at")
    if not ts:
        return ""
    try:
        t = _dt.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
        return t.astimezone().strftime("%d-%m-%Y %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _mark_job_finalized(job_id: int) -> bool:
    """Jobul a fost preluat de UI; un eșec de scriere se loghează, nu blochează."""
    try:
        return bool(mark_job_finalized(int(job_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[JOB] marcaj finalizare #%s: %s", job_id, exc)
        return False


def _migrate_legacy_finalized_marker() -> None:
    """Marcajul vechi din `.ui_state.json` trece o dată în baza stației.

    Regula veche: ultimul job COMPLETED cu id-ul marcajului era deja preluat.
    Cheia nu se mai scrie: `_save_settings` rescrie fișierul fără ea, dar numai
    după ce marcajul a ajuns în bază. Altfel (bază blocată, I/O) cheia rămâne,
    recuperarea din pornirea curentă o respectă, iar migrarea se reia data viitoare."""
    legacy = _legacy_finalized_job_id()
    if legacy is None:
        return
    if legacy > 0:
        last = get_latest_completed_job()
        if (
            last
            and int(last["id"]) == legacy
            and not last.get("ui_finalized_at")
            and not _mark_job_finalized(legacy)
        ):
            STATE["legacy_finalized_job_id"] = legacy
            return
    _save_settings()


def _recover_completed_job(*, allow_finalize: bool = True) -> None:
    """Reafișează rezultatul și cache-ul, fără calcul nou, mail sau oprire.

    Argumentul istoric rămâne acceptat pentru apelanții existenți; nici un job
    terminat înainte de pornirea interfeței nu este finalizat automat.
    """
    last = get_latest_completed_job()
    if not last:
        return
    jid = int(last["id"])
    taken = bool(last.get("ui_finalized_at")) or STATE.get("legacy_finalized_job_id") == jid
    payload = decode_queue_result(str(last.get("result_json") or "{}"))
    if not (isinstance(payload, tuple) and len(payload) == 2):
        if not taken:
            _mark_job_finalized(jid)
        return
    when = _completed_local_text(last) or "sesiune anterioară"
    with STATE_LOCK:
        STATE["results"] = payload
        STATE["result_sources"] = _result_sources_from_job(last)
        STATE["results_recovered"] = f"job #{jid} · {when}"
        STATE["_result_job_id"] = jid
    try:
        _load_cached_walk_forward()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RECOVERY] validare WF din cache: %s", exc)
    if not taken:
        try:
            _save_report_file()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RECOVERY] raport: %s", exc)
    logger.warning("[RECOVERY] job #%s reafișat, fără recalcul, mail sau oprire.", jid)


def _discard_finished_bench_at_startup() -> None:
    """O rulare încheiată înainte de pornire nu pornește istoricul la pornire.

    O rulare încă activă rămâne urmărită și va declanșa istoricul la final.
    Un PID cu acces interzis este considerat activ, ca în restul aplicației.
    """
    if _bench_running():
        return
    identity = STATE.pop("_bench_run_identity", None)
    if identity is None:
        return
    STATE["_bench_seen_run"] = identity[1]
    STATE["bench_was_running"] = False
    # Suprimarea este locală: o altă interfață care a lansat Re-Bench poate
    # avea încă de preluat rezultatul și nu trebuie să-i revendicăm markerul.
    logger.info("[BENCH] Rularea terminată înainte de pornire nu lansează istoricul automat.")


def _restore_rebench_history() -> bool:
    """Reîncarcă numai datele deja calculate, printr-o citire locală read-only."""
    try:
        from rebench_history_store import load
        loaded = load()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WF] Istoricul local nu poate fi citit: %s", exc)
        STATE["virtual_wf_store_error"] = f"Istoricul local este indisponibil: {exc}"
        STATE["virtual_wf_status"] = "Istoricul Re-Bench salvat local este indisponibil; rulează din nou."
        return False
    if loaded is None:
        return False
    payload, previous = loaded
    with STATE_LOCK:
        STATE["virtual_wf_results"] = payload["records"]
        STATE["virtual_wf_previous"] = previous
        STATE["virtual_wf_status"] = payload["status"]
        STATE["virtual_wf_provenance"] = payload["provenance"]
        STATE["virtual_wf_store_error"] = ""
        STATE["analysis_expanded"] = True
        STATE["virtual_wf_dirty"] = True
    return True


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _startup(*, schedule=None, deferred_queue=False) -> None:
    # NU marcăm joburile RUNNING ca eșuate: worker.py e proces separat care
    # supraviețuiește repornirii UI-ului → un job viu trebuie re-atașat, nu omorât.
    _load_settings()
    _restore_rebench_history()
    _discard_finished_bench_at_startup()
    try:
        # Verificarea e ieftină; recalcularea, dacă trebuie, după pornirea serverului.
        if _decision_target_mismatch():
            (schedule or _after_server_start)(_reconcile_decision_target_logged)
    except Exception as exc:  # noqa: BLE001
        logger.warning("verificare țintă decizie la pornire: %s", exc)
    if deferred_queue:
        STATE["_queue_boot_pending"] = True
        STATE["_queue_boot_start"] = time.monotonic()
        threading.Thread(target=_restore_queue_when_ready, name="restore-job-queue", daemon=True).start()
    else:
        _restore_queue_on_startup()


def _restore_queue_when_ready():
    while True:
        try:
            _restore_queue_on_startup()
        except Exception as exc:
            STATE["_queue_boot_unavailable"] = True
            STATE["results_dirty"] = True
            logger.warning("Coada este temporar indisponibilă la pornire: %s", exc)
            time.sleep(3)
        else:
            STATE.update(_queue_boot_pending=False, _queue_boot_unavailable=False, results_dirty=True)
            return


def _restore_queue_on_startup():
    init_job_queue()
    try:
        _migrate_legacy_finalized_marker()
    except Exception as exc:  # noqa: BLE001
        logger.warning("migrare marcaj job finalizat: %s", exc)
    # NU auto-încărcăm CSV-uri: utilizatorul încarcă manual de fiecare dată.
    # La boot-ul procesului UI user-ul n-a apăsat încă Generează. Un job 0%
    # fără log e leftover — dacă îl reatașăm, ecranul rămâne pe
    # «⏳ Job în rulare (#1) — 0% / se inițializează...» la infinit
    # (worker-ul nu l-a preluat). START_8000 anulează TOATE leftover-urile.
    fresh_start = is_fresh_ui_start()
    try:
        if fresh_start:
            n = cancel_pending_running_jobs(
                "Pornire START_8000: sesiune nouă, fără job automat."
            )
            if n:
                logger.warning(
                    "[STARTUP] LOTO_FRESH_START: anulate %s job(uri) leftover.",
                    n,
                )
        else:
            active = get_active_job()
            if active and is_unstarted_job(active):
                jid = int(active["id"])
                if cancel_unstarted_job(jid, f"Job orfan #{jid} anulat la pornirea UI."):
                    logger.warning("[STARTUP] job #%s nepornit → anulat (nu reatașez).", jid)
                active = get_active_job()
            if active and not is_unstarted_job(active):
                STATE["active_job_id"] = int(active["id"])
                ensure_worker_running()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_job startup: %s", exc)
        raise
    # Job terminat cât UI-ul era COMPLET jos (get_active_job vede doar PENDING/RUNNING)
    # → reafișăm rezultatul și istoricul deja salvat, fără calcul sau acțiuni post-finalizare.
    try:
        if not STATE.get("active_job_id"):
            _recover_completed_job(allow_finalize=not fresh_start)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recover completed job startup: %s", exc)
        raise


# Star-import skips _-prefixed names; functions look up globals in the defining
# module. Copy private helpers (and `time`) so Generate / Auto-Pilot / panels
# resolve after the UI split.
def _sync_ui_namespace() -> None:
    import ui_bench
    import ui_hits
    import ui_results
    import ui_runtime

    facade = sys.modules[__name__]
    modules = (ui_runtime, ui_results, ui_bench, ui_hits, facade)
    merged: dict = {}
    for mod in modules:
        for key, value in vars(mod).items():
            if key.startswith("__") and key.endswith("__"):
                continue
            if key.startswith("_") or key == "time":
                merged[key] = value
    merged["time"] = time
    for mod in modules:
        vars(mod).update(merged)


_sync_ui_namespace()

app.add_middleware(LimitHistoryUploadBody)
async def _startup_in_background():
    import asyncio
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(_startup, deferred_queue=True, schedule=lambda fn: loop.call_soon_threadsafe(_after_server_start, fn))


app.on_startup(_startup_in_background)

# Copiii ProcessPoolExecutor (spawn, Windows) sunt `__mp_main__`. Nu porni
# UI-ul acolo: validarea walk-forward poate porni procese din acest
# interpretor. `reload=False` — nu e reloaderul NiceGUI.
if __name__ == "__main__":
    _port = int(os.environ.get("LOTO_UI_PORT", "8000"))
    # show=False: browserul e deschis de START_8000.bat (mai fiabil pe Windows).
    # reconnect_timeout mărit: cât rulează bench/walk-forward, event-loop-ul poate fi
    # ocupat (citiri loguri OneDrive) → fără timeout generos, WebSocket pica 'connection lost'.
    ui.run(
        title=APP_TITLE,
        host="127.0.0.1",
        port=_port,
        reload=False,
        show=False,
        dark=True,
        reconnect_timeout=60.0,
    )
