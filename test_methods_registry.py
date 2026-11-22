"""Regression tests for the compact catalog and archived decision names."""

import builtins
import logging
from pathlib import Path

import pandas as pd
import pytest

from loto_enterprise.benchmark import methods, methods_compact
from loto_enterprise.benchmark.decision import (
    EXCLUDED_FROM_PRODUCTION,
    EXCLUDED_FROM_SINGLE_PICK,
    KNOWN_GAME_MAX_NUM,
    decide_optimal_config_for_pool,
)
from loto_enterprise.core import method_selector


def test_registry_contains_only_compact_catalog():
    expected = {
        "random",
        "frequency",
        "compact_multiscale_shrink",
        "compact_transition_prior",
        "compact_renewal_hazard",
        "markov_lag2",
    }
    assert set(methods.METHODS) == expected
    assert set(methods.list_methods()) == expected
    methods.require_complete_catalog(country="RO")
    methods.require_complete_catalog(country="DE")


def test_retired_method_in_saved_decision_triggers_fallback(caplog):
    for retired in ("alternating_parity", "season_period2", "interval_extrema_k16"):
        assert methods.resolve_method_name(retired) == retired
        assert not methods.method_meta(retired)["available"]
        assert method_selector._sanitize_production_name(retired, context="test") is None
    assert "alternating_parity" in caplog.text


def test_production_exclusions_and_markov_geography():
    assert EXCLUDED_FROM_PRODUCTION == frozenset({"random"})
    assert EXCLUDED_FROM_SINGLE_PICK == frozenset()
    for game in ("loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2"):
        assert methods.method_applies_to_game("markov_lag2", game)
        assert methods.catalog_version_for_game(game) == "compact-v2"
    assert not methods.method_applies_to_game("markov_lag2", "de_lotto")
    assert methods.catalog_version_for_game("de_lotto") == "compact-v1"


def test_folds_decision_picks_only_alive_methods():
    folds = Path("bench_results/folds.csv")
    if not folds.exists():
        pytest.skip("folds.csv absent")
    frame = pd.read_csv(folds)
    for game, pool, draw in (
        ("loto_6_49", 12, 6),
        ("loto_5_40", 11, 6),
        ("joker_urna1", 10, 5),
        ("joker_urna2", 1, 1),
    ):
        cfg = decide_optimal_config_for_pool(
            frame, game, pool, draw, max_num=KNOWN_GAME_MAX_NUM[game]
        )
        assert cfg["scorer"] in methods.METHODS


def test_extension_name_collision_is_logged_without_overwriting(monkeypatch, caplog):
    monkeypatch.setattr(methods, "METHODS", methods.METHODS.copy())
    monkeypatch.setattr(methods, "METHOD_LOAD_ERRORS", {})
    monkeypatch.setitem(
        methods_compact.COMPACT_METHODS,
        "random",
        methods_compact.COMPACT_METHODS["compact_multiscale_shrink"],
    )
    methods._load_extra_methods()
    assert methods.METHODS["random"] == methods.BASELINES["random"]
    assert any(
        record.levelno >= logging.ERROR
        and "duplicat" in record.getMessage()
        and "random" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize("module", ["methods_compact", "methods_markov"])
def test_import_failure_is_recorded_and_required_catalog_detects_it(
    monkeypatch, caplog, module
):
    monkeypatch.setattr(methods, "METHODS", methods.METHODS.copy())
    monkeypatch.setattr(methods, "METHOD_LOAD_ERRORS", {})
    real_import = builtins.__import__

    def fail_import(name, *args, **kwargs):
        if name == module:
            raise ModuleNotFoundError("dependency missing")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", fail_import)
        methods._load_extra_methods()
        assert set(methods.BASELINES) <= set(methods.METHODS)
        assert "dependency missing" in methods.METHOD_LOAD_ERRORS[module]
        assert any(
            record.levelno >= logging.ERROR and module in record.getMessage()
            for record in caplog.records
        )
        with pytest.raises(RuntimeError, match=module):
            methods.require_complete_catalog(country="RO")
        if module == "methods_markov":
            methods.require_complete_catalog(country="DE")
        else:
            with pytest.raises(RuntimeError, match=module):
                methods.require_complete_catalog(country="DE")

    methods._load_extra_methods()
    methods.require_complete_catalog(country="RO")
    assert methods.METHOD_LOAD_ERRORS == {}


def test_saved_method_fallback_reports_a_module_load_error(monkeypatch, caplog):
    monkeypatch.setattr(
        methods, "METHOD_LOAD_ERRORS", {"methods_compact": "dependency missing"}
    )
    assert method_selector._sanitize_production_name(
        "compact_multiscale_shrink_removed", context="test"
    ) is None
    assert "methods_compact" in caplog.text
    assert "dependency missing" in caplog.text
