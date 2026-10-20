"""Independent audit of the separate retrospective adaptive 6/49 study.

The legacy report key ``static_combinations`` contains FIXED ALGORITHMS whose
states may learn online; their scores are not static. The frozen mathematical
and selector primitives are reused without reading any earlier research result.
A separate integer replay, full recount, canonical pool checks and truncated
prefix replays check the new artifact. No production state is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from validate_pool_combinations_2026_10_19 import _compact, _metrics, replay_selector
from validate_pool_screen_2026_10_19 import holm_adjusted, hypergeom_tails, paired_test

POOLS = tuple(range(9, 17))
EXPERTS = ("frequency", "markov_lag2", "ewma_hl30")


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
        else Path(tempfile.gettempdir()) / "loto-pool-adaptive-20261019/cache"
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
        "bulgaria": "_ISTORIC/externe/bulgaria_toto2_6din49.csv",
    }
    check(
        set(report["countries"])
        == set(manifest["data_sha256"])
        == {"romania", "bulgaria"},
        "Romania+Bulgaria scope",
    )
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
                    "candidate_scores",
                    "expert_names",
                    "expert_scores",
                    "expert_invalid",
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

    prediction_audit = {}
    for country, values in country_data.items():
        prediction_audit[country] = _audit_predictions(
            country, values, descriptors, check
        )
    return {
        "report": str(report_path),
        "protocol_sha256": identity,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
        "country_banks": len(country_data),
        "algorithm_candidates": len(ids) - 2,
        "holm_families": {region: len(values) for region, values in static_p.items()},
        "prediction_audit": prediction_audit,
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "limits": (
            "Full array/statistic recount and independent integer selector replay; "
            "sampled canonical pools and truncated algorithm/expert prefixes. "
            "Prefix replay checks causal behavior, not an independent derivation "
            "of every model formula. No virgin or prospective evidence, and no "
            "calibration of the entire historical search."
        ),
    }


def _prefix_inputs(dates, draws, prior, targets, expert_scores, row):
    """Retain ORIGINAL target eligibility when cutting the raw CSV at a target.

    Recomputing the 50% boundary on a shorter history would change the online
    calibrator's training start and therefore test a different algorithm.
    The target draw is deliberately present: its label must not affect its own
    prediction, including when other draws share that calendar date.
    """
    stop = int(targets[row]) + 1
    prefix_dates = np.asarray(dates[:stop])
    groups = np.zeros(stop, dtype=np.int64)
    groups[1:] = np.cumsum(prefix_dates[1:] != prefix_dates[:-1])
    experts = {
        name: np.asarray(expert_scores[i, : row + 1]) for i, name in enumerate(EXPERTS)
    }
    data = {
        "dates": prefix_dates,
        "draws": np.asarray(draws[:stop]),
        "groups": groups,
        "prior": np.asarray(prior[:stop]),
        "targets": np.asarray(targets[: row + 1]),
        "frequency_past": experts["frequency"],
    }
    return data, experts


def _required_experts(spec, descriptors):
    if spec["name"] in EXPERTS:
        return {spec["name"]}
    # Frequency supplied solely as the flat-score reserve is not consumed by
    # an otherwise usable formula. Its own fallback mask already marks use.
    required = set()
    for name in spec["parent_names"]:
        if name in EXPERTS:
            required.add(name)
        else:
            required.update(_required_experts(descriptors[name], descriptors))
    return required


def _mean_ranks(values):
    """Ascending average ranks, using an explicit oracle instead of rankdata."""
    values = np.asarray(values)
    ranked = np.empty_like(values, dtype=np.float64)
    for row, vector in enumerate(values):
        order = np.argsort(vector, kind="stable")
        first = 0
        while first < len(order):
            stop = first + 1
            while stop < len(order) and vector[order[stop]] == vector[order[first]]:
                stop += 1
            ranked[row, order[first:stop]] = (first + 1 + stop) / 2.0
            first = stop
    return ranked


def _generate_with_blends(data, experts):
    from pool_adaptive_candidates_2026_10_19 import generate

    result = {}
    markov_ranks = _mean_ranks(experts["markov_lag2"])
    for name, _family, predictions, fallback in generate(data, experts):
        result[name] = (predictions, fallback)
        mixed = (markov_ranks + _mean_ranks(predictions) - 2.0) / 96.0
        flat = np.ptp(mixed, axis=1) <= 1e-12
        mixed[flat] = experts["frequency"][flat]
        result["mix_markov50__" + name] = (mixed, fallback | flat)
    return result


def _audit_predictions(country, values, descriptors, check):
    """Check saved scores, canonical selection and strict target-day prefixes."""
    from loto_enterprise.benchmark.methods import call_method
    from loto_enterprise.core.ranking import limit_consecutive_run, rank_by_score
    from loto_enterprise.core.score_validation import has_usable_score_variance

    bank, dates, draws, prior, _replay = values
    ids = [spec["name"] for spec in descriptors]
    index = {name: i for i, name in enumerate(ids)}
    specs = {spec["name"]: spec for spec in descriptors}
    targets = bank["targets"]
    scores = bank["candidate_scores"]
    experts = bank["expert_scores"]
    invalid = bank["expert_invalid"]
    check(tuple(bank["expert_names"]) == EXPERTS, country + ":expert order")
    check(
        experts.shape == (3, len(targets), 49)
        and invalid.shape == (3, len(targets))
        and invalid.dtype == np.bool_
        and np.isfinite(experts).all(),
        country + ":expert arrays",
    )
    check(
        scores.shape == (len(ids), len(targets), 49) and np.isfinite(scores).all(),
        country + ":candidate score arrays",
    )
    check(
        bank["valid"].dtype == np.bool_
        and bank["valid"].shape == scores.shape[:2]
        and bank["ties"].dtype == np.bool_,
        country + ":boolean audit arrays",
    )
    expert_index = {name: i for i, name in enumerate(EXPERTS)}
    for name in ("frequency", "markov_lag2"):
        i, parent = index[name], expert_index[name]
        check(np.array_equal(scores[i], experts[parent]), country + ":control:" + name)
        check(
            np.array_equal(bank["valid"][i], ~invalid[parent]),
            country + ":control validity:" + name,
        )
    for spec in descriptors[2:]:
        parent_names = _required_experts(spec, specs)
        parent_invalid = np.zeros(len(targets), dtype=bool)
        for name in parent_names:
            parent_invalid |= invalid[expert_index[name]]
        check(
            not bank["valid"][index[spec["name"]], parent_invalid].any(),
            country + ":invalid parent propagation:" + spec["name"],
        )

    # Independent canonical selection at each k; the fast main evaluator builds
    # constrained16 once and uses nested prefixes for k9..16 instead.
    sampled_rows = np.unique(
        np.linspace(0, len(targets) - 1, min(9, len(targets))).astype(int)
    )
    pool_checks = 0
    for row in sampled_rows:
        actual = set(map(int, draws[targets[row]]))
        for i, name in enumerate(ids):
            vector = scores[i, row]
            ranking = rank_by_score(dict(enumerate(vector, start=1)), 49)
            observed = np.zeros((2, 8), dtype=np.int8)
            tied = np.zeros(8, dtype=bool)
            for column, k in enumerate(POOLS):
                tied[column] = (
                    abs(vector[ranking[k - 1] - 1] - vector[ranking[k] - 1]) <= 1e-12
                )
                for mode, limit in enumerate((0, 2)):
                    pool, _, _ = limit_consecutive_run(ranking, k, limit)
                    observed[mode, column] = len(actual.intersection(pool))
                    pool_checks += 1
            check(
                np.array_equal(observed, bank["hits"][i, row]),
                f"{country}:canonical hits:{name}:{row}",
            )
            check(
                np.array_equal(tied, bank["ties"][i, row]),
                f"{country}:canonical ties:{name}:{row}",
            )

    unique = np.unique(dates)
    cutoff_date = unique[len(unique) * 80 // 100]
    row80 = int(np.searchsorted(dates[targets], cutoff_date, side="left"))
    prefix_rows = sorted({0, min(row80, len(targets) - 1), len(targets) - 1})
    replay_checks = 0
    for row in prefix_rows:
        target = int(targets[row])
        cutoff = int(prior[target])
        check(
            (dates[:cutoff] < dates[target]).all(), f"{country}:strict prefix:{target}"
        )
        for name, parent in expert_index.items():
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
            if not usable:
                raw, _ = call_method(
                    "frequency", draws[:cutoff], 49, history_cutoffs=prior[:cutoff]
                )
            direct = np.asarray([raw[n] for n in range(1, 50)])
            check(
                np.allclose(direct, experts[parent, row], rtol=1e-12, atol=1e-12),
                f"{country}:direct expert:{name}:{target}",
            )
            check(
                bool(invalid[parent, row]) == (not usable),
                f"{country}:expert validity:{name}:{target}",
            )

        data, prefix_experts = _prefix_inputs(
            dates, draws, prior, targets, experts, row
        )
        generated = _generate_with_blends(data, prefix_experts)
        check(set(generated) == set(ids[2:]), country + ":generated family")
        for name, (prediction, fallback) in generated.items():
            i = index[name]
            consumed = _required_experts(specs[name], specs)
            bad_parent = any(invalid[expert_index[parent], row] for parent in consumed)
            check(
                np.allclose(prediction[-1], scores[i, row], rtol=1e-12, atol=1e-12),
                f"{country}:truncated score:{name}:{target}",
            )
            check(
                bool(bank["valid"][i, row]) == (not (bool(fallback[-1]) or bad_parent)),
                f"{country}:truncated validity:{name}:{target}",
            )
            replay_checks += 1

        # One target-label perturbation per country. Both the full-prefix and
        # changed-label runs include the current draw; neither may train on it.
        if row == prefix_rows[-1]:
            changed = {**data, "draws": data["draws"].copy()}
            old_target = set(map(int, changed["draws"][-1]))
            changed["draws"][-1] = [n for n in range(1, 50) if n not in old_target][:6]
            perturbed = _generate_with_blends(changed, prefix_experts)
            check(set(perturbed) == set(generated), country + ":perturbed family")
            for name in generated:
                check(
                    np.array_equal(generated[name][0][-1], perturbed[name][0][-1])
                    and bool(generated[name][1][-1]) == bool(perturbed[name][1][-1]),
                    country + ":target-label invariance:" + name,
                )
    return {
        "canonical_target_rows": len(sampled_rows),
        "canonical_pool_checks": pool_checks,
        "truncated_target_prefixes": len(prefix_rows),
        "algorithm_prefix_checks": replay_checks,
        "direct_expert_prefix_checks": len(prefix_rows) * 3,
        "target_label_perturbations": 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_report(args.verify, args.cache_dir)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_bytes(rendered.encode("utf-8"))
    print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
