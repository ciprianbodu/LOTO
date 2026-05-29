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
UPLOAD_DIR = PROJECT_ROOT / "uploaded_data"
UPLOAD_MANIFEST = UPLOAD_DIR / "manifest.json"
BENCH_PID_FILE = PROJECT_ROOT / ".bench_pid"
BENCH_LOG_FILE = PROJECT_ROOT / "bench_full.log"
KNOWN_CSVS = ["joker.csv", "loto_6_49.csv", "loto_5_40.csv", "input.csv"]

UI_PERSIST_KEYS = [
    "pool_size_val", "guarantee_val", "max_variants_val", "lookback_val",
    "consecutive_filter_val", "auto_invert_val", "shutdown_on_complete",
    "sim_depth_val",
]
DEFAULTS = {
    "pool_size_val": 10, "guarantee_val": 4, "max_variants_val": 0,
    "lookback_val": 0, "consecutive_filter_val": True, "auto_invert_val": False,
    "shutdown_on_complete": False, "sim_depth_val": 40,
}

# --------------------------------------------------------------------------- #
# Stare server-side (single-user local app → globală e suficient)
# --------------------------------------------------------------------------- #
SETTINGS: dict = dict(DEFAULTS)
STATE: dict = {
    "datasets": [],          # list[(fname, DataFrame)]
    "active_job_id": None,
    "job_start_time": None,
    "results": None,         # (results_bundle, count)
    "retro": {},             # {f"{fname}_{game}": flat_walk_forward}
    "wf_status": "",         # text status walk-forward
    "pure_bench": False,
    "calib": {},             # {game_label: {"best": int, "detail": dict}}
    "calib_status": "",
    "show_all": {},          # {f"{fname}_{game}": bool} — toggle wheel complet
}

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


def _save_settings() -> None:
    try:
        UI_STATE_FILE.write_text(json.dumps(SETTINGS, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("save settings: %s", exc)


def _game_label_for(fname: str) -> str:
    low = fname.lower()
    if "5_40" in low or "5/40" in low:
        return "5/40"
    if "joker" in low:
        return "joker"
    return "6/49"


# --------------------------------------------------------------------------- #
# Datasets (upload + auto-load de pe disk)
# --------------------------------------------------------------------------- #
def _load_datasets_from_disk() -> None:
    """Restaurează CSV-urile încărcate (orice nume) din manifest, apoi fallback
    la numele cunoscute — identic cu auto-load-ul din app.py."""
    auto_ds = []
    if UPLOAD_MANIFEST.exists():
        try:
            for name in json.loads(UPLOAD_MANIFEST.read_text(encoding="utf-8")):
                fp = UPLOAD_DIR / name
                if fp.exists():
                    auto_ds.append((name, pd.read_csv(fp)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-load manifest: %s", exc)
    if not auto_ds:
        for name in KNOWN_CSVS:
            fp = PROJECT_ROOT / name
            if fp.exists():
                try:
                    auto_ds.append((name, pd.read_csv(fp)))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auto-load %s: %s", name, exc)
    STATE["datasets"] = auto_ds


def _persist_uploaded(name: str, content: bytes) -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / name).write_bytes(content)
    names = []
    if UPLOAD_MANIFEST.exists():
        try:
            names = json.loads(UPLOAD_MANIFEST.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            names = []
    if name not in names:
        names.append(name)
    UPLOAD_MANIFEST.write_text(json.dumps(names), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Submit job (contract config_json identic cu app.py)
# --------------------------------------------------------------------------- #
def _build_config_json() -> str:
    h = hashlib.sha256()
    for k in ("pool_size_val", "guarantee_val", "max_variants_val", "lookback_val",
              "consecutive_filter_val", "sim_depth_val"):
        h.update(str(SETTINGS[k]).encode("utf-8"))
    pure = bool(STATE.get("pure_bench"))
    datasets_cfg = []
    for fname, df in STATE["datasets"]:
        g_label = _game_label_for(fname)
        task = {
            "game_label": g_label,
            "pool_size": int(SETTINGS["pool_size_val"]),
            "guarantee": int(SETTINGS["guarantee_val"]),
            "max_variants": int(SETTINGS["max_variants_val"]),
            "lookback": int(SETTINGS["lookback_val"]),
            "filter_consecutives": False if pure else bool(SETTINGS["consecutive_filter_val"]),
            "smart_reduction": False if pure else True,
            "sim_depth_pct": int(SETTINGS["sim_depth_val"]),
            "pure_bench_mode": pure,
            "auto_invert": bool(SETTINGS["auto_invert_val"]),
        }
        datasets_cfg.append({
            "fname": fname,
            "df_json": df.to_json(orient="split"),
            "tasks": [task],
        })
        h.update(fname.encode("utf-8"))
    return json.dumps({"input_hash": h.hexdigest(), "use_cache": False, "datasets": datasets_cfg})


def submit_generation(pure: bool = False) -> None:
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
    cfg = _build_config_json()
    job_id = submit_job("pipeline", cfg)
    STATE["active_job_id"] = int(job_id)
    STATE["job_start_time"] = time.time()
    ui.notify(f"Job #{job_id} trimis.", type="positive")
    _refresh_status()


def apply_autopilot_and_generate() -> None:
    """Aplică sim_depth recomandat per joc din best_methods.json, apoi generează."""
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        recs = []
        for fname, _ in STATE["datasets"]:
            gk = _game_label_for(fname)
            cfg = recommend_optimal_config(gk, int(SETTINGS["pool_size_val"]))
            if cfg and not cfg.get("fallback"):
                SETTINGS["sim_depth_val"] = int(cfg.get("sim_depth_pct", SETTINGS["sim_depth_val"]))
                recs.append(f"{gk}: {cfg.get('scorer')} @ {cfg.get('sim_depth_pct')}%")
        if recs:
            _save_settings()
            ui.notify("Auto-Pilot: " + " | ".join(recs), type="info")
        else:
            ui.notify("Fără decizie bench încă — rulează un Re-Bench întâi. Folosesc setările curente.", type="warning")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Auto-Pilot indisponibil ({exc}); folosesc setările curente.", type="warning")
    submit_generation(pure=False)


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
        if os.name == "nt":
            proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), creationflags=flags)
        else:
            logf = open(BENCH_LOG_FILE, "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), stdout=logf, stderr=subprocess.STDOUT)
        BENCH_PID_FILE.write_text(f"{proc.pid}|{int(time.time())}", encoding="utf-8")
        ui.notify(f"{label} pornit (PID {proc.pid}).", type="positive")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Nu pot porni bench-ul: {exc}", type="negative")
    _refresh_status()


def run_full_rebench() -> None:
    _launch_bench(["--no-rich", "--percentiles", "10,20,30,40,50,60,70,80,90,100"], "FULL Re-Bench")


def run_quick_rebench() -> None:
    try:
        from loto_enterprise.benchmark.quick_rebench import quick_rebench_cli_args
        args = quick_rebench_cli_args()
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Quick args indisponibile ({exc}).", type="negative")
        return
    _launch_bench(args, "QUICK Re-Bench")


def _bench_progress() -> tuple[float, str]:
    """(fracție 0..1, text) din bench_full.log — caută [N/TOTAL]."""
    if not BENCH_LOG_FILE.exists():
        return 0.0, "Bench pornește..."
    try:
        import re
        txt = BENCH_LOG_FILE.read_text(encoding="utf-8", errors="replace")
        matches = re.findall(r"\[(\d+)/(\d+)\]", txt)
        if matches:
            cur, tot = matches[-1]
            frac = max(0.0, min(1.0, int(cur) / max(1, int(tot))))
            return frac, f"Bench: {cur}/{tot} ({int(frac*100)}%)"
    except Exception:  # noqa: BLE001
        pass
    return 0.05, "Bench în curs..."


# --------------------------------------------------------------------------- #
# Cancel
# --------------------------------------------------------------------------- #
def cancel_all() -> None:
    try:
        cancel_pending_running_jobs("Oprit de utilizator")
    except Exception as exc:  # noqa: BLE001
        logger.warning("cancel jobs: %s", exc)
    # Kill bench dacă rulează
    if BENCH_PID_FILE.exists():
        try:
            import psutil
            pid = int(BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")[0])
            if psutil.pid_exists(pid):
                psutil.Process(pid).terminate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("kill bench: %s", exc)
        try:
            BENCH_PID_FILE.unlink()
        except OSError:
            pass
    STATE["active_job_id"] = None
    unlock_engine()
    ui.notify("Proces anulat.", type="warning")
    _refresh_status()


# --------------------------------------------------------------------------- #
# Walk-forward backtest (în thread de fundal, ca să nu blocheze UI-ul)
# --------------------------------------------------------------------------- #
def _start_walk_forward() -> None:
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return
    results_bundle, _ = results
    # Dacă inversarea a fost activă, statisticile ar fi înșelătoare → sărim.
    for _fn, outs in results_bundle:
        for _gl, d in outs.items():
            if d.get("auto_invert"):
                STATE["wf_status"] = ("⚠️ Walk-forward sărit — inversarea automată a fost activă "
                                      "(statisticile ar fi pentru pool-ul normal, nu inversat).")
                return

    def _worker_wf() -> None:
        try:
            from loto_enterprise.core.walk_forward_adapter import run_honest_walk_forward
            ds_by_name = {fn: df for fn, df in STATE["datasets"]}
            total = sum(len(o) for _, o in results_bundle)
            done = 0
            for fname, outs in results_bundle:
                df_source = ds_by_name.get(fname)
                if df_source is None:
                    continue
                for g_label, data in outs.items():
                    done += 1
                    STATE["wf_status"] = f"📊 Walk-forward {done}/{total}: {g_label}..."
                    try:
                        flat, meta = run_honest_walk_forward(
                            df_source=df_source, game_type=g_label,
                            pool_size=int(data.get("pool_size") or 10),
                            backtest_depth_percent=5.0, lookback_percent=100.0, use_cache=True,
                        )
                        STATE["retro"][f"{fname}_{g_label}"] = flat
                    except Exception as exc:  # noqa: BLE001
                        logger.error("walk-forward %s: %s", g_label, exc)
            STATE["wf_status"] = ""
        except Exception as exc:  # noqa: BLE001
            STATE["wf_status"] = f"Walk-forward eșuat: {exc}"
        finally:
            try:
                results_panel.refresh()
            except Exception:  # noqa: BLE001
                pass

    STATE["wf_status"] = "📊 Pornesc walk-forward backtest (poate dura câteva minute)..."
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
            STATE["results"] = payload
            STATE["active_job_id"] = None
            unlock_engine()
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
            ui.label(f"⏳ Job în rulare (#{job_id}) — {pct}%")
            ui.linear_progress(value=pct / 100.0, show_value=False).props("instant-feedback")
        return

    if bench_on:
        frac, txt = _bench_progress()
        with ui.card().classes("w-full"):
            ui.label(f"🔬 {txt}")
            ui.linear_progress(value=frac, show_value=False).props("instant-feedback")
            ui.label("Generarea poate porni după ce bench-ul termină.").classes("text-caption")
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


@ui.refreshable
def logs_panel() -> None:
    ui.code(read_logs_filtered(50), language="text").classes("w-full max-h-60 overflow-auto text-xs")


def _badges(numbers, stats: dict | None = None):
    stats = stats or {}
    with ui.row().classes("flex-wrap gap-1"):
        for n in sorted(int(x) for x in (numbers or [])):
            freq = stats.get(str(n), stats.get(n))
            lbl = f"{n}" + (f" ({freq})" if freq is not None else "")
            ui.badge(lbl).props("color=primary").classes("text-sm")


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
    ("2_smart_selector", "2. Smart Selector", "#a78bfa",
     "Rafinare hibridă: 40% Gap + 25% Trend + 20% Frequency + 15% Positional."),
    ("3_anti_sequence", "3. Anti-Sequence Filter", "#f59e0b",
     "Elimină secvențe de 3+ numere consecutive rare; înlocuiește cu rezerve top-frecvență."),
    ("4_post_hoc_final", "4. POST-HOC Final", "#10b981",
     "Validare retrospectivă: substituții iterative ce maximizează hit-urile. Rescrie 40-70% din pool."),
]


def _game_key_from(game: str) -> str:
    low = game.lower()
    if "5/40" in low or "5_40" in low:
        return "5/40"
    if "joker" in low:
        return "joker"
    return "6/49"


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
    gk = _game_key_from(game)
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


def _render_walk_forward(flat, game: str) -> None:
    if not flat:
        return
    gk = _game_key_from(game)
    draw_n = 6 if gk == "6/49" else 5
    n = len(flat)
    uniq = {getattr(p, "draw_index", i) for i, p in enumerate(flat)}
    avg_var = sum(getattr(p, "hits", 0) for p in flat) / n
    avg_pool = sum(getattr(p, "hits_union", 0) for p in flat) / n
    best_var = max(getattr(p, "hits", 0) for p in flat)
    best_pool = max(getattr(p, "hits_union", 0) for p in flat)
    avg_rate = (avg_var / draw_n) * 100

    with ui.card().classes("w-full mt-2"):
        ui.label(f"📊 Walk-forward: {n} predicții pe {len(uniq)} extrageri").classes("text-bold text-info")
        with ui.row().classes("gap-8"):
            for lbl, val in [("Medie/variantă", f"{avg_var:.2f}"), ("Medie/pool", f"{avg_pool:.2f}"),
                             ("Rată medie", f"{avg_rate:.1f}%"), ("Max variantă", best_var), ("Max pool", best_pool)]:
                with ui.column().classes("items-center gap-0"):
                    ui.label(lbl).classes("text-caption")
                    ui.label(str(val)).classes("text-h6")

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

        # Tabel variante ≥3 + ROI
        highs = [p for p in flat if getattr(p, "hits", 0) >= 3]
        if highs:
            rows_v, total_prize = [], 0
            pm = PRIZE_MAP.get(gk, PRIZE_MAP["6/49"])
            for p in sorted(highs, key=lambda x: (x.hits, getattr(x, "draw_index", 0)), reverse=True):
                dd = getattr(p, "draw_date", getattr(p, "target_draw_date", None))
                prize = pm.get(p.hits, 0)
                total_prize += prize
                rows_v.append({"draw": str(dd) if dd and str(dd) != "None" else f"#{getattr(p,'draw_index',0)}",
                               "hits": f"⭐ {p.hits}", "prize": f"~{prize} Lei"})
            ui.label("🎯 Istoric Câștiguri Variante (≥3 numere):").classes("text-bold text-caption mt-2")
            ui.table(columns=[{"name": "draw", "label": "Data/Extragere", "field": "draw", "align": "left"},
                              {"name": "hits", "label": "Hits", "field": "hits", "align": "left"},
                              {"name": "prize", "label": "Est. Premiu", "field": "prize", "align": "left"}],
                     rows=rows_v).classes("w-full").props("dense")
            total_variants = len({tuple(getattr(p, "variant", ())) for p in flat}) or n
            cost = total_variants * PRICES.get(gk, 8.0) * len(uniq)
            profit = total_prize - cost
            roi = (profit / cost * 100) if cost > 0 else 0
            rc = "text-positive" if profit >= 0 else "text-negative"
            ui.label(f"Analiză financiară backtest: cost ≈ {cost:,.0f} Lei | premii ≈ {total_prize:,.0f} Lei "
                     f"| ROI: {'+' if profit>=0 else ''}{roi:.1f}%").classes(rc)


def _build_report() -> str:
    res = STATE.get("results")
    if not isinstance(res, tuple) or len(res) != 2:
        return "(fără rezultate)"
    rb, _ = res
    out = ["=" * 64, "LOTO ENTERPRISE WHEELING — RAPORT", "=" * 64]
    for fn, outs in rb:
        out.append(f"\nFișier: {fn}")
        for g, d in outs.items():
            out.append(f"  {g.upper()} | pool={d.get('pool_size')} garanție={d.get('guarantee')} "
                       f"variante={len(d.get('variants') or [])} extrageri={d.get('total_draws')}")
            out.append("  Nucleu dur: " + ", ".join(str(int(x)) for x in sorted(d.get("hard_core") or [])))
            if d.get("hard_core_joker"):
                out.append("  Joker: " + ", ".join(str(int(x)) for x in sorted(d["hard_core_joker"])))
            flat = STATE["retro"].get(f"{fn}_{g}")
            if flat:
                nn = len(flat)
                ap = sum(getattr(p, "hits_union", 0) for p in flat) / nn
                bp = max(getattr(p, "hits_union", 0) for p in flat)
                out.append(f"  Walk-forward: {nn} predicții | avg pool={ap:.2f} | best pool={bp}")
            for i, v in enumerate(d.get("variants") or [], 1):
                out.append(f"    V{i}: " + ", ".join(str(int(x)) for x in v))
    return "\n".join(out)


def _show_report() -> None:
    with ui.dialog() as dlg, ui.card().classes("w-11/12 max-w-3xl"):
        ui.label("Raport integral (selectează tot + Ctrl+C)").classes("text-bold")
        ui.textarea(value=_build_report()).classes("w-full").props("readonly autogrow filled")
        ui.button("Închide", on_click=dlg.close)
    dlg.open()


@ui.refreshable
def results_panel() -> None:
    if STATE.get("wf_status"):
        ui.label(STATE["wf_status"]).classes("text-info")

    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return
    results_bundle, _ = results
    elapsed = ""
    if STATE.get("job_start_time"):
        elapsed = f" (în {time.time() - STATE['job_start_time']:.0f}s)"

    with ui.row().classes("items-center gap-3 mt-2"):
        ui.label(f"Rezultate{elapsed}").classes("text-h6")
        ui.button("📋 Raport integral", on_click=_show_report).props("flat dense")

    for fname, outs in results_bundle:
        with ui.card().classes("w-full"):
            ui.label(f"📄 {fname}").classes("text-subtitle1 text-bold")
            for game, data in outs.items():
                with ui.expansion(f"🎯 {game.upper()}", value=True).classes("w-full"):
                    pool = data.get("hard_core") or []
                    stats = data.get("hard_core_stats") or {}
                    eff = data.get("pool_size")
                    req = data.get("pool_size_requested")
                    variants = data.get("variants") or []

                    with ui.row().classes("gap-6 items-center"):
                        ui.label(f"Pool efectiv: {eff}" + (f" (cerut {req})" if req and req != eff else ""))
                        ui.label(f"Garanție: {data.get('guarantee')}")
                        ui.label(f"Variante: {len(variants)}")
                        ui.label(f"Extrageri: {data.get('total_draws')}")

                    if data.get("auto_invert") and data.get("auto_invert_pool_a"):
                        pa = data["auto_invert_pool_a"]
                        ui.label("🔄 Inversare automată — Pool A (exclus):").classes("text-warning")
                        _badges(pa.get("hard_core", []), pa.get("hard_core_stats"))

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

                    # Cost financiar (scheme reduse oficiale sau sistem complet)
                    _render_cost(game, data)

                    # Walk-forward backtest (detaliat)
                    flat = STATE["retro"].get(f"{fname}_{game}")
                    if flat:
                        _render_walk_forward(flat, game)

                    # Variante: top 10 + toggle wheel complet
                    if variants:
                        is_jk = "joker" in game.lower()
                        skey = f"{fname}_{game}"
                        show_all = STATE["show_all"].get(skey, False)
                        with ui.expansion(f"Variante ({len(variants)})", value=False).classes("w-full"):
                            shown = variants if show_all else variants[:10]
                            for i, v in enumerate(shown, 1):
                                if is_jk and len(v) == 6:
                                    nums = ", ".join(str(int(x)) for x in v[:5]) + f"  +{int(v[-1])}"
                                else:
                                    nums = ", ".join(str(int(x)) for x in v)
                                ui.label(f"V{i:>3}: {nums}").classes("font-mono text-sm")
                            if len(variants) > 10:
                                def _toggle(k=skey):
                                    STATE["show_all"][k] = not STATE["show_all"].get(k, False)
                                    results_panel.refresh()
                                ui.button(
                                    f"🔼 Ascunde" if show_all else f"🔽 Arată toate ({len(variants)})",
                                    on_click=_toggle,
                                ).props("flat dense")

                    # Pipeline stage-by-stage (evoluția pool-ului)
                    if audit:
                        _render_stages(audit)
                        with ui.expansion("🔍 Audit brut (JSON)", value=False).classes("w-full"):
                            ui.code(json.dumps(audit, indent=2, ensure_ascii=False, default=str),
                                    language="json").classes("w-full max-h-80 overflow-auto text-xs")


def run_calibration_bg() -> None:
    """Calibrare per-CSV (sim_depth optim) într-un thread de fundal."""
    if not STATE["datasets"]:
        ui.notify("Încărcați cel puțin un fișier CSV!", type="negative")
        return
    if STATE.get("calib_status"):
        ui.notify("Calibrare deja în curs.", type="warning")
        return
    STATE["calib_status"] = "⚙️ Calibrare în curs..."
    analysis_panel.refresh()

    def _work() -> None:
        try:
            from calibreaza import run_calibration
            from loto_engine import LotoEngine
            res = {}
            pool = int(SETTINGS["pool_size_val"])
            for fname, df in STATE["datasets"]:
                gl = _game_label_for(fname)
                STATE["calib_status"] = f"⚙️ Calibrare {gl}..."
                eng = LotoEngine(game_type=gl)
                eng.data = df.copy()
                eng._build_draw_matrix()
                best, detail = run_calibration(eng, test_draws=2, pool_size=pool)
                res[gl] = {"best": int(best), "detail": detail}
                SETTINGS["sim_depth_val"] = int(best)
            STATE["calib"] = res
            _save_settings()
            STATE["calib_status"] = ""
        except Exception as exc:  # noqa: BLE001
            STATE["calib_status"] = f"Calibrare eșuată: {exc}"
            logger.error("calibrare: %s", exc)
        finally:
            try:
                analysis_panel.refresh()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_work, daemon=True).start()


def _render_matrix_html(matrix) -> None:
    """Heatmap HTML pentru o matrice (metode × ferestre %), verde = valoare mare."""
    try:
        vmin = float(matrix.values.min())
        vmax = float(matrix.values.max())
    except Exception:  # noqa: BLE001
        return
    span = (vmax - vmin) or 1.0
    cols = list(matrix.columns)
    head = "".join(f"<th style='padding:2px 6px;font-size:0.75em;'>{c}%</th>" for c in cols)
    body = ""
    for method, row in matrix.iterrows():
        cells = ""
        for c in cols:
            v = float(row[c])
            t = (v - vmin) / span  # 0..1
            r = int(220 - 140 * t)
            g = int(80 + 140 * t)
            cells += f"<td style='padding:2px 6px;background:rgb({r},{g},80);color:#111;font-size:0.78em;text-align:center;'>{v:.3f}</td>"
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
                   "quick_rebench": "🟡 Quick re-bench recomandat",
                   "full_rebench": "🔴 Full re-bench recomandat"}.get(rec, rec)
        ui.label(f"Freshness benchmark: {rec_lbl}").classes("text-bold")
        for gk, r in reports.items():
            ui.label(f"  • {gk}: {getattr(r, 'status', '?')} "
                     f"(rânduri {getattr(r,'current_rows','?')} vs cache {getattr(r,'cached_rows','?')})").classes("text-caption")
    except Exception as exc:  # noqa: BLE001
        ui.label(f"Freshness indisponibil ({exc}).").classes("text-caption")

    # --- Decizie benchmark per joc ---
    ui.label("Decizie benchmark (scorer optim per joc):").classes("text-bold mt-2")
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        any_dec = False
        for lbl, gk in GK_MATRIX.items():
            ps = 1 if gk == "joker_urna2" else pool
            cfg = recommend_optimal_config(gk, ps)
            if cfg and not cfg.get("fallback"):
                any_dec = True
                ui.label(f"  • {lbl} (K={ps}): {cfg.get('scorer')} @ {cfg.get('sim_depth_pct')}% "
                         f"(avg {cfg.get('avg_hits', 0):.3f}, BL={cfg.get('use_blacklist')})").classes("text-caption")
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
                    ui.label(f"{lbl} (K={ps}) — top: {s['best_method']} (avg={s['best_mean']:.3f})").classes("text-bold mt-1")
                    _render_matrix_html(s["matrix"])
        else:
            ui.label("Matrice walk-forward indisponibilă — rulează un benchmark.").classes("text-caption")
    except Exception as exc:  # noqa: BLE001
        ui.label(f"Matrice indisponibilă ({exc}).").classes("text-caption")

    # --- Calibrare per-CSV ---
    ui.separator()
    with ui.row().classes("items-center gap-3"):
        ui.button("⚙️ Calibrează AI-ul (sim_depth optim per CSV)", on_click=run_calibration_bg).props("outline")
        if STATE.get("calib_status"):
            ui.label(STATE["calib_status"]).classes("text-info")
    for gl, c in (STATE.get("calib") or {}).items():
        ui.label(f"  • {gl}: sim_depth optim = {c['best']}%").classes("text-caption text-positive")


def _refresh_status() -> None:
    status_panel.refresh()
    logs_panel.refresh()


# --------------------------------------------------------------------------- #
# Pagina principală
# --------------------------------------------------------------------------- #
@ui.page("/")
def main_page() -> None:
    ui.dark_mode().enable()

    with ui.header().classes("items-center justify-between"):
        ui.label("🎰 Loto Enterprise Wheeling").classes("text-h5")
        ui.label("NiceGUI — stare persistentă, fără reload").classes("text-caption")

    # ---- Sidebar (drawer stânga) ----
    with ui.left_drawer(fixed=False).props("width=360 bordered").classes("p-3"):
        ui.label("1. Încărcare Date CSV").classes("text-bold")

        def _on_upload(e) -> None:
            content = e.content.read()
            _persist_uploaded(e.name, content)
            _load_datasets_from_disk()
            ui.notify(f"Încărcat {e.name}.", type="positive")
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

        _bind_save(ui.number("Dimensiune Pool (Nucleu Dur)", min=6, max=24, step=1).classes("w-full"), "pool_size_val")
        _bind_save(ui.number("Garanție minimă (Set Cover)", min=3, max=5, step=1).classes("w-full"), "guarantee_val")
        _bind_save(ui.number("Limită maximă variante (0=nelimitat)", min=0, max=10000, step=10).classes("w-full"), "max_variants_val")
        _bind_save(ui.number("Analizează doar ultimele X% extrageri", min=0, max=100, step=5).classes("w-full"), "lookback_val")
        _bind_save(ui.number("Adâncime Simulare Backtesting (%)", min=10, max=100, step=10).classes("w-full"), "sim_depth_val")
        _bind_save(ui.checkbox("Filtru Anti-Secvență"), "consecutive_filter_val")
        _bind_save(ui.checkbox("🔄 Inversare automată"), "auto_invert_val")
        _bind_save(ui.checkbox("🔌 Oprește PC-ul automat la final"), "shutdown_on_complete")

        ui.separator()
        ui.label("3. Control Execuție").classes("text-bold")
        ui.button("⚡ Auto-Pilot: Decizie Bench + Generează", on_click=apply_autopilot_and_generate).props("color=primary").classes("w-full")
        ui.button("🎯 Auto-Pilot Pure (bench winner + Top N)", on_click=lambda: submit_generation(pure=True)).props("color=secondary outline").classes("w-full")
        ui.button("🚀 Generează cu setările manuale", on_click=lambda: submit_generation(pure=False)).classes("w-full")

        with ui.expansion("🛠️ Re-Bench / Power-User", value=False).classes("w-full"):
            ui.button("🧪 Re-Bench Quick (~5 min)", on_click=run_quick_rebench).classes("w-full")
            ui.button("🔬 Re-Bench Full (~50 min)", on_click=run_full_rebench).classes("w-full")

        ui.separator()
        ui.button("🔴 Anulează TOT Procesul", on_click=cancel_all).props("color=negative outline").classes("w-full")
        ui.button("🗑️ Șterge Log", on_click=lambda: (clear_logs(), logs_panel.refresh())).props("outline").classes("w-full")

    # ---- Zona principală ----
    with ui.column().classes("w-full p-4 gap-2"):
        status_panel()
        with ui.expansion("📈 Analiză & Calibrare (Power-User)", value=False).classes("w-full"):
            analysis_panel()
        with ui.expansion("🛠 Consolă DEBUG / Loguri (live)", value=True).classes("w-full"):
            logs_panel()
        results_panel()

    # ---- Polling fără reload (înlocuiește hack-ul JS window.location.reload) ----
    def _tick() -> None:
        if STATE.get("active_job_id") or _bench_running() or STATE.get("wf_status"):
            status_panel.refresh()
            logs_panel.refresh()
    ui.timer(2.0, _tick)


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _startup() -> None:
    init_job_queue()
    # NU marcăm joburile RUNNING ca eșuate: worker.py e proces separat care
    # supraviețuiește repornirii UI-ului → un job viu trebuie re-atașat, nu omorât.
    _load_settings()
    _load_datasets_from_disk()
    # Re-atașare la un job activ (dacă UI-ul a fost repornit cât rula worker-ul)
    try:
        active = get_active_job()
        if active:
            STATE["active_job_id"] = int(active["id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_job startup: %s", exc)


app.on_startup(_startup)

if __name__ in {"__main__", "__mp_main__"}:
    _port = int(os.environ.get("LOTO_UI_PORT", "8080"))
    ui.run(title="Loto Enterprise Wheeling", port=_port, reload=False, show=True, dark=True)
