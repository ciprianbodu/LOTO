"""Walk-forward cache key must change when history outside the recent tail changes."""
from __future__ import annotations

import pandas as pd

from loto_enterprise.core.walk_forward_adapter import CACHE_VERSION, _csv_hash


def test_csv_hash_changes_when_old_rows_change():
    cols = ["n1", "n2", "n3", "n4", "n5", "n6"]
    rows = [[i, i + 1, i + 2, i + 3, i + 4, i + 5] for i in range(600)]
    df1 = pd.DataFrame(rows, columns=cols)
    rows2 = list(rows)
    rows2[0] = [9, 9, 9, 9, 9, 9]  # în afara ultimelor 500
    df2 = pd.DataFrame(rows2, columns=cols)
    assert _csv_hash(df1, "6/49") != _csv_hash(df2, "6/49")


def test_csv_hash_stable_on_identical_history():
    cols = ["n1", "n2", "n3", "n4", "n5", "n6"]
    rows = [[i, i + 1, i + 2, i + 3, i + 4, i + 5] for i in range(80)]
    df_a = pd.DataFrame(rows, columns=cols)
    df_b = pd.DataFrame(rows, columns=cols)
    assert _csv_hash(df_a, "6/49") == _csv_hash(df_b, "6/49")


def test_cache_version_tracks_covering_design_signature(monkeypatch, tmp_path):
    import wheeling_methods as wm
    from loto_enterprise.core.walk_forward_adapter import _wheel_sig

    design = tmp_path / "C_12_6_4.txt"
    design.write_text("1 2 3 4 5 6\n", encoding="utf-8")
    monkeypatch.setattr(wm, "_LAJOLLA_DIRS", [tmp_path])
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)

    before = _wheel_sig(12, "6/49")
    design.write_text("1 2 3 4 5 7\n", encoding="utf-8")
    after = _wheel_sig(12, "6/49")

    assert CACHE_VERSION == "v22"
    assert before != after


def test_joker_wf_signature_includes_urna2_decision(monkeypatch):
    """Schimbarea bilei Joker trebuie să invalideze WF-ul, nu doar Urna 1."""
    import loto_enterprise.core.method_selector as selector
    import loto_enterprise.core.walk_forward_adapter as wf

    decisions = {
        "joker_urna1": {"scorer": "frequency", "sim_depth_pct": 40,
                         "ensemble": [{"method": "frequency", "weight": 1.0}]},
        "joker_urna2": {"scorer": "frequency", "hit_target": 1,
                         "ensemble": [{"method": "frequency", "weight": 1.0}]},
    }
    monkeypatch.setattr(selector, "recommend_optimal_config", lambda key, _pool: decisions[key])
    monkeypatch.setattr(wf, "_wheel_sig", lambda *_args: "wheel")

    before = wf._decision_sig("joker", 10)
    decisions["joker_urna2"] = {
        "scorer": "autocorr", "hit_target": 1,
        "ensemble": [{"method": "autocorr", "weight": 1.0}],
    }
    after = wf._decision_sig("joker", 10)

    assert before != after
