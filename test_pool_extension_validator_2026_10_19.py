"""Synthetic complete artifacts exercise recounts and detect changed evidence."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from scripts.analysis import pool_extension_2026_10_19 as study
from scripts.analysis.pool_extension_statistics_2026_10_19 import (
    holm_adjusted,
    hypergeom_tails,
    metrics,
    paired_test,
    replay_selector,
)
from scripts.analysis.validate_pool_extension_2026_10_19 import (
    rank_units,
    verify_report,
)


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(study, "HERE", tmp_path)
    monkeypatch.setattr(study, "CACHE", cache)
    marker = tmp_path / "marker.py"
    marker.write_bytes(b"# fixture source\n")
    manifest = {
        "games": study.GAMES,
        "candidate_specs": {g: study.specs(m) for g, m in study.GAMES.items()},
        "data_sha256": {},
        "source_sha256": {"marker.py": hashlib.sha256(marker.read_bytes()).hexdigest()},
        "current_baselines": {},
        "seed": 2026101904,
        "inference": {"permutations": 199, "bootstrap_samples": 20},
    }
    prepared = {}
    for game, meta in study.GAMES.items():
        n, dn, ks = meta["max_num"], meta["draw_n"], meta["ks"]
        rng = np.random.default_rng(n)
        y = np.stack([rng.choice(n, dn, replace=False) + 1 for _ in range(600)])
        date = pd.date_range("2020-01-01", periods=600).strftime("%d-%m-%Y").tolist()
        date[300] = date[299]
        frame = pd.DataFrame(y, columns=[f"n{i}" for i in range(1, dn + 1)])
        frame.insert(0, "date", date)
        p = tmp_path / meta["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(p, index=False)
        manifest["data_sha256"][game] = hashlib.sha256(p.read_bytes()).hexdigest()
        iso = (
            pd.to_datetime(frame["date"], format="%d-%m-%Y")
            .dt.strftime("%Y-%m-%d")
            .to_numpy(dtype="U10")
        )
        unique = np.unique(iso)
        prior = np.searchsorted(iso, iso, side="left")
        start = max(256, int(np.searchsorted(iso, unique[len(unique) // 2])))
        targets = np.flatnonzero(prior >= start)
        t = len(targets)
        scores = rng.normal(size=(34, t, n))
        indicator = np.zeros((t, n), dtype=np.int8)
        indicator[np.arange(t)[:, None], y[targets] - 1] = 1
        order = np.argsort(-scores, axis=2, kind="stable")
        raw = np.cumsum(
            np.take_along_axis(np.broadcast_to(indicator, (34, t, n)), order, axis=2),
            axis=2,
        )[:, :, np.array(ks) - 1]
        h = np.stack((raw, raw), axis=2).astype(np.int8)
        valid = np.ones((34, t), dtype=bool)
        ties = np.zeros((34, t, len(ks)), dtype=bool)
        ids = [s["name"] for s in manifest["candidate_specs"][game]]
        complexity = np.array(
            [s["complexity"] for s in manifest["candidate_specs"][game]]
        )
        replay = replay_selector(
            iso,
            targets,
            h[:, :, 1],
            ids,
            ks=ks,
            draw_n=dn,
            complexity=complexity,
            eligibility_mask=valid,
            ties=ties,
        )
        current = {
            f"k{k}": "frequency" if j % 2 else "markov_lag2" for j, k in enumerate(ks)
        }
        manifest["current_baselines"][game] = current
        refnames = ["frequency", "markov_lag2"]
        refs = h[[1, 0]]
        cur = np.stack(
            [
                h[1 if current[f"k{k}"] == "frequency" else 0, :, :, j]
                for j, k in enumerate(ks)
            ],
            axis=-1,
        )
        prepared[game] = (
            meta,
            iso,
            y,
            targets,
            start,
            scores,
            h,
            valid,
            ties,
            ids,
            complexity,
            replay,
            current,
            refnames,
            refs,
            cur,
        )
    protocol = tmp_path / f"{study.STEM}_protocol.json"
    study.write_json(protocol, manifest)
    identity = hashlib.sha256(protocol.read_bytes()).hexdigest()
    report = {
        "protocol_sha256": identity,
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "production_changed": False,
        "candidate_count_per_game": 32,
        "candidate_game_tests": 64,
        "games": {},
        "protected_before": {},
        "protected_after": {},
    }
    for game, values in prepared.items():
        (
            meta,
            iso,
            y,
            targets,
            start,
            scores,
            h,
            valid,
            ties,
            ids,
            complexity,
            replay,
            current,
            refnames,
            refs,
            cur,
        ) = values
        ks, dn = meta["ks"], meta["draw_n"]
        t = len(targets)
        days = iso[targets]
        selected = replay["selected_indices"]
        sel = h[selected, np.arange(t)]
        primary, diag = replay["primary_mask"], replay["diagnostic_mask"]
        describe = lambda a: metrics(a, ks=ks, draw_n=dn)
        compare = lambda a, b, boot: paired_test(
            a,
            b,
            days[primary],
            ks=ks,
            draw_n=dn,
            permutations=199,
            bootstrap_samples=boot,
            seed=2026101904,
        )
        part = {
            "geometry": meta,
            "input_rows": len(iso),
            "target_rows": t,
            "start_history_rows": start,
            "first_primary": str(days[primary][0]),
            "last_primary": str(days[primary][-1]),
            "blocks": study.compact_blocks(replay["blocks"]),
            "primary": {},
            "diagnostic": {},
            "raw": {},
            "current_baselines": current,
            "current_invalid_primary_by_k": {f"k{k}": 0 for k in ks},
            "invalid_primary_by_candidate": {},
            "invalid_target_by_candidate": {},
            "selected_primary_invalid_rows": 0,
            "hypergeometric": {
                str(k): hypergeom_tails(k, max_num=meta["max_num"], draw_n=dn)
                for k in ks
            },
            "candidates": {},
        }
        for segment, mask, mode in (
            ("primary", primary, 1),
            ("diagnostic", diag, 1),
            ("raw", primary, 0),
        ):
            for label, array in (("selector", sel), ("markov", h[0]), ("current", cur)):
                part[segment][label] = describe(array[mask, mode])
        part["primary"]["paired_vs_markov"] = compare(
            sel[primary, 1], h[0, primary, 1], 20
        )
        part["primary"]["paired_vs_current_diagnostic"] = compare(
            sel[primary, 1], cur[primary, 1], 20
        )
        for i, d in enumerate(manifest["candidate_specs"][game][2:], 2):
            test = compare(h[i, primary, 1], h[0, primary, 1], 0)
            part["candidates"][d["name"]] = {
                "spec": d,
                "limited": describe(h[i, primary, 1]),
                "raw": describe(h[i, primary, 0]),
                "vs_markov": {
                    key: test[key]
                    for key in (
                        "mean_difference",
                        "p_value",
                        "exact_sign_flip_p_diagnostic",
                        "wins",
                        "losses",
                        "days",
                    )
                },
                "invalid_primary_rows": 0,
            }
        for name, q in holm_adjusted(
            {name: r["vs_markov"]["p_value"] for name, r in part["candidates"].items()}
        ).items():
            part["candidates"][name]["holm_within_game"] = q
        report["games"][game] = part
        study.save_bank(
            cache / f"{game}_bank.npz",
            identity=np.asarray(identity),
            candidate_ids=np.asarray(ids),
            full_dates=iso,
            targets=targets,
            draws=y[targets],
            complexity=complexity,
            hits=h,
            ties=ties,
            valid=valid,
            candidate_scores=scores,
            expert_names=np.asarray(study.EXPERTS),
            expert_scores=np.stack((scores[1], scores[0], scores[2])),
            expert_invalid=np.zeros((3, t), dtype=bool),
            reference_names=np.asarray(refnames),
            reference_hits=refs,
            current_hits=cur,
            current_invalid=np.zeros((t, len(ks)), dtype=bool),
            selected_indices=selected,
            selector_hits=sel,
            primary_mask=primary,
            diagnostic_mask=diag,
        )
    for game, q in holm_adjusted(
        {
            g: p["primary"]["paired_vs_markov"]["p_value"]
            for g, p in report["games"].items()
        }
    ).items():
        p = report["games"][game]["primary"]
        p["holm_across_games"] = q
        p["selector_gate_passed"] = (
            q < 0.05 and p["paired_vs_markov"]["mean_difference"] > 0
        )
    for label, q in holm_adjusted(
        {
            f"{g}:{name}": row["vs_markov"]["p_value"]
            for g, p in report["games"].items()
            for name, row in p["candidates"].items()
        }
    ).items():
        g, name = label.split(":", 1)
        report["games"][g]["candidates"][name]["holm_across_games"] = q
    output = tmp_path / f"{study.STEM}.json"
    study.write_json(output, report)
    study.export_csv(report)
    return tmp_path, cache, output, report


def test_complete_recount_of_both_synthetic_games(artifact):
    root, cache, path, _report = artifact
    result = verify_report(path, cache, workspace_root=root, audit_scores=False)
    assert result["passed"], result["failures"]
    assert result["csv_rows"]["candidates"] == 576
    assert result["full_prediction_audit"] is False


def test_modified_rate_is_rejected(artifact):
    root, cache, path, report = artifact
    report["games"]["loto_5_40"]["primary"]["selector"]["primary"] += 0.1
    study.write_json(path, report)
    result = verify_report(path, cache, workspace_root=root, audit_scores=False)
    assert not result["passed"]
    assert any("primary:selector.primary" in label for label in result["failures"])


def test_modified_source_is_rejected(artifact):
    root, cache, path, _report = artifact
    (root / "marker.py").write_text("# changed\n", encoding="utf-8")
    assert (
        "source:marker.py"
        in verify_report(path, cache, workspace_root=root, audit_scores=False)[
            "failures"
        ]
    )


def test_prediction_audit_detects_invented_scores_and_noncanonical_pools(artifact):
    root, cache, path, _report = artifact
    result = verify_report(path, cache, workspace_root=root)
    assert not result["passed"]
    assert any("direct expert:" in label for label in result["failures"])
    assert result["full_prediction_audit"] is True


def test_double_mean_rank_units_are_exact_with_ties():
    np.testing.assert_array_equal(rank_units([9, 2, 2, 6]), [6, 1, 1, 4])
