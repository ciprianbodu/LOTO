"""An LR historical row must price and pay the SAME official code's variants."""
from copy import deepcopy
from collections import Counter

import pytest

import app_nicegui as app
import ui_prizes
from covering.lr_schemes import lr_schemes, scheme_tickets
from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw
from scripts.analysis.audit_output import capture_ui
from TESTE.test_rebench_hits_threshold import record, bindings


def source(game, pool_size, *, virtual=False, ball=7):
    pick = 6 if game == "6/49" else 5
    drawn = 5 if game == "joker" else 6
    return {"label": "2026-10-22", "drawn": list(range(1, drawn + 1)),
            "pool_numbers": list(reversed(range(1, pool_size + 1))),
            "best_variant_raw": list(range(1, pick + 1)) + ([ball] if game == "joker" else []),
            "best_variant_joker_hit": game == "joker", "best_ticket": pick,
            "generated_variants": [], "expected_variant_count": 9999,
            "virtual_full_system": virtual}


def category(game, variant, numbers, joker):
    main = variant[:5] if game == "joker" else variant
    hits = len(set(main) & set(numbers))
    if game == "6/49":
        return {3: "IV", 4: "III", 5: "II", 6: "I"}.get(hits)
    if game == "5/40":
        return ("I" if set(main) == set(numbers[:5]) else "II") if hits == 5 else "III" if hits == 4 else None
    return ({1: "VIII", 2: "VII", 3: "V", 4: "III", 5: "I"} if variant[-1] == joker else {3: "VI", 4: "IV", 5: "II"}).get(hits)


@pytest.mark.parametrize("game,n,code,count,unit,amount", [
    ("6/49", 16, 59, 112, 8, 896),
    ("joker", 12, 14, 38, 7, 266),
    ("5/40", 19, 48, 930, 5, 4650),
])
@pytest.mark.parametrize("virtual", [True, False])
def test_cost_and_prize_are_reconstructed_from_same_lr_variants(game, n, code, count, unit, amount, virtual):
    raw = source(game, n, virtual=virtual)
    original = deepcopy(raw)
    played = ui_prizes.history_played_system(game, raw)
    assert played["lr_scheme_code"] == code
    assert played["expected_variant_count"] == count
    assert count * unit == amount
    assert played["generated_variants"] == []  # Lazy, not a full-system list.
    assert not played["virtual_full_system"]
    assert raw == original
    scheme = next(s for s in lr_schemes(game, n) if s.code == code)
    actual = scheme_tickets(scheme, raw["pool_numbers"])
    if game == "joker":
        actual = [v + [7] for v in actual]
    assert ui_prizes._history_lr_variants(played) == actual
    labels = {
        "6/49": ("6/6", "5/6", "4/6", "3/6"),
        "5/40": ("5/5", "5/6", "4/6"),
        "joker": ("5/5+J", "5/5", "4/5+J", "4/5", "3/5+J", "3/5", "2/5+J", "1/5+J"),
    }[game]
    names = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")
    units = {name: (index + 1) * 100 for index, name in enumerate(names)}
    official = PrizeDraw({"6/49": "loto_6_49", "5/40": "loto_5_40", "joker": "joker"}[game],
        raw["label"], tuple(raw["drawn"]), 7 if game == "joker" else None,
        tuple(PrizeCategory(name, label, 10, units[name], None, units[name]) for name, label in zip(names, labels)),
        "https://www.loto.ro/test")
    counted = Counter(category(game, v, raw["drawn"], 7) for v in actual)
    counted.pop(None, None)
    gross = sum(units[cat] * value for cat, value in counted.items())
    shown = ui_prizes.pool_prize_text(game, played, ui_prizes.PrizeView("ready", official))
    assert shown.text == ui_prizes._money_number(gross) + " lei"
    assert f"Cod {code}" in shown.detail
    assert played["best_ticket"] == max(len(set(v[:5] if game == "joker" else v) & set(raw["drawn"])) for v in actual)


def test_code_selection_does_not_look_at_the_draw():
    before = source("6/49", 10)
    after = deepcopy(before)
    after["drawn"] = list(range(30, 36))
    first, second = [ui_prizes.history_played_system("6/49", r) for r in (before, after)]
    assert first["lr_scheme_code"] == second["lr_scheme_code"] == 49
    assert first["expected_variant_count"] == second["expected_variant_count"] == 15
    assert ui_prizes._history_lr_variants(first) == ui_prizes._history_lr_variants(second)


@pytest.mark.parametrize("change", [{"pool_numbers": list(range(1, 31))}, {"country": "AT"}, {"drawn": []}])
def test_unavailable_lr_or_foreign_game_keeps_original_system(change):
    raw = source("6/49", 16)
    raw.update(change)
    played = ui_prizes.history_played_system("6/49", raw)
    assert "lr_scheme_code" not in played
    assert played["expected_variant_count"] == 9999


def test_unknown_joker_cannot_be_chosen_after_reading_official_result():
    raw = source("joker", 12)
    raw["best_variant_raw"] = raw["best_variant_raw"][:5]
    played = ui_prizes.history_played_system("joker", raw)
    assert played["lr_scheme_code"] == 14
    assert "Joker" in ui_prizes._generated_variants_issue(played)
    assert "indisponibil" in ui_prizes.pool_prize_text("joker", played, ui_prizes.PrizeView()).text


@pytest.mark.parametrize("game,n,code,count,cost", [("6/49",16,59,112,"896"),("joker",12,14,38,"266"),("5/40",19,48,930,"4\u202f650")])
def test_real_history_table_uses_lr_cost_count_and_best_hits(bindings, game, n, code, count, cost):
    data = record(game, levels=(4,))
    data["pool"] = n
    data["flat"][0].ticket_context["hard_core"] = list(range(1, n + 1))
    with capture_ui() as view:
        app._render_virtual_history([data], show_status=False)
    table = next(node for node in view.walk() if node["kind"] == "table" and node["kwargs"].get("row_key") == "draw_index")
    row = table["attrs"]["rows"][0]
    assert f"Cod {code}" in row["system"]
    assert row["variants"] == str(count)
    assert row["pool_cost"].endswith(cost + " lei")
    assert {m["v"] for m in row["pool_marks"] if m["hit"]} == {1,2,3,4}
