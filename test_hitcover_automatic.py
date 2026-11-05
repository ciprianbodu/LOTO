"""Automatic budget optimization preserves the pipeline guarantees."""

import pytest

from covering.probability import wheel_hit_profile, wheel_hit_probabilities
from loto_engine import LotoEngine
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
    # Din 2026-10-16 motorul dă universul și pe ramura lotto: cu plafon, wheel-ul
    # clasic certificat ia locul designului trunchiat, fără scădere la vreun prag
    # și fără să acopere mai puține grupe „3 dacă 4”.
    from covering.common import lotto_coverage_pct

    lines, cov = engine.generate_predictions(3, 10, scores, condition=4)
    assert (lines, cov) == generate_wheel(
        "lotto", engine.hard_core, 6, 3, 10, scores, condition=4, draw_n=6, max_num=49
    )
    old, old_cov = generate_wheel("lotto", engine.hard_core, 6, 3, 10, scores, condition=4)
    p_new = wheel_hit_probabilities(engine.hard_core, [t[:6] for t in lines], 6, 49)["ticket"]
    p_old = wheel_hit_probabilities(engine.hard_core, old, 6, 49)["ticket"]
    assert len(lines) == len(old) == 10
    assert all(p_new[t] >= p_old[t] - 1e-15 for t in range(3, 7))
    assert lotto_coverage_pct([t[:6] for t in lines], engine.hard_core, 3, 4) >= old_cov
    assert wf._wheel_sig(16, "6/49", 3, 4, 10).startswith("lotto|")
    assert "|lc1:" in wf._wheel_sig(16, "6/49", 3, 4, 10)
