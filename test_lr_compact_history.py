"""Official LR replay must preserve terminal tickets, prizes and first maxima."""
from collections import Counter
from copy import deepcopy
import random

import pytest

import ui_prizes
from covering.lr_schemes import lr_schemes, scheme_tickets
from loto_enterprise.core.generated_prize import estimate_generated_prize
from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw


CONFIGS = (("6/49", 16, 59, 6, 6, 49),
           ("joker", 15, 12, 5, 5, 45),
           ("5/40", 19, 48, 5, 6, 40))
CATEGORIES = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")


def _case(config, seed):
    game, size, code, pick, drawn_n, universe = config
    rng = random.Random(64000 + seed)
    pool = rng.sample(range(1, universe + 1), size)
    hits = seed % (drawn_n + 1)
    outside = sorted(set(range(1, universe + 1)) - set(pool))
    drawn = rng.sample(pool, hits) + rng.sample(outside, drawn_n - hits)
    rng.shuffle(drawn)
    chosen = 7 if game == "joker" else None
    official_joker = (7 if seed % 2 else 13) if chosen else None
    row = {
        "label": "2026-11-01", "pool_numbers": pool, "drawn": drawn,
        "chosen_joker": chosen,
        "best_variant_raw": pool[:pick] + ([chosen] if chosen else []),
        "best_variant_joker_hit": chosen == official_joker if chosen else None,
        "best_ticket": 0, "expected_variant_count": 9999,
        "generated_variants": [], "virtual_full_system": seed % 2 == 0,
    }
    scheme = next(s for s in lr_schemes(game, size) if s.code == code)
    variants = scheme_tickets(scheme, pool)
    if chosen:
        variants = [v + [chosen] for v in variants]
    canonical = {"6/49": "loto_6_49", "5/40": "loto_5_40"}.get(game, game)
    official = PrizeDraw(
        canonical, row["label"], tuple(drawn), official_joker,
        tuple(PrizeCategory(c, "", 10, (i + 1) * 137, None, None)
              for i, c in enumerate(CATEGORIES)),
        "https://www.loto.ro/test", order_known=True,
    )
    return row, scheme, variants, official


def _category(game, variant, draw):
    """Independent current-category oracle, never using the production resolver."""
    main = variant[:5] if game == "joker" else variant
    hits = len(set(main) & set(draw.numbers))
    if game == "6/49":
        return {3: "IV", 4: "III", 5: "II", 6: "I"}.get(hits)
    if game == "5/40":
        if hits == 5:
            return "I" if set(main) == set(draw.numbers[:5]) else "II"
        return "III" if hits == 4 else None
    categories = ({1: "VIII", 2: "VII", 3: "V", 4: "III", 5: "I"}
                  if variant[-1] == draw.joker else {3: "VI", 4: "IV", 5: "II"})
    return categories.get(hits)


@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("seed", range(84))
def test_lr_compact_replay_matches_all_actual_tickets(config, seed, monkeypatch):
    game, _, _, pick, _, _ = config
    row, scheme, variants, official = _case(config, seed)
    source = deepcopy(row)
    played = ui_prizes.history_played_system(game, row)
    old_maximum = max(variants, key=lambda v: len(set(v[:pick]) & set(row["drawn"])))
    assert played["best_variant_raw"] == old_maximum
    assert played["best_ticket"] == len(set(old_maximum[:pick]) & set(row["drawn"]))
    assert row == source
    assert played["expected_variant_count"] == len(variants) == scheme.size
    groups = ui_prizes._history_lr_prize_groups(played)
    assert groups["total_variants"] == sum(g["count"] for g in groups["groups"]) == scheme.size
    assert len(groups["groups"]) <= 2 ** len(set(row["drawn"]) & set(row["pool_numbers"]))
    counts = Counter(_category(game, v, official) for v in variants)
    counts.pop(None, None)
    estimate = estimate_generated_prize(
        game, [], official, target_numbers=row["drawn"], target_date=row["label"],
        expected_count=scheme.size, variant_prize_groups=groups,
        joker_hit=row["best_variant_joker_hit"], pool_numbers=row["pool_numbers"],
    )
    expected_amount = sum(count * (CATEGORIES.index(c) + 1) * 137
                          for c, count in counts.items())
    assert estimate.status == "ready"
    assert estimate.category_counts == dict(counts)
    assert estimate.total_bani == expected_amount

    # A price refresh must use exact compact groups, not rebuild 930 tickets.
    monkeypatch.setattr(ui_prizes, "_history_lr_variants", lambda *_: pytest.fail("full LR expansion"))
    shown = ui_prizes.pool_prize_text(game, played, ui_prizes.PrizeView("ready", official))
    assert shown.text == ui_prizes._money_number(expected_amount) + " lei"
    assert f"Cod {scheme.code}" in shown.detail
    assert row == source


@pytest.mark.parametrize("ball", [True, 0, 21, 7.5, "bad", None])
def test_compact_joker_prizes_reject_invalid_chosen_ball(ball):
    row, _, _, official = _case(CONFIGS[1], 5)
    played = ui_prizes.history_played_system("joker", row)
    played["best_variant_raw"][-1] = ball
    assert "indisponibil" in ui_prizes.pool_prize_text(
        "joker", played, ui_prizes.PrizeView("ready", official)
    ).text


def test_compact_540_keeps_first_five_order_and_same_day_identity():
    row, scheme, _, official = _case(CONFIGS[2], 6)
    ticket = scheme_tickets(scheme, row["pool_numbers"])[0]
    sixth = next(n for n in row["pool_numbers"] if n not in ticket)
    row["drawn"] = ticket + [sixth]
    played = ui_prizes.history_played_system("5/40", row)
    groups = ui_prizes._history_lr_prize_groups(played)
    amounts = []
    for numbers in (ticket + [sixth], [sixth] + ticket):
        draw = PrizeDraw(official.game, official.draw_date, tuple(numbers), None,
                         official.categories, official.source_url, order_known=True)
        estimate = estimate_generated_prize(
            "5/40", [], draw, target_numbers=row["drawn"], target_date=row["label"],
            expected_count=scheme.size, variant_prize_groups=groups,
            pool_numbers=row["pool_numbers"],
        )
        expected = Counter(_category("5/40", v, draw)
                           for v in scheme_tickets(scheme, row["pool_numbers"]))
        expected.pop(None, None)
        assert estimate.status == "ready"
        assert estimate.category_counts == dict(expected)
        amounts.append(estimate.total_bani)
    assert amounts[0] > 0
    other = PrizeDraw(official.game, "2026-10-29", official.numbers, None,
                      official.categories, official.source_url)
    assert "indisponibil" in ui_prizes.pool_prize_text(
        "5/40", played, ui_prizes.PrizeView("ready", other)
    ).text


@pytest.mark.parametrize("key,value", [("lr_scheme_code", -1), ("expected_variant_count", 999)])
def test_compact_replay_rejects_stale_scheme_metadata(key, value):
    row, _, _, official = _case(CONFIGS[0], 6)
    played = ui_prizes.history_played_system("6/49", row)
    played[key] = value
    assert "indisponibil" in ui_prizes.pool_prize_text(
        "6/49", played, ui_prizes.PrizeView("ready", official)
    ).text


def test_unknown_540_order_preserves_full_list_partial_diagnostics(monkeypatch):
    from loto_enterprise.core import generated_prize

    row, scheme, variants, official = _case(CONFIGS[2], 6)
    ticket = variants[5]
    row["drawn"] = ticket + [next(n for n in row["pool_numbers"] if n not in ticket)]
    played = ui_prizes.history_played_system("5/40", row)
    draw = PrizeDraw(official.game, row["label"], tuple(row["drawn"]), None,
                     official.categories, official.source_url, order_known=False)
    expected = estimate_generated_prize(
        "5/40", variants, draw, target_numbers=row["drawn"], target_date=row["label"],
        expected_count=scheme.size, pool_numbers=row["pool_numbers"],
    )
    assert expected.status == "ambiguous_category"
    calls = []

    def capture(game, supplied, official_draw, **options):
        assert supplied == variants
        assert options["variant_prize_groups"] is None
        result = estimate_generated_prize(game, supplied, official_draw, **options)
        calls.append(result)
        return result

    monkeypatch.setattr(generated_prize, "estimate_generated_prize", capture)
    shown = ui_prizes.pool_prize_text("5/40", played, ui_prizes.PrizeView("ready", draw))
    assert "indisponibil" in shown.text
    assert calls == [expected]
