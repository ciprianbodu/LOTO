"""Walk-forward cu 1.000 de configurații fixe, pe arhivele celor trei jocuri RO.

După minimum 250 de extrageri anterioare se rezervă ultimele 30% dintre
zilele eligibile. Restul se împarte în patru blocuri consecutive. Pe fiecare
bloc după primul se verifică alegerile făcute doar pe blocurile precedente.
Finaliștii se îngheață folosind toate cele patru blocuri, înainte de calculul
semnalelor pentru segmentul final. Arhivele au fost folosite în studii anterioare.

Pentru fiecare joc se aleg reguli comune și specialiști atât pentru variantele
schemei oficiale LR, cât și pentru numerele prinse în pool. Regula
comună maximizează media exceselor standardizate la hazard între praguri și
blocuri (varianță cu minim 1). Specialistul maximizează numărul de evenimente
la pragul său; egalitățile se rup prin scorul comun, apoi ID mai mic.
Familia de 30 de teste finale folosește binomiale exacte unilaterale față de
hazardul uniform și o singură corecție Holm (4+/5+/6+, după geometrie).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import scipy
from scipy.stats import binom

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from covering.lr_schemes import scheme_odds
from covering.lr_selection import lr_scheme_signature, resolve_lr_scheme
from scripts.analysis.hit_evidence_2026_11_04 import (
    confidence_interval,
    holm,
    hypergeom_tail,
)
from scripts.analysis.lr_pool_study_2026_11_04 import exact_lookup, load_source
from scripts.analysis.tail_joker_1000 import joker_matches
from scripts.analysis.tail_pools_1000 import pool_numbers
from scripts.analysis.tail_references_1000 import reference_pools
from scripts.analysis.legacy_scoring.dynamic_bank import RECIPE_SEED as SEED
from scripts.analysis.legacy_scoring.dynamic_bank import SIGNAL_NAMES, configurations, signal_ranks

WARMUP = 250
FOLDS = 4
# Official systems confirmed for play and selected at application startup.
GAMES = (
    ("loto_6_49", "6/49", 49, 6, 6, 9, 48),
    ("loto_5_40", "5/40", 40, 6, 5, 8, 16),
    ("joker", "joker", 45, 5, 5, 10, 24),
)


def pools_for_recipe(recipe, ranks, size, fallback_pools=None):
    scores = np.zeros_like(ranks[:, 0])
    for signal, weight in recipe:
        scores += weight * ranks[:, signal]
    pools = pool_numbers(scores, size)
    # On an uninformative blend reproduce the app's frequency fallback.
    flat = np.ptp(scores, axis=1) == 0
    if flat.any():
        if fallback_pools is None:
            raise ValueError("Missing registered frequency fallback for flat scores")
        pools[flat] = fallback_pools[flat]
    return pools, int(flat.sum())


def outcome_hits(pools, indicator, lookup):
    matched = np.take_along_axis(indicator, pools.astype(int) - 1, axis=1)
    positional_weights = 1 << np.arange(pools.shape[1])
    masks = matched @ positional_weights
    return matched.sum(axis=1), lookup[masks]


def threshold_counts(hits, thresholds):
    return np.array([np.count_nonzero(hits >= t) for t in thresholds], dtype=int)


def as_counts(hits, thresholds):
    return {
        str(t): int(c)
        for t, c in zip(thresholds, threshold_counts(hits, thresholds), strict=True)
    }


def iso(date):
    return str(np.datetime64(date, "D"))


def window(indices, dates):
    return {
        "first_date": iso(dates[indices[0]]),
        "last_date": iso(dates[indices[-1]]),
        "draws": len(indices),
        "days": len(np.unique(dates[indices])),
    }


def periods(cutoffs):
    eligible = np.flatnonzero(cutoffs >= WARMUP)
    days = np.unique(cutoffs[eligible])
    if len(days) < 10:
        raise ValueError("Insufficient dates for four selection blocks and holdout")
    boundary = len(days) * 7 // 10
    training = eligible[cutoffs[eligible] < days[boundary]]
    day_blocks = np.array_split(days[:boundary], FOLDS)
    splits = [np.searchsorted(training, block[0]) for block in day_blocks[1:]]
    return np.split(training, splits), eligible[cutoffs[eligible] >= days[boundary]]


def select_finalists(counts, lengths, probabilities, pool_targets, ticket_targets, ids):
    # counts: configurations × chronological folds × endpoints (pool then LR).
    counts = np.asarray(counts)
    lengths = np.asarray(lengths)[:, None]
    expected = lengths * probabilities
    scale = np.sqrt(np.maximum(1.0, expected * (1 - probabilities)))
    standardized = (counts - expected) / scale
    pool_balanced = standardized[..., : len(pool_targets)].mean(axis=(1, 2))
    ticket_balanced = standardized[..., len(pool_targets) :].mean(axis=(1, 2))
    total = counts.sum(axis=1)
    results = {
        "balanced": ids[int(np.argmax(pool_balanced))],
        "ticket_balanced": ids[int(np.argmax(ticket_balanced))],
    }
    labels = [f"ge{t}" for t in pool_targets] + [
        f"ticket_ge{t}" for t in ticket_targets
    ]
    for j, name in enumerate(labels):
        best = np.flatnonzero(total[:, j] == total[:, j].max())
        balanced = ticket_balanced if name.startswith("ticket_") else pool_balanced
        results[name] = ids[best[int(np.argmax(balanced[best]))]]
    return results


def run_game(game, recipes, writer):
    key, label, universe, draw_n, pick_n, size, code = game
    draws, dates, cutoffs, digest = load_source(key, universe, draw_n)
    scheme = resolve_lr_scheme(label, code, size)
    lookup = exact_lookup(size, scheme.variants)
    pool_targets = list(range(4, draw_n + 1))
    ticket_targets = list(range(4, pick_n + 1))
    p_pool = np.array([hypergeom_tail(universe, draw_n, size, t) for t in pool_targets])
    odds = scheme_odds(scheme)
    p_ticket = {str(t): odds[t] for t in ticket_targets}
    uniform_probabilities = np.r_[p_pool, list(p_ticket.values())]
    blocks, final = periods(cutoffs)
    training = np.concatenate(blocks)
    sizes = [len(b) for b in blocks]
    edges = np.cumsum([0, *sizes])
    indicators = np.zeros((len(draws), universe), dtype=np.uint8)
    indicators[np.arange(len(draws))[:, None], draws - 1] = 1
    # The grid search receives a physically truncated history.
    names, features = signal_ranks(indicators[: final[0]], universe)
    if tuple(names) != SIGNAL_NAMES:
        raise ValueError("Unexpected signal order")
    features = features[cutoffs[training]]
    actual = indicators[training]
    fallback = reference_pools(
        key, draws, cutoffs, training, size, universe, methods=("frequency",)
    )["frequency"]
    counts = np.empty(
        (len(recipes), FOLDS, len(pool_targets) + len(ticket_targets)), dtype=int
    )
    signatures = set()
    flat_total = 0
    for i, (config, recipe) in enumerate(recipes.items()):
        pools, flat = pools_for_recipe(recipe, features, size, fallback)
        flat_total += flat
        signatures.add(hashlib.sha256(pools.tobytes()).digest())
        pool_hits, ticket_hits = outcome_hits(pools, actual, lookup)
        for j in range(FOLDS):
            part = slice(edges[j], edges[j + 1])
            pool_counts = as_counts(pool_hits[part], pool_targets)
            ticket_counts = as_counts(ticket_hits[part], ticket_targets)
            counts[i, j] = [*pool_counts.values(), *ticket_counts.values()]
            writer.writerow(
                [
                    key,
                    config,
                    j + 1,
                    sizes[j],
                    *[pool_counts.get(str(t), "") for t in (4, 5, 6)],
                    *[ticket_counts.get(str(t), "") for t in (4, 5, 6)],
                ]
            )
    ids = list(recipes)
    labels = [f"ge{t}" for t in pool_targets] + [
        f"ticket_ge{t}" for t in ticket_targets
    ]
    nested = []
    for j in range(1, FOLDS):
        chosen = select_finalists(
            counts[:, :j],
            sizes[:j],
            uniform_probabilities,
            pool_targets,
            ticket_targets,
            ids,
        )
        record = {}
        for strategy, config in chosen.items():
            values = counts[ids.index(config), j].tolist()
            record[strategy] = {
                "config": config,
                "counts": dict(zip(labels, values, strict=True)),
            }
        nested.append(
            {"selection_blocks": j, "evaluated_block": j + 1, "strategies": record}
        )
    finalists = select_finalists(
        counts, sizes, uniform_probabilities, pool_targets, ticket_targets, ids
    )
    print(f"{key}: 1000 configurations, finalists locked: {finalists}", flush=True)

    refs = reference_pools(key, draws, cutoffs, final, size, universe)
    _, final_features = signal_ranks(indicators, universe)
    final_features = final_features[cutoffs[final]]
    pools_by_config = {
        k: pools_for_recipe(recipes[k], final_features, size, refs["frequency"])
        for k in sorted(set(finalists.values()))
    }
    forecast = {k: pools_by_config[c][0] for k, c in finalists.items()}
    forecast.update(refs)
    strategies = {}
    tests = []
    bonus = joker_matches(dates, cutoffs, final) if key == "joker" else None
    outcomes = {
        name: outcome_hits(pools, indicators[final], lookup)
        for name, pools in forecast.items()
    }
    for name, (pool_hits, ticket_hits) in outcomes.items():
        strategies[name] = {
            "pool": as_counts(pool_hits, pool_targets),
            "ticket": as_counts(ticket_hits, ticket_targets),
        }
        if bonus is not None:
            strategies[name]["ticket_with_joker"] = as_counts(
                ticket_hits[bonus], ticket_targets
            )
        if name not in finalists:
            continue
        metric = "ticket" if name.startswith("ticket_") else "pool"
        all_targets = ticket_targets if metric == "ticket" else pool_targets
        targets = all_targets if name.endswith("balanced") else [int(name[-1])]
        probabilities = (
            p_ticket
            if metric == "ticket"
            else dict(zip(map(str, pool_targets), p_pool, strict=True))
        )
        for t in targets:
            p0 = probabilities[str(t)]
            observed = strategies[name][metric][str(t)]
            event = (ticket_hits if metric == "ticket" else pool_hits) >= t
            paired = {}
            for ref_name in refs:
                baseline = outcomes[ref_name][1 if metric == "ticket" else 0] >= t
                paired[ref_name] = {
                    "gains": int(np.count_nonzero(event & ~baseline)),
                    "losses": int(np.count_nonzero(~event & baseline)),
                }
            tests.append(
                {
                    "paired_vs_references": paired,
                    "game": key,
                    "strategy": name,
                    "config": finalists[name],
                    "metric": metric,
                    "threshold": t,
                    "n": len(final),
                    "events": observed,
                    "uniform_expected": len(final) * p0,
                    "rate_95pct_marginal": confidence_interval(observed, len(final)),
                    "p": float(binom.sf(observed - 1, len(final), p0)),
                }
            )
    return {
        "history": {**window(np.arange(len(draws)), dates), "sha256": digest},
        "pool_size": size,
        "lr_code": code,
        "lr_tickets": len(scheme.variants),
        "lr_signature": lr_scheme_signature(label, code, size),
        "pool_uniform_p": dict(zip(map(str, pool_targets), p_pool, strict=True)),
        "ticket_uniform_p": p_ticket,
        "selection_blocks": [window(b, dates) for b in blocks],
        "selection_draws": len(training),
        "unique_selection_pool_trajectories": len(signatures),
        "selection_flat_fallbacks": flat_total,
        "nested": nested,
        "finalists": finalists,
        "holdout": {
            **window(final, dates),
            "strategies": strategies,
            **({"correct_joker_draws": int(bonus.sum())} if bonus is not None else {}),
            "flat_fallbacks_by_config": {k: v[1] for k, v in pools_by_config.items()},
        },
    }, tests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    recipes = configurations()
    report = {
        "protocol": {
            "seed": SEED,
            "warmup_prior_draws": WARMUP,
            "max_consecutive_run": 2,
            "selection_share_days": 0.7,
            "selection_blocks": FOLDS,
            "tie_break": "larger number first",
            "alpha_Holm": 0.05,
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
        "configs": {k: {SIGNAL_NAMES[i]: w for i, w in r} for k, r in recipes.items()},
        "games": {},
        "tests": [],
    }
    with TemporaryDirectory(dir=args.out_dir) as tmp:
        output = Path(tmp)
        write_results(output, recipes, report)
        for name in ("tail_1000_folds.csv", "tail_1000_results.json"):
            (output / name).replace(args.out_dir / name)
    print(
        f"{len(report['tests'])} final comparisons written in {args.out_dir}",
        flush=True,
    )


def write_results(output, recipes, report):
    folds = output / "tail_1000_folds.csv"
    with folds.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "game",
                "config",
                "fold",
                "draws",
                "pool_ge4",
                "pool_ge5",
                "pool_ge6",
                "ticket_ge4",
                "ticket_ge5",
                "ticket_ge6",
            ]
        )
        for game in GAMES:
            data, tests = run_game(game, recipes, writer)
            report["games"][game[0]] = data
            report["tests"].extend(tests)
    for row, adjusted in zip(
        report["tests"], holm([r["p"] for r in report["tests"]]), strict=True
    ):
        row["p_holm"] = adjusted
    report["folds_sha256"] = hashlib.sha256(folds.read_bytes()).hexdigest()
    (output / "tail_1000_results.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
