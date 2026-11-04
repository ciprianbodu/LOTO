"""Independent exact checks for replaying the three requested official schemes."""
from collections import Counter
from itertools import combinations, islice
from math import comb
import random

import numpy as np
import pytest

from covering import lr_schemes as lr
from loto_enterprise.core.generated_prize import estimate_generated_prize
from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw


CASES = (("6/49", 16, 59, 49, 6), ("joker", 15, 12, 45, 5),
         ("5/40", 19, 48, 40, 6))
LABELS = {
    "6/49": ("6/6", "5/6", "4/6", "3/6"),
    "5/40": ("5/5", "5/6", "4/6"),
    "joker": ("5/5+J", "5/5", "4/5+J", "4/5", "3/5+J", "3/5", "2/5+J", "1/5+J"),
}
NAMES = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")


def scheme_for(game, n, code):
    return next(s for s in lr.lr_schemes(game, n) if s.code == code)


def sample(case, seed, hits):
    game, n, code, maximum, drawn = case
    rng = random.Random(seed)
    pool = rng.sample(range(1, maximum + 1), n)
    outside = sorted(set(range(1, maximum + 1)) - set(pool))
    draw = rng.sample(pool, hits) + rng.sample(outside, drawn - hits)
    rng.shuffle(draw)  # 5/40 retains this order, including its first five.
    return scheme_for(game, n, code), pool, draw


def tickets(scheme, pool, joker=None):
    numbers = sorted(set(pool))
    out = [[numbers[p - 1] for p in block] for block in scheme.variants]
    return [v + [joker] for v in out] if scheme.game == "joker" else out


def prize_draw(game, draw, joker=7, *, known=True):
    categories = tuple(
        PrizeCategory(name, label, 1, (i + 1) * 100, None, (i + 1) * 100)
        for i, (name, label) in enumerate(zip(NAMES, LABELS[game]))
    )
    return PrizeDraw(game, "2026-11-01", tuple(draw), joker if game == "joker" else None,
                     categories, "https://www.loto.ro/", order_known=known)


REPLAY_CASES = [
    (case, seed, hits) for case in CASES
    for hits in range(case[-1] + 1) for seed in range(15)
]


@pytest.mark.parametrize("case,seed,hits", REPLAY_CASES)
def test_best_and_groups_match_full_official_ticket_enumeration(case, seed, hits):
    scheme, pool, draw = sample(case, seed + 1000 * hits, hits)
    actual = tickets(scheme, pool, joker=7)
    main = [v[:5] if scheme.game == "joker" else v for v in actual]
    counted = [len(set(v).intersection(draw)) for v in main]
    maximum = max(counted)
    first = counted.index(maximum)
    assert lr.best_hits(scheme, pool, draw) == maximum
    assert lr.best_variant(scheme, pool, draw) == (main[first], maximum)
    compact = lr.scheme_prize_groups(scheme, pool, draw, joker=7)
    expected = {}
    for variant, numbers in zip(actual, main):
        signature = tuple(sorted(set(numbers).intersection(draw)))
        if signature not in expected:
            expected[signature] = {"variant": variant, "count": 0}
        expected[signature]["count"] += 1
    assert compact == {"version": 1, "total_variants": scheme.size,
                       "groups": list(expected.values())}
    assert len(compact["groups"]) <= 2 ** hits
    assert sum(group["count"] for group in compact["groups"]) == scheme.size


@pytest.mark.parametrize("case", CASES)
def test_exhaustive_draw_subsets_preserve_first_maximum_and_exact_odds(case):
    game, n, code, maximum, draw_n = case
    scheme = scheme_for(game, n, code)
    pool = list(range(1, n + 1))
    masks = np.asarray([sum(1 << (p - 1) for p in v) for v in scheme.variants],
                       dtype=np.uint32)
    counts = {}
    for size in range(draw_n + 1):
        subsets = combinations(pool, size)
        histogram = Counter()
        while batch := list(islice(subsets, 128)):
            draw_masks = np.asarray([sum(1 << (p - 1) for p in row) for row in batch],
                                    dtype=np.uint32)
            matches = np.bitwise_count(draw_masks[:, None] & masks[None, :])
            first = matches.argmax(axis=1)
            best = matches.max(axis=1)
            for row, index, score in zip(batch, first, best):
                expected = (list(scheme.variants[int(index)]), int(score))
                assert lr.best_variant(scheme, pool, row) == expected
                assert lr.best_hits(scheme, pool, row) == int(score)
                histogram[int(score)] += 1
        assert sum(histogram.values()) == comb(n, size)
        counts[size] = histogram
    numerators = {
        t: sum(sum(value for hit, value in counts[size].items() if hit >= t)
               * comb(maximum - n, draw_n - size)
               for size in range(draw_n + 1))
        for t in range(3, len(scheme.variants[0]) + 1)
    }
    expected = {"6/49": {3: 4168472, 4: 1039192, 5: 29008, 6: 112},
                "joker": {3: 241878, 4: 19293, 5: 118},
                "5/40": {3: 2374050, 4: 1085280, 5: 31473}}
    assert numerators == expected[game]
    assert numerators == lr.scheme_numerators(scheme)


PRIZE_CASES = [(case, hits, seed) for case in CASES
               for hits in range(case[-1] + 1) for seed in range(6)]


@pytest.mark.parametrize("case,hits,seed", PRIZE_CASES)
def test_compact_prize_matches_every_generated_variant(case, hits, seed):
    scheme, pool, draw = sample(case, 100 + 50 * seed + hits, hits)
    chosen = 7 if seed % 2 == 0 else 9
    official = prize_draw(case[0], draw, joker=7, known=seed % 3 != 0)
    variants = tickets(scheme, pool, joker=chosen)
    kwargs = dict(target_numbers=draw, target_date=official.draw_date,
                  expected_count=scheme.size, pool_numbers=pool)
    full = estimate_generated_prize(case[0], variants, official, **kwargs)
    summary = lr.scheme_prize_groups(scheme, pool, draw, joker=chosen)
    compact = estimate_generated_prize(case[0], [], official,
                                       variant_prize_groups=summary, **kwargs)
    if full.status == "ambiguous_category":
        # The UI falls back to the full list to preserve partial counters from
        # the validator's early return. Completed payouts remain exactly equal.
        assert compact.status == full.status
        assert compact.reason == full.reason
        assert compact.total_bani == full.total_bani is None
    else:
        assert compact == full


@pytest.mark.parametrize("chosen", [None, True, False, 7.25, "bad", 0, 21, 7, 7.0, "7"])
def test_chosen_joker_is_not_coerced_or_silently_repaired(chosen):
    scheme, pool, draw = sample(CASES[1], 123, 5)
    official = prize_draw("joker", draw, joker=7)
    variants = tickets(scheme, pool, joker=chosen)
    kwargs = dict(target_numbers=draw, target_date=official.draw_date,
                  expected_count=scheme.size, pool_numbers=pool)
    summary = lr.scheme_prize_groups(scheme, pool, draw, joker=chosen)
    assert all(group["variant"][-1] is chosen for group in summary["groups"])
    assert estimate_generated_prize("joker", [], official,
                                   variant_prize_groups=summary, **kwargs) == (
        estimate_generated_prize("joker", variants, official, **kwargs))


def test_540_first_five_categories_remain_distinct():
    scheme = scheme_for("5/40", 19, 48)
    pool = list(range(1, 20))
    first = list(scheme.variants[0])
    extra = next(n for n in pool if n not in first)
    draw = first + [extra]
    summary = lr.scheme_prize_groups(scheme, pool, draw)
    variants = tickets(scheme, pool)
    outputs = []
    for ordered in (draw, draw[1:] + draw[:1]):
        official = prize_draw("5/40", ordered)
        kwargs = dict(target_numbers=ordered, target_date=official.draw_date,
                      expected_count=scheme.size, pool_numbers=pool)
        old = estimate_generated_prize("5/40", variants, official, **kwargs)
        new = estimate_generated_prize("5/40", [], official,
                                       variant_prize_groups=summary, **kwargs)
        assert new == old
        outputs.append(new)
    assert outputs[0].category_counts.get("I", 0) == 1
    assert outputs[0].category_counts != outputs[1].category_counts


def test_derived_mask_cache_does_not_hash_the_large_scheme(monkeypatch):
    scheme = scheme_for("5/40", 19, 48)
    scheme.__dict__.pop("_position_data", None)
    def no_hash(_):
        raise AssertionError("Replay must not hash the 930-variant scheme")
    monkeypatch.setattr(lr.LRScheme, "__hash__", no_hash)
    assert lr.best_hits(scheme, range(1, 20), [1, 2, 3, 4, 25, 26]) == 4
    assert lr.best_variant(scheme, range(1, 20), [1, 2, 3, 4, 25, 26])[1] == 4
    assert lr.scheme_prize_groups(scheme, range(1, 20), [1, 2, 3, 4, 25, 26])["total_variants"] == 930
    cached = scheme._position_data
    assert scheme._position_data is cached


def test_summary_mutation_does_not_change_scheme_or_later_replays():
    scheme, pool, draw = sample(CASES[2], 100, 6)
    before = scheme.variants
    a = lr.scheme_prize_groups(scheme, pool, draw)
    b = lr.scheme_prize_groups(scheme, pool, draw)
    a["groups"][0]["variant"][0] = -999
    a["groups"][0]["count"] = -999
    assert lr.scheme_prize_groups(scheme, pool, draw) == b
    assert scheme.variants == before


@pytest.mark.parametrize("pool", [[], [1, 2], list(range(1, 16)), list(range(1, 18))])
def test_wrong_pool_size_keeps_existing_error(pool):
    scheme = scheme_for("6/49", 16, 59)
    for fn in (lr.best_hits, lr.best_variant, lr.scheme_prize_groups):
        with pytest.raises(ValueError, match="Cod 59 cere 16 numere"):
            fn(scheme, pool, [1, 2, 3, 4, 5, 6])


def test_empty_scheme_best_hits_keeps_existing_value_error():
    scheme = lr.LRScheme("6/49", 999, 16, ())
    with pytest.raises(ValueError):
        lr.best_hits(scheme, range(1, 17), [1, 2, 3, 4, 5, 6])


def test_first_maximum_tie_preserves_the_official_first_row():
    scheme = scheme_for("6/49", 16, 59)
    pool = list(reversed(range(1, 17)))
    assert lr.best_variant(scheme, pool, [30, 31, 32, 33, 34, 35]) == (
        list(scheme.variants[0]), 0)
