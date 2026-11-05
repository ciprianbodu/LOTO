"""The consecutive diagnostic describes the displayed result, not live settings."""
from copy import deepcopy

import pytest

import app_nicegui as app
from scripts.analysis.audit_output import capture_ui


def _result():
    return {
        "hard_core": [2, 3, 5, 6, 9, 12],
        "pool_size": 6,
        "guarantee": 4,
        "max_consecutive_run": 2,
        "variants": [],
        "audit": {
            "pipeline_stages": {"1_nqi_raw": [2, 3, 4, 5, 6, 12]},
            # Deliberately incorrect cached measurement: the pool is the source.
            "pool_longest_consecutive_run": 99,
            "consecutive_limit": {"requested": 2, "applied": 2},
        },
    }


def test_diagnostic_uses_actual_pool_and_raw_stage_without_mutation(monkeypatch):
    data = _result()
    original = deepcopy(data)
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", False)
    text = app._consecutive_pool_diagnostic(data)
    assert "pool-ul jucat: 2 numere" in text
    assert "selecția inițială după scor: 5 numere" in text
    assert "variantele sistemului folosesc pool-ul jucat" in text
    assert "oprită" not in text
    assert "99" not in text
    assert data == original
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", True)
    assert app._consecutive_pool_diagnostic(data) == text


def test_explicit_off_result_is_not_relabelled_by_current_checkbox(monkeypatch):
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", True)
    data = _result()
    data["max_consecutive_run"] = 0
    data["hard_core"] = [2, 3, 4, 5, 6, 12]
    text = app._consecutive_pool_diagnostic(data)
    assert "pool-ul jucat: 5 numere" in text
    assert "era oprită la generarea acestui rezultat" in text
    # Explicit worker echo takes precedence over legacy audit metadata.
    assert data["audit"]["consecutive_limit"]["requested"] == 2


def test_audit_off_is_used_when_worker_echo_is_absent():
    data = _result()
    data.pop("max_consecutive_run")
    data["audit"]["consecutive_limit"]["requested"] = 0
    assert "era oprită" in app._consecutive_pool_diagnostic(data)


@pytest.mark.parametrize("raw", [None, [], "2,3,4", ["invalid"]])
def test_legacy_or_invalid_raw_selection_is_not_invented_from_scores(raw):
    data = {
        "hard_core": [2, 3, 8, 11, 17, 22],
        "audit": {
            "timesfm_predictions": {n: 0.5 for n in range(1, 26)},
            "pipeline_stages": {"1_nqi_raw": raw},
        },
    }
    text = app._consecutive_pool_diagnostic(data)
    assert "pool-ul jucat: 2 numere" in text
    assert "inițială" not in text
    assert "oprită" not in text  # Absence is not proof of an explicit setting.


def test_unknown_legacy_result_reports_only_observed_pool():
    assert app._consecutive_pool_diagnostic({"hard_core": [2, 4, 6]}) == (
        "Secvența consecutivă maximă în pool-ul jucat: 1 număr."
    )


@pytest.mark.parametrize("pool", [None, [], "2,3,4", ["invalid"]])
def test_absent_or_invalid_pool_does_not_claim_a_run(pool):
    assert app._consecutive_pool_diagnostic({"hard_core": pool}) == ""


def test_relaxed_result_reports_real_run_and_existing_explanation():
    data = _result()
    data["hard_core"] = list(range(1, 7))
    data["audit"]["consecutive_limit"].update(applied=6, relaxed=True)
    assert "pool-ul jucat: 6 numere" in app._consecutive_pool_diagnostic(data)
    explanation = app._consecutive_limit_text(data["audit"])
    assert "nu a încăput în baza restrânsă" in explanation
    assert "până la 6 consecutive" in explanation


def test_panel_and_report_show_the_same_diagnostic(monkeypatch):
    data = _result()
    expected = app._consecutive_pool_diagnostic(data)
    monkeypatch.setitem(app.STATE, "results", ([("x.csv", {"6/49": data})], 1))
    monkeypatch.setitem(app.STATE, "retro", {})
    monkeypatch.setitem(app.STATE, "retro_meta", {})
    with capture_ui() as captured:
        app._render_pool_body("x.csv", "6/49", data)
    assert expected in captured.text()
    assert expected in app._build_report()
