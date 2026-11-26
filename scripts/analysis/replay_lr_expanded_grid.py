"""Regrade the archived LR witnesses and replay both grids through production.

python scripts/analysis/replay_lr_expanded_grid.py --archive _TESTE/Teste_LOTO_2026_11_25.zip --out _TESTE/lr_expanded_replay.json
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import multiprocessing
import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
FROZEN_REVISION = "e6cad789115d22ac839fc5e9390d3838bc4c9bfb"
ARCHIVE_ROOT = "loto_experiments_20261125/"
SUITES = {"cover": ("cover/cover_results.json", 42), "integration": ("cover/integration_results.json", 24)}
PROTECTED = ("attained", "4+", "5+", "6+")
RECHECK = PROTECTED + (
    "denominator", "any_prize", "prize_denominator", "cost_bani", "zones", "physical_tickets",
    "duplicate_paid_variants", "upper", "gap", "codes",
)


def _sha(contents):
    return sha256(contents).hexdigest()


def _git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, data):
    target = path.with_name(path.name + ".tmp")
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.replace(path)


def _read_archive(path):
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read(ARCHIVE_ROOT + "SHA256SUMS.json"))
        sources = {name: archive.read(ARCHIVE_ROOT + name) for name in manifest}
    for name, expected in manifest.items():
        if _sha(sources[name]) != expected:
            raise ValueError(f"Unexpected archive digest for {name}")
    cover, integration = (json.loads(sources[entry]) for entry, _count in SUITES.values())
    candidate = _sha(sources["cover/run_cover_experiment.py"])
    if (candidate != cover["source_sha256"]["experiment_harness"]
            or candidate != integration["candidate_sha256"]
            or _sha(sources["cover/run_integration_experiment.py"]) != integration["integration_harness_sha256"]):
        raise ValueError("The archive disagrees with its recorded experiment programs")
    for suite, records in zip(SUITES, (cover, integration), strict=True):
        if (records["protocol"]["source_commit"] != FROZEN_REVISION
                or records["protocol_sha256"] != _sha(sources[f"cover/{'integration_' if suite == 'integration' else ''}protocol.json"])
                or len(records["cases"]) != SUITES[suite][1]
                or len({case["id"] for case in records["cases"]}) != SUITES[suite][1]):
            raise ValueError(f"Unexpected archived protocol or case count for {suite}")
    return cover, integration, candidate, len(manifest)


def _snapshot_sources(project):
    paths = {p for p in ROOT.glob("*.py") if not p.name.startswith("test_")}
    for base in ("covering", "loto_enterprise"):
        paths.update((ROOT / base).rglob("*.py"))
    paths.update(path for path in (ROOT / "covering_designs").rglob("*") if path.suffix in {".json", ".txt"})
    paths.add(ROOT / "scripts/analysis/compare_lr_portfolio_search.py")
    digests = {}
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        source = path.read_bytes()
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        digests[relative.as_posix()] = _sha(source)
    return digests


def _isolate(runtime, project):
    os.environ["LOTO_RUNTIME_DIR"] = str(runtime)
    os.environ["LOTO_JOBS_DB"] = str(runtime / "jobs.sqlite3")
    os.environ["LOTO_POOL_HISTORY_FILE"] = str(runtime / "pools.json")
    os.environ["LOTO_WF_CACHE_DIR"] = str(runtime / "wf")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(project))


def _exact(row, pool, jokers):
    from scripts.analysis.compare_lr_portfolio_search import exact
    from loto_enterprise.core.lr_played_portfolio import layout_identity

    if row.get("evaluation") and row["evaluation"]["layout"] != layout_identity(row):
        raise AssertionError("The stored contract does not identify the physical layout")
    grade = exact(row, pool, jokers)
    objective_denominator = grade["prize_denominator"] if row["objective"] == "any_prize" else grade["denominator"]
    if not 0 <= grade["attained"] <= grade["upper"] <= objective_denominator:
        raise AssertionError("The reported bound is inconsistent with the achieved numerator")
    if row.get("optimal") and grade["gap"] != 0:
        raise AssertionError("A feasible layout with a remaining gap is marked optimal")
    return grade


def _difference(reference, actual, objective):
    if any(reference[key] != actual[key] for key in ("denominator", "prize_denominator")):
        raise AssertionError("The comparison changes the draw geometry")
    metrics = {}
    for key in (*PROTECTED, "any_prize"):
        value = actual[key] - reference[key]
        denominator = (actual["prize_denominator"] if key == "any_prize"
                       or key == "attained" and objective == "any_prize" else actual["denominator"])
        metrics[key] = {
            "numerator_delta": value,
            "percentage_points": 100 * value / denominator,
            "relative_percent": 100 * value / reference[key] if reference[key] else None,
        }
    cost = actual["cost_bani"] - reference["cost_bani"]
    protected = all(metrics[key]["numerator_delta"] >= 0 for key in PROTECTED)
    return {"metrics": metrics, "cost_delta_bani": cost, "protected": protected, "eligible": cost <= 0 and protected}


def _regrade_saved(suite, case):
    pool, jokers = tuple(case["pool"]), tuple(case["jokers"])
    names = ("baseline", "production", "experimental") if suite == "cover" else ("production", "experimental")
    independently_recounted = {}
    for name in names:
        recorded = case[name]
        verified = _exact(recorded["witness"], pool, jokers)
        wrong = [key for key in RECHECK if verified[key] != recorded["verified"][key]]
        if wrong:
            raise AssertionError((suite, case["id"], name, wrong))
        independently_recounted[name] = {"seconds": recorded["seconds"], "verified": verified}
    comparator = _difference(independently_recounted["production"]["verified"],
                             independently_recounted["experimental"]["verified"], case["objective"])
    if not comparator["eligible"]:
        raise AssertionError((suite, case["id"], "archived candidate violates its incumbent guards"))
    return independently_recounted


def _verified_histories(recorded):
    import pandas as pd
    from loto_enterprise.core.history import history_dates

    loaded = {}
    for game, entry in recorded.items():
        raw = _git("show", f"{FROZEN_REVISION}:{entry['file']}")
        if _sha(raw) != entry["sha256"]:
            raise AssertionError(f"The pinned source is not the archived historical prefix for {game}")
        frame = pd.read_csv(BytesIO(raw))
        actual_date = history_dates(frame).max().date().isoformat()
        if len(frame) != entry["rows"] or actual_date != entry["last_date"]:
            raise AssertionError(f"The pinned source does not reproduce the history bounds for {game}")
        loaded[game] = Path(entry["file"]).name, frame
    return loaded


def _on_explicit_pool(case, pool, jokers):
    from covering.lr_budget import recommend_games
    from covering.lr_pool_budget import restrict_to_pool

    budget, game, objective = case["budget_bani"], case["game"], case["objective"]
    plan, = recommend_games(max(100, (budget + 99) // 100), objective, game)
    if budget % 100:
        plan.update(tickets=[], price_bani=0, attained=0, variants=0)
    plan["budget_bani"] = budget
    return restrict_to_pool(plan, objective, pool, jokers)


def _generator(suite, case, histories):
    from loto_enterprise.core.lr_budget_forecast import recommend_playable_games

    budget, game, objective = case["budget_bani"], case["game"], case["objective"]
    if suite == "integration":
        plan, = recommend_playable_games(
            budget // 100, objective, game, histories=histories, pool_codes={}, generated=None,
            lookback=100, dynamic=True, joker_size=5, inverse=False, hit_target=None,
            number_range=None, lr_rebench_entries=(), selection_policy=None,
        )
        if not plan["tickets"] or plan.get("forecast_error"):
            raise AssertionError((case["id"], plan.get("forecast_error")))
        return plan, tuple(plan["forecast"]["pool"]), tuple(plan["forecast"]["jokers"])

    pool, jokers = tuple(case["pool"]), tuple(case["jokers"])
    return _on_explicit_pool(case, pool, jokers), pool, jokers


def _transpose(witness, prior_pool, prior_jokers, pool, jokers):
    from loto_enterprise.core.lr_played_portfolio import evaluation_contract

    transposed = deepcopy(witness)
    labels = dict(zip(sorted(prior_pool), sorted(pool), strict=True))
    bonuses = dict(zip(prior_jokers, jokers, strict=True))
    for ticket in transposed["tickets"]:
        for zone in ticket:
            zone["numbers"] = [labels[number] for number in zone["numbers"]]
            if zone["joker"] is not None:
                zone["joker"] = bonuses[zone["joker"]]
    transposed["forecast_pool"] = list(pool)
    transposed["forecast"]["pool"] = list(pool)
    transposed["forecast"]["jokers"] = list(jokers)
    settings = witness["evaluation"]
    transposed["evaluation"] = evaluation_contract(
        transposed, history_hash=settings["history_hash"], inverse=settings["inverse"],
        number_range=None, dynamic=settings["dynamic"], lookback=settings["lookback"],
        hit_target=settings["pool_hit_target"],
    )
    return transposed


def _selection(plan):
    forecast = plan["forecast"]
    return {field: forecast.get(field) for field in ("selection", "joker_selection", "history_hash")}


def _cache_counters():
    from covering.lr_expanded_templates import enhance_cached
    from covering.lr_pool_budget import _refine_cached
    from loto_enterprise.core import lr_budget_forecast

    counters = {"layout": _refine_cached.cache_info()._asdict(),
                "expanded": enhance_cached.cache_info()._asdict()}
    for name, obj in vars(lr_budget_forecast).items():
        if name.startswith("_") and callable(getattr(obj, "cache_info", None)):
            counters[name] = obj.cache_info()._asdict()
    return counters


def _current_case(suite, case, history_metadata, project):
    with TemporaryDirectory(prefix="loto-lr-replay-case-") as temporary:
        _isolate(Path(temporary), project)
        os.chdir(project)
        from loto_enterprise.core.ranking import longest_consecutive_run
        histories = _verified_histories({case["game"]: history_metadata[case["game"]]}) if suite == "integration" else None
        before = _cache_counters()
        cold_start = perf_counter()
        cold, pool, jokers = _generator(suite, case, histories)
        cold_time = perf_counter() - cold_start
        after_cold = _cache_counters()
        warm_start = perf_counter()
        warm, warm_pool, warm_jokers = _generator(suite, case, histories)
        warm_time = perf_counter() - warm_start
        after_warm = _cache_counters()
        if (pool, jokers, cold["tickets"], cold["price_bani"]) != (
            warm_pool, warm_jokers, warm["tickets"], warm["price_bani"],
        ):
            raise AssertionError((suite, case["id"], "the warm call changed the cold physical layout"))
        if suite == "integration" and (longest_consecutive_run(pool) > 2
                                       or len(jokers) != (5 if case["game"] == "joker" else 0)):
            raise AssertionError((case["id"], "invalid current pool or second-urn candidate count"))
        current = _exact(warm, pool, jokers)
        if current != _exact(cold, pool, jokers):
            raise AssertionError((suite, case["id"], "the warm and cold verified results differ"))
        source_pool, source_jokers = tuple(case["pool"]), tuple(case["jokers"])
        equivalent = pool == source_pool and jokers == source_jokers
        isomorphic = (len(pool), len(jokers)) == (len(source_pool), len(source_jokers))
        row = {
            "pool": pool, "jokers": jokers, "same_pool_and_joker_order": equivalent,
            "same_geometry": isomorphic, "same_joker_order": jokers == source_jokers,
            "cold_seconds": cold_time, "warm_seconds": warm_time,
            "cache_counters": {"initial": before, "cold": after_cold, "warm": after_warm},
            "verified": current, "witness": warm,
        }
        production, experimental = (case[name]["verified"] for name in ("production", "experimental"))
        if isomorphic and equivalent:
            row.update(against_archive_production=_difference(production, current, case["objective"]),
                       against_archive_experimental=_difference(experimental, current, case["objective"]))
        if suite == "integration":
            start = perf_counter()
            explicit = _on_explicit_pool(case, source_pool, source_jokers)
            seconds = perf_counter() - start
            fixed = _exact(explicit, source_pool, source_jokers)
            row["explicit_archive_pool"] = {
                "seconds_after_generation": seconds, "verified": fixed, "witness": explicit,
                "against_archive_production": _difference(production, fixed, case["objective"]),
                "against_archive_experimental": _difference(experimental, fixed, case["objective"]),
            }
        if isomorphic and not equivalent:
            from covering import lr_expanded_templates
            from loto_enterprise.core import lr_budget_forecast

            for name, expected in (("production", production), ("experimental", experimental)):
                control = _transpose(case[name]["witness"], source_pool, source_jokers, pool, jokers)
                verified = _exact(control, pool, jokers)
                if any(expected[key] != verified[key] for key in RECHECK):
                    raise AssertionError((case["id"], name, "positional transfer changed exact outcomes"))
                row[f"transposed_{name}"] = {"verified": verified, "witness": control}
            row.update(against_archive_production=_difference(production, current, case["objective"]),
                       against_archive_experimental=_difference(experimental, current, case["objective"]))
            # Remove only the optional covering stage and use the same Windows
            # process, prefix, eligibility rules and ranking implementation.
            with patch.object(lr_expanded_templates, "enhance_cached", lambda *args: None):
                lr_budget_forecast._live_budget_forecast.cache_clear()
                counterfactual, control_pool, control_jokers = _generator(suite, case, histories)
                incumbent = _on_explicit_pool(case, pool, jokers)
            incumbent_result = _exact(incumbent, pool, jokers)
            row["incumbent_on_current_pool"] = {
                "verified": incumbent_result, "witness": incumbent,
                "against_current_production": _difference(incumbent_result, current, case["objective"]),
            }
            row["pool_policy"] = {
                "archive": _selection(case["production"]["witness"]), "current": _selection(warm),
                "current_without_expansion": _selection(counterfactual),
                "pool_without_expansion": control_pool, "jokers_without_expansion": control_jokers,
            }
        return row


def _summarize(rows, suite):
    group = [case for case in rows if case["suite"] == suite]
    generated = [case["current"] for case in group if "current" in case]
    comparable = [row for row in generated if row["same_geometry"]]
    fixed = [row["explicit_archive_pool"] for row in generated if "explicit_archive_pool" in row]
    return {
        "cases": len(group),
        "witnesses_independently_regraded": sum(len(case["archive"]) for case in group),
        "generated": len(generated),
        "same_numeric_pool_and_joker_order": sum(row["same_pool_and_joker_order"] for row in generated),
        "same_geometry": len(comparable),
        "improved_over_original_production": sum(
            row["against_archive_production"]["metrics"]["attained"]["numerator_delta"] > 0 for row in comparable),
        "regressions_against_original_production": sum(not row["against_archive_production"]["eligible"]
                                                       for row in comparable),
        "meets_archived_experimental_witness": sum(row["against_archive_experimental"]["eligible"]
                                                  for row in comparable),
        "below_archived_experimental_witness": sum(not row["against_archive_experimental"]["eligible"]
                                                   for row in comparable),
        "replayed_on_explicit_archive_pool": len(fixed),
        "explicit_archive_pool_regressions": sum(not row["against_archive_experimental"]["eligible"] for row in fixed),
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--archive", type=Path, required=True)
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--verify-only", action="store_true", help="Recount archived layouts without invoking the generator.")
    cli.add_argument("--suite", choices=("all", *SUITES), default="all")
    cli.add_argument("--case", action="append", default=[], help="Restrict to IDs containing any supplied substring.")
    cli.add_argument("--workers", type=int, default=1, help="Fresh single-use processes; keep one for timings.")
    args = cli.parse_args()
    if not 1 <= args.workers <= 4:
        cli.error("workers must be between 1 and 4")
    output = args.out.resolve()
    if output == args.archive.resolve() or output.exists():
        cli.error("choose a new output path")
    cover, integration, candidate, archived_files = _read_archive(args.archive)
    selected = [(suite, case) for suite, record in zip(SUITES, (cover, integration), strict=True)
                for case in record["cases"] if args.suite in ("all", suite)
                and (not args.case or any(fragment in case["id"] for fragment in args.case))]
    if not selected:
        cli.error("no archived case matches these filters")
    with TemporaryDirectory(prefix="loto-lr-replay-") as temporary:
        project = Path(temporary) / "source"
        runtime = Path(temporary) / "runtime"
        runtime.mkdir()
        fingerprints = _snapshot_sources(project)
        _isolate(runtime, project)
        checked_evaluator = (project / "scripts/analysis/compare_lr_portfolio_search.py").read_bytes()
        if _sha(checked_evaluator.replace(b"\r\n", b"\n")) != cover["source_sha256"]["independent_evaluator"]:
            raise AssertionError("The independent evaluator has diverged from the archived protocol")
        import numpy
        import pandas
        import scipy
        report = {
            "started_utc": _now(), "git_head": _git("rev-parse", "HEAD").decode().strip(),
            "git_diff_sha256": _sha(_git("diff", "--binary", "HEAD")),
            "source_sha256": fingerprints,
            "archive_sha256": _sha(args.archive.read_bytes()), "archived_files_verified": archived_files,
            "archive_candidate_sha256": candidate, "frozen_source_revision": FROZEN_REVISION,
            "history_sha256": integration["histories"],
            "runtime": {"python": sys.version, "numpy": numpy.__version__, "pandas": pandas.__version__,
                        "scipy": scipy.__version__, "platform": platform.platform(), "workers": args.workers},
            "cases": [],
        }
        for suite, case in selected:
            report["cases"].append({"suite": suite, "id": case["id"], "budget_bani": case["budget_bani"],
                                    "archive": _regrade_saved(suite, case)})
        _write(output, report)
        if not args.verify_only:
            with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1,
                                     mp_context=multiprocessing.get_context("spawn")) as workers:
                futures = [workers.submit(_current_case, suite, case, integration["histories"], project)
                           for suite, case in selected]
                for recorded, future in zip(report["cases"], futures, strict=True):
                    recorded["current"] = future.result()
                    _write(output, report)
                    print(recorded["suite"], recorded["id"], recorded["current"]["verified"]["attained"], flush=True)
        report["summary"] = {suite: _summarize(report["cases"], suite) for suite in SUITES}
        report["finished_utc"] = _now()
        _write(output, report)
        print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
        return int(any(item["regressions_against_original_production"]
                       or item["below_archived_experimental_witness"]
                       or item["explicit_archive_pool_regressions"]
                       or item["same_geometry"] != item["generated"]
                       for item in report["summary"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
