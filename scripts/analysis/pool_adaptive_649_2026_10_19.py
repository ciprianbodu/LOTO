"""Frozen retrospective screen of new causal methods, Romania and Bulgaria only.

Register before evaluating outcomes. The older studies remain immutable. Cache
and long-lived streams are local to TEMP; final artifacts are written as UTF-8
LF bytes. No production method or saved decision is changed by this script.
"""

from __future__ import annotations

import os

for _key in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_key] = "1"

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
import scipy
from scipy.stats import rankdata

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from pool_adaptive_candidates_2026_10_19 import candidate_specs, generate
from pool_adaptive_cache_2026_10_19 import EXPERTS, load_scores
from pool_combination_eval_2026_10_19 import evaluate_bank
from pool_combinations_649_2026_10_19 import (
    compact_blocks,
    metrics,
    protected,
    sha,
    write_json,
)
from pool_score_cache_2026_10_19 import load_data
from validate_pool_combinations_2026_10_19 import replay_selector
from validate_pool_screen_2026_10_19 import holm_adjusted, hypergeom_tails, paired_test

BASELINE = "markov_lag2"
KS = tuple(range(9, 17))
SEED = 2026101903
STEM = "pool_adaptive_649_2026-10-19"
LOCAL = Path(tempfile.gettempdir()) / "loto-pool-adaptive-20261019"
CACHE = LOCAL / "cache"
MANIFEST = HERE / f"{STEM}_protocol.json"
REPORT = HERE / f"{STEM}.json"
DATASETS = {
    "romania": "_ISTORIC/loto_6_49.csv",
    "bulgaria": "_ISTORIC/externe/bulgaria_toto2_6din49.csv",
}


def specs():
    controls = [
        {"name": BASELINE, "family": "reference", "complexity": 1},
        {"name": "frequency", "family": "control", "complexity": 1},
    ]
    pure = candidate_specs()
    blends = [
        {
            "name": "mix_markov50__" + item["name"],
            "family": "markov_new_method_rank_blend",
            "complexity": item["complexity"] + 1,
            "parent_names": [BASELINE, item["name"]],
            "params": {
                "markov_weight": 0.5,
                "mean_rank_ties": "average",
                "flat_fallback": "frequency",
            },
        }
        for item in pure
    ]
    return controls + pure + blends


def source_hashes():
    paths = list((ROOT / "loto_enterprise").rglob("*.py"))
    paths += [
        HERE / name
        for name in (
            "pool_adaptive_649_2026_10_19.py",
            "pool_adaptive_candidates_2026_10_19.py",
            "pool_adaptive_cache_2026_10_19.py",
            "validate_pool_adaptive_2026_10_19.py",
            "pool_combinations_649_2026_10_19.py",
            "pool_combinations_2026_10_19.py",
            "pool_combination_cache_2026_10_19.py",
            "pool_combination_eval_2026_10_19.py",
            "pool_score_cache_2026_10_19.py",
            "validate_pool_combinations_2026_10_19.py",
            "validate_pool_screen_2026_10_19.py",
        )
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in sorted(paths)
    }


def protocol():
    return {
        "version": 1,
        "baseline": BASELINE,
        "pool_sizes": list(KS),
        "seed": SEED,
        "experts": list(EXPERTS),
        "candidate_specs": specs(),
        "candidate_count": len(specs()) - 2,
        "minimum_warmup_rows": 256,
        "score_start": "max(256, number of rows before floor(50% of full unique dates))",
        "training": "raw-history methods start at row0; calibration starts with the cached expert target stream at50%; all same-day predictions precede all updates for that day",
        "selection": {
            "origins": [0.7, 0.8, 0.9],
            "lookback": 0.2,
            "boundaries": "exact fractions of unique dates in FULL CSV",
            "criterion": "sum 4+ indicators over k9..16 in past window only",
            "tie_break": "baseline first, then declared model complexity, then ID",
            "gates": "no required-parent or own fallback in past window; every raw boundary tie fraction<.5; explicit baseline reserve",
            "future": "fixed choice inside the next10% block; future invalidity is counted, never used for reselection",
        },
        "primary": "rolling selector on80..100% vs Markov; max_consecutive_run2",
        "diagnostic": "selector70..80%; no outcome-dependent retuning",
        "secondary": "each fixed algorithm on final20%, raw topN with SAME choices,3+,mean hits,zero; pure and Markov50% blends",
        "inference": {
            "permutations": 99999,
            "bootstrap_samples": 2000,
            "unit": "whole day, same sign across8 overlapping pools and same-day draws",
            "external": "Bulgaria ONLY; same selection algorithm trained on Bulgarian past",
            "static_holm_families": "all fixed algorithm definitions in Romania and all in Bulgaria separately; controls excluded",
            "superiority_gate": "positive difference and one-sided p<.05 in BOTH Romania and Bulgaria",
            "assumption": "paired day-level method-label exchangeability conditional on observed trajectories; no full adaptive-search null calibration",
        },
        "legacy_schema": "static_combinations stores fixed algorithm DEFINITIONS, including causal online learners; scores themselves need not be static",
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "limitations": "Previously explored histories, no virgin holdout or prospective evidence. No automatic production promotion. Same-day bootstrap does not resolve serial dependence. Fixed-score IID target replay is not a valid full-pipeline null and is not used.",
        "environment": {
            "python": list(sys.version_info[:3]),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
        "source_sha256": source_hashes(),
        "data_sha256": {k: sha(ROOT / p) for k, p in DATASETS.items()},
        "sources": [
            "https://arxiv.org/abs/0710.3742",
            "https://ojs.aaai.org/index.php/AAAI/article/download/9602/9461",
        ],
        "source_scope": "BOCPD inspiration and probability-binning context only; custom rank calibration/interaction hypotheses, not a claim that these papers validate lottery prediction",
    }


def verify_protocol():
    old = json.loads(MANIFEST.read_bytes())
    if {k: v for k, v in old.items() if k != "registered_at_utc"} != protocol():
        raise RuntimeError("Frozen data, environment or scientific source changed")
    return old


def register():
    if MANIFEST.exists():
        verify_protocol()
        print("Protocol unchanged", flush=True)
    else:
        value = protocol()
        value["registered_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(MANIFEST, value)
        print(
            "Adaptive-method protocol registered before outcome evaluation", flush=True
        )


def blend_scores(left, right, frequency):
    left, right, frequency = [
        np.asarray(x, dtype=np.float64) for x in (left, right, frequency)
    ]
    if (
        left.ndim != 2
        or left.shape[1] != 49
        or right.shape != left.shape
        or frequency.shape != left.shape
    ):
        raise ValueError("three aligned [T,49] matrices required")
    if not all(np.isfinite(x).all() for x in (left, right, frequency)):
        raise ValueError("nonfinite blend inputs")
    score = (
        rankdata(left, axis=1, method="average")
        + rankdata(right, axis=1, method="average")
        - 2
    ) / 96
    flat = np.ptp(score, axis=1) <= 1e-12
    score[flat] = frequency[flat]
    return score, flat


def paired(candidate, baseline, dates, *, bootstrap=2000):
    return paired_test(
        candidate,
        baseline,
        dates,
        threshold=4,
        permutations=99999,
        seed=SEED,
        bootstrap_samples=bootstrap,
    )


def all_scores(data, results):
    values = {k: results[k][1] for k in EXPERTS}
    out = {k: (values[k], results[k][2].copy()) for k in (BASELINE, "frequency")}
    desc = {s["name"]: s for s in candidate_specs()}
    seen = set()
    for name, _family, score, fallback in generate(data, values):
        if name not in desc or name in seen:
            raise RuntimeError("unknown or duplicate generated candidate")
        seen.add(name)
        invalid = np.asarray(fallback, dtype=bool).copy()
        for parent in desc[name]["parent_names"]:
            invalid |= results[parent][2]
        out[name] = (score, invalid)
        mixed, flat = blend_scores(values[BASELINE], score, values["frequency"])
        out["mix_markov50__" + name] = (mixed, invalid | results[BASELINE][2] | flat)
    expected = {s["name"] for s in specs()}
    if set(out) != expected:
        raise RuntimeError("incomplete candidate bank")
    return out


def save_bank(path, **arrays):
    temporary = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, **arrays)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def country_bank(country, identity, workers):
    path = ROOT / DATASETS[country]
    full = load_data(path)
    dates = full["dates"]
    unique = np.unique(dates)
    start = max(256, int(np.searchsorted(dates, unique[len(unique) // 2])))
    data, results = load_scores(
        path,
        EXPERTS,
        CACHE / country,
        identity,
        workers=workers,
        start=start,
        require_reuse=True,
    )
    target = data["targets"]
    ids = [s["name"] for s in specs()]
    print(
        f"{country}: reused {len(results)} signed expert caches; generating new causal methods",
        flush=True,
    )
    generated = all_scores(data, results)
    scores = np.stack([generated[name][0] for name in ids])
    invalid = np.stack([generated[name][1] for name in ids])
    hits = np.empty((len(ids), len(target), 2, 8), dtype=np.int8)
    ties = np.empty((len(ids), len(target), 8), dtype=bool)
    positions = {name: i for i, name in enumerate(ids)}
    items = ((name, scores[i], invalid[i]) for i, name in enumerate(ids))
    for count, (name, h, t, _invalid) in enumerate(
        evaluate_bank(items, data["draws"][target], workers=workers), 1
    ):
        hits[positions[name]] = h
        ties[positions[name]] = t
        if count % 8 == 0 or count == len(ids):
            print(f"{country}: pools {count}/{len(ids)}", flush=True)
    valid = ~invalid
    complexity = np.array([s["complexity"] for s in specs()])
    replay = replay_selector(
        dates,
        target,
        hits[:, :, 1],
        ids,
        complexity=complexity,
        eligibility_mask=valid,
        ties=ties,
        baseline_id=BASELINE,
    )
    selected = replay["selected_indices"]
    selector = hits[selected, np.arange(len(target))]
    primary, diag = replay["primary_mask"], replay["diagnostic_mask"]
    baseline = hits[0]
    days = dates[target]
    summary = {
        "input_rows": len(dates),
        "target_rows": len(target),
        "start_history_rows": start,
        "blocks": compact_blocks(replay["blocks"]),
        "primary": {
            "selector": metrics(selector[primary, 1]),
            "baseline": metrics(baseline[primary, 1]),
            "paired": paired(selector[primary, 1], baseline[primary, 1], days[primary]),
        },
        "diagnostic": {
            "selector": metrics(selector[diag, 1]),
            "baseline": metrics(baseline[diag, 1]),
        },
        "raw": {
            "selector": metrics(selector[primary, 0]),
            "baseline": metrics(baseline[primary, 0]),
        },
        "first_primary": str(days[primary][0]),
        "last_primary": str(days[primary][-1]),
        "invalid_primary_by_candidate": {
            name: int((~valid[i, primary]).sum())
            for i, name in enumerate(ids)
            if not valid[i, primary].all()
        },
        "invalid_target_by_candidate": {
            name: int((~valid[i]).sum())
            for i, name in enumerate(ids)
            if not valid[i].all()
        },
        "selected_primary_invalid_rows": int(
            (~valid[selected, np.arange(len(target))][primary]).sum()
        ),
    }
    save_bank(
        CACHE / f"{country}_bank.npz",
        identity=np.asarray(identity),
        candidate_ids=np.asarray(ids),
        full_dates=dates,
        targets=target,
        draws=data["draws"][target],
        complexity=complexity,
        hits=hits,
        ties=ties,
        valid=valid,
        candidate_scores=scores,
        expert_names=np.asarray(EXPERTS),
        expert_scores=np.stack([results[n][1] for n in EXPERTS]),
        expert_invalid=np.stack([results[n][2] for n in EXPERTS]),
        selected_indices=selected,
        selector_hits=selector,
        primary_mask=primary,
        diagnostic_mask=diag,
    )
    print(f"{country}: causal selector replay complete", flush=True)
    return summary, hits[:, primary], selector[primary], days[primary]


def static_summary(candidate, baseline, dates):
    test = paired(candidate[:, 1], baseline[:, 1], dates, bootstrap=0)
    return {
        "limited": metrics(candidate[:, 1]),
        "raw": metrics(candidate[:, 0]),
        "difference": test["mean_difference"],
        "p_value": test["p_value"],
        "exact_sign_flip_p": test["exact_sign_flip_p_diagnostic"],
        "wins": test["wins"],
        "losses": test["losses"],
        "days": test["days"],
    }


def export_csv(report):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "candidate",
            "family",
            "pool_size",
            "region",
            "n",
            "candidate4",
            "markov4",
            "primary_p",
            "primary_holm",
        ]
    )
    for name, row in report["static_combinations"].items():
        for region, country in (("romania", "romania"), ("external", "bulgaria")):
            part = row[region]
            base = report["countries"][country]["primary"]["baseline"]
            for j, k in enumerate(KS):
                writer.writerow(
                    [
                        name,
                        row["spec"]["family"],
                        k,
                        region,
                        part["limited"]["n"],
                        part["limited"]["events4"][j],
                        base["events4"][j],
                        part["p_value"],
                        part["holm"],
                    ]
                )
    (HERE / f"{STEM}_candidates.csv").write_bytes(stream.getvalue().encode("utf-8"))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "country",
            "date",
            "segment",
            "chosen",
            "pool_size",
            "selector_c2",
            "markov_c2",
            "selector_raw",
            "markov_raw",
        ]
    )
    for country in DATASETS:
        with np.load(CACHE / f"{country}_bank.npz", allow_pickle=False) as data:
            for t in np.flatnonzero(data["primary_mask"] | data["diagnostic_mask"]):
                for j, k in enumerate(KS):
                    writer.writerow(
                        [
                            country,
                            data["full_dates"][data["targets"][t]],
                            "primary" if data["primary_mask"][t] else "diagnostic",
                            data["candidate_ids"][data["selected_indices"][t]],
                            k,
                            int(data["selector_hits"][t, 1, j]),
                            int(data["hits"][0, t, 1, j]),
                            int(data["selector_hits"][t, 0, j]),
                            int(data["hits"][0, t, 0, j]),
                        ]
                    )
    (HERE / f"{STEM}_trace.csv").write_bytes(stream.getvalue().encode("utf-8"))


def run(workers):
    frozen = verify_protocol()
    identity = sha(MANIFEST)
    before = protected()
    started = time.perf_counter()
    countries = {}
    arrays = {}
    for country in DATASETS:
        summary, hits, selector, days = country_bank(country, identity, workers)
        countries[country] = summary
        arrays[country] = (hits, selector, days)
    bg, bs, bd = arrays["bulgaria"]
    external = {
        "selector": metrics(bs[:, 1]),
        "baseline": metrics(bg[0, :, 1]),
        "raw_selector": metrics(bs[:, 0]),
        "raw_baseline": metrics(bg[0, :, 0]),
        "paired": paired(
            bs[:, 1], bg[0, :, 1], np.array([f"bulgaria:{d}" for d in bd])
        ),
    }
    countries["bulgaria"]["selector_country_holm"] = countries["bulgaria"]["primary"][
        "paired"
    ]["p_value"]
    fixed = {}
    for i, descriptor in enumerate(specs()[2:], 2):
        name = descriptor["name"]
        row = {"spec": descriptor}
        for region, country in (("romania", "romania"), ("external", "bulgaria")):
            h, _s, d = arrays[country]
            test_days = (
                d if country == "romania" else np.array([f"bulgaria:{x}" for x in d])
            )
            row[region] = static_summary(h[i], h[0], test_days)
            row[region]["invalid_primary_rows"] = countries[country][
                "invalid_primary_by_candidate"
            ].get(name, 0)
        fixed[name] = row
    for region in ("romania", "external"):
        for name, q in holm_adjusted(
            {n: v[region]["p_value"] for n, v in fixed.items()}
        ).items():
            fixed[name][region]["holm"] = q
    verify_protocol()
    after = protected()
    if before != after:
        raise RuntimeError("Protected runtime files changed while research ran")
    report = {
        "protocol_sha256": identity,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - started,
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "production_changed": False,
        "baseline": BASELINE,
        "candidate_count": frozen["candidate_count"],
        "countries": countries,
        "external_pooled": external,
        "static_combinations": fixed,
        "hypergeometric": {str(k): hypergeom_tails(k) for k in KS},
        "protected_before": before,
        "protected_after": after,
        "selector_superiority_gate_passed": all(
            part["paired"]["mean_difference"] > 0 and part["paired"]["p_value"] < 0.05
            for part in (countries["romania"]["primary"], external)
        ),
        "conclusion": "Retrospective comparison only; no automatic production change or proof of future advantage.",
    }
    write_json(REPORT, report)
    export_csv(report)
    print(
        json.dumps(
            {
                "candidate_count": len(fixed),
                "RO_selector": countries["romania"]["primary"]["selector"]["primary"],
                "RO_markov": countries["romania"]["primary"]["baseline"]["primary"],
                "BG_selector": external["selector"]["primary"],
                "BG_markov": external["baseline"]["primary"],
                "selector_gate": report["selector_superiority_gate_passed"],
                "seconds": report["elapsed_seconds"],
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
        run(max(1, min(args.workers, 4, os.cpu_count() or 1)))
