"""Independent paired statistics for the 2026-10-19 pool screen.

The primary observation is the average success over pools 9..16 for one draw.
Randomization swaps the two methods for an ENTIRE DAY, preserving dependence
between pool sizes and between simultaneous draws. The permutation test needs
exchangeability of paired method labels under the null; it is a retrospective
comparison, not proof of future predictive performance. No production scorer,
benchmark output, saved decision, or application cache is imported or written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from collections.abc import Mapping

import numpy as np
from scipy.stats import binomtest

POOLS = tuple(range(9, 17))


def holm_adjusted(p_values: Mapping[str, float]) -> dict[str, float]:
    """Holm family correction, independently implemented for this validator."""
    ordered = sorted((float(p), str(name)) for name, p in p_values.items())
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p, _ in ordered):
        raise ValueError("p values must be finite numbers in [0, 1]")
    adjusted: dict[str, float] = {}
    previous = 0.0
    count = len(ordered)
    for rank, (p, name) in enumerate(ordered):
        previous = max(previous, min(1.0, (count - rank) * p))
        adjusted[name] = previous
    return adjusted


def hypergeom_tails(pool_size: int, max_num: int = 49, draw_n: int = 6) -> dict:
    """Exact combinatorial baseline for a pool fixed before an unseen draw."""
    if not (max_num >= 1 and 0 <= draw_n <= max_num and 0 <= pool_size <= max_num):
        raise ValueError("invalid lottery geometry")
    denominator = math.comb(max_num, draw_n)
    counts = {
        h: math.comb(pool_size, h) * math.comb(max_num - pool_size, draw_n - h)
        for h in range(draw_n + 1)
        if h <= pool_size and draw_n - h <= max_num - pool_size
    }
    distribution = {str(h): counts.get(h, 0) / denominator for h in range(draw_n + 1)}
    return {
        "pool_size": pool_size,
        "draw_n": draw_n,
        "max_num": max_num,
        "expected_hits": pool_size * draw_n / max_num,
        "p0": counts.get(0, 0) / denominator,
        "p_ge_1": sum(v for h, v in counts.items() if h >= 1) / denominator,
        "p_ge_3": sum(v for h, v in counts.items() if h >= 3) / denominator,
        "p_ge_4": sum(v for h, v in counts.items() if h >= 4) / denominator,
        "distribution": distribution,
    }


def _validated_hits(values, name: str) -> np.ndarray:
    arr = np.asarray(values)
    if arr.ndim != 2 or arr.shape[1] != len(POOLS) or arr.shape[0] == 0:
        raise ValueError(f"{name} must have shape [nonzero rows, 8] for pools 9..16")
    numeric = np.asarray(arr, dtype=np.float64)
    if not np.isfinite(numeric).all():
        raise ValueError(f"{name} contains non-finite hits")
    if not ((numeric >= 0) & (numeric <= 6) & (numeric == np.floor(numeric))).all():
        raise ValueError(f"{name} must contain integer hit counts in 0..6")
    return numeric.astype(np.int64)


def _day_groups(day_ids, n_rows: int) -> np.ndarray:
    labels = np.asarray(day_ids)
    if labels.ndim != 1 or len(labels) != n_rows:
        raise ValueError("day_ids must contain exactly one label for each row")
    groups: dict[object, int] = {}
    inverse = np.empty(n_rows, dtype=np.int64)
    for i, value in enumerate(labels):
        if isinstance(value, np.datetime64) and np.isnat(value):
            raise ValueError("day_ids contains NaT")
        label = value.item() if isinstance(value, np.generic) else value
        if label is None or (isinstance(label, float) and not math.isfinite(label)):
            raise ValueError("day_ids contains a missing/non-finite date")
        inverse[i] = groups.setdefault(label, len(groups))
    return inverse


def _exact_sign_flip(day_differences: np.ndarray) -> float:
    """Exact sign-flip tail via integer-lattice convolution (diagnostic only)."""
    observed = int(day_differences.sum())
    probabilities = np.ones(1, dtype=np.float64)
    offset = 0
    for difference in day_differences:
        step = abs(int(difference))
        if step == 0:
            continue
        updated = np.zeros(len(probabilities) + 2 * step, dtype=np.float64)
        updated[: len(probabilities)] += 0.5 * probabilities
        updated[2 * step :] += 0.5 * probabilities
        offset += step
        probabilities = updated
    return min(1.0, max(0.0, float(probabilities[observed + offset :].sum())))


def paired_test(
    candidate_hits,
    baseline_hits,
    day_ids,
    threshold: int = 4,
    permutations: int = 99999,
    seed: int = 20261019,
    bootstrap_samples: int = 2000,
) -> dict:
    """One-sided paired test of the mean success improvement over pools 9..16.

    Positive differences favor the candidate. wins/losses count DAYS, not the
    eight overlapping pool columns. For the primary test every draw in a day
    receives the same random sign. CI95 resamples complete days and preserves
    weighting by the number of draws when a day has multiple rows. Per-pool
    McNemar tests are secondary; their Holm correction is a separate family.
    The exact sign-flip tail is an independent arithmetic diagnostic; the
    predeclared primary p value is the seeded Monte Carlo estimate.
    """
    candidate = _validated_hits(candidate_hits, "candidate_hits")
    baseline = _validated_hits(baseline_hits, "baseline_hits")
    if candidate.shape != baseline.shape:
        raise ValueError("candidate and baseline shapes differ")
    if threshold not in range(1, 7):
        raise ValueError("threshold must be an integer in 1..6")
    if int(permutations) != permutations or permutations < 1:
        raise ValueError("permutations must be a positive integer")
    if int(bootstrap_samples) != bootstrap_samples or bootstrap_samples < 0:
        raise ValueError("bootstrap_samples must be a nonnegative integer")
    n_rows, n_pools = candidate.shape
    inverse = _day_groups(day_ids, n_rows)
    n_days = int(inverse.max()) + 1
    candidate_events = candidate >= threshold
    baseline_events = baseline >= threshold
    row_differences = (candidate_events.astype(np.int64) - baseline_events).sum(axis=1)
    day_differences = np.zeros(n_days, dtype=np.int64)
    np.add.at(day_differences, inverse, row_differences)
    day_sizes = np.bincount(inverse, minlength=n_days)
    observed = int(day_differences.sum())
    wins = int((day_differences > 0).sum())
    losses = int((day_differences < 0).sum())
    nonzero = day_differences[day_differences != 0]
    rng = np.random.default_rng(seed)
    if len(nonzero):
        exceed = 0
        for start in range(0, permutations, 4096):
            batch = min(4096, permutations - start)
            signs = (
                rng.integers(0, 2, size=(batch, len(nonzero)), dtype=np.int8) * 2 - 1
            )
            simulated = signs @ nonzero
            exceed += int((simulated >= observed).sum())
        p_value = (exceed + 1) / (permutations + 1)
    else:
        exceed = permutations
        p_value = 1.0
    sign_p = (
        float(binomtest(wins, wins + losses, 0.5, alternative="greater").pvalue)
        if wins + losses
        else 1.0
    )
    ci95 = None
    if bootstrap_samples:
        boot_rng = np.random.default_rng(seed + 1)
        bootstrap = []
        for start in range(0, bootstrap_samples, 128):
            size = min(128, bootstrap_samples - start)
            selected = boot_rng.integers(0, n_days, size=(size, n_days))
            numerator = day_differences[selected].sum(axis=1)
            denominator = n_pools * day_sizes[selected].sum(axis=1)
            bootstrap.append(numerator / denominator)
        ci95 = list(map(float, np.quantile(np.concatenate(bootstrap), [0.025, 0.975])))
    per_pool = {}
    for column, k in enumerate(POOLS):
        cand = candidate_events[:, column]
        base = baseline_events[:, column]
        b = int((cand & ~base).sum())
        c = int((base & ~cand).sum())
        p = (
            float(binomtest(b, b + c, 0.5, alternative="greater").pvalue)
            if b + c
            else 1.0
        )
        per_pool[f"k{k}"] = {
            "candidate_rate": float(cand.mean()),
            "baseline_rate": float(base.mean()),
            "difference": float(cand.mean() - base.mean()),
            "candidate_only": b,
            "baseline_only": c,
            "mcnemar_p": p,
        }
    adjusted = holm_adjusted({k: value["mcnemar_p"] for k, value in per_pool.items()})
    for k, adjusted_p in adjusted.items():
        per_pool[k]["mcnemar_holm_p"] = adjusted_p
    return {
        "rows": n_rows,
        "days": n_days,
        "pools": list(POOLS),
        "threshold": threshold,
        "candidate_rate": float(candidate_events.mean()),
        "baseline_rate": float(baseline_events.mean()),
        "mean_difference": observed / (n_rows * n_pools),
        "difference_numerator": observed,
        "wins": wins,
        "losses": losses,
        "ties": int((day_differences == 0).sum()),
        "p_value": p_value,
        "exact_sign_flip_p_diagnostic": _exact_sign_flip(day_differences),
        "sign_p": sign_p,
        "permutations": permutations,
        "permutation_exceedances": exceed,
        "seed": seed,
        "ci95_day_cluster_bootstrap": ci95,
        "bootstrap_samples": bootstrap_samples,
        "per_pool": per_pool,
        "assumption": "paired method-label exchangeability under the null; day-cluster swaps",
    }


def _self_test() -> None:
    # Four winning days => exact one-sided sign-flip/sign probability 1/16.
    full = np.full((4, 8), 6)
    zero = np.zeros((4, 8), dtype=int)
    result = paired_test(full, zero, np.arange(4), permutations=19999)
    assert result["mean_difference"] == 1.0
    assert result["wins"] == 4 and result["losses"] == 0
    assert result["exact_sign_flip_p_diagnostic"] == 1 / 16
    assert result["sign_p"] == 1 / 16
    assert abs(result["p_value"] - 1 / 16) < 0.01
    assert result["ci95_day_cluster_bootstrap"] == [1.0, 1.0]
    # Same-day opposite outcomes cancel; treating rows as independent is wrong.
    cand = np.vstack([full[:1], zero[:1]])
    base = np.vstack([zero[:1], full[:1]])
    cancelled = paired_test(cand, base, ["same", "same"], permutations=199)
    assert cancelled["days"] == 1 and cancelled["mean_difference"] == 0
    assert cancelled["p_value"] == cancelled["sign_p"] == 1.0
    assert cancelled["wins"] == cancelled["losses"] == 0
    # One changed column has weight 1/8, not a full additional observation.
    one = zero[:1].copy()
    one[0, 0] = 4
    weighted = paired_test(one, zero[:1], [1], permutations=19999)
    assert weighted["mean_difference"] == 1 / 8
    assert weighted["exact_sign_flip_p_diagnostic"] == 0.5
    assert holm_adjusted({"a": 0.01, "b": 0.04, "c": 0.03}) == {
        "a": 0.03,
        "c": 0.06,
        "b": 0.06,
    }
    assert math.isclose(hypergeom_tails(16)["p0"], 1798 / 22701)
    assert math.isclose(sum(hypergeom_tails(9)["distribution"].values()), 1.0)


def _metrics_independent(hits: np.ndarray) -> dict:
    """Recount hit summaries from stored integer arrays, without main imports."""
    values = _validated_hits(hits, "stored hits")
    return {
        "n_draws": len(values),
        "primary": float(np.count_nonzero(values >= 4) / values.size),
        "mean_hits": float(values.sum() / values.size),
        "zero_rate": float(np.count_nonzero(values == 0) / values.size),
        "rate3plus": float(np.count_nonzero(values >= 3) / values.size),
        "per_k": {
            str(k): {
                "events4": int(np.count_nonzero(values[:, j] >= 4)),
                "events3": int(np.count_nonzero(values[:, j] >= 3)),
                "zero": int(np.count_nonzero(values[:, j] == 0)),
                "mean_hits": float(values[:, j].sum() / len(values)),
            }
            for j, k in enumerate(POOLS)
        },
    }


def verify_report(report_path, cache_dir=None) -> dict:
    """Recount every published array, selection, paired test and protected hash.

    Registry scorers are additionally sampled directly on strict CSV prefixes;
    the online finalist's score generation is tested separately by its own
    causality tests if it is not an importable production registry scorer.
    """
    report_path = Path(report_path).resolve()
    root = Path(__file__).resolve().parents[2]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path = report_path.with_name(report_path.stem + "_protocol.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    cache_dir = (
        Path(cache_dir).resolve()
        if cache_dir is not None
        else root / "bench_results" / "pool649_research_20261019"
    )
    failures: list[str] = []
    checks = 0

    def check(condition, label):
        nonlocal checks
        checks += 1
        if not bool(condition):
            failures.append(label)

    def same(actual, expected, label):
        if isinstance(expected, dict):
            check(isinstance(actual, dict), label + ":type")
            if not isinstance(actual, dict):
                return
            for key, value in expected.items():
                check(key in actual, label + ":missing:" + str(key))
                if key in actual:
                    same(actual[key], value, label + "." + str(key))
        elif isinstance(expected, (list, tuple)):
            check(
                actual is not None and len(actual) == len(expected), label + ":length"
            )
            if actual is not None and len(actual) == len(expected):
                for i, (a, b) in enumerate(zip(actual, expected, strict=True)):
                    same(a, b, f"{label}[{i}]")
        elif isinstance(expected, (float, np.floating)):
            check(
                actual is not None
                and math.isclose(
                    float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12
                ),
                label,
            )
        else:
            check(actual == expected, label)

    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    check(report["protocol_sha256"] == manifest_hash, "manifest hash")
    check(report.get("retrospective_reuse") is True, "retrospective label")
    check(report.get("production_changed") is False, "production unchanged label")
    same(report["protected_after"], report["protected_before"], "protected hashes")
    for relative, expected in manifest["source_sha256"].items():
        check(
            hashlib.sha256(
                (root / relative).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            == expected,
            "source:" + relative,
        )

    with np.load(cache_dir / "romania_comparison.npz", allow_pickle=False) as arrays:
        candidate = arrays["candidate"]
        baseline = arrays["baseline"]
        dates = arrays["dates"].astype(str)
        draws = arrays["draws"]
        labels = arrays["labels"].astype(str)
        targets = arrays["targets"]
        names = arrays["candidate_names"].astype(str)
        all_hits = arrays["all_hits"]
    check(candidate.shape == baseline.shape == (len(dates), 2, 8), "RO selected shapes")
    check(all_hits.shape == (len(names), len(dates), 2, 8), "RO all candidate shapes")
    check(set(names) == set(report["methods"]), "candidate name coverage")
    check(len(names) == len(set(names)), "unique candidate names")
    _validated_hits(all_hits.reshape(-1, 8), "all stored hits")
    check(
        np.all(np.diff(all_hits[:, :, 0], axis=-1) >= 0),
        "raw pools have nested hit counts",
    )
    index = {str(name): i for i, name in enumerate(names)}
    finalist = report["finalist"]
    baseline_name = report["baseline"]
    check(
        np.array_equal(candidate, all_hits[index[finalist]]), "selected candidate array"
    )
    check(
        np.array_equal(baseline, all_hits[index[baseline_name]]),
        "selected baseline array",
    )
    dev = labels == "development"
    confirm = labels == "confirmation"
    for label in ("train", "development", "confirmation"):
        rows = labels == label
        check(bool(rows.any()), "nonempty segment:" + label)
        same(
            report["split"][label],
            {
                "n": int(rows.sum()),
                "first": str(dates[rows][0]),
                "last": str(dates[rows][-1]),
            },
            "split:" + label,
        )
    for day in np.unique(dates):
        check(len(set(labels[dates == day])) == 1, "whole date split:" + day)

    for name, position in index.items():
        hits = all_hits[position]
        stored = report["methods"][name]
        for key, mask, mode in (
            ("development", dev, 1),
            ("confirmation", confirm, 1),
            ("all_evaluated", np.ones(len(dates), dtype=bool), 1),
            ("confirmation_raw", confirm, 0),
        ):
            same(stored[key], _metrics_independent(hits[mask, mode]), name + "." + key)
    challengers = report["eligible_challengers"]
    independently_eligible = sorted(
        name
        for name, detail in report["methods"].items()
        if detail["eligible"] and name != baseline_name
    )
    same(challengers, independently_eligible, "eligible challenger list")
    chosen = min(
        challengers,
        key=lambda name: (
            -_metrics_independent(all_hits[index[name], dev, 1])["primary"],
            -_metrics_independent(all_hits[index[name], dev, 1])["mean_hits"],
            name,
        ),
    )
    check(chosen == finalist, "development-only finalist selection")
    p_values = {}
    for name in challengers:
        result = paired_test(
            all_hits[index[name], confirm, 1],
            baseline[confirm, 1],
            dates[confirm],
            bootstrap_samples=2000 if name == finalist else 0,
        )
        same(report["methods"][name]["paired_confirmation"], result, name + ".paired")
        p_values[name] = result["p_value"]
        # This diagnostic uses a different exact integer convolution, not Monte Carlo.
        exact = result["exact_sign_flip_p_diagnostic"]
        mc_error = (
            7 * math.sqrt(max(exact * (1 - exact), 1 / 100000) / 100000) + 1 / 100000
        )
        check(
            abs(result["p_value"] - exact) <= mc_error, name + ".MC vs exact diagnostic"
        )
    for name, adjusted in holm_adjusted(p_values).items():
        same(report["methods"][name]["holm_confirmation"], adjusted, name + ".Holm")
    for k in POOLS:
        same(report["baselines"][str(k)], hypergeom_tails(k), "hypergeom:" + str(k))

    import pandas as pd

    external_files = {
        "germania": "germania_lotto_6aus49.csv",
        "cehia": "cehia_sportka_6din49.csv",
        "slovacia": "slovacia_loto_6din49.csv",
        "polonia": "polonia_lotto_6din49.csv",
        "spania": "spania_la_primitiva_6din49.csv",
        "bulgaria": "bulgaria_toto2_6din49.csv",
    }
    ext_p = {}
    parts = []
    for country, stored in report["external"].items():
        with np.load(
            cache_dir / f"{country}_comparison.npz", allow_pickle=False
        ) as arrays:
            ext_c = arrays["candidate"]
            ext_b = arrays["baseline"]
            ext_dates = arrays["dates"].astype(str)
            ext_draws = arrays["draws"]
            check(
                ext_c.shape == ext_b.shape == (len(ext_dates), 2, 8),
                country + ":shapes",
            )
        external_path = root / "_ISTORIC" / "externe" / external_files[country]
        check(
            hashlib.sha256(external_path.read_bytes()).hexdigest()
            == manifest["data_sha256"][country],
            country + ":CSV hash",
        )
        external_frame = pd.read_csv(external_path)
        external_frame["_date"] = pd.to_datetime(
            external_frame["date"], format="%d-%m-%Y", errors="raise"
        )
        external_frame = external_frame.sort_values("_date", kind="stable").reset_index(
            drop=True
        )
        source_dates = (
            external_frame["_date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10")
        )
        source_prior = np.searchsorted(source_dates, source_dates, side="left")
        source_targets = np.flatnonzero(source_prior >= manifest["warmup_rows"])
        check(
            np.array_equal(ext_dates, source_dates[source_targets]),
            country + ":strict-day target dates",
        )
        source_draws = external_frame[[f"n{i}" for i in range(1, 7)]].to_numpy(
            dtype=np.int64
        )
        check(
            np.array_equal(ext_draws, source_draws[source_targets]),
            country + ":target draws",
        )
        result = paired_test(
            ext_c[:, 1], ext_b[:, 1], ext_dates, bootstrap_samples=2000
        )
        same(
            stored["candidate"],
            _metrics_independent(ext_c[:, 1]),
            country + ":candidate",
        )
        same(
            stored["baseline"], _metrics_independent(ext_b[:, 1]), country + ":baseline"
        )
        same(stored["paired"], result, country + ":paired")
        ext_p[country] = result["p_value"]
        parts.append(
            (
                ext_c[:, 1],
                ext_b[:, 1],
                np.asarray([f"{country}:{day}" for day in ext_dates]),
            )
        )
    for country, adjusted in holm_adjusted(ext_p).items():
        same(report["external"][country]["holm_countries"], adjusted, country + ":Holm")
    pooled = paired_test(
        *(np.concatenate([part[j] for part in parts]) for j in range(3)),
        bootstrap_samples=2000,
    )
    same(report["external_pooled"], pooled, "external pooled")

    # Independently read chronology and strict-day cutoffs; do not call research cache loading.
    import pandas as pd

    sys.path.insert(0, str(root))
    from loto_enterprise.benchmark.methods import METHODS, call_method
    from loto_enterprise.core.ranking import limit_consecutive_run, rank_by_score
    from loto_enterprise.core.score_validation import has_usable_score_variance

    csv_path = root / "_ISTORIC" / "loto_6_49.csv"
    frame = pd.read_csv(csv_path)
    frame["_date"] = pd.to_datetime(frame["date"], format="%d-%m-%Y", errors="raise")
    frame = frame.sort_values("_date", kind="stable").reset_index(drop=True)
    all_dates = frame["_date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10")
    all_draws = frame[[f"n{i}" for i in range(1, 7)]].to_numpy(dtype=np.int64)
    check(
        hashlib.sha256(csv_path.read_bytes()).hexdigest()
        == manifest["data_sha256"]["romania"],
        "RO CSV hash",
    )
    check(np.array_equal(all_dates[targets], dates), "target dates")
    check(np.array_equal(all_draws[targets], draws), "target draws")
    prior = np.searchsorted(all_dates, all_dates, side="left")
    check(
        np.array_equal(np.flatnonzero(prior >= manifest["warmup_rows"]), targets),
        "strict-day target eligibility",
    )
    unique = np.unique(all_dates)
    dev_start = unique[int(len(unique) * 0.6)]
    confirm_start = unique[int(len(unique) * 0.8)]
    independent_labels = np.where(
        dates < dev_start,
        "train",
        np.where(dates < confirm_start, "development", "confirmation"),
    )
    check(np.array_equal(labels, independent_labels), "independent 60/20/20 split")
    sample_indices = set(map(int, np.linspace(0, len(targets) - 1, 7)))
    sample_indices.update(
        int(np.flatnonzero(labels == label)[0])
        for label in ("development", "confirmation")
    )
    directly_checked = {baseline_name: 0, finalist: 0}
    for name in sorted(set((baseline_name, finalist))):
        if name not in METHODS:
            continue
        for row in sorted(sample_indices):
            target = int(targets[row])
            cutoff = int(prior[target])
            check(
                cutoff < target + 1 and (all_dates[:cutoff] < all_dates[target]).all(),
                f"strict prefix:{name}:{target}",
            )
            scores, _ = call_method(
                name, all_draws[:cutoff], 49, history_cutoffs=prior[:cutoff]
            )
            if not has_usable_score_variance(scores):
                scores, _ = call_method("frequency", all_draws[:cutoff], 49)
            ranked = rank_by_score(scores, 49)
            actual = set(map(int, all_draws[target]))
            independent = np.zeros((2, 8), dtype=np.int64)
            for mode, limit in enumerate((0, 2)):
                for column, k in enumerate(POOLS):
                    pool, _, _ = limit_consecutive_run(ranked, k, limit)
                    independent[mode, column] = len(actual.intersection(pool))
            check(
                np.array_equal(independent, all_hits[index[name], row]),
                f"direct prefix hit count:{name}:{target}",
            )
            directly_checked[name] += 1

    return {
        "report": str(report_path),
        "protocol_sha256": manifest_hash,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
        "candidate_methods_recounted": len(names),
        "paired_confirmation_methods_recounted": len(challengers),
        "external_archives_recounted": len(report["external"]),
        "direct_prefix_samples": directly_checked,
        "online_finalist_direct_prefix_not_recomputed": finalist not in METHODS,
        "retrospective_reuse": True,
        "limits": "Independent array/statistics recount and sampled registry prefix checks; this does not create virgin or prospective evidence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify", type=Path, help="Published pool-screen JSON report")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument(
        "--output", type=Path, help="Optional independent validation JSON"
    )
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        print("Independent paired-statistics checks passed.")
    if args.verify:
        result = verify_report(args.verify, args.cache_dir)
        text = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        print(text, end="")
        return 0 if result["passed"] else 1
    if args.self_test:
        return 0
    parser.error("provide --self-test or --verify REPORT.json")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
