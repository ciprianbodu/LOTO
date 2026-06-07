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
from pathlib import Path

import pandas as pd
from nicegui import app, ui

from job_queue import (
    cancel_pending_running_jobs,
    get_active_job,
    get_job_status,
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
    read_logs_filtered,
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

UI_PERSIST_KEYS = [
    "pool_size_val", "guarantee_val", "max_variants_val", "lookback_val",
    "consecutive_filter_val", "auto_invert_val", "shutdown_on_complete",
    "sim_depth_val", "autopilot_after_bench",
]
DEFAULTS = {
    "pool_size_val": 10, "guarantee_val": 4, "max_variants_val": 0,
    "lookback_val": 0, "consecutive_filter_val": True, "auto_invert_val": False,
    "shutdown_on_complete": False, "sim_depth_val": 40, "autopilot_after_bench": True,
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
    "results": None,         # (results_bundle, count)
    "retro": {},             # {f"{fname}_{game}": flat_walk_forward}
    "wf_status": "",         # text status walk-forward
    "wf_progress": 0.0,      # fracție 0..1 progres walk-forward (bară)
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


def _ordered_game_items(outs):
    """Items din `outs` ordonate pentru afișare: 6/49, Joker, 5/40."""
    return sorted(
        outs.items(),
        key=lambda kv: _GAME_DISPLAY_ORDER.get(_game_label_for(str(kv[0])), 99),
    )


# --------------------------------------------------------------------------- #
# Submit job (contract config_json identic cu app.py)
# --------------------------------------------------------------------------- #
def _build_config_json(sim_depth_per_game: dict | None = None) -> str:
    sim_depth_per_game = sim_depth_per_game or {}
    h = hashlib.sha256()
    for k in ("pool_size_val", "guarantee_val", "max_variants_val", "lookback_val",
              "consecutive_filter_val", "auto_invert_val", "sim_depth_val"):
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
            "filter_consecutives": False if pure else bool(SETTINGS["consecutive_filter_val"]),
            "smart_reduction": False if pure else True,
            "sim_depth_pct": sd,
            "pure_bench_mode": pure,
            "auto_invert": bool(SETTINGS["auto_invert_val"]),
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
    STATE["_omnius_cache"] = {}  # OMNIUS se recalculează pt noile pool-uri
    STATE["wf_status"] = ""
    ensure_worker_running()
    lock_engine("deterministic_session")
    cfg = _build_config_json(sim_depth_per_game)
    job_id = submit_job("pipeline", cfg)
    STATE["active_job_id"] = int(job_id)
    STATE["job_start_time"] = time.time()
    STATE["job_elapsed"] = None  # reset; se fixează la COMPLETED
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
    try:
        # bench_all_methods.py își scrie SINGUR bench_full.log (FileHandler) → nu
        # mai redirectăm stdout aici (altfel doi writeri pe același fișier). Logul
        # există acum și pe Windows, vizibil în consola DEBUG.
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), creationflags=flags)
        BENCH_PID_FILE.write_text(f"{proc.pid}|{int(time.time())}", encoding="utf-8")
        ui.notify(f"{label} pornit (PID {proc.pid}).", type="positive")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Nu pot porni bench-ul: {exc}", type="negative")
    _refresh_status()


_PCTS = "10,30,60,100"  # 4 ferestre: 10% (zona unde 4+ a ieșit cel mai sus în măsurători)
# + 30/60/100 (scurt-mediu-lung). NOTĂ: 10% e cea mai SCUMPĂ (antrenare pe ~90%% din istoric
# → rețelele grele fac 25-30 min/fold); 100% e cea mai ieftină. Tunabil aici.


def run_full_rebench() -> None:
    _launch_bench(["--no-rich", "--percentiles", _PCTS], "FULL Re-Bench")

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
    paralelizează metodele CPU pe toate nucleele (ProcessPool) și rulează cele GPU
    secvențial — deci CPU(multi-nuclee) ‖ GPU în același bench, fără 2 procese/secțiuni."""
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
    # un singur bench, fără --methods (= TOATE), scrie best_methods.json (decizie 4+)
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
    Text = '% testat (N din M) · acum: joc/metodă · rămas ~X'."""
    if not log_path.exists():
        return None
    cur = tot = 0
    cpu_tot = gpu_tot = 0
    gpu_paused = False
    matches = []
    try:
        import re
        txt = log_path.read_text(encoding="utf-8", errors="replace")
        matches = re.findall(r"\[(\d+)/(\d+)\]\s*\[([^\]]+)\]", txt)
        if matches:
            cur, tot = int(matches[-1][0]), int(matches[-1][1])
        # marker scris de runner: [BENCH-SPLIT] cpu=N gpu=M total=T
        _sp = re.findall(r"\[BENCH-SPLIT\]\s*cpu=(\d+)\s*gpu=(\d+)", txt)
        if _sp:
            cpu_tot, gpu_tot = int(_sp[-1][0]), int(_sp[-1][1])
        # runner semnalează că nu există GPU → track-ul GPU e sărit (PAUSED).
        gpu_paused = "[BENCH-GPU-PAUSED]" in txt
    except Exception:  # noqa: BLE001
        pass
    if tot <= 0:
        return 0.03, "pornește... (estimez după primele teste)"
    frac = max(0.0, min(1.0, cur / tot))
    # Progres SEPARAT pe CPU și GPU (rulează concurent). Eticheta CPU/GPU vine AUTORITAR
    # din linia de log (seg[4] = CPU|GPU, scris de runner) — consistent cu totalurile din
    # [BENCH-SPLIT]. Fallback pe euristica de nume doar pt loguri vechi (fără tag).
    cpu_done = gpu_done = 0
    last_cpu = last_gpu = ""
    try:
        for _m in matches:
            seg = _m[2].split("/")
            if len(seg) >= 3:
                entry = f"{seg[0]} / {seg[1]} / {seg[2]} backtest"
                if len(seg) >= 5 and seg[4] in ("CPU", "GPU"):
                    is_gpu_line = (seg[4] == "GPU")
                else:
                    is_gpu_line = _method_is_gpu(seg[1])  # fallback log vechi
                if is_gpu_line:
                    gpu_done += 1
                    last_gpu = entry
                else:
                    cpu_done += 1
                    last_cpu = entry
    except Exception:  # noqa: BLE001
        pass

    elapsed = max(0.0, time.time() - start_ts) if start_ts else 0.0

    def _eta(done, total):
        if elapsed > 0 and done > 0 and total > done:
            return (total - done) * (elapsed / done)
        return None

    cpu_eta = _eta(cpu_done, cpu_tot)
    gpu_eta = _eta(gpu_done, gpu_tot)

    def _cat_line(emoji, color, label, done, total, eta, now_txt):
        parts = []
        if total > 0:
            pc = int(max(0.0, min(1.0, done / total)) * 100)
            parts.append(f"{pc}% ({done}/{total})")
        else:
            parts.append(f"{done} teste")
        if eta is not None:
            parts.append(f"rămas ~{_fmt_dur(eta)}")
        elif total > 0 and done >= total:
            parts.append("✅ gata")
        head = (f"<span style='color:{color}'>{emoji} {label}:</span> " + " · ".join(parts))
        return head + (f"<br><span style='opacity:.7'>&nbsp;&nbsp;&nbsp;&nbsp;acum: {now_txt}</span>" if now_txt else "")

    # ETA GLOBAL = MAXIMUL dintre CPU și GPU: rulează CONCURENT, deci bench-ul se termină
    # când termină cel mai LENT (de obicei GPU). Media/blend dădea un ETA fals de optimist
    # (CPU termină multe task-uri instant → rata părea uriașă → „~17s" deși GPU avea 14m).
    text = f"{int(frac*100)}% ({cur}/{tot} teste)"
    _etas = [e for e in (cpu_eta, gpu_eta) if e is not None]
    if _etas:
        text += f"  ·  rămas ~{_fmt_dur(max(_etas))} (cât cel mai lent track)"
    lines = [text]
    lines.append(_cat_line("🖥️", "#38bdf8", "CPU", cpu_done, cpu_tot, cpu_eta, last_cpu))
    if gpu_paused:
        lines.append("<span style='color:#c084fc'>⚡ GPU:</span> "
                     "<b style='color:#f59e0b'>⏸ PAUSED — fără GPU</b> "
                     "<span style='opacity:.7'>(CUDA indisponibil — metodele GPU sărite, fără fallback CPU)</span>")
    else:
        lines.append(_cat_line("⚡", "#c084fc", "GPU", gpu_done, gpu_tot, gpu_eta, last_gpu))
    return frac, "<br>".join(lines)


_HW_CACHE = {"html": "", "ts": 0.0, "running": False}


def _hw_telemetry_refresh() -> None:
    """Citește CPU/RAM/GPU/VRAM ÎN FUNDAL (thread) și cache-uiește HTML-ul. Apelat de un
    thread separat — NU pe event-loop-ul UI (nvidia-smi/psutil sunt blocante → ar pica
    WebSocket-ul 'connection lost')."""
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
    gpu = vram = ""
    try:
        import subprocess as _sp
        out = _sp.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            g, vu, vt = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
            gpu = f"{g}%"; vram = f"{float(vu)/1024:.1f}/{float(vt)/1024:.0f} GB"
    except Exception:  # noqa: BLE001
        pass
    parts = []
    if cpu:  parts.append(f"<span style='color:#38bdf8'>CPU {cpu}</span>")
    if ram:  parts.append(f"<span style='color:#60a5fa'>RAM {ram}</span>")
    if gpu:  parts.append(f"<span style='color:#c084fc'>GPU {gpu}</span>")
    if vram: parts.append(f"<span style='color:#a78bfa'>VRAM {vram}</span>")
    _HW_CACHE["html"] = ("<div style='margin-top:6px;font-size:.82em;font-family:monospace;"
                         "opacity:.9'>📊 " + " &nbsp;·&nbsp; ".join(parts) + "</div>") if parts else ""


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


def _bench_progress() -> tuple[float, str]:
    """Compat: progresul bench-ului principal (CPU/normal) din bench_full.log."""
    start_ts = None
    if BENCH_PID_FILE.exists():
        try:
            parts = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")
            if len(parts) > 1:
                start_ts = float(parts[1])
        except Exception:  # noqa: BLE001
            pass
    r = _bench_progress_from(BENCH_LOG_FILE, start_ts)
    if r is None:
        return 0.0, "Bench pornește..."
    return r


# --------------------------------------------------------------------------- #
# Cancel
# --------------------------------------------------------------------------- #
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
    ui.notify("Proces anulat (CPU + GPU).", type="warning")
    _refresh_status()


# --------------------------------------------------------------------------- #
# Walk-forward backtest (în thread de fundal, ca să nu blocheze UI-ul)
# --------------------------------------------------------------------------- #

def _start_walk_forward() -> None:
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return
    results_bundle, _ = results
    # NOTĂ inversare: walk-forward-ul rulează pipeline-ul NORMAL (Faza 1, pre-inversare),
    # deci când auto_invert e ON validează pool-ul normal, nu cel inversat afișat.
    # NU mai sărim — afișăm stats-urile etichetate clar ca "Faza 1" (vezi results_panel).
    _has_invert = any(d.get("auto_invert") for _fn, outs in results_bundle for _gl, d in outs.items())
    _pfx = ""  # bench unic → fără prefix de secțiune

    def _worker_wf() -> None:
        try:
            from loto_enterprise.core.walk_forward_adapter import run_honest_walk_forward
            with STATE_LOCK:
                ds_by_name = {fn: df for fn, df in STATE["datasets"]}
            total = sum(len(o) for _, o in results_bundle)
            done = 0
            for fname, outs in results_bundle:
                df_source = ds_by_name.get(fname)
                if df_source is None:
                    continue
                for g_label, data in _ordered_game_items(outs):
                    done += 1
                    base = (done - 1) / max(1, total)
                    STATE["wf_status"] = f"📊 Walk-forward {done}/{total}: {g_label}..."
                    STATE["wf_progress"] = base

                    def _wf_cb(frac, _b=base, _t=total):
                        # progres global = jocuri terminate + fracția jocului curent
                        STATE["wf_progress"] = min(1.0, _b + max(0.0, min(1.0, frac)) / _t)

                    try:
                        flat, meta = run_honest_walk_forward(
                            df_source=df_source, game_type=g_label,
                            pool_size=int(data.get("pool_size") or 10),
                            backtest_depth_percent=5.0, lookback_percent=100.0, use_cache=True,
                            progress_cb=_wf_cb,
                        )
                        with STATE_LOCK:
                            STATE["retro"][f"{_pfx}{fname}_{g_label}"] = flat
                    except Exception as exc:  # noqa: BLE001
                        logger.error("walk-forward %s: %s", g_label, exc)
                    STATE["wf_progress"] = done / max(1, total)
            STATE["wf_status"] = ""
            STATE["wf_progress"] = 1.0
        except Exception as exc:  # noqa: BLE001
            STATE["wf_status"] = f"Walk-forward eșuat: {exc}"
        finally:
            _save_report_file()  # rescriu raportul acum CU statisticile walk-forward
            try:
                results_panel.refresh()
            except Exception:  # noqa: BLE001
                pass

    STATE["wf_progress"] = 0.0
    STATE["wf_start"] = time.time()  # pt ETA walk-forward
    STATE["wf_status"] = ("📊 Pornesc walk-forward backtest (poate dura câteva minute)..."
                          + (" — validare FAZA 1 (pool normal), fiindcă auto-invert e ON" if _has_invert else ""))
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
            # Capturăm durata generării O SINGURĂ DATĂ (fixă) — altfel 'Rezultate (în X)'
            # creștea live cât rula walk-forward-ul (acum − start_job recalculat mereu).
            if STATE.get("job_start_time") and STATE.get("job_elapsed") is None:
                STATE["job_elapsed"] = time.time() - STATE["job_start_time"]
            with STATE_LOCK:
                STATE["results"] = payload
                STATE["active_job_id"] = None
            unlock_engine()
            _save_report_file()  # raport imediat (fără WF); rescris după walk-forward
            _start_walk_forward()
            results_panel.refresh()
            try:
                ui.run_javascript(SOUND_JS)  # beep de finalizare
            except Exception:  # noqa: BLE001
                pass
            _maybe_shutdown()
            ui.label("✅ Generare finalizată.").classes("text-positive text-lg")
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
                ui.html("🔬 <b style='color:#38bdf8'>RE-BENCH</b> — " + rc[1])
                ui.linear_progress(value=rc[0], show_value=False).props("instant-feedback").classes("w-full")
            ui.label("Testez toate metodele (CPU pe nuclee ‖ GPU). Auto-Pilot pornește la final.").classes("text-caption")
            ui.html(_hw_telemetry_html())  # consum live CPU/RAM/GPU/VRAM
        return

    _shutdown_banner()
    if isinstance(STATE.get("results"), tuple):
        ui.label("✅ Ultima generare e gata (vezi mai jos).").classes("text-positive")
    else:
        ui.label("Gata de lucru. Încarcă CSV-uri și apasă Generează / Auto-Pilot.").classes("text-caption")


SOUND_JS = (
    "try{const c=new (window.AudioContext||window.webkitAudioContext)();"
    "const o=c.createOscillator();const g=c.createGain();o.connect(g);g.connect(c.destination);"
    "o.type='sine';o.frequency.value=880;g.gain.value=0.08;o.start();"
    "o.stop(c.currentTime+0.35);}catch(e){}"
)


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
                    ui.html(
                        f'<span style="font-weight:700;font-size:1.1em">{n}</span>'
                        f'<span style="opacity:0.45;font-size:0.68em;margin-left:3px">({freq})</span>'
                    )
                else:
                    ui.html(f'<span style="font-weight:700;font-size:1.1em">{n}</span>')


# --------------------------------------------------------------------------- #
# Randare detaliată rezultate (audit, pipeline stages, financiar)
# --------------------------------------------------------------------------- #
PRICES = {"6/49": 8.0, "5/40": 5.0, "joker": 7.0}  # Lei/variantă (fallback loto.ro)
PRICE_SIMPLE_TICKET = 5.0  # Lei/bilet simplu la agenție

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
     "Pool brut din scorer (bench winner / TimesFM): top-K după scor de probabilitate."),
    ("2_smart_selector", "2. Pool brut (fără rafinare)", "#a78bfa",
     "Smart Selector ELIMINAT — pool-ul rămâne decizia PURĂ a scorerului câștigător "
     "(fără rafinare hibridă). Etapă păstrată doar pentru numerotare (Δ mereu 0)."),
    ("3_anti_sequence", "3. Anti-Sequence Filter", "#f59e0b",
     "Elimină secvențe de 3+ numere consecutive rare; înlocuiește cu rezerve top-frecvență."),
    ("4_post_hoc_final", "4. POST-HOC Final", "#10b981",
     "Validare retrospectivă: substituții iterative ce maximizează hit-urile. Rescrie 40-70% din pool."),
]




def _render_audit(audit: dict, final_pool: set) -> None:
    mi = (audit.get("manual_inversion") or {}).get("enforced_violations_fixed")
    if mi:
        rm = ", ".join(str(n) for n in mi.get("removed", [])) or "(none)"
        ad = ", ".join(str(n) for n in mi.get("added_replacements", [])) or "(none)"
        ui.markdown(f"🛡️ **Hard Enforcement Inversare:** scoase {rm}; înlocuite cu {ad}.").classes("text-info")

    cf = audit.get("consecutive_filter")
    if cf:
        ui.markdown("⚠️ **Intervenție Filtru Anti-Secvență:**\n" + "\n".join(f"- {m}" for m in cf)).classes("text-warning")
    if audit.get("kept_sequences"):
        ui.markdown("ℹ️ **Verificare Anti-Secvență:**\n" + "\n".join(f"- {m}" for m in audit["kept_sequences"])).classes("text-info")

    bw = audit.get("bench_winner") or {}
    scorer_lbl = (f"{next(iter(bw.values())).get('method','?').upper()} (bench winner)" if bw else "Google TimesFM")

    tex = audit.get("timesfm_excluded")
    if tex:
        s = ", ".join(f"{n} (inactiv {d}%)" for n, d in tex.items())
        msg = f"🚫 **{scorer_lbl}** a exclus {len(tex)} numere din Urna 1: {s}"
        tjk = audit.get("timesfm_excluded_joker")
        if tjk:
            sj = ", ".join(f"{n} (inactiv {d}%)" for n, d in tjk.items())
            msg += f"\n\n🚫 și {len(tjk)} numere din Urna 2 (Joker): {sj}"
        ui.markdown(msg).classes("text-negative")

    af = audit.get("anomaly_filter")
    if af:
        ui.markdown(f"🚀 **Neural Anomaly Scoring:** din {af['original_count']} variante au rămas "
                    f"**{af['final_count']}** (threshold {af['threshold']}).").classes("text-positive")

    sm = audit.get("smart_selector")
    if sm:
        scores = sm.get("final_scores", {})
        ui.markdown(f"🧠 **Smart Logic:** {sm.get('method','')}").classes("text-info")
        kept = [n for n in sm.get("kept_numbers", []) if n in final_pool]
        repl = [n for n in sm.get("replaced_numbers", []) if n not in final_pool]
        if kept:
            ui.markdown("✅ Păstrate: " + ", ".join(f"{n} ({scores.get(n,0):.3f})" for n in kept)).classes("text-caption")
        if repl:
            ui.markdown("🔄 Înlocuite: " + ", ".join(str(n) for n in repl)).classes("text-caption")


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
                    chips.append(f"<span style='background:#064e3b;color:#6ee7b7;padding:2px 8px;border-radius:10px;margin:2px;font-weight:bold;'>+{n}</span>")
                else:
                    chips.append(f"<span style='background:rgba(255,255,255,0.07);color:#e5e7eb;padding:2px 8px;border-radius:10px;margin:2px;'>{n}</span>")
            for n in sorted(removed):
                chips.append(f"<span style='background:#7f1d1d;color:#fecaca;padding:2px 8px;border-radius:10px;margin:2px;text-decoration:line-through;'>−{n}</span>")
            delta = f" (Δ: +{len(added)}, −{len(removed)})" if prev is not None else ""
            ui.html(
                f"<div style='margin-top:8px;padding:8px;background:rgba(255,255,255,0.03);border-left:3px solid {color};border-radius:4px;'>"
                f"<div style='font-weight:700;color:{color};'>{title}{delta}</div>"
                f"<div style='font-size:0.85em;color:#94a3b8;margin:2px 0 6px 0;'>{desc}</div>"
                f"<div>{''.join(chips)}</div></div>"
            )
            prev = pool_set


def _render_cost(game: str, data: dict) -> None:
    gk = _game_label_for(game)
    price = PRICES.get(gk, 8.0)
    draw_n = 6 if gk == "6/49" else 5
    pool_used = int(data.get("pool_size") or len(data.get("hard_core") or []))
    import math
    full_vars = math.comb(pool_used, draw_n) if pool_used >= draw_n else 0
    jmult = max(1, len(data.get("hard_core_joker", []))) if gk == "joker" else 1
    full_cost = full_vars * price * jmult

    if gk in LR_SCHEMES and pool_used in LR_SCHEMES[gk]:
        parts = []
        for code, base in LR_SCHEMES[gk][pool_used]:
            tot = base * jmult
            parts.append(f"**{code}** ({tot} var. ≈ {tot*price:,.0f} Lei)")
        ui.markdown(f"💡 **Cost Nucleu Dur la Agenție** ({pool_used} nr.): " + " sau ".join(parts) +
                    f"\n\n*(Sistem Complet ≈ {full_cost:,.0f} Lei)*").classes("text-info")
    else:
        ui.markdown(f"💡 **Cost Nucleu Dur la Agenție:** fără schemă redusă oficială pentru {pool_used} nr. la "
                    f"{game.upper()}. Sistem Complet = {full_vars*jmult} variante ≈ **{full_cost:,.0f} Lei**.").classes("text-info")

    variants = data.get("variants") or []
    if variants:
        n_simple = min(10, len(variants))
        ui.markdown(f"🎟️ **Top {n_simple} bilete simple** ≈ {n_simple*PRICE_SIMPLE_TICKET:,.0f} Lei "
                    f"| Wheel complet ({len(variants)} var.) ≈ {len(variants)*price:,.0f} Lei.").classes("text-caption")


PRIZE_MAP = {
    "6/49": {3: 30, 4: 300, 5: 30000, 6: 1000000},
    "5/40": {3: 50, 4: 500, 5: 50000, 6: 0},
    "joker": {3: 60, 4: 600, 5: 60000, 6: 1000000},
}


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
    parts = [f"<div style='font-weight:bold;margin-bottom:6px;'>{icon} Învățare Adaptivă: {msg} "
             f"<span style='background:{'#a020f0' if ast.get('active_mode')=='reset' else '#28a745'};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.8em;'>"
             f"{'RESET' if ast.get('active_mode')=='reset' else 'NORMAL'}</span></div>"]
    if event is not None:
        ext = f"Ultima extragere: <strong>{ast.get('last_hits')}</strong> hituri în pool"
        if baseline:
            ext += f" <small style='color:#888;'>(baseline aleator: {baseline})</small>"
        parts.append(f"<div>{ext}</div>")
    if ast.get("streak_zero", 0) >= 1:
        parts.append(f"<div>Streak catastrofe consecutive: <strong>{ast['streak_zero']}</strong></div>")
    if rolling is not None:
        rc = "#dc3545" if rolling < baseline else "#28a745"
        parts.append(f"<div>Media rolling (5 extrageri): <strong style='color:{rc};'>{rolling:.2f}</strong></div>")
    if ast.get("missed"):
        parts.append(f"<div style='color:#dc3545;'>Numere ratate: {', '.join(map(str, ast['missed']))} → boost la următoarea predicție</div>")
    if ast.get("false_positives"):
        parts.append(f"<div style='color:#6c757d;'>Prezise dar absente: {', '.join(map(str, ast['false_positives'][:10]))} → penalizare</div>")
    if ast.get("boosts"):
        parts.append("<div><span style='color:#28a745;'>↑ Boost activ:</span> " +
                     ", ".join(f"<strong>{n}</strong>×{m:.2f}" for n, m in ast["boosts"][:6]) + "</div>")
    if ast.get("penalties"):
        parts.append("<div><span style='color:#dc3545;'>↓ Penalizare activă:</span> " +
                     ", ".join(f"<strong>{n}</strong>×{m:.2f}" for n, m in ast["penalties"][:6]) + "</div>")
    cd = audit.get("catastrophe_diversification")
    if cd and cd.get("injected"):
        inj = ", ".join(f"{n}(gap×{gr})" for n, gr in cd["injected"])
        ev = ", ".join(str(n) for n, _ in cd.get("evicted", []))
        parts.append(f"<div style='color:#f4a261;'>💉 Diversificare forțată: injectate <strong>{inj}</strong> în locul lui <strong>{ev}</strong></div>")
    hi = audit.get("hard_inversion")
    if hi:
        excl = hi.get("excluded", [])
        parts.append(f"<div style='color:#e63946;'>🚫 Hard Inversion: <strong>{hi.get('n_excluded', len(excl))}</strong> "
                     f"numere excluse temporar → {', '.join(str(n) for n in excl[:20])}</div>")
    ui.html(f"<div style='margin-top:10px;padding:12px;background:rgba(20,30,50,0.5);border-left:4px solid {color};"
            f"border-radius:8px;font-size:0.9em;'>{''.join(parts)}</div>")


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

        def _hit_badge(h: int) -> str:
            ic = "🔥" if h >= 4 else ("⭐" if h >= 3 else ("🔹" if h >= 1 else "·"))
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
            ui.label("Pentru fiecare extragere reală testată: câte numere a prins Nucleul Dur (pool) "
                     "și cel mai bun bilet generat. 🔥=4+ · ⭐=3 · 🔹=1-2 · ·=0").classes("text-caption text-grey")
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
            ui.html(f"<div style='display:flex;align-items:center;gap:8px;'>"
                    f"<div style='width:110px;font-size:0.85em;'>{h} numere</div>"
                    f"<div style='flex:1;background:rgba(255,255,255,0.06);border-radius:4px;height:12px;'>"
                    f"<div style='background:{color};width:{pct}%;height:100%;border-radius:4px;'></div></div>"
                    f"<div style='width:120px;text-align:right;font-size:0.85em;'>{c} extrageri ({pct:.0f}%)</div></div>")

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
            ui.html(f"<div style='display:flex;align-items:center;gap:8px;'>"
                    f"<div style='width:110px;font-size:0.85em;'>{h} ghicite</div>"
                    f"<div style='flex:1;background:rgba(255,255,255,0.06);border-radius:4px;height:10px;'>"
                    f"<div style='background:{color};width:{pct}%;height:100%;border-radius:4px;'></div></div>"
                    f"<div style='width:90px;text-align:right;font-size:0.85em;'>{pct:.1f}%</div></div>")

        # Tabel pool ≥4
        rows_pool, seen2 = [], set()
        for p in sorted(flat, key=lambda x: (getattr(x, "hits_union", 0), getattr(x, "draw_index", 0)), reverse=True):
            hu = getattr(p, "hits_union", 0)
            di = getattr(p, "draw_index", 0)
            if hu >= 4 and di not in seen2:
                seen2.add(di)
                dd = getattr(p, "draw_date", getattr(p, "target_draw_date", None))
                rows_pool.append({"draw": str(dd) if dd and str(dd) != "None" else f"#{di}", "hits": f"🔥 {hu}"})
        if rows_pool:
            ui.label("🎯 Istoric Pool (≥4 numere):").classes("text-bold text-caption mt-2")
            ui.table(columns=[{"name": "draw", "label": "Data/Extragere", "field": "draw", "align": "left"},
                              {"name": "hits", "label": "Numere în Nucleu", "field": "hits", "align": "left"}],
                     rows=rows_pool).classes("w-full").props("dense")

        # Tabel variante ≥4 — AGREGAT pe extragere. (Înainte: o linie per variantă →
        # aceeași dată apărea de zeci de ori, fiindcă ~zeci de variante prind 4 pe
        # aceeași extragere. Ilizibil.) Acum: o linie per (extragere, hits) + nr. bilete.
        highs = [p for p in flat if getattr(p, "hits", 0) >= 4]
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
            ui.label(f"🎯 Istoric Câștiguri Variante (≥4 numere) — {n_draws_won} extrageri câștigătoare, agregat:").classes(
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
                     "fiecare extragere — scopul aplicației e ACOPERIREA (4+), nu profitul.").classes(
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
        _omn = _omnius_for_pool(g, d)
        if _omn:
            out.append(f"{indent}⭐ OMNIUS (cel mai bun bilet din acest pool): "
                       + ", ".join(str(n) for n in _omn))
        if d.get("hard_core_joker"):
            out.append(f"{indent}Joker: " + ", ".join(str(int(x)) for x in sorted(d["hard_core_joker"])))
        if d.get("p10") is not None:
            out.append(f"{indent}Interval p10–p90: {d.get('p10')} – {d.get('p90')} (g_range={d.get('g_range')})")
        au = d.get("audit") or {}
        if au:
            out.append(f"{indent}--- Audit complet (JSON) ---")
            for line in json.dumps(au, indent=2, ensure_ascii=False, default=str).splitlines():
                out.append(f"{indent}{line}")
        vs = d.get("variants") or []
        out.append(f"{indent}--- Variante simple ({len(vs)}) ---")
        for i, v in enumerate(vs, 1):
            out.append(f"{indent}  V{i}: " + ", ".join(str(int(x)) for x in v))

    for fn, outs in rb:
        out.append(f"\n{'#'*72}\nFIȘIER: {fn}\n{'#'*72}")
        for g, d in _ordered_game_items(outs):
            out.append(f"\n=================  JOC: {g.upper()}  =================")
            flat = STATE["retro"].get(f"{fn}_{g}")
            if d.get("auto_invert") and d.get("phase1"):
                _dump_pool(d["phase1"], "🟢 POOL 1 — normal (cu validare walk-forward)")
                wf = _wf_summary(flat)
                if wf:
                    out.append(f"  Walk-forward (Faza 1): {wf}")
                _dump_pool(d, "🔄 POOL 2 — inversat (numerele excluse din Pool 1; pe șansă, fără validare)")
            else:
                _dump_pool(d, None)
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
    "informer":   "rețea Transformer · NeuralForecast",
    "autoformer": "rețea Transformer (Auto-Correlation) · NeuralForecast",
    "fedformer":  "rețea Transformer (Fourier) · NeuralForecast",
    "patchtst":   "rețea Transformer (patch-based) · NeuralForecast",
    "nbeats":     "rețea MLP · NeuralForecast",
    "nhits":      "MLP ierarhic (N-HiTS) · NeuralForecast",
    "tide":       "model MLP (Google TiDE) · NeuralForecast",
    "dlinear":    "model liniar cu descompunere · NeuralForecast",
    "deepar":     "RNN probabilistic · NeuralForecast",
    "tcn":        "rețea convoluțională temporală · NeuralForecast",
    "timesnet":   "multi-scale conv SoTA 2023 · NeuralForecast",
    "kan":        "Kolmogorov-Arnold Network 2024 (învață funcții) · NeuralForecast",
    "timesfm":    "model foundation pre-antrenat · Google TimesFM",
    "chronos":    "model foundation pre-antrenat · Amazon Chronos",
    "moment":     "model foundation pre-antrenat · CMU MOMENT",
    "geo_spatial_kde_gpu": "densitate spațială 2D pe grila biletului (KDE conv) · PyTorch GPU",
    "geo_rowcol_gpu":      "propensiune geometrică rând × coloană pe bilet · PyTorch GPU",
    "geo_cnn_next_gpu":    "CNN spațial: geometria grilei următoare · PyTorch GPU",
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
    # GPU
    "ml_xgb_gpu":      "XGBoost pe GPU · gradient boosting",
    "ml_lgbm_gpu":     "LightGBM pe GPU · gradient boosting",
    "ml_catboost_gpu": "CatBoost pe GPU · gradient boosting",
    "torch_lstm_m":    "LSTM (PyTorch, GPU)",
    "torch_transformer": "Transformer (PyTorch, GPU)",
    "torch_tcn":       "rețea convoluțională temporală (PyTorch, GPU)",
    "torch_bayesian_lstm": "LSTM bayesian (PyTorch, GPU)",
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
        ui.label(f"Garanție: {data.get('guarantee')}")
        ui.label(f"Variante simple: {len(variants)}")
        ui.label(f"Extrageri: {data.get('total_draws')}")
        # Indicator GPU vs CPU — din audit.compute_device (scris de worker, device-ul REAL folosit)
        _au = data.get("audit") or {}
        _dev = _au.get("compute_device")
        _gms = (_au.get("performance") or {}).get("gpu_time_ms")
        _tsuf = f" <span style='opacity:.6'>({float(_gms)/1000:.1f}s)</span>" if _gms is not None else ""
        if _dev == "gpu":
            ui.html(f"<b style='color:#22c55e'>⚡ GPU</b>{_tsuf}")
        elif _dev == "cpu":
            ui.html(f"<b style='color:#f97316'>🐌 CPU</b>{_tsuf}")

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
                tail += f" <span style='opacity:.65'>— {desc}</span>"
            meta = ", ".join(x for x in [fam, (f"pool {ph}" if ph else "")] if x)
            if meta:
                tail += f" <span style='opacity:.45'>[{meta}]</span>"
            parts.append(f"{gkey} → <b style='color:#ff4d4f;font-size:1.05em'>{m}</b>{tail}")
        ui.html("🏆 Metodă câștigătoare (bench): " + "<br>".join(parts)).classes("text-caption")
    else:
        ui.label("🏆 Metodă scorer: fallback implicit (fără decizie bench disponibilă)").classes(
            "text-caption text-grey")

    ui.label("Nucleu dur (pool):").classes("text-bold mt-2")
    _badges(pool, stats)
    if data.get("hard_core_joker"):
        ui.label("Joker:").classes("text-bold mt-1")
        _badges(data.get("hard_core_joker"), data.get("hard_core_joker_stats"))

    if data.get("p10") is not None:
        ui.label(f"Interval p10–p90: {data.get('p10')} – {data.get('p90')} "
                 f"(g_range={data.get('g_range')})").classes("text-caption")

    audit = data.get("audit") or {}
    final_pool = set(int(x) for x in pool)
    if audit:
        _render_audit(audit, final_pool)
        _render_adaptive(audit)

    _render_cost(game, data)

    if with_wf:
        flat = STATE["retro"].get(f"{res_prefix}{fname}_{game}")
        if flat:
            _bw = (data.get("audit") or {}).get("bench_winner") or {}
            _wm = next((info.get("method") for info in _bw.values() if info.get("method")), "")
            _render_walk_forward(flat, game, is_invert=False, method=_wm)

    # OMNIUS — cel mai bun bilet din ACEST pool (separat per pool)
    _render_omnius_pool(game, data)

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
                ui.html(
                    f"<span style='color:#6b7280;font-weight:600'>V{i:>3}:</span> "
                    f"<span style='color:#e5e7eb'>{nums}</span>"
                ).classes("font-mono text-sm")
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


def _num_scores(d: dict) -> dict:
    """Scor per număr: smart_selector.final_scores (preferat) → frecvență → gol."""
    au = d.get("audit") or {}
    ss = (au.get("smart_selector") or {}).get("final_scores") or {}
    if ss:
        return {int(k): float(v) for k, v in ss.items()}
    stats = d.get("hard_core_stats") or {}
    try:
        return {int(k): float(v) for k, v in stats.items()}
    except (TypeError, ValueError):
        return {}


def _omnius_for_pool(game: str, d: dict) -> list:
    """Biletul OMNIUS din ACEST pool: cele mai bune draw_n numere (50% meta-învățare +
    50%% Smart Logic). CACHE-uit pe (joc, pool) — score_omnius durează ~7s, iar UI-ul
    îl chema la FIECARE randare/raport → satura CPU → 'connection lost'. Acum o dată."""
    gk = _game_label_for(game)
    draw_n = 6 if gk == "6/49" else 5
    pool = sorted(int(x) for x in (d.get("hard_core") or []))
    if not pool:
        return []

    # Cache: cheie = joc + pool exact. Dacă pool-ul e același, refolosim rezultatul.
    _ckey = (gk, tuple(pool))
    _cache = STATE.setdefault("_omnius_cache", {})
    if _ckey in _cache:
        return _cache[_ckey]

    # Încearcă meta-învățarea OMNIUS pe istoricul jocului (același scorer ca metoda bench)
    omni_scores = None
    try:
        df = None
        for fn, _df in STATE.get("datasets", []):
            if _game_label_for(fn) == gk:
                df = _df
                break
        if df is not None:
            from loto_enterprise.benchmark.methods_omnius import score_omnius
            cols = [c for c in df.columns if c.lower().startswith("n")][:draw_n]
            if len(cols) >= draw_n:
                import numpy as _np
                draws = df[cols].to_numpy(dtype=_np.int64)
                max_num = {"6/49": 49, "5/40": 40, "joker": 45}.get(gk, 49)
                omni_scores = score_omnius(draws, max_num, budget_s=2.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("OMNIUS meta pt bilet eșuat (fallback): %s", exc)

    scores = omni_scores or _num_scores(d)
    top = sorted([n for n, _ in sorted(((n, scores.get(n, 0.0)) for n in pool),
                                       key=lambda x: x[1], reverse=True)][:draw_n])
    _cache[_ckey] = top
    return top


def _render_omnius_pool(game: str, d: dict) -> None:
    """Card OMNIUS pentru un singur pool — cel mai bun bilet din el."""
    ticket = _omnius_for_pool(game, d)
    if not ticket:
        return
    chips = "".join(
        f"<span style='background:#064e3b;color:#fbbf24;padding:4px 11px;border-radius:14px;"
        f"margin:3px;font-weight:800;font-size:1.1em'>{n}</span>" for n in ticket)
    jk = d.get("hard_core_joker") or []
    jk_txt = ""
    if jk:
        jkn = sorted(int(x) for x in jk)[:1]
        if jkn:
            jk_txt = (" <span style='opacity:.7'>+ joker</span> "
                      f"<span style='background:#4c1d95;color:#ddd6fe;padding:4px 11px;"
                      f"border-radius:14px;font-weight:800'>{jkn[0]}</span>")
    with ui.card().classes("w-full").style("background:#1e1b4b;border:1px solid #f59e0b"):
        ui.html("⭐ <b style='color:#fbbf24'>OMNIUS</b> — biletul meta-adaptiv din acest pool "
                "<span style='opacity:.65;font-size:.8em'>(ponderează TOATE metodele matematice "
                "după performanța recentă + Smart Logic Hybrid)</span>")
        ui.html("<div style='margin:5px 0'>" + chips + jk_txt + "</div>")


@ui.refreshable
def wf_progress_panel() -> None:
    """Progres walk-forward, SEPARAT de results_panel: tick-ul (2s) refreshează DOAR
    asta, nu tot bundle-ul de rezultate — altfel expansion-urile deschise de user
    (ex. 🏆 Clasament bench, Variante, Pipeline) s-ar reseta/închide la fiecare poll."""
    if not STATE.get("wf_status"):
        return
    _wfp = float(STATE.get("wf_progress") or 0.0)
    # ETA walk-forward: estimare liniară din progres (elapsed × (1-p)/p).
    _eta = ""
    _ws = STATE.get("wf_start")
    if _ws and 0.02 < _wfp < 1.0:
        _rem = (time.time() - _ws) * (1.0 - _wfp) / _wfp
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
        elapsed = f" (în {_fmt_dur(STATE['job_elapsed'])})"
    with ui.row().classes("items-center gap-3 mt-2"):
        ui.label(f"Rezultate{elapsed}").classes("text-h6")
        ui.button("📋 Raport integral", on_click=_show_report).props("flat dense")

    _render_results_bundle(results[0])


# Nume de metode GPU (rețele neurale / foundation) — fallback când folds.csv vechi
# nu are coloana `family`. Evită importul registry-ului greu (torch) în UI.
_GPU_NAME_SET = {
    "dlinear", "nlinear", "nhits", "nbeats", "nbeatsx", "patchtst", "autoformer", "informer",
    "fedformer", "tide", "timesnet", "timemixer", "bitcn", "deepnpts", "tcn", "deepar", "kan",
    "mlpmultivariate", "vanilla_transformer", "itransformer", "tft", "moment", "timesfm",
    "chronos", "tinytimemixer", "lstm", "gru", "rnn",
}


def _method_is_gpu(name: str, family: str = "") -> bool:
    """Rulează pe GPU? Preferă `family` din folds.csv (autoritar, scris de runner);
    altfel cade pe euristica de nume. NU folosește telemetria (gpu%/vram), care la bench-ul
    paralel CPU‖GPU se 'scurge' pe rândurile metodelor CPU → ar eticheta greșit tot ca GPU."""
    f = (family or "").strip().lower()
    if f:
        return (f.startswith("nf-") or f.startswith("foundation") or f.startswith("torch")
                or f.endswith("-gpu") or f == "ssm")
    n = (name or "").lower()
    return (n.startswith("torch_") or n.startswith("ens_torch") or n.endswith("_gpu")
            or n in _GPU_NAME_SET)


def _method_library(name: str, family: str = "") -> str:
    """Librăria/categoria lizibilă a metodei. Din `family` (preferat) sau din nume (fallback)."""
    f = (family or "").strip().lower()
    if f:
        if f.startswith("nf-"):
            return "NeuralForecast"
        if f.startswith("foundation"):
            return "Foundation (TimesFM/Chronos/MOMENT)"
        if f.startswith("ml-"):
            # ml-boost-gpu = XGBoost/LightGBM/CatBoost pe GPU (NU PyTorch, NU sklearn pur)
            return "gradient boosting (XGBoost/LightGBM/CatBoost)" if "boost" in f else "scikit-learn"
        if f.startswith("torch") or f.endswith("-gpu"):
            return "PyTorch"
        if f == "ssm":
            return "state-space"
        if f.startswith("classical"):
            return "statsmodels"
        if f.startswith("ensemble"):
            return "ansamblu (mix de metode)"
        if f == "meta-adaptive":
            return "OMNIUS (meta-învățare)"
        if f == "coverage":
            return "greedy set-cover (numpy)"
        if f.startswith("math") or f.startswith("geometric") or f.startswith("probabil"):
            return "independent (numpy)"
        return family  # familia brută dacă n-o recunoaștem
    n = (name or "").lower()
    if n.startswith("ml_"):
        return ("gradient boosting (XGBoost/LightGBM/CatBoost)"
                if any(b in n for b in ("xgb", "lgbm", "catboost", "boost", "gbm"))
                else "scikit-learn")
    if n.startswith("torch_") or n.startswith("ens_torch") or n.endswith("_gpu"):
        return "PyTorch"
    if n in _GPU_NAME_SET:
        return "NeuralForecast/Foundation"
    if n in {"arima_auto", "ets_auto", "theta_auto", "holt_winters", "stl", "croston_classic"}:
        return "statsmodels"
    return "independent (numpy)"


# Eticheta UI/worker → cheia exactă din folds.csv / best_methods.json
_LABEL_TO_FOLDS_GAME = {
    "6/49": "loto_6_49",
    "5/40": "loto_5_40",
    "joker": "joker_urna1",
}


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
    metric_4plus_pool = f"rate_4plus_k{pool}"
    if metric_4plus_pool in sub.columns:
        has_4plus = True
        metric = metric_4plus_pool
    elif "rate_4plus" in sub.columns:
        has_4plus = True
        metric = "rate_4plus"
    else:
        has_4plus = False
        metric = "avg_hits_topk"
    if metric not in sub.columns:
        return
    has_family = "family" in sub.columns
    rows = []
    for m, grp in sub.groupby("method"):
        score = float(grp[metric].mean())
        avg = float(grp["avg_hits_topk"].mean()) if "avg_hits_topk" in grp.columns else score
        fam = ""
        if has_family:
            _f = grp["family"].dropna().astype(str)
            fam = _f.iloc[0] if not _f.empty else ""
        rows.append((m, score, avg, _method_is_gpu(m, fam), _method_library(m, fam)))
    rows.sort(key=lambda r: (r[1], r[2]), reverse=True)
    if not rows:
        return
    cpu_rows = [r for r in rows if not r[3]][:top_n]
    gpu_rows = [r for r in rows if r[3]][:top_n]
    label = (
        f"rata 4+ @ pool {pool}" if has_4plus and metric.startswith("rate_4plus_k")
        else "rata 4+ numere ghicite" if has_4plus
        else "medie hituri / extragere"
    )
    winner = rows[0]  # câștigătorul GLOBAL (CPU+GPU împreună) — cel ales de bench

    def _row(i, rec):
        m, score, avg, is_gpu, lib = rec
        tag = "⚡ GPU" if is_gpu else "🖥️ CPU"
        tag_cls = "text-deep-purple" if is_gpu else "text-blue"
        sc_txt = f"4+: {score*100:.1f}%" if has_4plus else f"medie: {score:.3f}"
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label(f"{i}.").classes("text-bold text-grey w-6")
            ui.label(tag).classes(f"text-caption text-bold {tag_cls}")
            ui.label(m).classes("text-bold")
            ui.label(f"· {lib} · {sc_txt} · medie/extragere {avg:.2f}").classes("text-caption text-grey")

    title = f"🏆 Clasament bench — {section_label} (CPU + GPU · {label})"
    with ui.expansion(title, value=False).classes("w-full"):
        ui.label(f"Câștigător GLOBAL (toate metodele): {winner[0]} "
                 f"({'⚡ GPU' if winner[3] else '🖥️ CPU'} · {winner[4]}).").classes(
            "text-caption text-bold text-positive")
        if not has_family:
            ui.label("ℹ️ Librăria e estimată din nume (folds.csv vechi). Rulează un Re-Bench "
                     "pentru etichete exacte.").classes("text-caption text-orange")
        # ── Top CPU ──
        ui.label(f"🖥️ Top {len(cpu_rows)} CPU (statistice / matematice / sklearn)").classes(
            "text-bold text-blue mt-2")
        for i, rec in enumerate(cpu_rows, 1):
            _row(i, rec)
        # ── Top GPU ──
        ui.label(f"⚡ Top {len(gpu_rows)} GPU (rețele neurale / foundation)").classes(
            "text-bold text-deep-purple mt-3")
        if gpu_rows:
            ui.label("Pe loto (date aleatoare) rețelele prind de obicei MAI PUȚINE 4+ decât "
                     "euristicile simple — de-aia rar intră în topul global.").classes("text-caption text-grey")
            for i, rec in enumerate(gpu_rows, 1):
                _row(i, rec)
        else:
            ui.label("Nicio metodă GPU în acest bench (toate cele rulate au fost CPU).").classes(
                "text-caption text-grey")


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


def _render_analysis_menu(results_bundle, res_prefix: str = "") -> None:
    """UN singur meniu global cu Clasamentul bench + Walk-forward pentru TOATE jocurile.

    Le scoatem din cardurile per-joc (unde îngreunau citirea pool-urilor) și le strângem
    aici, închis implicit. Așa rezultatele de jucat (pool + bilete) rămân curate.
    """
    has_folds = (PROJECT_ROOT / "bench_results" / "folds.csv").exists()
    has_wf = any(
        STATE["retro"].get(f"{res_prefix}{fn}_{g}")
        for fn, outs in results_bundle for g, _ in outs.items()
    )
    if not (has_folds or has_wf):
        return

    with ui.card().classes("w-full"):
        with ui.expansion(
            "📊 Analiză & Clasament — freshness · decizie · clasament · walk-forward",
            value=False,
        ).classes("w-full"):
            ui.label("Strâns aici ca rezultatele de sus (pool-uri + bilete de jucat) să rămână "
                     "curate. Deschide pentru detaliile de validare.").classes("text-caption text-grey")

            # --- Freshness + Decizie benchmark + Matrice (era în Power-User) ---
            analysis_panel()
            ui.separator().classes("my-3")

            for fname, outs in results_bundle:
                for game, data in _ordered_game_items(outs):
                    ui.separator().classes("my-3")
                    ui.label(f"🎯 {game.upper()}  ·  {fname}").classes("text-bold text-lg")
                    _render_bench_leaderboard(game)
                    # Walk-forward pe faza principală (phase1 când e auto-invert, altfel data)
                    main = (data.get("phase1")
                            if (data.get("auto_invert") and data.get("phase1")) else data)
                    flat = STATE["retro"].get(f"{res_prefix}{fname}_{game}")
                    if flat:
                        _bw = (main.get("audit") or {}).get("bench_winner") or {}
                        _wm = next((info.get("method") for info in _bw.values()
                                    if info.get("method")), "")
                        _render_walk_forward(flat, game, is_invert=False, method=_wm)


def _render_results_bundle(results_bundle, res_prefix: str = "") -> None:
    # 1) Meniu global analiză — sus, închis implicit.
    _render_analysis_menu(results_bundle, res_prefix)

    # 2) Pool-urile per joc — DOAR pool + bilete de jucat (fără clasament/walk-forward).
    for fname, outs in results_bundle:
        with ui.card().classes("w-full"):
            ui.label(f"📄 {fname}").classes("text-subtitle1 text-bold")
            for game, data in _ordered_game_items(outs):
                with ui.expansion(f"🎯 {game.upper()}", value=True).classes("w-full"):
                    if data.get("auto_invert") and data.get("phase1"):
                        # AUTO-INVERT → DOUĂ pool-uri de jucat:
                        ui.label("🟢 POOL 1 — normal (pe date; validat istoric — vezi 📊 Analiză)").classes(
                            "text-bold text-positive text-lg mt-1")
                        ui.label("Pariul principal: pool-ul validat istoric.").classes("text-caption")
                        _render_pool_body(fname, game, data["phase1"], skey_suffix="_p1", with_wf=False, res_prefix=res_prefix)

                        ui.separator().classes("my-3")
                        # Inversare neaplicată? (pool prea mare → Pool 2 = Pool 1)
                        _p1 = set(int(x) for x in (data["phase1"].get("hard_core") or []))
                        _p2 = set(int(x) for x in (data.get("hard_core") or []))
                        _mi = (data.get("audit") or {}).get("manual_inversion") or {}
                        if _p2 == _p1 or _mi.get("skipped"):
                            pmax = _mi.get("pool_max_pentru_inversare")
                            ui.label("⚠️ INVERSARE NEAPLICATĂ — Pool 2 e identic cu Pool 1!").classes(
                                "text-bold text-negative text-lg")
                            ui.label(
                                f"Pool-ul ({len(_p2)}) e prea mare pentru inversare la acest joc — după "
                                f"excluderea Pool 1 + numerele moarte nu mai rămân destule numere."
                                + (f" Reduceți pool-ul la ≤{pmax} pentru acest joc ca să meargă inversarea." if pmax else "")
                                + " Momentan Pool 2 NU e o alternativă reală."
                            ).classes("text-caption text-negative")
                        ui.label("🔄 POOL 2 — inversat (numerele EXCLUSE din Pool 1)").classes(
                            "text-bold text-warning text-lg")
                        ui.label("Plasă de siguranță, pe șansă — dacă Pool 1 nu nimerește nimic. "
                                 "Fără backtest/validare, intenționat.").classes("text-caption")
                        _render_pool_body(fname, game, data, skey_suffix="_p2", with_wf=False, res_prefix=res_prefix)
                    else:
                        _render_pool_body(fname, game, data, with_wf=False, res_prefix=res_prefix)



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
    head = "".join(f"<th style='padding:2px 6px;font-size:0.75em;'>{c}%</th>" for c in cols)
    body = ""
    for method, row in matrix.iterrows():
        cells = ""
        for c in cols:
            try:
                v = float(row[c])
            except Exception:  # noqa: BLE001
                v = float("nan")
            if not math.isfinite(v):
                cells += ("<td style='padding:2px 6px;background:#2a2a2a;color:#666;"
                          "font-size:0.78em;text-align:center;'>—</td>")
                continue
            t = (v - vmin) / span  # 0..1
            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
            r = int(220 - 140 * t)
            g = int(80 + 140 * t)
            cells += (f"<td style='padding:2px 6px;background:rgb({r},{g},80);color:#111;"
                      f"font-size:0.78em;text-align:center;'>{v:.3f}</td>")
        body += f"<tr><td style='padding:2px 6px;font-weight:600;font-size:0.78em;'>{method}</td>{cells}</tr>"
    ui.html(f"<table style='border-collapse:collapse;'><tr><th></th>{head}</tr>{body}</table>")


@ui.refreshable
def analysis_panel() -> None:
    pool = int(SETTINGS["pool_size_val"])

    # --- Status freshness ---
    try:
        from loto_enterprise.benchmark.freshness import check_freshness, aggregate_recommendation
        reports = check_freshness()
        rec = aggregate_recommendation(reports)
        rec_lbl = {"use_cache": "✅ Cache valid — fără re-bench",
                   "quick_rebench": "🟡 Re-bench recomandat (drift ușor)",
                   "full_rebench": "🔴 Re-bench recomandat (drift mare)"}.get(rec, rec)
        ui.label(f"Freshness benchmark: {rec_lbl}").classes("text-bold")
        for gk, r in reports.items():
            ui.label(f"  • {gk}: {getattr(r, 'status', '?')} "
                     f"(rânduri {getattr(r,'current_rows','?')} vs cache {getattr(r,'cached_rows','?')})").classes("text-caption")
    except Exception as exc:  # noqa: BLE001
        ui.label(f"Freshness indisponibil ({exc}).").classes("text-caption")

    # --- Decizie benchmark per joc ---
    ui.label("Decizie benchmark (scorer optim per joc — CPU/GPU + librărie):").classes("text-bold mt-2")
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        any_dec = False
        for lbl, gk in GK_MATRIX.items():
            ps = 1 if gk == "joker_urna2" else pool
            cfg = recommend_optimal_config(gk, ps)
            if cfg and not cfg.get("fallback"):
                any_dec = True
                m = cfg.get("scorer", "?")
                fam = str(cfg.get("family", "") or "")
                is_gpu = _method_is_gpu(m, fam)
                lib = _method_library(m, fam)
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"  • {lbl} (K={ps}):").classes("text-caption")
                    ui.label("⚡ GPU" if is_gpu else "🖥️ CPU").classes(
                        "text-caption text-bold " + ("text-deep-purple" if is_gpu else "text-blue"))
                    ui.label(f"{m} @ {cfg.get('sim_depth_pct')}% "
                             f"(avg {cfg.get('avg_hits', 0):.3f}, BL={cfg.get('use_blacklist')}) · {lib}").classes("text-caption")
        if not any_dec:
            ui.label("  Fără decizie încă — rulează un Re-Bench.").classes("text-caption text-warning")
    except Exception as exc:  # noqa: BLE001
        ui.label(f"  Decizie indisponibilă ({exc}).").classes("text-caption")

    # --- Matrice walk-forward onestă (din bench folds) ---
    try:
        from loto_enterprise.benchmark.matrix_reader import load_folds, summary_per_game
        folds = load_folds()
        if folds is not None and not folds.empty:
            with ui.expansion("🔬 Matrice Walk-Forward Onestă (joc × fereastră × model)", value=False).classes("w-full"):
                ui.label("Celule = avg hits/extragere pe ferestre regresive 10-100% (fără data leak). Verde = mai mare.").classes("text-caption")
                for lbl, gk in GK_MATRIX.items():
                    ps = 1 if gk == "joker_urna2" else pool
                    s = summary_per_game(folds, gk, ps)
                    if not s.get("available"):
                        continue
                    _bm = s["best_method"]
                    _tag = "⚡ GPU" if _method_is_gpu(_bm) else "🖥️ CPU"
                    _lib = _method_library(_bm)
                    ui.label(f"{lbl} (K={ps}) — top: {_tag} {_bm} (avg={s['best_mean']:.3f}) · {_lib}").classes("text-bold mt-1")
                    _render_matrix_html(s["matrix"])
        else:
            ui.label("Matrice walk-forward indisponibilă — rulează un benchmark.").classes("text-caption")
    except Exception as exc:  # noqa: BLE001
        ui.label(f"Matrice indisponibilă ({exc}).").classes("text-caption")


ADAPTIVE_STATE_FILE = PROJECT_ROOT / "adaptive_state.json"
SUPPORTED_POOLS = set(range(6, 17))  # 6..16 (pool max actual; intrari >16 = stale vechi)


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
        _bind_save(ui.checkbox("Filtru Anti-Secvență"), "consecutive_filter_val")
        _bind_save(ui.checkbox("🔄 Inversare automată"), "auto_invert_val")
        _bind_save(ui.checkbox("🔌 Oprește PC-ul automat la final"), "shutdown_on_complete")

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
        ui.button("🔬 RE-BENCH (CPU ‖ GPU paralel)", on_click=run_rebench
                  ).props("color=orange no-caps").classes(_BTN).style(_BTN_STYLE)
        ui.label("Un singur bench testează TOATE metodele. Intern, metodele CPU rulează pe "
                 "toate nucleele (în paralel) SIMULTAN cu metodele GPU. Toate concurează în "
                 "ACELAȘI clasament → UN câștigător (regula 4+) → UN Auto-Pilot → UN walk-forward. "
                 "Vezi clasamentul complet (CPU+GPU) la 🏆 Clasament bench.").classes("text-caption")
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
    ui.timer(2.0, _tick)


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


app.on_startup(_startup)

if __name__ in {"__main__", "__mp_main__"}:
    _port = int(os.environ.get("LOTO_UI_PORT", "8080"))
    # show=False: browserul e deschis de START_8000.bat (mai fiabil pe Windows).
    # reconnect_timeout mărit: cât rulează bench/walk-forward, event-loop-ul poate fi
    # ocupat (citiri loguri OneDrive) → fără timeout generos, WebSocket pica 'connection lost'.
    ui.run(title="Loto Enterprise Wheeling", port=_port, reload=False, show=False, dark=True,
           reconnect_timeout=60.0)
