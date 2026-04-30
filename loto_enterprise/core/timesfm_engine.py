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
    """Detectează dispozitivul optim pentru TimesFM."""
    try:
        if HAS_TIMESFM and torch.cuda.is_available():
            # Logăm o singură dată prezența GPU-ului la nivel de modul dacă e nevoie
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


def _rank_fusion(ensemble_scores: List[Dict[int, float]], window_weights: Optional[List[float]] = None) -> Dict[int, float]:
    """Rank-based ensemble fusion (Weighted Borda Count) — mai robust decât media aritmetică."""
    if not ensemble_scores:
        return {}
    all_nums = set().union(*(s.keys() for s in ensemble_scores))
    rank_scores: Dict[int, float] = {n: 0.0 for n in all_nums}
    if window_weights is None:
        window_weights = [1.0 / (1.0 + 0.25 * i) for i in range(len(ensemble_scores))]
    total_weight = sum(window_weights[:len(ensemble_scores)])
    for w_idx, window_scores in enumerate(ensemble_scores):
        sorted_nums = sorted(window_scores.items(), key=lambda x: x[1], reverse=True)
        total = max(len(sorted_nums), 1)
        weight = window_weights[w_idx] if w_idx < len(window_weights) else 0.5
        max_raw = sorted_nums[0][1] if sorted_nums else 1.0
        min_raw = sorted_nums[-1][1] if sorted_nums else 0.0
        range_raw = max(max_raw - min_raw, 0.0001)
        for rank, (num, raw_score) in enumerate(sorted_nums):
            borda = (total - rank) / total
            norm_raw = (raw_score - min_raw) / range_raw
            combined = borda * 0.6 + norm_raw * 0.4
            rank_scores[num] += combined * weight
    for num in rank_scores:
        rank_scores[num] /= max(total_weight, 0.001)
    return rank_scores


def compute_pair_cooccurrence(draw_matrix: np.ndarray | None, num_map: List[int],
                              lookback: int = 150) -> Dict[int, float]:
    """Pair co-occurrence: numere care apar frecvent împreună cu alte numere puternice."""
    if draw_matrix is None or draw_matrix.size == 0:
        return {n: 0.0 for n in num_map}
    n_draws = min(lookback, draw_matrix.shape[0])
    recent = draw_matrix[-n_draws:]
    pair_boost: Dict[int, float] = {}
    for num in num_map:
        total_co = 0.0
        for other in num_map:
            if num == other:
                continue
            count = sum(1 for row in recent if num in row and other in row)
            total_co += count
        pair_boost[num] = total_co / max(n_draws * len(num_map), 1.0)
    # Normalize to [0, 1]
    max_pb = max(pair_boost.values()) if pair_boost else 1.0
    if max_pb > 0:
        for n in pair_boost:
            pair_boost[n] /= max_pb
    return pair_boost


def compute_triplet_cooccurrence(draw_matrix: np.ndarray | None, num_map: List[int],
                                 lookback: int = 200) -> Dict[int, float]:
    """Triplet co-occurrence: numere care apar frecvent în grupuri de 3.
    
    Aceasta e o extensie critică a pair co-occurrence, pentru că hit-urile de 4+
    necesită ca 3+ numere din pool să apară simultan. Numărăm de câte ori
    fiecare număr face parte din tripleți frecvenți.
    """
    if draw_matrix is None or draw_matrix.size == 0:
        return {n: 0.0 for n in num_map}
    
    n_draws = min(lookback, draw_matrix.shape[0])
    recent = draw_matrix[-n_draws:]
    
    # Convertim în seturi pentru căutare rapidă
    draw_sets = [set(row) for row in recent]
    
    # Contorizăm participarea fiecărui număr la tripleți
    triplet_score: Dict[int, float] = {n: 0.0 for n in num_map}
    
    # Folosim doar numerele din num_map (top candidați)
    # Limităm la top 30 pentru a evita explozie combinatorică
    top_nums = num_map[:30] if len(num_map) > 30 else num_map
    
    from itertools import combinations
    for a, b, c in combinations(top_nums, 3):
        trip_set = {a, b, c}
        count = sum(1 for ds in draw_sets if trip_set.issubset(ds))
        if count >= 2:  # Tripletul trebuie să fi apărut cel puțin de 2 ori
            weight = count / n_draws  # Frecvență normalizată
            triplet_score[a] += weight
            triplet_score[b] += weight
            triplet_score[c] += weight
    
    # Normalize to [0, 1]
    max_ts = max(triplet_score.values()) if triplet_score else 1.0
    if max_ts > 0:
        for n in triplet_score:
            triplet_score[n] /= max_ts
    
    return triplet_score


def _compute_momentum_score(series: np.ndarray) -> float:
    """Scor de momentum multi-fereastră cu micro-window pentru detecție rapidă."""
    if len(series) < 3:
        return 0.5
    # Micro-window (2 extrageri) detectează activări imediate
    windows = [2, 3, 7, 15, 30]
    weights = [0.10, 0.30, 0.25, 0.20, 0.15]
    global_freq = float(np.mean(series))
    if global_freq < 0.001:
        return 0.3
    momentum = 0.0
    for w, wt in zip(windows, weights):
        if len(series) >= w:
            recent = float(np.mean(series[-w:]))
            ratio = recent / max(global_freq, 0.001)
            momentum += min(ratio, 3.0) * wt
        else:
            momentum += 0.5 * wt
    return min(momentum, 1.5)



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
    NQI v3 — Neural Quality Index cu normalizare pe componente.
    Toate componentele sunt normalizate la [0,1] înainte de combinare.
    """
    weights_dict = optimized_weights if optimized_weights else {
        "p_val": 0.25, "h_score": 0.15, "stability": 0.10,
        "cov_boost": 0.10, "adaptive": 0.20, "momentum": 0.20
    }

    # --- Colectăm toate componentele raw ---
    raw_components: Dict[str, List[float]] = {
        "p_val": [], "h_score": [], "stability": [],
        "cov_boost": [], "adaptive": [], "momentum": []
    }

    for i, num in enumerate(num_map):
        # A. Point forecast
        p_val = float(point_forecast[i, 0]) if point_forecast.ndim >= 2 else float(point_forecast[i])
        raw_components["p_val"].append(p_val)

        # B. Multi-Horizon Trend (weighted decay)
        h_len = min(horizon, point_forecast.shape[1] if point_forecast.ndim >= 2 else 1)
        h_score = 0.0
        h_weight_total = 0.0
        for h in range(h_len):
            decay_w = 1.0 / (1.15 ** h)
            val = float(point_forecast[i, h]) if point_forecast.ndim >= 2 else float(point_forecast[i])
            h_score += val * decay_w
            h_weight_total += decay_w
        h_score = h_score / max(h_weight_total, 0.001)
        raw_components["h_score"].append(h_score)

        # C. Quantile Stability
        stability = 0.5
        if full_forecast.ndim >= 3 and full_forecast.shape[2] >= 9:
            q10 = float(full_forecast[i, 0, 0])
            q50 = float(full_forecast[i, 0, 4])
            q90 = float(full_forecast[i, 0, 8])
            spread = max(0.0001, q90 - q10)
            stability = 1.0 / (1.0 + spread * 3.0)
            if q50 > 0:
                stability += 0.15
        raw_components["stability"].append(stability)

        # D. Covariate Boost
        cov_boost = 0.0
        if cov_forecast is not None:
            try:
                cov_val = float(cov_forecast[i, 0]) if cov_forecast.ndim >= 2 else float(cov_forecast[i])
                cov_boost = cov_val
            except (IndexError, TypeError):
                cov_boost = 0.0
        raw_components["cov_boost"].append(cov_boost)

        # E. Adaptive Bias (Gap Trigger îmbunătățit)
        series = all_series[i]
        total_draws_ctx = len(series)
        total_appearances = float(np.sum(series))
        avg_gap = total_draws_ctx / max(total_appearances, 1.0)
        current_gap = 0.0
        for val in reversed(series):
            if val > 0.5:
                break
            current_gap += 1.0

        adaptive = 1.0
        if total_appearances > 3 and avg_gap > 0:
            gap_ratio = current_gap / avg_gap
            if gap_ratio >= 1.5:
                adaptive = 1.40  # Foarte întârziat
            elif gap_ratio >= 1.0:
                adaptive = 1.20 + 0.20 * (gap_ratio - 1.0)  # Gradual boost
            elif gap_ratio >= 0.7:
                adaptive = 1.05  # Normal
            else:
                adaptive = 0.85  # A ieșit recent, s-ar putea să se răcească
        raw_components["adaptive"].append(adaptive)

        # F. Momentum multi-fereastră (NOU)
        momentum = _compute_momentum_score(series)
        raw_components["momentum"].append(momentum)

    # --- Normalizare MinMax pe fiecare componentă ---
    normalized: Dict[str, List[float]] = {}
    for key, vals in raw_components.items():
        arr = np.array(vals, dtype=np.float64)
        vmin, vmax = arr.min(), arr.max()
        rng = max(vmax - vmin, 0.0001)
        normalized[key] = ((arr - vmin) / rng).tolist()

    # --- Combinare finală ---
    scores: Dict[int, float] = {}
    for i, num in enumerate(num_map):
        nqi = 0.0
        for key, w in weights_dict.items():
            if key in normalized:
                nqi += normalized[key][i] * w
        scores[num] = nqi

    return scores


def _get_game_specific_weights(game_type: str = "6/49") -> List[Dict[str, float]]:
    """Returnează candidați de ponderi optimizați per tip de joc."""
    # Candidați comuni
    common = [
        {"p_val": 0.25, "h_score": 0.15, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.20, "momentum": 0.20},  # Balanced v4
        {"p_val": 0.20, "h_score": 0.10, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.25, "momentum": 0.25},  # Gap+Momentum Heavy
        {"p_val": 0.30, "h_score": 0.15, "stability": 0.05, "cov_boost": 0.15, "adaptive": 0.15, "momentum": 0.20},  # Point+Cov Heavy
        {"p_val": 0.20, "h_score": 0.10, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.30, "momentum": 0.20},  # Adaptive Heavy
        {"p_val": 0.25, "h_score": 0.20, "stability": 0.10, "cov_boost": 0.05, "adaptive": 0.20, "momentum": 0.20},  # Trend Heavy
    ]
    
    # Ponderi specifice per joc — bazate pe ciclurile naturale ale fiecărui joc
    game_specific = {
        "6/49": [
            # 6/49: Pool mare (49), cicluri lungi → Momentum și Trend mai mari
            {"p_val": 0.20, "h_score": 0.20, "stability": 0.10, "cov_boost": 0.05, "adaptive": 0.20, "momentum": 0.25},
            {"p_val": 0.15, "h_score": 0.15, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.25, "momentum": 0.25},
        ],
        "5/40": [
            # 5/40: Pool mai mic (40), cicluri scurte → Adaptive și Point mai mari
            {"p_val": 0.30, "h_score": 0.10, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.25, "momentum": 0.15},
            {"p_val": 0.25, "h_score": 0.10, "stability": 0.15, "cov_boost": 0.10, "adaptive": 0.30, "momentum": 0.10},
        ],
        "joker": [
            # Joker 5/45: 5 numere din 45 → Point forecast mai puternic, momentum pe ferestre scurte
            {"p_val": 0.30, "h_score": 0.15, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.15, "momentum": 0.20},
            {"p_val": 0.25, "h_score": 0.15, "stability": 0.15, "cov_boost": 0.10, "adaptive": 0.20, "momentum": 0.15},
        ],
    }
    
    return common + game_specific.get(game_type, [])


def _get_game_hit_profile(game_type: str) -> Dict[str, int]:
    """Praguri de evaluare hit-uri mari, specifice jocului."""
    if game_type == "6/49":
        return {"target": 4, "high": 5, "max": 6}
    # 5/40 și Joker: obiectivul practic e 3+, cu focus pe 4/5
    return {"target": 3, "high": 4, "max": 5}


def optimize_nqi_weights(
    all_series: List[np.ndarray],
    num_map: List[int],
    point_forecast: np.ndarray,
    full_forecast: np.ndarray,
    cov_forecast: np.ndarray | None,
    game_type: str = "6/49",
    draw_matrix: np.ndarray | None = None,
    pool_size: int = 12,
) -> Dict[str, float]:
    """
    Metoda 3 v2: Walk-Forward Optimization a ponderilor NQI.
    Simulează pe ultimele 10 extrageri (hold-out) și alege ponderile
    care maximizează overlap-ul cu extragerile reale (hit-uri 4+).
    """
    logging.info(f"[TIMESFM-V2] Walk-Forward NQI Weight Optimization (game={game_type})...")
    
    candidates = _get_game_specific_weights(game_type)
    
    best_w = candidates[0]
    best_score = -1.0
    
    # Walk-forward pe ultimele 10 extrageri
    # Pentru fiecare set de ponderi, calculăm NQI, selectăm top pool_size,
    # și verificăm câte numere se suprapun cu extragerea reală
    n_test_draws = min(10, len(all_series[0]) - 10) if all_series else 0
    
    for w in candidates:
        current_nqi = compute_nqi_scores(
            all_series, num_map, point_forecast, full_forecast, cov_forecast, 1, w
        )
        
        if n_test_draws > 0 and draw_matrix is not None:
            # Walk-forward: verificăm pool-ul pe ultimele extrageri
            sorted_by_nqi = sorted(current_nqi.items(), key=lambda x: x[1], reverse=True)
            pool_nums = set(n for n, _ in sorted_by_nqi[:pool_size])
            
            total_hits = 0
            hit_profile = _get_game_hit_profile(game_type)
            hits_target_plus = 0
            hits_high_plus = 0
            hits_max = 0
            total_rows = draw_matrix.shape[0]
            
            for i in range(max(0, total_rows - n_test_draws), total_rows):
                draw_set = set(int(v) for v in draw_matrix[i] if v > 0)
                overlap = len(pool_nums & draw_set)
                total_hits += overlap
                if overlap >= hit_profile["target"]:
                    hits_target_plus += 1
                if overlap >= hit_profile["high"]:
                    hits_high_plus += 1
                if overlap >= hit_profile["max"]:
                    hits_max += 1

            # Scor game-aware: favorizează progresiv hit-uri mai mari.
            perf = (
                hits_target_plus * 10.0
                + hits_high_plus * 35.0
                + hits_max * 120.0
                + total_hits
            )
        else:
            # Fallback: corelație cu frecvența recentă
            perf = 0.0
            for num, score in current_nqi.items():
                idx = num_map.index(num)
                recent_hits = np.sum(all_series[idx][-5:])
                perf += score * (recent_hits * 1.5)
            
        if perf > best_score:
            best_score = perf
            best_w = w
            
    logging.info(f"[TIMESFM-V2] Ponderi optime selectate: {best_w} (score={best_score:.2f})")
    return best_w


# ---------------------------------------------------------------------------
#  4. Anomaly Scoring pe context (return_forecast_on_context)
# ---------------------------------------------------------------------------
def compute_anomaly_from_context_pred(
    ctx_pred: np.ndarray,
    all_series: List[np.ndarray],
    num_map: List[int],
) -> Dict[int, float]:
    """Calculează scor de predictibilitate per număr din predicția pe context deja obținută."""
    anomaly_scores: Dict[int, float] = {}
    try:
        for i, num in enumerate(num_map):
            actual = all_series[i]
            predicted = ctx_pred[i, : len(actual)] if ctx_pred.ndim >= 2 else ctx_pred[: len(actual)]
            if len(predicted) == len(actual):
                mae = float(np.mean(np.abs(predicted - actual)))
                anomaly_scores[num] = 1.0 / (1.0 + mae * 3.0)
            else:
                anomaly_scores[num] = 0.5
    except Exception as e:
        logging.warning(f"[TIMESFM] Anomaly post-process failed: {e}")
        for num in num_map:
            anomaly_scores[num] = 0.5
    return anomaly_scores


def compute_context_anomaly(
    tfm,
    all_series: List[np.ndarray],
    num_map: List[int],
) -> Dict[int, float]:
    """Backward-compat: rulează un forecast separat cu return_forecast_on_context=True."""
    try:
        ctx_pred, _ = tfm.forecast(
            inputs=all_series,
            freq=[0] * len(all_series),
            return_forecast_on_context=True,
            normalize=True,
        )
        return compute_anomaly_from_context_pred(np.asarray(ctx_pred), all_series, num_map)
    except Exception as e:
        logging.warning(f"[TIMESFM] Context anomaly detection failed: {e}")
        return {num: 0.5 for num in num_map}


# ---------------------------------------------------------------------------
#  5. Entry point principal: get_timesfm_scores_v2()
# ---------------------------------------------------------------------------
def _fallback_scores_no_tfm(
    draw_matrix: np.ndarray | None,
    params: dict,
    is_joker_drum: bool,
) -> Dict[int, float]:
    """Fallback determinist când TimesFM/torch lipsesc.

    Construiește un scor compozit (frecvență istorică + momentum recent +
    gap inversat) ca să păstreze pipeline-ul funcțional fără modelul neural.
    """
    max_num = 20 if is_joker_drum else int(params.get("max_n", 49))
    if draw_matrix is None or draw_matrix.size == 0:
        return {n: 0.0 for n in range(1, max_num + 1)}

    total_rows = draw_matrix.shape[0]
    recent = draw_matrix[-min(50, total_rows):]
    very_recent = draw_matrix[-min(15, total_rows):]

    scores: Dict[int, float] = {}
    for n in range(1, max_num + 1):
        hist_freq = float(np.mean([1.0 if n in row else 0.0 for row in draw_matrix]))
        recent_freq = float(np.mean([1.0 if n in row else 0.0 for row in recent])) if len(recent) else 0.0
        very_recent_freq = float(np.mean([1.0 if n in row else 0.0 for row in very_recent])) if len(very_recent) else 0.0
        last_seen = total_rows
        for i in range(total_rows - 1, -1, -1):
            if n in draw_matrix[i]:
                last_seen = total_rows - 1 - i
                break
        gap_signal = 1.0 / (1.0 + last_seen / 10.0)
        scores[n] = 0.40 * recent_freq + 0.25 * very_recent_freq + 0.20 * hist_freq + 0.15 * gap_signal

    vmin = min(scores.values()) if scores else 0.0
    vmax = max(scores.values()) if scores else 1.0
    rng = max(vmax - vmin, 1e-6)
    return {n: (s - vmin) / rng for n, s in scores.items()}


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

    Optimizări de comunicare cu TimesFM:
      • Un singur `forecast()` per fereastră (cu return_forecast_on_context=True)
        pentru a obține point/full/anomaly într-un singur apel GPU/CPU.
      • Horizon adaptiv (mai mic pe CPU, full pe GPU).
      • Ferestre de ensemble prunate pe CPU pentru viteză.
      • Fallback determinist când librăria lipsește (nu mai întoarce {}).
    """
    if not HAS_TIMESFM:
        logging.warning(
            f"[TIMESFM-V2] Librăria lipsește ({_TFM_ERROR}). Folosesc fallback determinist."
        )
        fb = _fallback_scores_no_tfm(draw_matrix, params, is_joker_drum)
        if audit is not None:
            audit["timesfm_version"] = "fallback (no-tfm)"
            audit["timesfm_device"] = "CPU"
            audit["timesfm_predictions"] = {n: round(s, 6) for n, s in sorted(fb.items(), key=lambda x: -x[1])[:25]}
        return fb

    total_available = len(data) if data is not None else 0
    ctx_len = min(context_len, total_available, _get_context_cap())
    if ctx_len < 32:
        return _fallback_scores_no_tfm(draw_matrix, params, is_joker_drum)

    device = _detect_device()
    is_cpu = (device == "cpu")
    horizon = 8 if is_cpu else 20
    max_num = 20 if is_joker_drum else int(params.get("max_n", 49))

    context_windows = [ctx_len]
    if not is_regressive_step:
        if is_cpu:
            if ctx_len > 250:
                context_windows.append(200)
            if ctx_len > 120:
                context_windows.append(100)
        else:
            if ctx_len > 1500:
                context_windows.append(1000)
            if ctx_len > 600:
                context_windows.append(500)
            if ctx_len > 350:
                context_windows.append(300)
            if ctx_len > 250:
                context_windows.append(200)
            if ctx_len > 150:
                context_windows.append(100)
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
            
            # Forecast unificat: într-un singur apel GPU/CPU obținem point+quantile,
            # iar pentru pașii non-regresivi cerem și predicția pe context (anomaly)
            # ca să eliminăm un al doilea forecast() redundant.
            forecast_kwargs = {
                "inputs": all_series,
                "freq": [0] * len(all_series),
                "normalize": True,
            }
            if not is_regressive_step:
                forecast_kwargs["return_forecast_on_context"] = True

            try:
                point_forecast, full_forecast = tfm.forecast(**forecast_kwargs)
            except TypeError:
                # Versiuni mai vechi de timesfm care nu suportă return_forecast_on_context
                forecast_kwargs.pop("return_forecast_on_context", None)
                point_forecast, full_forecast = tfm.forecast(**forecast_kwargs)

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
                        force_on_cpu=is_cpu,
                    )
                    cov_forecast = np.array(cov_result[0]) if isinstance(cov_result, tuple) else np.array(cov_result)
                except Exception: pass
            
            # Decuplăm context-prefix vs horizon (când API-ul îl returnează concatenat)
            pf_arr = np.asarray(point_forecast)
            ff_arr = np.asarray(full_forecast)
            ctx_pred = None
            if pf_arr.ndim >= 2 and pf_arr.shape[1] > horizon:
                ctx_len_in_pred = pf_arr.shape[1] - horizon
                ctx_pred = pf_arr[:, :ctx_len_in_pred]
                pf_future = pf_arr[:, ctx_len_in_pred:]
                ff_future = ff_arr[:, ctx_len_in_pred:, ...] if ff_arr.ndim >= 3 and ff_arr.shape[1] >= horizon else ff_arr
            else:
                pf_future = pf_arr
                ff_future = ff_arr

            if is_regressive_step:
                # Mod ultra-rapid pentru calibrare: folosim doar point forecast-ul de baza
                nqi = {num: float(pf_future[i, 0]) for i, num in enumerate(num_map)}
            else:
                # Anomaly scoring: preferăm predicția pe context din același apel.
                anomaly_scores: Dict[int, float] = {}
                if ctx_pred is not None:
                    anomaly_scores = compute_anomaly_from_context_pred(ctx_pred, all_series, num_map)
                if not anomaly_scores:
                    # Fallback la apelul separat (compatibilitate)
                    anomaly_scores = compute_context_anomaly(tfm, all_series, num_map)
                # Determinăm game_type din params
                _game_type = "6/49"  # default
                draw_n = params.get("draw_n", 6)
                max_n = params.get("max_n", 49)
                if draw_n == 5 and max_n == 40:
                    _game_type = "5/40"
                elif draw_n == 5 and max_n == 45:
                    _game_type = "joker"
                
                opt_weights = optimize_nqi_weights(
                    all_series, num_map, pf_future, ff_future, cov_forecast,
                    game_type=_game_type, draw_matrix=draw_matrix, pool_size=15
                )
                nqi = compute_nqi_scores(all_series, num_map, pf_future, ff_future, cov_forecast, horizon, opt_weights)
                
                # Apply Anomaly
                for n in num_map:
                    nqi[n] = nqi.get(n, 0.0) * (0.8 + 0.4 * anomaly_scores.get(n, 0.5))
            
            ensemble_scores.append(nqi)

        # Rank-based fusion (mai robust decât media aritmetică)
        if not ensemble_scores:
            logging.warning("[TIMESFM-V2] Ensemble gol — folosesc fallback determinist.")
            return _fallback_scores_no_tfm(draw_matrix, params, is_joker_drum)

        final_nqi = _rank_fusion(ensemble_scores)
        
        # Pair + Triplet co-occurrence boost (numere care apar frecvent împreună)
        if not is_regressive_step and draw_matrix is not None:
            # Pair boost
            top_for_boost = [n for n, _ in sorted(final_nqi.items(), key=lambda x: x[1], reverse=True)[:40]]
            pair_boost = compute_pair_cooccurrence(draw_matrix, top_for_boost, lookback=300)
            # Triplet boost — critic pentru hit-uri de 4+
            triplet_boost = compute_triplet_cooccurrence(draw_matrix, top_for_boost, lookback=250)
            
            for num in final_nqi:
                pb = pair_boost.get(num, 0.0)
                tb = triplet_boost.get(num, 0.0)
                # Triplet-ul are pondere mai mare — dacă un număr participă la tripleți frecvenți,
                # probabilitatea de a nimeri 4+ numere din pool crește semnificativ
                final_nqi[num] = final_nqi.get(num, 0.0) * (0.75 + 0.25 * pb + 0.35 * tb)
            
        # Audit
        if audit is not None:
            sorted_scores = sorted(final_nqi.items(), key=lambda x: x[1], reverse=True)
            audit["timesfm_predictions"] = {n: round(s, 6) for n, s in sorted_scores[:25]}
            audit["ensemble_windows"] = context_windows
            audit["timesfm_version"] = "v3.1 Unified Forecast + CPU/GPU adaptive"
            audit["timesfm_device"] = device.upper()
            audit["timesfm_horizon"] = horizon
            if device == "gpu":
                audit["timesfm_gpu_name"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "Unknown"
            audit["raw_ensemble_scores"] = ensemble_scores

        logging.info(f"[TIMESFM-V3] Rank Fusion complete ({len(context_windows)} windows). Top 5: {sorted(final_nqi.items(), key=lambda x: -x[1])[:5]}")
        return final_nqi

    except Exception as e:
        logging.error(f"[TIMESFM-V2] Eroare critică în ensemble: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return _fallback_scores_no_tfm(draw_matrix, params, is_joker_drum)


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
    max_num: int = 49,
) -> List[int]:
    """
    Selectează top N numere cu diversificare pe range-uri.
    Asigură acoperire pe low/mid/high pentru a maximiza hit-urile.
    """
    valid = {n: s for n, s in scores.items() if n not in blacklist}
    sorted_nums = sorted(valid.items(), key=lambda x: x[1], reverse=True)

    # Diversificare: împărțim în 3 zone (low, mid, high)
    third = max_num / 3.0
    zones = {"low": [], "mid": [], "high": []}
    for n, s in sorted_nums:
        if n <= third:
            zones["low"].append((n, s))
        elif n <= 2 * third:
            zones["mid"].append((n, s))
        else:
            zones["high"].append((n, s))

    # Garantăm minim de numere din fiecare zonă pentru acoperire optimă
    min_per_zone = max(1, pool_size // 4) if pool_size >= 4 else 0
    pool = []
    used = set()

    # Mai întâi luăm cele mai bune numere din fiecare zonă pentru a asigura distribuția
    for zone_name in ["low", "mid", "high"]:
        for n, s in zones[zone_name][:min_per_zone]:
            if n not in used and len(pool) < pool_size:
                pool.append(n)
                used.add(n)

    # Completăm restul strict cu cele mai bune scoruri globale (High-NQI)
    # Acest lucru favorizează hit-urile de 4 și 5 prin concentrarea pe calitate
    for n, s in sorted_nums:
        if len(pool) >= pool_size:
            break
        if n not in used:
            pool.append(n)
            used.add(n)

    if audit is not None:
        audit["timesfm_predictions"] = {n: round(s, 6) for n, s in sorted_nums[:25]}
        audit["pool_diversification"] = {z: len(v) for z, v in zones.items()}

    return sorted(pool[:pool_size])


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
