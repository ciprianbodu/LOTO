"""Isolated functional tests: frozen source, exact odds, targeted submit and UI."""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import io
from itertools import combinations
import json
import math
from pathlib import Path
import threading

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
from loto_enterprise.core import lotteries as LOT


class CaptureUI:
    def __init__(self):
        self.nodes = []
        self.events = []

    def __getattr__(self, kind):
        def make(*args, **kwargs):
            self.nodes.append({"kind": kind, "args": args, "kwargs": kwargs})
            owner = self

            class Element:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def classes(self, *args):
                    return self

                def props(self, *args):
                    return self

                def open(self):
                    owner.events.append("open")

                def close(self):
                    owner.events.append("close")

            return Element()
        return make

    def text(self):
        return "\n".join(str(node["args"][0]) for node in self.nodes if node["args"])


def functions(path, names):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    frame = pd.DataFrame({"date": ["12-11-2026", "08-11-2026"], **{f"n{i}": [i, i + 6] for i in range(1, 7)}})
    sources = {"original540.csv": frame}
    state = {"datasets": [], "active_job_id": None, "results": ("old", 1), "retro": {}, "retro_meta": {}}
    settings = {"pool_size_540_val": 19, "max_variants_val": 1000, "guarantee_val": 3, "country_val": "DE"}
    calls = []
    ui = CaptureUI()

    def submit(task_type, config):
        calls.append((task_type, json.loads(config)))
        return 42

    env = {"ui": ui, "math": math, "json": json, "pd": pd, "io": io,
           "hashlib": hashlib, "STATE": state, "SETTINGS": settings,
           "STATE_LOCK": threading.RLock(), "time": __import__("time"), "_LOT": LOT,
           "_game_spec_for": lambda game, data=None: LOT.GAMES_BY_ID[(data or {}).get("game_id", game)],
           "_result_source": lambda name: sources.get(name),
           "_clamped_bench_target": lambda: 5,
           "_selected_games": lambda: LOT.games_for_country("DE"),
           "_country_label": lambda: "Germania",
           "ensure_worker_running": lambda: None, "submit_job": submit,
           "_refresh_status": lambda: None,
           "_thousands": lambda value: f"{value:g}",
           "_hypergeo_params": lambda game: (6, 40), "_ticket_pick": lambda game: 5}
    nodes = functions(ROOT / "ui_runtime.py", {"_wf_generation_options"})
    nodes += functions(ROOT / "app_nicegui.py", {"submit_generation", "_fixed_budget_540_config", "_submit_fixed_budget_540"})
    nodes += functions(ROOT / "ui_results.py", {"_fixed_budget_540_issue", "_fixed_budget_540_comparison",
                        "_show_fixed_budget_540", "_render_fixed_budget_540",
                        "_certified_fixed_budget_540_lines", "_wheel_probability_lines"})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "ui_fixed_budget_ast", "exec"), env)
    data = {"country": "RO", "game_id": "5/40", "hard_core": list(range(1, 8)),
            "variants": [list(v) for v in combinations(range(1, 8), 5)],
            "lookback": 60, "recent_penalty_draws": 2, "recent_penalty_factor": 0,
            "restrict_base_min": 0, "restrict_base_max": 0, "max_consecutive_run": 0,
            "guarantee": 5, "audit": {}}
    return env, data, sources, calls


def test_exact_baseline_comparison(environment):
    env, data, _, _ = environment
    odds = env["_fixed_budget_540_comparison"](data)
    assert odds["baseline_4"] == pytest.approx(0.004996899733741839)
    assert odds["baseline_5"] == pytest.approx(0.000182368603421235)
    assert odds["new_4"] == pytest.approx(0.01646788488893752)
    assert odds["new_5"] == pytest.approx(0.00019148703359229676)
    assert odds["category_i"] == 21 / math.comb(40, 5)


@pytest.mark.parametrize("change", ["duplicate", "foreign", "restriction", "consecutive", "audit_restriction", "bad_ticket"])
def test_targeted_eligibility_rejects_invalid_or_changed_contract(environment, change):
    env, data, _, _ = environment
    if change == "duplicate":
        data["variants"][-1] = data["variants"][0]
    elif change == "foreign":
        data["game_id"] = "de_lotto"
    elif change == "restriction":
        data["restrict_base_min"] = 10
    elif change == "audit_restriction":
        data["audit"]["restrict_base"] = {"min": 2, "max": 35}
    elif change == "consecutive":
        data["max_consecutive_run"] = 2
    elif change == "bad_ticket":
        data["variants"][0] = [True, 2, 3, 4, 5]
    assert env["_fixed_budget_540_issue"]("5/40", data)


def test_config_freezes_displayed_source_and_never_changes_settings(environment):
    env, data, sources, calls = environment
    before = deepcopy(env["SETTINGS"])
    original = sources["original540.csv"].copy(deep=True)
    text = env["_fixed_budget_540_config"]("original540.csv", data)
    sources["original540.csv"].loc[0, "n1"] = 40
    env["submit_generation"](config_json=text)
    assert len(calls) == 1
    config = calls[0][1]
    assert config["use_cache"] is False
    assert len(config["datasets"]) == 1
    ds = config["datasets"][0]
    restored = pd.read_json(io.StringIO(ds["df_json"]), orient="split", convert_dates=False)
    pd.testing.assert_frame_equal(restored, original)
    assert ds["tasks"] == [{"game_label": "5/40", "country": "RO", "pool_size": 40,
                            "guarantee": 5, "max_variants": 21, "wheel_condition": 0,
                            "lookback": 60, "recent_penalty_draws": 2, "recent_penalty_factor": 0.0,
                            "restrict_base_min": 0, "restrict_base_max": 0, "max_consecutive_run": 0,
                            "sim_depth_pct": 30, "pure_bench_mode": True, "bench_hit_target": 5}]
    assert env["STATE"]["results"] is None
    assert env["STATE"]["active_job_id"] == 42
    assert env["SETTINGS"] == before


@pytest.mark.parametrize("bad", ["cap", "source", "other_game"])
def test_invalid_prepared_submit_preserves_displayed_result(environment, bad):
    env, data, _, calls = environment
    cfg = json.loads(env["_fixed_budget_540_config"]("original540.csv", data))
    if bad == "cap":
        cfg["datasets"][0]["tasks"][0]["max_variants"] = 22
    elif bad == "other_game":
        cfg["datasets"][0]["tasks"][0]["game_label"] = "6/49"
    else:
        frame = pd.read_json(io.StringIO(cfg["datasets"][0]["df_json"]), orient="split", convert_dates=False)
        frame.loc[0, "n1"] = 99
        cfg["datasets"][0]["df_json"] = frame.to_json(orient="split")
    env["submit_generation"](config_json=json.dumps(cfg))
    assert calls == []
    assert env["STATE"]["results"] == ("old", 1)


@pytest.mark.parametrize("busy", ["virtual_wf_running", "active_job_id"])
def test_prepared_submit_keeps_active_job_guards(environment, busy):
    env, data, _, calls = environment
    text = env["_fixed_budget_540_config"]("original540.csv", data)
    env["STATE"][busy] = 17 if busy == "active_job_id" else True
    env["submit_generation"](config_json=text)
    assert calls == [] and env["STATE"]["results"] == ("old", 1)


@pytest.mark.parametrize("method", ["greedy", "lajolla", "weighted", "unknown"])
def test_explicit_other_wheel_method_blocks_offer_and_rechecks_prepared_submit(environment, monkeypatch, method):
    env, data, _, calls = environment
    text = env["_fixed_budget_540_config"]("original540.csv", data)
    monkeypatch.setenv("LOTO_WHEEL_METHOD", method)
    assert "impusă" in env["_fixed_budget_540_issue"]("5/40", data)
    env["submit_generation"](config_json=text)
    assert calls == [] and env["STATE"]["results"] == ("old", 1)


def test_dialog_callback_uses_frozen_config_and_shows_material_tradeoffs(environment):
    env, data, sources, calls = environment
    env["_render_fixed_budget_540"]("original540.csv", "5/40", data)
    action = next(node for node in env["ui"].nodes if node["kind"] == "button")
    action["kwargs"]["on_click"]()
    text = env["ui"].text()
    assert "105 lei" in text and "Categoria I rămâne identică" in text
    assert "valoarea așteptată" in text and "total 108 lei" in text
    assert "105,50 lei" in text and "98.3532%" in text
    assert "Rezultatul nou afișat va conține numai acest joc" in text
    original = sources["original540.csv"].copy(deep=True)
    sources["original540.csv"].loc[0, "n1"] = 40
    generate = next(node for node in env["ui"].nodes if node["kind"] == "button" and node["args"][0] == "Generează cele 21 variante")
    generate["kwargs"]["on_click"]()
    assert env["ui"].events == ["open", "close"]
    actual = pd.read_json(io.StringIO(calls[0][1]["datasets"][0]["df_json"]), orient="split", convert_dates=False)
    pd.testing.assert_frame_equal(actual, original)


def packed_data():
    from covering.dispatch import generate_wheel

    pool = list(range(1, 41))
    tickets, _ = generate_wheel("hitcover", pool, 5, 5, 21, None, None, 6, 40)
    return {"country": "RO", "game_id": "5/40", "hard_core": list(range(1, 41)),
            "variants": tickets, "audit": {"fixed_budget_540": {"position_pool": list(range(1, 41)), "ticket": {4: 1}}}}


def test_render_recalculates_certificate_from_actual_tickets(environment):
    env, _, _, _ = environment
    data = packed_data()
    text = "\n".join(env["_wheel_probability_lines"]("5/40", data))
    assert "1.646788%" in text and "0.019149%" in text
    assert "Categoria I" in text and "niciun premiu" in text.lower()
    assert "în pool 100" not in text


@pytest.mark.parametrize("tamper", ["ticket", "positions", "pool"])
def test_stale_or_modified_certificate_falls_back_to_existing_unknown_odds(environment, tamper):
    env, _, _, _ = environment
    data = packed_data()
    if tamper == "ticket":
        data["variants"][-1] = data["variants"][0]
    elif tamper == "positions":
        data["audit"]["fixed_budget_540"]["position_pool"] = list(reversed(range(1, 41)))
    else:
        data["hard_core"][-1] = 39
    text = "\n".join(env["_wheel_probability_lines"]("5/40", data))
    assert "1.646788%" not in text and "limita maximă" not in text
