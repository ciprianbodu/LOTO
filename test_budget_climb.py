"""Budgets above hitcover's range: complete design when it fits, exact climb otherwise."""

import pytest

from covering import budget_climb
from covering.common import _sorted_pool
from covering.designs import complete_design_size
from covering.probability import wheel_hit_probabilities, wheel_hit_profile
from loto_engine import LotoEngine
from loto_enterprise.core import walk_forward_adapter as wf
from wheeling_methods import generate_wheel

POOL = list(range(1, 17))
GAMES = {"6/49": (6, 6, 49), "5/40": (5, 6, 40), "joker": (5, 5, 45)}


@pytest.fixture(autouse=True)
def automatic_wheel(monkeypatch):
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)


def scores_for(pool):
    return {n: float((n * 17) % 23) + n / 100 for n in pool}


def dominates(pool, after, before):
    a, b = wheel_hit_profile(pool, after), wheel_hit_profile(pool, before)
    return all(x >= y for ra, rb in zip(a, b) for x, y in zip(ra, rb))


@pytest.mark.parametrize("game,budget", [("6/49", 100), ("5/40", 150), ("joker", 200)])
def test_budget_climb_dominates_and_raises_four_plus(game, budget):
    pick, draw, max_num = GAMES[game]
    scores = scores_for(POOL)
    before, _ = generate_wheel("hitcover", POOL, pick, 4, budget, scores, None, draw)
    after, cov = generate_wheel("hitcover", POOL, pick, 4, budget, scores, None, draw, max_num)
    assert len(after) == len(before) == budget
    assert len({tuple(sorted(t)) for t in after}) == budget
    assert all(len(set(t)) == pick and set(t) <= set(POOL) for t in after)
    assert {n for t in after for n in t} == set(POOL)
    assert dominates(POOL, after, before)
    p_before = wheel_hit_probabilities(POOL, before, draw, max_num)["ticket"]
    p_after = wheel_hit_probabilities(POOL, after, draw, max_num)["ticket"]
    assert p_after[4] > p_before[4] * 1.03
    assert 0.0 < cov < 100.0


def test_one_more_ticket_no_longer_lowers_the_chances():
    """5/40 pool 16: 65 bilete dădeau mai puțin la 3+ și 4+ decât 64."""
    pick, draw, max_num = GAMES["5/40"]
    p = {
        b: wheel_hit_probabilities(
            POOL,
            generate_wheel("hitcover", POOL, pick, 4, b, None, None, draw, max_num)[0],
            draw,
            max_num,
        )["ticket"]
        for b in (64, 65)
    }
    assert p[65][4] > p[64][4]
    assert p[65][3] >= p[64][3] - 0.01


def test_hitcover_range_is_unchanged():
    pick, draw, max_num = GAMES["6/49"]
    scores = scores_for(POOL)
    for budget in (10, 64):
        assert generate_wheel(
            "hitcover", POOL, pick, 4, budget, scores, None, draw, max_num
        ) == generate_wheel("hitcover", POOL, pick, 4, budget, scores, None, draw)


def test_a_budget_that_fits_the_complete_design_buys_it():
    pick, draw, max_num = GAMES["6/49"]
    size = complete_design_size(16, pick, 4)
    assert size is not None and size > 64
    scores = scores_for(POOL)
    uncapped = generate_wheel("lajolla", POOL, pick, 4, 0, scores, None, draw, max_num)
    for budget in (size, size + 50):
        wheel, cov = generate_wheel(
            "hitcover", POOL, pick, 4, budget, scores, None, draw, max_num
        )
        assert (wheel, cov) == uncapped
        assert cov == 100.0 and len(wheel) <= budget
    smaller, _ = generate_wheel(
        "hitcover", POOL, pick, 4, size - 1, scores, None, draw, max_num
    )
    assert len(smaller) <= size - 1


@pytest.mark.parametrize("game,guarantee", [("6/49", 3), ("5/40", 3), ("joker", 3)])
def test_a_small_budget_equal_to_the_design_is_complete(game, guarantee):
    """Audit 2026-10-08: buget = designul dădea 99,46% (sau bilete în plus)."""
    pick, draw, max_num = GAMES[game]
    size = complete_design_size(16, pick, guarantee)
    scores = scores_for(POOL)
    uncapped = generate_wheel("lajolla", POOL, pick, guarantee, 0, scores, None, draw, max_num)
    for budget in (size, size + 5):
        wheel, cov = generate_wheel(
            "hitcover", POOL, pick, guarantee, budget, scores, None, draw, max_num
        )
        assert (wheel, cov) == uncapped and cov == 100.0 and len(wheel) <= budget


def test_physical_tickets_keep_their_base():
    """Fără `max_num` (Bilet complet), bugetul nu trece prin rafinări."""
    pick, draw, _ = GAMES["6/49"]
    size = complete_design_size(16, pick, 3)
    wheel, _ = generate_wheel("hitcover", POOL, pick, 3, size, None, None, draw)
    assert wheel == generate_wheel("hitcover", POOL, pick, 3, size, None, None, draw)[0]
    assert len(wheel) == size


def test_the_result_is_deterministic_and_reused_across_scores():
    pick, draw, max_num = GAMES["6/49"]
    first, _ = generate_wheel("hitcover", POOL, pick, 4, 100, scores_for(POOL), None, draw, max_num)
    again, _ = generate_wheel("hitcover", POOL, pick, 4, 100, scores_for(POOL), None, draw, max_num)
    assert first == again
    other = {n: float(n % 5) for n in POOL}
    wheel, _ = generate_wheel("hitcover", POOL, pick, 4, 100, other, None, draw, max_num)
    ordered = _sorted_pool(POOL, other)
    positions = sorted(tuple(sorted(ordered.index(n) for n in t)) for t in wheel)
    ordered_first = _sorted_pool(POOL, scores_for(POOL))
    first_positions = sorted(tuple(sorted(ordered_first.index(n) for n in t)) for t in first)
    assert positions == first_positions


def test_outside_limits_the_incumbent_stays():
    wheel = [[1, 2, 3, 4, 5, 6]] * 2
    same, audit = budget_climb.improve_budget_hits(POOL, wheel, 4, 6, 49)
    assert same is wheel and not audit["applied"]
    big = list(range(1, 18))
    tickets = [[n, (n % 17) + 1, ((n + 1) % 17) + 1, ((n + 2) % 17) + 1, ((n + 3) % 17) + 1, ((n + 4) % 17) + 1] for n in big] * 4
    same, audit = budget_climb.improve_budget_hits(big, tickets, 4, 6, 49)
    assert same is tickets and audit["reason"] == "outside limits"


def test_engine_passes_the_universe_to_the_climb():
    for game, (pick, draw, max_num) in GAMES.items():
        engine = LotoEngine(game)
        engine.hard_core = list(POOL)
        engine.hard_core_joker = [7]
        scores = scores_for(POOL)
        lines, _ = engine.generate_predictions(4, 100, scores)
        main = [line[:pick] for line in lines]
        expected, _ = generate_wheel("hitcover", POOL, pick, 4, 100, scores, None, draw, max_num)
        assert main == expected


def test_walk_forward_key_names_the_budget_branch():
    assert wf._wheel_sig(16, "6/49", 4, None, 64).endswith("|rt1|hp1")
    assert wf._wheel_sig(16, "6/49", 4, None, 100).startswith("hitcover|")
    assert wf._wheel_sig(16, "6/49", 4, None, 100).endswith("|bc1")
    size = complete_design_size(16, 6, 4)
    full = wf._wheel_sig(16, "6/49", 4, None, size)
    assert full.startswith("lajolla|") and full.endswith("|bf1") and "|cd" in full
    assert wf._wheel_sig(16, "6/49", 4, None, size - 1).endswith("|bc1")
    small = complete_design_size(16, 6, 3)
    assert wf._wheel_sig(16, "6/49", 3, None, small).endswith("|bf1")
    assert wf._wheel_sig(16, "6/49", 3, None, small - 1) == (
        f"hitcover|g3|c3|cap{small - 1}|rt1|hp1"
    )


def test_pool_16_four_covers_are_smaller_than_the_old_greedy_ones():
    """198 → 172 bilete la 6/49, 467 → 416 la 5/40 și Joker, garanția 4 intactă."""
    assert complete_design_size(16, 6, 4) <= 172
    assert complete_design_size(16, 5, 4) <= 416


def test_equal_size_greedy_that_dominates_the_design_is_used(monkeypatch):
    """Audit 2026-10-08: la egalitate de bilete, designul rămânea și când greedy-ul
    cu scoruri îl domina exact (5/40 pool 16, garanție 3: 4+ 5,06% față de 5,10%)."""
    from covering import designs
    from covering.common import _greedy_fallback

    pool = list(range(1, 11))
    scores = {n: float((n * 17) % 23) for n in pool}
    greedy, _ = _greedy_fallback(pool, 5, 3, 0, scores)
    greedy = [sorted(t) for t in greedy]
    weak = greedy[:18] + [[1, 2, 3, 7, 9]] + greedy[19:]
    ordered = _sorted_pool(pool, scores)
    design = [sorted(ordered.index(n) + 1 for n in t) for t in weak]
    monkeypatch.setattr(designs, "_load_lajolla", lambda v, p, g: design)
    wheel, cov = designs.wheel_lajolla(pool, 5, 3, 0, scores)
    assert cov == 100.0
    assert sorted(tuple(sorted(t)) for t in wheel) == sorted(tuple(t) for t in greedy)
    assert wf._wheel_sig(16, "6/49", 4, None, 0).endswith("|tg1")


def test_union34_key_carries_the_tie_rule(monkeypatch):
    """union34 trece prin `wheel_lajolla`, deci primește aceeași regulă la egalitate."""
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "union34")
    assert wf._wheel_sig(16, "6/49", 4, None, 0).startswith("union34|")
    assert wf._wheel_sig(16, "6/49", 4, None, 0).endswith("|tg1")


@pytest.mark.parametrize(
    ("game", "hi", "lo", "expected"),
    [
        ("6/49", 12, 0, 12),
        ("6/49", 20, 10, 11),
        ("5/40", 0, 36, 5),
        ("6/49", 0, 0, 16),
        ("6/49", 40, 0, 16),
        ("6/49", 3, 0, 16),  # mai îngust decât un bilet: ignorat
        ("6/49", 5, 20, 16),  # inversat: ignorat
    ],
)
def test_effective_pool_follows_the_interval(game, hi, lo, expected):
    assert wf._effective_pool_size(16, game, hi, lo) == expected


def test_a_narrow_interval_keys_the_design_of_the_pool_it_leaves(monkeypatch):
    """Revizie 2026-10-08: intervalul 1..12 lasă 12 numere; pasul roțește designul
    de 12, deci cheia WF îl semnează pe acela, nu pe cel de 16."""
    seen = []
    real = wf._wheel_sig

    def spy(pool_size, *args, **kwargs):
        seen.append(pool_size)
        return real(pool_size, *args, **kwargs)

    monkeypatch.setattr(wf, "_wheel_sig", spy)
    wf._decision_sig("6/49", 16, restrict_base_max=12)
    assert seen and set(seen) == {12}
    seen.clear()
    wf._decision_sig("6/49", 16)
    assert seen and set(seen) == {16}
