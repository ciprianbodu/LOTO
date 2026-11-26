"""Exercise the LR budget UI callback, queued worker/mail and causal paid replay.

python scripts/analysis/replay_lr_expanded_workflow.py --out _TESTE/lr_expanded_workflow.json
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import smtplib
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

if __package__:
    from .replay_lr_expanded_grid import FROZEN_REVISION, _git, _isolate, _now, _snapshot_sources, _write
else:
    from replay_lr_expanded_grid import FROZEN_REVISION, _git, _isolate, _now, _snapshot_sources, _write

LABELS = {"6/49": "_ISTORIC/loto_6_49.csv", "5/40": "_ISTORIC/loto_5_40.csv", "joker": "_ISTORIC/joker.csv"}


def config(project, runtime):
    _isolate(runtime, project)
    os.chdir(project)


def worker_entry(project, runtime):
    config(project, runtime)
    import job_queue
    import worker
    smtplib.SMTP = smtplib.SMTP_SSL = blocked
    claimed = job_queue.fetch_pending_job(worker_token=worker.WORKER_TOKEN)
    assert claimed is not None
    worker._process_one_job(claimed)


def blocked(*args, **kwargs):
    raise AssertionError("External SMTP or shutdown forbidden during the smoke")


def snapshot(project):
    fingerprints = _snapshot_sources(project)
    for path in LABELS.values():
        target = project / path
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(_git("show", f"{FROZEN_REVISION}:{path}"))
    return fingerprints

def main(project, runtime):
    config(project, runtime)
    import pandas as pd
    import job_queue
    from covering.lr_budget import FOUR_PLUS, recommend_games
    from covering import lr_pool_budget
    from covering.lr_pool_ranking import LR_RESTRICTED_RANGE
    from loto_enterprise.core import lr_budget_forecast as forecast
    from loto_enterprise.core import lr_walk_forward
    from loto_enterprise.core.lr_played_portfolio import layout_identity
    from loto_enterprise.core.history import history_dates
    from loto_enterprise.benchmark import lr_budget_scoring
    from loto_enterprise.core import lr_range_analysis
    from nicegui import run
    import ui_lr_budget
    import ui_shared
    import app_nicegui as app
    from loto_enterprise.core import official_prizes

    assert all(module.restrict_to_pool is lr_pool_budget.restrict_to_pool for module in (
        forecast, lr_walk_forward, lr_budget_scoring, lr_range_analysis
    ))
    assert sys.modules["runtime_paths"].PROJECT_ROOT == project
    assert Path(job_queue.DB_PATH) == runtime / "jobs.sqlite3"
    assert not (project / "best_methods.json").exists()
    app._maybe_shutdown = blocked
    app._launch_bench = blocked
    official_prizes._fetch_month_html = blocked
    app.SETTINGS.update(mail_on_complete=True, shutdown_on_complete=False, lr_budget_lei=300,
                        lr_budget_objective=FOUR_PLUS)
    captured = []
    real_solver = forecast.restrict_to_pool
    phases = {}

    def observed(*args, **kwargs):
        solution = real_solver(*args, **kwargs)
        if solution.get("tickets"):
            captured.append(deepcopy(solution.get("optimization", {})))
        return solution

    def smtp_sink(config, subject, body):
        assert config["mail_to"] == "audit@example.invalid"
        phases["smtp"] = {"capture_only": True, "body_characters": len(body)}

    with patch.object(smtplib, "SMTP", blocked), patch.object(smtplib, "SMTP_SSL", blocked), \
            patch.object(app, "send_email", smtp_sink), patch.object(ui_shared, "send_email", smtp_sink), \
            patch.object(app, "load_mail_config", lambda _root: {"mail_to": "audit@example.invalid"}), \
            patch.object(forecast, "restrict_to_pool", observed):
        game = "6/49"
        src = pd.read_csv(LABELS[game])
        small = src[history_dates(src) <= "2026-09-13"].tail(125).reset_index(drop=True)
        assert (history_dates(small).dt.strftime("%Y-%m-%d") == "2026-09-13").sum() == 2
        name = Path(LABELS[game]).name
        forecast_options = {"pool_codes": {}, "dynamic": True, "lookback": 100, "joker_size": 5, "hit_target": 4}
        inputs = {**forecast_options, "histories": {game: (name, small)}}
        async def build_ui():
            return await run.io_bound(ui_lr_budget._generate_selection, 300, FOUR_PLUS, {game: None}, inputs, False)
        ui_plan, = asyncio.run(build_ui())
        assert ui_plan["tickets"] and captured and not ui_plan.get("forecast_error"), ui_plan
        assert captured[-1]["expanded_certificate"]["applied"] and ui_plan["attained"] >= 383414
        phases["ui"] = {"layout": layout_identity(ui_plan), "optimization": captured[-1], "solver_called": True}

        job_queue.init_job_queue()
        task = dict(game_id=game, country="RO", game_label="6/49", pool_size=9, guarantee=4,
                    lr_scheme_code=48, lookback=100, dynamic_method=False, max_consecutive_run=2)
        job_args = dict(use_cache=False, datasets=[dict(fname=name, df_json=small.to_json(orient="split"), tasks=[task])])
        job = job_queue.submit_job("pipeline", json.dumps(job_args))
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(project), str(runtime)], check=True)
        status = job_queue.get_job_status(job)
        assert status is not None and status["status"] == "COMPLETED", status
        results = ui_shared.decode_queue_result(status["result_json"])
        small.attrs["lr_mail_settings"] = {"lr_budget_lei": 300, "lr_budget_objective": FOUR_PLUS, "bench_hit_target": 4}
        snap = app._mail_snapshot_inputs(results=results, result_sources={name: small})
        before = len(captured)
        app._prepare_mail_alternatives(snap)
        alternative = snap["games"][0]["pool_alternatives"]["inverse_reduced"]
        assert alternative.get("tickets") and len(captured) > before, alternative
        assert captured[-1]["expanded_certificate"]["applied"]
        assert app._maybe_send_results_email(snap) == "done"
        phases["worker_to_mail"] = {"job_status": status["status"], "normal_code": 48,
                                    "cost_bani": alternative["price_bani"], "optimization": captured[-1],
                                    "solver_called": True}

        template, = recommend_games(300, FOUR_PLUS, game)
        scheme = forecast.budget_plan_scheme(template)
        paid = lr_walk_forward.validate_lr_walk_forward(
            game, (name, small), budget_lei=300, objective=FOUR_PLUS, scheme=scheme,
            inverse=False, restricted=False, dynamic=True, lookback=100, hit_target=4, joker_size=5,
            depth=3, replay_policy="daily",
        )
        by_day = {}
        for draw in paid["draws"]:
            assert not draw.get("error"), draw
            day = draw["date"]
            if day not in by_day:
                origin = draw["training_rows"]
                prefix = small.iloc[:origin]
                assert history_dates(prefix).max().date().isoformat() < day
                direct, = forecast.recommend_playable_games(
                    300, FOUR_PLUS, game, histories={game: (name, prefix)},
                    **{**forecast_options, "pool_codes": {game: scheme.code}},
                )
                by_day[day] = layout_identity(direct), direct["tickets"]
            expected, tickets = by_day[day]
            assert expected == draw["layout_id"] and tickets == draw["tickets"], day
        assert len(paid["draws"]) > len(by_day)
        phases["paid_walk_forward"] = {"dates": len(by_day), "draws": len(paid["draws"]), "physical_layout_matches": True}

        game = "5/40"
        source = (Path(LABELS[game]).name, pd.read_csv(LABELS[game]))
        # At 300 lei the direct 19-number pool leaves only 17 of the 36 eligible balls.
        budget = 300
        template, = recommend_games(budget, FOUR_PLUS, game)
        required = forecast.budget_plan_scheme(template).n
        available = LR_RESTRICTED_RANGE[1] - LR_RESTRICTED_RANGE[0] + 1 - required
        assert (required, available) == (19, 17)
        impossible, = forecast.recommend_playable_games(
            budget, FOUR_PLUS, game, histories={game: source}, inverse=True, number_range=LR_RESTRICTED_RANGE,
            **forecast_options,
        )
        assert not impossible.get("tickets")
        assert str(available) in impossible["forecast_error"] and str(required) in impossible["forecast_error"]
        try:
            lr_walk_forward.validate_lr_walk_forward(
                game, source, budget_lei=budget, objective=FOUR_PLUS, scheme=forecast.budget_plan_scheme(template),
                inverse=True, restricted=True, dynamic=True, lookback=100, hit_target=4, joker_size=5,
                depth=1, replay_policy="daily",
            )
        except ValueError as exc:
            assert str(available) in str(exc) and str(required) in str(exc)
            phases["impossible"] = {"generator": impossible["forecast_error"], "walk_forward": str(exc)}
        else:
            raise AssertionError("An infeasible 5/40 layout got evaluated")
    return phases


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    destination = args.out.resolve()
    if destination.exists():
        parser.error("choose a new output path")
    started = _now()
    with TemporaryDirectory(prefix="loto-certified-workflow-") as temporary:
        work = Path(temporary)
        project, runtime = work / "sources", work / "runtime"
        project.mkdir()
        runtime.mkdir()
        fingerprints = snapshot(project)
        try:
            result = main(project, runtime)
            result["git_head"] = _git("rev-parse", "HEAD").decode().strip()
            result["history_revision"] = FROZEN_REVISION
            result["source_sha256"] = {p: fingerprints[p] for p in (
                "covering/lr_pool_budget.py", "covering/lr_expanded_search.py", "covering/lr_expanded_templates.py",
                "covering/lr_catalog_cache.py", "covering/lr_schemes.py", "covering_designs/lr/expanded_certificates.json",
                "loto_enterprise/benchmark/lr_provenance.py", "loto_enterprise/core/lr_walk_forward.py",
                "loto_enterprise/core/lr_budget_forecast.py", "worker.py", "ui_lr_budget.py",
            )}
            result["started_utc"] = started
            result["finished_utc"] = _now()
            _write(destination, result)
        finally:
            logging.shutdown()
            os.chdir(work.parent)
    print("WORKFLOW_REPORT", destination, flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker_entry(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        cli()
