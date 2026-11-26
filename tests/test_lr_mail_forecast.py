"""The email adds just the inverse 5–40 option of each actual completed job."""

from copy import deepcopy

import pandas as pd
import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS
from loto_enterprise.core import lr_mail_forecast as mail
from loto_enterprise.core.lotteries import require_lottery


def _record(game, pool, code, **extra):
    lot = require_lottery(game, "RO")
    return {"fname": "uploaded.csv", "spec": lot, "data": {
        "hard_core": pool, "lr_scheme_code": code, "dynamic_method": True, **extra,
    }}


def _source(game):
    width = 5 if game == "joker" else 6
    source = pd.DataFrame([{f"n{i}": n + i for i in range(1, width + 1)} for n in range(12)])
    if game == "joker":
        source["joker"] = [19, 4] * 6
    return source


def test_different_budget_width_keeps_exact_original_main_pool():
    original = list(range(1, 17))
    record = _record("6/49", original, 59, lookback=0, total_draws=12)
    saved = deepcopy(record["data"])
    result = mail.prepare_inverse_mail_pools(record, _source("6/49"), budget_lei=200, dynamic=False, lookback=10)
    assert set(result) == {"inverse_reduced"}
    forecast = result["inverse_reduced"]
    assert forecast["excluded_pool"] == original and forecast["code"] == 57 and forecast["dynamic"]
    assert forecast["number_range"] == (5, 40) and forecast["eligible_count"] == 24
    assert forecast["rows"] == 12 and len(forecast["pool"]) == 12
    assert set(forecast["pool"]) <= set(range(17, 41))
    assert forecast["tickets"][0][0]["numbers"] == forecast["pool"]
    assert record["data"] == saved


def test_same_width_different_codes_and_legacy_strings_retain_original_numbers():
    raw = [2, 5, 7, 9, 10, 18, 22, 25, 33, 37, 41, 49]
    record = _record("6/49", list(map(str, raw)), 58)
    f = mail.prepare_inverse_mail_pools(record, _source("6/49"), budget_lei=200)["inverse_reduced"]
    assert f["code"] == 57 and f["excluded_pool"] == raw
    assert len(f["pool"]) == 12 and set(f["pool"]).isdisjoint(raw)


def test_joker_bonus_uses_whole_displayed_pool_even_if_hard_core_has_only_one_ball():
    record = _record("joker", [1, 2, 3, 4, 6, 8, 10], 45,
                     joker_pool=[20, 7, 10], hard_core_joker=[7], dynamic_method=False)
    f = mail.prepare_inverse_mail_pools(record, _source("joker"), budget_lei=100, joker_size=1)["inverse_reduced"]
    assert f["code"] == 24 and f["number_range"] == (5, 40) and not f["dynamic"]
    assert f["excluded_pool"] == record["data"]["hard_core"]
    assert f["excluded_jokers"] == (7, 10, 20) and f["eligible_joker_count"] == 17
    assert len(f["jokers"]) == 3 and set(f["jokers"]).isdisjoint({7, 10, 20})
    assert f["tickets"][0][0]["joker"] in f["jokers"]


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_mismatched_source_preserves_normal_without_substitution(missing):
    record = _record("6/49", list(range(1, 13)), 57, audit={"hash": "older-snapshot"})
    result = mail.prepare_inverse_mail_pools(record, None if missing else _source("6/49"))
    assert "error" in result["inverse_reduced"] and "pool" not in result["inverse_reduced"]
    assert record["data"]["hard_core"] == list(range(1, 13))


def test_infeasible_range_stays_visible_without_silently_switching_to_full():
    record = _record("5/40", list(range(5, 24)), 48)
    result = mail.prepare_inverse_mail_pools(record, _source("5/40"), budget_lei=300)
    assert set(result) == {"inverse_reduced"}
    assert "17 numere eligibile" in result["inverse_reduced"]["error"]
    assert "pool" not in result["inverse_reduced"]


def test_only_one_inverse_call_and_job_settings_win_over_later_controls(monkeypatch):
    record = _record("6/49", list(range(1, 13)), 57, lookback=25, dynamic_method=False,
                     lr_budget_lei=105, lr_budget_objective=ANY_PRIZE, bench_hit_target=5)
    calls = []
    def calculator(budget, objective, game, **options):
        calls.append((budget, objective, game, options))
        return [{"forecast_error": "eroare la un singur joc", "tickets": []}]
    monkeypatch.setattr(mail, "recommend_playable_games", calculator)
    outcome = mail.prepare_inverse_mail_pools(
        record, _source("6/49"), budget_lei=200, objective=FOUR_PLUS, dynamic=True, lookback=0, hit_target=3,
    )
    assert len(calls) == 1
    budget, objective, game, options = calls[0]
    assert budget == 105 and objective == ANY_PRIZE and game == "6/49"
    assert options["inverse"] and options["exclude_generated_pool"] and options["number_range"] == (5, 40)
    assert options["lookback"] == 25 and not options["dynamic"] and options["hit_target"] == 5
    assert outcome["inverse_reduced"]["error"] == "eroare la un singur joc"


def test_non_romanian_record_skips_inverse_without_attempting_a_romanian_plan():
    from loto_enterprise.core.lotteries import GAMES

    foreign = next(lot for lot in GAMES if not lot.is_romanian)
    assert mail.prepare_inverse_mail_pools({"fname": "foreign.csv", "spec": foreign, "data": {"hard_core": [2]}}, None) == {}