"""WF evaluează configurația generată, inclusiv designul condițional și factor 0."""
from itertools import combinations

import numpy as np
import pandas as pd
import pytest

import app_nicegui as app
from loto_engine import LotoEngine
from loto_enterprise.core import backtesting as bt
from loto_enterprise.core import walk_forward_adapter as wf
from scripts.analysis.audit_patterns_and_designs import holm, target_matrix
from wheeling_methods import compute_coverage_pct


def test_result_settings_override_current_ui_and_preserve_zero(monkeypatch):
    monkeypatch.setitem(app.SETTINGS, "recent_penalty_factor_val", .8)
    monkeypatch.setitem(app.SETTINGS, "guarantee_val", 6)
    result = {"guarantee": 6, "max_variants": 2, "recent_penalty_draws": 3,
              "recent_penalty_factor": 0., "wheel_condition": 6,
              "audit": {"wheel_guarantee_used": 3, "wheel_condition_used": 4}}
    assert app._wf_generation_options(result) == {
        "guarantee": 3, "wheel_condition": 4, "max_variants": 2,
        "recent_penalty_draws": 3, "recent_penalty_factor": 0.,
    }
    assert app._wf_generation_options({})["recent_penalty_factor"] == .5
    # Workerul păstrează cap-ul în context, nu în câmpurile top-level.
    assert app._wf_generation_options({"context": {"max_variants": 7}})["max_variants"] == 7


def test_cache_separates_all_generation_settings_and_lotto_file(tmp_path, monkeypatch):
    import wheeling_methods as wm

    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    monkeypatch.setattr(wm, "_LAJOLLA_DIRS", [tmp_path])
    variants = [(3, 3, 0), (3, 4, 0), (3, 4, 2), (4, 4, 0)]
    assert len({wf._decision_sig("6/49", 10, 100., 3, 0., *cfg) for cfg in variants}) == 4
    assert wf._penalty_sig(3, .5001) != wf._penalty_sig(3, .5002)
    before = wf._wheel_sig(10, "6/49", 3, 4, 2)
    (tmp_path / "L_10_6_4_3.txt").write_text("1 2 3 4 5 6\n", encoding="utf-8")
    after = wf._wheel_sig(10, "6/49", 3, 4, 2)
    assert before != after
    assert wf._wheel_sig(10, "6/49", 3, 3, 2).startswith("greedy|")
    assert wf._wheel_sig(10, "6/49", 3, 4, 2).startswith("lotto|")
    # Cererea de sistem complet rămâne explicită; API-ul vechi rămâne plafonat.
    assert wf._wf_geometry(16, "5/40", 5) == (5, 5, 0)
    assert wf._wf_geometry(16, "5/40") == (4, 4, 0)


def test_cache_fallback_still_separates_lookback(monkeypatch):
    from loto_enterprise.core import method_selector

    def fail(*args):
        raise RuntimeError("decizie indisponibilă")
    monkeypatch.setattr(method_selector, "recommend_optimal_config", fail)
    assert wf._decision_sig("6/49", 10, 30.) != wf._decision_sig("6/49", 10, 100.)


@pytest.mark.parametrize("depth", [5., 10.])
def test_wf_tickets_equal_direct_generation_with_conditional_cap(tmp_path, monkeypatch, depth):
    """O extragere = ramura serială; două = packing-ul ramurii stateless."""
    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(20).reset_index(drop=True)
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 1)
    monkeypatch.setattr(LotoEngine, "_get_timesfm_scores",
                        lambda self, **kw: {n: float(n) for n in range(1, 50)})
    opts = {"pool_size": 10, "guarantee": 3, "wheel_condition": 4,
            "max_variants": 2, "recent_penalty_draws": 3, "recent_penalty_factor": 0.}
    flat, meta = wf.run_honest_walk_forward(df, "6/49", backtest_depth_percent=depth,
                                           use_cache=False, **opts)
    assert meta["wheel_guarantee"] == 3 and meta["wheel_condition"] == 4
    assert meta["max_variants"] == 2 and not meta["partial"]
    assert len({r.draw_index for r in flat}) == int(len(df) * depth / 100)
    for index in {r.draw_index for r in flat}:
        engine = LotoEngine("6/49")
        engine.data = df.iloc[:index].copy()
        engine._build_draw_matrix()
        lines, *_, context, _ = engine.run_institutional_pipeline(track_pool_variation=False, **opts)
        rows = [r for r in flat if r.draw_index == index]
        assert [r.variant for r in rows] == lines
        assert len(lines) == 2
        actual = set(df.iloc[index][[f"n{i}" for i in range(1, 7)]])
        assert [r.hits for r in rows] == [len(set(line) & actual) for line in lines]
        assert rows[0].wheel_coverage == context["coverage_pct"]
        assert compute_coverage_pct(lines, engine.hard_core, 3, condition=4) == context["coverage_pct"]
        monkeypatch.setattr(bt, "_WF_SHARED", {
            "df": df, "draws": df[[f"n{i}" for i in range(1, 7)]].values.tolist(),
            "dates": df.date.tolist(), "game_type": "6/49",
        })
        dispatched = bt._wf_worker_step((index, 10, 3, 2, 100., False, False, 3, 0., 4))
        assert dispatched.variants == lines
    # Cache-hit-ul aceleiași configurații păstrează geometria și variantele.
    cached, cached_meta = wf.run_honest_walk_forward(df, "6/49", backtest_depth_percent=depth, **opts)
    assert cached_meta["from_cache"]
    assert cached == flat and cached_meta["max_variants"] == 2


def test_ui_describes_the_evaluated_conditional_budget():
    from types import SimpleNamespace
    from scripts.analysis.audit_output import capture_ui

    flat = [SimpleNamespace(draw_index=1, draw_date="03-09-2026", hits_union=3,
                            hits=2, wheel_coverage=100.)]
    with capture_ui() as ui:
        app._render_hits_4plus(flat, "6/49", {"wheel_guarantee": 3,
                              "wheel_condition": 4, "max_variants": 2})
    text = ui.text()
    assert "plafon 2 variante" in text and "numai de la 4" in text
    assert "Setările wheel-ului generat pot fi diferite" not in text


def test_pattern_audit_coverage_matches_exhaustive_intersections():
    blocks = [[1, 2, 3, 4, 5], [3, 4, 5, 6, 7], [1, 2, 3, 6, 7]]
    matrix = target_matrix(blocks, 7, 4, 3)
    expected = [[len(set(b) & set(t)) >= 3 for t in combinations(range(1, 8), 4)] for b in blocks]
    assert np.array_equal(matrix, expected)
    # Unele triplete rămân neacoperite chiar când condiția 4 e acoperită complet.
    assert matrix.any(axis=0).all()
    assert not target_matrix(blocks, 7, 3, 3).any(axis=0).all()
    assert holm({"a": .01, "b": .03, "c": .2}) == pytest.approx({"a": .03, "b": .06, "c": .2})
