"""Teste explicite pentru bugete imposibile și raportarea acoperirii parțiale
(AGENTS.md §12 P1 „acoperire și cost").

Contracte:
- un buget care nu poate acoperi garanția raportează acoperire PARȚIALĂ reală,
  niciodată 100.0 (clamp-ul 99.99 din covering.common._coverage_ratio_pct);
- wheel gol (pool < pick) → ([], 0.0), nu 100.0 și nu excepție;
- guarantee > pick → ValueError explicit (nu eroare din itertools);
- condition < guarantee → ValueError; condition > guarantee → ramura lotto;
- max_variants negativ/0 = fără plafon;
- pool == pick cu 1 bilet = sistem complet pe acel pool (acoperire 100).
"""

from __future__ import annotations

import pytest

from covering.common import _coverage_ratio_pct, compute_coverage_pct
from covering.dispatch import generate_wheel


def test_partial_coverage_never_rounds_up_to_100():
    """53129/53130 tinte acoperite (lipsește exact una) → 99.99, nu 100.0."""
    assert _coverage_ratio_pct(53129, 53130) == 99.99
    assert _coverage_ratio_pct(100, 100) == 100.0  # completul rămâne exact 100.0
    assert _coverage_ratio_pct(0, 0) == 0.0  # fără ținte nu există garanție îndeplinită
    assert _coverage_ratio_pct(1, 3) == pytest.approx(round(1 / 3 * 100, 2))


def test_impossible_budget_reports_partial_coverage_not_100():
    """1 bilet pe pool 16, pick 6, g4 nu poate acoperi C(16,4)=1820 de grupuri."""
    pool = list(range(1, 17))
    wheel, cov = generate_wheel("greedy", pool, 6, 4, 1, None)
    assert len(wheel) == 1
    assert 0.0 < cov < 100.0
    assert cov == compute_coverage_pct(wheel, pool, 4)
    # și prin API-ul de recalculare: același procent parțial, rotunjit la 2 zecimale
    assert compute_coverage_pct(wheel, pool, 4) < 100.0


def test_small_budget_coverage_is_reported_exactly():
    """Buget 10 pe pool 16 g4: acoperirea raportată e cea reală, măsurată independent."""
    from itertools import combinations

    pool = list(range(1, 17))
    wheel, cov = generate_wheel("greedy", pool, 6, 4, 10, None)
    assert len(wheel) == 10
    targets = set(combinations(pool, 4))
    covered = set()
    for t in wheel:
        covered.update(combinations(t, 4))
    expected = round(len(covered & targets) / len(targets) * 100, 2)
    if len(covered & targets) < len(targets) and expected >= 100.0:
        expected = 99.99
    assert cov == expected
    assert cov < 100.0


def test_empty_wheel_when_pool_smaller_than_pick():
    wheel, cov = generate_wheel("lajolla", [4, 8, 15], 6, 4, 0, None)
    assert wheel == [] and cov == 0.0
    assert compute_coverage_pct([], [4, 8, 15], 4) == 0.0


def test_guarantee_above_pick_raises_clear_error():
    with pytest.raises(ValueError, match="nu poate dep"):
        generate_wheel("greedy", list(range(1, 11)), 6, 7, 0, None)


def test_condition_below_guarantee_raises():
    with pytest.raises(ValueError, match="condition"):
        generate_wheel("greedy", list(range(1, 13)), 6, 4, 0, None, condition=3)


def test_condition_above_guarantee_uses_lotto_design():
    """condition > guarantee comută pe designul lotto „g dacă c” (mai ieftin)."""
    pool = list(range(1, 12))
    wheel, cov = generate_wheel("greedy", pool, 5, 3, 0, None, condition=4)
    assert wheel, "lotto design gol"
    assert 0.0 < cov <= 100.0
    # „3 dacă 4” pe 11 numere: fiecare 4-submulțime are un bilet cu ≥3 comune
    from itertools import combinations

    tsets = [set(t) for t in wheel]
    for sub in combinations(pool, 4):
        assert any(len(set(sub) & ts) >= 3 for ts in tsets)


def test_negative_and_zero_max_variants_mean_no_cap():
    pool = list(range(1, 13))
    w0, c0 = generate_wheel("greedy", pool, 6, 4, 0, None)
    wneg, cneg = generate_wheel("greedy", pool, 6, 4, -5, None)
    assert w0 == wneg and c0 == cneg == 100.0


def test_pool_equals_pick_single_ticket_is_complete_system():
    pool = list(range(1, 7))
    wheel, cov = generate_wheel("greedy", pool, 6, 6, 1, None)
    assert wheel == [pool] or wheel == [sorted(pool)]
    assert cov == 100.0


def test_capped_complete_system_reports_partial():
    """g==pick cu plafon sub C(v,pick): acoperire parțială raportată, nu 100."""
    from math import comb

    pool = list(range(1, 9))  # C(8,5)=56
    wheel, cov = generate_wheel("greedy", pool, 5, 5, 10, None)
    assert len(wheel) == 10
    assert cov == pytest.approx(round(comb(5, 5) * 10 / comb(8, 5) * 100, 2)) or cov < 100.0
    assert cov < 100.0
