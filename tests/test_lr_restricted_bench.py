"""The fixed inverse decision uses the ordinary benchmark on a restricted ranking."""
import numpy as np
import pytest

from loto_enterprise.benchmark import restricted_decision as bench
from loto_enterprise.benchmark.methods import score_frequency


def test_short_history_returns_only_eligible_frequency_scores():
    draws = np.arange(1, 19).reshape(3, 6)
    excluded = tuple(range(1, 13))
    scores, selection = bench.restricted_bench_scores(draws, (0, 1, 2), "loto_6_49", 12, excluded, 4)
    expected = score_frequency(draws, 49)
    assert scores == {n: value for n, value in expected.items() if n not in excluded}
    assert selection["fallback"] and selection["no_decision"]
    scores.clear()
    assert bench.restricted_bench_scores(draws, (0, 1, 2), "loto_6_49", 12, excluded, 4)[0]


def test_rebench_uses_all_four_windows_and_masks_comparator(monkeypatch):
    rng = np.random.default_rng(420)
    draws = np.array([rng.choice(np.arange(1, 50), 6, replace=False) for _ in range(340)])
    excluded = tuple(range(1, 13))
    seen = {}
    real_evaluate = bench._evaluate_fold

    def evaluate(method, train, test, game, block, *, score_method):
        def check(name, history, maximum, **context):
            result = score_method(name, history, maximum, **context)
            assert set(result[0]) == set(range(13, 50))
            return result

        seen.setdefault(method, set()).add(len(test))
        return real_evaluate(method, train, test, game, block, score_method=check)

    monkeypatch.setattr(bench, "_evaluate_fold", evaluate)
    scores, decision = bench.restricted_bench_scores(draws, range(len(draws)), "loto_6_49", 12, excluded, 4)
    assert "random" in seen and "frequency" in seen
    assert all(windows == {34, 102, 238, 260} for windows in seen.values())
    assert set(scores) == set(range(13, 50))
    assert decision["source"] == "restricted_rebench"
    assert decision["hit_target"] == 4


def test_joker_bonus_obeys_its_own_universe_and_target():
    draws = np.array([1, 2, 5, 19])
    scores, config = bench.restricted_bench_scores(draws, (0, 1, 1, 3), "joker_urna2", 1, (1, 5), 4)
    assert set(scores) == set(range(1, 21)) - {1, 5}
    assert config["hit_target"] == 1
    with pytest.raises(ValueError):
        bench.restricted_bench_scores(draws, (0, 1, 2, 2), "joker_urna2", 1, tuple(range(1, 21)), 4)
    with pytest.raises(ValueError):
        bench.restricted_bench_scores(draws, (0, 1, 2, 1), "joker_urna2", 1, (1, 5), 4)
