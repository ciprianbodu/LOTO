"""Caracterizare pentru normalizarea task-urilor din worker.py, extrasă din
`_run_pipeline_job_inner` (buclă inline, netestabilă direct) în funcții pure
`_map_game_label` / `_normalize_task` — parte din rescrierea completă cerută
explicit pentru toate modulele rămase.

Valorile de clamp (pool 6..16, guarantee 3..draw_n, recent_penalty_draws
0..50, recent_penalty_factor 0..0.99, lookback 0..100) sunt cele deja
existente în worker.py dinaintea rescrierii — pinned aici ca sursă de adevăr,
nu redefinite din memorie."""

from __future__ import annotations

import pytest

import worker


# --------------------------------------------------------------------------- #
# _map_game_label
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "label,expected_game,expected_draw_n",
    [
        ("Loto 6/49", "6/49", 6),
        ("6/49", "6/49", 6),
        ("Loto 5/40", "5/40", 5),
        ("5/40", "5/40", 5),
        ("Joker", "joker", 5),
        ("JOKER", "joker", 5),
        ("ceva necunoscut", "6/49", 6),  # fallback implicit
    ],
)
def test_map_game_label(label, expected_game, expected_draw_n):
    game, draw_n = worker._map_game_label(label)
    assert (game, draw_n) == (expected_game, expected_draw_n)


# --------------------------------------------------------------------------- #
# _normalize_task — clamp-uri
# --------------------------------------------------------------------------- #


def test_pool_size_clamped_to_6_16():
    assert worker._normalize_task({"pool_size": 2}, draw_n=6)["pool_size"] == 6
    assert worker._normalize_task({"pool_size": 99}, draw_n=6)["pool_size"] == 16
    assert worker._normalize_task({"pool_size": 10}, draw_n=6)["pool_size"] == 10
    assert worker._normalize_task({}, draw_n=6)["pool_size"] == 12  # default


def test_guarantee_clamped_to_3_and_draw_n():
    assert worker._normalize_task({"guarantee": 0}, draw_n=6)["guarantee"] == 3
    assert worker._normalize_task({"guarantee": 99}, draw_n=6)["guarantee"] == 6
    assert worker._normalize_task({"guarantee": 5}, draw_n=6)["guarantee"] == 5
    assert worker._normalize_task({}, draw_n=6)["guarantee"] == 4  # default raw=4
    # Joker: draw_n=5, guarantee implicit 4 ramane sub plafon
    assert worker._normalize_task({}, draw_n=5)["guarantee"] == 4


def test_max_variants_never_negative():
    assert worker._normalize_task({"max_variants": -5}, draw_n=6)["max_variants"] == 0
    assert worker._normalize_task({"max_variants": 20}, draw_n=6)["max_variants"] == 20
    assert worker._normalize_task({}, draw_n=6)["max_variants"] == 0


def test_wheel_condition_defaults_to_guarantee_when_missing_or_zero():
    t = worker._normalize_task({"guarantee": 4}, draw_n=6)
    assert t["wheel_condition"] == 4
    t2 = worker._normalize_task({"guarantee": 4, "wheel_condition": 0}, draw_n=6)
    assert t2["wheel_condition"] == 4
    t3 = worker._normalize_task({"guarantee": 4, "wheel_condition": -1}, draw_n=6)
    assert t3["wheel_condition"] == 4


def test_wheel_condition_clamped_between_guarantee_and_draw_n():
    # cerut sub guarantee -> ridicat la guarantee
    t = worker._normalize_task(
        {"guarantee": 4, "wheel_condition": 2}, draw_n=6
    )
    assert t["wheel_condition"] == 4
    # cerut peste draw_n -> coborat la draw_n
    t2 = worker._normalize_task(
        {"guarantee": 4, "wheel_condition": 99}, draw_n=6
    )
    assert t2["wheel_condition"] == 6
    # in interval -> neschimbat
    t3 = worker._normalize_task(
        {"guarantee": 4, "wheel_condition": 5}, draw_n=6
    )
    assert t3["wheel_condition"] == 5


def test_wheel_condition_non_numeric_falls_back_to_guarantee():
    t = worker._normalize_task(
        {"guarantee": 4, "wheel_condition": "abc"}, draw_n=6
    )
    assert t["wheel_condition"] == 4


def test_recent_penalty_draws_clamped_0_50():
    assert worker._normalize_task({"recent_penalty_draws": -1}, draw_n=6)[
        "recent_penalty_draws"
    ] == 0
    assert worker._normalize_task({"recent_penalty_draws": 999}, draw_n=6)[
        "recent_penalty_draws"
    ] == 50
    assert worker._normalize_task({"recent_penalty_draws": "abc"}, draw_n=6)[
        "recent_penalty_draws"
    ] == 0
    assert worker._normalize_task({}, draw_n=6)["recent_penalty_draws"] == 0


def test_recent_penalty_factor_default_and_clamp():
    assert worker._normalize_task({}, draw_n=6)["recent_penalty_factor"] == 0.5
    assert worker._normalize_task({"recent_penalty_factor": -1}, draw_n=6)[
        "recent_penalty_factor"
    ] == 0.0
    assert worker._normalize_task({"recent_penalty_factor": 5}, draw_n=6)[
        "recent_penalty_factor"
    ] == 0.99
    assert worker._normalize_task({"recent_penalty_factor": "abc"}, draw_n=6)[
        "recent_penalty_factor"
    ] == 0.5


def test_lookback_clamped_0_100():
    assert worker._normalize_task({"lookback": -10}, draw_n=6)["lookback"] == 0
    assert worker._normalize_task({"lookback": 500}, draw_n=6)["lookback"] == 100
    assert worker._normalize_task({}, draw_n=6)["lookback"] == 0  # default


def test_boolean_and_passthrough_fields():
    t = worker._normalize_task(
        {
            "filter_consecutives": True,
            "smart_reduction": True,
            "sim_depth_pct": 25,
            "pure_bench_mode": True,
        },
        draw_n=6,
    )
    assert t["filter_consecutives"] is True
    assert t["smart_reduction"] is True
    assert t["sim_depth_pct"] == 25
    assert t["pure_bench_mode"] is True

    defaults = worker._normalize_task({}, draw_n=6)
    assert defaults["filter_consecutives"] is False
    assert defaults["smart_reduction"] is False
    assert defaults["sim_depth_pct"] == 10
    assert defaults["pure_bench_mode"] is False


def test_raw_values_preserved_for_change_logging():
    """`_run_pipeline_job_inner` loghează un avertisment doar când clamp-ul a
    schimbat efectiv valoarea — are nevoie de valorile brute alături de cele
    normalizate."""
    t = worker._normalize_task(
        {"guarantee": 99, "max_variants": -5, "lookback": 500}, draw_n=6
    )
    assert (t["raw_guarantee"], t["raw_max_variants"], t["raw_lookback"]) == (
        99,
        -5,
        500,
    )
    assert (t["guarantee"], t["max_variants"], t["lookback"]) == (6, 0, 100)
