"""`covering.spread`: probabilitati exacte si variante dispersate (studiul din
2026-10-05); „Bilet complet” ia variantele numai din pool (bifa scoasa 2026-10-10)."""

from __future__ import annotations

import inspect
from itertools import combinations
from math import comb
from pathlib import Path

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
def test_full_ticket_variants_come_only_from_the_ticket_pool(game, tickets):
    t = build_full_ticket(game, _result(game), tickets)
    assert len(t["variants"]) == t["requested"]
    pick = GEOMETRY[game][2]
    plain = [v[:pick] for v in t["variants"]]
    assert {n for v in plain for n in v} <= set(t["pool"])
    assert set(t["chances"]) == {"thresholds", "shown"}
    assert t["chances"]["shown"] == pytest.approx(
        {k: v for k, v in ticket_hit_probabilities(plain, GEOMETRY[game][1], GEOMETRY[game][0]).items()
         if k in t["chances"]["thresholds"]},
        abs=1e-15,
    )


def test_full_ticket_has_no_spread_mode():
    params = inspect.signature(build_full_ticket).parameters
    assert "spread" not in params and "compare" not in params


def test_ui_has_no_spread_checkbox_and_ignores_the_old_setting():
    """`_load_settings` citește numai UI_PERSIST_KEYS: bifa salvată pornită de
    versiunea veche nu mai ajunge în SETTINGS și dispare la următoarea salvare."""
    import ui_runtime

    assert "full_ticket_spread_val" not in ui_runtime.UI_PERSIST_KEYS
    assert "full_ticket_spread_val" not in ui_runtime.DEFAULTS
    src = Path(__file__).with_name("app_nicegui.py").read_text(encoding="utf-8")
    assert "Variante dispersate" not in src and "full_ticket_spread_val" not in src


def test_ui_texts_describe_the_pool_ticket():
    import ui_results

    t = build_full_ticket("6/49", _result("6/49"), 2)
    summary = ui_results._full_ticket_summary(t, "6/49")
    assert "acoperire garanție" in summary and "Lei" in summary
    lines = ui_results._full_ticket_chances(t, False)
    assert lines == [lines[0]]
    assert lines[0].startswith("Șansa exactă ca cel puțin o variantă să prindă: 3+")


def test_chance_thresholds_skip_the_invariant_jackpot():
    assert chance_thresholds(3, 6, 6) == [3, 4, 5]
    assert chance_thresholds(4, 5, 6) == [4, 5]
    assert chance_thresholds(3, 5, 5) == [3, 4]
