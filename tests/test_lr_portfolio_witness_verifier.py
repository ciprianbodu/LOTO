"""Acceptance compares complete, individually verified paid LR portfolios."""

from copy import deepcopy
from hashlib import sha256
from itertools import combinations
import json
from math import comb
from pathlib import Path

import pytest

from covering.lr_portfolio import FEE_BANI
from loto_enterprise.core.lotteries import require_lottery
from scripts.analysis.verify_lr_portfolio_witnesses import _compare_to_incumbent, _verify_portfolio, verify_witnesses


_TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


@pytest.fixture(scope="module")
def controls():
    source = _TESTDATA / "lr_portfolio_witness_controls.json"
    return json.loads(source.read_text(encoding="utf-8"))["cases"]


def _recount(raw, case, budget=None):
    lot = require_lottery(case["game"], "RO")
    return _verify_portfolio(raw, lot, case["pool_size"], case["joker_slots"],
                             case["budget_bani"] if budget is None else budget, case["objective"])


def test_recounted_portfolio_without_a_paid_duplicate_is_cheaper_and_safe(controls):
    case = next(item for item in controls if item["game"] == "6/49")
    original = case["incumbent"]
    repeated = deepcopy(original)
    repeated["tickets"] *= 2
    repeated["paid_variants"] *= 2
    metrics = repeated["metrics"]
    metrics["cost_bani"] *= 2
    metrics["zones"] *= 2
    metrics["physical_tickets"] *= 2
    metrics["duplicate_paid_variants"] += len(original["paid_variants"])
    budget = metrics["cost_bani"]
    incumbent = _recount(repeated, case, budget)
    cheaper = _recount(original, case, budget)
    decision = _compare_to_incumbent(incumbent, cheaper, 6)
    assert decision["classification"] == "lower_cost_witness"
    assert decision["delta"]["cost_bani"] == -cheaper["cost_bani"]
    assert decision["delta"]["attained"] == 0
    assert decision["strictly_improves"] is True

    equal = _compare_to_incumbent(cheaper, cheaper, 6)
    assert equal["classification"] == "equal_cost_witness"
    assert equal["strictly_improves"] is False
    with pytest.raises(ValueError, match="costul"):
        _compare_to_incumbent(cheaper, incumbent, 6)


def test_recounted_cheaper_joker_portfolio_is_rejected_when_it_loses_prize_coverage(controls):
    case = next(item for item in controls if item["game"] == "joker"
                and item["objective"] == "any_prize" and item["budget_bani"] == 10_000)
    incumbent = _recount(case["incumbent"], case)
    shorter = deepcopy(case["incumbent"])
    shorter["tickets"][0].pop()
    remaining_slot = shorter["tickets"][0][0]["joker_slot"]
    shorter["paid_variants"] = [entry for entry in shorter["paid_variants"] if entry["joker_slot"] == remaining_slot]
    variants = [set(entry["positions"]) for entry in shorter["paid_variants"]]
    width = case["pool_size"]

    # Recount the remaining slot independently over every in-pool intersection.
    four = five = prizes = 0
    for count in range(6):
        for inside in combinations(range(1, width + 1), count):
            best = max(len(variant.intersection(inside)) for variant in variants)
            multiplicity = comb(45 - width, 5 - count)
            four += multiplicity * (best >= 4)
            five += multiplicity * (best >= 5)
            prizes += multiplicity * (20 if best >= 3 else int(best >= 1))
    lot = require_lottery("joker", "RO")
    shorter["metrics"].update({"attained": prizes, "any_prize": prizes, "4+": four, "5+": five,
                               "zones": 1, "cost_bani": len(variants) * round(lot.price * 100) + FEE_BANI})
    witness = _recount(shorter, case)
    assert witness["cost_bani"] < incumbent["cost_bani"]
    assert witness["attained"] < incumbent["attained"]
    with pytest.raises(ValueError, match="reduce rezultatul pentru obiectivul selectat"):
        _compare_to_incumbent(incumbent, witness, 5)


@pytest.mark.parametrize("field", ["attained", "4+", "5+", "6+"])
def test_same_cost_gain_cannot_trade_away_an_applicable_threshold(field):
    incumbent = {"attained": 30, "any_prize": 90, "4+": 29, "5+": 15, "6+": 2, "cost_bani": 9600}
    witness = dict(incumbent)
    rival = "4+" if field == "attained" else "attained"
    witness[rival] += 1
    witness[field] -= 1
    with pytest.raises(ValueError, match="reduce rezultatul"):
        _compare_to_incumbent(incumbent, witness, 6)


def test_all_controls_match_the_raw_source_portfolios():
    source = _TESTDATA / "portofolii_initiale_e6cad78.json"
    controls = json.loads((_TESTDATA / "lr_portfolio_witness_controls.json").read_text(encoding="utf-8"))
    checked = verify_witnesses(controls, source)
    assert checked["source_bytes_verified"] is True
    assert checked["source_portfolios_verified"] is True
    assert len(checked["cases"]) == 66


def test_rehashed_raw_joker_slot_swap_is_detected_at_equal_metrics(tmp_path):
    source = _TESTDATA / "portofolii_initiale_e6cad78.json"
    controls = json.loads((_TESTDATA / "lr_portfolio_witness_controls.json").read_text(encoding="utf-8"))
    raw = json.loads(source.read_text(encoding="utf-8"))
    selected = "generator-joker-100-any_prize"
    case = next(row for row in controls["cases"] if row["id"] == selected)
    original = next(row for row in raw if row["id"] == selected)
    first, second = original["tickets"][0]
    slot_a, slot_b = (original["jokers"].index(zone["joker"]) for zone in (first, second))
    assert slot_a != slot_b

    first["joker"], second["joker"] = second["joker"], first["joker"]
    swapped_projection = deepcopy(case["incumbent"])
    swaps = {slot_a: slot_b, slot_b: slot_a}
    for entry in (*swapped_projection["tickets"][0], *swapped_projection["paid_variants"]):
        entry["joker_slot"] = swaps[entry["joker_slot"]]
    assert _recount(swapped_projection, case) == case["incumbent"]["metrics"]

    altered = tmp_path / source.name
    data = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    altered.write_bytes(data)
    controls["source"]["sha256"] = sha256(data).hexdigest()
    with pytest.raises(ValueError, match="Biletele fizice din sursa brută diferă"):
        verify_witnesses(controls, altered)
