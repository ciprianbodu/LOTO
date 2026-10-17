"""Ecran geometric / matematic pentru hituri în pool, 2026-10-05.

Protocol blocat înainte de citirea confirmării:

- țintă primară: România 6/49, pool 12, eveniment = cel puțin 3 numere în pool;
- warmup: 200 de zile; dezvoltare = primele 70% din zilele de după warmup;
  confirmare = ultimele 30%. Aceeași zi nu intră în scor (cutoff pe zi);
- sute de scoreri: netezire spațială (linie, cerc, grilă 7×7, modulare),
  găuri spațiale, centroid, pantă, rafală, zi a săptămânii, tercile de sumă
  și de amplitudine. Toți văd numai zilele anterioare;
- baseline pereche: `frequency` din producție (exp linspace -2..0 pe prefix);
- un candidat cu egalitate pe locul K în >50% din zilele de dezvoltare iese
  (depinde de tie-break);
- selecția se face NUMAI pe dezvoltare. Testul nul al căutării: 40 de
  permutări globale ale etichetelor; se păstrează maximul de lift al tuturor
  candidaților. Dacă liftul real nu depășește toate maximele nule, nu există
  confirmare și nu se promovează nimic;
- dacă trece: confirmare blocată pe (1) ultimele 30% românești vs frequency,
  McNemar unilateral, (2) aceleași formule, fără re-acordare, pe 6/49 externe
  concatenate, (3) România 5/40 pool 10, țintă 4+, ultimele 30%. Holm pe cele
  trei. Promovare numai dacă toate trei au p ajustat < 0.05 și lift pozitiv.
"""

from __future__ import annotations

import json
import sys
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from loto_enterprise.benchmark.methods import score_frequency  # noqa: E402

WARMUP_DAYS = 200
DEV_FRACTION = 0.70
POOL_649 = 12
HIT_649 = 3
POOL_540 = 10
HIT_540 = 4
N_PERM = 40
ALPHAS = (0.03, 0.08, 0.15, 0.30)
SIGMAS = (1.2, 2.0, 3.5, 6.0)
RNG = np.random.default_rng(20261005)


def _load(path: Path, max_num: int, draw_n: int):
    df = pd.read_csv(path)
    dates = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    cols = [f"n{i}" for i in range(1, draw_n + 1)]
    nums = df[cols].apply(pd.to_numeric, errors="coerce")
    ok = dates.notna() & nums.notna().all(axis=1)
    nums = nums.loc[ok].astype(int)
    dates = dates.loc[ok]
    valid = (
        (nums.min(axis=1) >= 1)
        & (nums.max(axis=1) <= max_num)
        & (nums.nunique(axis=1) == draw_n)
    )
    nums = nums.loc[valid]
    dates = dates.loc[valid]
    order = np.argsort(dates.to_numpy(), kind="mergesort")
    dates = dates.to_numpy()[order]
    draws = nums.to_numpy()[order]
    day = np.empty(len(draws), dtype=np.int32)
    day[0] = 0
    for i in range(1, len(draws)):
        day[i] = day[i - 1] + int(dates[i] != dates[i - 1])
    return dates, draws, day


def _day_counts(draws: np.ndarray, day: np.ndarray, max_num: int) -> np.ndarray:
    n_days = int(day[-1]) + 1
    out = np.zeros((n_days, max_num), dtype=np.float32)
    rows = day
    cols = draws.ravel() - 1
    np.add.at(out, (np.repeat(rows, draws.shape[1]), cols), 1.0)
    return out


def _ewma_states(counts: np.ndarray, alpha: float) -> np.ndarray:
    """Stare văzută LA ÎNCEPUTUL zilei (fără ziua însăși)."""
    n_days, m = counts.shape
    state = np.zeros((n_days, m), dtype=np.float32)
    acc = np.zeros(m, dtype=np.float32)
    a = np.float32(alpha)
    for d in range(n_days - 1):
        acc = (1.0 - a) * acc + a * counts[d]
        state[d + 1] = acc
    return state


def _kernel_line(m: int, sigma: float, circular: bool) -> np.ndarray:
    x = np.arange(m, dtype=np.float32)
    d = np.abs(x[:, None] - x[None, :])
    if circular:
        d = np.minimum(d, m - d)
    k = np.exp(-(d ** 2) / (2.0 * np.float32(sigma) ** 2)).astype(np.float32)
    k /= k.sum(axis=0, keepdims=True)
    return k


def _grid_coords(m: int) -> np.ndarray:
    cols = 7 if m == 49 else 8 if m == 40 else int(np.ceil(np.sqrt(m)))
    idx = np.arange(m)
    return np.stack((idx // cols, idx % cols), axis=1).astype(np.float32)


def _kernel_grid(m: int, sigma: float) -> np.ndarray:
    xy = _grid_coords(m)
    d = xy[:, None, :] - xy[None, :, :]
    dist2 = (d ** 2).sum(axis=2)
    k = np.exp(-dist2 / (2.0 * np.float32(sigma) ** 2)).astype(np.float32)
    k /= k.sum(axis=0, keepdims=True)
    return k


def _kernel_modulo(m: int, modulus: int, sigma: float) -> np.ndarray:
    r = np.arange(m) % modulus
    d = np.abs(r[:, None] - r[None, :]).astype(np.float32)
    d = np.minimum(d, modulus - d)
    k = np.exp(-(d ** 2) / (2.0 * np.float32(sigma) ** 2)).astype(np.float32)
    k /= k.sum(axis=0, keepdims=True)
    return k


def _build_kernels(m: int) -> list[tuple[str, np.ndarray]]:
    kernels: list[tuple[str, np.ndarray]] = []
    for sigma in SIGMAS:
        kernels.append((f"line_s{sigma}", _kernel_line(m, sigma, False)))
        kernels.append((f"circle_s{sigma}", _kernel_line(m, sigma, True)))
        kernels.append((f"grid_s{sigma}", _kernel_grid(m, sigma)))
        kernels.append((f"mod7_s{sigma}", _kernel_modulo(m, 7, sigma)))
        kernels.append((f"mod10_s{sigma}", _kernel_modulo(m, 10, sigma)))
    ident = np.eye(m, dtype=np.float32)
    kernels.append(("identity", ident))
    return kernels


def _topk_hits(scores: np.ndarray, draws: np.ndarray, k: int, threshold: int) -> np.ndarray:
    """scores: (n_rows, m). Hit binar per rând, tie-break număr mare."""
    m = scores.shape[1]
    numbers = np.arange(1, m + 1, dtype=np.int16)
    # lexsort: ultima cheie e primară. Scor desc, apoi număr desc.
    order = np.lexsort((-numbers[None, :] + np.zeros((len(scores), 1)), -scores), axis=1)
    top = order[:, :k]
    present = np.zeros(scores.shape, dtype=bool)
    present[np.arange(len(draws))[:, None], draws - 1] = True
    got = present[np.arange(len(draws))[:, None], top].sum(axis=1)
    tie = scores[np.arange(len(scores)), order[:, k - 1]] == scores[
        np.arange(len(scores)), order[:, k]
    ]
    return got >= threshold, tie, got


def _frequency_scores(draws: np.ndarray, day: np.ndarray, max_num: int) -> np.ndarray:
    """Scorul `frequency` de producție, constant pe extragerile aceleiași zile."""
    n = len(draws)
    out = np.zeros((n, max_num), dtype=np.float32)
    # Un apel pe zi, nu pe rând.
    starts = np.flatnonzero(np.diff(day, prepend=-1))
    for s in starts:
        hist = draws[:s]
        raw = score_frequency(hist if len(hist) else np.zeros((0, draws.shape[1]), dtype=int), max_num)
        out[s : s + int(np.sum(day == day[s]))] = [raw[i] for i in range(1, max_num + 1)]
    return out


def _math_scores(counts: np.ndarray, dates_day: np.ndarray) -> dict[str, np.ndarray]:
    """Scoruri matematice (zile × numere), stare la începutul zilei."""
    n_days, m = counts.shape
    out: dict[str, np.ndarray] = {}
    # pantă pe 4 blocuri
    for window in (80, 160, 320):
        slope = np.zeros((n_days, m), dtype=np.float32)
        x = np.arange(4, dtype=np.float32)
        x = x - x.mean()
        denom = float((x ** 2).sum())
        block = window // 4
        csum = np.vstack([np.zeros((1, m), dtype=np.float32), np.cumsum(counts, axis=0)])
        for d in range(window, n_days):
            rates = []
            for b in range(4):
                a = d - window + b * block
                z = a + block
                rates.append((csum[z] - csum[a]) / block)
            rates = np.stack(rates, axis=0)
            slope[d] = (x[:, None] * (rates - rates.mean(axis=0))).sum(axis=0) / denom
        out[f"slope_w{window}"] = slope
    # rafală scurtă minus lungă
    csum = np.vstack([np.zeros((1, m), dtype=np.float32), np.cumsum(counts, axis=0)])
    for short, long in ((10, 60), (20, 120), (30, 200)):
        burst = np.zeros((n_days, m), dtype=np.float32)
        for d in range(long, n_days):
            burst[d] = (csum[d] - csum[d - short]) / short - (csum[d] - csum[d - long]) / long
        out[f"burst_{short}_{long}"] = burst
    # centroid pe linia numerelor și complementul (gaură față de nor)
    idx = np.arange(1, m + 1, dtype=np.float32)
    for window in (8, 20, 50):
        center = np.zeros((n_days, m), dtype=np.float32)
        hole = np.zeros((n_days, m), dtype=np.float32)
        for d in range(window, n_days):
            mass = counts[d - window : d].sum(axis=0)
            total = float(mass.sum()) + 1e-6
            mu = float((mass * idx).sum() / total)
            var = float((mass * (idx - mu) ** 2).sum() / total) + 1.0
            z = -((idx - mu) ** 2) / var
            center[d] = z
            hole[d] = -z
        out[f"centroid_w{window}"] = center
        out[f"hole_w{window}"] = hole
    # ziua săptămânii, cu contracție
    wd = pd.DatetimeIndex(dates_day).dayofweek.to_numpy()
    for prior in (8.0, 30.0):
        score = np.zeros((n_days, m), dtype=np.float32)
        hit = np.zeros((7, m), dtype=np.float32)
        seen = np.zeros(7, dtype=np.float32)
        base = np.float32(counts.shape[0] and 6.0 / m)
        for d in range(n_days):
            w = int(wd[d])
            score[d] = (hit[w] + prior * base) / (seen[w] + prior)
            hit[w] += counts[d]
            seen[w] += 1.0
        out[f"weekday_p{int(prior)}"] = score
    # tercile de sumă și de amplitudine ale zilei precedente
    # suma zilei = suma numerelor apărute (cu multiplicitate dacă sunt 2 extrageri)
    # Folosim media extragerilor din zi, reconstruită din counts nu e suma.
    return out


def _day_draw_features(draws: np.ndarray, day: np.ndarray, max_num: int):
    n_days = int(day[-1]) + 1
    sum_ = np.zeros(n_days, dtype=np.float32)
    span = np.zeros(n_days, dtype=np.float32)
    n_in = np.zeros(n_days, dtype=np.float32)
    for i, d in enumerate(day):
        row = draws[i]
        sum_[d] += float(row.sum())
        span[d] += float(row.max() - row.min())
        n_in[d] += 1.0
    return sum_ / np.maximum(n_in, 1.0), span / np.maximum(n_in, 1.0)


def _tercile_scores(counts: np.ndarray, feature: np.ndarray) -> np.ndarray:
    """Frecvență condiționată de tercila caracteristicii zilei precedente."""
    n_days, m = counts.shape
    score = np.zeros((n_days, m), dtype=np.float32)
    buckets = np.zeros((3, m), dtype=np.float32)
    seen = np.zeros(3, dtype=np.float32)
    hist: list[float] = []
    prev_bin = 1
    for d in range(n_days):
        score[d] = (buckets[prev_bin] + 2.0) / (seen[prev_bin] + 2.0)
        hist.append(float(feature[d]))
        if len(hist) >= 30:
            q1, q2 = np.quantile(hist, [1 / 3, 2 / 3])
            val = hist[-1]
            prev_bin = 0 if val <= q1 else 2 if val > q2 else 1
        buckets[prev_bin] += counts[d]
        seen[prev_bin] += 1.0
    return score


def _candidate_bank(states: dict[str, np.ndarray], kernels, math_scores) -> dict[str, np.ndarray]:
    """Toate scorurile la nivel de ZI. Cheie stabilă."""
    bank: dict[str, np.ndarray] = {}
    for aname, state in states.items():
        for kname, kernel in kernels:
            bank[f"{aname}__{kname}"] = state @ kernel
            bank[f"{aname}__anti_{kname}"] = -(state @ kernel)
        # blend fix cu identitatea (frecvența EWMA), fără alegere pe confirmare
        ident = state
        for kname, kernel in kernels:
            if kname == "identity":
                continue
            smoothed = state @ kernel
            for w in (0.35, 0.7):
                bank[f"{aname}__mix{w}_{kname}"] = w * smoothed + (1.0 - w) * ident
    bank.update(math_scores)
    return bank


def _rows_from_days(day_scores: np.ndarray, day: np.ndarray) -> np.ndarray:
    return day_scores[day]


def _mcnemar(win: np.ndarray, lose: np.ndarray) -> dict:
    b = int(np.sum(win & ~lose))
    c = int(np.sum(~win & lose))
    n = b + c
    if n == 0:
        p = 1.0
    else:
        p = float(binomtest(b, n, 0.5, alternative="greater").pvalue)
    return {"method_only": b, "baseline_only": c, "p": p, "lift_rows": int(win.sum() - lose.sum())}


def _hyp_p(max_num: int, draw_n: int, pool: int, threshold: int) -> float:
    total = comb(max_num, draw_n)
    lo = threshold
    hits = sum(comb(pool, h) * comb(max_num - pool, draw_n - h) for h in range(lo, min(pool, draw_n) + 1))
    return hits / total


def _screen_game(name: str, path: Path, max_num: int, draw_n: int, pool: int, threshold: int, confirm: bool):
    dates, draws, day = _load(path, max_num, draw_n)
    counts = _day_counts(draws, day, max_num)
    n_days = counts.shape[0]
    day_dates = pd.Series(dates).groupby(day).min().to_numpy()
    print(f"[{name}] rows={len(draws)} days={n_days} {dates[0]}..{dates[-1]}", flush=True)

    kernels = _build_kernels(max_num)
    states = {f"a{a}": _ewma_states(counts, a) for a in ALPHAS}
    math_scores = _math_scores(counts, day_dates)
    sums, spans = _day_draw_features(draws, day, max_num)
    math_scores["tercile_sum"] = _tercile_scores(counts, sums)
    math_scores["tercile_span"] = _tercile_scores(counts, spans)
    bank = _candidate_bank(states, kernels, math_scores)
    print(f"[{name}] candidates={len(bank)}", flush=True)

    freq = _frequency_scores(draws, day, max_num)
    freq_hit, _, _ = _topk_hits(freq, draws, pool, threshold)

    cut_day = WARMUP_DAYS + int(DEV_FRACTION * (n_days - WARMUP_DAYS))
    dev_rows = (day >= WARMUP_DAYS) & (day < cut_day)
    conf_rows = day >= cut_day
    mid = WARMUP_DAYS + (cut_day - WARMUP_DAYS) // 2
    dev1 = (day >= WARMUP_DAYS) & (day < mid)
    dev2 = (day >= mid) & (day < cut_day)

    rows = []
    hit_cache = {}
    for cname, day_scores in bank.items():
        scores = _rows_from_days(day_scores, day)
        hit, tie, _ = _topk_hits(scores, draws, pool, threshold)
        hit_cache[cname] = hit
        if dev_rows.sum() == 0:
            continue
        tie_rate = float(tie[dev_rows].mean())
        paired = _mcnemar(hit[dev_rows], freq_hit[dev_rows])
        lift1 = int(hit[dev1].sum() - freq_hit[dev1].sum())
        lift2 = int(hit[dev2].sum() - freq_hit[dev2].sum())
        rows.append(
            {
                "name": cname,
                "tie_rate": tie_rate,
                "dev_rate": float(hit[dev_rows].mean()),
                "freq_dev_rate": float(freq_hit[dev_rows].mean()),
                "lift_rows": paired["lift_rows"],
                "p_dev": paired["p"],
                "stable": lift1 > 0 and lift2 > 0 and tie_rate <= 0.50,
                "lift1": lift1,
                "lift2": lift2,
            }
        )
    table = pd.DataFrame(rows).sort_values(["stable", "lift_rows", "p_dev"], ascending=[False, False, True])
    stable = table[table["stable"]]
    winner_name = None if stable.empty else str(stable.iloc[0]["name"])

    # Nulul maxim: permutări de etichete, numai dacă există un stabil.
    perm_max = []
    if winner_name is not None:
        base_lift = int(stable.iloc[0]["lift_rows"])
        print(f"[{name}] dev winner {winner_name} lift_rows={base_lift} tie={stable.iloc[0]['tie_rate']:.2f}", flush=True)
        for p_i in range(N_PERM):
            perm = RNG.permutation(max_num)
            inv = np.empty_like(perm)
            inv[perm] = np.arange(max_num)
            counts_p = counts[:, perm]
            states_p = {f"a{a}": _ewma_states(counts_p, a) for a in ALPHAS}
            draws_p = inv[draws - 1] + 1
            sums_p, spans_p = _day_draw_features(draws_p, day, max_num)
            math_p = _math_scores(counts_p, day_dates)
            math_p["tercile_sum"] = _tercile_scores(counts_p, sums_p)
            math_p["tercile_span"] = _tercile_scores(counts_p, spans_p)
            bank_p = _candidate_bank(states_p, kernels, math_p)
            freq_p = freq[:, perm]
            freq_hit_p, _, _ = _topk_hits(freq_p, draws_p, pool, threshold)
            best = -10**9
            for cname, day_scores in bank_p.items():
                hit, tie, _ = _topk_hits(day_scores[day], draws_p, pool, threshold)
                if float(tie[dev_rows].mean()) > 0.50:
                    continue
                lift = int(hit[dev_rows].sum() - freq_hit_p[dev_rows].sum())
                if lift > best:
                    best = lift
            perm_max.append(best)
            print(f"[{name}] perm {p_i + 1}/{N_PERM} max_lift={best}", flush=True)
        survived = base_lift > max(perm_max)
    else:
        survived = False
        base_lift = None

    result = {
        "game": name,
        "rows": int(len(draws)),
        "days": int(n_days),
        "candidates": int(len(bank)),
        "stable_candidates": int(len(stable)),
        "dev_rows": int(dev_rows.sum()),
        "confirm_rows": int(conf_rows.sum()),
        "freq_dev_rate": float(freq_hit[dev_rows].mean()) if dev_rows.any() else None,
        "freq_confirm_rate": float(freq_hit[conf_rows].mean()) if conf_rows.any() else None,
        "hypergeometric": _hyp_p(max_num, draw_n, pool, threshold),
        "winner": winner_name,
        "winner_dev_lift_rows": base_lift,
        "perm_max_lifts": perm_max,
        "survived_max_null": bool(survived),
        "top10": table.head(10).to_dict(orient="records"),
    }
    if confirm and survived and winner_name is not None:
        hit = hit_cache[winner_name]
        conf = _mcnemar(hit[conf_rows], freq_hit[conf_rows])
        result["confirm_vs_frequency"] = conf
        result["confirm_rate"] = float(hit[conf_rows].mean())
        k = int(hit[conf_rows].sum())
        n = int(conf_rows.sum())
        p0 = result["hypergeometric"]
        result["confirm_vs_hyper"] = {
            "hits": k,
            "n": n,
            "rate": k / n,
            "p": float(binomtest(k, n, p0, alternative="greater").pvalue),
        }
    return result


def _holm(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m, dtype=float)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvals[idx])
        adj[idx] = min(1.0, running)
    # monotone enforcement from the worst back
    ordered_adj = adj[order]
    for i in range(m - 2, -1, -1):
        ordered_adj[i] = min(ordered_adj[i], ordered_adj[i + 1])
    adj[order] = ordered_adj
    return [float(x) for x in adj]


def main() -> None:
    primary = _screen_game(
        "ro_649",
        ROOT / "_ISTORIC" / "loto_6_49.csv",
        49,
        6,
        POOL_649,
        HIT_649,
        confirm=True,
    )
    extra = []
    if primary["survived_max_null"]:
        # aceleași familii, fără a re-alege câștigătorul: se reevaluează DOAR formula câștigătoare
        # Re-rularea completă ar re-deschide selecția. Confirmările externe sunt în _screen
        # doar pentru formula blocată — le calculăm separat mai jos, din numele câștigătorului.
        print("WINNER_LOCKED", primary["winner"], flush=True)
    out = {
        "protocol": {
            "primary": "RO 6/49 pool 12 hit>=3",
            "warmup_days": WARMUP_DAYS,
            "dev_fraction": DEV_FRACTION,
            "permutations": N_PERM,
            "promote_rule": "survives max-null on dev AND Holm<0.05 on RO confirm, external 6/49, RO 5/40",
        },
        "primary": primary,
        "external": extra,
    }
    dest = ROOT / "scripts" / "analysis" / "spatial_math_screen_2026-10-05.json"
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: primary[k] for k in (
        "candidates", "stable_candidates", "winner", "winner_dev_lift_rows",
        "survived_max_null", "freq_dev_rate", "hypergeometric",
    )}, indent=2))


if __name__ == "__main__":
    main()
