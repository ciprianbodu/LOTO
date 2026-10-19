"""Reproducible retrospective pool9..16 search against markov_lag2.

Run --register BEFORE --run. Production configuration is never written.
All scores are computed on prefixes ending strictly before the target day.
"""

from __future__ import annotations

import os

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
from loto_enterprise.benchmark.methods import METHODS
from loto_enterprise.core.pool_selection import apply_consecutive_limit
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance

BASELINE = "markov_lag2"
KS = tuple(range(9, 17))
SEED = 20261019
WARMUP = 256
EXPERTS = (
    "frequency",
    "markov_pairs",
    "markov_lag2",
    "ewma_hl30",
    "freq_window_50",
    "freq_window_200",
    "rank_ensemble_core",
    "logit_pooled_feats",
    "ridge_pooled_feats",
    "hot_consistency",
)
NEW_NAMES = tuple(
    f"weighted_experts_h{half_life}_{representation}"
    for half_life in (100, 400)
    for representation in ("rank", "vote")
)
DATASETS = {"romania": "_ISTORIC/loto_6_49.csv"}
DATASETS.update(
    {
        country: f"_ISTORIC/externe/{filename}.csv"
        for country, filename in (
            ("germania", "germania_lotto_6aus49"),
            ("cehia", "cehia_sportka_6din49"),
            ("slovacia", "slovacia_loto_6din49"),
            ("polonia", "polonia_lotto_6din49"),
            ("spania", "spania_la_primitiva_6din49"),
            ("bulgaria", "bulgaria_toto2_6din49"),
        )
    }
)
STEM = "pool_selection_649_2026-10-19"
MANIFEST = HERE / f"{STEM}_protocol.json"
REPORT = HERE / f"{STEM}.json"
CACHE = ROOT / "bench_results" / "pool649_research_20261019"
PROTECTED = (
    "best_methods.json",
    ".ui_state.json",
    "pool_history.json",
    "raport_complet.txt",
    "bench_results/folds.csv",
    "bench_results/report.json",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    paths = sorted((ROOT / "loto_enterprise" / "benchmark").glob("methods*.py"))
    paths += [
        ROOT / "loto_enterprise/core/ranking.py",
        ROOT / "loto_enterprise/core/pool_selection.py",
        ROOT / "loto_enterprise/core/score_validation.py",
        ROOT / "loto_enterprise/core/history.py",
        ROOT / "loto_enterprise/core/draw_validation.py",
        Path(__file__),
        HERE / "pool_candidates_2026_10_19.py",
        HERE / "pool_score_cache_2026_10_19.py",
        HERE / "validate_pool_screen_2026_10_19.py",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in paths
    }


def protocol() -> dict:
    return {
        "version": 1,
        "baseline": BASELINE,
        "pool_sizes": list(KS),
        "warmup_rows": WARMUP,
        "methods": sorted(set(METHODS) - EXCLUDED_FROM_PRODUCTION),
        "new_candidates": list(NEW_NAMES),
        "experts": list(EXPERTS),
        "candidate_parameters": {
            "half_lives": [100, 400],
            "eta": 1.0,
            "markov_prior_weight": 0.5,
            "fixed_share": 0.01,
            "reward": "mean 4+ over k9..16 after consecutive2",
            "update": "after all draws of target date, never within date",
        },
        "split": "whole unique dates: first60% train, next20% development, final20% confirmation",
        "selection": "one finalist: max dev primary, then mean hits, then name; invalid/tie-dependent excluded",
        "primary": "mean of 8 indicators hits>=4 per draw, max_consecutive_run=2",
        "secondary": ["raw topN", "3+", "mean hits", "zero hits", "per k"],
        "inference": {
            "unit": "date, same swap for all pool sizes and draws of same date",
            "permutations": 99999,
            "seed": SEED,
            "holm_family": "all eligible challengers on confirmation",
            "external": "same finalist unchanged on six archives; country Holm and pooled date test",
            "assumption": "paired sign-flip test assumes exchangeability of date-level differences under null; serial dependence can limit inference",
        },
        "retrospective_reuse": True,
        "limitations": "These histories were explored by earlier screens. This is retrospective robustness, not virgin holdout or prospective evidence. No production promotion solely from reused history.",
        "data_sha256": {n: sha(ROOT / p) for n, p in DATASETS.items()},
        "source_sha256": source_hashes(),
        "sources": [
            "https://proceedings.mlr.press/v128/korotin20a.html",
            "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html",
            "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html",
        ],
    }


def register() -> None:
    candidate = protocol()
    if MANIFEST.exists():
        stored = json.loads(MANIFEST.read_text(encoding="utf-8"))
        stored.pop("registered_at_utc", None)
        if stored != candidate:
            raise RuntimeError(
                "Registered protocol differs; do not overwrite after inspecting results"
            )
        print("Protocol already registered unchanged", flush=True)
        return
    candidate["registered_at_utc"] = datetime.now(timezone.utc).isoformat()
    MANIFEST.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    print("Protocol registered before evaluation", flush=True)


def verify_protocol() -> dict:
    stored = json.loads(MANIFEST.read_text(encoding="utf-8"))
    comparison = {k: v for k, v in stored.items() if k != "registered_at_utc"}
    if comparison != protocol():
        raise RuntimeError(
            "Input/code changed after registration; refusing stale or retuned result"
        )
    return stored


def protected_hashes() -> dict:
    return {p: sha(ROOT / p) if (ROOT / p).is_file() else None for p in PROTECTED}


def split_labels(dates: np.ndarray, targets: np.ndarray) -> np.ndarray:
    unique = np.unique(dates)
    dev_start = unique[int(len(unique) * 0.6)]
    confirm_start = unique[int(len(unique) * 0.8)]
    return np.where(
        dates[targets] < dev_start,
        "train",
        np.where(dates[targets] < confirm_start, "development", "confirmation"),
    )


def score_hits(
    scores: np.ndarray, draws: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Canonical production selection for both explicitly reported options."""
    hits = np.zeros((len(draws), 2, len(KS)), dtype=np.int8)
    pools = np.zeros((len(draws), 2, len(KS), max(KS)), dtype=np.int8)
    ties = np.zeros((len(draws), len(KS)), dtype=bool)
    for t, (row, actual) in enumerate(zip(scores, draws, strict=True)):
        mapping = {i + 1: float(value) for i, value in enumerate(row)}
        if not has_usable_score_variance(mapping):
            raise ValueError(f"unusable scores at row {t}")
        ranked = rank_by_score(mapping, 49)
        actual_set = set(map(int, actual))
        for j, k in enumerate(KS):
            ties[t, j] = np.isclose(
                row[ranked[k - 1] - 1], row[ranked[k] - 1], rtol=0, atol=1e-12
            )
            for mode, limit in enumerate((0, 2)):
                pool = apply_consecutive_limit(ranked, k, limit)
                assert len(pool) == k and len(set(pool)) == k
                hits[t, mode, j] = len(actual_set.intersection(pool))
                pools[t, mode, j, :k] = pool
    return hits, pools, ties


def metrics(hits: np.ndarray) -> dict:
    if len(hits) == 0:
        raise ValueError("empty evaluation segment")
    return {
        "n_draws": len(hits),
        "primary": float((hits >= 4).mean()),
        "mean_hits": float(hits.mean()),
        "zero_rate": float((hits == 0).mean()),
        "rate3plus": float((hits >= 3).mean()),
        "per_k": {
            str(k): {
                "events4": int((hits[:, j] >= 4).sum()),
                "events3": int((hits[:, j] >= 3).sum()),
                "zero": int((hits[:, j] == 0).sum()),
                "mean_hits": float(hits[:, j].mean()),
            }
            for j, k in enumerate(KS)
        },
    }


def collect_scores(
    country: str, names: list[str], identity: str, workers: int
) -> tuple[dict, dict]:
    from pool_score_cache_2026_10_19 import evaluate_method, load_data

    data = load_data(ROOT / DATASETS[country])
    cache_dir = CACHE / country
    cache_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                evaluate_method,
                {
                    "path": str(ROOT / DATASETS[country]),
                    "name": name,
                    "cache_dir": str(cache_dir),
                    "identity": identity,
                    "start": WARMUP,
                },
            ): name
            for name in names
        }
        for future in as_completed(futures):
            result = future.result()
            name = result[0]
            results[name] = result
            print(f"{country}: {name} done ({len(results)}/{len(names)})", flush=True)
    return data, results


def add_online(data: dict, results: dict) -> dict:
    from pool_candidates_2026_10_19 import candidate_scores

    targets = data["targets"]
    scores = np.stack([results[name][1] for name in EXPERTS], axis=1)
    out = candidate_scores(
        scores,
        data["draws"][targets],
        list(EXPERTS),
        groups=data["groups"][targets],
        frequency_past=data["frequency_past"],
    )
    if set(out) != set(NEW_NAMES):
        raise ValueError(f"candidate contract mismatch: {sorted(out)}")
    return out


def paired(
    candidate: np.ndarray, baseline: np.ndarray, dates: np.ndarray, *, bootstrap=0
) -> dict:
    from validate_pool_screen_2026_10_19 import paired_test

    return paired_test(
        candidate,
        baseline,
        dates,
        threshold=4,
        permutations=99999,
        seed=SEED,
        bootstrap_samples=bootstrap,
    )


def run(workers: int) -> None:
    from validate_pool_screen_2026_10_19 import holm_adjusted, hypergeom_tails

    reg = verify_protocol()
    identity = sha(MANIFEST)
    protected_before = protected_hashes()
    start = time.perf_counter()
    data, registry = collect_scores("romania", reg["methods"], identity, workers)
    online = add_online(data, registry)
    targets = data["targets"]
    dates, draws = data["dates"][targets], data["draws"][targets]
    labels = split_labels(data["dates"], targets)
    dev, confirm = labels == "development", labels == "confirmation"
    hits_by_name, summaries = {}, {}
    for name in reg["methods"] + list(NEW_NAMES):
        scores = online[name] if name in online else registry[name][1]
        hits, _pools, ties = score_hits(scores, draws)
        hits_by_name[name] = hits
        invalid = (
            np.zeros(len(targets), dtype=bool) if name in online else registry[name][2]
        )
        tie_rates = ties[dev].mean(axis=0)
        # Eligibility is frozen from pre-confirmation data, never target outcomes
        # or failures on the confirmation window.
        eligible = not invalid[~confirm].any() and not (tie_rates >= 0.5).any()
        summaries[name] = {
            "eligible": bool(eligible),
            "invalid_rows": int(invalid.sum()),
            "confirmation_invalid_rows": int(invalid[confirm].sum()),
            "tie_rates_development": tie_rates.tolist(),
            "scorer_seconds": None if name in online else float(registry[name][3]),
            "errors": {} if name in online else registry[name][4],
            "development": metrics(hits[dev, 1]),
            "confirmation": metrics(hits[confirm, 1]),
            "all_evaluated": metrics(hits[:, 1]),
            "confirmation_raw": metrics(hits[confirm, 0]),
        }
        print(f"RO canonical pools: {name}", flush=True)
    challengers = sorted(
        n for n, s in summaries.items() if s["eligible"] and n != BASELINE
    )
    if not challengers:
        raise ValueError("no eligible challenger")
    finalist = min(
        challengers,
        key=lambda n: (
            -summaries[n]["development"]["primary"],
            -summaries[n]["development"]["mean_hits"],
            n,
        ),
    )
    # This selection is completed before the paired confirmation statistics.
    print(f"Frozen development finalist: {finalist}", flush=True)
    frozen = {
        "name": finalist,
        "selected_on": "development only",
        "protocol_sha256": identity,
    }
    (CACHE / "finalist.json").write_text(
        json.dumps(frozen, indent=2) + "\n", encoding="utf-8"
    )
    base = hits_by_name[BASELINE]
    pvals = {}
    for name in challengers:
        result = paired(
            hits_by_name[name][confirm, 1],
            base[confirm, 1],
            dates[confirm],
            bootstrap=2000 if name == finalist else 0,
        )
        summaries[name]["paired_confirmation"] = result
        pvals[name] = result["p_value"]
    adjusted = holm_adjusted(pvals)
    for name, value in adjusted.items():
        summaries[name]["holm_confirmation"] = value
    external, external_parts = {}, []
    needed = list(EXPERTS) if finalist in NEW_NAMES else sorted({BASELINE, finalist})
    for country in DATASETS:
        if country == "romania":
            continue
        ext, evaluated = collect_scores(country, needed, identity, workers)
        et = ext["targets"]
        candidate_scores_array = (
            add_online(ext, evaluated)[finalist]
            if finalist in NEW_NAMES
            else evaluated[finalist][1]
        )
        candidate_hits, _pools, _ties = score_hits(
            candidate_scores_array, ext["draws"][et]
        )
        baseline_hits, _pools, _ties = score_hits(
            evaluated[BASELINE][1], ext["draws"][et]
        )
        detail = paired(
            candidate_hits[:, 1], baseline_hits[:, 1], ext["dates"][et], bootstrap=2000
        )
        external[country] = {
            "candidate": metrics(candidate_hits[:, 1]),
            "baseline": metrics(baseline_hits[:, 1]),
            "paired": detail,
            "invalid": {n: int(r[2].sum()) for n, r in evaluated.items()},
        }
        unique_dates = np.array([f"{country}:{date}" for date in ext["dates"][et]])
        external_parts.append((candidate_hits[:, 1], baseline_hits[:, 1], unique_dates))
        np.savez_compressed(
            CACHE / f"{country}_comparison.npz",
            candidate=candidate_hits,
            baseline=baseline_hits,
            dates=ext["dates"][et],
            draws=ext["draws"][et],
        )
    ep = {country: detail["paired"]["p_value"] for country, detail in external.items()}
    for country, value in holm_adjusted(ep).items():
        external[country]["holm_countries"] = value
    pooled = paired(
        *(np.concatenate([part[j] for part in external_parts]) for j in range(3)),
        bootstrap=2000,
    )
    protected_after = protected_hashes()
    verify_protocol()
    if protected_before != protected_after:
        raise RuntimeError(
            "Production runtime state changed during research; inspect before publishing"
        )
    report = {
        "protocol_sha256": identity,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - start,
        "retrospective_reuse": True,
        "baseline": BASELINE,
        "finalist": finalist,
        "n_registered": len(reg["methods"]) + len(NEW_NAMES),
        "eligible_challengers": challengers,
        "split": {
            label: {
                "n": int((labels == label).sum()),
                "first": str(dates[labels == label][0]),
                "last": str(dates[labels == label][-1]),
            }
            for label in ("train", "development", "confirmation")
        },
        "baselines": {str(k): hypergeom_tails(k) for k in KS},
        "methods": summaries,
        "external": external,
        "external_pooled": pooled,
        "protected_before": protected_before,
        "protected_after": protected_after,
        "production_changed": False,
        "conclusion": "Retrospective comparison only. No new method is promoted on reused historical data.",
    }
    REPORT.write_text(
        json.dumps(
            report,
            indent=2,
            default=lambda x: int(x) if isinstance(x, np.integer) else float(x),
        )
        + "\n",
        encoding="utf-8",
    )
    np.savez_compressed(
        CACHE / "romania_comparison.npz",
        candidate=hits_by_name[finalist],
        baseline=base,
        dates=dates,
        draws=draws,
        labels=labels,
        candidate_names=np.array(list(hits_by_name)),
        all_hits=np.stack(list(hits_by_name.values())),
        targets=targets,
    )
    with (HERE / f"{STEM}_comparison.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "date",
                "split",
                "pool_size",
                "baseline_consecutive2",
                "candidate_consecutive2",
                "baseline_raw",
                "candidate_raw",
            ]
        )
        for t, date in enumerate(dates):
            for j, k in enumerate(KS):
                writer.writerow(
                    [
                        str(date),
                        labels[t],
                        k,
                        int(base[t, 1, j]),
                        int(hits_by_name[finalist][t, 1, j]),
                        int(base[t, 0, j]),
                        int(hits_by_name[finalist][t, 0, j]),
                    ]
                )
    print(
        json.dumps(
            {
                "finalist": finalist,
                "holm_confirmation": adjusted[finalist],
                "external_p": pooled["p_value"],
                "elapsed_seconds": report["elapsed_seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.register:
        register()
    if args.run:
        run(max(1, min(args.workers, os.cpu_count() or 1)))
