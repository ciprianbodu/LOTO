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
# Pe CPU-only (CUDA_VISIBLE_DEVICES=-1 setat de START_8000.bat) sau cand
# LOTO_SKIP_TIMESFM=1, SAREM importul ca sa nu pierdem 30-60s la cold start.
HAS_TIMESFM = False
TIMESFM_ERROR = None
torch = None
timesfm = None
_SKIP_TIMESFM = (
    os.environ.get("LOTO_SKIP_TIMESFM") == "1"
    or os.environ.get("CUDA_VISIBLE_DEVICES") == "-1"
)
if _SKIP_TIMESFM:
    TIMESFM_ERROR = "Skip torch+timesfm (CPU-only sau LOTO_SKIP_TIMESFM=1)"
    logging.info(f"[TIMESFM] {TIMESFM_ERROR} - folosesc fallback determinist.")
else:
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

# Adaptive feedback (învățare persistentă post-extragere)
try:
    from loto_enterprise.core.adaptive_feedback import (
        load_adaptive_state,
        save_adaptive_state,
        compute_post_draw_feedback,
        record_predicted_pool,
        get_state_summary,
    )
    _HAS_ADAPTIVE = True
except ImportError:
    _HAS_ADAPTIVE = False

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

    # P3: cache ticket→target-set. Același ticket reapare la iterații diferite
    # (base_ticket overlap) → evităm recalcul itertools.combinations. Bit-identic.
    _tt_cache: dict = {}

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
        
        search_count = 0
        if len(remaining_pool) >= (pick - guarantee):
            # itertools.combinations pe un pool sortat va genera combinații
            # începând cu cele mai bune numere (scor maxim).
            for extra_nums in itertools.combinations(remaining_pool, pick - guarantee):
                ticket = tuple(sorted(list(base_ticket) + list(extra_nums)))

                # Evaluăm rapid (cu cache pe ticket — P3)
                ticket_targets = _tt_cache.get(ticket)
                if ticket_targets is None:
                    ticket_targets = set(itertools.combinations(ticket, guarantee))
                    _tt_cache[ticket] = ticket_targets
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
            if iteration % 20 == 0 or len(covered_targets) == total_targets:
                logging.info(f"[WHEEL] Progres {iteration}: Acoperite {len(covered_targets)}/{total_targets} ținte. Bilete: {len(wheel)}")
        else:
            logging.warning("[WHEEL] Nu am găsit acoperire suplimentară, oprire timpurie.")
            break
            
        if iteration > 1000:  # Timeout extins pt pool-uri mari, dar mult mai rapid
            logging.warning(f"[WHEEL] TIMEOUT: 1000 iterații.")
            break
            
    coverage_pct = 100.0 if total_targets == 0 else round((len(covered_targets) / total_targets) * 100, 2)
    logging.info(f"[WHEEL] Generare completă în {time.time() - start_time:.2f}s. Total variante: {len(wheel)}. Acoperire: {coverage_pct}%")
    return wheel, coverage_pct


def hypergeometric_hit_forecast(pool_size: int, draw_n: int, max_n: int, n_draws: int = 127) -> dict:
    """
    Calculează probabilitatea teoretică P(k+ hits) pentru un pool RANDOM și
    recomandă pool-ul minim necesar pentru a vedea ≥3 evenimente 4+ și 5+
    pe `n_draws` extrageri.

    Asta e baseline-ul matematic ABSOLUT — orice engine trebuie să bată
    cel puțin aceste valori pe 3+ ca să fie util. Pe 4+ și 5+ pe pool=10
    evenimentele așteptate sunt deja <2/127, deci varianța observațională
    e enormă (un engine bun poate părea "blocat la 0%" doar prin noroc rău).

    Folosit de UI pentru a recomanda automat pool-uri mai mari când userul
    vrea 4+ hits stabile statistic.

    Returns dict cu:
        - random_baseline.P(k+)% per k=1..draw_n
        - random_baseline.E(k+)/n (evenimente așteptate)
        - recommendations.pool_for_3_events_4+ (minim pool pentru ≥3 evt 4+)
        - recommendations.pool_for_3_events_5+ (minim pool pentru ≥3 evt 5+)
    """
    try:
        from math import comb
    except ImportError:
        return {}
    if pool_size > max_n or draw_n > max_n or draw_n <= 0:
        return {}
    total_combos = comb(max_n, draw_n)
    if total_combos == 0:
        return {}

    def p_exactly_k(k, pool):
        if k < 0 or k > draw_n or k > pool:
            return 0.0
        return comb(pool, k) * comb(max_n - pool, draw_n - k) / total_combos

    forecast: dict = {"pool_size": pool_size, "n_draws": n_draws, "random_baseline": {}}
    for k in range(1, draw_n + 1):
        p_geq_k = sum(p_exactly_k(j, pool_size) for j in range(k, draw_n + 1))
        forecast["random_baseline"][f"P({k}+)%"] = round(p_geq_k * 100, 4)
        forecast["random_baseline"][f"E({k}+)/n"] = round(p_geq_k * n_draws, 2)

    target_events = 3
    recommendations: dict = {}
    for target_k in (4, 5):
        rec_pool = None
        for trial_pool in range(pool_size, min(max_n, pool_size + 20) + 1):
            p_k = sum(
                comb(trial_pool, j) * comb(max_n - trial_pool, draw_n - j) / total_combos
                for j in range(target_k, draw_n + 1)
            )
            if p_k * n_draws >= target_events:
                rec_pool = trial_pool
                break
        recommendations[f"pool_for_3_events_{target_k}+"] = rec_pool
    forecast["recommendations"] = recommendations
    return forecast


def _audit_gpu_probe() -> str:
    """Întoarce 'gpu' dacă torch vede CUDA, altfel 'cpu'. Folosit pt indicatorul
    ⚡GPU/🐌CPU din audit — evaluat în procesul worker (device-ul REAL de calcul)."""
    try:
        import torch as _t
        return "gpu" if _t.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


class LotoEngine:
    # Routes _get_timesfm_scores through loto_enterprise.core.method_selector
    # — uses the model that WON the benchmark for this game/pool instead of
    # hard-coded TimesFM. Default ON (spec: integrează câștigătorul în app).
    # Set env LOTO_USE_BENCH_WINNER=0 to revert to the legacy TimesFM-only
    # path (e.g. for A/B comparison).
    use_bench_winner: bool = bool(int(os.environ.get("LOTO_USE_BENCH_WINNER", "1")))

    def __init__(self, game_type: str = "6/49"):
        self.game_type = game_type
        self.params = self._get_game_params(game_type)
        self.audit: dict = {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "timesfm_error": TIMESFM_ERROR,
            "compute_device": _audit_gpu_probe(),  # "gpu"/"cpu" real (torch.cuda in worker)
        }
        self.hard_core: list = []
        self.hard_core_stats: dict = {}
        self.hard_core_joker_stats: dict = {}
        self.data: pd.DataFrame | None = None
        self.arena2_index = None
        self._draw_matrix: np.ndarray | None = None
        self.error_correction_map: Dict[int, float] = {}  # num -> bias_multiplier
        # Pool size hint for method_selector (the wheeling pool, typically 12)
        self._winner_pool_hint: int = 12

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

    def _extract_draw_at_index(self, idx: int) -> Optional[List[int]]:
        """Returnează numerele extrase la index-ul `idx` (0-based) din self.data
        sau None dacă nu există / nu sunt valide.
        """
        if self.data is None or idx < 0 or idx >= len(self.data):
            return None
        if self._draw_matrix is not None and idx < len(self._draw_matrix):
            row = self._draw_matrix[idx]
            nums = [int(v) for v in row if int(v) > 0]
            if nums:
                return nums
        # Fallback prin coloane n*
        try:
            row = self.data.iloc[idx]
            n_cols = sorted(
                [c for c in self.data.columns
                 if str(c).lower().startswith("n") and str(c).lower() != "numbers"],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = primele 5 (Cat. I), nu toate 6
            nums = []
            for c in n_cols:
                if c in row and pd.notna(row[c]):
                    try:
                        nums.append(int(row[c]))
                    except (ValueError, TypeError):
                        continue
            return nums if nums else None
        except Exception:
            return None

    def _extract_date_at_index(self, idx: int) -> Optional[str]:
        """Returnează data extragerii la index-ul `idx` ca string sau None."""
        if self.data is None or idx < 0 or idx >= len(self.data):
            return None
        try:
            row = self.data.iloc[idx]
            for col in ("date", "data", "draw_date", "Data"):
                if col in row and pd.notna(row[col]):
                    return str(row[col])
        except Exception:
            pass
        return None

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
            n_cols = sorted(
                [c for c in self.data.columns if str(c).lower().startswith('n')],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = primele 5 (Cat. I)
            if n_cols:
                raw_vals = self.data[n_cols].values.ravel()
                all_numbers = raw_vals[~np.isnan(raw_vals)].astype(int).tolist()
            elif "numbers" in self.data.columns:
                for _, row in self.data.iterrows():
                    try:
                        nums = [int(x) for x in str(row["numbers"]).split(",") if str(x).strip().isdigit()]
                        all_numbers.extend(nums)
                    except (ValueError, TypeError) as exc:
                        logging.debug("analyze_frequency: skip row (parse): %s", exc)
                        continue
        
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

        # Wheeling: implicit greedy (bit-identic). Alternative selectabile prin env
        # LOTO_WHEEL_METHOD = ilp|annealing|genetic|lajolla (fallback la greedy).
        _wheel_method = os.environ.get("LOTO_WHEEL_METHOD", "greedy").strip().lower()
        if _wheel_method and _wheel_method != "greedy":
            from wheeling_methods import generate_wheel
            variants, coverage_pct = generate_wheel(
                _wheel_method,
                pool=self.hard_core,
                pick=self.params["draw_n"],
                guarantee=guarantee,
                max_variants=max_variants,
                scores=scores,
            )
        else:
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

    def run_institutional_pipeline(self, progress_cb=None, pool_size=12, guarantee=4, max_variants=0, lookback=0, filter_consecutives=False, smart_reduction=True, sim_depth_pct=10, enable_adaptive_persistence=False, pure_bench_mode=False, manual_blacklist=None):
        """Rulează pipeline-ul complet de analiză.

        enable_adaptive_persistence: Dacă True (live mode), încarcă/salvează
            adaptive_state.json — învățare persistentă din extrageri reale.
            Backtester-ul îl lasă False (își gestionează propriul state in-memory).

        manual_blacklist: Set/listă de numere pe care utilizatorul vrea să le
            excludă din selecție (ex: butonul "Inverseaza Pool" din UI care
            tratează pool-ul anterior ca blacklist). Se combină cu blacklist-ul
            calculat automat de motor, cu safety check anti-saturare.
        """
        # Memoram pool_size-ul cerut pentru ca _scores_via_bench_winner să poată
        # selecta câștigătorul corect din best_methods.json (per pool size).
        self._winner_pool_hint = int(pool_size)
        self.audit["pool_size_requested_by_ui"] = int(pool_size)
        logging.info(
            f"[PIPELINE] ▶ pool_size primit de la UI = {pool_size}, guarantee = {guarantee}"
        )

        # Pe pool=10, escalăm la guarantee=draw_n (full wheel) — C(10,draw_n)
        # maximizează numărul de bilete cu 4+/5 hits când pool union e mare.
        # Variantele sunt sortate după NQI sum descendent — primele bilete sunt
        # cele mai concentrate pe top-scoruri.
        draw_n_for_game = int(self.params.get("draw_n", 6))
        if pool_size == 10 and guarantee < draw_n_for_game:
            logging.info(
                "[PIPELINE] Pool=10 detectat: escalez guarantee de la %d la %d "
                "(full wheel C(10,%d) pentru maximizare hit-uri 4+/5).",
                guarantee, draw_n_for_game, draw_n_for_game,
            )
            guarantee = draw_n_for_game
            self.audit["full_wheel_pool10"] = True

        # === ADAPTIVE FEEDBACK PRE-RUN: detectăm extrageri reale apărute de la
        # ultima predicție și ajustăm error_correction_map ÎNAINTE de TimesFM. ===
        adaptive_event = None
        adaptive_info = None
        if enable_adaptive_persistence and _HAS_ADAPTIVE and self.data is not None:
            try:
                state = load_adaptive_state(self.game_type, pool_size)
                last_rows = int(state.get("last_data_rows", 0))
                current_rows = int(len(self.data))
                last_pool = state.get("last_pool", [])

                if last_pool and current_rows > last_rows:
                    new_actual = self._extract_draw_at_index(last_rows)
                    if new_actual:
                        rs = state.get("regime_state", {})
                        new_map, adaptive_event, adaptive_info = compute_post_draw_feedback(
                            last_pool=last_pool,
                            actual_draw=new_actual,
                            current_map=state.get("error_correction_map", {}),
                            history=state.get("history", []),
                            game_type=self.game_type,
                            pool_size=pool_size,
                            streak_zero=int(rs.get("streak_zero", 0)),
                            prev_mode=rs.get("active_mode", "normal"),
                            reset_duration=int(rs.get("reset_duration", 0)),
                        )
                        self.error_correction_map = new_map
                        # Persistăm istoricul + map-ul actualizat
                        history = list(state.get("history", []))
                        history.append({
                            "date": self._extract_date_at_index(last_rows),
                            "pool_hits": int(adaptive_info["pool_hits"]),
                            "actual": [int(n) for n in new_actual],
                            "event": adaptive_event,
                        })
                        state["error_correction_map"] = new_map
                        state["history"] = history[-50:]
                        state["regime_state"] = {
                            "streak_zero": int(adaptive_info["streak_zero"]),
                            "rolling_avg": (float(adaptive_info["rolling_avg"])
                                            if adaptive_info.get("rolling_avg") is not None else None),
                            "last_reset": (self._extract_date_at_index(last_rows)
                                           if adaptive_info["active_mode"] == "reset"
                                           else state.get("regime_state", {}).get("last_reset")),
                            "active_mode": adaptive_info["active_mode"],
                            "reset_duration": int(adaptive_info.get("reset_duration", 0)),
                        }
                        save_adaptive_state(self.game_type, pool_size, state)
                        logging.info(
                            f"[ADAPTIVE] Eveniment={adaptive_event} | hits={adaptive_info['pool_hits']} | "
                            f"streak_zero={adaptive_info['streak_zero']} | mode={adaptive_info['active_mode']} | "
                            f"missed={adaptive_info['missed']} | fp={adaptive_info['false_positives']}"
                        )
                # Stocăm evenimentul pentru consum în pipeline (diversificare/regime mode)
                self._adaptive_event = adaptive_event
                self._adaptive_info = adaptive_info
                self._adaptive_mode = (
                    adaptive_info["active_mode"] if adaptive_info else
                    state.get("regime_state", {}).get("active_mode", "normal")
                )
                # Hard Inversion Temporară: dacă tocmai am avut catastrofă,
                # excludem pool-ul ratat la următoarea selecție (1 extragere).
                try:
                    from loto_enterprise.core.adaptive_feedback import compute_temp_blacklist as _compute_temp_bl
                    evaluated = (adaptive_info or {}).get("evaluated_pool") if adaptive_info else None
                    if evaluated is None:
                        evaluated = state.get("last_pool", [])
                    self._temp_blacklist = _compute_temp_bl(
                        last_pool=list(evaluated or []),
                        last_event=adaptive_event,
                        universe_size=int(self.params.get("max_n", 49)),
                        pool_size=int(pool_size),
                        enable_full_inversion=True,
                    )
                    if self._temp_blacklist:
                        logging.warning(
                            f"[HARD-INVERSION] Catastrofă detectată — excludem temporar "
                            f"{sorted(self._temp_blacklist)} din pool-ul următor."
                        )
                except Exception as _e_inv:
                    logging.error(f"[HARD-INVERSION] Eroare la calcul temp_blacklist: {_e_inv}")
                    self._temp_blacklist = set()
            except Exception as e:
                logging.error(f"[ADAPTIVE] Eroare la procesarea feedback-ului: {e}")
                self._adaptive_event = None
                self._adaptive_info = None
                self._adaptive_mode = "normal"
                self._temp_blacklist = set()
        else:
            # NU suprascriem dacă au fost setate extern (e.g. de backtester
            # care îi pasează regime_mode în mod manual).
            if not hasattr(self, "_adaptive_event"):
                self._adaptive_event = None
            if not hasattr(self, "_adaptive_info"):
                self._adaptive_info = None
            if not hasattr(self, "_adaptive_mode"):
                self._adaptive_mode = "normal"
            if not hasattr(self, "_temp_blacklist"):
                self._temp_blacklist = set()

        logging.info(f"[PIPELINE] Inițializare scoring neural (câștigător bench / TimesFM) [pool_size={pool_size}, guarantee={guarantee}, max_variants={max_variants}, lookback={lookback}%, smart_reduction={smart_reduction}]...")
        
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
            
        # Calibrare bară progres: TimesFM e cea mai lungă etapă (~3-15s pe GPU,
        # 15-30s pe CPU). Înainte: salt 15→45% cu 14s tăcere — UI părea blocat.
        # Acum: callback per fereastră de ensemble → progres aproape continuu.
        # Etichetă progres = metoda REALĂ de scoring (câștigătorul bench dacă use_bench_winner
        # e ON), nu mereu „TimesFM v2" (înșelător — de fapt rulează ex. torch_bilstm_deep).
        _score_lbl = "TimesFM v2"
        if self.use_bench_winner:
            try:
                from loto_enterprise.core.method_selector import get_winner_name
                _gk = {"6/49": "loto_6_49", "5/40": "loto_5_40",
                       "joker": "joker_urna1"}.get(self.game_type, "loto_6_49")
                _wn = get_winner_name(_gk, pool_size=int(getattr(self, "_winner_pool_hint", 16)))
                if _wn:
                    _score_lbl = _wn
            except Exception:  # noqa: BLE001
                pass
        if progress_cb:
            progress_cb(f"🧠 Scoring neural: {_score_lbl} (ctx={actual_lookback})...", 15)

            def _tfm_window_progress(done, total, current_ctx):
                # Map [done/total] în intervalul [15, 45] = 30 puncte
                pct = 15 + int((done / max(total, 1)) * 30)
                progress_cb(f"🧠 {_score_lbl}: fereastră {done}/{total} (ctx={current_ctx})", pct)

            self._tfm_window_cb = _tfm_window_progress
        else:
            self._tfm_window_cb = None

        start_gpu = time.perf_counter()
        try:
            tfm_scores = self._get_timesfm_scores(context_len=actual_lookback)
        finally:
            self._tfm_window_cb = None  # Cleanup ca să nu polueze apeluri viitoare
        gpu_time = (time.perf_counter() - start_gpu) * 1000

        if progress_cb:
            progress_cb(f"🧠 {_score_lbl} complet ({gpu_time/1000:.1f}s). Procesez feedback adaptive...", 45)
        
        if 'performance' not in self.audit:
            self.audit['performance'] = {}
        self.audit['performance']['gpu_time_ms'] = round(gpu_time, 2)
        logging.info(f"[PIPELINE] Scoring neural ({_score_lbl}) GPU Time: {gpu_time:.2f}ms")

        # Aplicăm corecția de feedback (Adaptive Local Tuning) ANTERIOR rulării TimesFM
        if self.error_correction_map:
            logging.info(f"[PIPELINE] Aplicare feedback BEFORE TimesFM pe {len(self.error_correction_map)} numere...")
            for num, multiplier in self.error_correction_map.items():
                if num in tfm_scores:
                    tfm_scores[num] *= multiplier
            self.audit['feedback_correction'] = {str(k): round(v, 3) for k, v in self.error_correction_map.items()}

        # 5. Filtru Regresiv Multi-Timeframe (Smart Reduction)
        # Engine-ul consultă acum decizia bench (should_use_blacklist) per
        # (joc, pool). Dacă bench spune NO, _get_timesfm_regressive_blacklist
        # întoarce instant set() — fără cost suplimentar. Dacă spune YES,
        # rulează multi-window cu scorerul câștigător (fedformer/deepar/etc).
        blacklist = set()
        self.audit["sim_depth_pct"] = sim_depth_pct
        if smart_reduction:
            if progress_cb:
                progress_cb(f"Analiză regresivă multi-pass (depth={sim_depth_pct}%, ~3-10s)...", 50)

                def _regressive_step_progress(done, total, step_pct):
                    # Map [done/total] în intervalul [50, 65] = 15 puncte
                    pct = 50 + int((done / max(total, 1)) * 15)
                    progress_cb(f"Regresiv: pas {done}/{total} (fereastră {step_pct}%)", pct)

                self._regressive_step_cb = _regressive_step_progress
            else:
                self._regressive_step_cb = None

            logging.info(f"[PIPELINE] Activare Analiză Regresivă Neurală (depth: {sim_depth_pct}%)...")
            try:
                blacklist = self._get_timesfm_regressive_blacklist(sim_depth_pct)
            finally:
                self._regressive_step_cb = None
            # Modelul efectiv folosit pentru blacklist (poate fi diferit de scorerul principal
            # dacă bench dezactivează blacklist-ul pentru acest pool)
            bl_audit = self.audit.get("blacklist", {})
            scorer_lbl = bl_audit.get("regressive_scorer", "TimesFM v2 (legacy)")
            self.audit['reduction_filter'] = {
                'combined_blacklist': list(blacklist),
                'total_blocked': len(blacklist),
                'model_used': scorer_lbl,
                'sim_depth_pct': sim_depth_pct,
                'disabled_by_bench': scorer_lbl == "DISABLED_BY_BENCH",
            }
        else:
            logging.info("[PIPELINE] Reducție Smart dezactivată. Se continuă fără blacklist.")

        # === Hard Inversion Temporară (post-catastrofă) ===
        # Adăugăm pool-ul ratat anterior în blacklist DOAR pentru această extragere.
        # Nu se persistă, nu se acumulează — efemeră.
        temp_bl = getattr(self, "_temp_blacklist", set()) or set()
        if temp_bl:
            # Verificare safety: nu permitem ca union să elimine mai mult de 50% din univers.
            max_n_safe = int(self.params.get("max_n", 49))
            combined = blacklist | set(temp_bl)
            if len(combined) >= max_n_safe - pool_size:
                logging.warning(
                    f"[HARD-INVERSION] Temp blacklist ({len(temp_bl)}) + regular ({len(blacklist)}) "
                    f"= {len(combined)} blochează prea mult din univers ({max_n_safe}). Skip."
                )
            else:
                blacklist = combined
                self.audit["hard_inversion"] = {
                    "trigger": "previous_catastrophe",
                    "excluded": sorted(int(x) for x in temp_bl),
                    "n_excluded": len(temp_bl),
                    "scope": "single_draw",
                }

        # === Manual Inversion (utilizator: butonul "Inverseaza Pool") ===
        # Userul a apăsat butonul de inversare după ce pool-ul anterior n-a ieșit
        # bine în extragerile reale. Tratăm pool-ul anterior ca blacklist explicit.
        # Stocam pe instanta ca atribut pentru a fi RESPECTAT STRICT pana la final
        # (Smart Selector + pool-complete fallback pot altfel sa-l incalce).
        self._manual_blacklist_set: Set[int] = set()
        if manual_blacklist:
            try:
                manual_set = set(int(n) for n in manual_blacklist if int(n) > 0)
            except (TypeError, ValueError):
                manual_set = set()
                logging.warning("[MANUAL-INVERSION] manual_blacklist invalid, ignor.")
            if manual_set:
                max_n_safe = int(self.params.get("max_n", 49))
                combined = blacklist | manual_set
                if len(combined) >= max_n_safe - pool_size:
                    logging.warning(
                        f"[MANUAL-INVERSION] Manual ({len(manual_set)}) + auto ({len(blacklist)}) "
                        f"= {len(combined)} blocheaza prea mult din univers ({max_n_safe}). "
                        f"Pool_size cerut: {pool_size}. Skip pentru a evita pool gol."
                    )
                    # Marcam in audit ca inversarea NU s-a aplicat (pool prea mare pt joc)
                    # -> UI poate avertiza in loc sa arate Pool 2 identic cu Pool 1.
                    self.audit["manual_inversion"] = {
                        "skipped": True,
                        "reason": "pool prea mare pentru inversare",
                        "n_requested_exclude": len(manual_set),
                        "n_auto_blacklist": len(blacklist),
                        "max_num": max_n_safe,
                        "pool_size": pool_size,
                        "pool_max_pentru_inversare": max(1, (max_n_safe - len(blacklist)) // 2),
                    }
                else:
                    blacklist = combined
                    self._manual_blacklist_set = set(manual_set)  # STRICT
                    self.audit["manual_inversion"] = {
                        "trigger": "user_invert_button",
                        "excluded": sorted(manual_set),
                        "n_excluded": len(manual_set),
                        "scope": "single_run",
                    }
                    logging.info(
                        f"[MANUAL-INVERSION] User a cerut excluderea a {len(manual_set)} numere "
                        f"(pool anterior). Blacklist total: {len(blacklist)} din {max_n_safe}."
                    )

        self.hard_core = self._get_timesfm_pool(tfm_scores, pool_size=pool_size, blacklist=blacklist)
        
        # Transparența pipeline-ului: snapshot la fiecare etapă (pentru afișare în UI).
        # Cronologia e: NQI_raw → Smart → Anti-Seq → POST-HOC (final).
        self.audit['pipeline_stages'] = {
            "1_nqi_raw": sorted(self.hard_core.copy()),
        }

        # === PURE BENCH MODE — user request 2026-05-26 ===
        # Skip Smart Selector + POST-HOC + Anti-Sequence cand pure_bench_mode=True.
        # Flow minimal: scoring → pool top-N (deja făcut în _get_timesfm_pool cu
        # diversificare empirică decade/parity) → wheel direct.
        # Idea: bench-winner-ul DEJA e validat pe 1280 folds × 10 ferestre regresive.
        # Adăugarea de rafinări (Smart Selector heuristic, POST-HOC overfit pe ultimele
        # 50 extrageri, Anti-Sequence cu pragul 1%) poate INTRODUCE noise în loc să
        # îmbunătățească. Pure mode = încredere completă în bench winner.
        self.audit["pure_bench_mode"] = bool(pure_bench_mode)
        if pure_bench_mode:
            logging.info("[PIPELINE] 🎯 PURE BENCH MODE activ — skip Smart Selector + POST-HOC + Anti-Sequence")
            self.audit['pipeline_stages']["2_smart_selector"] = sorted(self.hard_core.copy())
            self.audit['pipeline_stages']["3_anti_sequence"] = sorted(self.hard_core.copy())
            self.audit['pipeline_stages']["4_post_hoc_final"] = sorted(self.hard_core.copy())
            self._consecutive_filter_applied = False
        else:
            # Smart Selector (rafinare Gap/Trend/Positional) ELIMINAT la cererea
            # utilizatorului: pe loterie aleatoare introducea noise peste decizia
            # scorerului castigator (overfit pe euristici fara valoare predictiva reala).
            # Pastram pool-ul BRUT al scorerului. POST-HOC + Anti-Sequence raman (la final).
            self.audit['pipeline_stages']["2_smart_selector"] = sorted(self.hard_core.copy())

            # NOTA: anti-sequence filter NU se mai aplica aici. POST-HOC poate
            # reintroduce orice secventa, deci e mai eficient sa-l rulam DOAR
            # la final (linia 656). Snapshot stage 3 pastrat pentru UI continuity.
            self.audit['pipeline_stages']["3_anti_sequence"] = sorted(self.hard_core.copy())

            # Garanție finală: Nucleul dur trebuie să aibă exact pool_size numere.
            # Trunchiere după scor TimesFM (nu după ordinea numerică — altfel pierzi bile tari).
            if len(self.hard_core) > pool_size:
                logging.warning(f"[PIPELINE] Nucleul dur avea {len(self.hard_core)} numere. Trunchiere la {pool_size} după scor NQI.")
                ranked = sorted(self.hard_core, key=lambda n: tfm_scores.get(n, 0.0), reverse=True)
                self.hard_core = sorted(ranked[:pool_size])
            elif len(self.hard_core) < pool_size:
                logging.warning(f"[PIPELINE] Nucleul dur avea doar {len(self.hard_core)} numere. Pool_size solicitat: {pool_size}.")
                self._consecutive_filter_applied = True
            else:
                self._consecutive_filter_applied = False

            logging.info(f"[PIPELINE] Nucleu (Pool) generat prin {_score_lbl} (NQI): {self.hard_core}")

            # POST-HOC VALIDATION: Verificăm pool-ul pe ultimele extrageri
            # Aceasta e optimizarea principală — un pool validat empiric
            if self.data is not None and self._draw_matrix is not None:
                self.hard_core = self._validate_pool_retrospective(
                    self.hard_core, tfm_scores, pool_size, blacklist
                )

            # Anti-secventa final — UNICUL apel din pipeline. Foloseste
            # tfm_scores (bench-winner) pentru picking inlocuiri, consistent cu
            # restul pipeline-ului.
            if filter_consecutives:
                logging.info("[PIPELINE] Aplicare Filtru Anti-Secventa pe nucleul final (cu bench-winner scoring)...")
                self.hard_core = self._apply_consecutive_filter(self.hard_core, freq, scores=tfm_scores)
            self.audit['pipeline_stages']["4_post_hoc_final"] = sorted(self.hard_core.copy())
        
        if self.game_type == "joker":
            logging.info(f"[PIPELINE] Scoring Urna 2 (Joker — câștigător bench / TimesFM)...")
            j_scores = self._get_timesfm_scores(is_joker_drum=True, context_len=actual_lookback)
            if j_scores:
                sorted_j = sorted(j_scores.items(), key=lambda x: x[1], reverse=True)
                self.hard_core_joker = [int(num) for num, _ in sorted_j[:2]]
                logging.info(f"[PIPELINE] Nucleu Joker (Urna 2): {self.hard_core_joker}")
                self.audit['joker_predictions'] = {num: round(score, 4) for num, score in sorted_j[:5]}
            else:
                freq_joker = self.analyze_joker_frequency()
                self.hard_core_joker = self._get_hard_core_joker(freq_joker, pool_size=2)
                logging.info(f"[PIPELINE] Nucleu Joker (Fallback Frecvență): {self.hard_core_joker}")

        if progress_cb:
            progress_cb("Generare predicții finale (Wheeling)...", 70)

        # === STRICT MANUAL BLACKLIST ENFORCEMENT ===
        # Smart Selector / consecutive filter / pool-complete fallback pot
        # reintroduce numere din manual_blacklist. Aplicam un filtru HARD aici,
        # imediat inainte de wheeling, ca sa garantam ca pool-ul final NU
        # contine niciun numar exclus de user.
        if getattr(self, "_manual_blacklist_set", None):
            mb = set(self._manual_blacklist_set)
            violated = [n for n in self.hard_core if n in mb]
            if violated:
                logging.warning(
                    f"[MANUAL-BLACKLIST] {len(violated)} numere din pool-ul exclus au fost "
                    f"reintroduse de pipeline ({sorted(violated)}). Le elimin si completez."
                )
                # Elimin violarile
                self.hard_core = [n for n in self.hard_core if n not in mb]
                # Completez cu top-scoring numere care NU sunt in manual_blacklist
                if tfm_scores:
                    sorted_clean = sorted(
                        ((n, s) for n, s in tfm_scores.items() if n not in mb and n not in self.hard_core),
                        key=lambda x: x[1], reverse=True,
                    )
                else:
                    freq_score = {i+1: float(f) for i, f in enumerate(freq)}
                    sorted_clean = sorted(
                        ((n, s) for n, s in freq_score.items() if n not in mb and n not in self.hard_core),
                        key=lambda x: x[1], reverse=True,
                    )
                needed = pool_size - len(self.hard_core)
                for n, _ in sorted_clean[:needed]:
                    self.hard_core.append(int(n))
                self.hard_core = sorted(self.hard_core)
                self.audit.setdefault("manual_inversion", {})["enforced_violations_fixed"] = {
                    "removed": sorted(violated),
                    "added_replacements": [n for n in self.hard_core if n not in violated][-needed:] if needed > 0 else [],
                }
                logging.info(f"[MANUAL-BLACKLIST] Pool final dupa enforcement: {self.hard_core}")

            # Enforcement-ul de mai sus a putut REINTRODUCE secvențe de 3+ consecutive
            # (înlocuiește numerele din blacklist cu top-scoring, fără să verifice
            # consecutivitatea). Re-aplicăm anti-secvența ca ULTIM pas (evitând blacklist-ul
            # ca să nu strice inversarea) → pool-ul FINAL inversat nu mai are 3+ consecutive.
            if filter_consecutives:
                self.hard_core = self._apply_consecutive_filter(
                    self.hard_core, freq, scores=tfm_scores, avoid=set(self._manual_blacklist_set))
                if 'pipeline_stages' in self.audit:
                    self.audit['pipeline_stages']["4_post_hoc_final"] = sorted(self.hard_core.copy())
            else:
                logging.info(f"[MANUAL-BLACKLIST] Pool curat ({len(self.hard_core)} numere, niciun violator).")

        logging.info("[PIPELINE] Începe generarea predicțiilor (Wheeling Set Cover)...")
        # Folosim tfm_scores dacă sunt disponibile, altfel fallback pe frecvență pentru wheeling
        wheeling_scores = tfm_scores if tfm_scores else {i+1: float(f) for i, f in enumerate(freq)}
        lines, coverage_pct = self.generate_predictions(guarantee=guarantee, max_variants=max_variants, scores=wheeling_scores)
        
        # Funcția 4: Anomaly Filtering via TimesFM v2 (skip pe Pure Mode)
        if smart_reduction and tfm_scores and not pure_bench_mode:
            logging.info(f"[PIPELINE] Aplicare Neural Anomaly Scoring v2 pe {len(lines)} variante...")
            original_count = len(lines)
            anomaly_threshold = 0.7
            lines = self.filter_variants_by_anomaly(lines, tfm_scores, threshold=anomaly_threshold)
            logging.info(f"[PIPELINE] Anomaly v2: eliminat {original_count - len(lines)} variante (threshold={anomaly_threshold}).")
            self.audit['anomaly_filter'] = {
                'original_count': original_count,
                'final_count': len(lines),
                'threshold': anomaly_threshold
            }
        elif pure_bench_mode:
            logging.info(f"[PIPELINE] 🎯 PURE MODE: skip Anomaly filter — păstrăm toate {len(lines)} variante")

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
            from ui_shared import atomic_write_json
            atomic_write_json(history_file, history)  # atomic: tmp+fsync+os.replace
                
            self.audit["pool_variation"] = pool_variation
        except Exception as e:
            logging.error(f"[PIPELINE] Eroare la tracker-ul de variație: {e}")

        # --- ADAPTIVE FEEDBACK POST-RUN: persistăm pool-ul nou + state ---
        if enable_adaptive_persistence and _HAS_ADAPTIVE:
            try:
                record_predicted_pool(
                    game_type=self.game_type,
                    pool_size=pool_size,
                    pool=self.hard_core,
                    data_rows=int(len(self.data)) if self.data is not None else 0,
                )
                summary = get_state_summary(self.game_type, pool_size)
                self.audit["adaptive_state"] = {
                    "event": adaptive_event,
                    "active_mode": self._adaptive_mode,
                    "last_hits": summary.get("last_hits"),
                    "streak_zero": summary.get("streak_zero"),
                    "rolling_avg": summary.get("rolling_avg"),
                    "baseline": round(summary.get("baseline", 0.0), 3),
                    "boosts": summary.get("boosts", []),
                    "penalties": summary.get("penalties", []),
                    "missed": (adaptive_info.get("missed") if adaptive_info else []),
                    "false_positives": (adaptive_info.get("false_positives") if adaptive_info else []),
                }
            except Exception as e:
                logging.error(f"[ADAPTIVE] Eroare la persistarea pool-ului: {e}")

        # === Hit Forecast Diagnostic — baseline matematic + recomandare pool size ===
        # Reaplicat din PR #2 (a fost pierdut la wipe-ul main din 2026-05-22).
        # Calculează P(k+ hits) pentru pool RANDOM și recomandă pool minim
        # pentru ≥3 evenimente 4+ și 5+. UI-ul citește audit.hit_forecast
        # și afișează banner "pentru 4+ stabil, mărește pool la N".
        try:
            n_recent_for_forecast = max(int(len(self.data) * 0.05), 1) if self.data is not None else 100
            forecast = hypergeometric_hit_forecast(
                pool_size=len(self.hard_core) if self.hard_core else int(pool_size),
                draw_n=int(self.params["draw_n"]),
                max_n=int(self.params["max_n"]),
                n_draws=max(n_recent_for_forecast, 100),
            )
            if forecast:
                self.audit["hit_forecast"] = forecast
        except Exception as _exc_fc:
            logging.debug(f"[PIPELINE] hit_forecast a eșuat: {_exc_fc}")

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

    def _apply_consecutive_filter(self, pool: list, freq: np.ndarray, scores: dict | None = None,
                                  avoid: set | None = None) -> list:
        """Sparge orice secvență de 3+ numere consecutive din pool (STRICT — nu
        păstrăm niciodată 3+ consecutive, indiferent de istoric). Pentru fiecare run
        de 3+: scoatem cel mai slab număr (după frecvență) și punem cea mai bine cotată
        rezervă care NU re-formează un run de 3+. Repetăm până nu mai rămâne niciun run.

        Înlocuirea folosește `scores` (bench-winner sau NQI) dacă e furnizat, altfel cade
        pe frecvența raw. Perechile (2 consecutive) sunt permise; doar 3+ sunt sparte.
        """
        if self.data is None or len(pool) < 3:
            return pool
            
        draw_sets = []
        if self._draw_matrix is not None and self._draw_matrix.size:
            draw_sets = [set(row) for row in self._draw_matrix]
        else:
            # Robust columns detection
            df = self.data
            n_cols = sorted(
                [c for c in df.columns if str(c).lower().startswith('n') and str(c).lower() != 'numbers'],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = primele 5 (Cat. I)
            if n_cols:
                for _, row in df.iterrows():
                    draw_sets.append(set(pd.to_numeric(row[n_cols], errors='coerce').dropna().astype(int)))
            elif "numbers" in df.columns:
                for _, row in df.iterrows():
                    try:
                        draw_sets.append(set(int(x) for x in str(row["numbers"]).split(",") if str(x).strip().isdigit()))
                    except (ValueError, TypeError) as exc:
                        logging.debug("anti-sequence: skip row (parse): %s", exc)
                        continue

        current_pool = sorted(pool.copy())
        # Ranking rezervelor: prefera bench-winner scoring daca disponibil,
        # altfel cade pe frecventa raw (legacy).
        if scores:
            ranked_reserves = sorted(
                range(1, len(freq) + 1),
                key=lambda n: scores.get(n, freq[n - 1] if n - 1 < len(freq) else 0),
                reverse=True,
            )
            # Convertim la indici 0-based pentru compatibilitate cu codul existent
            all_sorted_indices = np.array([n - 1 for n in ranked_reserves], dtype=np.int64)
            _replacement_signal = "bench-winner scores"
        else:
            all_sorted_indices = np.argsort(freq)[::-1]
            _replacement_signal = "raw frequency"
        modifications = []
        logging.info(f"[ANTI-SEQ] Replacement signal: {_replacement_signal}")

        # Trackăm numerele scoase ca să NU le re-adăugăm ca rezerve (altfel ai putea avea
        # "Scos 18 → adăugat 18"). Bug observat 2026-05-02.
        removed_nums: set[int] = set()

        def _forms_run3(pool_set: set, num: int) -> bool:
            """True dacă adăugarea lui `num` creează un run de 3+ numere consecutive."""
            run = 1
            x = num - 1
            while x in pool_set:
                run += 1; x -= 1
            x = num + 1
            while x in pool_set:
                run += 1; x += 1
            return run >= 3

        _iter_guard = 0
        while True:
            _iter_guard += 1
            if _iter_guard > 4 * max(len(pool), 1):  # anti-buclă (nu ar trebui atins)
                break
            found_sequence = None
            # Căutăm orice run de 3+ numere consecutive în pool.
            for start_idx in range(len(current_pool) - 2):
                consecutive_nums = [current_pool[start_idx]]
                for next_idx in range(start_idx + 1, len(current_pool)):
                    if current_pool[next_idx] == consecutive_nums[-1] + 1:
                        consecutive_nums.append(current_pool[next_idx])
                    else:
                        break
                if len(consecutive_nums) >= 3:
                    found_sequence = tuple(consecutive_nums)
                    break
            
            if not found_sequence:
                break
                
            # STRICT (cerință utilizator): NU păstrăm NICIODATĂ secvențe de 3+ numere
            # consecutive în pool, indiferent de istoric (4 consecutive apar întâmplător
            # ~o dată în mii de extrageri — nu e semnal, nu le ținem). Spargem secvența.
            seq_set = set(found_sequence)
            occurrences = sum(1 for d_set in draw_sets if seq_set.issubset(d_set))

            # Scoatem cel mai slab număr din secvență (după frecvență).
            weakest_num = min(found_sequence, key=lambda x: freq[x - 1])
            current_pool.remove(weakest_num)
            removed_nums.add(weakest_num)
            _pool_set = set(current_pool)

            # Alegem rezerva: cel mai bine cotat număr care NU re-formează un run de 3+;
            # dacă niciunul (improbabil), cădem pe cel mai bine cotat disponibil (păstrăm
            # dimensiunea pool-ului). Sărim numerele deja din pool sau scoase în rulare.
            chosen, chosen_any = None, None
            for idx in all_sorted_indices:
                num = int(idx) + 1
                if num in _pool_set or num in removed_nums or (avoid and num in avoid):
                    continue
                if chosen_any is None:
                    chosen_any = num
                if not _forms_run3(_pool_set, num):
                    chosen = num
                    break
            pick = chosen if chosen is not None else chosen_any
            if pick is None:
                break  # nu mai există rezerve (improbabil) — oprim
            current_pool.append(pick)
            modifications.append(
                f"Scos {weakest_num} (frecvență {int(freq[weakest_num - 1])}) din secvența "
                f"{found_sequence} [a ieșit de {occurrences}× în istoric], adăugat {pick} "
                f"(frecvență {int(freq[pick - 1])})"
            )
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
        Smart Selector v2 - sistem hibrid care aliniază rafinarea cu semnalul
        empiric folosit de POST-HOC Final, pentru a elimina overhead-ul unde
        POST-HOC rescria 40–47% din pool (observat 2026-05-02 pe loto_6_49,
        loto_5_40 și joker: Δ post-hoc +6/−6 până la +7/−7 din 15).

        Factori:
        1. Gap Analysis       (30%) - Analiza intervalelor (a fost 40%)
        2. Trend Analysis     (15%) - Analiza trend-urilor (a fost 25%)
        3. Hot/Cold Analysis  (15%) - Frecvență istorică  (a fost 20%)
        4. Positional         (10%) - Analiza pozițională (a fost 15%)
        5. Recent Empirical   (30%) - Apariții în ultimele 50 extrageri,
           weighted mai mult pe ultimele 15 (reactivitate). Acesta e același
           semnal pe care POST-HOC îl folosește pentru optimizarea hit-urilor
           4+/5+/6 — injectat aici, Smart Selector produce deja un pool
           "apropiat" de optimul POST-HOC, iar POST-HOC devine touch-up.
        """
        if not hasattr(self, 'hard_core') or not self.hard_core:
            return

        logging.info("[SMART] Inițiere Smart Selector v2 - analiză multi-factorială...")

        gap_scores = self._calculate_gap_scores()
        trend_scores = self._calculate_trend_scores()
        frequency_scores = self._calculate_frequency_scores(freq)
        positional_scores = self._calculate_positional_scores()
        recent_hit_scores = self._calculate_recent_hit_scores()

        final_scores = {}
        for num in self.hard_core:
            final_score = (
                gap_scores.get(num, 0) * 0.30 +
                trend_scores.get(num, 0) * 0.15 +
                frequency_scores.get(num, 0) * 0.15 +
                positional_scores.get(num, 0) * 0.10 +
                recent_hit_scores.get(num, 0) * 0.30
            )
            final_scores[num] = final_score

        sorted_by_score = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)

        # H2 v2 (2026-05-04): keep 90% (≈1 swap) doar pe pool 10-11. În
        # confirmarea statistică n=64, pool 9 cu H2+H3 a regresat -17% — prea
        # mic ca să tolereze simultan suprimarea quota-urilor de decadă (H3)
        # și reducerea swap-ului empiric (H2). Pool 9 și pool ≥ 12 rămân pe
        # 80% legacy.
        pool_len = len(self.hard_core)
        if pool_len in (10, 11):
            keep_ratio = 0.90  # ≈1 swap pe pool 10-11
        else:
            keep_ratio = 0.80  # legacy pe pool 9 și pool ≥ 12
        keep_count = max(6, int(pool_len * keep_ratio))
        best_numbers = [num for num, score in sorted_by_score[:keep_count]]
        excluded_numbers = [num for num in self.hard_core if num not in best_numbers]

        if excluded_numbers:
            self._replace_with_smart_alternatives(excluded_numbers, best_numbers, freq)

            if 'smart_selector' not in self.audit:
                self.audit['smart_selector'] = {}

            self.audit['smart_selector'] = {
                'method': 'Hybrid Analysis v2 (30% Gap + 15% Trend + 15% Freq + 10% Positional + 30% Recent-Hits)',
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
    
    def _calculate_recent_hit_scores(self) -> dict:
        """
        Recent Empirical Validity (REV) (30% weight) — aliniere cu POST-HOC.

        Calculează un scor [0,1] per număr bazat pe aparițiile în ultimele
        50 extrageri, cu o pondere mai mare pe ultimele 15 (reactivitate la
        regim recent). Un număr care a ieșit recent primește scor apropiat
        de 1.0; unul care nu a mai ieșit în >=50 extrageri primește ~0.

        Formula:
            short = hits în ultimele 15 / 15
            long  = hits în ultimele 50 / 50
            score = 0.65 * short + 0.35 * long

        Scale-ul e calibrat ca numerele cu frecvență tipică (baseline 15-20%
        pe ultima fereastră) să ajungă în [0.3, 0.6], iar cele cu burst
        recent să urce peste 0.7.
        """
        if self._draw_matrix is None or self._draw_matrix.shape[0] == 0:
            return {num: 0.5 for num in self.hard_core}

        n_short = min(15, self._draw_matrix.shape[0])
        n_long = min(50, self._draw_matrix.shape[0])

        short_matrix = self._draw_matrix[-n_short:]
        long_matrix = self._draw_matrix[-n_long:]

        short_sets = [set(int(v) for v in row if v > 0) for row in short_matrix]
        long_sets = [set(int(v) for v in row if v > 0) for row in long_matrix]

        recent_scores = {}
        for num in self.hard_core:
            short_hits = sum(1 for s in short_sets if num in s)
            long_hits = sum(1 for s in long_sets if num in s)
            short_rate = short_hits / n_short if n_short else 0.0
            long_rate = long_hits / n_long if n_long else 0.0
            # Boost ușor pe rate-ul scurt: un număr care iese în 3/15 (20%) recente
            # primește scor ~0.45, mult peste un număr care iese 0/15 recente → ~0.07.
            score = 0.65 * min(1.0, short_rate * 2.5) + 0.35 * min(1.0, long_rate * 3.0)
            recent_scores[num] = float(score)

        logging.info(f"[SMART] Recent-hit scores (short={n_short}, long={n_long}): {recent_scores}")
        return recent_scores

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
        """Înlocuiește numerele excluse cu cele mai bune alternative bazate pe Smart Selector v2.

        FIX (reapplied from PR #2):
        - Weights aligned cu _apply_smart_selector v2 (30/15/15/10/30, inclusiv
          Recent-Hits) — înainte foloseam vechi 40/25/20/15 fără Recent-Hits,
          ceea ce însemna că numerele înlocuite erau scorate cu reguli diferite
          decât cele păstrate. Pool-ul final amesteca două criterii.
        - try/finally garantează restaurarea self.hard_core la excepție —
          înainte, dacă apărea exception în vreun calculator, hard_core rămânea
          setat la lista de candidați (corupere de stare).
        """
        all_numbers = set(range(1, self.params["max_n"] + 1))
        current_pool = set(kept_numbers)
        available_numbers = all_numbers - current_pool

        alternative_scores: dict = {}
        temp_hard_core = list(available_numbers)
        original_hard_core = self.hard_core.copy()
        try:
            self.hard_core = temp_hard_core

            gap_scores = self._calculate_gap_scores()
            trend_scores = self._calculate_trend_scores()
            frequency_scores = self._calculate_frequency_scores(freq)
            positional_scores = self._calculate_positional_scores()
            recent_hit_scores = self._calculate_recent_hit_scores()

            # Aceleași weights ca _apply_smart_selector v2
            for num in available_numbers:
                alternative_scores[num] = (
                    gap_scores.get(num, 0) * 0.30 +
                    trend_scores.get(num, 0) * 0.15 +
                    frequency_scores.get(num, 0) * 0.15 +
                    positional_scores.get(num, 0) * 0.10 +
                    recent_hit_scores.get(num, 0) * 0.30
                )
        finally:
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
                        n_cols = sorted(
                            [c for c in self.data.columns if str(c).lower().startswith('n')],
                            key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
                        )[: int(self.params["draw_n"])]  # 5/40 = primele 5 (Cat. I)
                        nums = [int(row[c]) for c in n_cols if pd.notna(row.get(c))]
                    if num in nums:
                        appearances.append(i)
                except (ValueError, TypeError) as exc:
                    logging.debug("avg_gap: skip row (parse): %s", exc)
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

    def _scores_via_bench_winner(self, is_joker_drum: bool = False) -> Dict[int, float]:
        """Route scoring through the benchmark-winning method for this game/pool.

        Maps:
            self.game_type "6/49"  → game_key "loto_6_49"
            self.game_type "5/40"  → game_key "loto_5_40"
            self.game_type "joker" → "joker_urna2" if is_joker_drum else "joker_urna1"

        Reads best_methods.json via method_selector. Returns {} on any failure
        so the caller falls back to TimesFM.
        """
        try:
            from loto_enterprise.core.method_selector import get_scorer_for_game, get_winner_name
        except Exception as exc:
            logging.warning("[ENGINE] method_selector import failed: %s", exc)
            return {}

        # Mapping (game_type, is_joker_drum) -> (game_key, max_num, pool_hint).
        # joker_urna2 e single-pick (draw_n=1) — pool_hint trebuie sa fie 1,
        # nu pool-size-ul UI care e pentru Urna 1.
        if self.game_type == "joker":
            if is_joker_drum:
                game_key = "joker_urna2"
                max_num = 20
                _pool_hint = 1
            else:
                game_key = "joker_urna1"
                max_num = int(self.params["max_n"])
                _pool_hint = int(self._winner_pool_hint)
        elif self.game_type == "5/40":
            game_key = "loto_5_40"
            max_num = int(self.params["max_n"])
            _pool_hint = int(self._winner_pool_hint)
        else:
            game_key = "loto_6_49"
            max_num = int(self.params["max_n"])
            _pool_hint = int(self._winner_pool_hint)

        if self._draw_matrix is None:
            return {}
        if is_joker_drum and self.data is not None and "joker" in self.data.columns:
            draws_2d = self.data["joker"].to_numpy(dtype=np.int64).reshape(-1, 1)
        else:
            draws_2d = self._draw_matrix.astype(np.int64)

        try:
            winner = get_winner_name(game_key, pool_size=_pool_hint)
            scorer = get_scorer_for_game(game_key, pool_size=_pool_hint)
            logging.info("[ENGINE] bench-winner scoring: game=%s pool=%d -> %s",
                         game_key, _pool_hint, winner)
            scores = scorer(draws_2d, max_num)
            if not scores:
                return {}
            family = ""
            try:
                from loto_enterprise.benchmark.methods import METHODS as _METHODS
                meta = _METHODS.get(winner)
                if meta:
                    family = meta[1]
            except Exception as _exc_fam:
                logging.debug("[ENGINE] family lookup pt %s eșuat: %s", winner, _exc_fam)
            self.audit.setdefault("bench_winner", {})[game_key] = {
                "method": winner,
                "pool_hint": _pool_hint,
                "family": family,
            }
            return {int(k): float(v) for k, v in scores.items()}
        except Exception as exc:
            logging.warning("[ENGINE] bench-winner scoring failed: %s", exc)
            return {}

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
        # Opt-in (default ON): dacă use_bench_winner == True, rutăm prin
        # method_selector → modelul câștigător din benchmark per joc/pool.
        if self.use_bench_winner:
            scores = self._scores_via_bench_winner(is_joker_drum=is_joker_drum)
            if scores:
                return scores
            logging.warning("[ENGINE] bench-winner scoring returned empty — fallback TimesFM")
        if _HAS_TFM_V2:
            return get_timesfm_scores_v2(
                data=self.data,
                draw_matrix=self._draw_matrix,
                params=self.params,
                is_joker_drum=is_joker_drum,
                context_len=context_len,
                audit=self.audit,
                regime_mode=getattr(self, "_adaptive_mode", "normal"),
                window_progress_cb=getattr(self, "_tfm_window_cb", None),
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
        except (RuntimeError, AttributeError) as exc:
            logging.debug("[TIMESFM] cuda probe failed, fallback la CPU: %s", exc)
        # Salvăm device-ul REAL folosit (citit de UI pt indicatorul ⚡GPU/🐌CPU)
        self.audit.setdefault('performance', {})['device'] = device

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
        Analiză Regresivă multi-pass.
        Dacă `use_bench_winner` e activ, foloseste scorerul câștigător din
        benchmark (poate fi TimesFM, FEDformer, MOMENT etc.). Altfel cade
        pe TimesFM v2 hardcodat.
        Rulează scoring pe ferestre de 100%, 90%, 80%... și elimină doar intersecția.
        """
        # Prefer bench winner (consistent cu scorerul principal)
        if self.use_bench_winner and self._draw_matrix is not None:
            try:
                from loto_enterprise.core.method_selector import (
                    get_scorer_for_game, get_winner_name, should_use_blacklist,
                )
                from loto_enterprise.core.timesfm_engine import get_regressive_blacklist_generic

                if self.game_type == "joker":
                    game_key = "joker_urna1"  # blacklist-ul de Urna 1; joker drum are propriul flux
                    max_num = int(self.params["max_n"])
                elif self.game_type == "5/40":
                    game_key = "loto_5_40"
                    max_num = int(self.params["max_n"])
                else:
                    game_key = "loto_6_49"
                    max_num = int(self.params["max_n"])

                # Respect bench decision: pentru unele (joc, pool) variantele
                # SE COMPORTĂ MAI BINE FĂRĂ blacklist. Verificăm înainte să rulăm.
                if not should_use_blacklist(game_key, pool_size=self._winner_pool_hint):
                    logging.info(
                        f"[BLACKLIST] use_bench_winner=ON, dar benchmark spune "
                        f"NO-BLACKLIST pentru {game_key} pool={self._winner_pool_hint} "
                        f"→ skip blacklist (return empty set)"
                    )
                    self.audit.setdefault("blacklist", {})["regressive_scorer"] = "DISABLED_BY_BENCH"
                    self.audit["blacklist"]["regressive_blacklist"] = []
                    return set()

                scorer = get_scorer_for_game(game_key, pool_size=self._winner_pool_hint)
                winner_name = get_winner_name(game_key, pool_size=self._winner_pool_hint)
                logging.info(
                    f"[BLACKLIST] use_bench_winner=ON → folosesc scorerul câștigător "
                    f"<{winner_name}> pentru analiza regresivă"
                )
                bl = get_regressive_blacklist_generic(
                    draw_matrix_full=self._draw_matrix,
                    max_num=max_num,
                    sim_depth_pct=sim_depth_pct,
                    scorer_fn=scorer,
                    scorer_label=winner_name,
                )
                self.audit.setdefault("blacklist", {})["regressive_scorer"] = winner_name
                self.audit["blacklist"]["regressive_blacklist"] = sorted(bl)
                return bl
            except Exception as exc:
                logging.warning(
                    f"[BLACKLIST] bench-winner regressive failed ({exc}), "
                    f"fallback la TimesFM v2"
                )

        if _HAS_TFM_V2 and self.data is not None:
            return get_regressive_blacklist_v2(
                data_full=self.data,
                draw_matrix_full=self._draw_matrix,
                params=self.params,
                sim_depth_pct=sim_depth_pct,
                rebuild_matrix_fn=self._build_draw_matrix,
                audit=self.audit,
                step_progress_cb=getattr(self, "_regressive_step_cb", None),
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
            pool = select_pool_from_scores(
                scores, pool_size, blacklist, self.audit,
                max_num=max_num, draw_matrix=self._draw_matrix,
            )
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

        # === Catastrophe Diversification ===
        # Dacă ultima extragere a fost o catastrofă (0 hituri), forțăm includerea
        # a 2-3 numere "due" (cu cel mai mare gap_ratio) care nu sunt deja în pool.
        # Înlocuim numerele cu cel mai mic scor TimesFM. Acest lucru sparge bias-ul
        # care a dus la pierderea totală.
        if getattr(self, "_adaptive_event", None) == "catastrophe" and self._draw_matrix is not None:
            try:
                pool = self._inject_due_numbers(pool, scores, blacklist, pool_size, max_inject=3)
            except Exception as e:
                logging.error(f"[CATASTROPHE] Eroare la injectare numere 'due': {e}")

        return sorted(pool[:pool_size])

    def _inject_due_numbers(
        self,
        pool: List[int],
        scores: Dict[int, float],
        blacklist: Set[int],
        pool_size: int,
        max_inject: int = 3,
    ) -> List[int]:
        """Forțează includerea celor mai 'due' numere (gap_ratio mare) în pool,
        înlocuind numerele cu cel mai mic scor TimesFM. Apelat după catastrofă."""
        if self._draw_matrix is None or self._draw_matrix.shape[0] < 20:
            return pool

        max_n = int(self.params.get("max_n", 49))
        total_draws = self._draw_matrix.shape[0]

        # Calculăm gap_ratio pentru toate numerele 1..max_n
        gap_ratios: Dict[int, float] = {}
        for num in range(1, max_n + 1):
            if num in blacklist:
                continue
            appearances = [i for i, row in enumerate(self._draw_matrix) if num in row]
            if len(appearances) < 2:
                continue
            gaps = [appearances[i + 1] - appearances[i] for i in range(len(appearances) - 1)]
            avg_gap = sum(gaps) / len(gaps)
            current_gap = total_draws - appearances[-1]
            if avg_gap > 0:
                gap_ratios[num] = current_gap / avg_gap

        if not gap_ratios:
            return pool

        # Candidații de injectat: cei cu gap_ratio cel mai mare, NU deja în pool
        pool_set = set(pool)
        candidates = sorted(
            [(n, gr) for n, gr in gap_ratios.items() if n not in pool_set],
            key=lambda x: -x[1],
        )[:max_inject * 2]
        # Filtrăm doar candidați cu gap semnificativ (gap_ratio >= 1.3)
        strong_candidates = [(n, gr) for n, gr in candidates if gr >= 1.3][:max_inject]

        if not strong_candidates:
            logging.info("[CATASTROPHE] Niciun număr 'due' suficient de puternic pentru injectare.")
            return pool

        # Numerele care vor ieși din pool: cele cu cel mai mic scor TimesFM
        pool_with_scores = sorted(
            [(n, scores.get(n, 0.0)) for n in pool],
            key=lambda x: x[1],
        )

        new_pool = list(pool)
        injected = []
        evicted = []
        for (cand, gr), (weak, weak_score) in zip(strong_candidates, pool_with_scores):
            if cand in new_pool:
                continue
            if weak in new_pool:
                new_pool.remove(weak)
                new_pool.append(cand)
                injected.append((int(cand), round(gr, 2)))
                evicted.append((int(weak), round(weak_score, 4)))

        if injected:
            logging.warning(
                f"[CATASTROPHE] Diversificare forțată: injectate {injected}, "
                f"evacuate {evicted} din pool."
            )
            self.audit["catastrophe_diversification"] = {
                "injected": injected,
                "evicted": evicted,
                "trigger": "0_hits_previous_draw",
            }

        return new_pool

    def _validate_pool_retrospective(
        self, pool: List[int], scores: Dict[int, float], pool_size: int, blacklist: Set[int]
    ) -> List[int]:
        """
        Post-Hoc Pool Validation: Verifică și optimizează pool-ul pe ultimele extrageri.
        
        Strategii:
        1. Swap-out: Numerele din pool care NU au apărut deloc în ultimele 15 extrageri
           sunt înlocuite cu cele mai bune alternative (din scores, care nu sunt în blacklist).
        2. Walk-Forward Refinement: Iterativ, testăm dacă înlocuirea unui număr din pool
           cu un candidat extern crește rata de hit-uri de 4+.
        """
        if self._draw_matrix is None or self._draw_matrix.shape[0] < 20:
            if len(pool) > pool_size:
                ranked = sorted(pool, key=lambda n: scores.get(n, 0.0), reverse=True)
                pool = ranked[:pool_size]
            return sorted(pool)

        draw_n = int(self.params.get("draw_n", 6))
        
        # Determinăm pragul de hit bazat pe tipul de joc
        hit_target = max(draw_n - 2, 3)  # 4 pentru 6/49, 3 pentru 5/40
        
        logging.info(f"[POST-HOC] Validare retrospectivă pool (target: {hit_target}+ hits)...")
        
        n_recent = min(15, self._draw_matrix.shape[0] - 5)
        recent_matrix = self._draw_matrix[-n_recent:]
        recent_sets = [set(int(v) for v in row if v > 0) for row in recent_matrix]

        # === BUDGET TOTAL POST-HOC: 30% din pool partajat intre Pas 1 (dead-num
        # swap) si Pas 2 (walk-forward refinement). Astfel pastram >=70% din
        # pool-ul venit de la bench-winner + Smart Selector, indiferent cum se
        # distribuie swap-urile intre cei doi pasi. ===
        global_swap_budget = max(1, int(round(pool_size * 0.30)))
        swaps_used_so_far = 0
        logging.info(
            f"[POST-HOC] Buget total swap-uri: {global_swap_budget}/{pool_size} "
            f"(30%) partajat intre Pas 1 (dead nums) si Pas 2 (walk-forward)"
        )

        # Pas 1: Identificăm numerele "moarte" din pool (0 apariții în ultimele 15 extrageri)
        pool_set = set(pool)
        appearance_count = {num: 0 for num in pool}
        for draw_set in recent_sets:
            for num in pool:
                if num in draw_set:
                    appearance_count[num] += 1

        dead_nums = [num for num, count in appearance_count.items() if count == 0]

        if dead_nums:
            logging.info(f"[POST-HOC] Pas 1: numere fara aparitii recente (15 ext.): {dead_nums}")
            available = sorted(
                [(n, s) for n, s in scores.items() if n not in pool_set and n not in blacklist],
                key=lambda x: x[1], reverse=True
            )
            for dead_num in sorted(dead_nums, key=lambda x: scores.get(x, 0)):
                if swaps_used_so_far >= global_swap_budget or not available:
                    break
                for alt_num, alt_score in available:
                    alt_recent = sum(1 for ds in recent_sets if alt_num in ds)
                    if alt_recent >= 1:
                        pool_set.discard(dead_num)
                        pool_set.add(alt_num)
                        available = [(n, s) for n, s in available if n != alt_num]
                        swaps_used_so_far += 1
                        logging.info(
                            f"[POST-HOC] Pas 1 swap ({swaps_used_so_far}/{global_swap_budget}): "
                            f"{dead_num} (0 ap.) -> {alt_num} ({alt_recent} ap.)"
                        )
                        break

        current_pool = sorted(pool_set)
        
        # Pas 2: Walk-Forward Refinement (Optimizat pentru hit-uri majore, game-aware)
        # Combinație între fereastră scurtă (reactivitate) și lungă (stabilitate).
        n_backtest = min(50, self._draw_matrix.shape[0] - 5)
        backtest_sets = [set(int(v) for v in row if v > 0) for row in self._draw_matrix[-n_backtest:]]
        n_backtest_long = min(150, self._draw_matrix.shape[0] - 5)
        backtest_sets_long = [set(int(v) for v in row if v > 0) for row in self._draw_matrix[-n_backtest_long:]]
        
        if draw_n == 6:
            hit_target, hit_high, hit_max = 4, 5, 6
            w_target, w_high, w_max = 10, 45, 180
        else:
            hit_target, hit_high, hit_max = 3, 4, 5
            w_target, w_high, w_max = 8, 35, 140

        def _score_on_sets(test_set, target_sets):
            """Scoring game-aware pentru un set de extrageri."""
            total = 0
            target_hits = 0
            high_hits = 0
            max_hits = 0
            score = 0
            for ds in target_sets:
                overlap = len(test_set & ds)
                total += overlap
                if overlap >= hit_target:
                    target_hits += 1
                    score += w_target
                if overlap >= hit_high:
                    high_hits += 1
                    score += w_high
                if overlap >= hit_max:
                    max_hits += 1
                    score += w_max
            return score, target_hits, high_hits, max_hits, total

        def count_hits(test_pool):
            """Combină scorul pe ferestre scurtă/lungă pentru robustețe."""
            test_set = set(test_pool)
            short_score, short_target, short_high, short_max, short_total = _score_on_sets(test_set, backtest_sets)
            long_score, long_target, long_high, long_max, long_total = _score_on_sets(test_set, backtest_sets_long)
            combined_score = (0.7 * short_score) + (0.3 * long_score)
            combined_total = (0.7 * short_total) + (0.3 * long_total)
            return combined_score, short_target, short_high, short_max, combined_total
        
        base_score, base_target_hits, base_high_hits, base_max_hits, base_total = count_hits(current_pool)
        logging.info(
            f"[POST-HOC] Performanță curentă: Scor={base_score:.2f}, "
            f"hits {hit_target}+={base_target_hits}, {hit_high}+={base_high_hits}, {hit_max}={base_max_hits}, "
            f"total={base_total:.2f} pe {n_backtest}/{n_backtest_long} extrageri."
        )
        
        # Candidați externi (top 40 din scoruri, extins pt mai multe opțiuni)
        external_candidates = sorted(
            [(n, s) for n, s in scores.items() if n not in current_pool and n not in blacklist],
            key=lambda x: x[1], reverse=True
        )[:40]
        
        # Pas 2 foloseste BUGETUL RAMAS din budgetul GLOBAL (30% din pool).
        # Pas 1 (dead nums) a consumat swaps_used_so_far; restul ramane pentru Pas 2.
        # In total, max 30% din pool e schimbat de POST-HOC (Pas1 + Pas2 combinate).
        max_total_swaps = global_swap_budget
        total_swaps_done = swaps_used_so_far  # mostenit din Pas 1
        original_pool_snapshot = list(current_pool)
        logging.info(
            f"[POST-HOC] Pas 2 start: ramase {max_total_swaps - total_swaps_done}/{max_total_swaps} "
            f"swap-uri disponibile (Pas 1 a folosit {swaps_used_so_far})"
        )

        improved = True
        iteration = 0
        while improved and iteration < 8 and total_swaps_done < max_total_swaps:
            improved = False
            iteration += 1

            for pool_num in list(current_pool):
                if total_swaps_done >= max_total_swaps:
                    break
                best_replacement = None
                best_score = base_score
                best_target_hits = base_target_hits
                best_high_hits = base_high_hits
                best_max_hits = base_max_hits
                best_total = base_total
                
                for ext_num, _ in external_candidates:
                    if ext_num in current_pool:
                        continue
                    
                    test_pool = [n if n != pool_num else ext_num for n in current_pool]
                    test_score, test_target, test_high, test_max, test_total = count_hits(test_pool)
                    
                    # Prioritizăm scorul game-aware, apoi pragul principal și overlap-ul.
                    if (
                        (test_score > best_score)
                        or (test_score == best_score and test_target > best_target_hits)
                        or (test_score == best_score and test_target == best_target_hits and test_total > best_total)
                    ):
                        best_replacement = ext_num
                        best_score = test_score
                        best_target_hits = test_target
                        best_high_hits = test_high
                        best_max_hits = test_max
                        best_total = test_total
                
                if best_replacement is not None and (
                    best_score > base_score
                    or best_target_hits > base_target_hits
                    or best_total > base_total + 1
                ):
                    logging.info(f"[POST-HOC] Refinement: {pool_num} → {best_replacement} "
                                f"(Scor: {base_score:.2f}→{best_score:.2f}, "
                                f"{hit_target}+: {base_target_hits}→{best_target_hits}, "
                                f"{hit_high}+: {base_high_hits}→{best_high_hits}, "
                                f"{hit_max}: {base_max_hits}→{best_max_hits}, "
                                f"total: {base_total:.2f}→{best_total:.2f})")
                    current_pool = sorted([n if n != pool_num else best_replacement for n in current_pool])
                    
                    # Actualizăm candidații externi
                    external_candidates = [(n, s) for n, s in external_candidates if n != best_replacement]
                    external_candidates.append((pool_num, scores.get(pool_num, 0.0)))
                    
                    base_score = best_score
                    base_target_hits = best_target_hits
                    base_high_hits = best_high_hits
                    base_max_hits = best_max_hits
                    base_total = best_total
                    improved = True
                    total_swaps_done += 1
                    if total_swaps_done >= max_total_swaps:
                        logging.info(
                            f"[POST-HOC] Atins limita de swap-uri ({max_total_swaps}/{len(current_pool)}) — opresc"
                        )
                    break  # Restart loop after a swap

        if iteration > 0:
            new_score, new_target_hits, new_high_hits, new_max_hits, new_total = count_hits(current_pool)
            logging.info(
                f"[POST-HOC] Refinement finalizat ({iteration} iterații, {total_swaps_done}/{max_total_swaps} swaps). "
                f"Final: Scor={new_score:.2f}, {hit_target}+={new_target_hits}, "
                f"{hit_high}+={new_high_hits}, {hit_max}={new_max_hits}, total={new_total:.2f}."
            )
            
            # Ne asigurăm că original_counts e creat corect
            orig_score, orig_target_hits, orig_high_hits, orig_max_hits, orig_total = count_hits(pool)
            self.audit['post_hoc_validation'] = {
                'original_score': round(float(orig_score), 3),
                'final_score': round(float(new_score), 3),
                'hit_target': hit_target,
                'hit_high': hit_high,
                'hit_max': hit_max,
                'original_target_hits': int(orig_target_hits),
                'final_target_hits': int(new_target_hits),
                'original_high_hits': int(orig_high_hits),
                'final_high_hits': int(new_high_hits),
                'original_max_hits': int(orig_max_hits),
                'final_max_hits': int(new_max_hits),
                'original_total': round(float(orig_total), 3),
                'final_total': round(float(new_total), 3),
                'iterations': iteration,
                'backtest_draws_short': n_backtest,
                'backtest_draws_long': n_backtest_long
            }
        
        return sorted(current_pool[:pool_size])


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
        mean_prob = float(np.mean(variant_probs))

        # Scor de anomalie bazat pe distanța față de topul modelului
        max_prob = max(scores.values()) if scores else 1.0
        # Gardă numerică: evităm 0/0 și valorile negative (clamp la 1e-9 pe numitor)
        denom = max(float(max_prob), 1e-9)
        anomaly = 1.0 - (mean_prob / denom)
        # Clamp final în [0, 1] pentru stabilitate downstream
        if anomaly < 0.0:
            anomaly = 0.0
        elif anomaly > 1.0:
            anomaly = 1.0
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
        "6/49": {"max_n": 49, "draw_n": 6, "joker_max": None},
        "5/40": {"max_n": 40, "draw_n": 5, "joker_max": None},
        "joker": {"max_n": 45, "draw_n": 5, "joker_max": 20},
    }

    game_params = params.get(game, params["6/49"])
    rng = np.random.default_rng(42)

    rows = []
    for i in range(50):
        numbers = sorted(
            rng.choice(np.arange(1, game_params["max_n"] + 1), size=game_params["draw_n"], replace=False).tolist()
        )
        row = {f"n{k+1}": int(numbers[k]) for k in range(game_params["draw_n"])}
        if game_params.get("joker_max"):
            row["joker"] = int(rng.integers(1, game_params["joker_max"] + 1))
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    print(f"Date demo create în {csv_path}")
