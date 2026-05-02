"""Streamlit app for Loto Determinist: Frecvență + Combinatorial Wheeling."""

import base64
import json
import logging
import os
import pickle
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import re

from job_queue import (
    JOB_CANCELLED,
    cancel_pending_running_jobs,
    clear_pipeline_cache,
    fail_running_jobs,
    get_job_status,
    is_job_cancelled,
    reset_job_queue,
    submit_job,
)
from cancel import is_engine_busy, lock_engine, unlock_engine
from loto_enterprise.core.backtesting import LotoBacktester
import subprocess
import psutil
import sys
import streamlit.components.v1 as components

# ensure_worker_running() va fi apelat mai jos, dupa configurarea paginii

import platform
_node = platform.node()
LOG_FILE = f"loto-{_node}.log" if _node else "loto.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a"),
        logging.StreamHandler()
    ]
)

import re

def clear_logs():
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- Log curățat manual ---\n")
    except Exception:
        pass


def _is_worker_running():
    """Verifică dacă există un proces worker.py activ pentru proiectul curent."""
    worker_name = "worker.py"
    project_root = str(Path(__file__).resolve().parent)
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue
            cmd = " ".join(str(part) for part in cmdline)
            if worker_name in cmd and project_root in cmd:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False

def get_hardware_info():
    hw_info = []
    try:
        import platform
        import socket
        node_name = platform.node()
        hw_info.append(f"Statie: {node_name}")
    except Exception:
        pass

    try:
        import psutil
        cpu_cores = psutil.cpu_count(logical=True)
        ram_total_gb = round(psutil.virtual_memory().total / (1024**3), 1)
        hw_info.append(f"CPU: {cpu_cores} nuclee")
        hw_info.append(f"RAM: {ram_total_gb} GB")
    except Exception:
        pass

    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            vram_total_gb = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
            hw_info.append(f"GPU: {gpu_name}")
            hw_info.append(f"VRAM: {vram_total_gb} GB")
        else:
            hw_info.append("GPU: Inactiv (CPU)")
    except Exception:
        hw_info.append("GPU: Nedetectat")
        
    return " | ".join(hw_info) if hw_info else "Informații Hardware indisponibile"

def generate_hard_core_description(audit, total_draws, lookback_pct, pool_size, game_type, resource_stats=None):
    """
    Generează o descriere dinamică a Nucleului Dur bazată pe ce s-a realizat efectiv.
    """
    # Sistem de operare și Python
    py_ver = audit.get('python_version', 'Necunoscut')
    py_path = audit.get('python_executable', 'Necunoscut')
    
    # Selecție Pool
    if 'timesfm_xreg' in audit:
        xreg_info = audit['timesfm_xreg']
        covs = ', '.join(xreg_info.get('dynamic_covariates', []))
        pool_method = f"✅ Google TimesFM v2 (XReg: {covs})"
    elif 'timesfm_predictions' in audit:
        pool_method = "✅ Google TimesFM Forecast (Real Model)"
    elif 'smart_selector' in audit:
        pool_method = "Smart Selector (Hibrid)"
    else:
        pool_method = "⚠️ Fallback: Top Frecvență (Google TimesFM INACTIV)"
        err = audit.get('timesfm_error')
        if err:
            pool_method += f"<br>❌ **Eroare Model:** `{err}`"
        
    # Filtre de Reducție/Blacklist
    filter_methods = []
    if 'reduction_filter' in audit:
        rf = audit['reduction_filter']
        sim_depth = audit.get('sim_depth_pct', rf.get('sim_depth_pct', 40))
        if 'TimesFM' in rf.get('model_used', ''):
            filter_methods.append(f"📉 Backtesting Regresiv TimesFM (Adâncime: {sim_depth}%)")
        else:
            filter_methods.append("Reducere Statistică")
            
    if 'consecutive_filter' in audit:
        filter_methods.append("Filtru Anti-Secvență")
        
    if 'timesfm_excluded' in audit or 'timesfm_blacklist' in audit.get('reduction_filter', {}):
        if "TimesFM" not in "".join(filter_methods):
            filter_methods.append("TimesFM Inactivity Filter")

    if 'anomaly_filter' in audit:
        filter_methods.append("🚀 Neural Anomaly Scoring")
        
    if 'adaptive_bias' in audit or True: # Fiind implementat în engine, îl marcăm
        filter_methods.append("🧠 Adaptive Local Tuning")
    
    # Construire descriere
    desc_parts = []
    
    # Pool size
    desc_parts.append(f"Pool: {pool_size} numere")
    
    # Lookback
    if lookback_pct > 0:
        desc_parts.append(f"Istoric: Ultimele {lookback_pct}% extrageri")
    else:
        desc_parts.append(f"Istoric: Tot istoricul ({total_draws} extrageri)")
    
    # Garanție
    guarantee = audit.get('guarantee', 4)
    desc_parts.append(f"Garanție: {guarantee} numere")
    
    # Variante generate
    variants = audit.get('variants_count', 0)
    if variants > 0:
        desc_parts.append(f"Variante: {variants}")
    
    # Acoperire
    coverage = audit.get('coverage_pct', 100.0)
    desc_parts.append(f"Acoperire: {coverage:.1f}%")
    
    # Formatare finală
    description = " | ".join(desc_parts)
    description += f"<br>🎯 **Metodă Selecție Pool:** {pool_method}"
    if filter_methods:
        description += f"<br>🛡️ **Metode Filtrare:** {', '.join(filter_methods)}"
    
    description += f"<br>🐍 **Mediu Execuție:** Python {py_ver}"
    description += f"<br>📂 **Cale Python:** `{py_path}`"
    description += f"<br>💻 **Hardware Utilizat:** {get_hardware_info()}"
    
    if resource_stats:
        cpu = resource_stats.get('max_cpu', 0)
        ram = resource_stats.get('max_ram', 0)
        gpu = resource_stats.get('max_gpu', 0)
        vram = resource_stats.get('max_vram_gb', 0)
        res_info = f"Max CPU: {cpu}% | Max RAM: {ram}% | Max GPU: {gpu}% | Max VRAM: {vram} GB"
        description += f"<br><span style='color: #94a3b8; font-size: 0.85em;'>📊 **Utilizare Vârf:** {res_info}</span>"
    
    # Detalii Tehnice Google TimesFM
    if 'timesfm_xreg' in audit or 'timesfm_predictions' in audit:
        description += f"<br><br><b>⚙️ Specificații Tehnice Google TimesFM v2:</b>"
        description += f"<br>▪️ <b>Versiune API:</b> timesfm 1.3.0"
        description += f"<br>▪️ <b>Model Foundation:</b> google/timesfm-2.0-500m-pytorch (50 Layers)"
        description += f"<br>▪️ <b>Extreme Regression (XReg):</b> <i>forecast_with_covariates</i> folosind variabile dinamice și statice"
        description += f"<br>▪️ <b>Quantile Uncertainty Analysis:</b> Utilizare a 9 cuantile (p10-p90) pentru determinarea stabilității"
        description += f"<br>▪️ <b>Anomaly Detection:</b> <i>return_forecast_on_context</i> pe {total_draws} extrageri"
        description += f"<br>▪️ <b>Multi-Horizon:</b> H=20 pași cu decaying weight pentru momentum pe termen lung"
    
    # Detalii tehnice suplimentare
    if 'consecutive_filter' in audit and audit['consecutive_filter']:
        description += f"<br><br>🛠️ **Intervenții Secvențiale:** {len(audit['consecutive_filter'])} modificări anti-secvență"
        
    return description

def read_logs_filtered(n_lines=50):
    if not os.path.exists(LOG_FILE):
        return "Nu există log-uri încă."
    try:
        # errors="replace" pentru robustețe: log-ul poate conține linii vechi
        # scrise cu alt encoding (CP1252 din Windows console) pe lângă noile
        # scrieri utf-8. Fără asta, un singur byte 0x9B corupt (de ex. din mojibake
        # "ținte"→"E>inte") oprește complet citirea logului în UI.
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            
        # Extragem ultimele linii
        recent_lines = lines[-n_lines:]
        
        # Filtru anti-spam: maxim 3 mesaje similare (ex. loguri de progres [WHEEL] Iteratia...)
        filtered = []
        pattern = re.compile(r"Iteratia \d+: Acoperite \d+/\d+")
        similar_count = 0
        
        for line in recent_lines:
            if pattern.search(line):
                similar_count += 1
                if similar_count <= 3:
                    filtered.append(line)
                elif similar_count == 4:
                    filtered.append("... [mesaje de progres ascunse pentru claritate] ...\n")
            else:
                similar_count = 0  # reset for different messages
                filtered.append(line)
                
        return "".join(filtered).strip()
    except Exception as e:
        return f"Eroare citire log: {e}"

st.set_page_config(page_title="Loto Wheeling Determinist", layout="wide")

# --- INITIALIZARE STATE ---
if "pool_size_val" not in st.session_state:
    st.session_state["pool_size_val"] = 12
if "guarantee_val" not in st.session_state:
    st.session_state["guarantee_val"] = 4
if "lookback_val" not in st.session_state:
    st.session_state["lookback_val"] = 0
if "sim_depth_val" not in st.session_state:
    st.session_state["sim_depth_val"] = 40
if "sim_depth_suffix" not in st.session_state:
    st.session_state["sim_depth_suffix"] = 0
if "consecutive_filter_val" not in st.session_state:
    st.session_state["consecutive_filter_val"] = True

# Mutat aici pentru a permite paginii sa se randeze partial inainte de blocaje
def ensure_worker_running():
    """Verifică dacă worker.py este activ (pornit acum de scriptul .bat)."""
    # Nu mai pornim worker-ul de aici pentru a evita versiuni vechi ascunse în fundal
    pass

def _ensure_worker_running_runtime():
    if _is_worker_running():
        return

    worker_path = Path(__file__).resolve().with_name("worker.py")
    python_exe = sys.executable
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        subprocess.Popen(
            [python_exe, str(worker_path)],
            cwd=str(worker_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=(os.name != "nt"),
        )
        logging.info("[APP] Worker pornit automat cu interpreterul curent: %s", python_exe)
        time.sleep(1.0)
    except Exception as exc:
        logging.error("[APP] Nu pot porni worker-ul automat: %s", exc)


ensure_worker_running = _ensure_worker_running_runtime

_ST_GLOBAL_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    
    /* Global Styles */
    .stApp {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        font-family: 'Inter', sans-serif;
    }

    /* Glassmorphism Cards */
    .loto-card {
        background: rgba(30, 41, 59, 0.7);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        transition: transform 0.2s ease, border 0.2s ease;
    }
    .loto-card:hover {
        border: 1px solid rgba(23, 162, 184, 0.4);
    }

    /* Headers */
    .loto-header {
        font-size: 0.9rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        color: #38bdf8;
        margin-bottom: 15px;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    /* Badges & Numbers */
    .loto-badge {
        background: linear-gradient(135deg, #0ea5e9, #22d3ee);
        color: white;
        border-radius: 8px;
        padding: 4px 10px;
        margin: 3px;
        display: inline-block;
        font-weight: 600;
        font-size: 0.85rem;
        box-shadow: 0 4px 12px rgba(14, 165, 233, 0.3);
    }
    
    .loto-badge-hc {
        background: rgba(15, 23, 42, 0.8);
        color: #f8fafc;
        border: 1.5px solid #38bdf8;
        border-radius: 50%;
        width: 42px;
        height: 42px;
        line-height: 38px;
        text-align: center;
        margin: 5px;
        display: inline-block;
        font-weight: 700;
        font-size: 1rem;
        box-shadow: 0 0 15px rgba(56, 189, 248, 0.2);
    }

    /* Sidebar Customization */
    [data-testid="stSidebar"] {
        background-color: #0f172a;
        border-right: 1px solid rgba(255, 255, 255, 0.05);
    }

    /* Animations */
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }
    .loto-card { animation: fadeIn 0.5s ease-out forwards; }

    /* Tables */
    [data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid rgba(255, 255, 255, 0.05);
    }
</style>
"""

def reset_sidebar_settings():
    st.session_state["queue_submit_requested"] = False
    st.session_state.pop("active_job_id", None)
    try:
        reset_job_queue()
        clear_pipeline_cache()
        fail_running_jobs("Reset manual din interfață.")
        unlock_engine()
    except Exception:
        pass
    time.sleep(0.5)

def _decode_queue_result(result_json: str) -> object:
    data = json.loads(result_json)
    blob = base64.b64decode(str(data.get("payload", "")))
    return pickle.loads(blob)

st.markdown(_ST_GLOBAL_CSS, unsafe_allow_html=True)

# Main Dashboard Header
st.markdown("""
    <div style="display: flex; align-items: center; gap: 20px; margin-bottom: 30px;">
        <div style="background: linear-gradient(135deg, #0ea5e9, #22d3ee); width: 60px; height: 60px; border-radius: 15px; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 20px rgba(14, 165, 233, 0.4);">
            <svg width="35" height="35" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
        </div>
        <div>
            <h1 style="margin: 0; font-weight: 800; letter-spacing: -1px; color: #f8fafc;">Loto Enterprise <span style="color: #38bdf8;">Wheeling</span></h1>
            <p style="margin: 0; color: #94a3b8; font-size: 1rem;">Neural Foundation: Google TimesFM v2 • XReg Analysis • Quantile Uncertainty</p>
        </div>
    </div>
""", unsafe_allow_html=True)
st.caption("Sistem de analiză hibridă: Google TimesFM v2 + Wheeling Combinatorial Optimizat.")

# Container pentru Mesaje de Calibrare / Status
status_container = st.empty()

# Raportul de calibrare va fi afișat mai jos, sub consolă.

# Verificam daca utilizatorul a pornit calibrarea din sidebar
if st.session_state.get("start_calib_now", False):
    with status_container.container():
        st.markdown("### 🚀 Calibrare în curs...")
        p_bar = st.progress(0, text="⏳ Pas 0: Pregătire motor AI... (Așteaptă ~20 sec)")
        
        import time
        time.sleep(0.5) # Mic delay pentru a forța randarea UI-ului
        
        def update_p(val, msg):
            p_bar.progress(val, text=msg)

        from calibreaza import run_calibration
        from loto_engine import LotoEngine
        
        if "calib_res_dict" not in st.session_state:
            st.session_state["calib_res_dict"] = {}
        if "calib_best_depth_dict" not in st.session_state:
            st.session_state["calib_best_depth_dict"] = {}
            
        p_size_calib = st.session_state["pool_size_val"]
        total_datasets = len(st.session_state["loaded_datasets"])
        
        for idx, (fname, df) in enumerate(st.session_state["loaded_datasets"]):
            g_label = "6/49"
            if "5_40" in fname.lower() or "5/40" in fname.lower(): g_label = "5/40"
            elif "joker" in fname.lower(): g_label = "joker"
            
            def make_update_p(idx, total, g_label):
                def _update(val, msg):
                    overall_progress = (idx / total) + (val / total)
                    p_bar.progress(overall_progress, text=f"Calibrare {g_label}: {msg}")
                return _update
            
            cb = make_update_p(idx, total_datasets, g_label)
            
            calib_engine = LotoEngine(game_type=g_label)
            calib_engine.data = df.copy()
            calib_engine._build_draw_matrix()
            
            best_depth, calib_res = run_calibration(calib_engine, test_draws=2, pool_size=p_size_calib, progress_cb=cb)
            
            st.session_state["calib_res_dict"][g_label] = calib_res
            st.session_state["calib_best_depth_dict"][g_label] = best_depth
            
            # Păstrăm fallback pentru valoarea generală pe prima calibrare
            if idx == 0:
                st.session_state["sim_depth_val"] = best_depth
                
        st.session_state["sim_depth_suffix"] += 1
        st.session_state["start_calib_now"] = False # Oprim flag-ul
        
        if st.session_state.get("trigger_gen_after_calib"):
            st.session_state["trigger_gen_after_calib"] = False
            # Pregătim pentru generare imediată
            reset_sidebar_settings()
            ensure_worker_running()
            st.session_state["queue_submit_requested"] = True
            
        st.rerun()



if "persistent_results" in st.session_state:
    res = st.session_state["persistent_results"]
    if isinstance(res, tuple) and len(res) == 2:
        if "persistent_results_time" in st.session_state:
            elapsed = st.session_state["persistent_results_time"]
            mins = int(elapsed // 60)
            secs = elapsed % 60
            time_str = f"{mins} min și {secs:.1f} sec" if mins > 0 else f"{secs:.1f} secunde"
            st.success(f"✅ Generare finalizată în {time_str}.")
        else:
            st.success("✅ Generare finalizată.")

# --- NOU: Secțiune Vizualizare Istoric CSV ---
if "loaded_datasets" in st.session_state and st.session_state["loaded_datasets"]:
    with st.expander("📅 Sursă Date: Istoric Complet Extrageri (CSV)", expanded=False):
        for fname, df in st.session_state["loaded_datasets"]:
            st.markdown(f"**Fișier: `{fname}`**")
            
            # Pregătim coloanele pentru afișare
            display_df = df.copy()
            cols_to_show = []
            
            # Căutăm data
            date_cols = [c for c in display_df.columns if 'date' in c.lower() or 'data' in c.lower()]
            if date_cols: cols_to_show.append(date_cols[0])
            
            # Căutăm numerele (n1, n2... sau numbers)
            n_cols = [c for c in display_df.columns if re.match(r'^n\d+$', c.lower()) or c.lower() == 'joker']
            if not n_cols and 'numbers' in display_df.columns:
                cols_to_show.append('numbers')
            else:
                cols_to_show.extend(n_cols)
            
            if cols_to_show:
                st.dataframe(
                    display_df[cols_to_show].sort_index(ascending=False),
                    use_container_width=True,
                    height=150, # Mai strâns
                    hide_index=True
                )
            else:
                st.dataframe(display_df, use_container_width=True, height=150, hide_index=True)
        st.info("💡 Tabelul de mai sus arată datele brute folosite de Google TimesFM pentru analiză.")

# === Dashboard Istoric Adaptive Feedback ===
_ADAPTIVE_STATE_FILE = Path(__file__).parent / "adaptive_state.json"
if _ADAPTIVE_STATE_FILE.exists():
    try:
        with _ADAPTIVE_STATE_FILE.open("r", encoding="utf-8") as _f:
            _adaptive_raw = json.load(_f)
    except Exception:
        _adaptive_raw = {}

    if _adaptive_raw:
        with st.expander(f"🧠 Istoric Învățare Adaptivă ({len(_adaptive_raw)} configurări)", expanded=False):
            st.caption(
                "Stare persistentă a sistemului Adaptive Feedback v2 — telemetrie a "
                "evenimentelor (catastrofă/underperf/normal), regime resets și hard inversions "
                "aplicate de-a lungul timpului. Fișier: `adaptive_state.json`."
            )

            for _key in sorted(_adaptive_raw.keys()):
                _entry = _adaptive_raw[_key] or {}
                _hist = _entry.get("history", []) or []
                _rs = _entry.get("regime_state", {}) or {}
                _ecmap = _entry.get("error_correction_map", {}) or {}

                _mode = _rs.get("active_mode", "normal")
                _streak = int(_rs.get("streak_zero", 0) or 0)
                _rdur = int(_rs.get("reset_duration", 0) or 0)
                _last_reset = _rs.get("last_reset")
                _last_pool_date = _entry.get("last_pool_date")

                _events = [str(h.get("event", "?")) for h in _hist]
                _hits = [int(h.get("pool_hits", 0) or 0) for h in _hist]
                _n_cat = sum(1 for e in _events if e == "catastrophe")
                _n_under = sum(1 for e in _events if e == "underperf")
                _n_normal = sum(1 for e in _events if e == "normal")
                _n_hist = len(_events)
                _mean_hits = (sum(_hits) / _n_hist) if _n_hist else 0.0
                _max_h = max(_hits) if _hits else 0

                _mode_color = "#a020f0" if _mode == "reset" else "#28a745"
                _mode_badge = (
                    f"<span style='background:{_mode_color}; color:white; "
                    f"padding:2px 10px; border-radius:6px; font-size:0.8em; "
                    f"margin-left:8px;'>{_mode.upper()}</span>"
                )

                st.markdown(
                    f"#### `{_key}` {_mode_badge}",
                    unsafe_allow_html=True,
                )

                _col1, _col2, _col3, _col4, _col5 = st.columns(5)
                _col1.metric("Total extrageri", _n_hist)
                _col2.metric("Mean hits", f"{_mean_hits:.2f}")
                _col3.metric("Best", _max_h)
                _col4.metric("Catastrofe", _n_cat, delta=f"{(_n_cat/_n_hist*100):.1f}%" if _n_hist else None,
                             delta_color="inverse")
                _col5.metric("Streak zero", _streak,
                             delta=f"{_rdur} reset" if _rdur > 0 else None,
                             delta_color="off")

                _info_bits = []
                if _last_pool_date:
                    _info_bits.append(f"Ultima predicție: **{_last_pool_date}**")
                if _last_reset:
                    _info_bits.append(f"Ultimul regime_reset: **{_last_reset}**")
                if _ecmap:
                    _boosts = sorted(((int(k), float(v)) for k, v in _ecmap.items()),
                                     key=lambda x: x[1], reverse=True)
                    _top_boosts = [f"{n}×{m:.2f}" for n, m in _boosts[:5] if m > 1.0]
                    _top_pen = [f"{n}×{m:.2f}" for n, m in _boosts[-5:] if m < 1.0]
                    if _top_boosts:
                        _info_bits.append(f"↑ Top boost: {', '.join(_top_boosts)}")
                    if _top_pen:
                        _info_bits.append(f"↓ Top penalizare: {', '.join(_top_pen)}")
                if _info_bits:
                    st.markdown(" · ".join(_info_bits))

                if _hits:
                    _chart_df = pd.DataFrame({
                        "draw_index": list(range(1, len(_hits) + 1)),
                        "pool_hits": _hits,
                    }).set_index("draw_index")
                    st.line_chart(_chart_df, height=140)

                    _last_n = min(15, len(_hist))
                    _recent = _hist[-_last_n:]
                    _icons = {
                        "catastrophe": "🔥", "underperf": "⚠️",
                        "normal": "✅", "regime_reset": "🚨",
                    }
                    _seq = " ".join(
                        f"{_icons.get(str(h.get('event','?')), '•')}<sub>{int(h.get('pool_hits',0) or 0)}</sub>"
                        for h in _recent
                    )
                    st.markdown(
                        f"<small>Ultimele {_last_n}: {_seq}</small>",
                        unsafe_allow_html=True,
                    )

                st.markdown("---")

            st.caption(
                f"📈 Sumar global: **{sum(len(e.get('history', []) or []) for e in _adaptive_raw.values())} extrageri** "
                f"învățate, **{sum(1 for e in _adaptive_raw.values() if (e.get('regime_state') or {}).get('active_mode') == 'reset')}** "
                f"configurări curent în mod RESET."
            )

with st.sidebar:
    st.header("1. Încărcare Date CSV")
    uploaded_files = st.file_uploader(
        "Alege fișiere .csv (loto_6_49.csv etc.)",
        type=["csv"],
        accept_multiple_files=True
    )
    
    # Auto-load logic for persistence / refresh on start
    if "loaded_datasets" not in st.session_state:
        local_csvs = [f for f in ["joker.csv", "loto_6_49.csv", "loto_5_40.csv", "input.csv"] if os.path.exists(f)]
        if local_csvs:
            auto_ds = []
            for fpath in local_csvs:
                try:
                    df = pd.read_csv(fpath)
                    auto_ds.append((fpath, df))
                except:
                    pass
            if auto_ds:
                st.session_state["loaded_datasets"] = auto_ds
                st.session_state["loaded_datasets"] = auto_ds

    if uploaded_files:
        datasets = []
        for f in uploaded_files:
            try:
                df = pd.read_csv(f)
                datasets.append((f.name, df))
            except Exception as e:
                st.error(f"Nu pot citi {f.name}: {e}")
        if datasets:
            st.session_state["loaded_datasets"] = datasets
            # Set default lookback to 0 (100% history) ONLY if files changed
            current_files = [f.name for f in uploaded_files]
            if st.session_state.get("prev_uploaded_files") != current_files:
                st.session_state["lookback_val"] = 0
                st.session_state["prev_uploaded_files"] = current_files
            st.success(f"Încărcat {len(datasets)} fișiere.")

    st.header("2. Setări Algoritm Wheeling")
    
    pool_size_input = st.number_input(
        "Dimensiune Pool (Nucleu Dur)",
        min_value=7,
        max_value=24,
        value=st.session_state.get("pool_size_val", 12),
        step=1,
        help="Câte numere din topul frecvenței să fie folosite.",
        key="pool_size_val"
    )
    
        
    guarantee_input = st.number_input(
        "Garanție minimă (Set Cover)", 
        min_value=3, 
        max_value=5, 
        value=st.session_state.get("guarantee_val", 4), 
        step=1,
        help="Garanția matematică: 3, 4 sau 5 numere garantate.",
        key="guarantee_val"
    )
    
    max_variants_input = st.number_input(
        "Limită maxime variante (0 = fără limită)", 
        min_value=0, 
        max_value=10000, 
        value=st.session_state.get("max_variants_val", 0), 
        step=10,
        help="Oprește generarea la acest număr de variante (scade din acoperirea Set Cover, dar te încadrează în buget).",
        key="max_variants_val"
    )
    
    lookback_input = st.number_input(
        "Analizează doar ultimele X% extrageri din istoric (0 = 100% Tot istoricul)", 
        min_value=0, 
        max_value=100, 
        value=st.session_state.get("lookback_val", 0), 
        step=5,
        help="Dacă e 0, analizează toată arhiva (100%). Dacă e N, calculează frecvența doar pe ultimele N% din extrageri.",
        key="lookback_val"
    )

    apply_consecutive_filter = st.checkbox(
        "Filtru Anti-Secvență (Înlocuiește 3+ numere consecutive fără istoric)", 
        value=st.session_state.get("consecutive_filter_val", True), 
        help="Dacă nucleul dur conține 3 numere consecutive care nu au ieșit niciodată împreună, cel mai slab e înlocuit.",
        key="consecutive_filter_val"
    )

    

    st.header("3. Control Execuție")
    
    if "sim_depth_val" not in st.session_state:
        st.session_state["sim_depth_val"] = 40
    if "sim_depth_suffix" not in st.session_state:
        st.session_state["sim_depth_suffix"] = 0

    st.markdown("✨ **Mod Automat (Recomandat)**")
    if st.button("⚡ Auto-Pilot: Calibrează + Generează", type="primary", help="Găsește automat setarea optimă (calibrare) și generează imediat variantele finale. Este cea mai sigură și rapidă opțiune.", use_container_width=True):
        if not st.session_state.get("loaded_datasets"):
            st.error("Încărcați întâi un fișier CSV!")
        else:
            st.session_state["start_calib_now"] = True
            st.session_state["trigger_gen_after_calib"] = True
            st.rerun()

    st.markdown("---")
    st.markdown("🛠️ **Mod Manual (Avansați)**")

    if st.button("⚙️ Pasul 1: Calibrează AI-ul", help="Rulează simulări pe extragerile recente pentru a-ți recomanda cea mai bună valoare pentru adâncimea de mai jos.", use_container_width=True):
        if not st.session_state.get("loaded_datasets"):
            st.error("Încărcați întâi un fișier CSV!")
        else:
            st.session_state["start_calib_now"] = True
            st.rerun()

    if "calib_best_depth_dict" not in st.session_state:
        st.session_state["calib_best_depth_dict"] = {}

    selected_game_depth = st.selectbox(
        "Selectează jocul pentru care setezi adâncimea:",
        ["6/49", "5/40", "joker"],
        index=0,
        help="Alege jocul pentru care dorești să modifici adâncimea de simulare."
    )

    current_val_for_game = st.session_state["calib_best_depth_dict"].get(selected_game_depth, st.session_state.get("sim_depth_val", 40))

    sim_depth_input = st.slider(
        "Adâncime Simulare Backtesting (%)", 
        min_value=10, 
        max_value=100, 
        value=current_val_for_game,
        step=10,
        help="Stabilește cât din istoric analizează AI-ul pentru jocul selectat. (Valoare setată automat dacă folosiți Calibrarea)",
        key=f"sim_depth_key_{st.session_state['sim_depth_suffix']}_{selected_game_depth}"
    )
    st.session_state["calib_best_depth_dict"][selected_game_depth] = sim_depth_input
    st.session_state["sim_depth_val"] = sim_depth_input

    if st.button("🚀 Pasul 2: Generează Variante", use_container_width=True, help="Generează variantele finale folosind valoarea setată manual pe sliderul de mai sus."):
        reset_sidebar_settings()
        ensure_worker_running()
        st.session_state.pop("calib_res", None)
        st.session_state.pop("calib_best_depth", None)
        st.session_state.pop("calib_res_dict", None)
        st.session_state.pop("calib_best_depth_dict", None)
        st.session_state["queue_submit_requested"] = True

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔴 Anulează Proces", type="secondary", help="Oprește forțat orice generare sau calibrare în curs de desfășurare.", use_container_width=True):
            st.session_state["cancel_requested"] = True
            cancel_pending_running_jobs("Job anulat manual din interfață.")
            unlock_engine()
            st.session_state.pop("active_job_id", None)
            st.session_state["queue_submit_requested"] = False
            st.rerun()
            
    with col2:
        if st.button("🗑️ Șterge Log", type="secondary", help="Curăță fișierul de jurnal (log) salvat pe disc.", use_container_width=True):
            clear_logs()
            st.rerun()






if st.session_state.get("queue_submit_requested") and not st.session_state.get("active_job_id"):
    datasets_ok = "loaded_datasets" in st.session_state and st.session_state["loaded_datasets"]
    if not datasets_ok:
        st.error("Încărcați cel puțin un fișier CSV!")
        st.session_state["queue_submit_requested"] = False
    else:
        st.session_state["cancel_requested"] = False
        lock_engine("deterministic_session")
        
        datasets_cfg = []
        import hashlib
        h = hashlib.sha256()
        h.update(str(st.session_state["pool_size_val"]).encode("utf-8"))
        h.update(str(st.session_state["guarantee_val"]).encode("utf-8"))
        h.update(str(st.session_state["max_variants_val"]).encode("utf-8"))
        h.update(str(st.session_state["lookback_val"]).encode("utf-8"))
        h.update(str(st.session_state["consecutive_filter_val"]).encode("utf-8"))
        h.update(str(True).encode("utf-8"))
        h.update(str(st.session_state["sim_depth_val"]).encode("utf-8"))
        
        for fname, df in st.session_state["loaded_datasets"]:
            logging.info(f"[UI] Pregătire task pentru {fname}. Pool size în State: {st.session_state['pool_size_val']}")
            g_label = "6/49"
            if "5_40" in fname.lower() or "5/40" in fname.lower():
                g_label = "5/40"
            elif "joker" in fname.lower():
                g_label = "joker"
                
            # Căutăm adâncimea specifică per joc, sau folosim slider-ul manual
            game_sim_depth = st.session_state["sim_depth_val"]
            if "calib_best_depth_dict" in st.session_state and g_label in st.session_state["calib_best_depth_dict"]:
                game_sim_depth = st.session_state["calib_best_depth_dict"][g_label]
                
            task_dict = {
                "game_label": g_label,
                "pool_size": st.session_state["pool_size_val"],
                "guarantee": st.session_state["guarantee_val"],
                "max_variants": st.session_state["max_variants_val"],
                "lookback": st.session_state["lookback_val"],
                "filter_consecutives": st.session_state["consecutive_filter_val"],
                "smart_reduction": True,
                "sim_depth_pct": game_sim_depth,
            }
            
            logging.info(f"[APP] Submitere job pentru {g_label}: {task_dict}")
            datasets_cfg.append({
                "fname": fname,
                "df_json": df.to_json(orient="split"),
                "tasks": [task_dict]
            })
            h.update(fname.encode("utf-8"))
            
        cfg_json = json.dumps({
            "input_hash": h.hexdigest(),
            "use_cache": False,
            "datasets": datasets_cfg
        })
        
        job_id = submit_job("pipeline", cfg_json)
        st.session_state["active_job_id"] = job_id
        st.session_state["job_start_time"] = time.time()
        st.session_state["queue_submit_requested"] = False
        st.rerun()

if st.session_state.get("active_job_id"):
    job_id = int(st.session_state["active_job_id"])
    st.info(f"⏳ Job în rulare (#{job_id})...")
    prog_bar = st.progress(0)
    status_text = st.empty()
    log_placeholder = st.empty()
    
    for _ in range(600):
        stt = get_job_status(job_id)
        if not stt:
            st.error("Eroare job!")
            break
        
        pct = int(stt.get("progress_pct") or 0)
        state = str(stt.get("status") or "")
        
        prog_bar.progress(max(0, min(100, pct)))
        status_text.text(f"Progres: {pct}%")
        
        # Update live logs during progress
        with log_placeholder.container():
            with st.expander("🛠 Consola DEBUG / Loguri (Live)", expanded=True):
                logs_text = read_logs_filtered(50)
                st.code(logs_text, language="text")
        
        if state == "COMPLETED":
            result_json = str(stt.get("result_json") or "{}")
            payload = _decode_queue_result(result_json)
            st.session_state["persistent_results"] = payload
            if "job_start_time" in st.session_state:
                elapsed = time.time() - st.session_state["job_start_time"]
                st.session_state["persistent_results_time"] = elapsed
            
            # --- NOU: Calcul automat Hits (Backtesting) ---
            if isinstance(payload, tuple) and len(payload) == 2:
                results_bundle, _ = payload
                if "retro_results" not in st.session_state:
                    st.session_state["retro_results"] = {}
                
                for fname, outputs in results_bundle:
                    # Găsim DataFrame-ul original corespunzător fișierului
                    df_source = None
                    if "loaded_datasets" in st.session_state:
                        for ds_name, ds_df in st.session_state["loaded_datasets"]:
                            if ds_name == fname:
                                df_source = ds_df
                                break
                    
                    if df_source is not None:
                        for g_label, data in outputs.items():
                            vars_to_test = data.get("variants", [])
                            if vars_to_test:
                                try:
                                    bt = LotoBacktester(df_source, game_type=g_label)
                                    # Evaluăm pe ultimele 20% (sau cât a ales userul)
                                    lookback = data.get("lookback", 20.0)
                                    if lookback <= 0: lookback = 20.0
                                    
                                    # evaluate_variant returnează o listă de BacktestResult
                                    # Mapăm rezultatele la structura așteptată de UI (retro_predictions)
                                    summary = bt.evaluate_variants(vars_to_test, percentile=lookback)
                                    
                                    # Salvăm în formatul pe care UI îl consumă (listă de obiecte cu .hits)
                                    retro_key = f"{fname}_{g_label}"
                                    st.session_state["retro_results"][retro_key] = summary.all_results
                                except Exception as e:
                                    logging.error(f"Eroare backtesting automat: {e}")

            st.session_state.pop("active_job_id", None)
            st.session_state["play_completion_sound"] = True
            unlock_engine()
            st.rerun()
            break
            
        if state == "FAILED":
            st.error(f"Eroare: {stt.get('result_json')}")
            st.session_state.pop("active_job_id", None)
            unlock_engine()
            st.stop()

        if state == JOB_CANCELLED:
            st.warning("Jobul a fost anulat.")
            st.session_state.pop("active_job_id", None)
            unlock_engine()
            st.stop()
            
        time.sleep(1)

# Loguri statice dupã ce se terminã rularea
if not st.session_state.get("active_job_id"):
    with st.expander("🛠 Consola DEBUG / Loguri", expanded=False):
        col1, col2 = st.columns([4, 1])
        with col1:
            logs_text = read_logs_filtered(50)
            st.code(logs_text, language="text")
        with col2:
            if st.button("🗑️ Reset Console", help="Șterge toate log-urile din consolă"):
                try:
                    with open("loto.log", "w", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- [LOG RESETAT] Consolă resetată manual ---\n")
                    with open("worker_stdout.log", "w", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- [LOG RESETAT] Worker stdout resetat ---\n")
                    with open("worker_stderr.log", "w", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- [LOG RESETAT] Worker stderr resetat ---\n")
                    st.success("✅ Consola a fost resetată!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Eroare la resetarea consolei: {e}")

# Afișăm raportul de calibrare sub consolă
if "calib_res_dict" in st.session_state and st.session_state["calib_res_dict"]:
    for g_label, calib_res in st.session_state["calib_res_dict"].items():
        if g_label in st.session_state["calib_best_depth_dict"]:
            best_depth = st.session_state["calib_best_depth_dict"][g_label]
            current_depth = best_depth
            
            best_stats = calib_res[best_depth]
            
            # Calculăm media per extragere pentru claritate
            test_draws = 2  # Numărul de extrageri de test folosit în calibrare
            avg_excluded = best_stats['total_excluded'] // test_draws if test_draws > 0 else 0
            
            # Construim un raport detaliat sub formă de tabel/listă
            report_lines = [
                f"Adâncimea optimă identificată pentru {g_label.upper()}: **{best_depth}%**",
                f"📊 Eficiență istorică (la {best_depth}%): **{best_stats['total_excluded']} numere moarte eliminate** (total cumulat pe {test_draws} extrageri de test = ~{avg_excluded} numere/extragere).",
                f"🛡️ Siguranță (la {best_depth}%): **{best_stats['total_fatalities']} numere câștigătoare pierdute** (total cumulat pe {test_draws} extrageri)."
            ]
            
            if "eliminated_now" in best_stats and best_stats["eliminated_now"]:
                el_now = best_stats["eliminated_now"]
                report_lines.append(f"🚫 **Numere ce vor fi eliminate ACUM la generare:** {', '.join(map(str, el_now))}")
            elif "eliminated_now" in best_stats:
                report_lines.append(f"🚫 **Numere ce vor fi eliminate ACUM la generare:** *Niciun număr (condiții stricte neîndeplinite).*")
                
            report_lines.extend([
                "",
                "**📊 Analiză comparativă pe ferestre de timp (Intersecție Regresivă):**"
            ])
            
            # Sortăm descrescător pentru a vedea de la cel mai permisiv la cel mai restrictiv
            for d in sorted(calib_res.keys(), reverse=True):
                s = calib_res[d]
                status_icon = "✅" if s['total_fatalities'] == 0 else "❌"

                # Construim tooltip cu datele fatalităților
                fatality_dates = s.get('fatality_dates', [])
                if fatality_dates:
                    tooltip_text = "Fatalități la datele: " + ", ".join(fatality_dates)
                    fatality_html = f"<span title='{tooltip_text}' style='cursor: help; text-decoration: underline dotted; color: #ff6b6b; font-weight: bold;'>{s['total_fatalities']}</span>"
                else:
                    fatality_html = str(s['total_fatalities'])

                # Calculăm media per extragere pentru afișare
                avg_excl = s['total_excluded'] // test_draws if test_draws > 0 else 0
                line = f"- **{d}%** adâncime: {s['total_excluded']} excluse (~{avg_excl}/extragere) | {fatality_html} fatalități {status_icon}"

                # Adăugăm marcajele de UI pentru claritate
                if d == best_depth:
                    line += " ⭐ (Recomandat & Selectat Automat)"

                report_lines.append(line)
                
            report_lines.append("\n*💡 Notă: Valorile 'excluse' și 'fatalități' sunt **totale cumulate** pe extragerile de test. 'O adâncime mai mică (ex: 10%)' înseamnă o intersecție mai strictă (numărul trebuie să fie 'mort' în toate ferestrele simultan pentru a fi eliminat).")
            
            msg = "\n".join(report_lines)
            st.markdown(f"💡 **Raport Calibrare Auto-Tuning: {g_label.upper()}**\n\n{msg}", unsafe_allow_html=True)
# ─── MUZICĂ AMBIENTALĂ + FINALIZARE ──────────────────────────────────────────
_play_completion = st.session_state.pop("play_completion_sound", False)
_fan_js = ""
if _play_completion:
    _fan_js = """
    setTimeout(function() {
        function fanNote(freq, start, dur, vol) {
            vol = vol||0.22;
            var osc=ctx.createOscillator(), g=ctx.createGain();
            osc.type='triangle'; osc.frequency.value=freq;
            g.gain.setValueAtTime(0, ctx.currentTime+start);
            g.gain.linearRampToValueAtTime(vol, ctx.currentTime+start+0.05);
            g.gain.linearRampToValueAtTime(0, ctx.currentTime+start+dur);
            osc.connect(g); g.connect(masterGain); g.connect(reverb);
            osc.start(ctx.currentTime+start); osc.stop(ctx.currentTime+start+dur+0.2);
        }
        [261.6,329.6,392.0,523.3,659.3,783.99].forEach(function(f,i){fanNote(f,i*0.18,0.5);});
        [261.6,329.6,392.0,523.3].forEach(function(f){fanNote(f,6*0.18+0.1,1.8,0.15);});
    }, 900);"""
components.html(f"""<script>
(function(){{
    if(window._lotoAudio) return; window._lotoAudio=true;
    var ctx=new(window.AudioContext||window.webkitAudioContext)();
    var masterGain=ctx.createGain();
    masterGain.gain.setValueAtTime(0,ctx.currentTime);
    masterGain.gain.linearRampToValueAtTime(0.18,ctx.currentTime+3);
    masterGain.connect(ctx.destination);
    var len=ctx.sampleRate*2, buf=ctx.createBuffer(2,len,ctx.sampleRate);
    for(var c=0;c<2;c++){{var d=buf.getChannelData(c);for(var i=0;i<len;i++)d[i]=(Math.random()*2-1)*Math.pow(1-i/len,3);}}
    var reverb=ctx.createConvolver(); reverb.buffer=buf;
    var rvG=ctx.createGain(); rvG.gain.value=0.35; reverb.connect(rvG); rvG.connect(masterGain);
    function pad(f,s,dur,v){{
        v=v||0.04;
        var o=ctx.createOscillator(),g=ctx.createGain();
        o.type='sine'; o.frequency.value=f;
        g.gain.setValueAtTime(0,ctx.currentTime+s);
        g.gain.linearRampToValueAtTime(v,ctx.currentTime+s+2);
        g.gain.linearRampToValueAtTime(0,ctx.currentTime+s+dur);
        o.connect(g); g.connect(masterGain); g.connect(reverb);
        o.start(ctx.currentTime+s); o.stop(ctx.currentTime+s+dur+0.5);
    }}
    var chords=[[220,261.6,329.6,440],[174.6,220,261.6,349.2],[130.8,164.8,196,261.6],[196,246.9,293.7,392]];
    function loop(off){{chords.forEach(function(ch,ci){{ch.forEach(function(f){{pad(f,off+ci*4,5.5,0.04);pad(f*2,off+ci*4+0.5,4,0.015);}});}});}}
    loop(0.5); loop(17); loop(34);
    masterGain.gain.setValueAtTime(0.18,ctx.currentTime+46);
    masterGain.gain.linearRampToValueAtTime(0,ctx.currentTime+51);
    {_fan_js}
}})();
</script>""", height=0)
# ─────────────────────────────────────────────────────────────────────────────

if "persistent_results" in st.session_state:
    res = st.session_state["persistent_results"]
    if isinstance(res, tuple) and len(res) == 2:
        results_bundle, count = res
        
        # Sortăm fișierele pentru a forța ordinea: 6/49 -> joker -> 5/40
        def get_sort_weight(item):
            fname_lower = item[0].lower()
            if "joker" in fname_lower: return 2
            if "5_40" in fname_lower or "5/40" in fname_lower: return 3
            return 1  # 6/49 sau orice altceva e pe primul loc
            
        results_bundle = sorted(results_bundle, key=get_sort_weight)
        
        for fname, outputs in results_bundle:
            st.subheader(f"Fișier: {fname}")
            # Definim ordinea dorită: 6/49, joker, 5/40
            game_order = ["6/49", "joker", "5/40"]
            for game in game_order:
                if game not in outputs:
                    continue
                data = outputs[game]
                st.markdown(f'<div class="loto-card">', unsafe_allow_html=True)
                st.markdown(f'<div class="loto-header">{game.upper()}</div>', unsafe_allow_html=True)
                
                audit = data.get('audit', {})
                
                # Afișăm mesaj pentru sistemul inteligent de reducere
                if 'reduction_filter' in audit and audit['reduction_filter']:
                    reduction_data = audit['reduction_filter']
                    total_blocked = reduction_data.get('total_blocked', 0)
                    combined_blacklist = reduction_data.get('combined_blacklist', [])
                    
                    if total_blocked > 0 and combined_blacklist:
                        timesfm_count = len(reduction_data.get('timesfm_blacklist', []))
                        regressive_count = len(reduction_data.get('regressive_blacklist', []))
                        
                        msg = f"🚫 **Google TimesFM** a optimizat nucleul și a eliminat {total_blocked} numere capcană:\n> "
                        msg += f"Numere blocate: {', '.join([f'**{num}**' for num in sorted(combined_blacklist)])}"
                        
                        if timesfm_count > 0:
                            msg += f"\n\n• TimesFM Forecast (inactive): {timesfm_count} numere"
                        
                        msg += "\n\n*(Explicație: Modelul Google TimesFM a identificat aceste numere ca având un trend descendent sau fiind 'moarte' statistic. Au fost excluse pentru a maximiza șansele nucleului dur.)*"
                        st.warning(msg)
                
                if 'consecutive_filter' in audit and audit['consecutive_filter']:
                    st.warning("⚠️ **Intervenție Filtru Anti-Secvență:**\n" + "\n".join([f"- {m}" for m in audit['consecutive_filter']]))
                
                if 'kept_sequences' in audit and audit['kept_sequences']:
                    st.info("ℹ️ **Verificare Filtru Anti-Secvență:**\n" + "\n".join([f"- {m}" for m in audit['kept_sequences']]))
                
                if 'timesfm_excluded' in audit and audit['timesfm_excluded']:
                    excluded_u1 = audit['timesfm_excluded']
                    str_u1 = ", ".join([f"**{num}** (inactiv {delay}%)" for num, delay in excluded_u1.items()])
                    msg = f"🚫 **Google TimesFM** a exclus {len(excluded_u1)} numere din Urna 1:\n> {str_u1}"
                    
                    if 'timesfm_excluded_joker' in audit and audit['timesfm_excluded_joker']:
                        excluded_u2 = audit['timesfm_excluded_joker']
                        str_u2 = ", ".join([f"**{num}** (inactiv {delay}%)" for num, delay in excluded_u2.items()])
                        msg += f"\n\n🚫 **Google TimesFM** a exclus și {len(excluded_u2)} numere din Urna 2 (Joker):\n> {str_u2}"
                    
                    msg += "\n\n*(Explicație: Algoritmul a identificat aceste numere ca fiind 'inactive/moarte'. Procentajul indică porțiunea relativă din tot istoricul recent în care numărul a absentat complet. Statistic, ele au fost blocate deoarece au cele mai mici șanse de a ieși.)*"
                    st.error(msg)
                    
                if 'anomaly_filter' in audit:
                    af = audit['anomaly_filter']
                    st.success(f"🚀 **Neural Anomaly Scoring:** Din cele {af['original_count']} variante generate inițial, au fost păstrate doar **{af['final_count']}** care respectă distribuția de probabilitate Google TimesFM (Threshold: {af['threshold']}).")
                    
                if 'smart_selector' in audit and audit['smart_selector']:
                    smart_data = audit['smart_selector']
                    st.info(f"🧠 **Neural Hybrid Refinement (Smart Logic):** {smart_data['method']}")
                    
                    kept = smart_data.get('kept_numbers', [])
                    replaced = smart_data.get('replaced_numbers', [])
                    scores = smart_data.get('final_scores', {})
                    
                    if kept:
                        st.markdown(f"✅ **Numere păstrate (top 70%):** {', '.join([f'**{n}** ({scores.get(n, 0):.3f})' for n in kept])}")
                    
                    if replaced:
                        st.markdown(f"🔄 **Numere înlocuite:** {', '.join([f'**{n}**' for n in replaced])}")
                    
                    # Afișăm top scoruri
                    top_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
                    st.markdown("**Top scoruri Smart Selector:**")
                    score_text = " | ".join([f"{n}: {s:.3f}" for n, s in top_scores])
                    st.markdown(f"<small>{score_text}</small>", unsafe_allow_html=True)
                    
                
                context = data.get('context', {})
                first_3 = context.get('first_3', [])
                last_3 = context.get('last_3', [])
                
                if first_3 or last_3:
                    st.markdown("**Verificare CSV (Primele și ultimele 3 extrageri încărcate):**")
                    cols_d = st.columns(2)
                    with cols_d[0]:
                        st.caption("Primele 3 (cele mai vechi / top CSV)")
                        for d in first_3:
                            dt = d.get('date', '')
                            nums = d.get('numbers', [])
                            joker = d.get('joker')
                            
                            html = f"<div style='margin-bottom: 5px;'><small style='color: #aaa; margin-right: 10px;'>{dt}</small>"
                            html += "".join([f'<span class="loto-badge" style="background: #444; font-size: 0.8em; margin-right: 4px;">{int(n)}</span>' for n in nums])
                            if joker is not None:
                                html += f"<span style='margin: 0 5px; font-weight: bold;'>+</span><span class='loto-badge' style='background: #d62728; font-size: 0.8em;'>{int(joker)}</span>"
                            html += "</div>"
                            st.markdown(html, unsafe_allow_html=True)
                            
                    with cols_d[1]:
                        st.caption("Ultimele 3 (cele mai noi / bottom CSV)")
                        for d in last_3:
                            dt = d.get('date', '')
                            nums = d.get('numbers', [])
                            joker = d.get('joker')
                            
                            html = f"<div style='margin-bottom: 5px;'><small style='color: #aaa; margin-right: 10px;'>{dt}</small>"
                            html += "".join([f'<span class="loto-badge" style="background: #444; font-size: 0.8em; margin-right: 4px;">{int(n)}</span>' for n in nums])
                            if joker is not None:
                                html += f"<span style='margin: 0 5px; font-weight: bold;'>+</span><span class='loto-badge' style='background: #d62728; font-size: 0.8em;'>{int(joker)}</span>"
                            html += "</div>"
                            st.markdown(html, unsafe_allow_html=True)
                    st.markdown("<hr style='margin: 10px 0; border-color: rgba(255,255,255,0.1);'>", unsafe_allow_html=True)

                hc = data.get('hard_core', [])
                hc_stats = data.get('hard_core_stats', {})
                total_draws = data.get('total_draws', 1)
                if total_draws == 0: total_draws = 1

                hc_html = ""
                for n in hc:
                    hits = hc_stats.get(str(n), hc_stats.get(n, 0))
                    pct = f"{int((hits / total_draws) * 100)}%" if isinstance(hits, int) else "?"
                    hc_html += f'<span class="loto-badge-hc" title="A apărut de {hits} ori">{n} <small style="font-size:0.7em; color: white; opacity: 0.9; font-weight: bold;">({pct})</small></span> '
                
                hc_joker = data.get('hard_core_joker', [])
                hc_joker_stats = data.get('hard_core_joker_stats', {})
                if hc_joker:
                    hc_html += f" &nbsp; <span style='font-weight: bold;'>+ (Top {len(hc_joker)} Joker):</span> &nbsp; "
                    for n in hc_joker:
                        hits = hc_joker_stats.get(str(n), hc_joker_stats.get(n, 0))
                        pct = f"{int((hits / total_draws) * 100)}%" if isinstance(hits, int) else "?"
                        hc_html += f'<span class="loto-badge-hc" style="background: #600;" title="A apărut de {hits} ori">{n} <small style="font-size:0.7em; color: white; opacity: 0.9; font-weight: bold;">({pct})</small></span>'
                    
                used_lookback = data.get('lookback', 0)
                if used_lookback > 0:
                    lookback_text = f"pe ultimele {used_lookback}% din extrageri ({total_draws} extrageri analizate)"
                else:
                    lookback_text = f"pe tot istoricul ({total_draws} extrageri analizate)"
                
                # Generăm descrierea dinamică
                pool_size = data.get('pool_size', len(hc))
                dynamic_desc = generate_hard_core_description(
                    audit=audit,
                    total_draws=total_draws,
                    lookback_pct=used_lookback,
                    pool_size=pool_size,
                    game_type=game,
                    resource_stats=data.get('resource_stats')
                )
                
                pool_var = audit.get("pool_variation", {})
                if pool_var:
                    if pool_var.get("changed"):
                        added = pool_var.get("added", [])
                        removed = pool_var.get("removed", [])
                        var_html = "<div style='margin-top: 10px; padding: 10px; background: rgba(255,255,255,0.05); border-radius: 8px; font-size: 0.9em;'>"
                        var_html += "<strong>🔄 Dinamica Nucleului (față de ultima rulare):</strong><br>"
                        if added: var_html += f"<span style='color: #28a745;'>➕ Au intrat în top: {', '.join(map(str, added))}</span><br>"
                        if removed: var_html += f"<span style='color: #dc3545;'>➖ Au ieșit din top: {', '.join(map(str, removed))}</span>"
                        var_html += "</div>"
                        hc_html += var_html
                    else:
                        hc_html += "<div style='margin-top: 10px; font-size: 0.85em; color: #6c757d;'>🔄 Nucleul a rămas neschimbat față de ultima rulare pe acest joc.</div>"

                # === Card Învățare Adaptivă (post-extragere feedback) ===
                adaptive_state = audit.get("adaptive_state")
                if adaptive_state:
                    event = adaptive_state.get("event")
                    last_hits = adaptive_state.get("last_hits")
                    streak = adaptive_state.get("streak_zero", 0)
                    mode = adaptive_state.get("active_mode", "normal")
                    rolling = adaptive_state.get("rolling_avg")
                    baseline = adaptive_state.get("baseline", 0.0)
                    boosts = adaptive_state.get("boosts", []) or []
                    penalties = adaptive_state.get("penalties", []) or []
                    missed = adaptive_state.get("missed", []) or []
                    fps = adaptive_state.get("false_positives", []) or []

                    event_meta = {
                        "normal":        ("✅", "#28a745", "Performanță peste baseline"),
                        "underperf":     ("⚠️",  "#ffc107", "Sub baseline (1 hit) — corecție moderată"),
                        "catastrophe":   ("🔥", "#dc3545", "CATASTROFĂ (0 hituri) — corecție amplificată + diversificare"),
                        "regime_reset":  ("🚨", "#a020f0", "REGIM RESETAT — ponderi NQI rebalansate"),
                    }
                    icon, color, msg = event_meta.get(event, ("ℹ️", "#17a2b8", "Fără date pentru comparație"))

                    mode_badge = (
                        f"<span style='background: #a020f0; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; margin-left: 8px;'>RESET</span>"
                        if mode == "reset" else
                        f"<span style='background: #28a745; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; margin-left: 8px;'>NORMAL</span>"
                    )

                    adapt_html = (
                        f"<div style='margin-top: 12px; padding: 14px; background: rgba(20, 30, 50, 0.5); "
                        f"border-left: 4px solid {color}; border-radius: 8px; font-size: 0.9em;'>"
                        f"<div style='font-weight: bold; margin-bottom: 8px;'>{icon} Învățare Adaptivă: {msg} {mode_badge}</div>"
                    )
                    if event is not None:
                        adapt_html += f"<div>Ultima extragere: <strong>{last_hits}</strong> hituri în pool"
                        if baseline:
                            adapt_html += f" <small style='color:#888;'>(baseline aleator: {baseline})</small>"
                        adapt_html += "</div>"
                    if streak >= 1:
                        adapt_html += f"<div>Streak catastrofe consecutive: <strong>{streak}</strong></div>"
                    if rolling is not None:
                        rolling_color = "#dc3545" if rolling < baseline else "#28a745"
                        adapt_html += f"<div>Media rolling (5 extrageri): <strong style='color:{rolling_color};'>{rolling:.2f}</strong></div>"
                    if missed:
                        adapt_html += f"<div style='margin-top:6px; color:#dc3545;'>Numere ratate: {', '.join(map(str, missed))} → boost +X la următoarea predicție</div>"
                    if fps:
                        adapt_html += f"<div style='color:#6c757d;'>Numere prezise dar absente: {', '.join(map(str, fps[:10]))} → penalizare</div>"
                    if boosts:
                        b_str = ", ".join(f"<strong>{n}</strong>×{m:.2f}" for n, m in boosts[:6])
                        adapt_html += f"<div style='margin-top:6px;'><span style='color:#28a745;'>↑ Boost activ:</span> {b_str}</div>"
                    if penalties:
                        p_str = ", ".join(f"<strong>{n}</strong>×{m:.2f}" for n, m in penalties[:6])
                        adapt_html += f"<div><span style='color:#dc3545;'>↓ Penalizare activă:</span> {p_str}</div>"

                    cat_div = audit.get("catastrophe_diversification")
                    if cat_div:
                        inj = cat_div.get("injected", [])
                        ev = cat_div.get("evicted", [])
                        if inj:
                            inj_str = ", ".join(f"{n}<small>(gap×{gr})</small>" for n, gr in inj)
                            ev_str = ", ".join(str(n) for n, _ in ev)
                            adapt_html += (
                                f"<div style='margin-top:6px; color:#f4a261;'>"
                                f"💉 Diversificare forțată: injectate <strong>{inj_str}</strong> "
                                f"în locul lui <strong>{ev_str}</strong></div>"
                            )

                    hard_inv = audit.get("hard_inversion")
                    if hard_inv:
                        excl = hard_inv.get("excluded", [])
                        n_excl = hard_inv.get("n_excluded", len(excl))
                        excl_str = ", ".join(str(n) for n in excl[:20])
                        adapt_html += (
                            f"<div style='margin-top:6px; color:#e63946;'>"
                            f"🚫 Hard Inversion (catastrofă anterioară): "
                            f"<strong>{n_excl}</strong> numere excluse temporar (1 extragere) "
                            f"→ {excl_str}</div>"
                        )
                    adapt_html += "</div>"
                    hc_html += adapt_html

                st.markdown(f"**Nucleu Dur ({len(hc)} numere):**<br>{hc_html}", unsafe_allow_html=True)
                st.markdown(f"**{dynamic_desc}**", unsafe_allow_html=True)

                # --- EVOLUTIA POOL-ULUI PE ETAPE (transparenta pipeline) ---
                stages = audit.get("pipeline_stages") or {}
                if stages:
                    stage_meta = [
                        ("1_nqi_raw",         "1. TimesFM NQI (raw)",       "#60a5fa", "Pool inițial din scorurile NQI v2 (după blacklist + Hard Inversion)."),
                        ("2_smart_selector",  "2. Smart Selector",          "#a78bfa", "Rafinare hibridă: 40% Gap + 25% Trend + 20% Frequency + 15% Positional. Înlocuiește numerele cu scor slab."),
                        ("3_anti_sequence",   "3. Anti-Sequence Filter",    "#f59e0b", "Elimină secvențe de 3+ numere consecutive dacă nu au ieșit în >=1% din extrageri. Înlocuiește cu rezerve de top-frecvență."),
                        ("4_post_hoc_final",  "4. POST-HOC Final",          "#10b981", "Validare retrospectivă pe ultimele extrageri: substituții iterative care maximizează hit-urile 4+/5+/6."),
                    ]
                    with st.expander("🔍 Evoluția Pool-ului — Pipeline Stage-by-Stage", expanded=False):
                        st.caption("Urmărește cum pool-ul e modificat la fiecare etapă a pipeline-ului. Numerele roșii sunt scoase față de etapa precedentă, cele verzi sunt adăugate.")
                        prev_pool: set[int] | None = None
                        for key, title, color, desc in stage_meta:
                            pool_list = stages.get(key)
                            if not pool_list:
                                continue
                            pool_set = set(int(x) for x in pool_list)
                            added_here: set[int] = set()
                            removed_here: set[int] = set()
                            if prev_pool is not None:
                                added_here = pool_set - prev_pool
                                removed_here = prev_pool - pool_set
                            # Pool actual cu highlight pe adăugate
                            chips = []
                            for n in sorted(pool_set):
                                if n in added_here:
                                    chips.append(f"<span style='background:#064e3b;color:#6ee7b7;padding:2px 8px;border-radius:10px;margin:2px;font-weight:bold;'>+{n}</span>")
                                else:
                                    chips.append(f"<span style='background:rgba(255,255,255,0.07);color:#e5e7eb;padding:2px 8px;border-radius:10px;margin:2px;'>{n}</span>")
                            # Numere scoase (arătate inline ca "fantome")
                            removed_chips = [
                                f"<span style='background:#7f1d1d;color:#fecaca;padding:2px 8px;border-radius:10px;margin:2px;text-decoration:line-through;'>−{n}</span>"
                                for n in sorted(removed_here)
                            ]
                            delta_summary = ""
                            if prev_pool is not None:
                                delta_summary = f"<span style='color:#94a3b8;font-size:0.85em;'> (Δ: +{len(added_here)}, −{len(removed_here)})</span>"
                            st.markdown(
                                f"<div style='margin-top:10px;padding:10px;background:rgba(255,255,255,0.03);border-left:3px solid {color};border-radius:4px;'>"
                                f"<div style='font-weight:700;color:{color};'>{title}{delta_summary}</div>"
                                f"<div style='font-size:0.85em;color:#94a3b8;margin:4px 0 8px 0;'>{desc}</div>"
                                f"<div>{' '.join(chips)} {' '.join(removed_chips)}</div>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                            prev_pool = pool_set

                # --- AFISARE REZULTATE BACKTESTING SUB NUCLEUL DUR ---
                game_type_id = "6/49"
                if "5/40" in game.lower() or "5_40" in game.lower(): game_type_id = "5/40"
                elif "joker" in game.lower(): game_type_id = "joker"
                
                retro_key = f"{fname}_{game_type_id}"
                if "retro_results" in st.session_state and retro_key in st.session_state["retro_results"]:
                    retro_predictions = st.session_state["retro_results"][retro_key]
                    if retro_predictions:
                        # Calculăm numărul de extrageri unice și numărul de variante
                        unique_draws = len(set(p.draw_index for p in retro_predictions))
                        total_variants = len(set(tuple(p.variant) for p in retro_predictions))
                        total_sims = len(retro_predictions)
                        
                        total_hits = sum(p.hits for p in retro_predictions)
                        avg_hits = total_hits / total_sims
                        best_hit = max(p.hits for p in retro_predictions)
                        best_pool_hit = max(getattr(p, 'hits_union', 0) for p in retro_predictions)
                        
                        # Parametrii joc pentru rată (6/49 are 6 nr, 5/40 are 5 nr)
                        draw_n = 5 if game_type_id in ["5/40", "joker"] else 6
                        avg_rate = (avg_hits / draw_n) * 100
                        
                        # Calcul distribuție Variante (succes individual per bilet)
                        variant_dist = {i: 0 for i in range(draw_n + 1)}
                        for p in retro_predictions:
                            h = p.hits
                            if h in variant_dist:
                                variant_dist[h] += 1
                            else:
                                if h > draw_n:
                                    variant_dist[h] = variant_dist.get(h, 0) + 1
                                    
                        # Calcul distribuție Pool (succes colectiv Nucleu Dur)
                        # Luăm doar un rezultat per extragere pentru Pool
                        pool_dist = {i: 0 for i in range(draw_n + 1)}
                        seen_draws_dist = set()
                        for p in retro_predictions:
                            if p.draw_index not in seen_draws_dist:
                                seen_draws_dist.add(p.draw_index)
                                h_u = getattr(p, 'hits_union', 0)
                                if h_u in pool_dist:
                                    pool_dist[h_u] += 1
                                else:
                                    if h_u > draw_n:
                                        pool_dist[h_u] = pool_dist.get(h_u, 0) + 1
                        total_draws_eval = len(seen_draws_dist)
                        
                        st.markdown(f"""
                        <div style='background: rgba(10, 25, 40, 0.4); padding: 20px; border-radius: 12px; border: 1px solid rgba(23, 162, 184, 0.3); margin: 15px 0; box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
                            <div style='color: #17a2b8; font-weight: bold; font-size: 1.1em; margin-bottom: 15px; display: flex; align-items: center;'>
                                <span style='margin-right: 10px;'>📊</span> Analiză Performanță: {total_variants} variante verificate pe ultimele {unique_draws} extrageri
                            </div>
                        """, unsafe_allow_html=True)
                        
                        m1, m2, m3, m4, m5 = st.columns(5)
                        total_union_hits = sum(getattr(p, 'hits_union', 0) for p in retro_predictions)
                        avg_union_hits = total_union_hits / total_sims
                        
                        with m1:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Medie / Variantă</small><br><strong style='font-size: 1.4em; color: #fff;'>{avg_hits:.2f}</strong></div>", unsafe_allow_html=True)
                        with m2:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Medie / Pool</small><br><strong style='font-size: 1.4em; color: #e9c46a;'>{avg_union_hits:.2f}</strong></div>", unsafe_allow_html=True)
                        with m3:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Rată medie</small><br><strong style='font-size: 1.4em; color: #17a2b8;'>{avg_rate:.1f}%</strong></div>", unsafe_allow_html=True)
                        with m4:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Max Variantă</small><br><strong style='font-size: 1.4em; color: #28a745;'>{best_hit}</strong></div>", unsafe_allow_html=True)
                        with m5:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Max Pool</small><br><strong style='font-size: 1.4em; color: #f4a261;'>{best_pool_hit}</strong></div>", unsafe_allow_html=True)
                        
                        st.markdown("<div style='margin-top: 20px; font-weight: bold; color: #f4a261; font-size: 0.9em; margin-bottom: 10px;'>📊 Distribuție Nucleu Dur (Câte numere au fost în pool):</div>", unsafe_allow_html=True)
                        
                        # Afișăm distribuția Pool cu bare orizontale
                        for h_count in sorted(pool_dist.keys(), reverse=True):
                            count = pool_dist[h_count]
                            if count == 0 and h_count > 3: continue 
                            
                            pct = (count / total_draws_eval) * 100 if total_draws_eval > 0 else 0
                            bar_color = "#f4a261" if h_count >= 4 else ("#e9c46a" if h_count >= 3 else "#444")
                            
                            cols = st.columns([2, 8, 2])
                            with cols[0]:
                                st.markdown(f"<div style='font-size: 0.85em; color: #ccc;'>{h_count} numere în Pool</div>", unsafe_allow_html=True)
                            with cols[1]:
                                st.markdown(f"""
                                <div style='background: rgba(255,255,255,0.05); border-radius: 4px; height: 12px; margin-top: 4px; width: 100%;'>
                                    <div style='background: {bar_color}; width: {pct}%; height: 100%; border-radius: 4px;'></div>
                                </div>
                                """, unsafe_allow_html=True)
                            with cols[2]:
                                st.markdown(f"<div style='font-size: 0.85em; text-align: right; color: #fff;'>{count} extrageri ({pct:.0f}%)</div>", unsafe_allow_html=True)
                        
                        # --- NOU: Detalii Performanță Pool (AI Hard Core) ---
                        pool_hits = []
                        # Folosim un set de indecși pentru a raporta o singură dată per extragere
                        seen_draws = set()
                        
                        # Sortăm descrescător după hits_union (cele mai bune pool-uri sus)
                        for h in sorted(retro_predictions, key=lambda x: (getattr(x, 'hits_union', 0), getattr(x, 'draw_index', 0)), reverse=True):
                            u_hits = getattr(h, 'hits_union', 0)
                            d_idx = getattr(h, 'draw_index', 0)
                            
                            if u_hits >= 4 and d_idx not in seen_draws:
                                d_date = getattr(h, 'draw_date', getattr(h, 'target_draw_date', None))
                                label = d_date if d_date and str(d_date) != "None" else f"Extragerea #{d_idx}"
                                pool_hits.append({"Data / Extragere": label, "Numere în Nucleu": f"🔥 {u_hits} numere"})
                                seen_draws.add(d_idx)
                        
                        if pool_hits:
                            st.markdown("<div style='margin-top: 15px; font-weight: bold; color: #f4a261;'>🎯 Istoric Performanță Pool (Nucleu Dur - Minim 4 nr):</div>", unsafe_allow_html=True)
                            st.dataframe(pd.DataFrame(pool_hits), use_container_width=True, height=110, hide_index=True)

                        # --- Detalii câștiguri Variante (Minim 3 numere) ---
                        high_hits = [p for p in retro_predictions if p.hits >= 3]
                        if high_hits:
                            st.markdown("<div style='margin-top: 15px; font-weight: bold; color: #17a2b8;'>🎯 Istoric Câștiguri Variante (Bilete - Minim 3 nr):</div>", unsafe_allow_html=True)
                            hit_data = []
                            total_prize = 0
                            
                            # Estimări premii fixe Loto România
                            prize_map = {
                                "6/49": {3: 30, 4: 300, 5: 30000, 6: 1000000},
                                "5/40": {3: 50, 4: 500, 5: 50000, 6: 0},
                                "joker": {3: 60, 4: 600, 5: 60000, 6: 1000000}
                            }
                            
                            # Sortăm mai întâi după numărul de ghicite (descrescător) pentru a pune câștigurile mari sus, apoi după dată
                            for h in sorted(high_hits, key=lambda x: (x.hits, getattr(x, 'draw_index', 0)), reverse=True):
                                # Polimorfism: BacktestResult are .draw_date, RetroactivePrediction are .target_draw_date
                                d_date = getattr(h, 'draw_date', getattr(h, 'target_draw_date', None))
                                d_idx = getattr(h, 'draw_index', 0)
                                label = d_date if d_date and str(d_date) != "None" else f"Extragerea #{d_idx}"
                                p_val = prize_map.get(game_type_id, prize_map["6/49"]).get(h.hits, 0)
                                total_prize += p_val
                                hit_data.append({"Data / Extragere": label, "Hits": f"⭐ {h.hits} numere", "Est. Premiu (Lei)": f"~{p_val} Lei"})
                            
                            st.dataframe(pd.DataFrame(hit_data), use_container_width=True, height=120, hide_index=True)
                            
                            if 'get_live_prices' not in locals():
                                import requests
                                @st.cache_data(ttl=3600)
                                def get_live_prices():
                                    prices = {"6/49": 8.0, "5/40": 5.0, "joker": 7.0}
                                    try:
                                        headers = {'User-Agent': 'Mozilla/5.0'}
                                        req = requests.get('https://www.loto.ro/', headers=headers, timeout=5)
                                        if req.status_code == 200: pass
                                    except Exception: pass
                                    return prices
                                    
                            cost_per_var = get_live_prices().get(game_type_id, 8.0)
                            total_cost = total_variants * cost_per_var * unique_draws
                            profit = total_prize - total_cost
                            roi_pct = (profit / total_cost * 100) if total_cost > 0 else 0
                            roi_color = "#28a745" if profit >= 0 else "#dc3545"
                            sign = "+" if profit >= 0 else ""
                            
                            st.markdown(f"""
                            <div style='background: rgba(255,255,255,0.05); padding: 10px; border-radius: 8px; margin-top: 10px;'>
                                <div style='font-size: 0.9em; color: #aaa;'>Analiză Financiară Backtesting ({unique_draws} extrageri):</div>
                                <div style='display: flex; justify-content: space-between; align-items: center; margin-top: 5px;'>
                                    <div>Cost estimat: <strong>{total_cost:,.0f} Lei</strong></div>
                                    <div>Premii estimate: <strong>{total_prize:,.0f} Lei</strong></div>
                                    <div style='color: {roi_color}; font-size: 1.2em; font-weight: bold;'>ROI: {sign}{roi_pct:.1f}%</div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                                
                        st.markdown("</div>", unsafe_allow_html=True)

                
                import math
                import requests
                import re
                
                @st.cache_data(ttl=3600)
                def get_live_prices():
                    prices = {"6/49": 8.0, "5/40": 5.0, "joker": 7.0}
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0'}
                        req = requests.get('https://www.loto.ro/', headers=headers, timeout=5)
                        if req.status_code == 200:
                            pass
                    except Exception:
                        pass
                    return prices

                prices = get_live_prices()
                game_key = "6/49"
                if "5/40" in game.lower() or "5_40" in game.lower(): game_key = "5/40"
                elif "joker" in game.lower(): game_key = "joker"
                
                price_per_var = prices[game_key]
                draw_n = 5 if game_key == "5/40" else (5 if game_key == "joker" else 6)
                
                pool_size_used = data.get('pool_size', len(hc))
                full_system_vars = math.comb(pool_size_used, draw_n)
                full_cost = full_system_vars * price_per_var
                
                # Scheme reduse oficiale Loteria Romana (estimativ variante pt 6/49 si 5/40)
                lr_schemes = {
                    "6/49": {
                        9: [("Cod 48", 12)],
                        10: [("Cod 49", 15), ("Cod 50", 30)],
                        11: [("Cod 56", 66)],
                        12: [("Cod 57", 22), ("Cod 58", 132)],
                        16: [("Cod 59", 112)]
                    },
                    "5/40": {
                        7: [("Cod 15", 9)],
                        8: [("Cod 16", 21)],
                        9: [("Cod 17", 30)],
                        10: [("Cod 18", 51)]
                    },
                    "joker": {
                        7: [("Cod 45", 5)],
                        8: [("Cod 35", 6)],
                        9: [("Cod 34", 9)],
                        10: [("Cod 24", 14)],
                        11: [("Cod 15", 22)],
                        12: [("Cod 14", 38)]
                    }
                }
                
                if game_key in lr_schemes and pool_size_used in lr_schemes[game_key]:
                    sc_list = lr_schemes[game_key][pool_size_used]
                    sc_texts = []
                    joker_mult = max(1, len(data.get('hard_core_joker', []))) if game_key == "joker" else 1
                    
                    for code_name, base_vars in sc_list:
                        total_vars = base_vars * joker_mult
                        if joker_mult > 1:
                            sc_texts.append(f"**{code_name}** ({base_vars} var. × {joker_mult} Jokeri = {total_vars} var. totale = **~{total_vars * price_per_var:,.0f} Lei**)")
                        else:
                            sc_texts.append(f"**{code_name}** ({total_vars} var. = **~{total_vars * price_per_var:,.0f} Lei**)")
                            
                    sc_final_str = " sau ".join(sc_texts)
                    
                    st.info(f"💡 **Cost Nucleu Dur la Agenție:** Dacă joci aceste {pool_size_used} numere la {game.upper()} folosind o schemă redusă oficială, ai următoarele opțiuni:\n\n👉 {sc_final_str}.\n\n*(Spre comparație, Varianta Extinsă/Sistemul Complet ar costa ~{full_cost * joker_mult:,.0f} Lei)*")
                else:
                    joker_mult = max(1, len(data.get('hard_core_joker', []))) if game_key == "joker" else 1
                    st.info(f"💡 **Cost Nucleu Dur la Agenție:** Loteria Română **nu** are o schemă redusă predefinită (sau documentată de noi) pentru un nucleu de {pool_size_used} numere la {game.upper()}. Dacă joci acest nucleu ca Sistem Complet (Variantă Extinsă), biletul va avea {full_system_vars * joker_mult} variante și va costa **~{full_cost * joker_mult:,.0f} Lei**.")
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # Tracking eliminated as requested by user. 
                # Hits are calculated live during retroactive backtesting.
                variants = data.get('variants', [])

                
                cov_pct = context.get('coverage_pct', 100.0)
                if cov_pct < 100.0:
                    cov_html = f"<span style='color: #ffcc00;'>{cov_pct}% (Limitată)</span>"
                else:
                    cov_html = f"<span style='color: #28a745;'>{cov_pct}% (Completă)</span>"
                    
                my_vars = len(variants)
                my_cost = my_vars * price_per_var
                
                # Buton de toggle pentru variante (stil V rotit)
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"**Variante generate de noi ({my_vars})** &nbsp; | &nbsp; **Cost estimat:** ~{my_cost:,.0f} Lei")
                    st.caption(f"Acoperire matematică Garanție: {cov_html}", unsafe_allow_html=True)
                with col2:
                    # Inițializăm starea în session_state dacă nu există
                    if f'show_variants_{game}' not in st.session_state:
                        st.session_state[f'show_variants_{game}'] = False
                    
                    # Buton V rotit pentru toggle
                    button_label = "🔽" if not st.session_state[f'show_variants_{game}'] else "🔼"
                    if st.button(button_label, key=f'toggle_variants_{game}', help="Afișează/Ascunde variantele"):
                        st.session_state[f'show_variants_{game}'] = not st.session_state[f'show_variants_{game}']
                        st.rerun()

                # --- NOU: Distribuție variante integrată în plan ---
                if "retro_results" in st.session_state and retro_key in st.session_state["retro_results"]:
                    st.markdown("<div style='margin: 10px 0; padding: 10px; background: rgba(255,255,255,0.03); border-radius: 8px;'>", unsafe_allow_html=True)
                    st.markdown("<div style='font-size: 0.85em; font-weight: bold; color: #17a2b8; margin-bottom: 8px;'>📊 Distribuție Performanță Variante (Bilete):</div>", unsafe_allow_html=True)
                    
                    # Calculăm total_evaluations pentru procente corecte (variante x extrageri)
                    total_evals = len(retro_predictions)
                    for h_count in sorted(variant_dist.keys(), reverse=True):
                        count = variant_dist[h_count]
                        if count == 0 and h_count > 3: continue 
                        
                        pct = (count / total_evals) * 100 if total_evals > 0 else 0
                        bar_color = "#28a745" if h_count >= 3 else ("#17a2b8" if h_count >= 1 else "#444")
                        
                        vcols = st.columns([3, 7, 2])
                        with vcols[0]:
                            st.markdown(f"<div style='font-size: 0.8em; color: #ccc;'>{h_count} numere ghicite</div>", unsafe_allow_html=True)
                        with vcols[1]:
                            st.markdown(f"""
                            <div style='background: rgba(255,255,255,0.05); border-radius: 3px; height: 8px; margin-top: 5px; width: 100%;'>
                                <div style='background: {bar_color}; width: {pct}%; height: 100%; border-radius: 3px;'></div>
                            </div>
                            """, unsafe_allow_html=True)
                        with vcols[2]:
                            st.markdown(f"<div style='font-size: 0.8em; text-align: right; color: #fff;'>{pct:.1f}%</div>", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)
                
                # Afișăm variantele doar dacă sunt vizibile
                if st.session_state[f'show_variants_{game}']:
                    for i, v in enumerate(variants, 1):
                        if len(v) == 6 and 'joker' in game.lower():
                            # Primele 5 sunt urna 1, ultimul este urna 2
                            v_html = "".join([f'<span class="loto-badge">{n}</span>' for n in v[:5]])
                            v_html += f" &nbsp; <span style='font-weight: bold;'>+</span> &nbsp; <span class='loto-badge' style='background: #d62728;'>{v[-1]}</span>"
                        else:
                            v_html = "".join([f'<span class="loto-badge">{n}</span>' for n in v])
                        st.markdown(f"**V{i}:** {v_html}", unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
