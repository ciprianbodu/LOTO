"""Independent mathematical and leakage checks for the offline evidence screen."""
import itertools
import math
from collections import Counter

import numpy as np
import pytest
from scipy.stats import binomtest

from scripts.analysis import hit_evidence_2026_11_04 as screen

GEOMETRIES = [
    (universe, draw_n, pool)
    for universe in range(4, 13)
    for draw_n in range(1, min(6, universe) + 1)
    for pool in range(universe + 1)
]


@pytest.mark.parametrize("universe,draw_n,pool", GEOMETRIES)
def test_hypergeometric_matches_every_uniform_draw(universe, draw_n, pool):
    """Set-intersection enumeration is independent of the hypergeometric formula."""
    counts = Counter(
        sum(number < pool for number in draw)
        for draw in itertools.combinations(range(universe), draw_n)
    )
    total = math.comb(universe, draw_n)
    for threshold in range(draw_n + 2):
        expected = sum(count for hits, count in counts.items() if hits >= threshold) / total
        assert screen.hypergeom_tail(
            universe, draw_n, pool, threshold
        ) == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize("permutation", list(itertools.permutations(range(4))))
def test_holm_known_values_and_order_invariance(permutation):
    values = [0.001, 0.01, 0.04, 0.2]
    expected = [0.004, 0.03, 0.08, 0.2]
    assert screen.holm([values[i] for i in permutation]) == pytest.approx(
        [expected[i] for i in permutation]
    )


@pytest.mark.parametrize("p_values", [[-0.01], [1.01], [float("nan")], [float("inf")]])
def test_holm_rejects_invalid_values(p_values):
    with pytest.raises(ValueError):
        screen.holm(p_values)


def test_holm_empty_family():
    assert screen.holm([]) == []


@pytest.mark.parametrize(
    "successes,total",
    [(0, 1), (1, 1), (0, 50), (1, 50), (25, 50), (49, 50), (50, 50), (120, 1000)],
)
def test_confidence_interval_matches_independent_scipy_api(successes, total):
    expected = binomtest(successes, total).proportion_ci(method="exact")
    assert screen.confidence_interval(successes, total) == pytest.approx(
        [expected.low, expected.high], abs=2e-12
    )


@pytest.mark.parametrize("method_index", range(len(screen.METHODS)), ids=screen.METHODS)
def test_scores_exclude_target_day_and_all_future_rows(monkeypatch, method_index):
    rng = np.random.default_rng(410)
    original = np.array([rng.choice(np.arange(1, 15), size=5, replace=False) for _ in range(24)])
    altered = original.copy()
    # Change the first draw of a two-draw day and every future row. Scores for
    # both draws on that day must remain identical, even to the second draw.
    altered[12:] = [rng.choice(np.arange(1, 15), size=5, replace=False) for _ in range(12)]
    dates = np.repeat(np.arange(12), 2)
    captured = []
    actual_score_grid = screen.score_grid

    def recorder(*args):
        vectors = actual_score_grid(*args)
        captured.append((args[2], np.asarray(vectors[method_index]).copy()))
        return vectors

    monkeypatch.setattr(screen, "score_grid", recorder)
    screen.walk_forward_hits(original, dates, 14, (5, 10), warmup=6)
    before = dict(captured)
    captured.clear()
    screen.walk_forward_hits(altered, dates, 14, (5, 10), warmup=6)
    after = dict(captured)
    assert before.keys() == after.keys()
    assert 13 not in before  # both draws of the date share one pre-date score
    for index in before:
        if index <= 12:
            np.testing.assert_array_equal(before[index], after[index])


def test_540_reads_six_balls_and_pool_six_is_not_ticket_six():
    assert screen.GAMES["loto_5_40"][1] == 6
    assert 6 in screen.GAMES["loto_5_40"][3]
    assert 6 not in screen.GAMES["joker"][3]
    assert screen.hypergeom_tail(40, 6, 5, 6) == 0


@pytest.mark.parametrize("candidate,reference,expected", [
    ([True, True, False, False], [False, False, True, False], (2, 1, 0.5)),
    ([True, True], [True, True], (0, 0, 1.0)),
    ([True] * 5, [False] * 5, (5, 0, 1 / 32)),
])
def test_paired_exact_test_uses_only_discordances(candidate, reference, expected):
    assert screen.paired_pvalue(
        np.array(candidate), np.array(reference)
    ) == pytest.approx(expected)


def test_future_date_sort_is_required():
    with pytest.raises(ValueError, match="date-sorted"):
        screen.walk_forward_hits(np.array([[1, 2], [2, 3]]), np.array([2, 1]), 4, (2,), warmup=0)
