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

import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path

import pandas as pd
from nicegui import app, ui

from job_queue import (
    cancel_pending_running_jobs,
    get_active_job,
    get_job_status,
    get_latest_completed_job,
    init_job_queue,
    submit_job,
)
from cancel import lock_engine, unlock_engine
from ui_shared import (
    PROJECT_ROOT,
    atomic_write_json,
    atomic_write_text,
    clear_logs,
    decode_queue_result,
    ensure_worker_running,
    load_mail_config,
    read_logs_filtered,
    render_html_safe,
    send_email,
    html_escape,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("app_nicegui")

# --------------------------------------------------------------------------- #
# Constante / căi de stare pe disc (compatibile cu app.py & worker)
# --------------------------------------------------------------------------- #
UI_STATE_FILE = PROJECT_ROOT / ".ui_state.json"
BENCH_PID_FILE = PROJECT_ROOT / ".bench_pid"
BENCH_LOG_FILE = PROJECT_ROOT / "bench_full.log"
REPORT_FILE = PROJECT_ROOT / "raport_complet.txt"

# Buget de timp TOTAL pentru walk-forward (toate jocurile). Peste buget, validarea
# se oprește PARȚIAL și pipeline-ul continuă (mail + shutdown).
WF_TOTAL_BUDGET_S = 90 * 60
# Adâncime walk-forward: ultimele X% din istoric simulate onest (fără lookahead).
WF_DEPTH_PERCENT = 30.0

UI_PERSIST_KEYS = [
    "pool_size_val", "guarantee_val", "max_variants_val", "lookback_val",
    "auto_invert_val", "shutdown_on_complete",
    "sim_depth_val", "autopilot_after_bench", "mail_on_complete",
    "last_finalized_job_id", "wf_budget_min", "bench_hit_target",
]
DEFAULTS = {
    "pool_size_val": 10, "guarantee_val": 4, "max_variants_val": 0,
    "lookback_val": 0, "auto_invert_val": False,
    "shutdown_on_complete": False, "sim_depth_val": 40, "autopilot_after_bench": True,
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
STATE: dict = {
    "datasets": [],          # list[(fname, DataFrame)]
    "active_job_id": None,
    "job_start_time": None,
    "job_elapsed": None,     # durata FIXĂ a ultimei generări (sec); setată la COMPLETED
    "wf_elapsed": None,      # durata FIXĂ generare+walk-forward (sec); setată la finalul WF
    "results": None,         # (results_bundle, count)
    "results_recovered": None,  # etichetă „job #N · dată" dacă rezultatele-s recuperate (vechi)
    "retro": {},             # {f"{fname}_{game}": flat_walk_forward}
    "retro_meta": {},        # {aceeași cheie: {partial, n_test_draws, n_expected, from_cache}}
    "wf_status": "",         # text status walk-forward
    "wf_progress": 0.0,      # fracție 0..1 progres walk-forward (bară)
    "wf_start": None,        # timestamp pornire WF (ETA în UI)
    "pure_bench": False,
    "show_all": {},          # {f"{fname}_{game}": bool} — toggle wheel complet
    "bench_was_running": False,
    "bench_cancelled": False, # True după Anulează → _tick NU mai pornește Auto-Pilot
    "_log_cache": None,       # conținut loguri pre-citit în thread (ne-blocant pt UI)
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
    # Pool max 16 (la pool mai mare inversarea nu mai merge pe jocuri mici) — clamp valori vechi.
    try:
        if int(SETTINGS.get("pool_size_val", 10)) > 16:
            SETTINGS["pool_size_val"] = 16
    except (TypeError, ValueError):
        SETTINGS["pool_size_val"] = 10

    # Inițializează variabila din modulul decision și os.environ din setările salvate
    try:
        import loto_enterprise.benchmark.decision as decision
        target = int(SETTINGS.get("bench_hit_target", 3))
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


def _ordered_wf_game_items(outs):
    """Ordine walk-forward: Joker → 5/40 → 6/49 (6/49 ultim = mai mult timp rămas)."""
    return sorted(
        outs.items(),
        key=lambda kv: _WF_GAME_ORDER.get(_game_label_for(str(kv[0])), 99),
    )


def _iter_wf_jobs(results_bundle):
    """(fname, game_label, data, auto_invert=False) — un singur pool (fără inversare)."""
    for fname, outs in results_bundle:
        for g_label, data in _ordered_wf_game_items(outs):
            yield fname, g_label, data, False


def _count_wf_jobs(results_bundle) -> int:
    return sum(1 for _ in _iter_wf_jobs(results_bundle))


# --------------------------------------------------------------------------- #
# Submit job (contract config_json identic cu app.py)
# --------------------------------------------------------------------------- #
def _build_config_json(sim_depth_per_game: dict | None = None) -> str:
    sim_depth_per_game = sim_depth_per_game or {}
    h = hashlib.sha256()
    for k in ("pool_size_val", "guarantee_val", "max_variants_val", "lookback_val",
              "auto_invert_val", "sim_depth_val", "bench_hit_target"):
        h.update(str(SETTINGS[k]).encode("utf-8"))
    h.update(str(sorted(sim_depth_per_game.items())).encode("utf-8"))  # adâncime per joc → cache key
    pure = bool(STATE.get("pure_bench"))
    h.update(str(pure).encode("utf-8"))
    datasets_cfg = []
    for fname, df in STATE["datasets"]:
        g_label = _game_label_for(fname)
        df_json = df.to_json(orient="split")
        # adâncime backtesting: per joc (din Auto-Pilot) dacă există, altfel globală
        sd = int(sim_depth_per_game.get(g_label, SETTINGS["sim_depth_val"]))
        task = {
            "game_label": g_label,
            "pool_size": int(SETTINGS["pool_size_val"]),
            "guarantee": int(SETTINGS["guarantee_val"]),
            "max_variants": int(SETTINGS["max_variants_val"]),
            "lookback": int(SETTINGS["lookback_val"]),
            "filter_consecutives": False,
            "smart_reduction": False,
            "sim_depth_pct": sd,
            "pure_bench_mode": True,
            "auto_invert": False,  # mecanism scos 2026-08-09; cheia rămâne pt compat payload
            "bench_hit_target": int(SETTINGS.get("bench_hit_target", 3)),
        }
        datasets_cfg.append({
            "fname": fname,
            "df_json": df_json,
            "tasks": [task],
        })
        h.update(fname.encode("utf-8"))
        h.update(hashlib.sha256(df_json.encode("utf-8")).hexdigest().encode("ascii"))
    return json.dumps({"input_hash": h.hexdigest(), "use_cache": False, "datasets": datasets_cfg})


def submit_generation(pure: bool = False, sim_depth_per_game: dict | None = None) -> None:
    if not STATE["datasets"]:
        ui.notify("Încărcați cel puțin un fișier CSV!", type="negative")
        return
    if STATE["active_job_id"]:
        ui.notify("Există deja un job în rulare.", type="warning")
        return
    STATE["pure_bench"] = pure
    STATE["results"] = None
    STATE["retro"] = {}
    STATE["wf_status"] = ""
    ensure_worker_running()
    lock_engine("deterministic_session")
    cfg = _build_config_json(sim_depth_per_game)
    job_id = submit_job("pipeline", cfg)
    STATE["active_job_id"] = int(job_id)
    STATE["job_start_time"] = time.time()
    STATE["job_elapsed"] = None  # reset; se fixează la COMPLETED
    STATE["wf_elapsed"] = None   # reset; se fixează la finalul walk-forward
    ui.notify(f"Job #{job_id} trimis.", type="positive")
    _refresh_status()


def apply_autopilot_and_generate() -> None:
    """Aplică sim_depth recomandat per joc din best_methods.json, apoi generează."""
    # best_methods.json folosește CHEIA jocului (loto_6_49 ...), nu eticheta scurtă
    # (6/49) întoarsă de _game_label_for → altfel lookup-ul eșua mereu → fallback.
    _LABEL_TO_KEY = _LABEL_TO_FOLDS_GAME
    per_game: dict = {}  # {game_label: sim_depth_pct} — FIECARE joc cu adâncimea lui
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        recs = []
        for fname, _ in STATE["datasets"]:
            label = _game_label_for(fname)
            gk = _LABEL_TO_KEY.get(label, "loto_6_49")
            cfg = recommend_optimal_config(gk, int(SETTINGS["pool_size_val"]))
            if cfg and not cfg.get("fallback"):
                sd = int(cfg.get("sim_depth_pct", SETTINGS["sim_depth_val"]))
                per_game[label] = sd
                recs.append(f"{gk}: {cfg.get('scorer')} @ {sd}%")
        if recs:
            ui.notify("Auto-Pilot (adâncime per joc): " + " | ".join(recs), type="info")
        else:
            ui.notify("Fără decizie bench încă — rulează un Re-Bench întâi. Folosesc setările curente.", type="warning")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Auto-Pilot indisponibil ({exc}); folosesc setările curente.", type="warning")
    submit_generation(pure=False, sim_depth_per_game=per_game)


# --------------------------------------------------------------------------- #
# Bench (subprocess) + status
# --------------------------------------------------------------------------- #
def _bench_running() -> bool:
    if not BENCH_PID_FILE.exists():
        return False
    try:
        import psutil
        raw = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")[0]
        return psutil.pid_exists(int(raw))
    except Exception:  # noqa: BLE001
        return False

def _launch_bench(args: list[str], label: str) -> None:
    if _bench_running():
        ui.notify("Un bench rulează deja.", type="warning")
        return
    py = sys.executable
    cmd = [py, "bench_all_methods.py"] + args
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    # Bench exclusiv CPU (GPU eliminat complet din aplicație).
    env = dict(os.environ)
    env["LOTO_BENCH_TARGET"] = str(SETTINGS.get("bench_hit_target", 3))
    try:
        # bench_all_methods.py își scrie SINGUR bench_full.log (FileHandler) → nu
        # mai redirectăm stdout aici (altfel doi writeri pe același fișier). Logul
        # există acum și pe Windows, vizibil în consola DEBUG.
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), creationflags=flags, env=env)
        BENCH_PID_FILE.write_text(f"{proc.pid}|{int(time.time())}", encoding="utf-8")
        ui.notify(f"{label} pornit (PID {proc.pid}).", type="positive")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Nu pot porni bench-ul: {exc}", type="negative")
    _refresh_status()


_PCTS = "10,30,60,100"  # 4 ferestre: 10% (zona unde 4+ a ieșit cel mai sus în măsurători)
# + 30/60/100 (scurt-mediu-lung). NOTĂ: 10% e cea mai SCUMPĂ (antrenare pe ~90%% din istoric
# → rețelele grele fac 25-30 min/fold); 100% e cea mai ieftină. Tunabil aici.


def _on_bench_finished() -> None:
    """Re-Bench (unic) terminat → pornește Auto-Pilot automat (dacă e bifat)."""
    if (SETTINGS.get("autopilot_after_bench") and not STATE.get("active_job_id")
            and STATE["datasets"]):
        ui.notify("✅ Re-Bench terminat → pornesc Auto-Pilot automat.", type="positive")
        apply_autopilot_and_generate()

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


def run_rebench() -> None:
    """Re-Bench UNIC: un singur proces testează TOATE metodele. Intern, runner.py
    paralelizează metodele CPU pe toate nucleele (ProcessPool)."""
    if _bench_running():
        ui.notify("Un bench rulează deja.", type="warning")
        return
    if not _istoric_has_data():
        ui.notify("Nu există date în _ISTORIC/ — adaugă CSV-urile cu extragerile "
                  "(loto_6_49.csv, loto_5_40.csv, joker.csv) înainte de Re-Bench.",
                  type="negative", timeout=8000)
        return
    if not STATE["datasets"]:
        ui.notify("⚠️ Niciun CSV încărcat în UI — bench-ul va rula, dar Auto-Pilot-ul "
                  "de după NU va putea genera pool-uri. Încarcă fișierele la pasul 1.",
                  type="warning", timeout=8000)
    # un singur bench, fără --methods (= TOATE), scrie best_methods.json (decizie 3+)
    _launch_bench(["--no-rich", "--percentiles", _PCTS], "Re-Bench (toate metodele)")


def _estimate_bench_eta(target_folds: int, overhead: float = 1.25) -> str:
    """ETA bench pe baza ULTIMEI rulări (bench_results/folds.csv): avg runtime_sec
    al folds-urilor reale × nr. folds × overhead. Fallback la estimarea implicită
    dacă nu există bench anterior."""
    default = "~50 min" if target_folds >= 1000 else "~5 min"
    fp = PROJECT_ROOT / "bench_results" / "folds.csv"
    if not fp.exists():
        return default
    try:
        df = pd.read_csv(fp)
        if df.empty or "runtime_sec" not in df.columns:
            return default
        mask = (df.get("failed", False) == False) & (df["runtime_sec"] > 0.05)  # noqa: E712
        real = df[mask] if mask.any() else df
        avg = float(real["runtime_sec"].mean())
        total = avg * target_folds * overhead
        if total < 60:
            return f"~{int(total)} sec"
        if total < 3600:
            return f"~{int(total/60)} min"
        return f"~{total/3600:.1f} h"
    except Exception:  # noqa: BLE001
        return default


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
        txt = log_path.read_text(encoding="utf-8", errors="replace")
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
    eta = (tot - cur) * (elapsed / cur) if (elapsed > 0 and cur > 0 and tot > cur) else None

    text = f"{int(frac*100)}% ({cur}/{tot} teste)"
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
        ram = f"{vm.used/(1024**3):.1f}/{vm.total/(1024**3):.0f} GB ({vm.percent:.0f}%)"
    except Exception:  # noqa: BLE001
        pass
    parts = []
    if cpu:
        parts.append(render_html_safe(t"<span style='color:#38bdf8'>CPU {cpu}</span>"))
    if ram:
        parts.append(render_html_safe(t"<span style='color:#60a5fa'>RAM {ram}</span>"))
    _HW_CACHE["html"] = (
        render_html_safe(t"<div style='margin-top:6px;font-size:.82em;font-family:monospace;opacity:.9'>📊 ")
        + " &nbsp;·&nbsp; ".join(parts)
        + render_html_safe(t"</div>")
    ) if parts else ""


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
    try:
        cancel_pending_running_jobs("Oprit de utilizator")
    except Exception as exc:  # noqa: BLE001
        logger.warning("cancel jobs: %s", exc)
    # Kill bench (din .bench_pid) + fallback orice bench_all_methods.py din proiect
    import psutil
    if BENCH_PID_FILE.exists():
        try:
            pid = int(BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")[0])
            if psutil.pid_exists(pid):
                psutil.Process(pid).terminate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("kill bench pid: %s", exc)
    try:
        root = str(PROJECT_ROOT)
        for p in psutil.process_iter(["cmdline"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if "bench_all_methods.py" in cl and root in cl:
                p.terminate()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kill bench fallback: %s", exc)
    try:
        BENCH_PID_FILE.unlink()
    except OSError:
        pass
    STATE["active_job_id"] = None
    # IMPORTANT: marcăm că bench-ul NU mai e "în rulare" ca _tick să NU interpreteze
    # disparitia procesului ca "bench terminat → Auto-Pilot". Altfel Anuleaza pornea
    # generarea automat.
    STATE["bench_was_running"] = False
    STATE["bench_cancelled"] = True
    unlock_engine()
    ui.notify("Proces anulat.", type="warning")
    _refresh_status()


# --------------------------------------------------------------------------- #
# Walk-forward backtest (în thread de fundal, ca să nu blocheze UI-ul)
# --------------------------------------------------------------------------- #

def _start_walk_forward() -> None:
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        _finalize_pipeline()  # fără rezultate → nu rulează WF; mail deja trimis mai sus, doar shutdown
        return
    results_bundle, _ = results
    # Walk-forward: un singur pool per joc (auto-invert scos).
    _pfx = ""  # bench unic → fără prefix de secțiune

    def _worker_wf() -> None:
        _wf_t0 = float(STATE.get("wf_start") or time.time())

        def _budget_s_live() -> float:
            # Citit LIVE din SETTINGS (nu o dată la pornire) → schimbarea bugetului din
            # UI ÎN TIMPUL validării are efect imediat (mărești bugetul → rularea
            # curentă continuă; îl micșorezi → se oprește mai devreme).
            # Fallback-ul e DEFAULTS, nu o constantă separată: `or` se declanșează pe
            # orice valoare falsy (câmpul UI golit dă None), iar o valoare hardcodată
            # aici ar reduce tăcut bugetul — cu oprire parțială a validării, fără ca
            # UI-ul să arate altceva decât numărul pe care l-a introdus utilizatorul.
            try:
                _b = float(SETTINGS.get("wf_budget_min") or DEFAULTS["wf_budget_min"])
                return max(60.0, _b * 60.0)
            except (TypeError, ValueError):
                return float(WF_TOTAL_BUDGET_S)

        def _global_deadline() -> float:
            dl = _wf_t0 + _budget_s_live()
            STATE["wf_deadline"] = dl  # ETA-ul afișat urmărește și el schimbarea live
            return dl

        _global_deadline()  # inițializează STATE["wf_deadline"] pt panou

        def _wf_cancel_all():
            # Buget global depășit → oprire parțială walk-forward.
            return time.time() > _global_deadline()

        try:
            from loto_enterprise.core.walk_forward_adapter import run_honest_walk_forward
            with STATE_LOCK:
                ds_by_name = {fn: df for fn, df in STATE["datasets"]}
            if not ds_by_name:
                # Tipic la RECUPERARE după restart: CSV-urile nu-s reîncărcate (se încarcă
                # manual). Walk-forward se va sări (df_source None) → fără stats de validare,
                # dar mail-ul (= doar numerele) și shutdown-ul rulează normal.
                logger.warning("[WF] datasets goale (probabil recuperare după restart) → "
                               "walk-forward sărit; mail/shutdown continuă fără stats de validare.")
            total = _count_wf_jobs(results_bundle)
            done = 0
            for fname, g_label, data, wf_invert in _iter_wf_jobs(results_bundle):
                df_source = ds_by_name.get(fname)
                if df_source is None:
                    continue
                done += 1
                _pool_lbl = "Pool"
                base = (done - 1) / max(1, total)
                STATE["wf_status"] = f"📊 Walk-forward {_pool_lbl} {done}/{total}: {g_label}..."
                STATE["wf_progress"] = base

                def _wf_cb(frac, n_done=0, n_total=0, _b=base, _t=total,
                           _d=done, _tot=total, _lbl=_pool_lbl, _g=g_label):
                    frac = max(0.0, min(1.0, float(frac)))
                    STATE["wf_progress"] = min(1.0, _b + frac / _t)
                    if n_total > 0:
                        STATE["wf_status"] = (
                            f"📊 Walk-forward {_lbl} {_d}/{_tot}: {_g} — "
                            f"pas {n_done}/{n_total} ({int(frac * 100)}%)"
                        )
                    else:
                        STATE["wf_status"] = (
                            f"📊 Walk-forward {_lbl} {_d}/{_tot}: {_g} — "
                            f"{int(frac * 100)}%"
                        )

                # Buget PER JOC (felie adaptivă din timpul global rămas): un joc
                # lent nu mai înfometează jocurile următoare (bug văzut: joker a
                # consumat tot bugetul → 5/40 parțial, 6/49 aproape zero). Minim
                # 60s/joc. Felia se recalculează LIVE din deadline-ul global →
                # mărirea bugetului din UI extinde și felia jocului CURENT.
                _games_left = max(1, total - done + 1)
                _game_t0 = time.time()

                def _wf_should_cancel(_gt0=_game_t0, _gl=_games_left):
                    _gd = _gt0 + max(60.0, (_global_deadline() - _gt0) / _gl)
                    return _wf_cancel_all() or time.time() > _gd

                try:
                    flat, meta = run_honest_walk_forward(
                        df_source=df_source, game_type=g_label,
                        pool_size=int(data.get("pool_size") or 10),
                        backtest_depth_percent=WF_DEPTH_PERCENT, lookback_percent=100.0, use_cache=True,
                        progress_cb=_wf_cb,
                        should_cancel=_wf_should_cancel,
                        auto_invert=wf_invert,
                    )
                    if meta.get("partial"):
                        logger.warning("[WF] %s %s validat PARȚIAL: %s/%s extrageri "
                                       "(buget de timp / anulare) — acoperă extragerile RECENTE.",
                                       _pool_lbl, g_label, meta.get("n_test_draws"), meta.get("n_expected"))
                    _rk = f"{_pfx}{fname}_{g_label}" + ("_p2" if wf_invert else "")
                    with STATE_LOCK:
                        STATE["retro"][_rk] = flat
                        STATE.setdefault("retro_meta", {})[_rk] = {
                            "partial": bool(meta.get("partial")),
                            "n_test_draws": meta.get("n_test_draws"),
                            "n_expected": meta.get("n_expected"),
                            "from_cache": bool(meta.get("from_cache")),
                            "auto_invert": wf_invert,
                            # pool-ul REAL cu care a rulat WF (pt afișare corectă în istoric)
                            "pool_size": meta.get("pool_size"),
                        }
                except Exception as exc:  # noqa: BLE001
                    logger.error("walk-forward %s: %s", g_label, exc)
                STATE["wf_progress"] = done / max(1, total)
                if _wf_cancel_all():
                    logger.warning("[WF] oprire walk-forward (buget global) după %d/%d jocuri.",
                                   done, total)
                    break
            STATE["wf_status"] = ""
            STATE["wf_progress"] = 1.0
        except Exception as exc:  # noqa: BLE001
            STATE["wf_status"] = f"Walk-forward eșuat: {exc}"
        finally:
            if STATE.get("job_start_time") and STATE.get("wf_elapsed") is None:
                STATE["wf_elapsed"] = time.time() - STATE["job_start_time"]
            _save_report_file()  # rescriu raportul acum CU statisticile walk-forward
            try:
                results_panel.refresh()
            except Exception:  # noqa: BLE001
                pass
            # Mail-ul a plecat deja imediat după generare (vezi status_panel).
            # ABIA ACUM (walk-forward terminat): oprirea PC-ului (dacă e cerută).
            _finalize_pipeline()
            try:
                status_panel.refresh()  # ca banner-ul de oprire (anulabil) să apară imediat
            except Exception:  # noqa: BLE001
                pass

    STATE["wf_progress"] = 0.0
    STATE["wf_start"] = time.time()  # pt ETA walk-forward
    STATE["wf_status"] = "📊 Pornesc walk-forward backtest (paralel ~80% CPU) — Pool 1..."
    threading.Thread(target=_worker_wf, daemon=True).start()


# --------------------------------------------------------------------------- #
# UI — randare
# --------------------------------------------------------------------------- #
@ui.refreshable
def status_panel() -> None:
    job_id = STATE.get("active_job_id")
    bench_on = _bench_running()

    if job_id:
        stt = get_job_status(int(job_id))
        if not stt:
            STATE["active_job_id"] = None
            unlock_engine()
            ui.label("Job invalid / dispărut.").classes("text-negative")
            return
        pct = int(stt.get("progress_pct") or 0)
        state = str(stt.get("status") or "")
        if state == "COMPLETED":
            payload = decode_queue_result(str(stt.get("result_json") or "{}"))
            # Claim ATOMIC: un SINGUR renderer duce jobul în finalize. Dacă două
            # taburi/reconnect-uri intră aproape simultan în ramura COMPLETED, doar cel
            # care încă vede active_job_id == job_id procesează (mail/shutdown o dată);
            # ceilalți doar afișează. Și marcăm jobul ca finalizat ACUM (înainte de
            # WF/mail/shutdown) → o repornire în timpul walk-forward-ului NU-l reia.
            with STATE_LOCK:
                claimed = STATE.get("active_job_id") == int(job_id)
                if claimed:
                    # Durata generării O SINGURĂ DATĂ (fixă) — altfel 'Rezultate (în X)'
                    # creștea live cât rula walk-forward-ul.
                    if STATE.get("job_start_time") and STATE.get("job_elapsed") is None:
                        STATE["job_elapsed"] = time.time() - STATE["job_start_time"]
                    STATE["results"] = payload
                    STATE["results_recovered"] = None  # rezultat PROASPĂT → fără marcaj „vechi"
                    STATE["active_job_id"] = None
                    SETTINGS["last_finalized_job_id"] = int(job_id)
            if not claimed:
                ui.label("✅ Ultima generare e gata (vezi mai jos).").classes("text-positive")
                return
            _save_settings()
            unlock_engine()
            _save_report_file()  # raport imediat (fără WF); rescris după walk-forward
            # Mail-ul conține DOAR pool (fără stats WF, vezi _build_mail_body) →
            # numerele sunt deja fixate acum; nu are rost să aștepte walk-forward-ul de
            # raportare (poate dura minute/ore). Trimis o singură dată (claimed == True mai sus).
            try:
                _maybe_send_results_email()
            except Exception as exc:  # noqa: BLE001
                logger.error("[MAIL] trimitere imediată eșuată: %s", exc)
            _start_walk_forward()  # async; oprirea PC se face la FINALUL walk-forward-ului
            results_panel.refresh()
            try:
                ui.run_javascript(SOUND_JS)  # beep de finalizare
            except Exception:  # noqa: BLE001
                pass
            # _maybe_shutdown() NU aici — walk-forward-ul încă rulează în fundal.
            # Oprirea se declanșează în _worker_wf (la final) sau pe ramura fără rezultate.
            ui.label("✅ Generare finalizată — rulează walk-forward...").classes("text-positive text-lg")
            _shutdown_banner()
            return
        if state in ("FAILED", "CANCELLED"):
            STATE["active_job_id"] = None
            unlock_engine()
            ui.label(f"Job {state}: {stt.get('error_msg') or ''}").classes("text-negative")
            return
        with ui.card().classes("w-full"):
            tail = str(stt.get("log_tail") or "").strip()
            lines = tail.splitlines() if tail else []
            current = lines[-1] if lines else "se inițializează..."
            elapsed_txt = ""
            if STATE.get("job_start_time"):
                elapsed_txt = f" · scurs {_fmt_dur(time.time() - STATE['job_start_time'])}"
            ui.label(f"⏳ Job în rulare (#{job_id}) — {pct}%{elapsed_txt}").classes("text-bold")
            ui.linear_progress(value=pct / 100.0, show_value=False).props("instant-feedback")
            ui.label(f"➡️ {current}").classes("text-caption text-info")
            if len(lines) > 1:
                with ui.expansion(f"Pași detaliați ({len(lines)})", value=False).classes("w-full"):
                    ui.code("\n".join(lines[-15:]), language="text").classes(
                        "w-full max-h-48 overflow-auto text-xs")
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
                ui.html(render_html_safe(t"🔬 <b style='color:#38bdf8'>RE-BENCH</b> — {rc[1]}"))
                ui.linear_progress(value=rc[0], show_value=False).props("instant-feedback").classes("w-full")
            ui.label("Testez toate metodele (CPU, pe toate nucleele). Auto-Pilot pornește la final.").classes("text-caption")
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
            ui.label(f"⚠️ Rezultate RECUPERATE dintr-o sesiune anterioară ({rec}) — "
                     "verifică data extragerii înainte să joci; re-rulează pentru numere noi.") \
                .classes("text-warning text-bold")
        else:
            ui.label("✅ Ultima generare e gata (vezi mai jos).").classes("text-positive")
    else:
        ui.label("Gata de lucru. Încarcă CSV-uri și apasă Generează / Auto-Pilot.").classes("text-caption")


SOUND_JS = (
    "try{const c=new (window.AudioContext||window.webkitAudioContext)();"
    "const o=c.createOscillator();const g=c.createGain();o.connect(g);g.connect(c.destination);"
    "o.type='sine';o.frequency.value=880;g.gain.value=0.08;o.start();"
    "o.stop(c.currentTime+0.35);}catch(e){}"
)


def _next_draw_date() -> str:
    """Următoarea extragere (Loteria Română: 6/49, 5/40, Joker — JOI și DUMINICĂ)."""
    base = _dt.now().date()
    o = base.toordinal()
    for i in range(0, 8):
        d = base.fromordinal(o + i)
        if d.weekday() in (3, 6):  # 3 = Joi, 6 = Duminică
            return d.strftime("%d-%m-%Y")
    return base.strftime("%d-%m-%Y")


def _build_mail_body() -> str:
    """Conținut CONCIS pentru mail: data extragerii + pool per joc —
    DOAR numerele, fără raportul complet."""
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return "Nu există rezultate de generare."
    rb, _ = results

    def _nums(seq):
        return " ".join(str(int(x)) for x in sorted(seq)) if seq else "—"

    lines = [f"📅 Extragere (următoarea, Joi/Duminică): {_next_draw_date()}",
             f"(generat: {_dt.now().strftime('%d-%m-%Y %H:%M')})", ""]
    # Ordine FIXĂ în mail: 6/49 → Joker → 5/40 (aplatizăm jocurile din toate fișierele).
    # Păstrăm fname ca să putem arăta ultima extragere reală din CSV pentru fiecare joc.
    games = sorted(((fn, g, d) for fn, outs in rb for g, d in outs.items()),
                   key=lambda t: _GAME_DISPLAY_ORDER.get(_game_label_for(str(t[1])), 99))
    for fn, g, d in games:
        # Legacy auto_invert: phase1 era pool-ul normal — preferăm asta dacă există.
        pool = d["phase1"] if d.get("phase1") else d
        jk = sorted(int(x) for x in (pool.get("hard_core_joker") or []))
        lines.append(f"=== {g.upper()} ===")
        info = _last_csv_draw(fn)
        if info:
            _ds, _dn, _dj = info
            _draw = " ".join(str(x) for x in _dn) + (f" + joker {_dj}" if _dj is not None else "")
            lines.append(f"ultima extragere CSV: {_ds or '?'} → {_draw}")
        lines.append("POOL:     " + _nums(pool.get("hard_core") or [])
                     + (f"  | joker: {_nums(jk)}" if jk else ""))
        lines.append("")
    return "\n".join(lines).strip()


def _send_test_email() -> None:
    """Buton (declanșat de utilizator): trimite un mail de test ca să confirmi configul."""
    cfg = load_mail_config(PROJECT_ROOT)
    if not cfg:
        ui.notify("📧 Lipsesc credențialele în mail_config.json (smtp_user/smtp_pass).", type="warning")
        return
    body = ("Test e-mail Loto Enterprise — configurarea funcționează ✅\n"
            f"Următoarea extragere: {_next_draw_date()}\n"
            "La finalul bench-ului vei primi: data + pool-urile.")
    try:
        send_email(cfg, "🎰 Loto — mail de test", body)
        ui.notify(f"📧 Mail de test trimis la {cfg['mail_to']}. Verifică inbox-ul.", type="positive")
        logger.info("[MAIL] test trimis la %s", cfg["mail_to"])
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"📧 Test eșuat: {exc}", type="negative")
        logger.error("[MAIL] test eșuat: %s", exc)


def _maybe_send_results_email() -> None:
    """Trimite rezultatele pe mail la finalul pipeline-ului, dacă e bifat
    'mail_on_complete' ȘI SMTP-ul e configurat (mail_config.json / env). Best-effort:
    orice eroare e logată, NU oprește restul (shutdown etc.)."""
    if not SETTINGS.get("mail_on_complete"):
        return
    cfg = load_mail_config(PROJECT_ROOT)
    if not cfg:
        logger.warning("[MAIL] cerut, dar SMTP neconfigurat (mail_config.json / env) — sar peste.")
        try:
            ui.notify("📧 Mail cerut, dar lipsesc credențialele (vezi mail_config.json).", type="warning")
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        body = _build_mail_body()
    except Exception as exc:  # noqa: BLE001
        logger.error("[MAIL] build body: %s", exc)
        body = "(eroare la construirea conținutului)"
    subject = f"🎰 Loto — numere pentru extragerea {_next_draw_date()}"
    try:
        send_email(cfg, subject, body)  # doar esențialul (data + pool), fără atașament
        logger.info("[MAIL] rezultate trimise la %s", cfg["mail_to"])
        try:
            ui.notify(f"📧 Rezultate trimise pe mail ({cfg['mail_to']}).", type="positive")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.error("[MAIL] trimitere eșuată: %s", exc)
        try:
            ui.notify(f"📧 Mail eșuat: {exc}", type="negative")
        except Exception:  # noqa: BLE001
            pass


def _finalize_pipeline() -> None:
    """La finalul walk-forward-ului (sau ramura fără rezultate): oprește PC-ul (dacă
    e cerut). Mailul NU se trimite aici — pleacă imediat după generare (vezi
    status_panel, ramura COMPLETED), fiindcă nu are conținut dependent de WF
    (`_build_mail_body` = doar pool) și n-are rost să aștepte minute/ore
    de validare retroactivă doar ca notificare să ajungă mai târziu."""
    logger.info("[FINALIZE] post-walk-forward: shutdown_on_complete=%s",
                SETTINGS.get("shutdown_on_complete"))
    try:
        _maybe_shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.error("[SHUTDOWN] finalize a eșuat: %s", exc)


def _maybe_shutdown() -> None:
    """Auto-shutdown PC la final dacă e cerut (bifă sau .shutdown_pending.flag)."""
    flag = PROJECT_ROOT / ".shutdown_pending.flag"
    want = bool(SETTINGS.get("shutdown_on_complete")) or flag.exists()
    if not want or STATE.get("_shutdown_initiated"):
        return
    STATE["_shutdown_initiated"] = True
    STATE["_shutdown_at"] = time.time()
    if os.name == "nt":
        try:
            subprocess.Popen(["shutdown", "/s", "/t", "60", "/f", "/c",
                              "Loto Enterprise: shutdown automat după job complete"])
            logger.warning("[SHUTDOWN] shutdown /s /t 60 lansat (anulabil).")
        except Exception as exc:  # noqa: BLE001
            logger.error("[SHUTDOWN] eșuat: %s", exc)
            STATE["_shutdown_initiated"] = False
    else:
        logger.warning("[SHUTDOWN] cerut, dar OS non-Windows — sar peste comanda reală.")
    try:
        flag.unlink(missing_ok=True)
    except OSError:
        pass


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
        ui.button("❌ ANULEAZĂ OPRIREA", on_click=_cancel_shutdown).props("color=negative")


def _read_bench_log_tail(n: int = 50) -> str:
    """Ultimele n linii din bench_full.log (procesul de bench, separat de worker)."""
    if not BENCH_LOG_FILE.exists():
        return ""
    try:
        lines = BENCH_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:]).strip()
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
    ui.label("⚙️ Engine / Worker — loto.log (include ce se întâmplă DUPĂ bench)").classes(
        "text-xs text-bold text-cyan-400"
    )
    # citim din cache (populat de thread-ul _tick) ca să nu blocăm event-loop-ul UI
    _logtxt = STATE.get("_log_cache")
    if _logtxt is None:
        try:
            _logtxt = read_logs_filtered(120)
        except Exception:  # noqa: BLE001
            _logtxt = "(loguri indisponibile)"
    ui.code(_logtxt, language="text").classes(
        "w-full max-h-72 overflow-auto text-xs"
    )

    # ── Bench (bench_full.log) ── proces separat; afisat doar daca exista log.
    bench_tail = _read_bench_log_tail(50)
    if bench_tail:
        ui.label("📊 Bench — bench_full.log (benchmark metode + best_methods.json)").classes(
            "text-xs text-bold text-amber-400 mt-2"
        )
        ui.code(bench_tail, language="text").classes(
            "w-full max-h-56 overflow-auto text-xs"
        )


def _badges(numbers, stats: dict | None = None):
    stats = stats or {}
    with ui.row().classes("flex-wrap gap-1"):
        for n in sorted(int(x) for x in (numbers or [])):
            freq = stats.get(str(n), stats.get(n))
            # Numărul de pool = mare/bold/alb; frecvența din paranteze = ștearsă
            # (opacitate redusă) ca să NU concureze vizual cu numărul.
            with ui.badge().props("color=primary").classes("text-sm"):
                if freq is not None:
                    ui.html(render_html_safe(
                        t'<span style="font-weight:700;font-size:1.1em">{n}</span>'
                        t'<span style="opacity:0.45;font-size:0.68em;margin-left:3px">({freq})</span>'
                    ))
                else:
                    ui.html(render_html_safe(t'<span style="font-weight:700;font-size:1.1em">{n}</span>'))


# --------------------------------------------------------------------------- #
# Randare detaliată rezultate (audit, pipeline stages, financiar)
# --------------------------------------------------------------------------- #
PRICES = {"6/49": 8.0, "5/40": 5.0, "joker": 7.0}  # Lei/variantă (fallback loto.ro)

# Scheme reduse oficiale Loteria Română: (cod, n_variante) per (joc, pool_size)
LR_SCHEMES = {
    "6/49": {9: [("Cod 48", 12)], 10: [("Cod 49", 15), ("Cod 50", 30)],
             11: [("Cod 56", 66)], 12: [("Cod 57", 22), ("Cod 58", 132)], 16: [("Cod 59", 112)]},
    "5/40": {7: [("Cod 15", 9)], 8: [("Cod 16", 21)], 9: [("Cod 17", 30)], 10: [("Cod 18", 51)]},
    "joker": {7: [("Cod 45", 5)], 8: [("Cod 35", 6)], 9: [("Cod 34", 9)], 10: [("Cod 24", 14)],
              11: [("Cod 15", 22)], 12: [("Cod 14", 38)]},
}
STAGE_META = [
    ("1_nqi_raw", "1. NQI Raw (scorer)", "#60a5fa",
     "Pool brut din scorer (bench winner CPU / frecvență): top-K după scor de probabilitate."),
    ("2_smart_selector", "2. Pool brut (fără rafinare)", "#a78bfa",
     "Smart Selector ELIMINAT — pool-ul rămâne decizia PURĂ a scorerului câștigător "
     "(fără rafinare hibridă). Etapă păstrată doar pentru numerotare (Δ mereu 0)."),
    ("3_anti_sequence", "3. Anti-Sequence (dezactivat)", "#f59e0b",
     "Filtru anti-secvență ELIMINAT — pool-ul rămâne decizia scorerului."),
    ("4_post_hoc_final", "4. POST-HOC (dezactivat)", "#10b981",
     "Validare retrospectivă ELIMINATĂ — fără rescrieri post-scoring."),
]




def _fmt_num(x) -> str:
    """Formatează un număr pentru UI (evită repr numpy np.float64(...))."""
    if x is None:
        return "?"
    try:
        return f"{float(x):.1f}"
    except (TypeError, ValueError):
        return str(x)


def _fmt_g_range(g) -> str:
    if g is None:
        return "—"
    if isinstance(g, (list, tuple)) and len(g) >= 2:
        return f"[{_fmt_num(g[0])}, {_fmt_num(g[1])}]"
    return str(g)


def _render_audit(audit: dict) -> None:
    cf = audit.get("consecutive_filter")
    if cf:
        ui.markdown("⚠️ **Intervenție Filtru Anti-Secvență:**\n" + "\n".join(f"- {m}" for m in cf)).classes("text-warning")
    # Aici erau randate `timesfm_excluded`, `anomaly_filter`, `smart_selector` și
    # `kept_sequences`. Niciuna dintre chei nu mai are PRODUCĂTOR în engine (filtrele
    # TimesFM, Smart Selector și anti-anomalie au fost scoase din pipeline), deci
    # ramurile nu se mai executau niciodată.


def _render_stages(audit: dict) -> None:
    stages = audit.get("pipeline_stages") or {}
    if not stages:
        return
    with ui.expansion("🔍 Evoluția Pool-ului — Pipeline Stage-by-Stage", value=False).classes("w-full"):
        prev: set | None = None
        for key, title, color, desc in STAGE_META:
            pool_list = stages.get(key)
            if not pool_list:
                continue
            pool_set = set(int(x) for x in pool_list)
            added = (pool_set - prev) if prev is not None else set()
            removed = (prev - pool_set) if prev is not None else set()
            chips = []
            for n in sorted(pool_set):
                if n in added:
                    chips.append(render_html_safe(
                        t"<span style='background:#064e3b;color:#6ee7b7;padding:2px 8px;border-radius:10px;margin:2px;font-weight:bold;'>+{n}</span>"
                    ))
                else:
                    chips.append(render_html_safe(
                        t"<span style='background:rgba(255,255,255,0.07);color:#e5e7eb;padding:2px 8px;border-radius:10px;margin:2px;'>{n}</span>"
                    ))
            for n in sorted(removed):
                chips.append(render_html_safe(
                    t"<span style='background:#7f1d1d;color:#fecaca;padding:2px 8px;border-radius:10px;margin:2px;text-decoration:line-through;'>−{n}</span>"
                ))
            chips_html = "".join(chips)
            delta = f" (Δ: +{len(added)}, −{len(removed)})" if prev is not None else ""
            ui.html(render_html_safe(
                t"<div style='margin-top:8px;padding:8px;background:rgba(255,255,255,0.03);border-left:3px solid {color};border-radius:4px;'>"
                t"<div style='font-weight:700;color:{color};'>{title}{delta}</div>"
                t"<div style='font-size:0.85em;color:#94a3b8;margin:2px 0 6px 0;'>{desc}</div>"
                t"<div>{chips_html}</div></div>"
            ))
            prev = pool_set


def _render_cost(game: str, data: dict) -> None:
    gk = _game_label_for(game)
    price = PRICES.get(gk, 8.0)
    draw_n = 6 if gk == "6/49" else 5
    pool_used = int(data.get("pool_size") or len(data.get("hard_core") or []))
    import math
    full_vars = math.comb(pool_used, draw_n) if pool_used >= draw_n else 0
    full_cost = full_vars * price
    # FĂRĂ multiplicator de joker în NICIUNA dintre formulele de cost de mai jos.
    # Motiv (verificat în `loto_engine.generate_predictions`): jokerul se atașează
    # CICLIC — `assigned_joker = jokers[idx % len(jokers)]`, un singur număr per
    # variantă, NU produsul cartezian variante × jokeri. În plus nucleul Urnei 2 e
    # single-pick (`sorted_j[:1]` / `_get_hard_core_joker(pool_size=1)`, aliniat
    # bench), deci lista are oricum 1 element. Concluzie: nr. BILETE = nr. VARIANTE,
    # pe toate cele patru formule (scheme oficiale, sistem complet, bilete simple,
    # wheel) → toate se citesc pe ACEEAȘI bază.
    _jk_txt = " · 1 nr. joker/bilet" if gk == "joker" else ""

    # „Sistem complet" = TOATE combinațiile C(pool, draw_n) de la agenție (fără garanție
    # de acoperire — e exhaustiv). NU confunda cu „wheel-ul nostru" de mai jos, care e
    # un cover MINIM la garanția cerută (de ~10x mai ieftin).
    _full_lbl = f"Sistem complet C({pool_used},{draw_n}) = {full_vars} var.{_jk_txt} ≈ {full_cost:,.0f} Lei"
    if gk in LR_SCHEMES and pool_used in LR_SCHEMES[gk]:
        parts = []
        for code, base in LR_SCHEMES[gk][pool_used]:
            parts.append(f"**{code}** ({base} var.{_jk_txt} ≈ {base*price:,.0f} Lei)")
        ui.markdown(f"💡 **Scheme reduse oficiale la agenție** ({pool_used} nr.): " + " sau ".join(parts) +
                    f"\n\n*({_full_lbl} — toate combinațiile, exhaustiv)*").classes("text-info")
        # Garanția schemelor „Cod NN" NU e documentată nicăieri în proiect (doar codul
        # și numărul de variante) → nu o putem afirma. Fără avertisment, utilizatorul
        # poate crede că cele 15 variante de la „Cod 49" au aceeași garanție ca cele
        # 21 ale wheel-ului nostru (care ESTE verificată — vezi „Acoperire garanție").
        ui.markdown("⚠️ **Garanția schemelor oficiale nu e documentată în app** (avem doar "
                    "codul + numărul de variante). NU presupune că e aceeași cu garanția "
                    "configurată aici — verific-o la agenție înainte să compari numărul de "
                    "variante cu wheel-ul nostru de mai jos.").classes("text-caption text-orange")
    else:
        ui.markdown(f"💡 **Cost la agenție:** fără schemă redusă oficială pentru {pool_used} nr. la "
                    f"{game.upper()}. **{_full_lbl}** (toate combinațiile, exhaustiv).").classes("text-info")

    variants = data.get("variants") or []
    if variants:
        n_simple = min(10, len(variants))
        # Garanția EFECTIV folosită la wheel (audit) — cea care face diferența față de
        # schemele oficiale de mai sus; fallback pe cea cerută din setări.
        _g_used = (data.get("audit") or {}).get("wheel_guarantee_used")
        if _g_used is None:
            _g_used = data.get("guarantee")
        _g_txt = f"garanție {_g_used}" if _g_used is not None else "garanția configurată"
        ui.markdown(f"🎟️ **Top {n_simple} bilete simple** ({n_simple} var.{_jk_txt}) ≈ "
                    f"{n_simple*price:,.0f} Lei "
                    f"| **Wheel-ul nostru** ({_g_txt}, cover minim): {len(variants)} var.{_jk_txt} ≈ "
                    f"{len(variants)*price:,.0f} Lei.").classes("text-caption")


PRIZE_MAP = {
    "6/49": {3: 30, 4: 300, 5: 30000, 6: 1000000},
    "5/40": {3: 50, 4: 500, 5: 50000, 6: 0},
    "joker": {3: 60, 4: 600, 5: 60000, 6: 1000000},
}


def _hypergeo_params(game: str) -> tuple[int, int] | None:
    """(n numere extrase, M univers) pentru baseline-ul random hipergeometric.
    Acceptă etichete UI ("6/49", "5/40", "joker") și chei folds ("loto_6_49",
    "joker_urna1"). Urna 2 Joker (1/20) → None (baseline-ul 3+/4+ e irelevant)."""
    g = str(game).lower()
    if "6" in g and "49" in g:
        return (6, 49)
    if "5" in g and "40" in g:
        return (5, 40)
    if "urna2" in g:
        return None
    if "joker" in g:
        return (5, 45)
    return None


def _random_rate_hypergeo(game: str, k_pool: int, t_min: int) -> float | None:
    """Rata PUR aleatoare (hipergeometrică) de „≥t_min numere ghicite" pentru un
    pool de k_pool numere la jocul n-din-M:
        P = Σ_{k=t..n} C(K,k)·C(M−K,n−k) / C(M,n)
    Baseline-ul onest al hazardului — orice rată WF/bench trebuie comparată cu el
    (nu inventăm cifre: totul iese din parametrii jocului). None dacă jocul e
    necunoscut sau K invalid."""
    import math
    params = _hypergeo_params(game)
    if not params:
        return None
    n, M = params
    K = int(k_pool or 0)
    if K <= 0 or K > M:
        return None
    denom = math.comb(M, n)
    return sum(
        math.comb(K, k) * math.comb(M - K, n - k)
        for k in range(int(t_min), min(n, K) + 1)
        if n - k <= M - K
    ) / denom


def _render_adaptive(audit: dict) -> None:
    ast = audit.get("adaptive_state")
    if not ast:
        return
    event = ast.get("event")
    meta = {
        "normal": ("✅", "#28a745", "Performanță peste baseline"),
        "underperf": ("⚠️", "#ffc107", "Sub baseline (1 hit) — corecție moderată"),
        "catastrophe": ("🔥", "#dc3545", "CATASTROFĂ (0 hituri) — corecție amplificată + diversificare"),
        "regime_reset": ("🚨", "#a020f0", "REGIM RESETAT — ponderi NQI rebalansate"),
    }
    icon, color, msg = meta.get(event, ("ℹ️", "#17a2b8", "Fără date pentru comparație"))
    baseline = ast.get("baseline", 0.0) or 0.0
    rolling = ast.get("rolling_avg")
    _active_bg = "#a020f0" if ast.get("active_mode") == "reset" else "#28a745"
    _active_lbl = "RESET" if ast.get("active_mode") == "reset" else "NORMAL"
    parts = [render_html_safe(
        t"<div style='font-weight:bold;margin-bottom:6px;'>{icon} Învățare Adaptivă: {msg} "
        t"<span style='background:{_active_bg};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.8em;'>{_active_lbl}</span></div>"
    )]
    if event is not None:
        ext = render_html_safe(t"Ultima extragere: <strong>{ast.get('last_hits')}</strong> hituri în pool")
        if baseline:
            ext += render_html_safe(t" <small style='color:#888;'>(baseline aleator: {baseline})</small>")
        parts.append(render_html_safe(t"<div>{ext}</div>"))
    if ast.get("streak_zero", 0) >= 1:
        parts.append(render_html_safe(
            t"<div>Streak catastrofe consecutive: <strong>{ast['streak_zero']}</strong></div>"
        ))
    if rolling is not None:
        rc = "#dc3545" if rolling < baseline else "#28a745"
        parts.append(render_html_safe(
            t"<div>Media rolling (5 extrageri): <strong style='color:{rc};'>{rolling:.2f}</strong></div>"
        ))
    if ast.get("missed"):
        _missed = ", ".join(map(str, ast["missed"]))
        parts.append(render_html_safe(
            t"<div style='color:#dc3545;'>Numere ratate: {_missed} → boost la următoarea predicție</div>"
        ))
    if ast.get("false_positives"):
        _fp = ", ".join(map(str, ast["false_positives"][:10]))
        parts.append(render_html_safe(
            t"<div style='color:#6c757d;'>Prezise dar absente: {_fp} → penalizare</div>"
        ))
    if ast.get("boosts"):
        _boosts = ", ".join(f"{n}×{m:.2f}" for n, m in ast["boosts"][:6])
        parts.append(render_html_safe(
            t"<div><span style='color:#28a745;'>↑ Boost activ:</span> <strong>{_boosts}</strong></div>"
        ))
    if ast.get("penalties"):
        _pen = ", ".join(f"{n}×{m:.2f}" for n, m in ast["penalties"][:6])
        parts.append(render_html_safe(
            t"<div><span style='color:#dc3545;'>↓ Penalizare activă:</span> <strong>{_pen}</strong></div>"
        ))
    cd = audit.get("catastrophe_diversification")
    if cd and cd.get("injected"):
        inj = ", ".join(f"{n}(gap×{gr})" for n, gr in cd["injected"])
        ev = ", ".join(str(n) for n, _ in cd.get("evicted", []))
        parts.append(render_html_safe(
            t"<div style='color:#f4a261;'>💉 Diversificare forțată: injectate <strong>{inj}</strong> "
            t"în locul lui <strong>{ev}</strong></div>"
        ))
    hi = audit.get("hard_inversion")
    if hi:
        excl = hi.get("excluded", [])
        _excl_txt = ", ".join(str(n) for n in excl[:20])
        parts.append(render_html_safe(
            t"<div style='color:#e63946;'>🚫 Hard Inversion: <strong>{hi.get('n_excluded', len(excl))}</strong> "
            t"numere excluse temporar → {_excl_txt}</div>"
        ))
    ui.html(
        render_html_safe(
            t"<div style='margin-top:10px;padding:12px;background:rgba(20,30,50,0.5);border-left:4px solid {color};"
            t"border-radius:8px;font-size:0.9em;'>"
        )
        + "".join(parts)
        + render_html_safe(t"</div>")
    )


def _render_walk_forward(flat, game: str, is_invert: bool = False, method: str = "") -> None:
    if not flat:
        return
    gk = _game_label_for(game)
    draw_n = 6 if gk == "6/49" else 5
    n = len(flat)
    uniq = {getattr(p, "draw_index", i) for i, p in enumerate(flat)}
    avg_var = sum(getattr(p, "hits", 0) for p in flat) / n
    avg_pool = sum(getattr(p, "hits_union", 0) for p in flat) / n
    best_var = max(getattr(p, "hits", 0) for p in flat)
    best_pool = max(getattr(p, "hits_union", 0) for p in flat)
    avg_rate = (avg_var / draw_n) * 100

    _mtxt = f" · metodă: {method}" if method else ""
    _title = (f"📊 Walk-forward{' (Faza 1)' if is_invert else ''}{_mtxt}: rată {avg_rate:.1f}% · "
              f"medie/pool {avg_pool:.2f} · max pool {best_pool} · {n} predicții  "
              f"▶ CLICK pt istoric hits per extragere + distribuții")
    with ui.expansion(_title, value=False).classes("w-full mt-2"):
        if method:
            ui.label(f"✅ Validat pe metoda câștigătoare a bench-ului: {method} "
                     "(pipeline-ul regenerează pool-ul la fiecare extragere folosind acest scorer).").classes(
                "text-caption text-positive")
        ui.label(f"{n} predicții pe {len(uniq)} extrageri").classes("text-caption")
        if is_invert:
            ui.label("ℹ️ Validare FAZA 1 (pool normal, pre-inversare) — pool-ul afișat mai sus "
                     "este cel INVERSAT (Faza 2). Aceste cifre arată cum s-ar fi comportat istoric "
                     "pool-ul normal pe care se bazează inversarea.").classes("text-caption text-amber-400")
        with ui.row().classes("gap-8"):
            for lbl, val in [("Medie/variantă", f"{avg_var:.2f}"), ("Medie/pool", f"{avg_pool:.2f}"),
                             ("Rată medie", f"{avg_rate:.1f}%"), ("Max variantă", best_var), ("Max pool", best_pool)]:
                with ui.column().classes("items-center gap-0"):
                    ui.label(lbl).classes("text-caption")
                    ui.label(str(val)).classes("text-h6")

        # 📜 ISTORIC COMPLET hits per extragere (toate extragerile, cronologic) —
        # ce caută userul: nu doar ≥4, ci FIECARE extragere testată în walk-forward,
        # cu câte numere a prins pool-ul + cel mai bun bilet.
        per_draw: dict = {}
        for p in flat:
            di = getattr(p, "draw_index", id(p))
            d = per_draw.get(di)
            if d is None:
                dd = getattr(p, "draw_date", getattr(p, "target_draw_date", None))
                d = per_draw[di] = {"date": dd, "pool": getattr(p, "hits_union", 0), "best": 0}
            d["best"] = max(d["best"], getattr(p, "hits", 0))

        # Badge-urile urmează ținta bench (nu 4 fix) — la țintă 3 orice ≥3 e 🔥.
        _TT = _bench_target()

        def _hit_badge(h: int) -> str:
            ic = "🔥" if h >= _TT else ("⭐" if h >= 3 else ("🔹" if h >= 1 else "·"))
            return f"{ic} {h}"

        rows_hist = []
        for di in sorted(per_draw, reverse=True):  # cele mai recente extrageri sus
            d = per_draw[di]
            dd = d["date"]
            rows_hist.append({
                "draw": str(dd) if dd and str(dd) != "None" else f"#{di}",
                "pool": _hit_badge(int(d["pool"])),
                "best": _hit_badge(int(d["best"])),
            })
        if rows_hist:
            ui.label(f"📜 Istoric hits per extragere ({len(rows_hist)} extrageri, cronologic — cele mai recente sus):").classes(
                "text-bold text-caption mt-3")
            # Legendă corelată cu ținta bench: la țintă 3 nu există ⭐ (orice ≥3 e 🔥).
            _star = ("⭐=3 · " if _TT == 4 else f"⭐=3–{_TT - 1} · ") if _TT > 3 else ""
            ui.label("Pentru fiecare extragere reală testată: câte numere a prins Nucleul Dur (pool) "
                     f"și cel mai bun bilet generat. 🔥={_TT}+ · {_star}🔹=1-2 · ·=0").classes("text-caption text-grey")
            ui.table(
                columns=[
                    {"name": "draw", "label": "Data/Extragere", "field": "draw", "align": "left"},
                    {"name": "pool", "label": "În Nucleu (pool)", "field": "pool", "align": "center"},
                    {"name": "best", "label": "Cel mai bun bilet", "field": "best", "align": "center"},
                ],
                rows=rows_hist, pagination=15,
            ).classes("w-full").props("dense")

        # Distribuție Nucleu Dur (hits_union per extragere unică)
        seen, pool_dist = set(), {}
        for p in flat:
            di = getattr(p, "draw_index", id(p))
            if di in seen:
                continue
            seen.add(di)
            hu = getattr(p, "hits_union", 0)
            pool_dist[hu] = pool_dist.get(hu, 0) + 1
        tot = len(seen)
        ui.label("Distribuție Nucleu Dur (câte numere au fost în pool):").classes("text-bold text-caption mt-2")
        for h in sorted(pool_dist, reverse=True):
            c = pool_dist[h]
            if c == 0 and h > 3:
                continue
            pct = (c / tot * 100) if tot else 0
            color = "#f4a261" if h >= 4 else ("#e9c46a" if h >= 3 else "#666")
            ui.html(render_html_safe(
                t"<div style='display:flex;align-items:center;gap:8px;'>"
                t"<div style='width:110px;font-size:0.85em;'>{h} numere</div>"
                t"<div style='flex:1;background:rgba(255,255,255,0.06);border-radius:4px;height:12px;'>"
                t"<div style='background:{color};width:{pct}%;height:100%;border-radius:4px;'></div></div>"
                t"<div style='width:120px;text-align:right;font-size:0.85em;'>{c} extrageri ({pct:.0f}%)</div></div>"
            ))

        # Distribuție performanță variante (bilete) — din .hits
        var_dist = {}
        for p in flat:
            h = getattr(p, "hits", 0)
            var_dist[h] = var_dist.get(h, 0) + 1
        ui.label("Distribuție performanță variante (bilete):").classes("text-bold text-caption mt-2")
        for h in sorted(var_dist, reverse=True):
            c = var_dist[h]
            if c == 0 and h > 3:
                continue
            pct = (c / n * 100) if n else 0
            color = "#28a745" if h >= 3 else ("#17a2b8" if h >= 1 else "#666")
            ui.html(render_html_safe(
                t"<div style='display:flex;align-items:center;gap:8px;'>"
                t"<div style='width:110px;font-size:0.85em;'>{h} ghicite</div>"
                t"<div style='flex:1;background:rgba(255,255,255,0.06);border-radius:4px;height:10px;'>"
                t"<div style='background:{color};width:{pct}%;height:100%;border-radius:4px;'></div></div>"
                t"<div style='width:90px;text-align:right;font-size:0.85em;'>{pct:.1f}%</div></div>"
            ))

        # Tabel pool ≥T (urmează ținta bench-ului: ≥3 sau ≥4)
        _T = _bench_target()
        rows_pool, seen2 = [], set()
        for p in sorted(flat, key=lambda x: (getattr(x, "hits_union", 0), getattr(x, "draw_index", 0)), reverse=True):
            hu = getattr(p, "hits_union", 0)
            di = getattr(p, "draw_index", 0)
            if hu >= _T and di not in seen2:
                seen2.add(di)
                dd = getattr(p, "draw_date", getattr(p, "target_draw_date", None))
                rows_pool.append({"draw": str(dd) if dd and str(dd) != "None" else f"#{di}", "hits": f"🔥 {hu}"})
        if rows_pool:
            ui.label(f"🎯 Istoric Pool (≥{_T} numere):").classes("text-bold text-caption mt-2")
            ui.table(columns=[{"name": "draw", "label": "Data/Extragere", "field": "draw", "align": "left"},
                              {"name": "hits", "label": "Numere în Nucleu", "field": "hits", "align": "left"}],
                     rows=rows_pool).classes("w-full").props("dense")

        # Tabel variante ≥4 — AGREGAT pe extragere. (Înainte: o linie per variantă →
        # aceeași dată apărea de zeci de ori, fiindcă ~zeci de variante prind 4 pe
        # aceeași extragere. Ilizibil.) Acum: o linie per (extragere, hits) + nr. bilete.
        highs = [p for p in flat if getattr(p, "hits", 0) >= _T]
        if highs:
            pm = PRIZE_MAP.get(gk, PRIZE_MAP["6/49"])
            agg: dict = {}
            for p in highs:
                dd = getattr(p, "draw_date", getattr(p, "target_draw_date", None))
                lbl = str(dd) if dd and str(dd) != "None" else f"#{getattr(p, 'draw_index', 0)}"
                key = (lbl, int(p.hits))
                agg[key] = agg.get(key, 0) + 1
            rows_v = []
            for (draw, h), cnt in sorted(agg.items(), key=lambda kv: (kv[0][1], kv[1]), reverse=True):
                prize = pm.get(h, 0)
                rows_v.append({"draw": draw, "hits": f"⭐ {h}", "n": f"{cnt} bilete",
                               "prize": f"~{prize:,} Lei/bilet"})
            n_draws_won = len({d for d, _ in agg})
            ui.label(f"🎯 Istoric Câștiguri Variante (≥{_T} numere) — {n_draws_won} extrageri câștigătoare, agregat:").classes(
                "text-bold text-caption mt-2")
            ui.table(columns=[{"name": "draw", "label": "Data/Extragere", "field": "draw", "align": "left"},
                              {"name": "hits", "label": "Hits", "field": "hits", "align": "center"},
                              {"name": "n", "label": "Bilete câștigătoare", "field": "n", "align": "center"},
                              {"name": "prize", "label": "Est. Premiu", "field": "prize", "align": "right"}],
                     rows=rows_v, pagination=15).classes("w-full").props("dense")

            # Analiză financiară ONESTĂ: fiecare entry din flat = UN bilet (extragere ×
            # variantă) jucat. cost = nr. bilete × preț; premii = suma premiilor reale.
            # (Înainte: total_variants_unice × preț × nr_extrageri → cost umflat de ~100×.)
            total_prize = sum(pm.get(int(getattr(p, "hits", 0)), 0) for p in flat)
            cost = len(flat) * PRICES.get(gk, 8.0)
            profit = total_prize - cost
            roi = (profit / cost * 100) if cost > 0 else 0
            rc = "text-positive" if profit >= 0 else "text-negative"
            ui.label(f"Analiză financiară backtest (full wheel la fiecare din {len(uniq)} extrageri = "
                     f"{len(flat):,} bilete): cost ≈ {cost:,.0f} Lei | premii ≈ {total_prize:,.0f} Lei "
                     f"| ROI: {'+' if profit >= 0 else ''}{roi:.1f}%").classes(rc)
            ui.label("ℹ️ Pe loterie ALEATOARE ROI-ul e mereu puternic negativ dacă joci tot wheel-ul la "
                     "fiecare extragere — scopul aplicației e ACOPERIREA (3+), nu profitul.").classes(
                "text-caption text-grey")


def _wf_summary(flat) -> str | None:
    if not flat:
        return None
    nn = len(flat)
    ap = sum(getattr(p, "hits_union", 0) for p in flat) / nn
    av = sum(getattr(p, "hits", 0) for p in flat) / nn
    bp = max(getattr(p, "hits_union", 0) for p in flat)
    bv = max(getattr(p, "hits", 0) for p in flat)
    return (f"{nn} predicții | avg pool={ap:.2f} | avg variantă={av:.2f} "
            f"| best pool={bp} | best variantă={bv}")


def _build_report() -> str:
    res = STATE.get("results")
    if not isinstance(res, tuple) or len(res) != 2:
        return "(fără rezultate)"
    rb, _ = res
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    out = ["=" * 72, "LOTO ENTERPRISE WHEELING — RAPORT COMPLET", f"Generat: {ts}", "=" * 72]

    def _dump_pool(d: dict, label: str | None, indent: str = "  ") -> None:
        if label:
            out.append(f"\n{indent}{'-'*60}\n{indent}{label}\n{indent}{'-'*60}")
        pool = sorted(int(x) for x in (d.get("hard_core") or []))
        stats = d.get("hard_core_stats") or {}
        eff, req = d.get("pool_size"), d.get("pool_size_requested")
        out.append(f"{indent}Pool efectiv: {eff}"
                   + (f" (cerut {req})" if req and req != eff else "")
                   + f" | Garanție: {d.get('guarantee')} | Variante simple: {len(d.get('variants') or [])}"
                   + f" | Extrageri: {d.get('total_draws')}")
        out.append(f"{indent}Nucleu dur (nr(frecvență)): "
                   + ", ".join(f"{n}({stats.get(str(n), stats.get(n, '?'))})" for n in pool))
        if d.get("hard_core_joker"):
            out.append(f"{indent}Joker: " + ", ".join(str(int(x)) for x in sorted(d["hard_core_joker"])))
        if d.get("p10") is not None:
            out.append(f"{indent}Interval p10–p90: {_fmt_num(d.get('p10'))} – {_fmt_num(d.get('p90'))} "
                       f"(g_range={_fmt_g_range(d.get('g_range'))})")
        au = d.get("audit") or {}
        if au:
            out.append(f"{indent}--- Audit complet (JSON) ---")
            for line in json.dumps(au, indent=2, ensure_ascii=False, default=str).splitlines():
                out.append(f"{indent}{line}")
        vs = d.get("variants") or []
        out.append(f"{indent}--- Variante simple ({len(vs)}) ---")
        # La Joker ultimul element al variantei e NUMĂRUL DE JOKER, nu un al 6-lea
        # număr din urnă (engine-ul îl atașează ciclic în `generate_predictions`).
        # Îl separăm cu „+", ca în UI — altfel raportul îl arăta ca număr obișnuit,
        # deseori duplicând vizual o valoare deja prezentă în variantă.
        _is_jk = bool(d.get("hard_core_joker"))
        for i, v in enumerate(vs, 1):
            if _is_jk and len(v) == 6:
                nums = ", ".join(str(int(x)) for x in v[:5]) + f"  + joker {int(v[-1])}"
            else:
                nums = ", ".join(str(int(x)) for x in v)
            out.append(f"{indent}  V{i}: " + nums)

    for fn, outs in rb:
        out.append(f"\n{'#'*72}\nFIȘIER: {fn}\n{'#'*72}")
        for g, d in _ordered_game_items(outs):
            out.append(f"\n=================  JOC: {g.upper()}  =================")
            flat = STATE["retro"].get(f"{fn}_{g}")
            pool = d["phase1"] if d.get("phase1") else d
            _dump_pool(pool, None)
            wf = _wf_summary(flat)
            if wf:
                out.append(f"  Walk-forward: {wf}")
    return "\n".join(out)


def _save_report_file() -> None:
    """Scrie raport_complet.txt (atomic) după generare. Îl poți deschide/lipi oricând."""
    try:
        atomic_write_text(REPORT_FILE, _build_report())
    except Exception as exc:  # noqa: BLE001
        logger.warning("save raport: %s", exc)


def _show_report() -> None:
    _save_report_file()
    with ui.dialog() as dlg, ui.card().classes("w-11/12 max-w-3xl"):
        ui.label("Raport integral").classes("text-bold")
        ui.label(f"Salvat și în fișier: {REPORT_FILE.name} (în folderul proiectului)").classes("text-caption text-positive")
        ui.textarea(value=_build_report()).classes("w-full").props("readonly autogrow filled")
        ui.button("Închide", on_click=dlg.close)
    dlg.open()


# Descriere lizibilă per metodă (ce e + din ce librărie) — afișată lângă 🏆
_METHOD_DESC = {
    "cover_greedy": "greedy set-cover submodular (acoperire diversă) · CPU",
    "cover_rarity": "greedy cover ponderat pe raritatea extragerilor · CPU",
    "winslips": "stil WinSlips: acoperire roată abreviată pe perechi (covering design) · CPU",
    "frequency":  "euristică simplă · frecvență recentă ponderată",
    "recency":    "euristică simplă · gap-de-la-ultima-apariție",
    "random":     "baseline aleator (prag de referință)",
    # Matematice / statistice / geometrice
    "markov_1":   "lanț Markov ordin 1 (tranziții) · matematic",
    "markov_2":   "lanț Markov ordin 2 · matematic",
    "markov_3":   "lanț Markov ordin 3 · matematic",
    "ngram_bigram":  "n-gramă (bigram) · matematic",
    "ngram_trigram": "n-gramă (trigram) · matematic",
    "vlmm":       "model Markov cu lungime variabilă · matematic",
    "beta_binomial": "Bayesian Beta-Binomial · probabilistic",
    "polya_urn":  "urnă Pólya (auto-întărire) · probabilistic",
    "bayes_poisson": "Bayesian Poisson · probabilistic",
    "neg_binomial":  "binomial negativ · probabilistic",
    "fourier":    "analiză spectrală Fourier (cicluri) · geometric/frecvențial",
    "wavelet_haar": "transformată wavelet Haar · geometric/frecvențial",
    "stl":        "descompunere STL (trend+sezon) · serie temporală",
    "ssa":        "Singular Spectrum Analysis · geometric",
    "dmd":        "Dynamic Mode Decomposition · geometric",
    "hmm_gaussian":  "Hidden Markov Model gaussian · probabilistic",
    "holt_winters":  "Holt-Winters (netezire exponențială) · serie temporală",
    "theta_auto": "metoda Theta · serie temporală",
    "ets_auto":   "ETS (error-trend-seasonal) · serie temporală",
    "arima_auto": "ARIMA auto · serie temporală",
    "pca_resid_surprise": "surpriză residuală după PCA dominant · matematic",
    "mi_lag_bag": "mutual information cu bag-ul extragerii anterioare · matematic",
    "nmf_cooc": "NMF pe co-apariții recente · matematic",
    "cusum_appearance": "CUSUM pe reziduuri de apariție (regim) · matematic",
    "circular_kernel": "kernel densitate pe topologia circulară 1…N · matematic",
    "649_katz12_gap88": "12% KatzCommunity + 88% gap_poisson (search winner, +21.7% 4+ @ k16)",
    "649_katz15_gap85": "15% KatzCommunity + 85% gap_poisson (search blend)",
    "graph_649_katz_community": "60% KatzHigh + 40% community strength (graf)",
}


def _render_pool_body(fname: str, game: str, data: dict, *, skey_suffix: str = "",
                      with_wf: bool = True, res_prefix: str = "") -> None:
    """Randează un pool complet (badges, p10/p90, audit, cost, WF, variante, stages).
    Folosit o dată normal, sau de DOUĂ ori la auto-invert (Faza 1 + Faza 2)."""
    pool = data.get("hard_core") or []
    stats = data.get("hard_core_stats") or {}
    eff = data.get("pool_size")
    req = data.get("pool_size_requested")
    variants = data.get("variants") or []

    with ui.row().classes("gap-6 items-center"):
        ui.label(f"Pool efectiv: {eff}" + (f" (cerut {req})" if req and req != eff else ""))
        # Garanția EFECTIV folosită la wheel (audit.wheel_guarantee_used) vs cea CERUTĂ
        # din setări — pot diferi; rezultate vechi n-au cheia → fallback pe setare.
        _g_req = data.get("guarantee")
        _g_used = (data.get("audit") or {}).get("wheel_guarantee_used")
        if _g_used is None:
            _g_used = _g_req
        try:
            _g_diff = _g_req is not None and int(_g_used) != int(_g_req)
        except (TypeError, ValueError):
            _g_diff = _g_used != _g_req
        ui.label(f"Garanție: {_g_used}" + (f" (cerută: {_g_req})" if _g_diff else ""))
        ui.label(f"Variante simple: {len(variants)}")
        # Acoperirea REALĂ a garanției (set-cover), pe setul FINAL de bilete —
        # 100% = orice grup de `guarantee` numere prinse în pool apare garantat
        # pe cel puțin un bilet. Singura cauză rămasă pentru <100% e limita de
        # variante: nu mai există niciun filtru care să elimine bilete DUPĂ wheeling
        # (a doua ramură, pe `audit.anomaly_filter`, nu se mai executa niciodată —
        # engine-ul nu mai scrie cheia).
        _cov = (data.get("context") or {}).get("coverage_pct")
        if _cov is not None:
            if float(_cov) >= 100.0:
                ui.html(render_html_safe(t"<b style='color:#22c55e'>✅ Acoperire garanție: 100%</b>"))
            else:
                reason = (
                    "limita «Variante maxime» a tăiat garanția — "
                    "pune 0 = nelimitat pentru garanție completă"
                )
                ui.html(render_html_safe(
                    t"<b style='color:#ef4444'>⚠️ Acoperire garanție: {float(_cov):.1f}%</b> "
                    t"<span style='opacity:.7'>({reason})</span>"
                ))
        ui.label(f"Extrageri: {data.get('total_draws')}")
        # Timp de scoring (CPU — GPU eliminat complet).
        _au = data.get("audit") or {}
        _sms = (_au.get("performance") or {}).get("score_time_ms")
        if _sms is not None:
            ui.html(render_html_safe(
                t"<b style='color:#f97316'>🖥️ CPU</b> "
                t"<span style='opacity:.6'>({float(_sms) / 1000:.1f}s)</span>"
            ))

    # Metoda câștigătoare folosită de scorer (din bench/best_methods.json)
    bw = (data.get("audit") or {}).get("bench_winner") or {}
    if bw:
        parts = []
        for gkey, info in bw.items():
            m = info.get("method", "?")
            ph = info.get("pool_hint")
            fam = info.get("family", "")
            desc = _METHOD_DESC.get(m, "")
            tail = ""
            if desc:
                tail += render_html_safe(t" <span style='opacity:.65'>— {desc}</span>")
            meta = ", ".join(x for x in [fam, (f"pool {ph}" if ph else "")] if x)
            if meta:
                tail += render_html_safe(t" <span style='opacity:.45'>[{meta}]</span>")
            _ens = info.get("ensemble") or []
            _n_ens = len(_ens)
            if _n_ens > 1:
                _ens_str = " + ".join(
                    f"{e.get('method')} ({float(e.get('weight', 0)) * 100:.0f}%)" for e in _ens
                )
                tail += render_html_safe(
                    t"<br><span style='opacity:.75;font-size:.85em'>— pool-ul folosește ensemble-ul de {_n_ens} metode de mai jos</span>"
                    t"<br><span style='opacity:.6;font-size:.85em'>⚖️ ensemble (variance-reduction): {_ens_str}</span>"
                )
                # Cu ensemble >1, pool-ul NU vine dintr-o singură metodă →
                # eticheta onestă e „cap de listă", nu „metoda folosită".
                head = render_html_safe(
                    t"{gkey} → metoda CAP DE LISTĂ (cea mai stabilă): "
                    t"<b style='color:#ff4d4f;font-size:1.05em'>{m}</b>"
                )
            else:
                head = render_html_safe(
                    t"{gkey} → <b style='color:#ff4d4f;font-size:1.05em'>{m}</b>"
                )
            parts.append(head + tail)
        ui.html(
            render_html_safe(t"🏆 Metodă câștigătoare (bench): ")
            + "<br>".join(parts)
        ).classes("text-caption")
    else:
        ui.label("🏆 Metodă scorer: fallback implicit (fără decizie bench disponibilă)").classes(
            "text-caption text-grey")

    ui.label("Nucleu dur (pool):").classes("text-bold mt-2")
    _badges(pool, stats)
    if data.get("hard_core_joker"):
        ui.label("Joker:").classes("text-bold mt-1")
        _badges(data.get("hard_core_joker"), data.get("hard_core_joker_stats"))

    if data.get("p10") is not None:
        ui.label(f"Interval p10–p90: {_fmt_num(data.get('p10'))} – {_fmt_num(data.get('p90'))} "
                 f"(g_range={_fmt_g_range(data.get('g_range'))})").classes("text-caption")

    audit = data.get("audit") or {}
    if audit:
        _render_audit(audit)
        _render_adaptive(audit)

    _render_cost(game, data)

    if with_wf:
        flat = STATE["retro"].get(f"{res_prefix}{fname}_{game}")
        if flat:
            _bw = (data.get("audit") or {}).get("bench_winner") or {}
            _wm = next((info.get("method") for info in _bw.values() if info.get("method")), "")
            _render_walk_forward(flat, game, is_invert=False, method=_wm)

    if variants:
        is_jk = "joker" in game.lower()
        skey = f"{fname}_{game}{skey_suffix}"
        show_all = STATE["show_all"].get(skey, False)
        with ui.expansion(f"Variante simple ({len(variants)})", value=False).classes("w-full"):
            shown = variants if show_all else variants[:10]
            for i, v in enumerate(shown, 1):
                if is_jk and len(v) == 6:
                    nums = ", ".join(str(int(x)) for x in v[:5]) + f"  +{int(v[-1])}"
                else:
                    nums = ", ".join(str(int(x)) for x in v)
                ui.html(render_html_safe(
                    t"<span style='color:#6b7280;font-weight:600'>V{i:>3}:</span> "
                    t"<span style='color:#e5e7eb'>{nums}</span>"
                )).classes("font-mono text-sm")
            if len(variants) > 10:
                def _toggle(k=skey):
                    STATE["show_all"][k] = not STATE["show_all"].get(k, False)
                    results_panel.refresh()
                ui.button(
                    "🔼 Ascunde" if show_all else f"🔽 Arată toate ({len(variants)})",
                    on_click=_toggle,
                ).props("flat dense")

    if audit:
        _render_stages(audit)


@ui.refreshable
def wf_progress_panel() -> None:
    """Progres walk-forward, SEPARAT de results_panel: tick-ul (2s) refreshează DOAR
    asta, nu tot bundle-ul de rezultate — altfel expansion-urile deschise de user
    (ex. 🏆 Clasament bench, Variante, Pipeline) s-ar reseta/închide la fiecare poll."""
    if not STATE.get("wf_status"):
        return
    _wfp = float(STATE.get("wf_progress") or 0.0)
    # ETA walk-forward: estimare liniară din progres (elapsed × (1-p)/p), PLAFONATĂ
    # la bugetul de timp rămas (WF_TOTAL_BUDGET_S e deadline DUR — estimarea liniară
    # arăta „~14m rămas" când bugetul mai permitea doar câteva minute).
    _eta = ""
    _ws = STATE.get("wf_start")
    if _ws and 0.02 < _wfp < 1.0:
        _rem = (time.time() - _ws) * (1.0 - _wfp) / _wfp
        _dl = STATE.get("wf_deadline")
        if _dl:
            _budget_left = max(0.0, float(_dl) - time.time())
            if _rem > _budget_left:
                _eta = (f"  ·  rămas ≤{_fmt_dur(_budget_left)} (buget; estimare liniară "
                        f"~{_fmt_dur(_rem)} → jocurile rămase pot ieși PARȚIALE)")
            else:
                _eta = f"  ·  rămas ~{_fmt_dur(_rem)}"
        else:
            _eta = f"  ·  rămas ~{_fmt_dur(_rem)}"
    ui.label(STATE["wf_status"] + _eta).classes("text-info")
    ui.linear_progress(value=_wfp, show_value=False).props("instant-feedback rounded").classes("w-full")
    ui.label(f"{int(_wfp * 100)}%" + _eta).classes("text-caption text-info")


@ui.refreshable
def results_panel() -> None:
    wf_progress_panel()

    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return

    elapsed = ""
    if STATE.get("job_elapsed") is not None:
        elapsed = f" (generare: {_fmt_dur(STATE['job_elapsed'])}"
        if STATE.get("wf_elapsed") is not None:
            elapsed += f" · total cu walk-forward: {_fmt_dur(STATE['wf_elapsed'])}"
        elif STATE.get("wf_status"):
            elapsed += " · walk-forward încă rulează"
        elapsed += ")"
    with ui.row().classes("items-center gap-3 mt-2"):
        ui.label(f"Rezultate{elapsed}").classes("text-h6")
        ui.button("📋 Raport integral", on_click=_show_report).props("flat dense")

    _render_results_bundle(results[0])


def _method_library(name: str, family: str = "") -> str:
    """Librăria/categoria lizibilă a metodei. Din `family` (preferat) sau din nume (fallback)."""
    f = (family or "").strip().lower()
    if f:
        if f.startswith("ml-"):
            return "gradient boosting (XGBoost/LightGBM/CatBoost)" if "boost" in f else "scikit-learn"
        if f.startswith("classical"):
            return "statsmodels"
        if f.startswith("ensemble"):
            return "ansamblu (mix de metode)"
        if f == "coverage":
            return "greedy set-cover (numpy)"
        if f.startswith("graph"):
            return "graph/network (numpy)"
        if f.startswith("math") or f.startswith("geometric") or f.startswith("probabil"):
            return "independent (numpy)"
        return family  # familia brută dacă n-o recunoaștem
    n = (name or "").lower()
    if n.startswith("ml_"):
        return ("gradient boosting (XGBoost/LightGBM/CatBoost)"
                if any(b in n for b in ("xgb", "lgbm", "catboost", "boost", "gbm"))
                else "scikit-learn")
    if n in {"arima_auto", "ets_auto", "theta_auto", "holt_winters", "stl", "croston_classic"}:
        return "statsmodels"
    return "independent (numpy)"


# Eticheta UI/worker → cheia exactă din folds.csv / best_methods.json
_LABEL_TO_FOLDS_GAME = {
    "6/49": "loto_6_49",
    "5/40": "loto_5_40",
    "joker": "joker_urna1",
}


def _baseline_methods() -> frozenset[str]:
    """Metodele care sunt DOAR baseline de referință, NU candidați de producție.

    Trebuie să rămână SINCRON cu excluderea din decizie (decision.py:
    `methods = [m for m in ... if m != "random"]`). Preferăm constanta din
    decision.py dacă există; altfel fallback identic cu ce face decizia azi.
    NB: `frequency` are family="baseline" în folds.csv, DAR decizia NU o exclude
    (e și fallback-ul de scoring în producție) → rămâne candidat aici."""
    try:
        from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION as _EX
        return frozenset(str(m) for m in _EX)
    except Exception:  # noqa: BLE001
        return frozenset({"random"})


def _decision_entry(folds_game_key: str, pool: int) -> dict:
    """Intrarea deciziei pentru (joc, pool) din best_methods.json (`auto_pilot_per_pool[kN]`).

    `{}` dacă lipsește fișierul/cheia. Folosim `_load_config` din method_selector
    (cache invalidat pe mtime → vede un Re-Bench fără restart de UI)."""
    try:
        from loto_enterprise.core.method_selector import _load_config
        g = (_load_config().get("games") or {}).get(folds_game_key) or {}
        e = (g.get("auto_pilot_per_pool") or {}).get(f"k{int(pool)}")
        return e if isinstance(e, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _decision_low_confidence(entry: dict) -> bool | None:
    """Decizia a căzut pe ramura de FALLBACK (nicio metodă n-a bătut random consistent)?

    True/False, sau None dacă nu se poate ști. Citim `low_confidence` (cheie nouă)
    și, pentru best_methods.json scrise de decizia VECHE (fără cheia asta),
    deducem din `qualifying_methods == 0` — decision.py scrie 0 exact pe ramurile
    de fallback (`len(qualifying)` e 0 acolo)."""
    if not entry:
        return None
    if "low_confidence" in entry:
        return bool(entry["low_confidence"])
    if "qualifying_methods" in entry:
        try:
            return int(entry["qualifying_methods"]) == 0
        except Exception:  # noqa: BLE001
            return None
    return None


def _consistency_pct(entry: dict) -> int:
    """Pragul de consistență al deciziei, în %, ca ÎNTREG (60 = „≥60% din ferestre").

    Preferăm valoarea stampilată în best_methods.json (`consistency_threshold`,
    scrisă chiar de decizia care a produs intrarea), apoi constanta din
    decision.py; ultimul resort 60 = `CONSISTENCY_THRESHOLD` actual."""
    try:
        v = entry.get("consistency_threshold")
        if v is not None:
            return int(round(float(v) * 100))
    except Exception:  # noqa: BLE001
        pass
    try:
        from loto_enterprise.benchmark.decision import CONSISTENCY_THRESHOLD
        return int(round(float(CONSISTENCY_THRESHOLD) * 100))
    except Exception:  # noqa: BLE001
        return 60


def _wilson_pooled_rate(grp, metric: str) -> float | None:
    """Limita inferioară Wilson pe rata POOLED (Σ rate·n_test / Σ n_test).

    ACEEAȘI metrică pe care decision.py alege câștigătorul (`_rate_target_confidence`):
    formula Wilson e IMPORTATĂ din decision.py (nu duplicată); aici doar agregăm
    pooled pe n_test, ca ordinea din clasament să coincidă cu ordinea deciziei.
    (`_rate_target_confidence` e closure intern în decide_optimal_config_for_pool,
    deci neimportabil ca atare.) None = nu se poate calcula (fără n_test / import)."""
    try:
        from loto_enterprise.benchmark.decision import _wilson_lower_bound
    except Exception:  # noqa: BLE001
        return None
    if metric not in grp.columns or "n_test" not in grp.columns:
        return None
    pairs = grp[[metric, "n_test"]].dropna()
    if pairs.empty:
        return None
    successes = float((pairs[metric] * pairs["n_test"]).sum())
    n_total = float(pairs["n_test"].sum())
    if n_total <= 0:
        return None
    return float(_wilson_lower_bound(successes, n_total))


def _render_bench_leaderboard_slice(
    df: pd.DataFrame,
    folds_game_key: str,
    pool: int,
    section_label: str,
    top_n: int = 10,
) -> None:
    """Un clasament bench pentru (joc bench, pool K) — fără amestec între joker urna1/urna2."""
    sub = df[df["game"].astype(str) == folds_game_key]
    if "is_random" in sub.columns:
        sub = sub[sub["is_random"] == False]  # noqa: E712
    if "failed" in sub.columns:
        sub = sub[sub["failed"] != True]  # noqa: E712
    if sub.empty:
        return
    try:
        from loto_enterprise.benchmark.decision import BENCH_HIT_TARGET as _T
    except Exception:  # noqa: BLE001
        _T = 3
    # preferă pragul configurat (3+); cere coloană cu DATE (nu all-NaN din cache vechi);
    # fallback la 4+ apoi avg_hits.
    def _has(c):
        return c in sub.columns and sub[c].notna().any()
    _shown_t, metric = _T, None
    for _c in (f"rate_{_T}plus_k{pool}", f"rate_{_T}plus"):
        if _has(_c):
            metric = _c
            break
    if metric is None:
        for _c in (f"rate_4plus_k{pool}", "rate_4plus"):
            if _has(_c):
                _shown_t, metric = 4, _c
                break
    has_4plus = metric is not None
    if metric is None:
        metric = "avg_hits_topk"
    if metric not in sub.columns:
        return
    has_family = "family" in sub.columns

    def _rate_for(grp, n):
        """Rata de ≥n pentru o metodă, POOLED pe n_test (preferă coloana pe pool).

        Ponderarea e obligatorie, nu cosmetică: ferestrele sunt CUIBĂRITE (fiecare e
        ultimele P% din istoric — vezi `runner.run_benchmark`), deci au dimensiuni de
        ordine de mărime diferite (6/49: 258 / 772 / 1544 / 2492 extrageri). O medie
        neponderată dă aceeași greutate ferestrei de 258 ca celei de 2492 și fabrică
        astfel „lift-uri" care nu există în datele pooled — exact metrica pe care se
        ia decizia (`_wilson_pooled_rate` / decision.py) e pooled pe n_test.
        Fără `n_test` (folds vechi) cădem pe media neponderată, ca înainte."""
        for c in (f"rate_{n}plus_k{pool}", f"rate_{n}plus"):
            if c not in grp.columns:
                continue
            if "n_test" in grp.columns:
                pairs = grp[[c, "n_test"]].dropna()
                n_total = float(pairs["n_test"].sum()) if not pairs.empty else 0.0
                if n_total > 0:
                    return float((pairs[c] * pairs["n_test"]).sum() / n_total)
            v = float(grp[c].mean())
            if v == v:  # nu e NaN
                return v
        return None

    # TIE-BREAK IDENTIC cu decizia (decision.py: `qualifying.sort(key=(Wilson_lb,
    # w_lift, consistență))`). Egalitățile EXACTE pe Wilson sunt masive (succesele
    # sunt întregi → multe metode au aceeași proporție pooled), deci fără aceleași
    # chei secundare ordinea din UI ar diverge de decizie pe ~un sfert din poziții.
    # `base_col` = media de hituri la pool (k{pool}) — exact coloana pe care
    # decizia calculează lift-ul vs `random` și consistența pe ferestre.
    _base_col = f"k{pool}"
    _lift_fn = _beat_fn = None
    _rnd_frame = None
    if _base_col in sub.columns:
        try:
            from loto_enterprise.benchmark.decision import (
                _weighted_mean_lift as _lift_fn,
                _windows_method_beats_random as _beat_fn,
            )
            _rnd_frame = sub[sub["method"] == "random"]
            if _rnd_frame.empty:
                _lift_fn = _beat_fn = None
        except Exception:  # noqa: BLE001
            _lift_fn = _beat_fn = None

    _BASE = _baseline_methods()
    rows = []
    _conf_ok = False  # măcar o metodă are Wilson calculabil → sortăm ca decizia
    _lift_ok = False  # lift+consistență calculabile → tie-break identic cu decizia
    for m, grp in sub.groupby("method"):
        score = float(grp[metric].mean())
        avg = float(grp["avg_hits_topk"].mean()) if "avg_hits_topk" in grp.columns else score
        fam = ""
        if has_family:
            _f = grp["family"].dropna().astype(str)
            fam = _f.iloc[0] if not _f.empty else ""
        # Wilson doar pe RATE (proporții). Când metrica e fallback-ul avg_hits_topk
        # (folds vechi fără coloane rate_*), nu e proporție → nu are sens.
        conf = _wilson_pooled_rate(grp, metric) if has_4plus else None
        if conf is not None:
            _conf_ok = True
        w_lift = cons = None
        if _lift_fn is not None:
            try:
                w_lift = float(_lift_fn(grp, _rnd_frame, _base_col))
                _nb, _nt = _beat_fn(grp, _rnd_frame, _base_col)
                cons = _nb / max(_nt, 1)
                _lift_ok = True
            except Exception:  # noqa: BLE001
                w_lift = cons = None
        rows.append((m, score, avg, _method_library(m, fam),
                     _rate_for(grp, 3), _rate_for(grp, 4), conf, w_lift, cons))
    # ORDONARE = ACEEAȘI metrică ȘI aceleași chei secundare ca decizia (Wilson
    # pooled pe n_test → lift mediu ponderat vs random → consistență). Fără
    # decision.py / fără coloana k{pool} / fără rândurile `random` → cădem pe
    # (Wilson, rată brută, avg_hits) și eticheta o spune explicit.
    if _lift_ok:
        rows.sort(key=lambda r: ((r[6] if r[6] is not None else -1.0),
                                 (r[7] if r[7] is not None else -1e18),
                                 (r[8] if r[8] is not None else -1.0)), reverse=True)
    else:
        rows.sort(key=lambda r: ((r[6] if r[6] is not None else -1.0), r[1], r[2]), reverse=True)
    if not rows:
        return
    # Baseline-urile („random") NU sunt candidați: rămân vizibile ca reper, dar nu
    # primesc rang și nu intră în „Top N din M metode".
    competitors = [r for r in rows if r[0] not in _BASE]
    if not competitors:
        return
    # Slice afișat: primele `top_n` CANDIDATE + baseline-urile care cad printre ele.
    top_idx: list[int] = []
    _n_comp = 0
    for _i, rec in enumerate(rows):
        top_idx.append(_i)
        if rec[0] not in _BASE:
            _n_comp += 1
            if _n_comp >= top_n:
                break
    top_rows = [rows[i] for i in top_idx]
    _n_shown = _n_comp
    label = (
        f"rata {_shown_t}+ @ pool {pool}" if has_4plus and metric.endswith(f"_k{pool}")
        else f"rata {_shown_t}+ numere ghicite" if has_4plus
        else "medie hituri / extragere"
    )
    # Eticheta spune EXACT cât face ordonarea: „ca decizia" doar când și tie-break-ul
    # secundar e cel al deciziei (lift + consistență), altfel nu promite identitate.
    if _conf_ok and _lift_ok:
        label += " · sortat ca decizia (Wilson → lift → consistență)"
    elif _conf_ok:
        label += " · sortat după Wilson (tie-break ≠ decizia: rată brută, nu lift)"
    else:
        label += " · sortat după rata brută"
    # Baseline-ul PUR aleator (hipergeometric) la acest pool — afișat O DATĂ în titlu
    # + multiplicator pe fiecare rată. Onestitate: „3+: 10%" pare edge, dar hazardul
    # singur dă ~9% la pool 10 pe 6/49 → diferența reală e mică (zgomot).
    _rnd3 = _random_rate_hypergeo(folds_game_key, pool, 3)
    _rnd4 = _random_rate_hypergeo(folds_game_key, pool, 4)
    _rnd_t = _random_rate_hypergeo(folds_game_key, pool, _shown_t)
    if has_4plus and _rnd_t is not None:
        label += f" · baseline random = {_rnd_t * 100:.2f}%"
    winner = competitors[0]  # capul clasamentului (doar candidați, baseline-urile excluse)
    # Metoda EFECTIV aleasă pentru pool (best_methods.json) — poate diferi de #1 din
    # mai multe motive (filtru de consistență, ramura de fallback, decizie scrisă de
    # un bench mai vechi decât folds.csv). Explicația exactă se compune mai jos, din
    # intrarea reală a deciziei, nu dintr-o presupunere.
    _dec = _decision_entry(folds_game_key, pool)
    _dec_low = _decision_low_confidence(_dec)
    _cons_pct = _consistency_pct(_dec)
    try:
        from loto_enterprise.core.method_selector import get_winner_name
        chosen_name = get_winner_name(folds_game_key, pool)
    except Exception:  # noqa: BLE001
        chosen_name = winner[0]

    def _row(i, rec):
        """`i=None` → rând de BASELINE (referință, fără rang și fără pretenția de candidat)."""
        m, score, avg, lib = rec[:4]
        r3, r4, conf = rec[4], rec[5], rec[6]
        if has_4plus:
            parts = []
            # Primul = criteriul REAL de ordonare/decizie (Wilson pooled); ratele brute
            # rămân ca informație secundară.
            if conf is not None:
                parts.append(f"Wilson {_shown_t}+: {conf*100:.2f}%")
            if r3 is not None:
                _m3 = f" ({r3 / _rnd3:.2f}x random)" if _rnd3 else ""
                parts.append(f"brut 3+: {r3*100:.1f}%{_m3}")
            if r4 is not None:
                _m4 = f" ({r4 / _rnd4:.2f}x random)" if _rnd4 else ""
                parts.append(f"brut 4+: {r4*100:.1f}%{_m4}")
            sc_txt = " · ".join(parts) if parts else f"medie: {score:.3f}"
        else:
            sc_txt = f"medie: {score:.3f}"
        is_base = i is None
        is_chosen = (not is_base) and (m == chosen_name)
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label("🎲" if is_base else f"{i}.").classes("text-bold text-grey w-6")
            ui.label(("🏆 " + m) if is_chosen else m).classes(
                "text-bold text-positive" if is_chosen
                else "text-bold text-orange" if is_base else "text-bold")
            _pref = "baseline (referință, NU e candidat) · " if is_base else ""
            ui.label(f"· {_pref}{lib} · {sc_txt} · medie/extragere {avg:.2f}").classes("text-caption text-grey")

    title = f"🏆 Clasament bench — {section_label} ({label})"
    with ui.expansion(title, value=True).classes("w-full"):
        _chosen_lib = next((r[3] for r in rows if r[0] == chosen_name), "")
        _chosen_suffix = f" · {_chosen_lib}" if _chosen_lib else ""
        # Ensemble-ul EFECTIV folosit la pool (best_methods.json): cu >1 membru,
        # pool-ul NU e construit din metoda unică → „ALEASĂ" ar fi fals; capul de
        # listă e doar cea mai stabilă componentă a blend-ului.
        _ens_names: list[tuple[str, float]] = []
        try:
            from loto_enterprise.core.method_selector import get_ensemble_for_game
            _ens_names = [(nm, float(wt)) for nm, _fn, wt in
                          get_ensemble_for_game(folds_game_key, pool, max_methods=3)]
        except Exception:  # noqa: BLE001
            _ens_names = []
        if len(_ens_names) > 1:
            _n_ens = len(_ens_names)
            _ens_str = " + ".join(f"{nm} ({wt * 100:.0f}%)" for nm, wt in _ens_names)
            ui.html(render_html_safe(
                t"🏆 <b style='color:#22c55e'>Metoda CAP DE LISTĂ (cea mai stabilă): {chosen_name}</b>"
                t"{_chosen_suffix} <span style='opacity:.75'>— pool-ul folosește "
                t"ensemble-ul de {_n_ens} metode de mai jos</span>"
            )).classes("text-caption")
            ui.html(render_html_safe(
                t"<span style='opacity:.6;font-size:.85em'>⚖️ ensemble (variance-reduction): {_ens_str}</span>"
            )).classes("text-caption")
        else:
            ui.html(render_html_safe(
                t"🏆 <b style='color:#22c55e'>Metoda ALEASĂ (folosită la pool): {chosen_name}</b>{_chosen_suffix}"
            )).classes("text-caption")
        if chosen_name != winner[0]:
            # De ce diferă ALEASĂ de #1 — enumerăm doar cauzele care chiar există,
            # în funcție de ce spune intrarea reală a deciziei. Vechiul text invoca
            # DOAR filtrul de consistență: incomplet (mai există ordonarea și
            # decorelarea ensemble-ului) și FALS pe ramura de fallback, unde
            # scorer-ul e ales tocmai fiindcă nimeni n-a trecut pragul.
            if _conf_ok and _lift_ok:
                _ord = (f"aceleași chei ca decizia (limita Wilson a ratei {_shown_t}+ pooled pe "
                        f"n_test → lift mediu vs random → consistență)")
            elif _conf_ok:
                _ord = (f"limita Wilson a ratei {_shown_t}+ (pooled pe n_test); tie-break-ul "
                        f"secundar diferă de decizie (rată brută, nu lift)")
            else:
                _ord = f"rata brută {_shown_t}+ (n_test lipsă → fără Wilson)"
            if _dec_low is True:
                _why = ("nicio metodă n-a bătut random consistent (≥"
                        f"{_cons_pct}% din ferestre) → decizia a căzut pe "
                        "ramura CONSERVATOARE de fallback: alegerea nu e o dovadă de "
                        "superioritate, diferențele sunt zgomot")
            elif _dec_low is False:
                _why = (f"decizia aplică ÎN PLUS filtrul de consistență (să bată random în ≥"
                        f"{_cons_pct}% din ferestre), pe care clasamentul "
                        f"nu-l aplică; iar la scoring pool-ul folosește ensemble-ul, din care "
                        f"membrii redundanți/corelați sunt eliminați")
            else:
                _why = ("best_methods.json nu spune pe ce ramură s-a luat decizia (fișier scris "
                        "de o versiune veche) — poate fi filtrul de consistență, ramura de "
                        "fallback sau pur și simplu o decizie mai veche decât folds.csv")
            ui.label(f"ℹ️ Lista e sortată după {_ord}; cap: {winner[0]}. Metoda ALEASĂ "
                     f"({chosen_name}, marcată 🏆) diferă fiindcă {_why}.").classes(
                "text-caption text-grey")
        elif _dec_low is True:
            # Chiar și când ALEASĂ == #1, ramura de fallback trebuie spusă: „câștigătorul"
            # nu a bătut hazardul consistent.
            ui.label(f"⚠️ Decizia pentru acest pool e pe ramura de FALLBACK: nicio metodă n-a "
                     f"bătut random în ≥{_cons_pct}% din ferestre. "
                     f"Alegerea e conservatoare — diferențele dintre metode sunt zgomot.").classes(
                "text-caption text-warning")
        if not has_family:
            ui.label("ℹ️ Librăria e estimată din nume (folds.csv vechi). Rulează un Re-Bench "
                     "pentru etichete exacte.").classes("text-caption text-orange")
        ui.label(f"Top {_n_shown} din {len(competitors)} metode candidate").classes("text-bold text-blue mt-2")
        _cats = sorted({rec[3] for rec in competitors if rec[3]})  # categorii REALE (din folds)
        if _cats:
            ui.label("Categorii: " + " · ".join(_cats)).classes("text-caption text-grey")
        _rank = 0
        for rec in top_rows:
            if rec[0] in _BASE:
                _row(None, rec)          # baseline: vizibil ca reper, fără rang
            else:
                _rank += 1
                _row(_rank, rec)
        # Baseline-urile care NU au intrat în slice: spune unde ar cădea (informativ),
        # fără să pară competitor.
        _shown_names = {rec[0] for rec in top_rows}
        for _bi, _brec in enumerate(rows):
            if _brec[0] not in _BASE or _brec[0] in _shown_names:
                continue
            _better = sum(1 for r in rows[:_bi] if r[0] not in _BASE)
            # Numitorul include și baseline-ul însuși (nu doar candidații) — altfel
            # poziția poate ajunge la N+1 „din N" (contradicție) când baseline-ul
            # e sub TOȚI candidații.
            ui.label(f"🎲 baseline «{_brec[0]}» (referință, NU e candidat) — ar cădea pe locul "
                     f"{_better + 1} din {len(competitors) + 1} "
                     f"({len(competitors)} metode candidate + acest baseline).").classes(
                "text-caption text-grey")
        # SIMETRIC cu baseline-ul: dacă metoda EFECTIV folosită la generare nu apare în
        # slice, spune unde cade. Altfel 🏆 lipsește complet din listă, fără niciun
        # indiciu — exact metoda despre care utilizatorul vrea să știe cel mai mult.
        if chosen_name and chosen_name not in _shown_names:
            _ci = next((i for i, r in enumerate(rows) if r[0] == chosen_name), None)
            if _ci is None:
                ui.label(f"🏆 metoda ALEASĂ «{chosen_name}» nu apare în folds.csv pentru acest "
                         f"(joc, pool) — decizia e mai veche decât bench-ul curent.").classes(
                    "text-caption text-orange")
            else:
                _cbetter = sum(1 for r in rows[:_ci] if r[0] not in _BASE)
                ui.label(f"🏆 metoda ALEASĂ «{chosen_name}» — locul {_cbetter + 1} din "
                         f"{len(competitors)} metode candidate (în afara top-{top_n} afișat)."
                         ).classes("text-caption text-positive")


def _last_csv_draw(fname: str):
    """(date_str, [numere], joker|None) din ULTIMA linie a CSV-ului încărcat pentru
    acest fișier; None dacă lipsește. Faithful la CSV (exact ultima extragere)."""
    df = next((d for f, d in STATE.get("datasets", []) if f == fname), None)
    if df is None or len(df) == 0:
        return None
    try:
        last = df.iloc[-1]
    except Exception:  # noqa: BLE001
        return None
    cols = [str(c) for c in df.columns]
    num_cols = sorted((c for c in cols if len(c) > 1 and c[0] == "n" and c[1:].isdigit()),
                      key=lambda c: int(c[1:]))
    nums = []
    for c in num_cols:
        try:
            nums.append(int(last[c]))
        except Exception:  # noqa: BLE001
            pass
    if not nums:
        return None
    joker = None
    if "joker" in cols:
        try:
            joker = int(last["joker"])
        except Exception:  # noqa: BLE001
            joker = None
    date_str = ""
    for dc in ("date", "Data", "data", "Date"):
        if dc in cols:
            try:
                date_str = str(last[dc])
            except Exception:  # noqa: BLE001
                date_str = ""
            break
    return (date_str, nums, joker)


def _render_last_csv_draw(fname: str) -> None:
    """Reper lângă clasament: ULTIMA extragere reală din CSV-ul încărcat (data + numere
    + joker dacă există)."""
    info = _last_csv_draw(fname)
    if not info:
        return
    date_str, nums, joker = info
    txt = "  ".join(str(n) for n in nums)
    if joker is not None:
        txt += f"   ·   joker {joker}"
    cap = "📅 Ultima extragere din CSV" + (f" ({date_str})" if date_str else "") + ":"
    with ui.row().classes("items-center gap-2"):
        ui.label(cap).classes("text-caption text-grey")
        ui.label(txt).classes("text-bold text-info")


def _render_bench_leaderboard(game_label: str, top_n: int = 10) -> None:
    """Top-N metode din ULTIMUL bench pentru acest joc (folds.csv). Joker = urne separate."""
    fp = PROJECT_ROOT / "bench_results" / "folds.csv"
    if not fp.exists():
        return
    try:
        df = pd.read_csv(fp)
    except Exception:  # noqa: BLE001
        return
    if df.empty or "method" not in df.columns or "game" not in df.columns:
        return
    pool = int(SETTINGS.get("pool_size_val", 10))
    if game_label == "joker":
        slices = [
            ("joker_urna1", pool, "Joker Urna 1 (5/45)"),
            ("joker_urna2", 1, "Joker Urna 2 (1/20)"),
        ]
    else:
        folds_key = _LABEL_TO_FOLDS_GAME.get(game_label, game_label)
        slices = [(folds_key, pool, game_label.upper())]
    for folds_key, k_pool, sect in slices:
        _render_bench_leaderboard_slice(df, folds_key, k_pool, sect, top_n=top_n)


def _render_bench_live_leaderboard(bench_start=None, progress=None) -> None:
    """Clasament PARȚIAL în timpul bench-ului — din folds.csv (flush-uit periodic la
    ~100 rezultate). Metodele apar pe măsură ce TERMINĂ. Câștigătorul final + Auto-Pilot
    se decid abia la sfârșit. Citește folds.csv O DATĂ pe render (parțial → mic).
    `progress` (0..1) — la ≥1.0 testele-s gata, dar procesul încă scrie decizia/raportul
    → titlul NU mai zice „PARȚIAL" (inadvertență văzută în UI la 100%)."""
    fp = PROJECT_ROOT / "bench_results" / "folds.csv"
    if not fp.exists():
        return
    # Până la primul flush al rulării CURENTE, folds.csv încă are rezultatele bench-ului
    # ANTERIOR → nu le arăta ca „live".
    if bench_start:
        try:
            if fp.stat().st_mtime < float(bench_start) - 2:
                ui.label("⏳ Se calculează primele rezultate… (clasamentul parțial apare după primul flush).").classes("text-caption text-grey")
                return
        except Exception:  # noqa: BLE001
            pass
    try:
        df = pd.read_csv(fp)
    except Exception:  # noqa: BLE001
        return  # mid-flush / gol → reîncearcă la următorul tick
    if df.empty or "method" not in df.columns or "game" not in df.columns:
        return
    pool = int(SETTINGS.get("pool_size_val", 10))
    _done = progress is not None and float(progress) >= 1.0
    _title = ("🏆 Clasament COMPLET (teste 100% — se scrie decizia/raportul...)" if _done
              else "🏆 Clasament PARȚIAL (live — în timpul bench-ului)")
    with ui.expansion(_title, value=True).classes("w-full"):
        if _done:
            ui.label("✅ Toate testele au rulat. Procesul de bench finalizează decizia "
                     "(best_methods.json) + raportul — câștigătorul final și Auto-Pilot "
                     "pornesc în câteva momente.").classes("text-caption text-positive")
        else:
            ui.label("⏳ Se completează pe măsură ce metodele termină. "
                     "Câștigătorul final + Auto-Pilot se stabilesc abia la sfârșitul bench-ului.").classes("text-caption text-grey")
        ui.label("ℹ️ Walk-forward: istoricul listează +3 și +4; targetul bench/alerte rămâne "
                 f"≥{_bench_target()}.").classes("text-caption text-grey")
        for fk, kp, sect in [("loto_6_49", pool, "6/49"),
                             ("joker_urna1", pool, "Joker Urna 1 (5/45)"),
                             ("loto_5_40", pool, "5/40")]:
            _render_bench_leaderboard_slice(df, fk, kp, sect, top_n=10)


# NOTĂ: `_render_bench_winner_only` a fost ȘTEARSĂ (2026-07). Era cod MORT (zero
# call-site-uri) și rămăsese pe calea VECHE, divergentă: nu filtra `is_random`/`failed`,
# nu excludea baseline-urile (`EXCLUDED_FROM_PRODUCTION`), sorta după media BRUTĂ (nu
# Wilson) și nu citea deloc best_methods.json — deci putea anunța drept „câștigătoare"
# altă metodă decât cea folosită efectiv la generare (și putea pune `random` pe podium).
# Sursa UNICĂ de adevăr pentru „cine e câștigătorul" e `_render_bench_leaderboard_slice`.


def _parse_draw_date(s):
    """Parsează data unei extrageri → date. Acceptă dd-mm-yyyy sau yyyy-mm-dd.
    None dacă nu se poate (ex. eticheta '#index')."""
    raw = str(s).strip()
    # taie partea de oră dacă există (ex. '2025-04-27 00:00:00')
    raw = raw.split(" ")[0].split("T")[0]
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return _dt.strptime(raw, fmt).date()
        except Exception:  # noqa: BLE001
            continue
    return None


def _hit_gap_rows(items: list[tuple], today=None) -> list[dict]:
    """Construiește gap-uri pe secvența de hituri (draw_index), nu pe date unice.

    `items` = [(draw_index, d), ...] deja filtrate, orice ordine.
    Pentru fiecare hit (afișat newest-first):
      - cel mai recent: zile de la acel hit **până AZI** (cât a trecut de atunci)
      - restul: zile de la acel hit **până la hit-ul următor mai recent**
        (intervalul real dintre două hituri consecutive în WF)

    Asta elimină confuzia veche: Δ pe primul rând era „de la hit-ul anterior”,
    deci pe 11-01-2026 apărea „31 zile” (= Dec→Ian), deși de atunci trecuseră ~jumătate de an.
    """
    today = today or _dt.now().date()
    # Cronologic: vechi → nou (după draw_index = ordinea WF)
    chrono = sorted(items, key=lambda kv: kv[0])
    # gap_after[di] = zile de la acest hit până la următorul mai recent (sau azi)
    gap_after: dict[int, int | None] = {}
    for i, (di, d) in enumerate(chrono):
        d_here = _parse_draw_date(d.get("label"))
        if d_here is None:
            gap_after[di] = None
            continue
        if i + 1 < len(chrono):
            d_next = _parse_draw_date(chrono[i + 1][1].get("label"))
            gap_after[di] = (d_next - d_here).days if d_next is not None else None
        else:
            # cel mai recent hit din listă → până azi
            gap_after[di] = (today - d_here).days
    # Afișare: newest first
    rows_out = []
    newest_di = chrono[-1][0] if chrono else None
    for di, d in sorted(items, key=lambda kv: kv[0], reverse=True):
        g = gap_after.get(di)
        if g is None:
            gap_txt = "—"
        elif di == newest_di:
            gap_txt = f"acum {g} zile" if g != 1 else "acum 1 zi"
            if g == 0:
                gap_txt = "azi"
        else:
            gap_txt = f"{g} zile" if g != 1 else "1 zi"
        rows_out.append((di, d, gap_txt))
    return rows_out


def _bench_target() -> int:
    """Pragul de hituri pe care optimizează bench-ul (BENCH_HIT_TARGET, implicit 3).
    Analiza (tabel date ≥N, media între hituri, alerte) urmează ACELAȘI prag ca selecția."""
    try:
        from loto_enterprise.benchmark.decision import BENCH_HIT_TARGET
        return int(BENCH_HIT_TARGET)
    except Exception:  # noqa: BLE001
        return 3


def _curation_banner_info():
    """Starea curării de metode (curated_methods.json) pentru banner-ul de Re-Bench.

    Întoarce None dacă nu e nicio curare activă (fișier absent/gol → bench-ul
    rulează toate metodele available minus blacklist, ca înainte). Curarea e
    complet REVERSIBILĂ (nu e blacklist) — vezi CLAUDE.md.
    """
    try:
        from loto_enterprise.benchmark.curated import apply_curation, curated_path
        from loto_enterprise.benchmark.disabled import load_disabled
        from loto_enterprise.benchmark.methods import list_methods, method_meta

        disabled = load_disabled()
        avail = [m for m in list_methods()
                 if method_meta(m).get("available", True) and m not in disabled]
        _kept, info = apply_curation(avail)
        if not info.get("active"):
            return None
        info["path"] = curated_path().name
        return info
    except Exception:  # noqa: BLE001
        return None


def _target_data_ready() -> bool:
    """True dacă folds.csv conține deja rata pentru pragul curent (≥BENCH_HIT_TARGET).
    Dacă NU (după bump de schemă cache v2→v3 sau schimbare de prag), următorul
    Re-Bench e un recalcul COMPLET (lent), nu rapid din cache — deci banner-ul nu
    trebuie să mintă cu „cache rapid"."""
    try:
        _T = _bench_target()
        f = PROJECT_ROOT / "bench_results" / "folds.csv"
        if not f.exists():
            return False
        cols = [c for c in pd.read_csv(f, nrows=0).columns if c.startswith(f"rate_{_T}plus")]
        if not cols:
            return False
        df = pd.read_csv(f, usecols=cols)
        if df.empty:
            return False
        # fracția rândurilor cu măcar o valoare reală pentru prag (folduri calculate în schema curentă)
        return float(df.notna().any(axis=1).mean()) >= 0.9
    except Exception:  # noqa: BLE001
        return True  # la dubiu, nu speria utilizatorul



def _wf_per_draw_stats(flat) -> dict:
    """Dedup walk-forward pe extragere: pool hits_union.

    `flat` are o intrare per (extragere × variantă); aici păstrăm o singură
    intrare per extragere, fiindcă `hits_union` (câte numere din POOL au ieșit)
    e identic pentru toate variantele aceleiași extrageri."""
    per: dict = {}
    for p in flat:
        di = getattr(p, "draw_index", 0)
        if di not in per:
            dd = getattr(p, "draw_date", getattr(p, "target_draw_date", None))
            per[di] = {"label": str(dd) if dd and str(dd) != "None" else f"#{di}",
                       "pool": int(getattr(p, "hits_union", 0))}
    return per


def _render_hits_4plus(flat, game: str, meta: dict | None = None,
                       flat_p2=None, meta_p2: dict | None = None,
                       pool_n: int | None = None, pool_n_p2: int | None = None) -> None:
    """Istoric hits — SUMAR: POOL 1 (+ POOL 2 la auto-invert).
    `pool_n`/`pool_n_p2` = pool-ul EFECTIV din rezultat (apelant); meta WF (dacă
    are `pool_size`) are prioritate — NU setarea curentă din SETTINGS."""
    if not flat:
        return
    per = _wf_per_draw_stats(flat)
    n = len(per)
    if not n:
        ui.label("Niciun istoric walk-forward.").classes("text-caption text-grey")
        return
    pool3 = sum(1 for d in per.values() if d["pool"] >= 3)
    pool4 = sum(1 for d in per.values() if d["pool"] >= 4)

    per2 = _wf_per_draw_stats(flat_p2) if flat_p2 else {}
    n2 = len(per2)
    pool3_2 = sum(1 for d in per2.values() if d["pool"] >= 3) if per2 else 0
    pool4_2 = sum(1 for d in per2.values() if d["pool"] >= 4) if per2 else 0

    def _cell(k, denom):
        return f"{k} ({k / denom * 100:.0f}%)" if denom else "—"

    ui.label(f"🎯 Istoric hits (din {n} extrageri walk-forward):").classes("text-bold text-caption mt-2")
    for _m, _lbl in ((meta, "Pool"), (meta_p2, "Pool 2")):
        if _m and _m.get("partial"):
            ui.label(f"⚠️ Validare PARȚIALĂ ({_lbl}): {_m.get('n_test_draws')} din "
                     f"{_m.get('n_expected')} extrageri — extragerile CELE MAI RECENTE.").classes(
                "text-warning text-caption text-bold")
        if _m and _m.get("retrospective") and _lbl == "Pool 2":
            ui.label(
                "ℹ️ Pool 2: istoric RETROSPECTIV (pool + wheel de azi pe aceleași "
                f"extrageri ca WF Pool 1) — nu e walk-forward onest; doar informativ."
            ).classes("text-caption text-grey")
    # Memento „întârziat": media intervalului între hituri ≥TARGET pe POOL + cât a trecut.
    _TT = _bench_target()
    st = _due_status(flat)
    if st:
        if st["ratio"] >= 1.0:
            col, lvl = "#fca5a5", "🔴 ÎNTÂRZIAT"
        elif st["ratio"] >= _DUE_WARN_RATIO:
            col, lvl = "#fde68a", "🟡 SE APROPIE"
        else:
            col, lvl = "#86efac", "🟢 recent"
        _avg_d = f"{st['avg']:.0f}"
        _ratio_pct = f"{st['ratio'] * 100:.0f}"
        ui.html(render_html_safe(
            t"<span style='font-size:.85em'>📈 Media între hituri ≥{_TT} (pool): "
            t"<b>{_avg_d}</b> zile · ultimul acum <b>{st['days_since']}</b> zile "
            t"(<b style='color:{col}'>{_ratio_pct}%</b> din interval · "
            t"<span style='color:{col}'>{lvl}</span>)</span>"
        )).classes("mt-1")
    # (1) SUMAR: +3 / +4 pe pool + câștig BRUT estimat din istoricul WF.
    # Pool = wheel-ul COMPLET (Σ premiu pe TOATE variantele câștigătoare).
    gk = _game_label_for(game)
    pm = PRIZE_MAP.get(gk, PRIZE_MAP["6/49"])
    price = PRICES.get(gk, 8.0)
    # Pool-ul REAL (nu string fix „din 16"): meta WF (salvat la rulare) are prioritate,
    # apoi rezultatul pasat de apelant (pool_size / len(hard_core)); 0 = necunoscut.
    _pn1 = int((meta or {}).get("pool_size") or pool_n or 0)
    _pn2 = int((meta_p2 or {}).get("pool_size") or pool_n_p2 or _pn1 or 0)
    pool_gross = sum(pm.get(int(getattr(p, "hits", 0)), 0) for p in flat)        # toate variantele
    # BAZA DE COST = numărul REAL de bilete: o intrare din `flat` = 1 bilet la 1 extragere
    # (același set de obiecte peste care s-a sumat brutul → formula e corectă aritmetic).
    # DE CE diferă bazele celor două pool-uri (verificat pe cache-urile WF de pe disc, nu
    # presupus): Pool 2 replayează exact `draw_index`-ii din Pool 1 (n2 ≤ n, egale în
    # practică), deci diferența vine în esență din BILETE/EXTRAGERE. Cauza reală e că cele
    # două wheel-uri se generează cu PARAMETRI DIFERIȚI:
    #   • Pool 1 = walk-forward, care își impune propriile setări în
    #     `run_honest_walk_forward` (`guarantee=max(4, pool_size//3)`, `max_variants=0`);
    #   • Pool 2 = retrospectiv → replayează wheel-ul de AZI, generat cu garanția și
    #     capul de bilete configurate în UI → exact n2 × len(variante_azi).
    # Variația cover-ului între pașii WF e SECUNDARĂ, nu cauza (măsurat pe cache-urile
    # pool 10: 6/49 → 21 bilete la toate cele 769 extrageri, constant; 5/40 → 52 la 486
    # extrageri și 51 la 29; joker → 52 la 636 și 51 la 14).
    # De aceea afișăm EXPLICIT biletele + costul fiecăruia și ROI (brut/cost), singura
    # metrică adimensională, independentă de câte bilete se joacă.
    n_tick_1 = len(flat)
    cost_1 = n_tick_1 * price
    pool_net = pool_gross - cost_1
    tick_avg_1 = (n_tick_1 / n) if n else 0.0
    roi_1 = (pool_gross / cost_1) if cost_1 else 0.0
    net_draw_1 = (pool_net / n) if n else 0.0

    # Baseline-ul PUR aleator: Pool = K numere (hipergeometric).
    def _bcell(K):
        b3, b4 = _random_rate_hypergeo(gk, K, 3), _random_rate_hypergeo(gk, K, 4)
        if b3 is None or b4 is None:
            return "—"
        return f"{b3 * 100:.1f}% / {b4 * 100:.2f}%"

    def _tick_cell(n_tick, avg, cost):
        return f"{n_tick:,} ({avg:.2f}/extr.) · {cost:,.0f} Lei"

    def _net_cell(net, roi):
        return f"{net:,.0f} Lei (ROI {roi:.2f})"

    rows = [{"src": f"🔥 Pool (din {_pn1})" if _pn1 else "🔥 Pool",
             "p3": _cell(pool3, n), "p4": _cell(pool4, n),
             "rnd": _bcell(_pn1), "tick": _tick_cell(n_tick_1, tick_avg_1, cost_1),
             "win": f"~{pool_gross:,.0f} Lei", "net": _net_cell(pool_net, roi_1)}]
    if per2:
        pool_gross_2 = sum(pm.get(int(getattr(p, "hits", 0)), 0) for p in flat_p2)
        n_tick_2 = len(flat_p2)
        cost_2 = n_tick_2 * price
        pool_net_2 = pool_gross_2 - cost_2
        tick_avg_2 = (n_tick_2 / n2) if n2 else 0.0
        roi_2 = (pool_gross_2 / cost_2) if cost_2 else 0.0
        net_draw_2 = (pool_net_2 / n2) if n2 else 0.0
        rows.append(
            {"src": f"🔄 Pool 2 (din {_pn2}, retrospectiv)" if _pn2 else "🔄 Pool 2 (retrospectiv)",
             "p3": _cell(pool3_2, n2), "p4": _cell(pool4_2, n2),
             "rnd": _bcell(_pn2), "tick": _tick_cell(n_tick_2, tick_avg_2, cost_2),
             "win": f"~{pool_gross_2:,.0f} Lei", "net": _net_cell(pool_net_2, roi_2)},
        )
    ui.table(
        columns=[{"name": "src", "label": "Sursă", "field": "src", "align": "left"},
                 {"name": "p3", "label": "+3 (extrageri)", "field": "p3", "align": "center"},
                 {"name": "p4", "label": "+4 (extrageri)", "field": "p4", "align": "center"},
                 {"name": "rnd", "label": "🎲 random (3+ / 4+)", "field": "rnd", "align": "center"},
                 {"name": "tick", "label": "🎟️ Bilete jucate (cost)", "field": "tick", "align": "center"},
                 {"name": "win", "label": "💰 Câștig brut (WF)", "field": "win", "align": "right"},
                 {"name": "net", "label": "📉 NET (ROI)", "field": "net", "align": "right"}],
        rows=rows,
    ).classes("w-full").props("dense")
    _cap = ("💰 = câștig BRUT din premiile istoricului WF (fără costul biletelor). "
            "🎟️ = biletele efectiv jucate (o intrare walk-forward = 1 bilet la 1 extragere) "
            "și costul lor. NET = brut − cost (de regulă NEGATIV — loteria e aleatoare); "
            "ROI = brut / cost (lei câștigați per leu jucat). "
            "🎲 = baseline PUR aleator (hipergeometric, calculat din parametrii jocului și "
            "mărimea pool-ului) — pragul față de care trebuie citite coloanele +3 / +4. "
            "+3 / +4 = extrageri cu ≥3 / ≥4 nimerite.")
    if per2:
        # NET/extragere NU e o metrică normalizată: NET/extr = (bilete/extragere) × preț ×
        # (ROI − 1), deci scalează liniar cu câte bilete se joacă. E comparabil DOAR când
        # cele două baze de bilete/extragere coincid. Singura mărime adimensională e ROI.
        _same_base = abs(tick_avg_1 - tick_avg_2) <= 0.01 * max(tick_avg_1, tick_avg_2, 1e-9)
        _draws_txt = (f"aceleași {n} extrageri" if n == n2
                      else f"{n} vs {n2} extrageri")
        _cap += (
            f" {'ℹ️' if _same_base else '⚠️'} Bazele de cost "
            f"({_draws_txt}, bilete/extragere {tick_avg_1:.2f} vs {tick_avg_2:.2f}): "
            f"Pool 1 = {n_tick_1:,} bilete (wheel regenerat de walk-forward cu garanția LUI "
            f"internă, fără cap de bilete), Pool 2 = {n_tick_2:,} bilete (retrospectiv, ACELAȘI "
            f"wheel de azi, cu garanția/capul din setări, pe toate extragerile). "
            f"Comparabil direct e ROI ({roi_1:.2f} vs {roi_2:.2f}) — e adimensional. "
        )
        if _same_base:
            _cap += (f"Aici bazele coincid, deci și NET/extragere se poate compara "
                     f"({net_draw_1:,.1f} vs {net_draw_2:,.1f} Lei).")
        else:
            _cap += (f"NET/extragere ({net_draw_1:,.1f} vs {net_draw_2:,.1f} Lei) NU se compară "
                     f"aici: NET/extragere = bilete/extragere × preț × (ROI − 1), deci scalează "
                     f"cu numărul de bilete, nu cu calitatea pool-ului.")
    else:
        _cap += (f" Pool = {n_tick_1:,} bilete ({tick_avg_1:.2f}/extragere) · "
                 f"NET/extragere ≈ {net_draw_1:,.1f} Lei.")
    ui.label(_cap).classes("text-caption text-grey")
    # Onestitate: rata WF observată la ținta bench vs baseline-ul PUR aleator —
    # dacă nu-l bate, spune EXPLICIT (nu lăsa o rată „~10%" să pară edge).
    _tt_checks = [("Pool", sum(1 for d in per.values() if d["pool"] >= _TT), n, _pn1)]
    if per2:
        _tt_checks += [("Pool 2", sum(1 for d in per2.values() if d["pool"] >= _TT), n2, _pn2)]
    _losers = []
    for _lbl3, _cnt, _den, _K in _tt_checks:
        _b = _random_rate_hypergeo(gk, _K, _TT) if _K else None
        if _b is not None and _den and (_cnt / _den) <= _b:
            _losers.append(f"{_lbl3}: {_cnt / _den * 100:.1f}% ≤ random {_b * 100:.1f}%")
    if _losers:
        ui.label(
            f"⚠️ Onestitate (≥{_TT}): " + " · ".join(_losers) +
            " — pe fereastra validată metoda NU a bătut hazardul (diferența e zgomot)."
        ).classes("text-caption text-warning text-bold")

    # (2) DATELE prinderii — listă ≥3 (acoperire); 🔥 marchează targetul bench (≥_TT).
    # Δ pe cel mai recent = „acum X zile” (până azi); pe rest = interval până la hit-ul următor.
    def _pool_badge(d):
        h = int(d["pool"])
        return f"🔥 {h}" if h >= _TT else f"⭐ {h}"

    def _dates_table(title, pred, badge, empty_msg, gap_on=None, gap_label=None):
        items = [(di, d) for di, d in per.items() if pred(d)]
        if not items:
            ui.label(empty_msg).classes("text-caption text-grey mt-2")
            return
        _gp = gap_on or (lambda d: True)
        # Gap-uri doar pe hiturile care ating ținta; restul (dacă apar) fără Δ.
        gap_items = [(di, d) for di, d in items if _gp(d)]
        gap_txt = {di: g for di, _d, g in _hit_gap_rows(gap_items)}
        _gl = gap_label or f"Δ până la următorul ≥{_TT} (sau azi)"
        rows = []
        for di, d in sorted(items, key=lambda kv: kv[0], reverse=True):
            rows.append({
                "draw": d["label"],
                "hits": badge(d),
                "gap": gap_txt.get(di, "—") if _gp(d) else "—",
            })
        ui.label(f"{title} ({len(rows)} extrageri, cele mai recente întâi):").classes("text-bold text-caption mt-3")
        ui.table(
            columns=[{"name": "draw", "label": "Data", "field": "draw", "align": "left"},
                     {"name": "hits", "label": "Nimerite", "field": "hits", "align": "center"},
                     {"name": "gap", "label": _gl, "field": "gap", "align": "center"}],
            rows=rows, pagination=15,
        ).classes("w-full").props("dense")

    # Legendă condiționată de țintă: la _TT==3 orice ≥3 primește 🔥, deci ⭐ nu
    # apare niciodată → mențiunea lui ar fi text mort/contradictoriu.
    _star_leg = ("⭐ = exact 3; " if _TT == 4 else f"⭐ = 3–{_TT - 1}; ") if _TT > 3 else ""
    ui.label(
        f"🗓️ Istoric: extrageri cu ≥3 în pool. 🔥 = target bench (≥{_TT}); "
        f"{_star_leg}Δ pe primul rând = „acum X zile” (de la ultimul hit până azi); "
        f"pe rest = zile până la hit-ul următor mai recent."
    ).classes("text-caption text-grey mt-2")
    _dates_table("🗓️ POOL", lambda d: d["pool"] >= 3, _pool_badge,
                 "Pool-ul n-a prins ≥3 în istoricul walk-forward.",
                 gap_on=lambda d: d["pool"] >= _TT,
                 gap_label=f"Δ → următorul ≥{_TT} / azi")
    if per2:
        def _dates_table_p2(title, pred, badge, empty_msg, gap_on):
            items = [(di, d) for di, d in per2.items() if pred(d)]
            if not items:
                ui.label(empty_msg).classes("text-caption text-grey mt-2")
                return
            gap_items = [(di, d) for di, d in items if gap_on(d)]
            gap_txt = {di: g for di, _d, g in _hit_gap_rows(gap_items)}
            rows = []
            for di, d in sorted(items, key=lambda kv: kv[0], reverse=True):
                rows.append({
                    "draw": d["label"],
                    "hits": badge(d),
                    "gap": gap_txt.get(di, "—") if gap_on(d) else "—",
                })
            ui.label(f"{title} ({len(rows)} extrageri, cele mai recente întâi):").classes(
                "text-bold text-caption mt-3")
            ui.table(
                columns=[{"name": "draw", "label": "Data", "field": "draw", "align": "left"},
                         {"name": "hits", "label": "Nimerite", "field": "hits", "align": "center"},
                         {"name": "gap", "label": f"Δ → următorul ≥{_TT} / azi", "field": "gap", "align": "center"}],
                rows=rows, pagination=15,
            ).classes("w-full").props("dense")

        def _pool_badge_p2(d):
            h = int(d["pool"])
            return f"🔥 {h}" if h >= _TT else f"⭐ {h}"

        _dates_table_p2("🗓️ POOL 2", lambda d: d["pool"] >= 3, _pool_badge_p2,
                        "Pool 2 n-a prins ≥3 în istoricul walk-forward.",
                        lambda d: d["pool"] >= _TT)


# Pragul de la care considerăm că „se apropie media" (% din intervalul mediu).
_DUE_WARN_RATIO = 0.8


def _due_status(flat) -> dict | None:
    """Status 'due' pentru hit-urile ≥BENCH_HIT_TARGET pe POOL ale unui joc.

    Întoarce dict cu: avg (interval mediu zile), last (data ultimului ≥T),
    days_since (zile de la ultimul ≥T până AZI), ratio (days_since/avg), n (nr hituri).
    None dacă nu sunt destule date (<2 hituri ≥T cu dată validă)."""
    _T = _bench_target()
    dates = sorted({
        _parse_draw_date(getattr(p, "draw_date", getattr(p, "target_draw_date", None)))
        for p in flat if int(getattr(p, "hits_union", 0)) >= _T
    } - {None})
    if len(dates) < 2:
        return None
    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    avg = sum(gaps) / len(gaps)
    last = dates[-1]
    days_since = (_dt.now().date() - last).days
    ratio = (days_since / avg) if avg > 0 else 0.0
    return {"avg": avg, "last": last, "days_since": days_since, "ratio": ratio, "n": len(dates)}


def _render_due_alerts(results_bundle, res_prefix: str = "") -> None:
    """Banner + notificare când timpul de la ultimul ≥4 se apropie/depășește media.
    Memento informativ — NU schimbă șansele (loteria e aleatoare)."""
    flat_games = [
        (fname, game, data)
        for fname, outs in results_bundle
        for game, data in outs.items()
    ]
    flat_games.sort(key=lambda t: _GAME_DISPLAY_ORDER.get(_game_label_for(str(t[1])), 99))

    alerts = []
    for fname, game, _data in flat_games:
        flat = STATE["retro"].get(f"{res_prefix}{fname}_{game}")
        if not flat:
            continue
        st = _due_status(flat)
        if st and st["ratio"] >= _DUE_WARN_RATIO:
            _m = STATE.get("retro_meta", {}).get(f"{res_prefix}{fname}_{game}") or {}
            alerts.append((game, st, bool(_m.get("partial")), _m.get("n_test_draws")))

    if not alerts:
        return

    _T = _bench_target()
    with ui.card().classes("w-full").style("background:#3b0764;border:1px solid #f59e0b"):
        ui.html(render_html_safe(t"🔔 <b style='color:#fbbf24;font-size:1.05em'>Alertă — se apropie media de ≥{_T}</b>"))
        for game, st, _partial, _n_part in alerts:
            if st["ratio"] >= 1.0:
                lvl, col = "🔴 ÎNTÂRZIAT", "#fca5a5"
            else:
                lvl, col = "🟡 SE APROPIE", "#fde68a"
            # Validare parțială → media/intervalul vin din PUȚINE extrageri → alerta
            # e orientativă; spunem explicit (altfel „media ~18 zile" din 23 simulări
            # pare la fel de solidă ca una din 1082).
            _pnote = (
                render_html_safe(
                    t" <span style='opacity:.6'>(⚠️ validare PARȚIALĂ — doar {_n_part} "
                    t"extrageri simulate; medie orientativă)</span>"
                )
                if _partial else ""
            )
            _avg_d = f"{st['avg']:.0f}"
            _ratio_pct = f"{st['ratio'] * 100:.0f}"
            ui.html(render_html_safe(
                t"<span style='color:{col}'>{lvl}</span> — <b>{game.upper()}</b>: "
                t"au trecut <b>{st['days_since']}</b> zile de la ultimul ≥{_T} "
                t"({st['last'].strftime('%d-%m-%Y')}); media e ~<b>{_avg_d}</b> zile "
                t"(<b>{_ratio_pct}%</b> din interval).{_pnote}"
            ))
        ui.html(render_html_safe(
            t"<span style='opacity:.6;font-size:.8em'>⚠️ Loteria e aleatoare — "
            t"„întârzierea\" NU crește șansele. E doar un memento de acoperire, "
            t"nu o predicție.</span>"
        ))

    # Notificare transientă (pop-up) la randarea rezultatelor.
    try:
        names = ", ".join(a[0].upper() for a in alerts)
        ui.notify(f"🔔 Se apropie media de ≥{_T}: {names} — vezi alerta de sus.",
                  type="warning", position="top", timeout=10000, close_button=True)
    except Exception:  # noqa: BLE001
        pass


def _render_analysis_menu(results_bundle, res_prefix: str = "") -> None:
    """Meniu global: metoda câștigătoare + istoric ≥4 hits per joc. Închis implicit."""
    has_folds = (PROJECT_ROOT / "bench_results" / "folds.csv").exists()
    has_wf = any(
        STATE["retro"].get(f"{res_prefix}{fn}_{g}")
        for fn, outs in results_bundle for g, _ in outs.items()
    )
    if not (has_folds or has_wf):
        return

    with ui.card().classes("w-full"):
        with ui.expansion("📊 Analiză & Clasament", value=False).classes("w-full"):
            # Aplatizăm toate jocurile din toate fișierele și le sortăm GLOBAL: 6/49, Joker, 5/40.
            flat_games = [
                (fname, game, data)
                for fname, outs in results_bundle
                for game, data in outs.items()
            ]
            flat_games.sort(
                key=lambda t: _GAME_DISPLAY_ORDER.get(_game_label_for(str(t[1])), 99)
            )
            for fname, game, data in flat_games:
                ui.separator().classes("my-3")
                ui.label(f"🎯 {game.upper()}").classes("text-bold text-lg")

                # Reper: ultima extragere reală din CSV (deasupra clasamentului).
                _render_last_csv_draw(fname)

                # --- Top-10 metode ---
                _render_bench_leaderboard(game)

                # --- Istoric ≥4 hits — PLIABIL (în cadrul clasamentului, îl poți ascunde) ---
                main = data.get("phase1") if data.get("phase1") else data
                _pn_main = int(main.get("pool_size") or len(main.get("hard_core") or []))
                flat = STATE["retro"].get(f"{res_prefix}{fname}_{game}")
                if flat:
                    # Deschis implicit (apare după ce termină walk-forward), dar pliabil
                    # → îl poți ascunde dacă vrei. Apare DOAR după WF (vine din STATE["retro"]).
                    with ui.expansion("📜 Istoric hits (walk-forward) — click pentru ascunde",
                                      value=True).classes("w-full"):
                        _render_hits_4plus(
                            flat, game,
                            meta=STATE.get("retro_meta", {}).get(f"{res_prefix}{fname}_{game}"),
                            pool_n=_pn_main,
                        )


def _render_results_bundle(results_bundle, res_prefix: str = "") -> None:
    # 0) Alertă „se apropie media de ≥4" — sus de tot, vizibilă imediat.
    _render_due_alerts(results_bundle, res_prefix)

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
            for game, data in _ordered_game_items(outs):
                with ui.expansion(f"🎯 {game.upper()}", value=True).classes("w-full"):
                    # Un singur pool. La rezultate legacy cu auto_invert, phase1 = pool-ul normal.
                    pool = data["phase1"] if data.get("phase1") else data
                    _render_pool_body(fname, game, pool, with_wf=False, res_prefix=res_prefix)



def _render_matrix_html(matrix) -> None:
    """Heatmap HTML pentru o matrice (metode × ferestre %), verde = valoare mare.

    Robust la NaN: celulele fără date (metodă neevaluată la o fereastră) se
    afișează ca „—" în loc să crape randarea (`int(NaN)` arunca „cannot convert
    float NaN to integer" → toată matricea devenea indisponibilă).
    """
    import math
    # vmin/vmax DOAR pe valorile finite (ignoră NaN/inf).
    finite_vals = []
    for _m, row in matrix.iterrows():
        for c in matrix.columns:
            try:
                fv = float(row[c])
            except Exception:  # noqa: BLE001
                continue
            if math.isfinite(fv):
                finite_vals.append(fv)
    if not finite_vals:
        ui.label("(matrice goală — nicio fereastră cu date pentru aceste metode)").classes(
            "text-caption text-grey")
        return
    vmin, vmax = min(finite_vals), max(finite_vals)
    span = (vmax - vmin) or 1.0
    cols = list(matrix.columns)
    head = "".join(
        render_html_safe(t"<th style='padding:2px 6px;font-size:0.75em;'>{c}%</th>") for c in cols
    )
    body = ""
    for method, row in matrix.iterrows():
        cells = ""
        for c in cols:
            try:
                v = float(row[c])
            except Exception:  # noqa: BLE001
                v = float("nan")
            if not math.isfinite(v):
                cells += (
                    "<td style='padding:2px 6px;background:#2a2a2a;color:#666;"
                    "font-size:0.78em;text-align:center;'>—</td>"
                )
                continue
            frac = (v - vmin) / span  # 0..1
            frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
            r = int(220 - 140 * frac)
            g = int(80 + 140 * frac)
            cells += render_html_safe(
                t"<td style='padding:2px 6px;background:rgb({r},{g},80);color:#111;"
                t"font-size:0.78em;text-align:center;'>{v:.3f}</td>"
            )
        body += (
            render_html_safe(
                t"<tr><td style='padding:2px 6px;font-weight:600;font-size:0.78em;'>{method}</td>"
            )
            + cells
            + render_html_safe(t"</tr>")
        )
    ui.html(
        render_html_safe(t"<table style='border-collapse:collapse;'><tr><th></th>")
        + head
        + render_html_safe(t"</tr>")
        + body
        + render_html_safe(t"</table>")
    )


def _new_draws_summary():
    """Câte extrageri noi s-au adăugat de la ultimul bench (per joc + total), din
    semnăturile CSV stampilate în best_methods.json de modulul `freshness`
    (`write_signatures_to_best_methods`, apelat la finalul fiecărui bench).

    Întoarce None dacă freshness e indisponibil. `any_bench` = există măcar o
    semnătură de la un bench anterior (altfel primul bench e oricum complet)."""
    try:
        from loto_enterprise.benchmark.freshness import check_freshness, aggregate_recommendation
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
def _clean_stale_adaptive(stale_keys) -> None:
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
        ui.label("Fără istoric adaptiv încă (se creează după prima generare cu feedback).").classes("text-caption")
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
            if int(str(k).split("_")[-1]) not in SUPPORTED_POOLS:
                stale.append(k)
        except (ValueError, IndexError):
            pass

    ui.label("Stare persistentă Adaptive Feedback v2 — telemetrie evenimente "
             "(catastrofă/underperf/normal), regime resets, hard inversions.").classes("text-caption")
    if stale:
        with ui.row().classes("items-center gap-3"):
            ui.label(f"⚠️ {len(stale)} configurări STALE (pool inaccesibil 6-16): {', '.join(stale)}").classes("text-warning text-caption")
            ui.button("🗑️ Curăță stale", on_click=lambda s=stale: _clean_stale_adaptive(s)).props("flat dense color=negative")

    icons = {"catastrophe": "🔥", "underperf": "⚠️", "normal": "✅", "regime_reset": "🚨"}
    for key in sorted(raw):
        entry = raw[key] or {}
        hist = entry.get("history", []) or []
        rs = entry.get("regime_state", {}) or {}
        ecmap = entry.get("error_correction_map", {}) or {}
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
                cat_txt = f"{n_cat} ({n_cat/n*100:.0f}%)" if n else "0"
                for lbl, val in [("Total extrageri", n), ("Mean hits", f"{mean_h:.2f}"),
                                 ("Best", max_h), ("Catastrofe", cat_txt), ("Streak zero", streak)]:
                    with ui.column().classes("items-center gap-0"):
                        ui.label(lbl).classes("text-caption")
                        ui.label(str(val)).classes("text-subtitle1")
            if entry.get("last_pool_date"):
                ui.label(f"Ultima predicție: {entry['last_pool_date']}").classes("text-caption")
            if ecmap:
                boosts = sorted(((int(k2), float(v)) for k2, v in ecmap.items()), key=lambda x: x[1], reverse=True)
                tb = [f"{nn}×{m:.2f}" for nn, m in boosts[:5] if m > 1.0]
                tp = [f"{nn}×{m:.2f}" for nn, m in boosts[-5:] if m < 1.0]
                if tb:
                    ui.label("↑ Top boost: " + ", ".join(tb)).classes("text-caption text-positive")
                if tp:
                    ui.label("↓ Top penalizare: " + ", ".join(tp)).classes("text-caption text-negative")
            if hits:
                ui.echart({
                    "tooltip": {"trigger": "axis"},
                    "xAxis": {"type": "category", "data": list(range(1, len(hits) + 1))},
                    "yAxis": {"type": "value"},
                    "series": [{"type": "line", "data": hits, "smooth": True, "areaStyle": {}}],
                    "grid": {"left": 30, "right": 10, "top": 10, "bottom": 20},
                }).classes("w-full").style("height:140px")
                recent = hist[-min(15, len(hist)):]
                seq = " ".join(f"{icons.get(str(h.get('event','?')), '•')}{int(h.get('pool_hits',0) or 0)}" for h in recent)
                ui.label(f"Ultimele {len(recent)}: {seq}").classes("text-caption")

    total_learned = sum(len(e.get("history", []) or []) for e in raw.values())
    n_reset = sum(1 for e in raw.values() if (e.get("regime_state") or {}).get("active_mode") == "reset")
    ui.label(f"📈 Global: {total_learned} extrageri învățate · {n_reset} configurări în mod RESET.").classes("text-caption text-bold")


def _refresh_status() -> None:
    status_panel.refresh()
    logs_panel.refresh()


# --------------------------------------------------------------------------- #
# Pagina principală
# --------------------------------------------------------------------------- #
@ui.page("/")
def main_page() -> None:
    ui.dark_mode().enable()

    # Chevron-ul expansion-urilor: vârful în JOS când e DESCHIS (arată spre conținut),
    # în sus când e închis — invers față de implicitul Quasar. Inversăm rotația global
    # pentru toate expansion-urile (q-expansion-item) printr-o singură regulă.
    ui.add_css("""
        .q-expansion-item__toggle-icon { transform: rotate(180deg) !important; }
        .q-expansion-item__toggle-icon--rotated { transform: rotate(0deg) !important; }
    """)

    with ui.header().classes("items-center justify-between"):
        ui.label("🎰 Loto Enterprise Wheeling").classes("text-h5")
        ui.label("NiceGUI — stare persistentă, fără reload").classes("text-caption")

    # ---- Sidebar (drawer stânga) ----
    with ui.left_drawer(fixed=False).props("width=360 bordered").classes("p-3"):
        ui.label("1. Încărcare Date CSV").classes("text-bold")

        async def _on_upload(e) -> None:
            # NiceGUI 3.12: e.file.read() e async. Încărcare DOAR manuală — nu
            # persistăm/auto-restaurăm nimic; ce alegi tu intră în sesiune.
            try:
                content = await e.file.read()
                name = e.file.name
                df = pd.read_csv(io.BytesIO(content))
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Nu pot citi fișierul: {exc}", type="negative")
                return
            STATE["datasets"] = [(f, d) for f, d in STATE["datasets"] if f != name] + [(name, df)]
            ui.notify(f"Încărcat {name} ({len(df)} extrageri).", type="positive")
            datasets_label.refresh()

        ui.upload(on_upload=_on_upload, multiple=True, auto_upload=True).props('accept=.csv').classes("w-full")

        @ui.refreshable
        def datasets_label() -> None:
            if STATE["datasets"]:
                ui.label("Încărcate: " + ", ".join(fn for fn, _ in STATE["datasets"])).classes("text-caption text-positive")
                with ui.expansion("📅 Istoric CSV", value=False).classes("w-full"):
                    for fn, df in STATE["datasets"]:
                        ui.label(f"{fn}: {len(df)} extrageri × {len(df.columns)} coloane").classes("text-caption")
            else:
                ui.label("Niciun CSV încărcat.").classes("text-caption text-warning")
        datasets_label()

        ui.separator()
        ui.label("2. Setări Algoritm").classes("text-bold")

        def _bind_save(widget, key):
            widget.bind_value(SETTINGS, key)
            widget.on_value_change(lambda: _save_settings())
            return widget

        _bind_save(ui.number("Dimensiune Pool (Nucleu Dur)", min=6, max=16, step=1).classes("w-full"), "pool_size_val")
        _bind_save(ui.number("Garanție minimă (Set Cover)", min=3, max=5, step=1).classes("w-full"), "guarantee_val")
        _bind_save(ui.number("Limită maximă variante (0=nelimitat)", min=0, max=10000, step=10).classes("w-full"), "max_variants_val")
        _bind_save(ui.number("Analizează doar ultimele X% extrageri", min=0, max=100, step=5).classes("w-full"), "lookback_val")
        _bind_save(ui.number("Adâncime Simulare Backtesting (%)", min=10, max=100, step=10).classes("w-full"), "sim_depth_val")
        _bind_save(ui.number("⏱ Buget walk-forward (minute)", min=1, max=480, step=5).classes("w-full"), "wf_budget_min")

        def _on_target_change(e):
            SETTINGS["bench_hit_target"] = int(e.value)
            _save_settings()
            try:
                import loto_enterprise.benchmark.decision as decision
                target = int(e.value)
                decision.BENCH_HIT_TARGET = target
                os.environ["LOTO_BENCH_TARGET"] = str(target)
                if (PROJECT_ROOT / "bench_results" / "folds.csv").exists():
                    decision.update_best_methods_with_auto_pilot()
                    ui.notify(f"Decizia Auto-Pilot a fost actualizată pentru {target}+ hits!", type="info")
                    _refresh_status()
                    results_panel.refresh()
            except Exception as exc:
                logger.warning("Eroare la schimbarea țintei de hituri: %s", exc)

        ui.select(
            {3: "3+ Hits", 4: "4+ Hits"},
            value=int(SETTINGS.get("bench_hit_target", 3)),
            label="🎯 Țintă Optimizare / Bench",
            on_change=_on_target_change,
        ).classes("w-full")
        ui.label(f"Validarea (pe ultimele {int(WF_DEPTH_PERCENT)}% din istoric): "
                 "Joker → 5/40 → 6/49 (6/49 ultim). "
                 "WF paralel (~80% CPU) — de obicei minute, nu ore. Bugetul e plafon de siguranță."
                 ).classes("text-caption text-grey")
        _bind_save(ui.checkbox("🔌 Oprește PC-ul automat la final"), "shutdown_on_complete")
        _bind_save(ui.checkbox("📧 Trimite rezultatele pe mail la final"), "mail_on_complete")
        ui.button("📧 Trimite mail de test", on_click=_send_test_email).props("outline no-caps size=sm").classes("text-caption")

        ui.separator()
        ui.label("3. Control Execuție").classes("text-bold")
        _BTN = "w-full"
        _BTN_STYLE = "white-space:normal;line-height:1.2;min-height:40px"
        ui.button("⚡ Auto-Pilot (decizie bench + generează)", on_click=apply_autopilot_and_generate
                  ).props("color=primary no-caps").classes(_BTN).style(_BTN_STYLE)
        ui.button("🚀 Generează (setări manuale)", on_click=lambda: submit_generation(pure=False)
                  ).props("no-caps").classes(_BTN).style(_BTN_STYLE)

        ui.separator()
        _full_eta = _estimate_bench_eta(1280)
        ui.button("🔬 RE-BENCH", on_click=run_rebench
                  ).props("color=orange no-caps").classes(_BTN).style(_BTN_STYLE)
        try:
            from loto_enterprise.benchmark.decision import BENCH_HIT_TARGET as _bt
        except Exception:  # noqa: BLE001
            _bt = 3
        ui.label("Un singur bench testează metodele active (exclusiv CPU), pe toate nucleele "
                 "(în paralel). Toate concurează în "
                 f"ACELAȘI clasament → UN câștigător (regula {_bt}+) → UN Auto-Pilot → UN walk-forward. "
                 "Vezi clasamentul complet la 🏆 Clasament bench.").classes("text-caption")
        # Curare REVERSIBILĂ a setului de metode (curated_methods.json). Dacă e
        # activă, bench-ul rulează un SUBSET — spunem clar câte și cum se anulează.
        _cur = _curation_banner_info()
        if _cur is not None:
            ui.html(render_html_safe(
                t"🎯 <b>Curare activă: {_cur['n_after']} metode din {_cur['n_before']}</b> "
                t"(criteriu: acoperire de semnal, nu clasament)."
            )).classes("text-caption text-info")
            ui.label("Dezactivare (revine la toate metodele): șterge sau golește lista "
                     f"'active' din {_cur['path']}, apoi rulează un Re-Bench. "
                     "Nimic nu se pierde — nu e blacklist.").classes("text-caption text-grey")
            if _cur["missing_required"]:
                ui.label("⚠️ Lipsesc din curare metode structurale "
                         f"({', '.join(_cur['missing_required'])}) — decizia bench poate "
                         "cădea pe low_confidence. Adaugă-le în curated_methods.json."
                         ).classes("text-caption text-negative")
        # Gard anti-surpriză: extrageri noi de la ultimul bench + avertisment că datele
        # noi invalidează cache-ul (re-bench = recalcul complet). Snapshot la randarea
        # paginii (se reîmprospătează la reload). Vezi _new_draws_summary / freshness.
        _fresh = _new_draws_summary()
        if _fresh is not None and _fresh["any_bench"]:
            if _fresh["total"] > 0:
                _g2l = {v: k for k, v in GK_MATRIX.items()}
                _parts = ", ".join(f"{_g2l.get(gk, gk)} +{d}" for gk, d in _fresh["per"].items())
                _col = "text-negative" if _fresh["rec"] == "full_rebench" else "text-warning"
                _fresh_total = _fresh["total"]
                ui.html(render_html_safe(
                    t"🆕 <b>+{_fresh_total} extrageri noi</b> de la ultimul bench ({_parts})."
                )).classes("text-caption " + _col)
                ui.label("⚠️ Datele noi invalidează cache-ul → Re-Bench = recalcul COMPLET (nu rapid). "
                         "Pentru generarea zilnică NU e nevoie de re-bench: Auto-Pilot folosește deja "
                         "datele noi, iar câștigătorul bench abia se schimbă la câteva extrageri.").classes("text-caption " + _col)
            elif _fresh["rec"] in ("quick_rebench", "full_rebench"):
                ui.label("⚠️ Datele s-au schimbat de la ultimul bench → Re-Bench recalculează complet (fără cache).").classes("text-caption text-warning")
            elif _target_data_ready():
                ui.label("✅ Date neschimbate de la ultimul bench → Re-Bench folosește cache-ul (rapid).").classes("text-caption text-positive")
            else:
                ui.label(f"⚠️ Următorul Re-Bench va fi COMPLET (~lent, nu din cache): datele pentru pragul "
                         f"curent (≥{_bench_target()}) nu-s încă în cache (schemă nouă / prag schimbat). "
                         "O singură dată — apoi redevine rapid.").classes("text-caption text-warning")
        _bind_save(ui.checkbox("⚡ Pornește Auto-Pilot automat după Re-Bench"), "autopilot_after_bench")

        ui.separator()
        ui.button("🔴 Anulează TOT Procesul", on_click=cancel_all).props("color=negative outline no-caps").classes("w-full").style(_BTN_STYLE)
        ui.button("🗑️ Șterge Log", on_click=lambda: (clear_logs(), logs_panel.refresh())).props("outline no-caps").classes("w-full").style(_BTN_STYLE)

    # ---- Zona principală ----
    with ui.column().classes("w-full p-4 gap-2"):
        status_panel()
        with ui.expansion("🛠 Consolă DEBUG / Loguri (live)", value=False).classes("w-full"):
            logs_panel()
        results_panel()

    # ---- Polling fără reload. Munca BLOCANTĂ (citiri loguri OneDrive, psutil, pid-uri)
    # rulează în io_bound (thread), ca event-loop-ul UI să NU se blocheze → fără
    # 'connection lost'. Doar refresh-ul UI (rapid, din cache STATE) e pe loop. ----
    async def _tick() -> None:
        from nicegui import run as _nrun

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

        _active = bool(STATE.get("active_job_id") or bench_now or STATE.get("wf_status"))
        # Re-Bench terminat → Auto-Pilot automat
        if STATE.get("bench_was_running") and not bench_now:
            STATE["bench_was_running"] = False
            if not STATE.get("bench_cancelled"):
                _on_bench_finished()
        elif bench_now:
            STATE["bench_was_running"] = True
            STATE["bench_cancelled"] = False
        # Refresh UI (rapid, din STATE) — doar când e activ
        if _active:
            logs_panel.refresh()
            status_panel.refresh()
        if STATE.get("wf_status"):
            # DOAR progresul WF — NU tot bundle-ul, ca expansion-urile deschise
            # (🏆 Clasament bench etc.) să NU se închidă la fiecare poll de 2s.
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
        now_utc = _dt.now(_tz.utc).replace(tzinfo=None)  # naiv UTC (fără deprecation utcnow)
        delta = (now_utc - t).total_seconds()
        if delta < -120:
            # Ceas dat înapoi (corecție NTP, resume VM, baterie BIOS) → suspect, NU proaspăt.
            logger.warning("[RECOVERY] completed_at în viitor cu %.0fs (ceas?) → tratez ca vechi.", -delta)
            return None
        return max(0.0, delta)  # micile negative (sub-secundă) → 0
    except Exception:  # noqa: BLE001
        return None


def _recover_completed_job() -> None:
    """get_active_job() vede DOAR PENDING/RUNNING. Dacă worker-ul a terminat un job
    cât UI-ul era complet jos, rezultatul (+ mail/shutdown de la final) ar rămâne
    orfan. Îl readucem în flux O SINGURĂ DATĂ:
      • PROASPĂT (în fereastră)  → flux COMPLET prin status_panel: afișare +
        walk-forward + mail + shutdown (ca o finalizare normală pe care UI-ul a ratat-o);
      • VECHI / fără completed_at → DOAR afișare (fără shutdown-surpriză, fără mail vechi).
    `last_finalized_job_id` (persistat) împiedică re-procesarea la următoarea repornire."""
    last = get_latest_completed_job()
    if not last:
        return
    jid = int(last["id"])
    try:
        already = int(SETTINGS.get("last_finalized_job_id") or 0)
    except (TypeError, ValueError):
        already = 0
    if jid == already:
        return  # deja dus prin finalize într-o sesiune anterioară

    payload = decode_queue_result(str(last.get("result_json") or "{}"))
    if not (isinstance(payload, tuple) and len(payload) == 2):
        # payload gol/invalid (ex. cancel-race) → marcăm văzut, nu reîncercăm la infinit
        SETTINGS["last_finalized_job_id"] = jid
        _save_settings()
        return

    age = _completed_age_seconds(last)
    if age is not None and age <= RECOVERY_FINALIZE_WINDOW_S:
        # Proaspăt → status_panel îl preia exact ca pe o finalizare normală (decode +
        # STATE["results"] + walk-forward + mail + shutdown) și setează last_finalized.
        # NU pornim worker-ul (jobul e gata).
        STATE["active_job_id"] = jid
        logger.warning("[RECOVERY] job #%s terminat acum %ss (în fereastră) → "
                       "finalizez complet (mail/shutdown).", jid, int(age))
    else:
        # Vechi sau fără completed_at → doar afișăm numerele, fără mail/shutdown.
        # Marcăm CLAR că-s dintr-o sesiune anterioară (la loto, a juca numere vechi
        # crezându-le curente e o eroare reală) — afișat ca avertisment în status_panel.
        when = str(last.get("completed_at") or "")[:16] or "sesiune anterioară"
        with STATE_LOCK:
            STATE["results"] = payload
            STATE["results_recovered"] = f"job #{jid} · {when}"
        SETTINGS["last_finalized_job_id"] = jid
        _save_settings()
        try:
            _save_report_file()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RECOVERY] raport: %s", exc)
        logger.warning("[RECOVERY] job #%s prea vechi (%s) → doar afișez, fără mail/shutdown.",
                       jid, "necunoscut" if age is None else f"{int(age)}s")


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _startup() -> None:
    init_job_queue()
    # NU marcăm joburile RUNNING ca eșuate: worker.py e proces separat care
    # supraviețuiește repornirii UI-ului → un job viu trebuie re-atașat, nu omorât.
    _load_settings()
    # NU auto-încărcăm CSV-uri: utilizatorul încarcă manual de fiecare dată.
    # Re-atașare la un job activ (dacă UI-ul a fost repornit cât rula worker-ul)
    try:
        active = get_active_job()
        if active:
            STATE["active_job_id"] = int(active["id"])
            # Job orfan (worker poate fi mort) → pornim worker-ul ca să-l reia
            # (requeue_running_jobs la startup worker repune RUNNING→PENDING).
            ensure_worker_running()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_job startup: %s", exc)
    # Job terminat cât UI-ul era COMPLET jos (get_active_job vede doar PENDING/RUNNING)
    # → altfel rezultatul + mail/shutdown rămân orfane. Doar dacă nu avem deja unul activ.
    try:
        if not STATE.get("active_job_id"):
            _recover_completed_job()
    except Exception as exc:  # noqa: BLE001
        logger.warning("recover completed job startup: %s", exc)


app.on_startup(_startup)

if __name__ in {"__main__", "__mp_main__"}:
    _port = int(os.environ.get("LOTO_UI_PORT", "8080"))
    # show=False: browserul e deschis de START_8000.bat (mai fiabil pe Windows).
    # reconnect_timeout mărit: cât rulează bench/walk-forward, event-loop-ul poate fi
    # ocupat (citiri loguri OneDrive) → fără timeout generos, WebSocket pica 'connection lost'.
    ui.run(title="Loto Enterprise Wheeling", port=_port, reload=False, show=False, dark=True,
           reconnect_timeout=60.0)
