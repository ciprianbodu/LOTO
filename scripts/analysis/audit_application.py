"""Reproducible application audit; production files are read, never rewritten.

Checks every registered history and local covering design, all scorer geometries,
production pool parity, fixed-budget ticket odds, and a real separate worker.
Use --output to save the JSON evidence. Statistical selection is audited
separately with audit_method_windows.py; this command makes no prediction claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def stop_process_tree(process):
    """Close the Windows venv launcher and its children before deleting logs."""
    import psutil

    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    _, alive = psutil.wait_procs(children, timeout=10)
    for child in alive:
        child.kill()
    psutil.wait_procs(alive, timeout=10)


def audit_ui_http(runtime):
    """Start the real UI with an empty queue and isolated settings, then load it."""
    import socket
    from urllib.error import URLError
    from urllib.request import urlopen

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env.update(
        LOTO_JOBS_DB=str(runtime / "ui_jobs.db"),
        LOTO_FRESH_START="1",
        LOTO_AUDIT_UI_STATE=str(runtime / "ui_state.json"),
    )
    source = """
import os
from pathlib import Path
import app_nicegui as app
import ui_runtime
ui_runtime.UI_STATE_FILE = app.UI_STATE_FILE = Path(os.environ["LOTO_AUDIT_UI_STATE"])
app.ui.run(title="Loto Enterprise Wheeling", port=PORT, reload=False, show=False)
""".replace("PORT", str(port))
    log = runtime / "ui_http.log"
    with log.open("w", encoding="utf-8") as out:
        process = subprocess.Popen(
            [sys.executable, "-c", source],
            cwd=ROOT,
            env=env,
            stdout=out,
            stderr=out,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(log.read_text(encoding="utf-8"))
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
                        body = response.read()
                        assert response.status == 200
                        assert b"Loto Enterprise Wheeling" in body
                        return {
                            "status": response.status,
                            "bytes": len(body),
                            "isolated_queue_and_settings": True,
                        }
                except (URLError, TimeoutError):
                    time.sleep(0.25)
            raise RuntimeError("UI HTTP timeout: " + log.read_text(encoding="utf-8"))
        finally:
            stop_process_tree(process)


def run_audit(runtime):
    import numpy as np
    import pandas as pd
    from covering.probability import wheel_hit_probabilities
    from loto_engine import LotoEngine
    from loto_enterprise.benchmark.curated import load_curated, load_per_game
    from loto_enterprise.benchmark.methods import METHODS, METHOD_LOAD_ERRORS
    from loto_enterprise.benchmark.runner import discover_games, load_draws, _top_k
    from loto_enterprise.core.draw_validation import valid_draw_matrix
    from loto_enterprise.core.history import chronological_history, training_cutoffs
    from loto_enterprise.core.lotteries import GAMES
    from loto_enterprise.core.method_selector import combine_ensemble_scores
    from loto_enterprise.core.score_validation import has_usable_score_variance
    from scripts.analysis.audit_patterns_and_designs import audit_designs
    from wheeling_methods import compute_coverage_pct, generate_wheel

    logging.disable(logging.CRITICAL)
    assert not METHOD_LOAD_ERRORS, METHOD_LOAD_ERRORS
    report = {
        "python": sys.version,
        "methods": len(METHODS),
        "curated": len(load_curated()),
        "per_game": {k: len(v) for k, v in load_per_game().items()},
        "histories": [],
        "scorers": [],
        "pipelines": [],
        "budgets": [],
    }
    frames = {}
    for lot in GAMES:
        path = ROOT / lot.csv
        df = chronological_history(pd.read_csv(path))
        draws, valid = valid_draw_matrix(
            df,
            [f"n{i}" for i in range(1, lot.draw_n + 1)],
            draw_n=lot.draw_n,
            max_num=lot.max_n,
        )
        assert valid.all(), (lot.game_id, np.flatnonzero(~valid).tolist())
        cutoffs = training_cutoffs(df)
        assert all(0 <= c <= i for i, c in enumerate(cutoffs))
        second = lot.geo.second
        if second:
            _, second_valid = valid_draw_matrix(
                df, second.columns, draw_n=second.draw_n, max_num=second.max_n
            )
            assert second_valid.all(), (lot.game_id, "invalid second urn")
        frames[lot.game_id] = df
        report["histories"].append(
            dict(
                game=lot.game_id,
                country=lot.country,
                rows=len(draws),
                draw_n=lot.draw_n,
                pick_n=lot.pick_n,
                sha256=digest(path),
                same_day_targets=sum(c < i for i, c in enumerate(cutoffs)),
            )
        )
    print(f"Histories validated: {len(report['histories'])}", flush=True)
    for game in discover_games(str(ROOT / "_ISTORIC")):
        draws = load_draws(game)
        for length in (5, 30, 100, len(draws)):
            past = draws[:length]
            for name, (fn, *_) in METHODS.items():
                raw = fn(past, game.max_num)
                usable = has_usable_score_variance(raw)
                blended = combine_ensemble_scores([(name, raw, 1.0)])
                assert has_usable_score_variance(blended) == usable, (
                    game.key,
                    name,
                    length,
                )
                if usable:
                    assert blended == raw
                    for k in (1,) if game.is_single_pick else (game.base_k, 11, 16):
                        from loto_enterprise.core.pool_selection import (
                            select_pool_from_scores,
                        )

                        assert select_pool_from_scores(
                            raw, k, set(), max_num=game.max_num
                        ) == sorted(_top_k(raw, k))
                report["scorers"].append(
                    dict(game=game.key, method=name, length=length, usable=usable)
                )
    print(f"Scorer parity checks: {len(report['scorers'])}", flush=True)
    report["designs"] = audit_designs()
    for lot in GAMES:
        for cap in (0, 7):
            engine = LotoEngine(
                lot.geometry, game_key=lot.bench_key, country=lot.country
            )
            assert engine.load_data(str(ROOT / lot.csv))
            lines, *_, ctx, audit = engine.run_institutional_pipeline(
                pool_size=11,
                guarantee=4,
                max_variants=cap,
                track_pool_variation=False,
                enable_adaptive_persistence=False,
                max_consecutive_run=2,
            )
            main = [line[: lot.pick_n] for line in lines]
            assert main and all(len(t) == len(set(t)) == lot.pick_n for t in main)
            cov = compute_coverage_pct(main, engine.hard_core, 4)
            assert cov == ctx["coverage_pct"] and (cap or cov == 100.0)
            assert cap == 0 or len(main) <= cap
            assert audit["pool_numbers_not_on_tickets"] == sorted(
                set(engine.hard_core) - set().union(*map(set, main))
            )
            report["pipelines"].append(
                dict(
                    game=lot.game_id,
                    cap=cap,
                    pool=engine.hard_core,
                    tickets=len(main),
                    coverage=cov,
                    scorer=(audit.get("bench_winner") or {}).get(lot.bench_key),
                )
            )
    print(f"Production pipelines validated: {len(report['pipelines'])}", flush=True)
    for v in (11, 16):
        for draw_n, pick, universe in ((6, 6, 49), (6, 5, 40), (5, 5, 45)):
            for target in (3, 4):
                for cap in (7, 10):
                    pool = list(range(1, v + 1))
                    scores = {n: float((n * 17) % 23) for n in pool}
                    base, cov = generate_wheel(
                        "greedy", pool, pick, target, cap, scores
                    )
                    optimized, optimized_cov = generate_wheel(
                        "hitcover", pool, pick, target, cap, scores
                    )
                    a = wheel_hit_probabilities(pool, base, draw_n, universe)
                    b = wheel_hit_probabilities(pool, optimized, draw_n, universe)
                    assert len(base) == len(optimized) and optimized_cov >= cov
                    assert all(b["ticket"][t] >= a["ticket"][t] for t in a["ticket"])
                    report["budgets"].append(
                        dict(
                            pool=v,
                            draw_n=draw_n,
                            pick_n=pick,
                            universe=universe,
                            target=target,
                            cap=cap,
                            before_coverage=cov,
                            after_coverage=optimized_cov,
                            before=a,
                            after=b,
                        )
                    )
    # A real worker process uses only this temporary queue/history/log directory.
    import job_queue as queue
    from ui_shared import decode_queue_result

    tasks = []
    for lot in GAMES:
        task = {
            "game_label": lot.game_id,
            "country": lot.country,
            "pool_size": 11,
            "guarantee": 4,
            "max_variants": 7,
            "max_consecutive_run": 2,
        }
        tasks.append(
            {
                "fname": Path(lot.csv).name,
                "df_json": frames[lot.game_id].to_json(orient="split"),
                "tasks": [task],
            }
        )
    job = queue.submit_job(
        "pipeline", json.dumps({"datasets": tasks, "use_cache": False})
    )
    log = runtime / "worker_subprocess.log"
    with log.open("w", encoding="utf-8") as out:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "worker.py")],
            cwd=ROOT,
            stdout=out,
            stderr=out,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                state = queue.get_job_status(job)
                if state["status"] in {"COMPLETED", "FAILED"}:
                    break
                if process.poll() is not None:
                    raise RuntimeError(log.read_text(encoding="utf-8"))
                time.sleep(0.25)
            assert state["status"] == "COMPLETED", state
            bundle, n = decode_queue_result(state["result_json"])
            assert n == len(GAMES) and len(bundle) == n
            for (_, outputs), lot in zip(bundle, GAMES):
                data = outputs[lot.game_id]
                assert data["country"] == lot.country and data["game_id"] == lot.game_id
                assert data["variants"] and len(data["variants"]) <= 7
            report["worker_e2e"] = {
                "status": state["status"],
                "games": n,
                "payload_decoded": True,
            }
        finally:
            stop_process_tree(process)
    report["ui_http"] = audit_ui_http(runtime)
    print(f"UI HTTP verified: {report['ui_http']['status']}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protected = [
        ROOT / p
        for p in (
            "best_methods.json",
            "pool_history.json",
            "adaptive_state.json",
            ".ui_state.json",
            "bench_results/folds.csv",
            "bench_results/report.json",
        )
    ]
    protected += list((ROOT / "_ISTORIC").rglob("*.csv"))
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="loto_audit_") as tmp:
        runtime = Path(tmp)
        os.environ.update(
            LOTO_RUNTIME_DIR=str(runtime),
            LOTO_JOBS_DB=str(runtime / "jobs.db"),
            LOTO_WF_CACHE_DIR=str(runtime / "wf"),
            LOTO_POOL_HISTORY_FILE=str(runtime / "pool_history.json"),
        )
        os.environ.pop("LOTO_WHEEL_METHOD", None)
        try:
            report = run_audit(runtime)
        finally:
            logging.shutdown()
    after = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    report["production_unchanged"] = before == after
    report["protected_sha256"] = before
    report["seconds"] = time.perf_counter() - started
    assert report["production_unchanged"], "Production files changed during audit"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    from ui_shared import atomic_write_json

    atomic_write_json(args.output, report)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k in {"methods", "worker_e2e", "production_unchanged", "seconds"}
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
