"""Verifică progresul, persistența și anularea walk-forward LR cu date fictive."""
import asyncio
import json
import sys
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pandas as pd
import pytest

import lr_walk_forward_run as workflow


def _engine(*_args, **_kwargs):
    raise AssertionError("Testul trebuie să furnizeze propriul rezultat.")


@pytest.fixture(autouse=True)
def isolated_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow, "_state", workflow._RunState())
    monkeypatch.setattr(workflow, "_RESULT_PATH", tmp_path / ".nicegui" / "lr_walk_forward_latest.json")
    monkeypatch.setitem(
        sys.modules, "loto_enterprise.core.lr_walk_forward",
        SimpleNamespace(validate_lr_walk_forward=_engine),
    )


def _options(scheme=24):
    return dict(scheme=scheme, inverse=True, restricted=None, dynamic=True, lookback=80, hit_target=4,
                joker_size=2, budget_lei=200, objective="four_plus", depth=10)


def test_walk_forward_keeps_sources_and_settings_fixed_after_navigation(monkeypatch):
    frame = pd.DataFrame({"n1": [6]})
    options = _options(SimpleNamespace(code=24))

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def evaluate(function, game, history, *, progress_cb, cancel_path, **settings):
            assert function is _engine and game == "joker"
            progress_cb(4, 10)
            started.set()
            await release.wait()
            assert not cancel_path.exists()
            assert history[0] == "original.csv" and history[1].iloc[0]["n1"] == 6
            assert settings["lookback"] == 80 and settings["scheme"] == 24
            assert settings["restricted"] is None
            return {"restricted": True, "requested_restricted": None, "draws": [{"date": "2026-11-22"}]}

        monkeypatch.setattr(workflow.run, "io_bound", evaluate)
        jobs = [("joker", ("original.csv", frame), options)]
        launch = asyncio.create_task(workflow.run_lr_walk_forward(jobs))
        await asyncio.wait_for(started.wait(), timeout=2)
        revision, active, old, notice = workflow.lr_walk_forward_snapshot()
        assert active["current"] == (1, "Joker")
        assert active["progress"] == {"done": 4, "total": 10}
        assert active["current_settings"]["restricted"] is None
        assert old is None and not notice
        active["current_settings"]["lookback"] = 1
        assert workflow.lr_walk_forward_snapshot()[1]["current_settings"]["lookback"] == 80
        assert workflow.lr_walk_forward_snapshot(since=revision) is None
        assert not await workflow.run_lr_walk_forward(jobs)

        options["lookback"] = 99
        frame.loc[0, "n1"] = 8
        launch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await launch
        assert workflow.lr_walk_forward_running()
        release.set()
        await asyncio.wait_for(asyncio.shield(workflow._state.task), timeout=2)

        assert not workflow.lr_walk_forward_running()
        completed, = workflow.lr_walk_forward_snapshot()[2]["entries"]
        assert completed["settings"]["lookback"] == 80
        assert completed["result"]["restricted"] is True

    asyncio.run(scenario())


def test_walk_forward_reports_game_errors_and_restores_independently(monkeypatch, tmp_path):
    ordinary_report = tmp_path / "ordinary_walk_forward.json"
    ordinary_report.write_text('{"ordinary":true}', encoding="utf-8")
    frame = pd.DataFrame({"n1": [6]})
    calls = []

    async def evaluate(_function, game, _history, *, progress_cb, **_kwargs):
        calls.append(game)
        if game == "5/40":
            raise ValueError("Baza aleasă este indisponibilă.")
        progress_cb(1, 1)
        return {"game": game, "summary": {"evaluated": 1}}

    monkeypatch.setattr(workflow.run, "io_bound", evaluate)
    assert asyncio.run(workflow.run_lr_walk_forward([
        ("5/40", ("cinci.csv", frame), _options(15)),
        ("6/49", ("șase.csv", None), _options(57)),
        ("joker", ("joker.csv", frame), _options()),
    ]))
    assert calls == ["5/40", "joker"]
    saved = json.loads(workflow._RESULT_PATH.read_text(encoding="utf-8"))
    assert saved["entries"][0]["error"] == "Baza aleasă este indisponibilă."
    assert saved["entries"][1]["source"] == "șase.csv"
    assert saved["entries"][1]["error"] == "Istoricul jocului este indisponibil."
    assert saved["entries"][2]["result"]["summary"]["evaluated"] == 1
    assert ordinary_report.read_text(encoding="utf-8") == '{"ordinary":true}'
    monkeypatch.setattr(workflow, "_state", workflow._RunState())
    assert workflow.lr_walk_forward_snapshot()[2] == saved


@pytest.mark.parametrize(("previous_saved", "worker_raises"), [(True, False), (False, True)])
def test_walk_forward_cancellation_discards_late_worker_result_and_retains_previous_report(
    monkeypatch, previous_saved, worker_raises,
):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        frame = pd.DataFrame({"n1": [6]})
        markers = []

        async def first(*_args, **_kwargs):
            return {"draws": [{"date": "2026-11-22"}]}

        if previous_saved:
            monkeypatch.setattr(workflow.run, "io_bound", first)
            await workflow.run_lr_walk_forward([("joker", ("old.csv", frame), _options())])
            before = workflow._RESULT_PATH.read_bytes()
        else:
            assert not workflow._RESULT_PATH.parent.exists()

        async def evaluate(_fn, _game, _history, *, cancel_path, **_options_at_launch):
            markers.append(cancel_path)
            started.set()
            await release.wait()
            assert cancel_path.exists()
            if worker_raises:
                raise CancelledError()
            return {"draws": [{"date": "2026-11-25"}]}

        monkeypatch.setattr(workflow.run, "io_bound", evaluate)
        task = asyncio.create_task(workflow.run_lr_walk_forward([
            ("joker", ("joker.csv", frame), _options()),
            ("6/49", ("șase.csv", frame), _options(57)),
        ]))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert workflow.cancel_lr_walk_forward()
        assert not workflow.cancel_lr_walk_forward()
        assert workflow.lr_walk_forward_snapshot()[1]["cancelled"]
        release.set()
        assert await asyncio.wait_for(task, timeout=2)
        assert len(markers) == 1 and not markers[0].exists()
        assert "a fost anulată" in workflow.lr_walk_forward_snapshot()[3]
        if previous_saved:
            assert workflow._RESULT_PATH.read_bytes() == before
        else:
            assert not workflow._RESULT_PATH.exists()

    asyncio.run(scenario())


def test_progress_poll_skips_completed_draws_already_available_to_the_panel():
    class DrawRows:
        def __deepcopy__(self, memo):
            raise AssertionError("Progress display must not copy a historical draw array")

    workflow._state.active = {
        "progress": {"done": 3, "total": 10},
        "entries": [{"result": {"draws": DrawRows()}}],
    }
    workflow._state.latest = {"entries": [{"result": {"draws": DrawRows()}}]}
    _, active, latest, _ = workflow.lr_walk_forward_snapshot(
        include_active_entries=False, include_previous=False,
    )
    assert active == {"progress": {"done": 3, "total": 10}}
    active["progress"]["done"] = 9
    assert workflow._state.active["progress"]["done"] == 3
    assert latest is None

    workflow._state.latest = {"entries": [{"result": {"draws": [1, 2, 3]}}]}
    _, _, previous, _ = workflow.lr_walk_forward_snapshot(include_active_entries=False)
    assert previous == workflow._state.latest and previous is not workflow._state.latest

    workflow._state.active = None
    _, active, latest, _ = workflow.lr_walk_forward_snapshot(
        include_active_entries=False, include_previous=False,
    )
    assert active is None
    assert latest == workflow._state.latest and latest is not workflow._state.latest
