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


def _wf_metrics(flat) -> dict:
    """Agregă metrici simple din rezultatele walk-forward."""
    if not flat:
        return {}
    pool_hits = [getattr(r, "hits_union", getattr(r, "hits", 0)) for r in flat]
    var_hits = [getattr(r, "hits", 0) for r in flat]
    n = len(flat)
    return {
        "n": n,
        "avg_pool": sum(pool_hits) / n if n else 0,
        "best_pool": max(pool_hits) if pool_hits else 0,
        "avg_var": sum(var_hits) / n if n else 0,
        "best_var": max(var_hits) if var_hits else 0,
    }


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
            ui.label("✅ Generare finalizată.").classes("text-positive text-lg")
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

    ui.label("Gata de lucru. Încarcă CSV-uri și apasă Generează / Auto-Pilot.").classes("text-caption")


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

    ui.label(f"Rezultate{elapsed}").classes("text-h6 mt-2")

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

                    # Walk-forward backtest
                    flat = STATE["retro"].get(f"{fname}_{game}")
                    if flat:
                        m = _wf_metrics(flat)
                        with ui.row().classes("gap-6 mt-2"):
                            ui.label(f"WF extrageri test: {m.get('n')}")
                            ui.label(f"Avg pool hits: {m.get('avg_pool'):.2f}")
                            ui.label(f"Best pool: {m.get('best_pool')}")
                            ui.label(f"Avg variantă: {m.get('avg_var'):.2f}")
                            ui.label(f"Best variantă: {m.get('best_var')}")

                    # Top 10 variante simple
                    if variants:
                        with ui.expansion(f"Variante ({len(variants)}) — top 10", value=False).classes("w-full"):
                            for i, v in enumerate(variants[:10], 1):
                                nums = ", ".join(str(int(x)) for x in v)
                                ui.label(f"{i:>2}. {nums}").classes("font-mono text-sm")
                            if len(variants) > 10:
                                ui.label(f"... încă {len(variants) - 10} variante (cost ~{len(variants)*5} Lei).").classes("text-caption")

                    # Audit (rezumat)
                    audit = data.get("audit")
                    if audit:
                        with ui.expansion("🔍 Audit pipeline", value=False).classes("w-full"):
                            ui.code(json.dumps(audit, indent=2, ensure_ascii=False, default=str),
                                    language="json").classes("w-full max-h-80 overflow-auto text-xs")


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
    ui.run(title="Loto Enterprise Wheeling", port=8080, reload=False, show=False, dark=True)
