"""Substituirea nearest-k trebuie SEMNALIZATĂ, nu doar logată.

Bench-ul evaluează un set fix de pool-uri. Dacă UI-ul cere unul din afara lui,
`recommend_optimal_config` întorcea configul celui mai apropiat pool decis — cu
`rationale` copiat VERBATIM din acea intrare. UI-ul îl afișează lângă 🏆, deci
tipărea cifrele măsurate la k12 ca și cum ar fi ale pool-ului k16 cerut.
"""
import json

import pytest

from loto_enterprise.core.method_selector import recommend_optimal_config

_CFG = {"games": {"loto_6_49": {"auto_pilot_per_pool": {
    "k10": {"scorer": "frequency", "sim_depth_pct": 30, "avg_hits": 1.1,
            "rationale": "Wilson_lb=0.0903 la pool 10",
            "ensemble": [{"method": "frequency", "weight": 1.0}]},
    "k12": {"scorer": "fourier", "sim_depth_pct": 60, "avg_hits": 1.3,
            "rationale": "Wilson_lb=0.1102 la pool 12",
            "ensemble": [{"method": "fourier", "weight": 1.0}]},
}}}}


@pytest.fixture()
def cfg_path(tmp_path):
    p = tmp_path / "bm.json"
    p.write_text(json.dumps(_CFG), encoding="utf-8")
    return str(p)


def test_exact_pool_is_not_marked_as_substituted(cfg_path):
    c = recommend_optimal_config("loto_6_49", 10, config_path=cfg_path)
    assert c["scorer"] == "frequency"
    assert c.get("pool_substituted") is None
    assert "măsurat la pool" not in c["rationale"]


@pytest.mark.parametrize("asked,expected_k", [(11, 10), (16, 12), (6, 10)])
def test_substituted_pool_is_flagged(cfg_path, asked, expected_k):
    c = recommend_optimal_config("loto_6_49", asked, config_path=cfg_path)
    sub = c.get("pool_substituted")
    assert sub == {"requested": asked, "used": expected_k}
    # și rationale-ul spune de unde vin cifrele, ca UI-ul să nu le atribuie greșit
    assert f"măsurat la pool {expected_k}" in c["rationale"]
    assert _CFG["games"]["loto_6_49"]["auto_pilot_per_pool"][f"k{expected_k}"][
        "rationale"] in c["rationale"]


def test_key_present_on_the_no_decision_fallback(tmp_path):
    """Apelanții pot citi cheia necondiționat, fără KeyError."""
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"games": {"loto_6_49": {}}}), encoding="utf-8")
    c = recommend_optimal_config("loto_6_49", 12, config_path=str(p))
    assert "pool_substituted" in c and c["pool_substituted"] is None
