"""Background worker daemon consuming jobs from SQLite queue (Deterministic Version)."""

from __future__ import annotations

import base64
import io
import json
import logging
import pickle
import sys
import time
import traceback
import tempfile
import os

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("loto.log", encoding="utf-8", mode="a"),
        logging.StreamHandler(sys.stdout)
    ]
)

import pandas as pd

from job_queue import (
    JOB_RUNNING,
    JOB_CANCELLED,
    complete_job,
    fail_job,
    fetch_pending_job,
    fetch_running_job,
    get_pipeline_cache,
    is_job_cancelled,
    put_pipeline_cache,
    requeue_running_jobs,
    update_job_progress,
)

from loto_engine import LotoEngine

# Windows console safety: evită crash pe diacritice/Unicode în print-uri din stack.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    logging.debug("UTF-8 console reconfigure eșuat; continuu cu encoding implicit.", exc_info=True)


def _pack_result_payload(payload: object) -> str:
    blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    return json.dumps({"encoding": "pickle+b64", "payload": base64.b64encode(blob).decode("ascii")})


def _run_pipeline_job(job: dict) -> str:
    cfg = json.loads(job["config_json"])
    input_hash = str(cfg.get("input_hash", "") or "").strip()
    use_cache = bool(cfg.get("use_cache", True))
    datasets_cfg = list(cfg.get("datasets", []))
    job_id = int(job["id"])

    total_steps = max(1, sum(len(d.get("tasks", [])) for d in datasets_cfg))
    step_idx = 0
    results_bundle = []

    if use_cache and input_hash:
        cached = get_pipeline_cache(input_hash)
        if cached:
            update_job_progress(job_id, 100, "Cache hit: rezultat reutilizat (hash CSV identic).")
            return str(cached)

    for ds in datasets_cfg:
        fname = str(ds.get("fname", "dataset.csv"))
        df = pd.read_json(io.StringIO(str(ds["df_json"])), orient="split")
        outputs = {}
        
        # Salvăm un fișier temporar pentru a-l încărca cu engine-ul
        temp_csv_path = ""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", encoding="utf-8") as tmp:
            df.to_csv(tmp.name, index=False)
            temp_csv_path = tmp.name

        for task in ds.get("tasks", []):
            game_label = str(task["game_label"])
            p_size = int(task.get("pool_size", 12))
            guar = int(task.get("guarantee", 4))
            max_var = int(task.get("max_variants", 0))
            lookback = int(task.get("lookback", 0))
            filter_cons = bool(task.get("filter_consecutives", True))
            cold_pct = int(task.get("cold_percent", 0))
            
            def progress_cb(msg, pct):
                overall_pct = int(((step_idx + (pct / 100.0)) / total_steps) * 95)
                update_job_progress(job_id, overall_pct, f"[{fname}][{game_label}] {msg}")

            try:
                game_mapped = "6/49"
                if "5/40" in game_label.lower():
                    game_mapped = "5/40"
                elif "joker" in game_label.lower():
                    game_mapped = "joker"
                    
                engine = LotoEngine(game_type=game_mapped)
                engine.load_data(temp_csv_path)
                lines, p10, p90, g_range, context, audit = engine.run_institutional_pipeline(
                    progress_cb=progress_cb, 
                    pool_size=p_size, 
                    guarantee=guar, 
                    max_variants=max_var, 
                    lookback=lookback,
                    filter_consecutives=filter_cons,
                    cold_percent=cold_pct
                )
                
                outputs[game_label] = {
                    "total_draws": len(engine.data) if engine.data is not None else 0,
                    "hard_core": engine.hard_core,
                    "hard_core_stats": getattr(engine, 'hard_core_stats', {}),
                    "hard_core_joker": getattr(engine, 'hard_core_joker', []),
                    "hard_core_joker_stats": getattr(engine, 'hard_core_joker_stats', {}),
                    "variants": lines,
                    "pool_size": p_size,
                    "guarantee": guar,
                    "lookback": lookback,
                    "audit": audit,
                    "p10": p10,
                    "p90": p90,
                    "g_range": g_range,
                    "context": context
                }
            except Exception as e:
                logging.error(f"Eroare la procesarea task-ului {game_label}: {e}")
            finally:
                step_idx += 1

        if os.path.exists(temp_csv_path):
            os.remove(temp_csv_path)
            
        results_bundle.append((fname, outputs))

    update_job_progress(job_id, 99, "Pregătesc rezultatul final pentru UI...")
    persistent = (results_bundle, len(results_bundle))
    packed = _pack_result_payload(persistent)
    if use_cache and input_hash:
        put_pipeline_cache(input_hash, packed)
    return packed


def main() -> None:
    try:
        recovered = requeue_running_jobs()
        if recovered > 0:
            print(f"[worker] Recuperate {recovered} job(uri) RUNNING -> PENDING după restart.", flush=True)
    except Exception as exc:
        logging.debug("Nu pot requeue joburile RUNNING la startup worker: %s", exc)
    logging.info("[worker] Început loop principal - aștept job-uri...")
    
    while True:
        try:
            job = fetch_pending_job()
            if not job:
                job = fetch_running_job()
            if not job:
                time.sleep(2)
                continue
            if job.get("status") != JOB_RUNNING:
                time.sleep(2)
                continue
                
            task_type = str(job.get("task_type") or "")
            job_id = int(job["id"])
            update_job_progress(job_id, 2, "Job preluat de worker.")

            if is_job_cancelled(job_id):
                logging.info(f"[worker] Job {job_id} a fost anulat (CANCELLED), sarim.")
                continue

            if task_type == "pipeline":
                result_json = _run_pipeline_job(job)
            else:
                fail_job(job_id, f"Unsupported task type: {task_type}")
                continue

            if is_job_cancelled(job_id):
                logging.info(f"[worker] Job {job_id} anulat în timpul execuției, nu completăm.")
                continue

            complete_job(job_id, result_json)
            logging.info(f"[worker] Job {job.get('id')} completat cu succes, continuă loop...")
            
        except Exception as exc:
            tb = traceback.format_exc()
            logging.error(f"[worker] Eroare în job: {exc}\n{tb}")
            if "job" in locals() and isinstance(job, dict) and job.get("id"):
                fail_job(int(job["id"]), f"{exc}\n{tb}")
            time.sleep(2)


if __name__ == "__main__":
    main()
