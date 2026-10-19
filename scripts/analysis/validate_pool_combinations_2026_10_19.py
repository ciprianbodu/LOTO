"""Independent replay/audit for the retrospective 6/49 combination screen.

No old experimental result is loaded. Only the already frozen, outcome-agnostic
paired-statistics mathematics is reused. A selector choice at an origin sees
only the preceding 20% of original unique dates. The 80..100% trace is a
prequential retrospective evaluation, not a virgin holdout. No IID-search null
calibration is claimed, and no production state is modified here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# This module supplies mathematics only; it never reads its old report unless
# its separate verify_report() function is called, which this module does not do.
from validate_pool_screen_2026_10_19 import (
    holm_adjusted,
    hypergeom_tails,
    paired_test,
)

POOLS = tuple(range(9, 17))
STATISTICS_SOURCE = HERE / "validate_pool_screen_2026_10_19.py"


def _dates(values, name: str) -> np.ndarray:
    source = np.asarray(values)
    if source.ndim != 1 or not len(source):
        raise ValueError(f"{name} must be a nonempty one-dimensional date array")
    try:
        dates = source.astype("datetime64[D]")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} contains invalid dates") from exc
    if np.isnat(dates).any():
        raise ValueError(f"{name} contains missing dates")
    if np.any(dates[1:] < dates[:-1]):
        raise ValueError(f"{name} is not chronological")
    return dates


def _indices(values, length: int) -> np.ndarray:
    source = np.asarray(values)
    if source.ndim != 1 or not len(source) or source.dtype.kind == "b":
        raise ValueError("target_indices must be a nonempty integer vector")
    try:
        numeric = source.astype(np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_indices must be integers") from exc
    if not np.isfinite(numeric).all() or not np.equal(numeric, np.floor(numeric)).all():
        raise ValueError("target_indices must be finite integers")
    targets = numeric.astype(np.int64)
    if (
        np.any(targets < 0)
        or np.any(targets >= length)
        or np.any(np.diff(targets) <= 0)
    ):
        raise ValueError("target_indices must be strictly increasing and in range")
    return targets


def _fraction(value, name: str) -> Fraction:
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if not 0 < result < 1:
        raise ValueError(f"{name} must be strictly between zero and one")
    return result


def _boundary(n_days: int, value: Fraction) -> int:
    # Avoid int(N*(0.7-0.2)): the float subtraction is below 0.5 and can include
    # an extra day. Fractions keep every predeclared decimal boundary exact.
    return (n_days * value.numerator) // value.denominator


def replay_selector(
    full_dates,
    target_indices,
    bank_hits,
    candidate_ids,
    complexity=None,
    eligibility_mask=None,
    ties=None,
    baseline_id: str = "markov_lag2",
    origins=(0.7, 0.8, 0.9),
    lookback=0.2,
    tie_fraction_limit: float = 0.5,
) -> dict:
    """Reconstruct the fixed rolling selector from past observations only.

    bank_hits has shape [candidates, targets, 8] for a SINGLE explicitly chosen
    mode (primary consecutive2; raw uses the SAME selected_ids afterward).
    eligibility_mask[c,t] means that row's original candidate scores were usable
    before any fallback. Optional ties[c,t,k] marks a raw top-K boundary tie.
    An origin excludes challengers with any unusable score in its PAST window
    or a tie fraction >= the predeclared limit at any pool size in that window.

    The baseline is the explicit reference/reserve and remains available; any
    baseline gate issue is exposed in its diagnostics. Choices maximize the
    INTEGER count of 4+ indicators in the common window. Exact ties prefer the
    baseline, then smaller predeclared complexity, then candidate ID. The choice
    stays fixed in the next block. If its future scores use a fallback, the
    supplied bank pipeline's actual hits remain in the trace and the occurrence
    is reported; future validity never changes the earlier choice.

    Original full_dates determine all percentage boundaries, including repeated
    same-day draws. All rows of every selection/evaluation window must be present
    in target_indices; a partial cached window raises instead of biasing ranking.
    Arrays returned outside the active blocks use the baseline, with active_mask
    making that convention explicit. Inputs are never mutated.
    """
    dates = _dates(full_dates, "full_dates")
    targets = _indices(target_indices, len(dates))
    ids = tuple(map(str, candidate_ids))
    if not ids or len(ids) != len(set(ids)) or baseline_id not in ids:
        raise ValueError("candidate IDs must be unique and include the baseline")
    hits = np.asarray(bank_hits)
    shape = (len(ids), len(targets), len(POOLS))
    if hits.shape != shape:
        raise ValueError(f"bank_hits must have shape {shape}")
    numeric = np.asarray(hits, dtype=np.float64)
    if (
        not np.isfinite(numeric).all()
        or not ((numeric >= 0) & (numeric <= 6) & (numeric == np.floor(numeric))).all()
    ):
        raise ValueError("bank_hits must contain finite integer hit counts in 0..6")
    hits = numeric.astype(np.int8)
    if complexity is None:
        complexity_values = np.ones(len(ids))
    else:
        complexity_values = np.asarray(complexity, dtype=np.float64)
        if (
            complexity_values.shape != (len(ids),)
            or not (np.isfinite(complexity_values) & (complexity_values >= 0)).all()
        ):
            raise ValueError("complexity must be a finite nonnegative candidate vector")
    valid = np.ones(shape[:2], dtype=bool)
    if eligibility_mask is not None:
        supplied = np.asarray(eligibility_mask)
        if supplied.shape != shape[:2] or not ((supplied == 0) | (supplied == 1)).all():
            raise ValueError(
                "eligibility_mask must be a boolean [candidates, targets] matrix"
            )
        valid = supplied.astype(bool)
    tied = np.zeros(shape, dtype=bool)
    if ties is not None:
        supplied = np.asarray(ties)
        if supplied.shape != shape or not ((supplied == 0) | (supplied == 1)).all():
            raise ValueError("ties must be a boolean [candidates, targets, 8] array")
        tied = supplied.astype(bool)
    if not np.isfinite(tie_fraction_limit) or not 0 < tie_fraction_limit <= 1:
        raise ValueError("tie_fraction_limit must be in (0,1]")
    origin_values = tuple(_fraction(value, "origin") for value in origins)
    lookback_value = _fraction(lookback, "lookback")
    if not origin_values or tuple(sorted(set(origin_values))) != origin_values:
        raise ValueError("origins must be strictly increasing")
    if any(origin <= lookback_value for origin in origin_values):
        raise ValueError("every origin must have a complete positive past window")
    unique, full_day = np.unique(dates, return_inverse=True)
    target_day = full_day[targets]
    baseline = ids.index(baseline_id)
    selected = np.full(len(targets), baseline, dtype=np.int64)
    trace = hits[baseline].copy()
    active = np.zeros(len(targets), dtype=bool)
    blocks = []
    stops = origin_values[1:] + (Fraction(1),)
    for origin, stop in zip(origin_values, stops, strict=True):
        lo = _boundary(len(unique), origin - lookback_value)
        cutoff = _boundary(len(unique), origin)
        end = _boundary(len(unique), stop)
        if not 0 <= lo < cutoff < end <= len(unique):
            raise ValueError("too few original dates for the requested split")
        window = (target_day >= lo) & (target_day < cutoff)
        evaluation = (target_day >= cutoff) & (target_day < end)
        expected_window = np.flatnonzero((full_day >= lo) & (full_day < cutoff))
        expected_evaluation = np.flatnonzero((full_day >= cutoff) & (full_day < end))
        if not np.array_equal(targets[window], expected_window):
            raise ValueError("target_indices omit part of a past selection window")
        if not np.array_equal(targets[evaluation], expected_evaluation):
            raise ValueError("target_indices omit part of an evaluation block")
        if np.any(active & evaluation):
            raise ValueError("evaluation blocks overlap")
        event_counts = np.count_nonzero(hits[:, window] >= 4, axis=(1, 2))
        invalid_rows = np.count_nonzero(~valid[:, window], axis=1)
        tie_rates = tied[:, window].mean(axis=1)
        eligible = (invalid_rows == 0) & ~(tie_rates >= tie_fraction_limit).any(axis=1)
        original_baseline_gate = bool(eligible[baseline])
        eligible[baseline] = True
        available = np.flatnonzero(eligible)
        chosen = min(
            available,
            key=lambda index: (
                -int(event_counts[index]),
                0 if index == baseline else 1,
                float(complexity_values[index]),
                ids[index],
            ),
        )
        selected[evaluation] = chosen
        trace[evaluation] = hits[chosen, evaluation]
        active |= evaluation
        blocks.append(
            {
                "origin": float(origin),
                "stop": float(stop),
                "window_day_indices": [lo, cutoff],
                "evaluation_day_indices": [cutoff, end],
                "window_first": str(unique[lo]),
                "window_last": str(unique[cutoff - 1]),
                "evaluation_first": str(unique[cutoff]),
                "evaluation_last": str(unique[end - 1]),
                "window_rows": int(window.sum()),
                "evaluation_rows": int(evaluation.sum()),
                "chosen_id": ids[chosen],
                "chosen_index": int(chosen),
                "chosen_past_primary": int(event_counts[chosen])
                / (int(window.sum()) * 8),
                "baseline_past_primary": int(event_counts[baseline])
                / (int(window.sum()) * 8),
                "selected_future_invalid_rows": int(
                    np.count_nonzero(~valid[chosen, evaluation])
                ),
                "baseline_reference_override": not original_baseline_gate,
                "gates": {
                    name: {
                        "eligible": bool(eligible[index]),
                        "invalid_rows_in_past_window": int(invalid_rows[index]),
                        "tie_fractions_in_past_window": tie_rates[index].tolist(),
                        "past_4plus_count": int(event_counts[index]),
                        "complexity": float(complexity_values[index]),
                    }
                    for index, name in enumerate(ids)
                },
            }
        )
    primary_start = _boundary(len(unique), Fraction(4, 5))
    diagnostic_start = _boundary(len(unique), Fraction(7, 10))
    primary = active & (target_day >= primary_start)
    diagnostic = (
        active & (target_day >= diagnostic_start) & (target_day < primary_start)
    )
    return {
        "trace_hits": trace,
        "selected_indices": selected,
        "selected_ids": np.asarray(ids)[selected],
        "active_mask": active,
        "primary_mask": primary,
        "diagnostic_mask": diagnostic,
        "target_dates": dates[targets].astype(str),
        "blocks": blocks,
        "baseline_id": baseline_id,
        "pools": list(POOLS),
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
    }


def _metrics(hits) -> dict:
    values = np.asarray(hits)
    if values.ndim != 2 or values.shape[1] != 8 or not len(values):
        raise ValueError("metrics require nonempty [rows,8] hits")
    return {
        "n": len(values),
        "primary": np.count_nonzero(values >= 4) / values.size,
        "rate3plus": np.count_nonzero(values >= 3) / values.size,
        "mean_hits": float(values.sum() / values.size),
        "zero_rate": np.count_nonzero(values == 0) / values.size,
        "events4": np.count_nonzero(values >= 4, axis=0).tolist(),
        "events3": np.count_nonzero(values >= 3, axis=0).tolist(),
        "zero": np.count_nonzero(values == 0, axis=0).tolist(),
        "mean_hits_by_k": (values.sum(axis=0) / len(values)).tolist(),
    }


def _rank_one_based(values) -> np.ndarray:
    """Independent average-rank implementation; no bank/scipy ranking call."""
    values = np.asarray(values)
    order = np.argsort(values, kind="stable")
    out = np.empty(len(values), dtype=float)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        out[order[start:stop]] = (start + 1 + stop) / 2.0
        start = stop
    return out


def formula_from_descriptor(spec: dict, parent_scores: dict) -> tuple[np.ndarray, bool]:
    """Recalculate one row from its signed descriptor, independently of bank."""
    name = spec["name"]
    if spec.get("family") in ("reference", "control"):
        return np.asarray(parent_scores[name], dtype=float).copy(), False
    parents = spec["parent_names"]
    ranks = {parent: _rank_one_based(parent_scores[parent]) for parent in parents}
    quality = {parent: (rank - 1.0) / 48.0 for parent, rank in ranks.items()}
    positions = {parent: 50.0 - rank for parent, rank in ranks.items()}
    anchor = "markov_lag2"
    rule = spec["rule"]
    if rule == "weighted_mean_rank":
        weight = spec["markov_weight"]
        value = weight * quality[anchor] + (1.0 - weight) * quality[parents[1]]
    elif rule == "median_mean_rank":
        value = np.median(np.stack([quality[parent] for parent in parents]), axis=0)
    elif rule == "trimmed_mean_rank":
        ordered = np.sort(np.stack([quality[parent] for parent in parents]), axis=0)
        trim = int(len(parents) * spec["trim_fraction"])
        value = ordered[trim : len(parents) - trim].mean(axis=0)
    elif rule == "sum_reciprocal_rank":
        value = np.sum(
            np.stack(
                [1.0 / (spec["offset"] + positions[parent]) for parent in parents]
            ),
            axis=0,
        )
    elif rule == "negative_geometric_mean_position":
        value = -np.exp(
            np.stack([np.log(positions[parent]) for parent in parents]).mean(axis=0)
        )
    elif rule == "markov_plus_orthogonal_rank_residual":
        base_units = (2 * ranks[anchor] - 50).astype(np.int64)
        other_units = (2 * ranks[parents[1]] - 50).astype(np.int64)
        norm = sum(int(value) ** 2 for value in base_units)
        product = sum(
            int(a) * int(b) for a, b in zip(base_units, other_units, strict=True)
        )
        coefficient = product / norm if norm else 0.0
        residual = (other_units - coefficient * base_units) / 96.0
        value = quality[anchor] + spec["residual_weight"] * residual
    else:
        raise ValueError(f"unknown formula rule: {rule}")
    flat = bool(np.ptp(value) <= spec.get("flat_epsilon", 1e-12))
    if flat:
        value = np.asarray(parent_scores["frequency"], dtype=float).copy()
    return value, flat


def _compact(blocks, baseline: str) -> list:
    out = []
    for block in blocks:
        gates = block["gates"]
        out.append(
            {
                **{key: value for key, value in block.items() if key != "gates"},
                "eligible_count": sum(gate["eligible"] for gate in gates.values()),
                "chosen_gate": gates[block["chosen_id"]],
                "baseline_gate": gates[baseline],
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


def verify_report(report_path, cache_dir=None, *, workspace_root=None) -> dict:
    """Independent metric, inference, selector and sampled formula/prefix audit."""
    import pandas as pd

    root = (
        Path(workspace_root).resolve()
        if workspace_root is not None
        else HERE.parents[1]
    )
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path = report_path.with_name(report_path.stem + "_protocol.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    identity = hashlib.sha256(manifest_bytes).hexdigest()
    cache_dir = (
        Path(cache_dir).resolve()
        if cache_dir is not None
        else root / "bench_results/pool649_combinations_20261019"
    )
    failures = []
    checks = 0

    def check(condition, label):
        nonlocal checks
        checks += 1
        if not bool(condition):
            failures.append(label)

    def same(actual, expected, label):
        if isinstance(expected, dict):
            check(isinstance(actual, dict), label + ":type")
            if isinstance(actual, dict):
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

    check(report["protocol_sha256"] == identity, "manifest identity")
    check(report.get("retrospective_reuse") is True, "retrospective label")
    check(report.get("iid_search_calibrated") is False, "no IID calibration claim")
    check(report.get("production_changed") is False, "production unchanged label")
    same(report["protected_after"], report["protected_before"], "protected hashes")
    for relative, expected in manifest["source_sha256"].items():
        raw = (root / relative).read_bytes().replace(b"\r\n", b"\n")
        check(hashlib.sha256(raw).hexdigest() == expected, "source:" + relative)
    descriptors = manifest["candidate_specs"]
    ids = [spec["name"] for spec in descriptors]
    descriptor_by_id = {spec["name"]: spec for spec in descriptors}
    baseline = manifest["baseline"]
    baseline_index = ids.index(baseline)
    check(
        report["candidate_count"] == manifest["candidate_count"] == len(ids) - 2,
        "static bank count",
    )
    check(set(report["static_combinations"]) == set(ids[2:]), "complete static family")
    paths = {
        "romania": "_ISTORIC/loto_6_49.csv",
        "germania": "_ISTORIC/externe/germania_lotto_6aus49.csv",
        "cehia": "_ISTORIC/externe/cehia_sportka_6din49.csv",
        "slovacia": "_ISTORIC/externe/slovacia_loto_6din49.csv",
        "polonia": "_ISTORIC/externe/polonia_lotto_6din49.csv",
        "spania": "_ISTORIC/externe/spania_la_primitiva_6din49.csv",
        "bulgaria": "_ISTORIC/externe/bulgaria_toto2_6din49.csv",
    }
    check(set(report["countries"]) == set(manifest["data_sha256"]), "country coverage")
    seed = int(manifest["seed"])
    permutations = int(manifest["inference"]["permutations"])
    bootstrap = int(manifest["inference"]["bootstrap_samples"])

    def paired(candidate, base, dates, *, ci=True):
        return paired_test(
            candidate,
            base,
            dates,
            threshold=4,
            seed=seed,
            permutations=permutations,
            bootstrap_samples=bootstrap if ci else 0,
        )

    country_data = {}
    primary_data = {}
    country_p = {}
    for country, stored in report["countries"].items():
        with np.load(cache_dir / f"{country}_bank.npz", allow_pickle=False) as arrays:
            bank = {
                key: arrays[key]
                for key in (
                    "candidate_ids",
                    "full_dates",
                    "targets",
                    "draws",
                    "hits",
                    "ties",
                    "valid",
                    "complexity",
                    "selected_indices",
                    "selector_hits",
                    "primary_mask",
                    "diagnostic_mask",
                )
            }
            check(str(arrays["identity"].item()) == identity, country + ":NPZ identity")
        check(np.array_equal(bank["candidate_ids"], ids), country + ":ID order")
        check(
            np.array_equal(
                bank["complexity"], [spec["complexity"] for spec in descriptors]
            ),
            country + ":complexity",
        )
        path = root / paths[country]
        check(
            hashlib.sha256(path.read_bytes()).hexdigest()
            == manifest["data_sha256"][country],
            country + ":CSV SHA",
        )
        frame = pd.read_csv(path)
        frame["_date"] = pd.to_datetime(
            frame["date"], format="%d-%m-%Y", errors="raise"
        )
        frame = frame.sort_values("_date", kind="stable").reset_index(drop=True)
        dates = frame["_date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10")
        draws = frame[[f"n{i}" for i in range(1, 7)]].to_numpy(dtype=np.int64)
        check(
            ((draws >= 1) & (draws <= 49)).all()
            and (np.diff(np.sort(draws, axis=1), axis=1) > 0).all(),
            country + ":valid source draws",
        )
        check(np.array_equal(dates, bank["full_dates"]), country + ":full chronology")
        unique = np.unique(dates)
        start = max(
            manifest["minimum_warmup_rows"],
            int(np.searchsorted(dates, unique[len(unique) // 2], side="left")),
        )
        prior = np.searchsorted(dates, dates, side="left")
        targets = np.flatnonzero(prior >= start)
        check(np.array_equal(targets, bank["targets"]), country + ":strict-day targets")
        check(np.array_equal(draws[targets], bank["draws"]), country + ":target draws")
        check(
            bank["hits"].shape == (len(ids), len(targets), 2, 8), country + ":hit shape"
        )
        check(
            np.all(np.diff(bank["hits"], axis=-1) >= 0),
            country + ":nested pool hit counts",
        )
        replay = replay_selector(
            dates,
            targets,
            bank["hits"][:, :, 1],
            ids,
            complexity=bank["complexity"],
            eligibility_mask=bank["valid"],
            ties=bank["ties"],
            baseline_id=baseline,
            origins=manifest["selection"]["origins"],
            lookback=manifest["selection"]["lookback"],
        )
        same(
            stored["blocks"], _compact(replay["blocks"], baseline), country + ":blocks"
        )
        # Separate integer-percent implementation audits the replay implementation.
        day_indices = np.searchsorted(unique, dates[targets])
        manual = np.full(len(targets), baseline_index, dtype=int)
        for percentage in (70, 80, 90):
            lo, hi, stop = (
                len(unique) * (percentage - 20) // 100,
                len(unique) * percentage // 100,
                len(unique) * (percentage + 10) // 100,
            )
            past = (day_indices >= lo) & (day_indices < hi)
            future = (day_indices >= hi) & (day_indices < stop)
            counts = [
                int(np.count_nonzero(bank["hits"][i, past, 1] >= 4))
                for i in range(len(ids))
            ]
            available = [
                i
                for i in range(len(ids))
                if i == baseline_index
                or (
                    bool(bank["valid"][i, past].all())
                    and not (bank["ties"][i, past].mean(axis=0) >= 0.5).any()
                )
            ]
            chosen = sorted(
                available,
                key=lambda i: (
                    -counts[i],
                    i != baseline_index,
                    int(bank["complexity"][i]),
                    ids[i],
                ),
            )[0]
            manual[future] = chosen
        check(
            np.array_equal(manual, replay["selected_indices"]),
            country + ":independent integer replay",
        )
        check(
            np.array_equal(bank["selected_indices"], manual),
            country + ":selected indices",
        )
        for key in ("primary_mask", "diagnostic_mask"):
            check(np.array_equal(bank[key], replay[key]), country + ":" + key)
        selector = bank["hits"][manual, np.arange(len(targets))]
        check(
            np.array_equal(selector, bank["selector_hits"]),
            country + ":same raw/constrained choices",
        )
        primary = replay["primary_mask"]
        diag = replay["diagnostic_mask"]
        base = bank["hits"][baseline_index]
        pdays = dates[targets][primary]
        paired_result = paired(selector[primary, 1], base[primary, 1], pdays)
        expected = {
            "input_rows": len(dates),
            "target_rows": len(targets),
            "start_history_rows": start,
            "primary": {
                "selector": _metrics(selector[primary, 1]),
                "baseline": _metrics(base[primary, 1]),
                "paired": paired_result,
            },
            "diagnostic": {
                "selector": _metrics(selector[diag, 1]),
                "baseline": _metrics(base[diag, 1]),
            },
            "raw": {
                "selector": _metrics(selector[primary, 0]),
                "baseline": _metrics(base[primary, 0]),
            },
            "first_primary": str(pdays[0]),
            "last_primary": str(pdays[-1]),
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
                np.count_nonzero(
                    ~bank["valid"][manual, np.arange(len(targets))][primary]
                )
            ),
        }
        same(stored, expected, country)
        if country != "romania":
            country_p[country] = paired_result["p_value"]
        primary_data[country] = (bank["hits"][:, primary], selector[primary], pdays)
        country_data[country] = (bank, dates, draws, prior, replay)
    for country, adjusted in holm_adjusted(country_p).items():
        same(
            report["countries"][country]["selector_country_holm"],
            adjusted,
            country + ":country Holm",
        )
    external_countries = [
        country for country in report["countries"] if country != "romania"
    ]
    external_hits = np.concatenate(
        [primary_data[country][0] for country in external_countries], axis=1
    )
    external_selector = np.concatenate(
        [primary_data[country][1] for country in external_countries]
    )
    external_dates = np.concatenate(
        [
            np.asarray([f"{country}:{date}" for date in primary_data[country][2]])
            for country in external_countries
        ]
    )
    external_base = external_hits[baseline_index]
    external_paired = paired(
        external_selector[:, 1], external_base[:, 1], external_dates
    )
    same(
        report["external_pooled"],
        {
            "selector": _metrics(external_selector[:, 1]),
            "baseline": _metrics(external_base[:, 1]),
            "raw_selector": _metrics(external_selector[:, 0]),
            "raw_baseline": _metrics(external_base[:, 0]),
            "paired": external_paired,
        },
        "external pooled",
    )
    static_p = {"romania": {}, "external": {}}
    for index, spec in enumerate(descriptors[2:], start=2):
        name = spec["name"]
        same(report["static_combinations"][name]["spec"], spec, name + ":spec")
        for region, hits, dates in (
            ("romania", primary_data["romania"][0], primary_data["romania"][2]),
            ("external", external_hits, external_dates),
        ):
            result = paired(
                hits[index, :, 1], hits[baseline_index, :, 1], dates, ci=False
            )
            expected = {
                "limited": _metrics(hits[index, :, 1]),
                "raw": _metrics(hits[index, :, 0]),
                "difference": result["mean_difference"],
                "p_value": result["p_value"],
                "exact_sign_flip_p": result["exact_sign_flip_p_diagnostic"],
                "wins": result["wins"],
                "losses": result["losses"],
                "days": result["days"],
                "invalid_primary_rows": sum(
                    int(
                        np.count_nonzero(
                            ~values[0]["valid"][index, values[4]["primary_mask"]]
                        )
                    )
                    for country_name, values in country_data.items()
                    if (country_name == "romania") == (region == "romania")
                ),
            }
            same(
                report["static_combinations"][name][region],
                expected,
                name + ":" + region,
            )
            static_p[region][name] = result["p_value"]
    for region, values in static_p.items():
        for name, adjusted in holm_adjusted(values).items():
            same(
                report["static_combinations"][name][region]["holm"],
                adjusted,
                name + ":" + region + ":Holm",
            )
    for k in POOLS:
        same(report["hypergeometric"][str(k)], hypergeom_tails(k), f"hypergeom:{k}")
    expected_gate = all(
        part["mean_difference"] > 0 and part["p_value"] < 0.05
        for part in (
            report["countries"]["romania"]["primary"]["paired"],
            external_paired,
        )
    )
    check(
        report["selector_superiority_gate_passed"] == expected_gate,
        "selector superiority gate",
    )

    sys.path.insert(0, str(root))
    from loto_enterprise.benchmark.methods import call_method
    from loto_enterprise.core.ranking import limit_consecutive_run, rank_by_score
    from loto_enterprise.core.score_validation import has_usable_score_variance

    rng = np.random.default_rng(seed)
    sampled_static = [
        ids[2 + int(i)]
        for i in rng.choice(len(ids) - 2, min(3, len(ids) - 2), replace=False)
    ]
    prefix_counts = {}
    for country, (bank, dates, draws, prior, replay) in country_data.items():
        targets = bank["targets"]
        unique = np.unique(dates)
        sample_targets = [int(targets[0]), len(dates) - 2, len(dates) - 1]
        for percent in (70, 80, 90):
            boundary_date = unique[len(unique) * percent // 100]
            first = int(np.searchsorted(dates, boundary_date, side="left"))
            sample_targets.extend([first - 1, first])
        target_to_row = {int(target): row for row, target in enumerate(targets)}
        sample_targets = sorted(set(t for t in sample_targets if t in target_to_row))
        sample_ids = set(
            [baseline, "frequency"]
            + sampled_static
            + [block["chosen_id"] for block in replay["blocks"]]
        )
        count = 0
        for target in sample_targets:
            row = target_to_row[target]
            cutoff = int(prior[target])
            check(
                (dates[:cutoff] < dates[target]).all(),
                f"{country}:strict prefix:{target}",
            )
            needed = {"frequency"}
            for name in sample_ids:
                needed.update(descriptor_by_id[name]["parent_names"])
            parent_scores = {}
            parent_invalid = {}
            for name in sorted(needed):
                try:
                    raw, _ = call_method(
                        name, draws[:cutoff], 49, history_cutoffs=prior[:cutoff]
                    )
                    usable = (
                        len(raw) == 49
                        and set(raw) == set(range(1, 50))
                        and has_usable_score_variance(raw)
                    )
                except Exception:
                    usable = False
                parent_invalid[name] = not usable
                if not usable:
                    raw, _ = call_method(
                        "frequency", draws[:cutoff], 49, history_cutoffs=prior[:cutoff]
                    )
                parent_scores[name] = np.asarray(
                    [raw[n] for n in range(1, 50)], dtype=float
                )
            for name in sorted(sample_ids):
                spec = descriptor_by_id[name]
                scores, flat = formula_from_descriptor(spec, parent_scores)
                invalid = flat or any(
                    parent_invalid[parent] for parent in spec["parent_names"]
                )
                index = ids.index(name)
                check(
                    bool(bank["valid"][index, row]) == (not invalid),
                    f"{country}:valid:{name}:{target}",
                )
                ranking = rank_by_score(dict(enumerate(scores, start=1)), 49)
                actual = set(map(int, draws[target]))
                observed = np.zeros((2, 8), dtype=np.int8)
                observed_ties = np.zeros(8, dtype=bool)
                for j, k in enumerate(POOLS):
                    observed_ties[j] = (
                        abs(scores[ranking[k - 1] - 1] - scores[ranking[k] - 1])
                        <= 1e-12
                    )
                    for mode, limit in enumerate((0, 2)):
                        pool, _, _ = limit_consecutive_run(ranking, k, limit)
                        observed[mode, j] = len(actual.intersection(pool))
                check(
                    np.array_equal(observed, bank["hits"][index, row]),
                    f"{country}:direct hits:{name}:{target}",
                )
                check(
                    np.array_equal(observed_ties, bank["ties"][index, row]),
                    f"{country}:direct ties:{name}:{target}",
                )
                count += 1
        prefix_counts[country] = {
            "target_prefixes": len(sample_targets),
            "candidate_prefix_checks": count,
            "candidate_ids": sorted(sample_ids),
            "random_static_ids": sampled_static,
        }
    return {
        "report": str(report_path),
        "protocol_sha256": identity,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
        "country_banks": len(country_data),
        "static_candidates": len(ids) - 2,
        "static_holm_families": {
            region: len(values) for region, values in static_p.items()
        },
        "direct_prefix_audit": prefix_counts,
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "limits": "Array/statistic recount, independent selector implementation and sampled parent/formula prefixes; no virgin or prospective evidence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        dates = np.arange("2020-01-01", "2020-04-10", dtype="datetime64[D]")
        targets = np.arange(50, 100)
        bank = np.zeros((2, len(targets), 8), dtype=np.int8)
        bank[1, :20] = 4
        result = replay_selector(dates, targets, bank, ["markov_lag2", "combo"])
        assert result["blocks"][0]["window_day_indices"] == [50, 70]
        assert result["blocks"][0]["chosen_id"] == "combo"
        assert int(result["primary_mask"].sum()) == 20
        print("Independent rolling-selector checks passed.")
    if args.verify:
        result = verify_report(args.verify, args.cache_dir)
        text = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.write_bytes(text.encode("utf-8"))
        print(text, end="")
        return 0 if result["passed"] else 1
    if args.self_test:
        return 0
    parser.error("provide --self-test or --verify REPORT.json")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
