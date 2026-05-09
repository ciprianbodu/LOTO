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
    # GPU PERF: TensorCore + cudnn autotune. Free 1.5-2× pe Ampere/Ada/Hopper/Blackwell.
    # Setate o singură dată la load. Afectează inferenţa torch (TimesFM ascunde
    # detaliile sub backend="gpu", dar torch.matmul-urile interne primesc TF32).
    # NOTĂ: aceste flag-uri necesită kernel-uri pre-compilate pentru arhitectura GPU
    # (ex: SM_120 pe RTX 5060 Ti). Pe PyTorch fără kernel-urile potrivite ele cad cu
    # "no kernel image is available" — verificați torch.cuda.get_arch_list().
    if torch.cuda.is_available():
        try:
            arch_list = torch.cuda.get_arch_list()
            cap = torch.cuda.get_device_capability(0)
            cap_sm = f"sm_{cap[0]}{cap[1]}"
            if cap_sm in arch_list or any(int(a.replace("sm_", "")) >= int(cap_sm.replace("sm_", "")) for a in arch_list):
                torch.backends.cudnn.benchmark = True
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
                if hasattr(torch, "set_float32_matmul_precision"):
                    torch.set_float32_matmul_precision("high")
                logging.info(
                    "[TIMESFM] GPU perf flags active: cudnn.benchmark + TF32 (device=%s, archs=%s)",
                    cap_sm, arch_list,
                )
            else:
                logging.warning(
                    "[TIMESFM] GPU %s nu e in lista de archs torch %s — flag-urile perf "
                    "raman dezactivate. Update torch ca sa activezi GPU full speed.",
                    cap_sm, arch_list,
                )
        except Exception as _exc:
            logging.debug("[TIMESFM] GPU perf flags init failed: %s", _exc)
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
    except Exception as exc:
        logging.debug("[TIMESFM] cuda probe failed, fallback la CPU: %s", exc)
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
    """Creează seria 0/1 pentru fiecare număr în fereastra [start, end).

    GPU PERF (2026-05-04): vectorizat. Vechi: max_num iterații Python care
    construiau np.array prin list comprehension peste fereastră (O(max_num · N)
    cu overhead Python). Nou: o singură operație broadcast care produce
    matricea binară (max_num × N) și o despicăm pe rânduri. Pentru max_num=45
    și N=2124, e ~30× mai rapid și elimină GIL pressure în hot path.
    """
    if is_joker and joker_vals is not None:
        window = np.asarray(joker_vals[start:end], dtype=np.int64)
    elif draw_matrix is not None:
        # draw_matrix shape: (n_draws, draw_n). Window: (N, draw_n) cu N = end-start.
        window = np.asarray(draw_matrix[start:end], dtype=np.int64)
    else:
        return [], []

    n = window.shape[0]
    if n < 32:
        return [], []

    # Numere candidate ca vector (1..max_num)
    nums = np.arange(1, max_num + 1, dtype=np.int64)  # shape (max_num,)

    if is_joker and joker_vals is not None:
        # window: (N,). Broadcast vs nums (max_num,): (max_num, N)
        binary = (window[None, :] == nums[:, None]).astype(np.float32)
    else:
        # window: (N, draw_n). Pentru fiecare num, max pe axa draw_n dă 1
        # dacă num apare oriunde în extragere. Broadcast: (max_num, N, draw_n)
        # → reduce pe draw_n → (max_num, N).
        binary = (window[None, :, :] == nums[:, None, None]).any(axis=2).astype(np.float32)

    # Despicăm pe rânduri (echivalent cu loopul vechi). num_map e 1..max_num
    # deoarece toate numerele primesc o serie cu len = N (≥ 32 deja verificat).
    all_series = [binary[i] for i in range(max_num)]
    num_map = list(range(1, max_num + 1))
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
    """Media mobilă cu padding la stânga (left-causal, expanding până la window).

    GPU PERF: vectorizat. Vechi: O(N) Python loop pe cumsum. Nou: pur numpy
    cu cumsum + slicing — ~50× mai rapid pe N=2124, fără overhead Python.
    """
    n = len(arr)
    if n == 0:
        return arr
    csum = np.cumsum(arr, dtype=np.float64)
    sums = csum.copy()
    if window < n:
        # pentru i ≥ window, suma = csum[i] - csum[i-window]
        sums[window:] = csum[window:] - csum[:-window]
    counts = np.minimum(np.arange(1, n + 1, dtype=np.float64), float(window))
    return (sums / counts).astype(arr.dtype, copy=False)


def _gap_series(arr: np.ndarray) -> np.ndarray:
    """Construiește seria gap-urilor (câte extrageri de la ultima apariție).

    GPU PERF: vectorizat folosind reset-prefix-sum. Vechi: O(N) Python loop.
    Nou: pur numpy — la fiecare poziție unde arr[i] > 0.5, gap-ul revine la 0.
    Folosim trick-ul: gap[i] = i - max{j ≤ i : arr[j] > 0.5} (cu max=-1 dacă niciunul).
    """
    n = len(arr)
    if n == 0:
        return arr.astype(np.float32, copy=False)
    is_hit = arr > 0.5
    # idx_arr[i] = i dacă is_hit[i] altfel -1, apoi expanding max
    idx_arr = np.where(is_hit, np.arange(n), -1)
    last_hit = np.maximum.accumulate(idx_arr)
    pos = np.arange(n)
    # gap = (poziție curentă) - (poziția ultimei apariții, sau -1 dacă niciuna)
    # Pentru poziții fără hit anterior, gap = i + 1 (i.e., pos - (-1))
    gaps = (pos - last_hit).astype(np.float32)
    # Pe poziție hit, gap=0; numpy formula deja dă 0 când last_hit==pos.
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
    """Rank-based ensemble fusion (Weighted Borda Count) — mai robust decât media aritmetică.

    H6 (2026-05-07): default-ul vechi `[1.0 / (1.0 + 0.25*i)]` favoriza ferestrele
    LUNGI (poziția 0 = ctx_len, weight 1.0) în defavoarea celor scurte (poziția 6
    = window=50, weight 0.4). Pentru predicția extragerii URMĂTOARE, momentum-ul
    recent (50-300 extrageri) e mai informativ decât trendul de 2000+ extrageri.
    Distribuim ponderi cu vârful pe poziția mid-recent (300-100 extrageri):
    coadă lungă suficientă pentru stabilitate, vârf pe semnal proaspăt.

    Ordinea context_windows e: [ctx_len, 1000, 500, 300, 200, 100, 50]
    Ponderi noi:                [0.55,    0.75, 0.95, 1.10, 1.10, 0.95, 0.75]
    """
    if not ensemble_scores:
        return {}
    all_nums = set().union(*(s.keys() for s in ensemble_scores))
    rank_scores: Dict[int, float] = {n: 0.0 for n in all_nums}
    if window_weights is None:
        # Bell curve cu vârful pe pozițiile 3-4 (ferestre 300-200, mid-recent)
        n = len(ensemble_scores)
        if n <= 1:
            window_weights = [1.0]
        else:
            # Distribuție bell-shape: peak la mid, lower la extreme
            peak = (n - 1) / 2.0
            sigma = max(n / 3.0, 1.0)
            window_weights = [
                0.55 + 0.55 * np.exp(-((i - peak) ** 2) / (2.0 * sigma ** 2))
                for i in range(n)
            ]
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
    """Triplet co-occurrence v2 — scalează la top-40 fără explozie combinatorică.

    Strategie: în loc de toate combinațiile C(N,3), iterăm direct prin extrageri
    și extragem tripleții observați (C(draw_n, 3)). Acoperă spațiul real de
    tripleți frecvenți cu cost O(R · C(draw_n,3)) ≈ O(R · 20) pentru 6/49.
    """
    if draw_matrix is None or draw_matrix.size == 0:
        return {n: 0.0 for n in num_map}

    n_draws = min(lookback, draw_matrix.shape[0])
    recent = draw_matrix[-n_draws:]
    num_set = set(num_map)

    from itertools import combinations
    from collections import Counter

    triplet_counts: Counter = Counter()
    for row in recent:
        # Filtrăm doar numerele relevante (din num_map) ca să nu numărăm tripleți irelevanți
        nums_in_row = sorted(set(int(v) for v in row if int(v) > 0) & num_set)
        if len(nums_in_row) < 3:
            continue
        for trip in combinations(nums_in_row, 3):
            triplet_counts[trip] += 1

    triplet_score: Dict[int, float] = {n: 0.0 for n in num_map}
    for trip, count in triplet_counts.items():
        if count >= 2:  # Tripletul trebuie să fi apărut cel puțin de 2 ori (semnal stabil)
            weight = count / max(n_draws, 1)
            for a in trip:
                triplet_score[a] += weight

    # Normalize to [0, 1]
    max_ts = max(triplet_score.values()) if triplet_score else 1.0
    if max_ts > 0:
        for n in triplet_score:
            triplet_score[n] /= max_ts

    return triplet_score


def compute_quadruplet_cooccurrence(draw_matrix: np.ndarray | None, num_map: List[int],
                                    lookback: int = 350) -> Dict[int, float]:
    """Quadruplet (4-gram) co-occurrence — semnal direct pentru hit-uri 4+.

    Rationale: pentru un hit de 4 numere în pool, trebuie ca un cvadruplu să cadă
    împreună. Numerele care fac parte din cvadrupluri istoric recurente au
    probabilitate marginală mai mare să fie părți ale unui următor hit-4+.

    Cost: O(R · C(draw_n, 4)) ≈ O(R · 15) pentru 6/49 — perfect tractabil.
    Folosim un lookback mai lung (default 350) pentru că cvadrupluri sunt
    evenimente mai rare decât tripleți și avem nevoie de mai multe observații.
    """
    if draw_matrix is None or draw_matrix.size == 0:
        return {n: 0.0 for n in num_map}

    draw_n = draw_matrix.shape[1] if draw_matrix.ndim >= 2 else 6
    if draw_n < 4:
        return {n: 0.0 for n in num_map}

    n_draws = min(lookback, draw_matrix.shape[0])
    recent = draw_matrix[-n_draws:]
    num_set = set(num_map)

    from itertools import combinations
    from collections import Counter

    quad_counts: Counter = Counter()
    for row in recent:
        nums_in_row = sorted(set(int(v) for v in row if int(v) > 0) & num_set)
        if len(nums_in_row) < 4:
            continue
        for q in combinations(nums_in_row, 4):
            quad_counts[q] += 1

    quad_score: Dict[int, float] = {n: 0.0 for n in num_map}
    # Cvadrupluri fiind rare, considerăm count >= 1 dar ponderăm prin frecvența cvadruplului
    for q, count in quad_counts.items():
        # Ponderăm pătratic — cvadruplurile cu count 2+ sunt mult mai informative
        weight = (count ** 1.5) / max(n_draws, 1)
        for a in q:
            quad_score[a] += weight

    max_qs = max(quad_score.values()) if quad_score else 0.0
    if max_qs > 0:
        for n in quad_score:
            quad_score[n] /= max_qs

    return quad_score


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
            # NaN guard: dacă oricare cuantilă e invalidă, păstrăm fallback 0.5
            if not (np.isnan(q10) or np.isnan(q50) or np.isnan(q90)):
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

        # E. Adaptive Bias (Gap Trigger smooth)
        series = all_series[i]
        total_draws_ctx = len(series)
        total_appearances = float(np.sum(series))
        avg_gap = total_draws_ctx / max(total_appearances, 1.0)
        current_gap = 0.0
        for val in reversed(series):
            if val > 0.5:
                break
            current_gap += 1.0

        # Sigmoid-bazat (continuu) — evită discontinuitățile de la pragurile fixe.
        # Curba: f(x) = 0.85 + 0.65 / (1 + exp(-3*(x - 1)))
        #   gap_ratio = 0   → ~0.85   (numar recent — răceală ușoară)
        #   gap_ratio = 1   → ~1.18   (în jurul mediei — boost moderat)
        #   gap_ratio = 1.5 → ~1.39   (întârziat — boost puternic)
        #   gap_ratio →  ∞  → 1.50    (saturare la maxim)
        adaptive = 1.0
        if total_appearances > 3 and avg_gap > 0:
            gap_ratio = current_gap / avg_gap
            # Overflow guard pentru sigmoid: arg-ul e clampat la [-50, 50]
            # (np.exp(50) e ~5e21, sub limita float64; -50 dă termen ~0)
            arg = float(np.clip(-3.0 * (gap_ratio - 1.0), -50.0, 50.0))
            adaptive = 0.85 + 0.65 / (1.0 + np.exp(arg))
            adaptive = float(np.clip(adaptive, 0.80, 1.50))
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
    """Returnează candidați de ponderi optimizați per tip de joc.

    Set extins (v4): 5 candidați de bază + 4 specifici jocului + 30 mostre
    cvasi-aleatoare (Latin Hypercube-like) centrate pe profilul jocului. Acoperirea
    mai largă a spațiului de ponderi permite optimizatorului să descopere
    combinații ne-evidente care maximizează hit-urile în pool.
    """
    # Candidați comuni (calibrare istorică)
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
            # Pool-coverage focused: împinge mai multă pondere pe semnalele care detectează numere "active"
            {"p_val": 0.18, "h_score": 0.12, "stability": 0.08, "cov_boost": 0.12, "adaptive": 0.25, "momentum": 0.25},
            {"p_val": 0.22, "h_score": 0.18, "stability": 0.08, "cov_boost": 0.08, "adaptive": 0.22, "momentum": 0.22},
        ],
        "5/40": [
            # 5/40: Pool mai mic (40), cicluri scurte → Adaptive și Point mai mari
            {"p_val": 0.30, "h_score": 0.10, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.25, "momentum": 0.15},
            {"p_val": 0.25, "h_score": 0.10, "stability": 0.15, "cov_boost": 0.10, "adaptive": 0.30, "momentum": 0.10},
            {"p_val": 0.28, "h_score": 0.12, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.22, "momentum": 0.18},
            {"p_val": 0.22, "h_score": 0.15, "stability": 0.10, "cov_boost": 0.13, "adaptive": 0.25, "momentum": 0.15},
        ],
        "joker": [
            # Joker 5/45: 5 numere din 45 → Point forecast mai puternic, momentum pe ferestre scurte
            {"p_val": 0.30, "h_score": 0.15, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.15, "momentum": 0.20},
            {"p_val": 0.25, "h_score": 0.15, "stability": 0.15, "cov_boost": 0.10, "adaptive": 0.20, "momentum": 0.15},
            {"p_val": 0.28, "h_score": 0.13, "stability": 0.12, "cov_boost": 0.10, "adaptive": 0.18, "momentum": 0.19},
            {"p_val": 0.24, "h_score": 0.16, "stability": 0.10, "cov_boost": 0.12, "adaptive": 0.20, "momentum": 0.18},
        ],
    }

    base = common + game_specific.get(game_type, [])

    # Centroizi specifici jocului pentru sampling cvasi-aleator (LHS-like).
    # Acești centroizi reflectă bune-practici descoperite empiric: jocurile mari (6/49)
    # beneficiază de momentum/trend, cele mici (5/40) de adaptive/point.
    centroids = {
        "6/49":  {"p_val": 0.20, "h_score": 0.18, "stability": 0.09, "cov_boost": 0.08, "adaptive": 0.22, "momentum": 0.23},
        "5/40":  {"p_val": 0.27, "h_score": 0.12, "stability": 0.10, "cov_boost": 0.10, "adaptive": 0.26, "momentum": 0.15},
        "joker": {"p_val": 0.27, "h_score": 0.15, "stability": 0.12, "cov_boost": 0.10, "adaptive": 0.18, "momentum": 0.18},
    }
    centroid = centroids.get(game_type, centroids["6/49"])

    # Generăm 30 de mostre stratificate în jurul centroidului (Random + Renormalize).
    # Folosim seed determinist pentru reproductibilitate între rulări.
    rng = np.random.default_rng(seed=hash(game_type) & 0x7FFFFFFF)
    keys = ["p_val", "h_score", "stability", "cov_boost", "adaptive", "momentum"]
    centroid_arr = np.array([centroid[k] for k in keys], dtype=np.float64)

    sampled: List[Dict[str, float]] = []
    for _ in range(30):
        # Perturbație multiplicativă cu σ=0.35, apoi renormalizare la 1.0
        noise = rng.normal(loc=1.0, scale=0.35, size=len(keys))
        noise = np.clip(noise, 0.3, 1.8)  # Evităm explozia pe componente
        cand = centroid_arr * noise
        cand = np.maximum(cand, 0.02)  # Floor pentru a nu zero-out o componentă
        cand /= cand.sum()
        sampled.append({k: float(v) for k, v in zip(keys, cand)})

    return base + sampled


def _get_game_hit_profile(game_type: str) -> Dict[str, int]:
    """Praguri de evaluare hit-uri mari, specifice jocului."""
    if game_type == "6/49":
        return {"target": 4, "high": 5, "max": 6}
    # 5/40 și Joker: obiectivul practic e 3+, cu focus pe 4/5
    return {"target": 3, "high": 4, "max": 5}


def _get_cooccurrence_blend(game_type: str) -> Dict[str, float]:
    """Coeficienți per joc pentru mixajul pair+triplet+quad boost.

    Reguli:
      • base: cât din scorul NQI rămâne fără boost (fundament).
      • pair_w/triplet_w/quad_w: ponderea aditivă a fiecărui semnal.
      • lookback-uri: cât de mult istoric folosim pentru fiecare ordin.

    Pentru 6/49 (pool mare, draw_n=6): mai multă pondere pe quadruplet (hit-4 critic).
    Pentru 5/40 (draw_n=5): pair/triplet domină — quadruplet rar.
    Pentru Joker (5+1, draw_n=5): similar 5/40 dar cu lookback mai scurt (cicluri scurte).
    """
    # Notă: pentru obiectivul de pool-coverage (acoperire), păstrăm magnitudini apropiate
    # de configurația originală (base≈0.75, pair≈0.25, triplet≈0.35) și adăugăm quad ca
    # un mic bonus aditiv. Calibrarea agresivă riscă să distorsioneze ranking-ul NQI.
    profiles = {
        "6/49": {
            "base": 0.72, "pair_w": 0.24, "triplet_w": 0.34, "quad_w": 0.10,
            "pair_lookback": 300, "triplet_lookback": 250, "quad_lookback": 350,
        },
        "5/40": {
            "base": 0.74, "pair_w": 0.24, "triplet_w": 0.32, "quad_w": 0.07,
            "pair_lookback": 250, "triplet_lookback": 200, "quad_lookback": 300,
        },
        "joker": {
            "base": 0.76, "pair_w": 0.22, "triplet_w": 0.28, "quad_w": 0.06,
            "pair_lookback": 200, "triplet_lookback": 180, "quad_lookback": 250,
        },
    }
    return profiles.get(game_type, profiles["6/49"])


def _get_anomaly_blend(game_type: str) -> Tuple[float, float]:
    """Coeficienți (base, gain) pentru `anomaly` blend: nqi *= base + gain*anomaly.

    Pe 6/49 (pool mare, mai multe iluzii statistice) păstrăm anomaly-ul mai
    influent. Pe 5/40 și Joker (pool mai mic, semnale mai zgomotoase pe cicluri
    scurte) reducem amplificarea ca să nu over-suprimăm numere viabile.
    """
    profiles = {
        "6/49":  (0.78, 0.42),  # ușor mai slab decât 0.8/0.4 inițial — favorizează acoperirea
        "5/40":  (0.85, 0.30),
        "joker": (0.85, 0.28),
    }
    return profiles.get(game_type, profiles["6/49"])


def _evaluate_weight_candidate(
    w: Dict[str, float],
    all_series: List[np.ndarray],
    num_map: List[int],
    point_forecast: np.ndarray,
    full_forecast: np.ndarray,
    cov_forecast: np.ndarray | None,
    draw_matrix: np.ndarray | None,
    pool_size: int,
    game_type: str,
    n_test_draws: int,
) -> float:
    """Evaluează un set de ponderi pe ultimele n_test_draws extrageri.

    Obiectiv hibrid (poll-coverage prioritizat):
      • pool union hits (acoperire) — cât de multe numere câștigătoare apar în pool
      • hit‑uri 4+ / 5+ / 6+ ponderate progresiv
      • penalizare pe variața mare (preferăm consistență)
    """
    current_nqi = compute_nqi_scores(
        all_series, num_map, point_forecast, full_forecast, cov_forecast, 1, w
    )
    if n_test_draws <= 0 or draw_matrix is None:
        # Fallback: corelație cu frecvența recentă
        perf = 0.0
        for num, score in current_nqi.items():
            try:
                idx = num_map.index(num)
            except ValueError:
                continue
            recent_hits = float(np.sum(all_series[idx][-5:]))
            perf += score * (recent_hits * 1.5)
        return perf

    sorted_by_nqi = sorted(current_nqi.items(), key=lambda x: x[1], reverse=True)
    pool_nums = set(n for n, _ in sorted_by_nqi[:pool_size])

    hit_profile = _get_game_hit_profile(game_type)
    total_rows = draw_matrix.shape[0]
    overlaps: List[int] = []
    hits_target_plus = 0
    hits_high_plus = 0
    hits_max = 0

    for i in range(max(0, total_rows - n_test_draws), total_rows):
        draw_set = set(int(v) for v in draw_matrix[i] if v > 0)
        overlap = len(pool_nums & draw_set)
        overlaps.append(overlap)
        if overlap >= hit_profile["target"]:
            hits_target_plus += 1
        if overlap >= hit_profile["high"]:
            hits_high_plus += 1
        if overlap >= hit_profile["max"]:
            hits_max += 1

    if not overlaps:
        return 0.0

    total_hits = float(sum(overlaps))
    avg_hits = total_hits / max(len(overlaps), 1)
    var_hits = float(np.var(overlaps))
    consistency = 1.0 / (1.0 + var_hits)  # ∈ (0,1]

    # Obiectiv pool-coverage prioritizat: avg_hits dominant, urmat de hit-uri ridicate.
    perf = (
        avg_hits * 25.0  # Acoperire medie pool — semnal principal
        + hits_target_plus * 10.0
        + hits_high_plus * 35.0
        + hits_max * 120.0
        + total_hits * 1.0
        + consistency * 5.0
    )
    return perf


def _local_refine_weights(
    base_w: Dict[str, float],
    base_score: float,
    eval_fn,
    n_iter: int = 8,
    step: float = 0.07,
) -> Tuple[Dict[str, float], float]:
    """Coordonate descrescătoare cu rebalansare — small local-search în jurul celui mai bun candidat."""
    keys = list(base_w.keys())
    rng = np.random.default_rng(seed=12345)
    best_w = dict(base_w)
    best_score = base_score
    for _ in range(n_iter):
        # Alegem 2 chei și transferăm masă între ele
        i, j = rng.choice(len(keys), size=2, replace=False)
        ki, kj = keys[int(i)], keys[int(j)]
        delta = float(rng.uniform(-step, step))
        new_w = dict(best_w)
        new_w[ki] = max(0.02, new_w[ki] + delta)
        new_w[kj] = max(0.02, new_w[kj] - delta)
        s_total = sum(new_w.values())
        if s_total <= 0:
            continue
        new_w = {k: v / s_total for k, v in new_w.items()}
        score = eval_fn(new_w)
        if score > best_score:
            best_score = score
            best_w = new_w
    return best_w, best_score


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
    Walk-Forward Weight Optimization v3 — căutare extinsă + local refinement.

    1) Evaluează 40+ candidați (5 comune + 4 game-specific + 30 mostre stratificate).
    2) Folosește obiectiv hibrid centrat pe pool-coverage (avg union hits).
    3) Aplică local search (coordinate-swap) în jurul best_w pentru fine‑tuning.
    4) Crește fereastra de validare la 12 extrageri (mai stabil statistic).
    """
    logging.info(f"[TIMESFM-V3] Extended Weight Optimization (game={game_type})...")

    candidates = _get_game_specific_weights(game_type)

    # Mărim fereastra de validare pentru stabilitate statistică (era 10).
    ctx_len = len(all_series[0]) if all_series else 0
    n_test_draws = min(15, max(0, ctx_len - 10))

    def _eval(w: Dict[str, float]) -> float:
        return _evaluate_weight_candidate(
            w, all_series, num_map, point_forecast, full_forecast, cov_forecast,
            draw_matrix, pool_size, game_type, n_test_draws,
        )

    best_w = candidates[0]
    best_score = -1.0
    for w in candidates:
        score = _eval(w)
        if score > best_score:
            best_score = score
            best_w = w

    # Local refinement în jurul best_w
    if draw_matrix is not None and n_test_draws > 0:
        best_w, best_score = _local_refine_weights(best_w, best_score, _eval, n_iter=10, step=0.06)

    logging.info(f"[TIMESFM-V3] Ponderi optime selectate: {best_w} (score={best_score:.2f}, candidates={len(candidates)})")
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


def _get_regime_reset_weights() -> Dict[str, float]:
    """Ponderi NQI pentru modul `regime_reset`.

    Filozofie revizuită (după backtest empiric care a arătat că reset extrem
    performa MAI PROST decât normal): tilt MODERAT spre semnale reactive, nu
    extrem. Păstrăm încredere parțială în predicția modelului (`p_val`,
    `h_score`) dar dăm mai multă greutate semnalelor de exploatare (`adaptive`
    = gap trigger pentru numere "due", `momentum` = activitate recentă).

    Comparație cu profilul "balanced" normal:
        balanced:  p_val=0.25, h_score=0.15, stability=0.10, cov=0.10, adaptive=0.20, momentum=0.20
        reset:     p_val=0.18, h_score=0.13, stability=0.10, cov=0.07, adaptive=0.27, momentum=0.25
    """
    return {
        "p_val":     0.18,
        "h_score":   0.13,
        "stability": 0.10,
        "cov_boost": 0.07,
        "adaptive":  0.27,
        "momentum":  0.25,
    }


def get_timesfm_scores_v2(
    data,
    draw_matrix: np.ndarray | None,
    params: dict,
    is_joker_drum: bool = False,
    context_len: int = 4096,
    audit: dict | None = None,
    is_regressive_step: bool = False,
    regime_mode: str = "normal",
) -> Dict[int, float]:
    """
    Motor principal TimesFM v2 — folosește TOATE capabilitățile API-ului.

    Optimizări de comunicare cu TimesFM:
      • Un singur `forecast()` per fereastră (cu return_forecast_on_context=True)
        pentru a obține point/full/anomaly într-un singur apel GPU/CPU.
      • Horizon adaptiv (mai mic pe CPU, full pe GPU).
      • Ferestre de ensemble prunate pe CPU pentru viteză.
      • Fallback determinist când librăria lipsește (nu mai întoarce {}).

    regime_mode: "normal" (default) sau "reset". În "reset" folosim ponderi NQI
        rebalansate care scad încrederea în predicția directă a modelului și
        cresc semnalele reactive (adaptive bias + momentum). Folosit după
        catastrofe consecutive sau underperformance susținută.
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
                    # Pre-materializăm conversia np→list o singură dată (era refăcută per cov call)
                    inputs_list = [s.tolist() for s in all_series]
                    cov_result = tfm.forecast_with_covariates(
                        inputs=inputs_list,
                        dynamic_numerical_covariates=dyn_num,
                        static_numerical_covariates=static_num,
                        freq=[0] * len(all_series),
                        xreg_mode="xreg + timesfm",
                        normalize_xreg_target_per_input=True,
                        force_on_cpu=is_cpu,
                    )
                    cov_forecast = np.array(cov_result[0]) if isinstance(cov_result, tuple) else np.array(cov_result)
                except Exception as exc:
                    logging.debug("[TIMESFM] forecast_with_covariates a eșuat: %s", exc)
            
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
                
                if regime_mode == "reset":
                    # Skipăm optimizarea costisitoare — folosim ponderi reactive fixe.
                    opt_weights = _get_regime_reset_weights()
                    if audit is not None:
                        audit["regime_mode"] = "reset"
                        audit["nqi_weights_override"] = opt_weights
                else:
                    opt_weights = optimize_nqi_weights(
                        all_series, num_map, pf_future, ff_future, cov_forecast,
                        game_type=_game_type, draw_matrix=draw_matrix, pool_size=15
                    )
                nqi = compute_nqi_scores(all_series, num_map, pf_future, ff_future, cov_forecast, horizon, opt_weights)

                # Apply Anomaly cu coeficienți per joc (evită over-suppression pe pool-uri mici)
                anom_base, anom_gain = _get_anomaly_blend(_game_type)
                for n in num_map:
                    nqi[n] = nqi.get(n, 0.0) * (anom_base + anom_gain * anomaly_scores.get(n, 0.5))
            
            ensemble_scores.append(nqi)

        # Rank-based fusion (mai robust decât media aritmetică)
        if not ensemble_scores:
            logging.warning("[TIMESFM-V2] Ensemble gol — folosesc fallback determinist.")
            return _fallback_scores_no_tfm(draw_matrix, params, is_joker_drum)

        final_nqi = _rank_fusion(ensemble_scores)
        
        # Pair + Triplet + Quadruplet co-occurrence boost (numere care apar frecvent împreună)
        if not is_regressive_step and draw_matrix is not None:
            # Detectăm jocul pentru blend coefficients
            _gt = "6/49"
            _draw_n = params.get("draw_n", 6)
            _max_n = params.get("max_n", 49)
            if _draw_n == 5 and _max_n == 40:
                _gt = "5/40"
            elif _draw_n == 5 and _max_n == 45:
                _gt = "joker"

            blend = _get_cooccurrence_blend(_gt)
            top_for_boost = [n for n, _ in sorted(final_nqi.items(), key=lambda x: x[1], reverse=True)[:40]]

            pair_boost = compute_pair_cooccurrence(draw_matrix, top_for_boost, lookback=blend["pair_lookback"])
            # Triplet boost — critic pentru hit-uri de 4+
            triplet_boost = compute_triplet_cooccurrence(draw_matrix, top_for_boost, lookback=blend["triplet_lookback"])
            # Quadruplet boost — semnal direct pentru hit-uri 4+
            quad_boost = compute_quadruplet_cooccurrence(draw_matrix, top_for_boost, lookback=blend["quad_lookback"])

            base = blend["base"]
            wp = blend["pair_w"]
            wt = blend["triplet_w"]
            wq = blend["quad_w"]
            for num in final_nqi:
                pb = pair_boost.get(num, 0.0)
                tb = triplet_boost.get(num, 0.0)
                qb = quad_boost.get(num, 0.0)
                # Mixaj per-joc al boost-urilor; ponderile sunt sub-aditive (suma blend·× ≈ 1)
                final_nqi[num] = final_nqi.get(num, 0.0) * (base + wp * pb + wt * tb + wq * qb)

            if audit is not None:
                audit["cooccurrence_blend"] = {
                    "game_type": _gt,
                    "base": base,
                    "pair_w": wp,
                    "triplet_w": wt,
                    "quad_w": wq,
                }
            
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
def _empirical_decade_distribution(draw_matrix: np.ndarray | None, max_num: int,
                                   lookback: int = 400) -> List[float]:
    """Distribuția empirică a numerelor pe decade (0-9, 10-19, ...) din ultimele extrageri.

    Returnează probabilitatea ca o poziție random să cadă în fiecare decadă.
    """
    n_decades = (max_num + 9) // 10
    if draw_matrix is None or draw_matrix.size == 0:
        # Uniform fallback
        return [1.0 / n_decades] * n_decades

    n_draws = min(lookback, draw_matrix.shape[0])
    recent = draw_matrix[-n_draws:]
    counts = np.zeros(n_decades, dtype=np.float64)
    total = 0
    for row in recent:
        for v in row:
            iv = int(v)
            if 1 <= iv <= max_num:
                idx = (iv - 1) // 10
                if idx < n_decades:
                    counts[idx] += 1
                    total += 1
    if total == 0:
        return [1.0 / n_decades] * n_decades
    return (counts / total).tolist()


def _empirical_parity_ratio(draw_matrix: np.ndarray | None, lookback: int = 400) -> float:
    """Procentul mediu empiric de numere pare per extragere."""
    if draw_matrix is None or draw_matrix.size == 0:
        return 0.5
    n_draws = min(lookback, draw_matrix.shape[0])
    recent = draw_matrix[-n_draws:]
    even = 0
    total = 0
    for row in recent:
        for v in row:
            iv = int(v)
            if iv > 0:
                if iv % 2 == 0:
                    even += 1
                total += 1
    if total == 0:
        return 0.5
    return even / total


def select_pool_from_scores(
    scores: Dict[int, float],
    pool_size: int,
    blacklist: Set[int],
    audit: dict | None = None,
    max_num: int = 49,
    draw_matrix: np.ndarray | None = None,
) -> List[int]:
    """
    Selectează top N numere cu diversificare empirică pe decade & paritate.

    v3 schimbări față de v2:
      • Înlocuim zoning-ul fix low/mid/high (1/3) cu distribuție empirică pe decade
        — calculată din ultimele 400 extrageri. Aceasta reflectă mult mai bine
        unde cad efectiv numerele câștigătoare.
      • Adăugăm un nudge ușor spre raportul empiric pare/impare la step-ul de
        completare, fără a sacrifica top-NQI.
      • Păstrăm prioritatea scorurilor (top-NQI) — nu forțăm diversificarea
        agresivă; o aplicăm doar la "rest fill" pentru a nu strica ranking-ul.
    """
    valid = {n: s for n, s in scores.items() if n not in blacklist}
    sorted_nums = sorted(valid.items(), key=lambda x: x[1], reverse=True)

    # --- Distribuții empirice (target distribution) ---
    decade_probs = _empirical_decade_distribution(draw_matrix, max_num, lookback=400)
    target_even_ratio = _empirical_parity_ratio(draw_matrix, lookback=400)

    n_decades = len(decade_probs)
    # Cuote țintă per decadă (rotunjite, dar cu floor 1 pentru decadele cu probabilitate non-trivială)
    raw_quotas = [p * pool_size for p in decade_probs]
    target_quotas = [int(round(q)) for q in raw_quotas]
    # Asigurăm cel puțin 1 număr în decadele cu peste 8% probabilitate
    for i, p in enumerate(decade_probs):
        if p >= 0.08 and target_quotas[i] < 1:
            target_quotas[i] = 1
    # Limităm sumă la pool_size (ajustăm cea mai grea decadă în plus/minus)
    diff = sum(target_quotas) - pool_size
    while diff != 0:
        if diff > 0:
            # Tăiem din decada cu cel mai mare quota
            idx = int(np.argmax(target_quotas))
            target_quotas[idx] = max(0, target_quotas[idx] - 1)
            diff -= 1
        else:
            # Adăugăm la decada cu cea mai mare probabilitate empirică nesatisfăcută
            scaled = [p * pool_size - target_quotas[i] for i, p in enumerate(decade_probs)]
            idx = int(np.argmax(scaled))
            target_quotas[idx] += 1
            diff += 1

    # --- Pool selection: top-NQI cu respect pentru cuotele empirice ---
    pool: List[int] = []
    used: Set[int] = set()
    decade_filled = [0] * n_decades

    # H3 v2 (2026-05-04): skip decade quotas DOAR pe pool 10-11. Confirmarea
    # n=64 a arătat regresie -17% pe pool 9 cu skip — pool 9 prea mic ca să
    # tolereze pierderea diversificării empirice (top-9 NQI tinde să se
    # concentreze într-1-2 decade când quotele sunt off). Pool 12+ păstrează
    # quote-le legacy (au destule slot-uri pentru ambele).
    skip_decade_quotas = pool_size in (10, 11)

    # Pas 1: Încercăm să umplem cuotele decadelor cu cele mai bune scoruri globale.
    # Iterăm sortat după scor; un număr e acceptat dacă decada lui mai are loc.
    if not skip_decade_quotas:
        for n, _s in sorted_nums:
            if len(pool) >= pool_size:
                break
            if n in used:
                continue
            decade_idx = (int(n) - 1) // 10
            if decade_idx >= n_decades:
                continue
            if decade_filled[decade_idx] < target_quotas[decade_idx]:
                pool.append(n)
                used.add(n)
                decade_filled[decade_idx] += 1

    # Pas 2: Dacă rămân locuri (decadele saturate / cuote relaxate), umplem cu top-NQI rămași.
    # În acest pas aplicăm și un nudge ușor spre echilibrul empiric pare/impare.
    if len(pool) < pool_size:
        remaining = [(n, s) for n, s in sorted_nums if n not in used]
        # Calcul curent paritate
        even_count = sum(1 for n in pool if n % 2 == 0)
        for n, _s in remaining:
            if len(pool) >= pool_size:
                break
            current_total = max(len(pool), 1)
            current_even = even_count / current_total
            wanted_even = target_even_ratio
            # Preferăm numerele care apropie raportul de target (toleranță ±0.10)
            is_even = (n % 2 == 0)
            if abs(current_even - wanted_even) > 0.10:
                # Avem nevoie să corectăm paritatea
                need_more_even = current_even < wanted_even
                if need_more_even and not is_even:
                    continue  # skip impari când avem nevoie de pari
                if (not need_more_even) and is_even:
                    continue  # skip pari când avem nevoie de impari
            pool.append(n)
            used.add(n)
            if is_even:
                even_count += 1

    # Pas 3: Garanție absolută — dacă tot nu e plin (toleranțele au refuzat prea multe), umplem strict pe NQI
    if len(pool) < pool_size:
        for n, _s in sorted_nums:
            if len(pool) >= pool_size:
                break
            if n not in used:
                pool.append(n)
                used.add(n)

    if audit is not None:
        audit["timesfm_predictions"] = {n: round(s, 6) for n, s in sorted_nums[:25]}
        audit["pool_decade_quotas"] = target_quotas
        audit["pool_decade_filled"] = decade_filled
        audit["pool_target_even_ratio"] = round(target_even_ratio, 3)
        audit["pool_actual_even_ratio"] = round(
            sum(1 for n in pool if n % 2 == 0) / max(len(pool), 1), 3
        )

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
