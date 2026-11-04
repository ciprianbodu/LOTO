"""Exact swap accounting and certified geometry improvements, November 2026.

The golden hashes come from the unmodified 2026-10-31 large-pool algorithm.
Delta checks independently enumerate the covered family before/after a swap.
"""

from collections import Counter
import hashlib
import itertools
import math
import random

import pytest

from covering import large_pool as large
from covering.probability import wheel_hit_profile, wheel_hit_probabilities


@pytest.mark.parametrize("seed", range(240))
def test_swap_delta_equals_independent_full_enumeration(seed):
    rng = random.Random(seed)
    v = rng.randrange(7, 14)
    pick = rng.randrange(3, min(v, 6) + 1)
    blocks = list(itertools.combinations(range(v), pick))
    wheel = rng.sample(blocks, rng.randrange(2, min(20, len(blocks))))
    replacement = rng.choice(blocks)
    removed = rng.randrange(len(wheel))
    after = wheel[:removed] + [replacement] + wheel[removed + 1:]
    for size in range(1, pick + 1):
        counts = Counter(c for t in wheel for c in itertools.combinations(t, size))
        old = frozenset(itertools.combinations(wheel[removed], size))
        new = frozenset(itertools.combinations(replacement, size))
        unique = frozenset(c for c in old if counts[c] == 1)
        expected = len({c for t in after for c in itertools.combinations(t, size)}) - len(counts)
        assert large._replacement_gain(counts, unique, new) == expected


def test_common_unique_group_must_not_be_subtracted():
    old = frozenset(itertools.combinations((0, 1, 2, 3, 4, 5), 4))
    new = frozenset(itertools.combinations((0, 1, 2, 3, 4, 6), 4))
    counts = Counter(old)
    # Both single-ticket families cover 15 groups. Their 5 shared groups stay.
    assert len(old & new) == 5
    assert large._replacement_gain(counts, old, new) == 0
    assert sum(c not in counts for c in new) - len(old) == -5


_LEGACY_GOLDENS = [
    (20, 5, 3, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (20, 5, 3, 10, '5a3904efdf449319c52c2a447aced54c04accfeb5634a7b259f6e7fe88b516b5'),
    (20, 5, 3, 32, 'f07c6ee820eee35c0b57609263df6b810b5d488c98790d8e424b0281091bc2b4'),
    (20, 5, 4, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (20, 5, 4, 10, '2e1d600161aded58d0a5f491ec42d054179589a4ffdb6a6f54ff54ef5b9f0f30'),
    (20, 5, 4, 32, '3dcfeb04588d869927ccd6574bcbc60ca3e164ddbc07c7b344e629abc1d3f36f'),
    (20, 6, 3, 4, '2898c2d0863e662f4a52cf128781a3d273c853fb1c1109a83f7c6658518829ca'),
    (20, 6, 3, 10, '2c0296b97418acc0c8e66da13fd4fa6f74681b9ff786cccbee2e134b28585ee0'),
    (20, 6, 3, 32, '5df88e0e47861b63abfdf9e642a445c7f328915f954afd52910d3924fc781bb6'),
    (20, 6, 4, 4, '1ee68aa14b8a683416d04500afdaf950ac7ad07f02a24b1a748fc3122b63e786'),
    (20, 6, 4, 10, 'a150091dc071a2fc261dd511c763933d2c1ce2e17cf50886a93619352aee64f7'),
    (20, 6, 4, 32, '0733fbe28bd0dbb4b2eec27e1730b55e3cd694fa10c8f811554ccdf3fc037e8e'),
    (21, 5, 3, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (21, 5, 3, 10, '3813763a7459bbb27d691dae447a78a3afcdc7cfd61d0c3d3126e4c81b4c8e38'),
    (21, 5, 3, 32, '9e63906bd1cdac86d9a8ec27f85e459fb3a9ab252b197e7adbc6a427f13f5b45'),
    (21, 5, 4, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (21, 5, 4, 10, 'fb1792b3f4ef76a548c9c42e5e142093977420109c0063169495d6a739ec19e2'),
    (21, 5, 4, 32, 'd54d2ef7e63c31ba6fd1d47173cd4238a1c39604e32328e8ad56935797603734'),
    (21, 6, 3, 4, 'f3958cf89be79244961c48bf460f4cf60c049b9ea722a7a605579d52dbead66b'),
    (21, 6, 3, 10, '80a16b7a0ae92e4149e6cae4d771dd876f057becd74d92564ceb21391c15d075'),
    (21, 6, 3, 32, '9098d39470da2701042d158c1f5268936b401a629a1711f10c73a1c01d6fd8da'),
    (21, 6, 4, 4, 'f3958cf89be79244961c48bf460f4cf60c049b9ea722a7a605579d52dbead66b'),
    (21, 6, 4, 10, 'a58796ead50c03eb35cc210770679078bbafa2a18882d338a3bef869a55c04ed'),
    (21, 6, 4, 32, '327d78d672665af4c0e33ee076d17b96095ca6b2d0fafd815931083e0f742db5'),
    (24, 5, 3, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (24, 5, 3, 10, '6eeb541098ede52e8ceddc1610790e8d84095d37a4193ae04b722d94baccf99f'),
    (24, 5, 3, 32, '42194eb21858c266579752876f43c994bfd74130de638c9283eff02d8e3923f8'),
    (24, 5, 4, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (24, 5, 4, 10, 'ca7cde46594cc64b5ca3bf10b59acdac453e8d8675f5e11080ce3fc6bab2f3d7'),
    (24, 5, 4, 32, 'b2d6df6f2e1f6581eeee0a079d7a8ae3f0de24d2bd79a709af64f994f5dbb03f'),
    (24, 6, 3, 4, '093b0e07a5eb9011bcea43134f4a4178e7833b8b29a13141922d4d7f55f7a395'),
    (24, 6, 3, 10, '90f550d23cdddc1c244a456b4a0a2b5cae26c46712ca8cc2794b3f4483278507'),
    (24, 6, 3, 32, '6f7e2ce47b8197d4ea0b5a58ebd8e6c00c74f51db6fbc779c839fea352bc5532'),
    (24, 6, 4, 4, '093b0e07a5eb9011bcea43134f4a4178e7833b8b29a13141922d4d7f55f7a395'),
    (24, 6, 4, 10, '648417dcf7d9e05dfb20241daa66c606323ef2d22f10792ac8a625725dc445f9'),
    (24, 6, 4, 32, '5c2bc6adbf27bcebfe2d4dc18b21adfae3ac6fd0e5f39d7ac95d5f37a3c5037a'),
    (30, 5, 3, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (30, 5, 3, 10, 'af766508cae65c316f50fc635f9f35ec126f4e6cd36c6f48d900ccf6558db969'),
    (30, 5, 3, 32, '026c563e756e019eedb585d3f54dd4e54b517b5534a73261ff02cc65f1c7f15b'),
    (30, 5, 4, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (30, 5, 4, 10, 'ceddd933e2082f20c3e5b4ef2ed6a7b7dfd2dbecf65e14d607f2a0925cc165e4'),
    (30, 5, 4, 32, 'b8f2cc5c5348f3a4c288b86abceef9f06a53d8af6c703d49d080db07d283e1aa'),
    (30, 6, 3, 4, '093b0e07a5eb9011bcea43134f4a4178e7833b8b29a13141922d4d7f55f7a395'),
    (30, 6, 3, 10, '1c4e0ecb83404f63e8953665b650d575e0eeb6e3b3a8317be1455151bdf0dc43'),
    (30, 6, 3, 32, '70ca4a74aa7dc5aafa2e5dcdad1158036c975378fec2288b1db4e6fb40e70ebe'),
    (30, 6, 4, 4, '093b0e07a5eb9011bcea43134f4a4178e7833b8b29a13141922d4d7f55f7a395'),
    (30, 6, 4, 10, 'f4b7de48a154dde7e08dcb0c8f75d01bc6551d594c364792c91cb95b4f62151a'),
    (30, 6, 4, 32, 'e944de7c96a3f8918640ef3936a29abfd5e6d5a6296bf0afb26cf4cd48c89e2b'),
    (49, 5, 3, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (49, 5, 3, 10, 'd523b628f258f77d04de51490705cbe99f218dcea42dcb3cddf2bed5222f279e'),
    (49, 5, 3, 32, 'd0b9d2870dba180dd5efbb8e40ed0f9e62046dd3ab6841949658db979b22cea8'),
    (49, 5, 4, 4, 'df5b8447d7d4c952683e74a406ec0eba95b397d0b0c3bbf797161ab52b76101c'),
    (49, 5, 4, 10, 'd523b628f258f77d04de51490705cbe99f218dcea42dcb3cddf2bed5222f279e'),
    (49, 5, 4, 32, '73528a62b3f4419fa6a96cb640eb8ff20c182d85ea6ff23666703e8a35ad1859'),
    (49, 6, 3, 4, '093b0e07a5eb9011bcea43134f4a4178e7833b8b29a13141922d4d7f55f7a395'),
    (49, 6, 3, 10, '91ad55e00220104f690fc389ee509e6b95df59bcca3df90d16f7e75b029316ad'),
    (49, 6, 3, 32, 'b634018876f215ad0f43fd00412a7d3253953da7647ace7982bc1932a7406d93'),
    (49, 6, 4, 4, '093b0e07a5eb9011bcea43134f4a4178e7833b8b29a13141922d4d7f55f7a395'),
    (49, 6, 4, 10, '5fea575df89a9cd1162d4678481c9a44fdbd8150f3426ee0b05eca2880db0860'),
    (49, 6, 4, 32, '42767abe628276276d42c827394c57a2185d766f79e0f008c86b1a10a8b46721'),
]


@pytest.mark.parametrize("v,pick,g,cap,digest", _LEGACY_GOLDENS)
def test_cached_legacy_search_matches_frozen_old_geometry(v, pick, g, cap, digest):
    # A conditional request suppresses refinement but uses exactly the same
    # initial construction. Run the compatibility search explicitly at v20.
    raw = large._canonical_cover(v, pick, g, cap, g + 1)
    actual = large._legacy_refine_capped_cover(v, pick, g, cap, raw)
    assert hashlib.sha256(repr(actual).encode("ascii")).hexdigest() == digest
    if v > 20:
        assert large._canonical_cover(v, pick, g, cap) == actual


@pytest.mark.parametrize("cap", (152, 250))
def test_certified_refinement_improves_exact_4plus_5plus_at_same_cost(cap):
    pool = tuple(range(20))
    raw = large._canonical_cover(20, 6, 4, cap, 5)
    baseline = large._legacy_refine_capped_cover(20, 6, 4, cap, raw)
    improved = large._refine_capped_cover(20, 6, 4, cap, raw)
    assert len(improved) == len(set(improved)) == len(baseline) == cap
    assert set(itertools.chain.from_iterable(baseline)) == set(itertools.chain.from_iterable(improved))
    before = wheel_hit_profile(pool, baseline)
    after = wheel_hit_profile(pool, improved)
    for size in range(3, 7):
        for target in range(3, min(6, size) + 1):
            assert after[size][target] >= before[size][target]
    to_numbers = lambda wheel: [tuple(n + 1 for n in t) for t in wheel]
    pb = wheel_hit_probabilities(range(1, 21), to_numbers(baseline), 6, 49)["ticket"]
    pa = wheel_hit_probabilities(range(1, 21), to_numbers(improved), 6, 49)["ticket"]
    assert pa[4] > pb[4]
    assert pa[5] > pb[5]
    assert pa[6] == pb[6] == cap / math.comb(49, 6)


def test_exact_certificate_rejects_candidate_losing_lower_threshold():
    raw = large._canonical_cover(20, 6, 4, 100, 5)
    baseline = large._legacy_refine_capped_cover(20, 6, 4, 100, raw)
    candidate = large._refine_capped_cover_exact(20, 6, 4, 100, baseline)
    before = wheel_hit_profile(range(20), baseline)
    after = wheel_hit_profile(range(20), candidate)
    assert after[4][4] > before[4][4]
    assert after[3][3] < before[3][3]
    assert large._refine_capped_cover(20, 6, 4, 100, raw) == baseline


def test_refinement_keeps_global_rng_untouched_and_is_reproducible():
    raw = large._canonical_cover(20, 6, 4, 152, 5)
    state = random.getstate()
    a = large._refine_capped_cover(20, 6, 4, 152, raw)
    random.seed(123456)
    try:
        b = large._refine_capped_cover(20, 6, 4, 152, raw)
        assert a == b
        assert random.getstate() == random.Random(123456).getstate()
    finally:
        random.setstate(state)


@pytest.mark.parametrize("v,pick,g,cap,c,expected", [
    (20,6,4,152,0,True), (20,5,4,404,4,True), (20,6,3,4096,3,True),
    (19,6,4,152,0,False), (21,6,4,152,0,False), (20,6,6,152,0,False),
    (20,6,4,0,0,False), (20,6,4,1,0,False), (20,6,4,4097,0,False),
    (20,7,4,152,0,False), (20,6,4,152,5,False), (20,6,0,152,0,False),
])
def test_certificate_scope(v,pick,g,cap,c,expected):
    assert large.certified_refinement_applies(v,pick,g,cap,c) is expected


def test_every_registered_game_fits_certificate_draw_limit():
    from loto_enterprise.core.lotteries import GEOMETRIES
    assert all(geo.draw_n <= large.MAX_CERTIFIED_DRAW for geo in GEOMETRIES.values())
