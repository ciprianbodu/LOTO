"""Exact-dominance profile swaps: same tickets count, guarantees kept, deterministic."""

from itertools import combinations

import pytest

import covering.dispatch as dispatch
from covering.common import _coverage_pct, _sorted_pool, lotto_coverage_pct
from covering.probability import wheel_hit_probabilities, wheel_hit_profile
from covering.profile_swap import improve_hit_profile
from loto_enterprise.core.full_ticket import _fill_variants


def _dominates(new, old):
    return all(a >= b for ra, rb in zip(new, old) for a, b in zip(ra, rb))


def _identity(pool, tickets, *a, **k):
    return [sorted(t) for t in tickets], {"applied": False}


def _pair(monkeypatch, *args, **kwargs):
    monkeypatch.setattr(dispatch, "improve_hit_profile", _identity)
    before, _ = dispatch.generate_wheel(*args, **kwargs)
    monkeypatch.undo()
    after, cov = dispatch.generate_wheel(*args, **kwargs)
    return before, after, cov


@pytest.mark.parametrize(
    "pool_size,pick,g,cond,draw_n",
    [(14, 6, 3, 4, 6), (11, 6, 4, 5, 6), (12, 5, 3, 4, 6), (10, 5, 3, 4, 5)],
)
def test_lotto_design_keeps_t_if_p_and_dominates(monkeypatch, pool_size, pick, g, cond, draw_n):
    pool = list(range(1, pool_size + 1))
    before, after, cov = _pair(
        monkeypatch, "lotto", pool, pick, g, 0, None, condition=cond, draw_n=draw_n
    )
    assert len({tuple(t) for t in after}) == len(before)
    assert _dominates(wheel_hit_profile(pool, after), wheel_hit_profile(pool, before))
    # Exhaustive t-if-p check.
    sets = [set(t) for t in after]
    assert all(
        any(len(s & set(c)) >= g for s in sets) for c in combinations(pool, cond)
    )
    assert cov == lotto_coverage_pct(after, pool, g, cond) == 100.0


def test_lotto_measured_gain_is_strict(monkeypatch):
    pool = list(range(1, 15))
    before, after, _ = _pair(
        monkeypatch, "lotto", pool, 6, 3, 0, None, condition=4, draw_n=6
    )
    p0 = wheel_hit_probabilities(pool, before, 6, 49)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, 6, 49)["ticket"]
    assert p1[3] > p0[3] and p1[4] >= p0[4]


@pytest.mark.parametrize(
    "pool_size,pick,g,budget,draw_n",
    [(16, 6, 3, 0, 6), (14, 5, 3, 0, 6), (14, 6, 3, 20, 6), (11, 6, 3, 7, 6), (13, 5, 3, 100, 5)],
)
def test_classic_and_budget_preserve_count_coverage(monkeypatch, pool_size, pick, g, budget, draw_n):
    pool = list(range(1, pool_size + 1))
    method = dispatch.resolve_wheel_method(budget, "")
    before, after, cov = _pair(
        monkeypatch, method, pool, pick, g, budget, None, draw_n=draw_n
    )
    assert len(after) == len(before) == len({tuple(t) for t in after})
    if budget:
        assert len(after) <= budget
    assert _dominates(wheel_hit_profile(pool, after), wheel_hit_profile(pool, before))
    assert cov == _coverage_pct(after, pool, g) >= _coverage_pct(before, pool, g)
    assert set(pool) == {n for t in after for n in t}


def test_explicit_greedy_is_untouched(monkeypatch):
    pool = list(range(1, 12))
    before, after, _ = _pair(monkeypatch, "greedy", pool, 6, 3, 7, None, draw_n=6)
    assert before == after


def test_deterministic_and_frozen_prefix():
    pool = list(range(1, 13))
    wheel, _ = dispatch.generate_wheel("greedy", pool, 6, 3, 9, None)
    a, audit_a = improve_hit_profile(pool, wheel, 6, frozen=4)
    b, audit_b = improve_hit_profile(pool, wheel, 6, frozen=4)
    assert a == b and audit_a == audit_b
    assert a[:4] == [sorted(t) for t in wheel[:4]]
    assert _dominates(wheel_hit_profile(pool, a), wheel_hit_profile(pool, wheel))


def test_out_of_bounds_returns_input():
    pool = list(range(1, 18))
    wheel = [[1, 2, 3, 4, 5, 6]]
    out, audit = improve_hit_profile(pool, wheel, 6)
    assert out == wheel and not audit["applied"]


def test_full_ticket_fill_keeps_base_and_dominates(monkeypatch):
    import covering.profile_swap as ps

    pool = _sorted_pool(list(range(1, 10)), None)
    real = ps.improve_hit_profile
    monkeypatch.setattr(ps, "improve_hit_profile", _identity)
    before, n0 = _fill_variants(pool, 6, 3, 30, None)
    monkeypatch.setattr(ps, "improve_hit_profile", real)
    after, n1 = _fill_variants(pool, 6, 3, 30, None)
    assert n0 == n1 and after[:n1] == before[:n0]
    assert len({tuple(t) for t in after}) == len(before)
    assert _dominates(wheel_hit_profile(pool, after), wheel_hit_profile(pool, before))


@pytest.mark.parametrize(
    "pool_size,guarantee,tickets", [(8, 3, 3), (9, 3, 10), (9, 4, 10)]
)
def test_540_full_ticket_refines_six_draws_preserving_base(
    pool_size, guarantee, tickets
):
    from loto_enterprise.core.full_ticket import build_full_ticket

    pool = list(range(1, pool_size + 1))
    # Default draw_n preserves the prior released construction (pick=5).
    before, n_base = _fill_variants(pool, 5, guarantee, 4 * tickets, None)
    result = build_full_ticket(
        "5/40", {"hard_core": pool, "guarantee": guarantee}, tickets
    )
    after = result["variants"]
    old_profile = wheel_hit_profile(pool, before)
    new_profile = wheel_hit_profile(pool, after)
    assert result["guarantee_variants"] == n_base < len(after)
    assert after[:n_base] == before[:n_base]
    assert len(after) == len(before) == len({tuple(v) for v in after})
    assert _dominates(new_profile, old_profile)
    # Five hits with five drawn numbers is invariant at the same distinct
    # ticket count; only the sixth drawn number unlocks this strict gain.
    assert new_profile[5][5] == old_profile[5][5]
    assert new_profile[6][5] > old_profile[6][5]
    assert wheel_hit_probabilities(pool, after, 6, 40)["ticket"][5] > (
        wheel_hit_probabilities(pool, before, 6, 40)["ticket"][5]
    )


def test_540_full_ticket_keeps_base_without_filler_slots():
    from loto_enterprise.core.full_ticket import build_full_ticket

    pool = list(range(1, 11))
    before, n_base = _fill_variants(pool, 5, 4, 8, None)
    # Replacing the base search's draw_n=5 by 6 follows a different local
    # optimum and lowers 3+/4+ here. Keep the delivered base immutable.
    result = build_full_ticket(
        "5/40", {"hard_core": pool, "guarantee": 4}, tickets=2
    )
    assert n_base == 8 == result["guarantee_variants"]
    assert result["variants"] == before


# --------------------------------------------------------------------------- #
# Căutarea rapidă (2026-10-07) dă exact rezultatul căutării de referință.
# --------------------------------------------------------------------------- #


def _reference_event(v, mask, t):
    """Definiția: subseturile pool-ului care ating `mask` în cel puțin t locuri."""
    from covering.higher_hits import _supersets

    positions = [i for i in range(v) if mask >> i & 1]
    result = 0
    for subset in combinations(positions, t):
        result |= _supersets(v, sum(1 << i for i in subset))
    return result


def _reference_improve(v, pick, masks, draw_n, frozen, max_candidates):
    """Căutarea dinaintea accelerării: evenimente prin reuniuni de supramulțimi,
    rânduri complete pentru fiecare candidat, cheie (câștig, rânduri)."""
    from math import comb

    from covering.higher_hits import _layers
    from covering.profile_swap import MAX_PASSES, TARGETS, _row

    thresholds = tuple(range(1, pick + 1))
    selected = list(masks)
    tickets = [{t: _reference_event(v, m, t) for t in thresholds} for m in selected]
    current = {t: 0 for t in thresholds}
    for ev in tickets:
        for t in thresholds:
            current[t] |= ev[t]
    rows = {t: _row(v, current[t], t) for t in thresholds}
    full = {t: comb(v, t) for t in thresholds}
    strict = {t: min(draw_n, v) - t + 1 for t in TARGETS if t <= pick and t <= draw_n}
    order = tuple(sorted(thresholds, reverse=True))
    blocks = tuple(sum(1 << i for i in b) for b in combinations(range(v), pick))
    occupied = set(selected)
    swaps = evaluated = 0
    for _ in range(MAX_PASSES):
        changed = False
        for slot in range(frozen, len(selected)):
            if evaluated >= max_candidates:
                break
            old = selected[slot]
            others = {t: 0 for t in thresholds}
            for i, ev in enumerate(tickets):
                if i != slot:
                    for t in thresholds:
                        others[t] |= ev[t]
            required = 0
            for t in thresholds:
                if rows[t][0] == full[t]:
                    lost = current[t] & ~others[t] & _layers(v)[t]
                    while lost:
                        low = lost & -lost
                        required |= low.bit_length() - 1
                        lost ^= low
            if required.bit_count() >= pick:
                continue
            best = best_key = None
            for block in blocks:
                if evaluated >= max_candidates:
                    break
                if block in occupied or block & required != required:
                    continue
                evaluated += 1
                new_rows = {}
                for t in order:
                    row = _row(v, others[t] | _reference_event(v, block, t), t)
                    if any(a < b for a, b in zip(row, rows[t])):
                        break
                    new_rows[t] = row
                else:
                    gain = sum(
                        new_rows[t][i] - rows[t][i]
                        for t, n in strict.items()
                        for i in range(n)
                    )
                    key = (gain, tuple(new_rows[t] for t in order))
                    if gain > 0 and (best_key is None or key > best_key):
                        best, best_key = block, key
            if best is not None:
                occupied.discard(old)
                occupied.add(best)
                selected[slot] = best
                tickets[slot] = {t: _reference_event(v, best, t) for t in thresholds}
                current = {t: others[t] | tickets[slot][t] for t in thresholds}
                rows = {t: _row(v, current[t], t) for t in thresholds}
                swaps += 1
                changed = True
        if not changed or evaluated >= max_candidates:
            break
    return tuple(selected), swaps, evaluated


def test_threshold_events_match_the_definition():
    import random

    from covering.profile_swap import _threshold_events

    rng = random.Random(7)
    for _ in range(40):
        v = rng.randint(5, 12)
        pick = rng.randint(3, min(6, v - 1))
        mask = sum(1 << i for i in rng.sample(range(v), pick))
        events = _threshold_events(v, mask, pick)
        assert events[0] == (1 << (1 << v)) - 1
        for t in range(1, pick + 1):
            assert events[t] == _reference_event(v, mask, t)


def _random_wheels(seed, count):
    import random

    rng = random.Random(seed)
    for _ in range(count):
        v = rng.randint(6, 12)
        pick = rng.randint(3, min(6, v - 1))
        blocks = list(combinations(range(v), pick))
        chosen = rng.sample(blocks, rng.randint(2, min(24, len(blocks))))
        masks = tuple(sum(1 << i for i in b) for b in chosen)
        draw_n = rng.choice([pick, 5, 6])
        frozen = rng.randint(0, len(masks) - 1)
        yield v, pick, masks, max(3, draw_n), frozen, rng.choice([1500, 3000])


def test_fast_search_returns_exactly_the_reference_result():
    """Aceleași bilete, aceleași schimburi, același număr de candidați evaluați,
    inclusiv prefix înghețat, extrageri de 5 și 6 și plafon de candidați."""
    from covering.profile_swap import _improve

    seen_swaps = 0
    for args in _random_wheels(1, 16):
        _improve.cache_clear()
        fast = _improve(*args)
        assert fast == _reference_improve(*args), args[:2]
        seen_swaps += fast[1]
    # Testul trebuie să conțină și schimburi, nu doar intrări lăsate neschimbate.
    assert seen_swaps > 0


def test_equal_rows_keep_the_first_block_like_the_reference():
    """Două bilete: blocurile disjuncte de al doilea au rânduri identice. Ca în
    căutarea de referință, rămâne primul în ordine lexicografică."""
    from covering.profile_swap import _improve

    args = (9, 4, (0b1111, 0b10111), 6, 0, 3000)
    _improve.cache_clear()
    assert _improve(*args) == _reference_improve(*args) == ((0b11101000, 0b10111), 1, 496)


def test_fast_search_matches_the_reference_on_a_lotto_design(monkeypatch):
    from covering.profile_swap import _improve

    captured = []

    def _capture(pool, tickets, draw_n=None, frozen=0, **kwargs):
        captured.append((list(pool), [sorted(t) for t in tickets], draw_n, frozen))
        return [sorted(t) for t in tickets], {"applied": False}

    monkeypatch.setattr(dispatch, "improve_hit_profile", _capture)
    dispatch.generate_wheel("auto", list(range(1, 12)), 6, 3, 0, None, condition=4, draw_n=6)
    assert captured
    pool, tickets, draw_n, frozen = captured[0]
    pos = {n: i for i, n in enumerate(pool)}
    masks = tuple(sum(1 << pos[n] for n in t) for t in tickets)
    args = (len(pool), 6, masks, draw_n, frozen, 2000)
    _improve.cache_clear()
    assert _improve(*args) == _reference_improve(*args)
