"""Lotto design „t dacă p" și penalizarea după ultimele extrageri."""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
import pytest

from loto_engine import LotoEngine
from wheeling_methods import (
    compute_coverage_pct, generate_wheel, lotto_coverage_pct, wheel_lotto,
)


def _lotto_ok(wheel, pool, g, c) -> bool:
    """Verificare independentă: orice c-submulțime are ≥ g numere pe un bilet."""
    tickets = [set(t) for t in wheel]
    return all(any(len(tk & set(s)) >= g for tk in tickets) for s in combinations(pool, c))


@pytest.mark.parametrize("K,pick,g,c", [(11, 5, 3, 4), (11, 5, 3, 5), (9, 5, 4, 5), (12, 6, 3, 4)])
def test_wheel_lotto_covers_all_condition_subsets(K, pick, g, c):
    pool = list(range(1, K + 1))
    wheel, cov = wheel_lotto(pool, pick, g, c)
    assert cov == 100.0
    assert _lotto_ok(wheel, pool, g, c)
    assert all(len(t) == pick and len(set(t)) == pick and set(t) <= set(pool) for t in wheel)
    # mult mai ieftin decât coverul clasic „g dacă g"
    classic, _ = generate_wheel("lajolla", pool, pick, g, 0, None)
    assert len(wheel) < len(classic)


def test_condition_equal_to_guarantee_is_classic_cover():
    pool = list(range(1, 12))
    a, ca = generate_wheel("lajolla", pool, 5, 3, 0, None)
    b, cb = generate_wheel("lajolla", pool, 5, 3, 0, None, condition=3)
    assert a == b and ca == cb == 100.0
    assert compute_coverage_pct(a, pool, 3, condition=3) == compute_coverage_pct(a, pool, 3)


def test_lotto_coverage_semantics_and_budget_path():
    pool = list(range(1, 12))
    with pytest.raises(ValueError):
        generate_wheel("lajolla", pool, 5, 4, 0, None, condition=3)
    wheel, cov = generate_wheel("lajolla", pool, 5, 3, 4, None, condition=4)
    assert len(wheel) <= 4
    assert cov < 100.0
    assert cov == lotto_coverage_pct(wheel, pool, 3, 4)
    assert set(pool) <= {n for t in wheel for n in t} or len(wheel) * 5 < len(pool)


def _df(n: int, draw_n: int, max_n: int, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        nums = sorted(rng.choice(np.arange(1, max_n + 1), size=draw_n, replace=False).tolist())
        row = {"date": f"2026-01-{(i % 28) + 1:02d}"}
        row.update({f"n{j + 1}": v for j, v in enumerate(nums)})
        rows.append(row)
    return pd.DataFrame(rows)


def test_apply_recent_penalty_multiplies_by_factor_per_appearance():
    draws = np.array([[1, 2, 3, 4, 5, 6], [1, 7, 8, 9, 10, 11], [1, 2, 12, 13, 14, 15]])
    scores = {n: 1.0 for n in range(1, 50)}
    out, pen = LotoEngine.apply_recent_penalty(scores, draws, 2, 0.5, 49)
    assert out[1] == 0.25 and out[2] == 0.5 and out[7] == 0.5 and out[3] == 1.0 and out[49] == 1.0
    assert pen == {1: 2, 2: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1}
    same, none = LotoEngine.apply_recent_penalty(scores, draws, 0, 0.5, 49)
    assert same == scores and none == {}


def test_pipeline_penalty_changes_pool_and_is_audited():
    eng = LotoEngine("6/49")
    eng.data = _df(120, 6, 49)
    eng._build_draw_matrix()
    base_lines, *_, base_ctx, base_audit = eng.run_institutional_pipeline(
        pool_size=10, guarantee=3, max_variants=0, track_pool_variation=False)
    base_pool = list(eng.hard_core)
    assert base_audit["recent_penalty"]["draws"] == 0

    eng2 = LotoEngine("6/49")
    eng2.data = _df(120, 6, 49)
    eng2._build_draw_matrix()
    lines, *_, ctx, audit = eng2.run_institutional_pipeline(
        pool_size=10, guarantee=3, max_variants=0, track_pool_variation=False,
        recent_penalty_draws=3, recent_penalty_factor=0.0)
    recent = {int(v) for row in eng2._draw_matrix[-3:] for v in row}
    assert audit["recent_penalty"]["draws"] == 3
    assert set(audit["recent_penalty"]["penalized"]) == recent
    # factor 0: numerele din ultimele 3 extrageri ies din pool
    assert not (set(eng2.hard_core) & recent)
    assert ctx["recent_penalty"] == {"draws": 3, "factor": 0.0}


def test_pipeline_wheel_condition_uses_lotto_design_and_audits():
    eng = LotoEngine("5/40")
    eng.data = _df(100, 5, 40)
    eng._build_draw_matrix()
    lines, *_, ctx, audit = eng.run_institutional_pipeline(
        pool_size=11, guarantee=3, max_variants=0, track_pool_variation=False, wheel_condition=4)
    assert audit["wheel_guarantee_used"] == 3 and audit["wheel_condition_used"] == 4
    assert ctx["wheel_condition"] == 4 and ctx["coverage_pct"] == 100.0
    assert len(lines) < 20 and _lotto_ok(lines, eng.hard_core, 3, 4)
    # condiție invalidă → clamp la [garanție, draw_n]
    eng3 = LotoEngine("5/40")
    eng3.data = _df(100, 5, 40)
    eng3._build_draw_matrix()
    _, *_, ctx3, audit3 = eng3.run_institutional_pipeline(
        pool_size=11, guarantee=3, max_variants=0, track_pool_variation=False, wheel_condition=9)
    assert audit3["wheel_condition_used"] == 5


def test_wf_cache_signature_changes_only_when_penalty_active():
    from loto_enterprise.core import walk_forward_adapter as wfa
    assert wfa._penalty_sig(0, 0.5) == ""
    assert wfa._penalty_sig(3, 0.5) != wfa._penalty_sig(3, 0.25) != ""
    off = wfa._decision_sig("6/49", 10, 100.0)
    assert wfa._decision_sig("6/49", 10, 100.0, 0, 0.5) == off
    assert wfa._decision_sig("6/49", 10, 100.0, 3, 0.5) != off


def test_ui_task_and_worker_carry_the_new_keys():
    ui_src = open("app_nicegui.py", encoding="utf-8").read()
    for key in ('"wheel_condition"', '"recent_penalty_draws"', '"recent_penalty_factor"'):
        assert key in ui_src
    w_src = open("worker.py", encoding="utf-8").read()
    for key in ("wheel_condition=wheel_cond", "recent_penalty_draws=rp_draws", '"recent_penalty_factor": rp_factor'):
        assert key in w_src


def test_all_local_lotto_designs_are_valid_and_complete():
    """Fiecare L_v_pick_p_t.txt din covering_designs/ se încarcă și acoperă 100%."""
    from pathlib import Path
    from wheeling_methods import _load_lotto_design

    files = sorted(Path("covering_designs").glob("L_*_*_*_*.txt"))
    assert files, "niciun lotto design local"
    for f in files:
        v, pick, c, g = (int(x) for x in f.stem.split("_")[1:])
        blocks = _load_lotto_design(v, pick, g, c)
        assert blocks is not None, f.name
        assert _lotto_ok([list(b) for b in blocks], list(range(v)), g, c), f.name


def test_csv_last_date_reads_the_last_row_and_tolerates_missing_column():
    import app_nicegui as ui_mod

    df = pd.DataFrame({
        "date": ["20-08-2026", "23-08-2026", "30-08-2026"],
        "n1": [1, 2, 3], "n2": [4, 5, 6],
    })
    assert ui_mod._csv_last_date(df) == "30-08-2026"
    assert ui_mod._csv_last_date(df.drop(columns=["date"])) == ""
    assert ui_mod._csv_last_date(df.iloc[0:0]) == ""
    assert ui_mod._csv_last_date(None) == ""
    nan_df = df.copy()
    nan_df.loc[2, "date"] = np.nan
    assert ui_mod._csv_last_date(nan_df) == ""


def test_score_time_shows_milliseconds_under_a_tenth_of_a_second():
    import app_nicegui as ui_mod

    assert ui_mod._fmt_score_time(4.2) == "4ms"
    assert ui_mod._fmt_score_time(99) == "99ms"
    assert ui_mod._fmt_score_time(2500) == "2.5s"
    assert ui_mod._fmt_score_time(None) == "?"
