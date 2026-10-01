"""Automatic budget optimization reaches both pipeline and physical tickets."""

import pytest

from covering.probability import wheel_hit_profile, wheel_hit_probabilities
from loto_engine import LotoEngine
from loto_enterprise.core.full_ticket import build_full_ticket
from loto_enterprise.core import walk_forward_adapter as wf
from wheeling_methods import generate_wheel


def assert_dominates(pool, base, actual):
    before = wheel_hit_profile(pool, base)
    after = wheel_hit_profile(pool, actual)
    assert len(actual) == len(base)
    assert all(a >= b for ar, br in zip(after, before) for a, b in zip(ar, br))
    return before, after


@pytest.mark.parametrize("game,pick", [("6/49", 6), ("5/40", 5), ("joker", 5)])
def test_engine_automatically_preserves_every_hit_threshold(game, pick, monkeypatch):
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    engine = LotoEngine(game)
    engine.hard_core = list(range(1, 17))
    engine.hard_core_joker = [7]
    scores = {n: float((n * 17) % 23) for n in engine.hard_core}
    base, base_cov = generate_wheel("greedy", engine.hard_core, pick, 4, 10, scores)
    actual, cov = engine.generate_predictions(4, 10, scores)
    main = [line[:pick] for line in actual]
    assert_dominates(engine.hard_core, base, main)
    assert cov >= base_cov
    assert len(main) == 10 and len(set(map(tuple, main))) == 10
    assert wf._wheel_sig(16, game, 4, None, 10).startswith("hitcover|")
    if game == "joker":
        assert all(line[-1] == 7 and len(line) == 6 for line in actual)


@pytest.mark.parametrize("requested", ["greedy", " unknown-wheel "])
def test_explicit_greedy_and_unknown_override_keep_greedy(requested, monkeypatch):
    monkeypatch.setenv("LOTO_WHEEL_METHOD", requested)
    engine = LotoEngine("6/49")
    engine.hard_core = list(range(1, 17))
    scores = {n: float(n) for n in engine.hard_core}
    assert engine.generate_predictions(4, 7, scores) == generate_wheel(
        "greedy", engine.hard_core, 6, 4, 7, scores
    )
    assert wf._wheel_sig(16, "6/49", 4, None, 7).startswith("greedy|")


def test_uncapped_and_conditional_generation_keep_their_designs(monkeypatch):
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    engine = LotoEngine("6/49")
    engine.hard_core = list(range(1, 17))
    scores = {n: float(n) for n in engine.hard_core}
    assert engine.generate_predictions(4, 0, scores) == generate_wheel(
        "lajolla", engine.hard_core, 6, 4, 0, scores
    )
    assert wf._wheel_sig(16, "6/49", 4, None, 0).startswith("lajolla|")
    assert engine.generate_predictions(3, 10, scores, condition=4) == generate_wheel(
        "lotto", engine.hard_core, 6, 3, 10, scores, condition=4
    )
    assert wf._wheel_sig(16, "6/49", 3, 4, 10).startswith("lotto|")


@pytest.mark.parametrize("game,pick", [("6/49", 6), ("5/40", 5), ("joker", 5)])
def test_physical_tickets_automatically_preserve_all_thresholds(game, pick, monkeypatch):
    pool = list(range(1, 17))
    scores = {n: float((n * 17) % 23) for n in pool}
    data = {
        "hard_core": pool, "guarantee": 4, "hard_core_joker": [7],
        "audit": {"timesfm_predictions": scores},
    }
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "greedy")
    base = build_full_ticket(game, data, 10)
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    actual = build_full_ticket(game, data, 10)
    before, after = assert_dominates(
        pool, [v[:pick] for v in base["variants"]], [v[:pick] for v in actual["variants"]]
    )
    assert actual["error"] is None
    assert actual["pool"] == base["pool"] == pool
    assert actual["requested"] == base["requested"] == len(actual["variants"])
    assert actual["coverage"] >= base["coverage"]
    assert actual["guarantee_variants"] == base["guarantee_variants"]
    assert len(set(map(tuple, actual["variants"]))) == len(actual["variants"])
    if game == "6/49":
        assert any(after[h][3] > before[h][3] for h in range(3, 7))
        a = wheel_hit_probabilities(pool, base["variants"], 6, 49)
        b = wheel_hit_probabilities(pool, actual["variants"], 6, 49)
        assert b["ticket"][3] > a["ticket"][3]
        assert b["ticket"][4] > a["ticket"][4]
    if game == "joker":
        assert all(v[-1] == 7 for v in actual["variants"])


def test_completed_physical_base_and_extra_groups_stay_identical(monkeypatch):
    data = {
        "hard_core": list(range(1, 10)), "guarantee": 3,
        "audit": {"timesfm_predictions": {n: float(26 - n) for n in range(1, 26)}},
    }
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "greedy")
    base = build_full_ticket("6/49", data, 10)
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    actual = build_full_ticket("6/49", data, 10)
    assert actual == base
    assert actual["coverage"] == 100.0
    assert actual["guarantee_variants"] < len(actual["variants"]) == 30
    assert actual["upper_coverage"][0] == 4
