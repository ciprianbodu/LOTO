"""Hypergeometric hit forecast — extracted from loto_engine."""

from __future__ import annotations

def hypergeometric_hit_forecast(
    pool_size: int, draw_n: int, max_n: int, n_draws: int = 127
) -> dict:
    """
    Calculează probabilitatea teoretică P(k+ hits) pentru un pool RANDOM și
    recomandă pool-ul minim necesar pentru a vedea ≥3 evenimente pe ținta
    principală (3+) și pe 4+/5+.

    Baseline matematic de CALIBRARE, nu prag predictiv: într-o loterie aleatoare,
    orice pool fix de aceeași mărime are aceeași probabilitate teoretică. Un
    scorer poate devia pe eșantionul istoric doar prin zgomot; pârghiile reale
    pentru P(3+) sunt mărimea pool-ului și acoperirea pool→bilete. Pe 4+/5+ la
    pool mic evenimentele așteptate sunt rare.

    Returns dict cu:
        - random_baseline.P(k+)% per k=1..draw_n
        - recommendations.pool_for_3_events_{3,4,5}+
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
    for target_k in (3, 4, 5):
        if target_k > draw_n:
            continue
        rec_pool = None
        for trial_pool in range(pool_size, min(max_n, pool_size + 20) + 1):
            p_k = sum(
                comb(trial_pool, j)
                * comb(max_n - trial_pool, draw_n - j)
                / total_combos
                for j in range(target_k, draw_n + 1)
            )
            if p_k * n_draws >= target_events:
                rec_pool = trial_pool
                break
        recommendations[f"pool_for_3_events_{target_k}+"] = rec_pool
    forecast["recommendations"] = recommendations
    return forecast
