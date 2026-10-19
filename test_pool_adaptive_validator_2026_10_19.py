"""Synthetic regressions for the separate adaptive research validator."""

from __future__ import annotations

import json
import numpy as np
import pytest

from scripts.analysis.validate_pool_adaptive_2026_10_19 import (
    _mean_ranks,
    _prefix_inputs,
    _required_experts,
    verify_report,
)
from scripts.analysis.validate_pool_combinations_2026_10_19 import replay_selector
from scripts.analysis.validate_pool_screen_2026_10_19 import (
    paired_test,
    holm_adjusted,
    hypergeom_tails,
)


@pytest.fixture
def synthetic_complete_report(tmp_path):
    import hashlib
    import json
    import pandas as pd
    from loto_enterprise.benchmark.methods import call_method
    from scripts.analysis.pool_combination_eval_2026_10_19 import evaluate_scores
    from scripts.analysis.pool_adaptive_649_2026_10_19 import (
        all_scores,
        specs as bank_specs,
    )
    from scripts.analysis.validate_pool_combinations_2026_10_19 import (
        _compact,
        _metrics,
    )

    root = tmp_path
    cache = root / "cache"
    cache.mkdir()
    analysis = root / "scripts/analysis"
    analysis.mkdir(parents=True)
    histories = root / "_ISTORIC"
    (histories / "externe").mkdir(parents=True)
    rng = np.random.default_rng(2026101902)
    dates = np.repeat(
        np.arange("2020-01-01", "2020-04-10", dtype="datetime64[D]").astype("U10"), 2
    )
    draws = np.asarray([rng.choice(np.arange(1, 50), 6, replace=False) for _ in dates])
    frame = pd.DataFrame(draws, columns=[f"n{i}" for i in range(1, 7)])
    frame.insert(0, "date", pd.to_datetime(dates).strftime("%d-%m-%Y"))
    paths = {
        "romania": histories / "loto_6_49.csv",
        "bulgaria": histories / "externe/bulgaria_toto2_6din49.csv",
    }
    for path in paths.values():
        frame.to_csv(path, index=False)
    (root / "source_stub.py").write_bytes(b"# synthetic source\r\n")
    specs = bank_specs()
    ids = [spec["name"] for spec in specs]
    targets = np.arange(100, len(dates))
    prior = np.searchsorted(dates, dates, side="left")
    parents = {name: [] for name in ("frequency", "markov_lag2", "ewma_hl30")}
    for target in targets:
        for name in parents:
            raw, _ = call_method(
                name, draws[: prior[target]], 49, history_cutoffs=prior[: prior[target]]
            )
            parents[name].append([raw[n] for n in range(1, 50)])
    parents = {name: np.asarray(values) for name, values in parents.items()}
    data = {
        "dates": dates,
        "draws": draws,
        "prior": prior,
        "groups": np.repeat(np.arange(100), 2),
        "targets": targets,
        "frequency_past": parents["frequency"],
    }
    results = {
        name: (name, score, np.zeros(len(targets), dtype=bool), 0.0, {})
        for name, score in parents.items()
    }
    generated = all_scores(data, results)
    scores = {name: generated[name][0] for name in ids}
    hits = np.zeros((len(ids), len(targets), 2, 8), dtype=np.int8)
    ties = np.zeros((len(ids), len(targets), 8), dtype=bool)
    valid = np.ones((len(ids), len(targets)), dtype=bool)
    for i, name in enumerate(ids):
        hits[i], ties[i] = evaluate_scores(scores[name], draws[targets])
        valid[i] = ~generated[name][1]
    complexity = np.asarray([spec["complexity"] for spec in specs])
    manifest = {
        "baseline": "markov_lag2",
        "candidate_specs": specs,
        "candidate_count": len(specs) - 2,
        "seed": 2026101903,
        "minimum_warmup_rows": 20,
        "selection": {"origins": [0.7, 0.8, 0.9], "lookback": 0.2},
        "inference": {"permutations": 1999, "bootstrap_samples": 200},
        "source_sha256": {
            "source_stub.py": hashlib.sha256(b"# synthetic source\n").hexdigest()
        },
        "data_sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()
        },
    }
    manifest_path = analysis / "synthetic_protocol.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    identity = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    replay = replay_selector(
        dates,
        targets,
        hits[:, :, 1],
        ids,
        complexity=complexity,
        eligibility_mask=valid,
        ties=ties,
    )
    selected = replay["selected_indices"]
    selector = hits[selected, np.arange(len(targets))]
    primary, diagnostic = replay["primary_mask"], replay["diagnostic_mask"]
    primary_dates = dates[targets][primary]
    base = hits[0]
    pair = lambda a, b, d, ci=True: paired_test(
        a, b, d, seed=2026101903, permutations=1999, bootstrap_samples=200 if ci else 0
    )
    report = {
        "protocol_sha256": identity,
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "production_changed": False,
        "candidate_count": len(specs) - 2,
        "protected_before": {},
        "protected_after": {},
        "countries": {},
        "static_combinations": {},
        "hypergeometric": {str(k): hypergeom_tails(k) for k in range(9, 17)},
    }
    for country in paths:
        np.savez_compressed(
            cache / f"{country}_bank.npz",
            identity=np.asarray(identity),
            candidate_scores=np.stack([scores[name] for name in ids]),
            expert_names=np.asarray(list(parents)),
            expert_scores=np.stack(list(parents.values())),
            expert_invalid=np.zeros((3, len(targets)), dtype=bool),
            candidate_ids=np.asarray(ids),
            full_dates=dates,
            targets=targets,
            draws=draws[targets],
            hits=hits,
            ties=ties,
            valid=valid,
            complexity=complexity,
            selected_indices=selected,
            selector_hits=selector,
            primary_mask=primary,
            diagnostic_mask=diagnostic,
        )
        primary_pair = pair(selector[primary, 1], base[primary, 1], primary_dates)
        report["countries"][country] = {
            "input_rows": len(dates),
            "target_rows": len(targets),
            "start_history_rows": 100,
            "blocks": _compact(replay["blocks"], "markov_lag2"),
            "primary": {
                "selector": _metrics(selector[primary, 1]),
                "baseline": _metrics(base[primary, 1]),
                "paired": primary_pair,
            },
            "diagnostic": {
                "selector": _metrics(selector[diagnostic, 1]),
                "baseline": _metrics(base[diagnostic, 1]),
            },
            "raw": {
                "selector": _metrics(selector[primary, 0]),
                "baseline": _metrics(base[primary, 0]),
            },
            "first_primary": str(primary_dates[0]),
            "last_primary": str(primary_dates[-1]),
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
                (~valid[selected, np.arange(len(targets))][primary]).sum()
            ),
        }
    report["countries"]["bulgaria"]["selector_country_holm"] = report["countries"][
        "bulgaria"
    ]["primary"]["paired"]["p_value"]
    external_dates = np.asarray(["bulgaria:" + day for day in primary_dates])
    external_pair = pair(selector[primary, 1], base[primary, 1], external_dates)
    report["external_pooled"] = {
        "selector": _metrics(selector[primary, 1]),
        "baseline": _metrics(base[primary, 1]),
        "raw_selector": _metrics(selector[primary, 0]),
        "raw_baseline": _metrics(base[primary, 0]),
        "paired": external_pair,
    }
    families = {"romania": {}, "external": {}}
    for i, spec in enumerate(specs[2:], start=2):
        name = spec["name"]
        report["static_combinations"][name] = {"spec": spec}
        for region, selected_dates in (
            ("romania", primary_dates),
            ("external", external_dates),
        ):
            result = pair(
                hits[i, primary, 1], base[primary, 1], selected_dates, ci=False
            )
            report["static_combinations"][name][region] = {
                "limited": _metrics(hits[i, primary, 1]),
                "raw": _metrics(hits[i, primary, 0]),
                "difference": result["mean_difference"],
                "p_value": result["p_value"],
                "exact_sign_flip_p": result["exact_sign_flip_p_diagnostic"],
                "wins": result["wins"],
                "losses": result["losses"],
                "days": result["days"],
                "invalid_primary_rows": int((~valid[i, primary]).sum()),
            }
            families[region][name] = result["p_value"]
    for region, values in families.items():
        for name, adjusted in holm_adjusted(values).items():
            report["static_combinations"][name][region]["holm"] = adjusted
    report["selector_superiority_gate_passed"] = all(
        part["mean_difference"] > 0 and part["p_value"] < 0.05
        for part in (report["countries"]["romania"]["primary"]["paired"], external_pair)
    )
    report_path = analysis / "synthetic.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return root, cache, report_path


def test_mean_rank_oracle_and_flat_blend_match_main():
    from scipy.stats import rankdata
    from scripts.analysis.pool_adaptive_649_2026_10_19 import blend_scores

    rng = np.random.default_rng(2026101903)
    left = rng.integers(-3, 4, size=(7, 49)).astype(float)
    right = rng.integers(-4, 5, size=(7, 49)).astype(float)
    frequency = np.tile(np.linspace(0, 1, 49), (7, 1))
    left[-1], right[-1] = frequency[-1], -frequency[-1]
    ranks = _mean_ranks(left)
    assert np.array_equal(ranks, rankdata(left, axis=1, method="average"))
    expected = (ranks + _mean_ranks(right) - 2.0) / 96.0
    flat = np.ptp(expected, axis=1) <= 1e-12
    expected[flat] = frequency[flat]
    scores, invalid = blend_scores(left, right, frequency)
    assert np.array_equal(scores, expected)
    assert np.array_equal(invalid, flat)
    assert invalid[-1] and np.array_equal(scores[-1], frequency[-1])


def test_required_experts_follow_only_consumed_parents():
    from scripts.analysis.pool_adaptive_649_2026_10_19 import specs

    descriptors = {item["name"]: item for item in specs()}
    bocpd = descriptors["bocpd_daily_h128_k20"]
    assert _required_experts(bocpd, descriptors) == set()
    mixed = descriptors["mix_markov50__bocpd_daily_h128_k20"]
    assert _required_experts(mixed, descriptors) == {"markov_lag2"}
    ewma = descriptors["rank_bilinear_markov_ewma_hl30_g3_all"]
    assert _required_experts(ewma, descriptors) == {"markov_lag2", "ewma_hl30"}
    frequency = descriptors["rank_bilinear_markov_frequency_g3_all"]
    assert _required_experts(frequency, descriptors) == {"markov_lag2", "frequency"}


def test_prefix_inputs_preserve_original_stream_and_same_day_boundary():
    dates = np.repeat(
        np.arange("2020-01-01", "2020-01-07", dtype="datetime64[D]").astype("U10"), 2
    )
    draws = np.tile(np.arange(1, 7), (len(dates), 1))
    prior = np.searchsorted(dates, dates, side="left")
    targets = np.arange(6, 12)
    experts = np.arange(3 * 6 * 49).reshape(3, 6, 49)
    data, supplied = _prefix_inputs(dates, draws, prior, targets, experts, 1)
    assert np.array_equal(data["targets"], [6, 7])
    assert len(data["draws"]) == 8
    assert data["prior"][-1] == 6
    assert np.array_equal(supplied["frequency"], experts[0, :2])
    assert np.array_equal(data["frequency_past"], supplied["frequency"])
    assert np.array_equal(data["groups"], [0, 0, 1, 1, 2, 2, 3, 3])


def test_complete_synthetic_report_and_causal_prefixes(synthetic_complete_report):
    root, cache, report = synthetic_complete_report
    result = verify_report(report, cache, workspace_root=root)
    assert result["passed"], result["failures"]
    assert result["country_banks"] == 2
    assert result["algorithm_candidates"] == 32
    assert result["holm_families"] == {"romania": 32, "external": 32}
    for country in ("romania", "bulgaria"):
        audit = result["prediction_audit"][country]
        assert audit["truncated_target_prefixes"] == 3
        assert audit["algorithm_prefix_checks"] == 96
        assert audit["direct_expert_prefix_checks"] == 9
        assert audit["canonical_target_rows"] == 9
        assert audit["canonical_pool_checks"] == 34 * 9 * 8 * 2
        assert audit["target_label_perturbations"] == 1
    assert result["retrospective_reuse"] and not result["iid_search_calibrated"]


def _change_bank(cache, change):
    path = cache / "romania_bank.npz"
    with np.load(path, allow_pickle=False) as saved:
        arrays = {key: saved[key] for key in saved.files}
    change(arrays)
    np.savez_compressed(path, **arrays)


def test_verifier_detects_modified_metric(synthetic_complete_report):
    root, cache, report_path = synthetic_complete_report
    report = json.loads(report_path.read_text(encoding="utf-8"))
    name = next(iter(report["static_combinations"]))
    report["static_combinations"][name]["romania"]["limited"]["events4"][0] += 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result = verify_report(report_path, cache, workspace_root=root)
    assert not result["passed"]
    assert any("events4[0]" in failure for failure in result["failures"])


def test_verifier_detects_score_change_even_with_identical_ranks(
    synthetic_complete_report,
):
    root, cache, report = synthetic_complete_report

    def change(arrays):
        arrays["candidate_scores"][2, -1] += 0.25

    _change_bank(cache, change)
    result = verify_report(report, cache, workspace_root=root)
    assert not result["passed"]
    assert any("truncated score" in failure for failure in result["failures"])


def test_verifier_detects_bad_expert_validity_propagation(synthetic_complete_report):
    root, cache, report = synthetic_complete_report

    def change(arrays):
        arrays["expert_invalid"][2, 11] = True

    _change_bank(cache, change)
    result = verify_report(report, cache, workspace_root=root)
    assert not result["passed"]
    assert any(
        "invalid parent propagation" in failure for failure in result["failures"]
    )


def test_verifier_detects_modified_source(synthetic_complete_report):
    root, cache, report = synthetic_complete_report
    (root / "source_stub.py").write_text("# changed source\n", encoding="utf-8")
    result = verify_report(report, cache, workspace_root=root)
    assert not result["passed"]
    assert "source:source_stub.py" in result["failures"]
