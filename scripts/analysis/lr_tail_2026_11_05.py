"""Frozen, offline LR-tail experiment. See the adjacent protocol JSON.

The model describes a hypothesis, not an established lottery bias. Its exact
scheme probabilities optimize a declared predictive distribution; only held-out
outcomes can assess that distribution. These archives are already reused, so
even the chronological final window remains exploratory. Nothing is promoted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_variable] = "1"

import numpy as np
import scipy
from scipy.special import logsumexp
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from covering.lr_schemes import lr_schemes, scheme_odds
from loto_enterprise.benchmark.methods import METHOD_LOAD_ERRORS, call_method
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import longest_consecutive_run, rank_by_score
from scripts.analysis.hit_evidence_2026_11_04 import (
    confidence_interval, holm, hypergeom_tail, paired_pvalue,
)
from scripts.analysis.lr_pool_study_2026_11_04 import exact_lookup, load_source
from scripts.analysis.lr_tail_objective_2026_11_05 import LRTailObjective

PROTOCOL_PATH = Path(__file__).with_name("lr_tail_2026_11_05_protocol.json")
PROTOCOL = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
CANDIDATES = tuple(PROTOCOL["candidates"])
WARMUP = PROTOCOL["warmup_rows"]
GATE_THRESHOLD = PROTOCOL["gated_lr_tail"]["threshold"]
RELATIVE_TOLERANCE = PROTOCOL["lr_tail"]["relative_tolerance"]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def elementary_symmetric(weights, degree):
    coefficients = np.zeros(degree + 1, dtype=float)
    coefficients[0] = 1.0
    for value in weights:
        # Slices evaluate the RHS first, so this is descending-order DP.
        coefficients[1:] += value * coefficients[:-1]
    return float(coefficients[degree])


def draw_log_likelihood(rows, weights):
    """Joint likelihood of a complete day's draws under its frozen expert."""
    rows = np.asarray(rows, dtype=np.int64)
    denominator = elementary_symmetric(weights, rows.shape[1])
    return float(np.log(weights)[rows - 1].sum() - len(rows) * math.log(denominator))


def gate_is_open(log_alt_bf, threshold=GATE_THRESHOLD):
    if not math.isfinite(float(log_alt_bf)):
        raise ValueError("Nonfinite predictive evidence")
    return bool(log_alt_bf >= math.log(threshold))


class PrequentialModels:
    """Two positive shrinkage experts, updated only by completed day blocks."""

    def __init__(self, universe, draw_n):
        self.universe, self.draw_n = universe, draw_n
        self.counts = np.zeros((2, universe), dtype=float)
        self.effective_draws = np.zeros(2, dtype=float)
        self.log_lr = np.zeros(2, dtype=float)
        self.completed_rows = self.completed_days = 0
        predictors = PROTOCOL["predictors"]
        self.alpha = predictors["pseudo_draws"] * draw_n / universe
        self.decay = 2.0 ** (-1.0 / predictors["ewma_half_life_completed_days"])
        self.log_priors = np.log(predictors["initial_model_prior"])

    def snapshot(self):
        weights = (self.counts + self.alpha) / (
            self.effective_draws[:, None] * self.draw_n / self.universe + self.alpha
        )
        logs = self.log_priors + np.r_[0.0, self.log_lr]
        posterior = np.exp(logs - logsumexp(logs))
        # The equal alternative priors cancel. Stable even when their full
        # posterior mass is too tiny to add to the uniform constant.
        conditional = np.exp(self.log_lr - logsumexp(self.log_lr))
        normalized = weights / weights.mean(axis=1, keepdims=True)
        scores = dict(enumerate(conditional @ normalized, start=1))
        return {
            "weights": weights.copy(), "posterior": posterior,
            "conditional": conditional, "scores": scores,
            "log_alt_bf": float(logsumexp(self.log_lr) - math.log(2)),
            "completed_rows": self.completed_rows,
            "completed_days": self.completed_days,
        }

    def complete_day(self, rows):
        rows = np.asarray(rows)
        if (rows.ndim != 2 or rows.shape[1] != self.draw_n or not len(rows)
                or not np.issubdtype(rows.dtype, np.integer)
                or np.any(rows < 1) or np.any(rows > self.universe)
                or any(len(set(row)) != self.draw_n for row in rows)):
            raise ValueError("Invalid complete-day draws")
        previous = self.snapshot()
        uniform_log = -len(rows) * math.log(math.comb(self.universe, self.draw_n))
        for model, weights in enumerate(previous["weights"]):
            self.log_lr[model] += draw_log_likelihood(rows, weights) - uniform_log
        increments = np.bincount(rows.ravel(), minlength=self.universe + 1)[1:]
        self.counts[0] += increments
        self.counts[1] *= self.decay
        self.counts[1] += increments
        self.effective_draws[0] += len(rows)
        self.effective_draws[1] = self.decay * self.effective_draws[1] + len(rows)
        self.completed_rows += len(rows)
        self.completed_days += 1
        if not np.all(np.isfinite(self.log_lr)):
            raise ValueError("Nonfinite prequential evidence")


def checked_pool(scores, pool_n, universe):
    if set(scores) != set(range(1, universe + 1)) or not all(
        math.isfinite(float(score)) for score in scores.values()
    ):
        raise ValueError("Incomplete or nonfinite scoring result")
    pool = tuple(select_pool_from_scores(
        scores, pool_n, set(), max_num=universe,
        max_consecutive_run=PROTOCOL["max_consecutive_run"],
    ))
    if len(pool) != pool_n or len(set(pool)) != pool_n or longest_consecutive_run(pool) > 2:
        raise ValueError("Canonical selection violated fixed pool constraints")
    return pool


def optimize_pool(initial, scores, objectives, conditional):
    """At most 48 fixed one-swap proposals; Pareto improvement of exact tails."""
    ranked = rank_by_score(scores, len(scores))

    def evaluate(pools):
        values = sum(
            weight * objective.evaluate_many(pools)
            for weight, objective in zip(conditional, objectives, strict=True)
        )
        if not np.all(np.isfinite(values)) or np.any(values < 0) or np.any(values > 1):
            raise ValueError("Invalid exact model probabilities")
        return values

    pool = tuple(initial)
    start = current = evaluate([pool])[0]
    accepted = proposals_evaluated = 0
    for _ in range(PROTOCOL["lr_tail"]["passes"]):
        weak = [n for n in reversed(ranked) if n in pool][:6]
        outside = [n for n in ranked if n not in pool][:4]
        proposals = sorted({
            tuple(sorted((set(pool) - {removed}) | {added}))
            for removed in weak for added in outside
            if longest_consecutive_run((set(pool) - {removed}) | {added}) <= 2
        })
        if not proposals:
            break
        values = evaluate(proposals)
        proposals_evaluated += len(proposals)
        tolerance = RELATIVE_TOLERANCE * np.maximum(current, np.finfo(float).tiny)
        admissible = [i for i, value in enumerate(values)
                      if np.all(value >= current - tolerance)
                      and np.any(value > current + tolerance)]
        if not admissible:
            break
        chosen = max(admissible, key=lambda i: (*values[i], proposals[i]))
        pool, current = proposals[chosen], values[chosen]
        accepted += 1
    return pool, {
        "start": start, "finish": current, "accepted_swaps": accepted,
        "proposals_evaluated": proposals_evaluated,
    }


def primary_mask(dates):
    """Final 30% of eligible complete dates; deterministic before outcomes."""
    unique = np.unique(dates)
    if not len(unique):
        raise ValueError("No eligible forecast dates")
    split_date = unique[int(math.floor(0.70 * len(unique)))]
    return dates >= split_date


def simulate(config, matrix, dates, cutoffs, scheme, *, warmup=WARMUP, progress=False):
    """Walk forward all days. Test fixtures can lower warmup, CLI cannot."""
    universe, draw_n, pool_n = (config[key] for key in ("universe", "draw_n", "pool_n"))
    matrix, dates, cutoffs = np.asarray(matrix), np.asarray(dates), np.asarray(cutoffs)
    if len(matrix) != len(dates) or len(matrix) != len(cutoffs) or not len(matrix):
        raise ValueError("Misaligned or empty history")
    expected_cutoffs = np.searchsorted(dates, dates, side="left")
    if np.any(dates[1:] < dates[:-1]) or not np.array_equal(cutoffs, expected_cutoffs):
        raise ValueError("History must be chronological with whole-day cutoffs")
    state = PrequentialModels(universe, draw_n)
    indices = np.flatnonzero(cutoffs >= warmup)
    if not len(indices):
        raise ValueError("No targets after the fixed warmup")
    n = len(indices)
    arrays = {
        "indices": indices, "dates": dates[indices].astype("datetime64[D]"),
        "pools": np.empty((n, len(CANDIDATES), pool_n), dtype=np.uint8),
        "pool_hits": np.empty((n, len(CANDIDATES)), dtype=np.uint8),
        "ticket_hits": np.empty((n, len(CANDIDATES)), dtype=np.uint8),
        "posterior": np.empty((n, 3)), "log_alt_bf": np.empty(n),
        "gate_open": np.empty(n, dtype=bool), "model_tail_start": np.empty((n, 2)),
        "model_tail_finish": np.empty((n, 2)),
        "accepted_swaps": np.empty(n, dtype=np.uint8),
        "proposals_evaluated": np.empty(n, dtype=np.uint8),
    }
    lookup = exact_lookup(pool_n, scheme.variants)
    started = time.perf_counter()
    forecast_seconds = 0.0
    eligible_days = target = 0
    for cut in np.unique(cutoffs):
        end = int(np.searchsorted(cutoffs, cut, side="right"))
        cut = int(cut)
        if state.completed_rows != cut:
            raise AssertionError("State is not the strictly preceding whole-day prefix")
        if cut >= warmup:
            tick = time.perf_counter()
            snapshot = state.snapshot()
            frequency, _ = call_method(
                "frequency", matrix[:cut], universe,
                history_cutoffs=tuple(map(int, cutoffs[:cut])),
            )
            baseline = checked_pool(frequency, pool_n, universe)
            topk = checked_pool(snapshot["scores"], pool_n, universe)
            objectives = [LRTailObjective(scheme, dict(enumerate(w, start=1)))
                          for w in snapshot["weights"]]
            tail, audit = optimize_pool(topk, snapshot["scores"], objectives,
                                        snapshot["conditional"])
            opened = gate_is_open(snapshot["log_alt_bf"])
            pools = (baseline, topk, tail, tail if opened else baseline)
            forecast_seconds += time.perf_counter() - tick
            count = end - cut
            destination = slice(target, target + count)
            arrays["pools"][destination] = pools
            arrays["posterior"][destination] = snapshot["posterior"]
            arrays["log_alt_bf"][destination] = snapshot["log_alt_bf"]
            arrays["gate_open"][destination] = opened
            for field, key in (("model_tail_start", "start"), ("model_tail_finish", "finish"),
                               ("accepted_swaps", "accepted_swaps"),
                               ("proposals_evaluated", "proposals_evaluated")):
                arrays[field][destination] = audit[key]
            for offset, row in enumerate(matrix[cut:end]):
                actual = set(map(int, row))
                for candidate, pool in enumerate(pools):
                    mask = sum(1 << p for p, number in enumerate(pool) if number in actual)
                    arrays["pool_hits"][target + offset, candidate] = mask.bit_count()
                    arrays["ticket_hits"][target + offset, candidate] = lookup[mask]
            target += count
            eligible_days += 1
            if progress and eligible_days % 200 == 0:
                print(f"{config['key']}: {target}/{n} targets, {time.perf_counter()-started:.1f}s", flush=True)
        # This is the only operation that sees the current day's outcomes for
        # forecasting, and it is deliberately after every current-day pool.
        state.complete_day(matrix[cut:end])
    if target != n:
        raise AssertionError("Incomplete target evaluation")
    arrays["primary"] = primary_mask(arrays["dates"])
    final = state.snapshot()
    metadata = {
        "game": config, "candidates": CANDIDATES, "history_rows": len(matrix),
        "target_rows": n, "eligible_days": eligible_days,
        "forecast_seconds": forecast_seconds, "elapsed_seconds": time.perf_counter() - started,
        "final_after_last_day_posterior": final["posterior"].tolist(),
        "final_after_last_day_log_alt_bf": final["log_alt_bf"],
    }
    return arrays, metadata


def code_fingerprints():
    paths = [Path(__file__), PROTOCOL_PATH,
             Path(__file__).with_name("lr_tail_objective_2026_11_05.py"),
             Path(__file__).with_name("lr_pool_study_2026_11_04.py"),
             Path(__file__).with_name("hit_evidence_2026_11_04.py"),
             ROOT / "covering/lr_schemes.py", ROOT / "covering/probability.py"]
    paths += sorted((ROOT / "loto_enterprise/benchmark").glob("methods*.py"))
    paths += [ROOT / "loto_enterprise/core" / name for name in
              ("ranking.py", "pool_selection.py", "history.py", "draw_validation.py")]
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()) for path in paths}


def evaluate_game(config, output_dir):
    if METHOD_LOAD_ERRORS:
        raise RuntimeError(f"Incomplete scorer registry: {METHOD_LOAD_ERRORS}")
    scheme = next(s for s in lr_schemes(config["label"], config["pool_n"])
                  if s.code == config["code"])
    if scheme.size != config["variants"]:
        raise ValueError("Official scheme variant count differs from frozen protocol")
    matrix, dates, cutoffs, history_hash = load_source(
        config["key"], config["universe"], config["draw_n"])
    signature = {
        "protocol_sha256": sha256(PROTOCOL_PATH.read_bytes()),
        "history_sha256": history_hash,
        "scheme_sha256": sha256(json.dumps(scheme.variants).encode()),
        "code_sha256": code_fingerprints(),
    }
    arrays, metadata = simulate(config, matrix, dates, cutoffs, scheme, progress=True)
    exact_uniform = scheme_odds(scheme)
    modeled_uniform = LRTailObjective(scheme, dict.fromkeys(range(1, config["universe"] + 1), 1.0))
    check = modeled_uniform.evaluate(range(1, config["pool_n"] + 1))
    for threshold, name in ((5, "p5plus"), (6, "p6")):
        if threshold in exact_uniform and not math.isclose(
            check[name], exact_uniform[threshold], rel_tol=1e-12, abs_tol=0.0
        ):
            raise AssertionError("Weighted objective disagrees with independent uniform scheme odds")
    metadata.update({"signature": signature, "uniform_ticket_odds": exact_uniform})
    if code_fingerprints() != signature["code_sha256"]:
        raise RuntimeError("Study code changed while evaluating; refusing mixed-version results")
    if sha256((ROOT / "_ISTORIC" / f"{config['key']}.csv").read_bytes()) != history_hash:
        raise RuntimeError("History changed while evaluating; refusing inconsistent results")
    output = Path(output_dir) / f"{config['key']}.npz"
    np.savez_compressed(output, metadata=json.dumps(metadata), **arrays)
    print(f"{config['key']}: complete {metadata['elapsed_seconds']:.2f}s", flush=True)
    return str(output)


def summarize(paths):
    report = {
        "study_id": PROTOCOL["study_id"], "protocol": PROTOCOL,
        "protocol_sha256": sha256(PROTOCOL_PATH.read_bytes()),
        "interpretation": "Exploratory reused archives; model optimization is not evidence of predictive lift. No production promotion.",
        "statistical_assumptions": {
            "uniform_comparison": "One-sided binomial test is exact under conditionally independent uniform draws: each predictable pool has the same fixed-size pool/scheme probability.",
            "paired_comparison": "Discordance-binomial p-values assume exchangeable win/loss signs conditional on their observed count. Adaptive history-dependent forecasts can violate that conditioning assumption; these paired p-values are exploratory diagnostics, not anytime-valid evidence.",
            "archive_reuse": "Chronological exclusion prevents target leakage; it does not remove prior research reuse or provide a fresh confirmatory holdout.",
        },
        "runtime": {"python": sys.version, "numpy": np.__version__, "scipy": scipy.__version__},
        "games": [], "primary_comparisons": [],
    }
    for path in paths:
        with np.load(path, allow_pickle=False) as saved:
            arrays = {key: saved[key].copy() for key in saved.files if key != "metadata"}
            metadata = json.loads(str(saved["metadata"]))
        config = metadata["game"]
        primary = arrays["primary"]
        if not np.any(primary):
            raise ValueError("Empty primary window")
        diagnostics = {
            "primary_rows": int(primary.sum()),
            "primary_start": str(arrays["dates"][primary][0]),
            "primary_end": str(arrays["dates"][primary][-1]),
            "gate_open_rows": int(arrays["gate_open"].sum()),
            "primary_gate_open_rows": int(arrays["gate_open"][primary].sum()),
            "maximum_prior_log_alt_bf": float(arrays["log_alt_bf"].max()),
            "minimum_prior_uniform_posterior": float(arrays["posterior"][:, 0].min()),
            "rows_with_accepted_swap": int((arrays["accepted_swaps"] > 0).sum()),
            "maximum_proposals_per_day": int(arrays["proposals_evaluated"].max()),
            "mean_relative_model_p5_gain": float(np.mean(
                arrays["model_tail_finish"][:, 0] / arrays["model_tail_start"][:, 0] - 1)),
            "first_forecast_posterior": arrays["posterior"][0].tolist(),
            "last_forecast_posterior": arrays["posterior"][-1].tolist(),
            "primary_start_posterior": arrays["posterior"][primary][0].tolist(),
        }
        summaries = []
        endpoints = [("pool", 5), ("ticket", 5)]
        if config["draw_n"] == 6:
            endpoints.append(("pool", 6))
        if config["pick"] == 6:
            endpoints.append(("ticket", 6))
        for domain, threshold in endpoints:
            hits = arrays[f"{domain}_hits"]
            events = hits >= threshold
            probability = (hypergeom_tail(config["universe"], config["draw_n"], config["pool_n"], threshold)
                           if domain == "pool" else metadata["uniform_ticket_odds"][str(threshold)])
            for candidate, name in enumerate(CANDIDATES):
                outcome = events[primary, candidate]
                total, successes = len(outcome), int(outcome.sum())
                row = {
                    "candidate": name, "endpoint": f"{domain}{threshold}+",
                    "primary_hits": successes, "primary_rows": total,
                    "primary_rate": successes / total,
                    "marginal_rate_ci95": list(confidence_interval(successes, total)),
                    "uniform_probability": probability,
                    "uniform_expected_hits": probability * total,
                    "full_history_hits": int(events[:, candidate].sum()),
                    "full_history_rows": len(events),
                }
                summaries.append(row)
                if candidate:
                    wins, losses, paired = paired_pvalue(outcome, events[primary, 0])
                    report["primary_comparisons"].append({
                        "game": config["key"], "candidate": name,
                        "endpoint": row["endpoint"], "comparator": "frequency",
                        "candidate_only": wins, "baseline_only": losses,
                        "p_one_sided": paired,
                    })
                    report["primary_comparisons"].append({
                        "game": config["key"], "candidate": name,
                        "endpoint": row["endpoint"], "comparator": "uniform_exact",
                        "p_one_sided": float(binomtest(successes, total, probability, alternative="greater").pvalue),
                    })
            tail, topk = events[primary, 2], events[primary, 1]
            diagnostics[f"tail_vs_topk_{domain}{threshold}+"] = {
                "tail_only": int(np.sum(tail & ~topk)),
                "topk_only": int(np.sum(topk & ~tail)),
            }
        report["games"].append({**metadata, "diagnostics": diagnostics,
                                "outcomes": summaries, "artifact_sha256": sha256(Path(path).read_bytes())})
    comparisons = report["primary_comparisons"]
    adjusted = holm([row["p_one_sided"] for row in comparisons])
    for row, p in zip(comparisons, adjusted, strict=True):
        row["p_holm_all_primary"] = float(p)
    report["holm_family_size"] = len(comparisons)
    report["significant_primary_comparisons"] = sum(row["p_holm_all_primary"] <= .05 for row in comparisons)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="/tmp/loto-tail-20261105")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # Preserve the exact protocol alongside results before any outcome run.
    (output / "protocol.json").write_bytes(PROTOCOL_PATH.read_bytes())
    started = time.perf_counter()
    if args.workers == 1:
        paths = [evaluate_game(config, output) for config in PROTOCOL["games"]]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(evaluate_game, config, output) for config in PROTOCOL["games"]]
            paths = [future.result() for future in futures]
    report = summarize(paths)
    report["wall_seconds"] = time.perf_counter() - started
    result = output / "summary.json"
    result.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(result), "wall_seconds": report["wall_seconds"],
                      "holm_family_size": report["holm_family_size"],
                      "significant": report["significant_primary_comparisons"]}), flush=True)


if __name__ == "__main__":
    main()
