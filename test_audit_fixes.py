"""Regresii din auditul global — fără pandas (rulează în containerul cloud)."""
from __future__ import annotations

from loto_enterprise.benchmark.hit_target import clamp_bench_hit_target
from loto_enterprise.core import method_selector as ms
from loto_enterprise.core.wf_sig import ensemble_sig, lookback_pct


def test_clamp_bench_hit_target_only_3_or_4():
    assert clamp_bench_hit_target(3) == 3
    assert clamp_bench_hit_target(4) == 4
    assert clamp_bench_hit_target("4") == 4
    assert clamp_bench_hit_target(5) == 3
    assert clamp_bench_hit_target(2) == 3
    assert clamp_bench_hit_target("nope") == 3
    assert clamp_bench_hit_target(None) == 3
    assert clamp_bench_hit_target(5, default=4) == 4


def test_ensemble_sig_stable_for_list_of_dicts():
    """Lista {method, weight} trebuie serializată stabil (nu str(dict) dependent de ordine)."""
    a = ensemble_sig([
        {"method": "b", "weight": 0.4},
        {"method": "a", "weight": 0.6},
    ])
    b = ensemble_sig([
        {"method": "a", "weight": 0.6},
        {"method": "b", "weight": 0.4},
    ])
    assert a == b == "a:0.6,b:0.4"


def test_ensemble_sig_dict_and_empty():
    assert ensemble_sig({"z": 0.2, "a": 0.8}) == "a:0.8,z:0.2"
    assert ensemble_sig([]) == ""
    assert ensemble_sig(None) == ""


def test_lookback_pct_zero_means_all_history():
    """0 în UI = tot istoricul. Altfel 50 ≠ 100 → cache WF diferit."""
    assert lookback_pct(0) == 100
    assert lookback_pct(0.0) == 100
    assert lookback_pct(None) == 100
    assert lookback_pct(100.0) == 100
    assert lookback_pct(50) == 50
    assert lookback_pct(50) != lookback_pct(100)


def test_combine_all_nan_returns_empty_not_poisoned_pool():
    """Fallback pe dict cu NaN otrăvea rank_by_score — trebuie {} → frequency."""
    audit: dict = {}
    out = ms.combine_ensemble_scores(
        [("bad", {1: float("nan"), 2: 1.0, 3: 0.0}, 1.0)],
        audit=audit,
    )
    assert out == {}
    assert audit.get("ensemble_fallback_empty") is True
    assert audit.get("ensemble_active") == []


def test_combine_dropped_is_list_of_names():
    """combine_ensemble_scores scrie ensemble_dropped ca listă de NUME (nu dicturi)."""
    audit: dict = {}
    raw = {i: float(i) for i in range(1, 12)}
    ms.combine_ensemble_scores(
        [("methodA", raw, 0.5), ("methodB", dict(raw), 0.5)],
        audit=audit,
    )
    dropped = audit.get("ensemble_dropped") or []
    corr = audit.get("ensemble_dropped_correlated") or []
    assert audit.get("ensemble_active")
    names = list(dropped) + [t[0] for t in corr if t]
    assert names, "un membru identic trebuia sărit"
    assert all(isinstance(n, str) for n in dropped)
