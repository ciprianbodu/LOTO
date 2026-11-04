"""Exploratory nested study of all registered scorers on three official LR schemes.

The fixed protocol is defined below before evaluating outcomes. Histories have
already been reused in earlier research. Chronological nesting prevents future
information entering a simulated choice; it does not create an untouched study.
No production decisions, exclusions, schemes, histories or settings are written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# Set before numpy/scipy import, including Windows spawn workers.
for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_variable] = "1"

import numpy as np
import pandas as pd
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from covering.lr_schemes import lr_schemes, scheme_odds
from loto_enterprise.benchmark.methods import METHODS, METHOD_LOAD_ERRORS, call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import chronological_history, history_dates, training_cutoffs
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.score_validation import has_usable_score_variance
from scripts.analysis.hit_evidence_2026_11_04 import (
    confidence_interval,
    holm,
    hypergeom_tail,
    paired_pvalue,
)

GAMES = (
    ("loto_6_49", "6/49", 49, 6, 6, 16, 59),
    ("joker", "joker", 45, 5, 5, 15, 12),
    ("loto_5_40", "5/40", 40, 6, 5, 19, 48),
)
WARMUP = 250
MODES = (2, 0)  # Production default, then raw canonical sensitivity.
MEMORIES = (0, 200)  # 0=all past; first wins a validation tie.
OUTER_MINIMUM = 400
OUTER_BLOCK = 100
INNER_VALIDATION = 200
INNER_BLOCK = 50
CACHE_SEMANTICS = "lr53_nested1"


def exact_lookup(n, variants):
    """Best ticket hits for every positional intersection; integer subset DP."""
    masks = np.array(
        [sum(1 << (p - 1) for p in row) for row in variants], dtype=np.int64
    )
    family = np.zeros(1 << n, dtype=bool)
    family[masks] = True
    for position in range(n):
        view = family.reshape(-1, 2, 1 << position)
        view[:, 0, :] |= view[:, 1, :]
    best = np.bitwise_count(np.arange(1 << n, dtype=np.uint32)) * family
    for position in range(n):
        view = best.reshape(-1, 2, 1 << position)
        np.maximum(view[:, 1, :], view[:, 0, :], out=view[:, 1, :])
    return best


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def load_source(game, universe, draw_n):
    path = ROOT / "_ISTORIC" / f"{game}.csv"
    frame = chronological_history(pd.read_csv(path)).reset_index(drop=True)
    matrix, valid = valid_draw_matrix(
        frame, [f"n{i}" for i in range(1, draw_n + 1)],
        draw_n=draw_n, max_num=universe,
    )
    if not bool(np.all(valid)):
        raise ValueError(f"Invalid history rows in {path}; refusing partial results")
    dates = history_dates(frame).to_numpy()
    cutoffs = np.array(training_cutoffs(frame), dtype=np.int64)
    return matrix, dates, cutoffs, _hash(path.read_bytes())


def cache_signature(game, history_hash, variants):
    digest = hashlib.sha256()
    parts = sorted((ROOT / "loto_enterprise/benchmark").glob("methods*.py"))
    parts += [ROOT / "loto_enterprise/core" / name for name in (
        "ranking.py", "pool_selection.py", "score_validation.py", "history.py",
    )]
    for path in parts:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return {
        "semantics": CACHE_SEMANTICS,
        "game": game,
        "history_sha256": history_hash,
        "scoring_code_sha256": digest.hexdigest(),
        "variants_sha256": _hash(json.dumps(variants).encode("utf-8")),
        "methods": sorted(METHODS),
        "warmup": WARMUP,
        "consecutive_limits": list(MODES),
    }


def safe_scores(name, history, universe, day_cutoffs, frequency, caller=call_method):
    """Attempt every candidate; invalid output is visibly replaced by frequency."""
    reason = None
    try:
        scores, elapsed = caller(
            name, history, universe, history_cutoffs=day_cutoffs
        )
        if not has_usable_score_variance(scores):
            reason = "unusable_score_variance"
        else:
            normalized = {int(n): float(s) for n, s in scores.items()}
            if any(n < 1 or n > universe for n in normalized):
                reason = "number_out_of_range"
            elif len(normalized) != universe:
                reason = "incomplete_score_universe"
            else:
                return normalized, None, elapsed
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    if frequency is None or not has_usable_score_variance(frequency):
        raise RuntimeError(f"No usable frequency fallback after {name}: {reason}")
    return frequency, reason, 0.0


def evaluate_game(config, cache_dir):
    if METHOD_LOAD_ERRORS:
        raise RuntimeError(f"Incomplete scorer registry: {METHOD_LOAD_ERRORS}")
    started = time.perf_counter()
    game, label, universe, draw_n, pick, pool_n, code = config
    scheme = next(s for s in lr_schemes(label, pool_n) if s.code == code)
    matrix, dates, cutoffs, history_hash = load_source(game, universe, draw_n)
    signature = cache_signature(game, history_hash, scheme.variants)
    cache = Path(cache_dir) / f"{game}.npz"
    if cache.exists():
        with np.load(cache, allow_pickle=False) as saved:
            metadata = json.loads(str(saved["metadata"]))
            if metadata["signature"] == signature:
                print(f"{game}: validated isolated cache", flush=True)
                return {**metadata, **{
                    key: saved[key].copy() for key in
                    ("pool_hits", "ticket_hits", "indices", "dates", "fallback")
                }}
    methods = sorted(METHODS)
    indices = np.flatnonzero(cutoffs >= WARMUP)
    lookup = exact_lookup(pool_n, scheme.variants)
    shape = (len(MODES), len(indices), len(methods))
    pool_hits = np.empty(shape, dtype=np.uint8)
    ticket_hits = np.empty(shape, dtype=np.uint8)
    fallback = np.zeros((len(indices), len(methods)), dtype=bool)
    failures = {name: Counter() for name in methods}
    scoring_seconds = Counter()
    score_calls = Counter()
    last_cut = None
    selected = {}
    reasons = {}
    for t, source_index in enumerate(indices):
        cut = int(cutoffs[source_index])
        if cut != last_cut:
            history = matrix[:cut]
            day_cutoffs = tuple(map(int, cutoffs[:cut]))
            frequency, dt = call_method(
                "frequency", history, universe, history_cutoffs=day_cutoffs
            )
            if not has_usable_score_variance(frequency):
                raise RuntimeError("Registered frequency fallback is unusable")
            scoring_seconds["frequency"] += dt
            score_calls["frequency"] += 1
            for name in methods:
                if name == "frequency":
                    scores, reason = frequency, None
                else:
                    scores, reason, dt = safe_scores(
                        name, history, universe, day_cutoffs, frequency
                    )
                    scoring_seconds[name] += dt
                    score_calls[name] += 1
                pools = []
                try:
                    for limit in MODES:
                        pool = select_pool_from_scores(
                            scores, pool_n, set(), max_num=universe,
                            max_consecutive_run=limit,
                        )
                        if len(pool) != pool_n:
                            raise ValueError("Selected pool has the wrong size")
                        pools.append(pool)
                except Exception as exc:
                    reason = f"selection_{type(exc).__name__}: {exc}"
                    pools = [select_pool_from_scores(
                        frequency, pool_n, set(), max_num=universe,
                        max_consecutive_run=limit,
                    ) for limit in MODES]
                selected[name], reasons[name] = pools, reason
            last_cut = cut
        actual = set(map(int, matrix[source_index]))
        for m, name in enumerate(methods):
            reason = reasons[name]
            fallback[t, m] = reason is not None
            if reason is not None:
                failures[name][reason] += 1
            for mode, pool in enumerate(selected[name]):
                # select_pool_from_scores returns numeric ascending order,
                # exactly the terminal mapping used by scheme_tickets.
                mask = sum(1 << p for p, number in enumerate(pool) if number in actual)
                pool_hits[mode, t, m] = mask.bit_count()
                ticket_hits[mode, t, m] = lookup[mask]
        if (t + 1) % 200 == 0:
            print(f"{game}: {t + 1}/{len(indices)} days/rows, "
                  f"{time.perf_counter() - started:.0f}s", flush=True)
    metadata = {
        "signature": signature,
        "configuration": list(config),
        "scheme_variants": scheme.size,
        "scheme_uniform_odds": scheme_odds(scheme),
        "total_source_draws": len(matrix),
        "evaluated_draws": len(indices),
        "failure_reasons": {name: dict(counts) for name, counts in failures.items()},
        "scoring_seconds": dict(scoring_seconds),
        "score_calls": dict(score_calls),
        "seconds": time.perf_counter() - started,
    }
    arrays = dict(
        pool_hits=pool_hits, ticket_hits=ticket_hits, indices=indices,
        dates=dates[indices].astype("datetime64[D]").astype(np.int64),
        fallback=fallback,
    )
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(cache.name + f".{os.getpid()}.tmp")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, metadata=json.dumps(metadata), **arrays)
    os.replace(temporary, cache)
    print(f"{game}: completed {len(indices)} x {len(methods)}", flush=True)
    return {**metadata, **arrays}


def day_start(dates, index):
    if index >= len(dates):
        return len(dates)
    return int(np.searchsorted(dates, dates[max(0, index)], side="left"))


def block_end(dates, start, size):
    end = min(len(dates), start + size)
    if end < len(dates) and end > start and dates[end] == dates[end - 1]:
        end = int(np.searchsorted(dates, dates[end], side="right"))
    return end


def objective_prefix(outcomes):
    events = outcomes[:, :, None] >= np.array([4, 5, 6])[None, None, :]
    return np.concatenate((
        np.zeros((1, outcomes.shape[1], 3), dtype=np.int64),
        np.cumsum(events, axis=0, dtype=np.int64),
    ))


def best_method(prefix, dates, end, memory):
    if end != day_start(dates, end):
        raise ValueError("Selection would see another draw on the target date")
    start = day_start(dates, max(0, end - memory)) if memory else 0
    if end <= start:
        raise ValueError("No past observations for method selection")
    totals = prefix[end] - prefix[start]
    return max(range(len(totals)), key=lambda m: (*map(int, totals[m]), -m))


def nested_choices(outcomes, dates):
    """Tune memory only on inner forward folds, then freeze each outer block."""
    prefix = objective_prefix(outcomes)
    choices = np.full(len(dates), -1, dtype=np.int16)
    memories = np.full(len(dates), -1, dtype=np.int16)
    blocks = []
    start = block_end(dates, 0, OUTER_MINIMUM)
    while start < len(dates):
        validation_start = day_start(dates, start - INNER_VALIDATION)
        evaluations = []
        for memory in MEMORIES:
            totals = np.zeros(3, dtype=np.int64)
            inner = validation_start
            while inner < start:
                method = best_method(prefix, dates, inner, memory)
                end = min(start, block_end(dates, inner, INNER_BLOCK))
                totals += prefix[end, method] - prefix[inner, method]
                inner = end
            evaluations.append(tuple(map(int, totals)))
        # MEMORIES is ordered all-past first, the fixed tie rule.
        chosen_index = max(range(len(MEMORIES)), key=lambda i: (evaluations[i], -i))
        memory = MEMORIES[chosen_index]
        method = best_method(prefix, dates, start, memory)
        end = block_end(dates, start, OUTER_BLOCK)
        choices[start:end] = method
        memories[start:end] = memory
        blocks.append({
            "start": start, "end": end, "memory": memory, "method_index": method,
            "inner_start": validation_start,
            "inner_validation_scores": [list(values) for values in evaluations],
        })
        start = end
    return choices, memories, blocks


def result_row(game, mode, candidate, outcome, target, values, reference, p0, period):
    event = np.asarray(values) >= target
    baseline = np.asarray(reference) >= target
    successes, total = int(event.sum()), len(event)
    wins, losses, paired = paired_pvalue(event, baseline)
    return {
        "game": game, "max_consecutive_run": mode, "candidate": candidate,
        "outcome": outcome, "threshold": target, "period": period,
        "n": total, "successes": successes, "rate": successes / total,
        "uniform_probability": p0, "expected_successes": total * p0,
        "interval_95pct_marginal": confidence_interval(successes, total),
        "wins_vs_frequency": wins, "losses_vs_frequency": losses,
        "p_binomial": float(binomtest(successes, total, p0, alternative="greater").pvalue),
        "p_paired": paired,
    }


def summarize(evaluations):
    rows, games = [], []
    for data in evaluations:
        config = data["configuration"]
        game, label, universe, draw_n, pick, pool_n, code = config
        methods = data["signature"]["methods"]
        frequency = methods.index("frequency")
        dates = data["dates"]
        cut = block_end(dates, 0, int(len(dates) * 0.7))
        recent = np.arange(cut, len(dates))
        schemes = {int(k): v for k, v in data["scheme_uniform_odds"].items()}
        metadata = {key: value for key, value in data.items() if not isinstance(value, np.ndarray)}
        metadata["recent_period"] = {
            "start_date": str(np.datetime64(int(dates[cut]), "D")),
            "end_date": str(np.datetime64(int(dates[-1]), "D")),
            "draws": len(recent),
        }
        metadata["fallback_counts"] = {
            name: int(data["fallback"][:, m].sum()) for m, name in enumerate(methods)
        }
        metadata["nested"] = []
        for mode_index, mode in enumerate(MODES):
            for outcome, maximum in (("pool", draw_n), ("ticket", pick)):
                values = data[outcome + "_hits"][mode_index]
                for method, name in enumerate(methods):
                    for threshold in range(4, maximum + 1):
                        p0 = (hypergeom_tail(universe, draw_n, pool_n, threshold)
                              if outcome == "pool" else schemes[threshold])
                        rows.append(result_row(
                            game, mode, name, outcome, threshold,
                            values[recent, method], values[recent, frequency], p0,
                            "recent_30pct_all_methods",
                        ))
            for objective in ("pool", "ticket"):
                choices, memories, blocks = nested_choices(
                    data[objective + "_hits"][mode_index], dates
                )
                valid = np.flatnonzero(choices >= 0)
                valid_recent = valid[valid >= cut]
                for block in blocks:
                    block["method"] = methods[block["method_index"]]
                    block["start_date"] = str(np.datetime64(int(dates[block["start"]]), "D"))
                    block["last_date"] = str(np.datetime64(int(dates[block["end"] - 1]), "D"))
                records = []
                for t in valid:
                    method = int(choices[t])
                    records.append({
                        "date": str(np.datetime64(int(dates[t]), "D")),
                        "source_row_index": int(data["indices"][t]),
                        "method": methods[method], "memory": int(memories[t]),
                        "used_frequency_fallback": bool(data["fallback"][t, method]),
                        "pool_hits": int(data["pool_hits"][mode_index, t, method]),
                        "ticket_hits": int(data["ticket_hits"][mode_index, t, method]),
                        "frequency_pool_hits": int(data["pool_hits"][mode_index, t, frequency]),
                        "frequency_ticket_hits": int(data["ticket_hits"][mode_index, t, frequency]),
                    })
                metadata["nested"].append({
                    "objective": objective, "max_consecutive_run": mode,
                    "blocks": blocks, "predictions": records,
                    "evaluated_draws": len(valid), "recent_draws": len(valid_recent),
                })
                for outcome, maximum in (("pool", draw_n), ("ticket", pick)):
                    values = data[outcome + "_hits"][mode_index]
                    selected = values[valid_recent, choices[valid_recent]]
                    for threshold in range(4, maximum + 1):
                        p0 = (hypergeom_tail(universe, draw_n, pool_n, threshold)
                              if outcome == "pool" else schemes[threshold])
                        rows.append(result_row(
                            game, mode, "nested_" + objective, outcome, threshold,
                            selected, values[valid_recent, frequency], p0,
                            "recent_30pct_nested_outer_predictions",
                        ))
        games.append(metadata)
    adjusted = holm([r[k] for r in rows for k in ("p_binomial", "p_paired")])
    for i, row in enumerate(rows):
        row["p_binomial_holm"] = adjusted[2 * i]
        row["p_paired_holm"] = adjusted[2 * i + 1]
    return {
        "protocol": {
            "status": "exploratory_reused_archive_no_untouched_holdout",
            "games": [list(config) for config in GAMES],
            "registered_methods": len(METHODS), "method_exclusions": [],
            "baseline": "registered frequency: exp(linspace(-2,0,n)), not cumulative counts",
            "scorer_call": "call_method with prior-date prefix and aligned history_cutoffs",
            "fallback": "invalid scorer/selection visibly falls back to registered frequency",
            "warmup_draws": WARMUP, "pool_modes": list(MODES),
            "primary_mode": "max_consecutive_run=2, current application default",
            "other_settings": "full history, no blacklist, no base restriction, no recent penalty",
            "mapping": "official LR positions map to pool numbers in ascending numeric order",
            "targets": "4+, then 5+, then 6 where meaningful; pool and ticket reported separately",
            "outer_minimum": OUTER_MINIMUM, "outer_block": OUTER_BLOCK,
            "inner_validation_rows": INNER_VALIDATION, "inner_block": INNER_BLOCK,
            "memory_candidates": list(MEMORIES),
            "tie_rules": "method alphabetic; all-past memory before200; fixed before results",
            "same_day": "score prefixes, training windows and validation boundaries exclude target day",
            "comparison": "nested LR-ticket objective versus nested pool objective, frequency, random",
            "statistics": "one-sided binomial versus exact uniform scheme/pool probability; exploratory paired diagnostic versus frequency",
            "holm_family": "all53 methods plus2 nested rules, both modes, all3 games, both outcomes and all feasible4/5/6 thresholds, both tests",
            "statistical_tests": len(adjusted),
            "intervals": "95% marginal Clopper-Pearson; not simultaneous or selection-adjusted",
            "paired_assumption": "exchangeable discordant pairs; diagnostic, not a prospective test",
            "joker": "Urna1 only; Joker-ball categories/prizes are not modeled",
            "540": "six drawn main numbers; five per variant, maximum five ticket hits",
            "production_decision_writes": 0, "production_promotions": 0,
        },
        "games": games, "rows": rows,
        "summary": {
            "cells": len(rows), "tests": len(adjusted),
            "both_holm_below_005": sum(r["p_binomial_holm"] < .05 and r["p_paired_holm"] < .05 for r in rows),
            "min_binomial_holm": min(r["p_binomial_holm"] for r in rows),
            "min_paired_holm": min(r["p_paired_holm"] for r in rows),
        },
    }


def write_markdown(report, path):
    lines = [
        "# Studiu pe schemele LR cerute — 4 noiembrie 2026", "",
        "Toate cele 53 de metode înregistrate au fost evaluate pe Cod 59 / pool 16 la 6/49, Cod 12 / pool 15 Joker Urna 1 și Cod 48 / pool 19 la 5/40. Schemele au 112, 118 și 930 de variante. Nicio decizie sau metodă de producție nu a fost modificată.", "",
        "Protocolul este exploratoriu: arhivele au fost reutilizate anterior. Selecția nested vede numai trecutul fiecărei extrageri; acest lucru nu transformă arhiva într-un lot extern neatins. Referința frequency este metoda efectivă din registry, cu ponderare exponențială.", "",
        "Tabelul arată extragerile din ultimele 30% cu cel puțin pragul indicat pe o variantă LR; sunt numărători descriptive, la cost identic pe joc. Principal: limita de consecutive 2, ca în setările implicite. Sensibilitatea fără limită este păstrată integral în JSON.", "",
        "| Joc | Regulă | Extrageri | 4+ | 5+ | 6 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for game, *_ in GAMES:
        for candidate in ("frequency", "random", "nested_pool", "nested_ticket"):
            values = [r for r in report["rows"] if r["game"] == game and r["max_consecutive_run"] == 2 and r["candidate"] == candidate and r["outcome"] == "ticket"]
            counts = {r["threshold"]: r["successes"] for r in values}
            lines.append(f"| {game} | {candidate} | {values[0]['n']} | {counts.get(4, '—')} | {counts.get(5, '—')} | {counts.get(6, '—')} |")
    lines += ["", f"Corecția Holm cuprinde toate cele {report['summary']['tests']} teste. Celule care trec ambele comparații ajustate: {report['summary']['both_holm_below_005']}. Se publică toate metodele, inclusiv erorile și rezervele frequency; nu se promovează câștigătorul retrospectiv.", "",
        "Selecția externă se îngheață pe blocuri de 100 de extrageri după minimum 400 de predicții anterioare. Memoria 200 sau întregul trecut se alege în interiorul trecutului prin patru blocuri cronologice de 50 pe ultimele 200 de predicții. Scorul de selecție este lexicografic: 4+, apoi 5+ și 6; egalitățile au reguli fixe. Limitele respectă zilele cu extrageri multiple.", "",
        "La 6/49, 112 variante distincte au probabilitate jackpot 112/C(49,6), indiferent de metodă pe extrageri uniforme. La 5/40, șase hituri în pool nu pot deveni șase pe o variantă de cinci numere. Joker Urna 2 și premiile nu sunt incluse.", "",
        "Baseline-urile exacte și testele folosesc formulele din [SciPy hypergeom](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.hypergeom.html) și [SciPy binomtest](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html). JSON-ul păstrează amprentele surselor și scorerilor, configurațiile, fiecare alegere nested, ratele și intervalele marginale.", "",
        "Reproducere: `python scripts/analysis/lr_pool_study_2026_11_04.py --cache-dir <runtime_izolat> --workers 3`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_suffix(".json"))
    args = parser.parse_args()
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=min(3, max(1, args.workers))) as executor:
        futures = [executor.submit(evaluate_game, config, args.cache_dir) for config in GAMES]
        results = [future.result() for future in futures]
    report = summarize(results)
    report["seconds_including_cache_read"] = time.perf_counter() - started
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, args.output.with_suffix(".md"))
    print(json.dumps(report["summary"], indent=2), flush=True)
    print(f"Saved {args.output}; {time.perf_counter() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
