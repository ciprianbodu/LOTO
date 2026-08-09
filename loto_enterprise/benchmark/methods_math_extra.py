"""Metode matematice CPU suplimentare — semnale ortogonale față de curated.

Contract (ca restul registry-ului):
    score_xxx(draws_2d: np.ndarray, max_num: int) -> dict[int, float]

Scorurile se normalizează în [0,1]. Exclusiv numpy (+ sklearn NMF dacă e disponibil).
Nu clonează frequency/graph/gap deja din curated — țintesc axe lipsă:
PCA-residual, MI pe lag, NMF pe co-apariții, CUSUM, topologie circulară.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)


def _normalize(scores: dict[int, float], max_num: int) -> dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter((float(v) for v in scores.values()), dtype=np.float64)
    if not np.isfinite(vals).all():
        vals = np.nan_to_num(vals, nan=0.0, posinf=0.0, neginf=0.0)
        scores = {int(k): float(np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0))
                  for k, v in scores.items()}
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((float(v) - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def _build_binary(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """(n_draws, draw_n) → (max_num, n_draws) binary indicator."""
    n_draws = int(draws_2d.shape[0])
    bm = np.zeros((max_num, n_draws), dtype=np.float64)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                bm[vi - 1, i] = 1.0
    return bm


def _safe_draws(draws_2d: np.ndarray) -> np.ndarray | None:
    if draws_2d is None or getattr(draws_2d, "size", 0) == 0:
        return None
    arr = np.asarray(draws_2d)
    if arr.ndim != 2 or arr.shape[0] < 8:
        return None
    return arr


# ---------------------------------------------------------------------------
# 1) Surpriză față de modul dominant PCA (residual)
# ---------------------------------------------------------------------------

def score_pca_resid_surprise(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Residual după scoaterea modului PCA dominant al seriilor binare.

    Prin construcție e ortogonal față de „frecvența comună” (PC1 ≈ pattern
    global de apariție). Scor = |residual| pe ultima extragere + energia
    residuală recentă — nu e clone de frequency/bayes/gap.
    """
    arr = _safe_draws(draws_2d)
    if arr is None:
        return {}
    bm = _build_binary(arr, max_num)  # (max_num, T)
    T = bm.shape[1]
    win = min(T, 400)
    X = bm[:, -win:]
    Xc = X - X.mean(axis=1, keepdims=True)
    try:
        # economy SVD pe Xc.T ar fi (win x max_num); pe Xc e (max_num x win)
        # folosim SVD pe Xc (max_num x win) — u are direcțiile pe numere
        u, s, vt = np.linalg.svd(Xc, full_matrices=False)
    except np.linalg.LinAlgError:
        return {}
    if s.size < 1 or s[0] < 1e-12:
        return {}
    # reconstrucție PC1
    recon = (u[:, :1] * s[0]) @ vt[:1, :]
    resid = Xc - recon
    # energie residuală pe fereastra recentă (ultimele ~20%)
    tail = max(8, resid.shape[1] // 5)
    energy = np.sqrt(np.mean(resid[:, -tail:] ** 2, axis=1))
    # surpriza pe ultima coloană (cât de departe e de PC1)
    last_s = np.abs(resid[:, -1])
    scores = 0.65 * last_s + 0.35 * energy
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


# ---------------------------------------------------------------------------
# 2) Mutual information cu bag-ul extragerii anterioare
# ---------------------------------------------------------------------------

def score_mi_lag_bag(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """MI(număr în t | bag-ul din t-1) — afinitate informațională pe lag 1.

    Nu e lanț Markov pe stări (dezactivate), nici lift-centrality pe graf:
    scor = sumă MI binară cu indicatorii din extragerea precedentă (fereastră recentă).
    """
    arr = _safe_draws(draws_2d)
    if arr is None:
        return {}
    bm = _build_binary(arr, max_num)  # (max_num, T)
    T = bm.shape[1]
    # folosește ultimele min(400, T) extrageri pt stabilitate
    start = max(1, T - 400)
    X = bm[:, start:]          # numere la t
    prev = bm[:, start - 1:T - 1] if start >= 1 else bm[:, :-1]
    # aliniere: X[:, k] corespunde prev[:, k] = draw t-1 pentru draw t = start+k
    # bm[:, start:] are lungime T-start; prev din bm[:, start-1:T-1] are T-start
    if prev.shape[1] != X.shape[1]:
        m = min(prev.shape[1], X.shape[1])
        prev, X = prev[:, -m:], X[:, -m:]
    n = X.shape[1]
    if n < 16:
        return {}

    # Pentru fiecare număr i: MI cu fiecare j din bag-ul precedent, agregat pe
    # cât de des j a apărut recent. Folosim MI pe 2x2 contingency.
    def _mi_pair(a: np.ndarray, b: np.ndarray) -> float:
        # a,b binary length n
        p11 = float(np.mean(a * b))
        p10 = float(np.mean(a * (1.0 - b)))
        p01 = float(np.mean((1.0 - a) * b))
        p00 = float(np.mean((1.0 - a) * (1.0 - b)))
        pa1 = p11 + p10
        pb1 = p11 + p01
        mi = 0.0
        for pxy, px, py in (
            (p11, pa1, pb1),
            (p10, pa1, 1.0 - pb1),
            (p01, 1.0 - pa1, pb1),
            (p00, 1.0 - pa1, 1.0 - pb1),
        ):
            if pxy > 1e-12 and px > 1e-12 and py > 1e-12:
                mi += pxy * np.log(pxy / (px * py))
        return float(max(0.0, mi))

    # bag recent = ultima extragere (indicatori)
    last_bag = bm[:, -1] > 0.5
    bag_idx = np.where(last_bag)[0]
    if bag_idx.size == 0:
        return {}

    scores = np.zeros(max_num, dtype=np.float64)
    # precompute MI(i, j) pe istoric pentru j din bag; media pe j din bag
    for i in range(max_num):
        s = 0.0
        for j in bag_idx:
            s += _mi_pair(X[i], prev[j])
        scores[i] = s / float(bag_idx.size)
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


# ---------------------------------------------------------------------------
# 3) NMF pe matricea de co-apariții recente
# ---------------------------------------------------------------------------

def score_nmf_cooc(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """NMF pe co-apariții (fereastră recentă) — loading pe factorii dominanți.

    Ortogonal față de DMD/Fourier (pe serii per-număr) și față de Katz/PR
    (spectral pe graf de lift).
    """
    arr = _safe_draws(draws_2d)
    if arr is None:
        return {}
    # fereastră recentă
    win = arr[-min(len(arr), 300):]
    C = np.zeros((max_num, max_num), dtype=np.float64)
    for row in win:
        nums = [int(v) for v in row if 1 <= int(v) <= max_num]
        for a_i, a in enumerate(nums):
            for b in nums[a_i + 1:]:
                C[a - 1, b - 1] += 1.0
                C[b - 1, a - 1] += 1.0
    # diagonală = frecvență
    freq = np.zeros(max_num, dtype=np.float64)
    for row in win:
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                freq[vi - 1] += 1.0
    np.fill_diagonal(C, freq + 1e-6)
    C = np.maximum(C, 0.0)

    k = min(4, max(2, max_num // 15))
    try:
        from sklearn.decomposition import NMF
        model = NMF(
            n_components=k,
            init="nndsvd",
            max_iter=400,
            random_state=0,
            l1_ratio=0.0,
        )
        W = model.fit_transform(C)  # (max_num, k)
        # scor = normă pe factori, ușor ponderată pe energia H
        H = model.components_
        energy = np.linalg.norm(H, axis=1) + 1e-12
        energy /= energy.sum()
        scores = W @ energy
    except Exception:
        # fallback SVD non-negativ aproximativ: top singular vector pe C, clip
        try:
            u, s, _vt = np.linalg.svd(C, full_matrices=False)
            scores = np.abs(u[:, 0]) * float(s[0])
        except Exception:
            scores = freq
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


# ---------------------------------------------------------------------------
# 4) CUSUM pe reziduurile de apariție
# ---------------------------------------------------------------------------

def score_cusum_appearance(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """CUSUM per număr pe (observat − rata așteptată) — detectează regimuri.

    Diferă de momentum fix 15-vs-60: acumulează abateri până la reset.
    """
    arr = _safe_draws(draws_2d)
    if arr is None:
        return {}
    bm = _build_binary(arr, max_num)  # (max_num, T)
    T = bm.shape[1]
    # rata așteptată globală ≈ draw_n / max_num
    draw_n = max(1, int(arr.shape[1]))
    p0 = float(draw_n) / float(max_num)
    # CUSUM pozitiv (apariții peste așteptare) pe fereastra recentă
    start = max(0, T - 250)
    X = bm[:, start:]
    scores = np.zeros(max_num, dtype=np.float64)
    drift = 0.5 * p0  # allow small drift
    for i in range(max_num):
        s = 0.0
        peak = 0.0
        for t in range(X.shape[1]):
            s = max(0.0, s + float(X[i, t]) - p0 - drift)
            if s > peak:
                peak = s
        scores[i] = peak
    # amestec ușor cu gap scurt ca să nu fie plat pe numere „reci”
    last = np.zeros(max_num, dtype=np.float64)
    for i in range(max_num):
        idx = np.where(bm[i] > 0.5)[0]
        last[i] = float(T - 1 - idx[-1]) if idx.size else float(T)
    # overdue ușor: mai mare gap → boost mic (nu domină CUSUM)
    gap_term = last / (last.max() + 1e-12)
    mixed = scores + 0.15 * gap_term
    return _normalize({i + 1: float(mixed[i]) for i in range(max_num)}, max_num)


# ---------------------------------------------------------------------------
# 5) Kernel circular pe inelul 1…N
# ---------------------------------------------------------------------------

def score_circular_kernel(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Densitate pe topologia circulară a numerelor (1 lângă max_num).

    Completează parity/prime (curated) și decade/mod (disabled) cu geometrie
    pe cerc — numerele „atrase” de vecinii recenti pe inel.
    """
    arr = _safe_draws(draws_2d)
    if arr is None:
        return {}
    # bandwidth pe cerc (în „pași” de număr)
    bw = max(2.0, max_num / 12.0)
    scores = np.zeros(max_num, dtype=np.float64)
    # decay temporal pe ultimele extrageri
    win = arr[-min(len(arr), 120):]
    tw = np.exp(-np.linspace(0.0, 2.5, len(win))[::-1])
    tw /= tw.sum()
    positions = np.arange(1, max_num + 1, dtype=np.float64)

    def _circ_dist(a: np.ndarray, b: float) -> np.ndarray:
        d = np.abs(a - b)
        return np.minimum(d, max_num - d)

    for t, row in enumerate(win):
        wt = float(tw[t])
        for v in row:
            vi = int(v)
            if not (1 <= vi <= max_num):
                continue
            d = _circ_dist(positions, float(vi))
            scores += wt * np.exp(-0.5 * (d / bw) ** 2)
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


MATH_EXTRA_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    "pca_resid_surprise": (
        score_pca_resid_surprise,
        "math-pca",
        False,
        "Surpriză residuală după scoaterea modului PCA dominant",
    ),
    "mi_lag_bag": (
        score_mi_lag_bag,
        "math-mi",
        False,
        "Mutual information cu bag-ul extragerii anterioare",
    ),
    "nmf_cooc": (
        score_nmf_cooc,
        "math-nmf",
        False,
        "NMF pe matricea de co-apariții recente",
    ),
    "cusum_appearance": (
        score_cusum_appearance,
        "math-cusum",
        False,
        "CUSUM pe reziduuri de apariție (schimbare de regim)",
    ),
    "circular_kernel": (
        score_circular_kernel,
        "math-circular",
        False,
        "Kernel densitate pe topologia circulară 1…N",
    ),
}
