"""Background worker daemon consuming jobs from SQLite queue (Deterministic Version)."""

from __future__ import annotations

import atexit
import base64
import io
import json
import logging
import pickle
import signal
import sys
import time
import traceback
import tempfile
import os

from runtime_paths import ENGINE_LOG_FILE
from ui_shared import pack_queue_result, require_python_version

require_python_version()

# Rezultatul de pipeline e serializat complet în SQLite pentru apelanții care
# activează `use_cache`. Schimbările de semantică ale engine-ului nu pot reutiliza
# un payload produs de cod vechi doar fiindcă CSV-ul și setările coincid.
PIPELINE_CACHE_VERSION = "v3"

LOG_FILE = str(ENGINE_LOG_FILE)

# LOTO_DEBUG=1 → loguri DEBUG (mai mult detaliu despre ce face engine-ul DUPA
# bench: selectie metoda, scoring, POST-HOC, walk-forward). Vizibile in consola UI.
_LEVEL = logging.DEBUG if os.environ.get("LOTO_DEBUG") else logging.INFO

logging.basicConfig(
    level=_LEVEL,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a"),
        logging.StreamHandler(sys.stdout)
    ]
)

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

# Windows console safety: evită crash pe diacritice/Unicode în print-uri din stack.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    logging.debug("UTF-8 console reconfigure eșuat; continuu cu encoding implicit.", exc_info=True)


import psutil
import threading

class ResourceMonitor:
    def __init__(self, interval=0.5):
        self.interval = interval
        self.max_cpu = 0.0
        self.max_ram = 0.0
        self.running = False
        self._thread = None
        
    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        
    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join()
            
    def _monitor(self):
        while self.running:
            try:
                # CPU & RAM
                cpu = psutil.cpu_percent(interval=None)  # FIX: era cpu_percentage (typo)
                ram = psutil.virtual_memory().percent
                self.max_cpu = max(self.max_cpu, cpu)
                self.max_ram = max(self.max_ram, ram)
            except Exception as e:
                logging.warning(f"[MONITOR] Eroare CPU/RAM: {e}")

            time.sleep(self.interval)

    def get_stats(self):
        return {
            "max_cpu": round(self.max_cpu, 1),
            "max_ram": round(self.max_ram, 1),
        }

def _pack_result_payload(payload: object) -> str:
    return pack_queue_result(payload)


def _remove_temp_csv(temp_csv_path: str) -> None:
    """Șterge CSV-ul temporar al unui dataset (best-effort). Apelat și pe căile
    de EROARE/STOP — altfel fiecare job eșuat lăsa un CSV orfan (tot istoricul
    de extrageri) în %TEMP%."""
    if temp_csv_path and os.path.exists(temp_csv_path):
        try:
            os.remove(temp_csv_path)
        except OSError as exc:
            logging.warning("Nu pot șterge fișierul temporar %s: %s", temp_csv_path, exc)


def _run_pipeline_job(job: dict) -> str | None:
    monitor = ResourceMonitor()
    monitor.start()
    try:
        return _run_pipeline_job_inner(job, monitor)
    finally:
        monitor.stop()

def _run_pipeline_job_inner(job: dict, monitor: ResourceMonitor) -> str | None:
    cfg = json.loads(job["config_json"])
    input_hash = str(cfg.get("input_hash", "") or "").strip()
    cache_key = f"{PIPELINE_CACHE_VERSION}:{input_hash}" if input_hash else ""
    use_cache = bool(cfg.get("use_cache", True))
    datasets_cfg = list(cfg.get("datasets", []))
    job_id = int(job["id"])

    if not datasets_cfg:
        fail_job(job_id, "Job fără CSV — nimic de generat.")
        return None

    if update_job_progress(job_id, 3, "Încarc motorul de generare..."):
        logging.info("[worker] Job %s nu mai este RUNNING; opresc înainte de engine.", job_id)
        return None
    # Import GREU după ce jobul e deja preluat (altfel UI stă pe 0% /
    # «se inițializează...» cât se încarcă pandas+engine).
    import pandas as pd
    from loto_engine import LotoEngine

    total_steps = max(1, sum(len(d.get("tasks", [])) for d in datasets_cfg))
    step_idx = 0
    results_bundle = []

    if use_cache and cache_key:
        cached = get_pipeline_cache(cache_key)
        if cached:
            if update_job_progress(job_id, 100, "Cache hit: rezultat reutilizat (hash CSV identic)."):
                logging.info("[worker] Job %s a pierdut starea RUNNING la cache hit.", job_id)
                return None
            return str(cached)

    for ds in datasets_cfg:
        fname = str(ds.get("fname", "dataset.csv"))
        # IMPORTANT: convert_dates=False — pandas altfel auto-detectează coloana
        # "date" și o parsează cu inferență month-first (default), ceea ce strică
        # formatul DD-MM-YYYY din CSV: "02-04-2026" devine 2026-02-04 (4 feb) în
        # loc de 2026-04-02 (2 apr). Păstrăm string-ul original.
        df = pd.read_json(
            io.StringIO(str(ds["df_json"])), orient="split", convert_dates=False
        )
        outputs = {}
        
        # Salvăm un fișier temporar pentru a-l încărca cu engine-ul
        temp_csv_path = ""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", encoding="utf-8") as tmp:
            df.to_csv(tmp.name, index=False)
            temp_csv_path = tmp.name

        for task in ds.get("tasks", []):
            game_label = str(task["game_label"])
            game_mapped = "6/49"
            if "5/40" in game_label.lower():
                game_mapped = "5/40"
            elif "joker" in game_label.lower():
                game_mapped = "joker"
            draw_n = 6 if game_mapped == "6/49" else 5
            p_size = int(task.get("pool_size", 12))
            p_size = max(6, min(16, p_size))  # aliniat cu UI (pool_size_val max 16)
            raw_guar = int(task.get("guarantee", 4))
            # UI-ul permite 3..6; workerul poate primi însă joburi vechi sau
            # externe. O garanție 0 generează semantic un cover al mulțimii vide,
            # iar una peste draw_n nu e realizabilă pentru jocul respectiv.
            guar = max(3, min(draw_n, raw_guar))
            raw_max_var = int(task.get("max_variants", 0))
            max_var = max(0, raw_max_var)
            # Lotto design „guarantee dacă condition": lipsă/0 = cover clasic.
            try:
                raw_cond = int(task.get("wheel_condition") or 0)
            except (TypeError, ValueError):
                raw_cond = 0
            wheel_cond = guar if raw_cond <= 0 else max(guar, min(draw_n, raw_cond))
            # Penalizare după ultimele extrageri (0 = oprit); factor în [0, 1).
            try:
                rp_draws = max(0, min(50, int(task.get("recent_penalty_draws") or 0)))
            except (TypeError, ValueError):
                rp_draws = 0
            try:
                rp_factor = float(task.get("recent_penalty_factor", 0.5))
            except (TypeError, ValueError):
                rp_factor = 0.5
            rp_factor = max(0.0, min(0.99, rp_factor))
            raw_lookback = int(task.get("lookback", 0))
            lookback = max(0, min(100, raw_lookback))
            if (guar, max_var, lookback) != (raw_guar, raw_max_var, raw_lookback):
                logging.warning(
                    "[worker] Task normalizat %s: guarantee %s→%s, max_variants %s→%s, "
                    "lookback %s→%s",
                    game_label, raw_guar, guar, raw_max_var, max_var, raw_lookback, lookback,
                )
            filter_cons = bool(task.get("filter_consecutives", False))
            smart_red = bool(task.get("smart_reduction", False))
            sim_depth = int(task.get("sim_depth_pct", 10))
            pure_bench = bool(task.get("pure_bench_mode", False))
            try:
                from loto_enterprise.benchmark.hit_target import clamp_bench_hit_target
                bench_hit_target = clamp_bench_hit_target(task.get("bench_hit_target", 3))
            except Exception:
                bench_hit_target = 3

            try:
                import loto_enterprise.benchmark.decision as decision
                decision.BENCH_HIT_TARGET = bench_hit_target
                os.environ["LOTO_BENCH_TARGET"] = str(bench_hit_target)
                logging.info(f"[worker] S-a setat tinta de benchmark la {bench_hit_target}+ hits.")
            except Exception as exc:
                logging.warning(f"[worker] Nu s-a putut seta tinta de benchmark: {exc}")

            logging.info(f"[worker] Se procesează task pentru {game_label} (Pool: {task.get('pool_size')}, Garanție: {task.get('guarantee')})")
            logging.debug(f"[worker] Full task: {task}")
            
            def progress_cb(msg, pct):
                overall_pct = int(((step_idx + (pct / 100.0)) / total_steps) * 95)
                # Dacă update_job_progress returnează True, înseamnă că job-ul a fost anulat sau șters
                if update_job_progress(job_id, overall_pct, f"[{fname}][{game_label}] {msg}"):
                    # Aruncăm o eroare pentru a opri engine-ul imediat
                    raise Exception("STOP_REQUESTED")

            try:
                # Verificăm dacă job-ul a fost anulat între timp
                if is_job_cancelled(job_id):
                    logging.info(f"[worker] Job {job_id} anulat în timpul execuției (task {game_label}). Oprire.")
                    _remove_temp_csv(temp_csv_path)  # altfel istoricul rămâne în %TEMP% la fiecare anulare
                    return "{}"

                engine = LotoEngine(game_type=game_mapped)
                # Valoarea de retur NU se ignoră: cu date necitibile/corupte
                # pipeline-ul mergea până la capăt și scotea pool GOL / 0 bilete,
                # iar jobul se încheia COMPLETED — „succes" fără niciun bilet și
                # fără nicio eroare vizibilă.
                if not engine.load_data(temp_csv_path):
                    raise ValueError(
                        f"Datele pentru {game_label} nu au putut fi încărcate "
                        f"(fișier lipsă, corupt sau fără extrageri valide)."
                    )
                lines, p10, p90, g_range, context, audit = engine.run_institutional_pipeline(
                    progress_cb=progress_cb,
                    pool_size=p_size,
                    guarantee=guar,
                    max_variants=max_var,
                    wheel_condition=wheel_cond,
                    recent_penalty_draws=rp_draws,
                    recent_penalty_factor=rp_factor,
                    lookback=lookback,
                    filter_consecutives=filter_cons,
                    smart_reduction=smart_red,
                    sim_depth_pct=sim_depth,
                    enable_adaptive_persistence=False,
                    pure_bench_mode=pure_bench,
                )
                effective_pool = len(engine.hard_core) if engine.hard_core else p_size
                outputs[game_label] = {
                    "total_draws": len(engine.data) if engine.data is not None else 0,
                    "hard_core": engine.hard_core,
                    "hard_core_stats": getattr(engine, 'hard_core_stats', {}),
                    "hard_core_joker": getattr(engine, 'hard_core_joker', []),
                    "hard_core_joker_stats": getattr(engine, 'hard_core_joker_stats', {}),
                    "variants": lines,
                    "pool_size": effective_pool,
                    "pool_size_requested": p_size,
                    "guarantee": guar,
                    "wheel_condition": wheel_cond,
                    "recent_penalty_draws": rp_draws,
                    "recent_penalty_factor": rp_factor,
                    "lookback": lookback,
                    "audit": audit,
                    "resource_stats": monitor.get_stats(),
                    "p10": p10,
                    "p90": p90,
                    "g_range": g_range,
                    "context": context,
                }
            except Exception as e:
                if "STOP_REQUESTED" in str(e):
                    logging.info(f"[worker] Job {job_id} oprit la cerere (Stop Requested).")
                    _remove_temp_csv(temp_csv_path)
                    return "{}"
                logging.error(f"Eroare la procesarea task-ului {game_label}: {e}")
                _remove_temp_csv(temp_csv_path)
                raise
            finally:
                step_idx += 1

        _remove_temp_csv(temp_csv_path)
        results_bundle.append((fname, outputs))

    if update_job_progress(job_id, 99, "Pregătesc rezultatul final pentru UI..."):
        logging.info("[worker] Job %s nu mai este RUNNING înainte de serializare.", job_id)
        return None
    persistent = (results_bundle, len(results_bundle))
    packed = _pack_result_payload(persistent)
    if use_cache and cache_key:
        put_pipeline_cache(cache_key, packed)
    return packed


def _requeue_on_terminate(*_args) -> None:
    """La oprire bruscă (SIGTERM/SIGINT) re-punem jobul RUNNING pe PENDING ca să
    NU rămână blocat 'în curs' pe veci — la următoarea pornire worker-ul îl reia.
    Consistent cu requeue_running_jobs() de la startup."""
    try:
        requeue_running_jobs()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    atexit.register(_requeue_on_terminate)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, lambda s, f: (_requeue_on_terminate(), sys.exit(1)))
        except (ValueError, OSError):
            pass  # signal disponibil doar pe thread-ul principal
    try:
        recovered = requeue_running_jobs()
        if recovered > 0:
            print(f"[worker] Recuperate {recovered} job(uri) RUNNING -> PENDING după restart.", flush=True)
    except Exception as exc:
        logging.debug("Nu pot requeue joburile RUNNING la startup worker: %s", exc)
    logging.info("[worker] Început loop principal - aștept job-uri...")
    
    while True:
        job = None  # reset per iterație: altfel un fetch care crapă la iterația
        # următoare vede jobul VECHI (deja COMPLETED) și fail_job i-ar distruge rezultatul
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
            if update_job_progress(job_id, 2, "Job preluat de worker."):
                logging.info(
                    "[worker] Job %s nu mai este RUNNING imediat după claim; sarim.",
                    job_id,
                )
                continue

            if is_job_cancelled(job_id):
                logging.info(f"[worker] Job {job_id} a fost anulat (CANCELLED), sarim.")
                continue

            if task_type == "pipeline":
                result_json = _run_pipeline_job(job)
            else:
                fail_job(job_id, f"Unsupported task type: {task_type}")
                continue

            if result_json is None:
                continue

            if is_job_cancelled(job_id):
                logging.info(f"[worker] Job {job_id} anulat în timpul execuției, nu completăm.")
                continue

            if complete_job(job_id, result_json):
                logging.info(f"[worker] Job {job_id} completat cu succes, continuă loop...")
            else:
                # UPDATE-ul cere status = RUNNING. Dacă un al doilea worker a rulat
                # între timp `requeue_running_jobs()`, jobul e din nou PENDING și
                # rezultatul NU se scrie. Înainte logam „completat cu succes"
                # oricum — o rulare de 90 de minute dispărea tăcut. Salvăm
                # rezultatul pe disc ca să nu fie pierdut definitiv.
                _dump = os.path.join(
                    tempfile.gettempdir(), f"loto_orphan_result_{job_id}.txt"
                )
                try:
                    with open(_dump, "w", encoding="utf-8") as _fh:
                        _fh.write(result_json)
                    logging.error(
                        "[worker] Job %s: rezultatul NU a putut fi scris în coadă "
                        "(jobul nu mai era RUNNING). L-am salvat în %s", job_id, _dump,
                    )
                except OSError as _exc:
                    logging.error(
                        "[worker] Job %s: rezultat PIERDUT (nu mai era RUNNING) și "
                        "nici salvarea în %s n-a mers: %s", job_id, _dump, _exc,
                    )
            
        except Exception as exc:
            tb = traceback.format_exc()
            logging.error(f"[worker] Eroare în job: {exc}\n{tb}")
            if isinstance(job, dict) and job.get("id"):
                fail_job(int(job["id"]), f"{exc}\n{tb}")
            time.sleep(2)


if __name__ == "__main__":
    main()
