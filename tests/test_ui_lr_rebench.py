"""Rezultatele pe baza aleasă rămân independente de Re-Bench obișnuit."""
import asyncio
import json
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
    monkeypatch.setattr(panel.ui, "timer", lambda *_args: None)

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
            return _result(game, options["scheme"])

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
            return _result(game, options["scheme"])

        monkeypatch.setattr(panel.run, "io_bound", paused)
        pending = asyncio.create_task(panel.run_lr_rebench([
            ("joker", ("new.csv", frame), _settings(24)),
            ("6/49", ("also-new.csv", frame), _settings(57)),
        ]))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert panel.cancel_lr_rebench()
        assert not panel.cancel_lr_rebench()
        assert panel.lr_rebench_running()
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
