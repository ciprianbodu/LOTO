"""Graph / network-science prediction methods (categorie NOUĂ — nu statistic/ML).

Idee: construim un GRAF de co-apariție al numerelor (noduri = 1..max_num, muchii
ponderate = de câte ori două numere au ieșit împreună în aceeași extragere, opțional
ponderat după recență). Scorul fiecărui număr = o măsură de centralitate / poziție în
graf (PageRank, vector propriu, Katz, closeness, Fiedler spectral, comunități,
random-walk-with-restart, difuzie de căldură etc.).

Interfață identică cu restul: score_xxx(draws_2d: np.ndarray, max_num: int) -> Dict[int, float].
Pur numpy (fără dependențe noi, fără GPU). Pe date aleatoare rezultatele rămân zgomot —
e o familie nouă de EXPLORARE, nu o garanție de performanță.
"""
from __future__ import annotations

import logging
import warnings
from typing import Dict, Callable, Tuple

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _normalize(scores: Dict[int, float], max_num: int) -> Dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) if np.isfinite(v) else 0.0 for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def _vec(v: np.ndarray, max_num: int) -> Dict[int, float]:
    """Vector de lungime max_num → dict {numar: scor} normalizat."""
    return _normalize({i + 1: float(v[i]) for i in range(max_num)}, max_num)


def _indicator(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """(max_num, n_draws) — 1 dacă numărul a apărut în extragerea t."""
    arr = np.asarray(draws_2d)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    T = arr.shape[0]
    M = np.zeros((max_num, T), dtype=np.float64)
    for t in range(T):
        for v in arr[t]:
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= iv <= max_num:
                M[iv - 1, t] = 1.0
    return M


def _adj(draws_2d: np.ndarray, max_num: int, halflife: float | None = None) -> np.ndarray:
    """Adiacență (max_num × max_num) = co-apariție PESTE întâmplare (lift centrat).

    Co-apariția BRUTĂ pe loto e ~ proporțională cu frecvențele (graf cvasi-complet,
    aproape uniform) → degree/pagerank/eigenvector/closeness ar degenera TOATE în
    „frecvență" (am observat 13/30 clasamente unice). Scădem co-apariția AȘTEPTATĂ sub
    independență (c_i·c_j / W) și păstrăm doar excesul (≥0) → graf de ASOCIERE reală,
    pe care centralitățile devin distincte. halflife → recență (decay exponențial)."""
    M = _indicator(draws_2d, max_num)  # (N, T)
    T = M.shape[1]
    if T == 0:
        return np.zeros((max_num, max_num))
    if halflife and halflife > 0:
        ages = np.arange(T)[::-1].astype(np.float64)  # cea mai recentă = 0
        w = 0.5 ** (ages / float(halflife))
    else:
        w = np.ones(T, dtype=np.float64)
    W = float(w.sum()) or 1.0
    Mw = M * np.sqrt(w)[None, :]
    O = Mw @ Mw.T                       # co-apariție observată (ponderată)
    c = M @ w                           # „frecvențe" ponderate per număr
    E = np.outer(c, c) / W              # co-apariție așteptată sub independență
    A = np.maximum(O - E, 0.0)          # exces de asociere (lift centrat, ≥0)
    np.fill_diagonal(A, 0.0)
    return A


def _recency_seed(draws_2d: np.ndarray, max_num: int, halflife: float = 30.0) -> np.ndarray:
    """Vector seed (lungime max_num): cât de recent/des a apărut fiecare număr."""
    M = _indicator(draws_2d, max_num)
    T = M.shape[1]
    if T == 0:
        return np.ones(max_num) / max_num
    ages = np.arange(T)[::-1].astype(np.float64)
    w = 0.5 ** (ages / float(halflife))
    s = M @ w
    tot = s.sum()
    return s / tot if tot > 0 else np.ones(max_num) / max_num


def _degree(A: np.ndarray) -> np.ndarray:
    return A.sum(axis=1)


def _pagerank(A: np.ndarray, damping: float = 0.85, iters: int = 200) -> np.ndarray:
    n = A.shape[0]
    if n == 0 or A.sum() == 0:
        return np.ones(n) / max(n, 1)
    deg = A.sum(axis=1)
    safe = np.where(deg > 0, deg, 1.0)
    P = A / safe[:, None]               # tranziție rând-stocastică (i→j)
    dangling = (deg == 0).astype(np.float64)
    r = np.ones(n) / n
    for _ in range(iters):
        leaked = damping * (dangling @ r) / n
        r_new = (1.0 - damping) / n + damping * (P.T @ r) + leaked
        if np.allclose(r_new, r, atol=1e-13):
            r = r_new
            break
        r = r_new
    return r


def _eig_principal(A: np.ndarray) -> np.ndarray:
    if A.sum() == 0:
        return np.zeros(A.shape[0])
    vals, vecs = np.linalg.eigh(A)        # A simetrică
    return np.abs(vecs[:, int(np.argmax(vals))])


def _katz(A: np.ndarray, frac: float = 0.5) -> np.ndarray:
    n = A.shape[0]
    if A.sum() == 0:
        return np.zeros(n)
    vals = np.linalg.eigvalsh(A)
    lam = float(np.max(np.abs(vals))) or 1.0
    alpha = frac / lam                    # alpha < 1/λmax pentru convergență
    try:
        c = np.linalg.solve(np.eye(n) - alpha * A, np.ones(n))
        return np.abs(c)
    except np.linalg.LinAlgError:
        return _degree(A)


def _dist(A: np.ndarray) -> np.ndarray:
    """Distanțe drum-scurt (Floyd-Warshall), distanța muchiei = 1/pondere."""
    n = A.shape[0]
    W = np.where(A > 0, 1.0 / np.where(A > 0, A, 1.0), np.inf)
    np.fill_diagonal(W, 0.0)
    D = W.copy()
    for k in range(n):
        D = np.minimum(D, D[:, k][:, None] + D[k, :][None, :])
    return D


def _laplacian(A: np.ndarray) -> np.ndarray:
    return np.diag(A.sum(axis=1)) - A


def _fiedler_labels(A: np.ndarray) -> np.ndarray:
    """Bisecție spectrală: semnul vectorului Fiedler (al 2-lea vector propriu al L)."""
    n = A.shape[0]
    if A.sum() == 0:
        return np.zeros(n, dtype=int)
    L = _laplacian(A)
    vals, vecs = np.linalg.eigh(L)
    order = np.argsort(vals)
    fied = vecs[:, order[1]] if n > 1 else vecs[:, 0]
    return (fied >= 0).astype(int)


# --------------------------------------------------------------------------- #
# Centralitate
# --------------------------------------------------------------------------- #
def score_graph_degree(draws_2d, max_num):
    try:
        return _vec(_degree(_adj(draws_2d, max_num)), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_degree_recent(draws_2d, max_num):
    try:
        return _vec(_degree(_adj(draws_2d, max_num, halflife=40.0)), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_second_order(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        return _vec((A @ A).sum(axis=1), max_num)   # vecinii vecinilor
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_neighbor_degree(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        deg = _degree(A)
        safe = np.where(deg > 0, deg, 1.0)
        return _vec((A @ deg) / safe, max_num)       # gradul mediu al vecinilor
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_assort_residual(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        deg = _degree(A)
        safe = np.where(deg > 0, deg, 1.0)
        nbr = (A @ deg) / safe
        return _vec(deg - nbr, max_num)              # noduri „hub" cu vecini slabi
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_pagerank(draws_2d, max_num):
    try:
        return _vec(_pagerank(_adj(draws_2d, max_num), 0.85), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_pagerank_low(draws_2d, max_num):
    try:
        return _vec(_pagerank(_adj(draws_2d, max_num), 0.50), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_pagerank_high(draws_2d, max_num):
    try:
        return _vec(_pagerank(_adj(draws_2d, max_num), 0.95), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_pagerank_recent(draws_2d, max_num):
    try:
        return _vec(_pagerank(_adj(draws_2d, max_num, halflife=40.0), 0.85), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_eigenvector(draws_2d, max_num):
    try:
        return _vec(_eig_principal(_adj(draws_2d, max_num)), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_eigenvector_recent(draws_2d, max_num):
    try:
        return _vec(_eig_principal(_adj(draws_2d, max_num, halflife=40.0)), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_katz_low(draws_2d, max_num):
    try:
        return _vec(_katz(_adj(draws_2d, max_num), 0.30), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_katz_high(draws_2d, max_num):
    try:
        return _vec(_katz(_adj(draws_2d, max_num), 0.70), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_subgraph_centrality(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        vals, vecs = np.linalg.eigh(A)
        vals = np.clip(vals, None, 50.0)             # anti-overflow exp
        sc = (vecs ** 2) @ np.exp(vals)              # diag(expm(A))
        return _vec(sc, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_personalized_pr(draws_2d, max_num):
    """PageRank personalizat: teleport spre numerele recente (nu uniform)."""
    try:
        A = _adj(draws_2d, max_num)
        n = A.shape[0]
        if A.sum() == 0:
            return _normalize({}, max_num)
        seed = _recency_seed(draws_2d, max_num, halflife=30.0)
        deg = A.sum(axis=1)
        safe = np.where(deg > 0, deg, 1.0)
        P = A / safe[:, None]
        d = 0.85
        r = seed.copy()
        for _ in range(200):
            r_new = (1 - d) * seed + d * (P.T @ r)
            if np.allclose(r_new, r, atol=1e-13):
                r = r_new
                break
            r = r_new
        return _vec(r, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


# --------------------------------------------------------------------------- #
# Distanță
# --------------------------------------------------------------------------- #
def score_graph_closeness(draws_2d, max_num):
    try:
        D = _dist(_adj(draws_2d, max_num))
        n = D.shape[0]
        out = np.zeros(n)
        for i in range(n):
            finite = D[i][np.isfinite(D[i])]
            s = finite.sum()
            if s > 0:
                out[i] = (len(finite) - 1) / s
        return _vec(out, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_harmonic(draws_2d, max_num):
    try:
        D = _dist(_adj(draws_2d, max_num))
        with np.errstate(divide="ignore"):
            inv = np.where(D > 0, 1.0 / D, 0.0)
        inv[~np.isfinite(inv)] = 0.0
        return _vec(inv.sum(axis=1), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_eccentricity_inv(draws_2d, max_num):
    try:
        D = _dist(_adj(draws_2d, max_num))
        n = D.shape[0]
        out = np.zeros(n)
        for i in range(n):
            finite = D[i][np.isfinite(D[i]) & (D[i] > 0)]
            if finite.size:
                ecc = finite.max()
                out[i] = 1.0 / ecc if ecc > 0 else 0.0
        return _vec(out, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_commute(draws_2d, max_num):
    """Centralitate prin rezistență (commute-time): pseudoinversa Laplacianului."""
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        Lp = np.linalg.pinv(_laplacian(A))
        diag = np.diag(Lp)
        n = A.shape[0]
        res = diag[:, None] + diag[None, :] - 2 * Lp     # rezistențe r_ij
        avg = res.sum(axis=1) / max(n - 1, 1)
        return _vec(1.0 / np.where(avg > 0, avg, np.inf), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


# --------------------------------------------------------------------------- #
# Spectral
# --------------------------------------------------------------------------- #
def score_graph_fiedler(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        L = _laplacian(A)
        vals, vecs = np.linalg.eigh(L)
        order = np.argsort(vals)
        fied = vecs[:, order[1]] if A.shape[0] > 1 else vecs[:, 0]
        return _vec(np.abs(fied), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_spectral_embed(draws_2d, max_num):
    """Norma nodului în embedding-ul spectral (vectorii 1..3 non-triviali ai L)."""
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        L = _laplacian(A)
        vals, vecs = np.linalg.eigh(L)
        order = np.argsort(vals)
        k = [c for c in order[1:4] if c < vecs.shape[1]]
        emb = vecs[:, k] if k else vecs[:, :1]
        return _vec(np.linalg.norm(emb, axis=1), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_heat_diffuse(draws_2d, max_num):
    """Difuzie de căldură exp(-tL) aplicată seed-ului de recență."""
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        L = _laplacian(A)
        vals, vecs = np.linalg.eigh(L)
        seed = _recency_seed(draws_2d, max_num, halflife=30.0)
        t = 2.0 / (float(vals.max()) or 1.0)
        diffused = vecs @ (np.exp(-t * vals) * (vecs.T @ seed))
        return _vec(diffused, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


# --------------------------------------------------------------------------- #
# Comunități / clustering
# --------------------------------------------------------------------------- #
def score_graph_clustering(draws_2d, max_num):
    """Coeficient de clustering ponderat local (triunghiuri normalizate)."""
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        Aw = A / (A.max() or 1.0)
        tri = np.diag(Aw @ Aw @ Aw)                  # triunghiuri ponderate
        deg = (A > 0).sum(axis=1).astype(np.float64)
        denom = deg * (deg - 1)
        out = np.where(denom > 0, tri / denom, 0.0)
        return _vec(out, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_triangles(draws_2d, max_num):
    try:
        B = (_adj(draws_2d, max_num) > 0).astype(np.float64)
        return _vec(np.diag(B @ B @ B) / 2.0, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_weighted_triangles(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        Aw = np.cbrt(A)                              # medie geometrică pe triunghi
        return _vec(np.diag(Aw @ Aw @ Aw), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_community_strength(draws_2d, max_num):
    """Tărie în interiorul comunității (bisecție Fiedler): suma ponderilor intra-grup."""
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        lab = _fiedler_labels(A)
        same = (lab[:, None] == lab[None, :]).astype(np.float64)
        return _vec((A * same).sum(axis=1), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_anti_community(draws_2d, max_num):
    """Contrarian: numere conectate mai mult ÎN AFARA comunității lor (punți)."""
    try:
        A = _adj(draws_2d, max_num)
        if A.sum() == 0:
            return _normalize({}, max_num)
        lab = _fiedler_labels(A)
        diff = (lab[:, None] != lab[None, :]).astype(np.float64)
        return _vec((A * diff).sum(axis=1), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


# --------------------------------------------------------------------------- #
# Random walk / temporal
# --------------------------------------------------------------------------- #
def _rwr(A: np.ndarray, seed: np.ndarray, restart: float = 0.15, iters: int = 200) -> np.ndarray:
    n = A.shape[0]
    if A.sum() == 0:
        return seed
    deg = A.sum(axis=1)
    safe = np.where(deg > 0, deg, 1.0)
    P = A / safe[:, None]
    r = seed.copy()
    for _ in range(iters):
        r_new = restart * seed + (1 - restart) * (P.T @ r)
        if np.allclose(r_new, r, atol=1e-13):
            r = r_new
            break
        r = r_new
    return r


def score_graph_rwr(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        seed = np.ones(max_num) / max_num
        return _vec(_rwr(A, seed, 0.15), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_rwr_recent(draws_2d, max_num):
    try:
        A = _adj(draws_2d, max_num)
        seed = _recency_seed(draws_2d, max_num, halflife=25.0)
        return _vec(_rwr(A, seed, 0.20), max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


def score_graph_temporal_drift(draws_2d, max_num):
    """Numere a căror centralitate de grad CREȘTE recent față de istoricul vechi."""
    try:
        arr = np.asarray(draws_2d)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        T = arr.shape[0]
        if T < 20:
            return _vec(_degree(_adj(arr, max_num)), max_num)
        half = T // 2
        deg_old = _degree(_adj(arr[:half], max_num))
        deg_new = _degree(_adj(arr[half:], max_num))
        no = deg_old.sum() or 1.0
        nn = deg_new.sum() or 1.0
        return _vec(deg_new / nn - deg_old / no, max_num)
    except Exception:  # noqa: BLE001
        return _normalize({}, max_num)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
GRAPH_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    # centralitate
    "graph_degree":              (score_graph_degree,             "graph-centrality", False, "Grad ponderat în graful de co-apariție"),
    "graph_degree_recent":       (score_graph_degree_recent,      "graph-centrality", False, "Grad ponderat, recență (halflife 40)"),
    "graph_second_order":        (score_graph_second_order,       "graph-centrality", False, "Grad de ordin 2 (vecinii vecinilor)"),
    "graph_neighbor_degree":     (score_graph_neighbor_degree,    "graph-centrality", False, "Gradul mediu al vecinilor"),
    "graph_assort_residual":     (score_graph_assort_residual,    "graph-centrality", False, "Reziduu asortativitate (hub cu vecini slabi)"),
    "graph_pagerank":            (score_graph_pagerank,           "graph-centrality", False, "PageRank (damping 0.85)"),
    "graph_pagerank_low":        (score_graph_pagerank_low,       "graph-centrality", False, "PageRank (damping 0.50)"),
    "graph_pagerank_high":       (score_graph_pagerank_high,      "graph-centrality", False, "PageRank (damping 0.95)"),
    "graph_pagerank_recent":     (score_graph_pagerank_recent,    "graph-centrality", False, "PageRank pe graf cu recență"),
    "graph_eigenvector":         (score_graph_eigenvector,        "graph-centrality", False, "Centralitate vector propriu"),
    "graph_eigenvector_recent":  (score_graph_eigenvector_recent, "graph-centrality", False, "Vector propriu, graf cu recență"),
    "graph_katz_low":            (score_graph_katz_low,           "graph-centrality", False, "Katz (alpha mic)"),
    "graph_katz_high":           (score_graph_katz_high,          "graph-centrality", False, "Katz (alpha mare)"),
    "graph_subgraph_centrality": (score_graph_subgraph_centrality,"graph-centrality", False, "Subgraph centrality diag(exp(A))"),
    "graph_personalized_pr":     (score_graph_personalized_pr,    "graph-centrality", False, "PageRank personalizat (teleport recență)"),
    # distanță
    "graph_closeness":           (score_graph_closeness,          "graph-distance",   False, "Closeness centrality"),
    "graph_harmonic":            (score_graph_harmonic,           "graph-distance",   False, "Harmonic centrality"),
    "graph_eccentricity_inv":    (score_graph_eccentricity_inv,   "graph-distance",   False, "1 / excentricitate"),
    "graph_commute":             (score_graph_commute,            "graph-distance",   False, "Centralitate rezistență (commute-time)"),
    # spectral
    "graph_fiedler":             (score_graph_fiedler,            "graph-spectral",   False, "|Vector Fiedler| (al 2-lea al Laplacianului)"),
    "graph_spectral_embed":      (score_graph_spectral_embed,     "graph-spectral",   False, "Normă în embedding spectral (3 vectori)"),
    "graph_heat_diffuse":        (score_graph_heat_diffuse,       "graph-spectral",   False, "Difuzie de căldură exp(-tL) pe recență"),
    # comunități / clustering
    "graph_clustering":          (score_graph_clustering,         "graph-community",  False, "Coeficient de clustering ponderat"),
    "graph_triangles":           (score_graph_triangles,          "graph-community",  False, "Participare la triunghiuri (binar)"),
    "graph_weighted_triangles":  (score_graph_weighted_triangles, "graph-community",  False, "Triunghiuri ponderate (medie geometrică)"),
    "graph_community_strength":  (score_graph_community_strength, "graph-community",  False, "Tărie intra-comunitate (bisecție Fiedler)"),
    "graph_anti_community":      (score_graph_anti_community,     "graph-community",  False, "Contrarian: punți inter-comunitate"),
    # random walk / temporal
    "graph_rwr":                 (score_graph_rwr,                "graph-walk",       False, "Random walk with restart (uniform)"),
    "graph_rwr_recent":          (score_graph_rwr_recent,         "graph-walk",       False, "Random walk with restart (seed recență)"),
    "graph_temporal_drift":      (score_graph_temporal_drift,     "graph-walk",       False, "Drift de centralitate recent vs vechi"),
}
