"""Frozen retrospective comparison of 208 pool combinations and a rolling selector.

Research only: --register before --run. No production scorer/decision is written.
Signed JSON/CSV/Markdown artifacts are explicitly UTF-8/LF on every platform.
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
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import zipfile

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
from loto_enterprise.benchmark.methods import METHODS
from pool_combination_cache_2026_10_19 import load_scores
from pool_combination_eval_2026_10_19 import evaluate_bank
from pool_combinations_2026_10_19 import candidate_specs, iter_candidates
from pool_score_cache_2026_10_19 import load_data
from validate_pool_combinations_2026_10_19 import (
    holm_adjusted,
    hypergeom_tails,
    paired_test,
    replay_selector,
)

BASELINE = "markov_lag2"
KS = tuple(range(9, 17))
SEED = 2026101902
WARMUP = 256
STEM = "pool_combinations_649_2026-10-19"
CACHE = ROOT / "bench_results/pool649_combinations_20261019"
MANIFEST = HERE / f"{STEM}_protocol.json"
REPORT = HERE / f"{STEM}.json"
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
PROTECTED = (
    "best_methods.json",
    ".ui_state.json",
    "pool_history.json",
    "raport_complet.txt",
    "bench_results/folds.csv",
    "bench_results/report.json",
)


def write_json(path, value):
    Path(path).write_bytes(
        (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    paths = list((ROOT / "loto_enterprise").rglob("*.py"))
    paths += [
        HERE / name
        for name in (
            "pool_combinations_649_2026_10_19.py",
            "pool_combinations_2026_10_19.py",
            "pool_combination_cache_2026_10_19.py",
            "pool_combination_eval_2026_10_19.py",
            "validate_pool_combinations_2026_10_19.py",
            "pool_score_cache_2026_10_19.py",
            "validate_pool_screen_2026_10_19.py",
        )
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in sorted(paths)
    }


def methods():
    return sorted(set(METHODS) - EXCLUDED_FROM_PRODUCTION)


def specs():
    return [
        {
            "name": BASELINE,
            "family": "reference",
            "parent_names": [BASELINE],
            "complexity": 1,
        },
        {
            "name": "frequency",
            "family": "control",
            "parent_names": ["frequency"],
            "complexity": 1,
        },
    ] + candidate_specs(methods())


def protocol():
    return {
        "version": 1,
        "baseline": BASELINE,
        "pool_sizes": list(KS),
        "seed": SEED,
        "minimum_warmup_rows": WARMUP,
        "experts": methods(),
        "candidate_specs": specs(),
        "candidate_count": len(specs()) - 2,
        "score_start": "max(256, number of rows strictly before floor(50% of full unique dates))",
        "selection": {
            "origins": [0.7, 0.8, 0.9],
            "lookback": 0.2,
            "boundaries": "exact fractions of unique dates in the FULL CSV",
            "criterion": "sum 4+ indicators across k9..16 in past window only",
            "tie_break": "baseline first, then formula-parent count, then candidate ID",
            "gates": "no invalid source/flat-combination fallback in past window; every raw boundary tie fraction<.5; baseline reserve explicit",
            "future": "choice fixed until next origin; future invalidity reported without reselection",
        },
        "primary": "rolling selector vs Markov on80..100% of original days; constrained2",
        "diagnostic": "rolling selector70..80%; no parameter retuning after results",
        "secondary": "raw topN uses SAME choices as constrained2;3+,mean hits,zero;208 static candidates on final20%",
        "inference": {
            "permutations": 99999,
            "bootstrap_samples": 2000,
            "unit": "whole day; same swap for all8 pools and same-day draws",
            "external": "same selector from local past on six archives; last20% per country; pooled country:date",
            "static_holm_families": "all208 statics in RO and all208 in pooled external, separately; controls excluded",
            "country_holm": "six country selector tests",
            "superiority_gate": "positive selector difference and one-sided p<.05 in BOTH RO and pooled external",
            "assumption": "paired date-level label exchangeability; day bootstrap does not prove robustness to serial dependence",
        },
        "iid_search_calibrated": False,
        "retrospective_reuse": True,
        "limitations": "All histories were explored previously. No virgin holdout, no IID full-search replay, no proof of future advantage. No automatic production promotion from reused retrospective data.",
        "source_sha256": source_hashes(),
        "data_sha256": {name: sha(ROOT / path) for name, path in DATASETS.items()},
        "sources": [
            "https://cormack.uwaterloo.ca/cormack/cormacksigir09-rrf.pdf",
            "https://journal.r-project.org/articles/RJ-2018-052/",
            "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html",
        ],
    }


def register():
    value = protocol()
    if MANIFEST.exists():
        old = json.loads(MANIFEST.read_text(encoding="utf-8"))
        old.pop("registered_at_utc", None)
        if old != value:
            raise RuntimeError(
                "Frozen protocol differs; do not overwrite after evaluating outcomes"
            )
        print("Protocol unchanged", flush=True)
        return
    value["registered_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(MANIFEST, value)
    print("Combination protocol registered before outcome evaluation", flush=True)


def verify_protocol():
    frozen = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if {k: v for k, v in frozen.items() if k != "registered_at_utc"} != protocol():
        raise RuntimeError("Frozen input/code changed")
    return frozen


def protected():
    return {
        name: sha(ROOT / name) if (ROOT / name).is_file() else None
        for name in PROTECTED
    }


def metrics(hits):
    hits = np.asarray(hits)
    if hits.ndim != 2 or hits.shape[1] != 8 or not len(hits):
        raise ValueError("metrics require nonempty [rows,8] hits")
    return {
        "n": len(hits),
        "primary": float((hits >= 4).mean()),
        "rate3plus": float((hits >= 3).mean()),
        "mean_hits": float(hits.mean()),
        "zero_rate": float((hits == 0).mean()),
        "events4": (hits >= 4).sum(axis=0).tolist(),
        "events3": (hits >= 3).sum(axis=0).tolist(),
        "zero": (hits == 0).sum(axis=0).tolist(),
        "mean_hits_by_k": hits.mean(axis=0).tolist(),
    }


def compact_blocks(blocks):
    out = []
    for block in blocks:
        gates = block["gates"]
        out.append(
            {
                **{k: v for k, v in block.items() if k != "gates"},
                "eligible_count": sum(g["eligible"] for g in gates.values()),
                "chosen_gate": gates[block["chosen_id"]],
                "baseline_gate": gates[BASELINE],
                "excluded": {
                    name: {
                        "invalid_rows": gate["invalid_rows_in_past_window"],
                        "max_tie_fraction": max(gate["tie_fractions_in_past_window"]),
                    }
                    for name, gate in gates.items()
                    if not gate["eligible"]
                },
            }
        )
    return out


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


def item_generator(results, experts):
    for name in (BASELINE, "frequency"):
        yield name, results[name][1], results[name][2]
    tensor = np.stack([results[name][1] for name in experts], axis=1)
    audit = {}
    descriptors = {d["name"]: d for d in candidate_specs(experts)}
    for name, _family, score in iter_candidates(tensor, experts, audit=audit):
        invalid = audit.pop(name)
        for parent in descriptors[name]["parent_names"]:
            invalid = invalid | results[parent][2]
        yield name, score, invalid


def country_bank(country, identity, workers):
    path = ROOT / DATASETS[country]
    full = load_data(path)
    dates = full["dates"]
    unique = np.unique(dates)
    cutoff_date = unique[len(unique) // 2]
    start = max(WARMUP, int(np.searchsorted(dates, cutoff_date, side="left")))
    data = load_data(path, start=start)
    desc = specs()
    ids = [d["name"] for d in desc]
    target = data["targets"]
    draws = data["draws"][target]
    cache_dir = CACHE / country
    cache_dir.mkdir(parents=True, exist_ok=True)
    path_npz = CACHE / f"{country}_bank.npz"
    bank = None
    if path_npz.exists():
        try:
            with np.load(path_npz, allow_pickle=False) as stored:
                if (
                    str(stored["identity"].item()) == identity
                    and np.array_equal(stored["candidate_ids"], ids)
                    and np.array_equal(stored["targets"], target)
                ):
                    bank = {name: stored[name] for name in ("hits", "ties", "valid")}
        except OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile:
            bank = None
    if bank is None:
        print(
            f"{country}: generating/loading {len(methods())} strict-prefix scorers",
            flush=True,
        )
        data, results = load_scores(
            path, methods(), cache_dir, identity, workers=workers, start=start
        )
        t = len(target)
        c = len(ids)
        bank = {
            "hits": np.zeros((c, t, 2, 8), dtype=np.int8),
            "ties": np.zeros((c, t, 8), dtype=bool),
            "valid": np.zeros((c, t), dtype=bool),
        }
        positions = {name: i for i, name in enumerate(ids)}
        count = 0
        for name, hits, ties, invalid in evaluate_bank(
            item_generator(results, methods()), draws, workers=workers
        ):
            i = positions[name]
            bank["hits"][i] = hits
            bank["ties"][i] = ties
            bank["valid"][i] = ~invalid
            count += 1
            if count % 20 == 0 or count == c:
                print(f"{country}: exact pools {count}/{c}", flush=True)
        provenance = Counter(p["mode"] for p in data["score_cache_provenance"].values())
        write_json(cache_dir / "provenance_summary.json", dict(provenance))
    shape = (len(ids), len(target), 2, 8)
    if (
        bank["hits"].shape != shape
        or bank["ties"].shape != shape[:2] + (8,)
        or bank["valid"].shape != shape[:2]
    ):
        raise ValueError("cached bank shape mismatch")
    if not ((bank["hits"] >= 0) & (bank["hits"] <= 6)).all():
        raise ValueError("cached hits outside0..6")
    complexity = np.array([d["complexity"] for d in desc])
    replay = replay_selector(
        dates,
        target,
        bank["hits"][:, :, 1],
        ids,
        complexity=complexity,
        eligibility_mask=bank["valid"],
        ties=bank["ties"],
        baseline_id=BASELINE,
    )
    selected = replay["selected_indices"]
    selector_hits = bank["hits"][selected, np.arange(len(target))]
    baseline_hits = bank["hits"][ids.index(BASELINE)]
    primary = replay["primary_mask"]
    diag = replay["diagnostic_mask"]
    target_dates = dates[target]
    summary = {
        "input_rows": len(dates),
        "target_rows": len(target),
        "start_history_rows": start,
        "blocks": compact_blocks(replay["blocks"]),
        "primary": {
            "selector": metrics(selector_hits[primary, 1]),
            "baseline": metrics(baseline_hits[primary, 1]),
            "paired": paired(
                selector_hits[primary, 1],
                baseline_hits[primary, 1],
                target_dates[primary],
            ),
        },
        "diagnostic": {
            "selector": metrics(selector_hits[diag, 1]),
            "baseline": metrics(baseline_hits[diag, 1]),
        },
        "raw": {
            "selector": metrics(selector_hits[primary, 0]),
            "baseline": metrics(baseline_hits[primary, 0]),
        },
        "first_primary": str(target_dates[primary][0]),
        "last_primary": str(target_dates[primary][-1]),
        "invalid_primary_by_candidate": {
            name: int(np.count_nonzero(~bank["valid"][i, primary]))
            for i, name in enumerate(ids)
            if not bank["valid"][i, primary].all()
        },
        "invalid_target_by_candidate": {
            name: int(np.count_nonzero(~bank["valid"][i]))
            for i, name in enumerate(ids)
            if not bank["valid"][i].all()
        },
        "selected_primary_invalid_rows": int(
            np.count_nonzero(~bank["valid"][selected, np.arange(len(target))][primary])
        ),
    }
    with tempfile.NamedTemporaryFile(
        dir=path_npz.parent, prefix=f".{path_npz.name}.", suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
        np.savez_compressed(
            stream,
            identity=np.asarray(identity),
            candidate_ids=np.asarray(ids),
            full_dates=dates,
            targets=target,
            draws=draws,
            complexity=complexity,
            **bank,
            selected_indices=selected,
            selector_hits=selector_hits,
            primary_mask=primary,
            diagnostic_mask=diag,
        )
    try:
        temporary.replace(path_npz)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"{country}: selector replay complete", flush=True)
    return (
        summary,
        bank["hits"][:, primary],
        selector_hits[primary],
        target_dates[primary],
    )


def static_summary(hits, baseline, dates):
    result = paired(hits[:, 1], baseline[:, 1], dates, bootstrap=0)
    return {
        "limited": metrics(hits[:, 1]),
        "raw": metrics(hits[:, 0]),
        "difference": result["mean_difference"],
        "p_value": result["p_value"],
        "exact_sign_flip_p": result["exact_sign_flip_p_diagnostic"],
        "wins": result["wins"],
        "losses": result["losses"],
        "days": result["days"],
    }


def run(workers):
    frozen = verify_protocol()
    identity = sha(MANIFEST)
    before = protected()
    started = time.perf_counter()
    CACHE.mkdir(parents=True, exist_ok=True)
    countries = {}
    static_parts = []
    selector_parts = []
    date_parts = []
    ro_hits = None
    ro_dates = None
    for country in DATASETS:
        summary, hits, selector, dates = country_bank(country, identity, workers)
        countries[country] = summary
        if country == "romania":
            ro_hits, ro_dates = hits, dates
        else:
            static_parts.append(hits)
            selector_parts.append(selector)
            date_parts.append(np.array([f"{country}:{date}" for date in dates]))
    ids = [d["name"] for d in specs()]
    baseline = ids.index(BASELINE)
    external_hits = np.concatenate(static_parts, axis=1)
    external_selector = np.concatenate(selector_parts, axis=0)
    external_dates = np.concatenate(date_parts)
    external = {
        "selector": metrics(external_selector[:, 1]),
        "baseline": metrics(external_hits[baseline, :, 1]),
        "raw_selector": metrics(external_selector[:, 0]),
        "raw_baseline": metrics(external_hits[baseline, :, 0]),
        "paired": paired(
            external_selector[:, 1], external_hits[baseline, :, 1], external_dates
        ),
    }
    external_p = {
        country: summary["primary"]["paired"]["p_value"]
        for country, summary in countries.items()
        if country != "romania"
    }
    for country, q in holm_adjusted(external_p).items():
        countries[country]["selector_country_holm"] = q
    statics = {}
    for i, descriptor in enumerate(specs()[2:], start=2):
        statics[ids[i]] = {
            "spec": descriptor,
            "romania": static_summary(ro_hits[i], ro_hits[baseline], ro_dates),
            "external": static_summary(
                external_hits[i], external_hits[baseline], external_dates
            ),
        }
        statics[ids[i]]["romania"]["invalid_primary_rows"] = countries["romania"][
            "invalid_primary_by_candidate"
        ].get(ids[i], 0)
        statics[ids[i]]["external"]["invalid_primary_rows"] = sum(
            summary["invalid_primary_by_candidate"].get(ids[i], 0)
            for country, summary in countries.items()
            if country != "romania"
        )
    for region in ("romania", "external"):
        for name, q in holm_adjusted(
            {name: row[region]["p_value"] for name, row in statics.items()}
        ).items():
            statics[name][region]["holm"] = q
    after = protected()
    verify_protocol()
    if before != after:
        raise RuntimeError(
            "Protected runtime files changed while research ran; inspect independently"
        )
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
        "static_combinations": statics,
        "hypergeometric": {str(k): hypergeom_tails(k) for k in KS},
        "protected_before": before,
        "protected_after": after,
        "selector_superiority_gate_passed": all(
            row["paired"]["mean_difference"] > 0 and row["paired"]["p_value"] < 0.05
            for row in (countries["romania"]["primary"], external)
        ),
        "conclusion": "Retrospective research only; no automatic production change or claim of prospective proof.",
    }
    write_json(REPORT, report)
    with (HERE / f"{STEM}_static.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
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
        for name, row in statics.items():
            for region in ("romania", "external"):
                part = row[region]
                base = (
                    countries["romania"]["primary"]["baseline"]
                    if region == "romania"
                    else external["baseline"]
                )
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
    with np.load(CACHE / "romania_bank.npz", allow_pickle=False) as data:
        with (HERE / f"{STEM}_trace.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(
                [
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
            active = data["primary_mask"] | data["diagnostic_mask"]
            for t in np.flatnonzero(active):
                for j, k in enumerate(KS):
                    writer.writerow(
                        [
                            data["full_dates"][data["targets"][t]],
                            "primary" if data["primary_mask"][t] else "diagnostic",
                            ids[data["selected_indices"][t]],
                            k,
                            int(data["selector_hits"][t, 1, j]),
                            int(data["hits"][baseline, t, 1, j]),
                            int(data["selector_hits"][t, 0, j]),
                            int(data["hits"][baseline, t, 0, j]),
                        ]
                    )
    print(
        json.dumps(
            {
                "candidate_count": len(statics),
                "RO_selector": countries["romania"]["primary"]["selector"]["primary"],
                "RO_markov": countries["romania"]["primary"]["baseline"]["primary"],
                "external_selector": external["selector"]["primary"],
                "external_markov": external["baseline"]["primary"],
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
