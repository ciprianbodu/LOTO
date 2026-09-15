"""Al doilea val de metode (20), adăugat la 14.09.2026 la cererea utilizatorului,
inclusiv idei reluate din vechea listă `disabled` (rescrise de la zero, ieftin
și determinist; niciuna nu e filtru structural):

    ses_opt_alpha          netezire exponențială simplă cu α optimizat per număr (fost `ses`)
    croston_interval       Croston: intervalele dintre apariții netezite, rata = 1/interval (fost `croston_opt`)
    theta_drift            metoda Theta pe cumulul aparițiilor: drift + SES (fost `theta_auto`)
    weighted_recent_linear frecvență cu ponderi liniar descrescătoare pe 100 (fost `weighted_recent`)
    drift_linear           tendința liniară a ratei glisante (CMMP pe 300), extrapolată (fost `drift`)
    imapa_agg              SES la nivele de agregare 1/2/4/8 combinate (fost `imapa` / `adida`)
    haar_multiscale        coeficienți Haar la scările 2..16 din fereastra recentă (fost `wavelet_haar`)
    ssa_forecast           analiză spectrală singulară pe seria proprie, prognoză prin recurență (fost `ssa`)
    dmd_forecast           descompunere în moduri dinamice pe matricea-indicator (fost `dmd`)
    runs_persistence       z-ul testului seriilor (Wald–Wolfowitz) × deviația recentă (fost `runs_test`)
    alternating_parity     rata pe extragerile de aceeași paritate de index (sezonalitate 2)
    repeat_last_draw       repetarea ultimei extrageri (fost `naive_last`), tie-break pe frecvență
    vlmm_self_k3           Markov cu lungime variabilă pe seria proprie, context ≤ 3 (fost `vlmm`)
    knn_pattern_self       k-NN pe ferestrele proprii de 10 stări (fost `ml_knn_*`, pe serie)
    pair_transition        tranziții de ordinul 2: perechi din extragerea t → numere la t+1 (fost `assoc_rules`)
    rwr_last_draw          random walk with restart pe graful de co-apariție, pornit din ultima extragere
    hawkes_cross           excitație încrucișată cu decădere exponențială prin co-aparițiile normalizate
    nb_lags_pooled         Naive Bayes Bernoulli pe ultimele 10 stări, model comun (fost `ml_bernoulli_nb`)
    knn_feature_pooled     k-NN în spațiul celor 6 trăsături comune (fost `ml_knn_5`, pe trăsături)
    gbm_stumps_pooled      gradient boosting cu 30 de „stumps" pe cele 6 trăsături (fost `ml_gradient_boost`)
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .methods_common import (
    ewma_weights,
    expected_rate,
    indicator,
    make_registry,
    vector_to_scores,
)
from .methods_learning import _features_from, _TRAIN_WINDOW

_EPS = 1e-9


def _window_mean(ind: np.ndarray, window: int) -> np.ndarray:
    if ind.shape[0] == 0:
        return np.zeros(ind.shape[1])
    return ind[-int(window) :].mean(axis=0)


def _ses_series(x: np.ndarray, alpha: float) -> np.ndarray:
    """SES vectorizat pe axa 0: s_t = α x_t + (1−α) s_{t−1}, s_0 = x_0."""
    if x.shape[0] == 0:
        return x
    out = lfilter([alpha], [1.0, -(1.0 - alpha)], x, axis=0)
    out[0] = x[0]
    return out


# --------------------------------------------------------------------------- #
# Netezire și tendință
# --------------------------------------------------------------------------- #
def score_ses_opt_alpha(draws_2d, max_num, window: int = 400):
    """Pentru fiecare număr, α ∈ {0.02..0.30} cu cea mai mică eroare pătratică la un pas."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n, m = x.shape
    if n < 30:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    alphas = np.array([0.02, 0.05, 0.08, 0.12, 0.18, 0.25, 0.30])
    best_err = np.full(m, np.inf)
    best_level = np.zeros(m)
    for a in alphas:
        s = _ses_series(x, a)
        err = ((x[1:] - s[:-1]) ** 2).mean(axis=0)
        better = err < best_err
        best_err = np.where(better, err, best_err)
        best_level = np.where(better, s[-1], best_level)
    return vector_to_scores(best_level, max_num)


def score_croston_interval(draws_2d, max_num, alpha: float = 0.1):
    """Croston pe serie binară: netezirea intervalelor dintre apariții; rata = 1/interval."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    p0 = expected_rate(draws_2d, max_num)
    out = np.full(m, p0)
    for j in range(m):
        idx = np.flatnonzero(ind[:, j] > 0)
        if idx.size < 2:
            continue
        gaps = np.diff(idx).astype(np.float64)
        level = gaps[0]
        for g in gaps[1:]:
            level = alpha * g + (1.0 - alpha) * level
        elapsed = n - 1 - idx[-1]
        # golul scurs peste intervalul netezit crește șansa (hazard aproximativ constant)
        out[j] = 1.0 / max(level, 1.0) * (1.0 + elapsed / max(level, 1.0))
    return vector_to_scores(out, max_num)


def score_theta_drift(draws_2d, max_num, window: int = 300):
    """Theta: media dintre driftul liniar și SES(0.2) pe rata glisantă a numărului."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n, m = x.shape
    if n < 30:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    cum = np.cumsum(x, axis=0)
    drift = (cum[-1] - cum[0]) / max(n - 1, 1)
    ses = _ses_series(x, 0.2)[-1]
    return vector_to_scores(0.5 * drift + 0.5 * ses, max_num)


def score_weighted_recent_linear(draws_2d, max_num, window: int = 100):
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n = x.shape[0]
    if n == 0:
        return vector_to_scores(np.zeros(ind.shape[1]), max_num)
    w = np.arange(1, n + 1, dtype=np.float64)
    return vector_to_scores((w @ x) / w.sum(), max_num)


def score_drift_linear(draws_2d, max_num, window: int = 300, rate_win: int = 50):
    """CMMP pe rata glisantă (50) din ultimele 300 de extrageri; scor = valoarea extrapolată."""
    ind = indicator(draws_2d, max_num)
    x = ind[-(window + rate_win) :]
    n, m = x.shape
    if n < rate_win + 20:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    cs = np.vstack([np.zeros((1, m)), np.cumsum(x, axis=0)])
    rate = (cs[rate_win:] - cs[:-rate_win]) / rate_win  # (n - rate_win + 1, m)
    k = rate.shape[0]
    t = np.arange(k, dtype=np.float64)
    tc = t - t.mean()
    slope = (tc[:, None] * (rate - rate.mean(axis=0))).sum(axis=0) / (tc**2).sum()
    forecast = rate.mean(axis=0) + slope * (k - t.mean())
    return vector_to_scores(forecast, max_num)


def score_imapa_agg(draws_2d, max_num, window: int = 512):
    """SES(0.1) pe seria agregată la nivelele 1, 2, 4, 8; prognozele (per extragere) mediate."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n, m = x.shape
    if n < 32:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    acc = np.zeros(m)
    for level in (1, 2, 4, 8):
        nb = n // level
        agg = x[n - nb * level :].reshape(nb, level, m).sum(axis=1)
        acc += _ses_series(agg, 0.1)[-1] / level
    return vector_to_scores(acc / 4.0, max_num)


def score_haar_multiscale(draws_2d, max_num, window: int = 64):
    """Coeficienții Haar de detaliu (ultimul din fiecare scară 2..16), ponderați spre scările fine."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n, m = x.shape
    if n < 32:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    x = x - expected_rate(draws_2d, max_num)
    acc = np.zeros(m)
    for k, scale in enumerate((2, 4, 8, 16)):
        half = scale // 2
        recent = x[-half:].mean(axis=0)
        previous = x[-scale:-half].mean(axis=0)
        acc += (recent - previous) / (k + 1.0)
    return vector_to_scores(acc, max_num)


# --------------------------------------------------------------------------- #
# Descompuneri
# --------------------------------------------------------------------------- #
def score_ssa_forecast(draws_2d, max_num, window: int = 128, L: int = 24, rank: int = 3):
    """SSA per număr: Hankel L×K, primele `rank` componente, prognoză prin recurența liniară."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n, m = x.shape
    if n < 2 * L:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    x = x - x.mean(axis=0)
    K = n - L + 1
    out = np.zeros(m)
    idx = np.arange(L)[:, None] + np.arange(K)[None, :]
    for j in range(m):
        H = x[:, j][idx]  # (L, K)
        U, s, _ = np.linalg.svd(H, full_matrices=False)
        r = min(rank, U.shape[1])
        P = U[:, :r]
        pi = P[-1, :]
        nu2 = float(pi @ pi)
        if nu2 >= 1.0 - 1e-6:
            continue
        R = (P[:-1, :] @ pi) / (1.0 - nu2)  # coeficienții recurenței (L-1)
        out[j] = float(R @ x[-(L - 1) :, j])
    return vector_to_scores(out, max_num)


def score_dmd_forecast(draws_2d, max_num, window: int = 200, rank: int = 6):
    """DMD: A ≈ Y X⁺ pe matricea-indicator (numere × timp); scor = A · x_ultim."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:].T  # (m, n)
    m, n = x.shape
    if n < 20:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    x = x - x.mean(axis=1, keepdims=True)
    X, Y = x[:, :-1], x[:, 1:]
    U, s, Vt = np.linalg.svd(X, full_matrices=False)
    r = min(rank, int((s > 1e-8).sum()))
    if r == 0:
        return vector_to_scores(np.zeros(m), max_num)
    Ur, sr, Vtr = U[:, :r], s[:r], Vt[:r]
    A_tilde = Ur.T @ Y @ Vtr.T / sr
    A = Ur @ A_tilde @ Ur.T
    return vector_to_scores(A @ x[:, -1], max_num)


def score_runs_persistence(draws_2d, max_num, window: int = 300):
    """z-ul Wald–Wolfowitz (negativ = grupare) înmulțit cu deviația recentă: persistență."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n, m = x.shape
    if n < 30:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    n1 = x.sum(axis=0)
    n0 = n - n1
    runs = 1.0 + (x[1:] != x[:-1]).sum(axis=0)
    mu = 1.0 + 2.0 * n1 * n0 / n
    var = 2.0 * n1 * n0 * (2.0 * n1 * n0 - n) / (n * n * (n - 1.0))
    z = (runs - mu) / np.sqrt(np.maximum(var, _EPS))
    recent = x[-30:].mean(axis=0) - expected_rate(draws_2d, max_num)
    return vector_to_scores(-z * recent, max_num)


def score_alternating_parity(draws_2d, max_num, window: int = 400):
    """Rata pe extragerile cu aceeași paritate de index ca următoarea (sezonalitate de perioadă 2)."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 20:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    x = ind[-window:]
    k = x.shape[0]
    # următoarea extragere are indexul n; pe fereastră, aceeași paritate = pozițiile k-2, k-4, ...
    same = x[(k - 2) % 2 :: 2] if k >= 2 else x
    return vector_to_scores(same.mean(axis=0), max_num)


def score_repeat_last_draw(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    return vector_to_scores(ind[-1] + 0.001 * _window_mean(ind, 300), max_num)


# --------------------------------------------------------------------------- #
# Context propriu
# --------------------------------------------------------------------------- #
def score_vlmm_self_k3(draws_2d, max_num, min_count: int = 20):
    """P(1 | ultimele k stări proprii), k = 3 → 2 → 1 (back-off la contexte rare), Laplace."""
    ind = indicator(draws_2d, max_num).astype(np.int64)
    n, m = ind.shape
    if n < 5:
        return vector_to_scores(np.zeros(m), max_num)
    out = np.zeros(m)
    for k in (3, 2, 1):
        weights = 1 << np.arange(k)[::-1]  # codul contextului
        ctx = np.zeros((n - k, m), dtype=np.int64)
        for i in range(k):
            ctx += ind[i : n - k + i] * weights[i]
        nxt = ind[k:]
        cur = np.zeros(m, dtype=np.int64)
        for i in range(k):
            cur += ind[n - k + i] * weights[i]
        match = ctx == cur[None, :]
        cnt = match.sum(axis=0)
        ones = (match * nxt).sum(axis=0)
        p = (ones + 1.0) / (cnt + 2.0)
        if k == 3:
            out = p.astype(np.float64)
            settled = cnt >= min_count
        else:
            out = np.where(settled, out, p)
            settled = settled | (cnt >= min_count)
    return vector_to_scores(out, max_num)


def score_knn_pattern_self(draws_2d, max_num, L: int = 10, k: int = 30):
    """k-NN pe ferestrele proprii: cele mai apropiate L stări trecute → ce a urmat."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < L + k + 2:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    idx = np.arange(L)[None, :] + np.arange(n - L)[:, None]  # ferestre care se termină înainte de n-1
    out = np.zeros(m)
    query = ind[n - L :]
    for j in range(m):
        W = ind[:, j][idx]  # (n-L, L); fereastra i acoperă i..i+L-1, urmată de i+L
        d = np.abs(W - query[:, j][None, :]).sum(axis=1)
        order = np.lexsort((-np.arange(W.shape[0]), d))[:k]
        follow = ind[idx[order, -1] + 1, j]
        out[j] = follow.mean()
    return vector_to_scores(out, max_num)


# --------------------------------------------------------------------------- #
# Relații de ordin 2 și dinamică pe graf
# --------------------------------------------------------------------------- #
def score_pair_transition(draws_2d, max_num):
    """P(k la t+1 | perechea {i, j} la t), mediat peste perechile ultimei extrageri."""
    from scipy.sparse import csr_matrix

    arr = np.asarray(draws_2d)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    n, d = arr.shape
    m = int(max_num)
    if n < 3 or d < 2:
        return vector_to_scores(np.zeros(m), max_num)
    ind = indicator(draws_2d, max_num)
    vals = np.sort(arr.astype(np.int64), axis=1) - 1
    ok = (vals >= 0) & (vals < m)
    rows, cols = [], []
    for a in range(d):
        for b in range(a + 1, d):
            good = ok[:, a] & ok[:, b]
            t = np.flatnonzero(good)
            rows.append(t)
            cols.append(vals[t, a] * m + vals[t, b])
    rows = np.concatenate(rows)
    cols = np.concatenate(cols)
    P = csr_matrix((np.ones(rows.size), (rows, cols)), shape=(n, m * m))
    T2 = (P[:-1].T @ ind[1:])  # (m*m, m) numărători pereche → următor
    T2 = np.asarray(T2)
    cnt = np.asarray(P[:-1].sum(axis=0)).ravel()
    last_pairs = cols[rows == n - 1]
    if last_pairs.size == 0:
        return vector_to_scores(np.zeros(m), max_num)
    probs = (T2[last_pairs] + 1.0) / (cnt[last_pairs][:, None] + 2.0)
    return vector_to_scores(probs.mean(axis=0), max_num)


def score_rwr_last_draw(draws_2d, max_num, restart: float = 0.3, iters: int = 40):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    c = ind.T @ ind
    np.fill_diagonal(c, 0.0)
    col = c.sum(axis=0)
    if col.sum() <= 0:
        return vector_to_scores(np.zeros(m), max_num)
    M = c / np.maximum(col, _EPS)[None, :]
    seed = ind[-1] / max(float(ind[-1].sum()), 1.0)
    r = seed.copy()
    for _ in range(iters):
        r = (1.0 - restart) * (M @ r) + restart * seed
    return vector_to_scores(r, max_num)


def score_hawkes_cross(draws_2d, max_num, half_life: float = 20.0):
    """λ_j = Σ_i Cn[i,j]·E_i, E_i = excitația proprie cu decădere exponențială."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 2:
        return vector_to_scores(np.zeros(m), max_num)
    c = ind.T @ ind
    np.fill_diagonal(c, 0.0)
    cn = c / np.maximum(c.sum(axis=1, keepdims=True), _EPS)
    E = ewma_weights(n, half_life) @ ind
    return vector_to_scores(E @ cn, max_num)


# --------------------------------------------------------------------------- #
# Învățare, model comun
# --------------------------------------------------------------------------- #
def score_nb_lags_pooled(draws_2d, max_num, lags: int = 10, window: int = 400):
    """Naive Bayes Bernoulli pe ultimele 10 stări (model comun): log P(y=1|x) − log P(y=0|x)."""
    ind = indicator(draws_2d, max_num)
    x = ind[-window:]
    n, m = x.shape
    if n <= lags + 5:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    cols = [x[lags - k - 1 : n - k - 1] for k in range(lags)]
    X = np.stack(cols, axis=-1).reshape(-1, lags)
    y = x[lags:].reshape(-1)
    pos, neg = X[y > 0], X[y <= 0]
    p1 = (pos.sum(axis=0) + 1.0) / (pos.shape[0] + 2.0)
    p0 = (neg.sum(axis=0) + 1.0) / (neg.shape[0] + 2.0)
    prior = np.log((pos.shape[0] + 1.0) / (neg.shape[0] + 1.0))
    last = np.stack([x[n - k - 1] for k in range(lags)], axis=-1)  # (m, lags)
    ll = last @ (np.log(p1) - np.log(p0)) + (1.0 - last) @ (np.log(1 - p1) - np.log(1 - p0))
    return vector_to_scores(ll + prior, max_num)


def score_knn_feature_pooled(draws_2d, max_num, k: int = 25):
    """k-NN în spațiul celor 6 trăsături comune: media țintelor vecinilor (distanță standardizată)."""
    from scipy.spatial import cKDTree

    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 30:
        return vector_to_scores(ind.sum(axis=0), max_num)
    start = max(n - _TRAIN_WINDOW, 50)
    if start >= n:
        start = max(n - 20, 0)
    F = _features_from(ind, expected_rate(draws_2d, max_num), start)
    X = F[:-1, :, :5].reshape(-1, 5)
    y = ind[start:n].reshape(-1)
    Q = F[-1, :, :5]
    sd = X.std(axis=0) + _EPS
    tree = cKDTree(X / sd)
    kk = min(k, X.shape[0])
    _, nn = tree.query(Q / sd, k=kk)
    nn = np.asarray(nn).reshape(m, -1)
    return vector_to_scores(y[nn].mean(axis=1), max_num)


def score_gbm_stumps_pooled(draws_2d, max_num, rounds: int = 30, lr: float = 0.3):
    """Gradient boosting cu „stumps" (o tăietură pe o trăsătură), pierdere logistică, model comun.

    Tăieturile candidate sunt cele 9 cuantile ale fiecărei trăsături; câștigul se
    evaluează pe sume cumulate în ordinea sortată (o sortare per trăsătură, o
    dată), deci fiecare rundă costă O(rânduri), nu O(rânduri × tăieturi).
    """
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 30:
        return vector_to_scores(ind.sum(axis=0), max_num)
    start = max(n - _TRAIN_WINDOW, 50)
    if start >= n:
        start = max(n - 20, 0)
    F = _features_from(ind, expected_rate(draws_2d, max_num), start)
    X = F[:-1, :, :5].reshape(-1, 5)
    y = ind[start:n].reshape(-1)
    Q = F[-1, :, :5]
    rows, nf = X.shape
    base = float(np.log((y.mean() + 1e-6) / (1.0 - y.mean() + 1e-6)))
    f_train = np.full(rows, base)
    f_q = np.full(m, base)
    orders = [np.argsort(X[:, j], kind="stable") for j in range(nf)]
    quantiles = (np.linspace(0.1, 0.9, 9) * rows).astype(int).clip(1, rows - 1)
    # Tăieturile candidate: „x <= prag" cu pragul pe o graniță între valori
    # DISTINCTE (altfel, pe o trăsătură binară, „<= 1" punea totul în stânga și
    # câștiga fără să separe nimic). `splits[j]` = numărul de rânduri din stânga.
    splits: list[np.ndarray] = []
    for j in range(nf):
        xs = X[orders[j], j]
        left_counts = np.unique(np.searchsorted(xs, xs[quantiles - 1], side="right"))
        splits.append(left_counts[(left_counts >= 1) & (left_counts < rows)])
    for _ in range(rounds):
        p = 1.0 / (1.0 + np.exp(-f_train))
        g = y - p
        h = p * (1.0 - p) + 1e-6
        G, H = g.sum(), h.sum()
        best = None
        for j in range(nf):
            pos = splits[j]
            if pos.size == 0:
                continue
            o = orders[j]
            cg = np.cumsum(g[o])[pos - 1]
            ch = np.cumsum(h[o])[pos - 1]
            gain = cg * cg / (ch + 1.0) + (G - cg) ** 2 / (H - ch + 1.0)
            i = int(np.argmax(gain))
            if best is None or gain[i] > best[0]:
                thr = X[o[pos[i] - 1], j]
                best = (float(gain[i]), j, thr, cg[i] / (ch[i] + 1.0), (G - cg[i]) / (H - ch[i] + 1.0))
        if best is None:
            break
        _, j, c, wl, wr = best
        f_train += lr * np.where(X[:, j] <= c, wl, wr)
        f_q += lr * np.where(Q[:, j] <= c, wl, wr)
    return vector_to_scores(f_q, max_num)


WAVE2_METHODS = make_registry(
    [
        ("ses_opt_alpha", score_ses_opt_alpha, "timeseries", "SES cu α optimizat per număr"),
        ("croston_interval", score_croston_interval, "gap", "Croston pe intervalele dintre apariții"),
        ("theta_drift", score_theta_drift, "timeseries", "Theta: drift + SES pe rata numărului"),
        ("weighted_recent_linear", score_weighted_recent_linear, "recency", "ponderi liniare pe ultimele 100"),
        ("drift_linear", score_drift_linear, "timeseries", "tendința liniară a ratei glisante, extrapolată"),
        ("imapa_agg", score_imapa_agg, "timeseries", "SES la nivele de agregare 1/2/4/8"),
        ("haar_multiscale", score_haar_multiscale, "timeseries", "coeficienți Haar la scările 2..16"),
        ("ssa_forecast", score_ssa_forecast, "timeseries", "SSA cu recurență liniară"),
        ("dmd_forecast", score_dmd_forecast, "timeseries", "descompunere în moduri dinamice"),
        ("runs_persistence", score_runs_persistence, "timeseries", "z Wald–Wolfowitz × deviația recentă"),
        ("alternating_parity", score_alternating_parity, "recency", "rata pe extragerile de aceeași paritate de index"),
        ("repeat_last_draw", score_repeat_last_draw, "transition", "repetarea ultimei extrageri"),
        ("vlmm_self_k3", score_vlmm_self_k3, "transition", "Markov cu lungime variabilă pe seria proprie"),
        ("knn_pattern_self", score_knn_pattern_self, "similarity", "k-NN pe ferestrele proprii de 10 stări"),
        ("pair_transition", score_pair_transition, "transition", "perechi din extragerea t → numere la t+1"),
        ("rwr_last_draw", score_rwr_last_draw, "graph", "random walk with restart din ultima extragere"),
        ("hawkes_cross", score_hawkes_cross, "cooccurrence", "excitație încrucișată cu decădere"),
        ("nb_lags_pooled", score_nb_lags_pooled, "learning", "Naive Bayes Bernoulli pe 10 laguri, model comun"),
        ("knn_feature_pooled", score_knn_feature_pooled, "learning", "k-NN în spațiul trăsăturilor comune"),
        ("gbm_stumps_pooled", score_gbm_stumps_pooled, "learning", "boosting cu 30 de stumps, model comun"),
    ]
)

__all__ = ["WAVE2_METHODS"]
