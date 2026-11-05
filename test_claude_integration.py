"""Contracts for the experimental Claude wheel APIs and existing public facade."""

import pytest

import covering.dispatch as dispatch
import wheeling_methods as facade
from covering.multihit import multi_hit_stats
from loto_enterprise.core.walk_forward_adapter import _wheel_sig


def test_every_public_wheel_export_resolves():
    assert all(hasattr(facade, name) for name in facade.__all__)
    from covering.ilp import wheel_ilp

    assert facade.wheel_ilp is wheel_ilp
    assert facade._ILP_COVER_CACHE is not None
    assert callable(facade._ilp_cover_positions)


@pytest.mark.parametrize("cap,expected", [(0, "lajolla"), (7, "hitcover")])
def test_experiments_do_not_change_automatic_choice(monkeypatch, cap, expected):
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    assert dispatch.resolve_wheel_method(cap) == expected


def test_multihit_dispatch_preserves_double_cover(monkeypatch):
    def no_generic_refinement(*args, **kwargs):
        pytest.fail("one-cover refinement cannot certify multiplicity")

    monkeypatch.setattr(dispatch, "improve_hit_profile", no_generic_refinement)
    monkeypatch.setattr(dispatch, "improve_higher_hits", no_generic_refinement)
    pool = list(range(1, 9))
    wheel, coverage = dispatch.generate_wheel(
        "multihit", pool, 5, 3, draw_n=6, max_num=40
    )
    assert coverage == 100.0
    assert multi_hit_stats(wheel, pool, 3)["min_covers"] >= 2


def test_weighted_signature_tracks_uncapped_design(monkeypatch):
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "weighted")
    monkeypatch.setattr(facade, "covering_design_source_signature", lambda *a: "before")
    before = _wheel_sig(10, "6/49", 4)
    capped_before = _wheel_sig(10, "6/49", 4, max_variants=5)
    monkeypatch.setattr(facade, "covering_design_source_signature", lambda *a: "after")
    assert _wheel_sig(10, "6/49", 4) != before
    assert _wheel_sig(10, "6/49", 4, max_variants=5) == capped_before
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "multihit")
    assert _wheel_sig(10, "6/49", 4) != before
    assert "lambda2" in _wheel_sig(10, "6/49", 4)


@pytest.mark.parametrize("method", ["weighted", "multihit"])
def test_experimental_dispatch_accepts_invalid_scores(method):
    pool = list(range(1, 10))
    scores = {n: float(n) for n in pool}
    scores.update({1: None, 2: "bad", 3: float("nan"), 4: float("inf")})
    wheel, coverage = dispatch.generate_wheel(
        method, pool, 5, 3, 6, scores, draw_n=5, max_num=45
    )
    assert wheel and len(wheel) <= 6
    assert coverage == facade.compute_coverage_pct(wheel, pool, 3)


@pytest.mark.parametrize("urn", ["loto_6_49", "joker_urna2"])
def test_dmd_semantics_change_only_affected_wf_decisions(monkeypatch, urn):
    import loto_enterprise.core.walk_forward_adapter as adapter
    import loto_enterprise.core.method_selector as selector

    scorer = "frequency"

    def recommendation(game, pool, **kwargs):
        return {"scorer": scorer if game == urn else "frequency", "sim_depth_pct": 30}

    monkeypatch.setattr(selector, "recommend_optimal_config", recommendation)
    game_type = "joker" if urn == "joker_urna2" else "6/49"
    current = adapter._scorer_semantics
    unaffected = adapter._decision_sig(game_type, 10)
    monkeypatch.setattr(adapter, "_scorer_semantics", lambda cfg: "")
    assert adapter._decision_sig(game_type, 10) == unaffected
    scorer = "dmd_forecast"
    old = adapter._decision_sig(game_type, 10)
    monkeypatch.setattr(adapter, "_scorer_semantics", current)
    assert adapter._decision_sig(game_type, 10) != old


@pytest.mark.parametrize("ensemble", [[{"method": "dmd_forecast", "weight": 1}], {"dmd_forecast": 1}])
def test_dmd_ensemble_members_change_semantics(ensemble):
    from loto_enterprise.core.wf_sig import scorer_semantics

    assert scorer_semantics({"scorer": "frequency", "ensemble": ensemble})
    assert not scorer_semantics({"scorer": "frequency"})
