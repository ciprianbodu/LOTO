"""
Loto Engine - Motor principal de analiză și predicție (vectorizat unde e posibil).
"""

from __future__ import annotations

import hashlib
import json
import warnings
import functools
from datetime import datetime

import itertools
import numpy as np
import pandas as pd
from numba import jit
import time
import logging
import os
import sys
from typing import List, Tuple, Dict, Set, Optional

# Google TimesFM integration
HAS_TIMESFM = False
TIMESFM_ERROR = None
try:
    import torch
    import timesfm
    HAS_TIMESFM = True
except ImportError as e:
    TIMESFM_ERROR = str(e)
    logging.warning(f"[TIMESFM] Librăria 'timesfm' sau 'torch' nu a fost găsită: {e}")
except Exception as e:
    TIMESFM_ERROR = str(e)
    logging.error(f"[TIMESFM] Eroare neașteptată la încărcarea modelului: {e}")

# Cache global pentru modelul legacy
_GLOBAL_LEGACY_TFM = None

# TimesFM v2 engine (forecast_with_covariates, quantile, anomaly)
try:
    from loto_enterprise.core.timesfm_engine import (
        get_timesfm_scores_v2,
        get_regressive_blacklist_v2,
        select_pool_from_scores,
        filter_variants_by_anomaly_v2,
    )
    _HAS_TFM_V2 = True
except ImportError:
    _HAS_TFM_V2 = False

# Configurăm logging cu timestamp pentru debug detaliat
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
VERSION = "1.1.2"
warnings.filterwarnings("ignore")

def generate_combinatorial_wheel(pool, pick=6, guarantee=4, max_variants=0, scores=None):
    """
    Sistem de Wheeling (Set Cover Optimizat Memorie & Viteză)
    Optimizat pentru hit-uri de 4 și 5 numere prin prioritizarea scorurilor NQI/Frecvență.
    """
    start_time = time.time()
    pool_len = len(pool)
    logging.info(f"[WHEEL] Inițializare sistem Wheeling pentru pool de {pool_len} numere. Pick={pick}, Guarantee={guarantee}.")

    if pool_len < pick:
        return [list(pool)], 100.0

    # Sortăm pool-ul după scoruri pentru a favoriza numerele puternice
    if scores:
        pool = sorted(list(pool), key=lambda x: scores.get(x, 0), reverse=True)
    else:
        pool = sorted(list(pool))

    # Generăm toate combinațiile de garanție ca ținte, dar le sortăm numeric
    # pentru a fi consistente cu rezultatul numeric al wheeling-ului.
    # Folosim o listă pentru ordinea greedy și un set pentru lookup rapid.
    all_targets_list = [tuple(sorted(t)) for t in itertools.combinations(pool, guarantee)]

    # Dacă avem scoruri, sortăm țintele pentru a le acoperi întâi pe cele mai probabile
    if scores:
        all_targets_list.sort(key=lambda t: sum(scores.get(n, 0) for n in t), reverse=True)
        
    total_targets = len(all_targets_list)
    covered_targets = set()
    wheel = []
    
    iteration = 0
    max_search_per_iter = 10000 if pool_len <= 15 else 50000
    
    while len(covered_targets) < total_targets:
        if max_variants > 0 and len(wheel) >= max_variants:
            logging.info(f"[WHEEL] S-a atins limita maxima cerută de variante: {max_variants}.")
            break
            
        iteration += 1
        best_ticket = None
        best_coverage = -1
        best_targets_covered = set()
        
        # Găsim prima țintă neacoperită (cea mai valoroasă datorită sortării)
        target_to_cover = None
        for t in all_targets_list:
            if t not in covered_targets:
                target_to_cover = t
                break

        if not target_to_cover:
            break
            
        base_ticket = set(target_to_cover)
        # remaining_pool păstrează ordinea sortată după scor
        remaining_pool = [n for n in pool if n not in base_ticket]
        
        # Generăm N combinații care includ target_to_cover
        search_count = 0
        if len(remaining_pool) >= (pick - guarantee):
            # itertools.combinations pe un pool sortat va genera combinații
            # începând cu cele mai bune numere (scor maxim).
            for extra_nums in itertools.combinations(remaining_pool, pick - guarantee):
                ticket = tuple(sorted(list(base_ticket) + list(extra_nums)))
                
                # Evaluăm rapid
                ticket_targets = set(itertools.combinations(ticket, guarantee))
                new_coverage = ticket_targets - covered_targets
                
                if len(new_coverage) > best_coverage:
                    best_coverage = len(new_coverage)
                    best_ticket = ticket
                    best_targets_covered = ticket_targets
                
                search_count += 1
                if search_count > max_search_per_iter:
                    break
        
        if best_ticket:
            wheel.append(list(best_ticket))
            covered_targets.update(best_targets_covered)
            if iteration % 10 == 0 or len(covered_targets) == total_targets:
                logging.info(f"[WHEEL] Iteratia {iteration}: Acoperite {len(covered_targets)}/{total_targets} ținte. Bilete: {len(wheel)}")
        else:
            logging.warning("[WHEEL] Nu am găsit acoperire suplimentară.")
            break
            
        if iteration > 1000:  # Timeout extins pt pool-uri mari, dar mult mai rapid
            logging.warning(f"[WHEEL] TIMEOUT: 1000 iterații.")
            break
            
    coverage_pct = 100.0 if total_targets == 0 else round((len(covered_targets) / total_targets) * 100, 2)
    logging.info(f"[WHEEL] Generare completă în {time.time() - start_time:.2f}s. Total variante: {len(wheel)}. Acoperire: {coverage_pct}%")
    return wheel, coverage_pct


class LotoEngine:
    def __init__(self, game_type: str = "6/49"):
        self.game_type = game_type
        self.params = self._get_game_params(game_type)
        self.audit: dict = {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "timesfm_error": TIMESFM_ERROR
        }
        self.hard_core: list = []
        self.hard_core_stats: dict = {}
        self.hard_core_joker_stats: dict = {}
        self.data: pd.DataFrame | None = None
        self.arena2_index = None
        self._draw_matrix: np.ndarray | None = None
        self.error_correction_map: Dict[int, float] = {}  # num -> bias_multiplier

    def _get_game_params(self, game_type: str):
        params = {
            "6/49": {
                "max_n": 49,
                "draw_n": 6,
                "play_n": 6,
                "scheme": "2-2-2",
                "lookback": 20,
            },
            "5/40": {
                "max_n": 40,
                "draw_n": 5,
                "play_n": 5,
                "scheme": "2-1-2",
                "lookback": 25,
            },
            "joker": {
                "max_n": 45,
                "draw_n": 5,
                "play_n": 5,
                "scheme": "2-2-1",
                "lookback": 15,
                "max_joker": 20
            },
        }
        return params.get(game_type, params["6/49"])

    def load_data(self, csv_path: str) -> bool:
        """Încarcă date din CSV."""
        try:
            self.data = pd.read_csv(csv_path)
            self._build_draw_matrix()
            self.audit["rows_loaded"] = len(self.data)
            self.audit["game_detected"] = self.game_type
            # Bug 1.6 Fix: hashlib.sha256 determinist în loc de hash() care variază între rulări
            self.audit["hash"] = hashlib.sha256(self.data.values.tobytes()).hexdigest()[:16]
            return True
        except Exception as e:
            print(f"Eroare la încărcare date: {e}")
            return False

    def _build_draw_matrix(self) -> None:
        """Construiește o dată matrice (rows x draw_n) de numere întregi — fără split pe string în buclă."""
        if self.data is None:
            self._draw_matrix = None
            return
        df = self.data
        if "numbers" in df.columns:
            self._draw_matrix = None
            return
        n_cols = [c for c in df.columns if str(c).lower().startswith("n")]
        n_cols = sorted(n_cols, key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))[
            : int(self.params["draw_n"])
        ]
        if not n_cols:
            self._draw_matrix = None
            return
        raw = df[n_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
        self._draw_matrix = np.nan_to_num(raw, nan=0.0).astype(np.int32)

    def analyze_frequency(self) -> np.ndarray:
        """Analiză frecvență numerelor (vectorizat pe matrice sau coloana numbers)."""
        logging.info(f"[ENGINE] Analiză frecvență (Versiune {VERSION})...")
        if self.data is None:
            return np.array([], dtype=np.int64)

        max_n = int(self.params["max_n"])

        if self._draw_matrix is not None and self._draw_matrix.size:
            vals = self._draw_matrix.ravel()
            vals = vals[(vals >= 1) & (vals <= max_n)]
            if vals.size == 0:
                return np.zeros(max_n, dtype=np.int64)
            freq = np.bincount(vals.astype(np.int64), minlength=max_n + 1)
            return freq[1 : max_n + 1]

        # Fallback (dacă lipsește _draw_matrix)
        all_numbers = []
        if self.data is not None:
            n_cols = [c for c in self.data.columns if str(c).lower().startswith('n')]
            if n_cols:
                raw_vals = self.data[n_cols].values.ravel()
                all_numbers = raw_vals[~np.isnan(raw_vals)].astype(int).tolist()
            elif "numbers" in self.data.columns:
                for _, row in self.data.iterrows():
                    try:
                        nums = [int(x) for x in str(row["numbers"]).split(",") if str(x).strip().isdigit()]
                        all_numbers.extend(nums)
                    except: continue
        
        if not all_numbers:
            return np.zeros(max_n, dtype=np.int64)
            
        arr = np.asarray(all_numbers, dtype=np.int64)
        arr = arr[(arr >= 1) & (arr <= max_n)]
        freq = np.bincount(arr, minlength=max_n + 1)
        return freq[1 : max_n + 1]

    def analyze_joker_frequency(self) -> np.ndarray:
        """Analiză frecvență pentru Urna 2 la Joker (1-20)."""
        if self.data is None or "joker" not in self.data.columns:
            return np.zeros(20, dtype=np.int64)
            
        joker_vals = pd.to_numeric(self.data["joker"], errors="coerce").dropna().astype(np.int64)
        joker_vals = joker_vals[(joker_vals >= 1) & (joker_vals <= 20)]
        if joker_vals.empty:
            return np.zeros(20, dtype=np.int64)
        freq = np.bincount(joker_vals, minlength=21)
        return freq[1:21]

    def generate_predictions(self, guarantee=4, max_variants=0, scores=None):
        """Generează predicții bazate pe analiză."""
        if not hasattr(self, 'hard_core') or not self.hard_core:
            return [], 0.0

        # Înlocuim logica veche (Random Pick) cu Wheeling logic matematic sigură pentru Urna 1
        variants, coverage_pct = generate_combinatorial_wheel(
            pool=self.hard_core, 
            pick=self.params["draw_n"], 
            guarantee=guarantee,
            max_variants=max_variants,
            scores=scores
        )
        
        # Atașăm Joker din nucleul dur de Joker dacă e cazul
        if self.game_type == "joker" and hasattr(self, 'hard_core_joker') and self.hard_core_joker:
            # Ciclam prin jokerii favoriți pentru fiecare variantă de Urna 1
            jokers = self.hard_core_joker
            joker_pool_size = len(jokers)
            for idx, variant in enumerate(variants):
                assigned_joker = jokers[idx % joker_pool_size]
                variant.append(assigned_joker)  # Elementul 6 este Joker-ul

        return variants, coverage_pct

    def run_institutional_pipeline(self, progress_cb=None, pool_size=12, guarantee=4, max_variants=0, lookback=0, filter_consecutives=False, smart_reduction=True, sim_depth_pct=10):
        """Rulează pipeline-ul complet de analiză."""
        logging.info(f"[PIPELINE] Se inițializează motorul Neural Foundation (TimesFM) [pool_size={pool_size}, guarantee={guarantee}, max_variants={max_variants}, lookback={lookback}%, smart_reduction={smart_reduction}]...")
        
        if lookback > 0 and self.data is not None and not self.data.empty:
            effective_rows = int(len(self.data) * (lookback / 100.0))
            effective_rows = max(effective_rows, 1)
            actual_lookback = effective_rows
            if effective_rows == 0:
                actual_lookback = 1 # Măcar o extragere
            logging.info(f"[PIPELINE] Aplic limită de istoric: Ultimele {lookback}% ({actual_lookback} extrageri).")
            self.data = self.data.tail(effective_rows).copy()
            self._build_draw_matrix()
            
        if progress_cb:
            progress_cb("Inițializare motor...", 10)

        logging.info("[PIPELINE] Începe analiza frecvenței...")
        freq = self.analyze_frequency()

        if progress_cb:
            progress_cb("Analiza frecvenței...", 30)

        # 4. Calcul Scoruri NQI (Neural Quality Index) prin TimesFM v2 - MUTAT AICI PENTRU OPTIMIZARE CACHE
        tfm_scores = {}
        total_draws = len(self.data) if self.data is not None else 0
        actual_lookback = total_draws
        self.audit["effective_rows_used"] = total_draws
        self.audit["lookback_pct"] = lookback
            
        if progress_cb:
            progress_cb(f"TimesFM v2: forecast + XReg covariates + anomaly (ctx={actual_lookback})...", 40)
        
        start_gpu = time.perf_counter()
        tfm_scores = self._get_timesfm_scores(context_len=actual_lookback)
        gpu_time = (time.perf_counter() - start_gpu) * 1000
        
        if 'performance' not in self.audit:
            self.audit['performance'] = {}
        self.audit['performance']['gpu_time_ms'] = round(gpu_time, 2)
        logging.info(f"[PIPELINE] TimesFM GPU Time: {gpu_time:.2f}ms")

        # Aplicăm corecția de feedback (Adaptive Local Tuning) ANTERIOR rulării TimesFM
        if self.error_correction_map:
            logging.info(f"[PIPELINE] Aplicare feedback BEFORE TimesFM pe {len(self.error_correction_map)} numere...")
            for num, multiplier in self.error_correction_map.items():
                if num in tfm_scores:
                    tfm_scores[num] *= multiplier
            self.audit['feedback_correction'] = {str(k): round(v, 3) for k, v in self.error_correction_map.items()}

        # 5. Filtru Regresiv Multi-Timeframe (Smart Reduction / Folosește Ensemble Cache din tfm_scores)
        blacklist = set()
        self.audit["sim_depth_pct"] = sim_depth_pct
        if smart_reduction:
            logging.info(f"[PIPELINE] Activare Analiză Regresivă Neurală v2 (XReg + Quantile)...")
            blacklist = self._get_timesfm_regressive_blacklist(sim_depth_pct)
            
            self.audit['reduction_filter'] = {
                'combined_blacklist': list(blacklist),
                'total_blocked': len(blacklist),
                'model_used': 'Google TimesFM v2 (Ensemble Intersect)',
                'sim_depth_pct': sim_depth_pct
            }
        else:
            logging.info("[PIPELINE] Reducție Smart dezactivată. Se continuă fără blacklist.")

        self.hard_core = self._get_timesfm_pool(tfm_scores, pool_size=pool_size, blacklist=blacklist)
        
        # [NEW] Optimizare suplimentară prin Smart Selector (Gap/Trend/Positional)
        # Rafinează pool-ul pentru a maximiza hit-urile de 4 și 5 numere.
        if hasattr(self, '_apply_smart_selector'):
            logging.info("[PIPELINE] Rafinare nucleu prin Smart Selector (Hybrid Optimization)...")
            self._apply_smart_selector(freq)

        # Aplicăm filtrul anti-secvență pe nucleul generat de TimesFM
        if filter_consecutives:
            logging.info("[PIPELINE] Aplicare Filtru Anti-Secvență pe nucleul TimesFM...")
            self.hard_core = self._apply_consecutive_filter(self.hard_core, freq)

        # Garanție finală: Nucleul dur trebuie să aibă exact pool_size numere
        if len(self.hard_core) > pool_size:
            logging.warning(f"[PIPELINE] Nucleul dur avea {len(self.hard_core)} numere. Trunchiere la {pool_size}.")
            self.hard_core = self.hard_core[:pool_size]
        elif len(self.hard_core) < pool_size:
            logging.warning(f"[PIPELINE] Nucleul dur avea doar {len(self.hard_core)} numere. Pool_size solicitat: {pool_size}.")
            self._consecutive_filter_applied = True
        else:
            self._consecutive_filter_applied = False
            
        logging.info(f"[PIPELINE] Nucleu (Pool) generat prin TimesFM v2 NQI: {self.hard_core}")
        
        if self.game_type == "joker":
            logging.info(f"[PIPELINE] TimesFM v2 pentru Urna 2 (Joker)...")
            j_scores = self._get_timesfm_scores(is_joker_drum=True, context_len=actual_lookback)
            if j_scores:
                sorted_j = sorted(j_scores.items(), key=lambda x: x[1], reverse=True)
                self.hard_core_joker = [int(num) for num, _ in sorted_j[:2]]
                logging.info(f"[PIPELINE] Nucleu Joker (TimesFM v2): {self.hard_core_joker}")
                self.audit['joker_predictions'] = {num: round(score, 4) for num, score in sorted_j[:5]}
            else:
                freq_joker = self.analyze_joker_frequency()
                self.hard_core_joker = self._get_hard_core_joker(freq_joker, pool_size=2)
                logging.info(f"[PIPELINE] Nucleu Joker (Fallback Frecvență): {self.hard_core_joker}")

        if progress_cb:
            progress_cb("Generare predicții finale (Wheeling)...", 70)

        logging.info("[PIPELINE] Începe generarea predicțiilor (Wheeling Set Cover)...")
        # Folosim tfm_scores dacă sunt disponibile, altfel fallback pe frecvență pentru wheeling
        wheeling_scores = tfm_scores if tfm_scores else {i+1: float(f) for i, f in enumerate(freq)}
        lines, coverage_pct = self.generate_predictions(guarantee=guarantee, max_variants=max_variants, scores=wheeling_scores)
        
        # Funcția 4: Anomaly Filtering via TimesFM v2
        if smart_reduction and tfm_scores:
            logging.info(f"[PIPELINE] Aplicare Neural Anomaly Scoring v2 pe {len(lines)} variante...")
            original_count = len(lines)
            lines = self.filter_variants_by_anomaly(lines, tfm_scores, threshold=0.7)
            logging.info(f"[PIPELINE] Anomaly v2: eliminat {original_count - len(lines)} variante.")
            self.audit['anomaly_filter'] = {
                'original_count': original_count,
                'final_count': len(lines),
                'threshold': 0.7
            }

        logging.info(f"[PIPELINE] S-au generat {len(lines)} variante de joc. Acoperire: {coverage_pct}%")

        if progress_cb:
            progress_cb("Validare rezultate...", 90)

        # Recalculăm statisticile pentru afișare corectă în UI (procente)
        final_freq = self.analyze_frequency()
        self.hard_core_stats = {int(num): int(final_freq[num-1]) for num in self.hard_core if num-1 < len(final_freq)}
        
        if self.game_type == "joker":
            final_j_freq = self.analyze_joker_frequency()
            self.hard_core_joker_stats = {int(num): int(final_j_freq[num-1]) for num in self.hard_core_joker if num-1 < len(final_j_freq)}

        p10, p90 = np.percentile(final_freq, [10, 90]) if final_freq.size else (0.0, 0.0)
        g_range = [p10 * self.params["draw_n"], p90 * self.params["draw_n"]]

        context = {
            "first_3": [],
            "last_3": []
        }
        if self.data is not None and not self.data.empty:
            def extract_draws(df_subset):
                draws = []
                for _, row in df_subset.iterrows():
                    d = {"date": str(row.get("date", "")).split()[0] if "date" in row else "N/A", "numbers": [], "joker": None}
                    n_cols = sorted([c for c in df_subset.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"], key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))
                    nums = [row[c] for c in n_cols if pd.notna(row.get(c))]
                    d["numbers"] = [int(x) for x in nums]
                    if "joker" in df_subset.columns and pd.notna(row.get("joker")):
                        d["joker"] = int(row["joker"])
                    draws.append(d)
                return draws

            context["first_3"] = extract_draws(self.data.head(3))
            context["last_3"] = extract_draws(self.data.tail(3))
            
        context["coverage_pct"] = coverage_pct
            
        if progress_cb:
            progress_cb("Pipeline complet!", 100)

        # --- TRACK POOL VARIATION ---
        try:
            import json
            from pathlib import Path
            from datetime import datetime
            
            history_file = Path("pool_history.json")
            history = {}
            if history_file.exists():
                with open(history_file, "r") as f:
                    history = json.load(f)
            
            hist_key = f"{self.game_type}_{pool_size}"
            last_pool = history.get(hist_key, {}).get("pool", [])
            
            pool_variation = {}
            if last_pool:
                added = sorted(list(set(self.hard_core) - set(last_pool)))
                removed = sorted(list(set(last_pool) - set(self.hard_core)))
                pool_variation = {
                    "added": added,
                    "removed": removed,
                    "changed": bool(added or removed)
                }
            
            history[hist_key] = {
                "pool": self.hard_core,
                "date": datetime.now().isoformat()
            }
            with open(history_file, "w") as f:
                json.dump(history, f)
                
            self.audit["pool_variation"] = pool_variation
        except Exception as e:
            logging.error(f"[PIPELINE] Eroare la tracker-ul de variație: {e}")

        logging.info("[PIPELINE] Pipeline completat cu succes.")
        return lines, p10, p90, g_range, context, self.audit

    def _get_initial_hard_core(self, freq: np.ndarray, pool_size=12, filter_consecutives=False, blacklist=None) -> list:
        """Selectează nucleul dur inițial bazat pe top frecvență (va fi optimizat de Smart Selector)."""
        logging.info(f"[INIT] Generare nucleu inițial de {pool_size} numere...")
        
        # Inițializăm blacklist dacă nu e furnizat
        if blacklist is None:
            blacklist = set()
        
        # Luăm top cele mai frecvente numere ca punct de plecare
        sorted_indices = np.argsort(freq)[::-1]  # Descrescător
        valid_indices = [int(i) + 1 for i in sorted_indices if i < len(freq) and freq[i] > 0 and (int(i) + 1) not in blacklist]
        
        # Luăm primele N numere cele mai frecvente care nu sunt în blacklist
        pool = valid_indices[:pool_size]
        
        if filter_consecutives:
            pool = self._apply_consecutive_filter(pool, freq)
            self._consecutive_filter_applied = True
        else:
            self._consecutive_filter_applied = False
            
        # Salvăm statisticile inițiale
        self.hard_core_stats = {int(num): int(freq[num - 1]) for num in pool if num - 1 < len(freq)}
        logging.info(f"[INIT] Nucleu inițial: {pool}")
        return pool

    def _apply_consecutive_filter(self, pool: list, freq: np.ndarray) -> list:
        """Caută secvențe de 3+ și le înlocuiește dacă nu au istoric."""
        if self.data is None or len(pool) < 3:
            return pool
            
        draw_sets = []
        if self._draw_matrix is not None and self._draw_matrix.size:
            draw_sets = [set(row) for row in self._draw_matrix]
        else:
            # Robust columns detection
            df = self.data
            n_cols = [c for c in df.columns if str(c).lower().startswith('n') and str(c).lower() != 'numbers']
            if n_cols:
                for _, row in df.iterrows():
                    draw_sets.append(set(pd.to_numeric(row[n_cols], errors='coerce').dropna().astype(int)))
            elif "numbers" in df.columns:
                for _, row in df.iterrows():
                    try:
                        draw_sets.append(set(int(x) for x in str(row["numbers"]).split(",") if str(x).strip().isdigit()))
                    except: continue

        current_pool = sorted(pool.copy())
        all_sorted_indices = np.argsort(freq)[::-1]
        reserve_idx = len(pool)
        modifications = []
        
        # Prevenim bucla infinită memorând secvențele validate (care rămân în pool)
        validated_sequences = set()
        
        while True:
            found_sequence = None
            # Căutăm orice secvență de 3+ numere consecutive care nu a fost încă validată
            for start_idx in range(len(current_pool) - 2):
                consecutive_nums = [current_pool[start_idx]]
                
                # Extindem secvența atât timp cât găsim numere consecutive
                for next_idx in range(start_idx + 1, len(current_pool)):
                    if current_pool[next_idx] == consecutive_nums[-1] + 1:
                        consecutive_nums.append(current_pool[next_idx])
                    else:
                        break
                
                # Verificăm dacă avem cel puțin 3 numere consecutive
                if len(consecutive_nums) >= 3:
                    seq = tuple(consecutive_nums)
                    if seq not in validated_sequences:
                        found_sequence = seq
                        break
            
            if not found_sequence:
                break
                
            # Verificăm în istoric
            seq_set = set(found_sequence)
            occurrences = sum(1 for d_set in draw_sets if seq_set.issubset(d_set))
            
            total_draws = len(draw_sets)
            threshold = total_draws * 0.01
            
            if occurrences >= threshold:
                validated_sequences.add(found_sequence)
                if 'kept_sequences' not in self.audit:
                    self.audit['kept_sequences'] = []
                self.audit['kept_sequences'].append(f"Secvența {found_sequence} a fost păstrată (a ieșit de {occurrences} ori, adică >= 1% din cele {total_draws} extrageri).")
                continue # Trecem la următoarea secvență posibilă
                
            # Nu au ieșit. Găsim cel mai slab număr din secvență.
            weakest_num = min(found_sequence, key=lambda x: freq[x-1])
            current_pool.remove(weakest_num)
            
            # Adăugăm rezerva
            added = False
            while reserve_idx < len(all_sorted_indices):
                next_num = int(all_sorted_indices[reserve_idx]) + 1
                reserve_idx += 1
                if next_num not in current_pool:
                    current_pool.append(next_num)
                    modifications.append(f"Scos {weakest_num} (frecvență {int(freq[weakest_num-1])}), adăugat {next_num} (frecvență {int(freq[next_num-1])})")
                    added = True
                    break
            
            if not added: break
            current_pool = sorted(current_pool)
            
        if modifications:
            self.audit['consecutive_filter'] = modifications
            logging.info(f"[FILTER] Modificări anti-secvență: {modifications}")
            
        return current_pool
        
    def _get_hard_core_joker(self, freq: np.ndarray, pool_size=3) -> list:
        """Selectează nucleul dur pentru Joker (1-based) și salvează statisticile."""
        top_indices = np.argsort(freq)[-pool_size:][::-1]
        self.hard_core_joker_stats = {int(i) + 1: int(freq[i]) for i in top_indices}
        return [int(i) + 1 for i in top_indices]

    def _apply_timesfm_filter(self, freq: np.ndarray) -> None:
        """
        Implementare custom de TimesFM bazată pe analiză statistică avansată.
        Identifică și exclude numerele "inactive/moarte" din nucleu dur.
        IMPORTANT: NU excludem niciodată numerele Cold (respectăm selecția Hot/Cold a utilizatorului).
        """
        if not hasattr(self, 'hard_core') or not self.hard_core:
            return
            
        logging.info("[TIMESFM] Start analiză statistică avansată...")
        
        # Determinăm care numere din nucleu sunt Cold vs Hot
        cold_numbers, hot_numbers = self._identify_cold_hot_numbers(freq)
        logging.info(f"[TIMESFM] Numere Cold (protejate): {cold_numbers}")
        logging.info(f"[TIMESFM] Numere Hot (pot fi excluse): {hot_numbers}")
        
        # Calculăm metrici doar pentru numerele Hot (nu atingem Cold)
        exclusion_candidates = {}
        total_draws = len(self.data) if self.data is not None else 1
        
        for num in self.hard_core:
            # SĂRIM complet numerele Cold - nu pot fi excluse!
            if num in cold_numbers:
                logging.info(f"[TIMESFM] Protejăm numărul Cold {num} de la excludere")
                continue
                
            num_idx = num - 1  # 0-based index
            if num_idx >= len(freq):
                continue
                
            # 1. Frecvență absolută
            abs_freq = freq[num_idx]
            
            # 2. Frecvență relativă
            rel_freq = abs_freq / total_draws if total_draws > 0 else 0
            
            # 3. Analiză trend ultimele 20% extrageri
            recent_draws = max(20, int(total_draws * 0.2))
            recent_freq = self._calculate_recent_frequency(num, recent_draws)
            
            # 4. Interval mediu între apariții
            avg_gap = self._calculate_average_gap(num)
            
            # 5. Scor de inactivitate (doar pentru Hot numbers)
            inactivity_score = (
                (1 - recent_freq) * 0.5 +  # Trend recent scăzut (mai multă greutate)
                min(avg_gap / 50, 1) * 0.5   # Gap mare între apariții
            )
            
            exclusion_candidates[num] = {
                'inactivity_score': inactivity_score,
                'abs_freq': abs_freq,
                'rel_freq': rel_freq,
                'recent_freq': recent_freq,
                'avg_gap': avg_gap
            }
        
        if not exclusion_candidates:
            logging.info("[TIMESFM] Niciun număr Hot disponibil pentru analiză TimesFM")
            return
        
        # Sortăm după scor de inactivitate (descrescător)
        sorted_candidates = sorted(exclusion_candidates.items(), 
                                 key=lambda x: x[1]['inactivity_score'], 
                                 reverse=True)
        
        # Determinăm câte numere să excludem (max 20% din nucleu, doar din Hot)
        # Limităm excluderile la 10% din nucleu (numere Hot) pentru a păstra diversitatea
        max_exclusions = max(1, min(len(hot_numbers), int(len(self.hard_core) * 0.1)))
        numbers_to_exclude = []
        
        for num, metrics in sorted_candidates[:max_exclusions]:
            # Excludem doar dacă scorul de inactivitate e > 0.5 (mai permisiv pentru Hot)
            if metrics['inactivity_score'] > 0.5:
                numbers_to_exclude.append(num)
                logging.info(f"[TIMESFM] Candidat pentru excludere: {num} (scor: {metrics['inactivity_score']:.2f})")
        
        if numbers_to_exclude:
            # Excludem numerele și le înlocuim cu cele mai bune alternative
            self._replace_inactive_numbers(numbers_to_exclude, freq)
            
            # Salvăm în audit pentru afișare în UI
            if 'timesfm_excluded' not in self.audit:
                self.audit['timesfm_excluded'] = {}
                
            for num in numbers_to_exclude:
                metrics = exclusion_candidates[num]
                inactivity_pct = int(metrics['inactivity_score'] * 100)
                self.audit['timesfm_excluded'][num] = inactivity_pct
                
            logging.info(f"[TIMESFM] Excluse {len(numbers_to_exclude)} numere Hot inactive: {numbers_to_exclude}")
        else:
            logging.info("[TIMESFM] Niciun număr Hot nu a îndeplinit criteriile de excludere")
            
        # Aplicăm același proces pentru Joker dacă e cazul
        if self.game_type == "joker" and hasattr(self, 'hard_core_joker') and self.hard_core_joker:
            self._apply_timesfm_filter_joker()
    
    def _identify_cold_hot_numbers(self, freq: np.ndarray) -> tuple:
        """
        Identifică care numere din nucleul dur sunt Cold vs Hot
        Returnează: (cold_numbers, hot_numbers)
        """
        if not hasattr(self, 'hard_core') or not self.hard_core:
            return set(), set()
        
        # Calculăm frecvența mediană pentru a determina Cold vs Hot
        all_freqs = [freq[num-1] for num in self.hard_core if num-1 < len(freq)]
        if not all_freqs:
            return set(), set()
            
        median_freq = sorted(all_freqs)[len(all_freqs)//2]
        
        cold_numbers = set()
        hot_numbers = set()
        
        for num in self.hard_core:
            if num-1 < len(freq):
                if freq[num-1] <= median_freq:
                    cold_numbers.add(num)
                else:
                    hot_numbers.add(num)
        
        return cold_numbers, hot_numbers

    def _apply_smart_selector(self, freq: np.ndarray) -> None:
        """
        Smart Selector - sistem hibrid avansat care combină:
        1. Gap Analysis (40%) - Analiza intervalelor
        2. Trend Analysis (25%) - Analiza trend-urilor
        3. Hot/Cold Analysis (20%) - Frecvență istorică
        4. Positional Analysis (15%) - Analiza pozițională
        """
        if not hasattr(self, 'hard_core') or not self.hard_core:
            return
            
        logging.info("[SMART] Inițiere Smart Selector - analiză multi-factorială...")
        
        # Calculăm scoruri pentru fiecare metodă
        gap_scores = self._calculate_gap_scores()
        trend_scores = self._calculate_trend_scores()
        frequency_scores = self._calculate_frequency_scores(freq)
        positional_scores = self._calculate_positional_scores()
        
        # Combinăm scorurile cu ponderile specificate
        final_scores = {}
        for num in self.hard_core:
            final_score = (
                gap_scores.get(num, 0) * 0.40 +      # Gap Analysis
                trend_scores.get(num, 0) * 0.25 +    # Trend Analysis
                frequency_scores.get(num, 0) * 0.20 + # Hot/Cold
                positional_scores.get(num, 0) * 0.15  # Positional
            )
            final_scores[num] = final_score
        
        # Sortăm după scor final (descrescător)
        sorted_by_score = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        
        # Alegem top 70% din nucleu bazat pe scoruri
        # Păstrăm 80% din nucleu pentru a evita pierderea numerelor potențial câștigătoare
        keep_count = max(6, int(len(self.hard_core) * 0.8))
        best_numbers = [num for num, score in sorted_by_score[:keep_count]]
        
        # Găsim alternative pentru numerele excluse
        excluded_numbers = [num for num in self.hard_core if num not in best_numbers]
        
        if excluded_numbers:
            self._replace_with_smart_alternatives(excluded_numbers, best_numbers, freq)
            
            # Salvăm în audit
            if 'smart_selector' not in self.audit:
                self.audit['smart_selector'] = {}
            
            self.audit['smart_selector'] = {
                'method': 'Hybrid Analysis (40% Gap + 25% Trend + 20% Frequency + 15% Positional)',
                'kept_numbers': best_numbers,
                'replaced_numbers': excluded_numbers,
                'final_scores': {num: round(score, 3) for num, score in sorted_by_score}
            }
            
            logging.info(f"[SMART] Optimizat nucleul: păstrate {len(best_numbers)}, înlocuite {len(excluded_numbers)}")
    
    def _calculate_gap_scores(self) -> dict:
        """
        Gap Analysis (40% weight) - Analiza intervalelor între apariții
        Numerele "due" (care ar trebui să iasă) primesc scor maxim
        """
        if self.data is None:
            return {num: 0.5 for num in self.hard_core}
        
        gap_scores = {}
        total_draws = len(self.data)
        
        for num in self.hard_core:
            # Calculăm intervalul mediu istoric
            avg_gap = self._calculate_average_gap(num)
            
            # Calculăm de câte extrageri a trecut de la ultima apariție
            last_appearance = self._get_last_appearance(num)
            current_gap = total_draws - last_appearance
            
            # Scor: dacă current_gap > avg_gap, numărul e "due"
            if avg_gap == 0:
                score = 0.5  # Default dacă nu are istoric
            else:
                gap_ratio = current_gap / avg_gap
                # Scor mai mare dacă a trecut mai mult timp decât media
                score = min(1.0, gap_ratio * 0.7 + 0.3)  # Scale între 0.3-1.0
            
            gap_scores[num] = score
        
        logging.info(f"[SMART] Gap scores calculate: {gap_scores}")
        return gap_scores
    
    def _calculate_trend_scores(self) -> dict:
        """
        Trend Analysis (25% weight) - Analiza trend-urilor recente
        Identifică numerele în creștere/descreștere
        """
        if self.data is None:
            return {num: 0.5 for num in self.hard_core}
        
        trend_scores = {}
        recent_window = min(20, len(self.data) // 2)  # Ultimele 20 extrageri sau 50% din date
        
        for num in self.hard_core:
            # Calculăm frecvența în 2 perioade: recent vs mai vechi
            recent_freq = self._calculate_recent_frequency(num, recent_window)
            older_freq = self._calculate_recent_frequency(num, recent_window * 2, recent_window)
            
            # Trend scor: creștere = scor mai mare
            if older_freq == 0:
                score = 0.6 if recent_freq > 0 else 0.4
            else:
                trend_ratio = recent_freq / older_freq
                # Scale trend ratio la scor 0-1
                score = min(1.0, max(0.0, (trend_ratio - 0.5) * 2))
            
            trend_scores[num] = score
        
        logging.info(f"[SMART] Trend scores calculated: {trend_scores}")
        return trend_scores
    
    def _calculate_frequency_scores(self, freq: np.ndarray) -> dict:
        """
        Hot/Cold Analysis (20% weight) - Frecvența istorică normalizată
        """
        if freq.size == 0:
            return {num: 0.5 for num in self.hard_core}
        
        non_zero = freq[freq > 0]
        max_freq = np.max(non_zero) if non_zero.size else 1
        min_freq = np.min(non_zero) if non_zero.size else 1
        
        frequency_scores = {}
        for num in self.hard_core:
            if num - 1 < len(freq):
                actual_freq = freq[num - 1]
                # Normalizare frecvență la scor 0-1
                if max_freq == min_freq:
                    score = 0.5
                else:
                    score = (actual_freq - min_freq) / (max_freq - min_freq)
                frequency_scores[num] = score
            else:
                frequency_scores[num] = 0.5
        
        logging.info(f"[SMART] Frequency scores calculated: {frequency_scores}")
        return frequency_scores
    
    def _calculate_positional_scores(self) -> dict:
        """
        Positional Analysis (15% weight) - Analiza preferințelor poziționale
        Unele numere preferă anumite poziții (n1, n2, etc.)
        """
        if self.data is None or self._draw_matrix is None:
            return {num: 0.5 for num in self.hard_core}
        
        positional_scores = {}
        draw_n = self.params["draw_n"]
        
        for num in self.hard_core:
            position_counts = np.zeros(draw_n)
            
            # Numărăm aparițiile pe fiecare poziție
            for row in self._draw_matrix:
                for pos, val in enumerate(row):
                    if val == num:
                        position_counts[pos] += 1
            
            # Scor bazat pe consistența pozițională
            total_appearances = np.sum(position_counts)
            if total_appearances == 0:
                score = 0.5
            else:
                # Calculăm deviația standard - mai mică = mai consistent
                position_probs = position_counts / total_appearances
                expected_prob = 1.0 / draw_n
                variance = np.var(position_probs)
                
                # Scor mai mare dacă varianța e mică (poziții consistente)
                score = max(0.0, 1.0 - variance * draw_n)
            
            positional_scores[num] = score
        
        logging.info(f"[SMART] Positional scores calculated: {positional_scores}")
        return positional_scores
    
    def _get_last_appearance(self, num: int) -> int:
        """Returnează indexul ultimei apariții a unui număr"""
        if self._draw_matrix is not None:
            for i in range(len(self._draw_matrix) - 1, -1, -1):
                if num in self._draw_matrix[i]:
                    return i
        return -1
    
    def _calculate_recent_frequency(self, num: int, recent_draws: int, offset: int = 0) -> float:
        """Calculează frecvența unui număr într-o fereastră specifică"""
        if self.data is None or recent_draws <= 0:
            return 0.0
        
        end_idx = max(0, len(self.data) - offset)
        start_idx = max(0, end_idx - recent_draws)
        
        if start_idx >= end_idx:
            return 0.0
        
        count = 0
        if self._draw_matrix is not None and self._draw_matrix.size:
            window_matrix = self._draw_matrix[start_idx:end_idx]
            count = np.sum(window_matrix == num)
        
        return count / (end_idx - start_idx) if end_idx > start_idx else 0.0
    
    def _replace_with_smart_alternatives(self, excluded_numbers: list, kept_numbers: list, freq: np.ndarray) -> None:
        """Înlocuiește numerele excluse cu cele mai bune alternative bazate pe Smart Selector"""
        all_numbers = set(range(1, self.params["max_n"] + 1))
        current_pool = set(kept_numbers)
        available_numbers = all_numbers - current_pool
        
        # Calculăm scoruri pentru toate numerele disponibile
        alternative_scores = {}
        
        # Extindem calculul de scoruri pentru toate numerele disponibile
        temp_hard_core = list(available_numbers)
        original_hard_core = self.hard_core.copy()
        
        self.hard_core = temp_hard_core
        
        # Recalculăm scorurile pentru alternative
        gap_scores = self._calculate_gap_scores()
        trend_scores = self._calculate_trend_scores()
        frequency_scores = self._calculate_frequency_scores(freq)
        positional_scores = self._calculate_positional_scores()
        
        for num in available_numbers:
            final_score = (
                gap_scores.get(num, 0) * 0.40 +
                trend_scores.get(num, 0) * 0.25 +
                frequency_scores.get(num, 0) * 0.20 +
                positional_scores.get(num, 0) * 0.15
            )
            alternative_scores[num] = final_score
        
        # Restaurăm nucleul original
        self.hard_core = original_hard_core
        
        # Sortăm alternative după scor
        sorted_alternatives = sorted(alternative_scores.items(), key=lambda x: x[1], reverse=True)
        
        # Înlocuim numerele excluse
        new_hard_core = kept_numbers.copy()
        for i, excluded_num in enumerate(excluded_numbers):
            if i < len(sorted_alternatives):
                replacement = sorted_alternatives[i][0]
                new_hard_core.append(replacement)
                logging.info(f"[SMART] Înlocuit {excluded_num} cu {replacement} (scor: {sorted_alternatives[i][1]:.3f})")
        
        # Actualizăm nucleul dur
        self.hard_core = sorted(new_hard_core)
        
        # Aplicăm din nou filtrul anti-secvență dacă e necesar
        if hasattr(self, '_consecutive_filter_applied') and self._consecutive_filter_applied:
            self.hard_core = self._apply_consecutive_filter(self.hard_core, freq)
        
        self.hard_core_stats = {int(num): int(freq[num-1]) for num in self.hard_core if num-1 < len(freq)}
    
        
    def _calculate_average_gap(self, num: int) -> float:
        """Calculează intervalul mediu între aparițiile unui număr."""
        if self.data is None:
            return 0.0
            
        appearances = []
        
        if self._draw_matrix is not None and self._draw_matrix.size:
            for i, row in enumerate(self._draw_matrix):
                if num in row:
                    appearances.append(i)
        else:
            # Fallback
            for i, row in self.data.iterrows():
                try:
                    if "numbers" in row:
                        nums = [int(x) for x in str(row["numbers"]).split(",") if str(x).strip().isdigit()]
                    else:
                        n_cols = [c for c in self.data.columns if str(c).lower().startswith('n')]
                        nums = [int(row[c]) for c in n_cols if pd.notna(row.get(c))]
                    if num in nums:
                        appearances.append(i)
                except:
                    continue
        
        if len(appearances) < 2:
            return float(len(self.data))  # Dacă a apărut o dată sau niciodată
            
        gaps = [appearances[i+1] - appearances[i] for i in range(len(appearances)-1)]
        return sum(gaps) / len(gaps) if gaps else float(len(self.data))
    
    def _replace_inactive_numbers(self, excluded_numbers: list, freq: np.ndarray) -> None:
        """Înlocuiește numerele inactive cu cele mai bune alternative."""
        # Găsim cele mai bune alternative (cele mai frecvente numere care nu sunt în nucleu)
        all_numbers = set(range(1, self.params["max_n"] + 1))
        current_pool = set(self.hard_core)
        available_numbers = all_numbers - current_pool
        
        # Sortăm disponibile după frecvență (descrescător)
        available_sorted = sorted(available_numbers, 
                                key=lambda x: freq[x-1] if x-1 < len(freq) else 0, 
                                reverse=True)
        
        # Înlocuim numerele excluse
        for i, excluded_num in enumerate(excluded_numbers):
            if i < len(available_sorted):
                replacement = available_sorted[i]
                # Găsim indexul numărului exclus și îl înlocuim
                idx = self.hard_core.index(excluded_num)
                self.hard_core[idx] = replacement
                logging.info(f"[TIMESFM] Înlocuit {excluded_num} cu {replacement}")
        
        # Reactualizăm statisticile
        self.hard_core_stats = {int(num): int(freq[num-1]) for num in self.hard_core}
    
    def _apply_timesfm_filter_joker(self) -> None:
        """Aplică filtrul TimesFM și pentru numerele Joker."""
        if not hasattr(self, 'hard_core_joker') or not self.hard_core_joker:
            return
            
        freq_joker = self.analyze_joker_frequency()
        exclusion_candidates = {}
        
        for num in self.hard_core_joker:
            num_idx = num - 1
            if num_idx >= len(freq_joker):
                continue
                
            abs_freq = freq_joker[num_idx]
            total_joker_draws = np.sum(freq_joker)
            rel_freq = abs_freq / total_joker_draws if total_joker_draws > 0 else 0
            
            # Pentru Joker, criteriul e mai strict (excludem doar dacă frecvența e foarte mică)
            inactivity_score = 1 - rel_freq
            exclusion_candidates[num] = inactivity_score
        
        # Excludem doar dacă scorul > 0.8 (foarte rar)
        numbers_to_exclude = [num for num, score in exclusion_candidates.items() if score > 0.8]
        
        if numbers_to_exclude:
            # Găsim alternative
            all_joker = set(range(1, 21))  # Joker: 1-20
            current_joker = set(self.hard_core_joker)
            available_joker = all_joker - current_joker
            
            available_sorted = sorted(available_joker, 
                                    key=lambda x: freq_joker[x-1] if x-1 < len(freq_joker) else 0, 
                                    reverse=True)
            
            for i, excluded_num in enumerate(numbers_to_exclude):
                if i < len(available_sorted):
                    replacement = available_sorted[i]
                    idx = self.hard_core_joker.index(excluded_num)
                    self.hard_core_joker[idx] = replacement
            
            # Salvăm în audit
            if 'timesfm_excluded_joker' not in self.audit:
                self.audit['timesfm_excluded_joker'] = {}
                
            for num in numbers_to_exclude:
                inactivity_pct = int(exclusion_candidates[num] * 100)
                self.audit['timesfm_excluded_joker'][num] = inactivity_pct
                
            logging.info(f"[TIMESFM] Excluse {len(numbers_to_exclude)} numere Joker inactive: {numbers_to_exclude}")
            
        # Reactualizăm statisticile Joker
        self.hard_core_joker_stats = {int(num): int(freq_joker[num-1]) for num in self.hard_core_joker}

    @functools.lru_cache(maxsize=256)
    def _get_timesfm_scores(self, is_joker_drum: bool = False, context_len: int = 4096) -> Dict[int, float]:
        """
        TimesFM v2 Engine — folosește TOATE capabilitățile:
          1. forecast() cu normalize=True → point + 9 quantile (p10-p90)
          2. forecast_with_covariates() cu XReg real:
             - rolling_freq_10, rolling_freq_30 (dynamic numerical)
             - gap_counter, draw_sum (dynamic numerical)
             - number_norm, hist_frequency (static numerical)
          3. Context Anomaly Detection (return_forecast_on_context)
          4. Multi-Horizon Forecasting (H=20, weighted decay)
          5. Adaptive Local Tuning (bias correction)
        Rezultat: Neural Quality Index (NQI) per număr.
        """
        if _HAS_TFM_V2:
            return get_timesfm_scores_v2(
                data=self.data,
                draw_matrix=self._draw_matrix,
                params=self.params,
                is_joker_drum=is_joker_drum,
                context_len=context_len,
                audit=self.audit,
            )
        # Fallback la implementarea veche dacă modulul v2 nu e disponibil
        return self._get_timesfm_scores_legacy(is_joker_drum, context_len)

    def _get_timesfm_scores_legacy(self, is_joker_drum: bool = False, context_len: int = 4096) -> Dict[int, float]:
        """
        Interfața ULTRA cu Google TimesFM: Analiză multi-orizont, stabilitate (cuantile) și trend.
        """
        if not HAS_TIMESFM:
            logging.error("[TIMESFM] Nu se poate rula predicția. Librăria 'timesfm' lipsește.")
            return {}
        
        device = "cpu"
        try:
            if torch.cuda.is_available():
                device = "gpu"
        except: pass

        total_available = len(self.data) if self.data is not None else 0
        limit = min(context_len, total_available)
        
        if limit < 32:
            return {}

        try:
            global _GLOBAL_LEGACY_TFM
            if _GLOBAL_LEGACY_TFM is not None:
                tfm = _GLOBAL_LEGACY_TFM
                logging.info("[TIMESFM] Folosim modelul legacy din memorie (HOT START).")
            else:
                logging.info("[TIMESFM] Încărcăm modelul legacy în memorie (COLD START)...")
                # Configurăm orizontul la 5 pentru analiza de trend
                # Parametri optimizați pentru TimesFM 2.0/2.5 (suportă context mai mare și orizont extins)
                tfm = timesfm.TimesFm(
                    hparams=timesfm.TimesFmHparams(
                        context_len=4096, # Extins la 4096 pentru a suporta istoric complet
                        horizon_len=20,   # Capacitate maximă pentru predicții pe termen lung
                        input_patch_len=32,
                        output_patch_len=128,
                        num_layers=50,
                        use_positional_embedding=False,
                        backend=device
                    ),
                    checkpoint=timesfm.TimesFmCheckpoint(
                        huggingface_repo_id="google/timesfm-2.0-500m-pytorch"
                    )
                )
                _GLOBAL_LEGACY_TFM = tfm
            
            # Funcția 3: Pregătire Covariate (XReg Simulation) va fi apelată în interiorul buclei de ferestre.

            
            max_num = 20 if is_joker_drum else int(self.params["max_n"])
            windows = []
            stride = 512
            current_end = total_available
            current_start = max(0, current_end - limit)
            
            pos = total_available
            while pos > current_start + 32:
                w_start = max(current_start, pos - 4096)
                windows.append((w_start, pos))
                if pos <= 512 or (pos - stride) < current_start + 32: break
                pos -= stride
            
            # Structuri pentru NQI (Neural Quality Index)
            nqi_components = {n: {"point": 0.0, "stability": 0.0, "trend": 0.0} for n in range(1, max_num + 1)}
            total_weight = 0.0
            
            for idx, (w_start, w_end) in enumerate(windows):
                covariates = self._generate_loto_covariates(w_start, w_end)
                weight = 1.0 / (1.5 ** idx)
                total_weight += weight
                
                all_series = []
                num_map = []
                
                current_draw_slice = []
                if is_joker_drum:
                    current_draw_slice = self.data['joker'].values[w_start:w_end]
                else:
                    if self._draw_matrix is not None:
                        current_draw_slice = self._draw_matrix[w_start:w_end]

                for num in range(1, max_num + 1):
                    series = []
                    if is_joker_drum:
                        for val in current_draw_slice:
                            series.append(1.0 if int(val) == num else 0.0)
                    else:
                        for draw in current_draw_slice:
                            series.append(1.0 if num in draw else 0.0)
                    
                    if len(series) >= 32:
                        all_series.append(np.array(series, dtype=np.float32))
                        num_map.append(num)
                
                if all_series:
                    # Rulăm forecast-ul (ne dă point_forecast și full_forecast cu cuantile)
                    # point_forecast: (batch, horizon)
                    # full_forecast: (batch, horizon, num_quantiles)
                    point_forecast, full_forecast = tfm.forecast(inputs=all_series, freq=[0] * len(all_series))
                    
                    for i, num in enumerate(num_map):
                        # 1. Scorul Punctual (imediat)
                        p_val = float(point_forecast[i][0])
                        
                        # 2. Analiza de Stabilitate (Incertitudinea Cuantilelor)
                        # Cuantele standard TimesFM: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
                        # Indicele 0 e p10, Indicele 8 e p90
                        q10 = float(full_forecast[i, 0, 0])
                        q90 = float(full_forecast[i, 0, 8])
                        uncertainty = max(0.0001, q90 - q10)
                        stability = 1.0 / (1.0 + uncertainty) # Mai stabil = scor mai mare
                        
                        # 3. Analiza de Trend Multi-Orizont (Funcția 2)
                        future_values = point_forecast[i, :] # Toate cele 20 puncte viitoare
                        
                        # Calculăm un scor ponderat pe tot orizontul (decaying weight)
                        horizon_score = 0.0
                        for h_idx, h_val in enumerate(future_values):
                            h_weight = 1.0 / (1.2 ** h_idx) # Pondere mai mare pentru viitorul apropiat
                            horizon_score += float(h_val) * h_weight
                        
                        # Funcția 5: Adaptive Tuning (Compensarea erorilor recente)
                        # Dacă numărul a ieșit recent dar modelul l-a subestimat, aplicăm un bias de corecție
                        recent_bias = self._calculate_adaptive_bias(num)
                        
                        # Funcția 3: Integrare Covariate
                        # Ajustăm scorul în funcție de corelația cu variabilele externe (ziua săptămânii, etc)
                        covariate_bonus = self._apply_covariate_logic(num, covariates)

                        nqi_components[num]["point"] += p_val * weight * recent_bias
                        nqi_components[num]["stability"] += stability * weight
                        nqi_components[num]["trend"] += (horizon_score + covariate_bonus) * weight
            
            # Calculăm NQI Final
            final_scores = {}
            if total_weight > 0:
                for num in range(1, max_num + 1):
                    p = nqi_components[num]["point"] / total_weight
                    s = nqi_components[num]["stability"] / total_weight
                    t = nqi_components[num]["trend"] / total_weight
                    # NQI combină toți factorii: Predicție x Siguranță x Viitor Multi-Orizont
                    final_scores[num] = p * s * t
            
            return final_scores


        except Exception as e:
            logging.error(f"[TIMESFM] Eroare la execuția modelului avansat: {e}")
            return {}

    def _get_timesfm_regressive_blacklist(self, sim_depth_pct: int) -> Set[int]:
        """
        Analiză Regresivă multi-pass folosind Google TimesFM v2.
        Rulează scoring pe ferestre de 100%, 90%, 80%... și elimină doar intersecția.
        """
        if _HAS_TFM_V2 and self.data is not None:
            return get_regressive_blacklist_v2(
                data_full=self.data,
                draw_matrix_full=self._draw_matrix,
                params=self.params,
                sim_depth_pct=sim_depth_pct,
                rebuild_matrix_fn=self._build_draw_matrix,
                audit=self.audit,
            )
        # Fallback legacy
        if not HAS_TIMESFM: return set()
        logging.info(f"[TIMESFM] Începe Analiza Regresivă Neurală (Prag: {sim_depth_pct}%)...")
        steps = list(range(100, sim_depth_pct - 1, -10))
        all_blacklists = []
        original_data = self.data.copy() if self.data is not None else None
        for step in steps:
            num_rows = int(len(original_data) * (step / 100))
            if num_rows < 32: continue
            self.data = original_data.tail(num_rows).copy()
            self._build_draw_matrix()
            logging.info(f"[TIMESFM] Pas Regresiv: {step}% din istoric ({num_rows} extrageri)...")
            scores = self._get_timesfm_scores_legacy(context_len=num_rows)
            if scores:
                all_values = list(scores.values())
                threshold = np.percentile(all_values, 25)
                current_blacklist = {num for num, score in scores.items() if score <= threshold}
                all_blacklists.append(current_blacklist)
        self.data = original_data
        self._build_draw_matrix()
        if not all_blacklists: return set()
        final_blacklist = all_blacklists[0]
        for bl in all_blacklists[1:]:
            final_blacklist = final_blacklist.intersection(bl)
        logging.info(f"[TIMESFM] Analiză Regresivă finalizată. {len(final_blacklist)} numere.")
        return final_blacklist

    def _get_timesfm_pool(self, scores: Dict[int, float], pool_size: int, blacklist: Set[int]) -> List[int]:
        """
        Metoda principală de selecție a numerelor (Pool) bazată pe TimesFM v3.
        Folosește selecție diversificată pe zone (low/mid/high) pentru maximizarea hit-urilor.
        """
        if not scores:
            # Fallback pe frecvență dacă TimesFM e indisponibil
            freq = self.analyze_frequency()
            return self._get_initial_hard_core(freq, pool_size=pool_size, blacklist=blacklist)

        max_num = int(self.params.get("max_n", 49))
        
        # Folosim selectorul diversificat din v3 engine
        if _HAS_TFM_V2:
            pool = select_pool_from_scores(scores, pool_size, blacklist, self.audit, max_num=max_num)
        else:
            # Fallback legacy
            valid_scores = {num: score for num, score in scores.items() if num not in blacklist}
            sorted_nums = sorted(valid_scores.items(), key=lambda x: x[1], reverse=True)
            pool = [num for num, score in sorted_nums[:pool_size]]
        
        # Garanție pool complet: Dacă filtrele/blacklist-ul au fost prea agresive, completăm
        if len(pool) < pool_size:
            logging.warning(f"[TIMESFM] Pool incomplet ({len(pool)}/{pool_size}). Se completează cu cele mai bune numere din blacklist.")
            
            # Luăm numerele din blacklist, sortate după scor (cele mai bune dintre cele "rele")
            blacklisted_nums = [(num, score) for num, score in scores.items() if num in blacklist and num not in pool]
            sorted_blacklisted = sorted(blacklisted_nums, key=lambda x: x[1], reverse=True)
            
            needed = pool_size - len(pool)
            for num, _ in sorted_blacklisted[:needed]:
                pool.append(num)
                
            # Dacă tot nu avem destule (e.g. scores e incomplet), fallback final pe frecvență globală
            if len(pool) < pool_size:
                logging.warning(f"[TIMESFM] Pool încă incomplet ({len(pool)}/{pool_size}). Fallback final pe frecvență globală.")
                freq = getattr(self, 'freq', None)
                if freq is None:
                    freq = self.analyze_frequency()
                
                sorted_freq_indices = np.argsort(freq)[::-1]
                for idx in sorted_freq_indices:
                    num = int(idx) + 1
                    if num not in pool:
                        pool.append(num)
                        if len(pool) >= pool_size:
                            break
        
        return sorted(pool[:pool_size])


    def _generate_loto_covariates(self, start: int, end: int) -> dict:
        """Generează variabile externe (covariate) pentru fereastra de analiză."""
        if self.data is None: return {}
        subset = self.data.iloc[start:end]
        
        # Exemplu: Trendul sumei numerelor (indicator de 'căldură' a urnei)
        if self._draw_matrix is not None:
            draw_sums = np.sum(self._draw_matrix[start:end], axis=1)
            avg_sum = np.mean(draw_sums) if draw_sums.size > 0 else 0
        else:
            avg_sum = 0
            
        return {
            "avg_sum_trend": avg_sum,
            "is_recent": 1.0 if end > len(self.data) - 50 else 0.5
        }

    def _apply_covariate_logic(self, num: int, covariates: dict) -> float:
        """Ajustează scorul în funcție de variabilele externe."""
        # Exemplu: Dacă urna e 'fierbinte' (sume mari), favorizăm numerele mari
        bonus = 0.0
        if covariates.get("avg_sum_trend", 0) > 150 and num > 30:
            bonus += 0.05
        return bonus

    def _calculate_adaptive_bias(self, num: int) -> float:
        """
        Funcția 5: Adaptive Tuning.
        Calculează un factor de corecție bazat pe performanța recentă a modelului.
        """
        if self.data is None or len(self.data) < 10: return 1.0
        # Verificăm dacă numărul a ieșit în ultimele 5 extrageri
        recent_count = self._calculate_recent_frequency(num, 5)
        if recent_count > 0:
            return 1.15 # Bias pozitiv pentru numere 'on fire' neidentificate de modelul static
        return 1.0

    def calculate_variant_anomaly_score(self, variant: List[int], scores: Dict[int, float]) -> float:
        """
        Funcția 4: Anomaly Scoring.
        Evaluează cât de 'anormală' este o variantă față de predicția neurală.
        Scor mic = variantă conformă cu modelul. Scor mare = anomalie (probabilitate mică).
        """
        if not scores: return 0.0
        
        # Calculăm probabilitatea medie a variantei conform TimesFM
        variant_probs = [scores.get(num, 0.0001) for num in variant]
        mean_prob = np.mean(variant_probs)
        
        # Scor de anomalie bazat pe distanța față de topul modelului
        max_prob = max(scores.values()) if scores else 1.0
        anomaly = 1.0 - (mean_prob / max_prob)
        
        return float(anomaly)

    def filter_variants_by_anomaly(self, variants: List[List[int]], scores: Dict[int, float], threshold: float = 0.7) -> List[List[int]]:
        """Elimină variantele considerate anomalii statistice de model (v2 cu safe fallback)."""
        if _HAS_TFM_V2:
            return filter_variants_by_anomaly_v2(variants, scores, threshold)
        filtered = []
        for v in variants:
            score = self.calculate_variant_anomaly_score(v, scores)
            if score <= threshold:
                filtered.append(v)
        return filtered if filtered else variants

def create_demo_data(csv_path: str, game: str = "6/49") -> None:

    """Creează date demo pentru testare."""
    params = {
        "6/49": {"max_n": 49, "draw_n": 6},
        "5/40": {"max_n": 40, "draw_n": 5},
        "joker": {"max_n": 45, "draw_n": 6},
    }

    game_params = params.get(game, params["6/49"])
    rng = np.random.default_rng(42)

    rows = []
    for i in range(50):
        numbers = sorted(
            rng.choice(np.arange(1, game_params["max_n"] + 1), size=game_params["draw_n"], replace=False).tolist()
        )
        rows.append({f"n{k+1}": int(numbers[k]) for k in range(game_params["draw_n"])})

    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    print(f"Date demo create în {csv_path}")
