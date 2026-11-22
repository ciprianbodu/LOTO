"""Render the normal selection and its inverse 10–40 companion in one result email."""

from datetime import datetime

import pytest

from loto_enterprise.core.lotteries import lottery_by_id
from ui_shared import mail_pool_alternative_lines


NORMAL = [2, 3, 4, 5, 9, 10, 13, 20, 32, 33, 36, 46]
REDUCED = [12, 16, 17, 18, 19, 23, 25, 26, 27, 30, 39, 40]


def _reduced():
    return {
        "code": 57, "eligible_count": 25, "pool": REDUCED.copy(), "budget_lei": 200,
        "tickets": [[{"code": 57, "numbers": REDUCED.copy()}]],
    }


def test_only_inverse_reduced_is_rendered_and_single_zone_uses_the_shown_pool():
    body = "\n".join(mail_pool_alternative_lines({
        "inverse_full": {"pool": [1, 41, 45]}, "inverse_reduced": _reduced(),
    }, {"hard_core": NORMAL}, has_joker=False))
    assert "Pool invers · baza redusă 10–40 inclusiv · Cod de evaluare 57 · 25 de numere eligibile" in body
    assert body.count("12 16 17 18 19 23 25 26 27 30 39 40") == 1
    assert "Bilet 1 · zona A · Cod LR 57" in body and "200 lei" in body
    assert "baza completă" not in body and "1 41 45" not in body
    assert mail_pool_alternative_lines({"inverse_full": {}}, {}, has_joker=False) == []
    assert "Indisponibil" in mail_pool_alternative_lines({"inverse_reduced": None}, {}, has_joker=False)[1]


def test_multi_zone_report_separates_the_scoring_code_from_each_playable_zone():
    pool = list(range(10, 24))
    alternative = {
        "code": 28, "eligible_count": 25, "pool": pool,
        "tickets": [[{"code": 15, "numbers": pool[:7]}, {"code": 15, "numbers": pool[7:]}]],
    }
    lines = mail_pool_alternative_lines({"inverse_reduced": alternative}, {"hard_core": [1, 2]}, has_joker=False)
    assert "Cod de evaluare 28" in lines[0]
    assert "  Bilet 1 · zona A · Cod LR 15 · 10 11 12 13 14 15 16" in lines
    assert "  Bilet 1 · zona B · Cod LR 15 · 17 18 19 20 21 22 23" in lines


def test_joker_numbers_keep_ranking_and_label_the_actual_ticket_ball():
    item = _reduced()
    item.update(jokers=[8, 4], eligible_joker_count=18)
    item["tickets"][0][0]["joker"] = 8
    lines = mail_pool_alternative_lines(
        {"inverse_reduced": item}, {"hard_core": NORMAL, "joker_pool": [3, 7]}, has_joker=True,
    )
    assert "  Urna 2 Joker · 8 4 · 18 bile eligibile" in lines
    assert "  Bilet 1 · zona A · Cod LR 57 · bila Joker 8" in lines


@pytest.mark.parametrize("change", [{"pool": [1, 12]}, {"pool": [10, 12]}, {"code": None}])
def test_invalid_or_incomplete_alternative_is_not_printed_as_playable(change):
    item = {**_reduced(), **change}
    lines = mail_pool_alternative_lines({"inverse_reduced": item}, {"hard_core": NORMAL}, has_joker=False)
    assert any("Indisponibil." in line for line in lines)
    assert all("Numere principale" not in line and "Bilet 1" not in line for line in lines)


def test_per_game_error_and_normal_pool_survive_in_the_actual_email(monkeypatch):
    import app_nicegui as app

    prepared = []
    monkeypatch.setattr(app, "_prepare_mail_alternatives", prepared.append, raising=False)
    monkeypatch.setattr(app, "_mail_draw_date", lambda *args: "22-11-2026")
    monkeypatch.setattr(app, "_mail_draw_info", lambda *args: None)
    monkeypatch.setattr(app, "_mail_method_lines", lambda *args, **kwargs: [])
    monkeypatch.setattr(app, "_mail_best_draw_line", lambda *args: "Istoric de test")
    monkeypatch.setattr(app, "_lr_schemes_for", lambda *args: [])
    snapshot = {
        "valid": True, "generated_at": datetime(2026, 11, 22, 16), "sources": {},
        "games": [
            {"fname": "loto.csv", "game": "6/49", "spec": lottery_by_id("6/49"), "data": {"hard_core": NORMAL},
             "pool_alternatives": {"inverse_reduced": _reduced()}},
            {"fname": "joker.csv", "game": "joker", "spec": lottery_by_id("joker"),
             "data": {"hard_core": [1, 2, 3], "hard_core_joker": [2], "joker_pool": [2, 7]},
             "pool_alternatives": {"inverse_reduced": {"error": "Urna 2 nu conține destule bile eligibile."}}},
        ],
    }
    body = app._build_mail_body(snapshot)
    assert prepared == [snapshot]
    assert "POOL:   2 3 4 5 9 10 13 20 32 33 36 46  (pool normal)" in body
    assert "POOL:   1 2 3  | joker: 2 7  (pool normal)" in body
    assert body.count("Pool invers · baza redusă 10–40 inclusiv") == 2
    assert "Indisponibil. Urna 2 nu conține destule bile eligibile." in body
    assert "Numere principale · 12 16 17 18 19 23 25 26 27 30 39 40" in body
