"""All saved LR position certificates remain tied to their full paid witnesses."""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from loto_enterprise.core.lotteries import require_lottery
from scripts.analysis import verify_lr_experimental_archive as experiment
from scripts.analysis.verify_lr_portfolio_witnesses import _compare_to_incumbent, _verify_portfolio


ROOT = Path(__file__).resolve().parents[1]
WITNESSES = ROOT / "testdata/lr_experimental_witnesses_2026_11_25.json"
CERTIFICATES = ROOT / "covering_designs/lr/expanded_certificates.json"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_174_witnesses_recount_and_66_certificates_match():
    archive, compact = _read(WITNESSES), _read(CERTIFICATES)
    source = archive["source"]
    assert source["kind"] == "user_archive"
    assert source["sha256"] == compact["archive_sha256"] == experiment.ARCHIVE_SHA256
    assert source["commit"] == compact["source_commit"] == experiment.SOURCE_COMMIT
    assert source["candidate_sha256"] == compact["candidate_sha256"]
    assert compact["method"] == experiment.METHOD
    assert archive["evaluator"] == compact["evaluator"] == experiment.EVALUATOR
    assert compact["geometries"] == experiment._catalog_metadata()
    assert len(archive["cases"]) == len(compact["certificates"]) == 66
    members = {group: member for member, group, _, _ in experiment.REPORTS}
    assert set(source["members"]) == set(members.values())
    assert len({case["id"] for case in archive["cases"]}) == 66
    counts = {"reference": 0, "integration": 0}
    improvements = {"reference": 0, "integration": 0}

    for case, certificate in zip(archive["cases"], compact["certificates"], strict=True):
        group, source_case_id = case["id"].split(":", 1)
        assert case["source_member"] == members[group]
        assert case["source_case_id"] == source_case_id
        lot = require_lottery(case["game"], "RO")
        assert len(case["pool"]) == len(set(case["pool"])) == case["pool_size"]
        assert all(type(n) is int and 1 <= n <= lot.max_n for n in case["pool"])
        assert len(case["jokers"]) == len(set(case["jokers"])) == case["joker_slots"]
        if lot.geo.second:
            assert case["joker_slots"] > 0
            assert all(type(n) is int and 1 <= n <= lot.geo.second.max_n for n in case["jokers"])
        else:
            assert case["joker_slots"] == 0
        products = ("baseline", "incumbent", "witness") if group == "reference" else ("incumbent", "witness")
        for role in products:
            placed = case[role]
            exact = _verify_portfolio(
                placed, lot, case["pool_size"], case["joker_slots"], case["budget_bani"], case["objective"],
            )
            assert exact == placed["metrics"]
        counts[group] += len(products)
        paired = _compare_to_incumbent(case["incumbent"]["metrics"], case["witness"]["metrics"], lot.pick_n)
        assert paired["classification"] == "equal_cost_witness"
        delta = paired["delta"]
        assert case["production_to_experimental"] == {
            "objective_gain": delta["attained"],
            "cost_change_bani": delta["cost_bani"],
            "tail_gains": {threshold: delta[threshold] for threshold in ("4+", "5+", "6+")},
        }
        improvements[group] += paired["delta"]["attained"] > 0

        for key in ("game", "objective", "pool_size", "joker_slots", "budget_bani", "source_case_id", "source_member"):
            assert certificate[key] == case[key]
        assert certificate["incumbent_cost_bani"] == case["incumbent"]["metrics"]["cost_bani"]
        packed = json.dumps(case["incumbent"]["tickets"], sort_keys=True, separators=(",", ":")).encode("ascii")
        assert certificate["incumbent_sha256"] == sha256(packed).hexdigest()
        assert certificate["tickets"] == case["witness"]["tickets"]
        assert certificate["metrics"] == case["witness"]["metrics"]

    assert counts == {"reference": 126, "integration": 48}
    assert improvements == {"reference": 8, "integration": 7}


def test_joker_two_and_five_slot_certificates_keep_distinct_geometries():
    cases = [
        case for case in _read(WITNESSES)["cases"]
        if (case["game"], case["objective"], case["budget_bani"]) == ("joker", "any_prize", 30_000)
    ]
    pair = {case["joker_slots"]: case for case in cases}
    assert set(pair) == {2, 5}
    assert pair[2]["production_to_experimental"]["objective_gain"] > 0
    assert pair[5]["production_to_experimental"]["objective_gain"] == 0
    for case in pair.values():
        assert len(case["jokers"]) == case["joker_slots"]
        assert all(0 <= zone["joker_slot"] < len(case["jokers"]) for ticket in case["witness"]["tickets"] for zone in ticket)


def test_archive_with_different_bytes_is_rejected(tmp_path):
    altered = tmp_path / "Teste_LOTO_2026_11_25.zip"
    altered.write_bytes(b"different archive")
    with pytest.raises(ValueError, match="Amprenta ZIP diferă"):
        experiment.verify_archive(altered)
