"""Independent geometry, parity and chronological nesting checks for LR study."""
import itertools

import numpy as np
import pytest

from covering.lr_schemes import best_hits, lr_schemes, scheme_tickets
from loto_enterprise.benchmark.methods import call_method
from loto_enterprise.core.pool_selection import select_pool_from_scores
from scripts.analysis import lr_pool_study_2026_11_04 as study


GEOMETRIES = [
    (n, pick, seed)
    for n in range(4, 10)
    for pick in range(2, min(n, 6) + 1)
    for seed in range(3)
]


@pytest.mark.parametrize("n,pick,seed", GEOMETRIES)
def test_lookup_matches_explicit_intersections_for_every_mask(n, pick, seed):
    all_tickets = list(itertools.combinations(range(1, n + 1), pick))
    rng = np.random.default_rng(seed + 100 * n + pick)
    chosen = rng.choice(len(all_tickets), min(8, len(all_tickets)), replace=False)
    tickets = [all_tickets[index] for index in chosen]
    lookup = study.exact_lookup(n, tickets)
    assert lookup.dtype == np.uint8
    for mask in range(1 << n):
        drawn = {i + 1 for i in range(n) if mask & (1 << i)}
        expected = max(len(drawn & set(ticket)) for ticket in tickets)
        assert lookup[mask] == expected


@pytest.mark.parametrize("config", study.GAMES)
@pytest.mark.parametrize("seed", range(8))
def test_official_mapping_and_lookup_match_real_tickets(config, seed):
    _, game, universe, draw_n, _, n, code = config
    scheme = next(s for s in lr_schemes(game, n) if s.code == code)
    rng = np.random.default_rng(seed)
    # An unsorted scorer rank must never become official position mapping.
    pool = list(map(int, rng.choice(np.arange(1, universe + 1), n, replace=False)))
    drawn = set(map(int, rng.choice(np.arange(1, universe + 1), draw_n, replace=False)))
    tickets = scheme_tickets(scheme, pool)
    lookup = study.exact_lookup(n, scheme.variants)
    mask = sum(1 << i for i, number in enumerate(sorted(pool)) if number in drawn)
    expected = max(len(set(ticket) & drawn) for ticket in tickets)
    assert lookup[mask] == expected == best_hits(scheme, pool, drawn)


@pytest.mark.parametrize("bad", [{}, {1: 1.0, 2: 1.0}, {1: float("nan"), 2: 1.0}, {1: 0.0, 2: 1.0}])
def test_invalid_candidate_has_visible_frequency_fallback(bad):
    frequency = {1: 0.1, 2: 0.4, 3: 0.2, 4: 0.9}

    def caller(*args, **kwargs):
        return bad, 0.01

    result, reason, _ = study.safe_scores(
        "candidate", np.array([[1, 2]]), 4, (0,), frequency, caller=caller
    )
    assert result == frequency
    assert reason is not None


def test_scorer_exception_is_named_and_does_not_make_zero_hit_rows():
    def caller(*args, **kwargs):
        raise RuntimeError("synthetic scorer failure")

    frequency = {1: 0.1, 2: 0.9}
    result, reason, _ = study.safe_scores(
        "candidate", np.array([[1]]), 2, (0,), frequency, caller=caller
    )
    assert result == frequency
    assert reason == "RuntimeError: synthetic scorer failure"


def test_context_contains_only_supplied_prefix():
    history = np.array([[1, 3], [2, 4]])
    seen = []

    def caller(name, prefix, universe, *, history_cutoffs):
        seen.append((name, prefix.copy(), universe, history_cutoffs))
        return {1: 0.1, 2: 0.2, 3: 0.3, 4: 0.4}, 0.01

    study.safe_scores("candidate", history, 4, (0, 1), None, caller=caller)
    assert seen[0][0] == "candidate" and seen[0][2:] == (4, (0, 1))
    np.testing.assert_array_equal(seen[0][1], history)


def test_registered_frequency_is_not_cumulative_counts():
    # Same number of appearances, different recency: weighted registry has
    # ranking information while cumulative counts would be exactly flat.
    history = np.array([[1], [1], [2], [2]])
    scores, _ = call_method("frequency", history, 2)
    assert scores[2] > scores[1]
    assert select_pool_from_scores(scores, 1, set(), max_num=2) == [2]


@pytest.mark.parametrize("seed", range(12))
def test_nested_choice_never_sees_outer_block_outcomes(seed):
    rng = np.random.default_rng(seed)
    outcomes = rng.integers(0, 7, size=(850, 7), dtype=np.uint8)
    dates = np.arange(850)
    before, _, blocks = study.nested_choices(outcomes, dates)
    # Mutate all outcomes on/after each target block; the selected method and
    # tuning memory for that block must remain unchanged.
    for block in blocks:
        changed = outcomes.copy()
        changed[block["start"]:] = rng.integers(
            0, 7, size=changed[block["start"]:].shape, dtype=np.uint8
        )
        after, _, changed_blocks = study.nested_choices(changed, dates)
        match = next(b for b in changed_blocks if b["start"] == block["start"])
        assert match == block
        np.testing.assert_array_equal(
            before[:block["end"]], after[:block["end"]]
        )


@pytest.mark.parametrize("seed", range(12))
def test_nested_boundaries_do_not_split_same_day_draws(seed):
    rng = np.random.default_rng(seed)
    dates = np.repeat(np.arange(550), 2)
    outcomes = rng.integers(0, 7, size=(1100, 5), dtype=np.uint8)
    choices, _, blocks = study.nested_choices(outcomes, dates)
    for block in blocks:
        for name in ("start", "end", "inner_start"):
            index = block[name]
            if 0 < index < len(dates):
                assert dates[index] != dates[index - 1]
        assert np.all(choices[block["start"]:block["end"]] == block["method_index"])


def test_method_and_memory_ties_follow_fixed_rules():
    outcomes = np.zeros((700, 4), dtype=np.uint8)
    choices, memories, blocks = study.nested_choices(outcomes, np.arange(700))
    assert np.all(choices[400:] == 0)
    assert np.all(memories[400:] == 0)
    assert all(b["inner_validation_scores"] == [[0, 0, 0], [0, 0, 0]] for b in blocks)


def test_best_method_refuses_same_date_leakage():
    dates = np.array([1, 1, 2, 2])
    prefix = study.objective_prefix(np.ones((4, 2), dtype=np.uint8))
    with pytest.raises(ValueError, match="target date"):
        study.best_method(prefix, dates, 3, 200)


def test_objective_is_lexicographic_4_then_5_then_6():
    outcomes = np.array([[4, 6], [4, 3], [3, 3]], dtype=np.uint8)
    prefix = study.objective_prefix(outcomes)
    assert study.best_method(prefix, np.arange(3), 3, 0) == 0


def test_protocol_contains_all_registry_methods_and_only_requested_schemes():
    assert not study.METHOD_LOAD_ERRORS
    assert len(study.METHODS) == 53
    assert "random" in study.METHODS and "frequency" in study.METHODS
    assert [(g[5], g[6]) for g in study.GAMES] == [(16, 59), (15, 12), (19, 48)]
    assert study.MODES == (2, 0)
    assert study.MEMORIES == (0, 200)
