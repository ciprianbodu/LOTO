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

# ensure_worker_running() va fi apelat mai jos, dupa configurarea paginii

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("loto.log", encoding="utf-8", mode="a"),
        logging.StreamHandler()
    ]
)

import re

def clear_logs():
    try:
        with open("loto.log", "w", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- Log curățat manual ---\n")
    except Exception:
        pass

def generate_hard_core_description(audit, total_draws, lookback_pct, pool_size, game_type):
    """
    Generează o descriere dinamică a Nucleului Dur bazată pe ce s-a realizat efectiv.
    """
    # Sistem de operare și Python
    py_ver = audit.get('python_version', 'Necunoscut')
    py_path = audit.get('python_executable', 'Necunoscut')
    
    # Selecție Pool
    if 'timesfm_predictions' in audit:
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
        if rf.get('model_used') == 'Google TimesFM (Real)':
            filter_methods.append("Backtesting Regresiv TimesFM")
        else:
            filter_methods.append("Reducere Statistică")
            
    if 'consecutive_filter' in audit:
        filter_methods.append("Filtru Anti-Secvență")
        
    if 'timesfm_excluded' in audit or 'timesfm_blacklist' in audit.get('reduction_filter', {}):
        if "TimesFM" not in "".join(filter_methods):
            filter_methods.append("TimesFM Inactivity Filter")
    
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
    
    # Detalii tehnice suplimentare
    if 'consecutive_filter' in audit and audit['consecutive_filter']:
        description += f"<br>⚙️ **Intervenții:** {len(audit['consecutive_filter'])} modificări anti-secvență"
        
    return description

def read_logs_filtered(n_lines=50):
    if not os.path.exists("loto.log"):
        return "Nu există log-uri încă."
    try:
        with open("loto.log", "r", encoding="utf-8") as f:
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

# Mutat aici pentru a permite paginii sa se randeze partial inainte de blocaje
def ensure_worker_running():
    """Verifică dacă worker.py este activ (pornit acum de scriptul .bat)."""
    # Nu mai pornim worker-ul de aici pentru a evita versiuni vechi ascunse în fundal
    pass

if "worker_checked" not in st.session_state:
    ensure_worker_running()
    st.session_state["worker_checked"] = True

_ST_GLOBAL_CSS = """
<style>
    .loto-container { font-family: 'Inter', sans-serif; padding: 5px; }
    .loto-card {
        margin-bottom: 15px; padding: 12px; border-radius: 10px;
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .loto-header {
        font-weight: bold; text-transform: uppercase;
        color: #17a2b8; margin-bottom: 10px;
    }
    .loto-badge {
        background: linear-gradient(135deg, #1f77b4, #17a2b8);
        color: white; border-radius: 6px; padding: 3px 8px;
        margin: 2px; display: inline-block; font-weight: bold;
    }
    .loto-badge-hc {
        background: #444;
        color: white;
        border: 1px solid rgba(255, 255, 255, 0.8);
        border-radius: 15px;
        padding: 3px 10px;
        margin: 2px;
        display: inline-block;
        font-weight: bold;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    /* Tabel istoric mai strâns */
    [data-testid="stDataFrame"] {
        font-size: 0.8em;
    }
</style>
"""

def reset_sidebar_settings():
    st.session_state["queue_submit_requested"] = False
    st.session_state.pop("active_job_id", None)
    try:
        reset_job_queue()
        clear_pipeline_cache()
        fail_running_jobs()
        unlock_engine()
    except Exception:
        pass

def _decode_queue_result(result_json: str) -> object:
    data = json.loads(result_json)
    blob = base64.b64decode(str(data.get("payload", "")))
    return pickle.loads(blob)

st.markdown(_ST_GLOBAL_CSS, unsafe_allow_html=True)
st.title("Loto Determinist: Sistem Combinatorial (Wheeling)")
st.caption("Analiză hibridă: Google TimesFM (Foundation Model) + Wheeling Combinatorial.")

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
                max_h = max(len(df) for _, df in auto_ds)
                if "lookback_val" not in st.session_state:
                    st.session_state["lookback_val"] = max_h

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
            # Set default lookback to the maximum history found ONLY if files changed
            max_h = max(len(df) for _, df in datasets)
            current_files = [f.name for f in uploaded_files]
            if st.session_state.get("prev_uploaded_files") != current_files:
                st.session_state["lookback_val"] = max_h
                st.session_state["prev_uploaded_files"] = current_files
            st.success(f"Încărcat {len(datasets)} fișiere.")

    st.header("2. Setări Algoritm Wheeling")
    
    pool_size_input = st.number_input(
        "Dimensiune Pool (Nucleu Dur)",
        min_value=7,
        max_value=24,
        value=9,
        step=1,
        help="Câte numere din topul frecvenței să fie folosite."
    )
    
        
    guarantee_input = st.number_input(
        "Garanție minimă (Set Cover)", 
        min_value=3, 
        max_value=5, 
        value=4, 
        step=1,
        help="Garanția matematică: 3, 4 sau 5 numere garantate."
    )
    
    max_variants_input = st.number_input(
        "Limită maxime variante (0 = fără limită)", 
        min_value=0, 
        max_value=10000, 
        value=0, 
        step=10,
        help="Oprește generarea la acest număr de variante (scade din acoperirea Set Cover, dar te încadrează în buget)."
    )
    
    lookback_input = st.number_input(
        "Analizează doar ultimele X% extrageri din istoric (0 = 100% Tot istoricul)", 
        min_value=0, 
        max_value=100, 
        value=0, 
        step=5,
        help="Dacă e 0, analizează toată arhiva (100%). Dacă e N, calculează frecvența doar pe ultimele N% din extrageri.",
        key="lookback_val"
    )

    apply_consecutive_filter = st.checkbox(
        "Filtru Anti-Secvență (Înlocuiește 3+ numere consecutive fără istoric)", 
        value=True, 
        help="Dacă nucleul dur conține 3 numere consecutive care nu au ieșit niciodată împreună, cel mai slab e înlocuit."
    )

    
    key_numbers_count = st.number_input(
        "Număr de Baze (Sistem cu Cheie)",
        min_value=0,
        max_value=3,
        value=0,
        step=1,
        help="Forțează primele N numere din topul Hot în toate variantele. Scade dramatic costul schemei (mai puține bilete), dar bifele alese trebuie neapărat să fie extrase pentru a prinde garanția."
    )

    st.header("3. Control Execuție")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔴 Oprește Forțat", type="secondary", use_container_width=True):
            st.session_state["cancel_requested"] = True
            unlock_engine()
            st.session_state.pop("active_job_id", None)
            st.session_state["queue_submit_requested"] = False
            st.rerun()
            
    with col2:
        if st.button("🗑️ Curăță Log", type="secondary", use_container_width=True):
            clear_logs()
            st.rerun()

    if st.button("🚀 Generează Variante (Wheeling)", type="primary", use_container_width=True):
        reset_sidebar_settings()
        ensure_worker_running()
        st.session_state["queue_submit_requested"] = True



    # --- SECȚIUNE FILTRE INTELIGENTE ---
    st.markdown("---")
    
    # Sistem integrat de reducere (bazat pe Google TimesFM)
    smart_reduction_input = st.checkbox(
        "Sistem de Analiză Google TimesFM (Backtest + Predicție)", 
        value=True, 
        help="Utilizează modelul Google TimesFM pentru a prognoza tendințele și a optimiza nucleul dur de numere."
    )
    
    sim_depth_input = st.slider(
        "Adâncime Simulare Backtesting (%)", 
        min_value=10, 
        max_value=100, 
        value=100, 
        step=10,
        help="Procentul minim din istoric analizat de filtrul regresiv multi-timeframe pentru a depista momentum-ul fals."
    )

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
        h.update(str(pool_size_input).encode("utf-8"))
        h.update(str(guarantee_input).encode("utf-8"))
        h.update(str(max_variants_input).encode("utf-8"))
        h.update(str(lookback_input).encode("utf-8"))
        h.update(str(apply_consecutive_filter).encode("utf-8"))
        h.update(str(smart_reduction_input).encode("utf-8"))
        h.update(str(sim_depth_input).encode("utf-8"))
        h.update(str(key_numbers_count).encode("utf-8"))
        
        for fname, df in st.session_state["loaded_datasets"]:
            game_label = "6/49"
            if "5_40" in fname.lower() or "5/40" in fname.lower():
                game_label = "5/40"
            elif "joker" in fname.lower():
                game_label = "joker"
                
            task_dict = {
                "cfg_key": "LOTO_649", 
                "game_label": game_label,
                "pool_size": pool_size_input,
                "guarantee": guarantee_input,
                "max_variants": max_variants_input,
                "lookback": lookback_input,
                "filter_consecutives": apply_consecutive_filter,
                "smart_reduction": smart_reduction_input,
                "sim_depth_pct": sim_depth_input,
                "key_numbers_count": key_numbers_count
            }
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
            st.session_state.pop("active_job_id", None)
            unlock_engine()
            st.rerun()
            break
            
        if state == "FAILED":
            st.error(f"Eroare: {stt.get('result_json')}")
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

if "persistent_results" in st.session_state:
    res = st.session_state["persistent_results"]
    if isinstance(res, tuple) and len(res) == 2:
        results_bundle, count = res
        st.success("✅ Generare finalizată.")
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
                    
                if 'smart_selector' in audit and audit['smart_selector']:
                    smart_data = audit['smart_selector']
                    st.info(f"🧠 **Smart Selector (Hibrid Avansat):** {smart_data['method']}")
                    
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
                    
                if 'key_numbers' in audit and audit['key_numbers']:
                    st.success(f"🔑 **Sistem cu Cheie (Baze):** Următoarele numere au fost forțate în absolut TOATE variantele: **{audit['key_numbers']}**.")
                
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
                    hc_html += f'<span class="loto-badge-hc" title="A apărut de {hits} ori">{n} <small style="font-size:0.7em; color: white; opacity: 0.9; font-weight: bold;">({pct})</small></span>'
                
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
                    game_type=game
                )
                
                st.markdown(f"**Nucleu Dur:**<br>{hc_html}", unsafe_allow_html=True)
                st.markdown(f"**{dynamic_desc}**", unsafe_allow_html=True)
                
                # --- AFISARE REZULTATE BACKTESTING SUB NUCLEUL DUR ---
                game_type_id = "6/49"
                if "5/40" in game.lower() or "5_40" in game.lower(): game_type_id = "5/40"
                elif "joker" in game.lower(): game_type_id = "joker"
                
                retro_key = f"{fname}_{game_type_id}"
                if "retro_results" in st.session_state and retro_key in st.session_state["retro_results"]:
                    retro_predictions = st.session_state["retro_results"][retro_key]
                    if retro_predictions:
                        total_sims = len(retro_predictions)
                        total_hits = sum(p.hits for p in retro_predictions)
                        avg_hits = total_hits / total_sims
                        best_hit = max(p.hits for p in retro_predictions)
                        
                        # Parametrii joc pentru rată (6/49 are 6 nr, 5/40 are 5 nr)
                        draw_n = 5 if game_type_id in ["5/40", "joker"] else 6
                        avg_rate = (avg_hits / draw_n) * 100
                        
                        # Calcul distribuție
                        dist = {i: 0 for i in range(draw_n + 1)}
                        for p in retro_predictions:
                            if p.hits in dist:
                                dist[p.hits] += 1
                            else:
                                # Protecție pentru cazul Joker (unde pot fi 6 hits pe 5 numere?)
                                # Nu, am pus draw_n=6 pentru Joker in backtesting.py
                                if p.hits > draw_n:
                                    dist[p.hits] = dist.get(p.hits, 0) + 1
                        
                        st.markdown(f"""
                        <div style='background: rgba(10, 25, 40, 0.4); padding: 20px; border-radius: 12px; border: 1px solid rgba(23, 162, 184, 0.3); margin: 15px 0; box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
                            <div style='color: #17a2b8; font-weight: bold; font-size: 1.1em; margin-bottom: 15px; display: flex; align-items: center;'>
                                <span style='margin-right: 10px;'>📊</span> Backtesting — simulare pe {total_sims} extrageri istorice
                            </div>
                        """, unsafe_allow_html=True)
                        
                        m1, m2, m3 = st.columns(3)
                        with m1:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Medie numere ghicite</small><br><strong style='font-size: 1.8em; color: #fff;'>{avg_hits:.2f}</strong></div>", unsafe_allow_html=True)
                        with m2:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Rată medie</small><br><strong style='font-size: 1.8em; color: #17a2b8;'>{avg_rate:.1f}%</strong></div>", unsafe_allow_html=True)
                        with m3:
                            st.markdown(f"<div style='text-align: center;'><small style='color: #aaa;'>Max ghicit</small><br><strong style='font-size: 1.8em; color: #28a745;'>{best_hit}</strong></div>", unsafe_allow_html=True)
                        
                        st.markdown("<div style='margin-top: 20px; font-weight: bold; color: #eee; font-size: 0.9em; margin-bottom: 10px;'>Distribuție rezultate:</div>", unsafe_allow_html=True)
                        
                        # Afișăm distribuția cu bare orizontale
                        for h_count in sorted(dist.keys(), reverse=True):
                            count = dist[h_count]
                            if count == 0 and h_count > 3: continue 
                            
                            pct = (count / total_sims) * 100
                            bar_color = "#28a745" if h_count >= 3 else ("#17a2b8" if h_count >= 1 else "#444")
                            
                            cols = st.columns([2, 8, 2])
                            with cols[0]:
                                st.markdown(f"<div style='font-size: 0.85em; color: #ccc;'>{h_count} numere ghicite</div>", unsafe_allow_html=True)
                            with cols[1]:
                                st.markdown(f"""
                                <div style='background: rgba(255,255,255,0.05); border-radius: 4px; height: 12px; margin-top: 4px; width: 100%;'>
                                    <div style='background: {bar_color}; width: {pct}%; height: 100%; border-radius: 4px;'></div>
                                </div>
                                """, unsafe_allow_html=True)
                            with cols[2]:
                                st.markdown(f"<div style='font-size: 0.85em; text-align: right; color: #fff;'>{count} extrageri ({pct:.0f}%)</div>", unsafe_allow_html=True)
                        
                        # --- NOU: Detalii câștiguri (Minim 3 numere) ---
                        high_hits = [p for p in retro_predictions if p.hits >= 3]
                        if high_hits:
                            st.markdown("<div style='margin-top: 15px; font-weight: bold; color: #17a2b8;'>🎯 Istoric Câștiguri (Minim 3 numere):</div>", unsafe_allow_html=True)
                            hit_data = []
                            # Luăm datele din dataframe-ul original (stocat în session_state dacă e nevoie)
                            # Dar avem draw_index în RetroPrediction. 
                            # Pentru simplitate, afișăm Indexul și Numărul de hits într-un format compact.
                            for h in sorted(high_hits, key=lambda x: x.draw_index, reverse=True):
                                # Încercăm să găsim data în df-ul curent
                                draw_date = "Extragerea #" + str(h.draw_index)
                                hit_data.append({"Data/Index": draw_date, "Hits": f"⭐ {h.hits} numere"})
                            
                            st.dataframe(pd.DataFrame(hit_data), use_container_width=True, height=120, hide_index=True)
                                
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
