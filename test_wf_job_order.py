"""Walk-forward trebuie să ruleze Joker → 5/40 → 6/49, indiferent de ordinea CSV-urilor.

Regresia: _iter_wf_jobs itera pe results_bundle (ordinea de încărcare). Upload
6/49, 5/40, joker → 6/49 era 1/3 și mânca bugetul. Contract: 6/49 ULTIM.
"""
from __future__ import annotations

WF_GAME_ORDER = {"joker": 0, "5/40": 1, "6/49": 2}


def _label(name: str) -> str:
    low = name.lower()
    if "5_40" in low or "5/40" in low:
        return "5/40"
    if "joker" in low:
        return "joker"
    return "6/49"


def _ordered_jobs(bundle):
    jobs = []
    for fname, outs in bundle:
        for g_label, data in outs.items():
            jobs.append((fname, g_label, data, False))
    jobs.sort(key=lambda j: WF_GAME_ORDER.get(_label(str(j[1])), 99))
    return jobs


def test_wf_order_ignores_csv_upload_order():
    bundle = [
        ("loto_6_49.csv", {"6/49": {"pool_size": 11}}),
        ("loto_5_40.csv", {"5/40": {"pool_size": 11}}),
        ("joker.csv", {"joker": {"pool_size": 11}}),
    ]
    assert [j[1] for j in _ordered_jobs(bundle)] == ["joker", "5/40", "6/49"]


def test_wf_order_already_sorted_stays():
    bundle = [
        ("joker.csv", {"joker": {}}),
        ("loto_5_40.csv", {"5/40": {}}),
        ("loto_6_49.csv", {"6/49": {}}),
    ]
    assert [j[1] for j in _ordered_jobs(bundle)] == ["joker", "5/40", "6/49"]
