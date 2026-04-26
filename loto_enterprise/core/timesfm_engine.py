"""
TimesFM Engine v2 — Utilizare MAXIMĂ a Google TimesFM.

Funcționalități exploatate:
  1. forecast_with_covariates() cu XReg real (dynamic + static covariates)
  2. Quantile Uncertainty Analysis (9 cuantile: p10→p90)
  3. Multi-Horizon Forecasting (H=20, weighted decay)
  4. Context Anomaly Detection (return_forecast_on_context)
  5. Adaptive Local Tuning (bias correction din erori recente)
  6. Normalize inputs pentru performanță optimă
"""

from __future__ import annotations

import logging
import numpy as np
from typing import Dict, List, Set, Tuple, Optional

# Modul importat condițional din loto_engine
HAS_TIMESFM = False
_TFM_ERROR = None
try:
    import torch
    import timesfm
    HAS_TIMESFM = True
except ImportError as e:
    _TFM_ERROR = str(e)
except Exception as e:
    _TFM_ERROR = str(e)


def _detect_device() -> str:
    try:
        if HAS_TIMESFM and torch.cuda.is_available():
            return "gpu"
    except Exception:
        pass
    return "cpu"


def _align_context(ctx_len: int, patch_len: int = 32) -> int:
    """Aliniază context_len la următorul multiplu de patch_len."""
    aligned = ((ctx_len + patch_len - 1) // patch_len) * patch_len
    # Extins pentru a acoperi TOT istoricul loto (fără trunchiere la 2048).
    # TimesFM v2.0 acceptă și contexte mai lungi atâta timp cât RAM-ul permite.
    # Folosim 4096 ca prag de siguranță superioară pentru loto (istoricul având ~2500 extrageri)
    return min(aligned, _get_context_cap())


# Cache global pentru a reține modelul în memorie între execuțiile worker-ului
_GLOBAL_TFM_MODEL = None
_GLOBAL_TFM_CTX = None
_GLOBAL_TFM_HORIZON = None

def _get_context_cap() -> int:
    """Limitează contextul pe CPU pentru a preveni blocarea laptop-ului."""
    if _detect_device() == "cpu":
        return 512  # Suficient pentru trenduri, dar rapid pe CPU
    return 4096 # Full power pe GPU

def _init_model(ctx_len: int, horizon: int, device: str):
    """Inițializează modelul TimesFM și îl păstrează în memorie (Singleton)."""
    global _GLOBAL_TFM_MODEL, _GLOBAL_TFM_CTX, _GLOBAL_TFM_HORIZON
    
    # FORȚĂM dimensiunea maximă a contextului pentru a nu mai reîncărca modelul
    # de fiecare dată când se schimbă dimensiunea ferestrei de analiză.
    context_cap = _get_context_cap()
    aligned_ctx = _align_context(context_cap)
    
    # Returnăm din cache dacă modelul există deja și se potrivesc parametrii
    if _GLOBAL_TFM_MODEL is not None and _GLOBAL_TFM_CTX == aligned_ctx and _GLOBAL_TFM_HORIZON == horizon:
        # Nu logăm HOT START pentru a nu spama
        return _GLOBAL_TFM_MODEL, aligned_ctx
        
    logging.info(f"[TIMESFM-V2] Încărcare model complet nou în memorie (COLD START) - Size: {aligned_ctx}...")
    
    # Prevenim hang-urile cauzate de verificarea online a modelului pe Windows
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    
    tfm = timesfm.TimesFm(
        hparams=timesfm.TimesFmHparams(
            context_len=aligned_ctx,
            horizon_len=horizon,
            input_patch_len=32,
            output_patch_len=128,
            num_layers=50,
            use_positional_embedding=False,
            per_core_batch_size=32,
            quantiles=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
            backend=device,
        ),
        checkpoint=timesfm.TimesFmCheckpoint(
            huggingface_repo_id="google/timesfm-2.0-500m-pytorch"
        ),
    )
    
    logging.info("[TIMESFM-V2] Modelul a fost încărcat și inițializat cu succes.")
    
    _GLOBAL_TFM_MODEL = tfm
    _GLOBAL_TFM_CTX = aligned_ctx
    _GLOBAL_TFM_HORIZON = horizon
    
    return tfm, aligned_ctx


# ---------------------------------------------------------------------------
#  1. Construire serii binare per număr
# ---------------------------------------------------------------------------
def build_binary_series(
    draw_matrix: np.ndarray | None,
    joker_vals: np.ndarray | None,
    start: int,
    end: int,
    max_num: int,
    is_joker: bool,
) -> Tuple[List[np.ndarray], List[int]]:
    """Creează seria 0/1 pentru fiecare număr în fereastra [start, end)."""
    all_series: List[np.ndarray] = []
    num_map: List[int] = []

    for num in range(1, max_num + 1):
        if is_joker and joker_vals is not None:
            series = np.array(
                [1.0 if int(v) == num else 0.0 for v in joker_vals[start:end]],
                dtype=np.float32,
            )
        elif draw_matrix is not None:
            series = np.array(
                [1.0 if num in row else 0.0 for row in draw_matrix[start:end]],
                dtype=np.float32,
            )
        else:
            continue

        if len(series) >= 32:
            all_series.append(series)
            num_map.append(num)

    return all_series, num_map


# ---------------------------------------------------------------------------
#  2. Construire covariate REALE pentru forecast_with_covariates()
# ---------------------------------------------------------------------------
def build_covariates(
    all_series: List[np.ndarray],
    num_map: List[int],
    draw_matrix: np.ndarray | None,
    max_num: int,
    horizon: int,
) -> Tuple[Dict, Dict]:
    """
    Construiește covariate dinamice și statice pentru TimesFM XReg.

    Dynamic numerical (per serie, per timestep, lungime = context + horizon):
      - rolling_freq_10:  Frecvența mobilă pe 10 extrageri
      - rolling_freq_30:  Frecvența mobilă pe 30 extrageri
      - gap_counter:      Câte extrageri de la ultima apariție
      - draw_sum:         Suma numerelor extrase la fiecare extragere (normalizată)

    Static numerical (per serie):
      - number_norm:      Valoarea numărului normalizată la [0, 1]
      - hist_frequency:   Frecvența globală pe tot contextul
      - last_seen_pos:    Poziția (normalizată 0-1) în care a apărut ultima dată
    """
    ctx_len = len(all_series[0]) if all_series else 0
    total_len = ctx_len + horizon

    # Pre-calculăm sumele extragerilor (aceeași pentru toate seriile)
    if draw_matrix is not None and draw_matrix.shape[0] >= ctx_len:
        end = draw_matrix.shape[0]
        start = end - ctx_len
        draw_sums = np.sum(draw_matrix[start:end], axis=1).astype(np.float32)
        sum_mean = np.mean(draw_sums) if draw_sums.size else 1.0
        draw_sums_norm = (draw_sums / max(sum_mean, 1.0)).tolist()
        # Extrapolăm viitorul cu media ultimelor 10
        future_sum = float(np.mean(draw_sums[-10:])) / max(sum_mean, 1.0) if len(draw_sums) >= 10 else 1.0
        draw_sums_full = draw_sums_norm + [future_sum] * horizon
    else:
        draw_sums_full = [1.0] * total_len

    dyn_rolling_10: List[List[float]] = []
    dyn_rolling_30: List[List[float]] = []
    dyn_gap: List[List[float]] = []
    dyn_sum: List[List[float]] = []
    dyn_neighbor: List[List[float]] = [] # Metoda 2: Influența vecinilor
    static_num_norm: List[float] = []
    static_hist_freq: List[float] = []
    static_last_pos: List[float] = [] # Metoda 2: Poziția ultimă

    for i, (series, num) in enumerate(zip(all_series, num_map)):
        # --- Rolling frequency (short & long) ---
        rf10 = _rolling_mean(series, 10)
        rf30 = _rolling_mean(series, 30)

        # Extrapolăm viitorul cu ultima valoare
        last_rf10 = float(rf10[-1]) if len(rf10) else 0.0
        last_rf30 = float(rf30[-1]) if len(rf30) else 0.0
        rf10_full = rf10.tolist() + [last_rf10] * horizon
        rf30_full = rf30.tolist() + [last_rf30] * horizon

        # --- Gap counter ---
        gaps = _gap_series(series)
        last_gap = float(gaps[-1]) if len(gaps) else 0.0
        # În viitor, gap-ul crește
        future_gaps = [last_gap + j + 1 for j in range(horizon)]
        gap_full = gaps.tolist() + future_gaps
        # Normalizăm gap-ul
        gap_max = max(max(gap_full), 1.0)
        gap_full = [g / gap_max for g in gap_full]

        dyn_rolling_10.append(rf10_full[:total_len])
        dyn_rolling_30.append(rf30_full[:total_len])
        dyn_gap.append(gap_full[:total_len])
        dyn_sum.append(draw_sums_full[:total_len])
        
        # --- Neighbor Influence (Metoda 2) ---
        # Calculăm dacă numerele vecine (n-1, n+1) au ieșit recent
        # Acest semnal e dinamic per timestep
        neighbor_signal = _calculate_neighbor_signal(num, all_series, num_map)
        last_neigh = float(neighbor_signal[-1]) if len(neighbor_signal) else 0.0
        dyn_neighbor.append(neighbor_signal.tolist() + [last_neigh] * horizon)

        # --- Static (Metoda 2) ---
        static_num_norm.append(num / max_num)
        static_hist_freq.append(float(np.mean(series)))
        
        # Calculăm ultima poziție în care a apărut (normalizată)
        last_pos = _get_last_position(num, draw_matrix)
        draw_n = draw_matrix.shape[1] if draw_matrix is not None else 6
        static_last_pos.append(last_pos / float(draw_n)) 

    dyn_numerical = {
        "rolling_freq_10": dyn_rolling_10,
        "rolling_freq_30": dyn_rolling_30,
        "gap_counter": dyn_gap,
        "draw_sum": dyn_sum,
        "neighbor_influence": dyn_neighbor,
    }
    static_numerical = {
        "number_norm": static_num_norm,
        "hist_frequency": static_hist_freq,
        "last_seen_pos": static_last_pos,
    }

    return dyn_numerical, static_numerical


def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    """Media mobilă cu padding la stânga."""
    if len(arr) == 0:
        return arr
    cumsum = np.cumsum(arr)
    result = np.empty_like(arr)
    for i in range(len(arr)):
        start = max(0, i - window + 1)
        result[i] = cumsum[i] - (cumsum[start - 1] if start > 0 else 0)
        result[i] /= (i - start + 1)
    return result


def _gap_series(arr: np.ndarray) -> np.ndarray:
    """Construiește seria gap-urilor (câte extrageri de la ultima apariție)."""
    gaps = np.zeros(len(arr), dtype=np.float32)
    gap = 0.0
    for i in range(len(arr)):
        if arr[i] > 0.5:
            gap = 0.0
        else:
            gap += 1.0
        gaps[i] = gap
    return gaps


def _calculate_neighbor_signal(num: int, all_series: List[np.ndarray], num_map: List[int]) -> np.ndarray:
    """Calculează influența vecinilor (n-1, n+1) asupra numărului current."""
    ctx_len = len(all_series[0])
    signal = np.zeros(ctx_len, dtype=np.float32)
    
    n_minus = num - 1
    n_plus = num + 1
    
    # Găsim indicii seriilor pentru vecini
    idx_minus = -1
    idx_plus = -1
    for i, m in enumerate(num_map):
        if m == n_minus: idx_minus = i
        if m == n_plus: idx_plus = i
    
    if idx_minus != -1:
        signal += all_series[idx_minus] * 0.5
    if idx_plus != -1:
        signal += all_series[idx_plus] * 0.5
        
    return signal


def _get_last_position(num: int, draw_matrix: np.ndarray | None) -> float:
    """Găsește ultima poziție (1-6) în care a apărut numărul."""
    if draw_matrix is None:
        return 0.0
    for i in range(len(draw_matrix) - 1, -1, -1):
        row = draw_matrix[i]
        for pos, val in enumerate(row):
            if int(val) == num:
                return float(pos + 1)
    return 0.0


# ---------------------------------------------------------------------------
#  3. Scoring NQI (Neural Quality Index) — combină toate semnalele
# ---------------------------------------------------------------------------
def compute_nqi_scores(
    all_series: List[np.ndarray],
    num_map: List[int],
    point_forecast: np.ndarray,
    full_forecast: np.ndarray,
    cov_forecast: np.ndarray | None,
    horizon: int,
    optimized_weights: Optional[Dict[str, float]] = None,
) -> Dict[int, float]:
    """
    Calculează Neural Quality Index pentru fiecare număr.

    NQI = 0.20 * PointScore
        + 0.25 * HorizonTrend
        + 0.20 * Stability (quantile spread)
        + 0.20 * CovariateBoost
        + 0.15 * AdaptiveBias
    """
    weights_dict = optimized_weights if optimized_weights else {
        "p_val": 0.40, "h_score": 0.10, "stability": 0.10, "cov_boost": 0.15, "adaptive": 0.25
    }
    scores: Dict[int, float] = {}

    for i, num in enumerate(num_map):
        # --- A. Point forecast (predicția imediată) ---
        p_val = float(point_forecast[i, 0]) if point_forecast.ndim >= 2 else float(point_forecast[i])

        # --- B. Multi-Horizon Trend (weighted decay) ---
        h_len = min(horizon, point_forecast.shape[1] if point_forecast.ndim >= 2 else 1)
        h_score = 0.0
        h_weight_total = 0.0
        for h in range(h_len):
            decay_w = 1.0 / (1.15 ** h)
            val = float(point_forecast[i, h]) if point_forecast.ndim >= 2 else float(point_forecast[i])
            h_score += val * decay_w
            h_weight_total += decay_w
        h_score = h_score / max(h_weight_total, 0.001)

        # --- C. Quantile Stability (spread p10-p90) ---
        stability = 0.5
        if full_forecast.ndim >= 3 and full_forecast.shape[2] >= 9:
            q10 = float(full_forecast[i, 0, 0])
            q50 = float(full_forecast[i, 0, 4])
            q90 = float(full_forecast[i, 0, 8])
            spread = max(0.0001, q90 - q10)
            stability = 1.0 / (1.0 + spread * 5.0)
            # Bonus dacă mediana e pozitivă
            if q50 > 0:
                stability += 0.1

        # --- D. Covariate Boost ---
        cov_boost = 0.0
        if cov_forecast is not None:
            try:
                cov_val = float(cov_forecast[i, 0]) if cov_forecast.ndim >= 2 else float(cov_forecast[i])
                cov_boost = cov_val
            except (IndexError, TypeError):
                cov_boost = 0.0

        # --- E. Adaptive Bias (recent momentum & GAP TRIGGER) ---
        series = all_series[i]
        recent_5 = series[-5:] if len(series) >= 5 else series
        recent_15 = series[-15:] if len(series) >= 15 else series
        recent_freq_5 = float(np.mean(recent_5))
        recent_freq_15 = float(np.mean(recent_15))
        global_freq = float(np.mean(series))
        
        # Calculăm distanța față de ultima apariție (Gap)
        total_draws_ctx = len(series)
        total_appearances = np.sum(series)
        avg_gap = total_draws_ctx / max(total_appearances, 1.0)
        
        current_gap = 0.0
        for val in reversed(series):
            if val > 0.5: break
            current_gap += 1.0
        
        adaptive = 1.0
        if current_gap >= avg_gap and avg_gap > 0 and total_appearances > 3:
            # Gap Trigger: e întârziat peste media lui obișnuită
            adaptive = 1.25
        elif recent_freq_5 > recent_freq_15 + 0.1:
            # Prea cald, overdue for a cooling off
            adaptive = 0.90
        elif recent_freq_5 < recent_freq_15 - 0.1 and global_freq > 0:
            # Due for a hit pe termen scurt
            adaptive = 1.15
        elif recent_freq_5 > 0 and recent_freq_15 > 0:
            # Consistent momentum
            adaptive = 1.05

        # --- NQI Final (Ponderi optimizate pt Loto) ---
        nqi = (
            p_val * weights_dict["p_val"]
            + h_score * weights_dict["h_score"]
            + stability * weights_dict["stability"]
            + cov_boost * weights_dict["cov_boost"]
            + (p_val * adaptive) * weights_dict["adaptive"]
        )
        scores[num] = nqi

    return scores


def optimize_nqi_weights(
    all_series: List[np.ndarray],
    num_map: List[int],
    point_forecast: np.ndarray,
    full_forecast: np.ndarray,
    cov_forecast: np.ndarray | None,
) -> Dict[str, float]:
    """
    Metoda 3: Optimizare locală a ponderilor NQI.
    Simulează performanța pe ultimele 5 extrageri și alege ponderile optime.
    """
    logging.info("[TIMESFM-V2] Metoda 3: Optimizare locală a ponderilor NQI...")
    
    # Variante de ponderi (Ensemble Candidates)
    candidates = [
        {"p_val": 0.40, "h_score": 0.10, "stability": 0.10, "cov_boost": 0.15, "adaptive": 0.25}, # Precision & Gap Trigger Heavy
        {"p_val": 0.50, "h_score": 0.05, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.25}, # Point Heavy
        {"p_val": 0.30, "h_score": 0.15, "stability": 0.15, "cov_boost": 0.20, "adaptive": 0.20}, # Balanced
    ]
    
    best_w = candidates[0]
    best_score = -1.0
    
    # Validare simplificată (look-back pe serii)
    # În mod real ar trebui un walk-forward, dar aici facem o aproximare rapidă
    # bazată pe corelația scorurilor curente cu ultimele 3 apariții
    
    for w in candidates:
        current_nqi = compute_nqi_scores(all_series, num_map, point_forecast, full_forecast, cov_forecast, 1, w)
        # Evaluăm performanța retrospectivă simulată pe ultimele 3 extrageri
        perf = 0.0
        for num, score in current_nqi.items():
            idx = num_map.index(num)
            recent_hits = np.sum(all_series[idx][-3:]) # Performanța mai relevantă pe extragerile cele mai recente
            # Penalizăm varianța pentru a prefera predicțiile consistente
            perf += score * (recent_hits * 1.5)
            
        if perf > best_score:
            best_score = perf
            best_w = w
            
    logging.info(f"[TIMESFM-V2] Ponderi optime selectate: {best_w}")
    return best_w


# ---------------------------------------------------------------------------
#  4. Anomaly Scoring pe context (return_forecast_on_context)
# ---------------------------------------------------------------------------
def compute_context_anomaly(
    tfm,
    all_series: List[np.ndarray],
    num_map: List[int],
) -> Dict[int, float]:
    """
    Rulează forecast cu return_forecast_on_context=True.
    Compară predicțiile modelului pe context cu valorile reale.
    Returnează un scor de „predictibilitate" per număr (mai mare = mai predictibil).
    """
    anomaly_scores: Dict[int, float] = {}
    try:
        ctx_pred, _ = tfm.forecast(
            inputs=all_series,
            freq=[0] * len(all_series),
            return_forecast_on_context=True,
            normalize=True,
        )
        for i, num in enumerate(num_map):
            actual = all_series[i]
            predicted = ctx_pred[i, : len(actual)]
            if len(predicted) == len(actual):
                mae = float(np.mean(np.abs(predicted - actual)))
                anomaly_scores[num] = 1.0 / (1.0 + mae * 3.0)
            else:
                anomaly_scores[num] = 0.5
    except Exception as e:
        logging.warning(f"[TIMESFM] Context anomaly detection failed: {e}")
        for num in num_map:
            anomaly_scores[num] = 0.5

    return anomaly_scores


# ---------------------------------------------------------------------------
#  5. Entry point principal: get_timesfm_scores_v2()
# ---------------------------------------------------------------------------
def get_timesfm_scores_v2(
    data,
    draw_matrix: np.ndarray | None,
    params: dict,
    is_joker_drum: bool = False,
    context_len: int = 4096,
    audit: dict | None = None,
    is_regressive_step: bool = False,
) -> Dict[int, float]:
    """
    Motor principal TimesFM v2 — folosește TOATE capabilitățile API-ului.
    """
    if not HAS_TIMESFM:
        logging.error(f"[TIMESFM-V2] Librăria lipsește: {_TFM_ERROR}")
        return {}

    total_available = len(data) if data is not None else 0
    ctx_len = min(context_len, total_available, _get_context_cap())
    if ctx_len < 32:
        return {}

    horizon = 20 # Capacitate maximă pentru analiza de trend pe termen lung
    max_num = 20 if is_joker_drum else int(params.get("max_n", 49))
    device = _detect_device()

    context_windows = [ctx_len]
    if not is_regressive_step:
        is_cpu = (_detect_device() == "cpu")
        if not is_cpu and ctx_len > 1500:
            context_windows.append(1000)
        if ctx_len > 600:
            context_windows.append(500)
        if ctx_len > 250:
            context_windows.append(200)
        if ctx_len > 80:
            context_windows.append(50)
        
    ensemble_scores: List[Dict[int, float]] = []
    
    try:
        for current_ctx in context_windows:
            logging.info(f"[TIMESFM-V2] Processing context window: {current_ctx} draws...")
            tfm, aligned_ctx = _init_model(current_ctx, horizon, device)
            
            # Re-build/Pad series for this context
            start_idx = max(0, total_available - current_ctx)
            joker_vals = data["joker"].values if is_joker_drum and "joker" in data.columns else None
            all_series, num_map = build_binary_series(
                draw_matrix, joker_vals, start_idx, total_available, max_num, is_joker_drum
            )
            
            if not all_series: continue
            
            raw_len = len(all_series[0])
            if raw_len < aligned_ctx:
                pad_len = aligned_ctx - raw_len
                all_series = [np.concatenate([np.zeros(pad_len, dtype=np.float32), s]) for s in all_series]
            
            # Forecast & Scores
            point_forecast, full_forecast = tfm.forecast(inputs=all_series, freq=[0]*len(all_series), normalize=True)
            
            cov_forecast = None
            if not is_regressive_step:
                try:
                    dyn_num, static_num = build_covariates(all_series, num_map, draw_matrix, max_num, horizon)
                    cov_result = tfm.forecast_with_covariates(
                        inputs=[s.tolist() for s in all_series],
                        dynamic_numerical_covariates=dyn_num,
                        static_numerical_covariates=static_num,
                        freq=[0]*len(all_series),
                        xreg_mode="xreg + timesfm",
                        normalize_xreg_target_per_input=True,
                        force_on_cpu=(device == "cpu"),
                    )
                    cov_forecast = np.array(cov_result[0]) if isinstance(cov_result, tuple) else np.array(cov_result)
                except Exception: pass
            
            if is_regressive_step:
                # Mod ultra-rapid pentru calibrare: folosim doar point forecast-ul de baza
                nqi = {num: float(point_forecast[i, 0]) for i, num in enumerate(num_map)}
            else:
                anomaly_scores = compute_context_anomaly(tfm, all_series, num_map)
                opt_weights = optimize_nqi_weights(all_series, num_map, point_forecast, full_forecast, cov_forecast)
                nqi = compute_nqi_scores(all_series, num_map, point_forecast, full_forecast, cov_forecast, horizon, opt_weights)
                
                # Apply Anomaly
                for n in num_map:
                    nqi[n] = nqi.get(n, 0.0) * (0.8 + 0.4 * anomaly_scores.get(n, 0.5))
            
            ensemble_scores.append(nqi)

        # Average ensemble scores
        if not ensemble_scores: return {}
        
        final_nqi: Dict[int, float] = {}
        all_nums = set().union(*(s.keys() for s in ensemble_scores))
        for num in all_nums:
            vals = [s[num] for s in ensemble_scores if num in s]
            final_nqi[num] = float(np.mean(vals)) if vals else 0.0
            
        # Audit
        if audit is not None:
            sorted_scores = sorted(final_nqi.items(), key=lambda x: x[1], reverse=True)
            audit["timesfm_predictions"] = {n: round(s, 6) for n, s in sorted_scores[:25]}
            audit["ensemble_windows"] = context_windows
            audit["timesfm_version"] = "v2 Ensemble (Full + Mid + Short)"
            audit["raw_ensemble_scores"] = ensemble_scores # Optimizare Cache pentru Regressive Blacklist

        logging.info(f"[TIMESFM-V2] Ensemble complete ({len(context_windows)} windows). Top 5: {sorted(final_nqi.items(), key=lambda x: -x[1])[:5]}")
        return final_nqi

    except Exception as e:
        logging.error(f"[TIMESFM-V2] Eroare critică în ensemble: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return {}


# ---------------------------------------------------------------------------
#  6. Regressive Blacklist (multi-pass cu TimesFM)
# ---------------------------------------------------------------------------
def get_regressive_blacklist_v2(
    data_full,
    draw_matrix_full: np.ndarray | None,
    params: dict,
    sim_depth_pct: int,
    rebuild_matrix_fn,
    is_joker: bool = False,
    audit: dict | None = None,
) -> Set[int]:
    """
    Analiză regresivă multi-pass: rulează TimesFM pe ferestre de 100%, 90%, 80% etc.
    Doar numerele confirmate ca slabe în TOATE pașii sunt eliminate.
    """
    if not HAS_TIMESFM:
        return set()

    # Fallback / True Regressive Logic (depth: sim_depth_pct%)
    # Dezactivam cache-ul Ensemble pentru a asigura consistenta 100% cu Auto-Tuning-ul
    logging.info(f"[TIMESFM-V2] Regressive Blacklist Analysis (depth: {sim_depth_pct}%)...")
    steps = list(range(100, max(sim_depth_pct - 1, 9), -10))
    all_blacklists: List[Set[int]] = []
    total_rows = len(data_full)

    for step in steps:
        num_rows = int(total_rows * (step / 100))
        if num_rows < 32:
            continue

        data_slice = data_full.tail(num_rows).copy()
        dm_slice = draw_matrix_full[-num_rows:] if draw_matrix_full is not None else None

        logging.info(f"[TIMESFM-V2] Pas regresiv fallback: {step}% ({num_rows} extrageri)...")
        scores = get_timesfm_scores_v2(
            data_slice, dm_slice, params,
            is_joker_drum=is_joker, context_len=num_rows,
            is_regressive_step=True
        )

        if scores:
            vals = list(scores.values())
            threshold = np.percentile(vals, 25)
            bl = {n for n, s in scores.items() if s <= threshold}
            all_blacklists.append(bl)

    if not all_blacklists:
        return set()

    # Intersecție: trebuie pe blacklist în TOATE pașii
    result = all_blacklists[0]
    for bl in all_blacklists[1:]:
        result = result.intersection(bl)

    logging.info(f"[TIMESFM-V2] Regressive blacklist final: {len(result)} numere ({sorted(result)})")
    return result


# ---------------------------------------------------------------------------
#  7. Pool Selection (top N din scoruri, excl. blacklist)
# ---------------------------------------------------------------------------
def select_pool_from_scores(
    scores: Dict[int, float],
    pool_size: int,
    blacklist: Set[int],
    audit: dict | None = None,
) -> List[int]:
    """Selectează top N numere din scoruri, excluzând blacklist-ul."""
    valid = {n: s for n, s in scores.items() if n not in blacklist}
    sorted_nums = sorted(valid.items(), key=lambda x: x[1], reverse=True)
    pool = [n for n, _ in sorted_nums[:pool_size]]

    if audit is not None:
        audit["timesfm_predictions"] = {n: round(s, 6) for n, s in sorted_nums[:25]}

    return sorted(pool)


# ---------------------------------------------------------------------------
#  8. Variant Anomaly Filter
# ---------------------------------------------------------------------------
def filter_variants_by_anomaly_v2(
    variants: List[List[int]],
    scores: Dict[int, float],
    threshold: float = 0.7,
) -> List[List[int]]:
    """Elimină variantele considerate anomalii statistice de model."""
    if not scores:
        return variants

    max_score = max(scores.values()) if scores else 1.0
    filtered = []
    for v in variants:
        probs = [scores.get(n, 0.0001) for n in v]
        mean_prob = np.mean(probs)
        anomaly = 1.0 - (mean_prob / max(max_score, 0.0001))
        if anomaly <= threshold:
            filtered.append(v)

    return filtered if filtered else variants  # Nu returnăm lista goală
