"""Pool extins pentru 4+ (2026-10-22): modulul de studiu și șansa exactă la 17-20.

Opțiunea a fost scoasă din aplicație în aceeași zi, la cererea utilizatorului
(joacă numai scheme LR). Rămân șansa exactă până la 20 de numere, folosită de
schemele LR 5/40 de 17-19 numere, și modulul `covering/extended_pool.py` cu
designurile lui, ca studiu.
"""

import random
from itertools import combinations
from math import comb

import pytest

from covering import extended_pool as xp
from covering import probability as prob
from covering.dispatch import generate_wheel

# joc: (bilet, extrase, univers, variante)
GAMES = {"6/49": (6, 6, 49, 152), "5/40": (5, 6, 40, 404), "joker": (5, 5, 45, 404)}


# --------------------------------------------------------------------------- #
# Șansa exactă până la 20 de numere
# --------------------------------------------------------------------------- #
def _random_masks(v, pick, n, seed):
    rng = random.Random(seed)
    return tuple(
        sorted({sum(1 << p for p in rng.sample(range(v), pick)) for _ in range(n)})
    )


@pytest.mark.parametrize("v,pick", [(6, 5), (9, 6), (12, 5), (16, 6)])
def test_numpy_table_equals_the_python_loop_up_to_16(v, pick):
    masks = _random_masks(v, pick, 25, v * 7 + pick)
    assert prob._hit_counts_numpy(v, masks) == prob._hit_counts.__wrapped__(v, masks)


def _brute_ticket_odds(v, tickets, draw_n, max_num):
    """P(cel puțin t pe o variantă), prin enumerarea submulțimilor pool-ului."""
    sets = [frozenset(t) for t in tickets]
    total = comb(max_num, draw_n)
    out = {t: 0 for t in range(1, draw_n + 1)}
    for k in range(0, min(v, draw_n) + 1):
        weight = comb(max_num - v, draw_n - k)
        for sub in combinations(range(v), k):
            best = max(len(s.intersection(sub)) for s in sets)
            for t in range(1, best + 1):
                out[t] += weight
    return {t: c / total for t, c in out.items()}


@pytest.mark.parametrize("v,pick,draw_n,max_num", [(17, 6, 6, 49), (18, 5, 6, 40), (20, 5, 5, 45)])
def test_exact_odds_over_16_match_brute_force(v, pick, draw_n, max_num):
    rng = random.Random(v)
    tickets = [rng.sample(range(v), pick) for _ in range(12)]
    got = prob.wheel_hit_probabilities(
        list(range(1, v + 1)), [[p + 1 for p in t] for t in tickets], draw_n, max_num
    )
    want = _brute_ticket_odds(v, tickets, draw_n, max_num)
    for t in range(1, draw_n + 1):
        assert got["ticket"][t] == pytest.approx(want[t], rel=1e-12, abs=1e-15)


def test_exact_odds_stop_at_20_numbers():
    with pytest.raises(ValueError):
        prob.wheel_hit_probabilities(list(range(1, 22)), [[1, 2, 3, 4, 5]], 5, 45)


# --------------------------------------------------------------------------- #
# Designurile livrate și certificarea
# --------------------------------------------------------------------------- #
def _base_cover(pick, draw_n, max_num, scores=None):
    base = list(range(1, 17))
    scores = scores or {n: float(30 - n) for n in base}
    wheel, cov = generate_wheel(
        "lajolla", base, pick, 4, 0, {n: scores[n] for n in base}, None, draw_n, max_num
    )
    assert cov == 100.0
    return wheel


@pytest.mark.parametrize("game", sorted(GAMES))
@pytest.mark.parametrize("v", xp.EXTENDED_SIZES)
def test_every_delivered_design_certifies(game, v):
    pick, draw_n, max_num, budget = GAMES[game]
    assert xp.extended_budget(pick) == budget
    pool = list(range(1, v + 1))
    scores = {n: float(30 - n) for n in pool}
    incumbent = _base_cover(pick, draw_n, max_num, scores)
    assert len(incumbent) == budget
    candidate, audit = xp.apply(pool, scores, incumbent, v, pick, draw_n, max_num)
    assert candidate is not None, audit.get("reason")
    assert audit["applied"] and audit["variants"] == budget
    assert len({tuple(t) for t in candidate}) == budget
    assert {n for t in candidate for n in t} == set(pool)
    assert audit["base_pool"] == list(range(1, 17))
    top = min(pick, draw_n)
    for t in range(3, top + 1):
        c = audit["chances"][str(t)]
        assert c["extended"] >= c["base"] - 1e-15
    assert audit["chances"]["4"]["extended"] > audit["chances"]["4"]["base"]
    groups = audit["groups4"]
    assert groups["base"] == comb(16, 4)
    assert groups["base"] <= groups["extended"] < groups["pool_total"] == comb(v, 4)
    assert audit["guarantee3_complete"] is True


def test_published_gains_at_4plus():
    """Cifrele din README-ul studiului (scripts/analysis/extended_pool)."""
    want = {
        ("6/49", 17): 0.08499139,
        ("6/49", 18): 0.09320038,
        ("5/40", 17): 0.17219582,
        ("5/40", 18): 0.17551154,
        ("joker", 17): 0.05094622,
        ("joker", 18): 0.05165258,
    }
    for (game, v), p4 in want.items():
        pick, draw_n, max_num, _ = GAMES[game]
        pool = list(range(1, v + 1))
        scores = {n: float(30 - n) for n in pool}
        _cand, audit = xp.apply(
            pool, scores, _base_cover(pick, draw_n, max_num, scores), v, pick, draw_n, max_num
        )
        assert audit["chances"]["4"]["extended"] == pytest.approx(p4, abs=5e-9)


def test_certification_rejects_with_a_reason():
    pick, draw_n, max_num, _ = GAMES["6/49"]
    incumbent = _base_cover(pick, draw_n, max_num)
    blocks, _sig = xp.load_design(18, pick, draw_n, max_num)
    candidate = [list(b) for b in blocks]

    why, _ = xp.certify(incumbent, candidate[:-1] + [candidate[0]], pick, draw_n, max_num)
    assert why == "designul are variante repetate"
    why, _ = xp.certify(incumbent[:-1], candidate, pick, draw_n, max_num)
    assert why.startswith("varianta de bază pe 16 numere are 151 variante, nu 152")
    # Baza pe alte 16 numere decât cele jucate de design.
    shifted = [[n + 30 for n in t] for t in incumbent]
    why, _ = xp.certify(shifted, candidate, pick, draw_n, max_num)
    assert why == "cele 16 numere de bază nu sunt toate în pool-ul extins"
    # Același design față de el însuși: niciun câștig strict.
    why, _ = xp.certify(candidate, candidate, pick, draw_n, max_num)
    assert why == "niciun câștig strict"
    # Ultima variantă strânsă pe prima: 4 numere deja împreună, deci mai puține
    # grupe de 4 noi și 4+ mai mic (5/40, aceleași 20 de numere jucate).
    spread = [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15], [16, 17, 18, 19, 20]]
    why, _ = xp.certify(spread + [[1, 6, 11, 16, 2]], spread + [[1, 2, 3, 4, 6]], 5, 6, 40)
    assert why == "șansa ar scădea la un prag"


def test_apply_without_option_or_with_a_narrow_pool_keeps_the_base():
    pick, draw_n, max_num, _ = GAMES["6/49"]
    incumbent = _base_cover(pick, draw_n, max_num)
    cand, audit = xp.apply(list(range(1, 19)), {}, incumbent, 0, pick, draw_n, max_num)
    assert cand is None and audit["reason"] == "mărime nelivrată"
    cand, audit = xp.apply(list(range(1, 17)), {}, incumbent, 18, pick, draw_n, max_num)
    assert cand is None and audit["reason"].startswith("pool-ul are 16 numere, nu 18")
    assert audit["base_pool"] == list(range(1, 17))


def test_a_corrupt_design_file_is_skipped(tmp_path, monkeypatch):
    pick, draw_n, max_num, budget = GAMES["6/49"]
    name = xp.design_name(18, pick, draw_n, max_num, budget)
    text = (xp.DESIGN_DIR / name).read_text(encoding="utf-8")
    (tmp_path / name).write_text(text.replace("\n1 ", "\n1 1 ", 1), encoding="utf-8")
    monkeypatch.setattr(xp, "DESIGN_DIR", tmp_path)
    assert xp.load_design(18, pick, draw_n, max_num) is None
    assert xp.design_signature(18, pick, draw_n, max_num) is None
    (tmp_path / name).write_text(text, encoding="utf-8")
    assert xp.load_design(18, pick, draw_n, max_num) is not None


@pytest.mark.parametrize("value,expected", [(None, 0), (0, 0), ("18", 18), (17, 17), (16, 0), (19, 0), ("x", 0)])
def test_normalize_size(value, expected):
    assert xp.normalize_size(value) == expected
