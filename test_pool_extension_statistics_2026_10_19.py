"""Generic day clustering and selection cannot inherit a fixed8-pool denominator."""

import numpy as np
import pytest

from scripts.analysis.pool_extension_statistics_2026_10_19 import (
    hypergeom_tails,
    metrics,
    paired_test,
    replay_selector,
)


@pytest.mark.parametrize(
    "max_num,draw_n,ks", [(40, 6, tuple(range(9, 16))), (45, 5, tuple(range(9, 20)))]
)
def test_whole_day_sign_and_arbitrary_number_of_pools(max_num, draw_n, ks):
    full = np.full((4, len(ks)), draw_n)
    zero = np.zeros_like(full)
    result = paired_test(
        full,
        zero,
        np.arange(4),
        ks=ks,
        draw_n=draw_n,
        permutations=19999,
        bootstrap_samples=100,
    )
    assert result["mean_difference"] == 1
    assert result["candidate_rate"] == 1 and result["baseline_rate"] == 0
    assert result["exact_sign_flip_p_diagnostic"] == 1 / 16
    assert abs(result["p_value"] - 1 / 16) < 0.01
    assert result["ci95_day_cluster_bootstrap"] == [1.0, 1.0]
    cancelled = paired_test(
        np.vstack((full[:1], zero[:1])),
        np.vstack((zero[:1], full[:1])),
        ["same", "same"],
        ks=ks,
        draw_n=draw_n,
        permutations=199,
        bootstrap_samples=0,
    )
    assert cancelled["mean_difference"] == 0 and cancelled["p_value"] == 1
    assert cancelled["days"] == 1
    assert all(p["day_signflip_p"] == 1 for p in cancelled["per_pool"].values())
    probability = hypergeom_tails(ks[-1], max_num=max_num, draw_n=draw_n)
    assert sum(probability["distribution"].values()) == pytest.approx(1)


@pytest.mark.parametrize(
    "ks,draw_n", [(tuple(range(9, 16)), 6), (tuple(range(9, 20)), 5)]
)
def test_one_pool_has_exactly_one_over_column_count_weight(ks, draw_n):
    candidate = np.zeros((1, len(ks)), dtype=int)
    candidate[0, -1] = 4
    zero = np.zeros_like(candidate)
    value = paired_test(
        candidate,
        zero,
        ["a"],
        ks=ks,
        draw_n=draw_n,
        permutations=199,
        bootstrap_samples=0,
    )
    assert value["mean_difference"] == 1 / len(ks)
    assert metrics(candidate, ks=ks, draw_n=draw_n)["primary"] == 1 / len(ks)


@pytest.mark.parametrize(
    "ks,draw_n", [(tuple(range(9, 16)), 6), (tuple(range(9, 20)), 5)]
)
def test_selector_uses_complete_past_windows_with_generic_pool_denominator(ks, draw_n):
    dates = np.arange("2020-01-01", "2020-04-10", dtype="datetime64[D]")
    targets = np.arange(len(dates))
    hits = np.zeros((2, len(dates), len(ks)), dtype=int)
    hits[1, :70] = 4
    result = replay_selector(
        dates, targets, hits, ["markov_lag2", "new"], ks=ks, draw_n=draw_n
    )
    assert result["blocks"][0]["chosen_id"] == "new"
    assert result["blocks"][0]["chosen_past_primary"] == 1
    assert result["blocks"][0]["baseline_past_primary"] == 0
    changed = hits.copy()
    changed[0, 70:80] = draw_n
    again = replay_selector(
        dates, targets, changed, ["markov_lag2", "new"], ks=ks, draw_n=draw_n
    )
    assert again["blocks"][0]["chosen_id"] == "new"
    tied = np.zeros_like(hits, dtype=bool)
    tied[1, 50:70, -1] = True
    gated = replay_selector(
        dates, targets, hits, ["markov_lag2", "new"], ks=ks, draw_n=draw_n, ties=tied
    )
    assert gated["blocks"][0]["chosen_id"] == "markov_lag2"


def test_joker_rejects_six_hits_but_540_records_all_six():
    values = np.array([[6] * 7])
    assert (
        metrics(values, ks=range(9, 16), draw_n=6)["events_by_threshold"]["6"]
        == [1] * 7
    )
    with pytest.raises(ValueError, match="integer hits"):
        metrics(np.array([[6] * 11]), ks=range(9, 20), draw_n=5)
