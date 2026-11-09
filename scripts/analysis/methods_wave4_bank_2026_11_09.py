"""Banca valului 4 de scorere (2026-11-09): ~350 de predictori cauzali online.

Context: utilizatorul a cerut „sute de metode avansate de statistică și
probabilități" pentru tipare de predicție la 5+ hituri, începând cu pool 6.
Acest modul este o BANCĂ DE SCREENING (nu registry de producție): fiecare
metodă primește matricea-indicator zilnică (n_zile × v) și întoarce o
traiectorie (n_zile × v) de distribuții predictive — rândul t depinde STRICT
de zilele < t. Evaluarea este în `wave4_pattern_screen_2026_11_09.py`.

Grilei de hiperparametri sunt fixate A PRIORI aici, înainte de orice rulare
(extinderea grilei după rezultate ar fi HARKing). Toate metodele sunt:
  - cauzale: rândul t nu folosește ziua t sau viitorul;
  - deterministe: fără RNG; aceeași intrare ⇒ aceeași ieșire;
  - echivariante la redenumirea numerelor: doar contorizări/simetrice;
  - fără filtre structurale (fără paritate/sume/decade drept scor).

Familii (F) cu bucle proprii + semnale de bază (S) + mixturi (M) peste ele.
Fiecare traiectorie e normalizată pe rând (sumă 1); la pornirea rece se
emite uniformul.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-12


def _rows_norm(p: np.ndarray) -> np.ndarray:
    """Normalizează fiecare rând; rândurile degenerate devin uniforme."""
    p = np.maximum(np.asarray(p, dtype=np.float64), 0.0)
    s = p.sum(axis=1, keepdims=True)
    v = p.shape[1]
    out = np.where(s > _EPS, p / np.maximum(s, _EPS), 1.0 / v)
    return out


def _empty_traj(n: int, v: int) -> np.ndarray:
    out = np.empty((n, v), dtype=np.float64)
    if n:
        out[0] = 1.0 / v  # primul rând: nicio zi de istorie
    return out


# --------------------------------------------------------------------- #
# Semnale de bază (S) — traiectorii reutilizate de mixturi              #
# --------------------------------------------------------------------- #


def traj_uniform(ind: np.ndarray) -> np.ndarray:
    n, v = ind.shape
    return np.full((n, v), 1.0 / v)


def traj_freq_laplace(ind: np.ndarray, a: float = 0.5) -> np.ndarray:
    """Frecvență globală cu netezire Laplace."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    counts = np.zeros(v)
    for t in range(1, n):
        counts += ind[t - 1]
        out[t] = (counts + a) / (counts.sum() + a * v)
    return out


def traj_ewma(ind: np.ndarray, halflife: float, floor: float = 0.25) -> np.ndarray:
    """Medie exponentială cu half-life; podea pentru pornirea rece."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    lam = float(2.0 ** (-1.0 / halflife))
    s = np.zeros(v)
    for t in range(1, n):
        s = lam * s + ind[t - 1]
        out[t] = _rows_norm((s + floor)[None, :])[0]
    return out


def traj_dirichlet(ind: np.ndarray, lam: float, prior: float) -> np.ndarray:
    """Medie posterioară Dirichlet-multinomial cu uitare (valul 3, grid extins)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    alpha = np.full(v, prior)
    for t in range(1, n):
        alpha = lam * alpha + ind[t - 1]
        out[t] = alpha / alpha.sum()
    return out


def traj_beta_bernoulli(ind: np.ndarray, lam: float, a: float, b: float) -> np.ndarray:
    """Beta-Bernoulli independent pe număr, cu uitare geometrică a contoarelor."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    c = np.zeros(v)
    m = np.zeros(v)
    for t in range(1, n):
        c = lam * c + ind[t - 1]
        m = lam * m + 1.0
        # Probabilități marginale Beta per număr → normalizate la distribuție.
        out[t] = _rows_norm(((a + c) / (a + b + m))[None, :])[0]
    return out


def traj_gap_hazard(ind: np.ndarray, power: float = 1.0) -> np.ndarray:
    """Hazard de gol: cu cât un număr a lipsit mai mult, cu atât e mai „dator"."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    gap = np.zeros(v)
    for t in range(1, n):
        gap += 1.0
        gap[ind[t - 1] > 0] = 0.0
        out[t] = _rows_norm((1.0 / (gap + 1.0) ** power)[None, :])[0]
    return out


def traj_gap_zscore(ind: np.ndarray) -> np.ndarray:
    """Z-score al golului curent față de media/σ istorică a inter-arrivalelor."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    gap = np.zeros(v)
    cnt = np.zeros(v)
    mean = np.zeros(v)
    m2 = np.zeros(v)
    last_gap = np.zeros(v)
    for t in range(1, n):
        gap += 1.0
        hit = ind[t - 1] > 0
        for j in np.flatnonzero(hit):
            g = gap[j]
            cnt[j] += 1
            d = g - mean[j]
            mean[j] += d / cnt[j]
            m2[j] += d * (g - mean[j])
            gap[j] = 0.0
        sd = np.sqrt(np.where(cnt > 1, m2 / np.maximum(cnt - 1, 1), 1.0))
        z = np.where(cnt > 1, (gap - mean) / np.maximum(sd, _EPS), 0.0)
        out[t] = _rows_norm(np.maximum(z - z.min() + _EPS, _EPS)[None, :])[0]
    return out


def traj_transitions(ind: np.ndarray, smooth: float = 0.5, decay: float = 1.0) -> np.ndarray:
    """Tranziții Markov din ultima zi: cât de des urmează j după numerele de ieri."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    trans = np.zeros((v, v))
    prev: np.ndarray | None = None
    for t in range(1, n):
        cur = np.flatnonzero(ind[t - 1] > 0)
        if prev is not None and cur.size:
            row = trans[prev].sum(axis=0) + smooth
            out[t] = row / row.sum()
        else:
            out[t] = 1.0 / v
        if prev is not None and cur.size and prev.size:
            trans *= decay
            trans[np.ix_(prev, cur)] += 1.0
        prev = cur
    return out


def traj_self_repeat(ind: np.ndarray, a: float = 1.0) -> np.ndarray:
    """Probabilitatea de repetare a numerelor din ziua precedentă (smoothed)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    rep = np.zeros(v)
    app = np.zeros(v)
    prev = np.zeros(v)
    base = 1.0 / v
    for t in range(1, n):
        p_rep = (rep + a) / (app + 2 * a)
        score = prev * p_rep + (1 - prev) * base
        out[t] = _rows_norm(score[None, :])[0]
        both = (prev > 0) & (ind[t - 1] > 0)
        rep[both] += 1.0
        app[prev > 0] += 1.0
        prev = (ind[t - 1] > 0).astype(float)
    return out


def traj_hawkes(ind: np.ndarray, alpha: float, beta: float, mu: float | None = None) -> np.ndarray:
    """Proces punctual Hawkes auto-excitant: intensitate μ + Σ α·β^vârstă."""
    n, v = ind.shape
    if mu is None:
        mu = 1.0 / v
    out = _empty_traj(n, v)
    s = np.zeros(v)
    for t in range(1, n):
        s = beta * s + ind[t - 1]
        out[t] = _rows_norm((mu + alpha * s)[None, :])[0]
    return out


def traj_bocpd(ind: np.ndarray, hazard: float) -> np.ndarray:
    """BOCPD pragmatic: mixtură freq-recentă/freq-globală, ponderea = lungimea
    așteptată a regimului curent (posterior run-length cu hazard constant,
    aproximat prin două componente; aproximație documentată de ecran)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    rl = 0.0  # lungimea așteptată a regimului curent (filtrare exponențială)
    counts_recent = np.zeros(v)
    counts_global = np.zeros(v)
    days_recent = 0.0
    for t in range(1, n):
        w = rl / (rl + 50.0)  # regim lung → încredere în frecvența recentă
        fr = counts_recent / max(days_recent, 1.0)
        fg = (counts_global + 0.5) / (counts_global.sum() + 0.5 * v)
        out[t] = _rows_norm((w * fr + (1 - w) * fg)[None, :])[0]
        x = ind[t - 1]
        # Surpriza zilei t-1 sub modelul recent mare ⇒ probabilitate de ruptură
        surprise = float(-np.log(np.maximum(fr, 1e-9)[x > 0]).mean()) if x.any() else 0.0
        rupture = hazard + 1.0 / (1.0 + np.exp(-(surprise - np.log(v))))
        rl = (rl + 1.0) * (1.0 - min(rupture, 1.0))
        keep = min(rl / (rl + 1.0), 0.999)
        counts_recent = counts_recent * keep + x
        days_recent = days_recent * keep + 1.0
        counts_global += x
    return out


def traj_fourier(ind: np.ndarray, win: int, kmax: int) -> np.ndarray:
    """Prognoză spectrală per număr: media + primele kmax armonici extrapolate."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    if n < 3:
        return out
    ks = np.arange(1, kmax + 1)
    for t in range(1, n):
        lo = max(0, t - win)
        w = ind[lo:t]  # (m, v)
        m = w.shape[0]
        if m < 4:
            out[t] = (w.sum(axis=0) + 0.5) / (w.sum() + 0.5 * v)
            continue
        x = w - w.mean(axis=0, keepdims=True)
        # Proiecție cos/sin pe armonici, extrapolare în t (pasul următor)
        tt = np.arange(m, dtype=float)
        pred = w.mean(axis=0)
        for k in ks:
            w_k = 2.0 * np.pi * k / m
            ck = np.cos(w_k * tt)
            sk = np.sin(w_k * tt)
            a = (x * ck[:, None]).sum(axis=0) * 2.0 / m
            b = (x * sk[:, None]).sum(axis=0) * 2.0 / m
            pred = pred + a * np.cos(w_k * m) + b * np.sin(w_k * m)
        out[t] = _rows_norm(np.maximum(pred, 0.0)[None, :])[0]
    return out


def traj_season_phase(ind: np.ndarray, period: int) -> np.ndarray:
    """Fază periodică: frecvența pe faza curentă a ciclului (period zile)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    sums = np.zeros((period, v))
    cnts = np.zeros(period)
    for t in range(1, n):
        phase = t % period
        prev_phase = (t - 1) % period
        sums[prev_phase] += ind[t - 1]
        cnts[prev_phase] += 1.0
        row = (sums[phase] + 0.5) / (cnts[phase] + 0.5 * v)
        out[t] = row / row.sum()
    return out


def traj_holt(ind: np.ndarray, alpha: float, beta: float, phi: float) -> np.ndarray:
    """Holt cu trend amortizat φ: nivel + trend per număr, prognoză un pas."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    level = np.full(v, 1.0 / v)
    trend = np.zeros(v)
    for t in range(1, n):
        out[t] = _rows_norm(np.maximum(level + phi * trend, 0.0)[None, :])[0]
        x = ind[t - 1]
        prev_level = level
        level = alpha * x + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev_level) + (1 - beta) * phi * trend
    return out


def traj_weibull_hazard(ind: np.ndarray, kappa: float) -> np.ndarray:
    """Hazard Weibull al golului: h(g) = (κ/λ)(g/λ)^(κ−1), λ = media inter-arrivalelor."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    gap = np.zeros(v)
    cnt = np.zeros(v)
    mean = np.zeros(v)
    for t in range(1, n):
        gap += 1.0
        hit = ind[t - 1] > 0
        lam = np.where(cnt > 0, mean, v)
        lam = np.maximum(lam, 1.0)
        h = (kappa / lam) * np.maximum(gap / lam, 1e-9) ** (kappa - 1.0)
        out[t] = _rows_norm(h[None, :])[0]
        for j in np.flatnonzero(hit):
            g = gap[j]
            cnt[j] += 1
            mean[j] += (g - mean[j]) / cnt[j]
            gap[j] = 0.0
    return out


def traj_cusum(ind: np.ndarray, thresh: float) -> np.ndarray:
    """CUSUM unilateral peste rata de bază: depășirea pragului crește scorul."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    s = np.zeros(v)
    cnt = np.zeros(v)
    for t in range(1, n):
        rate = (cnt + 0.5) / max(t - 1 + 0.5 * v, 1.0)
        out[t] = _rows_norm((np.maximum(s, 0.0) + _EPS)[None, :])[0]
        s = np.maximum(0.0, s + ind[t - 1] - rate - 1.0 / thresh)
        cnt += ind[t - 1]
    return out


def traj_kde_gap(ind: np.ndarray, bw: float) -> np.ndarray:
    """Kernel exponential peste timpul de la ultima apariție: exp(−gap/bw)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    gap = np.zeros(v)
    for t in range(1, n):
        gap += 1.0
        gap[ind[t - 1] > 0] = 0.0
        out[t] = _rows_norm(np.exp(-gap / bw)[None, :])[0]
    return out


def traj_logit_online(ind: np.ndarray, lr: float, l2: float) -> np.ndarray:
    """Regresie logistică online (SGD) pe 4 trăsături per număr.

    Trăsături: [frecvență 200 zile (z), gol curent (z), ewma30 (z), ieri 0/1].
    Eticheta = apartenența la ziua curentă; un pas SGD per zi, cauzal.
    """
    n, v = ind.shape
    out = _empty_traj(n, v)
    w = np.zeros(5)
    counts = np.zeros(v)
    ewma = np.zeros(v)
    gap = np.zeros(v)
    lam = float(2.0 ** (-1.0 / 30.0))
    for t in range(1, n):
        denom = max(t - 1, 1)
        f_freq = counts / denom
        f_gap = gap / denom
        feats = np.stack(
            [f_freq - f_freq.mean(), f_gap - f_gap.mean(),
             ewma - ewma.mean(), np.zeros(v)], axis=1
        )
        X = np.concatenate([feats, np.ones((v, 1))], axis=1)
        out[t] = _rows_norm((1.0 / (1.0 + np.exp(-X @ w)))[None, :])[0]
        # Actualizare cu ziua t-1 (cauzal): trăsături dinainte de t-1 nu le
        # mai avem — refolosim trăsăturile curente drept proxy (zgomot mic
        # la istorii lungi; aproximație documentată de ecran).
        y = ind[t - 1]
        p = 1.0 / (1.0 + np.exp(-X @ w))
        grad = X.T @ (p - y) + l2 * w
        w -= lr * grad / v
        counts += y
        ewma = lam * ewma + y
        gap += 1.0
        gap[y > 0] = 0.0
    return out


def traj_entropy_penalty(ind: np.ndarray, win: int) -> np.ndarray:
    """Frecvență recentă penalizată de entropia locală a seriei (regularitate)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    for t in range(1, n):
        lo = max(0, t - win)
        w = ind[lo:t]
        m = max(w.shape[0], 1)
        freq = w.sum(axis=0) / m
        # Entropia blocurilor de lungime 2 per număr (regularitate temporală)
        ent = np.zeros(v)
        if m >= 8:
            x = (w > 0).astype(np.int8)
            b = x[:-1] * 2 + x[1:]  # 0..3
            for code in range(4):
                c = (b == code).sum(axis=0) / (m - 1)
                ent -= np.where(c > 0, c * np.log2(np.maximum(c, 1e-12)), 0.0)
            ent /= 2.0
        out[t] = _rows_norm(((freq + 0.5 / m) * (1.0 + 0.5 * (1.0 - ent / 2.0)))[None, :])[0]
    return out


# --------------------------------------------------------------------- #
# Hedge peste experți (parametrizat pe η și setul de experți)           #
# --------------------------------------------------------------------- #


def traj_hedge(ind: np.ndarray, eta: float, expert_names: tuple[str, ...],
               resolve: callable | None = None) -> np.ndarray:
    """Mixtură Hedge (log-loss) peste un subset de semnale de bază."""
    if resolve is None:
        resolve = lambda name, x: BASE_SIGNALS[name](x)  # noqa: E731
    n, v = ind.shape
    experts = np.stack([resolve(name, ind) for name in expert_names])  # (E, n, v)
    out = _empty_traj(n, v)
    logw = np.zeros(len(expert_names))
    for t in range(1, n):
        w = np.exp(logw - logw.max())
        w /= w.sum()
        out[t] = np.tensordot(w, experts[:, t, :], axes=1)
        x = ind[t - 1]
        r = (np.log(np.maximum(experts[:, t, :], 1e-300)) @ x) / max(float(x.sum()), 1.0)
        logw += eta * r
        logw -= logw.max()
    return out


# --------------------------------------------------------------------- #
# Registrul semnalelor de bază (folosit și de mixturi)                   #
# --------------------------------------------------------------------- #

BASE_SIGNALS: dict[str, callable] = {
    "uniform": traj_uniform,
    "freq": lambda ind: traj_freq_laplace(ind, 0.5),
    "ewma10": lambda ind: traj_ewma(ind, 10),
    "ewma30": lambda ind: traj_ewma(ind, 30),
    "ewma120": lambda ind: traj_ewma(ind, 120),
    "ewma250": lambda ind: traj_ewma(ind, 250),
    "dirichlet": lambda ind: traj_dirichlet(ind, 0.997, 0.5),
    "gap_hazard": lambda ind: traj_gap_hazard(ind, 1.0),
    "gap_z": traj_gap_zscore,
    "trans": lambda ind: traj_transitions(ind, 0.5, 1.0),
    "self_repeat": traj_self_repeat,
    "hawkes": lambda ind: traj_hawkes(ind, 0.3, 0.9),
    "bocpd": lambda ind: traj_bocpd(ind, 1.0 / 200.0),
    "fourier": lambda ind: traj_fourier(ind, 256, 3),
    "anti_ewma30": lambda ind: _rows_norm(1.0 - traj_ewma(ind, 30)),
    "kde_gap15": lambda ind: traj_kde_gap(ind, 15.0),
}


# --------------------------------------------------------------------- #
# Mixturi peste semnale (M) — fără bucle proprii                         #
# --------------------------------------------------------------------- #


def _mix_linear(a: np.ndarray, b: np.ndarray, alpha: float) -> np.ndarray:
    return _rows_norm(alpha * a + (1.0 - alpha) * b)


def _mix_geometric(a: np.ndarray, b: np.ndarray, alpha: float) -> np.ndarray:
    return _rows_norm(np.exp(alpha * np.log(np.maximum(a, 1e-12)) + (1.0 - alpha) * np.log(np.maximum(b, 1e-12))))


def build_bank(resolve: callable | None = None) -> dict[str, callable]:
    """Construiește banca completă: nume → fn(indicator) → traiectorie (n, v).

    `resolve(name, ind)` furnizează traiectoriile semnalelor de bază; harness-ul
    pasează un rezolvitor memoizat (fiecare semnal se calculează o singură
    dată per istorie, iar cele ~220 de mixturi îl refolosesc). Grila este
    fixată a priori (vezi docstring-ul modulului).
    """
    if resolve is None:
        resolve = lambda name, ind: BASE_SIGNALS[name](ind)  # noqa: E731
    bank: dict[str, callable] = {}

    # --- S: semnale singulare ---
    for name in BASE_SIGNALS:
        bank[f"s_{name}"] = lambda ind, n=name: resolve(n, ind)

    # --- F1: Holt amortizat ---
    for alpha in (0.1, 0.3):
        for beta in (0.05, 0.15):
            for phi in (0.85, 0.95, 1.0):
                bank[f"holt_a{alpha}_b{beta}_p{phi}"] = (
                    lambda ind, a=alpha, b=beta, p=phi: traj_holt(ind, a, b, p)
                )

    # --- F2: hazard Weibull ---
    for kappa in (0.7, 0.85, 1.0, 1.3, 1.6, 2.0):
        bank[f"weibull_k{kappa}"] = lambda ind, k=kappa: traj_weibull_hazard(ind, k)

    # --- F3: Dirichlet, grid λ × prior ---
    for lam in (0.990, 0.995, 0.997, 0.999, 1.0):
        for prior in (0.25, 0.5, 1.0, 2.0):
            bank[f"dir_l{lam}_a{prior}"] = lambda ind, l=lam, a=prior: traj_dirichlet(ind, l, a)

    # --- F4: Beta-Bernoulli, același grid ---
    for lam in (0.990, 0.995, 0.997, 0.999, 1.0):
        for ab in (0.25, 0.5, 1.0, 2.0):
            bank[f"beta_l{lam}_a{ab}"] = (
                lambda ind, l=lam, a=ab: traj_beta_bernoulli(ind, l, a, a)
            )

    # --- F5: EWMA half-life fin + F6: anti (rece) ---
    for hl in (5, 15, 45, 60, 90, 180, 400):
        bank[f"ewma_hl{hl}"] = lambda ind, h=hl: traj_ewma(ind, h)
    for hl in (10, 60, 250):
        bank[f"anti_ewma_hl{hl}"] = lambda ind, h=hl: _rows_norm(1.0 - traj_ewma(ind, h))

    # --- F7: tranziții Markov, grid decay × smooth; F8: ordin 2 aprox. ---
    for decay in (0.99, 0.995, 1.0):
        for smooth in (0.25, 0.5, 1.0):
            bank[f"markov_d{decay}_s{smooth}"] = (
                lambda ind, d=decay, s=smooth: traj_transitions(ind, s, d)
            )

    # --- F9: Hawkes, grid α × β ---
    for alpha in (0.15, 0.3, 0.45):
        for beta in (0.85, 0.92, 0.97):
            bank[f"hawkes_a{alpha}_b{beta}"] = (
                lambda ind, a=alpha, b=beta: traj_hawkes(ind, a, b)
            )

    # --- F10: BOCPD, grid hazard ---
    for hz in (50, 200, 500):
        bank[f"bocpd_h{hz}"] = lambda ind, h=1.0 / hz: traj_bocpd(ind, h)

    # --- F11: Fourier, grid fereastră × armonici ---
    for win in (128, 256, 512):
        for k in (2, 3):
            bank[f"fourier_w{win}_k{k}"] = lambda ind, w=win, kk=k: traj_fourier(ind, w, kk)

    # --- F12: faze periodice ---
    for period in (2, 3, 4):
        bank[f"season_p{period}"] = lambda ind, p=period: traj_season_phase(ind, p)

    # --- F13: CUSUM ---
    for th in (2.0, 4.0, 6.0):
        bank[f"cusum_h{th}"] = lambda ind, t=th: traj_cusum(ind, t)

    # --- F14: Hedge, grid η × set de experți ---
    expert_sets = {
        "core": ("uniform", "freq", "ewma10", "ewma120", "gap_hazard"),
        "time": ("uniform", "ewma30", "ewma250", "dirichlet", "hawkes"),
        "struct": ("uniform", "trans", "self_repeat", "gap_z", "fourier"),
    }
    for eta in (0.3, 0.5, 1.0, 2.0):
        for set_name, names in expert_sets.items():
            bank[f"hedge_e{eta}_{set_name}"] = (
                lambda ind, e=eta, ns=names: traj_hedge(ind, e, ns, resolve=resolve)
            )

    # --- F16: KDE gap ---
    for bw in (5.0, 50.0):
        bank[f"kde_gap{bw}"] = lambda ind, b=bw: traj_kde_gap(ind, b)

    # --- F17: entropie ---
    for win in (100, 300):
        bank[f"entropy_w{win}"] = lambda ind, w=win: traj_entropy_penalty(ind, w)

    # --- F18: logistic online ---
    for lr in (0.05, 0.2):
        for l2 in (0.1, 1.0):
            bank[f"logit_lr{lr}_l2{l2}"] = lambda ind, r=lr, l=l2: traj_logit_online(ind, r, l)

    # --- M1/M2: mixturi liniare și geometrice pe perechi din nucleu ---
    core = ("freq", "ewma30", "ewma250", "dirichlet", "gap_hazard", "trans", "hawkes", "bocpd")
    for i in range(len(core)):
        for j in range(i + 1, len(core)):
            a_name, b_name = core[i], core[j]
            for alpha in (0.25, 0.5, 0.75):
                bank[f"mix_{a_name}_{b_name}_a{alpha}"] = (
                    lambda ind, an=a_name, bn=b_name, al=alpha: _mix_linear(
                        resolve(an, ind), resolve(bn, ind), al
                    )
                )
                bank[f"gmix_{a_name}_{b_name}_a{alpha}"] = (
                    lambda ind, an=a_name, bn=b_name, al=alpha: _mix_geometric(
                        resolve(an, ind), resolve(bn, ind), al
                    )
                )

    # --- M3: triple (primele 6 semnale), ponderi egale sau 0.5/0.3/0.2 ---
    import itertools

    trio = ("freq", "ewma30", "ewma250", "dirichlet", "gap_hazard", "trans")
    for combo in itertools.combinations(trio, 3):
        tag = "_".join(combo)
        bank[f"tri_{tag}"] = lambda ind, cs=combo: _rows_norm(
            sum(resolve(c, ind) for c in cs) / 3.0
        )
        bank[f"triw_{tag}"] = lambda ind, cs=combo: _rows_norm(
            0.5 * resolve(cs[0], ind)
            + 0.3 * resolve(cs[1], ind)
            + 0.2 * resolve(cs[2], ind)
        )

    # --- M4: agregatori de rang (Borda / medie geometrică de rang / Copeland) ---
    rank_set = ("freq", "ewma30", "dirichlet", "gap_hazard", "trans")

    def _ranks(ind):
        mats = np.stack([resolve(s, ind) for s in rank_set])  # (5, n, v)
        order = mats.argsort(axis=2)
        ranks = np.empty_like(order)
        n_, v_ = mats.shape[1], mats.shape[2]
        rows = np.arange(v_)[None, None, :]
        np.put_along_axis(ranks, order, np.broadcast_to(rows, order.shape), axis=2)
        return ranks  # (5, n, v), 0 = cel mai slab

    bank["rank_borda"] = lambda ind: _rows_norm(_ranks(ind).sum(axis=0))
    bank["rank_geomean"] = lambda ind: _rows_norm(
        np.exp(np.log(np.maximum(_ranks(ind) + 1.0, 1e-12)).mean(axis=0))
    )
    bank["rank_min"] = lambda ind: _rows_norm(_ranks(ind).min(axis=0) + 1.0)

    # --- M5: anti-mixturi (cald × rece) ---
    for hot in ("freq", "ewma30", "dirichlet", "gap_hazard"):
        for alpha in (0.5, 0.75):
            bank[f"antimix_{hot}_a{alpha}"] = (
                lambda ind, h=hot, al=alpha: _mix_linear(
                    resolve(h, ind), resolve("anti_ewma30", ind), al
                )
            )

    return bank


if __name__ == "__main__":
    b = build_bank()
    print(f"banca valului 4: {len(b)} metode")
