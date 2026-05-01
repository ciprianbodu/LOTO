"""
Test pentru modulul `loto_enterprise.core.adaptive_feedback`.

Acoperă:
  • Clasificarea evenimentelor (normal / underperf / catastrophe)
  • Magnitudini diferențiate per eveniment
  • Streak detector pentru catastrofe consecutive
  • Regime mismatch via fereastră rolling sub baseline
  • Persistența stării (load/save round-trip)
  • Curățarea automată a multiplicatorilor revenuți la ~1.0
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loto_enterprise.core import adaptive_feedback as af


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirecționăm fișierul de stare către un tmp_path izolat."""
    fake = tmp_path / "adaptive_state.json"
    monkeypatch.setattr(af, "_STATE_FILE", fake)
    yield fake


# ---------------------------------------------------------------------------
# classify_event
# ---------------------------------------------------------------------------

def test_classify_zero_hits_is_catastrophe():
    assert af.classify_event(0) == "catastrophe"


def test_classify_one_hit_is_underperf():
    assert af.classify_event(1) == "underperf"


@pytest.mark.parametrize("hits", [2, 3, 4, 5, 6])
def test_classify_two_or_more_is_normal(hits):
    assert af.classify_event(hits) == "normal"


# ---------------------------------------------------------------------------
# compute_post_draw_feedback — magnitudini per eveniment
# ---------------------------------------------------------------------------

def test_normal_event_has_small_magnitudes():
    pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    actual = [1, 2, 3, 47, 48, 49]
    new_map, event, info = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual, current_map={},
        history=[], game_type="6/49", pool_size=12,
    )
    assert event == "normal"
    assert info["pool_hits"] == 3
    # missed = {47, 48, 49} (au ieșit, nu erau în pool)
    assert set(info["missed"]) == {47, 48, 49}
    # boost +0.10 pe cele ratate
    for m in info["missed"]:
        # După decay (0.8): 1.0 + (1.10 - 1.0) * 0.8 = 1.08
        assert new_map[m] == pytest.approx(1.08, abs=1e-3)


def test_underperf_event_amplifies_feedback():
    pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    actual = [1, 47, 48, 41, 42, 43]
    new_map, event, info = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual, current_map={},
        history=[], game_type="6/49", pool_size=12,
    )
    assert event == "underperf"
    assert info["pool_hits"] == 1
    # underperf: missed +0.15 → după decay: 1.12
    for m in info["missed"]:
        assert new_map[m] == pytest.approx(1.12, abs=1e-3)


def test_catastrophe_event_has_largest_magnitude():
    pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    actual = [40, 41, 42, 43, 44, 45]  # zero overlap
    new_map, event, info = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual, current_map={},
        history=[], game_type="6/49", pool_size=12,
    )
    assert event == "catastrophe"
    assert info["pool_hits"] == 0
    assert info["streak_zero"] == 1
    # catastrophe: missed +0.30 → după decay: 1.24
    # Dar la prima catastrofă, nu suntem în reset încă (streak=1, nu 2+).
    for m in info["missed"]:
        assert new_map[m] == pytest.approx(1.24, abs=1e-3)


# ---------------------------------------------------------------------------
# Streak detector → regime_reset
# ---------------------------------------------------------------------------

def test_three_consecutive_catastrophes_trigger_regime_reset():
    """Trigger reset cere streak >= 3 (mărit de la 2 pentru a evita varianța naturală)."""
    pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    actual1 = [40, 41, 42, 43, 44, 45]
    actual2 = [30, 31, 32, 33, 34, 35]
    actual3 = [20, 21, 22, 23, 24, 25]

    new_map, ev1, info1 = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual1, current_map={},
        history=[], game_type="6/49", pool_size=12, streak_zero=0,
    )
    assert ev1 == "catastrophe"
    assert info1["active_mode"] == "normal"
    assert info1["streak_zero"] == 1

    new_map2, ev2, info2 = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual2, current_map=new_map,
        history=[{"pool_hits": 0}], game_type="6/49", pool_size=12,
        streak_zero=info1["streak_zero"],
    )
    assert ev2 == "catastrophe"
    assert info2["streak_zero"] == 2
    # Cu noul threshold (>=3), 2 catastrofe NU mai trigger reset.
    # Dar fereastra rolling cu 2 hits=0 (window=5) nu are destule date.
    assert info2["active_mode"] == "normal"

    new_map3, ev3, info3 = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual3, current_map=new_map2,
        history=[{"pool_hits": 0}, {"pool_hits": 0}],
        game_type="6/49", pool_size=12,
        streak_zero=info2["streak_zero"],
    )
    assert ev3 == "catastrophe"
    assert info3["streak_zero"] == 3
    assert info3["active_mode"] == "reset"
    # Reset clamp: toate valorile între [0.7, 1.4]
    for v in new_map3.values():
        assert 0.7 <= v <= 1.4


def test_reset_max_duration_force_exit():
    """După _REGIME_MAX_DURATION extrageri în reset, ieșim automat la normal
    chiar dacă rolling avg e încă sub baseline."""
    pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    # Construim un istoric care a triggerat reset (e.g. 5 extrageri foarte slabe)
    bad_history = [{"pool_hits": 0} for _ in range(5)]
    actual = [20, 21, 22, 23, 24, 25]  # tot 0 hits

    # Suntem deja în reset de 5 extrageri (max duration)
    _, event, info = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual, current_map={1: 1.3},
        history=bad_history, game_type="6/49", pool_size=12,
        streak_zero=5, prev_mode="reset", reset_duration=5,
    )
    # Trebuie să ieșim înapoi la normal forțat
    assert info["active_mode"] == "normal"
    assert info["reset_duration"] == 0


# ---------------------------------------------------------------------------
# Regime mismatch detector
# ---------------------------------------------------------------------------

def test_rolling_severely_underperf_triggers_regime_reset():
    # Baseline 6/49 cu pool 12 = 6 * 12 / 49 ≈ 1.47
    # Cutoff = 1.47 * (1 - 0.25) = 1.10
    # Avg 0.6 e sub cutoff → trigger
    history = [{"pool_hits": h} for h in [1, 1, 0, 1, 0]]
    is_mm, avg = af.detect_regime_mismatch(history, "6/49", 12)
    assert is_mm is True
    assert avg == pytest.approx(0.6, abs=0.01)


def test_rolling_just_under_baseline_no_trigger():
    """Sub baseline simplu (1.47) DAR peste cutoff (1.10) nu mai triggerează —
    asta era exact bug-ul: false positives pe varianță naturală."""
    # Avg = 1.2, sub baseline 1.47 dar peste cutoff 1.10
    history = [{"pool_hits": h} for h in [1, 2, 1, 1, 1]]
    is_mm, avg = af.detect_regime_mismatch(history, "6/49", 12)
    assert avg == pytest.approx(1.2, abs=0.01)
    assert is_mm is False  # NU mai triggerează — varianță naturală acceptabilă


def test_rolling_above_baseline_no_mismatch():
    history = [{"pool_hits": h} for h in [2, 3, 1, 2, 4]]
    is_mm, avg = af.detect_regime_mismatch(history, "6/49", 12)
    assert is_mm is False
    assert avg == pytest.approx(2.4, abs=0.01)


def test_short_history_no_mismatch():
    history = [{"pool_hits": 0}, {"pool_hits": 0}]
    is_mm, _ = af.detect_regime_mismatch(history, "6/49", 12, window=5)
    assert is_mm is False


# ---------------------------------------------------------------------------
# Persistență
# ---------------------------------------------------------------------------

def test_temp_blacklist_no_op_outside_catastrophe():
    """Temp blacklist NU se generează decât după catastrofă."""
    pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert af.compute_temp_blacklist(pool, "normal", 49, 12) == set()
    assert af.compute_temp_blacklist(pool, "underperf", 49, 12) == set()
    assert af.compute_temp_blacklist(pool, None, 49, 12) == set()


def test_temp_blacklist_full_inversion_after_catastrophe():
    """După catastrofă cu spațiu suficient, excludem TOT pool-ul ratat."""
    pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    # 6/49 cu pool 12: spațiu disponibil după excludere = 49-12 = 37 >= 12 → full
    bl = af.compute_temp_blacklist(pool, "catastrophe", 49, 12, enable_full_inversion=True)
    assert bl == set(pool)


def test_temp_blacklist_partial_when_insufficient_space():
    """Dacă spațiul complementar e prea mic, fallback la partial_k."""
    # Pool de 30 dintr-un univers de 40 → 40-30 = 10 < 30 → fallback
    pool = list(range(1, 31))
    bl = af.compute_temp_blacklist(
        pool, "catastrophe", universe_size=40, pool_size=30,
        enable_full_inversion=True, partial_k=4,
    )
    assert len(bl) == 4
    assert bl == {1, 2, 3, 4}


def test_temp_blacklist_partial_explicit():
    pool = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
    bl = af.compute_temp_blacklist(
        pool, "catastrophe", 49, 12,
        enable_full_inversion=False, partial_k=3,
    )
    assert len(bl) == 3
    assert bl == {10, 11, 12}


def test_temp_blacklist_empty_pool():
    assert af.compute_temp_blacklist([], "catastrophe", 49, 12) == set()


def test_save_load_round_trip(isolated_state):
    entry = {
        "last_pool": [1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 47, 49],
        "last_pool_date": "2026-05-01T10:00:00",
        "last_data_rows": 2137,
        "history": [{"date": "2026-04-30", "pool_hits": 2, "actual": [1, 5, 9, 33, 41, 48], "event": "normal"}],
        "error_correction_map": {7: 1.25, 11: 0.85},
        "regime_state": {
            "streak_zero": 0, "rolling_avg": 1.8, "last_reset": None,
            "active_mode": "normal", "reset_duration": 0,
        },
    }
    af.save_adaptive_state("6/49", 12, entry)

    loaded = af.load_adaptive_state("6/49", 12)
    assert loaded["last_pool"] == entry["last_pool"]
    assert loaded["last_data_rows"] == 2137
    assert loaded["error_correction_map"] == {7: 1.25, 11: 0.85}
    assert loaded["regime_state"]["active_mode"] == "normal"
    assert loaded["regime_state"]["reset_duration"] == 0
    assert isolated_state.exists()


def test_record_predicted_pool_persists(isolated_state):
    af.record_predicted_pool("6/49", 12, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], data_rows=100)
    raw = json.loads(isolated_state.read_text(encoding="utf-8"))
    assert "6/49_12" in raw
    assert raw["6/49_12"]["last_data_rows"] == 100
    assert len(raw["6/49_12"]["last_pool"]) == 12


def test_load_state_missing_file_returns_empty(isolated_state):
    # isolated_state nu există încă
    assert not isolated_state.exists()
    state = af.load_adaptive_state("6/49", 12)
    assert state["last_pool"] == []
    assert state["error_correction_map"] == {}


# ---------------------------------------------------------------------------
# Cleanup multiplicatori la ~1.0
# ---------------------------------------------------------------------------

def test_neutral_multipliers_are_pruned():
    # Map cu valori aproape de 1.0 → după decay devin ~1.0 → eliminate
    initial_map = {1: 1.001, 2: 1.5, 3: 0.998}
    pool = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
    actual = [10, 11, 22, 23, 24, 25]  # pool_hits=2 → normal

    new_map, _, _ = af.compute_post_draw_feedback(
        last_pool=pool, actual_draw=actual, current_map=initial_map,
        history=[], game_type="6/49", pool_size=12,
    )
    # Cele cu abs(v-1.0) <= 0.005 ar trebui curățate
    assert 1 not in new_map
    assert 3 not in new_map
    # 2 era 1.5 → după decay: 1.0 + 0.5 * 0.8 = 1.4 → rămâne
    assert 2 in new_map
    assert new_map[2] == pytest.approx(1.4, abs=1e-3)


# ---------------------------------------------------------------------------
# Baseline sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("game,pool,expected", [
    ("6/49", 12, 6 * 12 / 49),
    ("6/49", 15, 6 * 15 / 49),
    ("5/40", 12, 5 * 12 / 40),
    ("joker", 12, 5 * 12 / 45),
])
def test_baseline_random_hits(game, pool, expected):
    assert af._baseline_random_hits(game, pool) == pytest.approx(expected, abs=1e-6)
