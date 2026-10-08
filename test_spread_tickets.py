"""Variante dispersate: probabilitati exacte si dominanta fata de variantele din pool."""

from __future__ import annotations

from itertools import combinations
from math import comb

import numpy as np
import pytest

from covering.probability import wheel_hit_probabilities
from covering.spread import (
    max_consecutive_on_variant,
    pair_joint_probability,
    spread_variants,
    ticket_hit_probabilities,
)
from loto_enterprise.core.full_ticket import build_full_ticket, chance_thresholds
from loto_enterprise.core.pool_selection import select_pool_from_scores

GEOMETRY = {"6/49": (49, 6, 6, 3), "5/40": (40, 6, 5, 4), "joker": (45, 5, 5, 3)}


def _single(max_num, draw_n, pick, t):
    return sum(
        comb(pick, h) * comb(max_num - pick, draw_n - h) for h in range(t, min(pick, draw_n) + 1)
    ) / comb(max_num, draw_n)


@pytest.mark.parametrize("game", sorted(GEOMETRY))
def test_exact_evaluator_matches_the_pool_profile(game):
    max_num, draw_n, pick, _t = GEOMETRY[game]
    rng = np.random.default_rng(5)
    pool = sorted(int(x) for x in rng.choice(np.arange(1, max_num + 1), 12, replace=False))
    tickets = {tuple(sorted(rng.choice(pool, pick, replace=False).tolist())) for _ in range(9)}
    exact = ticket_hit_probabilities(tickets, draw_n, max_num)
    ref = wheel_hit_probabilities(pool, tickets, draw_n, max_num)["ticket"]
    assert exact == pytest.approx(ref, abs=1e-15)


def test_pair_joint_probability_matches_brute_force():
    max_num, draw_n, pick = 11, 4, 4
    for s in range(pick):
        a = set(range(pick))
        b = set(range(pick - s, 2 * pick - s))
        for t in (1, 2, 3):
            hits = sum(
                len(a & set(d)) >= t and len(b & set(d)) >= t
                for d in combinations(range(max_num), draw_n)
            )
            assert pair_joint_probability(max_num, draw_n, pick, s, t) == pytest.approx(
                hits / comb(max_num, draw_n), abs=1e-15
            )


@pytest.mark.parametrize("game", sorted(GEOMETRY))
def test_disjoint_variants_reach_the_upper_bound(game):
    """Cand incap, variantele sunt disjuncte: P(>=t) = B * P(o varianta) la 4+."""
    max_num, draw_n, pick, target = GEOMETRY[game]
    n_var = max_num // pick
    v = spread_variants(range(max_num, 0, -1), n_var, pick, draw_n, max_num, target)
    assert len(v) == n_var and len({x for row in v for x in row}) == n_var * pick
    p = ticket_hit_probabilities(v, draw_n, max_num)
    for t in range(4, min(pick, draw_n) + 1):
        assert p[t] == pytest.approx(n_var * _single(max_num, draw_n, pick, t), rel=1e-12)


def test_649_thirty_variants_share_at_most_one_number_and_hit_the_4_plus_bound():
    v = spread_variants(range(49, 0, -1), 30, 6, 6, 49, 3)
    sets = [set(x) for x in v]
    assert len({tuple(x) for x in v}) == 30
    assert max(len(a & b) for i, a in enumerate(sets) for b in sets[i + 1 :]) <= 1
    p = ticket_hit_probabilities(v, 6, 49)
    # Doua variante cu cel mult un numar comun nu pot avea ambele 4+ din 6 extrase.
    assert p[4] == pytest.approx(30 * _single(49, 6, 6, 4), rel=1e-12)
    assert p[3] > 0.48


def test_spread_is_deterministic_and_respects_the_consecutive_limit():
    args = (range(1, 50), 15, 6, 6, 49, 3)
    a = spread_variants(*args, max_run=2)
    assert a == spread_variants(*args, max_run=2)
    assert all(max_consecutive_on_variant(x) <= 2 for x in a)


def _result(game, size=10, **audit_extra):
    max_num = GEOMETRY[game][0]
    scores = {n: float(max_num - n) / max_num for n in range(1, max_num + 1)}
    audit: dict = {}
    pool = select_pool_from_scores(scores, size, set(), audit, max_num=max_num)
    audit.update(audit_extra)
    return {"hard_core": pool, "guarantee": 4, "audit": audit, "hard_core_joker": [7]}


@pytest.mark.parametrize("game", sorted(GEOMETRY))
@pytest.mark.parametrize("tickets", [1, 3, 10])
def test_spread_dominates_pool_variants_at_every_threshold(game, tickets):
    t_pool = build_full_ticket(game, _result(game), tickets, compare=True)
    t_spread = build_full_ticket(game, _result(game), tickets, spread=True, compare=True)
    assert t_pool["mode"] == "pool" and t_spread["mode"] == "spread"
    assert len(t_spread["variants"]) == len(t_pool["variants"]) == t_pool["requested"]
    pool_p, spread_p = t_pool["chances"]["shown"], t_spread["chances"]["shown"]
    assert t_pool["chances"]["other"] == pytest.approx(spread_p, abs=1e-15)
    assert t_spread["chances"]["other"] == pytest.approx(pool_p, abs=1e-15)
    for t in t_pool["chances"]["thresholds"]:
        assert spread_p[t] >= pool_p[t] - 1e-15
    if tickets >= 3:
        low = t_pool["chances"]["thresholds"][0]
        assert spread_p[low] > pool_p[low]


def test_spread_plays_the_displayed_pool_first():
    data = _result("6/49", size=12)
    t = build_full_ticket("6/49", data, 1, spread=True)
    assert set(data["hard_core"]) <= set(t["pool"])
    assert len(t["pool"]) == 18 and t["max_overlap"] == 0


def test_spread_keeps_the_restricted_base_and_the_joker_ball():
    data = _result("joker", restrict_base={"min": 10, "max": 40, "excluded": []})
    data["hard_core"] = [n for n in range(31, 41)]
    data["audit"]["timesfm_predictions"] = {n: 1.0 for n in range(40, 9, -1)}
    t = build_full_ticket("joker", data, 10, spread=True)
    assert t["joker"] == 7 and all(v[-1] == 7 for v in t["variants"])
    assert set(t["pool"]) <= set(range(10, 41))


def test_spread_applies_the_consecutive_limit_per_variant():
    data = _result("6/49")
    data["audit"]["consecutive_limit"] = {"requested": 2, "applied": 2}
    t = build_full_ticket("6/49", data, 10, spread=True)
    assert all(max_consecutive_on_variant(v) <= 2 for v in t["variants"])
    assert "consecutive" in t["note"]


def test_spread_keeps_the_requested_limit_when_only_the_pool_was_relaxed():
    """Audit 2026-10-08: pool-ul de 16 nu încăpea în bază cu limita 2 (aplicată 3);
    fiecare variantă de 5-6 numere respectă totuși limita cerută."""
    data = _result("6/49")
    data["audit"]["consecutive_limit"] = {"requested": 2, "applied": 16, "relaxed": True}
    t = build_full_ticket("6/49", data, 10, spread=True)
    assert all(max_consecutive_on_variant(v) <= 2 for v in t["variants"])
    assert "mai mult de 2 numere consecutive" in t["note"]
    assert "16" not in t["note"]


def test_default_full_ticket_stays_in_the_pool():
    data = _result("6/49")
    t = build_full_ticket("6/49", data, 2)
    assert t["mode"] == "pool" and t["chances"]["other"] is None
    assert {n for v in t["variants"] for n in v} <= set(t["pool"])


def test_chance_thresholds_skip_the_invariant_jackpot():
    assert chance_thresholds(3, 6, 6) == [3, 4, 5]
    assert chance_thresholds(4, 5, 6) == [4, 5]
    assert chance_thresholds(3, 5, 5) == [3, 4]


def test_ui_setting_is_persisted_and_off_by_default():
    import ui_runtime

    assert "full_ticket_spread_val" in ui_runtime.UI_PERSIST_KEYS
    assert ui_runtime.DEFAULTS["full_ticket_spread_val"] is False


def test_ui_texts_describe_the_spread_ticket():
    import ui_results

    t = build_full_ticket("6/49", _result("6/49"), 2, spread=True, compare=True)
    summary = ui_results._full_ticket_summary(t, "6/49")
    assert "dispersate pe" in summary and "Lei" in summary
    lines = ui_results._full_ticket_chances(t, False)
    assert lines[0].startswith("Șansa exactă ca cel puțin o variantă să prindă: 3+")
    assert "din pool" in lines[1]
    assert "Media variantelor câștigătoare e aceeași" in lines[-1]


@pytest.mark.parametrize(
    "overlap, text",
    [
        (0, "fără numere comune între variante"),
        (1, "cel mult un număr comun între două variante"),
        (2, "cel mult 2 numere comune între două variante"),
    ],
)
def test_spread_summary_states_the_overlap_in_words(overlap, text):
    import ui_results

    t = build_full_ticket("joker", _result("joker"), 1, spread=True)
    summary = ui_results._full_ticket_summary({**t, "max_overlap": overlap}, "joker")
    assert text in summary
