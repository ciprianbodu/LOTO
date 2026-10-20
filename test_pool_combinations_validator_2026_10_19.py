"""Adversarial checks for a past-only combination selector, not its outcomes."""

import numpy as np
import pytest

from scripts.analysis.validate_pool_combinations_2026_10_19 import (
    holm_adjusted,
    hypergeom_tails,
    paired_test,
    replay_selector,
)


def fixture_bank():
    dates = np.arange("2020-01-01", "2020-04-10", dtype="datetime64[D]")
    targets = np.arange(50, 100)
    bank = np.zeros((3, len(targets), 8), dtype=np.int8)
    bank[1, :20] = 4  # A wins the past 50..70% window.
    bank[2, 20:40] = 4  # B becomes available only after 70% outcomes arrive.
    return dates, targets, bank


def test_rolling_choices_use_the_correct_previous_windows():
    dates, targets, bank = fixture_bank()
    result = replay_selector(
        dates, targets, bank, ["markov_lag2", "A", "B"], complexity=[1, 2, 1]
    )
    assert [block["window_day_indices"] for block in result["blocks"]] == [
        [50, 70],
        [60, 80],
        [70, 90],
    ]
    assert [block["evaluation_day_indices"] for block in result["blocks"]] == [
        [70, 80],
        [80, 90],
        [90, 100],
    ]
    assert [block["chosen_id"] for block in result["blocks"]] == ["A", "B", "B"]
    assert result["diagnostic_mask"].sum() == 10
    assert result["primary_mask"].sum() == 20
    assert result["active_mask"].sum() == 30
    for row, chosen in enumerate(result["selected_indices"]):
        np.testing.assert_array_equal(result["trace_hits"][row], bank[chosen, row])


def test_changing_future_outcomes_cannot_change_an_earlier_choice():
    dates, targets, bank = fixture_bank()
    original = replay_selector(dates, targets, bank, ["markov_lag2", "A", "B"])
    modified = bank.copy()
    modified[:, targets >= 70] = 0
    modified[2, targets >= 70] = 6
    changed = replay_selector(dates, targets, modified, ["markov_lag2", "A", "B"])
    assert original["blocks"][0] == changed["blocks"][0]
    np.testing.assert_array_equal(
        original["selected_ids"][targets < 80], changed["selected_ids"][targets < 80]
    )


def test_same_day_rows_and_decimal_boundaries_are_never_split():
    dates = np.repeat(np.arange("2020-01-01", "2020-01-11", dtype="datetime64[D]"), 2)
    targets = np.arange(10, 20)
    bank = np.zeros((2, len(targets), 8), dtype=np.int8)
    bank[1, :4] = 4
    result = replay_selector(dates, targets, bank, ["markov_lag2", "combo"])
    first = result["blocks"][0]
    assert first["window_day_indices"] == [5, 7]  # NOT floor(10*(.7-.2)) == 4.
    assert first["window_rows"] == 4
    assert first["evaluation_rows"] == 2
    assert first["window_last"] == "2020-01-07"
    assert first["evaluation_first"] == "2020-01-08"
    for day in np.unique(dates[targets]):
        assert len(set(result["selected_ids"][dates[targets] == day])) == 1


def test_ties_prefer_reference_then_fixed_complexity_then_name():
    dates, targets, bank = fixture_bank()
    bank[:] = 4
    result = replay_selector(
        dates, targets, bank, ["A", "markov_lag2", "B"], complexity=[0, 10, 0]
    )
    assert all(block["chosen_id"] == "markov_lag2" for block in result["blocks"])
    bank[1] = 0  # reference loses; A and B tie.
    result = replay_selector(
        dates, targets, bank, ["A", "markov_lag2", "B"], complexity=[2, 1, 1]
    )
    assert all(block["chosen_id"] == "B" for block in result["blocks"])
    result = replay_selector(dates, targets, bank, ["A", "markov_lag2", "B"])
    assert all(block["chosen_id"] == "A" for block in result["blocks"])


def test_candidate_row_order_does_not_change_selection():
    dates, targets, bank = fixture_bank()
    ids = np.array(["markov_lag2", "A", "B"])
    first = replay_selector(dates, targets, bank, ids)
    permutation = [2, 0, 1]
    moved = replay_selector(dates, targets, bank[permutation], ids[permutation])
    np.testing.assert_array_equal(first["selected_ids"], moved["selected_ids"])
    np.testing.assert_array_equal(first["trace_hits"], moved["trace_hits"])


def test_validity_gate_reads_only_the_previous_window():
    dates, targets, bank = fixture_bank()
    valid = np.ones(bank.shape[:2], dtype=bool)
    valid[1, targets >= 70] = False
    result = replay_selector(
        dates, targets, bank, ["markov_lag2", "A", "B"], eligibility_mask=valid
    )
    assert result["blocks"][0]["chosen_id"] == "A"
    assert result["blocks"][0]["selected_future_invalid_rows"] == 10
    assert result["blocks"][0]["gates"]["A"]["eligible"]
    assert not result["blocks"][1]["gates"]["A"]["eligible"]
    valid[1, targets == 50] = False
    changed = replay_selector(
        dates, targets, bank, ["markov_lag2", "A", "B"], eligibility_mask=valid
    )
    assert changed["blocks"][0]["chosen_id"] == "markov_lag2"
    # The invalid day drops out of the next lookback; no permanent blacklist.
    valid[1, targets >= 70] = True
    resumed = replay_selector(
        dates, targets, bank, ["markov_lag2", "A", "B"], eligibility_mask=valid
    )
    assert resumed["blocks"][1]["gates"]["A"]["eligible"]


def test_tie_boundary_gate_uses_all_pools_and_includes_half_threshold():
    dates, targets, bank = fixture_bank()
    ties = np.zeros(bank.shape, dtype=bool)
    ties[1, :10, 7] = True
    excluded = replay_selector(
        dates, targets, bank, ["markov_lag2", "A", "B"], ties=ties
    )
    assert excluded["blocks"][0]["gates"]["A"]["tie_fractions_in_past_window"][7] == 0.5
    assert excluded["blocks"][0]["chosen_id"] == "markov_lag2"
    ties[1, 9, 7] = False
    included = replay_selector(
        dates, targets, bank, ["markov_lag2", "A", "B"], ties=ties
    )
    assert included["blocks"][0]["chosen_id"] == "A"


def test_reference_reserve_is_explicitly_reported():
    dates, targets, bank = fixture_bank()
    valid = np.zeros(bank.shape[:2], dtype=bool)
    result = replay_selector(
        dates, targets, bank, ["markov_lag2", "A", "B"], eligibility_mask=valid
    )
    assert all(block["chosen_id"] == "markov_lag2" for block in result["blocks"])
    assert all(block["baseline_reference_override"] for block in result["blocks"])


def test_selector_does_not_mutate_readonly_inputs():
    dates, targets, bank = fixture_bank()
    originals = [value.copy() for value in (dates, targets, bank)]
    for value in (dates, targets, bank):
        value.flags.writeable = False
    replay_selector(dates, targets, bank, ["markov_lag2", "A", "B"])
    for value, original in zip((dates, targets, bank), originals, strict=True):
        np.testing.assert_array_equal(value, original)


@pytest.mark.parametrize(
    "change",
    [
        "missing_past",
        "missing_future",
        "duplicate",
        "bad_hits",
        "bad_ids",
        "bad_dates",
        "bad_complexity",
        "bad_validity",
        "bad_ties",
    ],
)
def test_invalid_or_partial_inputs_cannot_silently_bias_selection(change):
    dates, targets, bank = fixture_bank()
    ids = ["markov_lag2", "A", "B"]
    kwargs = {}
    if change in ("missing_past", "missing_future"):
        position = 0 if change == "missing_past" else -1
        targets = np.delete(targets, position)
        bank = np.delete(bank, position, axis=1)
    elif change == "duplicate":
        targets = targets.copy()
        targets[1] = targets[0]
    elif change == "bad_hits":
        bank = bank.astype(float)
        bank[0, 0, 0] = np.nan
    elif change == "bad_ids":
        ids = ["markov_lag2", "A", "A"]
    elif change == "bad_dates":
        dates = dates[::-1]
    elif change == "bad_complexity":
        kwargs["complexity"] = [1, -1, 1]
    elif change == "bad_validity":
        kwargs["eligibility_mask"] = np.ones((1, 1))
    elif change == "bad_ties":
        kwargs["ties"] = np.ones((3, 50, 7))
    with pytest.raises(ValueError):
        replay_selector(dates, targets, bank, ids, **kwargs)


def test_statistics_keep_day_clusters_and_known_family_correction():
    candidate = np.array([[4] * 8, [0] * 8])
    baseline = candidate[::-1].copy()
    result = paired_test(
        candidate, baseline, ["same-day", "same-day"], permutations=199
    )
    assert result["mean_difference"] == 0
    assert result["p_value"] == 1 and result["days"] == 1
    assert holm_adjusted({"a": 0.01, "b": 0.04, "c": 0.03}) == {
        "a": 0.03,
        "b": 0.06,
        "c": 0.06,
    }
    assert hypergeom_tails(16)["p0"] == 1798 / 22701


def test_independent_formula_oracle_matches_every_fixed_bank_formula():
    from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
    from loto_enterprise.benchmark.methods import METHODS
    from scripts.analysis.pool_combinations_2026_10_19 import (
        candidate_specs,
        iter_candidates,
    )
    from scripts.analysis.validate_pool_combinations_2026_10_19 import (
        formula_from_descriptor,
    )

    names = sorted(set(METHODS) - EXCLUDED_FROM_PRODUCTION)
    rng = np.random.default_rng(2026101902)
    scores = rng.integers(0, 11, size=(3, len(names), 49)).astype(float)
    # Includes a mathematically flat equal-weight anti-ranking combination.
    scores[0, names.index("markov_lag2")] = np.arange(49)
    scores[0, names.index("frequency")] = np.arange(49)[::-1]
    descriptors = {spec["name"]: spec for spec in candidate_specs(names)}
    audit = {}
    checked = 0
    for name, _family, generated in iter_candidates(scores, names, audit=audit):
        for row in range(len(scores)):
            parent_scores = {expert: scores[row, j] for j, expert in enumerate(names)}
            expected, flat = formula_from_descriptor(descriptors[name], parent_scores)
            np.testing.assert_array_equal(generated[row], expected)
            assert bool(audit[name][row]) == flat
            checked += 1
    assert checked == 208 * 3


@pytest.fixture
def synthetic_complete_report(tmp_path):
    import hashlib
    import json
    import pandas as pd
    from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
    from loto_enterprise.benchmark.methods import METHODS, call_method
    from scripts.analysis.pool_combination_eval_2026_10_19 import evaluate_scores
    from scripts.analysis.pool_combinations_2026_10_19 import (
        candidate_specs,
        iter_candidates,
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
    dates = np.arange("2020-01-01", "2020-04-10", dtype="datetime64[D]").astype("U10")
    draws = np.asarray([rng.choice(np.arange(1, 50), 6, replace=False) for _ in dates])
    frame = pd.DataFrame(draws, columns=[f"n{i}" for i in range(1, 7)])
    frame.insert(0, "date", pd.to_datetime(dates).strftime("%d-%m-%Y"))
    paths = {
        "romania": histories / "loto_6_49.csv",
        "germania": histories / "externe/germania_lotto_6aus49.csv",
    }
    for path in paths.values():
        frame.to_csv(path, index=False)
    (root / "source_stub.py").write_bytes(b"# synthetic source\r\n")
    names = sorted(set(METHODS) - EXCLUDED_FROM_PRODUCTION)
    chosen_names = {
        "combo_rank_m025__frequency",
        "combo_rank_m050__frequency",
        "combo_residual_a050__frequency",
    }
    specs = [
        {
            "name": "markov_lag2",
            "family": "reference",
            "parent_names": ["markov_lag2"],
            "complexity": 1,
        },
        {
            "name": "frequency",
            "family": "control",
            "parent_names": ["frequency"],
            "complexity": 1,
        },
    ] + [spec for spec in candidate_specs(names) if spec["name"] in chosen_names]
    ids = [spec["name"] for spec in specs]
    targets = np.arange(50, 100)
    parents = {name: [] for name in ("frequency", "markov_lag2")}
    for target in targets:
        for name in parents:
            raw, _ = call_method(name, draws[:target], 49)
            parents[name].append([raw[n] for n in range(1, 50)])
    parents = {name: np.asarray(values) for name, values in parents.items()}
    # Only the selected descriptors use these two real causal parents; other
    # semantic rows are deterministic placeholders and are never published.
    tensor = np.stack(
        [parents.get(name, parents["frequency"]) for name in names], axis=1
    )
    scores = dict(parents)
    flat = {name: np.zeros(len(targets), dtype=bool) for name in parents}
    audit = {}
    for name, _family, values in iter_candidates(tensor, names, audit=audit):
        if name in chosen_names:
            scores[name] = values
            flat[name] = audit[name]
    hits = np.zeros((len(ids), len(targets), 2, 8), dtype=np.int8)
    ties = np.zeros((len(ids), len(targets), 8), dtype=bool)
    valid = np.ones((len(ids), len(targets)), dtype=bool)
    for i, name in enumerate(ids):
        hits[i], ties[i] = evaluate_scores(scores[name], draws[targets])
        valid[i] = ~flat[name]
    complexity = np.asarray([spec["complexity"] for spec in specs])
    manifest = {
        "baseline": "markov_lag2",
        "candidate_specs": specs,
        "candidate_count": len(specs) - 2,
        "seed": 2026101902,
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
        a, b, d, seed=2026101902, permutations=1999, bootstrap_samples=200 if ci else 0
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
            "start_history_rows": 50,
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
    report["countries"]["germania"]["selector_country_holm"] = report["countries"][
        "germania"
    ]["primary"]["paired"]["p_value"]
    external_dates = np.asarray(["germania:" + day for day in primary_dates])
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


def test_complete_synthetic_report_checks_prefixes_formulas_and_line_endings(
    synthetic_complete_report,
):
    from scripts.analysis.validate_pool_combinations_2026_10_19 import verify_report

    root, cache, report_path = synthetic_complete_report
    result = verify_report(report_path, cache, workspace_root=root)
    assert result["passed"], result["failures"]
    assert result["country_banks"] == 2
    assert result["static_holm_families"] == {"romania": 3, "external": 3}
    assert all(
        part["target_prefixes"] == 9 for part in result["direct_prefix_audit"].values()
    )


def test_verifier_detects_a_modified_static_metric(synthetic_complete_report):
    import json
    from scripts.analysis.validate_pool_combinations_2026_10_19 import verify_report

    root, cache, report_path = synthetic_complete_report
    report = json.loads(report_path.read_text(encoding="utf-8"))
    name = next(iter(report["static_combinations"]))
    report["static_combinations"][name]["romania"]["limited"]["events4"][0] += 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result = verify_report(report_path, cache, workspace_root=root)
    assert not result["passed"]
    assert any("events4[0]" in failure for failure in result["failures"])


def test_verifier_detects_a_changed_selector_identity_even_if_hits_match(
    synthetic_complete_report,
):
    from scripts.analysis.validate_pool_combinations_2026_10_19 import verify_report

    root, cache, report_path = synthetic_complete_report
    path = cache / "romania_bank.npz"
    with np.load(path, allow_pickle=False) as stored:
        values = {key: stored[key] for key in stored.files}
    row = int(np.flatnonzero(values["primary_mask"])[0])
    values["selected_indices"][row] = (values["selected_indices"][row] + 1) % len(
        values["candidate_ids"]
    )
    np.savez_compressed(path, **values)
    result = verify_report(report_path, cache, workspace_root=root)
    assert not result["passed"]
    assert any("selected indices" in failure for failure in result["failures"])
