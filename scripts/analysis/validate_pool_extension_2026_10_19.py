"""Separate recount, canonical pool and strict-prefix audit of the extension.

The current-method comparison is diagnostic, selected on reused history. This
validator checks recorded arithmetic and causal implementation; it does not
turn a retrospective comparison into independent prospective evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from pool_extension_statistics_2026_10_19 import (
    holm_adjusted,
    hypergeom_tails,
    paired_test,
    replay_selector,
)


def recount(hits, draw_n):
    values = np.asarray(hits, dtype=np.int64)
    return {
        "n": len(values),
        "primary": float(np.count_nonzero(values >= 4) / values.size),
        "rate3plus": float(np.count_nonzero(values >= 3) / values.size),
        "rate5plus": float(np.count_nonzero(values >= 5) / values.size),
        "mean_hits": float(values.sum() / values.size),
        "zero_rate": float(np.count_nonzero(values == 0) / values.size),
        "events4": np.count_nonzero(values >= 4, axis=0).tolist(),
        "events3": np.count_nonzero(values >= 3, axis=0).tolist(),
        "events5": np.count_nonzero(values >= 5, axis=0).tolist(),
        "zero": np.count_nonzero(values == 0, axis=0).tolist(),
        "mean_hits_by_k": (values.sum(axis=0) / len(values)).tolist(),
        "events_by_threshold": {
            str(t): np.count_nonzero(values >= t, axis=0).tolist()
            for t in range(1, draw_n + 1)
        },
    }


def compact(blocks):
    result = []
    for b in blocks:
        gates = b["gates"]
        result.append(
            {
                **{k: v for k, v in b.items() if k != "gates"},
                "eligible_count": sum(g["eligible"] for g in gates.values()),
                "chosen_gate": gates[b["chosen_id"]],
                "baseline_gate": gates["markov_lag2"],
                "excluded": {
                    name: {
                        "invalid_rows": g["invalid_rows_in_past_window"],
                        "max_tie_fraction": max(g["tie_fractions_in_past_window"]),
                    }
                    for name, g in gates.items()
                    if not g["eligible"]
                },
            }
        )
    return result


def rank_units(row):
    """Independent exact double mean ranks, zero-based (integer half-rank units)."""
    row = np.asarray(row)
    order = np.argsort(row, kind="stable")
    result = np.empty(len(row), dtype=np.int64)
    start = 0
    while start < len(row):
        stop = start + 1
        while stop < len(row) and row[order[stop]] == row[order[start]]:
            stop += 1
        result[order[start:stop]] = start + stop - 1
        start = stop
    return result


def prefix_inputs(dates, draws, targets, expert_scores, expert_names, row):
    end = int(targets[row]) + 1
    d = dates[:end]
    y = draws[:end]
    prior = np.searchsorted(d, d, side="left").astype(np.int64)
    groups = np.zeros(len(d), dtype=np.int64)
    if len(d) > 1:
        groups[1:] = np.cumsum(d[1:] != d[:-1])
    parents = {name: expert_scores[i, : row + 1] for i, name in enumerate(expert_names)}
    data = {
        "dates": d,
        "draws": y,
        "prior": prior,
        "groups": groups,
        "targets": targets[: row + 1],
        "frequency_past": parents["frequency"],
    }
    return data, parents


def audit_predictions(bank, dates, draws, meta, descriptors, check):
    from pool_extension_candidates_2026_10_19 import generate

    from loto_enterprise.benchmark.methods import call_method
    from loto_enterprise.core.pool_selection import apply_consecutive_limit
    from loto_enterprise.core.ranking import rank_by_score
    from loto_enterprise.core.score_validation import has_usable_score_variance

    n, dn, ks = meta["max_num"], meta["draw_n"], meta["ks"]
    targets = bank["targets"]
    ids = list(map(str, bank["candidate_ids"]))
    index = {name: i for i, name in enumerate(ids)}
    names = list(map(str, bank["expert_names"]))
    exindex = {name: i for i, name in enumerate(names)}
    scores = bank["candidate_scores"]
    experts = bank["expert_scores"]
    bad = bank["expert_invalid"]
    samples = np.unique(
        np.linspace(0, len(targets) - 1, min(9, len(targets)), dtype=int)
    )
    pool_checks = 0
    for row in samples:
        actual = set(map(int, draws[targets[row]]))
        for i, name in enumerate(ids):
            mapping = dict(enumerate(map(float, scores[i, row]), start=1))
            check(has_usable_score_variance(mapping), f"usable:{name}:{row}")
            ranked = rank_by_score(mapping, n)
            for j, k in enumerate(ks):
                limited = apply_consecutive_limit(ranked, k, 2)
                check(
                    len(set(ranked[:k]) & actual) == int(bank["hits"][i, row, 0, j]),
                    f"canonical raw:{name}:{row}:k{k}",
                )
                check(
                    len(set(limited) & actual) == int(bank["hits"][i, row, 1, j]),
                    f"canonical limited:{name}:{row}:k{k}",
                )
                tied = abs(mapping[ranked[k - 1]] - mapping[ranked[k]]) <= 1e-12
                check(
                    bool(bank["ties"][i, row, j]) == tied,
                    f"canonical tie:{name}:{row}:k{k}",
                )
                pool_checks += 2
        for name in names:
            ranked = rank_by_score(
                dict(enumerate(map(float, experts[exindex[name], row]), start=1)), n
            )
            for j, k in enumerate(ks):
                if bank["current_names_by_k"][j] != name:
                    continue
                limited = apply_consecutive_limit(ranked, k, 2)
                check(
                    int(bank["current_hits"][row, 0, j])
                    == len(set(ranked[:k]) & actual),
                    f"current raw:{name}:{row}:k{k}",
                )
                check(
                    int(bank["current_hits"][row, 1, j]) == len(set(limited) & actual),
                    f"current limited:{name}:{row}:k{k}",
                )
    unique = np.unique(dates)
    boundary = unique[len(unique) * 80 // 100]
    middle = min(int(np.searchsorted(dates[targets], boundary)), len(targets) - 1)
    prefixes = sorted({0, middle, len(targets) - 1})
    algorithm_checks = 0
    prior = np.searchsorted(dates, dates, side="left")
    for row in prefixes:
        cutoff = int(prior[targets[row]])
        check(bool((dates[:cutoff] < dates[targets[row]]).all()), f"strict day:{row}")
        for name in names:
            try:
                raw, _ = call_method(
                    name, draws[:cutoff], n, history_cutoffs=prior[:cutoff]
                )
                usable = set(raw) == set(range(1, n + 1)) and has_usable_score_variance(
                    raw
                )
            except Exception:
                usable = False
            if not usable:
                raw, _ = call_method(
                    "frequency", draws[:cutoff], n, history_cutoffs=prior[:cutoff]
                )
            check(
                np.allclose(
                    [raw[x] for x in range(1, n + 1)],
                    experts[exindex[name], row],
                    rtol=1e-12,
                    atol=1e-12,
                ),
                f"direct expert:{name}:{row}",
            )
            check(
                bool(bad[exindex[name], row]) == (not usable),
                f"direct expert validity:{name}:{row}",
            )
        data, parents = prefix_inputs(dates, draws, targets, experts, names, row)
        generated = {}
        for name, _family, prediction, fallback in generate(
            data, parents, max_num=n, draw_n=dn
        ):
            own_bad = bool(fallback[-1])
            consumed = descriptors[name]["parent_names"]
            invalid = own_bad or any(bad[exindex[p], row] for p in consumed)
            check(
                np.allclose(
                    prediction[-1], scores[index[name], row], rtol=1e-12, atol=1e-12
                ),
                f"truncated score:{name}:{row}",
            )
            check(
                bool(bank["valid"][index[name], row]) == (not invalid),
                f"truncated validity:{name}:{row}",
            )
            mixed = (
                rank_units(parents["markov_lag2"][-1]) + rank_units(prediction[-1])
            ) / (4 * (n - 1))
            flat = np.ptp(mixed) <= 1e-12
            if flat:
                mixed = parents["frequency"][-1].copy()
            blend = "mix_markov50__" + name
            check(
                np.allclose(mixed, scores[index[blend], row], rtol=1e-12, atol=1e-12),
                f"truncated blend:{name}:{row}",
            )
            mixed_invalid = invalid or bool(bad[exindex["markov_lag2"], row]) or flat
            check(
                bool(bank["valid"][index[blend], row]) == (not mixed_invalid),
                f"truncated blend validity:{name}:{row}",
            )
            generated[name] = prediction[-1].copy()
            algorithm_checks += 2
        if row == prefixes[-1]:
            changed = {**data, "draws": data["draws"].copy()}
            old = set(map(int, changed["draws"][-1]))
            changed["draws"][-1] = [x for x in range(1, n + 1) if x not in old][:dn]
            for name, _family, prediction, _fallback in generate(
                changed, parents, max_num=n, draw_n=dn
            ):
                check(
                    np.array_equal(prediction[-1], generated[name]),
                    "target label invariance:" + name,
                )
    return {
        "canonical_target_rows": len(samples),
        "canonical_pool_checks": pool_checks,
        "truncated_target_prefixes": len(prefixes),
        "algorithm_prefix_checks": algorithm_checks,
        "direct_expert_prefix_checks": len(prefixes) * len(names),
        "target_label_perturbations": 1,
    }


def verify_report(
    report_path, cache_dir=None, *, workspace_root=None, audit_scores=True
):
    root = Path(workspace_root).resolve() if workspace_root else ROOT
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_bytes())
    manifest_path = report_path.with_name(report_path.stem + "_protocol.json")
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    identity = hashlib.sha256(manifest_raw).hexdigest()
    cache = (
        Path(cache_dir)
        if cache_dir
        else Path(tempfile.gettempdir()) / "loto-pool-extension-20261019/cache"
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
            check(isinstance(actual, dict), label + ":dict")
            if isinstance(actual, dict):
                for key, value in expected.items():
                    same(actual.get(key), value, label + "." + str(key))
        elif isinstance(expected, (list, tuple)):
            check(
                isinstance(actual, (list, tuple)) and len(actual) == len(expected),
                label + ":list",
            )
            if isinstance(actual, (list, tuple)) and len(actual) == len(expected):
                for i, (a, b) in enumerate(zip(actual, expected, strict=True)):
                    same(a, b, label + f"[{i}]")
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

    check(report["protocol_sha256"] == identity, "protocol identity")
    check(
        report["retrospective_reuse"] is True
        and report["iid_search_calibrated"] is False
        and report["production_changed"] is False,
        "scope labels",
    )
    check(
        set(report["games"]) == set(manifest["games"]) == {"loto_5_40", "joker_urna1"},
        "game scope",
    )
    same(report["protected_after"], report["protected_before"], "protected hashes")
    for relative, expected in manifest["source_sha256"].items():
        check(
            hashlib.sha256(
                (root / relative).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            == expected,
            "source:" + relative,
        )
    for relative, expected in report["protected_after"].items():
        path = root / relative
        value = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
        check(value == expected, "protected current:" + relative)
    pvalues = {}
    selector_p = {}
    prediction_audit = {}
    candidate_csv = []
    trace_csv = []
    for game, stored in report["games"].items():
        meta = manifest["games"][game]
        n, dn, ks = meta["max_num"], meta["draw_n"], meta["ks"]
        descriptors = manifest["candidate_specs"][game]
        ids = [d["name"] for d in descriptors]
        byid = {d["name"]: d for d in descriptors}
        check(
            ids[:2] == ["markov_lag2", "frequency"] and len(set(ids)) == 34,
            "candidate scope:" + game,
        )
        check(set(stored["candidates"]) == set(ids[2:]), "fixed family:" + game)
        with np.load(cache / f"{game}_bank.npz", allow_pickle=False) as a:
            bank = {key: a[key] for key in a.files}
        check(str(bank["identity"].item()) == identity, "NPZ identity:" + game)
        check(np.array_equal(bank["candidate_ids"], ids), "candidate order:" + game)
        path = root / meta["path"]
        check(
            hashlib.sha256(path.read_bytes()).hexdigest()
            == manifest["data_sha256"][game],
            "data SHA:" + game,
        )
        frame = pd.read_csv(path)
        frame["_date"] = pd.to_datetime(
            frame["date"], format="%d-%m-%Y", errors="raise"
        )
        frame = frame.sort_values("_date", kind="stable").reset_index(drop=True)
        dates = frame["_date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10")
        draws = frame[[f"n{i}" for i in range(1, dn + 1)]].to_numpy(dtype=np.int64)
        check(
            bool(
                ((draws >= 1) & (draws <= n)).all()
                and (np.diff(np.sort(draws, axis=1), axis=1) > 0).all()
            ),
            "valid draws:" + game,
        )
        unique = np.unique(dates)
        prior = np.searchsorted(dates, dates, side="left")
        start = max(256, int(np.searchsorted(dates, unique[len(unique) // 2])))
        targets = np.flatnonzero(prior >= start)
        days = dates[targets]
        t = len(targets)
        check(
            np.array_equal(bank["full_dates"], dates)
            and np.array_equal(bank["targets"], targets)
            and np.array_equal(bank["draws"], draws[targets]),
            "chronology:" + game,
        )
        h = bank["hits"]
        valid = bank["valid"]
        ties = bank["ties"]
        check(
            h.shape == (34, t, 2, len(ks)) and bool(((h >= 0) & (h <= dn)).all()),
            "hit geometry:" + game,
        )
        check(
            bank["candidate_scores"].shape == (34, t, n)
            and np.isfinite(bank["candidate_scores"]).all(),
            "score geometry:" + game,
        )
        check(
            valid.shape == (34, t) and ties.shape == (34, t, len(ks)),
            "mask geometry:" + game,
        )
        check(
            np.array_equal(bank["complexity"], [d["complexity"] for d in descriptors]),
            "complexity:" + game,
        )
        replay = replay_selector(
            dates,
            targets,
            h[:, :, 1],
            ids,
            ks=ks,
            draw_n=dn,
            complexity=bank["complexity"],
            eligibility_mask=valid,
            ties=ties,
        )
        selected = replay["selected_indices"]
        primary = replay["primary_mask"]
        diag = replay["diagnostic_mask"]
        for key, value in (
            ("selected_indices", selected),
            ("primary_mask", primary),
            ("diagnostic_mask", diag),
        ):
            check(np.array_equal(bank[key], value), game + ":" + key)
        dayindex = np.searchsorted(unique, days)
        manual = np.zeros(t, dtype=int)
        for pct in (70, 80, 90):
            past = (dayindex >= len(unique) * (pct - 20) // 100) & (
                dayindex < len(unique) * pct // 100
            )
            future = (dayindex >= len(unique) * pct // 100) & (
                dayindex < len(unique) * (pct + 10) // 100
            )
            eligible = [
                i
                for i in range(34)
                if i == 0
                or (
                    valid[i, past].all()
                    and not (ties[i, past].mean(axis=0) >= 0.5).any()
                )
            ]
            chosen = min(
                eligible,
                key=lambda i: (
                    -int((h[i, past, 1] >= 4).sum()),
                    i != 0,
                    int(bank["complexity"][i]),
                    ids[i],
                ),
            )
            manual[future] = chosen
        check(np.array_equal(manual, selected), "independent integer selector:" + game)
        same(stored["blocks"], compact(replay["blocks"]), "blocks:" + game)
        selector = h[selected, np.arange(t)]
        check(np.array_equal(selector, bank["selector_hits"]), "selector trace:" + game)
        curmap = manifest["current_baselines"][game]
        same(stored["current_baselines"], curmap, "current IDs:" + game)
        refs = {name: i for i, name in enumerate(map(str, bank["reference_names"]))}
        current = np.stack(
            [
                bank["reference_hits"][refs[curmap[f"k{k}"]], :, :, j]
                for j, k in enumerate(ks)
            ],
            axis=-1,
        )
        check(
            np.array_equal(current, bank["current_hits"]),
            "current per-pool assembly:" + game,
        )
        exindex = {name: i for i, name in enumerate(map(str, bank["expert_names"]))}
        current_invalid = np.stack(
            [bank["expert_invalid"][exindex[curmap[f"k{k}"]]] for k in ks], axis=-1
        )
        check(
            np.array_equal(current_invalid, bank["current_invalid"]),
            "current invalidity:" + game,
        )
        for segment, mask, mode in (
            ("primary", primary, 1),
            ("diagnostic", diag, 1),
            ("raw", primary, 0),
        ):
            for name, values in (
                ("selector", selector),
                ("markov", h[0]),
                ("current", current),
            ):
                same(
                    stored[segment][name],
                    recount(values[mask, mode], dn),
                    f"{game}:{segment}:{name}",
                )
        same(stored["geometry"], meta, "geometry:" + game)
        same(
            {
                k: stored[k]
                for k in (
                    "input_rows",
                    "target_rows",
                    "start_history_rows",
                    "first_primary",
                    "last_primary",
                )
            },
            {
                "input_rows": len(dates),
                "target_rows": t,
                "start_history_rows": start,
                "first_primary": str(days[primary][0]),
                "last_primary": str(days[primary][-1]),
            },
            "sample:" + game,
        )
        same(
            stored["invalid_primary_by_candidate"],
            {
                name: int((~valid[i, primary]).sum())
                for i, name in enumerate(ids)
                if not valid[i, primary].all()
            },
            "invalid primary:" + game,
        )
        same(
            stored["invalid_target_by_candidate"],
            {
                name: int((~valid[i]).sum())
                for i, name in enumerate(ids)
                if not valid[i].all()
            },
            "invalid all:" + game,
        )
        same(
            stored["selected_primary_invalid_rows"],
            int((~valid[selected, np.arange(t)][primary]).sum()),
            "invalid selected:" + game,
        )
        same(
            stored["current_invalid_primary_by_k"],
            {f"k{k}": int(current_invalid[primary, j].sum()) for j, k in enumerate(ks)},
            "invalid current:" + game,
        )
        same(
            stored["hypergeometric"],
            {str(k): hypergeom_tails(k, max_num=n, draw_n=dn) for k in ks},
            "hypergeometric:" + game,
        )

        def paired(a, b, bootstrap):
            return paired_test(
                a,
                b,
                days[primary],
                ks=ks,
                draw_n=dn,
                threshold=4,
                permutations=manifest["inference"]["permutations"],
                bootstrap_samples=bootstrap,
                seed=manifest["seed"],
            )

        test = paired(
            selector[primary, 1],
            h[0, primary, 1],
            manifest["inference"]["bootstrap_samples"],
        )
        same(stored["primary"]["paired_vs_markov"], test, "primary paired:" + game)
        selector_p[game] = test["p_value"]
        same(
            stored["primary"]["paired_vs_current_diagnostic"],
            paired(
                selector[primary, 1],
                current[primary, 1],
                manifest["inference"]["bootstrap_samples"],
            ),
            "current paired:" + game,
        )
        within = {}
        for i, name in enumerate(ids[2:], 2):
            row = stored["candidates"][name]
            same(row["spec"], byid[name], "spec:" + game + ":" + name)
            same(
                row["limited"],
                recount(h[i, primary, 1], dn),
                "limited:" + game + ":" + name,
            )
            same(row["raw"], recount(h[i, primary, 0], dn), "raw:" + game + ":" + name)
            test = paired(h[i, primary, 1], h[0, primary, 1], 0)
            expected = {
                key: test[key]
                for key in (
                    "mean_difference",
                    "p_value",
                    "exact_sign_flip_p_diagnostic",
                    "wins",
                    "losses",
                    "days",
                )
            }
            same(row["vs_markov"], expected, "fixed paired:" + game + ":" + name)
            same(
                row["invalid_primary_rows"],
                int((~valid[i, primary]).sum()),
                "fixed invalid:" + game + ":" + name,
            )
            within[name] = test["p_value"]
            pvalues[game + ":" + name] = test["p_value"]
        for name, q in holm_adjusted(within).items():
            same(
                stored["candidates"][name]["holm_within_game"],
                q,
                "Holm32:" + game + ":" + name,
            )
        for name, row in stored["candidates"].items():
            for j, k in enumerate(ks):
                candidate_csv.append(
                    dict(
                        zip(
                            (
                                "game",
                                "candidate",
                                "family",
                                "pool_size",
                                "n",
                                "candidate4",
                                "markov4",
                                "current4",
                                "p_vs_markov",
                                "holm32",
                                "holm64",
                            ),
                            map(
                                str,
                                (
                                    game,
                                    name,
                                    row["spec"]["family"],
                                    k,
                                    row["limited"]["n"],
                                    row["limited"]["events4"][j],
                                    stored["primary"]["markov"]["events4"][j],
                                    stored["primary"]["current"]["events4"][j],
                                    row["vs_markov"]["p_value"],
                                    row["holm_within_game"],
                                    row["holm_across_games"],
                                ),
                            ),
                            strict=True,
                        )
                    )
                )
        for r in np.flatnonzero(primary | diag):
            for j, k in enumerate(ks):
                trace_csv.append(
                    dict(
                        zip(
                            (
                                "game",
                                "date",
                                "segment",
                                "chosen",
                                "pool_size",
                                "selector_c2",
                                "markov_c2",
                                "current_c2",
                                "selector_raw",
                                "markov_raw",
                                "current_raw",
                            ),
                            map(
                                str,
                                (
                                    game,
                                    days[r],
                                    "primary" if primary[r] else "diagnostic",
                                    ids[selected[r]],
                                    k,
                                    int(selector[r, 1, j]),
                                    int(h[0, r, 1, j]),
                                    int(current[r, 1, j]),
                                    int(selector[r, 0, j]),
                                    int(h[0, r, 0, j]),
                                    int(current[r, 0, j]),
                                ),
                            ),
                            strict=True,
                        )
                    )
                )
        if audit_scores:
            bank["current_names_by_k"] = [curmap[f"k{k}"] for k in ks]
            prediction_audit[game] = audit_predictions(
                bank,
                dates,
                draws,
                meta,
                byid,
                lambda condition, label: check(condition, game + ":" + label),
            )
    for game, q in holm_adjusted(selector_p).items():
        part = report["games"][game]["primary"]
        same(part["holm_across_games"], q, "selector Holm2:" + game)
        same(
            part["selector_gate_passed"],
            q < 0.05 and part["paired_vs_markov"]["mean_difference"] > 0,
            "selector gate:" + game,
        )
    for label, q in holm_adjusted(pvalues).items():
        game, name = label.split(":", 1)
        same(
            report["games"][game]["candidates"][name]["holm_across_games"],
            q,
            "Holm64:" + label,
        )
    same(report["candidate_count_per_game"], 32, "candidate count")
    same(report["candidate_game_tests"], len(pvalues), "candidate-game tests")
    for suffix, expected in (
        ("_candidates.csv", candidate_csv),
        ("_trace.csv", trace_csv),
    ):
        with report_path.with_name(report_path.stem + suffix).open(
            encoding="utf-8", newline=""
        ) as stream:
            actual = list(csv.DictReader(stream))
        check(actual == expected, "CSV parity:" + suffix)
    return {
        "report": str(report_path),
        "protocol_sha256": identity,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
        "game_banks": len(report["games"]),
        "candidate_game_tests": len(pvalues),
        "prediction_audit": prediction_audit,
        "full_prediction_audit": bool(audit_scores),
        "csv_rows": {"candidates": len(candidate_csv), "trace": len(trace_csv)},
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "limits": "Full metric/selector/statistic recount, CSV parity, sampled canonical pools and strict-prefix scores. Implementation audit is not prospective evidence or entire-search IID calibration.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_report(args.verify, args.cache_dir)
    raw = (json.dumps(result, indent=2) + "\n").encode("utf-8")
    if args.output:
        args.output.write_bytes(raw)
    print(raw.decode("utf-8"), end="")
    raise SystemExit(0 if result["passed"] else 1)
