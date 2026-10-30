"""Official category rules, actual variants and unavailable evidence boundaries."""

from dataclasses import dataclass
from datetime import date

import pytest

from loto_enterprise.core.prize_categories import resolve_variant_category


@dataclass(frozen=True)
class PublishedCategory:
    category: str
    matches_label: str


@pytest.mark.parametrize("hits,category", [(0, None), (1, None), (2, None), (3, "IV"), (4, "III"), (5, "II"), (6, "I")])
def test_649_categories_are_per_actual_simple_variant(hits, category):
    variant = list(range(1, hits + 1)) + list(range(20, 26 - hits))
    result = resolve_variant_category("6/49", variant, [1, 2, 3, 4, 5, 6], draw_date="2026-10-29")
    assert result.category == category
    assert result.main_hits == hits
    assert result.status == ("matched" if category else "no_prize")


@pytest.mark.parametrize("when", ["2010-05-01", "01-05-2010", date(2004, 6, 1)])
def test_three_hits_before_fourth_category_existed_are_not_a_prize(when):
    result = resolve_variant_category("6/49", [1, 2, 3, 40, 41, 42], [1, 2, 3, 4, 5, 6], draw_date=when)
    assert result.category is None and result.status == "no_prize"


def test_649_fourth_category_starts_on_official_faq_boundary():
    result = resolve_variant_category("6/49", [1, 2, 3, 40, 41, 42], [1, 2, 3, 4, 5, 6], draw_date="2010-05-02")
    assert result.category == "IV"


def test_three_hits_without_historical_date_require_published_fourth_category():
    args = ("6/49", [1, 2, 3, 40, 41, 42], [1, 2, 3, 4, 5, 6])
    assert resolve_variant_category(*args).status == "missing_draw_date"
    assert resolve_variant_category(*args, available_categories=["I", "II", "III"]).status == "missing_draw_date"
    assert resolve_variant_category(*args, available_categories=[PublishedCategory("IV", "3/6")]).category == "IV"


def test_540_first_five_official_numbers_win_i_only():
    drawn = [26, 39, 40, 18, 1, 2]  # Actual official extraction order is material.
    result = resolve_variant_category("5/40", [40, 1, 26, 18, 39], drawn)
    assert result.category == "I" and result.main_hits == 5
    assert result.possible_categories == ("I",)


def test_540_five_hits_including_sixth_ball_win_ii():
    result = resolve_variant_category("Loto 5/40", [26, 39, 40, 18, 2], [26, 39, 40, 18, 1, 2])
    assert result.category == "II" and result.main_hits == 5


@pytest.mark.parametrize("variant,category", [([26, 39, 40, 2, 3], "III"), ([26, 39, 40, 3, 4], None)])
def test_540_four_hits_use_all_six_and_three_hits_do_not_win(variant, category):
    result = resolve_variant_category("loto_5_40", variant, [26, 39, 40, 18, 1, 2])
    assert result.category == category


def test_540_five_hits_cannot_be_classified_from_a_sorted_draw():
    result = resolve_variant_category("5/40", [1, 2, 18, 26, 39], [1, 2, 18, 26, 39, 40], draw_order_known=False)
    assert result.category is None
    assert result.status == "ambiguous_category"
    assert result.possible_categories == ("I", "II")


@pytest.mark.parametrize("hits,match,category", [(5, True, "I"), (5, False, "II"), (4, True, "III"), (4, False, "IV"), (3, True, "V"), (3, False, "VI"), (2, True, "VII"), (2, False, None), (1, True, "VIII"), (1, False, None), (0, True, None), (0, False, None)])
def test_every_joker_category_depends_on_the_same_actual_variant(hits, match, category):
    main = list(range(1, hits + 1)) + list(range(30, 35 - hits))
    result = resolve_variant_category("Joker", main + [20 if match else 19], [1, 2, 3, 4, 5], official_joker=20)
    assert result.category == category
    assert result.main_hits == hits
    assert result.joker_hit is match


def test_joker_ball_is_a_separate_urn_even_if_number_repeats_in_main_variant():
    result = resolve_variant_category("joker", [1, 2, 3, 4, 5, 1], [1, 2, 3, 4, 5], official_joker=1)
    assert result.category == "I"


def test_tied_main_hit_variants_can_have_different_categories():
    args = ("joker", [1, 2, 3, 4, 5])
    without_joker = resolve_variant_category(args[0], [1, 2, 3, 4, 30, 19], args[1], official_joker=20)
    with_joker = resolve_variant_category(args[0], [1, 2, 3, 4, 31, 20], args[1], official_joker=20)
    assert without_joker.main_hits == with_joker.main_hits == 4
    assert without_joker.category == "IV" and with_joker.category == "III"


def test_known_joker_hit_flag_can_classify_legacy_five_number_variant():
    result = resolve_variant_category("joker", [1, 2, 3, 4, 30], [1, 2, 3, 4, 5], joker_hit=True)
    assert result.category == "III"


def test_unknown_joker_is_not_silently_assumed_to_have_missed():
    result = resolve_variant_category("joker", [1, 2, 3, 4, 30], [1, 2, 3, 4, 5], official_joker=20)
    assert result.category is None and result.status == "missing_joker"
    assert result.possible_categories == ("III", "IV")


def test_contradictory_joker_flag_is_not_used_to_claim_a_prize():
    result = resolve_variant_category("joker", [1, 2, 3, 4, 30, 19], [1, 2, 3, 4, 5], official_joker=20, joker_hit=True)
    assert result.category is None and result.status == "invalid_variant"


def test_published_category_must_exist_and_cannot_be_downgraded():
    result = resolve_variant_category("5/40", [1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 6], available_categories=["II", "III"])
    assert result.category is None and result.status == "category_unavailable"
    assert result.possible_categories == ("I",)


@pytest.mark.parametrize("label,status", [("5/6*", "matched"), ("5/5", "matched"), ("5/6", "unsupported_rules"), ("4/6", "unsupported_rules")])
def test_published_540_rule_signature_is_checked(label, status):
    result = resolve_variant_category("5/40", [1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 6], available_categories=[PublishedCategory("I", label)])
    assert result.status == status


@pytest.mark.parametrize("table", [["III (4/6)"], {"III": {"matches_label": "4/6"}}, [PublishedCategory("III", "4/6")]])
def test_published_category_formats_keep_category_and_match_signature(table):
    result = resolve_variant_category("6/49", [1, 2, 3, 4, 30, 31], [1, 2, 3, 4, 5, 6], available_categories=table)
    assert result.category == "III"


@pytest.mark.parametrize("game,variant", [("6/49", list(range(1, 17))), ("5/40", list(range(1, 20))), ("joker", list(range(1, 16)))])
def test_pool_union_is_never_treated_as_a_played_simple_variant(game, variant):
    result = resolve_variant_category(game, variant, [1, 2, 3, 4, 5, 6])
    assert result.category is None and result.status == "invalid_variant"


@pytest.mark.parametrize("variant", [None, []])
def test_missing_variants_cannot_be_recovered_from_a_pool_hit_count(variant):
    assert resolve_variant_category("6/49", variant, [1, 2, 3, 4, 5, 6]).status == "missing_variant"


@pytest.mark.parametrize("variant", [[1, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 50], [1, 2, 3, 4, 5, 6.5], [True, 2, 3, 4, 5, 6]])
def test_invalid_actual_variants_do_not_get_a_category(variant):
    assert resolve_variant_category("6/49", variant, [1, 2, 3, 4, 5, 6]).status == "invalid_variant"


def test_incomplete_official_draw_is_not_reconstructed_or_guessed():
    assert resolve_variant_category("5/40", [1, 2, 3, 4, 5], [1, 2, 3, 4, 5]).status == "invalid_draw"


@pytest.mark.parametrize("value", ["123456", 123456])
def test_a_scalar_is_not_split_into_lottery_numbers(value):
    numbers = [1, 2, 3, 4, 5, 6]
    assert resolve_variant_category("6/49", value, numbers).status == "invalid_variant"
    assert resolve_variant_category("6/49", numbers, value).status == "invalid_draw"


def test_joker_urn2_or_foreign_games_do_not_borrow_romanian_main_rules():
    for game in ("joker_urna2", "de_lotto", "DE 6/49"):
        assert resolve_variant_category(game, [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]).status == "unsupported_game"
