"""Scheme reduse oficiale — datele publice din official_schemes.py.

Nu importă NiceGUI / engine: trebuie să treacă pe Python 3.12 din VM.
"""
from __future__ import annotations

from itertools import combinations

from official_schemes import (
    COD_48_PRIZE_PATTERNS,
    COD_48_TICKETS,
    LR_SCHEMES,
    URL_649_SCHEMES,
    category_counts_649,
    format_official_block,
    our_cover_size,
    schonheim,
)


def test_n_full_is_complete_system():
    from math import comb
    from official_schemes import SCHEMES
    pick = {"6/49": 6, "5/40": 5, "joker": 5}
    for game, items in SCHEMES.items():
        for s in items:
            assert s.n_full == comb(s.n_numbers, pick[game]), s


def test_649_table1_matches_loto_ro():
    from official_schemes import SCHEMES
    got = {(s.code, s.n_numbers, s.n_variants, s.n_full) for s in SCHEMES["6/49"]}
    assert got == {
        (48, 9, 12, 84),
        (49, 10, 15, 210),
        (50, 10, 30, 210),
        (56, 11, 66, 462),
        (57, 12, 22, 924),
        (58, 12, 132, 924),
        (59, 16, 112, 8008),
    }


def test_lr_schemes_keeps_original_agency_codes():
    """Pool-urile afișate înainte rămân; s-au adăugat și pool-uri ≤16."""
    assert LR_SCHEMES["6/49"][9] == [("Cod 48", 12)]
    assert ("Cod 49", 15) in LR_SCHEMES["6/49"][10]
    assert ("Cod 50", 30) in LR_SCHEMES["6/49"][10]
    assert ("Cod 57", 22) in LR_SCHEMES["6/49"][12]
    assert ("Cod 58", 132) in LR_SCHEMES["6/49"][12]
    assert LR_SCHEMES["5/40"][7] == [("Cod 15", 9)]
    assert ("Cod 17", 30) in LR_SCHEMES["5/40"][9]
    assert ("Cod 45", 5) in LR_SCHEMES["joker"][7]


def test_cod48_tickets_are_the_official_twelve():
    assert len(COD_48_TICKETS) == 12
    assert len({tuple(sorted(t)) for t in COD_48_TICKETS}) == 12
    assert all(len(t) == 6 and set(t) <= set(range(1, 10)) for t in COD_48_TICKETS)


def test_cod48_is_3_and_4_cover_of_nine():
    pool = list(range(1, 10))
    covered3 = set()
    covered4 = set()
    covered5 = set()
    for t in COD_48_TICKETS:
        covered3.update(combinations(sorted(t), 3))
        covered4.update(combinations(sorted(t), 4))
        covered5.update(combinations(sorted(t), 5))
    assert covered3.issuperset(combinations(pool, 3))
    assert covered4.issuperset(combinations(pool, 4))
    # Nu e 5-cover: pagina oficială spune explicit că 5 din 9 poate fi fără cat. II.
    assert not covered5.issuperset(combinations(pool, 5))


def test_cod48_prize_index_matches_loto_ro():
    pool = list(range(1, 10))
    for k, expected in COD_48_PRIZE_PATTERNS.items():
        seen = set()
        for drawn in combinations(pool, k):
            seen.add(category_counts_649(COD_48_TICKETS, drawn))
        assert seen == expected, f"k={k}: {seen} != {expected}"


def test_cod57_cannot_be_4_cover():
    assert 22 < schonheim(12, 6, 4)
    md = format_official_block(
        "6/49", 12, price=8.0, pick=6,
        full_lbl="Sistem complet C(12,6) = 924 var. ≈ 7,392 Lei",
        our_guarantee=4, our_n_tickets=41,
    )
    assert "nu e documentat" not in md.lower()
    assert "Cod 57" in md and "Cod 58" in md
    assert URL_649_SCHEMES in md
    assert "nu poate fi 4-cover" in md
    assert "3, 4, 5 sau 6" in md


def test_cod48_block_quotes_official_minima():
    md = format_official_block(
        "6/49", 9, price=8.0, pick=6,
        full_lbl="x", our_guarantee=4, our_n_tickets=12,
    )
    assert "≥2× cat. IV" in md
    assert "≥1× cat. III" in md
    ours = our_cover_size(9, 6, 4)
    if ours == 12:
        assert "același număr" in md


def test_540_cod15_mentions_cat3_not_3hit_prize():
    md = format_official_block(
        "5/40", 7, price=5.0, pick=5,
        full_lbl="x", our_guarantee=4,
    )
    assert "Cod 15" in md
    assert "nu există cat. pentru 3 hituri" in md
    assert "4 din 7" in md


def test_joker_n1_and_no_fake_t():
    md = format_official_block(
        "joker", 12, price=7.0, pick=5,
        full_lbl="x", joker_note=" · 1 nr. joker/bilet",
        our_guarantee=4,
    )
    assert "Cod 14" in md
    assert "N=1" in md
    assert "fără index t public" in md
    assert "nu e documentat" not in md.lower()


def test_app_no_longer_claims_undocumented():
    from pathlib import Path
    text = Path("app_nicegui.py").read_text(encoding="utf-8")
    assert "Garanția schemelor oficiale nu e documentată" not in text
    assert "format_official_block" in text


def test_no_scheme_for_unlisted_pool():
    md = format_official_block(
        "6/49", 8, price=8.0, pick=6, full_lbl="Sistem complet",
    )
    assert "fără schemă redusă oficială" in md
    assert "Cod 48" not in md
