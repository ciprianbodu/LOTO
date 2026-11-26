"""Certificates apply to paid position layouts and exact Joker roles, across pool relabelings."""

import json
from copy import deepcopy
from time import monotonic

import pytest

from covering import lr_expanded_search as search
from covering import lr_expanded_store as store
from covering import lr_expanded_templates as templates
from covering import lr_schemes
from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_catalog_cache import refresh_catalog
from covering.lr_pool_budget import restrict_to_pool
from loto_enterprise.benchmark.lr_provenance import layout_formulas
from loto_enterprise.core.lotteries import require_lottery
from scripts.analysis.compare_lr_portfolio_search import exact


@pytest.mark.parametrize("game,width,budget,slots,objective,score,cost", [
    ("6/49", 16, 300, (), FOUR_PLUS, 383414, 29650),
    ("6/49", 16, 400, (), FOUR_PLUS, 468744, 39250),
    ("5/40", 19, 300, (), ANY_PRIZE, 119402, 28550),
    ("joker", 15, 300, (20, 3), ANY_PRIZE, 5111685, 29550),
    ("joker", 15, 300, (20, 3, 17, 1, 4), ANY_PRIZE, 6720220, 29600),
])
def test_actual_paid_certificate_and_relabelled_hot_template(monkeypatch, game, width, budget,
                                                             slots, objective, score, cost):
    def live_search_should_not_run(*args, **kwargs):
        pytest.fail("certified scope unnecessarily invoked expensive live search")

    monkeypatch.setattr(search, "refine_expanded", live_search_should_not_run)
    plan, = recommend_games(budget, objective, game)
    first = restrict_to_pool(deepcopy(plan), objective, range(1, width + 1), slots)
    counts = templates.enhance_cached.cache_info()
    pool = tuple(range(3, 3 + width))
    later = restrict_to_pool(deepcopy(plan), objective, reversed(pool), tuple(reversed(slots)))
    assert templates.enhance_cached.cache_info().hits == counts.hits + 1
    assert first["attained"] == later["attained"] == score
    assert first["price_bani"] == later["price_bani"] == cost
    assert [z["numbers"] for t in later["tickets"] for z in t] == [
        [n + 2 for n in z["numbers"]] for t in first["tickets"] for z in t]
    if slots:
        mapped = dict(zip(slots, reversed(slots)))
        assert [z["joker"] for t in later["tickets"] for z in t] == [
            mapped[z["joker"]] for t in first["tickets"] for z in t]
    recounted = exact(later, pool, tuple(reversed(slots)))
    assert recounted["attained"] == score and recounted["cost_bani"] == cost
    report = later["optimization"]
    assert all(recounted[f"{n}+"] == value for n, value in report["protected_numerators"].items())
    cert = report["expanded_certificate"]
    assert cert["source_member"] == ("cover/integration_results.json" if len(slots) == 5 else "cover/cover_results.json")
    assert len(cert["incumbent_sha256"]) == len(cert["source_incumbent_sha256"]) == 64
    assert later["optimal"] == (score == later["upper"])


def _candidate_data():
    data = json.loads((lr_schemes.LR_DIR / "expanded_certificates.json").read_bytes())
    entry = next(e for e in data["certificates"] if e["source_case_id"] == "6/49|four_plus|300 lei")
    lot = require_lottery("6/49", "RO")
    zones = templates._unpack(entry, lot, 16, 0)
    metrics = entry["metrics"]
    floor = tuple((str(n), metrics[f"{n}+"]) for n in (4, 5, 6))
    return data, entry, zones, floor, templates.geometry(lot)


def test_changed_incumbent_with_identical_payments_and_probabilities_is_recertified():
    _, entry, zones, floor, tariff = _candidate_data()
    swapped = tuple(reversed(zones))
    assert store.incumbent_hash(zones, tariff[2]) != store.incumbent_hash(swapped, tariff[2])
    metrics = entry["metrics"]
    args = ("6/49", 16, FOUR_PLUS, 30000, metrics["cost_bani"], 0)
    result = templates._from_certificate(*args, swapped, metrics["attained"], floor, 0,
                                         metrics["denominator"], tariff, monotonic() + 5, None)
    assert result[0] == swapped
    cert = result[-1]["expanded_certificate"]
    assert cert["verification"] == "recertified_incumbent"
    assert cert["incumbent_sha256"] == store.incumbent_hash(swapped, tariff[2])
    assert cert["source_incumbent_sha256"] == entry["incumbent_sha256"] != cert["incumbent_sha256"]
    with pytest.raises(ValueError, match="numărătorii plătiți"):
        templates._from_certificate(*args, swapped, metrics["attained"], (("4", 1),), 0,
                                    metrics["denominator"], tariff, monotonic() + 5, None)


@pytest.mark.parametrize("corruption", ["position", "slot", "cost", "count"])
def test_invalid_archive_never_changes_the_incumbent(monkeypatch, corruption):
    data, entry, zones, floor, tariff = _candidate_data()
    metrics = entry["metrics"].copy()
    data["certificates"] = [entry]
    if corruption == "position":
        entry["tickets"][0][0]["positions"][0] = 99
    elif corruption == "slot":
        entry["tickets"][0][0]["joker_slot"] = 0
    elif corruption == "cost":
        entry["metrics"]["cost_bani"] -= 50
    else:
        entry["metrics"]["5+"] -= 1
    monkeypatch.setattr(templates, "certificate_snapshot", lambda directory: ("changed", json.dumps(data).encode()))
    result = templates.enhance("6/49", 16, FOUR_PLUS, 30000, metrics["cost_bani"], 0, 4,
                               metrics["denominator"], zones, metrics["attained"], floor, 0, tariff, 2, None)
    assert result is None


def test_changed_archive_changes_semantics_even_when_size_and_timestamp_are_preserved(tmp_path, monkeypatch):
    import os

    archive = tmp_path / "expanded_certificates.json"
    archive.write_bytes(b'{"v":1}')
    timestamp = archive.stat()
    monkeypatch.setattr(lr_schemes, "LR_DIR", tmp_path)
    first = refresh_catalog(tmp_path), layout_formulas()
    archive.write_bytes(b'{"v":2}')
    os.utime(archive, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    second = refresh_catalog(tmp_path), layout_formulas()
    assert all(a != b for a, b in zip(first, second))
    old = layout_formulas()
    monkeypatch.setattr(search, "EVALUATOR_VERSION", "replacement")
    assert layout_formulas() != old


def test_bound_cancellation_and_search_errors_return_current_paid_plan(monkeypatch):
    data, entry, zones, floor, tariff = _candidate_data()
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("simulated")

    monkeypatch.setattr(templates, "_from_certificate", unavailable)
    score = entry["metrics"]["attained"]
    def run(upper, seconds=2, cancelled=None):
        return templates.enhance("6/49", 16, FOUR_PLUS, 30000, 29650, 0, 4, upper,
                                 zones, score, floor, 0, tariff, seconds, cancelled)
    assert run(score) is None and run(score + 1, 0) is None
    assert run(score + 1, cancelled=lambda: True) is None and not calls
    assert run(score + 1) is None and calls == [True]
    monkeypatch.setattr(templates, "_from_certificate", lambda *args: None)
    monkeypatch.setattr(search, "refine_expanded", unavailable)
    assert run(score + 1) is None and calls == [True, True]



def test_equal_objective_cost_and_duplicates_adopt_a_strictly_better_protected_tail(monkeypatch):
    data, entry, zones, floor, tariff = _candidate_data()
    starting = tuple(reversed(zones))
    entry["incumbent_sha256"] = store.incumbent_hash(starting, tariff[2])
    data["certificates"] = [entry]
    monkeypatch.setattr(templates, "certificate_snapshot", lambda directory: ("changed", json.dumps(data).encode()))
    # The preceding verified optimizer supplies these floors. Hold everything but 5+ equal.
    protected = tuple((t, n - (t == "5")) for t, n in floor)
    result = templates._from_certificate("6/49", 16, FOUR_PLUS, 30000, 29650, 0, starting,
                                         entry["metrics"]["attained"], protected, 0, 13983816,
                                         tariff, monotonic() + 5, None)
    assert result[0] == zones and result[3]["expanded_certificate"]["applied"]
    assert result[3]["protected_numerators"] == dict(floor)


def test_new_geometry_is_recounted_and_reused_in_another_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    env = dict(os.environ, LOTO_RUNTIME_DIR=str(tmp_path), LOTO_WF_CACHE_DIR=str(tmp_path / "wf"),
               PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    script = """
import json
from covering import lr_expanded_templates as t
from covering.lr_catalog_cache import refresh_catalog
from covering.lr_schemes import LR_DIR
from loto_enterprise.core.lotteries import require_lottery
refresh_catalog(LR_DIR)
entry = next(e for e in json.loads((LR_DIR / 'expanded_certificates.json').read_bytes())['certificates']
             if e['source_case_id'] == '6/49|four_plus|300 lei')
lot = require_lottery('6/49', 'RO')
zones = t._unpack(entry, lot, 16, 0)
m = entry['metrics']
floor = tuple((str(n), m[f'{n}+']) for n in (4, 5, 6))
result = t.enhance_cached('6/49', 16, 'four_plus', 30001, 29650, 0, 4, 13983816, zones,
                          m['attained'], floor, 0, t.geometry(lot))
print(json.dumps(result))
"""
    def run():
        completed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                                   env=env, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(completed.stdout)

    first = run()
    cache, = (tmp_path / "wf" / "lr_expanded").glob("*.json")
    frozen = cache.read_bytes()
    persisted = json.loads(frozen)
    assert persisted["context"]["budget_bani"] == 30001
    second = run()
    assert second[:3] == first[:3] and cache.read_bytes() == frozen
    assert second[3]["expanded_local_certificate"] == first[3]["expanded_local_certificate"]
    assert "search" in first[3] and "search" not in second[3]
    persisted["metrics"]["5+"] -= 1
    cache.write_text(json.dumps(persisted), encoding="utf8")
    repaired = run()
    assert "search" in repaired[3]
    assert json.loads(cache.read_bytes())["metrics"]["5+"] == repaired[3]["protected_numerators"]["5"]


@pytest.mark.parametrize("reason", [{"stopped": "cancelled"}, {"fallback": "search_error"}])
def test_incomplete_search_is_never_memoized(monkeypatch, tmp_path, reason):
    import runtime_paths

    monkeypatch.setattr(runtime_paths, "WF_CACHE_DIR", tmp_path)
    _, entry, zones, floor, tariff = _candidate_data()
    score = entry["metrics"]["attained"]
    calls = []

    def unfinished(*args, **kwargs):
        calls.append(1)
        return zones, score, 29650, {"protected_numerators": dict(floor),
                                     "duplicate_paid_variants": 0, "search": reason}

    monkeypatch.setattr(search, "refine_expanded", unfinished)
    for _ in range(2):
        result = templates.enhance_cached("6/49", 16, FOUR_PLUS, 30001, 29650, 0, 4, 13983816,
                                          zones, score, floor, 0, tariff)
        assert result[0] == zones
    assert len(calls) == 2 and not tuple(tmp_path.rglob("*.json"))


def test_unreadable_optional_certificate_keeps_paid_plan_and_can_recover(tmp_path, monkeypatch):
    import shutil

    source = lr_schemes.LR_DIR
    catalog = tmp_path / "lr"
    shutil.copytree(source, catalog, ignore=shutil.ignore_patterns("expanded_certificates.json"))
    cert = catalog / "expanded_certificates.json"
    cert.mkdir()
    monkeypatch.setattr(lr_schemes, "LR_DIR", catalog)
    plan, = recommend_games(300, FOUR_PLUS, "6/49")
    current = restrict_to_pool(deepcopy(plan), FOUR_PLUS, range(1, 17))
    assert current["attained"] > 0 and "expanded_certificate" not in current["optimization"]
    before = layout_formulas()
    cert.rmdir()
    shutil.copyfile(source / cert.name, cert)
    assert before != layout_formulas()
    recovered = restrict_to_pool(deepcopy(plan), FOUR_PLUS, range(1, 17))
    assert recovered["attained"] == 383414 and "expanded_certificate" in recovered["optimization"]
