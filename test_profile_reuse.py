"""Reluarea „Bilet complet” pe pașii WF: profilul exact se refolosește între pași.

Profilul unui wheel (`wheel_hit_profile`) nu depinde de etichete. La pool 16,
pașii WF dau aceleași geometrii în pozițiile clasamentului (candidații
poziționali ai hitcover, biletele recoacerii), dar cu alte numere. Măștile pe
etichete sortate le făceau diferite la fiecare pas, iar `_hit_counts`
(~0,3 s în Python la 16 numere) se recalcula de 6-8 ori pe pas; reluarea din
„📜 Istoric hits” ajungea la 16-20 de minute pe configurația pool 16.
"""

from covering.probability import _hit_counts, wheel_hit_probabilities, wheel_hit_profile


def _relabel():
    pool_a = list(range(1, 17))
    # Scoruri distincte, clasament amestecat față de etichete.
    scores_a = {n: 1.0 + 0.01 * ((n * 7) % 17) for n in pool_a}
    phi = {n: 40 - n for n in pool_a}  # inversează ordinea etichetelor
    pool_b = sorted(phi.values())
    scores_b = {phi[n]: s for n, s in scores_a.items()}
    return pool_a, scores_a, pool_b, scores_b, phi


def test_profile_of_a_relabeled_wheel_reuses_the_memo():
    from covering.common import _sorted_pool

    pool_a, scores_a, pool_b, scores_b, phi = _relabel()
    ranked_a = _sorted_pool(pool_a, scores_a)
    ranked_b = _sorted_pool(pool_b, scores_b)
    tickets_a = [ranked_a[i : i + 6] for i in (0, 3, 6, 9)]
    tickets_b = [[phi[n] for n in t] for t in tickets_a]
    _hit_counts.cache_clear()
    pa = wheel_hit_profile(ranked_a, tickets_a)
    misses = _hit_counts.cache_info().misses
    pb = wheel_hit_profile(ranked_b, tickets_b)
    assert pa == pb
    assert _hit_counts.cache_info().misses == misses
    qa = wheel_hit_probabilities(ranked_a, tickets_a, 6, 49)
    qb = wheel_hit_probabilities(ranked_b, tickets_b, 6, 49)
    assert qa == qb
    # Ordinea dată nu schimbă numărătorile: aceleași valori ca pe etichete sortate.
    assert wheel_hit_profile(sorted(pool_a), tickets_a) == pa


def test_hitcover_reuses_candidate_profiles_across_relabeled_steps():
    from budget_cover import wheel_hitcover

    pool_a, scores_a, pool_b, scores_b, phi = _relabel()
    _hit_counts.cache_clear()
    wa, cov_a = wheel_hitcover(pool_a, 6, 4, 9, scores_a)
    first = _hit_counts.cache_info().misses
    wb, cov_b = wheel_hitcover(pool_b, 6, 4, 9, scores_b)
    # Același wheel, renumerotat; niciun profil nou de calculat.
    assert sorted(sorted(phi[n] for n in t) for t in wa) == sorted(sorted(t) for t in wb)
    assert cov_a == cov_b
    assert first >= 2
    assert _hit_counts.cache_info().misses == first
