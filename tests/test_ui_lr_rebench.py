"""Rezultatele pe baza aleasă rămân independente de Re-Bench obișnuit."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import CancelledError

import pandas as pd
import pytest
from nicegui import Client, ui

import ui_lr_rebench as panel
from covering.lr_selection import resolve_lr_scheme


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(panel, "_RESULT_PATH", tmp_path / ".nicegui" / "lr_rebench_latest.json")
    monkeypatch.setattr(panel, "_task", None)
    monkeypatch.setattr(panel, "_active", None)
    monkeypatch.setattr(panel, "_last", None)
    monkeypatch.setattr(panel, "_loaded", False)
    monkeypatch.setattr(panel, "_notice", "")
    monkeypatch.setattr(panel, "_revision", 0)


def _settings(scheme):
    return dict(scheme=scheme, inverse=True, restricted=True, dynamic=True,
                lookback=80, hit_target=4, joker_size=2)


def _result(game, code):
    urns = [
        {"name": "Urna principală", "pool_size": 10, "target": 4, "eligible_count": 21,
         "method": "frequency", "folds": [10, 30, 70, 90], "fallback": False,
         "protocol": "Se evaluează toate extragerile din ferestre.",
         "rows": [{"method": "frequency", "evaluated": 40, "hit_rate": 0.5, "avg_hits": 1.125}]},
    ]
    if game == "joker":
        urns.append({"name": "Urna 2 Joker", "pool_size": 1, "target": 1, "eligible_count": 18,
                     "method": "random", "folds": [10], "fallback": True,
                     "rows": [{"method": "random", "evaluated": 30, "hit_rate": 0.1, "avg_hits": 0.1}]})
    return {"game": game, "scheme_code": code, "as_of": "2026-11-22", "urns": urns}


def test_reports_restore_after_navigation_and_show_both_urns_and_per_game_errors(monkeypatch, tmp_path):
    ordinary = tmp_path / "best_methods.json"
    ordinary.write_text('{"ordinary":true}', encoding="utf-8")
    frame = pd.DataFrame({"n1": [6]})
    dispatched = []

    async def evaluate(fn, game, history, **settings):
        assert fn is panel.benchmark_lr_hypotheses
        assert not settings["cancel_path"].exists()
        dispatched.append(game)
        if game == "5/40":
            raise ValueError("Fereastră indisponibilă.")
        return _result(game, settings["scheme"])

    monkeypatch.setattr(panel.run, "io_bound", evaluate)
    # Timerul aparține clientului din browser. Prima redare execută codul real.
    timers = []
    monkeypatch.setattr(panel.ui, "timer", lambda interval, callback, **kwargs: timers.append(callback))

    async def scenario():
        scheme = resolve_lr_scheme("joker", 24)
        assert await panel.run_lr_rebench([
            ("joker", ("jocuri.csv", frame), _settings(scheme)),
            ("5/40", ("cinci.csv", frame), _settings(15)),
            ("6/49", ("lipsa.csv", None), _settings(57)),
        ])
        assert not panel.lr_rebench_running()
        assert dispatched == ["joker", "5/40"]
        assert ordinary.read_text(encoding="utf-8") == '{"ordinary":true}'
        stored = json.loads(panel._RESULT_PATH.read_text(encoding="utf-8"))
        assert [record["game"] for record in stored["entries"]] == ["joker", "5/40", "6/49"]
        assert stored["entries"][0]["settings"] == _settings(24)

        monkeypatch.setattr(panel, "_last", None)
        monkeypatch.setattr(panel, "_loaded", False)
        client = Client(ui.page("/test-lr-rebench"))
        try:
            with client:
                rendered = panel.lr_rebench_panel()
                await timers[-1]()
            labels = {node.text for node in rendered.descendants() if isinstance(node, ui.label)}
            assert {"Urna principală", "Urna 2 Joker", "Fereastră indisponibilă.",
                    "Istoricul jocului este indisponibil."} <= labels
            assert any("jocuri.csv" in text and "22.11.2026" in text and "80%" in text for text in labels)
            assert any("Cod LR 24" in text and "inversă" in text and "10–40" in text for text in labels)
            tables = [node for node in rendered.descendants() if isinstance(node, ui.table)]
            assert [(table.rows[0]["evaluated"], table.rows[0]["hit_rate"]) for table in tables] == [
                (40, "50,00%"), (30, "10,00%"),
            ]
        finally:
            client.delete()

    asyncio.run(scenario())


def test_ruling_is_unique_and_retains_snapshot_when_the_initiating_tab_leaves(monkeypatch):
    frame = pd.DataFrame({"n1": [6]})
    settings = _settings(24)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def evaluate(fn, game, history, **options):
            started.set()
            await release.wait()
            assert history[0] == "original.csv"
            assert history[1].iloc[0]["n1"] == 6
            assert options["lookback"] == 80
            return _result(game, options["scheme"])

        monkeypatch.setattr(panel.run, "io_bound", evaluate)
        jobs = [("joker", ("original.csv", frame), settings)]
        initiating = asyncio.create_task(panel.run_lr_rebench(jobs))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert panel.lr_rebench_running()
        assert panel._active["current"] == (1, "Joker")
        assert not await panel.run_lr_rebench(jobs)
        settings["lookback"] = 50
        frame.loc[0, "n1"] = 8
        initiating.cancel()
        with pytest.raises(asyncio.CancelledError):
            await initiating
        assert panel.lr_rebench_running()
        release.set()
        await asyncio.wait_for(asyncio.shield(panel._task), timeout=2)
        assert not panel.lr_rebench_running()
        assert panel._last["entries"][0]["settings"]["lookback"] == 80

    asyncio.run(scenario())


@pytest.mark.parametrize(("worker_raises", "previous_saved"), [(False, True), (True, True), (True, False)])
def test_cancellation_discards_in_flight_report_and_keeps_last_saved(monkeypatch, worker_raises, previous_saved):
    async def scenario():
        started, resume = asyncio.Event(), asyncio.Event()
        cancel_files = []
        frame = pd.DataFrame({"n1": [6]})

        async def immediate(_fn, game, _history, **options):
            return _profile_result(game, options["scheme"], "a" * 16)

        if previous_saved:
            monkeypatch.setattr(panel.run, "io_bound", immediate)
            await panel.run_lr_rebench([("joker", ("previous.csv", frame), _settings(24))])
            last = panel._RESULT_PATH.read_bytes()
        else:
            assert not panel._RESULT_PATH.parent.exists()

        async def paused(_fn, game, _history, **options):
            cancel = options["cancel_path"]
            cancel_files.append(cancel)
            started.set()
            await resume.wait()
            assert cancel.exists()
            if worker_raises:
                raise CancelledError()
            return _profile_result(game, options["scheme"], "b" * 16)

        monkeypatch.setattr(panel.run, "io_bound", paused)
        pending = asyncio.create_task(panel.run_lr_rebench([
            ("joker", ("new.csv", frame), _settings(24)),
            ("6/49", ("also-new.csv", frame), _settings(57)),
        ]))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert panel.cancel_lr_rebench()
        assert not panel.cancel_lr_rebench()
        assert panel.lr_rebench_running()
        await panel._active["cancel_write"]
        resume.set()
        assert await asyncio.wait_for(pending, timeout=2)
        assert len(cancel_files) == 1 and not cancel_files[0].exists()
        assert not panel.lr_rebench_running()
        if previous_saved:
            assert panel._RESULT_PATH.read_bytes() == last
        else:
            assert not panel._RESULT_PATH.exists()
        assert "a fost anulată" in panel._notice

    asyncio.run(scenario())


def _profile_result(game, scheme, fingerprint, *, partial=False):
    report = _result(game, scheme)
    report.update(history_hash=fingerprint, history_rows=240, max_consecutive_run=2,
                  semantics={"version": 1, "formulas": "fixture"})
    keys = {
        "joker": ("joker_urna1", "joker_urna2"),
        "6/49": ("loto_6_49",),
        "5/40": ("loto_5_40",),
    }
    for urn, key in zip(report["urns"], keys[game], strict=True):
        urn["game_key"] = key
        urn["decision"] = {
            "members": [{"method": "frequency", "weight": 0.65}, {"method": "ewma_40", "weight": 0.35}],
            "reference_members": [{"method": "frequency", "weight": 1.0}],
            "target": urn["target"], "fallback": False, "folds": [10], "evaluated": 24,
        }
    if partial:
        report["urns"][-1]["decision"] = None
        report["urns"][-1]["error"] = "Istoric insuficient în Urna 2 Joker."
    return report


def test_saved_decisions_retain_each_game_and_hypothesis_and_replace_only_complete_profiles(monkeypatch):
    frame = pd.DataFrame({"n1": [6]})
    old, newer = "a" * 16, "b" * 16
    joker_options = _settings(24)

    async def scenario():
        async def publish(game, name, result, settings):
            async def evaluate(*_args, **_kwargs):
                if isinstance(result, Exception):
                    raise result
                return result
            monkeypatch.setattr(panel.run, "io_bound", evaluate)
            assert await panel.run_lr_rebench([(game, (name, frame), settings)])

        await publish("joker", "joker.csv", _profile_result("joker", 24, old), joker_options)
        await publish("6/49", "six.csv", _profile_result("6/49", 57, old), _settings(57))
        await publish("joker", "joker.csv", _profile_result("joker", 24, old), {**joker_options, "restricted": False})
        baseline = panel.lr_rebench_decisions()
        assert set(baseline) == {"joker", "6/49"}
        assert [entry["settings"]["restricted"] for entry in baseline["joker"]] == [False, True]

        await publish("joker", "joker.csv", _profile_result("joker", 24, newer, partial=True), joker_options)
        await publish("joker", "joker.csv", ValueError("test failed"), joker_options)
        assert panel.lr_rebench_decisions() == baseline

        await publish("joker", "joker.csv", _profile_result("joker", 24, newer), joker_options)
        await publish("joker", "uploaded.csv", _profile_result("joker", 24, old), joker_options)
        profiles = panel.lr_rebench_decisions()
        candidates = profiles["joker"]
        assert [(item["source"], item["settings"]["restricted"], item["result"]["history_hash"])
                for item in candidates] == [
                    ("uploaded.csv", True, old), ("joker.csv", True, newer), ("joker.csv", False, old),
                ]
        assert [urn["game_key"] for urn in candidates[1]["result"]["urns"]] == ["joker_urna1", "joker_urna2"]
        assert "rows" not in candidates[1]["result"]["urns"][0]
        assert candidates[1]["result"]["urns"][0]["decision"]["members"] == [
            {"method": "frequency", "weight": 0.65}, {"method": "ewma_40", "weight": 0.35},
        ]
        assert profiles["6/49"] == baseline["6/49"]
        mutated = panel.lr_rebench_decisions()
        mutated["joker"][0]["result"]["urns"][0]["decision"]["members"][0]["weight"] = 99
        assert panel.lr_rebench_decisions() == profiles
        monkeypatch.setattr(panel, "_last", None)
        monkeypatch.setattr(panel, "_loaded", False)
        assert panel.lr_rebench_decisions() == profiles

    asyncio.run(scenario())


def test_legacy_report_is_visible_but_produces_no_reusable_decision():
    prior = _profile_result("joker", 24, "a" * 16)
    entry = {"game": "joker", "source": "legacy.csv", "settings": _settings(24), "result": prior}
    finished = "2026-11-25T11:20:00+02:00"
    saved_profile = panel._compact_profile(entry, finished)
    del saved_profile["result"]["max_consecutive_run"]
    del prior["max_consecutive_run"]
    panel.atomic_write_json(panel._RESULT_PATH, {
        "schema": 1, "total": 1, "finished_at": finished, "entries": [entry],
        "decision_profiles": {"joker": [saved_profile]},
    })
    assert panel.lr_rebench_decisions() == {}
    assert panel._last["entries"][0]["result"]["urns"][0]["method"] == "frequency"


def test_two_independent_ui_processes_merge_results_and_preserve_unsigned_history(tmp_path):
    finished = "2024-01-01T10:00:00+02:00"
    legacy = _profile_result("6/49", 49, "a" * 16)
    del legacy["semantics"]
    legacy["urns"][0]["decision"]["members"] = [{"method": "former_method", "weight": 1.0}]
    old = {"game": "6/49", "source": "six.csv", "name": "Loto 6/49", "settings": _settings(49), "result": legacy}
    panel.atomic_write_json(panel._RESULT_PATH, {"schema": 1, "total": 1, "finished_at": finished,
                                                "entries": [old], "decision_profiles": {}})
    child = """
import asyncio, pathlib, runpy, sys, time
helpers = runpy.run_path(sys.argv[1])
p = helpers['panel']
p._RESULT_PATH = pathlib.Path(sys.argv[2])
game, code, source, signal = sys.argv[3:7]
control = pathlib.Path(signal)
p._restore_report()
assert p._last['entries'][0]['result']['history_hash'] == 'a'*16
control.with_suffix('.ready').touch()
for _ in range(400):
    if control.with_name('go').exists(): break
    time.sleep(.025)
else: raise RuntimeError('timeout')
async def complete(*args, **kwargs):
    return helpers['_profile_result'](game, int(code), source[0]*16)
p.run.io_bound = complete
frame = helpers['pd'].DataFrame({'n1':[1]})
asyncio.run(p.run_lr_rebench([(game, (source, frame), helpers['_settings'](int(code)))]))
"""
    children = []
    env = {**os.environ, "LOTO_RUNTIME_DIR": str(tmp_path/"runtime"), "LOTO_JOBS_DB": str(tmp_path/"jobs.sqlite")}
    try:
        for i, (game, code, source) in enumerate([("6/49", "49", "six.csv"), ("joker", "24", "joker.csv")]):
            children.append(subprocess.Popen(
                [sys.executable, "-c", child, __file__, str(panel._RESULT_PATH), game, code, source, str(tmp_path/f"p{i}")],
                cwd=Path(__file__).resolve().parents[1], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
        deadline = time.monotonic() + 30
        while not all((tmp_path/f"p{i}.ready").exists() for i in range(2)):
            assert time.monotonic() < deadline
            time.sleep(.05)
        (tmp_path/"go").touch()
        for process in children:
            output, _ = process.communicate(timeout=30)
            assert process.returncode == 0, output
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait()

    # This process still had the old file in memory. The next read sees both completions.
    assert set(panel.lr_rebench_decisions()) == {"6/49", "joker"}
    archive = panel._last["decision_history"]
    old, = [d for d in archive if d["history_hash"] == "a" * 16]
    assert old["signature"] is None and old["urns"][0]["members"][0]["method"] == "former_method"
    client = Client(ui.page("/historical-rebench"))
    try:
        with client:
            with ui.column() as view:
                panel._historical_decision(old)
        assert any("former_method" in node.text for node in view.descendants() if isinstance(node, ui.label))
    finally:
        client.delete()
    latest = {k: v for k, v in panel._last.items() if k not in ("decision_history", "decision_profiles")}
    for _ in range(3):
        panel._persist_report(latest)
    assert panel._last["decision_history"] == archive


def test_new_formula_same_history_and_settings_preserves_previous_weights():
    def finish(version, method, when):
        result = _profile_result("6/49", 49, "d"*16)
        result["semantics"]["formulas"] = version
        result["urns"][0]["decision"]["members"] = [{"method": method, "weight": 1.0}]
        entry = {"game": "6/49", "source": "six.csv", "settings": _settings(49), "result": result}
        panel._persist_report({"schema": 1, "total": 1, "finished_at": when, "entries": [entry]})
    finish("first", "frequency", "2026-11-25T17:00:00+02:00")
    finish("second", "random", "2026-11-25T18:00:00+02:00")
    latest, = panel.lr_rebench_decisions()["6/49"]
    old, = panel._last["decision_history"]
    assert latest["result"]["urns"][0]["decision"]["members"][0]["method"] == "random"
    assert old["signature"] and old["urns"][0]["members"] == [{"method": "frequency", "weight": 1.0}]
    assert old["history_hash"] == latest["result"]["history_hash"]


@pytest.mark.parametrize("chosen", [None, (10, 40)])
def test_automatic_scope_uses_captured_budget_and_persists_only_the_effective_choice(monkeypatch, chosen):
    frame = pd.DataFrame({"n1": [6]})
    settings = {**_settings(24), "restricted": False, "auto_budget": (200, "4+")}
    recipe = object()
    calls = []

    async def execute(function, *args, **kwargs):
        calls.append(function)
        if function is panel.compare_budget_ranges:
            assert args[:3] == (200, "4+", "joker")
            assert args[3]["joker"][0] == "input.csv"
            pd.testing.assert_frame_equal(args[3]["joker"][1], frame)
            assert args[3]["joker"][1] is not frame
            assert kwargs == dict(inverse=True, dynamic=True, lookback=80, hit_target=4,
                                  joker_size=2, pool_codes={"joker": 24}, return_recipe=True)
            return {"recommended_range": chosen, "paid_recipe": recipe}
        assert function is panel.benchmark_lr_hypotheses
        assert kwargs["recipe"] is recipe
        assert kwargs["restricted"] is (chosen is not None)
        assert "auto_budget" not in kwargs
        return _profile_result("joker", 24, "a" * 16)

    monkeypatch.setattr(panel.run, "io_bound", execute)
    assert asyncio.run(panel.run_lr_rebench([("joker", ("input.csv", frame), settings)]))
    assert calls == [panel.compare_budget_ranges, panel.benchmark_lr_hypotheses]
    saved, = panel.lr_rebench_decisions()["joker"]
    assert saved["settings"]["restricted"] is (chosen is not None)
    assert "auto_budget" not in saved["settings"]
    assert settings["restricted"] is False


def test_cancelling_during_automatic_scope_does_not_start_benchmark(monkeypatch):
    frame = pd.DataFrame({"n1": [6]})
    settings = {**_settings(24), "restricted": False, "auto_budget": (200, "4+")}

    async def scenario():
        comparing, release = asyncio.Event(), asyncio.Event()

        async def execute(function, *_args, **_kwargs):
            assert function is panel.compare_budget_ranges
            comparing.set()
            await release.wait()
            return {"recommended_range": (10, 40)}

        monkeypatch.setattr(panel.run, "io_bound", execute)
        task = asyncio.create_task(panel.run_lr_rebench([("joker", ("input.csv", frame), settings)]))
        await asyncio.wait_for(comparing.wait(), timeout=2)
        assert panel._active["comparing"]
        assert panel.cancel_lr_rebench()
        release.set()
        assert await asyncio.wait_for(task, timeout=2)
        assert panel.lr_rebench_decisions() == {}
        assert not panel._RESULT_PATH.exists()

    asyncio.run(scenario())


def test_provisional_report_is_visible_and_cannot_replace_a_complete_profile():
    prior = _profile_result("joker", 24, "a" * 16)
    settings = _settings(24)
    entry = {"game": "joker", "source": "test.csv", "settings": settings, "result": prior}
    first = {"finished_at": "2026-11-23T14:00:00+02:00", "entries": [entry]}
    profiles = panel._remember_profiles(first, {})
    assert len(profiles["joker"]) == 1
    failed = {**prior, "portfolio_refinement": {"complete": False, "issues": ["refinement_error"], "recipe_sha256": []}}
    second = {"finished_at": "2026-11-23T15:00:00+02:00", "entries": [{**entry, "result": failed}]}
    assert panel._remember_profiles(second, {"decision_profiles": profiles}) == profiles
    assert panel._compact_profile(second["entries"][0], second["finished_at"]) is None
