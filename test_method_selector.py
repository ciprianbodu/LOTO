"""
Teste pentru `loto_enterprise/core/method_selector.py` — partea de ENSEMBLE
(variance-reduction), parte din optimizarea "hits+4":

  1. `combine_ensemble_scores` — combinarea ponderată+normalizată a scorurilor
     mai multor metode. Cazul cu UN SINGUR membru trebuie să fie BIT-IDENTIC
     cu scorurile brute (fără normalizare) — ca să nu schimbăm comportamentul
     când decizia n-a construit un blend real.
  2. `get_ensemble_for_game` — citirea ensemble-ului din best_methods.json
     (config izolat, fișier temporar), cu fallback la scorer unic.
"""
from __future__ import annotations

import json

import pytest

from loto_enterprise.core import method_selector as ms


# ---------------------------------------------------------------------------
# combine_ensemble_scores
# ---------------------------------------------------------------------------
def test_single_member_returns_raw_scores_unchanged():
    """Cu UN SINGUR membru (weight irelevant), rezultatul trebuie să fie
    IDENTIC cu scorurile brute — bit-identic cu comportamentul pre-ensemble."""
    raw = {1: 0.2, 2: 0.9, 3: 0.05}
    out = ms.combine_ensemble_scores([("methodA", raw, 1.0)])
    assert out == {1: 0.2, 2: 0.9, 3: 0.05}


def test_single_member_ignores_weight_value():
    raw = {1: 0.2, 2: 0.9}
    out_w1 = ms.combine_ensemble_scores([("methodA", raw, 1.0)])
    out_w05 = ms.combine_ensemble_scores([("methodA", raw, 0.5)])
    assert out_w1 == out_w05 == raw


def test_empty_contributions_returns_empty_dict():
    assert ms.combine_ensemble_scores([]) == {}
    assert ms.combine_ensemble_scores([("m", {}, 1.0)]) == {}


def test_skips_members_with_empty_scores_and_renormalizes():
    """O metodă eșuată (raw gol) nu trebuie să reducă artificial suma
    ponderilor active — trebuie tratată ca simplu absentă."""
    raw_a = {1: 1.0, 2: 0.0}
    out_with_failed = ms.combine_ensemble_scores([
        ("methodA", raw_a, 0.5),
        ("methodFailed", {}, 0.5),
    ])
    out_single = ms.combine_ensemble_scores([("methodA", raw_a, 0.5)])
    assert out_with_failed == out_single


def test_two_members_min_max_normalized_before_weighted_sum():
    # methodA: numărul 1 e cel mai bun (max), numărul 2 cel mai slab (min).
    raw_a = {1: 10.0, 2: 0.0}
    # methodB: pe scală total diferită (0..1000), dar ACEEAȘI ordine relativă.
    raw_b = {1: 1000.0, 2: 0.0}
    out = ms.combine_ensemble_scores([
        ("methodA", raw_a, 0.5),
        ("methodB", raw_b, 0.5),
    ])
    # Ambele normalizate la [0,1]: num 1 -> 1.0, num 2 -> 0.0 în ambele metode.
    assert out[1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(0.0)


def test_two_members_weighted_combination_respects_weights():
    raw_a = {1: 1.0, 2: 0.0}  # num1=max(1.0), num2=min(0.0)
    raw_b = {1: 0.0, 2: 1.0}  # invers: num2=max, num1=min
    # Pondere 3:1 în favoarea metodei A -> num1 trebuie să domine combinat.
    out = ms.combine_ensemble_scores([
        ("methodA", raw_a, 0.75),
        ("methodB", raw_b, 0.25),
    ])
    assert out[1] > out[2]
    assert out[1] == pytest.approx(0.75)
    assert out[2] == pytest.approx(0.25)


def test_unnormalized_weights_are_renormalized_internally():
    """Ponderile de intrare NU trebuie să fie deja normalizate la sumă 1 —
    funcția renormalizează intern pe baza membrilor ACTIVI."""
    raw_a = {1: 1.0, 2: 0.0}
    raw_b = {1: 0.0, 2: 1.0}
    out_raw_weights = ms.combine_ensemble_scores([
        ("methodA", raw_a, 3.0),   # 3:1, la fel ca 0.75:0.25 mai sus
        ("methodB", raw_b, 1.0),
    ])
    assert out_raw_weights[1] == pytest.approx(0.75)
    assert out_raw_weights[2] == pytest.approx(0.25)


def test_flat_scores_no_division_by_zero():
    """Toate numerele cu același scor (span=0) — nu trebuie să crape."""
    raw = {1: 5.0, 2: 5.0, 3: 5.0}
    out = ms.combine_ensemble_scores([
        ("methodA", raw, 0.5),
        ("methodB", {1: 1.0, 2: 2.0, 3: 3.0}, 0.5),
    ])
    assert set(out.keys()) == {1, 2, 3}
    assert all(v == v for v in out.values())  # nu NaN


# ---------------------------------------------------------------------------
# get_ensemble_for_game
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_config(tmp_path):
    def _write(games: dict) -> str:
        path = tmp_path / "best_methods_test.json"
        path.write_text(json.dumps({"games": games}), encoding="utf-8")
        return str(path)
    return _write


def test_get_ensemble_falls_back_to_single_winner_when_no_ensemble_field(temp_config):
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {"scorer": "frequency", "sim_depth_pct": 30, "use_blacklist": False}
            }
        }
    })
    result = ms.get_ensemble_for_game("loto_6_49", pool_size=10, config_path=cfg_path)
    assert len(result) == 1
    name, fn, weight = result[0]
    assert name == "frequency"
    assert weight == 1.0
    assert callable(fn)


def test_get_ensemble_reads_multi_method_ensemble(temp_config):
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {
                    "scorer": "frequency",
                    "ensemble": [
                        {"method": "frequency", "weight": 0.6},
                        {"method": "ml_logistic", "weight": 0.4},
                    ],
                }
            }
        }
    })
    result = ms.get_ensemble_for_game("loto_6_49", pool_size=10, config_path=cfg_path)
    names = {name for name, _fn, _w in result}
    assert "frequency" in names
    assert "random" not in names
    # ml_logistic e în METHODS (nu e blacklistat); dacă import sklearn eșuează tot rămâne frequency
    assert abs(sum(w for _n, _fn, w in result) - 1.0) < 1e-9
    if "ml_logistic" in names:
        weights = {name: w for name, _fn, w in result}
        assert weights["frequency"] == pytest.approx(0.6)
        assert weights["ml_logistic"] == pytest.approx(0.4)


def test_ensemble_skips_random_even_if_listed(temp_config):
    """random e EXCLUDED_FROM_PRODUCTION — nu intră în blend chiar dacă e în JSON."""
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {
                    "scorer": "frequency",
                    "ensemble": [
                        {"method": "frequency", "weight": 0.5},
                        {"method": "random", "weight": 0.5},
                    ],
                }
            }
        }
    })
    result = ms.get_ensemble_for_game("loto_6_49", pool_size=10, config_path=cfg_path)
    assert len(result) == 1
    assert result[0][0] == "frequency"
    assert result[0][2] == pytest.approx(1.0)


def test_dead_scorer_live_ensemble_aligns_winner(temp_config):
    """Scorer mort + ensemble viu → winner/scorer/ensemble = același membru."""
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {
                    "scorer": "recency",
                    "ensemble": [
                        {"method": "frequency", "weight": 0.6},
                        {"method": "recency", "weight": 0.4},
                    ],
                }
            }
        }
    })
    assert ms.get_winner_name("loto_6_49", pool_size=10, config_path=cfg_path) == "frequency"
    ens = ms.get_ensemble_for_game("loto_6_49", pool_size=10, config_path=cfg_path)
    assert len(ens) == 1
    assert ens[0][0] == "frequency"
    rec = ms.recommend_optimal_config("loto_6_49", 10, config_path=cfg_path)
    assert rec["scorer"] == "frequency"
    assert rec["fallback"] is True
    assert all(e["method"] == "frequency" for e in rec["ensemble"])


def test_get_winner_rejects_random_scorer(temp_config):
    """random e EXCLUDED_FROM_PRODUCTION — scorer → frequency."""
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {"scorer": "random", "ensemble": [{"method": "random", "weight": 1.0}]},
            }
        }
    })
    assert ms.get_winner_name("loto_6_49", pool_size=10, config_path=cfg_path) == "frequency"


def test_unknown_legacy_alias_falls_back_to_frequency_named(temp_config):
    """ml_xgb_cpu (alias mort) → frequency pe NUME și pe callable, nu nume fals."""
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {
                    "scorer": "ml_xgb_cpu",
                    "ensemble": [{"method": "ml_xgb_cpu", "weight": 1.0}],
                }
            }
        }
    })
    assert ms.get_winner_name("loto_6_49", pool_size=10, config_path=cfg_path) == "frequency"
    ens = ms.get_ensemble_for_game("loto_6_49", pool_size=10, config_path=cfg_path)
    assert len(ens) == 1
    assert ens[0][0] == "frequency"
    rec = ms.recommend_optimal_config("loto_6_49", 10, config_path=cfg_path)
    assert rec["scorer"] == "frequency"
    assert all(e.get("method") == "frequency" for e in (rec.get("ensemble") or []))
    assert rec["scorer"] not in ("ml_xgb_cpu", "ml_xgb", "random")


def test_get_ensemble_skips_unknown_method_and_renormalizes(temp_config):
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {
                    "scorer": "frequency",
                    "ensemble": [
                        {"method": "frequency", "weight": 0.5},
                        {"method": "this_method_does_not_exist", "weight": 0.5},
                    ],
                }
            }
        }
    })
    result = ms.get_ensemble_for_game("loto_6_49", pool_size=10, config_path=cfg_path)
    assert len(result) == 1
    name, _fn, weight = result[0]
    assert name == "frequency"
    assert weight == pytest.approx(1.0)  # renormalizat, singurul membru rămas


def test_get_ensemble_all_unavailable_falls_back_to_winner(temp_config):
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {
                    "scorer": "frequency",
                    "ensemble": [
                        {"method": "nope1", "weight": 0.5},
                        {"method": "nope2", "weight": 0.5},
                    ],
                }
            }
        }
    })
    result = ms.get_ensemble_for_game("loto_6_49", pool_size=10, config_path=cfg_path)
    assert len(result) == 1
    name, _fn, weight = result[0]
    assert name == "frequency"  # fallback la get_winner_name (scorer)
    assert weight == 1.0


def test_get_winner_name_nearest_pool(temp_config):
    """Pool k11 absent → folosește k10 (cel mai apropiat decis), fără WARNING."""
    cfg_path = temp_config({
        "loto_6_49": {
            "auto_pilot_per_pool": {
                "k10": {
                    "scorer": "frequency",
                    "ensemble": [{"method": "frequency", "weight": 1.0}],
                },
            }
        }
    })
    assert ms.get_winner_name("loto_6_49", pool_size=11, config_path=cfg_path) == "frequency"


def test_joker_urna2_uses_the_configured_top1_benchmark_ensemble(temp_config):
    """Urna 2 citește propria decizie top-1, nu mai este blocată pe frequency."""
    cfg_path = temp_config({
        "joker_urna2": {
            "auto_pilot_per_pool": {
                "k1": {
                    "scorer": "autocorr",
                    "hit_target": 1,
                    "target_label": "top-1 (1/1)",
                    "ensemble": [
                        {"method": "autocorr", "weight": 0.7},
                        {"method": "frequency", "weight": 0.3},
                    ],
                }
            }
        }
    })
    assert ms.get_winner_name("joker_urna2", pool_size=1, config_path=cfg_path) == "autocorr"
    result = ms.get_ensemble_for_game("joker_urna2", pool_size=1, config_path=cfg_path)
    assert [name for name, _fn, _weight in result] == ["autocorr", "frequency"]
    assert sum(weight for _name, _fn, weight in result) == pytest.approx(1.0)
    cfg = ms.recommend_optimal_config("joker_urna2", 1, config_path=cfg_path)
    assert (cfg["hit_target"], cfg["target_label"]) == (1, "top-1 (1/1)")
