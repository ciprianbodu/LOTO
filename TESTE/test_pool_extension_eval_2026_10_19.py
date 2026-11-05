"""Independent canonical parity for every requested extension pool size."""

from __future__ import annotations

import numpy as np
import pytest

from loto_enterprise.core.pool_selection import apply_consecutive_limit
from loto_enterprise.core.ranking import longest_consecutive_run, rank_by_score
from scripts.analysis import pool_extension_eval_2026_10_19 as module


@pytest.mark.parametrize("max_num,draw_n,ks", [(40, 6, range(9, 16)), (45, 5, range(9, 20)), (49, 6, range(9, 17))])
def test_each_geometry_matches_separate_canonical_calls(max_num, draw_n, ks):
    rng = np.random.default_rng(2026101904)
    # Put blocking adjacent pairs first, then their blocked neighbors. These
    # reach far beyond the nominal K-th raw position without fabricating a
    # nonnested counterexample for the complete40/45 universes at these sizes.
    adversarial = []
    for a in range(2, max_num, 4):
        adversarial.extend(n for n in (a, a + 1, a - 1, a + 2) if n <= max_num)
    adversarial += [n for n in range(1, max_num + 1) if n not in adversarial]
    vector = np.empty(max_num)
    vector[np.asarray(adversarial) - 1] = np.arange(max_num, 0, -1)
    rows = [vector, np.arange(max_num), -np.arange(max_num), np.arange(max_num) // 3]
    rows += [rng.permutation(max_num) for _ in range(35)]
    rows += [rng.integers(0, 4, max_num) for _ in range(20)]
    scores = np.asarray(rows, dtype=float)
    draws = np.asarray([rng.choice(np.arange(1, max_num + 1), draw_n, replace=False) for _ in rows])
    hits, ties = module.evaluate_scores(scores, draws, max_num=max_num, draw_n=draw_n, ks=ks)
    assert hits.shape == (len(rows), 2, len(ks))
    for row, values in enumerate(scores):
        ranked = rank_by_score(dict(enumerate(values, start=1)), max_num)
        actual = set(draws[row])
        for column, k in enumerate(ks):
            for mode, limit in enumerate((0, 2)):
                chosen = apply_consecutive_limit(ranked, k, limit)
                assert hits[row, mode, column] == len(actual.intersection(chosen))
                assert len(chosen) == k
                if mode:
                    assert longest_consecutive_run(chosen) <= 2
            expected_tie = abs(values[ranked[k - 1] - 1] - values[ranked[k] - 1]) <= 1e-12
            assert ties[row, column] == expected_tie


def test_completion_guard_can_produce_nonnested_pools_in_a_narrow_universe():
    # Four eligible consecutive numbers inside a larger game's restricted base:
    # accepting the first two greedily blocks BOTH remaining numbers. K=3 must
    # skip the second-ranked number, while K=2 takes it. This is a genuine
    # counterexample; it is not asserted for full40/k15 or full45/k19 universes.
    ranked = [2, 3, 1, 4]
    small = apply_consecutive_limit(ranked, 2, 2)
    large = apply_consecutive_limit(ranked, 3, 2)
    assert small == [2, 3]
    assert large == [2, 1, 4]
    assert small != large[:2]
    greedy = []
    for ball in ranked:
        if longest_consecutive_run(greedy + [ball]) <= 2:
            greedy.append(ball)
    assert len(greedy) == 2
    scores = np.asarray([[2.0, 4.0, 3.0, 1.0]])
    hits, _ = module.evaluate_scores(scores, [[2, 3]], max_num=4, draw_n=2, ks=(2, 3))
    assert hits[0, 1].tolist() == [2, 1]  # A monotonicity assertion would be wrong.


def test_540_counts_the_sixth_drawn_ball_independently_of_ticket_pick_five():
    scores = np.arange(40, dtype=float)[None]
    hits, _ = module.evaluate_scores(scores, [[1, 2, 3, 4, 5, 40]], max_num=40, draw_n=6, ks=range(9, 16))
    assert (hits == 1).all()
    with pytest.raises(ValueError, match="shape"):
        module.evaluate_scores(scores, [[1, 2, 3, 4, 5]], max_num=40, draw_n=6, ks=(9,))


def test_joker_uses_five_main_balls_and_does_not_accept_a_sixth():
    scores = np.arange(45, dtype=float)[None]
    with pytest.raises(ValueError, match="shape"):
        module.evaluate_scores(scores, [[1, 2, 3, 4, 5, 6]], max_num=45, draw_n=5, ks=(19,))


def test_old_649_results_and_ties_remain_bit_identical():
    from scripts.analysis.pool_combination_eval_2026_10_19 import evaluate_scores as old_evaluate

    rng = np.random.default_rng(2026101904)
    scores = rng.integers(0, 8, (12, 49)).astype(float)
    draws = np.asarray([rng.choice(np.arange(1, 50), 6, replace=False) for _ in scores])
    old = old_evaluate(scores, draws)
    new = module.evaluate_scores(scores, draws, max_num=49, draw_n=6, ks=range(9, 17))
    assert all(np.array_equal(a, b) for a, b in zip(old, new, strict=True))


def test_arbitrary_column_order_full_pool_boundary_and_readonly_inputs():
    scores = np.arange(40, dtype=float)[None]
    draws = np.asarray([[1, 2, 3, 4, 5, 40]])
    scores.flags.writeable = draws.flags.writeable = False
    hits, ties = module.evaluate_scores(scores, draws, max_num=40, draw_n=6, ks=(40, 15, 9))
    assert hits[0, 0, 0] == 6
    assert not ties[0, 0]
    assert np.array_equal(scores, np.arange(40)[None])


@pytest.mark.parametrize("change", ["flat", "nan", "inf", "shape", "duplicate", "fraction", "range", "draw_shape", "bool_geometry", "duplicate_ks", "bad_ks"])
def test_invalid_inputs_fail_closed(change):
    scores = np.arange(40, dtype=float)[None]
    draws = np.asarray([[1, 2, 3, 4, 5, 6]], dtype=float)
    geometry = dict(max_num=40, draw_n=6, ks=(9, 15))
    if change == "flat": scores[:] = 1
    elif change == "nan": scores[0, 2] = np.nan
    elif change == "inf": scores[0, 2] = np.inf
    elif change == "shape": scores = scores[:, :-1]
    elif change == "duplicate": draws[0, -1] = draws[0, 0]
    elif change == "fraction": draws[0, -1] = 6.5
    elif change == "range": draws[0, -1] = 41
    elif change == "draw_shape": draws = draws[:, :-1]
    elif change == "bool_geometry": geometry["draw_n"] = True
    elif change == "duplicate_ks": geometry["ks"] = (9, 9)
    elif change == "bad_ks": geometry["ks"] = (0, 15)
    with pytest.raises(ValueError):
        module.evaluate_scores(scores, draws, **geometry)


def test_serial_and_parallel_banks_match_scores_and_invalid_masks():
    scores = np.arange(40, dtype=float)[None].repeat(2, axis=0)
    draws = np.asarray([[1, 2, 3, 4, 5, 40], [1, 2, 3, 4, 5, 39]])
    items = [("a", scores, np.asarray([False, True])), ("b", -scores, np.asarray([True, False]))]
    geometry = dict(max_num=40, draw_n=6, ks=(9, 15))
    serial = {row[0]: row[1:] for row in module.evaluate_bank(items, draws, workers=1, **geometry)}
    parallel = {row[0]: row[1:] for row in module.evaluate_bank(items, draws, workers=2, **geometry)}
    assert serial.keys() == parallel.keys()
    for name in serial:
        assert all(np.array_equal(a, b) for a, b in zip(serial[name], parallel[name], strict=True))


def test_invalid_mask_must_be_boolean_and_aligned():
    items = [("a", np.arange(40, dtype=float)[None], np.asarray([0]))]
    with pytest.raises(ValueError, match="boolean"):
        list(module.evaluate_bank(items, [[1, 2, 3, 4, 5, 6]], max_num=40, draw_n=6, ks=(15,), workers=1))
