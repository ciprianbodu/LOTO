"""Independent certificates for complete-four-cover higher-hit swaps."""
from itertools import combinations

import pytest

from covering import higher_hits
from covering.common import _sorted_pool, compute_coverage_pct
from covering.greedy import generate_combinatorial_wheel
from covering.probability import wheel_hit_probabilities, wheel_hit_profile


def _fixture(v, pick, guarantee=4, cap=0):
    pool = list(range(1, v + 1))
    scores = {n: float((n * 17) % 23) for n in pool}
    baseline, _ = generate_combinatorial_wheel(pool, pick, guarantee, cap, scores)
    return _sorted_pool(pool, scores), baseline


@pytest.mark.parametrize("v", [11, 16])
@pytest.mark.parametrize("pick", [5, 6])
@pytest.mark.parametrize("guarantee", [3, 4])
@pytest.mark.parametrize("cap", [0, 7, 10, 30])
def test_complete_cover_swaps_preserve_every_threshold(v, pick, guarantee, cap):
    pool, baseline = _fixture(v, pick, guarantee, cap)
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    assert len(candidate) == len(baseline)
    assert len(set(map(tuple, map(sorted, candidate)))) == len(candidate)
    assert all(len(set(ticket)) == pick for ticket in candidate)
    old, new = wheel_hit_profile(pool, baseline), wheel_hit_profile(pool, candidate)
    assert all(
        a >= b for nr, or_ in zip(new, old) for a, b in zip(nr, or_)
    )
    assert compute_coverage_pct(candidate, pool, guarantee) >= compute_coverage_pct(
        baseline, pool, guarantee
    )
    assert audit["candidates_evaluated"] <= 16384
    assert candidate == higher_hits.improve_higher_hits(pool, baseline, 6)[0]


@pytest.mark.parametrize("pick,max_num", [(6, 49), (5, 40)])
def test_same_cost_pool16_complete_four_cover_improves_five_hits(pick, max_num):
    pool, baseline = _fixture(16, pick)
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    old = wheel_hit_probabilities(pool, baseline, 6, max_num)
    new = wheel_hit_probabilities(pool, candidate, 6, max_num)
    assert audit["applied"]
    assert audit["swaps"] > 0
    assert len(candidate) == len(baseline)
    assert compute_coverage_pct(candidate, pool, 4) == 100.0
    assert new["pool"] == old["pool"]
    assert new["ticket"][5] > old["ticket"][5]
    assert new["ticket"][3] == old["ticket"][3]
    assert new["ticket"][4] == old["ticket"][4]
    assert new["ticket"][6] == old["ticket"][6]


def _brute_profile(pool, tickets):
    result = []
    ticket_sets = [set(ticket) for ticket in tickets]
    for h in range(len(pool) + 1):
        counts = [0] * (len(pool) + 1)
        for subset in combinations(pool, h):
            hits = max(len(set(subset) & ticket) for ticket in ticket_sets)
            for threshold in range(hits + 1):
                counts[threshold] += 1
        result.append(tuple(counts))
    return tuple(result)


def test_swaps_certificate_matches_independent_exhaustive_intersections():
    pool, baseline = _fixture(11, 6)
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    assert audit["applied"]
    old, new = _brute_profile(pool, baseline), _brute_profile(pool, candidate)
    assert old == wheel_hit_profile(pool, baseline)
    assert new == wheel_hit_profile(pool, candidate)
    assert all(a >= b for nr, or_ in zip(new, old) for a, b in zip(nr, or_))
    assert new[5][5] > old[5][5]


def test_five_number_draws_have_fixed_five_hit_probability(monkeypatch):
    pool, baseline = _fixture(16, 5)

    def forbidden_search(*args):
        raise AssertionError("Fixed count of distinct five-number tickets is invariant")

    monkeypatch.setattr(higher_hits, "_improve_positions", forbidden_search)
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 5)
    assert candidate == baseline
    assert not audit["applied"]
    assert audit["candidates_evaluated"] == 0
    probability = wheel_hit_probabilities(pool, candidate, 5, 45)["ticket"][5]
    from math import comb
    assert probability == len(candidate) / comb(45, 5)


def test_candidate_limit_is_enforced(monkeypatch):
    pool, baseline = _fixture(11, 6)
    higher_hits._improve_positions.cache_clear()
    monkeypatch.setattr(higher_hits, "_MAX_CANDIDATES", 1)
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    assert audit["candidates_evaluated"] <= 1
    assert len(candidate) == len(baseline)
    assert compute_coverage_pct(candidate, pool, 4) == 100.0
    higher_hits._improve_positions.cache_clear()


def test_geometry_search_is_memoized(monkeypatch):
    pool, baseline = _fixture(11, 6)
    higher_hits._improve_positions.cache_clear()
    first = higher_hits.improve_higher_hits(pool, baseline, 6)

    def unexpected_events(*args):
        raise AssertionError("Geometry search was recomputed")

    monkeypatch.setattr(higher_hits, "_events", unexpected_events)
    assert higher_hits.improve_higher_hits(pool, baseline, 6) == first


def test_final_full_profile_gate_rejects_an_adversarial_internal_result(monkeypatch):
    pool, baseline = _fixture(11, 6)
    count = len(baseline)
    bad_masks = tuple(
        sum(1 << pos for pos in b)
        for b in list(combinations(range(len(pool)), 6))[:count]
    )
    assert len(set(bad_masks)) == count
    monkeypatch.setattr(
        higher_hits, "_improve_positions", lambda *args: (bad_masks, 1, 1)
    )
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    assert candidate == baseline
    assert not audit["applied"]


def test_label_mapping_preserves_pure_geometry():
    pool, baseline = _fixture(11, 6)
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    labels = {number: 50 - number * 2 for number in pool}
    renamed_pool = [labels[number] for number in pool]
    renamed_baseline = [[labels[number] for number in ticket] for ticket in baseline]
    renamed_candidate, renamed_audit = higher_hits.improve_higher_hits(
        renamed_pool, renamed_baseline, 6
    )
    assert renamed_audit == audit
    assert renamed_candidate == [sorted(labels[n] for n in t) for t in candidate]


@pytest.mark.parametrize("v,pick", [(17, 6), (11, 4)])
def test_unsupported_geometry_returns_original_wheel(v, pick):
    pool = list(range(1, v + 1))
    baseline = [pool[:pick]]
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    assert candidate == baseline
    assert not audit["applied"]


def test_partial_cover_is_preserved():
    pool, baseline = _fixture(16, 6, 4, 7)
    candidate, audit = higher_hits.improve_higher_hits(pool, baseline, 6)
    assert candidate == baseline
    assert audit["candidates_evaluated"] == 0


@pytest.mark.parametrize("pick,draw_n", [(6, None), (6, 6), (5, 6)])
def test_dispatch_refines_complete_four_cover_automatically(pick, draw_n):
    from covering.designs import wheel_lajolla
    from covering.dispatch import generate_wheel

    pool = list(range(1, 17))
    scores = {n: float((n * 17) % 23) for n in pool}
    baseline, old_cov = wheel_lajolla(pool, pick, 4, 0, scores)
    candidate, cov = generate_wheel(
        "lajolla", pool, pick, 4, 0, scores, draw_n=draw_n
    )
    assert len(candidate) == len(baseline)
    assert cov == old_cov == 100.0
    old, new = wheel_hit_profile(pool, baseline), wheel_hit_profile(pool, candidate)
    assert all(a >= b for nr, or_ in zip(new, old) for a, b in zip(nr, or_))
    assert any(new[h][5] > old[h][5] for h in (5, 6))


@pytest.mark.parametrize("draw_n", [None, 5])
def test_dispatch_five_number_draw_keeps_existing_design(draw_n, monkeypatch):
    from covering import dispatch
    from covering.designs import wheel_lajolla

    def forbidden_search(*args, **kwargs):
        raise AssertionError("Five-number draws cannot gain five hits at fixed count")

    monkeypatch.setattr(dispatch, "improve_higher_hits", forbidden_search)
    pool = list(range(1, 17))
    scores = {n: float((n * 17) % 23) for n in pool}
    assert dispatch.generate_wheel(
        "lajolla", pool, 5, 4, 0, scores, draw_n=draw_n
    ) == wheel_lajolla(pool, 5, 4, 0, scores)


def test_dispatch_explicit_greedy_and_conditional_routes_bypass_new_search(monkeypatch):
    from covering import dispatch
    from covering.designs import wheel_lotto

    def forbidden_search(*args, **kwargs):
        raise AssertionError("Reference and conditional paths must remain unchanged")

    monkeypatch.setattr(dispatch, "improve_higher_hits", forbidden_search)
    pool = list(range(1, 17))
    scores = {n: float((n * 17) % 23) for n in pool}
    assert dispatch.generate_wheel(
        "greedy", pool, 6, 4, 0, scores, draw_n=6
    ) == generate_combinatorial_wheel(pool, 6, 4, 0, scores)
    monkeypatch.setattr(dispatch, "improve_hit_profile", lambda p, t, **k: (t, {"applied": False}))
    assert dispatch.generate_wheel(
        "lajolla", pool, 6, 4, 0, scores, condition=5, draw_n=6
    ) == wheel_lotto(pool, 6, 4, 5, 0, scores)


@pytest.mark.parametrize("game,pick,draw_n", [("6/49", 6, 6), ("5/40", 5, 6), ("joker", 5, 5)])
def test_engine_forwards_draw_geometry_for_uncapped_generation(
    game, pick, draw_n, monkeypatch
):
    from loto_engine import LotoEngine
    from covering.designs import wheel_lajolla
    from covering.dispatch import generate_wheel

    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    engine = LotoEngine(game)
    engine.hard_core = list(range(1, 17))
    engine.hard_core_joker = [7]
    scores = {n: float((n * 17) % 23) for n in engine.hard_core}
    actual, cov = engine.generate_predictions(4, 0, scores)
    expected, expected_cov = generate_wheel(
        "lajolla", engine.hard_core, pick, 4, 0, scores, draw_n=draw_n
    )
    assert [v[:pick] for v in actual] == expected
    assert cov == expected_cov == 100.0
    baseline, _ = wheel_lajolla(engine.hard_core, pick, 4, 0, scores)
    if draw_n == 6:
        old = wheel_hit_profile(engine.hard_core, baseline)
        new = wheel_hit_profile(engine.hard_core, expected)
        assert any(new[h][5] > old[h][5] for h in (5, 6))
    else:
        assert expected == baseline
        assert all(len(v) == 6 and v[-1] == 7 for v in actual)
